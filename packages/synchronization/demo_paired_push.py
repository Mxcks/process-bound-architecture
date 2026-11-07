"""
Demo for experiment 030 (sync-mesh case 19): one server
pushes its record AND its anchor to a peer, and the peer
judges the pair together.

Two node server processes are started over copies of the
same two-entry registry (alice's and bob's policies), each
consulting its own anchor file, the anchors SIGNED by alice
(experiment 019) with each server configured for that
signer. Then, with neither process restarted and no hand
touching server B's files:

1. Alice revokes her policy; the anchor lifecycle is
   followed for server A alone (A's anchor re-written over
   the grown record by the same signer), and the grown
   record plus its new anchor reach A through A's own
   accepted case 17 paired delivery (experiment 028). B
   still serves alice's policy - the stale window, printed,
   not hidden.
2. The control: a record-only push (case 16) from A to B
   refuses anchor_stale - B's anchor still stands over the
   old head, and the record alone cannot advance it.
3. ONE driven paired push (POST /push-anchor) from A: A
   sends the entries its PolicyNode stands on and the
   anchor object its own anchor file holds to B's case 17
   paired surface; B judges the pair together and applies
   both - B's snapshot file IS the channel's record and
   B's anchor file IS the anchor A sent - alice's policy
   refuses over B's wire with case 12's clean 410
   policy_revoked, bob's still serves, B's /health reports
   the paired delivery as its last anchor delivery, and
   A's /health carries the push as its last anchor push.
4. The same paired push driven again reports no_change:
   B already stands on the identical record under the
   identical anchor, and neither of its files is rewritten.

Run:  python3 packages/synchronization/demo_paired_push.py
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
    as in policy_node.py and experiment 027's demo."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_demo_paired_push_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"


def entries_of(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def anchor_of(path: Path) -> dict:
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


def start_server(tmp: Path, name: str, snapshot: Path,
                 payloads_file: Path, anchor_path: Path,
                 secret_file: Path, log_fh):
    port_file = tmp / f"{name}.port"
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "node_server.py"),
         "--node-id", name,
         "--port", "0", "--port-file", str(port_file),
         "--snapshot", str(snapshot),
         "--payloads", str(payloads_file),
         "--serve-policies",
         "--anchor", str(anchor_path),
         "--anchor-signer", "alice",
         "--anchor-secret-file", str(secret_file)],
        stdout=log_fh, stderr=subprocess.STDOUT)
    return proc, wait_ready(proc, port_file)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-paired-push-demo-"))
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
    payloads_file = tmp / "payloads.json"
    payloads_file.write_text(json.dumps({
        alice.policy_hash: ALICE_CODE.hex(),
        bob.policy_hash: BOB_CODE.hex(),
    }), encoding="utf-8")

    anchor_a = tmp / "a-outside" / "registry-anchor.json"
    anchor_b = tmp / "b-outside" / "registry-anchor.json"
    write_anchor(registry_log, anchor_a,
                 signer="alice", secret_hex=alice_secret)
    write_anchor(registry_log, anchor_b,
                 signer="alice", secret_hex=alice_secret)
    secret_a = anchor_a.parent / "anchor-secret.txt"
    secret_a.write_text(alice_secret, encoding="utf-8")
    secret_b = anchor_b.parent / "anchor-secret.txt"
    secret_b.write_text(alice_secret, encoding="utf-8")
    snap_a = tmp / "server-a" / "snapshot.jsonl"
    snap_b = tmp / "server-b" / "snapshot.jsonl"
    for snap in (snap_a, snap_b):
        snap.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(registry_log, snap)

    log_a = (tmp / "server-a.log").open("w", encoding="utf-8")
    log_b = (tmp / "server-b.log").open("w", encoding="utf-8")
    proc_a = proc_b = None
    try:
        proc_a, base_a = start_server(
            tmp, "pair-demo-a", snap_a, payloads_file, anchor_a,
            secret_a, log_a)
        proc_b, base_b = start_server(
            tmp, "pair-demo-b", snap_b, payloads_file, anchor_b,
            secret_b, log_b)
        print(f"Setup. Two servers running on the same 2-entry "
              f"record, each under its own alice-signed anchor: "
              f"A pid {proc_a.pid}, B pid {proc_b.pid}")

        channel.revoke(
            author="alice", policy_hash=alice.policy_hash,
            signature=sign_revocation(alice_secret, alice.policy_hash))
        # The anchor lifecycle, followed for A alone.
        write_anchor(registry_log, anchor_a,
                     signer="alice", secret_hex=alice_secret)
        report = node_server.deliver_anchor(
            base_a, anchor_of(anchor_a), entries_of(registry_log))
        status, _body = node_server.fetch_policy(base_b,
                                                 alice.policy_hash)
        print(f"1. Revocation delivered to A as a pair (case "
              f"17): {report['status']} - B still serves "
              f"alice's policy (the stale window): "
              f"{status == 200}")

        b_snap_before = snap_b.read_bytes()
        b_anchor_before = anchor_b.read_bytes()
        status, push = node_server.push_snapshot(base_a, base_b)
        print(f"2. Control - the record-only push (case 16): "
              f"{push['status']} / {push['delivery']['reason']} "
              f"- B's files unchanged: "
              f"{snap_b.read_bytes() == b_snap_before and anchor_b.read_bytes() == b_anchor_before}")

        status, push = node_server.push_anchor(base_a, base_b)
        delivery = push["delivery"]
        print(f"3. ONE driven paired push, A -> B: "
              f"{push['status']} - snapshot half: "
              f"{delivery['snapshot_status']} (entries "
              f"{delivery['before_count']} -> "
              f"{delivery['after_count']}, loaded "
              f"{delivery['loaded_entries']}), anchor "
              f"sequence {delivery['before_seq']} -> "
              f"{delivery['after_seq']}")
        print(f"   B's snapshot IS the delivered record: "
              f"{snap_b.read_bytes() == registry_log.read_bytes()}; "
              f"B's anchor IS the anchor A sent: "
              f"{anchor_b.read_bytes() == anchor_a.read_bytes()}")
        status, body = node_server.fetch_policy(base_b,
                                                alice.policy_hash)
        print(f"   alice over B's wire now: {status} - "
              f"error: {body.get('error')}")
        status, body = node_server.fetch_policy(base_b, bob.policy_hash)
        print(f"   bob still serves from B: {status} - "
              f"{base64.b64decode(body['code_base64']) == BOB_CODE}")
        health_a = node_server.fetch_health(base_a)
        health_b = node_server.fetch_health(base_b)
        print(f"   B health: snapshot_entries="
              f"{health_b['snapshot_entries']} - last anchor "
              f"delivery: "
              f"{health_b['last_anchor_delivery']['status']}; "
              f"A health: last anchor push: "
              f"{health_a['last_anchor_push']['status']}")

        b_snap_now = snap_b.read_bytes()
        b_anchor_now = anchor_b.read_bytes()
        status, push = node_server.push_anchor(base_a, base_b)
        print(f"4. The same paired push again: {push['status']} "
              f"- B's snapshot rewritten: "
              f"{snap_b.read_bytes() != b_snap_now}; B's anchor "
              f"rewritten: {anchor_b.read_bytes() != b_anchor_now}")
        print(f"   neither process was restarted: A pid "
              f"{proc_a.pid}, B pid {proc_b.pid} throughout, and "
              f"no hand touched B's files")
    finally:
        for proc in (proc_a, proc_b):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        log_a.close()
        log_b.close()
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
