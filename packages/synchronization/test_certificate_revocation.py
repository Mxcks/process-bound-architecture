"""
Automated tests for certificate revocation on the protected
links (experiment 034).

Each test spawns real node-server processes (node_server.py) on
ephemeral localhost ports, started with mutual-TLS arguments plus
--tls-bind-identity, and - in the legs under test - --tls-crl.
The outcomes under test (tests/test-cases/sync-mesh.md, case 23):

1. A credentialed peer works exactly as in experiments 014/017
   BEFORE revocation; after its certificate is revoked at the CA
   and the accepting server is restarted with a CRL naming that
   certificate's serial, the same presentation is refused AT THE
   HANDSHAKE - no endpoint runs, no HTTP answer comes back, and
   the refused client's own error names the refusal
   "certificate revoked" (the alert the accepting side returns
   when it kills the handshake: sslv3 alert certificate
   revoked). A sibling peer the CRL does not name keeps working
   against the same restarted server.
2. Three doors, three refusals, asserted separately: a revoked
   certificate fails the handshake named "certificate revoked";
   a rogue-CA credential fails the handshake as in case 8 with
   NO revocation named anywhere in the evidence; a valid,
   unrevoked certificate claiming the wrong node id completes
   the handshake and is refused over HTTP 403 as
   identity_mismatch (case 9). The layers differ, and the suite
   asserts the difference, not just the refusal.
3. The initiating direction: a client context configured with a
   CRL naming a SERVER's certificate refuses that server at the
   handshake when it connects out (the refusal names
   "certificate revoked" on the initiating side, and no state
   moves); the same in server-to-server form, a peer process
   started with --tls-crl refusing to sync with the revoked
   server. A CRL that does not name the server's certificate
   connects and syncs normally.
4. A stale CRL enforces exactly its own list: a CRL issued
   before a second revocation refuses the first certificate and
   still accepts the second; only the later CRL refuses both.
   An empty CRL (issued before any revocation) refuses nobody.
5. Revocation names serials, not node ids: a NEW certificate
   issued by the same CA for the same node id, with a serial
   the CRL does not name, still completes the handshake and
   works against the CRL-enforcing server.
6. A malformed CRL refuses the start: a --tls-crl path that
   does not exist, and one whose contents carry no parseable
   CRL, each exit the process nonzero at startup naming
   crl_unreadable, serving nothing. A process must never run
   believing it enforces revocations it does not hold.
7. Control: a server started WITHOUT --tls-crl accepts a
   certificate the CA has revoked - the list exists but was
   never handed to it, and behavior is exactly experiments
   014/017's, as the unmodified earlier suites in the same run
   also prove.

Test credentials come from a CA that keeps its own books
(protected_link.generate_revocation_credentials, an openssl ca
database inside each test's temporary directory), because
revocation is entered into the CA's records and a CRL is
issued over them - the one-shot issuance of experiment 014
keeps no records to revoke into. Everything is generated at
runtime; no credential, key, or CRL file is committed or
leaves the temporary directory.

Process cleanup is part of the contract: every spawned server
is registered with addCleanup the moment it starts, and
cleanup terminates, waits, and kills if needed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_certificate_revocation.py"
    python3 packages/synchronization/test_certificate_revocation.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
"""

import json
import socket
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

import node_server  # noqa: E402
import protected_link  # noqa: E402

SERVER = HERE / "node_server.py"
NODE_NAMES = [
    "srv-main", "srv-peer", "srv-target",
    "net-a", "net-b", "net-c", "test-client",
]


class ServerProcess:
    """One node_server.py child process speaking mutual TLS with
    identity binding, reliably cleaned up. Mirrors experiment
    017's harness: label names the port/log files, node_id is the
    id the process CLAIMS, cert_name the credential it PRESENTS,
    and crl (when given) is handed to the process as --tls-crl."""

    def __init__(self, test: unittest.TestCase, workdir: Path, label: str,
                 node_id: str, cert_name: str, creds,
                 args: list[str] | None = None, crl: Path | None = None,
                 expect_start_failure: bool = False):
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
            "--tls-bind-identity",
            *(args or []),
        ]
        if crl is not None:
            cmd += ["--tls-crl", str(crl)]
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)
        self.base_url: str | None = None
        self.port: int | None = None
        self.expect_start_failure = expect_start_failure

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

    def wait_exited(self, timeout: float = 20.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rc = self.proc.poll()
            if rc is not None:
                return rc
            time.sleep(0.05)
        raise AssertionError(f"server {self.label} did not exit in {timeout}s")

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


class CertificateRevocationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(node_server.clear_client_tls)
        self.tmp = Path(self._tmp.name)

        # A CA that keeps its own books, generated at runtime
        # into this test's temporary directory: one certificate
        # per node identity (server and client roles alike), plus
        # a rogue CA with its own client certificate.
        self.creds = protected_link.generate_revocation_credentials(
            self.tmp / "creds", NODE_NAMES,
        )
        self.use_client("test-client")

    # -- helpers ---------------------------------------------------

    def use_client(self, name: str, crl: Path | None = None) -> None:
        """Point this test process's client helpers at `name`'s
        credential (claiming `name`), optionally enforcing a CRL
        against the servers it connects to."""
        cred = self.creds.nodes[name]
        node_server.set_client_tls(
            cred.cert, cred.key, self.creds.ca_cert, node_id=name, crl=crl,
        )

    def start_server(self, label: str, *, node_id: str | None = None,
                     cert_name: str | None = None,
                     crl: Path | None = None) -> ServerProcess:
        server = ServerProcess(
            self, self.tmp, label, node_id or label, cert_name or node_id or label,
            self.creds, crl=crl,
        )
        server.wait_ready()
        return server

    def attempt(self, base_url: str, cert: Path, key: Path, claimed: str,
                ca_cert: Path | None = None) -> tuple[int, dict]:
        """One raw HTTPS GET /health presenting the given
        certificate while claiming `claimed`. Returns
        (status, body) - an HTTP answer, as opposed to a
        handshake failure, which raises."""
        context = protected_link.client_context(
            cert, key, ca_cert or self.creds.ca_cert
        )
        req = urllib.request.Request(base_url + "/health", method="GET")
        req.add_header(node_server.CLAIM_HEADER, claimed)
        try:
            with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def attempt_raises(self, base_url: str, cert: Path, key: Path,
                       claimed: str, ca_cert: Path | None = None) -> Exception:
        """The same attempt, where the expected outcome is that
        no HTTP answer exists at all: returns the exception."""
        with self.assertRaises(
            (urllib.error.URLError, OSError, ssl.SSLError)
        ) as ctx:
            self.attempt(base_url, cert, key, claimed, ca_cert=ca_cert)
        return ctx.exception

    def handshake_error(self, base_url: str, cert: Path, key: Path,
                        ca_cert: Path | None = None) -> Exception | None:
        """One raw handshake presenting the given certificate,
        with no HTTP request on top. Returns the error the
        handshake died with, or None if it completed. The probe
        forces TLS 1.2 (the protected links' floor) because the
        alert that NAMES a handshake refusal - certificate
        revoked, unknown ca - is delivered in-band during a 1.2
        handshake, deterministically; under TLS 1.3 the same
        refusal can surface on the first read as a bare EOF,
        which names nothing. The refusal itself is identical in
        both versions: no endpoint is ever reached."""
        context = protected_link.client_context(
            cert, key, ca_cert or self.creds.ca_cert
        )
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        host, port = base_url.removeprefix("https://").split(":")
        raw = socket.create_connection((host, int(port)), timeout=10)
        try:
            with context.wrap_socket(raw, server_hostname=host):
                return None
        except (ssl.SSLError, OSError) as exc:
            return exc

    # -- Outcome 1: the story - works before, refused after --------

    def test_revoked_peer_refused_at_handshake_after_restart(self):
        srv = self.start_server("srv-main")
        peer = self.start_server("srv-peer", node_id="net-a", cert_name="net-a")

        # Before revocation the peer works exactly as in cases
        # 8/9: it mutates its own state and syncs it to the
        # server over the protected, identity-bound link.
        node_server.mutate(peer.base_url, "region", "us-central")
        outcome = node_server.request_sync(peer.base_url, srv.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertFalse(outcome["peer"]["divergence_detected"])
        self.assertEqual(
            node_server.fetch_state(srv.base_url)["state"].get("region"),
            "us-central",
        )
        cred_a = self.creds.nodes["net-a"]
        status, _ = self.attempt(srv.base_url, cred_a.cert, cred_a.key, "net-a")
        self.assertEqual(status, 200)

        # The lifecycle at the CA: net-a's certificate is
        # revoked in the CA's records, a CRL is issued naming
        # it, and the accepting server is restarted WITH the
        # CRL. Updating the list is a restart-time operator
        # act (case 23 states the limit plainly).
        protected_link.revoke_certificate(self.creds, "net-a")
        crl = protected_link.write_crl(self.creds, "crl-after-net-a")
        srv.stop()
        srv2 = self.start_server("srv-main-2", node_id="srv-main",
                                 cert_name="srv-main", crl=crl)

        # The same presentation is now refused at the handshake:
        # a fresh mutation on the peer never reaches the server,
        # and the sync is refused before any state moves. (srv2
        # is a fresh process: what must hold is that the refused
        # mutation is nowhere in its state, not that the old
        # value survived - the old process is gone.)
        node_server.mutate(peer.base_url, "region", "eu-west")
        with self.assertRaises(AssertionError):
            node_server.request_sync(peer.base_url, srv2.base_url)
        self.assertNotIn(
            "region", node_server.fetch_state(srv2.base_url)["state"]
        )
        self.attempt_raises(srv2.base_url, cred_a.cert, cred_a.key, "net-a")
        # The refusal is NAMED, in the exchange itself: the
        # alert the accepting side returns when it kills the
        # handshake says the certificate was revoked.
        exc = self.handshake_error(srv2.base_url, cred_a.cert, cred_a.key)
        self.assertIsNotNone(exc)
        self.assertIn("certificate revoked", str(exc).lower())

        # A sibling peer whose certificate the CRL does not name
        # keeps working against the same restarted server.
        sibling = self.start_server("srv-sibling", node_id="net-b", cert_name="net-b")
        node_server.mutate(sibling.base_url, "tier", "member")
        outcome = node_server.request_sync(sibling.base_url, srv2.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertEqual(
            node_server.fetch_state(srv2.base_url)["state"].get("tier"),
            "member",
        )

    # -- Outcome 2: three doors, three refusals --------------------

    def test_three_refusals_are_distinguishable(self):
        protected_link.revoke_certificate(self.creds, "net-a")
        crl = protected_link.write_crl(self.creds, "crl-three-doors")
        srv = self.start_server("srv-main", crl=crl)

        # (a) Revoked: the handshake fails with no HTTP answer,
        # and the refusal is named in the exchange - the alert
        # the accepting side returns when it kills the
        # handshake says the certificate was revoked.
        cred_a = self.creds.nodes["net-a"]
        self.attempt_raises(srv.base_url, cred_a.cert, cred_a.key, "net-a")
        exc = self.handshake_error(srv.base_url, cred_a.cert, cred_a.key)
        self.assertIsNotNone(exc)
        self.assertIn("certificate revoked", str(exc).lower())

        # (b) Rogue CA: the handshake fails as in case 8, with
        # no HTTP answer, and no revocation is named anywhere
        # in the evidence - the alert speaks of an unknown
        # authority, not of a list.
        self.attempt_raises(
            srv.base_url,
            self.creds.rogue_client_cert, self.creds.rogue_client_key,
            "rogue-client",
        )
        exc = self.handshake_error(
            srv.base_url,
            self.creds.rogue_client_cert, self.creds.rogue_client_key,
        )
        self.assertIsNotNone(exc)
        self.assertNotIn("revoked", str(exc).lower())

        # (c) Identity mismatch: the handshake SUCCEEDS (an HTTP
        # answer exists at all), and the refusal is the 403
        # identity_mismatch of case 9.
        cred_c = self.creds.nodes["net-c"]
        status, body = self.attempt(
            srv.base_url, cred_c.cert, cred_c.key, "net-a"
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "identity_mismatch")
        self.assertEqual(body["claimed"], "net-a")
        self.assertEqual(body["certified"], "net-c")

    # -- Outcome 3: the initiating direction ------------------------

    def test_initiating_side_refuses_revoked_server_certificate(self):
        target = self.start_server("srv-target")
        node_server.mutate(target.base_url, "origin", "target")

        # A CRL issued before the revocation names nobody.
        crl_nameless = protected_link.write_crl(self.creds, "crl-nameless")

        # This test process as initiator, enforcing a CRL that
        # names the server's certificate: the connection out is
        # refused at the handshake, naming the revocation on
        # THIS side, before any health answer exists.
        protected_link.revoke_certificate(self.creds, "srv-target")
        crl = protected_link.write_crl(self.creds, "crl-target")
        self.use_client("test-client", crl=crl)
        with self.assertRaises(
            (urllib.error.URLError, OSError, ssl.SSLError)
        ) as ctx:
            node_server.fetch_health(target.base_url)
        self.assertIn("revoked", str(ctx.exception).lower())

        # The same initiating side with a CRL that does not name
        # the server's certificate connects normally.
        self.use_client("test-client", crl=crl_nameless)
        self.assertEqual(
            node_server.fetch_health(target.base_url)["node_id"], "srv-target"
        )
        self.use_client("test-client")

        # Server-to-server form: a peer process started with
        # --tls-crl naming the target's certificate refuses to
        # sync with it - the sync reports the refusal and no
        # state moves - while the same peer syncs with an
        # unrevoked server in the same run.
        initiator = self.start_server("srv-peer", node_id="net-b", cert_name="net-b", crl=crl)
        before = node_server.fetch_state(initiator.base_url)["state_hash"]
        with self.assertRaises(AssertionError) as ctx:
            node_server.request_sync(initiator.base_url, target.base_url)
        self.assertIn("revoked", str(ctx.exception).lower())
        self.assertEqual(
            node_server.fetch_state(initiator.base_url)["state_hash"], before
        )
        other = self.start_server("srv-main")
        node_server.mutate(other.base_url, "origin", "main")
        outcome = node_server.request_sync(initiator.base_url, other.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertEqual(
            node_server.fetch_state(initiator.base_url)["state"].get("origin"),
            "main",
        )

    # -- Outcome 4: a stale CRL enforces exactly its list ----------

    def test_stale_crl_enforces_exactly_its_list(self):
        # An empty CRL, issued before any revocation exists,
        # refuses nobody.
        crl_empty = protected_link.write_crl(self.creds, "crl-empty")
        srv = self.start_server("srv-main", crl=crl_empty)
        for name in ("net-a", "net-b"):
            cred = self.creds.nodes[name]
            status, _ = self.attempt(srv.base_url, cred.cert, cred.key, name)
            self.assertEqual(status, 200)
        srv.stop()

        # Revoke net-a, issue the list; then revoke net-b and
        # issue the later list. The first list is now stale: it
        # is validly issued and enforces exactly what it names.
        protected_link.revoke_certificate(self.creds, "net-a")
        crl_stale = protected_link.write_crl(self.creds, "crl-stale")
        protected_link.revoke_certificate(self.creds, "net-b")
        crl_fresh = protected_link.write_crl(self.creds, "crl-fresh")

        srv2 = self.start_server("srv-main-2", node_id="srv-main",
                                 cert_name="srv-main", crl=crl_stale)
        cred_a, cred_b = self.creds.nodes["net-a"], self.creds.nodes["net-b"]
        self.attempt_raises(srv2.base_url, cred_a.cert, cred_a.key, "net-a")
        # Naming evidence comes from the TLS 1.2 probe, not
        # the driven attempt: under TLS 1.3 the same refusal
        # sometimes surfaces as a bare protocol EOF instead of
        # the named alert (seen under full-suite load; see the
        # suite docstring's probe note).
        exc = self.handshake_error(srv2.base_url, cred_a.cert, cred_a.key)
        self.assertIsNotNone(exc)
        self.assertIn("certificate revoked", str(exc).lower())
        status, _ = self.attempt(srv2.base_url, cred_b.cert, cred_b.key, "net-b")
        self.assertEqual(status, 200)
        srv2.stop()

        srv3 = self.start_server("srv-main-3", node_id="srv-main",
                                 cert_name="srv-main", crl=crl_fresh)
        self.attempt_raises(srv3.base_url, cred_a.cert, cred_a.key, "net-a")
        self.attempt_raises(srv3.base_url, cred_b.cert, cred_b.key, "net-b")

    # -- Outcome 5: revocation names serials, not node ids ---------

    def test_reissued_certificate_for_same_node_id_still_works(self):
        protected_link.revoke_certificate(self.creds, "net-a")
        crl = protected_link.write_crl(self.creds, "crl-reissue")
        srv = self.start_server("srv-main", crl=crl)

        cred_a = self.creds.nodes["net-a"]
        self.attempt_raises(srv.base_url, cred_a.cert, cred_a.key, "net-a")

        # The CA issues a NEW certificate for the same node id:
        # a new key, a new serial the list does not name. It
        # completes the handshake and works - the revocation cut
        # the old credential, not the identity.
        renewed = protected_link.issue_additional_certificate(
            self.creds, "net-a", "net-a-renewed"
        )
        self.assertEqual(
            protected_link.cert_file_node_id(renewed.cert), "net-a"
        )
        status, body = self.attempt(
            srv.base_url, renewed.cert, renewed.key, "net-a"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["node_id"], "srv-main")

    # -- Outcome 6: a malformed CRL refuses the start ---------------

    def test_malformed_or_missing_crl_refuses_start(self):
        garbage = self.tmp / "not-a-crl.pem"
        garbage.write_text("this is not a CRL at all\n", encoding="utf-8")
        for label, crl_path in (
            ("srv-missing", self.tmp / "no-such-crl.pem"),
            ("srv-garbage", garbage),
        ):
            server = ServerProcess(
                self, self.tmp, label, "srv-main", "srv-main", self.creds,
                crl=crl_path, expect_start_failure=True,
            )
            rc = server.wait_exited()
            self.assertNotEqual(rc, 0)
            log = server.log_text()
            self.assertIn("crl_unreadable", log)
            self.assertNotIn("LISTENING", log)
            self.assertFalse(server.port_file.exists())

    # -- Outcome 7: control - no CRL handed over, no enforcement ----

    def test_server_without_crl_accepts_revoked_certificate(self):
        protected_link.revoke_certificate(self.creds, "net-a")
        protected_link.write_crl(self.creds, "crl-unhanded")
        srv = self.start_server("srv-main")  # no --tls-crl
        cred_a = self.creds.nodes["net-a"]
        status, _ = self.attempt(srv.base_url, cred_a.cert, cred_a.key, "net-a")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
