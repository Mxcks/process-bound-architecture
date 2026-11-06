"""
Runnable demo of anchor evidence in the Gate 5 verdict
(experiment 018).

    python3 packages/validation/demo_gate5_anchor.py

Builds the Gate 5 canary scenario, anchors both of its chains
(experiment 012), and prints three verdicts: with no anchors the
verdict is experiment 006's; with genuine anchors it still passes
and the two anchor checks pass as anchored; after the registry
snapshot is rewritten in a way that passes every internal check
(alice's deterministic entry intact, bob's entries carrying
different code, so the head moves while the chain verifies and
the canary policy still serves), the SAME gate code returns FAIL
and the registry anchor check is the only failing check, named
anchor_mismatch. All state lives in a temp directory that is left
on disk and printed at the end.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gate5_harness import CANARY_CODE, CANARY_POLICY_NAME, CANARY_POLICY_VERSION, Gate5Harness  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


registry_anchor = _load_module(
    "pba_gate5_demo_registry_anchor", HERE.parent / "registration" / "anchor.py")
mesh_anchor = _load_module(
    "pba_gate5_demo_mesh_anchor", HERE.parent / "synchronization" / "anchor.py")


def show(label: str, verdict) -> None:
    failing = [c.name for c in verdict.checks if not c.ok]
    anchors = {c.name: c.detail for c in verdict.checks
               if c.name.startswith("anchor_evidence")}
    print(f"{label}: passed={verdict.passed} failing={failing}")
    for name, detail in anchors.items():
        print(f"   {name}: {detail}")


def fresh_harness(tmp: Path, label: str):
    """One fresh scenario per verdict: a gate run injects faults,
    and the injected faults are recorded in the canary node's
    divergence log, so a second run_gate on the same harness would
    judge the first run's faults as an unclean run. That is the
    harness's existing single-run shape, not an anchor effect, and
    the demo honors it instead of hiding it."""
    harness = Gate5Harness(tmp / label)
    harness.build_scenario()
    outside = tmp / f"{label}-outside"
    reg_anchor_path = outside / "registry-anchor.json"
    registry_anchor.write_anchor(harness.snapshot_path, reg_anchor_path)
    mesh_anchor_path = outside / "mesh-anchor.json"
    mesh_anchor.write_mesh_anchor(harness.canary_node, mesh_anchor_path)
    return harness, reg_anchor_path, mesh_anchor_path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-gate5-anchor-demo-"))
    print("Setup. One fresh canary scenario per verdict; both chains "
          "anchored outside each scenario.")

    harness, _r, _m = fresh_harness(tmp, "plain")
    show("1. No anchors configured (experiment 006 verdict)",
         harness.run_gate(iterations=10))

    harness, reg_anchor_path, mesh_anchor_path = fresh_harness(tmp, "genuine")
    show("2. Genuine anchors configured",
         harness.run_gate(iterations=10,
                          registry_anchor_path=reg_anchor_path,
                          mesh_anchor_path=mesh_anchor_path))

    # Rewrite the registry chain, preserving alice's deterministic
    # entry and changing bob's entries, as in the test suite.
    harness, reg_anchor_path, mesh_anchor_path = fresh_harness(tmp, "rewritten")
    rewrite_dir = tmp / "rewrite-central"
    rewrite_log = rewrite_dir / "registry.jsonl"
    channel = RegistrationChannel(rewrite_log, rewrite_dir / "audit.jsonl")
    alice_key = channel.enroll_user("alice")
    channel.register(
        author="alice", name=CANARY_POLICY_NAME, version=CANARY_POLICY_VERSION,
        code=CANARY_CODE,
        signature=sign_library(alice_key, "alice", CANARY_POLICY_NAME,
                               CANARY_POLICY_VERSION, CANARY_CODE))
    bob_key = channel.enroll_user("bob")
    for name, code in (("rate-limit", b"policy: limit 999/min - rewritten\n"),
                       ("audit-trail", b"policy: log nothing - rewritten\n")):
        channel.register(author="bob", name=name, version="1.0.0", code=code,
                         signature=sign_library(bob_key, "bob", name, "1.0.0",
                                                code))
    harness.snapshot_path.write_bytes(rewrite_log.read_bytes())
    show("3. Registry rewritten (passes every internal check), anchors on",
         harness.run_gate(iterations=10,
                          registry_anchor_path=reg_anchor_path,
                          mesh_anchor_path=mesh_anchor_path))

    print(f"Demo state (temp): {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
