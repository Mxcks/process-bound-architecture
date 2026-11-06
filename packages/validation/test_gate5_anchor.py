"""
Automated tests for anchor evidence in the Gate 5 verdict
(experiment 018).

Each test maps to tests/test-cases/registration.md case 9 and
tests/test-cases/sync-mesh.md case 10 (written before this code).
Run with either:

    python3 -m unittest discover -s packages/validation -v -p "test_gate5_anchor.py"
    python3 packages/validation/test_gate5_anchor.py

run_gate gains opt-in anchor evidence: a registry anchor path
(experiment 012's anchor for the harness's registry snapshot
chain) and a mesh anchor path (experiment 012's mesh anchor for
the canary node's converged state). Each configured anchor is
consulted with experiment 012's check functions and classified
with experiment 015's classification (anchored / anchor_mismatch /
anchor_stale), and enters the verdict as its own named check
(anchor_evidence_registry / anchor_evidence_mesh). With no anchor
configured there is no anchor check and the verdict is exactly
experiment 006's, as the unmodified test_gate5.py suite asserts in
the same run.

The rewrite used here passes every internal check the gate
already runs, stated in the test rather than assumed: the
rewritten snapshot verifies on its own (Gate 2's standalone
verifier), the canary policy's entry is present in it and still
serves through the harness's serving node, and the mesh lineage
verifies - so the anchor check is the ONLY check that can fail,
and the tests assert it is the only one that does. Honest growth
is asserted as anchor_stale for both chains: failed, under its
own name, never a pass as anchored and never a mismatch.
Standard library only.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Only HERE is inserted here, deliberately: gate5_harness sets up
# the registration / policy-library / synchronization paths itself
# (synchronization frontmost, which is what anchored_sync's own
# `import anchor` needs), and pre-inserting them in another order
# would resolve that import to the registration anchor instead.

from gate5_harness import Gate5Harness, verify_mesh_lineage  # noqa: E402
from policy_node import verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402


def _load_module(name: str, path: Path):
    """Load an anchor module by file path under a private name:
    two packages ship an anchor.py (registration, synchronization),
    so a plain import could resolve to the wrong one, the same
    reason policy_node.py loads the registry anchor this way."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_module(
    "pba_gate5_test_registry_anchor", HERE.parent / "registration" / "anchor.py")
_mesh_anchor = _load_module(
    "pba_gate5_test_mesh_anchor", HERE.parent / "synchronization" / "anchor.py")

BOB_REWRITTEN = (
    ("rate-limit", b"policy: limit 999/min - rewritten\n"),
    ("audit-trail", b"policy: log nothing - rewritten\n"),
)


class Gate5AnchorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.harness = Gate5Harness(self._tmp.name)
        self.harness.build_scenario()
        self.tmp = Path(self._tmp.name)
        self.registry_anchor_path = self.tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.harness.snapshot_path, self.registry_anchor_path)
        self.mesh_anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        _mesh_anchor.write_mesh_anchor(
            self.harness.canary_node, self.mesh_anchor_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _anchor_checks(self, verdict):
        return {c.name: c for c in verdict.checks
                if c.name.startswith("anchor_evidence")}

    def _rewrite_snapshot_passing_internal_checks(self):
        """A registry chain rewritten from genesis: alice's entry
        is deterministic and identical, bob's two entries carry
        different code, so the head differs while the chain
        verifies on its own, alice's policy is present, and the
        harness's serving node still serves it."""
        rewrite_dir = self.tmp / "rewrite-central"
        rewrite_log = rewrite_dir / "registry.jsonl"
        channel = RegistrationChannel(rewrite_log, rewrite_dir / "audit.jsonl")
        alice_key = channel.enroll_user("alice")
        from gate5_harness import CANARY_CODE, CANARY_POLICY_NAME, CANARY_POLICY_VERSION
        channel.register(
            author="alice", name=CANARY_POLICY_NAME,
            version=CANARY_POLICY_VERSION, code=CANARY_CODE,
            signature=sign_library(alice_key, "alice", CANARY_POLICY_NAME,
                                   CANARY_POLICY_VERSION, CANARY_CODE),
        )
        bob_key = channel.enroll_user("bob")
        for name, code in BOB_REWRITTEN:
            channel.register(
                author="bob", name=name, version="1.0.0", code=code,
                signature=sign_library(bob_key, "bob", name, "1.0.0", code),
            )
        rewritten = rewrite_log.read_bytes()
        self.harness.snapshot_path.write_bytes(rewritten)
        # Stated, not assumed: every internal check the gate runs
        # still passes on the rewritten chain.
        entries = verify_registry_snapshot(self.harness.snapshot_path)
        assert any(e["policy_hash"] == self.harness.record.policy_hash
                   for e in entries)
        assert self.harness.serving_node.serve_policy(
            self.harness.record.policy_hash).code == CANARY_CODE
        assert verify_mesh_lineage(self.harness.canary_node)

    # Case 9, verdict without anchors: no anchor check appears and
    # the verdict is exactly experiment 006's (also covered by the
    # unmodified test_gate5.py in the same run).
    def test_no_anchor_verdict_has_no_anchor_checks(self):
        verdict = self.harness.run_gate(iterations=10)
        self.assertTrue(verdict.passed)
        self.assertEqual(self._anchor_checks(verdict), {})

    # Case 9/10, genuine chains with matching anchors: the verdict
    # keeps passing exactly as experiments 006/009 produced it -
    # every earlier check present and passing - with the two
    # anchor checks additional and passing, classified anchored.
    def test_genuine_anchors_keep_verdict_passing(self):
        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
        )
        self.assertTrue(verdict.passed)
        self.assertTrue(all(c.ok for c in verdict.checks))
        names = {c.name for c in verdict.checks}
        for earlier in ("scenario_built", "clean_run_no_divergence",
                        "canary_path_completed", "lineage_measured",
                        "rollback_exact_after_forked",
                        "rollback_exact_after_tampered", "halt_triggered"):
            self.assertIn(earlier, names)
        checks = self._anchor_checks(verdict)
        self.assertEqual(
            set(checks), {"anchor_evidence_registry", "anchor_evidence_mesh"})
        for check in checks.values():
            self.assertTrue(check.ok)
            self.assertIn("anchored", check.detail)
            self.assertNotIn("anchor_mismatch", check.detail)
            self.assertNotIn("anchor_stale", check.detail)

    # Case 9, negative control: a registry chain rewritten so it
    # passes every internal check makes the verdict FAIL, and the
    # anchor check is the only failing check, named anchor_mismatch.
    def test_rewritten_registry_fails_verdict_on_anchor_only(self):
        self._rewrite_snapshot_passing_internal_checks()
        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
        )
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_registry"])
        check = self._anchor_checks(verdict)["anchor_evidence_registry"]
        self.assertIn("anchor_mismatch", check.detail)
        # The mesh anchor check still passes: only the rewritten
        # chain's evidence is bad.
        self.assertTrue(
            self._anchor_checks(verdict)["anchor_evidence_mesh"].ok)

    # Case 9, honest growth, registry: one honest registration
    # appended to the snapshot chain after anchoring. The verdict
    # FAILS under the name anchor_stale for the registry check -
    # not a mismatch, never a silent pass as anchored.
    def test_honest_registry_growth_fails_verdict_as_stale(self):
        channel = RegistrationChannel(
            self.harness.snapshot_path, self.tmp / "grow-audit.jsonl")
        secret = channel.enroll_user("charlie")
        code = b"policy: charlie's honest addition\n"
        channel.register(
            author="charlie", name="charlie-addition", version="1.0.0",
            code=code,
            signature=sign_library(secret, "charlie", "charlie-addition",
                                   "1.0.0", code),
        )
        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
        )
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_registry"])
        check = self._anchor_checks(verdict)["anchor_evidence_registry"]
        self.assertIn("anchor_stale", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)

    # Case 10, honest growth, mesh: every node converges on one
    # honestly appended entry past the mesh anchor. The verdict
    # FAILS under the name anchor_stale for the mesh check only.
    def test_honest_mesh_growth_fails_verdict_as_stale(self):
        self.harness.canary_node.mutate("grown-key", "grown-value")
        everything = []
        for node in self.harness.mesh:
            everything.extend(node.entries())
        for node in self.harness.mesh:
            result = node.receive_entries(everything)
            assert not result.divergence_detected
        assert len({n.state_hash() for n in self.harness.mesh}) == 1

        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
        )
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_mesh"])
        check = self._anchor_checks(verdict)["anchor_evidence_mesh"]
        self.assertIn("anchor_stale", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
