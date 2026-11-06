"""
Automated tests for the networked node server (experiment 007).

Each test spawns real node-server processes (node_server.py) on
ephemeral localhost ports and talks to them only over HTTP. The
outcomes under test:

1. Two server processes exchange mesh entries across the process
   boundary and converge (server-to-server /sync, plus client-side
   verification of entries fetched over HTTP).
2. A registry snapshot and a registered policy cross the process
   boundary: the snapshot verifies with Gate 2's standalone verifier
   on receipt, the policy is served over HTTP by an opted-in process
   (and refused by a non-opted-in one), and a tampered entry pushed
   over HTTP is rejected by Gate 3 verification on receipt - the
   transport does not bypass it. A server started over a tampered
   snapshot refuses to start.
3. Killing one server process does not take the state or the policy
   down with it: the survivors still serve, and they converge with
   each other on new state written after the kill, including the
   killed process's already-synced entries.

Process cleanup is part of the contract: every spawned server is
registered with addCleanup the moment it starts, and cleanup
terminates, waits, and kills if needed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_network_nodes.py"
    python3 packages/synchronization/test_network_nodes.py

Standard library only. Localhost only; nothing leaves this machine.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from policy_node import PolicyNode, verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402
from sync_mesh import SyncNode, tamper_entry  # noqa: E402

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class ServerProcess:
    """One node_server.py child process, reliably cleaned up."""

    def __init__(self, test: unittest.TestCase, workdir: Path, name: str, args: list[str]):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        self.log_path = workdir / f"{name}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", name,
            "--port", "0",
            "--port-file", str(self.port_file),
            *args,
        ]
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)
        self.base_url: str | None = None

    def wait_ready(self, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.name} exited early rc={self.proc.returncode}; "
                    f"log:\n{self.log_path.read_text(encoding='utf-8')}"
                )
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.name} not ready in {timeout}s")

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

    def kill(self) -> None:
        """Simulate the host process dying: SIGKILL, no cleanup."""
        self.proc.kill()
        self.proc.wait(timeout=5)


class NetworkedNodeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # Setup phase (in this test process only): Gate 1 registers
        # alice's policy through the real channel. The server
        # processes started below never see the channel; each gets
        # its OWN copy of the snapshot file and the payloads file.
        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(self.registry_log, central / "audit.jsonl")
        secret = channel.enroll_user("alice")
        self.record, created = channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE),
        )
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(
            json.dumps({self.record.policy_hash: ALICE_CODE.hex()}),
            encoding="utf-8",
        )

    def _snapshot_copy(self, name: str) -> Path:
        dest = self.tmp / f"{name}-snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def start_server(self, name: str, *, serve: bool = True,
                     with_policy: bool = True, snapshot: Path | None = None) -> ServerProcess:
        args: list[str] = []
        if with_policy:
            args += [
                "--snapshot", str(snapshot or self._snapshot_copy(name)),
                "--payloads", str(self.payloads_file),
            ]
            if serve:
                args.append("--serve-policies")
        server = ServerProcess(self, self.tmp, name, args)
        server.wait_ready()
        return server

    # -- Outcome 1: two processes exchange entries and converge ------

    def test_two_processes_exchange_entries_and_converge(self):
        a = self.start_server("net-a", with_policy=False)
        b = self.start_server("net-b", with_policy=False)
        self.assertNotEqual(a.base_url, b.base_url)  # distinct processes/ports

        # Each process mutates its OWN node, driven over HTTP.
        node_server.mutate(a.base_url, "region", "us-central")
        node_server.mutate(a.base_url, "shared", "from-a")
        node_server.mutate(b.base_url, "tier", "member")
        node_server.mutate(b.base_url, "shared", "from-b")

        state_a = node_server.fetch_state(a.base_url)
        state_b = node_server.fetch_state(b.base_url)
        self.assertNotEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(len(state_a["entry_ids"]), 2)
        self.assertEqual(len(state_b["entry_ids"]), 2)

        # Server-to-server sync: A pulls B's entries, verifies and
        # merges them inside A's process, then pushes the merged set
        # to B, whose process verifies them on receipt.
        outcome = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertFalse(outcome["self"]["rolled_back"])
        self.assertEqual(outcome["self"]["accepted"], 2)
        self.assertFalse(outcome["peer"]["divergence_detected"])
        self.assertEqual(outcome["peer"]["accepted"], 2)

        # Converged: identical state, hash, and entry set, read back
        # from each process separately.
        state_a = node_server.fetch_state(a.base_url)
        state_b = node_server.fetch_state(b.base_url)
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(state_a["state"], state_b["state"])
        self.assertEqual(state_a["entry_ids"], state_b["entry_ids"])
        self.assertEqual(len(state_a["entry_ids"]), 4)
        self.assertEqual(state_a["state"]["region"], "us-central")
        self.assertEqual(state_a["state"]["tier"], "member")
        self.assertIn(state_a["state"]["shared"], {"from-a", "from-b"})

        # Entries fetched over HTTP also verify on the CLIENT side:
        # a fresh local SyncNode merges A's entries through the same
        # receive_entries path and lands on the same state hash.
        local = SyncNode("client-verifier")
        result = local.receive_entries(node_server.fetch_entries(a.base_url))
        self.assertFalse(result.divergence_detected)
        self.assertEqual(local.state_hash(), state_a["state_hash"])

    # -- Outcome 2: snapshot + policy across the boundary, verified --

    def test_snapshot_and_policy_cross_boundary_with_verification(self):
        a = self.start_server("net-a", serve=True)
        c = self.start_server("net-c", serve=False)  # holds, did not opt in

        # The registered policy is served over HTTP by A's process.
        # The registration channel is never involved at runtime; the
        # servers only ever had snapshot copies.
        status, body = node_server.fetch_policy(a.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["policy_hash"], self.record.policy_hash)
        self.assertEqual(body["code_hash"], self.record.code_hash)
        self.assertEqual(body["validation_channel"], self.record.validation_channel)
        self.assertEqual(body["record_hash"], self.record.record_hash)
        self.assertEqual(body["served_by"], "net-a")

        # A node that did not opt in holds the same verified policy
        # and still refuses to serve it, over HTTP too (403).
        status, body = node_server.fetch_policy(c.base_url, self.record.policy_hash)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "not_serving")

        # Unknown policy: 404, not a guess.
        status, _ = node_server.fetch_policy(a.base_url, "ab" * 32)
        self.assertEqual(status, 404)

        # The registry snapshot crosses as raw bytes and verifies on
        # receipt with Gate 2's standalone verifier - the exact check
        # a node runs before trusting a snapshot. A PolicyNode built
        # over the RECEIVED bytes serves the policy in this process.
        received = self.tmp / "received-snapshot.jsonl"
        received.write_bytes(node_server.fetch_snapshot_bytes(a.base_url))
        entries = verify_registry_snapshot(received)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["policy_hash"], self.record.policy_hash)
        received_node = PolicyNode(
            node_id="receiver",
            registry_snapshot_path=received,
            payloads={self.record.policy_hash: ALICE_CODE},
            serve_policies=True,
        )
        self.assertEqual(
            received_node.serve_policy(self.record.policy_hash).code, ALICE_CODE
        )

        # Verification is not bypassed on receipt: a tampered entry
        # pushed over HTTP gets Gate 3's divergence + rollback from
        # A's process, and A's state is untouched by it.
        node_server.mutate(a.base_url, "mode", "normal")
        before = node_server.fetch_state(a.base_url)
        real = next(
            e for e in node_server.fetch_entries(a.base_url) if e.origin == "net-a"
        )
        forged = tamper_entry(real, value="forged-over-http")
        self.assertFalse(forged.hash_verifies())
        result = node_server.push_entries(a.base_url, [forged])
        self.assertTrue(result["divergence_detected"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["reason"], "tampered_entry")
        after = node_server.fetch_state(a.base_url)
        self.assertEqual(after["state_hash"], before["state_hash"])
        self.assertEqual(after["state"], before["state"])

    def test_server_refuses_to_start_over_tampered_snapshot(self):
        # Edit one snapshot entry: the hash chain no longer verifies.
        tampered = self.tmp / "tampered-snapshot.jsonl"
        entry = json.loads(self.registry_log.read_text(encoding="utf-8").splitlines()[0])
        entry["name"] = "forged-name"
        tampered.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

        server = ServerProcess(self, self.tmp, "net-tampered", [
            "--snapshot", str(tampered),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
        ])
        # The process must exit on its own, nonzero, without serving.
        rc = server.proc.wait(timeout=10)
        self.assertNotEqual(rc, 0)
        log = server.log_path.read_text(encoding="utf-8")
        self.assertIn("TamperedRecord", log)

    # -- Outcome 3: a killed process does not take the mesh down -----

    def test_killed_process_survivors_still_serve_and_converge(self):
        a = self.start_server("net-a", serve=True)
        b = self.start_server("net-b", serve=True)
        c = self.start_server("net-c", serve=True)

        # State originates in two processes, then replicates to all
        # three before anyone dies.
        node_server.mutate(a.base_url, "origin-a", "written-by-a")
        node_server.mutate(b.base_url, "origin-b", "written-by-b")
        node_server.request_sync(a.base_url, b.base_url)
        node_server.request_sync(a.base_url, c.base_url)
        node_server.request_sync(b.base_url, c.base_url)
        hashes = {
            node_server.fetch_state(s.base_url)["state_hash"] for s in (a, b, c)
        }
        self.assertEqual(len(hashes), 1)  # all three converged

        # Kill B's process outright: no shutdown hook, no goodbye.
        b.kill()
        self.assertIsNotNone(b.proc.poll())
        with self.assertRaises(OSError):
            urllib.request.urlopen(b.base_url + "/health", timeout=3)

        # The survivors still serve: mesh state from A, the policy
        # from C - including B's entry, which survives B because it
        # had already replicated.
        state_a = node_server.fetch_state(a.base_url)
        self.assertEqual(state_a["state"]["origin-a"], "written-by-a")
        self.assertEqual(state_a["state"]["origin-b"], "written-by-b")
        status, body = node_server.fetch_policy(c.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["served_by"], "net-c")

        # And the mesh keeps working without B: new state written on
        # C after the kill converges onto A.
        node_server.mutate(c.base_url, "after-kill", "written-by-c")
        node_server.request_sync(c.base_url, a.base_url)
        state_a = node_server.fetch_state(a.base_url)
        state_c = node_server.fetch_state(c.base_url)
        self.assertEqual(state_a["state_hash"], state_c["state_hash"])
        self.assertEqual(state_a["state"]["after-kill"], "written-by-c")
        self.assertEqual(state_a["state"]["origin-b"], "written-by-b")
        self.assertEqual(len(state_a["entry_ids"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
