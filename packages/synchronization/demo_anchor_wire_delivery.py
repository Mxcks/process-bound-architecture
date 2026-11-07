"""
Demo for experiment 028 (sync-mesh case 17): a running server
receives its registry anchor over the wire.

One signer-configured node server process (experiment 007's
transport, experiment 018's --anchor, experiment 019's
--anchor-signer) is started over a copy of the two-entry
registry (alice's and bob's policies) with its signed anchor.
Then, with the process never restarted and its anchor file
never written by hand after startup:

1. An anchor delivery byte-for-byte identical to the anchor
   the server holds reports no_change.
2. Alice revokes her policy. The deliverer follows the anchor
   lifecycle at the source (alice re-anchors the grown head)
   and delivers the NEW ANCHOR ALONE: refused as
   anchor_unpaired - it pairs with the record the server does
   not hold yet, not the one it stands on.
3. The deliverer delivers the PAIR - the grown record plus
   its anchor - in one call: judged together, both stand,
   both of the server's files are replaced from the verified
   pair, and the revocation is live over the wire (alice
   refuses 410, bob serves) with no restart and no manual
   lifecycle step at this server.
4. The ORIGINAL anchor (a kept history one entry shorter) is
   delivered alone: refused as anchor_regression - an earlier
   version of the server's own anchor file, refused for where
   it points, and audit-logged beside the anchor.
5. The finding, printed as it happens: mallory's rewritten
   registry (the same length, internally consistent) paired
   with an anchor over that rewrite signed by ALICE - the
   current signer, its history honestly extended by case 10's
   own write path, the deliverer legitimately holding alice's
   enrollment secret as every verifier in this symmetric
   model does - is ACCEPTED, and the server ends serving the
   rewritten record. What stops a rewrite from being blessed
   is the custody of the signer's secret, and nothing else;
   the wire adds no weakness, and the weakness is experiment
   019's recorded symmetric-model limit.

Run:  python3 packages/synchronization/demo_anchor_wire_delivery.py
Standard library only. Localhost only.
"""

import base64
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def _load_registry_anchor():
    """Experiment 012's registry anchor module, loaded by file
    path under a private name (two packages ship an anchor.py),
    as in policy_node.py and experiment 025's demo."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_demo_anchor_wire_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_anchor_mod = _load_registry_anchor()
write_anchor = _anchor_mod.write_anchor
read_anchor_audit = _anchor_mod.read_anchor_audit

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"
MALLORY2_CODE = b"policy: allow if caller.tier == 'attacker-guest'\n"


def entries_of(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def anchor_dict(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def wait_ready(proc, port_file: Path, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early rc={proc.returncode}")
        if port_file.exists():
            port = int(port_file.read_text(encoding="utf-8").strip())
            base_url = f"http://127.0.0.1:{port}"
            try:
                node_server.fetch_health(base_url)
                return base_url
            except (AssertionError, urllib.error.URLError, OSError):
                pass
        time.sleep(0.05)
    raise RuntimeError("server not ready")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-anchor-wire-demo-"))
    central = tmp / "central"
    registry_log = central / "registry.jsonl"
    channel = RegistrationChannel(registry_log, central / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    bob_secret = channel.enroll_user("bob")
    alice, _ = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=ALICE_CODE,
        signature=sign_library(alice_secret, "alice", "member-access",
                               "1.0.0", ALICE_CODE))
    bob, _ = channel.register(
        author="bob", name="guest-access", version="1.0.0",
        code=BOB_CODE,
        signature=sign_library(bob_secret, "bob", "guest-access",
                               "1.0.0", BOB_CODE))

    # Mallory's rewritten registry, built now so the server can
    # hold her first policy's payload from the start (a node
    # serves only payloads it holds; the record decides whose
    # policy that is).
    evil_dir = tmp / "evil-central"
    evil_log = evil_dir / "registry.jsonl"
    evil = RegistrationChannel(evil_log, evil_dir / "audit.jsonl")
    mallory_secret = evil.enroll_user("mallory")
    mallory, _ = evil.register(
        author="mallory", name="member-access", version="1.0.0",
        code=MALLORY_CODE,
        signature=sign_library(mallory_secret, "mallory",
                               "member-access", "1.0.0", MALLORY_CODE))
    evil.register(
        author="mallory", name="guest-access", version="1.0.0",
        code=MALLORY2_CODE,
        signature=sign_library(mallory_secret, "mallory",
                               "guest-access", "1.0.0", MALLORY2_CODE))

    payloads_file = tmp / "payloads.json"
    payloads_file.write_text(json.dumps({
        alice.policy_hash: ALICE_CODE.hex(),
        bob.policy_hash: BOB_CODE.hex(),
        mallory.policy_hash: MALLORY_CODE.hex(),
    }), encoding="utf-8")

    anchor_path = tmp / "outside" / "registry-anchor.json"
    write_anchor(registry_log, anchor_path,
                 signer="alice", secret_hex=alice_secret)
    original_anchor = tmp / "original-anchor.json"
    shutil.copyfile(anchor_path, original_anchor)
    secret_file = tmp / "outside" / "anchor-secret.txt"
    secret_file.write_text(alice_secret, encoding="utf-8")
    snapshot = tmp / "server" / "snapshot.jsonl"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(registry_log, snapshot)

    log_fh = (tmp / "server.log").open("w", encoding="utf-8")
    port_file = tmp / "server.port"
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "node_server.py"),
         "--node-id", "anchor-demo",
         "--port", "0", "--port-file", str(port_file),
         "--snapshot", str(snapshot),
         "--payloads", str(payloads_file),
         "--serve-policies",
         "--anchor", str(anchor_path),
         "--anchor-signer", "alice",
         "--anchor-secret-file", str(secret_file)],
        stdout=log_fh, stderr=subprocess.STDOUT)
    try:
        base = wait_ready(proc, port_file)
        print(f"Setup. One signer-configured server running on "
              f"the 2-entry record under alice's signed anchor: "
              f"pid {proc.pid}")

        report = node_server.deliver_anchor(base, anchor_dict(anchor_path))
        print(f"1. The anchor it already holds, delivered again: "
              f"{report['status']} - nothing written")

        channel.revoke(
            author="alice", policy_hash=alice.policy_hash,
            signature=sign_revocation(alice_secret, alice.policy_hash))
        staging = tmp / "staging"
        staging.mkdir(exist_ok=True)
        grown_anchor = staging / "grown-anchor.json"
        shutil.copyfile(anchor_path, grown_anchor)
        write_anchor(registry_log, grown_anchor,
                     signer="alice", secret_hex=alice_secret)
        report = node_server.deliver_anchor(
            base, anchor_dict(grown_anchor))
        status, _body = node_server.fetch_policy(base, alice.policy_hash)
        print(f"2. The new anchor alone, before its record: "
              f"{report['status']} / {report['reason']} - it "
              f"pairs with nothing the server holds yet; alice "
              f"still serves (the stale window): {status == 200}")

        report = node_server.deliver_anchor(
            base, anchor_dict(grown_anchor),
            entries=entries_of(registry_log))
        status_a, body_a = node_server.fetch_policy(base,
                                                    alice.policy_hash)
        status_b, body_b = node_server.fetch_policy(base,
                                                    bob.policy_hash)
        health = node_server.fetch_health(base)
        print(f"3. Record + anchor delivered together: "
              f"{report['status']} - loaded "
              f"{report['loaded_entries']} entries under the "
              f"delivered anchor; alice over the wire now: "
              f"{status_a} / {body_a.get('error')}; bob serves: "
              f"{status_b == 200 and base64.b64decode(body_b['code_base64']) == BOB_CODE}; "
              f"health: snapshot_entries="
              f"{health['snapshot_entries']}, anchor_status="
              f"{health['anchor_status']}, last anchor delivery: "
              f"{health['last_anchor_delivery']['status']}")

        anchor_now = anchor_path.read_bytes()
        report = node_server.deliver_anchor(
            base, anchor_dict(original_anchor))
        events = read_anchor_audit(anchor_path)
        print(f"4. The ORIGINAL anchor delivered alone: "
              f"{report['status']} / {report['reason']} - an "
              f"earlier version of the server's own anchor file; "
              f"anchor file unchanged: "
              f"{anchor_path.read_bytes() == anchor_now}; audit "
              f"log's last event: {events[-1]['event']} / "
              f"{events[-1]['reason']}")

        evil_anchor = staging / "evil-anchor.json"
        shutil.copyfile(anchor_path, evil_anchor)
        write_anchor(evil_log, evil_anchor,
                     signer="alice", secret_hex=alice_secret)
        report = node_server.deliver_anchor(
            base, anchor_dict(evil_anchor),
            entries=entries_of(evil_log))
        status_m, body_m = node_server.fetch_policy(base,
                                                    mallory.policy_hash)
        status_a2, _body_a2 = node_server.fetch_policy(base,
                                                       alice.policy_hash)
        print(f"5. FINDING: mallory's rewritten record + an "
              f"anchor over it signed by alice (the current "
              f"signer, history honestly extended): "
              f"{report['status']} - the server now serves the "
              f"rewritten record: mallory {status_m} with her "
              f"exact bytes: "
              f"{base64.b64decode(body_m['code_base64']) == MALLORY_CODE}; "
              f"alice's genuine policy: {status_a2}. What stops "
              f"a rewrite from being blessed is the custody of "
              f"the signer's secret, and nothing else")
        print(f"   the process was never restarted: pid "
              f"{proc.pid} throughout, and its anchor file was "
              f"never written by hand after startup")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log_fh.close()
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
