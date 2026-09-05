import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np

from save_points.points import IMAGES_DIR, read_saved_points
from utils.board_view import (
    BoardAlignmentError, BoardFrame, BoardView, BoardViewTracker,
    estimate_board_translation,
)
from utils.diamond_centers import read_image


class BoardViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = read_image(IMAGES_DIR / "8.png")
        cls.points = read_saved_points(8, expected_n=10)

    def shifted(self, dx, dy):
        return cv2.warpAffine(
            self.reference, np.float32([[1, 0, dx], [0, 1, dy]]), (1280, 720),
        )

    def test_consensus_recovers_positive_negative_and_boundary_offsets(self):
        for dx, dy in ((0, 0), (2, -10), (-8, 9), (16, -16)):
            with self.subTest(offset=(dx, dy)):
                actual = estimate_board_translation(self.reference, self.shifted(dx, dy), self.points)
                self.assertEqual(actual[:2], (dx, dy))
                self.assertGreaterEqual(len(actual[2]), 5)

    def test_blank_wrong_resolution_and_out_of_bounds_offsets_are_rejected(self):
        for image in (np.zeros_like(self.reference), self.reference[:360], self.shifted(17, 0), self.shifted(25, 25)):
            with self.assertRaises(BoardAlignmentError):
                estimate_board_translation(self.reference, image, self.points)

    def test_frame_snapshot_survives_later_entry_and_moves_only_board_analysis(self):
        tracker = BoardViewTracker()
        tracker.current = BoardView(16, 1, 2, -10)
        frame = tracker.frame(self.shifted(2, -10), 16)
        tracker.current = BoardView(16, 2, -3, 4)
        self.assertEqual(frame.view.point((856, 282)), (858, 272))
        self.assertEqual(tracker.snapshot(16).point((856, 282)), (853, 286))
        self.assertTrue(np.array_equal(frame.reference_image()[30:-30, 30:-30], self.reference[30:-30, 30:-30]))
        self.assertFalse(np.array_equal(frame.raw, frame.reference_image()))
        with self.assertRaises(FrozenInstanceError):
            frame.view.dx = 100
        tracker.invalidate()
        with self.assertRaises(BoardAlignmentError):
            tracker.snapshot(16)
        self.assertEqual(frame.view.entry_id, 1)

    def test_two_frame_establishment_does_not_accumulate_offsets_or_reuse_other_level(self):
        tracker = BoardViewTracker()
        adb = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            for level, offset in ((8, (2, -10)), (16, (-3, 4))):
                adb.read_screenshot.side_effect = [self.shifted(*offset)] * 2
                view = tracker.establish(adb, level, directory)
                self.assertEqual((view.dx, view.dy), offset)
                self.assertEqual(view.level, level)
            with self.assertRaises(BoardAlignmentError):
                tracker.snapshot(8)
            metadata = Path(directory) / "level_16/entry_0002/alignment.json"
            self.assertTrue(metadata.is_file())
        adb.swipe.assert_not_called()
        adb.click.assert_not_called()

    def test_unstable_failed_or_interrupted_entry_invalidates_previous_view(self):
        for frames in ([self.reference, self.shifted(0, 8)], [np.zeros_like(self.reference)], [RuntimeError("capture failed")]):
            tracker = BoardViewTracker()
            tracker.current = BoardView(16, 1, 2, -10)
            adb = MagicMock()
            adb.read_screenshot.side_effect = frames
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(RuntimeError):
                    tracker.establish(adb, 16, directory)
                self.assertTrue((Path(directory) / "level_16/entry_0001/failure.json").is_file())
            self.assertIsNone(tracker.current)
            adb.click.assert_not_called()
            adb.swipe.assert_not_called()

    def test_early_levels_need_no_template_or_capture(self):
        tracker = BoardViewTracker()
        adb = MagicMock()
        view = tracker.establish(adb, 7, "unused")
        self.assertEqual(view.point((10, 20)), (10, 20))
        self.assertFalse(adb.mock_calls)


if __name__ == "__main__":
    unittest.main()
