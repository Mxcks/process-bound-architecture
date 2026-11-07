"""
Demo for experiment 027 (sync-mesh case 16): one server
pushes its standing record to another server.

Two anchored node server processes (experiment 007's
transport, experiment 018's --anchor) are started over copies
of the same two-entry registry (alice's and bob's policies),
each consulting its own anchor file. Then, with neither
process restarted and server B's snapshot file never copied
by hand:

1. Alice revokes her policy; the anchor lifecycle is
   followed for both servers, and the grown record reaches
   server A through A's own accepted case 14 delivery
   (experiment 025). B still serves alice's policy - the
   stale window, printed, not hidden.
2. A push from A to B is driven (POST /push-snapshot): A
   sends the entries its PolicyNode stands on to B's case 14
   surface; B judges them exactly as it judges any delivery
   and applies them - B's snapshot file is rewritten from
   the verified delivery (it IS the channel's record now),
   alice's policy refuses over B's wire with case 12's clean
   410 policy_revoked, bob's still serves, B's /health
   reports the new standing, and A's /health carries the
   push as its last one.
3. The same push driven again reports no_change: B already
   stands on the identical record, and its file is not
   rewritten.

Run:  python3 packages/synchronization/demo_server_to_server_delivery.py
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
        "pba_demo_server_push_registry_anchor", anchor_file)
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
                 payloads_file: Path, anchor_path: Path, log_fh):
    port_file = tmp / f"{name}.port"
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "node_server.py"),
         "--node-id", name,
         "--port", "0", "--port-file", str(port_file),
         "--snapshot", str(snapshot),
         "--payloads", str(payloads_file),
         "--serve-policies",
         "--anchor", str(anchor_path)],
        stdout=log_fh, stderr=subprocess.STDOUT)
    return proc, wait_ready(proc, port_file)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-server-push-demo-"))
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
    write_anchor(registry_log, anchor_a)
    write_anchor(registry_log, anchor_b)
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
            tmp, "push-demo-a", snap_a, payloads_file, anchor_a, log_a)
        proc_b, base_b = start_server(
            tmp, "push-demo-b", snap_b, payloads_file, anchor_b, log_b)
        print(f"Setup. Two anchored servers running on the same "
              f"2-entry record: A pid {proc_a.pid}, B pid {proc_b.pid}")

        channel.revoke(
            author="alice", policy_hash=alice.policy_hash,
            signature=sign_revocation(alice_secret, alice.policy_hash))
        write_anchor(registry_log, anchor_a)  # the anchor lifecycle,
        write_anchor(registry_log, anchor_b)  # performed for both
        report = node_server.deliver_snapshot(
            base_a, entries_of(registry_log))
        status, _body = node_server.fetch_policy(base_b,
                                                 alice.policy_hash)
        print(f"1. Revocation delivered to A (case 14): "
              f"{report['status']} - B still serves alice's policy "
              f"(the stale window): {status == 200}")

        b_before = snap_b.read_bytes()
        status, push = node_server.push_snapshot(base_a, base_b)
        delivery = push["delivery"]
        print(f"2. One driven push, A -> B: {push['status']} - "
              f"entries {delivery['before_count']} -> "
              f"{delivery['after_count']} - loaded: "
              f"{delivery['loaded_entries']}")
        print(f"   B's file IS the delivered record now: "
              f"{snap_b.read_bytes() == registry_log.read_bytes()} "
              f"(and it changed: {snap_b.read_bytes() != b_before})")
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
              f"{health_b['snapshot_entries']} - last delivery: "
              f"{health_b['last_snapshot_delivery']['status']}; "
              f"A health: last push: "
              f"{health_a['last_snapshot_push']['status']}")

        b_now = snap_b.read_bytes()
        status, push = node_server.push_snapshot(base_a, base_b)
        print(f"3. The same push again: {push['status']} - B's "
              f"file rewritten: {snap_b.read_bytes() != b_now}")
        print(f"   neither process was restarted: A pid "
              f"{proc_a.pid}, B pid {proc_b.pid} throughout, and "
              f"B's snapshot file was never copied by hand")
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
