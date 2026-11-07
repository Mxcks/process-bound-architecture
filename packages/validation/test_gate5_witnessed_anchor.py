"""
Automated tests for witnessed-anchor evidence in the Gate 5
verdict (experiment 029).

Each test maps to tests/test-cases/registration.md case 11
and tests/test-cases/sync-mesh.md case 18 (written before
this code). Run with either:

    python3 -m unittest discover -s packages/validation -v -p "test_gate5_witnessed_anchor.py"
    python3 packages/validation/test_gate5_witnessed_anchor.py

run_gate's anchor evidence (experiments 018/019) gains the
witness, opt-in exactly as the signer is: when a witness
identity is passed for a chain (witness + the verification
secret for that identity), that chain's anchor check also
requires the anchor's countersignature by that witness,
reusing experiment 029's consultation at the components the
harness already probes with (a PolicyNode for the registry,
an AnchoredSyncNode for the mesh). Expected: genuine chains
with correctly countersigned anchors keep the verdict
passing; an anchor signed by the signer alone makes the
verdict FAIL, and that chain's anchor check is the only
failing check, named anchor_witness_invalid - never
anchor_signature_invalid, never anchor_mismatch, never
anchor_stale. With no witness passed, the verdict's anchor
evidence is exactly experiment 019's. The signer and the
witness are real Gate 1 enrollments made on a channel this
test controls; their secrets are issued at runtime here.
Standard library only.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Only HERE is inserted here, deliberately, as in
# test_gate5_signed_anchor.py: gate5_harness sets the package
# order.

from gate5_harness import Gate5Harness  # noqa: E402
from registration import RegistrationChannel  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_module(
    "pba_gate5_witnessed_registry_anchor",
    HERE.parent / "registration" / "anchor.py")
_mesh_anchor = _load_module(
    "pba_gate5_witnessed_mesh_anchor",
    HERE.parent / "synchronization" / "anchor.py")


class Gate5WitnessedAnchorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.harness = Gate5Harness(self.tmp / "scenario")
        self.harness.build_scenario()

        # The signer and the witness are separate enrolled
        # identities on a channel this test controls, as in
        # test_gate5_signed_anchor.py: the enrollment is real,
        # the secrets issued at runtime here, and nothing
        # about the harness's chains depends on WHO signs or
        # countersigns their anchors.
        id_dir = self.tmp / "identity"
        channel = RegistrationChannel(
            id_dir / "registry.jsonl", id_dir / "audit.jsonl"
        )
        self.signer_secret = channel.enroll_user("anchor-keeper")
        self.witness_secret = channel.enroll_user("anchor-witness")

        self.registry_anchor_path = self.tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.harness.snapshot_path, self.registry_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
            witness="anchor-witness",
            witness_secret_hex=self.witness_secret,
        )
        self.mesh_anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        _mesh_anchor.write_mesh_anchor(
            self.harness.canary_node, self.mesh_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
            witness="anchor-witness",
            witness_secret_hex=self.witness_secret,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _anchor_checks(self, verdict):
        return {c.name: c for c in verdict.checks
                if c.name.startswith("anchor_evidence")}

    def _run(self, **overrides):
        kwargs = dict(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
            registry_anchor_signer="anchor-keeper",
            registry_anchor_secret_hex=self.signer_secret,
            registry_anchor_witness="anchor-witness",
            registry_anchor_witness_secret_hex=self.witness_secret,
            mesh_anchor_signer="anchor-keeper",
            mesh_anchor_secret_hex=self.signer_secret,
            mesh_anchor_witness="anchor-witness",
            mesh_anchor_witness_secret_hex=self.witness_secret,
        )
        kwargs.update(overrides)
        return self.harness.run_gate(**kwargs)

    def _rewrite_registry_anchor_signer_alone(self):
        # A case 10 anchor over the same head: alice's
        # successor write by the same signer, no witness.
        # (write_anchor appends to the kept history; the
        # anchor it produces is genuinely signed and stands
        # on the same chain - only the countersignature is
        # absent, which is exactly the fact under test.)
        _registry_anchor.write_anchor(
            self.harness.snapshot_path, self.registry_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
        )

    def _rewrite_mesh_anchor_signer_alone(self):
        _mesh_anchor.write_mesh_anchor(
            self.harness.canary_node, self.mesh_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
        )

    # Case 11/18, verdict: genuine chains with correctly
    # countersigned anchors and their witnesses configured
    # keep the verdict passing, the two anchor checks
    # additional and passing as anchored.
    def test_countersigned_anchors_keep_verdict_passing(self):
        verdict = self._run()
        self.assertTrue(verdict.passed)
        self.assertTrue(all(c.ok for c in verdict.checks))
        checks = self._anchor_checks(verdict)
        self.assertEqual(
            set(checks), {"anchor_evidence_registry", "anchor_evidence_mesh"})
        for check in checks.values():
            self.assertIn("anchored", check.detail)
            self.assertNotIn("anchor_witness_invalid", check.detail)
        print("GATE 5 witnessed evidence: countersigned "
              "anchors on both chains, verdict passing")

    # Case 11, verdict, registry: a signer-alone registry
    # anchor fails the verdict, the registry anchor check is
    # the ONLY failing check, named anchor_witness_invalid.
    def test_signer_alone_registry_anchor_fails_verdict_on_witness_only(self):
        self._rewrite_registry_anchor_signer_alone()
        verdict = self._run()
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_registry"])
        check = self._anchor_checks(verdict)["anchor_evidence_registry"]
        self.assertIn("anchor_witness_invalid", check.detail)
        self.assertNotIn("anchor_signature_invalid", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)
        self.assertNotIn("anchor_stale", check.detail)
        self.assertTrue(
            self._anchor_checks(verdict)["anchor_evidence_mesh"].ok)
        print("GATE 5 witnessed evidence: a signer-alone "
              "registry anchor fails the verdict on the "
              "registry anchor check alone, named "
              "anchor_witness_invalid")

    # Case 18, verdict, mesh: a signer-alone mesh anchor
    # fails the verdict under the same name, for the mesh
    # check only.
    def test_signer_alone_mesh_anchor_fails_verdict_on_witness_only(self):
        self._rewrite_mesh_anchor_signer_alone()
        verdict = self._run()
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_mesh"])
        check = self._anchor_checks(verdict)["anchor_evidence_mesh"]
        self.assertIn("anchor_witness_invalid", check.detail)
        self.assertNotIn("anchor_signature_invalid", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)
        self.assertNotIn("anchor_stale", check.detail)
        self.assertTrue(
            self._anchor_checks(verdict)["anchor_evidence_registry"].ok)

    # Case 11/18, verdict backward compatibility: signer-alone
    # anchors with NO witness passed are judged exactly as
    # experiment 019 judged them - by signer and head - and
    # the verdict passes.
    def test_signer_alone_anchors_without_witness_behave_as_019(self):
        self._rewrite_registry_anchor_signer_alone()
        self._rewrite_mesh_anchor_signer_alone()
        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
            registry_anchor_signer="anchor-keeper",
            registry_anchor_secret_hex=self.signer_secret,
            mesh_anchor_signer="anchor-keeper",
            mesh_anchor_secret_hex=self.signer_secret,
        )
        self.assertTrue(verdict.passed)
        checks = self._anchor_checks(verdict)
        self.assertEqual(
            set(checks), {"anchor_evidence_registry", "anchor_evidence_mesh"})
        for check in checks.values():
            self.assertTrue(check.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
