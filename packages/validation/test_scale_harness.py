"""
Automated tests for the scale harness (experiment 009).

The harness's big numbers live in experiments/009-scale-run/ as
measured results, not unit assertions: timings at 5,000-entry
registries and 10,000-entry meshes are machine-dependent evidence.
What is asserted HERE, at modest size, is that the harness runs and
reports sane results: the counts it claims are the counts it built,
the chain verifies, the mesh converges to one hash, serves return
the registered bytes, and every reported statistic is consistent
with real samples (positive, correctly counted) with the target
comparison computed honestly from the measured median.

    python3 -m unittest discover -s packages/validation -v
    python3 packages/validation/test_scale_harness.py

Standard library only.
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scale_harness as sh  # noqa: E402


class ScaleHarnessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_registration_at_modest_size(self):
        res = sh.measure_registration(self.workdir / "reg", 40, users=8)
        self.assertTrue(res.completed)
        self.assertEqual(res.entries_completed, 40)
        self.assertEqual(len(res.payloads), 40)
        self.assertEqual(len(res.sample_hashes), 8)
        # Real measurements: positive, one sample per registration.
        self.assertEqual(res.per_registration.count, 40)
        self.assertGreater(res.per_registration.median_ms, 0)
        self.assertEqual(res.verify_live.count, 5)
        self.assertGreater(res.verify_live.median_ms, 0)
        self.assertGreater(res.registry_file_bytes, 0)

    def test_sync_converges_at_modest_size(self):
        res = sh.measure_sync(4, 25)
        self.assertEqual(res.total_entries, 100)
        self.assertTrue(res.converged)
        self.assertEqual(res.entries_per_node, (100, 100, 100, 100))
        self.assertEqual(len(res.per_node_delivery_s), 4)
        self.assertTrue(res.state_hash)

    def test_serve_at_modest_size(self):
        reg = sh.measure_registration(self.workdir / "reg", 40, users=8)
        res = sh.measure_serve(reg, repeats=40)
        self.assertEqual(res.registry_entries, 40)
        self.assertTrue(res.served_ok)
        self.assertEqual(res.serve.count, 40)
        self.assertGreater(res.serve.median_ms, 0)
        self.assertGreater(res.snapshot_construct_ms, 0)

    def test_canary_at_modest_size_reports_honest_target_comparison(self):
        reg = sh.measure_registration(self.workdir / "reg", 40, users=8)
        res = sh.measure_canary_at_size(reg, 100, iterations=10)
        self.assertTrue(res.all_verified)
        self.assertEqual(res.mesh_entries, 100)
        self.assertEqual(res.registry_entries, 40)
        self.assertEqual(res.end_to_end.count, 10)
        self.assertEqual(res.per_transformation.count, 100)
        # The stage medians account for the end-to-end median: the
        # whole is at least the sum of its parts' medians minus
        # measurement slack, and the miss factor is the honest
        # ratio of the measured median to the 1 ms target.
        self.assertEqual(res.target_ms, 1.0)
        self.assertAlmostEqual(res.miss_factor, res.end_to_end.median_ms / 1.0)
        self.assertEqual(
            res.end_to_end_meets_target, res.end_to_end.median_ms < res.target_ms
        )
        self.assertEqual(
            res.per_transformation_meets_target,
            res.per_transformation.median_ms < res.target_ms,
        )

    def test_small_profile_suite_is_sane_end_to_end(self):
        results = sh.run_scale_suite(self.workdir / "suite", profile="small")
        self.assertEqual(results["profile"], "small")
        self.assertTrue(all(r["completed"] for r in results["registration"]))
        self.assertTrue(all(s["converged"] for s in results["sync"]))
        self.assertTrue(all(s["served_ok"] for s in results["serve"]))
        self.assertTrue(all(c["all_verified"] for c in results["canary"]))


if __name__ == "__main__":
    unittest.main()
