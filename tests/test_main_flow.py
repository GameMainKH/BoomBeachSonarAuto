import importlib
import sys
import unittest
from unittest.mock import patch


class FakeAdb:
    instances = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        FakeAdb.instances.append(self)

    def delay(self, seconds):
        self.calls.append(("delay", seconds))
        return self

    def close_app(self, package_name):
        self.calls.append(("close_app", package_name))

    def open_app(self, package_name):
        self.calls.append(("open_app", package_name))
        return self

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def swipe(self, start_x, start_y, end_x, end_y):
        self.calls.append(("swipe", start_x, start_y, end_x, end_y))
        return self

    def enable_weak_network(self, package_name):
        self.calls.append(("enable_weak_network", package_name))


class DummyMatch:
    def __init__(self, center):
        self.center = center


class MainFlowTest(unittest.TestCase):
    def setUp(self):
        FakeAdb.instances.clear()
        self.utils = importlib.import_module("utils")
        self.original_adb_controller = self.utils.AdbController
        self.utils.AdbController = FakeAdb
        sys.modules.pop("main", None)
        self.main = importlib.import_module("main")
        self.adb = self.main.adb

    def tearDown(self):
        sys.modules.pop("main", None)
        self.utils.AdbController = self.original_adb_controller
        FakeAdb.instances.clear()

    def test_enter_activity_recovers_after_activity_button_missing(self):
        waits = iter(
            [
                None,
                DummyMatch((10, 20)),
                DummyMatch((30, 40)),
                DummyMatch((50, 60)),
            ]
        )

        with patch.object(self.main, "wait_until_occur", side_effect=lambda *args, **kwargs: next(waits)):
            self.main.enter_activity(max_retries=2)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertEqual(self.adb.calls.count(("close_app", package_name)), 1)
        self.assertEqual(self.adb.calls.count(("open_app", package_name)), 1)
        self.assertIn(("click", 10, 20), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)
        self.assertEqual(self.adb.calls.count(("enable_weak_network", package_name)), 1)
        self.assertEqual(
            [
                call for call in self.adb.calls
                if call == ("swipe", 1000, 660, 1000, 180)
            ],
            [
                ("swipe", 1000, 660, 1000, 180),
                ("swipe", 1000, 660, 1000, 180),
            ],
        )

    def test_enter_activity_stops_after_max_retries(self):
        with patch.object(self.main, "wait_until_occur", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "最大重试次数 2"):
                self.main.enter_activity(max_retries=2)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertEqual(self.adb.calls.count(("close_app", package_name)), 2)
        self.assertEqual(self.adb.calls.count(("open_app", package_name)), 2)

    def test_re_enter_skips_first_enter_only_actions(self):
        waits = iter(
            [
                DummyMatch((30, 40)),
                DummyMatch((50, 60)),
            ]
        )

        with patch.object(self.main, "wait_until_occur", side_effect=lambda *args, **kwargs: next(waits)):
            self.main.enter_activity(re_enter=True, max_retries=1)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("enable_weak_network", package_name), self.adb.calls)
        self.assertNotIn(("swipe", 1000, 660, 1000, 180), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)


if __name__ == "__main__":
    unittest.main()
