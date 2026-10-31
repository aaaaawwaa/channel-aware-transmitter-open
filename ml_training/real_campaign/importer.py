"""Import existing real IQ sessions into an index (never copy/modify source files).

Truth policy:
* modulation truth comes only from an explicit signal configuration
  (``demod_result.submitted.config.mode``) or an independent experiment/scenario record;
* ``model_result`` and environment labels are recorded only as non-truth diagnostics and can
  never become a truth class;
* synthetic fixtures are always marked ``synthetic=true`` and are never counted as real data.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import domain
from .sources import ROOT, resolve

CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
QUALITY_LABELS = ("OFF", "TONE", "NOISE", "CLIP")
TRUTH_SOURCE_SIGNAL = "signal_config"
TRUTH_SOURCE_EXPERIMENT = "experiment_record"
TRUTH_SOURCE_SYNTHETIC = "synthetic_generator"
TRUTH_SOURCE_TX_CONFIG = "independent_tx_config"
TRUTH_SOURCE_MODEL = "model_prediction"
TRUTH_SOURCE_ENV = "environment_label"
ALLOWED_TRUTH_SOURCES = (TRUTH_SOURCE_SIGNAL, TRUTH_SOURCE_EXPERIMENT,
                         TRUTH_SOURCE_SYNTHETIC, TRUTH_SOURCE_TX_CONFIG)

TASK68_TRUTH_SCHEMA = "ml-task68-tx-truth-v1"

REASON_MISSING_IQ = "missing_iq_file"
REASON_MISSING_METADATA = "missing_metadata"
REASON_DIGEST_MISMATCH = "digest_mismatch"
REASON_TRUTH_MISSING = "truth_missing"
REASON_TRUTH_NOT_INDEPENDENT = "truth_source_not_independent"
REASON_TRUTH_UNSUPPORTED = "unsupported_truth_class"
REASON_SIGNAL_CONTEXT_MISMATCH = "signal_config_context_mismatch"
REASON_EXPERIMENT_RECORD_MISSING = "experiment_record_missing"
REASON_METADATA_ERROR = "metadata_error"
REASON_TRUTH_CONFIG_INCOMPLETE = "truth_config_incomplete"
REASON_TRUTH_IDENTITY_MISMATCH = "truth_identity_mismatch"
REASON_TRUTH_FILE_MISSING = "truth_file_missing"
REASON_TRUTH_SCHEMA_INVALID = "truth_schema_invalid"


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _path_get(document, dotted, default=None):
    value = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _read_declaration(source):
    """Return (release_id, boot_id, declaration_path, declaration_sha) if declared."""
    value = source.get("declaration")
    if not value:
        return None, None, None, None
    path = resolve(value)
    if not path.is_file():
        return None, None, str(path), None
    document = _load_json(path)
    release = _path_get(document, source.get("declaration_release_path",
                                             "board_reference.release_id"))
    boot = _path_get(document, source.get("declaration_boot_path",
                                          "board_reference.boot_id"))
    return release, boot, str(path), domain.sha256_file(path)


def _base_record(source, frame_id, session_id, split, iq_path, metadata_path):
    return {
        "frame_id": frame_id,
        "source_id": source["source_id"],
        "session_id": session_id,
        "session_key": "%s/%s" % (source["source_id"], session_id),
        "split": split,
        "synthetic": bool(source.get("synthetic", False)),
        "kind": "other",
        "scenario": None,
        "truth_class": None,
        "truth_source": None,
        "truth_detail": None,
        "physical": None,
        "iq_path": str(Path(iq_path).relative_to(ROOT)).replace("\\", "/"),
        "metadata_path": (str(Path(metadata_path).relative_to(ROOT)).replace("\\", "/")
                          if metadata_path else None),
        "iq_sha256": None,
        "metadata_iq_sha256": None,
        "digest_match": None,
        "context": None,
        "context_check": None,
        "domain_status": "NOT_CHECKED",
        "domain_reason": None,
        "identity": {"release_id": None, "boot_id": None, "boot_declared": False,
                     "declaration_release_id": None},
        "non_truth_labels": {},
        "status": "OK",
        "invalid_reasons": [],
    }


def _add_invalid(record, reason):
    record["status"] = "INVALID"
    if reason and reason not in record["invalid_reasons"]:
        record["invalid_reasons"].append(reason)


def _domain_check(record, iq_check):
    if not iq_check["ok"]:
        record["domain_status"] = "INVALID"
        record["domain_reason"] = iq_check["reason"]
        _add_invalid(record, iq_check["reason"])
        return False
    context = record.get("context")
    check = domain.validate_context(context)
    record["context_check"] = check
    if not check["ok"]:
        record["domain_status"] = "UNSUPPORTED"
        record["domain_reason"] = check["reason"]
        _add_invalid(record, check["reason"])
        return False
    record["domain_status"] = "OK"
    record["domain_reason"] = None
    return True


def _parse_board_trainval(source):
    records, errors = [], []
    declared_release, declared_boot, decl_path, decl_sha = _read_declaration(source)
    root = resolve(source["root"])
    for session_dir_name in source.get("session_dirs", []):
        session_dir = root / session_dir_name
        if not session_dir.is_dir():
            errors.append({"source_id": source["source_id"], "session": session_dir_name,
                           "error": "session_dir_missing"})
            continue
        for metadata_path in sorted(session_dir.glob("*.json")):
            name = metadata_path.name
            if name.endswith(".environment.json") or name.startswith("session-result"):
                continue
            if not (name.startswith("normal-") or name.startswith("off-")):
                continue
            stem = metadata_path.stem
            iq_path = metadata_path.with_suffix(".iq")
            frame_id = "%s/%s/%s" % (source["source_id"], session_dir_name, stem)
            try:
                meta = _load_json(metadata_path)
            except Exception as exc:
                record = _base_record(source, frame_id, session_dir_name, "UNASSIGNED",
                                      iq_path, metadata_path)
                _add_invalid(record, REASON_METADATA_ERROR)
                record["metadata_error"] = "%s: %s" % (type(exc).__name__, exc)
                records.append(record)
                continue
            session_meta = meta.get("session") or {}
            session_id = session_meta.get("session_id") or session_dir_name
            split = str(session_meta.get("split") or
                        session_dir_name.split("-")[0]).upper()
            record = _base_record(source, frame_id, session_id, split, iq_path,
                                  metadata_path)
            record["source_root"] = source["root"]
            record["physical"] = bool(meta.get("physical"))
            scenario = _path_get(meta, "trainval.scenario") or _path_get(meta,
                                                                         "trainval.truth")
            record["scenario"] = scenario
            submitted = _path_get(meta, "demod_result.submitted.config")
            iq_check = domain.load_iq(iq_path)
            record["iq_sha256"] = iq_check["digest"]
            record["metadata_iq_sha256"] = meta.get("iq_sha256")
            record["digest_match"] = (iq_check["digest"] == meta.get("iq_sha256"))
            if not record["digest_match"]:
                _add_invalid(record, REASON_DIGEST_MISMATCH)
            observed = {
                "format": meta.get("format"), "samples": meta.get("samples"),
                "sample_rate_hz": meta.get("sample_rate_hz"),
                "decimation": meta.get("decimation"),
            }
            if submitted:
                context = dict(source.get("declared_context") or {})
                context["symbol_rate_hz"] = submitted.get("symbol_rate_hz",
                                                          context.get("symbol_rate_hz"))
                context["shaping"] = submitted.get("shaping", context.get("shaping"))
            else:
                context = dict(source.get("declared_context") or {})
            record["context"] = context
            record["observed_input"] = observed
            record["signal_config"] = ({key: submitted.get(key) for key in
                                        ("mode", "symbol_rate_hz", "shaping",
                                         "reference_prbs7_seed")}
                                       if isinstance(submitted, dict) else None)
            record["identity"].update({
                "release_id": meta.get("release_id"),
                "boot_id": declared_boot,
                "boot_declared": bool(declared_boot),
                "declaration_release_id": declared_release,
                "declaration_path": (str(Path(decl_path).relative_to(ROOT)).replace("\\", "/")
                                     if decl_path else None),
                "declaration_sha256": decl_sha,
            })
            model_result = meta.get("model_result") or {}
            environment = meta.get("environment") or {}
            record["non_truth_labels"] = {
                "model_result_label": model_result.get("label"),
                "environment_label": (environment.get("label")
                                      if isinstance(environment, dict) else None),
            }
            if scenario in ("normal", "clean"):
                mode = (submitted or {}).get("mode")
                record["kind"] = "clean"
                record["truth_source"] = TRUTH_SOURCE_SIGNAL
                record["truth_detail"] = "demod_result.submitted.config.mode"
                record["truth_class"] = mode
                if mode not in CLASSES:
                    _add_invalid(record, REASON_TRUTH_UNSUPPORTED)
            elif scenario == "off":
                record["kind"] = "quality"
                record["truth_class"] = "OFF"
                record["truth_source"] = TRUTH_SOURCE_EXPERIMENT
                record["truth_detail"] = "trainval.truth=off"
            else:
                record["kind"] = "other"
                record["truth_source"] = TRUTH_SOURCE_EXPERIMENT
                record["truth_detail"] = "trainval.scenario=%r" % (scenario,)
                if scenario not in ("off",):
                    _add_invalid(record, REASON_TRUTH_UNSUPPORTED)
            if isinstance(submitted, dict) and record["context"]:
                if (submitted.get("symbol_rate_hz") != record["context"].get("symbol_rate_hz")
                        or submitted.get("shaping") != record["context"].get("shaping")):
                    _add_invalid(record, REASON_SIGNAL_CONTEXT_MISMATCH)
            if not iq_path.is_file():
                _add_invalid(record, REASON_MISSING_IQ)
            _domain_check(record, iq_check)
            records.append(record)
    return records, errors


def _load_experiment_records(source):
    root = resolve(source["root"])
    records = []
    for path in sorted(root.glob(source.get("record_glob", "records/experiment-*/metadata.json"))):
        try:
            metadata = _load_json(path)
        except Exception:
            continue
        frame_iq = path.parent / "frame.iq"
        digest = domain.sha256_file(frame_iq) if frame_iq.is_file() else None
        records.append({"path": path, "metadata": metadata, "digest": digest})
    return records


def _parse_teaching_experiment(source):
    records, errors = [], []
    root = resolve(source["root"])
    experiment_records = _load_experiment_records(source)
    by_digest = {}
    for item in experiment_records:
        if item["digest"]:
            by_digest.setdefault(item["digest"], item)
    for metadata_path in sorted(root.glob(source.get("frame_glob", "nf-*.json"))):
        name = metadata_path.name
        if name.endswith(".environment.json") or name.endswith(".capture-observation.json"):
            continue
        stem = metadata_path.stem
        iq_path = metadata_path.with_suffix(".iq")
        frame_id = "%s/%s" % (source["source_id"], stem)
        try:
            meta = _load_json(metadata_path)
        except Exception as exc:
            record = _base_record(source, frame_id, source.get("session_id", stem),
                                  source.get("split", "UNASSIGNED"), iq_path, metadata_path)
            _add_invalid(record, REASON_METADATA_ERROR)
            record["metadata_error"] = "%s: %s" % (type(exc).__name__, exc)
            records.append(record)
            continue
        iq_check = domain.load_iq(iq_path)
        record = _base_record(source, frame_id, source.get("session_id", stem),
                              source.get("split", "UNASSIGNED"), iq_path, metadata_path)
        record["physical"] = bool(meta.get("physical"))
        record["iq_sha256"] = iq_check["digest"]
        record["metadata_iq_sha256"] = meta.get("iq_sha256")
        record["digest_match"] = (iq_check["digest"] == meta.get("iq_sha256"))
        if not record["digest_match"]:
            _add_invalid(record, REASON_DIGEST_MISMATCH)
        experiment = by_digest.get(iq_check["digest"])
        if experiment is None:
            record["kind"] = "other"
            _add_invalid(record, REASON_EXPERIMENT_RECORD_MISSING)
            record["context"] = dict(source.get("declared_context") or {})
            _domain_check(record, iq_check)
            records.append(record)
            continue
        experiment_meta = experiment["metadata"]
        record["experiment_record_path"] = str(
            experiment["path"].relative_to(ROOT)).replace("\\", "/")
        record["experiment_record_sha256"] = domain.sha256_file(experiment["path"])
        declaration = experiment_meta.get("scenario_declaration") or {}
        scenario = declaration.get("truth") or experiment_meta.get("scenario")
        record["scenario"] = scenario
        submitted = _path_get(experiment_meta, "demod_result.submitted.config")
        record["signal_config"] = ({key: submitted.get(key) for key in
                                    ("mode", "symbol_rate_hz", "shaping",
                                     "reference_prbs7_seed")}
                                   if isinstance(submitted, dict) else None)
        if scenario in ("normal", "clean"):
            record["kind"] = "clean"
        elif scenario in ("narrowband", "tone", "noise", "interference"):
            record["kind"] = "stress"
        elif scenario == "off":
            record["kind"] = "quality"
        else:
            record["kind"] = "other"
        if scenario == "off":
            record["truth_class"] = "OFF"
            record["truth_source"] = TRUTH_SOURCE_EXPERIMENT
            record["truth_detail"] = "scenario_declaration.truth=off"
        else:
            mode = (submitted or {}).get("mode")
            record["truth_class"] = mode
            record["truth_source"] = TRUTH_SOURCE_SIGNAL
            record["truth_detail"] = ("experiment_record.scenario_declaration + "
                                      "demod_result.submitted.config.mode")
            if mode not in CLASSES:
                _add_invalid(record, REASON_TRUTH_UNSUPPORTED)
        if not scenario:
            _add_invalid(record, REASON_TRUTH_MISSING)
        context = dict(source.get("declared_context") or {})
        if isinstance(submitted, dict):
            context["symbol_rate_hz"] = submitted.get("symbol_rate_hz",
                                                      context.get("symbol_rate_hz"))
            context["shaping"] = submitted.get("shaping", context.get("shaping"))
        record["context"] = context
        record["observed_input"] = {
            "format": meta.get("format"), "samples": meta.get("samples"),
            "sample_rate_hz": meta.get("sample_rate_hz"),
            "decimation": meta.get("decimation"),
            "physical": meta.get("physical"),
        }
        record["identity"].update({
            "release_id": meta.get("release_id") or experiment_meta.get("release_id"),
            "boot_id": None,
            "boot_declared": False,
            "declaration_release_id": None,
        })
        model_result = meta.get("model_result") or {}
        environment = (experiment_meta.get("environment") or {})
        record["non_truth_labels"] = {
            "model_result_label": model_result.get("label"),
            "environment_label": (environment.get("label")
                                  if isinstance(environment, dict) else None),
        }
        if isinstance(submitted, dict) and record["context"]:
            if (submitted.get("symbol_rate_hz") != record["context"].get("symbol_rate_hz")
                    or submitted.get("shaping") != record["context"].get("shaping")):
                _add_invalid(record, REASON_SIGNAL_CONTEXT_MISMATCH)
        if not iq_path.is_file():
            _add_invalid(record, REASON_MISSING_IQ)
        _domain_check(record, iq_check)
        records.append(record)
    return records, errors


def _parse_fixture_records(source):
    records = []
    root = resolve(source["root"])
    for metadata_path in sorted(root.rglob("*.json")):
        if metadata_path.name == "manifest.json":
            continue
        try:
            meta = _load_json(metadata_path)
        except Exception:
            continue
        if meta.get("schema") != "ml-real-campaign-fixture-v1":
            continue
        iq_name = meta.get("iq_file") or (metadata_path.stem + ".iq")
        iq_path = metadata_path.parent / iq_name
        frame_id = "%s/%s" % (source["source_id"],
                              str(metadata_path.relative_to(root)).replace("\\", "/"))
        record = _base_record(source, frame_id, meta.get("session_id") or "synthetic-fixtures",
                              str(meta.get("split") or "UNASSIGNED").upper(),
                              iq_path, metadata_path)
        record["physical"] = False
        record["scenario"] = meta.get("scenario") or meta.get("purpose")
        record["kind"] = meta.get("kind") or "other"
        record["synthetic"] = True
        record["fixture_purpose"] = meta.get("purpose")
        record["expected_import_status"] = meta.get("expected_import_status")
        record["expected_reason"] = meta.get("expected_reason")
        record["truth_source"] = meta.get("truth_source")
        record["truth_class"] = meta.get("truth_class")
        record["truth_detail"] = meta.get("truth_detail")
        record["context"] = meta.get("context")
        record["non_truth_labels"] = meta.get("non_truth_labels") or {}
        iq_check = domain.load_iq(iq_path)
        record["iq_sha256"] = iq_check["digest"]
        record["metadata_iq_sha256"] = meta.get("iq_sha256")
        record["digest_match"] = (iq_check["digest"] == meta.get("iq_sha256"))
        if not record["digest_match"]:
            _add_invalid(record, REASON_DIGEST_MISMATCH)
        if record["truth_source"] not in ALLOWED_TRUTH_SOURCES:
            _add_invalid(record, REASON_TRUTH_NOT_INDEPENDENT)
        if record["kind"] == "clean" and record["truth_class"] is None:
            _add_invalid(record, REASON_TRUTH_MISSING)
        if (record["kind"] == "clean" and record["truth_class"] is not None
                and record["truth_class"] not in CLASSES):
            _add_invalid(record, REASON_TRUTH_UNSUPPORTED)
        if not iq_path.is_file():
            _add_invalid(record, REASON_MISSING_IQ)
        _domain_check(record, iq_check)
        records.append(record)
    return records, []


def import_all(config):
    records, errors = [], []
    for source in config["sources"]:
        kind = source["kind"]
        if kind == "board_trainval":
            parsed, source_errors = _parse_board_trainval(source)
        elif kind == "teaching_experiment":
            parsed, source_errors = _parse_teaching_experiment(source)
        elif kind == "fixture_records":
            parsed, source_errors = _parse_fixture_records(source)
        else:
            raise ValueError("unknown source kind: %r" % kind)
        records.extend(parsed)
        errors.extend(source_errors)
    real = [record for record in records if not record["synthetic"]]
    synthetic = [record for record in records if record["synthetic"]]
    return {
        "schema": "ml-real-campaign-import-v1",
        "records": records,
        "real_records": real,
        "synthetic_records": synthetic,
        "errors": errors,
        "counts": {
            "records": len(records),
            "real": len(real),
            "synthetic": len(synthetic),
            "invalid": len([record for record in records if record["status"] != "OK"]),
            "real_invalid": len([record for record in real if record["status"] != "OK"]),
        },
    }


# ---------------------------------------------------------------------------
# task68: real four-class frames with independent TX truth
# ---------------------------------------------------------------------------
_TX_TRUTH_SOURCE_ALIASES = {
    TRUTH_SOURCE_TX_CONFIG,
    "independent_tx_configuration",
    "tx_config",
}
_TASK68_SKIP_METADATA_NAMES = {
    "manifest.json", "final.json", "records.json", "result.json",
    "run-result.json", "task68_eval.json", "task68_review.json",
}
_TASK68_SKIP_SUFFIXES = (".environment.json", ".tx_truth.json")


def _task68_add_unevaluable(record, reason):
    """Mark an import as not evaluable without pretending it is a truth."""
    record["truth_evaluable"] = False
    if reason and reason not in record["invalid_reasons"]:
        record["invalid_reasons"].append(reason)
    if record["status"] == "OK":
        record["status"] = "UNEVALUABLE"


def _task68_truth_index(truth_path):
    """Load the independent TX truth file; never read RX/model labels for truth."""
    if truth_path is None:
        return {}, REASON_TRUTH_FILE_MISSING, None, None
    truth_path = Path(truth_path)
    if not truth_path.is_file():
        return {}, REASON_TRUTH_FILE_MISSING, str(truth_path), None
    sha = domain.sha256_file(truth_path)
    try:
        doc = _load_json(truth_path)
    except Exception:
        return {}, REASON_TRUTH_SCHEMA_INVALID, str(truth_path), sha
    if not isinstance(doc, dict) or doc.get("schema") != TASK68_TRUTH_SCHEMA:
        return {}, REASON_TRUTH_SCHEMA_INVALID, str(truth_path), sha
    default_source = doc.get("truth_source")
    common = doc.get("config") or {}
    index, by_capture = {}, {}
    if not isinstance(doc.get("frames"), list):
        return {}, REASON_TRUTH_SCHEMA_INVALID, str(truth_path), sha
    for entry in doc["frames"]:
        if not isinstance(entry, dict):
            continue
        identity = entry.get("frame_identity") or {}
        tx = dict(common) if isinstance(common, dict) else {}
        if isinstance(entry.get("tx"), dict):
            tx.update(entry["tx"])
        source = entry.get("truth_source", default_source)
        capture_id = identity.get("capture_id")
        iq_sha = identity.get("iq_sha256")
        key = (capture_id, iq_sha)
        if not capture_id or not iq_sha:
            continue
        entry = dict(entry)
        entry["tx"] = tx
        entry["truth_source"] = source
        entry["_truth_file"] = str(truth_path)
        entry["_truth_sha256"] = sha
        index[key] = entry
        by_capture.setdefault(capture_id, entry)
    return index, None, str(truth_path), sha


def _task68_frame_identity(meta):
    board = meta.get("modrec4_result") or {}
    nested = board.get("frame_identity") or {}
    return {
        "capture_id": meta.get("capture_id") or nested.get("capture_id"),
        "request_id": meta.get("request_id", nested.get("request_id")),
        "iq_sha256": meta.get("iq_sha256") or nested.get("iq_sha256"),
        "release_id": meta.get("release_id") or nested.get("release_id"),
        "connection_generation": meta.get("connection_generation",
                                           nested.get("connection_generation")),
        "config_revision": meta.get("config_revision", nested.get("config_revision")),
    }


def _task68_frame_files(root):
    for path in sorted(Path(root).rglob("*.json")):
        name = path.name
        if name in _TASK68_SKIP_METADATA_NAMES:
            continue
        if any(name.endswith(suffix) for suffix in _TASK68_SKIP_SUFFIXES):
            continue
        try:
            meta = _load_json(path)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if not meta.get("capture_id") or "iq_sha256" not in meta:
            continue
        yield path, meta


def _task68_truth_problem(entry, meta, record):
    """Return a reason string if the independent truth is missing or unsafe."""
    if entry is None:
        return REASON_TRUTH_MISSING
    source = entry.get("truth_source")
    if source not in _TX_TRUTH_SOURCE_ALIASES:
        return REASON_TRUTH_NOT_INDEPENDENT
    tx = entry.get("tx") or {}
    mode = tx.get("mode")
    if not isinstance(mode, str):
        return REASON_TRUTH_CONFIG_INCOMPLETE
    if mode not in CLASSES and mode != "OFF":
        return REASON_TRUTH_UNSUPPORTED
    clean = mode in CLASSES
    if clean:
        seed = tx.get("seed")
        if type(seed) is not int or not 1 <= seed <= 127:
            return REASON_TRUTH_CONFIG_INCOMPLETE
    for field in ("sample_rate_hz", "symbol_rate_hz", "shaping"):
        if tx.get(field) in (None, ""):
            return REASON_TRUTH_CONFIG_INCOMPLETE
    if clean and tx.get("amplitude") is None:
        return REASON_TRUTH_CONFIG_INCOMPLETE
    if clean and tx.get("rolloff") in (None, ""):
        return REASON_TRUTH_CONFIG_INCOMPLETE
    # The board runner intentionally keeps the BPSK demodulator configuration for
    # all four TX modes; the 4-class decision is made by modrec4.  RX mode is
    # diagnostic only and must not veto a matching independent TX truth.
    identity = entry.get("frame_identity") or {}
    actual = _task68_frame_identity(meta)
    if identity.get("iq_sha256") and identity["iq_sha256"] != record.get("iq_sha256"):
        return REASON_TRUTH_IDENTITY_MISMATCH
    for key in ("capture_id", "request_id", "release_id", "connection_generation",
                "config_revision"):
        if key in identity and actual.get(key) != identity.get(key):
            return REASON_TRUTH_IDENTITY_MISMATCH
    context = record.get("context") or {}
    if (tx.get("sample_rate_hz") != context.get("sample_rate_hz")
            or tx.get("symbol_rate_hz") != context.get("symbol_rate_hz")
            or tx.get("shaping") != context.get("shaping")):
        return REASON_SIGNAL_CONTEXT_MISMATCH
    return None


def parse_task68_frames(frames_root, truth_file, source_id="task68", split="TEST"):
    """Parse task68 real frames with an independent TX truth file.

    Truth is only ``truth_file.frames[*].tx`` (mode/seed/config) matched to the
    frame identity.  RX ``expected_mode`` is recorded as a diagnostic only; it can
    never supply truth and does not veto a matching independent TX truth.
    Missing/unsafe truth -> ``status=UNEVALUABLE``.  Invalid IQ/OFF inputs ->
    ``status=INVALID`` and are skipped by the frozen evaluator.
    """
    frames_root = Path(frames_root)
    if not frames_root.is_absolute():
        frames_root = resolve(str(frames_root))
    truth_path = Path(truth_file) if truth_file else None
    if truth_path is not None and not truth_path.is_absolute():
        truth_path = resolve(str(truth_file))
    truth_index, truth_error, truth_path_s, truth_sha = _task68_truth_index(truth_path)
    truth_by_capture = {}
    for entry in truth_index.values():
        capture = (entry.get("frame_identity") or {}).get("capture_id")
        if capture:
            truth_by_capture[capture] = entry
    records = []
    source_config = {"source_id": source_id, "synthetic": False}
    if not frames_root.is_dir():
        return {"schema": "ml-task68-import-v1",
                "records": [], "truth_file": truth_path_s,
                "truth_sha256": truth_sha, "truth_error": truth_error,
                "errors": [{"source_id": source_id,
                            "error": "frames_root_missing",
                            "path": str(frames_root)}]}
    for metadata_path, meta in _task68_frame_files(frames_root):
        iq_path = metadata_path.with_suffix(".iq")
        frame_id = "%s/%s" % (source_id,
                              str(metadata_path.relative_to(frames_root)).replace("\\", "/"))
        session_id = (meta.get("session") or {}).get("session_id") or meta.get(
            "session_id") or source_id
        record = _base_record(source_config, frame_id, session_id, split, iq_path,
                              metadata_path)
        record["source_root"] = str(frames_root)
        iq_check = domain.load_iq(iq_path)
        record["iq_sha256"] = iq_check["digest"]
        record["metadata_iq_sha256"] = meta.get("iq_sha256")
        record["digest_match"] = (iq_check["digest"] == meta.get("iq_sha256"))
        if not record["digest_match"]:
            _add_invalid(record, REASON_DIGEST_MISMATCH)
        submitted = _path_get(meta, "demod_result.submitted.config") or {}
        context = dict(domain.FROZEN_CONTEXT)
        if isinstance(submitted, dict):
            if submitted.get("symbol_rate_hz") is not None:
                context["symbol_rate_hz"] = submitted["symbol_rate_hz"]
            if submitted.get("shaping") is not None:
                context["shaping"] = submitted["shaping"]
        record["context"] = context
        record["observed_input"] = {
            "format": meta.get("format"), "samples": meta.get("samples"),
            "sample_rate_hz": meta.get("sample_rate_hz"),
            "decimation": meta.get("decimation"),
        }
        record["signal_config"] = ({key: submitted.get(key) for key in
                                    ("mode", "symbol_rate_hz", "shaping",
                                     "reference_prbs7_seed")}
                                   if isinstance(submitted, dict) else None)
        record["frame_identity"] = _task68_frame_identity(meta)
        record["board_original_results"] = {
            key: meta[key] for key in ("environment_result", "demod_result",
                                       "model_result", "modrec4_result")
            if key in meta}
        model_result = meta.get("model_result") or {}
        environment = meta.get("environment_result") or {}
        record["non_truth_labels"] = {
            "model_result_label": model_result.get("label"),
            "environment_label": environment.get("label"),
            "rx_expected_mode": submitted.get("mode") if isinstance(submitted, dict) else None,
        }
        # Match independent truth by identity.  Use the actual IQ digest so a
        # stale metadata digest cannot select a truth entry.
        actual_key = (meta.get("capture_id"), iq_check["digest"])
        selected = truth_index.get(actual_key)
        if selected is None:
            selected = truth_by_capture.get(meta.get("capture_id"))
            if selected is not None:
                selected = dict(selected)
                selected["_identity_only"] = True
        if selected is not None:
            tx = dict(selected.get("tx") or {})
            truth_source_value = selected.get("truth_source")
            canonical_source = (TRUTH_SOURCE_TX_CONFIG
                                if truth_source_value in _TX_TRUTH_SOURCE_ALIASES
                                else truth_source_value)
            record["tx_truth"] = {
                "source": canonical_source,
                "truth_file": truth_path_s,
                "truth_sha256": truth_sha,
                "frame_identity": selected.get("frame_identity"),
                "mode": tx.get("mode"),
                "seed": tx.get("seed"),
                "config": tx,
            }
            rx_mode = submitted.get("mode") if isinstance(submitted, dict) else None
            record["rx_expected_mode_diagnostic"] = {
                "rx_expected_mode": rx_mode,
                "tx_mode": tx.get("mode"),
                "mismatch": bool(rx_mode and tx.get("mode") and rx_mode != tx.get("mode")),
                "note": ("diagnostic only: runner keeps BPSK demod config; 4-class "
                         "decision is made by modrec4 and RX mode does not veto TX truth"),
            }
            mode = tx.get("mode")
            if mode in CLASSES:
                record["kind"] = "clean"
                record["scenario"] = "normal"
                record["truth_class"] = mode
            elif mode == "OFF":
                record["kind"] = "quality"
                record["scenario"] = "off"
                record["truth_class"] = "OFF"
            else:
                record["kind"] = "other"
                record["scenario"] = "unknown"
                record["truth_class"] = None
            record["truth_source"] = canonical_source
            record["truth_detail"] = {
                "source": "independent TX mode/seed/config",
                "truth_file": truth_path_s,
                "truth_sha256": truth_sha,
                "tx": tx,
            }
        else:
            record["kind"] = "other"
            record["truth_class"] = None
            record["truth_source"] = None
            record["tx_truth"] = None
        problem = _task68_truth_problem(selected, meta, record)
        if selected is None:
            problem = truth_error or REASON_TRUTH_MISSING
        if not iq_path.is_file():
            _add_invalid(record, REASON_MISSING_IQ)
        _domain_check(record, iq_check)
        record["truth_evaluable"] = problem is None and record["status"] == "OK"
        if problem:
            _task68_add_unevaluable(record, problem)
        if record["kind"] == "clean" and record["truth_class"] not in CLASSES:
            _task68_add_unevaluable(record, REASON_TRUTH_UNSUPPORTED)
        records.append(record)
    return {"schema": "ml-task68-import-v1", "records": records,
            "truth_file": truth_path_s, "truth_sha256": truth_sha,
            "truth_error": truth_error, "errors": []}

