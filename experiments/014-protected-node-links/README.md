# 014. Protected node links: encrypted and authenticated connections between networked nodes

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/synchronization/` (new `protected_link.py`,
`test_protected_nodes.py`, `demo_protected_nodes.py`; additive
mutual-TLS support in `node_server.py`: `--tls-cert/--tls-key/
--tls-ca` server arguments, an optional TLS wrap in `build_server`,
and `set_client_tls`/`clear_client_tls` for its client helpers).
No endpoint, handler, or verification path was changed, and started
without the TLS arguments a server is exactly experiment 007's
plaintext node - the unmodified experiment 007 suite passes in the
same discovery run as the new one. This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and it does
not work on deliberate removal (availability case 3, experiment
011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 7, added to that
file before the code was built, in the detailed format of
`tests/test-cases/policy-availability.md` case 4. The new tests
were run against the unbuilt code first (import failure,
`ModuleNotFoundError: No module named 'protected_link'`) so the
case is proven to be what the code had to satisfy; that failing
output is recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages, the
new suite by direct file run, the handoff check, the new demo, and
the leftover-process check).

## Question

Experiment 007 put nodes in real processes on a real localhost
transport and named its own limit plainly: the connection is
unencrypted, with no sign-in or authentication, and nodes find
each other only by set addresses. Anything that could reach a
node's port could mutate its state, sync with it, and fetch its
policies, and everything crossed in plaintext. Can the prototype
now close the first two parts of that limit - encryption on the
wire, and authentication of who is on the other end - without
moving or bypassing the verification the earlier gates built, and
with experiment 007's behaviors (convergence, a policy crossing
with verification, survivors serving after a kill) still passing
over the protected link? Discovery by set addresses is the third
part of the limit and stays open by design.

## Result: all case 7 outcomes observed, 4 new tests pass, full regression green

Run on Python 3.12.3, standard library only for the transport
(`ssl`); test certificates are generated at runtime with the
machine's openssl (3.0.13), which is a test-time tool, not a
runtime dependency:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 12 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing)
packages/synchronization  Ran 16 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 3 coordinated rollback + 4 new)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

51 tests total (baseline before this experiment was 47), every
suite on `unittest discover`, the new suite also by direct file
run, the new demo run end to end, and a post-run process check
confirmed zero leftover `node_server` processes. All of it is
quoted verbatim in `test-output.txt`.

| # | Case 7 outcome | Test | Result |
|---|---|---|---|
| 1 | Two node processes exchange entries and a policy over a connection encrypted in transit, proven by capture: traffic driven through a raw byte-capturing forwarding proxy succeeds, and the captured bytes contain no plaintext it carried | `test_protected_processes_converge_and_wire_capture_has_no_plaintext` | PASS: through the proxy, a mutation carrying the distinctive value `capture-secret-value-014` was written, the entries were pulled back with that value intact, and alice's policy was fetched with status 200 and code matching the Gate 1 record; the capture held over 500 bytes (16,222 bytes in the demo's equivalent run) and contained neither the secret value, its key, the policy code, nor the policy's identifying strings. Off the proxy, the two processes then converged over HTTPS exactly as in experiment 007 (identical state, hash, and 3 entry ids), and entries fetched over the protected link merged into a fresh local `SyncNode` through the same `receive_entries` verification, landing on the same state hash. |
| 2 | A connection from an unauthenticated peer is refused: it cannot sync entries or receive a policy; and there is no plaintext fallback on a protected port | `test_unauthenticated_and_rogue_peers_are_refused` | PASS: a client context that trusts the test CA but presents no certificate fails the handshake against `/health`, `/entries`, and `/policy/<hash>`; a client presenting a certificate issued by a separately generated rogue CA fails the same way; a plain HTTP request to the protected port produces no usable response; and none of the refusals wedged the server - a credentialed client connected and was served immediately afterward. |
| 3 | A tampered message in transit is still caught by the existing verification - encryption does not bypass Gate 3 or Gate 1/2 checks | `test_policy_crosses_protected_link_verified_and_tamper_still_caught` | PASS: over HTTPS, an opted-in process served alice's policy with code, code hash, validation channel, and record hash all matching the Gate 1 record; a non-opted-in process holding the same verified policy refused with HTTP 403; an unknown hash got 404; the registry snapshot crossed as raw bytes over the protected link and verified with Gate 2's standalone `verify_registry_snapshot` on receipt, and a `PolicyNode` built over the received bytes served the policy; a tampered entry pushed over the protected link got Gate 3's `divergence_detected=True, rolled_back=True, reason=tampered_entry` from the receiving process, whose state hash was unchanged. |
| 4 | The experiment 007 behaviors still pass over the protected link: convergence, policy crossing with verification, survivors serving after one process is killed | `test_protected_processes_converge_and_wire_capture_has_no_plaintext`, `test_policy_crosses_protected_link_verified_and_tamper_still_caught`, `test_killed_protected_process_survivors_still_serve_and_converge` | PASS: convergence and policy crossing are outcomes 1 and 3 above; for the kill outcome, three protected processes converged, the second was SIGKILLed and its port stopped answering, and the survivors still served the mesh state (including the killed process's already-replicated entry) and the policy, then converged with each other on new state written after the kill, ending with equal state hashes and 3 entries on both. |

The demo (`python3 packages/synchronization/demo_protected_nodes.py`)
also runs end to end and prints all four of its outcomes True
(see `test-output.txt`): two protected processes converge, a
capture through the proxy holds 16,222 bytes with neither the
secret value nor the policy code in it, a credential-less client
is refused at the handshake, and after the second process is
killed the first still serves state and policy.

## How the mechanism works, in plain words

The protection is mutual TLS, and it lives at the socket, before
HTTP. A server started with `--tls-cert`, `--tls-key`, and
`--tls-ca` wraps its listening socket in a server TLS context
(`protected_link.server_context`) whose verification mode is
`CERT_REQUIRED` against that CA: a peer that cannot present a
certificate chaining to the CA never completes the handshake, so
it never reaches an endpoint, and there is no plaintext mode on
that port to fall back to. Every client of a protected node -
the tests, the demo, and a server process initiating `/sync` -
presents a certificate and verifies the server against the same
CA with the address check on (`protected_link.client_context`;
node certificates carry SANs for 127.0.0.1 and localhost). A
server process uses its OWN node certificate as its client
credential for outgoing sync, so a node is authenticated the same
way in both directions: the credential is the node's identity as
a peer, not a separate operator login.

One CA per deployment is the whole trust structure at this scale:
"authenticated" means exactly "holds a certificate this CA
issued". The certificate's CN names a node, but nothing in the
transport checks that name against the node id a process claims
in its payloads - TLS authenticates the connection, and Gate 3/2
verification, running unchanged inside the receiving process,
still authenticates the CONTENT. That division is deliberate and
is what outcome 3 proves: the transport decides who may connect
and keeps the wire unreadable; it grants the payload no trust.

Credentials in the tests are throwaway by construction.
`protected_link.generate_test_credentials` shells out to openssl
to create, inside the caller's own directory, one test CA, one
certificate per named node plus one for the test process as a
client, and a second, rogue CA with its own client certificate
for the refusal tests. The tests call it into their per-test
temporary directory and the demo into a temporary directory that
is deleted at exit. No credential, key, or certificate file
exists in this repository, and none was pushed anywhere.

Design choice worth naming: the TLS support in `node_server.py`
is additive and opt-in per process, rather than a new server,
because the claim being tested is "the SAME transport, protected"
- the same endpoints, handlers, and verification code, with the
socket wrapped. The experiment 007 suite running unmodified in
the same discovery run is the evidence that the plaintext path
did not move.

## What failed

The tests-first run failed as designed: before
`protected_link.py` existed, the new suite failed at import
(`ModuleNotFoundError`), and that failure is recorded verbatim in
`test-output.txt`, not only summarized here. After the modules
were built, nothing failed: the new suite (4 of 4 on its first
full run and again in the recorded runs), the full regression
across all four tested packages, the handoff check, and the demo
passed on their runs quoted in `test-output.txt`. No earlier test
was modified.

## Honest limits (this is a prototype, not production)

- Localhost processes on one machine only, exactly like
  experiment 007. Nothing here demonstrates TLS across real
  machines, real networks, NAT or firewalls, or a CA operated by
  anyone - the test CA is generated per test run on the same
  machine that runs both ends, which proves the handshake and
  capture logic and nothing about operating a real PKI.
- Authentication is possession of a CA-issued certificate, full
  stop. There is no identity proofing of who SHOULD hold one
  (Gate 1's production-crypto question, unchanged), no check
  binding a certificate's CN to the node id a process claims, no
  per-endpoint authorization (any credentialed peer can mutate,
  sync, and fetch from an opted-in node, exactly as any peer could
  in experiment 007 - the change is that an UNcredentialed peer
  now cannot), no certificate revocation [superseded in part
  by experiment 034: a CA-issued CRL over its own books,
  selected by --tls-crl, now refuses a revoked certificate at
  the handshake in both directions - rotation, live CRL
  reload, and expiry handling remain unbuilt], no rotation or expiry
  handling beyond the throwaway certificates' 3-day validity, and
  no protection of the key files at rest - a process reads its key
  from a plain file path it is started with.
- The encryption proof is a capture through a forwarding proxy on
  the same machine, checking for the specific plaintexts the
  exchange carried (an entry key and value, the policy code, and
  identifying strings). It is not a cryptographic audit of the TLS
  configuration: the floor is TLS 1.2 and the cipher suite is
  whatever Python's `ssl` defaults negotiate; no cipher policy was
  chosen, measured, or claimed, and no performance, latency, or
  throughput numbers were measured for the protected link.
- Discovery is unchanged and still open: peer addresses are
  configured by whoever starts the processes and whoever calls
  `/sync`. No peer list, no gossip schedule, no automatic re-sync
  or retry was added, and a protected node cannot discover or be
  discovered - it can only refuse strangers at addresses it is
  explicitly told about.
- Inherited limits stand unchanged: no persistence (mesh state is
  in each process's memory), no Byzantine consensus or quorum,
  local-only rollback in Gate 3's sync itself, Gate 1 signing is
  HMAC prototype crypto, anchor files are unsigned local
  stand-ins that nothing consults automatically, and deliberate
  removal with notification (availability case 3) remains
  unbuilt. Coordinated rollback (experiment 013) is still an
  in-process step; this experiment does not wire it into the
  networked processes, protected or otherwise.
- Test processes are spawned on ephemeral ports and cleaned up by
  the tests (terminate, wait, kill if needed, registered as
  cleanup the moment each process starts); the demo cleans up the
  same way and deletes its temporary credential directory. A
  post-run process check found zero leftover servers.

## Process captured

Reusable process: when a transport gains protection, prove the
protection at the layer it actually operates on, and prove the
layers it must not replace at the same time. Concretely: the
encryption claim is proven by a byte capture of a SUCCESSFUL
exchange (a capture of a failed connection would prove nothing),
checked for the exact plaintexts that exchange carried; the
authentication claim is proven with two distinct strangers (no
credential, and a credential from a different authority), because
they fail for different reasons and only one of them shows the
server is checking the chain rather than merely requiring bytes;
and the non-bypass claim is proven by re-running the previous
transport's own tamper and refusal cases over the new link,
expecting the same verdicts from the same unmodified verification
code. A protected transport that required rewriting the
verification cases to pass would have failed this experiment's
real question even with all its new tests green. Opportunity
Stack entry: extends experiment 007's transport clause (a
transport must add reach without adding trust) with a protection
clause (a protected transport must add confidentiality and peer
authentication without subtracting verification: every refusal
and verdict the plaintext transport produced from content must
still be producible, unchanged, over the protected link). What
would differ next time: record the negotiated TLS version and
cipher in the test output as an observed fact, so a future
`ssl`-defaults change on a different Python is visible in the
evidence instead of silent.

## Next, per the build order

Protected links close the transport-security item experiments
002-013 listed as open, at prototype scale, for the encryption
and authentication parts of experiment 007's stated limit. The
honest distance to utilization is unchanged in kind and shorter
by one item: deliberate-removal notification (availability
case 3, blocked on Max's answers in experiment 011), discovery
and automatic sync, liveness detection and automatic healing
triggers, persistence and restart recovery, production crypto
and identity proofing (including binding a node's certificate
to its claimed identity and a certificate lifecycle), consensus,
revocation delivery to placed policies, anchor consultation
wired into serving/sync/the Gate 5 verdict, a witnessed or
signed anchor for production, coordinated rollback wired into
the networked processes with an automatic trigger, and Gate 5's
open production benchmark. `PBA-FIX-001` remains talk-first and
untouched by this experiment. Nothing here is deployed beyond
the localhost test processes the tests themselves spawned and
cleaned up, and nothing in this experiment changes that.
