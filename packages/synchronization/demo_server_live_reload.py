"""
Demo for experiment 024 (sync-mesh case 13): a RUNNING server
learns of a revocation without a restart.

One anchored node server process (experiment 007's transport,
experiment 018's --anchor) is started over a two-entry registry
(alice's and bob's policies). Then, with the process never
restarted:

1. A snapshot check over an unchanged snapshot reports
   no_change and serving is undisturbed.
2. Alice revokes her policy; the updated record is copied over
   the server's snapshot file WITHOUT the re-anchor. The check
   refuses the reload as anchor_stale, /health carries the
   refusal, and the running server still serves alice's policy
   (the stale window, printed, not hidden).
3. The grown head is re-anchored (experiment 012's lifecycle)
   and ONE more check reloads the running process in that same
   check: alice's policy now refuses over the wire with case
   12's clean 410 policy_revoked, bob's still serves, and
   /health reports the new standing (3 entries loaded, last
   check "reloaded").

Run:  python3 packages/synchronization/demo_server_live_reload.py
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
    as in policy_node.py and experiment 018's demo."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_demo_live_reload_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"


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
    tmp = Path(tempfile.mkdtemp(prefix="pba-server-live-reload-demo-"))
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
             "--node-id", "live-demo",
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

            report = node_server.check_snapshot(base)
            print(f"1. Check, nothing changed: {report['status']} - "
                  f"loaded_entries: {report['loaded_entries']}")

            channel.revoke(
                author="alice", policy_hash=alice.policy_hash,
                signature=sign_revocation(alice_secret, alice.policy_hash))
            shutil.copyfile(registry_log, snapshot)  # delivered, NOT re-anchored
            report = node_server.check_snapshot(base)
            status, _body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"2. Revoked + delivered, no re-anchor: "
                  f"{report['status']} - reason: {report['reason']} - "
                  f"alice still serves (stale window): {status == 200}")

            write_anchor(snapshot, anchor_path)  # the anchor lifecycle
            report = node_server.check_snapshot(base)
            print(f"3. Re-anchored, one check: {report['status']} - "
                  f"entries {report['before_count']} -> "
                  f"{report['after_count']} - loaded: "
                  f"{report['loaded_entries']}")
            status, body = node_server.fetch_policy(base, alice.policy_hash)
            print(f"   alice over the wire now: {status} - "
                  f"error: {body.get('error')}")
            status, body = node_server.fetch_policy(base, bob.policy_hash)
            print(f"   bob still serves: {status} - "
                  f"{base64.b64decode(body['code_base64']) == BOB_CODE}")
            health = node_server.fetch_health(base)
            print(f"   health standing: anchor_status="
                  f"{health['anchor_status']} - snapshot_entries="
                  f"{health['snapshot_entries']} - last check: "
                  f"{health['last_snapshot_check']['status']}")
            print(f"   the server process was never restarted: "
                  f"pid {proc.pid} throughout")
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
