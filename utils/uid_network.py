"""owner-first UID filtering with a narrowly triggered xt_bpf fallback.

Network protocol is unchanged: DROP -> result -> REJECT -> retry -> login.
Backend preparation and rule verification never remove an existing blocker.
"""
import re
import shlex

from utils.logger import get_logger
from utils.uid_bpf import NetworkControlError, UidBpfProgram, bpf_match, pin_path, validate_uid


logger = get_logger(__name__)
CHAINS = {"drop": "BBMA_WEAKNET", "reject": "BBMA_REJECTNET"}


def owner_match(uid):
    return ["-m", "owner", "--uid-owner", str(validate_uid(uid))]


def owner_unavailable(result):
    if result.returncode == 0:
        return False
    output = (result.stdout + "\n" + result.stderr).lower()
    # Transport, permission, lock and arbitrary rule errors must NOT select fallback.
    if any(text in output for text in ("permission denied", "operation not permitted", "device offline", "error: closed", "xtables lock")):
        return False
    return bool(
        re.search(r"couldn.t (?:find|load) match [`'\"]owner[`'\"]", output)
        or re.search(r"extension owner revision \d+ not supported", output)
        or ("unknown option" in output and "--uid-owner" in output)
    )


def enable_script(command, uid, mode, backend):
    chain = CHAINS[mode]
    match = shlex.join(owner_match(uid) if backend == "owner" else bpf_match(uid))
    create = f"{command} -N {chain} 2>/dev/null || {command} -S {chain} >/dev/null"
    if mode == "drop":
        body = f"{command} -F {chain}; {command} -A {chain} -j DROP"
    else:
        unreachable = "icmp6-port-unreachable" if command == "ip6tables" else "icmp-port-unreachable"
        body = (
            f"{command} -F {chain}; "
            f"{command} -A {chain} -p tcp -j REJECT --reject-with tcp-reset; "
            f"{command} -A {chain} -j REJECT --reject-with {unreachable}"
        )
    # Populate chains before installing any hook. REJECT is inserted above DROP.
    # Existing non-empty chains are never flushed: they may protect another UID.
    return (
        f"set -e; {create}; "
        f"if ! {command} -S {chain} | grep -q '^-A '; then {body}; fi; "
        f"{command} -C OUTPUT {match} -j {chain} 2>/dev/null || "
        f"{command} -I OUTPUT 1 {match} -j {chain}"
    )


class UidNetworkRules:
    def __init__(self, controller):
        self.controller = controller
        # Resolve callbacks dynamically so test overrides/reconnections stay valid.
        self.bpf = UidBpfProgram(self.run, lambda args: controller._run(args))
        self.backends = {}

    def run(self, script, *, check=False):
        return self.controller._run_privileged_script(script, check=check)

    def checked(self, script):
        result = self.run(script)
        if result.returncode or result.stderr.strip():
            raise NetworkControlError(f"网络规则操作失败 (rc={result.returncode}): {result.stderr.strip()} {result.stdout.strip()}")
        return result

    def commands(self):
        if self.controller._is_ip6tables_available():
            return ("iptables", "ip6tables")
        routes = self.checked("ip -6 route show table all")
        if routes.stdout.strip():
            raise NetworkControlError("存在 IPv6 路由但 ip6tables 不可用，拒绝不完整隔离")
        return ("iptables",)

    def output_rules(self, command):
        result = self.checked(f"{command} -S OUTPUT")
        try:
            lines = [shlex.split(line) for line in result.stdout.splitlines() if line.strip()]
        except ValueError as exc:
            raise NetworkControlError("OUTPUT 规则格式不明") from exc
        if ["-P", "OUTPUT", "ACCEPT"] not in lines:
            raise NetworkControlError("OUTPUT 默认策略不明或不是 ACCEPT")
        if any(line[:2] not in (["-P", "OUTPUT"], ["-A", "OUTPUT"]) for line in lines):
            raise NetworkControlError("OUTPUT 规则格式不明")
        return [line[2:] for line in lines if line[:2] == ["-A", "OUTPUT"]]

    def scope_uid(self, rule):
        """Only recognize exact application-owned hooks. Unknown scopes fail closed."""
        match = rule[:-2]
        if len(match) == 4 and match[:3] == ["-m", "owner", "--uid-owner"] and match[3].isascii() and match[3].isdecimal():
            return validate_uid(int(match[3]))
        if len(match) == 4 and match[:3] == ["-m", "bpf", "--object-pinned"]:
            uid = re.fullmatch(re.escape(pin_path(0).rsplit("_", 1)[0]) + r"_(0|[1-9][0-9]*)", match[3])
            if uid:
                value = validate_uid(int(uid.group(1)))
                self.bpf.verify(value)
                return value
        raise NetworkControlError(f"发现无法确认 UID 范围的项目网络规则: {shlex.join(rule)}")

    def hooks(self, command, uid, mode):
        chain = CHAINS[mode]
        found = []
        for rule in self.output_rules(command):
            if chain not in rule:
                continue
            if len(rule) < 2 or rule[-2:] != ["-j", chain]:
                raise NetworkControlError("项目网络规则跳转格式不明")
            if self.scope_uid(rule) == uid:
                found.append(rule)
        return found

    def verify_chain(self, command, mode):
        chain = CHAINS[mode]
        result = self.checked(f"{command} -S {chain}")
        lines = [shlex.split(line) for line in result.stdout.splitlines() if line.strip()]
        rules = [line[2:] for line in lines if line[:2] == ["-A", chain]]
        if mode == "drop":
            expected = [["-j", "DROP"]]
        else:
            unreachable = "icmp6-port-unreachable" if command == "ip6tables" else "icmp-port-unreachable"
            expected = [["-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"],
                        ["-j", "REJECT", "--reject-with", unreachable]]
        if rules != expected:
            raise NetworkControlError(f"{command} {chain} 内容不符合隔离要求")

    def enable(self, uid, mode):
        validate_uid(uid)
        commands = self.commands()   # Validate IPv6 availability before any mutation.
        for command in commands:
            backend = self.backends.get((command, uid), "owner")
            if backend == "bpf":
                self.bpf.ensure(uid)
            result = self.run(enable_script(command, uid, mode, backend))
            if result.returncode:
                if backend != "owner" or not owner_unavailable(result):
                    raise NetworkControlError(f"{command} {mode} 失败，保留已有规则: {result.stderr.strip()} {result.stdout.strip()}")
                logger.warning("%s owner 不可用，切换 UID eBPF: uid=%s", command, uid)
                self.bpf.ensure(uid)
                backend = "bpf"
                self.checked(enable_script(command, uid, mode, backend))
            elif result.stderr.strip():
                raise NetworkControlError(f"网络规则返回未确认警告: {result.stderr.strip()}")
            self.backends[(command, uid)] = backend
            self.verify_chain(command, mode)
            rules = self.output_rules(command)
            expected = (owner_match(uid) if backend == "owner" else bpf_match(uid)) + ["-j", CHAINS[mode]]
            if not rules or rules[0] != expected:
                raise NetworkControlError("UID 阻断规则未处于 OUTPUT 首位，拒绝继续")
            if backend == "bpf":
                self.bpf.verify(uid)

    def disable(self, uid, mode):
        validate_uid(uid)
        for command in self.commands():
            # Read actual rules, not backend cache: handles residue after restarting.
            for rule in self.hooks(command, uid, mode):
                self.checked(f"{command} -D OUTPUT {shlex.join(rule)}")
            if self.hooks(command, uid, mode):
                raise NetworkControlError("UID 阻断规则仍残留，拒绝报告恢复成功")
        # Unreferenced chains and verified pins remain for reuse; they block no traffic.

    def active(self, uid, mode, command=None):
        found = False
        for family in ((command,) if command else self.commands()):
            if self.hooks(family, uid, mode):
                self.verify_chain(family, mode)
                found = True
        return found

    def diagnostics(self, uid, mode):
        lines = [f"uid={uid}", f"bpf_pin={pin_path(uid)}"]
        for command in self.commands():
            result = self.run(f"{command} -S OUTPUT; {command} -L {CHAINS[mode]} -n -v -x")
            lines.extend((f"[{command}] rc={result.returncode}", result.stdout.strip(), result.stderr.strip()))
        return "\n".join(line for line in lines if line)
