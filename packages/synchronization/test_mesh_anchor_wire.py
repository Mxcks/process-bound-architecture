"""
Automated tests for mesh-anchor consultation over the wire for
server processes (tests/test-cases/sync-mesh.md, case 15),
built in experiment 026.

Case 8 (experiment 015) built anchor consultation for Gate 3
sync between in-process objects, and case 11 (experiment 019)
added signed mesh anchors to it. The experiment 007/014 server
processes were left out of both: their mesh side is a plain
SyncNode and consults no mesh anchor, so a rewritten peer's
mesh state - flagged divergent in-process - is accepted by a
running server without that check. This suite asserts the
server-side close:

- A node server started with --mesh-anchor (and, where the
  mesh anchor is signed, --mesh-anchor-signer plus
  --mesh-anchor-secret-file - flags in the style of the
  registry half's --anchor family, but independent of
  --snapshot) holds an AnchoredSyncNode for its mesh side, so
  the receives the process already performs (POST /entries
  and both sides of POST /sync) go through case 8's
  consultation unchanged. /health reports the standing beside
  the registry half's fields: mesh_anchor_configured and
  mesh_anchor_status, classified by case 8's own
  anchor_status().
- Honest convergence over the wire is unchanged: a server
  restarted with --mesh-anchor holds nothing (mesh state is
  process memory, experiment 007), classifies at rest as
  anchor_mismatch (it is not the anchored state and not an
  extension of it), then syncs with the honest peer holding
  the converged state and is accepted onto exactly the
  anchored state; /health then reports "anchored".
- A peer PROCESS claiming an honest origin's node id, its
  chain rewritten from genesis with different mutations
  (every hash recomputing - case 5/8's negative control), is
  flagged divergent over the wire: the anchored server's side
  of the /sync reports reason anchor_mismatch, accepts
  nothing, and its own mesh state is unchanged; /health names
  the same standing. A server started WITHOUT --mesh-anchor
  accepts the same rewrite and converges to it - stated
  plainly, as in case 8, because that acceptance is the hole
  this case closes only where a mesh anchor is configured.
- Honest growth is anchor_stale over the wire: refused under
  that name, never silently adopted past the anchor, the
  anchored server keeping exactly the anchored state, until
  the grown state is re-anchored, when the same sync lands.
- An entry edited in transit is refused by Gate 3's own
  verification - tampered_entry, case 8's stated order - and
  where a signing identity is configured, a mesh anchor
  signed by a different enrolled identity refuses every
  consultation as anchor_signature_invalid (sync result and
  /health alike) until the genuine anchor is restored, after
  which the growth lifecycle proceeds under its own names.
- A configured mesh anchor that cannot be read refuses in
  shape: /health answers with mesh_anchor_status
  "anchor_unreadable", and a mesh receive returns the
  existing SyncResult shape under that reason - never a
  dropped connection (case 15's one stated wire adaptation
  of case 8, which raises in-process).
- The full consultation is also proven over experiment 014's
  protected transport, with a signed mesh anchor: a rewritten
  chain pushed to a fresh anchored server over mutual TLS is
  refused as anchor_mismatch, and the same server then
  converges with the honest peer over /sync and reports
  "anchored".

Process cleanup is part of the contract, as in experiments
007-025: every spawned server is registered with addCleanup
the moment it starts, and no leg ever runs more than two
server processes at once. Test credentials and enrollment
secrets are generated inside the tests at runtime into the
test's temporary directory; no credential or secret is
committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_mesh_anchor_wire.py"
    python3 packages/synchronization/test_mesh_anchor_wire.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

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
from sync_mesh import SyncNode, tamper_entry  # noqa: E402


def _load_mesh_anchor():
    """Load experiment 012's MESH anchor module by file path
    under a private name, the way test_snapshot_wire_transport
    loads the registry one: two packages ship an anchor.py,
    and this suite puts the registration package on sys.path
    too, so a plain `import anchor` may resolve to the wrong
    one."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_mesh_anchor_wire_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_mesh_anchor = _load_mesh_anchor().write_mesh_anchor

SERVER = HERE / "node_server.py"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_snapshot_wire_transport.py's helper, with the label
    (port/log files) split from the claimed node id so a
    process can be restarted under the same node id."""

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
                port = int(self.port_file.read_text(encoding="utf-8").strip())
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


class MeshAnchorWireSetup(unittest.TestCase):
    """Shared setup: a temp dir, the mesh anchor path outside
    any server's state, and the honest-pair driver. Mesh-only
    servers need no registry; signed legs enroll identities
    for their anchor secrets."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

    # -- helpers -----------------------------------------------------

    def _start(self, label, node_id, args=(), tls=None):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args), tls=tls)
        server.wait_ready()
        return server

    def _honest_pair(self):
        """Two plain server processes that mutate and converge
        exactly as in case 4/7: mesh-a writes greeting/mode,
        mesh-b writes region; after one /sync both hold all
        three entries under one state hash."""
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

    def _write_anchor_from(self, holder, **signing):
        """Write the mesh anchor over the holder process's
        current view, to this test's anchor path."""
        entries = node_server.fetch_entries(holder.base_url)
        return write_mesh_anchor(view_of(entries), self.anchor_path,
                                 **signing)

    def _converged_anchored_server(self, label="mesh-a-anchored",
                                   extra_args=()):
        """The shared shape of the plaintext legs: an honest
        pair converges, the anchor is written over the
        converged state, mesh-a is stopped, and a fresh
        process claiming mesh-a is started with --mesh-anchor
        and converged by one /sync toward the honest peer that
        kept the state. Returns (anchored_server,
        honest_peer)."""
        a, b = self._honest_pair()
        self._write_anchor_from(b)
        a.stop()
        anchored = self._start(label, "mesh-a",
                               ["--mesh-anchor", str(self.anchor_path),
                                *extra_args])
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        return anchored, b


class PlaintextMeshAnchorWireTests(MeshAnchorWireSetup):

    # Case 15, main outcome over plaintext: a server restarted
    # with --mesh-anchor holds nothing (no persistence), its
    # /health classifies its own standing as anchor_mismatch
    # until it syncs, and one /sync toward the honest peer
    # holding the converged state lands it exactly on the
    # anchored state; /health then reports "anchored". The
    # plain peer reports itself unconfigured throughout.
    def test_honest_convergence_with_mesh_anchor(self):
        a, b = self._honest_pair()
        anchored_state_hash = node_server.fetch_state(b.base_url)["state_hash"]
        self._write_anchor_from(b)
        a.stop()

        anchored = self._start("mesh-a-anchored", "mesh-a",
                               ["--mesh-anchor", str(self.anchor_path)])
        health = node_server.fetch_health(anchored.base_url)
        self.assertTrue(health["mesh_anchor_configured"])
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        self.assertEqual(health["entries"], 0)
        peer_health = node_server.fetch_health(b.base_url)
        self.assertFalse(peer_health["mesh_anchor_configured"])
        self.assertEqual(peer_health["mesh_anchor_status"], "no_anchor")

        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        self.assertEqual(result["state_hash"], anchored_state_hash)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state"], {
            "greeting": "hello", "mode": "member", "region": "north"})
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["entries"], 3)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR WIRE (plaintext): restarted server "
              "consulted its mesh anchor on the /sync receive "
              "and converged onto exactly the anchored state; "
              "/health reports mesh_anchor_status anchored")

    # Case 15, negative control over the wire: a peer PROCESS
    # claiming mesh-a whose chain was rewritten from genesis
    # (different mutations, every hash recomputing) is flagged
    # divergent by the anchored server's side of a /sync -
    # anchor_mismatch, nothing accepted, the anchored server's
    # own state exactly what it was - and /health names the
    # same standing. A plain server syncing with the same
    # attacker accepts the rewrite: the hole this case closes
    # only where a mesh anchor is configured.
    def test_rewritten_peer_flagged_anchor_mismatch_over_the_wire(self):
        a, b = self._honest_pair()
        self._write_anchor_from(b)
        a.stop()
        b.stop()

        victim = self._start("mesh-victim", "mesh-victim",
                             ["--mesh-anchor", str(self.anchor_path)])
        attacker = self._start("mesh-attacker", "mesh-a")
        node_server.mutate(attacker.base_url, "greeting", "pwned")
        node_server.mutate(attacker.base_url, "mode", "admin")
        empty_hash = node_server.fetch_state(victim.base_url)["state_hash"]

        result = node_server.request_sync(victim.base_url, attacker.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertTrue(result["self"]["rolled_back"])
        self.assertEqual(result["self"]["reason"], "anchor_mismatch")
        self.assertEqual(result["self"]["accepted"], 0)
        self.assertEqual(result["self"]["state_hash"], empty_hash)
        state = node_server.fetch_state(victim.base_url)
        self.assertEqual(state["entry_ids"], [])
        self.assertEqual(state["state"], {})
        health = node_server.fetch_health(victim.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        print("MESH ANCHOR WIRE anchor_mismatch: a rewritten "
              "peer process flagged divergent over /sync, "
              "nothing accepted, the anchored server's state "
              "unchanged, /health names anchor_mismatch")
        victim.stop()

        plain = self._start("mesh-plain", "mesh-plain")
        result = node_server.request_sync(plain.base_url, attacker.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        state = node_server.fetch_state(plain.base_url)
        self.assertEqual(state["state"]["greeting"], "pwned")
        self.assertEqual(state["state"]["mode"], "admin")
        print("MESH ANCHOR WIRE control: the same rewritten "
              "peer is ACCEPTED by a server with no mesh anchor "
              "- the pre-026 behavior, stated plainly")

    # Case 15, honest growth over the wire: the anchored
    # server, converged on the anchored state, syncs with the
    # honest peer after one more honest mutation; the merged
    # state is the anchored state intact plus the append, so
    # the sync is refused as anchor_stale and the anchored
    # server keeps exactly the anchored state (its own
    # /health standing stays "anchored"). After the grown
    # state is re-anchored, the same sync lands.
    def test_honest_growth_anchor_stale_until_reanchored(self):
        anchored, b = self._converged_anchored_server()
        anchored_hash = node_server.fetch_state(anchored.base_url)["state_hash"]

        node_server.mutate(b.base_url, "level", "3")
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["reason"], "anchor_stale")
        self.assertEqual(result["self"]["accepted"], 0)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state_hash"], anchored_hash)
        self.assertEqual(len(state["entry_ids"]), 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR WIRE anchor_stale: honest growth "
              "past the anchor refused under its own name, not "
              "adopted, not called a mismatch")

        self._write_anchor_from(b)
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        self.assertEqual(
            result["state_hash"],
            node_server.fetch_state(b.base_url)["state_hash"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["entries"], 4)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

    # Case 15, tampering in transit: an entry edited in
    # transit (value changed, entry_hash left standing) is
    # refused by Gate 3's own verification on receipt -
    # tampered_entry, case 8's stated order, anchor or not -
    # and the anchored server's state is unchanged.
    def test_tampered_entry_in_transit_refused(self):
        anchored, b = self._converged_anchored_server()
        anchored_hash = node_server.fetch_state(anchored.base_url)["state_hash"]

        entries = node_server.fetch_entries(b.base_url)
        tampered = [tamper_entry(entries[0], value="evil-value")]
        tampered += entries[1:]
        result = node_server.push_entries(anchored.base_url, tampered)
        self.assertTrue(result["divergence_detected"])
        self.assertEqual(result["reason"], "tampered_entry")
        self.assertEqual(result["accepted"], 0)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state_hash"], anchored_hash)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR WIRE tampered_entry: an entry "
              "edited in transit refused by Gate 3's receipt "
              "verification, the anchored state unchanged")

    # Case 15, unreadable anchor: with the anchor file gone,
    # /health still answers - mesh_anchor_status
    # "anchor_unreadable" - and a mesh receive returns the
    # SyncResult shape under that reason instead of dropping
    # the connection; the state is unchanged, and restoring
    # the anchor file restores normal consultation.
    def test_unreadable_mesh_anchor_refused_in_shape(self):
        anchored, b = self._converged_anchored_server()
        anchored_hash = node_server.fetch_state(anchored.base_url)["state_hash"]

        self.anchor_path.unlink()
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchor_unreadable")
        result = node_server.push_entries(
            anchored.base_url, node_server.fetch_entries(b.base_url))
        self.assertTrue(result["divergence_detected"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["reason"], "anchor_unreadable")
        self.assertEqual(result["accepted"], 0)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(state["state_hash"], anchored_hash)
        print("MESH ANCHOR WIRE anchor_unreadable: health "
              "answers and the receive refuses in the "
              "SyncResult shape - no dropped connection")

        self._write_anchor_from(b)
        result = node_server.push_entries(
            anchored.base_url, node_server.fetch_entries(b.base_url))
        self.assertFalse(result["divergence_detected"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")


class SignedMeshAnchorWireTests(MeshAnchorWireSetup):

    def setUp(self):
        super().setUp()
        central = self.tmp / "central"
        channel = RegistrationChannel(
            central / "registry.jsonl", central / "audit.jsonl")
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.secret_file = self.tmp / "outside" / "anchor-secret.txt"
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")

    def _server_args(self):
        return [
            "--mesh-anchor", str(self.anchor_path),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(self.secret_file),
        ]

    # Case 15, signed mesh anchor over the wire: the anchored
    # server verifies the anchor's signature at every
    # consultation (case 11). Converged honestly, it then
    # faces the same heads anchored by a DIFFERENT enrolled
    # identity: the next sync refuses as
    # anchor_signature_invalid, in the result and on /health,
    # and nothing is adopted. Restoring alice's genuine
    # anchor returns the lifecycle to its own names: the
    # honest growth is anchor_stale, and after alice's
    # same-signer re-anchor (history appended) the sync
    # lands and the processes converge.
    def test_signed_mesh_anchor_signature_invalid_then_lifecycle(self):
        a, b = self._honest_pair()
        self._write_anchor_from(
            b, signer="alice", secret_hex=self.alice_secret)
        good_copy = self.tmp / "outside" / "mesh-anchor-good.json"
        shutil.copyfile(self.anchor_path, good_copy)
        a.stop()

        anchored = self._start("mesh-a-signed", "mesh-a",
                               self._server_args())
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")

        bob_anchor = self.tmp / "bob-outside" / "mesh-anchor.json"
        entries = node_server.fetch_entries(b.base_url)
        write_mesh_anchor(view_of(entries), bob_anchor,
                          signer="bob", secret_hex=self.bob_secret)
        shutil.copyfile(bob_anchor, self.anchor_path)
        node_server.mutate(b.base_url, "level", "3")

        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertTrue(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["reason"],
                         "anchor_signature_invalid")
        self.assertEqual(result["self"]["accepted"], 0)
        state = node_server.fetch_state(anchored.base_url)
        self.assertEqual(len(state["entry_ids"]), 3)
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["mesh_anchor_status"],
                         "anchor_signature_invalid")
        print("MESH ANCHOR WIRE anchor_signature_invalid: "
              "the same heads signed by another identity "
              "refused at consultation, in the sync result "
              "and on /health alike")

        shutil.copyfile(good_copy, self.anchor_path)
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertEqual(result["self"]["reason"], "anchor_stale")
        self.assertEqual(result["self"]["accepted"], 0)

        self._write_anchor_from(
            b, signer="alice", secret_hex=self.alice_secret)
        result = node_server.request_sync(anchored.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 1)
        self.assertEqual(
            result["state_hash"],
            node_server.fetch_state(b.base_url)["state_hash"])
        health = node_server.fetch_health(anchored.base_url)
        self.assertEqual(health["entries"], 4)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR WIRE signed lifecycle: genuine "
              "anchor restored - growth anchor_stale, "
              "same-signer re-anchor, sync converged")


class SignedTlsMeshAnchorTests(MeshAnchorWireSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        central = self.tmp / "central"
        channel = RegistrationChannel(
            central / "registry.jsonl", central / "audit.jsonl")
        self.alice_secret = channel.enroll_user("alice")
        self.secret_file = self.tmp / "outside" / "anchor-secret.txt"
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds",
            ["mesh-a", "mesh-b", "mesh-victim", "test-client"])
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls(self, node_id):
        cert = self.creds.nodes[node_id]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 15 over the protected transport, with a signed
    # mesh anchor: two TLS processes converge; the anchor is
    # written signed over their state; a fresh TLS server
    # configured with the mesh anchor and its signing
    # identity is first pushed a rewritten mesh-a chain over
    # the wire (built in-process in case 5/8's shape and
    # carried by POST /entries, the receive this case puts
    # under consultation) and refuses it as anchor_mismatch,
    # adopting nothing; the same server then converges with
    # the honest peer over a server-to-server /sync and
    # reports "anchored" on /health - all over mutual TLS.
    def test_mesh_anchor_over_protected_link(self):
        a = self._start("tls-a", "mesh-a", tls=self._tls("mesh-a"))
        b = self._start("tls-b", "mesh-b", tls=self._tls("mesh-b"))
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        node_server.mutate(b.base_url, "region", "north")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        anchored_state_hash = node_server.fetch_state(b.base_url)["state_hash"]
        self._write_anchor_from(
            b, signer="alice", secret_hex=self.alice_secret)
        a.stop()

        victim = self._start(
            "tls-victim", "mesh-victim",
            ["--mesh-anchor", str(self.anchor_path),
             "--mesh-anchor-signer", "alice",
             "--mesh-anchor-secret-file", str(self.secret_file)],
            tls=self._tls("mesh-victim"))
        rogue = SyncNode("mesh-a")
        rogue.mutate("greeting", "pwned")
        rogue.mutate("mode", "admin")
        self.assertTrue(all(e.hash_verifies() for e in rogue.entries()))
        result = node_server.push_entries(victim.base_url, rogue.entries())
        self.assertTrue(result["divergence_detected"])
        self.assertEqual(result["reason"], "anchor_mismatch")
        self.assertEqual(result["accepted"], 0)
        state = node_server.fetch_state(victim.base_url)
        self.assertEqual(state["entry_ids"], [])
        health = node_server.fetch_health(victim.base_url)
        self.assertTrue(health["mesh_anchor_configured"])
        self.assertEqual(health["mesh_anchor_status"], "anchor_mismatch")
        print("MESH ANCHOR WIRE (mutual TLS, signed anchor): "
              "a rewritten chain pushed over the protected "
              "link refused as anchor_mismatch, nothing adopted")

        result = node_server.request_sync(victim.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(result["self"]["accepted"], 3)
        self.assertEqual(result["state_hash"], anchored_state_hash)
        health = node_server.fetch_health(victim.base_url)
        self.assertEqual(health["entries"], 3)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("MESH ANCHOR WIRE (mutual TLS, signed anchor): "
              "the same server then converged with the honest "
              "peer over /sync; /health reports anchored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
