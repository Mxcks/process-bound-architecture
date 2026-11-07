"""
Demo for experiment 034 (sync-mesh case 23): a certificate
on a protected link can be revoked, and the revocation is
enforced at the handshake.

Cases 8/9 protected the networked transport - mutual TLS
(experiment 014) and certificate-to-node-id binding
(experiment 017) - and both stated the same remaining gap:
a certificate the CA issued kept working until it expired,
however badly its holder misbehaved. This demo drives the
cut case 23 builds, over real server processes:

1. A server runs WITHOUT --tls-crl. A credentialed peer
   (net-a) syncs with it exactly as in cases 8/9.
2. The CA revokes net-a's certificate in its own books and
   issues a CRL naming it. The server is restarted WITH
   --tls-crl: the same peer's sync now dies before any
   state moves, and a raw handshake probe names the
   refusal - the alert the server returns says the
   certificate was revoked. A sibling (net-b), whose
   certificate the list does not name, syncs fine.
3. The CA issues net-a a NEW certificate (new key, new
   serial): the revocation named the old serial, not the
   identity, so the renewed credential works against the
   same CRL-enforcing server.
4. A server started with a --tls-crl file that is not a
   CRL refuses to start, naming crl_unreadable: it never
   runs believing it enforces a list it does not hold.

Credentials come from a CA that keeps its own books
(protected_link.generate_revocation_credentials - an
openssl ca database in a temporary directory), generated
at runtime; nothing is read from the repository.

Run:  python3 packages/synchronization/demo_certificate_revocation.py
Standard library only (openssl subprocess for test credentials).
Localhost only.
"""

import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import node_server  # noqa: E402
import protected_link  # noqa: E402

SERVER = HERE / "node_server.py"


class Server:
    def __init__(self, tmp: Path, label: str, node_id: str, creds,
                 crl: Path | None = None):
        self.label = label
        self.port_file = tmp / f"{label}.port"
        self.log_path = tmp / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[node_id]
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id,
            "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            "--tls-bind-identity",
        ]
        if crl is not None:
            cmd += ["--tls-crl", str(crl)]
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        self.base_url: str | None = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.label} exited early; log:\n"
                    f"{self.log_path.read_text(encoding='utf-8')}"
                )
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError, ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.label} not ready")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        finally:
            self._log_fh.close()


def handshake_error(base_url: str, cert: Path, key: Path,
                    ca_cert: Path) -> Exception | None:
    """One raw TLS 1.2 handshake (see the suite for why 1.2:
    the refusal's alert names itself in-band there)."""
    context = protected_link.client_context(cert, key, ca_cert)
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    host, port = base_url.removeprefix("https://").split(":")
    raw = socket.create_connection((host, int(port)), timeout=10)
    try:
        with context.wrap_socket(raw, server_hostname=host):
            return None
    except (ssl.SSLError, OSError) as exc:
        return exc


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-demo-034-"))
    creds = protected_link.generate_revocation_credentials(
        tmp / "creds", ["srv-main", "net-a", "net-b", "test-client"],
    )
    client = creds.nodes["test-client"]
    node_server.set_client_tls(
        client.cert, client.key, creds.ca_cert, node_id="test-client"
    )
    servers: list[Server] = []
    try:
        # 1. No CRL handed over: the peer works as in cases 8/9.
        srv = Server(tmp, "srv", "srv-main", creds)
        servers.append(srv)
        srv.wait_ready()
        peer = Server(tmp, "peer", "net-a", creds)
        servers.append(peer)
        peer.wait_ready()
        node_server.mutate(peer.base_url, "region", "us-central")
        node_server.request_sync(peer.base_url, srv.base_url)
        print("1. before revocation: net-a syncs with the server;",
              "server state:",
              node_server.fetch_state(srv.base_url)["state"])

        # 2. Revoke at the CA, issue the CRL, restart with it.
        protected_link.revoke_certificate(creds, "net-a")
        crl = protected_link.write_crl(creds, "crl-after-net-a")
        srv.stop()
        srv2 = Server(tmp, "srv2", "srv-main", creds, crl=crl)
        servers.append(srv2)
        srv2.wait_ready()
        node_server.mutate(peer.base_url, "region", "eu-west")
        try:
            node_server.request_sync(peer.base_url, srv2.base_url)
            print("2. UNEXPECTED: the revoked peer synced")
            return 1
        except AssertionError:
            print("2. after revocation: the same peer's sync is refused;",
                  "server state still:",
                  node_server.fetch_state(srv2.base_url)["state"])
        cred_a = creds.nodes["net-a"]
        exc = handshake_error(srv2.base_url, cred_a.cert, cred_a.key,
                              creds.ca_cert)
        print("   raw handshake with the revoked certificate:",
              exc)
        sibling = Server(tmp, "sibling", "net-b", creds)
        servers.append(sibling)
        sibling.wait_ready()
        node_server.mutate(sibling.base_url, "tier", "member")
        node_server.request_sync(sibling.base_url, srv2.base_url)
        print("   sibling net-b (not on the list) syncs fine;",
              "server state:",
              node_server.fetch_state(srv2.base_url)["state"])

        # 3. Re-issuance: a new certificate for net-a works.
        renewed = protected_link.issue_additional_certificate(
            creds, "net-a", "net-a-renewed"
        )
        exc = handshake_error(srv2.base_url, renewed.cert, renewed.key,
                              creds.ca_cert)
        print("3. renewed certificate for net-a (new serial):",
              "handshake OK" if exc is None else exc)

        # 4. A malformed CRL refuses the start, by name.
        garbage = tmp / "not-a-crl.pem"
        garbage.write_text("this is not a CRL at all\n", encoding="utf-8")
        bad = Server(tmp, "bad", "srv-main", creds, crl=garbage)
        servers.append(bad)
        rc = bad.proc.wait(timeout=20)
        named = "crl_unreadable" in bad.log_text()
        print(f"4. malformed CRL: start refused rc={rc},",
              f"named crl_unreadable: {named}")
        return 0 if (rc != 0 and named) else 1
    finally:
        for server in servers:
            server.stop()
        node_server.clear_client_tls()


if __name__ == "__main__":
    raise SystemExit(main())
