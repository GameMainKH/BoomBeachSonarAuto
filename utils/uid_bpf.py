"""Independently pinned socket-UID matcher; no dependency on Android netd maps."""
import hashlib
import shlex
import struct
import uuid
from pathlib import Path


LOADER_SHA256 = "5f9aa2e57b20e3dc3b8ef5fd60dac724c38ebad985099d1b3b364c48381e076b"
LOADER = Path(__file__).with_name("native") / "uid_bpf_x86_64"
DEVICE_DIRECTORY = "/data/local/tmp/bbsa_uid_bpf"
DEVICE_LOADER = f"{DEVICE_DIRECTORY}/loader_{LOADER_SHA256[:16]}"
PIN_DIRECTORY = "/sys/fs/bpf/bbsa_uid_v1"
READY = "BBSA_UID_BPF_V1_OK"


class NetworkControlError(RuntimeError):
    """Unknown isolation state: retain any existing blockers and stop."""


def validate_uid(uid):
    if type(uid) is not int or not 0 <= uid <= 0x7FFFFFFF:
        raise ValueError("UID 必须为 0 至 2147483647 的整数")
    return uid


def program_bytes(uid):
    validate_uid(uid)
    return b"".join(struct.pack("<BBhi", *item) for item in (
        (0x85, 0, 0, 47),       # bpf_get_socket_uid(skb)
        (0x15, 0, 2, uid),
        (0xB7, 0, 0, 0), (0x95, 0, 0, 0),
        (0xB7, 0, 0, 1), (0x95, 0, 0, 0),
    ))


def program_tag(uid):
    # Linux bpf_prog_calc_tag uses the first 8 bytes of SHA1 of these map-free insns.
    return hashlib.sha1(program_bytes(uid)).hexdigest()[:16]


def pin_path(uid):
    return f"{PIN_DIRECTORY}/uid_{validate_uid(uid)}"


def bpf_match(uid):
    return ["-m", "bpf", "--object-pinned", pin_path(uid)]


class UidBpfProgram:
    def __init__(self, run_privileged, run_adb):
        self.run = run_privileged
        self.adb = run_adb
        self.deployed = False

    def _checked(self, script):
        result = self.run(script, check=False)
        if result.returncode:
            raise NetworkControlError(f"UID eBPF 操作失败 (rc={result.returncode}): {result.stderr.strip()} {result.stdout.strip()}")
        return result

    def ensure(self, uid):
        validate_uid(uid)
        if not self.deployed:
            self._deploy()
        # Never mount/replace the system BPF filesystem, or modify netd objects.
        script = (
            "set -e; grep -q ' /sys/fs/bpf bpf ' /proc/mounts; "
            f"test ! -L {PIN_DIRECTORY}; "
            f"mkdir -p {PIN_DIRECTORY}; "
            f"test \"$(stat -c %u {PIN_DIRECTORY})\" = 0; chmod 700 {PIN_DIRECTORY}; "
            f"{DEVICE_LOADER} ensure {pin_path(uid)} {uid} {program_tag(uid)}"
        )
        result = self._checked(script)
        if result.stdout.strip() != READY:
            raise NetworkControlError("UID eBPF 加载器未返回可信身份校验结果")

    def verify(self, uid):
        """Read only. A fresh controller can verify old pins without deploying anything."""
        result = self._checked(
            f"{DEVICE_LOADER} check {pin_path(uid)} {validate_uid(uid)} {program_tag(uid)}"
        )
        if result.stdout.strip() != READY:
            raise NetworkControlError("UID eBPF 程序身份校验失败")

    def _deploy(self):
        architecture = self._checked("uname -m").stdout.strip()
        if architecture != "x86_64":
            raise NetworkControlError(f"owner 不可用；eBPF 加载器暂仅支持 x86_64，当前为 {architecture}")
        if hashlib.sha256(LOADER.read_bytes()).hexdigest() != LOADER_SHA256:
            raise NetworkControlError("本地 UID eBPF 加载器 SHA256 不匹配")
        present = self.run(
            f"test -f {DEVICE_LOADER} && test ! -L {DEVICE_LOADER} && "
            f"test \"$(stat -c %u {DEVICE_LOADER})\" = 0 && "
            f"test \"$(sha256sum {DEVICE_LOADER} | cut -d ' ' -f 1)\" = {LOADER_SHA256}",
            check=False,
        )
        if present.returncode:
            staging = f"/data/local/tmp/bbsa-bpf-{uuid.uuid4().hex}.upload"
            self.adb(["push", str(LOADER), staging])
            # Install under a root-owned private directory; never execute the upload.
            self._checked(
                f"set -e; test ! -L {DEVICE_DIRECTORY}; mkdir -p {DEVICE_DIRECTORY}; "
                f"test \"$(stat -c %u {DEVICE_DIRECTORY})\" = 0; chmod 700 {DEVICE_DIRECTORY}; "
                f"test \"$(sha256sum {shlex.quote(staging)} | cut -d ' ' -f 1)\" = {LOADER_SHA256}; "
                f"cp {shlex.quote(staging)} {DEVICE_LOADER}.new; "
                f"chown 0:0 {DEVICE_LOADER}.new; chmod 700 {DEVICE_LOADER}.new; "
                f"mv -f {DEVICE_LOADER}.new {DEVICE_LOADER}; rm -f {shlex.quote(staging)}"
            )
        self.deployed = True
