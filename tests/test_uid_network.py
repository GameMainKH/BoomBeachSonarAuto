import hashlib
import re
import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from utils.uid_bpf import NetworkControlError, UidBpfProgram, READY, LOADER_SHA256, LOADER, program_bytes, program_tag, pin_path
from utils.uid_network import CHAINS, UidNetworkRules, bpf_match, owner_match, owner_unavailable


def result(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class RulesDevice:
    """Small rule store that observes hooks; never calls ADB."""
    def __init__(self):
        self.rules = {"iptables": [], "ip6tables": []}
        self.chains, self.errors, self.calls = {}, {}, []
        self.ipv6, self.routes = True, "default dev wlan0\n"

    def _is_ip6tables_available(self):
        return self.ipv6

    def _run_privileged_script(self, script, **kwargs):
        self.calls.append(script)
        if script == "ip -6 route show table all":
            return result(out=self.routes)
        if script.startswith("set -e;"):
            command, chain = re.search(r"(ip6?tables) -N (\w+)", script).groups()
            backend = "owner" if "-m owner" in script else "bpf"
            if (command, backend) in self.errors:
                return self.errors[command, backend]
            spec = shlex.split(script.rsplit("-I OUTPUT 1 ", 1)[1])
            if spec not in self.rules[command]:
                self.rules[command].insert(0, spec)
            if (command, chain) not in self.chains:
                if chain == CHAINS["drop"]:
                    body = [["-j", "DROP"]]
                else:
                    unreachable = "icmp6-port-unreachable" if command == "ip6tables" else "icmp-port-unreachable"
                    body = [["-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"], ["-j", "REJECT", "--reject-with", unreachable]]
                self.chains[command, chain] = body
            return result()
        fields = shlex.split(script)
        command = fields[0]
        if fields[1:3] == ["-S", "OUTPUT"]:
            return result(out="-P OUTPUT ACCEPT\n" + "".join("-A OUTPUT " + shlex.join(rule) + "\n" for rule in self.rules[command]))
        if fields[1] == "-S":
            chain = fields[2]
            return result(out=f"-N {chain}\n" + "".join(f"-A {chain} " + shlex.join(rule) + "\n" for rule in self.chains[command, chain]))
        if fields[1:3] == ["-D", "OUTPUT"]:
            self.rules[command].remove(fields[3:])
            return result()
        raise AssertionError(f"Unexpected fake command: {script}")

    def _run(self, args):
        raise AssertionError("Native deployment must be explicitly mocked")


class UidNetworkTests(unittest.TestCase):
    def setUp(self):
        self.device = RulesDevice()
        self.network = UidNetworkRules(self.device)
        self.network.bpf = Mock()

    def test_owner_is_preferred_without_native_deployment(self):
        self.network.enable(10051, "drop")
        self.assertTrue(self.network.active(10051, "drop"))
        self.network.bpf.ensure.assert_not_called()
        for rules in self.device.rules.values():
            self.assertEqual(rules[0], owner_match(10051) + ["-j", "BBMA_WEAKNET"])

    def test_owner_failure_falls_back_and_reject_reuses_backend(self):
        self.device.errors["iptables", "owner"] = result(2, err="iptables: Couldn't find match `owner'")
        self.network.enable(10051, "drop")
        self.network.enable(10051, "reject")
        self.assertEqual(self.network.backends["iptables", 10051], "bpf")
        self.assertEqual(self.network.backends["ip6tables", 10051], "owner")
        self.assertEqual(self.device.rules["iptables"][0], bpf_match(10051) + ["-j", "BBMA_REJECTNET"])
        self.assertEqual(self.device.rules["iptables"][1], bpf_match(10051) + ["-j", "BBMA_WEAKNET"])
        self.network.bpf.ensure.assert_called_with(10051)
        self.network.bpf.verify.assert_called_with(10051)

    def test_operational_errors_never_trigger_fallback(self):
        for text in ("Permission denied", "device offline", "xtables lock", "No chain/target/match by that name", "Couldn't load target `REJECT'", "syntax error"):
            with self.subTest(error=text):
                self.device.errors["iptables", "owner"] = result(1, err=text)
                with self.assertRaises(NetworkControlError):
                    self.network.enable(10051, "drop")
                self.network.bpf.ensure.assert_not_called()

    def test_ipv6_failure_retains_ipv4_blocker_and_is_fatal(self):
        self.device.errors["ip6tables", "owner"] = result(1, err="Permission denied")
        with self.assertRaises(NetworkControlError):
            self.network.enable(10051, "drop")
        self.assertTrue(self.device.rules["iptables"])
        self.assertFalse(any(" -D " in call for call in self.device.calls))

    def test_ipv6_availability_is_checked_before_mutation(self):
        self.device.ipv6 = False
        with self.assertRaises(NetworkControlError):
            self.network.enable(10051, "drop")
        self.assertFalse(self.device.rules["iptables"])
        self.device.routes = ""
        self.network.enable(10051, "drop")

    def test_restart_cleanup_handles_both_backends_and_preserves_other_uid(self):
        self.network.enable(10051, "drop")
        for rules in self.device.rules.values():
            rules.extend((bpf_match(10051) + ["-j", "BBMA_WEAKNET"], owner_match(10052) + ["-j", "BBMA_WEAKNET"]))
        self.network.backends.clear()
        self.network.disable(10051, "drop")
        for rules in self.device.rules.values():
            self.assertEqual(rules, [owner_match(10052) + ["-j", "BBMA_WEAKNET"]])
        self.assertFalse(self.network.active(10051, "drop"))
        self.network.bpf.ensure.assert_not_called()

    def test_reject_cleanup_does_not_remove_drop(self):
        self.network.enable(10051, "drop")
        self.network.enable(10051, "reject")
        self.network.disable(10051, "reject")
        self.assertTrue(self.network.active(10051, "drop"))
        self.assertFalse(self.network.active(10051, "reject"))

    def test_bad_pin_identity_is_neither_accepted_nor_deleted(self):
        self.network.enable(10051, "drop")
        self.device.rules["iptables"] = [bpf_match(10051) + ["-j", "BBMA_WEAKNET"]]
        self.network.bpf.verify.side_effect = NetworkControlError("identity mismatch")
        for action in (self.network.active, self.network.disable):
            with self.assertRaises(NetworkControlError):
                action(10051, "drop")
        self.assertTrue(self.device.rules["iptables"])
        self.assertFalse(any(" -D " in call for call in self.device.calls))

    def test_unknown_scope_cannot_be_reported_clear(self):
        self.device.rules["iptables"] = [["-j", "BBMA_WEAKNET"]]
        with self.assertRaises(NetworkControlError):
            self.network.disable(10051, "drop")
        self.assertTrue(self.device.rules["iptables"])

    def test_bad_chain_and_shadowed_hook_are_rejected(self):
        self.network.enable(10051, "drop")
        self.device.chains["iptables", "BBMA_WEAKNET"] = [["-j", "RETURN"]]
        with self.assertRaises(NetworkControlError):
            self.network.enable(10051, "drop")
        self.device.chains["iptables", "BBMA_WEAKNET"] = [["-j", "DROP"]]
        self.device.rules["iptables"].insert(0, ["-j", "ACCEPT"])
        with self.assertRaises(NetworkControlError):
            self.network.enable(10051, "drop")

    def test_query_failure_never_means_no_rule(self):
        with patch.object(self.device, "_run_privileged_script", return_value=result(1, err="closed")):
            with self.assertRaises(NetworkControlError):
                self.network.active(10051, "drop")


class UidBpfTests(unittest.TestCase):
    def test_instruction_and_loader_identity(self):
        self.assertEqual(len(program_bytes(10051)), 48)
        self.assertNotEqual(program_tag(10051), program_tag(10052))
        binary = LOADER.read_bytes()
        self.assertEqual(hashlib.sha256(binary).hexdigest(), LOADER_SHA256)
        self.assertEqual(binary[0x2000:0x2030], program_bytes(0))
        for uid in (-1, 1 << 31, True, "10051"):
            with self.assertRaises(ValueError):
                pin_path(uid)

    def test_verify_is_read_only_and_requires_ready_marker(self):
        run, push = Mock(return_value=result(out=READY + "\n")), Mock()
        program = UidBpfProgram(run, push)
        program.verify(10051)
        self.assertIn(" check ", run.call_args.args[0])
        self.assertNotIn("ensure", run.call_args.args[0])
        push.assert_not_called()
        run.return_value = result(out="unexpected")
        with self.assertRaises(NetworkControlError):
            program.verify(10051)

    def test_unsupported_architecture_never_pushes(self):
        run, push = Mock(return_value=result(out="aarch64\n")), Mock()
        with self.assertRaises(NetworkControlError):
            UidBpfProgram(run, push).ensure(10051)
        push.assert_not_called()
        run.assert_called_once_with("uname -m", check=False)

    def test_owner_error_classification(self):
        for text in ("Couldn't find match `owner'", "Couldn't load match 'owner':No such file or directory", "Extension owner revision 1 not supported, missing kernel module?", 'unknown option "--uid-owner"'):
            self.assertTrue(owner_unavailable(result(2, err=text)))
            self.assertFalse(owner_unavailable(result(0, err=text)))
        self.assertFalse(owner_unavailable(result(2, err="Couldn't find match `owner'; Permission denied")))

    def test_failed_bpf_preparation_does_not_install_a_fallback_hook(self):
        device = RulesDevice()
        device.errors["iptables", "owner"] = result(2, err="Couldn't find match `owner'")
        network = UidNetworkRules(device)
        network.bpf = Mock()
        network.bpf.ensure.side_effect = NetworkControlError("loader unavailable")
        with self.assertRaises(NetworkControlError):
            network.enable(10051, "drop")
        self.assertFalse(device.rules["iptables"])
        self.assertFalse(any("-m bpf" in script for script in device.calls))


if __name__ == "__main__":
    unittest.main()
