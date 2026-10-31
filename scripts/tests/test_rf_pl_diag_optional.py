import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pynq_overlay" / "sw"))

from rf_status_client import StatusProtocolError, _pl_status


def test_optional_diagnostics_are_kept():
    parsed = _pl_status({
        "sampled_at_monotonic_ns": 1, "io_ok": True,
        "tx": {"source_selected": True, "length_words": 2540, "position_words": 372},
        "rx": {"armed": True, "done": False, "position_words": 0},
        "diagnostics": {"status": {"valid": True, "raw": 65559},
                        "cap_status": {"valid": True, "raw": 8192}},
    })
    assert parsed.diagnostics["status"]["raw"] == 65559
    assert parsed.rx.position_words == 0


def test_old_daemon_without_diagnostics_still_parses():
    parsed = _pl_status({
        "sampled_at_monotonic_ns": 1, "io_ok": False,
        "tx": {"source_selected": None, "length_words": None, "position_words": None},
        "rx": {"armed": None, "done": None, "position_words": None},
    })
    assert parsed.diagnostics is None


def test_non_object_diagnostics_is_rejected():
    try:
        _pl_status({
            "sampled_at_monotonic_ns": 1, "io_ok": True,
            "tx": {"source_selected": True, "length_words": 1, "position_words": 0},
            "rx": {"armed": True, "done": False, "position_words": 0},
            "diagnostics": ["not", "an", "object"],
        })
    except StatusProtocolError:
        return
    raise AssertionError("non-object diagnostics was accepted")
