import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from save_points import points


ROOT = Path(__file__).resolve().parents[1]


class SharedPointsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "points.json"
        self.shared = points.read_level_entry(10)

    def test_legacy_records_resolve_to_tenth_level_without_rewriting_on_read(self):
        stale = deepcopy(self.shared)
        stale["points"][0] = [999, 999]
        data = {"version": 1, "levels": {"9": stale, "10": self.shared, "16": stale, "50": stale}}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        original = self.path.read_bytes()
        for level in (10, 11, 16, 50, 51):
            self.assertEqual(points.read_level_entry(level, self.path), self.shared)
        self.assertEqual(points.read_level_entry(9, self.path), stale)
        self.assertEqual(self.path.read_bytes(), original)
        normalized = points.load_points_data(self.path)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(set(normalized["levels"]), {"9", "10"})

    def test_saving_any_later_level_updates_one_shared_record_and_quad(self):
        changed = deepcopy(self.shared)
        changed["points"][0] = [700, 80]
        changed["quad"][0] = [700, 50]
        changed["image"] = "save_points/imgs/27.png"
        points.save_level_entry(27, changed, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(raw["levels"]), {"10"})
        self.assertEqual(raw["levels"]["10"]["image"], "save_points/imgs/10.png")
        for level in (10, 16, 27, 50):
            self.assertEqual(points.read_saved_points(level, self.path, expected_n=10)[0], (700, 80))
            self.assertEqual(points.read_saved_quad(level, self.path)[0].tolist(), [700, 50])
        self.assertEqual(changed["image"], "save_points/imgs/27.png")

    def test_missing_or_wrong_size_shared_calibration_is_not_replaced_by_stale_level(self):
        self.path.write_text(json.dumps({"levels": {"16": self.shared}}), encoding="utf-8")
        self.assertIsNone(points.read_saved_points(16, self.path, expected_n=10))
        points.save_level_entry(10, self.shared, self.path)
        self.assertIsNone(points.read_saved_points(16, self.path, expected_n=9))

    def test_generator_ignores_old_duplicate_template_images(self):
        images = Path(self.directory.name) / "imgs"
        images.mkdir()
        for level in (9, 10, 11, 16, 50):
            (images / f"{level}.png").touch()
        with patch.object(points, "detect_level_entry", return_value=self.shared) as detect:
            data = points.generate_points_json(images, self.path)
        self.assertEqual(set(data["levels"]), {"9", "10"})
        self.assertEqual([call.args[0].name for call in detect.call_args_list], ["9.png", "10.png"])

    def test_repository_contains_only_ten_templates_and_all_later_levels_share_them(self):
        raw = json.loads((ROOT / "save_points" / "points.json").read_text(encoding="utf-8"))
        self.assertEqual(set(raw["levels"]), {str(i) for i in range(1, 11)})
        images = list((ROOT / "save_points" / "imgs").glob("*.png"))
        self.assertEqual({p.stem for p in images}, {str(i) for i in range(1, 11)})
        for level in range(10, 51):
            entry = points.read_level_entry(level)
            self.assertEqual(entry, self.shared)
            self.assertTrue((ROOT / entry["image"]).is_file())


class SharedPointEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_editor_exposes_one_shared_item_and_saves_it_for_every_later_level(self):
        from _debug import point_editor as editor

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "points.json"
            points.save_points_data(points.load_points_data(), target)
            with (
                patch.object(editor, "SAVED_POINTS_FILE", target),
                patch.object(editor, "load_points_data", side_effect=lambda: points.load_points_data(target)),
            ):
                window = editor.PointEditorWindow()
                try:
                    self.assertEqual(window.level_list.count(), 10)
                    window.level_list.setCurrentRow(9)
                    self.assertIn("及以后（共用）", window.level_list.currentItem().text())
                    edited = window.canvas.current_points()
                    edited[0] = (700, 80)
                    window.canvas.replace_points(edited)
                    window.save_current_level()
                    self.assertEqual(points.read_saved_points(50, target)[0], (700, 80))
                    replacement = deepcopy(points.read_level_entry(10))
                    with patch.object(editor, "detect_level_entry", return_value=replacement) as detect:
                        window.reset_current_level()
                    self.assertEqual(detect.call_args.args[0], points.IMAGES_DIR / "10.png")
                    window.save_all_levels()
                    self.assertEqual(points.read_level_entry(16, target), replacement)
                finally:
                    window.close()


if __name__ == "__main__":
    unittest.main()
