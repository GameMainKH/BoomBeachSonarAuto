import unittest
from unittest.mock import Mock

from _debug.weak_network_gui import AdbSession, WeakNetworkWorker


class AdbSessionTests(unittest.TestCase):
    def test_reuses_controller_for_same_trimmed_serial(self):
        controller = Mock()
        factory = Mock(return_value=controller)
        session = AdbSession(factory)

        first = session.get_controller(" 127.0.0.1:5555 ")
        second = session.get_controller("127.0.0.1:5555")

        self.assertIs(first, controller)
        self.assertIs(second, controller)
        factory.assert_called_once_with("127.0.0.1:5555", auto_connect=False)
        controller.connect.assert_called_once_with()
        controller.ensure_root_shell.assert_called_once_with()

    def test_keeps_separate_controller_per_serial(self):
        factory = Mock(side_effect=lambda *_args, **_kwargs: Mock())
        session = AdbSession(factory)

        first = session.get_controller("127.0.0.1:5555")
        second = session.get_controller("emulator-5554")

        self.assertIsNot(first, second)
        self.assertEqual(len(session.controllers()), 2)

    def test_rejects_empty_serial(self):
        session = AdbSession(Mock())

        with self.assertRaisesRegex(ValueError, "ADB 地址不能为空"):
            session.get_controller("   ")


class WeakNetworkWorkerTests(unittest.TestCase):
    def test_fast_path_does_not_collect_full_diagnostics(self):
        controller = Mock()
        session = Mock()
        session.get_controller.return_value = controller
        worker = WeakNetworkWorker(
            session,
            "127.0.0.1:5555",
            "drop",
            True,
            detailed_diagnostics=False,
        )

        worker.run()

        session.get_controller.assert_called_once_with("127.0.0.1:5555")
        controller.enable_weak_network.assert_called_once()
        controller.get_weak_network_diagnostics.assert_not_called()


if __name__ == "__main__":
    unittest.main()
