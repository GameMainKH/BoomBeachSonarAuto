"""Rebuild the bundled Linux x86_64 loader using GNU as/objcopy (Windows or Linux).

This is a maintainer tool. Runtime users do not need binutils or an Android NDK.
The assembly's code/data boundary is one page from its entry point.
"""
import hashlib
import struct
import subprocess
import tempfile
from pathlib import Path


def build():
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="bbsa-bpf-build-") as directory:
        obj, raw = Path(directory) / "loader.o", Path(directory) / "loader.code"
        subprocess.run(["as", "--64", str(here / "uid_bpf_x86_64.S"), "-o", str(obj)], check=True)
        subprocess.run(["objcopy", "-O", "binary", "-j", ".text", str(obj), str(raw)], check=True)
        code = raw.read_bytes()
        # PE-hosted as pads the section; ELF-hosted as may not. Normalize it.
        code = code.ljust((len(code) + 4095) & ~4095, b"\0")
    base, entry, data_offset = 0x400000, 0x1000, 0x2000
    data_size = len(code) - 0x1000
    if not 0 < data_size < 0x10000:
        raise RuntimeError("Unexpected code/data layout")
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 62, 1, base + entry, 64, 0, 0, 64, 56, 3, 0, 0, 0)
    rx = struct.pack("<IIQQQQQQ", 1, 5, 0, base, base, data_offset, data_offset, 4096)
    rw = struct.pack("<IIQQQQQQ", 1, 6, data_offset, base + data_offset, base + data_offset, data_size, data_size, 4096)
    stack = struct.pack("<IIQQQQQQ", 0x6474E551, 6, 0, 0, 0, 0, 0, 16)
    payload = (header + rx + rw + stack).ljust(entry, b"\0") + code
    destination = here / "uid_bpf_x86_64"
    destination.write_bytes(payload)
    print(f"{destination.name}: {len(payload)} bytes; SHA256={hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    build()
