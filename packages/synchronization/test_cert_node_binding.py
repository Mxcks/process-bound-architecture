"""
Automated tests for certificate binding to a claimed node id
(experiment 017).

Each test spawns real node-server processes (node_server.py) on
ephemeral localhost ports, started with mutual-TLS arguments plus
--tls-bind-identity, and talks to them over HTTPS. The outcomes
under test (tests/test-cases/sync-mesh.md, case 9):

1. Certificates are issued for a specific node id, carried in the
   certificate itself as a URI subjectAltName
   (urn:pba:node:<node-id>), and peers whose claim matches their
   certificate work exactly as in experiment 014: two processes
   converge over the protected link and a registered policy
   crosses verified.
2. A peer presenting a VALID certificate issued by the same CA
   for a DIFFERENT node id than the one it claims is refused by
   the accepting node with a plainly stated identity_mismatch
   (HTTP 403 naming claimed and certified ids) on the health,
   entries, and policy paths. This refusal is distinct from
   experiment 014's: no-credential and rogue-CA peers fail the
   handshake itself and get no HTTP answer at all, while the
   mismatched peer's handshake succeeds and the same certificate
   is served when its claim matches its issued id. After the
   refusals the server still serves a correctly identified peer.
3. A process claiming an id its certificate was not issued for
   refuses to start when binding is enabled (its log states the
   identity mismatch plainly); started without binding
   (experiment 014 mode), such a peer is refused by a
   binding-enabled node that initiates sync toward it: the
   initiator checks the peer's certificate id against the id the
   peer claims in its health response before pulling any entries,
   and no state moves.
4. A mismatched peer process initiating sync toward a
   binding-enabled node is refused on the incoming link (its
   claim and its certificate disagree), nothing merges in either
   node, and correctly identified peers still sync afterward.

Test credentials are generated inside the tests at runtime
(protected_link.generate_test_credentials, openssl on the
machine) into each test's temporary directory. No credential,
key, or certificate file is committed or leaves that directory.

Process cleanup is part of the contract: every spawned server is
registered with addCleanup the moment it starts, and cleanup
terminates, waits, and kills if needed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_cert_node_binding.py"
    python3 packages/synchronization/test_cert_node_binding.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
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
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from protected_link import (  # noqa: E402
    IdentityMismatch,
    cert_file_node_id,
    peercert_node_id,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class ServerProcess:
    """One node_server.py child process speaking mutual TLS with
    identity binding (unless bind=False), reliably cleaned up.

    label names the port/log files; node_id is the id the process
    CLAIMS; cert_name names the credential it PRESENTS. Keeping
    the two separate is the whole point of this suite: a mismatch
    between them is the attack under test."""

    def __init__(self, test: unittest.TestCase, workdir: Path, label: str,
                 node_id: str, cert_name: str, args: list[str], creds,
                 bind: bool = True):
        self.label = label
        self.node_id = node_id
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[cert_name]
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id,
            "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            *args,
        ]
        if bind:
            cmd.append("--tls-bind-identity")
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)
        self.base_url: str | None = None
        self.port: int | None = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.label} exited early rc={self.proc.returncode}; "
                    f"log:\n{self.log_path.read_text(encoding='utf-8')}"
                )
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.port = port
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError, ssl.SSLError):
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


class CertNodeBindingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(node_server.clear_client_tls)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # Test credentials, generated at runtime into this test's
        # temporary directory: one test CA, one certificate per
        # node identity plus one for this test process as a client,
        # and a rogue CA with its own client certificate.
        self.creds = protected_link.generate_test_credentials(
            tmp / "creds",
            ["net-a", "net-b", "net-c", "test-client"],
        )
        # This test process claims "test-client" on its links,
        # matching the certificate it presents.
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(
            client.cert, client.key, self.creds.ca_cert, node_id="test-client"
        )

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

    def _snapshot_copy(self, label: str) -> Path:
        dest = self.tmp / f"{label}-snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def start_server(self, label: str, *, node_id: str | None = None,
                     cert_name: str | None = None, serve: bool = True,
                     with_policy: bool = True, bind: bool = True) -> ServerProcess:
        node_id = node_id or label
        cert_name = cert_name or node_id
        args: list[str] = []
        if with_policy:
            args += [
                "--snapshot", str(self._snapshot_copy(label)),
                "--payloads", str(self.payloads_file),
            ]
            if serve:
                args.append("--serve-policies")
        server = ServerProcess(
            self, self.tmp, label, node_id, cert_name, args, self.creds,
            bind=bind,
        )
        server.wait_ready()
        return server

    def attempt(self, base_url: str, path: str, cred_name: str,
                claimed: str) -> tuple[int, dict]:
        """One raw HTTPS request presenting cred_name's certificate
        while claiming `claimed` in the identity header. Returns
        (status, body) - an HTTP answer, as opposed to a handshake
        failure, which raises."""
        cred = self.creds.nodes[cred_name]
        context = protected_link.client_context(
            cred.cert, cred.key, self.creds.ca_cert
        )
        req = urllib.request.Request(base_url + path, method="GET")
        req.add_header(node_server.CLAIM_HEADER, claimed)
        try:
            with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # -- Outcome 1: the id is in the certificate; matched peers work --

    def test_cert_carries_issued_node_id_and_matched_peers_converge(self):
        # The id is carried in the certificate itself: every
        # generated leaf certificate names exactly the node id it
        # was issued for, including the rogue CA's client (a
        # different authority, but still an identified cert).
        for name, cred in self.creds.nodes.items():
            self.assertEqual(cert_file_node_id(cred.cert), name)
        self.assertEqual(
            cert_file_node_id(self.creds.rogue_client_cert), "rogue-client"
        )

        a = self.start_server("net-a", serve=True)
        b = self.start_server("net-b", with_policy=False)

        # The initiating side's check agrees with reality: probing
        # A's link yields the id A claims, and asking for a
        # different expected id is an IdentityMismatch.
        self.assertEqual(node_server.verify_peer_identity(a.base_url), "net-a")
        self.assertEqual(
            node_server.verify_peer_identity(a.base_url, "net-a"), "net-a"
        )
        with self.assertRaises(IdentityMismatch) as ctx:
            node_server.verify_peer_identity(a.base_url, "net-b")
        self.assertEqual(ctx.exception.claimed, "net-b")
        self.assertEqual(ctx.exception.certified, "net-a")

        # Experiment 014 behavior, unchanged, between correctly
        # identified peers: mutate on both sides, sync (which
        # exercises BOTH directions of the binding: A initiates and
        # checks B's certificate against B's claim before pulling,
        # and B accepts A's incoming links only because A's claim
        # matches A's certificate), converge, policy crosses.
        node_server.mutate(a.base_url, "region", "us-central")
        node_server.mutate(b.base_url, "tier", "member")
        outcome = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertFalse(outcome["peer"]["divergence_detected"])
        state_a = node_server.fetch_state(a.base_url)
        state_b = node_server.fetch_state(b.base_url)
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(state_a["state"], state_b["state"])
        self.assertEqual(len(state_a["entry_ids"]), 2)

        status, body = node_server.fetch_policy(a.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["served_by"], "net-a")

    # -- Outcome 2: mismatched claim refused, distinctly -------------

    def test_mismatched_claim_refused_distinctly_and_server_still_serves(self):
        a = self.start_server("net-a", serve=True)

        # net-b's certificate is VALID - same CA, handshake succeeds
        # on possession alone - but the claim says net-a, an id
        # that certificate was not issued for. Every path refuses
        # with a plainly stated identity_mismatch over HTTP.
        for path in ("/health", "/entries", f"/policy/{self.record.policy_hash}"):
            status, body = self.attempt(a.base_url, path, "net-b", "net-a")
            self.assertEqual(status, 403, path)
            self.assertEqual(body["error"], "identity_mismatch")
            self.assertEqual(body["claimed"], "net-a")
            self.assertEqual(body["certified"], "net-b")

        # Distinctness, part 1: the refusal is the binding, not the
        # credential. The SAME net-b certificate, claiming the id
        # it was actually issued for, is served.
        status, body = self.attempt(a.base_url, "/health", "net-b", "net-b")
        self.assertEqual(status, 200)
        self.assertEqual(body["node_id"], "net-a")

        # Distinctness, part 2: experiment 014's refusals never get
        # an HTTP answer at all - no credential and rogue CA both
        # die in the handshake. (If either attempt returned, the
        # attempt() helper would have produced a status instead of
        # raising.)
        url = a.base_url + "/health"
        anon = protected_link.anonymous_client_context(self.creds.ca_cert)
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            req = urllib.request.Request(url, method="GET")
            req.add_header(node_server.CLAIM_HEADER, "net-a")
            urllib.request.urlopen(req, timeout=10, context=anon).read()
        rogue = protected_link.client_context(
            self.creds.rogue_client_cert, self.creds.rogue_client_key,
            self.creds.ca_cert,
        )
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            req = urllib.request.Request(url, method="GET")
            req.add_header(node_server.CLAIM_HEADER, "rogue-client")
            urllib.request.urlopen(req, timeout=10, context=rogue).read()

        # The server logs the mismatch plainly, and the refusals did
        # not wedge it: a correctly identified peer is still served.
        self.assertIn("identity_mismatch", a.log_text())
        health = node_server.fetch_health(a.base_url)
        self.assertTrue(health["ok"])
        status, body = node_server.fetch_policy(a.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

    # -- Outcome 3: a false-claiming process cannot start bound, and
    #    is refused by an initiator that checks before pulling -------

    def test_false_claiming_process_refused_at_start_and_by_initiator(self):
        # Part A: with binding enabled, a process started claiming
        # net-a while presenting net-b's certificate refuses to
        # start, and its log states the mismatch plainly.
        raw = ServerProcess(
            self, self.tmp, "false-start", "net-a", "net-b",
            ["--snapshot", str(self._snapshot_copy("false-start")),
             "--payloads", str(self.payloads_file), "--serve-policies"],
            self.creds, bind=True,
        )
        deadline = time.monotonic() + 20
        while raw.proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(raw.proc.poll(), "false-claiming process kept running")
        self.assertNotEqual(raw.proc.returncode, 0)
        log = raw.log_text()
        self.assertIn("identity_mismatch", log)
        self.assertIn("net-a", log)
        self.assertIn("net-b", log)

        # Part B: the same false claim, started WITHOUT binding
        # (experiment 014 mode, so it runs), is refused by a
        # binding-enabled initiator: net-c checks the impostor's
        # certificate against its claimed id BEFORE pulling entries.
        impostor = self.start_server(
            "impostor", node_id="net-a", cert_name="net-b",
            serve=True, bind=False,
        )
        health = node_server.fetch_health(impostor.base_url)
        self.assertEqual(health["node_id"], "net-a")  # the claim
        self.assertEqual(
            cert_file_node_id(self.creds.nodes["net-b"].cert), "net-b"
        )  # the certificate's actual id
        with self.assertRaises(IdentityMismatch) as ctx:
            node_server.verify_peer_identity(impostor.base_url)
        self.assertEqual(ctx.exception.claimed, "net-a")
        self.assertEqual(ctx.exception.certified, "net-b")

        node_server.mutate(impostor.base_url, "impostor-key", "must-not-move")
        c = self.start_server("net-c", with_policy=False)
        status, body = node_server.post_json(
            c.base_url, "/sync", {"peer": impostor.base_url}
        )
        self.assertEqual(status, 502)
        self.assertEqual(body["error"], "identity_mismatch")
        self.assertEqual(body["claimed"], "net-a")
        self.assertEqual(body["certified"], "net-b")

        # No state moved: the initiator holds nothing of the
        # impostor's, and the impostor gained nothing either.
        state_c = node_server.fetch_state(c.base_url)
        self.assertEqual(state_c["entry_ids"], [])
        state_i = node_server.fetch_state(impostor.base_url)
        self.assertEqual(state_i["state"], {"impostor-key": "must-not-move"})

    # -- Outcome 4: a mismatched initiator is refused on acceptance --

    def test_mismatched_initiator_refused_on_acceptance_nothing_merges(self):
        a = self.start_server("net-a", serve=True)
        node_server.mutate(a.base_url, "honest", "written-by-a")
        before_a = node_server.fetch_state(a.base_url)

        # An impostor process claiming net-b but presenting net-c's
        # certificate. Started without binding, so it runs in
        # experiment 014 mode and initiates sync toward A.
        impostor = self.start_server(
            "impostor-init", node_id="net-b", cert_name="net-c",
            with_policy=False, bind=False,
        )
        node_server.mutate(impostor.base_url, "rogue-write", "written-by-impostor")
        before_i = node_server.fetch_state(impostor.base_url)

        status, body = node_server.post_json(
            impostor.base_url, "/sync", {"peer": a.base_url}
        )
        # A's accepting side refused the incoming link: A's claim
        # check saw claimed net-b against certified net-c, answered
        # 403 identity_mismatch, and the impostor's sync reports
        # the failure instead of a merge.
        self.assertEqual(status, 502)
        self.assertIn("identity_mismatch", json.dumps(body))
        self.assertIn("net-b", json.dumps(body))
        self.assertIn("net-c", json.dumps(body))

        # Nothing merged in either node.
        after_a = node_server.fetch_state(a.base_url)
        after_i = node_server.fetch_state(impostor.base_url)
        self.assertEqual(after_a["state_hash"], before_a["state_hash"])
        self.assertEqual(after_a["state"], {"honest": "written-by-a"})
        self.assertEqual(after_i["state_hash"], before_i["state_hash"])
        self.assertEqual(after_i["state"], {"rogue-write": "written-by-impostor"})
        self.assertIn("identity_mismatch", a.log_text())

        # And a correctly identified peer still syncs with A
        # afterward: the refusal was the mismatch, not the server.
        b = self.start_server("net-b", with_policy=False)
        node_server.mutate(b.base_url, "tier", "member")
        outcome = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        state_a = node_server.fetch_state(a.base_url)
        state_b = node_server.fetch_state(b.base_url)
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(state_a["state"]["tier"], "member")
        self.assertEqual(state_a["state"]["honest"], "written-by-a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
