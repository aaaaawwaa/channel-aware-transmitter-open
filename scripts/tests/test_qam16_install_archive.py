"""Real archive/layout regressions; all shell writes stay in pytest's sandbox."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
QAM = ROOT / "evidence/rf-teaching/qam16-rect-product-2026-10-24"
RELEASE = QAM / "candidate/release"
OLD_ARCHIVE = QAM / "candidate/candidate.tgz"
BASE = ROOT / "scripts/rf_teaching_install.sh"
ID = "cd76818afc3bee7c610678574e892b26"
PARENT = "bedf98c77283002c632dd7f345abaff9"


def builder():
    spec = importlib.util.spec_from_file_location("qam_archive_builder", QAM / "build_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def shell(tmp_path):
    bash = Path("<LOCAL_PATH>")
    if not bash.is_file():
        found = shutil.which("bash")
        if not found:
            pytest.skip("POSIX shell required for the real installer prefix")
        bash = Path(found)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "python3"
    python = shlex.quote(Path(sys.executable).as_posix())
    if os.name == "nt":
        # Windows Python emits CRLF; POSIX command substitution must see LF.
        shim.write_bytes(("#!/bin/bash\nset -o pipefail\n" + python
                          + ' "$@" | sed \'s/\\r$//\'\n').encode())
    else:
        shim.write_bytes(("#!/bin/sh\nexec " + python + ' "$@"\n').encode())
    shim.chmod(0o755)

    def run(script, args=(), extra_env=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1",
                   TAR_OPTIONS="--force-local")
        env.update(extra_env or {})
        return subprocess.run(
            [str(bash), "--noprofile", "--norc", "-c",
             'PATH="$(cd "$1" && pwd):$PATH"; export PATH; shift; exec sh "$@"',
             "installer-test", bindir.as_posix(), script.as_posix(), *map(str, args)],
            env=env, capture_output=True, text=True, timeout=45)
    return bindir, run


def test_archive_contains_installer_sources_from_the_frozen_release(tmp_path):
    archive = tmp_path / "fixed.tgz"
    builder().write_candidate_archive(RELEASE, archive)
    with tarfile.open(archive) as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        assert "pynq_overlay/sw/install.sh" in members
        assert "sdk/src/sincos_q15.h" in members
        assert "rf_overlay/sw/rf-daemon.service" in members
        for name in ["install.sh", "ui_model.py", "rf_qam16_receiver.py"]:
            assert tar.extractfile("release/" + name).read() == (RELEASE / name).read_bytes()
        assert tar.extractfile("pynq_overlay/sw/install.sh").read() == (RELEASE / "install.sh").read_bytes()


@pytest.mark.parametrize("fixed", [False, True])
def test_real_base_prefix_checks_layout_before_any_service_stop(tmp_path, shell, fixed):
    stage = tmp_path / "stage"
    stage.mkdir()
    archive = stage / "candidate.tgz"
    if fixed:
        builder().write_candidate_archive(RELEASE, archive)
    else:
        shutil.copyfile(OLD_ARCHIVE, archive)
    dest = tmp_path / "dest"
    (dest / "releases").mkdir(parents=True)
    # Only board state is replaced; run real extract/verify/layout checks.
    (stage / "install_state.py").write_text("# no board in this test\n", encoding="utf-8")
    text = BASE.read_text(encoding="utf-8").split("cp /etc/systemd/system/pynq-status.service", 1)[0]
    text = text.replace("dest=/home/xilinx/overlays/awg_merged", "dest=" + shlex.quote(dest.as_posix()))
    text = text.replace('case "$stage" in /tmp/rf-teaching-install-*)', 'case "$stage" in *)')
    text = text.replace('old=$(readlink -f "$dest/current")', 'old="$dest/releases/$parent"')
    text += "\nprintf 'PAST_LAYOUT_CHECK\\n'\n"
    script = tmp_path / "prefix.sh"
    script.write_bytes(text.encode())
    _, run = shell
    result = run(script, [stage.as_posix(), ID, PARENT, digest(archive)])
    if fixed:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PAST_LAYOUT_CHECK" in result.stdout
    else:
        assert result.returncode != 0, "broken release-only archive reached the service-stop boundary"
        assert "INSTALL_LAYOUT" in result.stderr
        assert "PAST_LAYOUT_CHECK" not in result.stdout


def test_actual_inner_installer_copies_and_verifies_all_frozen_files(tmp_path, shell):
    archive = tmp_path / "fixed.tgz"
    builder().write_candidate_archive(RELEASE, archive)
    tree = tmp_path / "unpacked"
    tree.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tree)
    dest = tmp_path / "installed"
    # Run the actual copy + manifest verification, stopping before activation.
    # The executed copy changes only board paths/SRC; the attested install.sh
    # copied into the sandbox remains the original frozen file.
    text = (tree / "pynq_overlay/sw/install.sh").read_text(encoding="utf-8")
    text = text.split('chmod -R a-w "$STAGE"', 1)[0]
    text = text.replace('/home/xilinx/overlays/awg_merged', dest.as_posix())
    text = text.replace('SRC=$(cd "$(dirname "$0")" && pwd)',
                        "SRC=" + shlex.quote((tree / "pynq_overlay/sw").as_posix()))
    text += '\ntrap - EXIT INT TERM\nprintf "COPIED_RELEASE=%s\\n" "$STAGE"\n'
    script = tmp_path / "inner-sandbox.sh"
    script.write_bytes(text.encode())
    bindir, run = shell
    systemctl = bindir / "systemctl"
    systemctl.write_bytes(b'#!/bin/sh\n[ "$1" = is-active ] && exit 1\nexit 99\n')
    systemctl.chmod(0o755)
    result = run(script, extra_env={"LEDBTN_SOURCE": (tree / "release").as_posix()})
    assert result.returncode == 0, result.stdout + result.stderr
    stages = list((dest / "releases").glob(".staging.*"))
    assert len(stages) == 1
    for original in RELEASE.iterdir():
        assert (stages[0] / original.name).read_bytes() == original.read_bytes(), original.name
