# 017. Certificate binding: a node's certificate must match the node id it claims

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/synchronization/` (`protected_link.py` gains
per-certificate node-id issuance - a URI subjectAltName
`urn:pba:node:<node-id>` in every leaf certificate - plus
`IdentityMismatch`, `peercert_node_id`, and `cert_file_node_id`;
`node_server.py` gains the opt-in `--tls-bind-identity` flag, a
startup self-check, the accepting-side claim check in the request
handler, the initiating-side check in `/sync`, the `X-PBA-Node-Id`
claim header in its client helpers, and
`probe_peer_node_id`/`verify_peer_identity`; new
`test_cert_node_binding.py` and `demo_cert_node_binding.py`).
Without `--tls-bind-identity` a server is exactly experiment 014's
protected node - the unmodified experiment 007 and 014 suites pass
in the same discovery run as the new one. This experiment does
not touch `proposals/PBA-FIX-001.md` or implement any part of it,
and it does not work on deliberate removal (availability case 3,
experiment 011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 9, added to that
file before the code was built, in the detailed format of case 7.
The new tests were run against the unbuilt code first
(`ImportError: cannot import name 'IdentityMismatch' from
'protected_link'`) so the case is proven to be what the code had
to satisfy; that failing output is recorded verbatim at the top
of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages, the
new suite by direct file run, the handoff check, the new demo,
the experiment 014 demo as a regression, and the
leftover-process check).

## Question

Experiment 014 put networked nodes on mutual TLS and named its
own limit plainly: authentication is possession of a CA-issued
certificate, full stop. The certificate's name was never checked
against the node id a process claims, so any enrolled node's
certificate could be presented while claiming to be any other
node - a stolen or borrowed credential was a complete identity,
not just a key. Can the prototype now bind the two, at prototype
scale: the id inside the certificate must equal the id the peer
claims, on both directions the 014 transport exposes, without
moving Gate 3/Gate 2 verification and without changing the
behavior of a node that does not opt in?

## Result: all case 9 outcomes observed, 4 new tests pass, full regression green

Run on Python 3.12.3, standard library only for the transport
(`ssl`); test certificates are generated at runtime with the
machine's openssl (3.0.13), a test-time tool, not a runtime
dependency:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 21 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing + 5 anchor consultation + 4 liveness)
packages/synchronization  Ran 24 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 4 anchor consultation + 3 coordinated rollback + 4 protected + 4 new)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

68 tests total (baseline before this experiment was 64), every
suite on `unittest discover`, the new suite also by direct file
run, the new demo and the experiment 014 demo run end to end, and
a post-run process check confirmed zero leftover `node_server`
processes. All of it is quoted verbatim in `test-output.txt`.

| # | Case 9 outcome | Test | Result |
|---|---|---|---|
| 1 | Certificates are issued for a specific node id, carried in the certificate itself (URI SAN `urn:pba:node:<node-id>`, read back out of the file by `cert_file_node_id`); correctly identified peers work exactly as in experiment 014: convergence over the protected link, a policy crossing verified, and the initiating-side check (`verify_peer_identity`) agreeing with the peer's claim - and disagreeing, as an `IdentityMismatch`, when a different id is expected | `test_cert_carries_issued_node_id_and_matched_peers_converge` | PASS: every generated leaf certificate (net-a/b/c, test-client, and the rogue CA's client) decodes to exactly the id it was issued for; probing net-a's link verifies as net-a and raises `IdentityMismatch(claimed net-b, certified net-a)` when net-b is expected; net-a and net-b mutated, synced server-to-server with no divergence on either side (both binding directions exercised in that one sync: net-a initiates and checks net-b's certificate against net-b's claim before pulling, net-b accepts net-a's links only because net-a's claim matches net-a's certificate), converged to one state hash with 2 entries, and alice's policy was served by net-a with code matching the Gate 1 record. |
| 2 | A peer presenting a VALID CA-issued certificate for a DIFFERENT node id than it claims is refused by the accepting node, plainly - and the refusal is distinct from experiment 014's: the handshake succeeds (possession is fine), the refusal is an HTTP 403 `identity_mismatch` naming claimed and certified ids, the SAME certificate is served when its claim matches its issued id, and experiment 014's strangers (no credential, rogue CA) still die in the handshake with no HTTP answer at all; the server still serves a correctly identified peer afterward | `test_mismatched_claim_refused_distinctly_and_server_still_serves` | PASS: net-b's certificate claiming net-a got 403 `identity_mismatch` (claimed net-a, certified net-b) on `/health`, `/entries`, and `/policy/<hash>` alike, and the refusal was printed to the server's log in the same plain form; the same net-b certificate claiming net-b got 200; the anonymous and rogue contexts raised at the handshake on the same server; afterward the test-client credential fetched health and the policy with 200. |
| 3 | A process claiming an id its certificate was not issued for cannot run under binding at all (startup self-check refuses it, log states the mismatch plainly), and started without binding (experiment 014 mode) it is refused by a binding-enabled INITIATOR before any entries are pulled: the initiator checks the peer server's certificate id against the id in its health claim | `test_false_claiming_process_refused_at_start_and_by_initiator` | PASS: the bound false-start process (claims net-a, presents net-b's certificate) exited on its own with a nonzero code and a log line `identity_mismatch: this process claims node id 'net-a' but its certificate was issued for 'net-b'; refusing to start`; the same false claim running unbound reported health node_id net-a while its certificate decodes to net-b, `verify_peer_identity` raised `IdentityMismatch(claimed net-a, certified net-b)`, and net-c's `/sync` toward it returned 502 `identity_mismatch` with both ids - after which net-c held zero entries and the impostor's state was unchanged. |
| 4 | A mismatched peer INITIATING toward a binding-enabled node is refused on the accepting side: its claim and its certificate disagree on the incoming link, nothing merges in either node, and correctly identified peers still sync afterward | `test_mismatched_initiator_refused_on_acceptance_nothing_merges` | PASS: the impostor (claims net-b, presents net-c's certificate, running in 014 mode) initiated `/sync` toward net-a; net-a's claim check refused its incoming links with 403 `identity_mismatch` and the impostor's sync reported 502 carrying `identity_mismatch` and both ids (net-b, net-c); net-a's state hash and the impostor's state hash were both exactly their pre-attempt values, the refusal was in net-a's log, and a correctly identified net-b then synced with net-a and converged, including net-a's pre-attempt entry. |

The demo (`python3 packages/synchronization/demo_cert_node_binding.py`)
also runs end to end and prints all four of its outcomes True
(see `test-output.txt`): matched peers converge and the policy
crosses, net-b's certificate claiming net-a is refused 403 while
the same certificate claiming net-b is served, a false-claiming
process exits at startup under binding with the mismatch in its
log, and a binding initiator refuses the unbound false claimant
with no state moving.

## How the mechanism works, in plain words

The id lives in the certificate. When `generate_test_credentials`
issues a leaf, the certificate's extension file is written for
that one leaf and carries `URI:urn:pba:node:<node-id>` in its
subjectAltName, next to the 127.0.0.1/localhost SANs the address
check needs. Two readers get the id back out: `peercert_node_id`
reads it from the dictionary a live socket's `getpeercert()`
returns (URI SAN authoritative, subject CN the fallback), and
`cert_file_node_id` decodes a certificate file into the same
shape and applies the same reader.

Binding is one flag, `--tls-bind-identity`, and it checks the
claim at the three places a claim exists. At startup, the
process decodes its own certificate and refuses to start unless
the id inside it equals `--node-id` - a node cannot even come up
lying about who it is. On acceptance, the request handler reads
the peer certificate from the accepted TLS connection itself
(not from anything the peer sent in a body) and compares it with
the claim the peer makes in the `X-PBA-Node-Id` header, which the
client helpers send from the same configuration that loads their
certificate; a disagreement is a 403 `identity_mismatch` naming
both ids, printed to the server log in the same words, before any
endpoint runs. On initiation, a server's `/sync` first asks the
peer who it claims to be (its health response) and probes the
peer's certificate over a fresh TLS connection; if the two
disagree, the sync is refused as 502 `identity_mismatch` before a
single entry is pulled. A server process always sends its own
node id as its outgoing claim when it speaks TLS - binding flag
or not - so a false-claiming process running in experiment 014
mode is still caught by any binding node it initiates toward
(outcome 4), and caught by any binding initiator that syncs
toward it (outcome 3). The claim header by itself grants nothing:
it is only ever believed against the certificate the same
connection presented.

Design choice worth naming: the check hooks into the transport,
not into Gate 3 or Gate 2, for the same reason experiment 014
gave - TLS decides who may connect, content verification decides
what may be trusted. Binding extends the transport's answer from
"holds a credential our CA issued" to "holds the credential
issued for the node id it claims", and grants the payload no
additional trust: entries arriving from a correctly identified
peer still go through `receive_entries` unchanged, and policy
serving still runs Gate 2 verification inside the serving
process, as outcomes 1 and 2 re-exercise.

Credentials in the tests are throwaway by construction, exactly
as in experiment 014: generated per test into a temporary
directory by openssl, deleted with it. No credential, key, or
certificate file exists in this repository, and none was pushed
anywhere.

## What failed

The tests-first run failed as designed: before the binding code
existed, the new suite failed at import (`IdentityMismatch` not
yet in `protected_link`), recorded verbatim in
`test-output.txt`. After the build, nothing failed: the new
suite (4 of 4 on its first post-build run and again in the
recorded runs), the full regression across all four tested
packages (with the experiment 007 and 014 suites unmodified in
the same discovery run), the handoff check, and both demos
passed on the runs quoted in `test-output.txt`. No earlier test
was modified. One observed cost, stated plainly rather than
tuned away: the synchronization discovery run took about 139
seconds in the recorded run (the new suite about 67-74 seconds
of it across its runs) versus about 18 seconds for the same
discovery in experiment 014's record - the new suite spawns more
bound processes per test and every connection is a full mutual
TLS handshake with RSA-2048 test credentials generated per test;
no performance number is claimed for the binding check itself.

## Honest limits (this is a prototype, not production)

- Binding is opt-in per process and off by default. A node
  started without `--tls-bind-identity` is experiment 014's node:
  it accepts any CA-issued certificate under any claim, exactly
  as before. Nothing here makes binding mandatory, notices a
  node that should have it and does not, or distributes the flag
  - the same per-node opt-in shape as experiment 015's anchor
  consultation, with the same consequence.
- The claim on an incoming link is a header the peer sets about
  itself. The check proves the claim matches the certificate the
  SAME connection presented; it does not prove the peer is
  entitled to the entries it then syncs or the policies it
  fetches. There is no per-endpoint authorization: any correctly
  identified credentialed peer can mutate, sync, and fetch from
  an opted-in node, exactly as any credentialed peer could in
  experiment 014.
- Identity proofing is untouched and still Gate 1's open
  production item: who SHOULD hold the certificate for a node id,
  how they prove it before issuance, and how issuance is governed
  are all unbuilt. The test CA issues a certificate for any name
  it is asked for, on the same machine, per test run. A party
  that can get the CA to issue it a certificate for someone
  else's node id passes binding completely - binding ties a
  claim to an issuance, it does not make the issuance honest.
- No certificate lifecycle: no revocation (a compromised
  certificate binds perfectly until expiry), no rotation, no
  expiry handling beyond the throwaway certificates' 3-day
  validity, and no protection of key files at rest.
- The initiating-side check judges the peer once, at sync start,
  by health claim plus a fresh probe connection. It does not
  re-check per request inside the pull/push that follows (the
  accepting side's per-request check is what covers those
  connections), and a peer whose certificate changed between the
  probe and the pull is a race this prototype does not model.
- Localhost processes on one machine only, exactly like
  experiments 007/014: nothing demonstrates binding across real
  machines or networks, and discovery by set addresses is
  unchanged and still open.
- Inherited limits stand unchanged: no persistence, no Byzantine
  consensus or quorum, Gate 1 signing is HMAC prototype crypto,
  anchor files are unsigned local stand-ins, and deliberate
  removal with notification (availability case 3) remains
  unbuilt. Experiment 015's anchor consultation and experiment
  016's liveness are not wired into these networked processes.

## Process captured

Reusable process: when a check binds two assertions of the same
fact (here, a claimed id and an issued id), the refusal that
proves the binding must be isolated from every older refusal on
the same path - the test set therefore needs the same credential
succeeding under its true claim on the same server in the same
test, and the older strangers (no credential, rogue CA) shown
failing EARLIER, at the handshake, with no HTTP answer, so the
new refusal's layer and cause are the only variables left.
Second lesson: a binding that only runs on acceptance is half a
binding - every direction the transport exposes identity needs
its own refusal test, including the awkward one where the false
claimant cannot be started under the new rules at all and must
be started under the old rules to be refused by a peer.
Opportunity Stack entry: extends experiment 014's protection
clause with an identity clause (a protected transport must not
only authenticate a credential, it must authenticate the
credential FOR THE IDENTITY CLAIMED on that link: possession of
any enrolled credential presented under any name is an
impersonation the transport itself must refuse, on acceptance
and on initiation alike). What would differ next time: record
the per-suite wall time in every experiment's test output as a
matter of routine, so a slowdown like this one's is visible in
the evidence of the experiment that caused it instead of only
recoverable by comparing two experiments' files.

## Next, per the build order

Certificate binding closes the cert-to-node-id item experiment
014 listed as open (and experiments 015/016 carried as part of
production identity proofing), at prototype scale, for the
protected networked processes. The honest distance to
utilization is unchanged in kind and shorter by that item:
deliberate-removal notification (availability case 3, blocked on
Max's answers in experiment 011), identity proofing and a
certificate lifecycle (issuance governance, revocation,
rotation - binding without them is stated above for exactly
what it is), anchor consultation and liveness wired into the
networked server processes, a witnessed or signed anchor for
production (plus a rule for who may re-anchor), discovery and
automatic sync, persistence and restart recovery, production
crypto beyond Gate 1's HMAC prototype, consensus, revocation
delivery to placed policies, and Gate 5's open production
benchmark. `PBA-FIX-001` remains talk-first and untouched by
this experiment. Nothing here is deployed beyond the localhost
test processes the tests themselves spawned and cleaned up, and
nothing in this experiment changes that.
