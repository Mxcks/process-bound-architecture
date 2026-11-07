"""
Automated tests for one server pushing its standing registry
record to another server (tests/test-cases/sync-mesh.md,
case 16), built in experiment 027.

Case 14 (experiment 025) gave a running server a wire
delivery surface - POST /deliver-snapshot, judged exactly
as strictly as a file delivery - and stated its remaining
limit in the same breath: the pushing peer in every case 14
test is a credential-holding node CLIENT driving the client
helpers; no server process ever pushes its standing record
to another server, so two running servers cannot bring each
other current on the registry side even though their mesh
sides sync. This suite asserts the server-to-server close:

- The receiving side is case 14's surface, unmodified. The
  sending side is one new driven endpoint on the pushing
  server - POST /push-snapshot with payload {"peer": url},
  in the exact style of /sync - and a new client helper
  (node_server.push_snapshot, returning the (status, body)
  pair as fetch_policy does, because a push's refusal can
  ride a non-200 status and the caller must see its body).
  Driven, the pushing server sends the entries its
  PolicyNode STANDS ON (its loaded, verified entries) to the
  peer's /deliver-snapshot; the peer judges and applies them
  under exactly case 14's rules; the push's HTTP 200 report
  carries the peer's delivery report under "delivery" with
  "status" equal to the peer's delivery status, and the
  pushing server's /health records the push as
  last_snapshot_push.
- The end-to-end story, over two running plaintext servers:
  both start over copies of the same 2-entry record, each
  consulting its own anchor file; alice revokes, the anchor
  lifecycle is followed for both servers, the grown record
  reaches server A through A's own accepted case 14
  delivery, and ONE driven push from A delivers it to B -
  never restarted, its file never copied by hand. B flips
  to case 12's clean 410 policy_revoked, bob's policy keeps
  serving, B's file is byte-for-byte the delivered record,
  and the pair's mesh surface still syncs.
- A push to a peer already standing on the identical record
  is case 14's no_change - not an error, not a rewrite.
- Refusals across servers, each reported in the push result
  the sender sees, each leaving the receiving server's file
  and view unchanged: snapshot_regression (the peer is ahead
  and the push is exactly an earlier prefix of its chain),
  anchor_stale (the record reached A with A's lifecycle
  followed, but the peer's anchor still stands over the old
  head - then, once the lifecycle is completed for the peer,
  the same push delivers), and anchor_mismatch (an
  anchorless server accepts experiment 012's rewritten
  record and pushes it to an anchored peer: the rewrite does
  not spread).
- The full flow over mutual TLS with identity binding
  (experiments 014/017) and a signed anchor (experiment
  019): A accepts the revocation delivery with the
  same-signer lifecycle, then pushes over HTTPS to B;
  delivered, 410 over the protected link, B's file is the
  delivered record.
- The identity refusal across servers, at the transport,
  exactly as in experiment 017: a pushing server started in
  experiment 014 mode (mutual TLS, binding OFF) claiming a
  node id its certificate was NOT issued for is driven to
  push to a peer with binding on; the peer's accepting-side
  check refuses before case 14's endpoint runs, and the
  push is reported on HTTP 502 with error identity_mismatch
  naming the claimed and certified ids; the peer never even
  records a delivery.
- A mesh-only pushing server reports no_snapshot (HTTP 200,
  delivery null) and is undisturbed.

Process cleanup is part of the contract, as in experiments
007-026: every spawned server is registered with addCleanup
the moment it starts, and no leg ever runs more than two
server processes at once. Test credentials are generated
inside the tests at runtime into the test's temporary
directory; the anchor signers' secrets are enrollments
issued at runtime and never leave the test's channel calls.
No credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_server_to_server_delivery.py"
    python3 packages/synchronization/test_server_to_server_delivery.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

import base64
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
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def _load_registry_anchor():
    """Load experiment 012's REGISTRY anchor module by file path
    under a private name, the way policy_node.py and experiment
    025's suite do: two packages ship an anchor.py, and in a
    shared discovery process `import anchor` may already have
    resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_server_push_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
BOB2_CODE = b"policy: allow if caller.tier == 'guest-friend'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_snapshot_wire_transport.py's helper."""

    def __init__(self, test, workdir: Path, name: str, args: list[str],
                 tls=None, node_id: str | None = None):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        self.log_path = workdir / f"{name}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id if node_id is not None else name,
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
                    f"server {self.name} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"{self.scheme}://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError,
                        ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.name} not ready in {timeout}s")

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


def entries_of(path: Path) -> list[dict]:
    """The Gate 1 log entries of a snapshot file, as the JSON
    dicts a wire delivery carries."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ServerPushSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry through
    the real Gate 1 channel. Servers get only snapshot copies +
    payloads; each server consults its OWN anchor file, and the
    lifecycle helpers write every server's anchor, as
    out-of-band anchor delivery would reach each."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, central / "audit.jsonl")
        self.alice_secret = self.channel.enroll_user("alice")
        self.bob_secret = self.channel.enroll_user("bob")
        self.alice_record, created = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE))
        assert created
        self.bob_record, created = self.channel.register(
            author="bob", name="guest-access", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access", "1.0.0", BOB_CODE))
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
        }), encoding="utf-8")

    # -- helpers -----------------------------------------------------

    def _anchor_path(self, name: str) -> Path:
        """One server's own anchor file, outside the registry's
        directory, as in experiment 012."""
        return self.tmp / f"{name}-outside" / "registry-anchor.json"

    def _anchor_for(self, *names: str):
        """Write the anchor over the channel's current log for
        each named server - the anchor lifecycle, performed per
        server as out-of-band anchor delivery would reach
        each."""
        for name in names:
            write_anchor(self.registry_log, self._anchor_path(name))

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, name: str, snapshot: Path, extra=(),
                     anchored: bool = True, payloads: Path | None = None):
        args = [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads if payloads is not None
                              else self.payloads_file),
            "--serve-policies",
        ]
        if anchored:
            args += ["--anchor", str(self._anchor_path(name))]
        args += list(extra)
        return args

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

    def _rewritten_log(self) -> Path:
        """Experiment 012's negative control: mallory's registry,
        registered from genesis with different content.
        Internally consistent - it passes Gate 2's own snapshot
        verification - and not the anchored chain."""
        evil_dir = self.tmp / "evil-central"
        evil_log = evil_dir / "registry.jsonl"
        channel = RegistrationChannel(evil_log, evil_dir / "audit.jsonl")
        secret = channel.enroll_user("mallory")
        record, created = channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE,
            signature=sign_library(secret, "mallory", "member-access",
                                   "1.0.0", MALLORY_CODE))
        assert created
        return evil_log

    def _pair(self, name_a: str, name_b: str):
        """Two plaintext servers over copies of the same record,
        each with its own unsigned anchor over it."""
        self._anchor_for(name_a, name_b)
        snap_a = self._snapshot_copy(name_a)
        snap_b = self._snapshot_copy(name_b)
        a = ServerProcess(self, self.tmp, name_a,
                          self._server_args(name_a, snap_a))
        a.wait_ready()
        b = ServerProcess(self, self.tmp, name_b,
                          self._server_args(name_b, snap_b))
        b.wait_ready()
        return a, b, snap_a, snap_b

    def _assert_revoked_over_wire(self, base_url: str):
        """Case 12's clean refusal from the given process, plus
        the non-revoked policy still serving."""
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        self.assertIn("revoked", body["detail"])
        self.assertNotIn("code_base64", body)
        status, body = node_server.fetch_policy(
            base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)

    def _assert_alice_serves(self, base_url: str):
        """The stale window, asserted honestly: this process
        does not yet know of the revocation."""
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)


class PlaintextServerPushTests(ServerPushSetup):

    # Case 16, the end-to-end story: the revocation reaches A
    # through A's own accepted case 14 delivery; ONE driven
    # push from A delivers it to B. Then a second push is
    # no_change, and the pair's mesh surface still syncs.
    def test_server_push_delivers_revocation_end_to_end(self):
        a, b, snap_a, snap_b = self._pair("push-a", "push-b")
        self._assert_alice_serves(a.base_url)
        self._assert_alice_serves(b.base_url)
        health = node_server.fetch_health(a.base_url)
        self.assertIsNone(health["last_snapshot_push"])

        # The revocation lands in A's record through A's own
        # accepted case 14 delivery, lifecycle followed for A.
        self._revoke_alice()
        self._anchor_for("push-a", "push-b")
        report = node_server.deliver_snapshot(
            a.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        # B does not know yet: the stale window, on B.
        self._assert_alice_serves(b.base_url)
        b_file_before = snap_b.read_bytes()

        # ONE driven push, server to server.
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["node_id"], "push-a")
        self.assertEqual(push["peer"], b.base_url)
        self.assertEqual(push["status"], "delivered")
        delivery = push["delivery"]
        self.assertEqual(delivery["status"], "delivered")
        self.assertEqual(delivery["node_id"], "push-b")
        self.assertEqual(delivery["before_count"], 2)
        self.assertEqual(delivery["after_count"], 3)
        self.assertEqual(delivery["loaded_entries"], 3)
        print("SERVER PUSH (plaintext): A pushed its standing "
              "record to B; B applied it under case 14's rules - "
              "delivered 2 -> 3, no restart, no file copy")

        # B's file IS the delivered record; B's wire flipped.
        self.assertNotEqual(snap_b.read_bytes(), b_file_before)
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 3)
        self.assertEqual(health_b["last_snapshot_delivery"]["status"],
                         "delivered")
        # The push is recorded on the SENDER's health surface.
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(health_a["last_snapshot_push"]["status"],
                         "delivered")
        self.assertEqual(
            health_a["last_snapshot_push"]["delivery"]["node_id"],
            "push-b")

        # A second push: B already stands on the identical
        # record - no_change, not an error, not a rewrite.
        b_file_now = snap_b.read_bytes()
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "no_change")
        self.assertIsNone(push["delivery"]["loaded_entries"])
        self.assertEqual(snap_b.read_bytes(), b_file_now)
        print("SERVER PUSH no_change: a push to a peer already "
              "standing on the identical record rewrites nothing")

        # The pair's mesh surface is undisturbed (case 4).
        node_server.mutate(a.base_url, "mode", "normal")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        state_b = node_server.fetch_state(b.base_url)
        self.assertEqual(state_b["state"]["mode"], "normal")

    # Case 16: a push that would move the peer backward is
    # refused as snapshot_regression, reported in the push
    # result; the ahead peer and the sender are unchanged.
    def test_push_backward_to_ahead_peer_refused_snapshot_regression(self):
        a, b, snap_a, snap_b = self._pair("back-a", "back-b")
        self._revoke_alice()
        self._anchor_for("back-a", "back-b")
        report = node_server.deliver_snapshot(
            a.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(push["status"], "delivered")
        self._assert_revoked_over_wire(b.base_url)

        # B moves AHEAD of A: bob's second registration is
        # delivered to B alone, through B's own case 14
        # delivery, lifecycle followed for B.
        record2, created = self.channel.register(
            author="bob", name="guest-access-2", version="1.0.0",
            code=BOB2_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access-2", "1.0.0", BOB2_CODE))
        assert created
        self._anchor_for("back-b")
        report = node_server.deliver_snapshot(
            b.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 4)
        b_file_ahead = snap_b.read_bytes()
        a_file_before = snap_a.read_bytes()

        # A's push carries A's 3-entry record: exactly an
        # earlier prefix of the 4-entry chain B stands on.
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "snapshot_regression")
        self.assertEqual(push["delivery"]["before_count"], 4)
        self.assertEqual(push["delivery"]["after_count"], 3)
        print("SERVER PUSH snapshot_regression: A's older "
              "record refused by the ahead peer under the "
              "backward name, reported in the push result")
        # B unchanged: file, standing, wire.
        self.assertEqual(snap_b.read_bytes(), b_file_ahead)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 4)
        self.assertEqual(health_b["last_snapshot_delivery"]["reason"],
                         "snapshot_regression")
        self._assert_revoked_over_wire(b.base_url)
        # A unchanged.
        self.assertEqual(snap_a.read_bytes(), a_file_before)
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(health_a["snapshot_entries"], 3)

    # Case 16: a push whose anchor lifecycle the peer cannot
    # accept is refused as anchor_stale in the push result;
    # once the lifecycle is completed for the peer, the same
    # push delivers.
    def test_push_with_peer_anchor_behind_refused_anchor_stale(self):
        a, b, snap_a, snap_b = self._pair("stale-a", "stale-b")
        self._revoke_alice()
        # The lifecycle is followed for A ONLY: B's anchor
        # still stands over the 2-entry head.
        self._anchor_for("stale-a")
        report = node_server.deliver_snapshot(
            a.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        b_file_before = snap_b.read_bytes()

        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"], "anchor_stale")
        print("SERVER PUSH anchor_stale: the peer's anchor "
              "still stands over the old head; the push is "
              "refused under the lifecycle's own name")
        # B unchanged: file untouched, stale window serving.
        self.assertEqual(snap_b.read_bytes(), b_file_before)
        self._assert_alice_serves(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertEqual(health_b["last_snapshot_delivery"]["reason"],
                         "anchor_stale")

        # The lifecycle completed for B: the same push lands.
        self._anchor_for("stale-b")
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(b.base_url)

    # Case 16: an anchorless server accepts the rewritten
    # record (no consultation - stated plainly, as in cases
    # 5/8), but its push cannot spread the rewrite to an
    # anchored peer: anchor_mismatch, reported in the push
    # result, the anchored peer unchanged.
    def test_unanchored_sender_cannot_spread_rewrite_to_anchored_peer(self):
        evil_log = self._rewritten_log()
        # Sender: NO anchor. It accepts the rewrite through its
        # own case 14 delivery, exactly as an anchorless node
        # accepts what verifies as a chain. Its payloads file
        # also holds mallory's code, so the acceptance is
        # servable, not merely recorded.
        payloads_plus = self.tmp / "payloads-plus.json"
        payloads_plus.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
            self._mallory_hash(evil_log): MALLORY_CODE.hex(),
        }), encoding="utf-8")
        snap_s = self._snapshot_copy("spread-s")
        sender = ServerProcess(
            self, self.tmp, "spread-s",
            self._server_args("spread-s", snap_s, anchored=False,
                              payloads=payloads_plus))
        sender.wait_ready()
        report = node_server.deliver_snapshot(
            sender.base_url, entries_of(evil_log))
        self.assertEqual(report["status"], "delivered")
        # The sender now stands on (and serves) mallory's
        # record under alice's policy name - the acceptance an
        # anchorless node makes, stated, not hidden.
        status, body = node_server.fetch_policy(
            sender.base_url, self._mallory_hash(evil_log))
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]),
                         MALLORY_CODE)

        # Peer: anchored on the genuine chain.
        self._anchor_for("spread-b")
        snap_b = self._snapshot_copy("spread-b")
        b = ServerProcess(self, self.tmp, "spread-b",
                          self._server_args("spread-b", snap_b))
        b.wait_ready()
        b_file_before = snap_b.read_bytes()

        status, push = node_server.push_snapshot(
            sender.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"], "anchor_mismatch")
        print("SERVER PUSH anchor_mismatch: a rewrite accepted "
              "by an anchorless server does not spread to an "
              "anchored peer")
        self.assertEqual(snap_b.read_bytes(), b_file_before)
        self._assert_alice_serves(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertEqual(health_b["last_snapshot_delivery"]["reason"],
                         "anchor_mismatch")

    def _mallory_hash(self, evil_log: Path) -> str:
        return entries_of(evil_log)[0]["policy_hash"]

    # Case 16: a mesh-only pushing server has no record to
    # push - no_snapshot on HTTP 200, delivery null, and the
    # server is undisturbed.
    def test_mesh_only_sender_reports_no_snapshot(self):
        # The target's anchor is written before its process
        # starts, as in _pair.
        self._anchor_for("push-target")
        b = ServerProcess(self, self.tmp, "push-target",
                          self._server_args(
                              "push-target",
                              self._snapshot_copy("push-target")))
        b.wait_ready()
        sender = ServerProcess(self, self.tmp, "push-mesh-only", [])
        sender.wait_ready()
        status, push = node_server.push_snapshot(
            sender.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "no_snapshot")
        self.assertIsNone(push["delivery"])
        health = node_server.fetch_health(sender.base_url)
        self.assertEqual(health["last_snapshot_push"]["status"],
                         "no_snapshot")
        node_server.mutate(sender.base_url, "mode", "normal")
        state = node_server.fetch_state(sender.base_url)
        self.assertEqual(state["state"]["mode"], "normal")


class SignedTlsServerPushTests(ServerPushSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds",
            ["push-tls-a", "push-tls-b", "test-client", "push-tls-other"])
        client = self.creds.nodes["test-client"]
        # The driving client claims the node id its certificate
        # was issued for; the receiving servers run with
        # identity binding on and check the claim (case 11).
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _signed_anchor(self, name: str, entries: int | None = None):
        """Alice's signed anchor over the channel's current log,
        for the named server, plus that server's secret file."""
        anchor = self._anchor_path(name)
        write_anchor(self.registry_log, anchor,
                     signer="alice", secret_hex=self.alice_secret)
        secret_file = anchor.parent / "anchor-secret.txt"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(self.alice_secret, encoding="utf-8")
        return secret_file

    def _tls_args(self, name: str, snapshot: Path, secret_file: Path,
                  extra=()):
        return self._server_args(name, snapshot, extra=(
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(secret_file),
            "--tls-bind-identity",
            *extra,
        ))

    def _tls(self, node_name: str):
        cert = self.creds.nodes[node_name]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 16 over the protected link, with identity binding
    # and a signed anchor: A accepts the revocation delivery
    # with the same-signer lifecycle followed for both
    # servers; ONE driven push over HTTPS delivers A's record
    # to B; B refuses 410 over the protected link and its
    # file is the delivered record.
    def test_signed_tls_server_push_full_flow(self):
        secret_a = self._signed_anchor("push-tls-a")
        secret_b = self._signed_anchor("push-tls-b")
        snap_a = self._snapshot_copy("push-tls-a")
        snap_b = self._snapshot_copy("push-tls-b")
        a = ServerProcess(self, self.tmp, "push-tls-a",
                          self._tls_args("push-tls-a", snap_a, secret_a),
                          tls=self._tls("push-tls-a"))
        a.wait_ready()
        b = ServerProcess(self, self.tmp, "push-tls-b",
                          self._tls_args("push-tls-b", snap_b, secret_b),
                          tls=self._tls("push-tls-b"))
        b.wait_ready()
        self._assert_alice_serves(b.base_url)

        self._revoke_alice()
        # Same-signer lifecycle, re-performed for both servers'
        # anchors over the grown log.
        self._signed_anchor("push-tls-a")
        self._signed_anchor("push-tls-b")
        report = node_server.deliver_snapshot(
            a.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        self._assert_alice_serves(b.base_url)

        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(push["delivery"]["loaded_entries"], 3)
        print("SERVER PUSH (mutual TLS, identity binding, "
              "signed anchor): A's push delivered to B over "
              "HTTPS; 410 over the protected link")
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 3)
        self.assertEqual(health_b["last_snapshot_delivery"]["status"],
                         "delivered")
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(health_a["last_snapshot_push"]["status"],
                         "delivered")

    # Case 16: the identity refusal across servers, at the
    # transport. The pushing server runs in experiment 014
    # mode - mutual TLS, binding OFF - claiming "push-false"
    # while presenting the certificate issued for
    # "push-tls-other" (the false-claiming process case 11's
    # notes describe). The receiving server runs with binding
    # on and refuses the connection's claim before case 14's
    # endpoint runs; the push is reported 502
    # identity_mismatch naming claimed and certified, and the
    # peer never even records a delivery.
    def test_false_claiming_pusher_refused_identity_mismatch(self):
        secret_b = self._signed_anchor("push-tls-b")
        snap_b = self._snapshot_copy("push-tls-b")
        b = ServerProcess(self, self.tmp, "push-tls-b",
                          self._tls_args("push-tls-b", snap_b, secret_b),
                          tls=self._tls("push-tls-b"))
        b.wait_ready()
        b_file_before = snap_b.read_bytes()

        # The impostor needs a snapshot of its own to have a
        # record to push; its own accepting side has binding
        # off, so the driving client's matching claim passes,
        # exactly as in experiment 014 mode.
        snap_i = self._snapshot_copy("impostor")
        impostor = ServerProcess(
            self, self.tmp, "impostor",
            self._server_args("impostor", snap_i, anchored=False),
            tls=self._tls("push-tls-other"), node_id="push-false")
        impostor.wait_ready()

        status, push = node_server.push_snapshot(
            impostor.base_url, b.base_url)
        self.assertEqual(status, 502)
        self.assertEqual(push["error"], "identity_mismatch")
        self.assertEqual(push["claimed"], "push-false")
        self.assertEqual(push["certified"], "push-tls-other")
        print("SERVER PUSH identity_mismatch: a pusher "
              "claiming an id its certificate was not issued "
              "for refused at the binding peer's transport; "
              "the refusal names claimed and certified ids")
        # The peer never even recorded a delivery; file, view,
        # and standing unchanged.
        self.assertEqual(snap_b.read_bytes(), b_file_before)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertIsNone(health_b["last_snapshot_delivery"])
        self._assert_alice_serves(b.base_url)
        # And the refusal is in the peer's log, as in case 11.
        self.assertIn("identity_mismatch", b.log_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
