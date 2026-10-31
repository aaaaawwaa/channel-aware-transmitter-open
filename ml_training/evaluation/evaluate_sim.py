#!/usr/bin/env python
"""Evaluate the 4-class CNN and the feature baseline on the simulated test split.

Contract: ``ml_training/docs/CONTRACTS.md`` §2, §9, §10.

* 4-class metrics use ONLY the independent ``test`` split and ONLY frames with
  ``clipped=False`` (CONTRACTS §3/§6); the classifier decision is the argmax over
  the four class scores.
* Quality-gate metrics use ONLY ``gate_test`` (OFF/NOISE/TONE/CLIP) plus, for the
  known-class false-rejection rate, the known ``test`` frames.  The gate
  population never enters the 4x4 confusion matrix; label-space mixing raises.
* All inference is host CPU numpy via the Lead's ``ml_training/model/numpy_runtime.py``
  and ``ml_training/model/baseline.py`` (called, never re-implemented).

Outputs (under ``--out``): ``sim_metrics.json``, ``sim_confusion.md``,
``sim_snr.md``, ``gate.md``.  Exit code 0 on success.
"""
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml_training.evaluation import _common as C  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "ml_training" / "evaluation" / "reports"

NOT_RUN = list(C.HOST_NOT_RUN) + [
    "real_data_transfer: NOT_RUN here - real (physical) data is evaluated separately and "
    "read-only by ml_training/evaluation/real_transfer.py; it is not mixed into simulated metrics.",
    "val_split_metrics: NOT_RUN - the val split is reserved for checkpoint/threshold selection "
    "(CONTRACTS §6/§13); this script reports the independent test split only.",
    "train_split_metrics: NOT_RUN - training split accuracy is not an evaluation metric.",
    "baseline_gate_metrics: NOT_RUN - the feature baseline has no rejection gate in "
    "CONTRACTS §9; only the CNN quality gate is characterised on gate_test.",
    "arm_latency: NOT_RUN - no latency measurement was performed by this script.",
]

LIMITATIONS = [
    "Metrics are single-run point estimates on the frozen test split; no confidence "
    "intervals are computed.",
    "The synthetic impairment model explicitly excludes IQ imbalance, DC offset, receive "
    "filter/RRC distortion, multipath, phase noise, AGC transients, hardware frequency "
    "response and RF-chain nonlinearity (CONTRACTS §5); these are unmodeled, not calibrated.",
    "gate_test CLIP frames are kept even when their quantization overflow sets clipped=True; "
    "they are used only for the gate rejection rates, never for the 4x4 matrix.",
    "Per-SNR macro-F1 is the mean F1 over classes with support>0 at that SNR; recall is "
    "null for a class with no support at that SNR.",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate 4-class CNN + feature baseline on the simulated dataset "
                    "(host CPU numpy; NOT ARM/board).")
    parser.add_argument("--dataset", required=True,
                        help="dataset directory containing manifest.json (e.g. ml_training/datasets/sim-v1)")
    parser.add_argument("--model", required=True,
                        help="CNN artefact directory (contains model.json) or model.json path")
    parser.add_argument("--baseline", required=True,
                        help="feature-baseline artefact directory (contains model.json) or model.json path")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output directory for sim_metrics.json / *.md")
    parser.add_argument("--no-verify-hashes", action="store_true",
                        help="skip sha256 verification of npz files and per-frame iq_sha256")
    return parser.parse_args(argv)


def _dataset_document(manifest):
    per_split = C.split_counts(manifest)
    return OrderedDict([
        ("dataset_id", manifest.get("dataset_id")),
        ("directory", C.relpath(manifest["_dir"])),
        ("manifest_path", C.relpath(manifest["_path"])),
        ("manifest_sha256", manifest["_sha256"]),
        ("schema", manifest.get("schema")),
        ("config_path", manifest.get("config_path")),
        ("config_sha256", manifest.get("config_sha256")),
        ("created_utc", manifest.get("created_utc")),
        ("classes", manifest.get("classes")),
        ("gate_kinds", manifest.get("gate_kinds")),
        ("numpy_version", manifest.get("numpy_version")),
        ("files", manifest.get("files")),
        ("per_split_counts", per_split),
        ("summary", manifest.get("summary")),
    ])


def _comparison(cnn_report, baseline_report):
    per_class = OrderedDict()
    for label in C.CLASS_ORDER:
        cnn = cnn_report["per_class"][label]
        baseline = baseline_report["per_class"][label]
        per_class[label] = OrderedDict([
            ("cnn_recall", cnn["recall"]), ("baseline_recall", baseline["recall"]),
            ("recall_delta_cnn_minus_baseline", cnn["recall"] - baseline["recall"]),
            ("cnn_f1", cnn["f1"]), ("baseline_f1", baseline["f1"]),
            ("f1_delta_cnn_minus_baseline", cnn["f1"] - baseline["f1"]),
        ])
    return OrderedDict([
        ("accuracy", OrderedDict([
            ("cnn", cnn_report["accuracy"]), ("baseline", baseline_report["accuracy"]),
            ("delta_cnn_minus_baseline", cnn_report["accuracy"] - baseline_report["accuracy"]),
        ])),
        ("macro_f1", OrderedDict([
            ("cnn", cnn_report["macro_f1"]), ("baseline", baseline_report["macro_f1"]),
            ("delta_cnn_minus_baseline", cnn_report["macro_f1"] - baseline_report["macro_f1"]),
        ])),
        ("per_class", per_class),
        ("note", "Delta is CNN minus feature baseline on the identical test frames."),
    ])


def evaluate(dataset_dir, cnn_predictor, baseline_predictor, out_dir, verify_hashes=True):
    """Run the full simulated evaluation; returns the sim_metrics document."""
    dataset_dir = Path(dataset_dir)
    out_dir = Path(out_dir)
    manifest = C.load_manifest(dataset_dir, verify_hashes=verify_hashes)

    test_frames = C.load_split(dataset_dir, manifest, "test", labels=C.CLASS_ORDER,
                               include_clipped=False,
                               verify_file_hashes=verify_hashes,
                               verify_frame_hashes=verify_hashes)
    truth = [str(label) for label in test_frames["label"]]
    snr_values = [float(value) for value in test_frames["snr_db"]]
    cnn_predictions = C.predict_many(cnn_predictor, test_frames["X"], label="cnn test")
    baseline_predictions = C.predict_many(baseline_predictor, test_frames["X"],
                                          label="baseline test")
    cnn_report = C.four_class_report(truth, cnn_predictions, snr_values)
    baseline_report = C.four_class_report(truth, baseline_predictions, snr_values)

    gate_frames = C.load_split(dataset_dir, manifest, "gate_test", kinds=C.GATE_KINDS,
                               include_clipped=True,
                               verify_file_hashes=verify_hashes,
                               verify_frame_hashes=verify_hashes)
    gate_predictions = C.predict_many(cnn_predictor, gate_frames["X"],
                                      allow_clipping_fallback=True, label="cnn gate_test")
    gate_rows = [{"kind": str(kind), "prediction": prediction, "clipped_flag": bool(flag)}
                 for kind, prediction, flag in zip(gate_frames["label"], gate_predictions,
                                                   gate_frames["_clipped_flags"])]
    known_rows = [{"label": label, "prediction": prediction}
                  for label, prediction in zip(truth, cnn_predictions)]
    gate = C.gate_metrics(gate_rows, known_rows)

    baseline_rejected_known = sum(1 for prediction in baseline_predictions
                                  if C.prediction_is_rejected(prediction))
    gate["baseline"] = OrderedDict([
        ("status", "NOT_RUN"),
        ("reason", "feature baseline has no quality gate in CONTRACTS §9; only the CNN "
                   "quality gate is characterised. Informational baseline label-rejection "
                   "counts are recorded in sim_metrics.json."),
        ("known_frames_rejected_by_label", baseline_rejected_known),
    ])

    protocol = OrderedDict([
        ("four_class_population", "test split only; clipped=False frames only"),
        ("four_class_prediction", "argmax over the four class scores returned by the "
                                  "predictor; a quality-gate rejection does not change the "
                                  "4-class prediction"),
        ("gate_population", "gate_test split only (OFF/NOISE/TONE/CLIP); CLIP frames kept"),
        ("gate_known_false_rejection_population", "known test frames (clipped=False); gate "
                                                  "metrics only, never the 4x4 matrix"),
        ("test_evaluated_once", True),
        ("selection_discipline", "model/threshold selection used only train/val/gate_cal by "
                                 "the Lead; this script reports the independent test split once"),
        ("host_only", True),
        ("arm_or_board_numbers", "NOT_RUN - no ARM/board execution in this script"),
        ("truth_source", "synthetic dataset npz labels (manifest split isolation, CONTRACTS §6)"),
        ("n_test_frames", len(truth)),
        ("n_test_frames_dropped_clipped", test_frames["_dropped_clipped"]),
        ("n_gate_test_frames", int(gate_frames["X"].shape[0])),
        ("n_gate_test_clipped_flagged", int(sum(gate_frames["_clipped_flags"]))),
    ])

    document = OrderedDict([
        ("schema", C.EVAL_SCHEMA_SIM),
        ("generated_utc", C.utc_now()),
        ("host", C.host_environment()),
        ("dataset", _dataset_document(manifest)),
        ("model", cnn_predictor.info),
        ("baseline", baseline_predictor.info),
        ("protocol", protocol),
        ("cnn", cnn_report),
        ("baseline_report", baseline_report),
        ("comparison", _comparison(cnn_report, baseline_report)),
        ("gate", gate),
        ("not_run", NOT_RUN),
        ("limitations", LIMITATIONS),
    ])

    out_dir.mkdir(parents=True, exist_ok=True)
    C.write_json(out_dir / "sim_metrics.json", document)
    C.write_text(out_dir / "sim_confusion.md", render_confusion_md(document))
    C.write_text(out_dir / "sim_snr.md", render_snr_md(document))
    C.write_text(out_dir / "gate.md", render_gate_md(document))
    return document


# ---------------------------------------------------------------------------
# markdown renderers
# ---------------------------------------------------------------------------
def _artifact_line(info):
    thresholds = info.get("thresholds") or {}
    return ("artifact `%s`, model.json sha256 `%s`, status `%s`, thresholds "
            "max_softmax=%s margin=%s min_complex_rms=%s"
            % (info.get("artifact_path"), info.get("model_json_sha256"),
               info.get("status"), thresholds.get("max_softmax"),
               thresholds.get("margin"), thresholds.get("min_complex_rms")))


def _confusion_table(report):
    labels = report["labels"]
    rows = []
    for row_index, label in enumerate(labels):
        counts = report["confusion_matrix"][row_index]
        rows.append([label] + list(counts) + [sum(counts)])
    return C.md_table(["true \\ predicted"] + labels + ["row total"], rows)


def _per_class_table(cnn_report, baseline_report):
    rows = []
    for label in C.CLASS_ORDER:
        cnn = cnn_report["per_class"][label]
        baseline = baseline_report["per_class"][label]
        rows.append([label, cnn["support"],
                     C.fmt(cnn["precision"]), C.fmt(cnn["recall"]), C.fmt(cnn["f1"]),
                     C.fmt(baseline["precision"]), C.fmt(baseline["recall"]),
                     C.fmt(baseline["f1"])])
    return C.md_table(["class", "support", "CNN P", "CNN R", "CNN F1",
                       "baseline P", "baseline R", "baseline F1"], rows)


def render_confusion_md(document):
    protocol = document["protocol"]
    cnn = document["cnn"]
    baseline = document["baseline_report"]
    comparison = document["comparison"]
    lines = [
        "# Simulated 4-class test confusion matrix",
        "",
        "Generated: %s  " % document["generated_utc"],
        "Dataset: `%s` (manifest sha256 `%s`)  "
        % (document["dataset"]["directory"], document["dataset"]["manifest_sha256"]),
        "CNN: %s  " % _artifact_line(document["model"]),
        "Baseline: %s  " % _artifact_line(document["baseline"]),
        "",
        "## Protocol (frozen separation)",
        "",
        "- 4-class population: **test split only, clipped=False frames only** "
        "(%d frames used, %d dropped as clipped)."
        % (protocol["n_test_frames"], protocol["n_test_frames_dropped_clipped"]),
        "- Prediction: %s." % protocol["four_class_prediction"],
        "- Quality-gate results are reported separately in `gate.md` and never enter "
        "the 4x4 matrix.",
        "- Host CPU numpy runtime: these are **NOT ARM and NOT board numbers**.",
        "",
        "## CNN confusion matrix",
        "",
        _confusion_table(cnn),
        "",
        "## Feature-baseline confusion matrix",
        "",
        _confusion_table(baseline),
        "",
        "## Per-class precision / recall / F1",
        "",
        _per_class_table(cnn, baseline),
        "",
        "## Summary",
        "",
        C.md_table(
            ["metric", "CNN", "baseline", "delta (CNN - baseline)"],
            [["accuracy", C.fmt(cnn["accuracy"]), C.fmt(baseline["accuracy"]),
              C.fmt(comparison["accuracy"]["delta_cnn_minus_baseline"])],
             ["macro-F1", C.fmt(cnn["macro_f1"]), C.fmt(baseline["macro_f1"]),
              C.fmt(comparison["macro_f1"]["delta_cnn_minus_baseline"])],
             ["gate-rejected known frames (count)",
              cnn["gate_rejected_known_frames"], baseline["gate_rejected_known_frames"],
              cnn["gate_rejected_known_frames"] - baseline["gate_rejected_known_frames"]],
             ["accuracy with gate rejections counted as errors",
              C.fmt(cnn["accuracy_with_gate"]), C.fmt(baseline["accuracy_with_gate"]),
              C.fmt(cnn["accuracy_with_gate"] - baseline["accuracy_with_gate"])]]),
        "",
        "`accuracy with gate rejections` is informational: the frozen 4-class metric is "
        "the pure argmax accuracy above.",
        "",
        "## NOT_RUN",
        "",
    ]
    lines += ["- %s" % item for item in document["not_run"]]
    lines += ["", "## Limitations", ""]
    lines += ["- %s" % item for item in document["limitations"]]
    return "\n".join(lines) + "\n"


def _snr_table(report):
    rows = []
    for entry in report["per_snr"]:
        recall = entry["recall"]
        rows.append([entry["snr_db"], entry["n"], C.fmt(entry["accuracy"]),
                     C.fmt(entry["macro_f1"])]
                    + [C.fmt(recall[label]) for label in C.CLASS_ORDER])
    return C.md_table(["SNR (dB)", "n", "accuracy", "macro-F1",
                       "recall BPSK", "recall QPSK", "recall QAM16", "recall QAM64"], rows)


def render_snr_md(document):
    cnn = document["cnn"]
    baseline = document["baseline_report"]
    lines = [
        "# Simulated per-SNR metrics (4-class test split)",
        "",
        "Generated: %s  " % document["generated_utc"],
        "Dataset: `%s`  " % document["dataset"]["directory"],
        "CNN: %s  " % _artifact_line(document["model"]),
        "Baseline: %s  " % _artifact_line(document["baseline"]),
        "",
        "Population: test split only, clipped=False; `n` is the number of frames at that "
        "SNR. Per-SNR macro-F1 is the mean F1 over classes with support>0 at that SNR; "
        "a recall of `-` means no frame of that class at that SNR.",
        "",
        "## CNN",
        "",
        _snr_table(cnn),
        "",
        "## Feature baseline",
        "",
        _snr_table(baseline),
        "",
        "Quality-gate rejection rates are not part of these tables (see `gate.md`). "
        "Host CPU numpy runtime, NOT ARM/board.",
        "",
        "## NOT_RUN",
        "",
    ]
    lines += ["- %s" % item for item in document["not_run"]]
    return "\n".join(lines) + "\n"


def render_gate_md(document):
    gate = document["gate"]
    per_kind = gate["per_kind"]
    known = gate["known_class_false_rejection"]
    rows = []
    for kind in C.GATE_KINDS:
        record = per_kind[kind]
        accepted = ", ".join("%s:%d" % pair for pair in record["accepted_labels"].items()) or "-"
        reasons = ", ".join("%s:%d" % pair for pair in record["reject_reasons"].items()) or "-"
        rows.append([kind, record["n"], record["clipped_flag"], record["rejected"],
                     C.fmt(record["rejection_rate"]), accepted, reasons])
    known_rows = []
    for label in C.CLASS_ORDER:
        record = known["per_class"][label]
        known_rows.append([label, record["n"], record["rejected"],
                           C.fmt(record["rejection_rate"])])
    known_reasons = ", ".join("%s:%d" % pair for pair in known["reject_reasons"].items()) or "-"
    lines = [
        "# Quality-gate / rejection report (gate_test)",
        "",
        "Generated: %s  " % document["generated_utc"],
        "CNN: %s  " % _artifact_line(document["model"]),
        "",
        "## Strict separation from the 4-class matrix",
        "",
        "- Gate population: **gate_test split only** (OFF / NOISE / TONE / CLIP).",
        "- Known-class false-rejection population: **test split known frames only** "
        "(clipped=False).",
        "- Neither population is added to, or subtracted from, the 4x4 test confusion "
        "matrix (`sim_confusion.md`). Mixing the label spaces raises `LabelLeakError`.",
        "",
        "## gate_test rejection rates per kind",
        "",
        C.md_table(["kind", "n", "clipped flag", "rejected", "rejection rate",
                    "accepted labels (false accepts)", "rejection reasons"], rows),
        "",
        "A rejection is any predictor output whose label is not one of the four known "
        "classes (i.e. `UNKNOWN`); the reason column shows the gate reason.",
        "",
        "## Known-class false rejection (known test frames)",
        "",
        C.md_table(["class", "n", "rejected", "rejection rate"], known_rows),
        "",
        "Overall: %d / %d known frames rejected (rate %s); reasons: %s."
        % (known["rejected"], known["n_frames"], C.fmt(known["rate"]), known_reasons),
        "",
        "## Baseline quality gate",
        "",
        "Status: **%s** - %s" % (gate["baseline"]["status"], gate["baseline"]["reason"]),
        "",
        "Informational: the feature baseline rejected %s known test frames by label only "
        "(it has no gate)." % gate["baseline"]["known_frames_rejected_by_label"],
        "",
        "## NOT_RUN",
        "",
    ]
    lines += ["- %s" % item for item in document["not_run"]]
    return "\n".join(lines) + "\n"


def _print_summary(document):
    cnn = document["cnn"]
    baseline = document["baseline_report"]
    print("dataset: %s (%d test frames, %d clipped dropped)"
          % (document["dataset"]["directory"], document["protocol"]["n_test_frames"],
             document["protocol"]["n_test_frames_dropped_clipped"]))
    print("CNN     : accuracy=%.4f macro_F1=%.4f gate-rejected-known=%d/%d"
          % (cnn["accuracy"], cnn["macro_f1"], cnn["gate_rejected_known_frames"],
             cnn["n_frames"]))
    print("baseline: accuracy=%.4f macro_F1=%.4f"
          % (baseline["accuracy"], baseline["macro_f1"]))
    for kind in C.GATE_KINDS:
        record = document["gate"]["per_kind"][kind]
        print("gate_test %-5s rejection=%.4f (%d/%d)"
              % (kind, record["rejection_rate"], record["rejected"], record["n"]))
    print("known false rejection=%.4f (%d/%d)"
          % (document["gate"]["known_class_false_rejection"]["rate"],
             document["gate"]["known_class_false_rejection"]["rejected"],
             document["gate"]["known_class_false_rejection"]["n_frames"]))


def main(argv=None):
    args = parse_args(argv)
    try:
        cnn_predictor = C.load_cnn_predictor(args.model)
        baseline_predictor = C.load_baseline_predictor(args.baseline)
    except C.ArtifactMissing as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    try:
        document = evaluate(Path(args.dataset), cnn_predictor, baseline_predictor,
                            Path(args.out), verify_hashes=not args.no_verify_hashes)
    except (C.EvaluationError, C.DatasetError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    _print_summary(document)
    print("wrote: %s" % (Path(args.out) / "sim_metrics.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
