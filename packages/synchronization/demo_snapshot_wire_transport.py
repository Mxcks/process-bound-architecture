"""
Demo for experiment 025 (sync-mesh case 14): a RUNNING server
receives the updated registry record over the wire.

One anchored node server process (experiment 007's transport,
experiment 018's --anchor) is started over a two-entry registry
(alice's and bob's policies). Then, with the process never
restarted and its snapshot file never copied by hand:

1. A push of the very entries the server already stands on
   reports no_change and rewrites nothing.
2. Alice revokes her policy; the grown entries are pushed
   WITHOUT the re-anchor. The delivery is refused as
   anchor_stale, the server's file is byte-for-byte what it
   was, and the running server still serves alice's policy
   (the stale window, printed, not hidden).
3. The grown head is re-anchored (experiment 012's lifecycle)
   and the same entries are pushed again: the delivery stands,
   the server's snapshot file is rewritten from the verified
   delivery, the running PolicyNode reloads from it - alice's
   policy now refuses over the wire with case 12's clean 410
   policy_revoked, bob's still serves, /health reports the
   new standing (3 entries loaded, last delivery "delivered"),
   and a case 13 check right after reports no_change, because
   the file and the view moved together.
4. The old two-entry record is pushed once more: it is
   exactly an earlier prefix of the chain the server now
   stands on, and is refused as snapshot_regression - the
   standing record never moves backward over the wire.

Run:  python3 packages/synchronization/demo_snapshot_wire_transport.py
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
    as in policy_node.py and experiment 024's demo."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_demo_wire_transport_registry_anchor", anchor_file)
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-wire-transport-demo-"))
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
    anchor_path = tmp / "outside" / "registry-anchor.json"
    write_anchor(registry_log, anchor_path)

    snapshot = tmp / "server" / "snapshot.jsonl"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(registry_log, snapshot)

    port_file = tmp / "server.port"
    log_path = tmp / "server.log"
    with log_path.open("w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "node_server.py"),
             "--node-id", "wire-demo",
             "--port", "0", "--port-file", str(port_file),
             "--snapshot", str(snapshot),
             "--payloads", str(payloads_file),
             "--serve-policies",
             "--anchor", str(anchor_path)],
            stdout=log_fh, stderr=subprocess.STDOUT)
        try:
            base = wait_ready(proc, port_file)
            health = node_server.fetch_health(base)
            print(f"Setup. Anchored server running: anchor_status="
                  f"{health['anchor_status']} - snapshot_entries="
                  f"{health['snapshot_entries']}")
            status, body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"   alice's policy serves: {status} - "
                  f"{base64.b64decode(body['code_base64']) == ALICE_CODE}")

            standing = entries_of(registry_log)
            file_before = snapshot.read_bytes()
            report = node_server.deliver_snapshot(base, standing)
            print(f"1. Push of the standing record: {report['status']} - "
                  f"file untouched: {snapshot.read_bytes() == file_before}")

            channel.revoke(
                author="alice", policy_hash=alice.policy_hash,
                signature=sign_revocation(alice_secret, alice.policy_hash))
            grown = entries_of(registry_log)
            report = node_server.deliver_snapshot(base, grown)
            status, _body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"2. Revoked + pushed, no re-anchor: "
                  f"{report['status']} - reason: {report['reason']} - "
                  f"file untouched: {snapshot.read_bytes() == file_before} - "
                  f"alice still serves (stale window): {status == 200}")

            write_anchor(registry_log, anchor_path)  # the anchor lifecycle
            report = node_server.deliver_snapshot(base, grown)
            print(f"3. Re-anchored, pushed again: {report['status']} - "
                  f"entries {report['before_count']} -> "
                  f"{report['after_count']} - loaded: "
                  f"{report['loaded_entries']}")
            print(f"   the server's file IS the delivered record now: "
                  f"{snapshot.read_bytes() == registry_log.read_bytes()}")
            status, body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"   alice over the wire now: {status} - "
                  f"error: {body.get('error')}")
            status, body = node_server.fetch_policy(base, bob.policy_hash)
            print(f"   bob still serves: {status} - "
                  f"{base64.b64decode(body['code_base64']) == BOB_CODE}")
            report = node_server.check_snapshot(base)
            print(f"   a case 13 check right after the delivery: "
                  f"{report['status']} (file and view moved together)")

            report = node_server.deliver_snapshot(base, standing)
            status, _body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"4. The old record pushed again: {report['status']} - "
                  f"reason: {report['reason']} - alice still refuses "
                  f"(the standing record never moved): {status == 410}")

            health = node_server.fetch_health(base)
            print(f"   health standing: anchor_status="
                  f"{health['anchor_status']} - snapshot_entries="
                  f"{health['snapshot_entries']} - last delivery: "
                  f"{health['last_snapshot_delivery']['status']} / "
                  f"{health['last_snapshot_delivery']['reason']}")
            print(f"   the server process was never restarted: "
                  f"pid {proc.pid} throughout, and its snapshot file "
                  f"was never copied by hand")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
