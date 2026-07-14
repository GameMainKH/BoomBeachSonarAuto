import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.adb_control import AdbController


class AdbNetworkStateTests(unittest.TestCase):
    def test_weak_network_query_detects_ipv6_only_residue(self):
        adb = AdbController(auto_connect=False)
        with (
            patch.object(adb, "_get_package_uid", return_value=10051),
            patch.object(adb, "_is_weak_network_rule_active", return_value=False),
            patch.object(adb, "_is_ip6tables_available", return_value=True),
            patch.object(
                adb,
                "_run_privileged_script",
                return_value=SimpleNamespace(returncode=0),
            ) as run_script,
        ):
            self.assertTrue(adb.is_weak_network_enabled("example.package"))

        run_script.assert_called_once_with(
            "ip6tables -C OUTPUT -m owner --uid-owner 10051 -j BBMA_WEAKNET",
            check=False,
        )

    def test_reject_network_query_detects_ipv6_only_residue(self):
        adb = AdbController(auto_connect=False)
        with (
            patch.object(adb, "_get_package_uid", return_value=10051),
            patch.object(adb, "_is_reject_network_rule_active", return_value=False),
            patch.object(adb, "_is_ip6tables_available", return_value=True),
            patch.object(
                adb,
                "_run_privileged_script",
                return_value=SimpleNamespace(returncode=0),
            ) as run_script,
        ):
            self.assertTrue(adb.is_reject_network_enabled("example.package"))

        run_script.assert_called_once_with(
            "ip6tables -C OUTPUT -m owner --uid-owner 10051 -j BBMA_REJECTNET",
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
