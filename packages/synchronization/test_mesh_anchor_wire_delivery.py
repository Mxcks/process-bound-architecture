"""
Automated tests for mesh anchor delivery over the wire
(tests/test-cases/sync-mesh.md, case 20), built in
experiment 031.

Case 17 (experiment 028) gave a running server a surface
that RECEIVES its registry anchor over the wire, and case
19 (experiment 030) a server that pushes its own. Case 15's
mesh anchor was left out of both: a server started with
--mesh-anchor (experiment 026) reads a file placed by hand,
and completing the mesh anchor's lifecycle at it - the
same-signer re-anchor of case 11, the witness
countersignature of case 18 - is a manual act no wire
performs, so the grown state a sync refuses as
anchor_stale can be adopted only after a hand replaces the
file. This suite asserts the mesh half's close:

- A running server started with mesh-anchor configuration
  gains one driven surface, POST /deliver-mesh-anchor,
  carrying the mesh anchor object's own JSON, never a
  path, with a deliver_mesh_anchor client helper in
  deliver_anchor's style and last_mesh_anchor_delivery on
  /health beside mesh_anchor_status. There is no paired
  form: the anchor is judged against the mesh state the
  server HOLDS and the anchor it already stands under
  (case 20's stated pairing decision - mesh entries keep
  their own consulted receive path, case 15).
- The 026 scenario driven forward by a wire delivery:
  converged and anchored, the honest peer grows, the sync
  refuses anchor_stale, the successor anchor (the same
  signer, the current anchor's kept history extended by
  case 11's own write path) is DELIVERED, and the same
  sync then converges - no hand ever touches the server's
  anchor file.
- The catch-up standing: an anchor pinning exactly the
  held state is delivered onto it (the server's own
  mutations never consult, so its standing can lag its
  own anchor), and a byte-identical delivery is
  no_change.
- The refusals, each leaving the mesh anchor file
  byte-for-byte unchanged, the held state untouched, and
  sync behavior undisturbed: a foreign chain's anchor in
  a fresh lineage is anchor_mismatch; a different
  signer's anchor over the same state is different_signer
  and audit-logged beside the mesh anchor in case 11's
  shape; a tampered kept history is
  anchor_signature_invalid; an earlier version of the
  server's own anchor file is anchor_regression
  (shape i), and - on a head-only server - an unsigned
  anchor pinning an earlier prefix of the chain the
  current anchor pins is anchor_regression too
  (shape ii); an anchor over an intermediate state the
  held state already stands honestly past is
  anchor_stale; a configured mesh anchor that cannot be
  read refuses the delivery as anchor_unreadable, in
  shape.
- The witness fence (case 18) at delivery: at a
  witness-configured server, a signer-alone successor is
  anchor_witness_invalid, a successor countersigned by a
  non-witness is the same, and the configured witness's
  countersigned successor is delivered and unblocks the
  sync.
- THE LINEAGE FINDING, stated as the evidence shows it:
  a successor anchor over experiment 012's REWRITTEN
  mesh chain, written from the server's current anchor
  file by case 11's own write path with the CURRENT
  signer's secret (which every verifier of a signed
  anchor legitimately holds in this symmetric model), is
  ACCEPTED - its history honestly extends the current
  anchor's, its signature verifies - and the server ends
  standing under the rewrite's anchor, its held genuine
  state classified anchor_mismatch and its next honest
  sync refused under that name. Custody of the signer's
  secret is the fence at this surface, exactly as case
  17's finding recorded it for the registry half.
- Configuration edge: a server started without
  --mesh-anchor reports a delivery as no_mesh_anchor
  and is otherwise undisturbed.
- The full flow over experiment 014's protected
  transport with a signed mesh anchor: the successor
  anchor is delivered over HTTPS, and the stale-refused
  sync then converges over the protected link.

Process cleanup is part of the contract, as in
experiments 007-030: every spawned server is registered
with addCleanup the moment it starts, and no leg ever
runs more than two server processes at once. Test
credentials and enrollment secrets are generated inside
the tests at runtime into the test's temporary
directory; no credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_mesh_anchor_wire_delivery.py"
    python3 packages/synchronization/test_mesh_anchor_wire_delivery.py

Standard library only. Localhost only; nothing leaves
this machine.
"""

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
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def _load_mesh_anchor():
    """Load experiment 012's MESH anchor module by file path
    under a private name, the way test_mesh_anchor_wire
    loads it: two packages ship an anchor.py, and this
    suite puts the registration package on sys.path too,
    so a plain `import anchor` may resolve to the wrong
    one."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_mesh_anchor_delivery_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mesh_anchor = _load_mesh_anchor()
write_mesh_anchor = _mesh_anchor.write_mesh_anchor
read_anchor_audit = _mesh_anchor.read_anchor_audit

SERVER = HERE / "node_server.py"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_mesh_anchor_wire.py's helper."""

    def __init__(self, test, workdir: Path, label: str, node_id: str,
                 args: list[str], tls=None):
        self.label = label
        self.node_id = node_id
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id,
            "--port", "0",
            "--port-file", str(self.port_file),
        ]
        if tls is not None:
            cert, key, ca = tls
            cmd += ["--tls-cert", str(cert), "--tls-key", str(key),
                    "--tls-ca", str(ca)]
        cmd += args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        test.addCleanup(self.stop)
        self.base_url = None
        self.scheme = "https" if tls is not None else "http"

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.label} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                try:
                    port = int(
                        self.port_file.read_text(encoding="utf-8").strip())
                except ValueError:
                    # The port file caught mid-write (the
                    # known race in these helpers under load):
                    # keep polling rather than erroring.
                    time.sleep(0.05)
                    continue
                self.base_url = f"{self.scheme}://127.0.0.1:{port}"
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


def view_of(entries) -> SyncNode:
    """An in-process node holding exactly `entries`, rebuilt
    through experiment 013's restore primitive: the view a
    mesh anchor is written from (anchor.py writes from a
    node's own view; the holder here is a server process)."""
    view = SyncNode("mesh-view")
    view.restore_to_entries(entries)
    return view


class MeshAnchorDeliverySetup(unittest.TestCase):
    """Shared setup: a temp dir, enrolled identities for the
    anchor secrets (alice the signer, bob another identity,
    carol the witness), and the honest-pair driver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        central = self.tmp / "central"
        channel = RegistrationChannel(
            central / "registry.jsonl", central / "audit.jsonl")
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.carol_secret = channel.enroll_user("carol")
        self.alice_secret_file = self.tmp / "outside" / "alice-secret.txt"
        self.alice_secret_file.write_text(self.alice_secret,
                                          encoding="utf-8")
        self.carol_secret_file = self.tmp / "outside" / "carol-secret.txt"
        self.carol_secret_file.write_text(self.carol_secret,
                                          encoding="utf-8")

    # -- helpers -----------------------------------------------------

    def _start(self, label, node_id, args=(), tls=None):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args), tls=tls)
        server.wait_ready()
        return server

    def _honest_pair(self):
        """Two plain server processes that mutate and converge
        exactly as in case 4/7 (mesh-a writes greeting/mode,
        mesh-b writes region; after one /sync both hold all
        three entries under one state hash)."""
        a = self._start("mesh-a", "mesh-a")
        b = self._start("mesh-b", "mesh-b")
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        node_server.mutate(b.base_url, "region", "north")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["state_hash"],
                         node_server.fetch_state(b.base_url)["state_hash"])
        return a, b

    def _write_anchor(self, view_entries, path, **signing):
        return write_mesh_anchor(view_of(view_entries), path, **signing)

    def _anchor_object(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _signer_args(self, anchor_path=None):
        return [
            "--mesh-anchor", str(anchor_path or self.anchor_path),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(self.alice_secret_file),
        ]

    def _witness_args(self, anchor_path=None):
        return self._signer_args(anchor_path) + [
            "--mesh-anchor-witness", "carol",
            "--mesh-anchor-witness-secret-file",
            str(self.carol_secret_file),
        ]

    def _converged_anchored(self, args, signing, label="mesh-a-anchored"):
        """Case 15's shared shape, parameterized: an honest
        pair converges, the mesh anchor is written over the
        converged state with `signing`, mesh-a is stopped, and
        a fresh process claiming mesh-a is started with the
        given mesh-anchor args and converged by one /sync
        toward the honest peer. Returns (anchored, peer)."""
        a, b = self._honest_pair()
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_path, **signing)
        a.stop()
        anchored = self._start(label, "mesh-a", args)
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        return anchored, b

    def _successor_anchor_object(self, view_entries, scratch_name,
                                 **signing):
        """Extend the server's CURRENT anchor lineage at the
        source: copy the server's anchor file to a scratch
        path and re-anchor over `view_entries` from that
        copy with case 11's write path, so the scratch
        anchor's kept history extends the current one.
        Returns (scratch_path, anchor_object)."""
        scratch = self.tmp / "source" / scratch_name
        scratch.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.anchor_path, scratch)
        self._write_anchor(view_entries, scratch, **signing)
        return scratch, self._anchor_object(scratch)


class MeshAnchorDeliveryTests(MeshAnchorDeliverySetup):

    # Case 20, the core scenario: the 026 story driven
    # forward by a wire delivery instead of a hand-placed
    # file. Converged and anchored under a signed mesh
    # anchor, the honest peer grows; the anchored server's
    # sync refuses anchor_stale and keeps exactly the
    # anchored state; the successor anchor - the same
    # signer, the current anchor's history extended at the
    # source - is delivered over the wire and reports
    # delivered, the server's anchor file byte-for-byte the
    # delivered anchor; /health classifies the not-yet-
    # converged held state anchor_mismatch (case 15's
    # at-rest classification, stated), and the same sync
    # then converges on the grown state.
    def test_stale_sync_then_wire_delivery_converges(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        anchored_hash = node_server.fetch_state(
            anchored.base_url)["state_hash"]

        node_server.mutate(b.base_url, "level", "3")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["reason"], "anchor_stale")
        self.assertEqual(result["self"]["accepted"], 0)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state_hash"], anchored_hash)

        scratch, delivered_obj = self._successor_anchor_object(
            node_server.fetch_entries(b.base_url), "next-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, delivered_obj)
        self.assertEqual(report["status"], "delivered")
        self.assertIsNone(report["reason"])
        self.assertEqual(report["before_count"], 3)
        self.assertEqual(report["after_count"], 4)
        self.assertEqual(report["signer"], "alice")
        self.assertEqual(
            self.anchor_path.read_bytes(), scratch.read_bytes())
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["last_mesh_anchor_delivery"]["status"],
                         "delivered")
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        print("MESH ANCHOR DELIVERY: a sync refused "
              "anchor_stale was unblocked by an anchor "
              "delivered over the wire - no hand placed the "
              "file; /health classifies the not-yet-converged "
              "held state anchor_mismatch, as in case 15")

        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        self.assertEqual(
            result["state_hash"],
            node_server.fetch_state(b.base_url)["state_hash"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["entries"], 4)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 20, the catch-up standing: the anchored server's
    # own mutations never consult the anchor (case 8), so
    # its own growth leaves its /health standing
    # anchor_stale against its own anchor. The anchor over
    # exactly the state it holds - the current lineage
    # extended at the source - delivered over the wire
    # reports delivered, and /health then reports anchored,
    # with no sync having to move.
    def test_delivery_onto_exactly_held_state(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        node_server.mutate(anchored.base_url, "level", "3")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchor_stale")

        _scratch, delivered_obj = self._successor_anchor_object(
            node_server.fetch_entries(anchored.base_url),
            "catchup-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, delivered_obj)
        self.assertEqual(report["status"], "delivered")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(health["entries"], 4)
        print("MESH ANCHOR DELIVERY catch-up: an anchor "
              "pinning exactly the held state delivered onto "
              "it; the server's own growth is anchored "
              "without a sync")

    # Case 20, no-change: a byte-identical mesh anchor
    # delivery reports no_change, writes nothing, and is
    # carried on /health as the last mesh anchor delivery.
    def test_identical_anchor_no_change(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        before = self.anchor_path.read_bytes()
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, self._anchor_object(self.anchor_path))
        self.assertEqual(report["status"], "no_change")
        self.assertIsNone(report["reason"])
        self.assertEqual(self.anchor_path.read_bytes(), before)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(
            health["last_mesh_anchor_delivery"]["status"], "no_change")
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 20, foreign chain: the same identity's genuine
    # signed anchor over a rewritten mesh chain, built in a
    # FRESH lineage (its kept history its own, not an
    # extension of the current anchor's), is refused as
    # anchor_mismatch - signer and signature pass, the head
    # pairs with nothing held and the lineage is not the
    # current anchor's. File, state, and standing unchanged.
    def test_foreign_chain_anchor_refused_mismatch(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        before = self.anchor_path.read_bytes()
        anchored_hash = node_server.fetch_state(
            anchored.base_url)["state_hash"]

        rogue_a = SyncNode("mesh-a")
        rogue_a.mutate("greeting", "pwned")
        rogue_a.mutate("mode", "admin")
        rogue_b = SyncNode("mesh-b")
        rogue_b.mutate("region", "south")
        foreign_path = self.tmp / "foreign" / "mesh-anchor.json"
        foreign_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_anchor(rogue_a.entries() + rogue_b.entries(),
                           foreign_path,
                           signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, self._anchor_object(foreign_path))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_mismatch")
        self.assertEqual(self.anchor_path.read_bytes(), before)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state_hash"], anchored_hash)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR DELIVERY anchor_mismatch: the "
              "same signer's anchor over a foreign chain, in "
              "a fresh lineage, refused - it pairs with "
              "nothing held and succeeds nothing")

    # Case 20, succession: an anchor signed by a DIFFERENT
    # enrolled identity over the very same held state is
    # refused as different_signer, the mesh anchor file
    # byte-for-byte unchanged, and the refusal audit-logged
    # beside it in case 11's shape for the mesh chain.
    def test_different_signer_refused_and_audited(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        before = self.anchor_path.read_bytes()
        bob_path = self.tmp / "bob" / "mesh-anchor.json"
        bob_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           bob_path,
                           signer="bob", secret_hex=self.bob_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, self._anchor_object(bob_path))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "different_signer")
        self.assertEqual(self.anchor_path.read_bytes(), before)
        events = read_anchor_audit(self.anchor_path)
        self.assertTrue(any(
            e.get("event") == "reanchor_rejected"
            and e.get("reason") == "different_signer"
            and e.get("attempted_signer") == "bob"
            and e.get("current_signer") == "alice"
            and e.get("chain") == "sync-mesh"
            for e in events), events)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR DELIVERY different_signer: bob's "
              "anchor over the same state refused at "
              "succession and audit-logged beside the mesh "
              "anchor")

    # Case 20, tampered history: the genuine successor
    # anchor (the current lineage extended over the same
    # head) with one history entry's recorded state hash
    # edited in the delivered object - entry hashes left
    # standing, case 17's tamper shape - is refused as
    # anchor_signature_invalid, before any head judgement.
    def test_tampered_history_signature_invalid(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        before = self.anchor_path.read_bytes()
        _scratch, delivered_obj = self._successor_anchor_object(
            node_server.fetch_entries(b.base_url),
            "tamper-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        delivered_obj["history"][0]["state_hash"] = "f" * 64
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, delivered_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_signature_invalid")
        self.assertEqual(self.anchor_path.read_bytes(), before)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 20, the backward fence, shape (i): an earlier
    # version of the server's own mesh anchor file - the
    # same signed anchor one re-anchoring earlier, its kept
    # history a proper prefix of the current one's -
    # delivered after the anchor has advanced (here, by a
    # wire delivery and a sync, the full lifecycle) is
    # refused as anchor_regression, audit-logged, the
    # current anchor byte-for-byte unchanged.
    def test_backward_fence_earlier_version_of_own_file(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        original_obj = self._anchor_object(self.anchor_path)

        node_server.mutate(b.base_url, "level", "3")
        _scratch, next_obj = self._successor_anchor_object(
            node_server.fetch_entries(b.base_url), "advance-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, next_obj)
        self.assertEqual(report["status"], "delivered")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        advanced_bytes = self.anchor_path.read_bytes()

        report = node_server.deliver_mesh_anchor(
            anchored.base_url, original_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_regression")
        self.assertEqual(self.anchor_path.read_bytes(), advanced_bytes)
        events = read_anchor_audit(self.anchor_path)
        self.assertTrue(any(
            e.get("event") == "reanchor_rejected"
            and e.get("reason") == "anchor_regression"
            for e in events), events)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR DELIVERY anchor_regression (i): "
              "an earlier version of the server's own anchor "
              "file refused after the anchor advanced")

    # Case 20, honest growth past a delivered anchor: the
    # anchored server grows its own state to five entries
    # (its own mutations); an anchor over the INTERMEDIATE
    # four-entry state - genuinely the current anchor's
    # successor in lineage - is refused as anchor_stale,
    # because the held state already stands honestly past
    # what it pins; the anchor over the held head itself
    # then reports delivered.
    def test_honest_growth_past_delivered_anchor_is_stale(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        before = self.anchor_path.read_bytes()
        node_server.mutate(anchored.base_url, "level", "3")
        node_server.mutate(anchored.base_url, "extra", "x")
        held = node_server.fetch_entries(anchored.base_url)
        self.assertEqual(len(held), 5)
        converged = {(e.origin, e.seq) for e in
                     node_server.fetch_entries(b.base_url)}
        first_own = next(e for e in held
                         if (e.origin, e.seq) not in converged)
        intermediate = [e for e in held
                        if (e.origin, e.seq) in converged
                        or e is first_own]
        self.assertEqual(len(intermediate), 4)

        _s1, intermediate_obj = self._successor_anchor_object(
            intermediate, "intermediate-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, intermediate_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_stale")
        self.assertEqual(self.anchor_path.read_bytes(), before)
        print("MESH ANCHOR DELIVERY anchor_stale: an anchor "
              "over an intermediate state refused - the held "
              "state already stands honestly past it")

        _s2, held_obj = self._successor_anchor_object(
            held, "held-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, held_obj)
        self.assertEqual(report["status"], "delivered")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 20, THE LINEAGE FINDING: the deliverer takes
    # the server's current mesh anchor file and re-anchors
    # over the REWRITTEN mesh chain from that file, by case
    # 11's own write path, with the CURRENT signer's secret
    # - a secret every verifier of a signed anchor
    # legitimately holds in this symmetric model. The
    # delivered anchor's history honestly extends the
    # current anchor's and its signature verifies, so it
    # is ACCEPTED: the server ends standing under an
    # anchor over the rewritten chain while still holding
    # the genuine state - /health classifies the standing
    # anchor_mismatch, and the next sync with the honest
    # peer is refused under that name. Stated plainly:
    # custody of the signer's secret is the whole fence at
    # this surface, as case 17's finding recorded for the
    # registry half; the wire adds no weakness, because a
    # hand-placed successor anchor is accepted by the same
    # consultation.
    def test_lineage_successor_over_rewrite_accepted(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        rogue_a = SyncNode("mesh-a")
        rogue_a.mutate("greeting", "pwned")
        rogue_a.mutate("mode", "admin")
        rogue_b = SyncNode("mesh-b")
        rogue_b.mutate("region", "south")
        rogue_entries = rogue_a.entries() + rogue_b.entries()

        scratch, rewrite_obj = self._successor_anchor_object(
            rogue_entries, "rewrite-anchor.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, rewrite_obj)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(self.anchor_path.read_bytes(),
                         scratch.read_bytes())
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["reason"], "anchor_mismatch")
        self.assertEqual(result["self"]["accepted"], 0)
        print("MESH ANCHOR DELIVERY FINDING: a successor "
              "anchor over the REWRITTEN chain, written from "
              "the current anchor file with the current "
              "signer's secret, is ACCEPTED - custody of "
              "that secret is the whole fence at this "
              "surface, exactly as case 17 found for the "
              "registry half")

    # Case 20, unreadable current anchor: with the mesh
    # anchor file corrupted by hand, a delivery that would
    # otherwise stand is refused as anchor_unreadable, the
    # corrupted file left byte-for-byte as it was, /health
    # answering; restoring the genuine file restores
    # normal delivery.
    def test_unreadable_current_anchor_refused(self):
        anchored, b = self._converged_anchored(
            self._signer_args(),
            {"signer": "alice", "secret_hex": self.alice_secret})
        good_bytes = self.anchor_path.read_bytes()
        node_server.mutate(b.base_url, "level", "3")
        _scratch, next_obj = self._successor_anchor_object(
            node_server.fetch_entries(b.base_url),
            "unreadable-next.json",
            signer="alice", secret_hex=self.alice_secret)

        self.anchor_path.write_bytes(b"this is not an anchor\n")
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, next_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_unreadable")
        self.assertEqual(self.anchor_path.read_bytes(),
                         b"this is not an anchor\n")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"],
                         "anchor_unreadable")

        self.anchor_path.write_bytes(good_bytes)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, next_obj)
        self.assertEqual(report["status"], "delivered")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 20, configuration edge: a server started
    # without --mesh-anchor reports a mesh anchor delivery
    # as no_mesh_anchor and is otherwise undisturbed; the
    # same edge in-process on a bare NetworkNode.
    def test_no_mesh_anchor_configured(self):
        plain = self._start("mesh-plain", "mesh-plain")
        anchor_obj = {"format": "PBA-ANCHOR-v1",
                      "chain": "sync-mesh", "origin_heads": {},
                      "state_hash": "0" * 64, "entry_count": 0}
        report = node_server.deliver_mesh_anchor(
            plain.base_url, anchor_obj)
        self.assertEqual(report["status"], "no_mesh_anchor")
        health = node_server.fetch_health(plain.base_url)
        self.assertFalse(health["mesh_anchor_configured"])
        self.assertEqual(health["mesh_anchor_status"], "no_anchor")
        self.assertEqual(
            health["last_mesh_anchor_delivery"]["status"],
            "no_mesh_anchor")
        node_server.mutate(plain.base_url, "greeting", "hello")
        state = node_server.fetch_state(plain.base_url)
        self.assertEqual(state["state"], {"greeting": "hello"})

        direct = node_server.NetworkNode(node_id="mesh-direct")
        report = direct.deliver_mesh_anchor(anchor_obj)
        self.assertEqual(report["status"], "no_mesh_anchor")
        print("MESH ANCHOR DELIVERY no_mesh_anchor: a "
              "server with no mesh anchor configured reports "
              "the edge and is undisturbed")


class UnsignedMeshAnchorDeliveryTests(MeshAnchorDeliverySetup):

    # Case 20, the backward fence, shape (ii), on a
    # head-only server: the mesh anchor is UNSIGNED and no
    # identity is configured. The server's state grows by
    # its own mutations and its anchor is re-anchored by
    # hand over the grown state (the pre-031 lifecycle, an
    # operator act), so the current anchor pins five
    # entries. The ORIGINAL three-entry anchor - pinning
    # an earlier prefix of the very chain the current
    # anchor pins, its entries all still held - delivered
    # over the wire is refused as anchor_regression:
    # refused for where it points, not for how it is
    # signed (it is not signed at all).
    def test_backward_fence_prefix_of_pinned_chain_unsigned(self):
        a, b = self._honest_pair()
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_path)
        original_obj = self._anchor_object(self.anchor_path)
        original_bytes = self.anchor_path.read_bytes()
        a.stop()
        anchored = self._start("mesh-a-anchored", "mesh-a",
                               ["--mesh-anchor", str(self.anchor_path)])
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

        node_server.mutate(anchored.base_url, "level", "3")
        node_server.mutate(anchored.base_url, "extra", "x")
        self._write_anchor(
            node_server.fetch_entries(anchored.base_url),
            self.anchor_path)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertNotEqual(self.anchor_path.read_bytes(),
                            original_bytes)

        report = node_server.deliver_mesh_anchor(
            anchored.base_url, original_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_regression")
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(health["entries"], 5)
        print("MESH ANCHOR DELIVERY anchor_regression (ii): "
              "an unsigned anchor pinning an earlier prefix "
              "of the pinned chain refused on a head-only "
              "server")


class WitnessedMeshAnchorDeliveryTests(MeshAnchorDeliverySetup):

    # Case 20, the witness fence (case 18) at delivery: at
    # a server configured with mesh signer alice and mesh
    # witness carol, the grown state's successor anchor
    # signed by alice ALONE is refused as
    # anchor_witness_invalid; countersigned by bob (an
    # enrolled identity who is not the witness) it is
    # refused under the same name; countersigned by carol
    # it is delivered, and the sync it unblocks converges.
    def test_witness_fence_at_delivery(self):
        anchored, b = self._converged_anchored(
            self._witness_args(),
            {"signer": "alice", "secret_hex": self.alice_secret,
             "witness": "carol", "witness_secret_hex": self.carol_secret})
        before = self.anchor_path.read_bytes()

        node_server.mutate(b.base_url, "level", "3")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertEqual(result["self"]["reason"], "anchor_stale")
        grown = node_server.fetch_entries(b.base_url)

        _s1, alone_obj = self._successor_anchor_object(
            grown, "witness-alone.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, alone_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self.assertEqual(self.anchor_path.read_bytes(), before)

        _s2, bob_obj = self._successor_anchor_object(
            grown, "witness-bob.json",
            signer="alice", secret_hex=self.alice_secret,
            witness="bob", witness_secret_hex=self.bob_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, bob_obj)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self.assertEqual(self.anchor_path.read_bytes(), before)
        print("MESH ANCHOR DELIVERY anchor_witness_invalid: "
              "a signer-alone successor and a wrongly "
              "countersigned successor both refused at a "
              "witness-configured server")

        _s3, carol_obj = self._successor_anchor_object(
            grown, "witness-carol.json",
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, carol_obj)
        self.assertEqual(report["status"], "delivered")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR DELIVERY witnessed: the "
              "configured witness's countersigned successor "
              "delivered, and the sync it unblocked converged")


class TlsMeshAnchorDeliveryTests(MeshAnchorDeliverySetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds",
            ["mesh-a", "mesh-b", "test-client"])
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls(self, node_id):
        cert = self.creds.nodes[node_id]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 20 over experiment 014's protected transport,
    # with a signed mesh anchor: two TLS processes
    # converge; one restarts anchored; the peer grows and
    # the sync refuses anchor_stale; the successor anchor
    # is delivered over HTTPS and reports delivered; the
    # same sync then converges over the protected link.
    def test_delivery_over_protected_link(self):
        a = self._start("tls-a", "mesh-a", tls=self._tls("mesh-a"))
        b = self._start("tls-b", "mesh-b", tls=self._tls("mesh-b"))
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        node_server.mutate(b.base_url, "region", "north")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_path,
                           signer="alice", secret_hex=self.alice_secret)
        a.stop()

        anchored = self._start("tls-a-anchored", "mesh-a",
                               self._signer_args(), tls=self._tls("mesh-a"))
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

        node_server.mutate(b.base_url, "level", "3")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertEqual(result["self"]["reason"], "anchor_stale")

        _scratch, next_obj = self._successor_anchor_object(
            node_server.fetch_entries(b.base_url), "tls-next.json",
            signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_mesh_anchor(
            anchored.base_url, next_obj)
        self.assertEqual(report["status"], "delivered")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["entries"], 4)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(
            health["last_mesh_anchor_delivery"]["status"], "delivered")
        print("MESH ANCHOR DELIVERY (mutual TLS): the "
              "successor anchor delivered over the protected "
              "link; the stale-refused sync converged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
