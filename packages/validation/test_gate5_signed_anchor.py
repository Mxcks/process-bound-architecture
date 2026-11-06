"""
Automated tests for signed-anchor evidence in the Gate 5
verdict (experiment 019).

Each test maps to tests/test-cases/registration.md case 10 and
tests/test-cases/sync-mesh.md case 11 (written before this
code). Run with either:

    python3 -m unittest discover -s packages/validation -v -p "test_gate5_signed_anchor.py"
    python3 packages/validation/test_gate5_signed_anchor.py

run_gate's experiment 018 anchor evidence gains the signing
identity, opt-in exactly as the anchor paths are: when a
signing identity is passed for a chain (signer + the
verification secret for that identity), the anchor check for
that chain also verifies the anchor's signature, reusing
experiment 019's consultation at the components the harness
already probes with (a PolicyNode for the registry, an
AnchoredSyncNode for the mesh). Expected: genuine chains with
correctly signed anchors keep the verdict passing, the anchor
checks additional and passing; a signed anchor whose
signature is tampered with after signing makes the verdict
FAIL, and that chain's anchor check is the only failing
check, named anchor_signature_invalid - never anchor_mismatch
and never anchor_stale. With no signing identity passed, the
verdict's anchor evidence is exactly experiment 018's.
Signing identities are real Gate 1 enrollments made by the
harness scenario (alice and bob are enrolled there); the
secrets reach this test because it builds its scenario
through a channel it controls - see setUp. Standard library
only.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Only HERE is inserted here, deliberately, as in
# test_gate5_anchor.py: gate5_harness sets the package order.

from gate5_harness import Gate5Harness  # noqa: E402
from registration import RegistrationChannel  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_module(
    "pba_gate5_signed_registry_anchor", HERE.parent / "registration" / "anchor.py")
_mesh_anchor = _load_module(
    "pba_gate5_signed_mesh_anchor", HERE.parent / "synchronization" / "anchor.py")


class Gate5SignedAnchorEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.harness = Gate5Harness(self.tmp / "scenario")
        self.harness.build_scenario()

        # The harness scenario enrolls alice and bob inside its
        # own channel, whose secrets are not returned. For this
        # suite the anchor signer is a separate enrolled identity
        # the test controls, enrolled on its own channel - the
        # enrollment is real, the secret issued at runtime here,
        # and nothing about the harness's chains depends on WHO
        # signs their anchors (case 10: any enrolled identity may
        # establish an anchor's signer).
        id_dir = self.tmp / "identity"
        channel = RegistrationChannel(
            id_dir / "registry.jsonl", id_dir / "audit.jsonl"
        )
        self.signer_secret = channel.enroll_user("anchor-keeper")

        self.registry_anchor_path = self.tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.harness.snapshot_path, self.registry_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
        )
        self.mesh_anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        _mesh_anchor.write_mesh_anchor(
            self.harness.canary_node, self.mesh_anchor_path,
            signer="anchor-keeper", secret_hex=self.signer_secret,
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
            mesh_anchor_signer="anchor-keeper",
            mesh_anchor_secret_hex=self.signer_secret,
        )
        kwargs.update(overrides)
        return self.harness.run_gate(**kwargs)

    def _tamper_signature(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        sig = data["signature"]
        data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    # Case 10/11, verdict: genuine chains with correctly signed
    # anchors and their signing identities keep the verdict
    # passing exactly as in experiment 018, the two anchor
    # checks additional, passing, and classified anchored.
    def test_genuine_signed_anchors_keep_verdict_passing(self):
        verdict = self._run()
        self.assertTrue(verdict.passed)
        self.assertTrue(all(c.ok for c in verdict.checks))
        checks = self._anchor_checks(verdict)
        self.assertEqual(
            set(checks), {"anchor_evidence_registry", "anchor_evidence_mesh"})
        for check in checks.values():
            self.assertIn("anchored", check.detail)
            self.assertNotIn("anchor_signature_invalid", check.detail)

    # Case 10, verdict, registry: a tampered anchor signature
    # fails the verdict, the registry anchor check is the only
    # failing check, and it is named anchor_signature_invalid.
    def test_tampered_registry_signature_fails_verdict_on_signature_only(self):
        self._tamper_signature(self.registry_anchor_path)
        verdict = self._run()
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_registry"])
        check = self._anchor_checks(verdict)["anchor_evidence_registry"]
        self.assertIn("anchor_signature_invalid", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)
        self.assertNotIn("anchor_stale", check.detail)
        self.assertTrue(
            self._anchor_checks(verdict)["anchor_evidence_mesh"].ok)

    # Case 11, verdict, mesh: a tampered mesh anchor signature
    # fails the verdict under the same name, for the mesh check
    # only.
    def test_tampered_mesh_signature_fails_verdict_on_signature_only(self):
        self._tamper_signature(self.mesh_anchor_path)
        verdict = self._run()
        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertEqual(failing, ["anchor_evidence_mesh"])
        check = self._anchor_checks(verdict)["anchor_evidence_mesh"]
        self.assertIn("anchor_signature_invalid", check.detail)
        self.assertNotIn("anchor_mismatch", check.detail)
        self.assertNotIn("anchor_stale", check.detail)
        self.assertTrue(
            self._anchor_checks(verdict)["anchor_evidence_registry"].ok)

    # Case 10/11, verdict backward compatibility: the same
    # signed anchors with NO signing identity passed are judged
    # exactly as experiment 018 judged anchors - by head and
    # state alone - and the verdict passes.
    def test_signed_anchors_without_identity_behave_as_018(self):
        verdict = self.harness.run_gate(
            iterations=10,
            registry_anchor_path=self.registry_anchor_path,
            mesh_anchor_path=self.mesh_anchor_path,
        )
        self.assertTrue(verdict.passed)
        checks = self._anchor_checks(verdict)
        self.assertEqual(
            set(checks), {"anchor_evidence_registry", "anchor_evidence_mesh"})
        for check in checks.values():
            self.assertTrue(check.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
