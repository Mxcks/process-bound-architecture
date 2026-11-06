"""
Process-Bound Architecture - protected node links (experiment 014).

Mutual TLS for the networked node transport (experiment 007's
node_server.py), plus the test-credential generation the protected
tests use. Python standard library only: `ssl` for the transport;
certificate generation shells out to the machine's `openssl`
binary, which is a test-time tool, not a runtime dependency of
any server (a server only ever reads certificate files it is
started with).

WHAT THIS CLOSES, AND WHAT IT DOES NOT
--------------------------------------
Experiment 007's honest limits named the gap exactly: its nodes
talk over plain HTTP on localhost, so anything that can reach a
node's port can mutate its state, sync with it, and fetch policies
from an opted-in node, and everything crosses in plaintext. This
module is the transport-security step that experiment pointed at:

- Encryption in transit: every protected connection is TLS, so the
  bytes on the wire are ciphertext. Experiment 014's tests prove
  this by capture, not by assertion: traffic driven through a raw
  byte-capturing proxy contains neither policy code nor entry
  contents, while the exchange over that same path succeeds.
- Authentication of the peer: the TLS handshake is mutual. A
  server started with TLS arguments requires a client certificate
  chaining to the CA it was given, and a client verifies the
  server's certificate against the same CA and the address it
  dialed (hostname/IP check stays ON). A peer with no credential,
  or with a credential from a different authority, cannot complete
  the handshake at all - it never reaches an endpoint, so it can
  neither sync entries nor receive a policy. When a server process
  initiates sync with a peer, it presents its OWN node certificate
  as the client, so authentication is symmetric: a node is a
  credentialed peer in both directions.

Authentication here answers "does this peer hold a credential our
CA issued" and nothing more. Which node identity a certificate
carries (its CN) is not checked against the node id a process
claims, there is no revocation of certificates, no rotation, and
no identity proofing of WHO should hold a credential - that is
Gate 1's production-crypto question, unchanged by this module.

VERIFICATION IS NOT MOVED OR BYPASSED
-------------------------------------
TLS decides who may connect and keeps the wire unreadable. It
grants the payload no trust: entries arriving over a protected
link still go through Gate 3's receive_entries unchanged, the
registry snapshot still verifies with Gate 2's verifier on
receipt, and policy serving still runs Gate 2 verification inside
the serving process. Experiment 014's tests push a tampered entry
over the protected link and require the same divergence + rollback
as the plaintext transport produced.

CREDENTIALS IN TESTS
--------------------
generate_test_credentials() exists for tests and the demo. It
creates, inside a caller-provided directory, one throwaway CA,
one certificate per named node, one client certificate, and a
second (rogue) CA with its own client certificate for the refusal
tests. Certificates carry SANs for 127.0.0.1 and localhost and
both serverAuth and clientAuth usages, because the same node
certificate is presented in both directions. Keys are generated
at runtime by the caller of the function - the tests call it into
their own temporary directory - and must never be committed: the
repository contains no credential, key, or certificate file.
"""

from __future__ import annotations

import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path


class CredentialError(RuntimeError):
    """Test-credential generation failed (for example, openssl is
    not available on this machine). Raised, never hidden: a test
    that cannot make credentials has not tested anything."""


@dataclass(frozen=True)
class NodeCredential:
    cert: Path
    key: Path


@dataclass(frozen=True)
class CredentialBundle:
    ca_cert: Path
    ca_key: Path
    nodes: dict[str, NodeCredential]
    rogue_ca_cert: Path
    rogue_ca_key: Path
    rogue_client_cert: Path
    rogue_client_key: Path


_EXTENSIONS = """\
subjectAltName=IP:127.0.0.1,DNS:localhost
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
"""


def _run(openssl_args: list[str], what: str) -> None:
    try:
        proc = subprocess.run(
            ["openssl", *openssl_args],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError as exc:
        raise CredentialError(
            "openssl binary not found; test credentials cannot be "
            "generated on this machine"
        ) from exc
    if proc.returncode != 0:
        raise CredentialError(
            f"openssl failed while {what}: {proc.stderr.strip()}"
        )


def _make_ca(workdir: Path, stem: str, common_name: str) -> tuple[Path, Path]:
    cert, key = workdir / f"{stem}.crt", workdir / f"{stem}.key"
    _run([
        "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "3",
        "-subj", f"/CN={common_name}",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    ], f"creating CA {stem}")
    return cert, key


def _issue(workdir: Path, stem: str, common_name: str,
           ca_cert: Path, ca_key: Path, ext_file: Path) -> NodeCredential:
    cert, key, csr = (
        workdir / f"{stem}.crt",
        workdir / f"{stem}.key",
        workdir / f"{stem}.csr",
    )
    _run([
        "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(csr),
        "-subj", f"/CN={common_name}",
    ], f"creating key and CSR for {stem}")
    _run([
        "x509", "-req", "-in", str(csr),
        "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(cert), "-days", "3", "-sha256",
        "-extfile", str(ext_file),
    ], f"issuing certificate for {stem}")
    csr.unlink(missing_ok=True)
    return NodeCredential(cert=cert, key=key)


def generate_test_credentials(workdir: str | Path,
                              node_names: list[str]) -> CredentialBundle:
    """Create a full throwaway credential set in workdir: one test
    CA, one certificate per name in node_names, and a rogue CA with
    a rogue client certificate (for refusal tests). Everything is
    generated by this call; nothing is read from the repository."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    ext_file = workdir / "leaf-extensions.cnf"
    ext_file.write_text(_EXTENSIONS, encoding="utf-8")

    ca_cert, ca_key = _make_ca(workdir, "ca", "PBA Test CA (experiment 014)")
    nodes = {
        name: _issue(workdir, name, name, ca_cert, ca_key, ext_file)
        for name in node_names
    }
    rogue_ca_cert, rogue_ca_key = _make_ca(
        workdir, "rogue-ca", "PBA Rogue CA (experiment 014)"
    )
    rogue = _issue(
        workdir, "rogue-client", "rogue-client",
        rogue_ca_cert, rogue_ca_key, ext_file,
    )
    return CredentialBundle(
        ca_cert=ca_cert,
        ca_key=ca_key,
        nodes=nodes,
        rogue_ca_cert=rogue_ca_cert,
        rogue_ca_key=rogue_ca_key,
        rogue_client_cert=rogue.cert,
        rogue_client_key=rogue.key,
    )


# -- TLS contexts -------------------------------------------------------------


def server_context(cert: str | Path, key: str | Path,
                   ca_cert: str | Path) -> ssl.SSLContext:
    """Server side of a protected link: present this node's
    certificate, and REQUIRE a client certificate chaining to
    ca_cert. A peer that cannot present one never completes the
    handshake, so it never reaches any endpoint."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    context.load_verify_locations(cafile=str(ca_cert))
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def client_context(cert: str | Path, key: str | Path,
                   ca_cert: str | Path) -> ssl.SSLContext:
    """Client side of a protected link: verify the server against
    ca_cert with the address check ON (the certificate must carry
    a SAN for the host dialed), and present this peer's own
    certificate as its credential."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_cert))
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def anonymous_client_context(ca_cert: str | Path) -> ssl.SSLContext:
    """A client that can verify the server but presents NO
    credential of its own. Exists for the refusal tests: a
    protected server must reject this handshake."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_cert))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context
