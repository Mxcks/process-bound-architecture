"""
Runnable demo of anchor consultation in the networked node
server processes (experiment 018).

    python3 packages/synchronization/demo_anchor_in_servers.py

Walks through registration test case 9's server half in order and
prints what happened: a server started with --anchor over the
genuine snapshot starts and serves; the negative control - a
registry rewritten from genesis, internally consistent - is
served by a server with NO anchor (the pre-018 behavior, stated)
and refused at startup, named anchor_mismatch, by a server WITH
the anchor; and honest growth past the anchor is refused at
startup as anchor_stale until the grown head is re-anchored, after
which the same snapshot starts and serves. All state is in a
temporary directory; servers are spawned on ephemeral localhost
ports and terminated. Nothing is deployed.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from anchor import write_anchor  # noqa: E402  (registration anchor, exp 012)
from registration import RegistrationChannel, sign_library  # noqa: E402

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"
BOB_CODE = b"policy: billing export for members\n"


def spawn(tmp: Path, name: str, args: list[str]):
    port_file = tmp / f"{name}.port"
    log_path = tmp / f"{name}.log"
    log_fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--node-id", name, "--port", "0",
         "--port-file", str(port_file), *args],
        stdout=log_fh, stderr=subprocess.STDOUT,
    )
    return proc, port_file, log_path, log_fh


def wait_url(proc, port_file, name: str, timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server {name} exited early rc={proc.returncode}")
        if port_file.exists():
            try:
                port = int(port_file.read_text().strip())
            except (ValueError, OSError):
                time.sleep(0.05)  # port file written but not yet flushed
                continue
            url = f"http://127.0.0.1:{port}"
            try:
                node_server.fetch_health(url)
                return url
            except Exception:
                pass
        time.sleep(0.05)
    raise AssertionError(f"server {name} not ready")


def stop(proc, log_fh) -> None:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
    log_fh.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-anchor-servers-demo-"))
    central = tmp / "central"
    registry_log = central / "registry.jsonl"
    channel = RegistrationChannel(registry_log, central / "audit.jsonl")
    secret = channel.enroll_user("alice")
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0", code=ALICE_CODE,
        signature=sign_library(secret, "alice", "member-access", "1.0.0",
                               ALICE_CODE),
    )
    payloads = tmp / "payloads.json"
    payloads.write_text(json.dumps({record.policy_hash: ALICE_CODE.hex()}),
                        encoding="utf-8")
    anchor_path = tmp / "outside" / "registry-anchor.json"
    write_anchor(registry_log, anchor_path)

    def snapshot(name: str, source: Path = registry_log) -> Path:
        d = tmp / f"{name}-dir"
        d.mkdir(exist_ok=True)
        dest = d / "snapshot.jsonl"
        shutil.copyfile(source, dest)
        return dest

    print("Setup. Alice registered; the genuine head is anchored outside.")

    # 1. Genuine snapshot + anchor: starts and serves.
    proc, pf, _lp, fh = spawn(tmp, "demo-genuine", [
        "--snapshot", str(snapshot("demo-genuine")), "--payloads",
        str(payloads), "--serve-policies", "--anchor", str(anchor_path)])
    url = wait_url(proc, pf, "demo-genuine")
    health = node_server.fetch_health(url)
    status, body = node_server.fetch_policy(url, record.policy_hash)
    print("1. Anchored server over the genuine snapshot serves:",
          status == 200 and base64.b64decode(body["code_base64"]) == ALICE_CODE,
          "- anchor_status:", health["anchor_status"])
    stop(proc, fh)

    # 2. Negative control: a rewritten registry. Anchorless serves
    # it (pre-018); anchored refuses to start, anchor_mismatch.
    evil = tmp / "evil-central"
    evil_log = evil / "registry.jsonl"
    ech = RegistrationChannel(evil_log, evil / "audit.jsonl")
    esecret = ech.enroll_user("mallory")
    erecord, _ = ech.register(
        author="mallory", name="member-access", version="1.0.0",
        code=MALLORY_CODE,
        signature=sign_library(esecret, "mallory", "member-access", "1.0.0",
                               MALLORY_CODE),
    )
    epayloads = tmp / "evil-payloads.json"
    epayloads.write_text(json.dumps({erecord.policy_hash: MALLORY_CODE.hex()}),
                         encoding="utf-8")
    proc, pf, _lp, fh = spawn(tmp, "demo-anchorless", [
        "--snapshot", str(snapshot("demo-anchorless", evil_log)),
        "--payloads", str(epayloads), "--serve-policies"])
    url = wait_url(proc, pf, "demo-anchorless")
    status, body = node_server.fetch_policy(url, erecord.policy_hash)
    print("2. Anchorless server serves the rewrite (pre-018 behavior):",
          status == 200)
    stop(proc, fh)
    proc, _pf, lp, fh = spawn(tmp, "demo-rewritten", [
        "--snapshot", str(snapshot("demo-rewritten", evil_log)),
        "--payloads", str(epayloads), "--serve-policies",
        "--anchor", str(anchor_path)])
    rc = proc.wait(timeout=15)
    fh.close()
    log = lp.read_text(encoding="utf-8")
    print("   Anchored server refused to start over the rewrite:",
          rc != 0, "- log names anchor_mismatch:", "anchor_mismatch" in log)

    # 3. Honest growth: refused as anchor_stale until re-anchored.
    grown = snapshot("demo-grown")
    gch = RegistrationChannel(grown, tmp / "grow-audit.jsonl")
    gsecret = gch.enroll_user("bob")
    gch.register(author="bob", name="billing-export", version="1.0.0",
                 code=BOB_CODE,
                 signature=sign_library(gsecret, "bob", "billing-export",
                                        "1.0.0", BOB_CODE))
    proc, _pf, lp, fh = spawn(tmp, "demo-stale", [
        "--snapshot", str(grown), "--payloads", str(payloads),
        "--serve-policies", "--anchor", str(anchor_path)])
    rc = proc.wait(timeout=15)
    fh.close()
    log = lp.read_text(encoding="utf-8")
    print("3. Anchored server refused honest growth as anchor_stale:",
          rc != 0 and "anchor_stale" in log)
    write_anchor(grown, anchor_path)
    proc, pf, _lp, fh = spawn(tmp, "demo-reanchored", [
        "--snapshot", str(grown), "--payloads", str(payloads),
        "--serve-policies", "--anchor", str(anchor_path)])
    url = wait_url(proc, pf, "demo-reanchored")
    status, _body = node_server.fetch_policy(url, record.policy_hash)
    print("   After re-anchoring the grown head, it starts and serves:",
          status == 200)
    stop(proc, fh)

    print(f"Demo state (temp, servers terminated): {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
