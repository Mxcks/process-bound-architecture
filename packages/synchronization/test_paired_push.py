"""
Automated tests for one server pushing its record AND its
anchor to a peer, the pair judged together at the peer
(tests/test-cases/sync-mesh.md, case 19), built in
experiment 030.

Case 16 (experiment 027) gave a server a push for the record
alone and stated its limit in the same breath: the push
transports the record, not the anchor, so a push to a peer
whose anchor has not advanced refuses anchor_stale there,
and completing the lifecycle is an out-of-band act no push
performs. Case 17 (experiment 028) gave a running server a
surface that RECEIVES its anchor over the wire, alone or
paired with the record it blesses - but the driving end of
that surface is a credential-holding client: no server
sends its own anchor onward. This suite asserts the
server-held close of that gap:

- The receiving side is case 17's paired surface,
  unmodified. The sending side is one new driven endpoint
  on the pushing server - POST /push-anchor with payload
  {"peer": url}, in the exact style of /push-snapshot - and
  a new client helper (node_server.push_anchor, returning
  the (status, body) pair as push_snapshot does). Driven,
  the pushing server sends the entries its PolicyNode
  STANDS ON (case 16's rule) and the anchor object its own
  anchor file holds (for the anchor the file IS the
  standing: the process's PolicyNode consults that very
  file) to the peer's /deliver-anchor in its paired form;
  the peer judges the pair together under case 17's whole
  discipline - succession, the backward fence, signature
  and kept history, case 18's witness judgement where
  configured, pairing - and the push's HTTP 200 report
  carries the peer's paired delivery report under
  "delivery" with "status" equal to it; the pushing
  server's /health records the push as last_anchor_push.
- The end-to-end story, over two running plaintext servers
  with SIGNED anchors: the revocation lands at A through
  A's own accepted case 17 paired delivery (the lifecycle
  followed for A alone); a record-only push from A still
  refuses anchor_stale at B (the control); ONE paired push
  brings B fully current - B's snapshot file byte-for-byte
  the delivered record AND B's anchor file byte-for-byte
  the anchor A sent, 410 over B's wire, bob serving - and a
  second paired push is the pair's no_change, rewriting
  neither of B's files.
- Refusals across servers, each reported in the push result
  the sender sees, each leaving both of the peer's files
  and its serving unchanged: different_signer (the sender's
  anchor signed by another identity than the peer's
  current signer), anchor_regression (the peer's anchor is
  a later version over the same head than the sender's),
  and anchor_witness_invalid (the peer witness-configured,
  the sender's anchor signer-alone - then delivered once
  the sender's anchor is countersigned by the configured
  witness, the lifecycle completed at the sender).
- The sender's own shapes: a pushing process with a
  PolicyNode but NO anchor configured pushes the record
  only, through case 14's surface exactly as case 16 does,
  the outcome recorded as its last anchor push; a mesh-only
  sender reports no_snapshot; a sender whose anchor file
  cannot be read at push time reports 502 "own anchor
  unreadable" and makes no contact with the peer.
- The full paired flow over mutual TLS with identity
  binding (experiments 014/017) and a signed anchor, and
  the identity refusal at the transport: a pushing server
  claiming an id its certificate was not issued for is
  refused before case 17's endpoint runs, reported on 502
  identity_mismatch, the peer recording no anchor delivery.

Process cleanup is part of the contract, as in experiments
007-029: every spawned server is registered with addCleanup
the moment it starts, and no leg ever runs more than two
server processes at once. Test credentials are generated
inside the tests at runtime into the test's temporary
directory; the anchor signers' and witness's secrets are
enrollments issued at runtime and never leave the test's
channel calls. No credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_paired_push.py"
    python3 packages/synchronization/test_paired_push.py

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
    027's suite do: two packages ship an anchor.py, and in a
    shared discovery process `import anchor` may already have
    resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_paired_push_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_server_to_server_delivery.py's helper."""

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


def anchor_of(path: Path) -> dict:
    """An anchor file's content as the parsed anchor object a
    wire delivery carries."""
    return json.loads(path.read_text(encoding="utf-8"))


class PairPushSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry through
    the real Gate 1 channel, carol enrolled as the witness of
    case 18. Servers get only snapshot copies + payloads; each
    server consults its OWN anchor file."""

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
        self.carol_secret = self.channel.enroll_user("carol")
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

    def _secret_file(self, name: str, filename: str, secret: str) -> Path:
        """A secret file beside the named server's anchor, in
        the experiment 019/029 CLI style (the server reads the
        secret from the file; the secret itself never rides a
        command line)."""
        path = self._anchor_path(name).parent / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        return path

    def _signed_anchor_for(self, *names: str, witness: bool = False):
        """Alice's signed anchor over the channel's current log,
        for each named server - the anchor lifecycle, performed
        per server as out-of-band anchor delivery would reach
        each. With witness=True the write is also countersigned
        by carol (case 18)."""
        for name in names:
            kwargs = {}
            if witness:
                kwargs = {"witness": "carol",
                          "witness_secret_hex": self.carol_secret}
            write_anchor(self.registry_log, self._anchor_path(name),
                         signer="alice", secret_hex=self.alice_secret,
                         **kwargs)

    def _anchor_for(self, *names: str):
        """Unsigned anchors over the channel's current log, for
        each named server (the experiment 012 lifecycle)."""
        for name in names:
            write_anchor(self.registry_log, self._anchor_path(name))

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, name: str, snapshot: Path, extra=(),
                     anchored: bool = True, signed: bool = False,
                     witness: bool = False,
                     payloads: Path | None = None):
        args = [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads if payloads is not None
                              else self.payloads_file),
            "--serve-policies",
        ]
        if anchored:
            args += ["--anchor", str(self._anchor_path(name))]
        if signed:
            args += [
                "--anchor-signer", "alice",
                "--anchor-secret-file", str(self._secret_file(
                    name, "anchor-secret.txt", self.alice_secret)),
            ]
        if witness:
            args += [
                "--anchor-witness", "carol",
                "--anchor-witness-secret-file", str(self._secret_file(
                    name, "anchor-witness-secret.txt",
                    self.carol_secret)),
            ]
        args += list(extra)
        return args

    def _signed_pair(self, name_a: str, name_b: str):
        """Two plaintext servers over copies of the same record,
        each with its own alice-signed anchor over it and each
        configured for that signer."""
        self._signed_anchor_for(name_a, name_b)
        snap_a = self._snapshot_copy(name_a)
        snap_b = self._snapshot_copy(name_b)
        a = ServerProcess(self, self.tmp, name_a,
                          self._server_args(name_a, snap_a, signed=True))
        a.wait_ready()
        b = ServerProcess(self, self.tmp, name_b,
                          self._server_args(name_b, snap_b, signed=True))
        b.wait_ready()
        return a, b, snap_a, snap_b

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

    def _deliver_pair_to(self, base_url: str, anchor_path: Path):
        """Case 17's paired delivery, driven by the client
        helpers as in experiment 028: the grown record plus
        the anchor file's current object."""
        return node_server.deliver_anchor(
            base_url, anchor_of(anchor_path),
            entries_of(self.registry_log))

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


class PlaintextPairPushTests(PairPushSetup):

    # Case 19, the end-to-end story: the revocation reaches A
    # through A's own accepted case 17 paired delivery; the
    # record-only push still refuses anchor_stale at B (the
    # control); ONE paired push brings B fully current - both
    # files - and a second paired push is the pair's
    # no_change, rewriting neither file.
    def test_paired_push_brings_peer_fully_current(self):
        a, b, snap_a, snap_b = self._signed_pair("pair-a", "pair-b")
        self._assert_alice_serves(a.base_url)
        self._assert_alice_serves(b.base_url)
        health = node_server.fetch_health(a.base_url)
        self.assertIsNone(health["last_anchor_push"])

        # The revocation lands at A through A's own accepted
        # case 17 paired delivery, lifecycle followed for A
        # alone: A's anchor is re-written over the grown
        # record by the same signer before the pair travels.
        self._revoke_alice()
        self._signed_anchor_for("pair-a")
        report = self._deliver_pair_to(a.base_url,
                                       self._anchor_path("pair-a"))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        # B does not know yet: the stale window, on B.
        self._assert_alice_serves(b.base_url)
        b_snap_before = snap_b.read_bytes()
        b_anchor_before = self._anchor_path("pair-b").read_bytes()

        # The control, in the same setup: the record alone
        # cannot advance B's anchor - case 16's push refuses
        # anchor_stale, and B is unchanged.
        status, push = node_server.push_snapshot(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"], "anchor_stale")
        self.assertEqual(snap_b.read_bytes(), b_snap_before)
        self.assertEqual(self._anchor_path("pair-b").read_bytes(),
                         b_anchor_before)
        self._assert_alice_serves(b.base_url)
        print("PAIRED PUSH control: the record-only push still "
              "refuses anchor_stale at B - the record alone "
              "cannot advance the peer's anchor")

        # ONE driven paired push, server to server.
        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["node_id"], "pair-a")
        self.assertEqual(push["peer"], b.base_url)
        self.assertEqual(push["status"], "delivered")
        delivery = push["delivery"]
        self.assertEqual(delivery["node_id"], "pair-b")
        self.assertEqual(delivery["status"], "delivered")
        self.assertTrue(delivery["paired"])
        self.assertEqual(delivery["snapshot_status"], "delivered")
        self.assertEqual(delivery["loaded_entries"], 3)
        print("PAIRED PUSH (plaintext, signed anchors): A "
              "pushed its record AND its anchor to B; B judged "
              "the pair together and applied both - delivered, "
              "no restart, no hand at B")

        # BOTH of B's files are the delivered pair: the
        # snapshot is the delivered record, and the anchor is
        # byte-for-byte the anchor A sent - which is A's own
        # anchor file, the anchor format being one
        # serialization.
        self.assertNotEqual(snap_b.read_bytes(), b_snap_before)
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self.assertNotEqual(self._anchor_path("pair-b").read_bytes(),
                            b_anchor_before)
        self.assertEqual(self._anchor_path("pair-b").read_bytes(),
                         self._anchor_path("pair-a").read_bytes())
        self._assert_revoked_over_wire(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 3)
        self.assertEqual(health_b["last_anchor_delivery"]["status"],
                         "delivered")
        # The push is recorded on the SENDER's health surface.
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(health_a["last_anchor_push"]["status"],
                         "delivered")
        self.assertEqual(
            health_a["last_anchor_push"]["delivery"]["node_id"],
            "pair-b")

        # A second paired push: B already stands on the
        # identical record under the identical anchor - the
        # pair's no_change, and NEITHER file is rewritten.
        b_snap_now = snap_b.read_bytes()
        b_anchor_now = self._anchor_path("pair-b").read_bytes()
        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "no_change")
        self.assertEqual(push["delivery"]["snapshot_status"],
                         "no_change")
        self.assertIsNone(push["delivery"]["loaded_entries"])
        self.assertEqual(snap_b.read_bytes(), b_snap_now)
        self.assertEqual(self._anchor_path("pair-b").read_bytes(),
                         b_anchor_now)
        print("PAIRED PUSH no_change: a paired push to a peer "
              "already current under the identical anchor "
              "rewrites neither of its files")

    # Case 19: succession across servers. The sender's anchor
    # is signed by BOB over the same genuine record; the
    # peer's current anchor is alice's. The pair's record
    # half is identical, so the anchor half is judged at
    # succession: different_signer, in the push result, the
    # refusal audit-logged beside the peer's anchor, and the
    # peer unchanged in both files and on the wire.
    def test_paired_push_different_signer_refused(self):
        # Peer B: alice-signed anchor, configured for alice.
        self._signed_anchor_for("signer-b")
        snap_b = self._snapshot_copy("signer-b")
        b = ServerProcess(self, self.tmp, "signer-b",
                          self._server_args("signer-b", snap_b,
                                            signed=True))
        b.wait_ready()
        # Sender S: the same genuine record, but ITS anchor is
        # signed by bob, and S is configured for bob - S's own
        # consultation passes under its own signer.
        write_anchor(self.registry_log, self._anchor_path("signer-s"),
                     signer="bob", secret_hex=self.bob_secret)
        snap_s = self._snapshot_copy("signer-s")
        args_s = [
            "--snapshot", str(snap_s),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self._anchor_path("signer-s")),
            "--anchor-signer", "bob",
            "--anchor-secret-file", str(self._secret_file(
                "signer-s", "anchor-secret.txt", self.bob_secret)),
        ]
        s = ServerProcess(self, self.tmp, "signer-s", args_s)
        s.wait_ready()
        self._assert_alice_serves(s.base_url)
        self._assert_alice_serves(b.base_url)
        b_snap_before = snap_b.read_bytes()
        b_anchor_before = self._anchor_path("signer-b").read_bytes()

        status, push = node_server.push_anchor(s.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"], "different_signer")
        print("PAIRED PUSH different_signer: a pair whose "
              "anchor names another signer refused at "
              "succession, reported in the push result")
        # The peer is unchanged: both files, the wire, health.
        self.assertEqual(snap_b.read_bytes(), b_snap_before)
        self.assertEqual(self._anchor_path("signer-b").read_bytes(),
                         b_anchor_before)
        self._assert_alice_serves(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertEqual(health_b["last_anchor_delivery"]["reason"],
                         "different_signer")
        # And the refusal is audit-logged beside B's anchor,
        # in case 10's shape, by case 17's code unmodified.
        audit_path = self._anchor_path("signer-b").parent / (
            self._anchor_path("signer-b").name + ".audit.jsonl")
        audit_text = audit_path.read_text(encoding="utf-8")
        self.assertIn("reanchor_rejected", audit_text)
        self.assertIn("different_signer", audit_text)

    # Case 19: the backward fence across servers. B's anchor
    # is a LATER version over the SAME head as A's (refreshed
    # once more over the standing record and delivered to B
    # alone); A still holds the earlier version of that same
    # lineage. A's paired push - its record half identical -
    # is refused anchor_regression, and B keeps the later
    # anchor, byte-for-byte.
    def test_paired_push_anchor_regression_refused(self):
        # One anchor lineage, written once and distributed to
        # both servers' own anchor files - the out-of-band
        # distribution the prototype stands in for.
        staging = self.tmp / "staging-outside" / "registry-anchor.json"
        write_anchor(self.registry_log, staging,
                     signer="alice", secret_hex=self.alice_secret)
        for name in ("regr-a", "regr-b"):
            dest = self._anchor_path(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging, dest)
        snap_a = self._snapshot_copy("regr-a")
        snap_b = self._snapshot_copy("regr-b")
        a = ServerProcess(self, self.tmp, "regr-a",
                          self._server_args("regr-a", snap_a,
                                            signed=True))
        a.wait_ready()
        b = ServerProcess(self, self.tmp, "regr-b",
                          self._server_args("regr-b", snap_b,
                                            signed=True))
        b.wait_ready()

        # B's anchor advances one version over the SAME head:
        # the lifecycle write extends a staging copy of the
        # lineage B holds (writing B's own file directly would
        # move the ground the delivery is judged against - the
        # delivery would then be byte-identical, a no_change),
        # and the refreshed anchor reaches B by delivery alone.
        staging_v1 = (self.tmp / "staging2-outside"
                      / "registry-anchor.json")
        staging_v1.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._anchor_path("regr-b"), staging_v1)
        write_anchor(self.registry_log, staging_v1,
                     signer="alice", secret_hex=self.alice_secret)
        report = node_server.deliver_anchor(
            b.base_url, anchor_of(staging_v1))
        self.assertEqual(report["status"], "delivered")
        b_anchor_now = self._anchor_path("regr-b").read_bytes()
        b_snap_now = snap_b.read_bytes()
        self.assertNotEqual(b_anchor_now, staging.read_bytes())

        # A's pair: the record half is the chain B stands on;
        # the anchor half is the earlier version of B's own
        # lineage - its kept history a proper prefix.
        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_regression")
        print("PAIRED PUSH anchor_regression: an earlier "
              "version of the peer's own anchor lineage "
              "refused under the backward name, in the push "
              "result")
        self.assertEqual(self._anchor_path("regr-b").read_bytes(),
                         b_anchor_now)
        self.assertEqual(snap_b.read_bytes(), b_snap_now)
        self._assert_alice_serves(b.base_url)
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(
            health_a["last_anchor_push"]["delivery"]["reason"],
            "anchor_regression")

    # Case 19: the witness fence across servers (case 18's
    # discipline, carried). The peer is witness-configured;
    # the sender is not, and its anchors are signer-alone.
    # The sender advances under its own configuration, then
    # pushes: anchor_witness_invalid, the peer unchanged.
    # Once the sender's anchor over the same head is
    # countersigned by the configured witness - the lifecycle
    # completed at the sender - the same push delivers.
    def test_paired_push_witness_refused_then_delivered(self):
        # B: anchors countersigned by carol from the start,
        # configured signer + witness.
        self._signed_anchor_for("wit-b", witness=True)
        snap_b = self._snapshot_copy("wit-b")
        b = ServerProcess(self, self.tmp, "wit-b",
                          self._server_args("wit-b", snap_b,
                                            signed=True, witness=True))
        b.wait_ready()
        # A: signer-alone anchors, no witness configured.
        self._signed_anchor_for("wit-a")
        snap_a = self._snapshot_copy("wit-a")
        a = ServerProcess(self, self.tmp, "wit-a",
                          self._server_args("wit-a", snap_a,
                                            signed=True))
        a.wait_ready()
        self._assert_alice_serves(b.base_url)

        # A advances under its own configuration: revocation,
        # signer-alone re-anchor, pair delivered to A.
        self._revoke_alice()
        self._signed_anchor_for("wit-a")
        report = self._deliver_pair_to(a.base_url,
                                       self._anchor_path("wit-a"))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        b_snap_before = snap_b.read_bytes()
        b_anchor_before = self._anchor_path("wit-b").read_bytes()

        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_witness_invalid")
        print("PAIRED PUSH anchor_witness_invalid: the "
              "sender's signer-alone anchor refused at the "
              "witness-configured peer, in the push result")
        self.assertEqual(snap_b.read_bytes(), b_snap_before)
        self.assertEqual(self._anchor_path("wit-b").read_bytes(),
                         b_anchor_before)
        self._assert_alice_serves(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertEqual(health_b["last_anchor_delivery"]["reason"],
                         "anchor_witness_invalid")

        # The lifecycle completed at the SENDER: A's anchor
        # over the same grown head is re-written, now
        # countersigned by carol. The push carries that
        # completed lifecycle; it performs none at B.
        write_anchor(self.registry_log, self._anchor_path("wit-a"),
                     signer="alice", secret_hex=self.alice_secret,
                     witness="carol",
                     witness_secret_hex=self.carol_secret)
        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        print("PAIRED PUSH witnessed: once the sender's "
              "anchor is countersigned by the configured "
              "witness, the same paired push delivers")
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self.assertEqual(self._anchor_path("wit-b").read_bytes(),
                         self._anchor_path("wit-a").read_bytes())
        self._assert_revoked_over_wire(b.base_url)

    # Case 19: a sender with a PolicyNode but NO anchor
    # configured pushes the record only - case 16's push
    # exactly, through case 14's surface - with the outcome
    # recorded as its last anchor push and nothing recorded
    # as a snapshot push by this surface.
    def test_push_anchor_without_anchor_pushes_record_only(self):
        # Target B: unsigned anchor, lifecycle followed for B
        # (case 16's stale leg, completed by hand here).
        snap_s = self._snapshot_copy("bare-s")
        s = ServerProcess(self, self.tmp, "bare-s",
                          self._server_args("bare-s", snap_s,
                                            anchored=False))
        s.wait_ready()
        self._anchor_for("bare-b")
        snap_b = self._snapshot_copy("bare-b")
        b = ServerProcess(self, self.tmp, "bare-b",
                          self._server_args("bare-b", snap_b))
        b.wait_ready()
        health_s = node_server.fetch_health(s.base_url)
        self.assertFalse(health_s["anchor_configured"])

        self._revoke_alice()
        self._anchor_for("bare-b")
        report = node_server.deliver_snapshot(
            s.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(s.base_url)
        self._assert_alice_serves(b.base_url)

        status, push = node_server.push_anchor(s.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        # The delivery under the push is case 14's snapshot
        # delivery report - it carries no paired half.
        delivery = push["delivery"]
        self.assertEqual(delivery["status"], "delivered")
        self.assertEqual(delivery["before_count"], 2)
        self.assertEqual(delivery["after_count"], 3)
        self.assertEqual(delivery["loaded_entries"], 3)
        self.assertNotIn("paired", delivery)
        print("PAIRED PUSH without an anchor: the record "
              "travels alone, through case 14's surface, "
              "exactly as case 16's push carries it")
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(b.base_url)
        health_s = node_server.fetch_health(s.base_url)
        self.assertEqual(health_s["last_anchor_push"]["status"],
                         "delivered")
        self.assertIsNone(health_s["last_snapshot_push"])

    # Case 19: a mesh-only pushing server holds neither half
    # to push - no_snapshot on HTTP 200, delivery null, and
    # the server is undisturbed.
    def test_push_anchor_mesh_only_sender_no_snapshot(self):
        self._signed_anchor_for("pair-target")
        b = ServerProcess(self, self.tmp, "pair-target",
                          self._server_args(
                              "pair-target",
                              self._snapshot_copy("pair-target"),
                              signed=True))
        b.wait_ready()
        sender = ServerProcess(self, self.tmp, "pair-mesh-only", [])
        sender.wait_ready()
        status, push = node_server.push_anchor(
            sender.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "no_snapshot")
        self.assertIsNone(push["delivery"])
        health = node_server.fetch_health(sender.base_url)
        self.assertEqual(health["last_anchor_push"]["status"],
                         "no_snapshot")
        node_server.mutate(sender.base_url, "mode", "normal")
        state = node_server.fetch_state(sender.base_url)
        self.assertEqual(state["state"]["mode"], "normal")

    # Case 19: a sender whose anchor file cannot be read at
    # push time has nothing honest to send - the push reports
    # 502 "own anchor unreadable", records it as its last
    # anchor push, and makes no contact with the peer (the
    # peer records no anchor delivery at all).
    def test_push_anchor_own_anchor_unreadable(self):
        a, b, snap_a, snap_b = self._signed_pair("gone-a", "gone-b")
        self._anchor_path("gone-a").unlink()
        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 502)
        self.assertTrue(push["error"].startswith(
            "own anchor unreadable"), push)
        print("PAIRED PUSH own anchor unreadable: the "
              "sender's anchor file is gone at push time; the "
              "push refuses on 502 and the peer is never "
              "contacted")
        health_a = node_server.fetch_health(a.base_url)
        self.assertTrue(health_a["last_anchor_push"]["error"]
                        .startswith("own anchor unreadable"))
        health_b = node_server.fetch_health(b.base_url)
        self.assertIsNone(health_b["last_anchor_delivery"])
        self._assert_alice_serves(b.base_url)


class SignedTlsPairPushTests(PairPushSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds",
            ["pair-tls-a", "pair-tls-b", "test-client",
             "pair-tls-other"])
        client = self.creds.nodes["test-client"]
        # The driving client claims the node id its certificate
        # was issued for; the receiving servers run with
        # identity binding on and check the claim (case 11).
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls_args(self, name: str, snapshot: Path, extra=()):
        return self._server_args(name, snapshot, signed=True, extra=(
            "--tls-bind-identity",
            *extra,
        ))

    def _tls(self, node_name: str):
        cert = self.creds.nodes[node_name]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 19 over the protected link, with identity binding
    # and a signed anchor: A accepts the revocation's paired
    # delivery with the same-signer lifecycle followed for A,
    # then pushes its pair over HTTPS; B applies both halves,
    # the revoked policy refuses 410 over the protected link,
    # and both health surfaces report the new standing.
    def test_signed_tls_paired_push_full_flow(self):
        self._signed_anchor_for("pair-tls-a", "pair-tls-b")
        snap_a = self._snapshot_copy("pair-tls-a")
        snap_b = self._snapshot_copy("pair-tls-b")
        a = ServerProcess(self, self.tmp, "pair-tls-a",
                          self._tls_args("pair-tls-a", snap_a),
                          tls=self._tls("pair-tls-a"))
        a.wait_ready()
        b = ServerProcess(self, self.tmp, "pair-tls-b",
                          self._tls_args("pair-tls-b", snap_b),
                          tls=self._tls("pair-tls-b"))
        b.wait_ready()
        self._assert_alice_serves(b.base_url)

        self._revoke_alice()
        self._signed_anchor_for("pair-tls-a")
        report = self._deliver_pair_to(a.base_url,
                                       self._anchor_path("pair-tls-a"))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(a.base_url)
        self._assert_alice_serves(b.base_url)

        status, push = node_server.push_anchor(a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(push["delivery"]["loaded_entries"], 3)
        print("PAIRED PUSH (mutual TLS, identity binding, "
              "signed anchor): A's pair delivered to B over "
              "HTTPS; both files moved; 410 over the "
              "protected link")
        self.assertEqual(snap_b.read_bytes(),
                         self.registry_log.read_bytes())
        self.assertEqual(self._anchor_path("pair-tls-b").read_bytes(),
                         self._anchor_path("pair-tls-a").read_bytes())
        self._assert_revoked_over_wire(b.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 3)
        self.assertEqual(health_b["last_anchor_delivery"]["status"],
                         "delivered")
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(health_a["last_anchor_push"]["status"],
                         "delivered")

    # Case 19: the identity refusal across servers, at the
    # transport. The pushing server runs in experiment 014
    # mode - mutual TLS, binding OFF - claiming "pair-false"
    # while presenting the certificate issued for
    # "pair-tls-other", and it holds an anchor of its own so
    # the PAIRED form is what it attempts. The receiving
    # server runs with binding on and refuses the connection's
    # claim before case 17's endpoint runs; the push is
    # reported on HTTP 502 identity_mismatch naming claimed
    # and certified, and the peer never records an anchor
    # delivery.
    def test_false_claiming_paired_pusher_refused(self):
        self._signed_anchor_for("pair-tls-b")
        snap_b = self._snapshot_copy("pair-tls-b")
        b = ServerProcess(self, self.tmp, "pair-tls-b",
                          self._tls_args("pair-tls-b", snap_b),
                          tls=self._tls("pair-tls-b"))
        b.wait_ready()
        b_snap_before = snap_b.read_bytes()
        b_anchor_before = self._anchor_path("pair-tls-b").read_bytes()

        # The impostor holds an anchor of its own (unsigned,
        # over its snapshot copy) so its push attempts the
        # paired form; its own accepting side has binding off,
        # so the driving client's matching claim passes,
        # exactly as in experiment 014 mode.
        self._anchor_for("impostor")
        snap_i = self._snapshot_copy("impostor")
        impostor = ServerProcess(
            self, self.tmp, "impostor",
            self._server_args("impostor", snap_i),
            tls=self._tls("pair-tls-other"), node_id="pair-false")
        impostor.wait_ready()

        status, push = node_server.push_anchor(
            impostor.base_url, b.base_url)
        self.assertEqual(status, 502)
        self.assertEqual(push["error"], "identity_mismatch")
        self.assertEqual(push["claimed"], "pair-false")
        self.assertEqual(push["certified"], "pair-tls-other")
        print("PAIRED PUSH identity_mismatch: a pusher "
              "claiming an id its certificate was not issued "
              "for refused at the binding peer's transport "
              "before the paired endpoint runs; the refusal "
              "names claimed and certified ids")
        # The peer never recorded an anchor delivery - nor a
        # snapshot delivery; both files unchanged, wire same.
        self.assertEqual(snap_b.read_bytes(), b_snap_before)
        self.assertEqual(self._anchor_path("pair-tls-b").read_bytes(),
                         b_anchor_before)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 2)
        self.assertIsNone(health_b["last_anchor_delivery"])
        self.assertIsNone(health_b["last_snapshot_delivery"])
        self._assert_alice_serves(b.base_url)
        # And the refusal is in the peer's log, as in case 11.
        self.assertIn("identity_mismatch", b.log_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
