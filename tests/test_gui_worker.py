import subprocess
import unittest
from unittest.mock import patch
from gui_worker import check_device


class DeviceCheckTests(unittest.TestCase):
    @patch("gui_worker.subprocess.run")
    def test_ready(self, run):
        run.side_effect = [subprocess.CompletedProcess([], 0, "connected", ""),
                           subprocess.CompletedProcess([], 0, "device\n", "")]
        with patch("config.ADB_SERIAL", "127.0.0.1:5555"):
            check_device()
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    @patch("gui_worker.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_adb(self, run):
        with self.assertRaisesRegex(RuntimeError, "找不到 adb"):
            check_device()

    @patch("gui_worker.subprocess.run", side_effect=subprocess.TimeoutExpired("adb", 15))
    def test_timeout(self, run):
        with self.assertRaisesRegex(RuntimeError, "15 秒"):
            check_device()

    @patch("gui_worker.subprocess.run")
    def test_offline(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "device offline")
        with self.assertRaisesRegex(RuntimeError, "设备未就绪"):
            check_device()
