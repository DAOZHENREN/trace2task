"""Offline source-clock invariants; no screen capture or input injection."""
import ctypes
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "opencua"
sys.path.insert(0, str(SCRIPTS))
try:
    import validate_dxgi_clock as probe
finally:
    sys.path.remove(str(SCRIPTS))


def frame(index, marker, present, copied=None):
    return {"index": index, "marker": marker, "frequency": 1000, "last_present": present,
                "before_acquire": present, "after_acquire": present+2,
                "after_copy": copied if copied is not None else present+3,
                "protected_content": 0, "accumulated_frames": 1}


class SourceClockTests(unittest.TestCase):
    def test_abi(self):
        self.assertEqual(ctypes.sizeof(probe.FrameInfo), 56)

    def test_before_selection_and_positive_residual(self):
        rows = [frame(0, 0, 100), frame(1, 1, 210)]
        report = probe.analyze(rows, [{"marker": 1, "before_update_ns": 200_000_000}])
        self.assertEqual(report["poststate_in_prestate"], 0)
        self.assertEqual(report["results"][0]["source_prestate_frame"], 0)
        self.assertEqual(report["residual"]["median_ms"], 10)

    def test_early_mislabel_detected(self):
        rows = [frame(0, 0, 100), frame(1, 1, 190, copied=220)]
        report = probe.analyze(rows, [{"marker": 1, "before_update_ns": 200_000_000}])
        self.assertEqual(report["poststate_in_prestate"], 1)
        self.assertEqual(report["buffered_poststate"], 0)

    def test_equal_time_is_not_before(self):
        rows = [frame(0, 0, 100), frame(1, 1, 200)]
        report = probe.analyze(rows, [{"marker": 1, "before_update_ns": 200_000_000}])
        self.assertEqual(report["results"][0]["source_prestate_frame"], 0)

    def test_missing_evidence_is_not_success(self):
        report = probe.analyze([frame(0, None, 100)], [{"marker": 1, "before_update_ns": 200_000_000}])
        self.assertEqual(report["matched_markers"], 0)
        self.assertEqual(report["unverifiable_prestate"], 1)

    def test_invalid_metadata_rejected(self):
        cases = [{"last_present": 0}, {"last_present": 500}, {"protected_content": 1},
                 {"frequency": 0}, {"index": 4}, {"before_acquire": 400}]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                probe.analyze([{**frame(0, 0, 100), **updates}], [{"marker": 1, "before_update_ns": 200_000_000}])
        with self.assertRaises(ValueError):
            probe.analyze([frame(0, 0, 100), frame(1, 1, 100)], [{"marker": 1, "before_update_ns": 200_000_000}])


if __name__ == "__main__":
    unittest.main()
