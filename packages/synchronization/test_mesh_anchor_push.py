"""
Server-to-server MESH anchor push tests (experiment 032,
sync-mesh case 21).

Case 20 (experiment 031) built the receiving half of the
mesh anchor's wire transport: a running server accepts a
mesh anchor delivery at POST /deliver-mesh-anchor, judged
against the mesh state it holds. But the sending end of
that surface was a client: no server sent its own mesh
anchor onward, so advancing a peer's mesh anchor from the
one kind of process that holds a mesh anchor of its own
still took a credential-holding client in the middle, or a
hand at the peer - the exact gap case 19 closed for the
registry anchor one experiment after case 17 built its
receiving surface.

Experiment 032 closes the sender's half for the mesh
anchor: a running server gains POST /push-mesh-anchor, in
/push-snapshot's and /push-anchor's shape (cases 16/19).
Driven on the sender, it sends the anchor object the
sender's own MESH anchor file holds to the peer's POST
/deliver-mesh-anchor - experiment 031's surface,
UNMODIFIED - and the peer's delivery report is the push's
result. /health gains last_mesh_anchor_push beside
last_mesh_anchor_delivery.

What is asserted here, at prototype scale:

- The end-to-end story (case 21): two servers stand on
  the same mesh state under the same signed mesh anchor;
  the mesh grows honestly at the sender; the peer's sync
  toward the sender is refused anchor_stale; the sender's
  mesh anchor is advanced by the proper lifecycle AT THE
  SENDER (the same signer re-anchors its own file); ONE
  push reports delivered, the peer's mesh anchor file
  ends byte-for-byte the anchor the sender sent, the
  peer's /health classifies its not-yet-converged state
  anchor_mismatch until the same sync converges - no
  restart, no client driving the peer, no hand at the
  peer's anchor file. A second identical push is
  no_change and rewrites nothing.
- The peer's refusals surface in the push result under
  case 20's own names, each leaving the peer's anchor
  file and mesh state unchanged: different_signer (the
  refusal audit-logged beside the peer's mesh anchor),
  anchor_regression (the peer standing on a later version
  of the same lineage over the same head),
  anchor_signature_invalid (the sender's file tampered by
  hand), anchor_mismatch (a foreign chain's anchor in a
  fresh lineage), and anchor_witness_invalid at a
  mesh-witness-configured peer when the sender's advanced
  anchor carries no countersignature - then delivered
  once the lifecycle is completed at the sender.
- The sender's own shapes: no mesh anchor configured is
  200 no_mesh_anchor with no contact made; its own mesh
  anchor file unreadable at push time is 502 "own anchor
  unreadable", the peer never contacted.
- The full story over mutual TLS with identity binding,
  and a false-claiming pusher in experiment 014 mode
  refused at the binding peer's transport as
  identity_mismatch before case 20's endpoint runs.

Run: python3 -m unittest test_mesh_anchor_push -v
(from this directory) or via discovery from the repo root.
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
    under a private name, as test_mesh_anchor_wire_delivery
    loads it: two packages ship an anchor.py, and this
    suite puts the registration package on sys.path too,
    so a plain `import anchor` may resolve to the wrong
    one."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_mesh_anchor_push_sync_anchor", anchor_file)
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
    test_mesh_anchor_wire_delivery.py's helper, including
    its port-file race guard."""

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
    node's own view; the holders here are server
    processes)."""
    view = SyncNode("mesh-view")
    view.restore_to_entries(entries)
    return view


class MeshAnchorPushSetup(unittest.TestCase):
    """Shared setup: a temp dir, enrolled identities for the
    anchor secrets (alice the signer, bob another identity,
    carol the witness), per-node mesh anchor paths, and the
    anchored-pair driver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.anchor_a = self.tmp / "a-outside" / "mesh-anchor.json"
        self.anchor_b = self.tmp / "b-outside" / "mesh-anchor.json"
        self.anchor_a.parent.mkdir(parents=True, exist_ok=True)
        self.anchor_b.parent.mkdir(parents=True, exist_ok=True)
        central = self.tmp / "central"
        channel = RegistrationChannel(
            central / "registry.jsonl", central / "audit.jsonl")
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.carol_secret = channel.enroll_user("carol")
        outside = self.tmp / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        self.alice_secret_file = outside / "alice-secret.txt"
        self.alice_secret_file.write_text(self.alice_secret,
                                          encoding="utf-8")
        self.bob_secret_file = outside / "bob-secret.txt"
        self.bob_secret_file.write_text(self.bob_secret,
                                        encoding="utf-8")
        self.carol_secret_file = outside / "carol-secret.txt"
        self.carol_secret_file.write_text(self.carol_secret,
                                          encoding="utf-8")

    # -- helpers -----------------------------------------------------

    def _start(self, label, node_id, args=(), tls=None):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args), tls=tls)
        server.wait_ready()
        return server

    def _honest_pair(self, tls_a=None, tls_b=None):
        """Two server processes that mutate and converge
        exactly as in case 4/7 (mesh-a writes greeting/mode,
        mesh-b writes region; after one /sync both hold all
        three entries under one state hash)."""
        a = self._start("mesh-a", "mesh-a", tls=tls_a)
        b = self._start("mesh-b", "mesh-b", tls=tls_b)
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

    def _mesh_args(self, anchor_path, signer=None, secret_file=None,
                   witness=None, witness_secret_file=None):
        args = ["--mesh-anchor", str(anchor_path)]
        if signer is not None:
            args += ["--mesh-anchor-signer", signer,
                     "--mesh-anchor-secret-file", str(secret_file)]
        if witness is not None:
            args += ["--mesh-anchor-witness", witness,
                     "--mesh-anchor-witness-secret-file",
                     str(witness_secret_file)]
        return args

    def _anchor_pair(self, signing_a, signing_b, args_a, args_b,
                     tls_a=None, tls_b=None):
        """Case 21's shared shape: an honest pair converges;
        each node's mesh anchor is written over the
        converged state with its own signing (deterministic
        writes over identical state with identical signing
        are byte-identical, so equal signing means the two
        files are copies of one anchor); mesh-a is stopped
        and restarted anchored under its copy and converged
        by one /sync; then mesh-b the same. Both servers end
        standing on the same mesh state under the same
        anchor lineage, each consulting its own file.
        Returns (anchored_a, anchored_b)."""
        a, b = self._honest_pair(tls_a=tls_a, tls_b=tls_b)
        entries = node_server.fetch_entries(b.base_url)
        self._write_anchor(entries, self.anchor_a, **signing_a)
        self._write_anchor(entries, self.anchor_b, **signing_b)
        a.stop()
        anchored_a = self._start("mesh-a-anchored", "mesh-a",
                                 args_a, tls=tls_a)
        result = node_server.request_sync(anchored_a.base_url,
                                          b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored_a.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        b.stop()
        anchored_b = self._start("mesh-b-anchored", "mesh-b",
                                 args_b, tls=tls_b)
        result = node_server.request_sync(anchored_b.base_url,
                                          anchored_a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored_b.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        return anchored_a, anchored_b

    def _alice(self):
        return {"signer": "alice", "secret_hex": self.alice_secret}

    def _alice_args(self, anchor_path):
        return self._mesh_args(anchor_path, "alice",
                               self.alice_secret_file)


class MeshAnchorPushTests(MeshAnchorPushSetup):

    # Case 21, the core scenario: both servers stand on the
    # same mesh state under alice's signed anchor; the mesh
    # grows at the SENDER; the peer's sync toward the sender
    # refuses anchor_stale; the sender's anchor is advanced
    # at the sender by the proper lifecycle; ONE push
    # delivers it; the peer's anchor file is byte-for-byte
    # the sender's; the same sync converges. A second push
    # is no_change.
    def test_mesh_anchor_push_brings_peer_current(self):
        a, b = self._anchor_pair(
            self._alice(), self._alice(),
            self._alice_args(self.anchor_a),
            self._alice_args(self.anchor_b))
        health = node_server.fetch_health(a.base_url)
        self.assertIsNone(health["last_mesh_anchor_push"])
        peer_hash = node_server.fetch_state(b.base_url)["state_hash"]

        node_server.mutate(a.base_url, "level", "3")
        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["reason"], "anchor_stale")
        self.assertEqual(result["self"]["accepted"], 0)
        self.assertEqual(
            node_server.fetch_state(b.base_url)["state_hash"],
            peer_hash)
        print("MESH ANCHOR PUSH setup: the mesh grew at the "
              "sender; the peer's sync toward it refused "
              "anchor_stale, the peer keeping the anchored state")

        # The lifecycle, completed at the sender: the same
        # signer re-anchors the grown head from the sender's
        # own anchor file, extending its kept history.
        self._write_anchor(node_server.fetch_entries(a.base_url),
                           self.anchor_a, **self._alice())
        sender_anchor_bytes = self.anchor_a.read_bytes()

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["node_id"], "mesh-a")
        self.assertEqual(push["peer"], b.base_url)
        self.assertEqual(push["status"], "delivered")
        delivery = push["delivery"]
        self.assertEqual(delivery["node_id"], "mesh-b")
        self.assertEqual(delivery["status"], "delivered")
        self.assertIsNone(delivery["reason"])
        self.assertEqual(delivery["before_count"], 3)
        self.assertEqual(delivery["after_count"], 4)
        self.assertEqual(delivery["before_seq"], 0)
        self.assertEqual(delivery["after_seq"], 1)
        self.assertEqual(delivery["signer"], "alice")
        self.assertEqual(self.anchor_b.read_bytes(),
                         sender_anchor_bytes)
        print("MESH ANCHOR PUSH (plaintext): ONE push from "
              "the sender delivered the advanced mesh anchor "
              "- entries 3 -> 4, history sequence 0 -> 1; "
              "the peer's anchor file is byte-for-byte the "
              "sender's")

        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(
            health_b["last_mesh_anchor_delivery"]["status"],
            "delivered")
        # The anchor moved first; the state catches up by
        # sync, in that order (case 15's at-rest
        # classification, as in case 20).
        self.assertEqual(health_b["mesh_anchor_status"],
                         "anchor_mismatch")
        self.assertEqual(health_b["entries"], 3)
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(
            health_a["last_mesh_anchor_push"]["status"],
            "delivered")
        self.assertEqual(
            health_a["last_mesh_anchor_push"]["delivery"]
            ["node_id"], "mesh-b")

        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        self.assertEqual(
            result["state_hash"],
            node_server.fetch_state(a.base_url)["state_hash"])
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["entries"], 4)
        self.assertEqual(health_b["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR PUSH: the same sync then converged "
              "on the grown state; both servers anchored")

        # No-change: the identical anchor pushed again
        # rewrites nothing at the peer.
        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "no_change")
        self.assertEqual(push["delivery"]["status"], "no_change")
        self.assertEqual(self.anchor_b.read_bytes(),
                         sender_anchor_bytes)
        print("MESH ANCHOR PUSH no_change: a second push of "
              "the identical anchor rewrote nothing at the peer")

    # Case 21, succession across servers: the sender's mesh
    # anchor is signed by bob, the peer's current anchor by
    # alice; the push reports different_signer, the refusal
    # is audit-logged beside the peer's mesh anchor, and
    # the peer is unchanged.
    def test_mesh_anchor_push_different_signer_refused(self):
        a, b = self._anchor_pair(
            {"signer": "bob", "secret_hex": self.bob_secret},
            self._alice(),
            self._mesh_args(self.anchor_a, "bob",
                            self.bob_secret_file),
            self._alice_args(self.anchor_b))
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(b.base_url)["state_hash"]

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "different_signer")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(b.base_url)["state_hash"],
            peer_hash)
        health = node_server.fetch_health(b.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(
            health["last_mesh_anchor_delivery"]["reason"],
            "different_signer")
        events = read_anchor_audit(self.anchor_b)
        self.assertTrue(any(
            row.get("event") == "reanchor_rejected"
            and row.get("reason") == "different_signer"
            and row.get("attempted_signer") == "bob"
            and row.get("current_signer") == "alice"
            and row.get("chain") == "sync-mesh"
            for row in events))
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(
            health_a["last_mesh_anchor_push"]["delivery"]
            ["reason"], "different_signer")
        print("MESH ANCHOR PUSH different_signer: a push of "
              "bob's anchor to an alice-anchored peer refused "
              "at succession and audit-logged beside the "
              "peer's mesh anchor; the peer unchanged")

    # Case 21, the backward fence across servers: the
    # peer's mesh anchor is refreshed once more over the
    # SAME head (a later version of the same lineage,
    # delivered to the peer alone under case 20 and
    # accepted) while the sender still holds the earlier
    # version; the push reports anchor_regression and the
    # peer keeps the later anchor byte-for-byte.
    def test_mesh_anchor_push_anchor_regression_refused(self):
        a, b = self._anchor_pair(
            self._alice(), self._alice(),
            self._alice_args(self.anchor_a),
            self._alice_args(self.anchor_b))
        staging = self.tmp / "source" / "b-refresh.json"
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.anchor_b, staging)
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           staging, **self._alice())
        report = node_server.deliver_mesh_anchor(
            b.base_url, self._anchor_object(staging))
        self.assertEqual(report["status"], "delivered")
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(b.base_url)["state_hash"]

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_regression")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(b.base_url)["state_hash"],
            peer_hash)
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(
            health_a["last_mesh_anchor_push"]["delivery"]
            ["reason"], "anchor_regression")
        print("MESH ANCHOR PUSH anchor_regression: the "
              "earlier version of the lineage pushed to a "
              "peer standing on its later version refused "
              "under the backward name; the peer kept the "
              "later anchor byte-for-byte")

    # Case 21, a tampered sender anchor: the sender's mesh
    # anchor file is edited by hand after startup (one
    # kept-history entry's recorded state hash changed,
    # the entry hashes left standing - case 17's tamper
    # shape); the push transports the file's content and
    # the peer judges the signature first:
    # anchor_signature_invalid, the peer unchanged.
    def test_mesh_anchor_push_signature_invalid_refused(self):
        a, b = self._anchor_pair(
            self._alice(), self._alice(),
            self._alice_args(self.anchor_a),
            self._alice_args(self.anchor_b))
        tampered = self._anchor_object(self.anchor_a)
        tampered["history"][0]["state_hash"] = "f" * 64
        self.anchor_a.write_text(
            json.dumps(tampered, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(b.base_url)["state_hash"]

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_signature_invalid")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(b.base_url)["state_hash"],
            peer_hash)
        health = node_server.fetch_health(b.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR PUSH anchor_signature_invalid: "
              "the peer judged the sender's tampered anchor "
              "file content and refused it before any head "
              "judgement; the peer unchanged")

    # Case 21, a foreign chain's anchor across servers: a
    # sender standing under a mesh anchor over a rewritten
    # mesh chain, in a FRESH lineage signed by the peer's
    # own signer, pushes; the peer judges at the head:
    # anchor_mismatch - the anchor succeeds nothing and
    # pairs with nothing the peer holds.
    def test_mesh_anchor_push_anchor_mismatch_refused(self):
        a, b = self._honest_pair()
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_b, **self._alice())
        b.stop()
        anchored_b = self._start("mesh-b-anchored", "mesh-b",
                                 self._alice_args(self.anchor_b))
        result = node_server.request_sync(anchored_b.base_url,
                                          a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        a.stop()

        rogue = SyncNode("mesh-a")
        rogue.mutate("greeting", "pwned")
        rogue.mutate("mode", "admin")
        rogue_b = SyncNode("mesh-b")
        rogue_b.mutate("region", "south")
        foreign_path = self.tmp / "foreign" / "mesh-anchor.json"
        foreign_path.parent.mkdir(parents=True, exist_ok=True)
        write_mesh_anchor(
            view_of([*rogue.entries(), *rogue_b.entries()]),
            foreign_path, **self._alice())
        sender = self._start("mesh-foreign", "mesh-a",
                             self._alice_args(foreign_path))
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(
            anchored_b.base_url)["state_hash"]

        status, push = node_server.push_mesh_anchor(
            sender.base_url, anchored_b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_mismatch")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(anchored_b.base_url)
            ["state_hash"], peer_hash)
        health = node_server.fetch_health(anchored_b.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(
            health["last_mesh_anchor_delivery"]["reason"],
            "anchor_mismatch")
        health_s = node_server.fetch_health(sender.base_url)
        self.assertEqual(
            health_s["last_mesh_anchor_push"]["delivery"]
            ["reason"], "anchor_mismatch")
        print("MESH ANCHOR PUSH anchor_mismatch: a foreign "
              "chain's anchor in a fresh lineage, pushed by "
              "a server standing under it, refused at the "
              "peer's head judgement; the peer unchanged")

    # Case 21, the configuration edge: a pushing process
    # with NO mesh anchor configured answers 200
    # no_mesh_anchor with delivery null and makes no
    # contact - the peer records no mesh anchor delivery.
    def test_push_mesh_anchor_no_mesh_anchor_configured(self):
        a, b = self._honest_pair()
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_b, **self._alice())
        b.stop()
        anchored_b = self._start("mesh-b-anchored", "mesh-b",
                                 self._alice_args(self.anchor_b))
        result = node_server.request_sync(anchored_b.base_url,
                                          a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        a.stop()
        peer_bytes = self.anchor_b.read_bytes()

        plain = self._start("mesh-plain", "mesh-plain")
        status, push = node_server.push_mesh_anchor(
            plain.base_url, anchored_b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["node_id"], "mesh-plain")
        self.assertEqual(push["status"], "no_mesh_anchor")
        self.assertIsNone(push["delivery"])
        health = node_server.fetch_health(plain.base_url)
        self.assertEqual(
            health["last_mesh_anchor_push"]["status"],
            "no_mesh_anchor")
        health_b = node_server.fetch_health(anchored_b.base_url)
        self.assertIsNone(health_b["last_mesh_anchor_delivery"])
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        # The unconfigured sender is otherwise undisturbed.
        node_server.mutate(plain.base_url, "note", "still serving")
        self.assertEqual(
            node_server.fetch_state(plain.base_url)["state"]
            ["note"], "still serving")
        print("MESH ANCHOR PUSH no_mesh_anchor: a sender "
              "with no mesh anchor configured reported the "
              "configuration edge on 200 and contacted no peer")

    # Case 21, the sender's own unreadable anchor: its mesh
    # anchor file deleted after startup, the push reports
    # 502 "own anchor unreadable", recorded as the last
    # mesh anchor push, and the peer is never contacted.
    def test_push_mesh_anchor_own_anchor_unreadable(self):
        a, b = self._anchor_pair(
            self._alice(), self._alice(),
            self._alice_args(self.anchor_a),
            self._alice_args(self.anchor_b))
        peer_bytes = self.anchor_b.read_bytes()
        self.anchor_a.unlink()

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 502)
        self.assertTrue(
            push["error"].startswith("own anchor unreadable"),
            push)
        health = node_server.fetch_health(a.base_url)
        self.assertTrue(
            health["last_mesh_anchor_push"]["error"].startswith(
                "own anchor unreadable"))
        health_b = node_server.fetch_health(b.base_url)
        self.assertIsNone(health_b["last_mesh_anchor_delivery"])
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        node_server.mutate(b.base_url, "note", "peer undisturbed")
        print("MESH ANCHOR PUSH own anchor unreadable: the "
              "sender with its mesh anchor file deleted "
              "reported the refusal on 502 and contacted "
              "no peer")


class WitnessedMeshAnchorPushTests(MeshAnchorPushSetup):

    # Case 21, the witness fence across servers (case 18's
    # discipline on the mesh half): both servers stand
    # under the same countersigned anchor; the peer is
    # mesh-witness-configured, the sender is not. The
    # sender advances its anchor signer-alone - a process
    # that is not witness-configured writes no
    # countersignature - and the push is refused
    # anchor_witness_invalid; once the sender's anchor
    # over the same grown head is countersigned by carol
    # AT THE SENDER, the same push delivers and the peer's
    # sync converges.
    def test_mesh_anchor_push_witness_refused_then_delivered(self):
        witnessed = {"signer": "alice",
                     "secret_hex": self.alice_secret,
                     "witness": "carol",
                     "witness_secret_hex": self.carol_secret}
        a, b = self._anchor_pair(
            witnessed, witnessed,
            self._alice_args(self.anchor_a),
            self._mesh_args(self.anchor_b, "alice",
                            self.alice_secret_file, "carol",
                            self.carol_secret_file))
        node_server.mutate(a.base_url, "level", "3")
        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertEqual(result["self"]["reason"], "anchor_stale")

        self._write_anchor(node_server.fetch_entries(a.base_url),
                           self.anchor_a, **self._alice())
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(b.base_url)["state_hash"]
        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivery_refused")
        self.assertEqual(push["delivery"]["reason"],
                         "anchor_witness_invalid")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(b.base_url)["state_hash"],
            peer_hash)
        print("MESH ANCHOR PUSH anchor_witness_invalid: the "
              "sender's signer-alone advance refused at the "
              "witness-configured peer; the peer unchanged")

        self._write_anchor(node_server.fetch_entries(a.base_url),
                           self.anchor_a,
                           signer="alice",
                           secret_hex=self.alice_secret,
                           witness="carol",
                           witness_secret_hex=self.carol_secret)
        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(self.anchor_b.read_bytes(),
                         self.anchor_a.read_bytes())
        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        health = node_server.fetch_health(b.base_url)
        self.assertEqual(health["entries"], 4)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR PUSH witnessed: the same push "
              "delivered once the sender's anchor was "
              "countersigned by the configured witness at "
              "the sender; the peer's sync converged")


class TlsMeshAnchorPushTests(MeshAnchorPushSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds",
            ["mesh-a", "mesh-b", "mesh-push-other", "test-client"])
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls(self, node_id):
        cert = self.creds.nodes[node_id]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 21 over experiment 014's protected transport
    # with experiment 017's identity binding on and a
    # signed mesh anchor: the main story end to end over
    # HTTPS - stale-refused sync, one push delivered, the
    # same sync converged, both /health surfaces reporting
    # the new standing.
    def test_mesh_anchor_push_over_protected_link(self):
        bind = ["--tls-bind-identity"]
        a, b = self._anchor_pair(
            self._alice(), self._alice(),
            self._alice_args(self.anchor_a) + bind,
            self._alice_args(self.anchor_b) + bind,
            tls_a=self._tls("mesh-a"), tls_b=self._tls("mesh-b"))
        node_server.mutate(a.base_url, "level", "3")
        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertEqual(result["self"]["reason"], "anchor_stale")

        self._write_anchor(node_server.fetch_entries(a.base_url),
                           self.anchor_a, **self._alice())
        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(self.anchor_b.read_bytes(),
                         self.anchor_a.read_bytes())
        result = node_server.request_sync(b.base_url, a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        health_a = node_server.fetch_health(a.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["entries"], 4)
        self.assertEqual(health_a["mesh_anchor_status"], "anchored")
        self.assertEqual(health_b["mesh_anchor_status"], "anchored")
        self.assertEqual(
            health_a["last_mesh_anchor_push"]["status"],
            "delivered")
        self.assertEqual(
            health_b["last_mesh_anchor_delivery"]["status"],
            "delivered")
        print("MESH ANCHOR PUSH (mutual TLS, identity "
              "binding, signed anchor): the stale-refused "
              "sync, ONE push over HTTPS delivered, and the "
              "same sync converged over the protected link")

    # Case 21, the identity refusal at the transport: a
    # pusher in experiment 014 mode (mutual TLS, binding
    # OFF) claiming push-false, an id its certificate (for
    # mesh-push-other) was not issued for, and holding a
    # mesh anchor of its own so the push is what it
    # attempts, pushes to a binding anchored peer. The
    # peer's accepting-side check refuses the connection
    # before case 20's endpoint runs: 502
    # identity_mismatch naming claimed and certified, and
    # the peer records no mesh anchor delivery.
    def test_false_claiming_pusher_refused_at_transport(self):
        tls_b = self._tls("mesh-b")
        a, b = self._honest_pair(tls_a=self._tls("mesh-a"),
                                 tls_b=tls_b)
        self._write_anchor(node_server.fetch_entries(b.base_url),
                           self.anchor_b, **self._alice())
        b.stop()
        anchored_b = self._start(
            "mesh-b-anchored", "mesh-b",
            self._alice_args(self.anchor_b) + ["--tls-bind-identity"],
            tls=tls_b)
        result = node_server.request_sync(anchored_b.base_url,
                                          a.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        a.stop()
        peer_bytes = self.anchor_b.read_bytes()
        peer_hash = node_server.fetch_state(
            anchored_b.base_url)["state_hash"]

        impostor_state = SyncNode("push-false")
        impostor_state.mutate("intent", "borrowed")
        impostor_anchor = self.tmp / "impostor" / "mesh-anchor.json"
        impostor_anchor.parent.mkdir(parents=True, exist_ok=True)
        write_mesh_anchor(impostor_state, impostor_anchor)
        impostor = self._start(
            "mesh-impostor", "push-false",
            ["--mesh-anchor", str(impostor_anchor)],
            tls=self._tls("mesh-push-other"))

        status, push = node_server.push_mesh_anchor(
            impostor.base_url, anchored_b.base_url)
        self.assertEqual(status, 502)
        self.assertEqual(push["error"], "identity_mismatch")
        self.assertEqual(push["claimed"], "push-false")
        self.assertEqual(push["certified"], "mesh-push-other")
        health = node_server.fetch_health(anchored_b.base_url)
        self.assertIsNone(health["last_mesh_anchor_delivery"])
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(self.anchor_b.read_bytes(), peer_bytes)
        self.assertEqual(
            node_server.fetch_state(anchored_b.base_url)
            ["state_hash"], peer_hash)
        self.assertIn("identity_mismatch", anchored_b.log_text())
        print("MESH ANCHOR PUSH identity_mismatch: a pusher "
              "claiming an id its certificate was not issued "
              "for refused at the binding peer's transport; "
              "the peer recorded no mesh anchor delivery")


if __name__ == "__main__":
    unittest.main(verbosity=2)
