#!/usr/bin/env python3
"""Validate the self-hosted Vivado runner before consuming a build slot."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


CGROUP_ROOT = Path("/sys/fs/cgroup")
_UNLIMITED_MEMORY = 1 << 60


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeError):
        return None


def _read_int(path: Path) -> Optional[int]:
    value = _read_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _finite_limit(value: Optional[int]) -> Optional[int]:
    if value is None or value < 0 or value >= _UNLIMITED_MEMORY:
        return None
    return value


def cgroup_memory_available_bytes(root: Optional[Path] = None) -> Optional[int]:
    if root is None:
        root = CGROUP_ROOT
    v1_limit_path = root / "memory" / "memory.limit_in_bytes"
    v1_limit = _finite_limit(_read_int(v1_limit_path))
    if v1_limit_path.is_file():
        if v1_limit is None:
            return None
        usage = _read_int(root / "memory" / "memory.usage_in_bytes")
        return max(0, v1_limit - usage) if usage is not None else v1_limit

    v2_limit_path = root / "memory.max"
    v2_limit_text = _read_text(v2_limit_path)
    if v2_limit_text is None:
        return None
    if v2_limit_text == "max":
        return None
    try:
        v2_limit = _finite_limit(int(v2_limit_text))
    except ValueError:
        return None
    if v2_limit is None:
        return None
    usage = _read_int(root / "memory.current")
    return max(0, v2_limit - usage) if usage is not None else v2_limit


def cgroup_cpu_limit(root: Optional[Path] = None) -> Optional[float]:
    if root is None:
        root = CGROUP_ROOT
    v1_quota_path = root / "cpu" / "cpu.cfs_quota_us"
    v1_period_path = root / "cpu" / "cpu.cfs_period_us"
    if v1_quota_path.is_file() or v1_period_path.is_file():
        quota = _read_int(v1_quota_path)
        period = _read_int(v1_period_path)
        if quota is None or period is None or quota <= 0 or period <= 0:
            return None
        return quota / period

    v2_value = _read_text(root / "cpu.max")
    if v2_value is None:
        return None
    fields = v2_value.split()
    if len(fields) != 2 or fields[0] == "max":
        return None
    try:
        quota, period = int(fields[0]), int(fields[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def host_available_bytes() -> int:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise RuntimeError("GlobalMemoryStatusEx failed")
        return int(status.avail_phys)

    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values = {}
        for line in meminfo.read_text(encoding="ascii").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.split()[0]) * 1024
        if "MemAvailable" in values:
            return values["MemAvailable"]

    if shutil.which("vm_stat"):
        page_size = 4096
        output = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, check=True
        ).stdout
        free_pages = 0
        for line in output.splitlines():
            if line.startswith(("Pages free", "Pages inactive", "Pages speculative")):
                free_pages += int(line.split(":", 1)[1].strip().rstrip("."))
        return free_pages * page_size

    raise RuntimeError("Cannot determine available physical memory")


def available_bytes() -> int:
    host_available = host_available_bytes()
    cgroup_available = cgroup_memory_available_bytes()
    if cgroup_available is None:
        return host_available
    return min(host_available, cgroup_available)


def main() -> int:
    vivado = os.environ.get("VIVADO_BIN", "").strip()
    if not vivado:
        print("VIVADO_BIN is required; point it at Vivado 2025.2.", file=sys.stderr)
        return 2

    vivado_path = Path(vivado)
    if not vivado_path.exists() and not shutil.which(vivado):
        print(f"Vivado executable not found: {vivado}", file=sys.stderr)
        return 2

    version = subprocess.run(
        [vivado, "-version"], capture_output=True, text=True, errors="replace"
    )
    version_text = version.stdout + version.stderr
    print(version_text.strip())
    if "2025.2" not in version_text:
        print("The runner must provide Vivado 2025.2.", file=sys.stderr)
        return 2

    available_gb = available_bytes() / (1024**3)
    required_gb = float(os.environ.get("CI_MIN_AVAILABLE_GB", "20"))
    print(f"Available physical memory: {available_gb:.1f} GiB")
    print(f"Required available memory: {required_gb:.1f} GiB")
    if available_gb < required_gb:
        print("Not enough available memory for the Vivado build.", file=sys.stderr)
        return 3

    cpu_limit = cgroup_cpu_limit()
    effective_cpu = cpu_limit if cpu_limit is not None else float(os.cpu_count() or 1)
    required_cpu = float(os.environ.get("CI_MIN_CPU", "1"))
    print(f"Effective CPU capacity: {effective_cpu:.2f}")
    print(f"Required CPU capacity: {required_cpu:.2f}")
    if effective_cpu < required_cpu:
        print("Not enough CPU capacity for the Vivado build.", file=sys.stderr)
        return 3

    if not any(
        os.environ.get(name, "").strip()
        for name in ("XILINXD_LICENSE_FILE", "LM_LICENSE_FILE", "HOG_XIL_LICENSE")
    ):
        print("License variables are not set; the runner may use a local Vivado license.")

    print("Vivado runner validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
