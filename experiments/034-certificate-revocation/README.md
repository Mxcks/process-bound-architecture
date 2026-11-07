# Experiment 034: certificate revocation for the protected links - a revoked certificate is refused at the handshake

**Question.** Experiments 014 and 017 protected the networked
transport: mutual TLS authenticates possession of a CA-issued
certificate, and 017 binds that certificate to the node id the
process claims. Both stated the same remaining gap in their
honest limits: there was no certificate revocation. A
certificate issued to a node that later had to be cut off kept
working until it expired, and nothing in the transport could
refuse it - the binding was perfect, forever, for anyone who
had ever been issued a credential. This experiment adds the
cut: a certificate revocation list (CRL) issued by the same CA
over its own books, naming revoked certificates by serial,
that a server process can be started with, and that the TLS
handshake then enforces in both directions, before any
endpoint runs.

Framing: `tests/test-cases/sync-mesh.md` case 23 (this
experiment's case, written before the build - the flag, the
mechanism the standard library forces, the fixture extension,
the start-time operator model, and the refusal name are all
stated there), experiment 014 (the protected links and the
rogue-CA refusal this experiment's revoked refusal is kept
distinct from), experiment 017 (the identity binding and its
`identity_mismatch`, the second refusal kept distinct), and
experiment 033 (the startup-gate shape a malformed CRL's
refused start mirrors: a named error at start, never a silent
run without the configured check).

## Design choices (at prototype scale)

- **The flag is `--tls-crl <path>`, and it requires the TLS
  arguments.** In the style of `--mesh-anchor` and
  `--tls-bind-identity`: revocation checking is an opt-in on
  top of the protected links, meaningless without them, and a
  server started without it behaves exactly as experiments
  014-033 shipped - proven by the control test in the same
  suite. What the flag selects is enforced in BOTH contexts
  the process builds: its accepting side refuses a revoked
  client certificate, and its initiating side (every outbound
  connection it makes, `/sync` and the push surfaces included)
  refuses a revoked server certificate.
- **The mechanism is the standard library's, named plainly.**
  Python's `ssl` checks a presented certificate against a CRL
  when the context carries `VERIFY_CRL_CHECK_LEAF` and the CRL
  sits in the context's verify store beside the CA. So
  `protected_link.server_context` and `client_context` gained
  an optional `crl` argument that does exactly that, and
  nothing else: no application-level serial list, no endpoint
  code, no new refusal surface inside the server - the refusal
  happens inside the handshake, which is what "before any
  endpoint runs" means here. The check is leaf-only (the
  presented end certificate), which is the certificate this
  architecture issues and revokes.
- **A CRL names serials, not node ids.** That is what a CRL
  is, and the consequence is deliberate and tested: revoking
  one certificate for a node id cuts that certificate; the CA
  issuing the same node id a new certificate (new key, new
  serial) restores its access against the very same enforcing
  server. Revocation is per-credential, and re-issuance is
  the recovery path - the same shape real PKIs have.
- **Updating the CRL is an operator act at start time.** A
  running server holds the CRL it was started with; a new list
  takes effect at a restart. Stated as this experiment's
  limit, mirroring how 033's anchor gate judges at startup. A
  stale-but-validly-issued CRL is not an error: it enforces
  exactly the revocations it lists, no more (a revocation
  recorded after it was issued is not enforced until the
  newer list is picked up) and no less.
- **A malformed CRL refuses the start, by name.** The CRL
  file is read and validated when the context is built, at
  startup: a missing file, a file with no CRL block, or a CRL
  the library cannot parse raises `RevocationError` naming
  `crl_unreadable`, and the server process exits through its
  argument error path before serving anything. A server never
  runs believing it enforces a list it does not hold.
- **The fixture CA learned to keep its own books.** The
  one-shot fixtures (`openssl req -x509`, experiment 014)
  keep no record of what they issued, so they cannot revoke
  or issue a CRL. `generate_revocation_credentials` is the
  smallest extension that can: a bookkeeping CA (an
  `openssl ca` database - index, serial, CRL number - in the
  test's temporary directory) issuing the same certificates
  (same SANs, same usages, same node ids) through `openssl
  ca`, able to revoke one by serial and issue a CRL over the
  revocations it has recorded. It lives beside
  `generate_test_credentials` in `protected_link.py`, where
  the test-credential generation has always lived: fixture
  work, not product surface. Everything is generated at
  runtime into a temporary directory; no key, certificate, or
  CRL material is read from or written to the repository. One
  fixture decision is stated rather than hidden: the
  bookkeeping CA's config sets `unique_subject = no`, because
  openssl's default refuses to issue two certificates for one
  subject name, and the re-issuance proof needs exactly that.

## Results

- **Tests written first.** Case 23 was added to
  `tests/test-cases/sync-mesh.md` before the build, in the
  detailed format of cases 12-22. The new suite
  (`packages/synchronization/test_certificate_revocation.py`,
  7 tests) ran against the unbuilt code first: FAILED
  (errors=7), every error the named absence -
  `AttributeError: module 'protected_link' has no attribute
  'generate_revocation_credentials'` in setUp, before any
  assertion. The capture is in `test-output.txt`.
- **The story, end to end over running processes.** A peer
  (net-a) presenting its valid, identity-bound certificate
  syncs with a server that has no CRL, exactly as in cases
  8/9. The CA revokes net-a's certificate, issues the CRL,
  and the server restarts with `--tls-crl`: the same peer's
  sync is refused, none of its state crosses (the refused
  mutation is absent server-side), and a raw handshake probe
  with the revoked certificate is answered with the TLS alert
  naming the revocation - `SSLV3_ALERT_CERTIFICATE_REVOKED`.
  A sibling (net-b), certified by the same CA and not on the
  list, connects and syncs against the same enforcing server.
- **Three doors, three refusals, asserted separately.** In
  one test: (a) the revoked certificate is refused with the
  handshake alert naming the revocation; (b) a valid,
  unrevoked certificate presented under a DIFFERENT claimed
  node id is refused by 017's check, reported as
  `identity_mismatch` - an HTTP-layer refusal after a
  completed handshake, a different kind of event from (a);
  (c) a certificate from a rogue CA is refused at the
  handshake with an alert that does NOT name a revocation.
  No leg borrows another leg's evidence.
- **The initiating direction.** An initiator whose client
  context carries the CRL refuses to sync with a server whose
  certificate the list names: the pull dies at the initiator's
  own handshake before any state moves, and the initiator's
  holdings are unchanged. The same refusal holds in the
  server-to-server form, where the initiating side is itself
  a server process pushing to the revoked peer.
- **Stale and empty lists.** A CRL issued when only net-a was
  revoked enforces exactly that: after net-b is also revoked
  at the CA, the stale list still lets net-b work and still
  refuses net-a. A validly issued CRL naming nothing lets
  every credentialed peer work - an empty list is a working
  configuration, not an error.
- **Re-issuance.** After revocation, the CA issues net-a a
  new certificate (new key, new serial); it works against the
  same CRL-enforcing server with no restart and no list
  change. The revocation named the serial, not the identity.
- **The refused start.** A server started with `--tls-crl`
  pointing at a garbage file, and one pointing at a file that
  does not exist, each exit nonzero naming `crl_unreadable`
  and serve nothing.
- **No-CRL control.** Servers and clients started without
  `--tls-crl` behave exactly as before: every credentialed
  peer works, including - stated plainly - the peer whose
  certificate stands revoked at the CA, because no list was
  handed to anyone. Unchanged behavior is the default;
  enforcement is the opt-in.
- **Full suite after the build:** registration 25,
  policy-library 42, synchronization 139 (132 + the 7 new),
  validation 22 - 228 tests, all passing, plus the EAI
  handoff check (`scripts/verify-handoffs.py`) PASS 3/3. No
  earlier suite was modified. The demo
  (`demo_certificate_revocation.py`) walks the whole arc in
  four printed stages, exit 0. No `node_server` processes
  were left running.

## What failed on the way (recorded, not hidden)

- The first built run failed 4 tests. Three were defects in
  the TESTS, not the build: peer servers were started
  claiming their process label as node id while presenting
  another node's certificate, and 017's binding self-check
  correctly refused them at startup. The fix was to claim the
  node id the certificate was issued for. The fourth taught
  something real: a server whose handshake refuses a client
  logs NOTHING about it (the accept error is swallowed by the
  server machinery), so the suite's naming evidence for a
  handshake refusal cannot come from a server log - it comes
  from the exchange itself, via a raw handshake probe.
- The second built run failed 3: two more test-side defects
  of the same families (malformed-CRL start legs claiming a
  node id their certificate was not issued for, so the
  binding self-check fired before the CRL was ever read; and
  an assertion expecting pre-restart state on a FRESH
  restarted process), and one real mechanism finding: under
  TLS 1.3 the revoked alert's naming is racy from the
  client's side - the same refusal sometimes surfaces as the
  named alert and sometimes as a bare protocol EOF, because
  whether the alert is read before the connection closes is a
  race. Forcing the probe to TLS 1.2 (the links' own floor)
  made the naming deterministic, 3 for 3. The suite's
  handshake probe does exactly that, with the reason written
  in its docstring, and case 23's text was amended to say so.
  The production paths are untouched by this: enforcement is
  identical at either version; only the test's naming
  evidence needed determinism.
- The race bit once more, later: the first full-suite
  regression run passed everything, but a confirmation re-run
  of the synchronization package under load failed the
  stale-CRL test at the one naming assertion that still read
  the driven attempt's error text instead of the probe's -
  same TLS 1.3 race, third sighting. That assertion now takes
  its naming evidence from the TLS 1.2 probe like every other
  leg, and the package was re-run clean (the counts in
  test-output.txt are from the final runs).
- One environmental interruption is recorded for honesty: a
  service restart on this machine killed the demo's first
  foreground run mid-flight. It left no processes behind
  (checked: 0), and the demo was re-run clean to exit 0.

## Honest limits

- **CRL updates are restart-only.** A running server never
  learns a newer list. Live CRL reload, OCSP, or any
  revocation distribution over the mesh itself is unbuilt -
  and distribution has a bootstrapping question this
  experiment does not touch: the links the CRL would travel
  over are the links it governs.
- **A stale CRL enforces exactly its list.** Between a
  revocation at the CA and the restart that picks up the
  list naming it, the revoked certificate still works
  everywhere the newer list has not reached. That window is
  an operator-discipline window, not a mechanism window, and
  it is stated here rather than smoothed over.
- **The check is leaf-only.** `VERIFY_CRL_CHECK_LEAF` judges
  the presented end certificate. Chain-wide CRL checking
  (`VERIFY_CRL_CHECK_CHAIN`) would additionally require CRLs
  for every issuer in the chain; this prototype's chain is
  one CA and its leaves, and the leaf is the certificate that
  gets revoked.
- **The fixture CA is test tooling, not a production PKI.**
  It keeps its books in a temporary directory, issues for any
  name it is asked for (014/017's stated limit, unchanged),
  and its CRL carries no next-update discipline an operator
  would have to honor. Issuance governance, rotation, and
  expiry handling beyond the throwaway certificates' validity
  remain unbuilt, exactly as 014 and 017 stated; what changed
  is only that revocation itself now exists.
- **Prototype scale, one machine, localhost processes** -
  the same terms as every networked experiment since 007.
  Gate 5's end-to-end timing target remains an open
  production benchmark, untouched by this experiment.
