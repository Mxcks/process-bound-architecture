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
carries is not checked against the node id a process claims by
this module alone, there is no revocation of certificates, no
rotation, and no identity proofing of WHO should hold a
credential - that is Gate 1's production-crypto question,
unchanged by this module. (Superseded in part by experiment 034:
revocation now exists - see CERTIFICATE REVOCATION below.
Rotation and identity proofing remain unbuilt.)

CERTIFICATE-TO-NODE-ID BINDING (experiment 017)
-----------------------------------------------
Every leaf certificate this module issues carries the node id it
was issued for IN the certificate itself, as a URI subjectAltName
`urn:pba:node:<node-id>` (alongside the 127.0.0.1/localhost SANs
the address check needs). `peercert_node_id` reads that id back
out of a peer certificate dictionary (the shape
`SSLSocket.getpeercert` returns), and `cert_file_node_id` reads
it out of a certificate file. node_server.py uses those readers
when a process is started with `--tls-bind-identity`
(experiment 017): the process refuses to start if its own
certificate's id differs from the node id it claims, an accepting
node refuses a connecting peer whose claim (the X-PBA-Node-Id
header) differs from its certificate's id, and an initiating node
checks a peer server's certificate id against the id that peer
claims before pulling entries. A mismatch raises / is reported
as `IdentityMismatch`, plainly naming the claimed id and the
certified id. Binding is opt-in per process; without it, this
module's behavior is exactly experiment 014's.

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


NODE_ID_URI_PREFIX = "urn:pba:node:"


def _leaf_extensions(node_id: str) -> str:
    """Extension block for one leaf certificate. The SAN line is
    per-certificate because it carries the node id THIS
    certificate is issued for (experiment 017), as a URI SAN,
    alongside the address SANs the hostname check requires."""
    return (
        "subjectAltName=IP:127.0.0.1,DNS:localhost,"
        f"URI:{NODE_ID_URI_PREFIX}{node_id}\n"
        "basicConstraints=CA:FALSE\n"
        "keyUsage=digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
    )


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
           ca_cert: Path, ca_key: Path) -> NodeCredential:
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
    # The certificate is issued FOR common_name as a node id: the
    # id goes into the certificate itself as a URI SAN
    # (experiment 017), via this leaf's own extension file.
    ext_file = workdir / f"{stem}-extensions.cnf"
    ext_file.write_text(_leaf_extensions(common_name), encoding="utf-8")
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

    ca_cert, ca_key = _make_ca(workdir, "ca", "PBA Test CA (experiment 014)")
    nodes = {
        name: _issue(workdir, name, name, ca_cert, ca_key)
        for name in node_names
    }
    rogue_ca_cert, rogue_ca_key = _make_ca(
        workdir, "rogue-ca", "PBA Rogue CA (experiment 014)"
    )
    rogue = _issue(
        workdir, "rogue-client", "rogue-client",
        rogue_ca_cert, rogue_ca_key,
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


# -- A CA that keeps its own books (experiment 034 test fixtures) -------------
#
# generate_test_credentials issues certificates with one-shot
# `openssl x509 -req` calls, which keep no record a revocation
# could be entered into. Revoking a certificate is an act in
# the CA's own books: the certificate is marked revoked in the
# CA's index, and a CRL is issued over the revocations recorded
# there. The smallest fixture extension that can do that is a
# CA with an `openssl ca` database (index, serial, CRL number)
# in the test's temporary directory. It issues the SAME
# certificates as the one-shot path - same SANs, same usages,
# issued FOR the same node ids, through the same extension
# block - so a credential from this CA is interchangeable with
# one from generate_test_credentials everywhere except that
# this CA can also revoke it. Test-time tooling on the same
# terms as the rest of this module: everything is generated at
# runtime into a caller-provided directory, and no key, index,
# or CRL file is ever committed.


@dataclass(frozen=True)
class RevocationBundle:
    ca_cert: Path
    ca_key: Path
    config: Path
    workdir: Path
    nodes: dict[str, NodeCredential]
    rogue_ca_cert: Path
    rogue_ca_key: Path
    rogue_client_cert: Path
    rogue_client_key: Path


def _ca_config(workdir: Path, db_dir: Path, ca_cert: Path,
               ca_key: Path) -> Path:
    config = workdir / "ca.cnf"
    config.write_text(
        "[ ca ]\n"
        "default_ca = pba_test_ca\n"
        "[ pba_test_ca ]\n"
        f"dir = {db_dir}\n"
        "database = $dir/index.txt\n"
        "new_certs_dir = $dir\n"
        "serial = $dir/serial\n"
        "crlnumber = $dir/crlnumber\n"
        f"certificate = {ca_cert}\n"
        f"private_key = {ca_key}\n"
        "default_md = sha256\n"
        "default_days = 3\n"
        "default_crl_days = 3\n"
        # A fixture decision, stated: the same node id may be
        # issued a fresh certificate while an earlier one stands
        # revoked - re-issuance after revocation is one of the
        # behaviors under test (case 23), so the database does
        # not refuse a duplicate subject.
        "unique_subject = no\n"
        "policy = pba_policy\n"
        "[ pba_policy ]\n"
        "commonName = supplied\n",
        encoding="utf-8",
    )
    return config


def _issue_managed(workdir: Path, stem: str, common_name: str,
                   config: Path) -> NodeCredential:
    """Issue one leaf certificate through the bookkeeping CA:
    same CSR and extension block as the one-shot `_issue`, but
    signed via `openssl ca` so the issuance lands in the CA's
    index, where a later revocation can find it."""
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
    ext_file = workdir / f"{stem}-extensions.cnf"
    ext_file.write_text(_leaf_extensions(common_name), encoding="utf-8")
    _run([
        "ca", "-batch", "-config", str(config),
        "-in", str(csr), "-out", str(cert),
        "-extfile", str(ext_file), "-days", "3",
    ], f"issuing certificate for {stem} through the CA database")
    csr.unlink(missing_ok=True)
    return NodeCredential(cert=cert, key=key)


def generate_revocation_credentials(
        workdir: str | Path, node_names: list[str]) -> RevocationBundle:
    """Create a throwaway credential set in workdir whose CA
    keeps its own books: one bookkeeping CA, one certificate per
    name in node_names (each recorded in the CA's index, so each
    can later be revoked by name), and a rogue CA with a rogue
    client certificate, as in generate_test_credentials."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    ca_cert, ca_key = _make_ca(
        workdir, "ca", "PBA Test CA (experiment 034, bookkeeping)"
    )
    db_dir = workdir / "ca-db"
    db_dir.mkdir(exist_ok=True)
    (db_dir / "index.txt").write_text("", encoding="utf-8")
    (db_dir / "serial").write_text("1000\n", encoding="utf-8")
    (db_dir / "crlnumber").write_text("1000\n", encoding="utf-8")
    config = _ca_config(workdir, db_dir, ca_cert, ca_key)
    nodes = {
        name: _issue_managed(workdir, name, name, config)
        for name in node_names
    }
    rogue_ca_cert, rogue_ca_key = _make_ca(
        workdir, "rogue-ca", "PBA Rogue CA (experiment 034)"
    )
    rogue = _issue(
        workdir, "rogue-client", "rogue-client",
        rogue_ca_cert, rogue_ca_key,
    )
    return RevocationBundle(
        ca_cert=ca_cert,
        ca_key=ca_key,
        config=config,
        workdir=workdir,
        nodes=nodes,
        rogue_ca_cert=rogue_ca_cert,
        rogue_ca_key=rogue_ca_key,
        rogue_client_cert=rogue.cert,
        rogue_client_key=rogue.key,
    )


def issue_additional_certificate(bundle: RevocationBundle, node_name: str,
                                 stem: str) -> NodeCredential:
    """Issue a NEW certificate for an existing node id through
    the bookkeeping CA: a new key and a new serial, recorded in
    the same index. This is re-issuance - the CA operator's
    separate act after a revocation (case 23: the list names
    serials, so the new certificate is not on it)."""
    return _issue_managed(bundle.workdir, stem, node_name, bundle.config)


def revoke_certificate(bundle: RevocationBundle, node_name: str) -> None:
    """Enter one issued certificate's revocation into the CA's
    books. Takes effect on the NEXT CRL written: a CRL issued
    before this call does not name the certificate, which is
    exactly the stale-list behavior case 23 asserts."""
    cert = bundle.nodes[node_name].cert
    _run([
        "ca", "-revoke", str(cert), "-config", str(bundle.config),
    ], f"revoking certificate for {node_name}")


def write_crl(bundle: RevocationBundle, stem: str) -> Path:
    """Issue a CRL over every revocation the CA has recorded so
    far, written to workdir/<stem>.pem. Each call writes a new
    file, so a test can hold the stale list and the fresh list
    side by side and hand each to a different process."""
    out = bundle.workdir / f"{stem}.pem"
    _run([
        "ca", "-gencrl", "-config", str(bundle.config),
        "-out", str(out),
    ], f"issuing CRL {stem}")
    return out


# -- Certificate identity (experiment 017) ------------------------------------


class IdentityMismatch(RuntimeError):
    """A peer's certificate was issued for a different node id
    than the one it claims. Carries both ids so a refusal can
    state them plainly: `claimed` is the id the peer asserts,
    `certified` is the id inside the certificate it presented."""

    def __init__(self, claimed: str | None, certified: str | None):
        self.claimed = claimed
        self.certified = certified
        super().__init__(
            f"identity_mismatch: peer claims node id {claimed!r} "
            f"but its certificate was issued for {certified!r}"
        )


def peercert_node_id(peercert: dict) -> str | None:
    """The node id a peer certificate was issued for, read from
    the dictionary SSLSocket.getpeercert() returns: the URI SAN
    `urn:pba:node:<node-id>` is authoritative; the subject CN is
    the fallback for a certificate that carries no such SAN.
    Returns None when the certificate names no node id at all."""
    for kind, value in peercert.get("subjectAltName", ()):
        if kind == "URI" and value.startswith(NODE_ID_URI_PREFIX):
            return value[len(NODE_ID_URI_PREFIX):]
    for rdn in peercert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return value
    return None


def cert_file_node_id(cert: str | Path) -> str | None:
    """The node id a certificate FILE was issued for, decoded with
    the standard library's certificate decoder (the same shape
    getpeercert returns, so peercert_node_id applies unchanged)."""
    decode = getattr(ssl, "_test_decode_cert", None) or getattr(
        ssl._ssl, "_test_decode_cert", None
    )
    if decode is None:  # pragma: no cover - stdlib shape check
        raise CredentialError(
            "this Python's ssl module cannot decode a certificate file"
        )
    return peercert_node_id(decode(str(cert)))


# -- Certificate revocation (experiment 034) ----------------------------------
#
# Cases 8/9 left one gap stated in both experiments' honest
# limits: a certificate the CA issued kept working until it
# expired, however badly its holder misbehaved - nothing in the
# transport could cut one certificate off while leaving its
# siblings standing. Revocation is a certificate revocation
# list (CRL): a statement signed by the CA naming revoked
# certificates by serial number. The standard library carries
# the enforcement as a verification flag, VERIFY_CRL_CHECK_LEAF,
# with the CRL loaded into the same verify store as the CA -
# that is the mechanism, named because the library forces it:
# both context builders below accept the same optional CRL and
# apply it the same way, so a process enforces the list in both
# directions of its links (the peers it accepts, and the peers
# it connects out to). The check is the LEAF check the flag
# names: the peer's own certificate is checked against the
# list. A CRL names serials, not node ids: a new certificate
# issued for the same node id under a new serial is not on the
# list and still works - the revocation cuts a credential, not
# an identity. A CRL is loaded once, when the context is built:
# a process enforces the list it was handed at start, and a new
# list takes effect on restart. That is the stated limit, not
# a discovered one.


class RevocationError(RuntimeError):
    """A configured CRL cannot be read or carries no parseable
    revocation list. Raised as `crl_unreadable`, in the family
    of anchor_unreadable (experiment 026) and
    mesh_state_unreadable (experiment 033): a caller who asked
    for revocation checking is never silently run without it,
    which would be worse than no revocation at all - the
    process would believe it enforces a list it does not hold."""


CRL_PEM_MARKER = "-----BEGIN X509 CRL-----"


def _apply_crl(context: ssl.SSLContext, crl: str | Path) -> None:
    """Load one PEM CRL into a context's verify store and turn
    on leaf revocation checking. Any failure to read or parse
    the file is a RevocationError naming crl_unreadable: the
    file must exist, must carry an X509 CRL block, and the
    block must load - all three judged here, at context build
    (process start), before anything is served."""
    path = Path(crl)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RevocationError(
            f"crl_unreadable: cannot read CRL file {path}: {exc}"
        ) from exc
    if CRL_PEM_MARKER not in text:
        raise RevocationError(
            f"crl_unreadable: {path} carries no X509 CRL block; "
            "refusing to run without the revocation checking "
            "that was configured"
        )
    context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    try:
        context.load_verify_locations(cafile=str(path))
    except ssl.SSLError as exc:
        raise RevocationError(
            f"crl_unreadable: CRL file {path} does not parse: {exc}"
        ) from exc


# -- TLS contexts -------------------------------------------------------------


def server_context(cert: str | Path, key: str | Path,
                   ca_cert: str | Path,
                   crl: str | Path | None = None) -> ssl.SSLContext:
    """Server side of a protected link: present this node's
    certificate, and REQUIRE a client certificate chaining to
    ca_cert. A peer that cannot present one never completes the
    handshake, so it never reaches any endpoint. With `crl`
    (experiment 034), a client certificate the CRL names is
    refused at the same handshake, before any endpoint."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    context.load_verify_locations(cafile=str(ca_cert))
    context.verify_mode = ssl.CERT_REQUIRED
    if crl is not None:
        _apply_crl(context, crl)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def client_context(cert: str | Path, key: str | Path,
                   ca_cert: str | Path,
                   crl: str | Path | None = None) -> ssl.SSLContext:
    """Client side of a protected link: verify the server against
    ca_cert with the address check ON (the certificate must carry
    a SAN for the host dialed), and present this peer's own
    certificate as its credential. With `crl` (experiment 034),
    a server certificate the CRL names is refused at the
    handshake on this initiating side."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_cert))
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    if crl is not None:
        _apply_crl(context, crl)
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
