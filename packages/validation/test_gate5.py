"""
Automated tests for the Gate 5 verification gate prototype.

Each test maps to a Gate 5 outcome in
docs/architecture/08-outcomes-that-unlock-the-next-build.md and to
tests/test-cases/sync-mesh.md case 4 (the lineage benchmark, which
belongs to this gate). Run with either:

    python3 -m unittest discover -s packages/validation -v
    python3 packages/validation/test_gate5.py

Standard library only. Timing assertions never assert the
sub-millisecond target itself (that is machine-dependent and is a
benchmark to report, per docs/architecture/08); they assert the
numbers are real measurements: positive, present for every
iteration, and consistent with the raw samples they summarize.
"""

import statistics
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gate5_harness import Gate5Harness  # noqa: E402


class Gate5VerificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.harness = Gate5Harness(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # Outcome 1 (sync-mesh case 4): a canary run actually measures
    # end-to-end lineage time under load and reports real numbers.
    def test_canary_measures_real_lineage_times(self):
        report = self.harness.run_canary(iterations=30)

        # The load is real: a converged mesh of 4 nodes x 25 mutations.
        self.assertEqual(report.mesh_entries, 100)
        self.assertEqual(report.registry_entries, 3)
        self.assertEqual(report.iterations, 30)

        # Every iteration produced a timing sample, and the summary
        # statistics are exactly the statistics of the raw samples:
        # the numbers come from measurement, not from a constant.
        self.assertEqual(len(report.end_to_end_samples_ns), 30)
        self.assertEqual(report.end_to_end.count, 30)
        raw_ms = [s / 1e6 for s in report.end_to_end_samples_ns]
        self.assertAlmostEqual(report.end_to_end.median_ms, statistics.median(raw_ms))
        self.assertAlmostEqual(report.end_to_end.mean_ms, statistics.fmean(raw_ms))
        self.assertEqual(report.end_to_end.min_ms, min(raw_ms))
        self.assertEqual(report.end_to_end.max_ms, max(raw_ms))
        self.assertGreater(report.end_to_end.median_ms, 0)

        # Per-transformation: one sample per mesh entry, same proof.
        self.assertEqual(len(report.per_transformation_samples_ns), 100)
        self.assertEqual(report.per_transformation.count, 100)
        raw_entry_ms = [s / 1e6 for s in report.per_transformation_samples_ns]
        self.assertAlmostEqual(
            report.per_transformation.median_ms, statistics.median(raw_entry_ms)
        )

        # The target is present and the meets-target flags are the
        # honest comparison of the measured medians against it,
        # whichever way they land on this machine.
        self.assertEqual(report.target_ms, 1.0)
        self.assertEqual(
            report.per_transformation_meets_target,
            report.per_transformation.median_ms < report.target_ms,
        )
        self.assertEqual(
            report.end_to_end_meets_target,
            report.end_to_end.median_ms < report.target_ms,
        )

        # The canary path itself worked on every timed run.
        self.assertTrue(report.served_ok)

    # Outcome 2: a forced Byzantine / divergence fault triggers the
    # halt-and-rollback path automatically, and state ends exactly
    # at the pre-fault checkpoint.
    def test_forced_fault_halts_and_rolls_back_to_checkpoint(self):
        self.harness.build_scenario()
        self.harness.run_canary(iterations=5)
        checkpoint = self.harness.take_checkpoint()
        node = self.harness.canary_node

        # Byzantine-style fork: valid hash, false history.
        fork = self.harness.inject_fault("forked", checkpoint)
        self.assertTrue(fork.divergence_detected)
        self.assertEqual(fork.reason, "forked_entry")
        self.assertTrue(fork.rolled_back)
        self.assertTrue(fork.halt_triggered)
        self.assertEqual(self.harness.rollout_state, "halted_and_rolled_back")
        # Exact restoration, three ways.
        self.assertTrue(fork.restored_exactly)
        self.assertEqual(node.materialized_state(), checkpoint.state)
        self.assertEqual(node.state_hash(), checkpoint.state_hash)
        self.assertEqual(node.entry_ids(), checkpoint.entry_ids)

        # Second fault class, same checkpoint: a tampered entry.
        tampered = self.harness.inject_fault("tampered", checkpoint)
        self.assertTrue(tampered.divergence_detected)
        self.assertEqual(tampered.reason, "tampered_entry")
        self.assertTrue(tampered.rolled_back)
        self.assertTrue(tampered.restored_exactly)
        self.assertEqual(node.state_hash(), checkpoint.state_hash)
        self.assertEqual(len(node.divergence_log), 2)

    # Outcome 3: the gate as a whole produces one pass/fail verdict
    # from measured evidence, thresholds checked in code.
    def test_gate_verdict_from_measured_evidence(self):
        verdict = self.harness.run_gate(iterations=20)

        self.assertTrue(verdict.passed)
        self.assertTrue(all(c.ok for c in verdict.checks))
        names = {c.name for c in verdict.checks}
        self.assertIn("lineage_measured", names)
        self.assertIn("rollback_exact_after_forked", names)
        self.assertIn("halt_triggered", names)

        # The verdict carries the measured numbers and the honest
        # benchmark comparison, matching the canary it ran.
        self.assertIsNotNone(verdict.canary)
        self.assertEqual(verdict.canary["iterations"], 20)
        self.assertEqual(
            verdict.benchmark["per_transformation_median_ms"],
            verdict.canary["per_transformation"]["median_ms"],
        )
        self.assertEqual(verdict.benchmark["target_ms"], 1.0)
        self.assertEqual(len(verdict.faults), 2)
        self.assertTrue(all(f["restored_exactly"] for f in verdict.faults))

    # Outcome 3, negative half: the verdict is not hardcoded. When
    # the evidence is bad (the registry snapshot on disk no longer
    # verifies), the same gate code returns FAIL and names the
    # failing check.
    def test_gate_verdict_fails_on_bad_evidence(self):
        self.harness.build_scenario()
        # Corrupt the serving node's snapshot: flip a character in
        # the first entry's stored hash chain evidence.
        lines = self.harness.snapshot_path.read_text(encoding="utf-8").splitlines()
        assert '"entry_hash": "0' not in lines[0]  # real hash, not genesis
        lines[0] = lines[0].replace('"version": "1.0.0"', '"version": "9.9.9"')
        self.harness.snapshot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        verdict = self.harness.run_gate(iterations=5)

        self.assertFalse(verdict.passed)
        failing = [c.name for c in verdict.checks if not c.ok]
        self.assertIn("canary_path_completed", failing)


if __name__ == "__main__":
    unittest.main()
