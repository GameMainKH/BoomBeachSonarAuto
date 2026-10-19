import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.adb_control import AdbController


class AdbNetworkStateTests(unittest.TestCase):
    def test_queries_detect_ipv6_only_residue(self):
        for mode, chain, query in (
            ("drop", "BBMA_WEAKNET", "is_weak_network_enabled"),
            ("reject", "BBMA_REJECTNET", "is_reject_network_enabled"),
        ):
            with self.subTest(mode=mode):
                adb = AdbController(auto_connect=False)
                def run(script, **kwargs):
                    text = "-P OUTPUT ACCEPT\n"
                    if script == "ip6tables -S OUTPUT":
                        text += f"-A OUTPUT -m owner --uid-owner 10051 -j {chain}\n"
                    elif script == f"ip6tables -S {chain}":
                        text = f"-N {chain}\n"
                        if mode == "drop":
                            text += f"-A {chain} -j DROP\n"
                        else:
                            text += f"-A {chain} -p tcp -j REJECT --reject-with tcp-reset\n-A {chain} -j REJECT --reject-with icmp6-port-unreachable\n"
                    return SimpleNamespace(returncode=0, stdout=text, stderr="")
                with (
                    patch.object(adb, "_get_package_uid", return_value=10051),
                    patch.object(adb, "_is_ip6tables_available", return_value=True),
                    patch.object(adb, "_run_privileged_script", side_effect=run),
                ):
                    self.assertTrue(getattr(adb, query)("example.package"))

    def test_failed_enable_does_not_set_success_cache(self):
        adb = AdbController(auto_connect=False)
        with patch.object(adb._uid_network, "enable", side_effect=RuntimeError("blocked")):
            with self.assertRaises(RuntimeError):
                adb._set_weak_network_rule(10051, enabled=True)
        self.assertNotIn(10051, adb._weak_network_enabled_uids)


if __name__ == "__main__":
    unittest.main()
