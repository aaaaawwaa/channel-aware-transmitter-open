"""Versioned release closure and optional, data-only model artifacts."""
import copy
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pynq_overlay.sw import merged_release as R
from scripts.package_merged_overlay import package_release

ROOT = Path(__file__).resolve().parents[3]
PARENT = ROOT / "evidence/2026-10-01-rf-build-hmi/package-safe/releases/56d0d0d4bd36d4e22d1c030926917a16"
SHAPES = {"conv1.weight": [8, 2, 7], "conv1.bias": [8],
          "conv2.weight": [16, 8, 5], "conv2.bias": [16],
          "conv3.weight": [16, 16, 3], "conv3.bias": [16],
          "head.weight": [6, 16], "head.bias": [6]}
CLASSES = ["TONE", "BPSK", "QPSK", "QAM16", "QAM64", "OTHER"]


def _write(path, value):
    path.write_text(json.dumps(value, allow_nan=True), encoding="utf-8")


def _model(tree):
    model = {"schema_version": 1, "network": "tiny-iq-cnn-v1",
             "preprocessing": "dc-complex-rms-v1", "input_shape": [2, 4096],
             "classes": CLASSES, "training_manifest_sha256": "1" * 64,
             "evaluation_manifest_sha256": "2" * 64,
             "supported_domain": {"input_fs_hz": 60000000, "decim": [1],
                                  "symbol_rates_hz": [2000000, 3000000, 5000000],
                                  "shaping": ["RECT", "RRC0.35"]},
             "thresholds": {"max_softmax": 0.8, "margin": 0.2, "min_complex_rms": 1.0},
             "weights": {name: {"shape": shape, "values": [0.0] * math.prod(shape)}
                         for name, shape in SHAPES.items()}}
    _write(tree / "rf_model.json", model)
    evaluation = {"schema_version": 1, "model_sha256": R.sha256_file(tree / "rf_model.json"),
                  "training_manifest_sha256": "1" * 64,
                  "evaluation_manifest_sha256": "2" * 64,
                  "supported_domain": model["supported_domain"],
                  "split": "test", "origin": "cabled", "macro_f1": 0.95,
                  "class_recall": {name: 0.9 for name in CLASSES[:-1]},
                  "unknown_rejection": 0.95, "off_rejection": 0.95,
                  "class_sessions": {name: 3 for name in CLASSES},
                  "class_frames": {name: 30 for name in CLASSES},
                  "off_sessions": 3, "off_frames": 30}
    _write(tree / "rf_model_evaluation.json", evaluation)
    return {"schema_version": 1, "status": "experimental",
            "model_file": "rf_model.json", "model_sha256": R.sha256_file(tree / "rf_model.json"),
            "evaluation_file": "rf_model_evaluation.json",
            "evaluation_sha256": R.sha256_file(tree / "rf_model_evaluation.json")}


def _release(tree, contract="rx-v1", artifact=False):
    tree.mkdir(exist_ok=True)
    (tree / R.BIT_NAME).write_bytes(b"verified bit")
    (tree / R.HWH_NAME).write_text("<SYSTEM>" + "".join(
        '<MODULE INSTANCE="%s"><PARAMETER NAME="C_BASEADDR" VALUE="%s"/></MODULE>' % item
        for item in R.ADDRESS_PINS.items()) + "</SYSTEM>", encoding="utf-8")
    (tree / R.DAEMON_NAME).write_bytes(b"daemon")
    software = {}
    for name in R.software_sources(contract):
        (tree / name).write_bytes(b"source contract")
        software[name] = R.sha256_file(tree / name)
    receipt = {"schema_version": 1, "build_id": "test-build",
               "bit_sha256": R.sha256_file(tree / R.BIT_NAME),
               "hwh_sha256": R.sha256_file(tree / R.HWH_NAME),
               "software": software, "software_contract": contract}
    if artifact:
        receipt["model_artifact"] = _model(tree)
    manifest = dict(receipt, daemon_version="1.0.0", protocol_version=1,
                    bit_file=R.BIT_NAME, hwh_file=R.HWH_NAME, daemon_file=R.DAEMON_NAME,
                    daemon_sha256=R.sha256_file(tree / R.DAEMON_NAME),
                    address_contract=R.parse_hwh_addresses(tree / R.HWH_NAME))
    _save_identity(tree, manifest, receipt)
    return manifest, receipt


def _save_identity(tree, manifest, receipt):
    manifest["release_id"] = R.release_identity(
        manifest["build_id"], manifest["bit_sha256"], manifest["hwh_sha256"],
        manifest["daemon_version"], manifest["protocol_version"], manifest["daemon_sha256"],
        manifest.get("software"), software_contract=manifest.get("software_contract"),
        model_artifact=manifest.get("model_artifact"))
    _write(tree / R.MANIFEST_NAME, manifest)
    _write(tree / "merged_build_receipt.json", receipt)


def _rehash_artifact(tree, manifest, receipt):
    artifact = manifest["model_artifact"]
    artifact["model_sha256"] = R.sha256_file(tree / "rf_model.json")
    artifact["evaluation_sha256"] = R.sha256_file(tree / "rf_model_evaluation.json")
    receipt["model_artifact"] = copy.deepcopy(artifact)
    _save_identity(tree, manifest, receipt)


def _set_model_status(tree, status):
    path = tree / "rf_model.json"
    model = json.loads(path.read_text())
    model["status"] = status
    _write(path, model)
    evaluation_path = tree / "rf_model_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    evaluation["model_sha256"] = R.sha256_file(path)
    _write(evaluation_path, evaluation)


def test_verified_legacy_parent_retains_identity_and_full_contract():
    manifest = R.verify_release(PARENT, require_software=True)
    assert manifest["release_id"] == PARENT.name
    assert set(manifest["software"]) == set(R.software_sources("legacy-v1"))


@pytest.mark.parametrize("contract,missing", [("legacy-v1", "ui_units.py"),
    ("rx-v1", "rf_rx_types.py"), ("rx-v1", "rf_rx_analysis.py"),
    ("cnn-v1", "rf_cnn_runtime.py")])
def test_software_contract_rejects_self_consistent_omitted_module(tmp_path, contract, missing):
    manifest, receipt = _release(tmp_path, contract)
    del manifest["software"][missing]
    (tmp_path / missing).unlink()
    _save_identity(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="software contract"):
        R.verify_release(tmp_path, require_software=True)


def test_rx_contract_cannot_be_implicitly_legacy(tmp_path):
    manifest, receipt = _release(tmp_path)
    del manifest["software_contract"]
    del receipt["software_contract"]
    _save_identity(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="software contract"):
        R.verify_release(tmp_path, require_software=True)


def test_no_model_rx_release_imports_and_has_no_model_capability(tmp_path):
    manifest, receipt = _release(tmp_path)
    for name, relative in R.software_sources("rx-v1").items():
        shutil.copyfile(ROOT / relative, tmp_path / name)
        manifest["software"][name] = R.sha256_file(tmp_path / name)
    _save_identity(tmp_path, manifest, receipt)
    R.verify_release(tmp_path, require_software=True)
    R.verify_panel_imports(tmp_path)
    assert not R.model_capability(manifest)
    assert not (tmp_path / "rf_cnn_runtime.py").exists()
    script = ("import sys,site; sys.path.append(site.getusersitepackages()); "
              "sys.path.insert(0,sys.argv[1]); import ui_model; "
              "app=ui_model.UiApp(); assert not app.capabilities.model; "
              "assert not app.caps['model']")
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(tmp_path)],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_installer_carries_the_complete_rx_map():
    text = (ROOT / "pynq_overlay/sw/install.sh").read_text(encoding="utf-8")
    active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for name in R.software_sources("rx-v1"):
        assert name in active, "installer omits attested software: " + name


def test_installer_copy_routes_produce_an_importable_rx_tree(tmp_path):
    """Stage the installer's copy routes, not a glob of all checkout Python files."""
    text = (ROOT / "pynq_overlay/sw/install.sh").read_text(encoding="utf-8")
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
    # The sidecar has a documented fallback when the upload does not flatten it.
    assert 'install -m 644 "$SIDECAR_UNIT" "$STAGE/$SIDECAR_UNIT_NAME"' in text
    assert 'SIDECAR_UNIT_NAME=rf-daemon.service' in text
    routes["rf-daemon.service"] = "rf_overlay/sw/rf-daemon.service"
    for name, relative in R.software_sources("rx-v1").items():
        assert name in routes, "missing installer destination: " + name
        source = (ROOT / routes[name]).resolve()
        assert source == (ROOT / relative).resolve(), name
        shutil.copyfile(source, tmp_path / name)
    R.verify_panel_imports(tmp_path)


def test_artifact_identity_changes_release_and_experimental_stays_disabled(tmp_path):
    manifest, receipt = _release(tmp_path, artifact=True)
    initial = manifest["release_id"]
    R.verify_release(tmp_path, require_software=True)
    assert not R.model_capability(manifest)
    evaluation = json.loads((tmp_path / "rf_model_evaluation.json").read_text())
    evaluation["macro_f1"] = 0.96
    _write(tmp_path / "rf_model_evaluation.json", evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    assert manifest["release_id"] != initial
    R.verify_release(tmp_path, require_software=True)


@pytest.mark.parametrize("filename", ["rf_model.json", "rf_model_evaluation.json"])
def test_declared_artifact_missing_or_tampered_is_rejected(tmp_path, filename):
    _release(tmp_path, artifact=True)
    path = tmp_path / filename
    old = path.read_bytes()
    path.write_bytes(old + b" ")
    with pytest.raises(ValueError, match="artifact.*hash"):
        R.verify_release(tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="artifact.*missing"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("defect", ["schema", "shape", "nan", "classes", "thresholds", "domain", "bool_shape"])
def test_self_consistently_hashed_invalid_model_is_rejected(tmp_path, defect):
    manifest, receipt = _release(tmp_path, artifact=True)
    path = tmp_path / "rf_model.json"
    model = json.loads(path.read_text())
    if defect == "schema": model["schema_version"] = 2
    elif defect == "shape": model["weights"]["conv1.weight"]["shape"] = [8, 2, 8]
    elif defect == "nan": model["weights"]["conv1.bias"]["values"][0] = float("nan")
    elif defect == "classes": model["classes"].reverse()
    elif defect == "thresholds": model["thresholds"]["margin"] = 2
    elif defect == "bool_shape": model["input_shape"] = [2, 4096.0]
    else: model["supported_domain"] = {}
    _write(path, model)
    evaluation_path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    evaluation["model_sha256"] = R.sha256_file(path)
    _write(evaluation_path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="model"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("defect", ["schema", "model", "training", "evaluation", "split", "domain"])
def test_evaluation_identity_is_checked_beyond_its_file_hash(tmp_path, defect):
    manifest, receipt = _release(tmp_path, artifact=True)
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    key = {"schema": "schema_version", "model": "model_sha256",
           "training": "training_manifest_sha256", "evaluation": "evaluation_manifest_sha256",
           "split": "split", "domain": "supported_domain"}[defect]
    evaluation[key] = 2 if defect == "schema" else "wrong"
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="evaluation"):
        R.verify_release(tmp_path)


def test_undeclared_model_file_is_rejected(tmp_path):
    _release(tmp_path)
    _model(tmp_path)
    with pytest.raises(ValueError, match="undeclared model"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("defect", ["contract", "metrics", "sessions", "frames"])
def test_production_model_requires_cnn_and_independent_acceptance(tmp_path, defect):
    manifest, receipt = _release(tmp_path, "rx-v1" if defect == "contract" else "cnn-v1", True)
    manifest["model_artifact"]["status"] = "production"
    _set_model_status(tmp_path, "production")
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    if defect == "metrics": evaluation["macro_f1"] = 0.89
    elif defect == "sessions": evaluation["class_sessions"]["TONE"] = 2
    elif defect == "frames": evaluation["off_frames"] = 29
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="production model"):
        R.verify_release(tmp_path)


def test_package_copies_declared_artifacts_and_explicit_contract(tmp_path):
    source = tmp_path / "source"
    manifest, receipt = _release(source, artifact=True)
    for stem, old, filename in (("bit", R.BIT_NAME, "host_bd_wrapper.bit"),
                                ("hwh", R.HWH_NAME, "host_bd.hwh")):
        shutil.copyfile(source / old, source / filename)
        receipt[stem + "_source"] = str((source / filename).resolve())
    receipt["software"] = R.software_digests(ROOT, contract="rx-v1")
    path = source / "merged_build_receipt.json"
    _write(path, receipt)
    release = package_release(path, tmp_path / "output", "1.0.0", 1, source / R.DAEMON_NAME)
    checked = R.verify_release(release, require_software=True)
    assert checked["software_contract"] == "rx-v1"
    assert checked["model_artifact"] == manifest["model_artifact"]
    assert (release / "rf_model.json").is_file()


def test_installer_uses_manifest_for_optional_cnn_and_model_files():
    text = (ROOT / "pynq_overlay/sw/install.sh").read_text(encoding="utf-8")
    assert "--artifact-files" in text
    assert "rf_cnn_runtime.py" in text
    assert 'install -m 644 "$OVERLAY_SRC/$artifact" "$STAGE/$artifact"' in text


def test_verified_artifact_cli_lists_exactly_the_declared_files(tmp_path, capsys):
    _release(tmp_path, artifact=True)
    assert R.main([str(tmp_path), "--artifact-files"]) == 0
    assert capsys.readouterr().out.splitlines() == ["rf_model.json", "rf_model_evaluation.json"]


def test_production_model_accepts_complete_cnn_contract_and_evaluation(tmp_path):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    manifest["model_artifact"]["status"] = "production"
    _set_model_status(tmp_path, "production")
    _rehash_artifact(tmp_path, manifest, receipt)
    checked = R.verify_release(tmp_path, require_software=True)
    assert R.model_capability(checked)


def test_oversized_model_artifact_is_rejected(tmp_path):
    manifest, receipt = _release(tmp_path, artifact=True)
    path = tmp_path / "rf_model.json"
    path.write_bytes(path.read_bytes() + b" " * (128 * 1024))
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="128 KiB"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("name", SHAPES)
@pytest.mark.parametrize("dimension", ["float", "bool", "wrong"])
def test_weight_shape_requires_exact_integer_dimensions(tmp_path, name, dimension):
    manifest, receipt = _release(tmp_path, artifact=True)
    path = tmp_path / "rf_model.json"
    model = json.loads(path.read_text())
    shape = model["weights"][name]["shape"]
    shape[0] = {"float": float(shape[0]), "bool": True, "wrong": shape[0] + 1}[dimension]
    _write(path, model)
    evaluation_path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    evaluation["model_sha256"] = R.sha256_file(path)
    _write(evaluation_path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="model weight shape"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("status", ["experimental", "production"])
@pytest.mark.parametrize("key", ["macro_f1", "unknown_rejection", "off_rejection", "class_recall.TONE"])
@pytest.mark.parametrize("defect", ["missing", "negative", "nan", "infinite", "above_one", "bool", "string"])
def test_evaluation_metrics_require_complete_finite_probabilities(tmp_path, status, key, defect):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    manifest["model_artifact"]["status"] = status
    _set_model_status(tmp_path, status)
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    if "." in key:
        group, key = key.split(".")
        target = evaluation[group]
    else:
        target = evaluation
    if defect == "missing":
        del target[key]
    else:
        target[key] = {"negative": -0.01, "nan": float("nan"), "infinite": float("inf"),
                       "above_one": 1.01, "bool": True, "string": "0.95"}[defect]
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="evaluation|production model"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("status", ["experimental", "production"])
@pytest.mark.parametrize("key", ["class_sessions.TONE", "class_frames.OTHER", "off_sessions", "off_frames"])
@pytest.mark.parametrize("defect", ["missing", "negative", "float", "bool", "string"])
def test_evaluation_counts_require_complete_nonnegative_integers(tmp_path, status, key, defect):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    manifest["model_artifact"]["status"] = status
    _set_model_status(tmp_path, status)
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    if "." in key:
        group, key = key.split(".")
        target = evaluation[group]
    else:
        target = evaluation
    if defect == "missing":
        del target[key]
    else:
        target[key] = {"negative": -1, "float": 30.0, "bool": True, "string": "30"}[defect]
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="evaluation|production model"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("status", ["experimental", "production"])
@pytest.mark.parametrize("key", ["class_recall", "class_sessions", "class_frames"])
@pytest.mark.parametrize("defect", ["missing", "list", "extra_class"])
def test_evaluation_class_maps_require_expected_classes(tmp_path, status, key, defect):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    manifest["model_artifact"]["status"] = status
    _set_model_status(tmp_path, status)
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    if defect == "missing":
        del evaluation[key]
    elif defect == "list":
        evaluation[key] = []
    else:
        evaluation[key]["UNSUPPORTED"] = 1
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="evaluation|production model"):
        R.verify_release(tmp_path)


@pytest.mark.parametrize("contract", ["rx-v1", "cnn-v1"])
def test_experimental_model_accepts_complete_below_gate_evaluation_without_capability(tmp_path, contract):
    manifest, receipt = _release(tmp_path, contract, True)
    path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(path.read_text())
    evaluation.update(origin="synthetic", macro_f1=0.1, unknown_rejection=0.2, off_rejection=0.3,
                      class_recall={name: 0.0 for name in CLASSES[:-1]},
                      class_sessions={name: 1 for name in CLASSES},
                      class_frames={name: 2 for name in CLASSES}, off_sessions=1, off_frames=2)
    _write(path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    checked = R.verify_release(tmp_path, require_software=True)
    assert not R.model_capability(checked)


@pytest.mark.parametrize("defect", ["decim2", "decim_duplicate", "float_fs",
    "rate_duplicate", "shaping_duplicate", "weight_extra"])
def test_release_rejects_models_the_runtime_cannot_load(tmp_path, defect):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    path = tmp_path / "rf_model.json"
    model = json.loads(path.read_text())
    domain = model["supported_domain"]
    if defect == "decim2": domain["decim"] = [2]
    elif defect == "decim_duplicate": domain["decim"] = [1, 1]
    elif defect == "float_fs": domain["input_fs_hz"] = 60000000.0
    elif defect == "rate_duplicate": domain["symbol_rates_hz"] = [2000000, 2000000]
    elif defect == "shaping_duplicate": domain["shaping"] = ["RECT", "RECT"]
    else: model["weights"]["head.bias"]["unused"] = []
    _write(path, model)
    evaluation_path = tmp_path / "rf_model_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    evaluation.update(model_sha256=R.sha256_file(path), supported_domain=domain)
    _write(evaluation_path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    from pynq_overlay.sw.rf_cnn_runtime import load_model
    with pytest.raises(ValueError): load_model(path, manifest["model_artifact"]["model_sha256"])
    with pytest.raises(ValueError, match="model"): R.verify_release(tmp_path)


@pytest.mark.parametrize("status", [None, "invalid", True, "production"])
def test_release_rejects_invalid_or_mismatched_model_status(tmp_path, status):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    _set_model_status(tmp_path, status)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="model.*status"): R.verify_release(tmp_path)


@pytest.mark.parametrize("status", ["missing", "experimental"])
def test_production_declaration_cannot_promote_experimental_model(tmp_path, status):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    manifest["model_artifact"]["status"] = "production"
    if status != "missing": _set_model_status(tmp_path, status)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="model.*status"): R.verify_release(tmp_path)


@pytest.mark.parametrize("filename", ["rf_model.json", "rf_model_evaluation.json"])
def test_release_rejects_duplicate_json_fields(tmp_path, filename):
    manifest, receipt = _release(tmp_path, "cnn-v1", True)
    path = tmp_path / filename
    path.write_text(path.read_text().replace('{', '{"schema_version":1,', 1), encoding="utf-8")
    if filename == "rf_model.json":
        evaluation_path = tmp_path / "rf_model_evaluation.json"
        evaluation = json.loads(evaluation_path.read_text())
        evaluation["model_sha256"] = R.sha256_file(path)
        _write(evaluation_path, evaluation)
    _rehash_artifact(tmp_path, manifest, receipt)
    with pytest.raises(ValueError, match="JSON|duplicate"): R.verify_release(tmp_path)
