"""Offline checks for the opt-in OBS probe; never starts OBS or injects input."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "opencua"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("sync_probe", SCRIPTS / "validate_sync.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
finally:
    sys.path.remove(str(SCRIPTS))


def frame(marker):
    result = np.zeros((56, 14 * 28, 3), dtype=np.uint8)
    for index, bit in enumerate([1, 0] + [(marker >> i) & 1 for i in range(12)]):
        result[:, index * 28:(index + 1) * 28] = 238 if bit else 17
    return result


class FakeCapture:
    def __init__(self, _):
        self.index = -1

    def isOpened(self):
        return True

    def read(self):
        self.index += 1
        return (True, frame([0, 0, 1][self.index])) if self.index < 3 else (False, None)

    def get(self, _):
        return [0, 100, 200][self.index]

    def set(self, _, index):
        self.index = int(index) - 1

    def release(self):
        pass


class SyncProbeTests(unittest.TestCase):
    def test_decode(self):
        for value in [0, 1, 24, 4095]:
            self.assertEqual(probe.decode_marker(frame(value), [0, 0]), value)
        self.assertIsNone(probe.decode_marker(np.full_like(frame(0), 120), [0, 0]))

    def test_prestate_and_residual_sign(self):
        cv2 = SimpleNamespace(VideoCapture=FakeCapture, CAP_PROP_POS_MSEC=0,
                              CAP_PROP_POS_FRAMES=1, imwrite=lambda *_: True)
        with tempfile.TemporaryDirectory() as output, patch.dict(sys.modules, {"cv2": cv2}):
            for event_time, expected, residual in [(10.15, False, 50), (10.25, True, -50), (10.2, False, 0)]:
                report = probe.analyze("unused", [{"marker": 1, "before_update": event_time,
                    "after_update": event_time}], 10, [0, 0], Path(output))
                self.assertEqual(report["results"][0]["poststate_in_prestate"], expected)
                self.assertAlmostEqual(report["results"][0]["residual_ms"], residual)


if __name__ == "__main__":
    unittest.main()
