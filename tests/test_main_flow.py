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

    def read_screenshot(self, output_path=None):
        self.calls.append(("read_screenshot", output_path))
        return object()

    def swipe(self, start_x, start_y, end_x, end_y):
        self.calls.append(("swipe", start_x, start_y, end_x, end_y))
        return self

    def enable_weak_network(self, package_name):
        self.calls.append(("enable_weak_network", package_name))

    def disable_weak_network(self, package_name):
        self.calls.append(("disable_weak_network", package_name))

    def enable_reject_network(self, package_name):
        self.calls.append(("enable_reject_network", package_name))

    def disable_reject_network(self, package_name):
        self.calls.append(("disable_reject_network", package_name))


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

        with patch.object(
            self.main,
            "wait_until_occur",
            side_effect=lambda *args, **kwargs: next(waits),
        ):
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
                call
                for call in self.adb.calls
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

        with patch.object(
            self.main,
            "wait_until_occur",
            side_effect=lambda *args, **kwargs: next(waits),
        ):
            self.main.enter_activity(re_enter=True, max_retries=1)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("enable_weak_network", package_name), self.adb.calls)
        self.assertNotIn(("swipe", 1000, 660, 1000, 180), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)

    def test_re_enter_failure_does_not_use_normal_restart_recovery(self):
        with patch.object(self.main, "wait_until_occur", return_value=None):
            with self.assertRaisesRegex(
                self.main.ProbeProtocolError,
                "第二次进入活动",
            ):
                self.main.enter_activity(re_enter=True, max_retries=1)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("close_app", package_name), self.adb.calls)
        self.assertNotIn(("open_app", package_name), self.adb.calls)
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)

    def test_cleanup_keeps_drop_when_probe_request_may_be_pending(self):
        transaction = self.main.ProbeTransaction(level=1, cell=(0, 0), index=0)
        transaction.advance(self.main.ProbePhase.REQUEST_PENDING)
        self.main._active_probe = transaction

        self.main.cleanup_weak_network("测试清理")

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)
        self.assertFalse(self.main._weak_network_cleanup_done)

    def test_probe_transaction_preserves_network_order(self):
        waits = iter(
            [
                DummyMatch((1, 1)),  # 点击前已在详情页
                DummyMatch((10, 20)),  # 第二次进入：活动按钮
                DummyMatch((30, 40)),  # 第二次进入：详情页
                DummyMatch((50, 60)),  # REJECT 后的重试按钮
                DummyMatch((70, 80)),  # 登录后下一轮：活动按钮
                DummyMatch((90, 100)),  # 登录后下一轮：详情页
            ]
        )
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "wait_until_occur",
                side_effect=lambda *args, **kwargs: next(waits),
            ),
            patch.object(self.main, "click_template", return_value=True),
            patch.object(self.main, "is_diamond_hit", return_value=True),
        ):
            result = self.main._probe_cell(
                level=1,
                hit_map=hit_map,
                cell=(0, 1),
                point=(400, 300),
                index=1,
            )

        package_name = self.main.GAME_PACKAGE_NAME
        network_calls = [
            call
            for call in self.adb.calls
            if call[0]
            in {
                "enable_reject_network",
                "disable_reject_network",
                "disable_weak_network",
                "enable_weak_network",
            }
        ]
        self.assertTrue(result)
        self.assertEqual(hit_map[0][1], 1)
        self.assertIsNone(self.main._active_probe)
        self.assertEqual(
            network_calls,
            [
                ("enable_reject_network", package_name),
                ("disable_reject_network", package_name),
                ("disable_weak_network", package_name),
                ("enable_weak_network", package_name),
            ],
        )

    def test_missing_retry_keeps_probe_pending_and_does_not_restore_drop(self):
        waits = iter(
            [
                DummyMatch((1, 1)),  # 点击前已在详情页
                DummyMatch((10, 20)),  # 第二次进入：活动按钮
                DummyMatch((30, 40)),  # 第二次进入：详情页
                None,  # REJECT 后没有重试按钮
            ]
        )
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "wait_until_occur",
                side_effect=lambda *args, **kwargs: next(waits),
            ),
            patch.object(self.main, "click_template", return_value=True),
            patch.object(self.main, "is_diamond_hit", return_value=False),
        ):
            with self.assertRaisesRegex(
                self.main.ProbeProtocolError,
                "未出现重试按钮",
            ):
                self.main._probe_cell(
                    level=1,
                    hit_map=hit_map,
                    cell=(0, 1),
                    point=(400, 300),
                    index=1,
                )

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertIsNotNone(self.main._active_probe)
        self.assertTrue(self.main._active_probe.request_may_be_pending)
        self.assertIn(("enable_reject_network", package_name), self.adb.calls)
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)

    def test_preflight_failure_retries_the_same_cell(self):
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "_execute_probe_transaction",
                side_effect=[
                    self.main.ProbeNotReadyError("页面未准备好"),
                    False,
                ],
            ) as execute,
            patch.object(self.main, "enter_activity") as recover,
        ):
            result = self.main._probe_cell(
                level=1,
                hit_map=hit_map,
                cell=(0, 1),
                point=(400, 300),
                index=1,
            )

        self.assertFalse(result)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(execute.call_args_list[0], execute.call_args_list[1])
        recover.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
