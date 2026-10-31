"""Exercise the installed panel, rather than imports from the source checkout."""
import ast
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import merged_release as R

ROOT = Path(__file__).resolve().parents[3]
SW = ROOT / "pynq_overlay/sw"


def test_release_map_covers_transitive_panel_imports_and_existing_sources():
    # The tree is the bpsk-capable state (guarded engine imports), so the closure
    # must be covered by the largest contract; per-contract isolation (legacy/rx/cnn
    # stay bpsk-free) is verified in test_rf_bpsk_release.py.
    # Headroom is the largest map: it inherits environment and adds the research
    # module the panel imports for the research summary.  "Largest" is asserted,
    # not assumed -- a map that stopped being a superset would break the closure
    # this test exists to protect.
    sources = R.software_sources("cnn-bpsk-headroom-v1")
    assert set(sources) > set(R.software_sources("cnn-bpsk-environment-v1"))
    pending = ["ui_view", "rf_status_worker", "pynq_status"]
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = SW / (name + ".py")
        assert path.name in sources, path.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            # QPSK/RRC/QAM16 pipelines are function-local, contract-guarded
            # imports added by additive contracts.  They must not be forced into
            # the headroom/legacy map or change an old release identity.
            guarded = {"rf_qpsk_pipeline", "rf_rrc_pipeline", "rf_rrc_receiver",
                       "rf_qam16_pipeline", "rf_qam16_receiver"}
            pending.extend(m.split(".")[0] for m in modules
                           if (SW / (m.split(".")[0] + ".py")).is_file()
                           and m.split(".")[0] not in guarded)
    for name, relative in sources.items():
        assert (ROOT / relative).is_file(), name


def _panel_tree(tmp_path):
    for path in SW.glob("*.py"):
        shutil.copyfile(str(path), str(tmp_path / path.name))
    shutil.copyfile(str(ROOT / "sdk/src/sincos_q15.h"), str(tmp_path / "sincos_q15.h"))
    return tmp_path


@pytest.mark.parametrize("missing", ["rf_iq_signal.py", "ui_units.py"])
def test_isolated_panel_check_rejects_missing_dependency(tmp_path, missing):
    tree = _panel_tree(tmp_path)
    (tree / missing).unlink()
    with pytest.raises(ValueError, match=missing[:-3]):
        R.verify_panel_imports(tree)


def test_isolated_panel_check_accepts_complete_tree(tmp_path):
    R.verify_panel_imports(_panel_tree(tmp_path))


def test_install_checks_panel_before_publishing():
    text = (SW / "install.sh").read_text(encoding="utf-8")
    assert "--check-panel" in text
    assert text.index("--check-panel") < text.index('mv "$STAGE" "$RELEASE"')


@pytest.mark.parametrize("name", ["install.sh", "pynq-status.service"])
def test_linux_deploy_files_have_lf_endings(name):
    assert b"\r" not in (SW / name).read_bytes()


def test_startup_requires_every_attested_software_file(tmp_path, monkeypatch):
    import pynq_status as daemon
    release = tmp_path / "releases" / "test-release"
    release.mkdir(parents=True)
    # Avoid symlink creation privileges on Windows; resolution still returns this
    # explicitly pinned directory, as it would through `current` on the board.
    monkeypatch.setattr(daemon, "RELEASE_ROOT", tmp_path)
    original = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda p, **kw:
                        release if p == tmp_path / "current" else original(p, **kw))
    calls = []
    def verify(path, **kwargs):
        calls.append((path, kwargs))
        return {"release_id": release.name}
    monkeypatch.setattr(daemon.merged_release, "verify_release", verify)
    assert daemon.resolve_verified_release()[0] == release
    assert calls == [(release, {"require_software": True})]



def test_qpsk_modules_stay_out_of_existing_maps_and_new_contract_is_additive():
    old = R.software_sources("cnn-bpsk-modrec4-v1")
    new = R.software_sources("cnn-bpsk-modrec4-qpsk-v1")
    headroom = R.software_sources("cnn-bpsk-headroom-v1")
    qpsk = {"rf_modem_result.py", "rf_qpsk_receiver.py", "rf_qpsk_pipeline.py"}
    assert len(old) == 55 and len(new) == 58
    assert set(old) < set(new) and set(new) - set(old) == qpsk
    assert qpsk.isdisjoint(headroom)
    assert "cnn-bpsk-modrec4-qpsk-v1" in R.SOFTWARE_CONTRACTS
    assert "cnn-bpsk-modrec4-v1" in R.SOFTWARE_CONTRACTS


def test_rrc_contract_is_additive_60_and_keeps_old_maps():
    old = R.software_sources("cnn-bpsk-modrec4-qpsk-v1")
    new = R.software_sources("cnn-bpsk-modrec4-qpsk-rrc-v1")
    headroom = R.software_sources("cnn-bpsk-headroom-v1")
    rrc = {"rf_rrc_receiver.py", "rf_rrc_pipeline.py"}
    assert len(old) == 58 and len(new) == 60
    assert set(old) < set(new) and set(new) - set(old) == rrc
    assert rrc.isdisjoint(headroom)
    assert "cnn-bpsk-modrec4-qpsk-rrc-v1" in R.SOFTWARE_CONTRACTS
    assert len(R.software_sources("cnn-bpsk-modrec4-v1")) == 55
