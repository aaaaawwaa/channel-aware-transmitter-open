"""cnn-bpsk-v1 software contract: complete module set, isolation, model qualification.

The BPSK engine (receiver + pure pipeline) must travel with the panel and worker under
the new contract only; legacy/rx/cnn releases stay verifiable without them, and the
installer must stage them from the tree (never from an unhashed glob).
"""
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pynq_overlay.sw import merged_release as R
from test_rf_model_release import _release, _set_model_status, _rehash_artifact

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("missing", ["rf_bpsk_receiver.py", "rf_bpsk_pipeline.py"])
def test_bpsk_contract_requires_complete_module_set(tmp_path, missing):
    tree = tmp_path / "r"
    manifest, receipt = _release(tree, contract="cnn-bpsk-v1")
    assert "rf_bpsk_receiver.py" in manifest["software"]
    assert "rf_bpsk_pipeline.py" in manifest["software"]
    (tree / missing).unlink()
    with pytest.raises((ValueError, OSError)):
        R.verify_release(tree, require_software=True)


def test_bpsk_contract_rejects_extra_undeclared_python(tmp_path):
    tree = tmp_path / "r"
    manifest, receipt = _release(tree, contract="cnn-bpsk-v1")
    (tree / "intruder.py").write_text("# unhashed extra module")
    with pytest.raises((ValueError, OSError)):
        R.verify_release(tree, require_software=True)


@pytest.mark.parametrize("contract", ["legacy-v1", "rx-v1", "cnn-v1"])
def test_older_contracts_stay_verifiable_without_bpsk_modules(tmp_path, contract):
    tree = tmp_path / "r"
    manifest, receipt = _release(tree, contract=contract)
    assert "rf_bpsk_receiver.py" not in manifest["software"]
    R.verify_release(tree, require_software=True)


def test_bpsk_release_passes_isolated_panel_import_with_real_sources(tmp_path):
    """New UI/worker imports the BPSK engine only when the contract ships it;
    the isolated panel import closure must work from the staged tree alone."""
    tree = tmp_path / "r"
    manifest, receipt = _release(tree, contract="cnn-bpsk-v1")
    for name, relative in R.software_sources("cnn-bpsk-v1").items():
        shutil.copyfile(ROOT / relative, tree / name)
        manifest["software"][name] = R.sha256_file(tree / name)
    from test_rf_model_release import _save_identity
    _save_identity(tree, manifest, receipt)
    R.verify_release(tree, require_software=True)
    R.verify_panel_imports(tree)
    script = ("import sys,site; sys.path.append(site.getusersitepackages()); "
              "sys.path.insert(0,sys.argv[1]); import rf_bpsk_pipeline; "
              "assert rf_bpsk_pipeline.freeze_demod_config is not None")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(tree)],
                            cwd=str(tree), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_production_model_qualifies_under_cnn_bpsk_contract(tmp_path):
    tree = tmp_path / "r"
    manifest, receipt = _release(tree, contract="cnn-bpsk-v1", artifact=True)
    manifest["model_artifact"]["status"] = "production"
    _set_model_status(tree, "production")
    _rehash_artifact(tree, manifest, receipt)
    checked = R.verify_release(tree, require_software=True)
    assert R.model_capability(checked)
    checked["software_contract"] = "rx-v1"
    assert not R.model_capability(checked)


def test_installer_carries_and_stages_bpsk_modules(tmp_path):
    text = (ROOT / "pynq_overlay/sw/install.sh").read_text(encoding="utf-8")
    for name in R.software_sources("cnn-bpsk-v1"):
        assert name in text, "installer omits attested software: " + name
    routes = {}
    for match in re.finditer(r"for module in (.*?); do\s*(.*?)\bdone", text, re.S):
        names, body = match.groups()
        command = shlex.split(body.strip())
        assert command[:3] == ["install", "-m", "644"]
        for name in shlex.split(names.replace("\\\n", " ")):
            routes[command[4].replace("$module", name).replace("$STAGE/", "")] = (
                command[3].replace("$module", name).replace("$SRC", "pynq_overlay/sw"))
    for source, destination in re.findall(r'install -m 644 "(\$SRC[^"\n]+)" "\$STAGE/([^"\n]+)"', text):
        if "$module" not in source:
            routes[destination] = source.replace("$SRC", "pynq_overlay/sw")
    routes["rf-daemon.service"] = "rf_overlay/sw/rf-daemon.service"
    for name, relative in R.software_sources("cnn-bpsk-v1").items():
        assert name in routes, "missing installer destination: " + name
        source = (ROOT / routes[name]).resolve()
        assert source == (ROOT / relative).resolve(), name
        shutil.copyfile(source, tmp_path / name)
    R.verify_panel_imports(tmp_path)
