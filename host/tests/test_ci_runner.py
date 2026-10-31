from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "ci" / "check_vivado_runner.py"
_spec = spec_from_file_location("check_vivado_runner", MODULE_PATH)
_runner = module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_runner)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")


def test_cgroup_v1_memory_available_uses_limit_minus_usage(tmp_path):
    root = tmp_path / "cgroup"
    _write(root / "memory" / "memory.limit_in_bytes", str(2 * 1024**3))
    _write(root / "memory" / "memory.usage_in_bytes", str(512 * 1024**2))

    assert _runner.cgroup_memory_available_bytes(root) == 1536 * 1024**2


def test_cgroup_v1_cpu_quota_is_fractional_cpu(tmp_path):
    root = tmp_path / "cgroup"
    _write(root / "cpu" / "cpu.cfs_quota_us", "50000")
    _write(root / "cpu" / "cpu.cfs_period_us", "100000")

    assert _runner.cgroup_cpu_limit(root) == 0.5


def test_cgroup_v2_memory_and_cpu_limits(tmp_path):
    root = tmp_path / "cgroup"
    _write(root / "memory.max", str(40 * 1024**3))
    _write(root / "memory.current", str(4 * 1024**3))
    _write(root / "cpu.max", "400000 100000")

    assert _runner.cgroup_memory_available_bytes(root) == 36 * 1024**3
    assert _runner.cgroup_cpu_limit(root) == 4.0


def test_unlimited_cgroup_values_are_ignored(tmp_path):
    root = tmp_path / "cgroup"
    _write(root / "memory.max", "max")
    _write(root / "cpu.max", "max 100000")

    assert _runner.cgroup_memory_available_bytes(root) is None
    assert _runner.cgroup_cpu_limit(root) is None


def test_available_bytes_is_capped_by_cgroup(monkeypatch, tmp_path):
    root = tmp_path / "cgroup"
    _write(root / "memory.max", str(2 * 1024**3))
    _write(root / "memory.current", str(256 * 1024**2))
    monkeypatch.setattr(_runner, "CGROUP_ROOT", root)
    monkeypatch.setattr(_runner, "host_available_bytes", lambda: 300 * 1024**3)

    assert _runner.available_bytes() == 1792 * 1024**2
