"""Shared helpers for the offline modulation-recognition evaluation.

Implements, per ``ml_training/docs/CONTRACTS.md``:

* dataset manifest / npz loading with per-frame integrity checks (§3, §7);
* 4-class confusion matrix, per-class precision/recall/F1, macro-F1, accuracy
  and per-SNR metrics, used ONLY on the independent ``test`` split with
  ``clipped=False`` frames (§2, §10);
* quality-gate rejection metrics on ``gate_test`` ONLY, kept strictly separate
  from the 4-class matrix (§2, §10);
* adapters that call the *real* model artefacts produced by the Lead:
  ``ml_training/model/numpy_runtime.py`` (``load_model`` / ``predict_iq``) and
  ``ml_training/model/baseline.py`` (``load_baseline`` / ``predict_iq``) (§9).
  No model mathematics is re-implemented here.

The module deliberately raises ``LabelLeakError`` when gate kinds leak into the
4-class label space (or vice versa): mixing the quality-gate population into the
4x4 matrix is a contract violation, not a warning.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import platform
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Frozen label orders (CONTRACTS §2) - never reorder these.
CLASS_ORDER = ["BPSK", "QPSK", "QAM16", "QAM64"]
GATE_KINDS = ["OFF", "NOISE", "TONE", "CLIP"]
UNKNOWN = "UNKNOWN"

DATASET_SCHEMA = "ml-modrec-synthetic-v1"
SPLITS = ("train", "val", "test", "gate_cal", "gate_test")
FRAME_ARRAYS = (
    "X", "frame_id", "session_id", "label", "seed_family", "symbol_seed",
    "noise_seed", "snr_db", "amplitude_codes", "phase_rad", "cfo_hz",
    "window_offset", "noise_sigma_codes", "iq_sha256", "clipped",
)
SAMPLES = 4096
FRAME_BYTES = SAMPLES * 2 * 2
MIN_CODE = -2048
MAX_CODE = 2047

EVAL_SCHEMA_SIM = "ml-modrec-eval-sim-v1"
EVAL_SCHEMA_REAL = "ml-modrec-eval-real-transfer-v1"
EVAL_SCHEMA_REAL_FAILURES = "ml-modrec-eval-real-failures-v1"

HOST_NOT_RUN = (
    "arm_inference: NOT_RUN - all inference in this report ran on the host CPU "
    "with the numpy runtime; these numbers are NOT ARM numbers and NOT board numbers.",
    "board_hardware_accuracy: NOT_RUN - no board/hardware execution was performed by "
    "the evaluation tools.",
    "azure_vm: NOT_RUN - no Azure connection information is available in this round.",
)


class EvaluationError(RuntimeError):
    """Base error for evaluation tooling."""


class DatasetError(EvaluationError):
    """The dataset does not match the frozen manifest / npz contract."""


class ArtifactMissing(EvaluationError):
    """A model/baseline artefact or the module that loads it is missing."""


class LabelLeakError(EvaluationError):
    """Gate kinds / algorithm labels leaked into the 4-class label space (or vice versa)."""


class TruthUnavailable(EvaluationError):
    """A real frame has no contract-conforming ground truth (trainval.truth)."""


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def load_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsonable(value):
    """Recursively convert numpy scalar/array values into plain JSON values."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(jsonable(document), ensure_ascii=False, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def relpath(path):
    """Project-relative POSIX path when possible (stable across machines)."""
    path = Path(path)
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def host_environment():
    return OrderedDict([
        ("platform", platform.platform()),
        ("machine", platform.machine()),
        ("python", platform.python_version()),
        ("numpy", np.__version__),
        ("backend", "host CPU numpy runtime (NOT ARM, NOT board)"),
    ])


def fmt(value, digits=4):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "-"
        return ("%." + str(digits) + "f") % value
    return str(value)


def md_table(headers, rows):
    lines = ["| " + " | ".join(str(head) for head in headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(fmt(cell) for cell in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# dataset manifest / npz loading (CONTRACTS §3, §7)
# ---------------------------------------------------------------------------
def load_manifest(dataset_dir, verify_hashes=False):
    """Load and validate ``<dataset_dir>/manifest.json``.

    Returns the manifest dict with extra ``_path`` / ``_dir`` / ``_sha256`` keys.
    """
    dataset_dir = Path(dataset_dir)
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetError("dataset manifest is missing: %s" % manifest_path)
    manifest = load_json_file(manifest_path)
    if manifest.get("schema") != DATASET_SCHEMA:
        raise DatasetError("unsupported dataset schema: %r" % (manifest.get("schema"),))
    dataset_id = manifest.get("dataset_id")
    if not dataset_id:
        raise DatasetError("manifest is missing dataset_id")
    if str(dataset_id) != dataset_dir.name:
        raise DatasetError("manifest dataset_id %r does not match directory %r"
                           % (dataset_id, dataset_dir.name))
    classes = list(manifest.get("classes") or [])
    if classes != CLASS_ORDER:
        raise DatasetError("manifest classes %r are not the frozen CLASS_ORDER %r"
                           % (classes, CLASS_ORDER))
    gate_kinds = list(manifest.get("gate_kinds") or [])
    if gate_kinds != GATE_KINDS:
        raise DatasetError("manifest gate_kinds %r are not the frozen GATE_KINDS %r"
                           % (gate_kinds, GATE_KINDS))
    splits = manifest.get("splits")
    if not isinstance(splits, dict):
        raise DatasetError("manifest is missing the splits object")
    for split in SPLITS:
        if split not in splits:
            raise DatasetError("manifest is missing split %r" % (split,))
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise DatasetError("manifest has no frame files")
    for entry in entries:
        split = entry.get("split")
        if split not in SPLITS:
            raise DatasetError("manifest file has unknown split: %r" % (split,))
        label = entry.get("label")
        allowed = GATE_KINDS if split in ("gate_cal", "gate_test") else CLASS_ORDER
        if label not in allowed:
            raise DatasetError("manifest file %r has label %r outside %r"
                               % (entry.get("path"), label, allowed))
        rel = entry.get("path")
        if not rel:
            raise DatasetError("manifest file entry is missing path")
        frame_path = dataset_dir / rel
        if not frame_path.is_file():
            raise DatasetError("manifest file is missing on disk: " + rel)
        if verify_hashes and entry.get("sha256"):
            actual = sha256_file(frame_path)
            if actual != entry["sha256"]:
                raise DatasetError("npz sha256 mismatch for %s (manifest %s, actual %s)"
                                   % (rel, entry["sha256"], actual))
    manifest["_path"] = manifest_path
    manifest["_dir"] = dataset_dir
    manifest["_sha256"] = sha256_file(manifest_path)
    return manifest


def _manifest_entries(manifest, split, labels=None, kinds=None):
    entries = [entry for entry in manifest["files"] if entry["split"] == split]
    if labels is not None:
        wanted = set(labels)
        entries = [entry for entry in entries if entry["label"] in wanted]
    if kinds is not None:
        wanted_kinds = set(kinds)
        entries = [entry for entry in entries if entry["label"] in wanted_kinds]
    if not entries:
        raise DatasetError("no manifest files for split %r (labels=%r kinds=%r)"
                           % (split, labels, kinds))
    return entries


def _validate_frame_archive(archive, path):
    missing = [key for key in FRAME_ARRAYS if key not in archive]
    if missing:
        raise DatasetError("npz %s is missing contract arrays: %s" % (path, ", ".join(missing)))
    x = np.asarray(archive["X"])
    if x.ndim != 3 or x.shape[1:] != (SAMPLES, 2):
        raise DatasetError("npz %s X shape %r is not (N, %d, 2)" % (path, x.shape, SAMPLES))
    if x.dtype != np.int16:
        raise DatasetError("npz %s X dtype %r is not int16" % (path, x.dtype))
    n = x.shape[0]
    for key in FRAME_ARRAYS:
        values = np.asarray(archive[key])
        if values.shape[0] != n:
            raise DatasetError("npz %s array %r length %d != X length %d"
                               % (path, key, values.shape[0], n))
    return n


def load_split(dataset_dir, manifest, split, labels=None, kinds=None,
               include_clipped=False, verify_file_hashes=True,
               verify_frame_hashes=True):
    """Load one split into stacked arrays, enforcing the frozen contract.

    Frames flagged ``clipped`` are dropped unless ``include_clipped`` is true
    (4-class test metrics must never see them; ``gate_test`` keeps CLIP frames).
    Per-frame ``iq_sha256`` and per-file manifest ``sha256`` are verified.
    """
    dataset_dir = Path(dataset_dir)
    entries = _manifest_entries(manifest, split, labels=labels, kinds=kinds)
    buckets = {key: [] for key in FRAME_ARRAYS}
    clipped_flags = []
    dropped = 0
    loaded_files = []
    for entry in entries:
        path = dataset_dir / entry["path"]
        if verify_file_hashes and entry.get("sha256"):
            actual = sha256_file(path)
            if actual != entry["sha256"]:
                raise DatasetError("npz sha256 mismatch for %s" % entry["path"])
        with np.load(path, allow_pickle=False) as archive:
            count = _validate_frame_archive(archive, path)
            keep = np.ones(count, dtype=bool)
            clipped = np.asarray(archive["clipped"], dtype=bool)
            if not include_clipped:
                keep = ~clipped
                dropped += int((~keep).sum())
            for key in FRAME_ARRAYS:
                values = np.asarray(archive[key])
                if verify_frame_hashes and key == "X":
                    recorded = [str(item) for item in np.asarray(archive["iq_sha256"])]
                    for index in np.nonzero(keep)[0]:
                        actual = sha256_bytes(np.ascontiguousarray(values[index]).astype("<i2").tobytes())
                        if actual != recorded[index]:
                            raise DatasetError(
                                "per-frame iq_sha256 mismatch in %s at index %d"
                                % (entry["path"], int(index)))
                buckets[key].append(values[keep])
            clipped_flags.append(clipped[keep])
        loaded_files.append(entry["path"])
    if not buckets["X"]:
        raise DatasetError("no usable frames for split %r" % (split,))
    stacked = {}
    for key in FRAME_ARRAYS:
        stacked[key] = np.concatenate(buckets[key], axis=0)
    stacked["_split"] = split
    stacked["_dropped_clipped"] = dropped
    stacked["_files"] = loaded_files
    stacked["_clipped_flags"] = np.concatenate(clipped_flags, axis=0)
    stacked["_include_clipped"] = include_clipped
    return stacked


def split_counts(manifest):
    counts = OrderedDict()
    for entry in manifest["files"]:
        key = (entry["split"], str(entry["label"]))
        record = counts.setdefault(key, {"path": entry["path"], "count": 0,
                                         "clipped_count": entry.get("clipped_count")})
        record["count"] = int(entry.get("count", 0))
    per_split = OrderedDict()
    for (split, label), record in counts.items():
        per_split.setdefault(split, {})[label] = record
    return per_split


# ---------------------------------------------------------------------------
# 4-class metrics (CONTRACTS §2, §10) - gate kinds are rejected
# ---------------------------------------------------------------------------
def _guard_label_space(values, allowed, context):
    bad = sorted({str(value) for value in values} - set(allowed))
    if bad:
        raise LabelLeakError(
            "%s contains labels outside %r: %r. Gate kinds %r may never enter the "
            "4-class matrix and 4-class labels may never be counted as gate results."
            % (context, list(allowed), bad, sorted(set(GATE_KINDS) & set(bad))))


def confusion_matrix(y_true, y_pred, labels=CLASS_ORDER):
    _guard_label_space(list(y_true), labels, "y_true")
    _guard_label_space(list(y_pred), labels, "y_pred")
    index = {label: position for position, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    for truth, predicted in zip(y_true, y_pred):
        matrix[index[str(truth)]][index[str(predicted)]] += 1
    return matrix


def classification_metrics(y_true, y_pred, labels=CLASS_ORDER):
    """Per-class precision/recall/F1 + macro-F1 + accuracy (zero division -> 0.0)."""
    labels = list(labels)
    matrix = confusion_matrix(y_true, y_pred, labels=labels)
    per_class = OrderedDict()
    f1_values = []
    for position, label in enumerate(labels):
        true_positive = matrix[position][position]
        support = sum(matrix[position])
        predicted_positive = sum(matrix[row][position] for row in range(len(labels)))
        precision = (true_positive / predicted_positive) if predicted_positive else 0.0
        recall = (true_positive / support) if support else 0.0
        if precision + recall > 0.0:
            f1 = 2.0 * precision * recall / (precision + recall)
        else:
            f1 = 0.0
        per_class[label] = OrderedDict([
            ("precision", precision), ("recall", recall), ("f1", f1),
            ("support", support), ("predicted", predicted_positive),
            ("true_positive", true_positive),
        ])
        f1_values.append(f1)
    total = sum(sum(row) for row in matrix)
    correct = sum(matrix[position][position] for position in range(len(labels)))
    return OrderedDict([
        ("labels", labels),
        ("confusion_matrix", matrix),
        ("per_class", per_class),
        ("macro_f1", float(np.mean(f1_values)) if f1_values else 0.0),
        ("accuracy", (correct / total) if total else 0.0),
        ("n_frames", total),
        ("correct", correct),
    ])


def round_snr(value):
    return float(np.round(float(value), 3))


def per_snr_metrics(y_true, y_pred, snr_values, labels=CLASS_ORDER):
    """Metrics per distinct SNR value (sorted ascending)."""
    _guard_label_space(list(y_true), labels, "y_true")
    _guard_label_space(list(y_pred), labels, "y_pred")
    if not (len(y_true) == len(y_pred) == len(snr_values)):
        raise EvaluationError("per_snr_metrics received inconsistent lengths")
    groups = OrderedDict()
    for truth, predicted, snr in zip(y_true, y_pred, snr_values):
        groups.setdefault(round_snr(snr), []).append((str(truth), str(predicted)))
    rows = []
    for snr in sorted(groups):
        pairs = groups[snr]
        metrics = classification_metrics([pair[0] for pair in pairs],
                                         [pair[1] for pair in pairs], labels=labels)
        present = [label for label in labels if metrics["per_class"][label]["support"]]
        present_f1 = [metrics["per_class"][label]["f1"] for label in present]
        rows.append(OrderedDict([
            ("snr_db", snr),
            ("n", metrics["n_frames"]),
            ("accuracy", metrics["accuracy"]),
            ("macro_f1", float(np.mean(present_f1)) if present_f1 else 0.0),
            ("macro_f1_all_classes", metrics["macro_f1"]),
            ("macro_f1_scope", "mean F1 over classes with support>0 at this SNR"),
            ("recall", OrderedDict(
                (label, (metrics["per_class"][label]["recall"]
                         if metrics["per_class"][label]["support"] else None))
                for label in labels)),
            ("support", OrderedDict(
                (label, metrics["per_class"][label]["support"]) for label in labels)),
            ("confusion_matrix", metrics["confusion_matrix"]),
        ]))
    return rows


def class_from_scores(prediction, classes=CLASS_ORDER):
    """Argmax class from a normalized prediction's 4-class scores."""
    scores = prediction.get("scores")
    if scores is None:
        raise EvaluationError(
            "predictor returned no class scores; 4-class metrics need the raw 4-class "
            "score vector (CONTRACTS §9)")
    scores = [float(value) for value in scores]
    if len(scores) != len(classes):
        raise EvaluationError("predictor returned %d scores for %d classes"
                              % (len(scores), len(classes)))
    return classes[int(np.argmax(scores))]


def prediction_is_rejected(prediction, classes=CLASS_ORDER):
    return str(prediction.get("label")) not in set(classes)


def four_class_report(truth_labels, predictions, snr_values=None, classes=CLASS_ORDER):
    """Full 4-class report from truth labels and normalized predictions."""
    _guard_label_space([str(item) for item in truth_labels], classes, "test truth")
    predicted = [class_from_scores(prediction, classes) for prediction in predictions]
    metrics = classification_metrics([str(item) for item in truth_labels], predicted,
                                     labels=classes)
    rejected = sum(1 for prediction in predictions
                   if prediction_is_rejected(prediction, classes))
    gated_correct = sum(1 for truth, prediction in zip(truth_labels, predictions)
                        if str(prediction.get("label")) == str(truth))
    total = metrics["n_frames"]
    metrics["gate_rejected_known_frames"] = rejected
    metrics["gate_rejection_rate"] = (rejected / total) if total else 0.0
    metrics["accuracy_with_gate"] = (gated_correct / total) if total else 0.0
    if snr_values is not None:
        metrics["per_snr"] = per_snr_metrics([str(item) for item in truth_labels],
                                             predicted, snr_values, labels=classes)
    return metrics


# ---------------------------------------------------------------------------
# quality-gate metrics (CONTRACTS §2, §10) - gate_test only, strictly separate
# ---------------------------------------------------------------------------
def gate_metrics(gate_rows, known_rows):
    """Rejection metrics for the quality gate.

    ``gate_rows``: iterable of ``{"kind": <GATE_KINDS>, "prediction": <normalized>}``
    ``known_rows``: iterable of ``{"label": <4-class truth>, "prediction": <normalized>}``

    Raises ``LabelLeakError`` when a gate row carries a 4-class label or a known
    row carries a gate kind: the gate population and the 4-class population must
    never be mixed.
    """
    gate_rows = list(gate_rows)
    known_rows = list(known_rows)
    _guard_label_space([row.get("kind") for row in gate_rows], GATE_KINDS, "gate rows")
    _guard_label_space([row.get("label") for row in known_rows], CLASS_ORDER,
                       "known-class rows")
    per_kind = OrderedDict()
    for kind in GATE_KINDS:
        rows = [row for row in gate_rows if row["kind"] == kind]
        rejected = 0
        accepted_labels = Counter()
        reasons = Counter()
        for row in rows:
            prediction = row["prediction"]
            if prediction_is_rejected(prediction):
                rejected += 1
                reasons[str(prediction.get("reason") or "unknown_reason")] += 1
            else:
                accepted_labels[str(prediction.get("label"))] += 1
        per_kind[kind] = OrderedDict([
            ("n", len(rows)),
            ("clipped_flag", sum(1 for row in rows if row.get("clipped_flag"))),
            ("rejected", rejected),
            ("rejection_rate", (rejected / len(rows)) if rows else 0.0),
            ("accepted_labels", OrderedDict(sorted(accepted_labels.items()))),
            ("reject_reasons", OrderedDict(sorted(reasons.items()))),
        ])
    known_rejected = 0
    per_class = OrderedDict()
    reasons = Counter()
    for label in CLASS_ORDER:
        rows = [row for row in known_rows if str(row["label"]) == label]
        rejected = sum(1 for row in rows if prediction_is_rejected(row["prediction"]))
        known_rejected += rejected
        per_class[label] = OrderedDict([
            ("n", len(rows)),
            ("rejected", rejected),
            ("rejection_rate", (rejected / len(rows)) if rows else 0.0),
        ])
    for row in known_rows:
        prediction = row["prediction"]
        if prediction_is_rejected(prediction):
            reasons[str(prediction.get("reason") or "unknown_reason")] += 1
    total_known = len(known_rows)
    return OrderedDict([
        ("split", "gate_test"),
        ("n_gate_frames", sum(record["n"] for record in per_kind.values())),
        ("per_kind", per_kind),
        ("known_class_false_rejection", OrderedDict([
            ("source", "independent test split (clipped=False) - gate metrics only; "
                       "these frames never enter the gate_test rejection rates"),
            ("n_frames", total_known),
            ("rejected", known_rejected),
            ("rate", (known_rejected / total_known) if total_known else 0.0),
            ("per_class", per_class),
            ("reject_reasons", OrderedDict(sorted(reasons.items()))),
        ])),
        ("separation", OrderedDict([
            ("gate_kinds", list(GATE_KINDS)),
            ("gate_frames_never_enter_four_class_matrix", True),
            ("four_class_labels_never_enter_gate_rates", True),
            ("note", "OFF/NOISE/TONE/CLIP rejection rates use gate_test only; the "
                     "known-class false-rejection rate uses test known frames only. "
                     "Neither population changes the 4x4 test confusion matrix."),
        ])),
    ])


# ---------------------------------------------------------------------------
# prediction normalisation and model adapters (CONTRACTS §9)
# ---------------------------------------------------------------------------
def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_prediction(result, classes=CLASS_ORDER):
    """Normalize any contract predictor output into one dict shape."""
    if not isinstance(result, dict):
        raise EvaluationError("predictor output is not a dict: %r" % (type(result),))
    scores = result.get("scores")
    scores_list = None
    if scores is not None:
        scores_list = [float(value) for value in np.asarray(scores).reshape(-1)]
    label = result.get("label")
    if label is not None:
        label = str(label)
    elif scores_list is not None:
        label = str(classes[int(np.argmax(scores_list))])
    score = _as_float(result.get("score"))
    margin = _as_float(result.get("margin"))
    if scores_list is not None:
        if score is None:
            score = max(scores_list)
        if margin is None and len(scores_list) > 1:
            ordered = sorted(scores_list, reverse=True)
            margin = ordered[0] - ordered[1]
    prediction = OrderedDict([
        ("label", label),
        ("reason", str(result.get("reason") or "")),
        ("score", score),
        ("margin", margin),
        ("scores", scores_list),
        ("gate", result.get("gate")),
    ])
    if "accepted" in result:
        prediction["accepted"] = bool(result["accepted"])
    if "error" in result:
        prediction["error"] = str(result["error"])
    if "fallback" in result:
        prediction["fallback"] = str(result["fallback"])
    return prediction


class Predictor(object):
    """Base wrapper: real model objects must provide ``predict_iq(iq_int16)``."""

    kind = "predictor"

    def __init__(self, model, info):
        self._model = model
        self.info = info

    def raw_predict(self, iq):
        raise NotImplementedError

    def predict_iq(self, iq):
        array = np.asarray(iq)
        if array.shape != (SAMPLES, 2):
            raise EvaluationError("IQ frame must have shape (%d, 2), got %r"
                                  % (SAMPLES, array.shape))
        if array.dtype.kind not in "iu":
            raise EvaluationError("IQ frame must be integer codes, got %r" % (array.dtype,))
        array = np.ascontiguousarray(array, dtype=np.int16)
        return normalize_prediction(self.raw_predict(array), self.info["classes"])


class NumpyRuntimePredictor(Predictor):
    kind = "cnn"

    def raw_predict(self, iq):
        return self._model.predict_iq(iq)


class BaselinePredictor(Predictor):
    kind = "baseline"

    def __init__(self, model, info, invoke):
        super().__init__(model, info)
        self._invoke = invoke

    def raw_predict(self, iq):
        return self._invoke(iq)


def _import_required_module(dotted, purpose):
    try:
        return importlib.import_module(dotted)
    except Exception as exc:  # ImportError or a broken dependency chain
        raise ArtifactMissing(
            "cannot import %s (%s): %s. The evaluation tools call the real model "
            "modules from CONTRACTS §9; they never re-implement model math."
            % (dotted, purpose, exc)) from exc


def resolve_artifact_path(path, filename="model.json"):
    """Return (artifact_dir, model_json_path) for a CLI --model/--baseline value."""
    path = Path(path)
    if path.is_dir():
        return path, path / filename
    return path.parent, path


def describe_artifact(path, expected_classes=CLASS_ORDER):
    """Read ``model.json`` metadata; returns (artifact_dir, json_path, info)."""
    artifact_dir, json_path = resolve_artifact_path(path)
    if not json_path.is_file():
        raise ArtifactMissing("model artefact JSON is missing: %s" % json_path)
    document = load_json_file(json_path)
    classes = list(document.get("classes") or expected_classes)
    if classes != list(expected_classes):
        raise EvaluationError("artefact classes %r are not the frozen order %r"
                              % (classes, list(expected_classes)))
    thresholds = document.get("thresholds") or {}
    info = OrderedDict([
        ("kind", None),
        ("artifact_path", relpath(artifact_dir)),
        ("model_json_path", relpath(json_path)),
        ("model_json_sha256", sha256_file(json_path)),
        ("network", document.get("network")),
        ("variant", document.get("variant")),
        ("preprocessing", document.get("preprocessing")),
        ("classes", classes),
        ("thresholds", thresholds),
        ("status", document.get("status")),
        ("params", document.get("params")),
        ("board_compat", document.get("board_compat")),
        ("training", document.get("training")),
        ("supported_domain", document.get("supported_domain")),
    ])
    return artifact_dir, json_path, info


def load_cnn_predictor(model_path):
    """Load the real numpy CNN runtime (CONTRACTS §9) - no local model math."""
    artifact_dir, json_path, info = describe_artifact(model_path)
    info["kind"] = "cnn"
    module = _import_required_module("ml_training.model.numpy_runtime",
                                     "CNN numpy runtime (load_model/predict_iq)")
    loader = getattr(module, "load_model", None)
    if not callable(loader):
        raise ArtifactMissing("ml_training/model/numpy_runtime.py has no load_model()")
    targets = [str(json_path), str(artifact_dir)]
    targets = list(dict.fromkeys(targets))  # keep order, drop exact duplicates
    last_error = None
    for target in targets:
        try:
            model = loader(target)
            return NumpyRuntimePredictor(model, info)
        except Exception as exc:  # e.g. loader expects the explicit model.json path
            last_error = exc
    raise ArtifactMissing("load_model(%r) failed: %s" % (targets[0], last_error))


def load_baseline_predictor(baseline_path):
    """Load the real feature baseline (CONTRACTS §9) - no local model math."""
    artifact_dir, json_path, info = describe_artifact(baseline_path)
    info["kind"] = "baseline"
    module = _import_required_module("ml_training.model.baseline",
                                     "feature baseline (load_baseline/predict_iq)")
    loader = getattr(module, "load_baseline", None)
    if not callable(loader):
        raise ArtifactMissing("ml_training/model/baseline.py has no load_baseline()")
    targets = [str(json_path), str(artifact_dir)]
    targets = list(dict.fromkeys(targets))
    last_error = None
    model = None
    for target in targets:
        try:
            model = loader(target)
            break
        except Exception as exc:
            last_error = exc
    if model is None:
        raise ArtifactMissing("load_baseline(%r) failed: %s" % (targets[0], last_error))
    if hasattr(model, "predict_iq") and callable(model.predict_iq):
        invoke = model.predict_iq
    elif callable(getattr(module, "predict_iq", None)):
        invoke = lambda iq, _model=model, _module=module: _module.predict_iq(_model, iq)
    elif isinstance(model, dict) and callable(model.get("predict_iq")):
        invoke = model["predict_iq"]
    else:
        raise ArtifactMissing(
            "ml_training/model/baseline.py exposes neither predict_iq(model, iq) nor a "
            "baseline object with predict_iq(); contract §9 requires one of them")
    return BaselinePredictor(model, info, invoke)


def out_of_signed12(iq):
    array = np.asarray(iq)
    return bool(np.any(array < MIN_CODE) or np.any(array > MAX_CODE))


def predict_safe(predictor, iq, allow_clipping_fallback=False):
    """Call a predictor; on out-of-range int16 codes fall back to a clipping reject.

    Gate CLIP frames are stored with quantization overflow (CONTRACTS §3: the frame
    is flagged ``clipped`` and kept).  If the real runtime refuses such values in its
    pre-processing range check, the contract-consistent outcome is a clipping reject,
    not a crash of the whole evaluation.
    """
    try:
        return predictor.predict_iq(iq)
    except Exception as exc:
        if allow_clipping_fallback and out_of_signed12(iq):
            return OrderedDict([
                ("label", UNKNOWN), ("reason", "clipping"), ("score", 0.0),
                ("margin", 0.0), ("scores", None),
                ("gate", {"ok": False, "reason": "clipping"}),
                ("fallback", "out_of_range_signed12_codes"),
                ("error", "%s: %s" % (type(exc).__name__, exc)),
            ])
        raise


def predict_many(predictor, frames, allow_clipping_fallback=False, label=""):
    """Predict every frame row; returns a list of normalized predictions."""
    predictions = []
    for index in range(frames.shape[0]):
        try:
            predictions.append(predict_safe(predictor, frames[index],
                                            allow_clipping_fallback=allow_clipping_fallback))
        except Exception as exc:
            raise EvaluationError("prediction failed for %s frame %d: %s"
                                  % (label or predictor.kind, index, exc)) from exc
    return predictions


# ---------------------------------------------------------------------------
# real IQ frame I/O (read-only, CONTRACTS §3)
# ---------------------------------------------------------------------------
def read_iq_frame(path):
    raw = Path(path).read_bytes()
    if len(raw) != FRAME_BYTES:
        raise DatasetError("IQ frame %s must be %d bytes, got %d"
                           % (path, FRAME_BYTES, len(raw)))
    return np.frombuffer(raw, dtype="<i2").reshape(SAMPLES, 2).astype(np.int16)


def count_overrides(values):
    """Count values outside the signed-12-bit capture range (clipping detection)."""
    array = np.asarray(values)
    return int(np.count_nonzero((array <= MIN_CODE) | (array >= MAX_CODE)))
