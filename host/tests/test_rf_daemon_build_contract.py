"""The main tree must build a daemon, not silently only the legacy board CLI."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "rf_overlay/sw/board_app"


def test_default_make_target_builds_the_daemon_from_available_sources():
    text = (APP / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^all:.*\$\(DAEMON_BIN\)", text, re.M)
    assert text.index("DAEMON_BIN := rf_daemon") < text.index("all:")
    for name in ("rf_daemon_main.c", "rf_daemon.c", "rf_protocol.c", "rf_state.c",
                 "rf_iq.c", "rf_core.c", "rf_pl_status_reader.c"):
        assert (APP / name).is_file(), name
        assert name in text


def test_main_tree_has_release_packaging_and_one_software_map():
    assert (ROOT / "scripts/package_merged_overlay.py").is_file()
    wrapper = (ROOT / "scripts/merged_release.py").read_text(encoding="utf-8")
    assert "pynq_overlay.sw.merged_release" in wrapper


def test_validation_entry_point_rejects_an_absent_release(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/merged_release.py"),
                             "--require-software", str(tmp_path / "absent")],
                            capture_output=True, text=True)
    assert result.returncode != 0, "a validator cannot silently skip its CLI"
