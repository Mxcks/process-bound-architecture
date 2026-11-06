"""
Automated tests for the full-chain integration scenario
(experiment 020).

The single test case is tests/test-cases/full-chain.md case 1,
written before this code: Max's whole original chain run in ONE
scenario, in order, over ONE policy, with the mechanisms already
built by experiments 002-019, changing no existing mechanism's
behavior. The only code change this experiment makes anywhere
is a pure wiring addition in packages/policy-library/
replication.py: place_policy(), ReplicatedPolicy, and heal()
gain optional anchor_path / anchor_signer / anchor_secret_hex
parameters, all defaulting to None (no consultation, exactly
the pre-020 behavior), passed through to the PolicyNode
constructions placement and healing already perform, so a
placed holder can consult a signed anchor (015/019) the way a
standalone PolicyNode already could. Everything else here is
composition of existing public functions and fixtures.

Stages, in order (test_full_chain_end_to_end):
  1. Gate 1 registration through the authenticated channel (002)
  2. Signed registry anchor written by the author (019)
  3. Placement at a replication floor of 3, holders consulting
     the signed anchor (005 + the wiring addition above)
  4. Gate 2 serving from a second holder, in process (003)
  5. Serving from a second PROCESS over mutual TLS with
     certificate-to-node-id binding and the signed anchor
     configured at startup, plus mesh sync over that link
     (014, 017, 018/019, 007)
  6. Mesh sync convergence in process, then mesh-anchor
     consultation on the converged state (004, 015/019)
  7. Liveness detection and automatic healing on the modeled
     clock (016 over 010), floor restored, coordinated state
     after healing
  8. Author revocation (008): boundary refusal, the anchor
     lifecycle on delivery (stale first, re-anchor by the same
     signer, then reload refuses as revoked), final state

Two further test methods cover the findings this scenario
exposed on surfaces no earlier experiment exercised together
(the finding clause of the test case, stated before the build).
As built in experiment 020 they asserted the findings exactly
as found (F1: a placed policy could not be reloaded; F2: a
revoked policy over the network dropped the connection).
Experiment 021 fixed both findings, and these two methods now
assert the FIXED behavior (the 020 README carries a
superseded-in-part note saying so):
  - test_placed_holders_reload_delivers_revocation: revocation
    delivered to PLACED holders via reload_snapshot (005/010/016).
  - test_revoked_policy_over_network_transport_clean_refusal:
    revocation served over the network transport as a clean
    HTTP refusal naming the revocation (007/014),
    plus the Gate 5 verdict stage (018/019 pattern) in
    test_gate5_verdict_genuine_passes_rewritten_fails.

Test credentials are generated inside the tests at runtime
(protected_link.generate_test_credentials, openssl on the
machine) into each test's temporary directory. Signing secrets
are real Gate 1 enrollments issued at runtime in the tests. No
credential, key, certificate, or secret is committed or leaves
the temporary directories. Process cleanup is part of the
contract, as in experiments 007/014/017/018/019: every spawned
server is registered with addCleanup the moment it starts.

Run with either:
    python3 -m unittest discover -s packages/synchronization -v -p "test_full_chain_integration.py"
    python3 packages/synchronization/test_full_chain_integration.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
"""

import base64
import importlib.util
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Insert in reverse so HERE (synchronization) ends at the front:
# the plain module name `anchor` must resolve to THIS package's
# anchor.py (the mesh anchor), as in gate5_harness's own path
# setup; the registration anchor is loaded by file path below.
sys.path.insert(0, str(HERE.parent / "validation"))
sys.path.insert(0, str(HERE.parent / "registration"))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from anchored_sync import AnchoredSyncNode  # noqa: E402
from gate5_harness import Gate5Harness  # noqa: E402
from liveness import LOST_AFTER_MISSED_BEATS, LivenessWatcher  # noqa: E402
from policy_node import (  # noqa: E402
    AnchorStale,
    PolicyNode,
    PolicyRevoked,
)
from registration import (  # noqa: E402
    BlockedRevoked,
    ExecutionBoundary,
    RegistrationChannel,
    sign_library,
    sign_revocation,
)
from replication import NodeSpec, place_policy  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def _load_module(name: str, path: Path):
    """Load an anchor module by file path under a private name:
    two packages ship an anchor.py, and a plain import can
    resolve to the wrong one (the lesson of 015/018/019)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_module(
    "pba_fullchain_registry_anchor", HERE.parent / "registration" / "anchor.py")
_mesh_anchor = _load_module(
    "pba_fullchain_mesh_anchor", HERE / "anchor.py")

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")


class ServerProcess:
    """One node_server.py child process speaking mutual TLS with
    identity binding and a signed registry anchor configured,
    reliably cleaned up. Mirrors test_cert_node_binding.py /
    test_signed_anchor_in_servers.py."""

    def __init__(self, test, workdir: Path, label: str, args: list[str], creds):
        self.label = label
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[label]
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", label,
            "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            "--tls-bind-identity",
        ] + args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        test.addCleanup(self.stop)
        self.base_url = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.label} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError,
                        ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.label} not ready in {timeout}s")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
        finally:
            self._log_fh.close()


class FullChainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(node_server.clear_client_tls)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # Stage 1 setup: the authenticated channel exists first.
        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, self.central_dir / "audit.jsonl")
        self.alice_secret = self.channel.enroll_user("alice")

        # TLS credentials for the process stages, generated at
        # runtime into this test's temp dir only.
        self.creds = protected_link.generate_test_credentials(
            tmp / "creds",
            ["srv-a", "srv-b", "test-client"],
        )
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(
            client.cert, client.key, self.creds.ca_cert,
            node_id="test-client")

        self.payloads = None  # filled by _register()
        self.record = None

    # -- shared setup helpers -------------------------------------------

    def _register(self):
        """Stage 1: alice registers her policy through the channel."""
        channel = self.channel
        assert channel.is_ready()
        sig = sign_library(self.alice_secret, "alice", "member-access",
                           "1.0.0", ALICE_CODE)
        self.record, created = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig)
        assert created
        self.payloads = {self.record.policy_hash: ALICE_CODE}
        return self.record

    def _anchor_registry(self):
        """Stage 2: the signed registry anchor, written outside
        the registry directory by alice's enrolled identity."""
        self.anchor_path = self.tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        self.secret_file = self.tmp / "outside" / "anchor-secret.txt"
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")
        return self.anchor_path

    def _snapshot_copy(self, label: str) -> Path:
        dest_dir = self.tmp / f"{label}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _placement_snapshot(self) -> Path:
        self.placement_snapshot_path = self.tmp / "placement-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.placement_snapshot_path)
        return self.placement_snapshot_path

    def _place(self, snapshot: Path):
        return place_policy(
            policy_hash=self.record.policy_hash,
            code=ALICE_CODE,
            registry_snapshot_path=snapshot,
            candidates=[NodeSpec(n, serve_policies=True) for n in HOLDERS],
            floor=3,
            anchor_path=self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
        )

    def _server_args(self, label: str, snapshot: Path):
        payloads_file = self.tmp / f"{label}-payloads.json"
        payloads_file.write_text(
            json.dumps({self.record.policy_hash: ALICE_CODE.hex()}),
            encoding="utf-8")
        return [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.secret_file),
        ]

    def _revoke(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.record.policy_hash))
        assert created
        return revocation

    # -- the full chain ---------------------------------------------------

    def test_full_chain_end_to_end(self):
        stages = []

        # Stage 1 (002): registration through the authenticated
        # channel, admitted at the boundary, validation channel
        # bound, one deterministic record.
        record = self._register()
        boundary = ExecutionBoundary(self.channel)
        admitted = boundary.admit(policy_hash=record.policy_hash,
                                  code=ALICE_CODE)
        self.assertEqual(admitted.record_hash, record.record_hash)
        self.assertEqual(self.channel.validation_channel_for(
            record.policy_hash), record.validation_channel)
        self.assertTrue(self.channel.verify_registry_chain())
        stages.append("1 registration: PASS")

        # Stage 2 (019): signed anchor over the 1-entry registry.
        self._anchor_registry()
        check = _registry_anchor.check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        self.assertTrue(check.ok)
        self.assertTrue(check.signature_checked)
        self.assertTrue(check.signature_valid)
        stages.append("2 signed registry anchor: PASS")

        # Stage 3 (005 + wiring): placement at floor 3, holders
        # consulting the signed anchor at construction.
        snapshot = self._placement_snapshot()
        replicated = self._place(snapshot)
        report = replicated.placement_report()
        self.assertEqual(report.holder_node_ids, HOLDERS)
        self.assertEqual(report.holder_count, 3)
        self.assertTrue(report.floor_met)
        self.assertEqual(report.status, "available")
        stages.append("3 placement floor 3 with anchor consultation: PASS")

        # Stage 4 (003): serving from a second holder, in process.
        # (Holder order is placement order; serve() walks it, so
        # a standalone second node proves the same Gate 2 path
        # independently of serve()'s first-holder choice.)
        second = PolicyNode(
            node_id="holder-b",
            registry_snapshot_path=snapshot,
            payloads=self.payloads,
            serve_policies=True,
            anchor_path=self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
        )
        self.assertEqual(second.anchor_status, "anchored")
        served = second.serve_policy(record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.author, "alice")
        self.assertEqual(served.record_hash, record.record_hash)
        stages.append("4 Gate 2 serving from second holder: PASS")

        # Stage 5 (014/017/018/019/007): two processes over mutual
        # TLS with identity binding and the signed anchor at
        # startup; the policy is fetched from the SECOND process;
        # mesh state syncs over the same link and converges.
        srv_a = ServerProcess(
            self, self.tmp, "srv-a",
            self._server_args("srv-a", self._snapshot_copy("srv-a")),
            self.creds)
        srv_a.wait_ready()
        srv_b = ServerProcess(
            self, self.tmp, "srv-b",
            self._server_args("srv-b", self._snapshot_copy("srv-b")),
            self.creds)
        srv_b.wait_ready()
        health_b = node_server.fetch_health(srv_b.base_url)
        self.assertTrue(health_b["anchor_configured"])
        self.assertEqual(health_b["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(srv_b.base_url,
                                                record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["served_by"], "srv-b")
        node_server.mutate(srv_a.base_url, "region", "us-central")
        node_server.mutate(srv_b.base_url, "tier", "member")
        outcome = node_server.request_sync(srv_a.base_url, srv_b.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertFalse(outcome["peer"]["divergence_detected"])
        state_a = node_server.fetch_state(srv_a.base_url)
        state_b = node_server.fetch_state(srv_b.base_url)
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(state_a["state"], state_b["state"])
        stages.append("5 protected identity-bound process serving + sync: PASS")

        # Stage 6 (004 + 015/019): in-process mesh convergence,
        # then a signed mesh anchor over the converged state and
        # an AnchoredSyncNode accepting exactly that state.
        mesh = [SyncNode(f"mesh-{i}") for i in range(3)]
        mesh[0].mutate(f"policy:{record.policy_hash}", "placed-at-floor-3")
        mesh[1].mutate("region", "us-central")
        mesh[2].mutate("tier", "member")
        everything = []
        for node in mesh:
            everything.extend(node.entries())
        for node in mesh:
            result = node.receive_entries(everything)
            self.assertFalse(result.divergence_detected)
        self.assertEqual(len({n.state_hash() for n in mesh}), 1)
        mesh_anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        _mesh_anchor.write_mesh_anchor(
            mesh[0], mesh_anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        anchored = AnchoredSyncNode(
            "mesh-anchored", anchor_path=mesh_anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret)
        accepted = anchored.receive_entries(everything)
        self.assertFalse(accepted.divergence_detected)
        self.assertEqual(anchored.anchor_status(), "anchored")
        self.assertEqual(anchored.state_hash(), mesh[0].state_hash())
        stages.append("6 mesh convergence + mesh anchor consultation: PASS")

        # Stage 7 (016 over 010): holder-a goes silent on the
        # modeled clock; silent-short first (no healing), then
        # lost, and healing fires in the same check, recruiting
        # replacement-d under placement's rules - constructed,
        # through the wiring, with the same anchor consultation.
        watcher = LivenessWatcher(
            replicated,
            candidates=[
                NodeSpec("holder-b", serve_policies=True),  # already holds
                NodeSpec("not-opted-in", serve_policies=False),
                NodeSpec("replacement-d", serve_policies=True),
            ],
        )
        report = watcher.tick(beating=list(HOLDERS))
        self.assertEqual(report.status, "no_action")
        for _ in range(LOST_AFTER_MISSED_BEATS - 1):
            report = watcher.tick(
                beating=[n for n in HOLDERS if n != "holder-a"])
        self.assertEqual(report.holder_states["holder-a"], "silent_short")
        self.assertIsNone(report.healing)
        self.assertEqual(replicated.holder_node_ids(), HOLDERS)
        report = watcher.tick(beating=[n for n in HOLDERS if n != "holder-a"])
        self.assertEqual(report.lost_node_ids, ("holder-a",))
        self.assertIsNotNone(report.healing)
        self.assertEqual(report.healing.recruited_node_ids, ("replacement-d",))
        self.assertTrue(report.healing.floor_restored)
        self.assertEqual(report.status, "healed")
        self.assertEqual(replicated.holder_node_ids(),
                         ("holder-b", "holder-c", "replacement-d"))
        # Coordinated state after healing: the placed policy still
        # serves verified, and the mesh from stage 6 is still one
        # converged hash on every node.
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        self.assertEqual(replicated.serve().record_hash, record.record_hash)
        self.assertEqual(len({n.state_hash() for n in mesh}), 1)
        stages.append("7 liveness auto-healing, floor restored: PASS")

        # Stage 8 (008): author revocation, delivered through the
        # anchor lifecycle. The boundary refuses; the channel no
        # longer honors the validation channel.
        self._revoke()
        self.assertTrue(self.channel.is_revoked(record.policy_hash))
        self.assertIsNone(self.channel.validation_channel_for(
            record.policy_hash))
        with self.assertRaises(BlockedRevoked):
            boundary.admit(policy_hash=record.policy_hash, code=ALICE_CODE)
        self.assertTrue(self.channel.verify_registry_chain())

        # Delivery, first attempt: `second` still holds the
        # pre-revocation snapshot file; copy the grown registry
        # over it and reload WITHOUT re-anchoring: AnchorStale,
        # and the node's previous view is left untouched (it
        # still serves - the stale window, named as in 008/015).
        shutil.copyfile(self.registry_log, snapshot)
        with self.assertRaises(AnchorStale):
            second.reload_snapshot()
        self.assertEqual(second.serve_policy(record.policy_hash).code,
                         ALICE_CODE)

        # Re-anchor the grown registry, SAME signer (019's rule),
        # then the reload stands and serving stops as revoked.
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        loaded = second.reload_snapshot()
        self.assertEqual(loaded, 2)
        self.assertEqual(second.anchor_status, "anchored")
        self.assertTrue(second.is_revoked(record.policy_hash))
        with self.assertRaises(PolicyRevoked):
            second.serve_policy(record.policy_hash)

        # Final state, in-process surfaces: a fresh node over the
        # updated, re-anchored snapshot refuses as revoked, and a
        # placement REBUILT over that snapshot (the placed-object
        # surface is the finding in the next test) refuses too.
        fresh = PolicyNode(
            node_id="holder-c",
            registry_snapshot_path=snapshot,
            payloads=self.payloads,
            serve_policies=True,
            anchor_path=self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
        )
        with self.assertRaises(PolicyRevoked):
            fresh.serve_policy(record.policy_hash)
        rebuilt = self._place(snapshot)
        self.assertTrue(rebuilt.placement_report().floor_met)
        with self.assertRaises(PolicyRevoked):
            rebuilt.serve()
        stages.append("8 revocation delivered, serving stopped: PASS")

        for line in stages:
            print(f"STAGE {line}")

    # -- finding F1 (fixed in experiment 021): revocation to placed holders --

    def test_placed_holders_reload_delivers_revocation(self):
        record = self._register()
        self._anchor_registry()
        snapshot = self._placement_snapshot()
        replicated = self._place(snapshot)
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        self._revoke()
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        # The updated, re-anchored record is delivered the only
        # way placement knows: over the shared snapshot file.
        shutil.copyfile(self.registry_log, snapshot)

        # Experiment 020 found F1 here: ReplicatedPolicy exposed
        # no reload/update method and the placed object still
        # served the revoked policy after this delivery.
        # Experiment 021 fixed it (see that experiment's README
        # and the superseded-in-part note in this experiment's
        # README): the reload path now exists, the placed object
        # still serves from its placement-time view until the
        # reload is called (that remaining fact is asserted, not
        # hidden), and after the reload every holder refuses.
        has_reload = hasattr(replicated, "reload_snapshot")
        still_serves_before_reload = replicated.serve().code == ALICE_CODE
        loaded = replicated.reload_snapshot() if has_reload else None
        refused = False
        try:
            replicated.serve()
        except PolicyRevoked:
            refused = True
        print(
            "FIXED F1 placed-holders-reload (experiment 021): "
            f"ReplicatedPolicy exposes reload_snapshot: {has_reload}; "
            f"before the reload the placed object still serves from "
            f"its placement-time view: {still_serves_before_reload}; "
            f"reload loaded entries: {loaded}; after the reload "
            f"serve() refuses as PolicyRevoked: {refused}")
        self.assertTrue(has_reload)
        self.assertTrue(still_serves_before_reload)
        self.assertEqual(loaded, 2)
        self.assertTrue(refused)

    # -- finding F2 (fixed in experiment 021): revocation over the network --

    def test_revoked_policy_over_network_transport_clean_refusal(self):
        record = self._register()
        self._anchor_registry()
        snap_b = self._snapshot_copy("srv-b")
        args = self._server_args("srv-b", snap_b)
        srv_b = ServerProcess(self, self.tmp, "srv-b", args, self.creds)
        srv_b.wait_ready()
        status, body = node_server.fetch_policy(srv_b.base_url,
                                                record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        srv_b.stop()

        # Revoke, re-anchor (same signer), deliver the updated
        # snapshot to the server's own copy, restart the process.
        self._revoke()
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        shutil.copyfile(self.registry_log, snap_b)
        srv_b2 = ServerProcess(self, self.tmp, "srv-b", args, self.creds)
        srv_b2.wait_ready()
        health = node_server.fetch_health(srv_b2.base_url)
        self.assertTrue(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "anchored")

        # Experiment 020 found F2 here: node_server's policy
        # endpoint caught NotServing / PolicyNotFound /
        # TamperedRecord but NOT PolicyRevoked (a sibling
        # PolicyLibraryError), so the refusal was an unhandled
        # exception in the handler - the connection dropped with
        # no HTTP response at all. Experiment 021 fixed it (see
        # that experiment's README and the superseded-in-part
        # note in this experiment's README): the request now
        # returns a clean HTTP refusal naming the revocation,
        # the policy bytes are not served, and the server keeps
        # serving afterward (health answers).
        status, body = node_server.fetch_policy(srv_b2.base_url,
                                                record.policy_hash)
        print(
            "FIXED F2 revoked-over-network (experiment 021): "
            f"policy request after revocation produced: "
            f"http_{status} {json.dumps(body)[:200]}")
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        self.assertNotIn("code_base64", body)
        health_after = node_server.fetch_health(srv_b2.base_url)
        self.assertTrue(health_after["ok"])

    def test_gate5_verdict_genuine_passes_rewritten_fails(self):
        # Gate 5 over the scenario's evidence, 018/019 pattern: a
        # harness scenario (its own Gate 1 registration, Gate 2
        # serving node, Gate 3 mesh) with SIGNED anchors on both
        # chains, signed by an enrolled identity this test controls.
        tmp = self.tmp
        signer_dir = tmp / "signer"
        signer_channel = RegistrationChannel(
            signer_dir / "registry.jsonl", signer_dir / "audit.jsonl")
        signer_secret = signer_channel.enroll_user("anchor-keeper")

        def fresh_harness(label):
            harness = Gate5Harness(tmp / label)
            harness.build_scenario()
            outside = tmp / f"{label}-outside"
            reg_anchor = outside / "registry-anchor.json"
            _registry_anchor.write_anchor(
                harness.snapshot_path, reg_anchor,
                signer="anchor-keeper", secret_hex=signer_secret)
            mesh_anchor = outside / "mesh-anchor.json"
            _mesh_anchor.write_mesh_anchor(
                harness.canary_node, mesh_anchor,
                signer="anchor-keeper", secret_hex=signer_secret)
            return harness, reg_anchor, mesh_anchor

        def run(harness, reg_anchor, mesh_anchor):
            return harness.run_gate(
                iterations=10,
                registry_anchor_path=reg_anchor,
                mesh_anchor_path=mesh_anchor,
                registry_anchor_signer="anchor-keeper",
                registry_anchor_secret_hex=signer_secret,
                mesh_anchor_signer="anchor-keeper",
                mesh_anchor_secret_hex=signer_secret)

        harness, reg_anchor, mesh_anchor = fresh_harness("genuine")
        verdict = run(harness, reg_anchor, mesh_anchor)
        self.assertTrue(verdict.passed)
        anchors = {c.name: c for c in verdict.checks
                   if c.name.startswith("anchor_evidence")}
        self.assertEqual(set(anchors), {"anchor_evidence_registry",
                                        "anchor_evidence_mesh"})
        for c in anchors.values():
            self.assertTrue(c.ok)
            self.assertIn("anchored", c.detail)

        # Rewritten registry: a fresh chain whose alice entry is
        # deterministic and identical and whose other entries
        # carry different code, so the chain verifies internally,
        # the canary policy is present, and only the anchor can
        # tell - exactly 018's rewrite.
        harness, reg_anchor, mesh_anchor = fresh_harness("rewritten")
        from gate5_harness import (CANARY_CODE, CANARY_POLICY_NAME,
                                   CANARY_POLICY_VERSION)
        rewrite_dir = tmp / "rewrite-central"
        rewrite_log = rewrite_dir / "registry.jsonl"
        channel = RegistrationChannel(rewrite_log,
                                      rewrite_dir / "audit.jsonl")
        alice_key = channel.enroll_user("alice")
        channel.register(
            author="alice", name=CANARY_POLICY_NAME,
            version=CANARY_POLICY_VERSION, code=CANARY_CODE,
            signature=sign_library(alice_key, "alice", CANARY_POLICY_NAME,
                                   CANARY_POLICY_VERSION, CANARY_CODE))
        bob_key = channel.enroll_user("bob")
        for name, code in (("rate-limit", b"policy: limit 999/min - rewritten\n"),
                           ("audit-trail", b"policy: log nothing - rewritten\n")):
            channel.register(
                author="bob", name=name, version="1.0.0", code=code,
                signature=sign_library(bob_key, "bob", name, "1.0.0", code))
        harness.snapshot_path.write_bytes(rewrite_log.read_bytes())
        verdict = run(harness, reg_anchor, mesh_anchor)
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_registry"])
        reg_check = {c.name: c for c in verdict.checks}[
            "anchor_evidence_registry"]
        self.assertIn("anchor_mismatch", reg_check.detail)
        print("STAGE 9 Gate 5 verdict: genuine PASS, rewritten FAIL "
              "on anchor_evidence_registry only (anchor_mismatch)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
