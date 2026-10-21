import os
import json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QApplication
from gui import MainWindow
from utils.gui_config import load_config, save_config


class GuiTests(unittest.TestCase):
    def test_progress_footer_updates_without_log_noise(self):
        window = MainWindow()
        window.show_progress(json.dumps({"description": "第 11 关探索", "current": 4,
            "total": 16, "postfix": "确认 1/5 | 探测 12/100 | 最坏剩余 88 | 总运行 00:01:05"}))
        self.assertEqual(window.progress_bar.value(), 4)
        self.assertEqual(window.progress_bar.maximum(), 16)
        self.assertIn("已找到潜艇格", window.progress_bar.format())
        self.assertIn("最坏剩余 88", window.progress_details.text())
        self.assertEqual(window.logs.toPlainText(), "")
        window.show_progress(json.dumps({"description": "第 11 关逐格扫描", "current": 1,
            "total": 8, "postfix": "还需 7 次 | 总运行 00:01:08"}))
        self.assertIn("已扫描", window.progress_bar.format())
        window.close()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_loaded_and_idle(self):
        window = MainWindow()
        self.assertEqual(window.serial.text(), window.config["ADB_SERIAL"])
        self.assertTrue(window.start_button.isEnabled())
        window.close()

    def test_failed_launch_restores_controls(self):
        window = MainWindow()
        with patch.object(window, "save_settings", return_value=True), patch.object(QProcess, "start"):
            window.start_run()
        self.assertFalse(window.start_button.isEnabled())
        window.process_error(QProcess.ProcessError.FailedToStart)
        self.assertTrue(window.start_button.isEnabled())
        self.assertIsNone(window.process)
        window.close()

    def test_save_config_preserves_other_expressions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.py"
            path.write_text("# comment\nVALUE = 'old'\nOTHER = 2 * 3\n", encoding="utf-8")
            save_config(path, {"VALUE": "new'quoted"})
            self.assertEqual(load_config(path)["VALUE"], "new'quoted")
            self.assertIn("OTHER = 2 * 3", path.read_text())
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_config(path, {"UNKNOWN": 1})
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
