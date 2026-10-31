"""Verified identity for one immutable AWG/PYNQ/RF release."""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MANIFEST_NAME = "merged_release_manifest.json"
BIT_NAME = "awg_merged.bit"
HWH_NAME = "awg_merged.hwh"
DAEMON_NAME = "rf_daemon"
SOFTWARE_KEY = "software"
SOFTWARE_CONTRACT_KEY = "software_contract"
CURRENT_SOFTWARE_CONTRACT = "rx-v1"
MODEL_KEY = "model_artifact"
MODEL_FILE = "rf_model.json"
EVALUATION_FILE = "rf_model_evaluation.json"
MODREC4_MODEL_KEY = "modrec4_model"
MODREC4_ARTIFACT_FIELDS = {
    "schema_version", "status", "cnn_file", "cnn_sha256",
    "baseline_file", "baseline_sha256", "rule_file", "rule_sha256",
}
ADDRESS_PINS = {
    "axi_bram_ctrl_0": "0x40000000",
    "axi_dma_0": "0x40400000",
    "axi_gpio_fb": "0x41300000",
    "axi_gpio_led": "0x41310000",
    "axi_ctrl_0": "0x43000000",
    "pl_spi_0": "0x43010000",
    "axi_gpio_ctrl": "0x43020000",
    "axi_bram_ctrl_tx": "0x43030000",
    "axi_bram_ctrl_rx": "0x43040000",
}
# The software a release carries, as release-directory name -> path relative to the
# repository root.  This exists because `release_id` alone only attested bit/HWH/daemon:
# the panel Python, the installer and the systemd units were uploaded from the working
# tree at deploy time, so nothing tied a running release to the software it executes.
# The map is computed by the builder, carried in the receipt, folded into the release id
# (see release_identity), and re-checked by install.sh against the bytes it just copied.
# `tests/*` are flattened because install.sh installs them into the release root.
SOFTWARE_SOURCES = {
    "install.sh": "pynq_overlay/sw/install.sh",
    "merged_release.py": "pynq_overlay/sw/merged_release.py",
    "pynq-status.service": "pynq_overlay/sw/pynq-status.service",
    "rf-daemon.service": "rf_overlay/sw/rf-daemon.service",
    "pynq_status.py": "pynq_overlay/sw/pynq_status.py",
    "pynq_lcd.py": "pynq_overlay/sw/pynq_lcd.py",
    "pynq_lcd_ui.py": "pynq_overlay/sw/pynq_lcd_ui.py",
    "pynq_touch.py": "pynq_overlay/sw/pynq_touch.py",
    "touch_demo.py": "pynq_overlay/sw/touch_demo.py",
    "awg_ctl.py": "pynq_overlay/sw/awg_ctl.py",
    "awg_bridge.py": "pynq_overlay/sw/awg_bridge.py",
    "ui_model.py": "pynq_overlay/sw/ui_model.py",
    "ui_view.py": "pynq_overlay/sw/ui_view.py",
    "ui_units.py": "pynq_overlay/sw/ui_units.py",
    "rf_iq_signal.py": "pynq_overlay/sw/rf_iq_signal.py",
    "sincos_q15.h": "sdk/src/sincos_q15.h",
    "panel_preview.py": "pynq_overlay/sw/panel_preview.py",
    "glyph_check.py": "pynq_overlay/sw/glyph_check.py",
    "font_probe.py": "pynq_overlay/sw/font_probe.py",
    "p7_e2e.py": "pynq_overlay/sw/p7_e2e.py",
    "awg_ddr_pp.py": "pynq_overlay/sw/awg_ddr_pp.py",
    "awg_ddr_ring.py": "pynq_overlay/sw/awg_ddr_ring.py",
    "feeder_decision.py": "pynq_overlay/sw/feeder_decision.py",
    "ring_thread.py": "pynq_overlay/sw/ring_thread.py",
    "p5b_ringtest.py": "pynq_overlay/sw/p5b_ringtest.py",
    "rf_status_client.py": "pynq_overlay/sw/rf_status_client.py",
    "rf_status_worker.py": "pynq_overlay/sw/rf_status_worker.py",
    # The binary RX path's immutable frame types.  rf_status_client imports it, and
    # the panel imports rf_status_worker, so a release that installs the panel
    # without it cannot import at all (test_panel_release walks exactly that
    # closure).  verify_release checks the map a RECEIPT carries, not this dict, so
    # adding the entry does not make an older parent unverifiable.
    "rf_rx_types.py": "pynq_overlay/sw/rf_rx_types.py",
    # The pure IQ analysis.  Task 4 wires it into the panel: the worker imports it for
    # the freshness windows, the capture-time summarise() and the frozen handoff types,
    # so a release that installs the panel without it cannot import (test_panel_release
    # walks exactly that closure).  verify_release checks the map a RECEIPT carries, not
    # this dict, so the already verified parent 56d0d0d4 stays verifiable.  The versioned
    # legacy/RX-only/CNN contracts remain Task 5's job.
    "rf_rx_analysis.py": "pynq_overlay/sw/rf_rx_analysis.py",
    "rf_tx_waveform.py": "pynq_overlay/sw/rf_tx_waveform.py",
    "ddr_e2e.py": "pynq_overlay/sw/tests/ddr_e2e.py",
    "check_play_userbuf.py": "pynq_overlay/sw/tests/check_play_userbuf.py",
    "prove_play_read.py": "pynq_overlay/sw/tests/prove_play_read.py",
}
# File kinds the release directory must never carry unlisted: an extra .py/.sh/.service
# next to the attested set is exactly the "software nobody hashed" the map exists to stop.
_SOFTWARE_SUFFIXES = {".py", ".sh", ".service"}
_HEX = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOFTWARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
LEGACY_SOFTWARE_SOURCES = {name: path for name, path in SOFTWARE_SOURCES.items()
                           if name not in {"rf_rx_types.py", "rf_rx_analysis.py"}}
CNN_SOFTWARE_SOURCES = dict(SOFTWARE_SOURCES,
    **{"rf_cnn_runtime.py": "pynq_overlay/sw/rf_cnn_runtime.py",
       "rf_cnn_conv.so": "pynq_overlay/sw/rf_cnn_conv.so",
       "rf_cnn_conv.c": "pynq_overlay/sw/rf_cnn_conv.c"})
BPSK_SOFTWARE_SOURCES = dict(CNN_SOFTWARE_SOURCES,
    **{"rf_bpsk_receiver.py": "pynq_overlay/sw/rf_bpsk_receiver.py",
       "rf_bpsk_pipeline.py": "pynq_overlay/sw/rf_bpsk_pipeline.py"})
TEACHING_SOFTWARE_SOURCES = dict(BPSK_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_teaching_profile.py','rf_rx_constellation.py','rf_experiment_record.py')})
ENVIRONMENT_SOFTWARE_SOURCES = dict(TEACHING_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_environment.py', 'rf_experiment_scenarios.py', 'rf_environment_baseline.json')})
# HEADROOM-1 is additive: it inherits the environment map unchanged and adds the
# research plan/controller and its offline replay.  Membership of the older
# contracts is untouched, so the already verified candidates stay verifiable and
# a 1.0.3 / minor 5 release cannot claim the research contract by installing an
# extra file.
HEADROOM_SOFTWARE_SOURCES = dict(ENVIRONMENT_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_headroom.py', 'rf_headroom_replay.py')})
# MODREC4 is additive: it inherits the environment contract and adds the frozen
# 4-class adapter, its pure-NumPy runtime and the candidate full-path harness.
# The six-class model/contract, environment baseline and RF 46 dB are untouched.
MODREC4_SOFTWARE_SOURCES = dict(ENVIRONMENT_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_modrec4_adapter.py', 'rf_modrec4_input_domain.py',
        'rf_modrec4_fullpath.py',
        'rf_modrec4_baseline.py', 'rf_modrec4_features.py',
        'rf_modrec4_gate.py', 'rf_modrec4_iq.py',
        'rf_modrec4_numpy_runtime.py', 'rf_modrec4_predictor.py',
        'rf_modrec4_preprocess.py', 'rf_modrec4_spectral.py')})
# QPSK is a NEW additive contract, not an expansion of the already installed
# cnn-bpsk-modrec4-v1 map: a parent/receipt carrying the old 55-member map must
# remain byte-verifiable.  The three QPSK modules are only released together.
MODREC4_QPSK_SOFTWARE_SOURCES = dict(MODREC4_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_modem_result.py', 'rf_qpsk_receiver.py', 'rf_qpsk_pipeline.py')})
# RRC is another additive contract.  The installed 58-member QPSK contract and
# the original 55-member modrec4 contract remain byte-verifiable and unchanged;
# the RRC receiver/pipeline only travel together in the new 60-member map.
MODREC4_QPSK_RRC_SOFTWARE_SOURCES = dict(MODREC4_QPSK_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_rrc_receiver.py', 'rf_rrc_pipeline.py')})
# QAM16 is additive on top of the installed 60-member RRC contract.  The QAM16
# receiver and its explicit handoff travel together, so a release without the
# new contract cannot claim QAM16 capability by installing one stray file.
MODREC4_QPSK_RRC_QAM16_SOFTWARE_SOURCES = dict(MODREC4_QPSK_RRC_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_qam16_receiver.py', 'rf_qam16_pipeline.py')})
# QAM64 is additive on top of the installed 62-member QAM16 contract; the two
# modules and the new contract travel together.
MODREC4_QPSK_RRC_QAM16_QAM64_SOFTWARE_SOURCES = dict(
    MODREC4_QPSK_RRC_QAM16_SOFTWARE_SOURCES,
    **{name: 'pynq_overlay/sw/'+name for name in
       ('rf_qam64_receiver.py', 'rf_qam64_pipeline.py')})
SOFTWARE_CONTRACTS = {"legacy-v1": LEGACY_SOFTWARE_SOURCES,
                      "rx-v1": SOFTWARE_SOURCES, "cnn-v1": CNN_SOFTWARE_SOURCES,
                      "cnn-bpsk-v1": BPSK_SOFTWARE_SOURCES,
                      "cnn-bpsk-teaching-v1": TEACHING_SOFTWARE_SOURCES,
                      "cnn-bpsk-environment-v1": ENVIRONMENT_SOFTWARE_SOURCES,
                      "cnn-bpsk-headroom-v1": HEADROOM_SOFTWARE_SOURCES,
                      "cnn-bpsk-modrec4-v1": MODREC4_SOFTWARE_SOURCES,
                      "cnn-bpsk-modrec4-qpsk-v1": MODREC4_QPSK_SOFTWARE_SOURCES,
                      "cnn-bpsk-modrec4-qpsk-rrc-v1": MODREC4_QPSK_RRC_SOFTWARE_SOURCES,
                      "cnn-bpsk-modrec4-qpsk-rrc-qam16-v1": MODREC4_QPSK_RRC_QAM16_SOFTWARE_SOURCES,
                      "cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1":
                          MODREC4_QPSK_RRC_QAM16_QAM64_SOFTWARE_SOURCES}
# Contracts that carry the production model and the teaching/diagnostic stack.
# The research contract inherits all of it; it is listed explicitly rather than
# matched by prefix so a typo in a contract name cannot silently gain capability.
MODEL_CONTRACTS = ("cnn-v1", "cnn-bpsk-v1", "cnn-bpsk-teaching-v1",
                   "cnn-bpsk-environment-v1", "cnn-bpsk-headroom-v1",
                   "cnn-bpsk-modrec4-v1", "cnn-bpsk-modrec4-qpsk-v1",
                   "cnn-bpsk-modrec4-qpsk-rrc-v1",
                   "cnn-bpsk-modrec4-qpsk-rrc-qam16-v1",
                   "cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1")
MODEL_CLASSES = ["TONE", "BPSK", "QPSK", "QAM16", "QAM64", "OTHER"]
MODEL_SHAPES = {"conv1.weight": [8, 2, 7], "conv1.bias": [8],
                "conv2.weight": [16, 8, 5], "conv2.bias": [16],
                "conv3.weight": [16, 16, 3], "conv3.bias": [16],
                "head.weight": [6, 16], "head.bias": [6]}


def software_sources(contract=CURRENT_SOFTWARE_CONTRACT):
    if not isinstance(contract, str) or contract not in SOFTWARE_CONTRACTS:
        raise ValueError("unknown software contract")
    return dict(SOFTWARE_CONTRACTS[contract])


def validate_software_contract(software, contract=None):
    """Unversioned maps retain the original full legacy contract and identity."""
    validate_software_map(software)
    expected = software_sources("legacy-v1" if contract is None else contract)
    if set(software) != set(expected):
        raise ValueError("software contract mismatch: missing=%s extra=%s" % (
            sorted(set(expected) - set(software)), sorted(set(software) - set(expected))))
    return "legacy-v1" if contract is None else contract


def validate_model_declaration(artifact):
    if not isinstance(artifact, dict) or type(artifact.get("schema_version")) is not int or artifact["schema_version"] != 1:
        raise ValueError("invalid model artifact schema")
    if set(artifact) != {"schema_version", "status", "model_file", "model_sha256",
                         "evaluation_file", "evaluation_sha256"}:
        raise ValueError("invalid model artifact fields")
    if not isinstance(artifact["status"], str) or artifact["status"] not in {"experimental", "production"}:
        raise ValueError("invalid model artifact status")
    for stem, filename in (("model", MODEL_FILE), ("evaluation", EVALUATION_FILE)):
        if artifact[stem + "_file"] != filename:
            raise ValueError("invalid model artifact filename")
        digest = artifact[stem + "_sha256"]
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError("invalid model artifact hash")
    return artifact


def validate_modrec4_declaration(artifact):
    """Validate the additive 4-class model artifact declaration (not MODEL_KEY)."""
    if not isinstance(artifact, dict) or type(artifact.get("schema_version")) is not int \
            or artifact["schema_version"] != 1:
        raise ValueError("invalid modrec4 artifact schema")
    if set(artifact) != MODREC4_ARTIFACT_FIELDS:
        raise ValueError("invalid modrec4 artifact fields")
    if artifact.get("status") not in {"experimental", "production"}:
        raise ValueError("invalid modrec4 artifact status")
    for stem, filename in (("cnn", "modrec4_cnn_model.json"),
                           ("baseline", "modrec4_baseline_model.json"),
                           ("rule", "modrec4_rule_config.json")):
        if artifact.get(stem + "_file") != filename:
            raise ValueError("invalid modrec4 artifact filename")
        digest = artifact.get(stem + "_sha256")
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError("invalid modrec4 artifact hash")
    return artifact


def verify_modrec4_artifact(directory, artifact):
    """Verify the optional additive 4-class model files by declared hash."""
    if artifact is None:
        return
    validate_modrec4_declaration(artifact)
    directory = Path(directory)
    for stem in ("cnn", "baseline", "rule"):
        path = directory / artifact[stem + "_file"]
        if not path.is_file():
            raise ValueError("modrec4 artifact file missing: " + path.name)
        if sha256_file(path) != artifact[stem + "_sha256"]:
            raise ValueError("modrec4 artifact hash mismatch: " + path.name)


def model_capability(manifest):
    artifact = manifest.get(MODEL_KEY)
    return bool(manifest.get(SOFTWARE_CONTRACT_KEY) in MODEL_CONTRACTS
                and isinstance(artifact, dict) and artifact.get("status") == "production")


def _unique_json_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def _bounded_json(path, what):
    if not path.is_file():
        raise ValueError("model artifact missing: " + path.name)
    if path.stat().st_size > 128 * 1024:
        raise ValueError(what + " exceeds 128 KiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("invalid " + what + " JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("invalid " + what + " schema")
    return value


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def verify_model_artifact(directory, artifact, contract):
    """Validate data-only fixed-network JSON; this does not perform inference."""
    directory = Path(directory)
    if artifact is None:
        if any((directory / name).exists() for name in (MODEL_FILE, EVALUATION_FILE)):
            raise ValueError("undeclared model artifact")
        return
    validate_model_declaration(artifact)
    documents = {}
    for stem in ("model", "evaluation"):
        path = directory / artifact[stem + "_file"]
        documents[stem] = _bounded_json(path, stem)
        if sha256_file(path) != artifact[stem + "_sha256"]:
            raise ValueError("model artifact hash mismatch: " + path.name)
    model, evaluation = documents["model"], documents["evaluation"]
    # Missing status predates runtime export and means experimental only.
    # A declaration may not promote such a model to production.
    status = model.get("status", "experimental")
    if (not isinstance(status, str) or status not in {"experimental", "production"}
            or status != artifact["status"]):
        raise ValueError("invalid model status or artifact status mismatch")
    if (type(model.get("schema_version")) is not int or model["schema_version"] != 1
            or model.get("network") != "tiny-iq-cnn-v1"
            or model.get("preprocessing") != "dc-complex-rms-v1"
            or model.get("input_shape") != [2, 4096]
            or any(type(x) is not int for x in model.get("input_shape", []))
            or model.get("classes") != MODEL_CLASSES):
        raise ValueError("invalid model schema/network/preprocessing/classes")
    for key in ("training_manifest_sha256", "evaluation_manifest_sha256"):
        if not isinstance(model.get(key), str) or not _HEX.fullmatch(model[key]):
            raise ValueError("invalid model manifest identity")
    weights = model.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(MODEL_SHAPES):
        raise ValueError("invalid model weights")
    for name, shape in MODEL_SHAPES.items():
        tensor = weights[name]
        if (not isinstance(tensor, dict) or set(tensor) != {"shape", "values"}
                or not isinstance(tensor.get("shape"), list)
                or any(type(dimension) is not int for dimension in tensor["shape"])
                or tensor["shape"] != shape):
            raise ValueError("invalid model weight shape: " + name)
        values = tensor.get("values")
        if (not isinstance(values, list) or len(values) != math.prod(shape)
                or any(not _finite_number(value) or abs(value) > 3.402823466e38 for value in values)):
            raise ValueError("invalid model weight values: " + name)
    thresholds = model.get("thresholds")
    if (not isinstance(thresholds, dict)
            or set(thresholds) != {"max_softmax", "margin", "min_complex_rms"}
            or any(not _finite_number(value) for value in thresholds.values())
            or not 0 <= thresholds["max_softmax"] <= 1
            or not 0 <= thresholds["margin"] <= 1 or thresholds["min_complex_rms"] <= 0):
        raise ValueError("invalid model thresholds")
    domain = model.get("supported_domain")
    if (not isinstance(domain, dict) or type(domain.get("input_fs_hz")) is not int
            or domain["input_fs_hz"] != 60000000
            or domain.get("decim") != [1] or type(domain["decim"][0]) is not int
            or not isinstance(domain.get("symbol_rates_hz"), list) or not domain["symbol_rates_hz"]
            or any(type(x) is not int or x not in {2000000, 3000000, 5000000} for x in domain["symbol_rates_hz"])
            or len(set(domain["symbol_rates_hz"])) != len(domain["symbol_rates_hz"])
            or not isinstance(domain.get("shaping"), list) or not domain["shaping"]
            or any(type(x) is not str or x not in {"RECT", "RRC0.35"} for x in domain["shaping"])
            or len(set(domain["shaping"])) != len(domain["shaping"])):
        raise ValueError("invalid model supported domain")
    if (type(evaluation.get("schema_version")) is not int or evaluation["schema_version"] != 1
            or evaluation.get("model_sha256") != artifact["model_sha256"]
            or evaluation.get("split") != "test"
            or evaluation.get("supported_domain") != model["supported_domain"]
            or not isinstance(evaluation.get("origin"), str)
            or evaluation.get("origin") not in {"synthetic", "cabled"}):
        raise ValueError("invalid evaluation schema/model identity/split")
    for key in ("training_manifest_sha256", "evaluation_manifest_sha256"):
        if evaluation.get(key) != model[key]:
            raise ValueError("evaluation manifest identity mismatch: " + key)
    # Experimental artifacts have the same evidence schema as production. Their
    # scores and sample counts may be below the acceptance gates, but missing or
    # malformed evidence must not become a hash-valid artifact.
    for key in ("macro_f1", "unknown_rejection", "off_rejection"):
        value = evaluation.get(key)
        if not _finite_number(value) or not 0 <= value <= 1:
            raise ValueError("invalid evaluation metric: " + key)
    recalls = evaluation.get("class_recall")
    if (not isinstance(recalls, dict) or set(recalls) != set(MODEL_CLASSES[:-1])
            or any(not _finite_number(value) or not 0 <= value <= 1 for value in recalls.values())):
        raise ValueError("invalid evaluation class recall")
    sessions, frames = evaluation.get("class_sessions"), evaluation.get("class_frames")
    for key, counts in (("class_sessions", sessions), ("class_frames", frames)):
        if (not isinstance(counts, dict) or set(counts) != set(MODEL_CLASSES)
                or any(type(value) is not int or value < 0 for value in counts.values())):
            raise ValueError("invalid evaluation counts: " + key)
    for key in ("off_sessions", "off_frames"):
        value = evaluation.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("invalid evaluation counts: " + key)
    if artifact["status"] == "production":
        valid = contract in MODEL_CONTRACTS and evaluation.get("origin") == "cabled"
        for key, threshold in (("macro_f1", 0.90), ("unknown_rejection", 0.90), ("off_rejection", 0.90)):
            valid = valid and evaluation[key] >= threshold
        for name in MODEL_CLASSES:
            for counts, minimum in ((sessions, 3), (frames, 30)):
                valid = valid and counts[name] >= minimum
            if name != "OTHER":
                valid = valid and recalls[name] >= 0.80
        for key, minimum in (("off_sessions", 3), ("off_frames", 30)):
            valid = valid and evaluation[key] >= minimum
        if not valid:
            raise ValueError("production model acceptance failed")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_hwh_addresses(path: Path) -> dict[str, str]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"invalid HWH: {exc}") from exc
    found: dict[str, str] = {}
    for module in root.iter("MODULE"):
        instance = module.get("INSTANCE")
        if not instance:
            continue
        bases = [
            p.get("VALUE") for p in module.iter("PARAMETER")
            if p.get("NAME") in {"C_BASEADDR", "C_S_AXI_BASEADDR"}
        ]
        if len(bases) > 1:
            raise ValueError(f"ambiguous HWH address for {instance}")
        if bases:
            try:
                found[instance] = f"0x{int(bases[0], 16):08x}"
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid HWH address for {instance}") from exc
    if not found:
        raise ValueError("HWH has no address map")
    return found


def validate_address_contract(addresses: dict[str, str]) -> None:
    if not isinstance(addresses, dict) or not addresses:
        raise ValueError("missing address contract")
    for name, expected in ADDRESS_PINS.items():
        if addresses.get(name, "").lower() != expected:
            raise ValueError(f"address contract mismatch for {name}: expected {expected}")


def validate_software_map(value: object) -> dict[str, str]:
    """Shape-check a software digest map (release-directory name -> sha256)."""
    if not isinstance(value, dict) or not value:
        raise ValueError("invalid software map")
    for name, digest in value.items():
        if not isinstance(name, str) or not _SOFTWARE_NAME.fullmatch(name):
            raise ValueError(f"invalid software name: {name!r}")
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError(f"invalid software hash for {name}")
    return value


def software_digests(root: Path, contract=CURRENT_SOFTWARE_CONTRACT) -> dict[str, str]:
    """Digest every carried software file, from the repository that builds the release."""
    root = Path(root)
    digests: dict[str, str] = {}
    for name, relative in sorted(software_sources(contract).items()):
        path = root / relative
        if not path.is_file():
            raise ValueError(f"software source missing: {relative}")
        digests[name] = sha256_file(path)
    return digests


def release_identity(build_id: str, bit_sha256: str, hwh_sha256: str,
                     daemon_version: str, protocol_version: int,
                     daemon_sha256: str, software: dict[str, str] | None = None,
                     *, software_contract=None, model_artifact=None,
                     modrec4_artifact=None) -> str:
    """Derive the release id.

    Without a software map this reproduces the original six-field identity, so releases
    built before the map existed keep their ids and still verify.  With a map the whole
    carried software set is part of the id, which is what makes `release_id` attest it.
    """
    parts = [build_id, bit_sha256, hwh_sha256, daemon_version,
             str(protocol_version), daemon_sha256]
    if software:
        validate_software_map(software)
        parts.append(",".join(f"{name}={software[name]}" for name in sorted(software)))
    if software_contract is not None:
        software_sources(software_contract)
        parts.append("software_contract=" + software_contract)
    if model_artifact is not None:
        validate_model_declaration(model_artifact)
        parts.append("model_artifact=" + json.dumps(model_artifact, sort_keys=True, separators=(",", ":")))
    if modrec4_artifact is not None:
        validate_modrec4_declaration(modrec4_artifact)
        parts.append("modrec4_artifact=" + json.dumps(modrec4_artifact, sort_keys=True, separators=(",", ":")))
    return hashlib.sha256(":".join(parts).encode("ascii")).hexdigest()[:32]


def _simple_filename(value: object, expected: str) -> str:
    if value != expected:
        raise ValueError(f"invalid artifact filename: expected {expected}")
    return expected


def verify_release(directory: Path, *, require_software: bool = False) -> dict:
    """Verify one release directory.

    `require_software=True` additionally demands that every attested software file is
    present -- the state install.sh must reach before it publishes.  Without it, files
    that are declared but not yet carried (the builder's five-file release directory)
    are allowed, while any software file that IS present must match its digest.
    """
    directory = Path(directory)
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"missing or invalid merged manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("invalid merged manifest schema_version")
    for key in ("release_id", "build_id", "daemon_version"):
        if not isinstance(manifest.get(key), str) or not _ID.fullmatch(manifest[key]):
            raise ValueError(f"invalid merged manifest {key}")
    if type(manifest.get("protocol_version")) is not int or manifest["protocol_version"] != 1:
        raise ValueError("invalid merged protocol_version")
    digests: dict[str, str] = {}
    for stem, filename in (("bit", BIT_NAME), ("hwh", HWH_NAME), ("daemon", DAEMON_NAME)):
        _simple_filename(manifest.get(stem + "_file"), filename)
        expected = manifest.get(stem + "_sha256")
        if not isinstance(expected, str) or not _HEX.fullmatch(expected):
            raise ValueError(f"invalid {stem} hash")
        actual = sha256_file(directory / filename)
        if actual != expected:
            raise ValueError(f"{stem} hash mismatch")
        digests[stem] = actual
    try:
        receipt = json.loads((directory / "merged_build_receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"missing or invalid merged build receipt: {exc}") from exc
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise ValueError("invalid merged build receipt schema")
    for key in ("build_id", "bit_sha256", "hwh_sha256"):
        if receipt.get(key) != manifest[key]:
            raise ValueError(f"merged build receipt mismatch: {key}")
    software = receipt.get(SOFTWARE_KEY)
    contract = receipt.get(SOFTWARE_CONTRACT_KEY)
    if manifest.get(SOFTWARE_CONTRACT_KEY) != contract:
        raise ValueError("manifest/receipt software contract mismatch")
    artifact = receipt.get(MODEL_KEY)
    if manifest.get(MODEL_KEY) != artifact:
        raise ValueError("manifest/receipt model artifact mismatch")
    modrec4_artifact = receipt.get(MODREC4_MODEL_KEY)
    if manifest.get(MODREC4_MODEL_KEY) != modrec4_artifact:
        raise ValueError("manifest/receipt modrec4 model artifact mismatch")
    if software is None:
        if contract is not None or artifact is not None:
            raise ValueError("software contract requires software map")
        if require_software:
            raise ValueError("release does not attest its software (no software map)")
        if manifest.get(SOFTWARE_KEY) is not None:
            raise ValueError("manifest declares software the receipt does not")
    else:
        validate_software_contract(software, contract)
        if manifest.get(SOFTWARE_KEY) != software:
            raise ValueError("manifest/receipt software map mismatch")
        for name in sorted(software):
            path = directory / name
            if not path.is_file():
                if require_software:
                    raise ValueError(f"software file missing: {name}")
                continue
            if sha256_file(path) != software[name]:
                raise ValueError(f"software hash mismatch: {name}")
        for path in sorted(directory.iterdir()):
            if (path.is_file() and path.suffix in _SOFTWARE_SUFFIXES
                    and path.name not in software):
                raise ValueError(f"unattested software file: {path.name}")
    expected_id = release_identity(
        manifest["build_id"], manifest["bit_sha256"], manifest["hwh_sha256"],
        manifest["daemon_version"], manifest["protocol_version"],
        digests["daemon"], software if isinstance(software, dict) else None,
        software_contract=contract, model_artifact=artifact,
        modrec4_artifact=modrec4_artifact)
    if manifest["release_id"] != expected_id:
        raise ValueError("release_id does not match the attested identity")
    hwh_addresses = parse_hwh_addresses(directory / HWH_NAME)
    validate_address_contract(hwh_addresses)
    if manifest.get("address_contract") != hwh_addresses:
        raise ValueError("HWH address contract mismatch")
    verify_model_artifact(directory, artifact, contract)
    verify_modrec4_artifact(directory, modrec4_artifact)
    return manifest


def verify_panel_imports(directory: Path) -> None:
    """Import the staged panel in isolation, without touching PL or the display.

    Source-tree pytest imports can hide files omitted by the installer. An isolated
    interpreter can only obtain the local panel modules from this release directory.
    """
    script = (
        "import sys, site; sys.path.append(site.getusersitepackages()); "
        "sys.path.insert(0, sys.argv[1]); "
        "import ui_view, rf_status_worker; "
        "ui_view.M.UiApp(); "
        "from pathlib import Path; "
        "root = Path(sys.argv[1]).resolve(); "
        "expected = " + repr([Path(n).stem for n in SOFTWARE_SOURCES if n.endswith(".py")]) + "; "
        "foreign = [n for n in expected if n in sys.modules "
        "and Path(sys.modules[n].__file__).resolve().parent != root]; "
        "assert not foreign, 'panel modules outside release: ' + ', '.join(foreign)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(Path(directory).resolve())],
            cwd=str(directory), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding="utf-8", errors="replace", timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("panel import check timed out") from exc
    if result.returncode:
        raise ValueError("panel import check failed: " + result.stderr.strip()[-2000:])


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--require-software", action="store_true",
                        help="also require every attested software file to be present")
    parser.add_argument("--check-panel", action="store_true",
                        help="import the staged panel without source-tree dependencies")
    parser.add_argument("--artifact-files", action="store_true",
                        help="print verified optional artifact filenames for installation")
    parser.add_argument("--software-contract", action="store_true",
                        help="print the verified versioned software contract")
    args = parser.parse_args(argv)
    try:
        manifest = verify_release(args.release, require_software=args.require_software)
        if args.check_panel:
            verify_panel_imports(args.release)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"REFUSE {exc}\n")
    if args.software_contract:
        print(manifest.get(SOFTWARE_CONTRACT_KEY, "legacy-v1"))
    elif args.artifact_files:
        artifact = manifest.get(MODEL_KEY)
        if artifact:
            print(artifact["model_file"] + "\n" + artifact["evaluation_file"])
    else:
        print(manifest["release_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
