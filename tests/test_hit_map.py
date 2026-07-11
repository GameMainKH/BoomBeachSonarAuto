import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from utils.hit_map import save_hit_map_image


class HitMapTest(unittest.TestCase):
    def test_save_hit_map_image_writes_red_hit_overlay(self):
        base = np.zeros((120, 120, 3), dtype=np.uint8)
        quad = np.array(
            [[60, 10], [110, 60], [60, 110], [10, 60]],
            dtype=np.float32,
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "hit_map.png"
            save_hit_map_image(base, quad, [[1, 0], [0, 0]], output)
            rendered = cv2.imread(str(output))

        self.assertIsNotNone(rendered)
        self.assertGreater(int(np.count_nonzero(rendered[:, :, 2])), 0)

    def test_save_hit_map_image_rejects_non_square_map(self):
        base = np.zeros((20, 20, 3), dtype=np.uint8)
        quad = np.zeros((4, 2), dtype=np.float32)

        with self.assertRaisesRegex(ValueError, "N x N"):
            save_hit_map_image(base, quad, [[1, 0], [0]], "unused.png")


if __name__ == "__main__":
    unittest.main()
