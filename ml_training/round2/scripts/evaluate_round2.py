"""Round-2 final evaluation on the NEW, untouched round-2 test / gate_test data.

Protocol
--------
* ``sim-r2/test`` and ``sim-r2/gate_test`` are read here **once** and were never used for
  calibration (calibration used ``sim-r2/val`` + ``sim-r2/gate_cal`` only).
* BEFORE  = frozen round-1 CNN runtime (``numpy_runtime.load_model(...).predict_iq``), whose
  gate thresholds are the frozen round-1 values.
* AFTER   = ``round2.predictor.Round2Predictor`` (frozen CNN + interpretable BPSK/QPSK
  arbitration + spectral tone/noise rejection).  No retraining, no new parameters.
* The 4-class metric is the pure class decision (argmax / arbitrated class) independent of
  gate rejection, matching the round-1 reporting protocol; gate rejection is reported
  separately (gate_test) together with known-class false rejection.
* Real physical frames are development replay only (they participated in round-1 analysis
  and round-2 design); an independent unseen real session is still required for any
  generalisation claim.

Writes ``round2/evaluation/reports/`` (JSON + markdown) and prints a summary.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402
from ml_training.round2.predictor import Round2Predictor, load_rules  # noqa: E402

ML = ROOT / "ml_training"
DATASET = ML / "round2" / "datasets" / "sim-r2"
REPORTS = ML / "round2" / "evaluation" / "reports"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / \
    "campaign5-val-followup" / "download"
INDEPENDENT_ROOT = ROOT / "evidence" / "rf-teaching" / "s2" / \
    "46db-trainval-campaign3-2026-10-18" / "download" / "train-01"
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
SESSIONS = ("train-01", "train-02", "val-01")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def softmax(logits):
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - logits.max()
    exp = np.exp(shifted)
    return exp / exp.sum()


def confusion(truth, predicted, num=4):
    matrix = np.zeros((num, num), dtype=np.int64)
    for expected, actual in zip(truth, predicted):
        matrix[int(expected), int(actual)] += 1
    return matrix


def class_metrics(matrix):
    f1, recall, precision = [], [], []
    for index in range(matrix.shape[0]):
        row = matrix[index].sum()
        column = matrix[:, index].sum()
        recall.append(float(matrix[index, index] / row) if row else 0.0)
        precision.append(float(matrix[index, index] / column) if column else 0.0)
    for r, p in zip(recall, precision):
        f1.append(2 * p * r / (p + r) if (p + r) else 0.0)
    accuracy = float(np.trace(matrix) / max(matrix.sum(), 1))
    return {"accuracy": accuracy, "macro_f1": float(np.mean(f1)),
            "recall": recall, "precision": precision, "f1": f1,
            "matrix": matrix.tolist()}


def frame_class(model, iq):
    """Pure class decision of the frozen round-1 CNN (gate ignored)."""
    x, gate = preprocess_iq(iq)
    if not gate["ok"]:
        return None
    return int(np.argmax(model.forward(x)))


def evaluate_sim(cnn, predictor):
    manifest = load_manifest(DATASET)
    test = load_split(DATASET, manifest, "test", labels=list(CLASSES))
    gate = load_split(DATASET, manifest, "gate_test", kinds=list(GATE_KINDS),
                      include_clipped=True)
    truth = np.asarray([CLASSES.index(str(label)) for label in test["label"]])
    snr = np.asarray(test["snr_db"], dtype=np.float64)

    plain_classes, plain_probs = [], []
    for iq in test["X"]:
        x, gate_ok = preprocess_iq(iq)
        probs = softmax(cnn.forward(x))
        plain_probs.append(probs)
        plain_classes.append(int(np.argmax(probs)))
    plain_classes = np.asarray(plain_classes)

    proposed_classes = np.asarray([predictor.classify(iq)["class_index"] for iq in test["X"]])
    plain_gate = [cnn.predict_iq(iq) for iq in test["X"]]
    proposed_gate = [predictor.decide(iq) for iq in test["X"]]

    plain_gate_test = [cnn.predict_iq(iq) for iq in gate["X"]]
    proposed_gate_test = [predictor.decide(iq) for iq in gate["X"]]
    kinds = np.asarray([str(kind) for kind in gate["label"]])
    gate_before = {kind: {"n": 0, "rejected": 0, "reasons": {}} for kind in GATE_KINDS}
    gate_after = {kind: {"n": 0, "rejected": 0, "reasons": {}} for kind in GATE_KINDS}
    for index, kind in enumerate(kinds):
        for table, values in ((gate_before, plain_gate_test),
                              (gate_after, proposed_gate_test)):
            table[kind]["n"] += 1
            if values[index]["label"] == "UNKNOWN":
                table[kind]["rejected"] += 1
                reason = values[index]["reason"] or "unknown"
                table[kind]["reasons"][reason] = table[kind]["reasons"].get(reason, 0) + 1

    def with_rate(table):
        return {kind: {"n": item["n"], "rejected": item["rejected"],
                       "rejection_rate": item["rejected"] / max(item["n"], 1),
                       "reasons": item["reasons"]}
                for kind, item in table.items()}

    result = {
        "protocol": {
            "test_used_for_tuning": False,
            "test_frames": int(len(truth)),
            "clipped_dropped": int(test["_dropped"]),
            "four_class_metric": "pure class decision, gate rejection reported separately",
        },
        "before": class_metrics(confusion(truth, plain_classes)),
        "after": class_metrics(confusion(truth, proposed_classes)),
        "before_accuracy": float(np.mean(plain_classes == truth)),
        "after_accuracy": float(np.mean(proposed_classes == truth)),
        "arbitration_changed": int(np.sum(plain_classes != proposed_classes)),
        "arbitration_changed_to_correct": int(np.sum((plain_classes != proposed_classes)
                                                     & (proposed_classes == truth))),
        "arbitration_changed_from_correct": int(np.sum((plain_classes != proposed_classes)
                                                       & (plain_classes == truth))),
        "gate_test": {
            "before": with_rate(gate_before),
            "after": with_rate(gate_after),
            "known_test": {
                "frames": int(len(truth)),
                "before_rejected": int(sum(1 for item in plain_gate
                                           if item["label"] == "UNKNOWN")),
                "after_rejected": int(sum(1 for item in proposed_gate
                                          if item["label"] == "UNKNOWN")),
                "before_accuracy_with_gate": float(np.mean(
                    [CLASSES.index(item["label"]) if item["label"] in CLASSES else -1
                     for item in plain_gate] == truth)),
                "after_accuracy_with_gate": float(np.mean(
                    [CLASSES.index(item["label"]) if item["label"] in CLASSES else -1
                     for item in proposed_gate] == truth)),
            },
        },
        "per_snr": {},
    }
    known_reasons = {"before": {}, "after": {}}
    for name, values in (("before", plain_gate), ("after", proposed_gate)):
        for item in values:
            if item["label"] == "UNKNOWN":
                reason = item["reason"] or "unknown"
                known_reasons[name][reason] = known_reasons[name].get(reason, 0) + 1
    per_snr_rejection = {}
    for value in sorted(set(np.round(snr, 3))):
        positions = np.where(np.isclose(snr, value))[0]
        per_snr_rejection["%.1f" % value] = {
            "n": int(positions.size),
            "before_rejected": int(sum(1 for position in positions
                                       if plain_gate[position]["label"] == "UNKNOWN")),
            "after_rejected": int(sum(1 for position in positions
                                      if proposed_gate[position]["label"] == "UNKNOWN")),
        }
    per_class_rejection = {}
    for index, label in enumerate(CLASSES):
        positions = np.where(truth == index)[0]
        per_class_rejection[label] = {
            "n": int(positions.size),
            "before_rejected": int(sum(1 for position in positions
                                       if plain_gate[position]["label"] == "UNKNOWN")),
            "after_rejected": int(sum(1 for position in positions
                                      if proposed_gate[position]["label"] == "UNKNOWN")),
        }
    accepted_after = [position for position in range(len(truth))
                      if proposed_gate[position]["label"] != "UNKNOWN"]
    accepted_after_accuracy = (float(np.mean(proposed_classes[accepted_after]
                                             == truth[accepted_after]))
                               if accepted_after else 0.0)
    result["gate_test"]["known_test"].update({
        "reasons_before": known_reasons["before"],
        "reasons_after": known_reasons["after"],
        "per_snr": per_snr_rejection,
        "per_class": per_class_rejection,
        "accepted_after": len(accepted_after),
        "accepted_after_accuracy": accepted_after_accuracy,
    })
    # per SNR (pure class metrics)
    for value in sorted(set(np.round(snr, 3))):
        mask = np.isclose(snr, value)
        before = class_metrics(confusion(truth[mask], plain_classes[mask]))
        after = class_metrics(confusion(truth[mask], proposed_classes[mask]))
        result["per_snr"]["%.1f" % value] = {
            "n": int(mask.sum()),
            "before_accuracy": before["accuracy"], "before_macro_f1": before["macro_f1"],
            "after_accuracy": after["accuracy"], "after_macro_f1": after["macro_f1"],
            "before_recall": before["recall"], "after_recall": after["recall"],
        }
    return result


def evaluate_real(predictor, cnn):
    rows = []
    for session in SESSIONS:
        for pattern in ("normal-*.iq", "off-*.iq"):
            for iq_path in sorted((EVIDENCE / session).glob(pattern)):
                meta = json.loads((EVIDENCE / session / (iq_path.stem + ".json"))
                                  .read_text(encoding="utf-8"))
                truth = meta.get("trainval", {}).get("truth")
                iq = read_frame(iq_path)
                plain = cnn.predict_iq(iq)
                proposed = predictor.decide(iq)
                rows.append({"session": session, "frame": iq_path.stem, "truth": truth,
                             "plain_label": plain["label"], "plain_reason": plain["reason"],
                             "proposed_label": proposed["label"],
                             "proposed_reason": proposed["reason"],
                             "arbitration_applied": proposed["arbitration_applied"]})
    summary = {}
    for session in SESSIONS:
        normal = [row for row in rows if row["session"] == session and row["truth"] == "normal"]
        off = [row for row in rows if row["session"] == session and row["truth"] == "off"]
        summary[session] = {
            "n_normal": len(normal), "n_off": len(off),
            "plain_bpsk": sum(1 for row in normal if row["plain_label"] == "BPSK"),
            "proposed_bpsk": sum(1 for row in normal if row["proposed_label"] == "BPSK"),
            "plain_bpsk_rate": sum(1 for row in normal if row["plain_label"] == "BPSK") / max(len(normal), 1),
            "proposed_bpsk_rate": sum(1 for row in normal if row["proposed_label"] == "BPSK") / max(len(normal), 1),
            "arbitration_applied": sum(1 for row in normal if row["arbitration_applied"]),
            "off_plain_rejected": sum(1 for row in off if row["plain_label"] == "UNKNOWN"),
            "off_proposed_rejected": sum(1 for row in off if row["proposed_label"] == "UNKNOWN"),
            "off_proposed_reasons": sorted(set(row["proposed_reason"] for row in off)),
        }
    totals = {
        "n_normal": sum(item["n_normal"] for item in summary.values()),
        "plain_bpsk": sum(item["plain_bpsk"] for item in summary.values()),
        "proposed_bpsk": sum(item["proposed_bpsk"] for item in summary.values()),
        "n_off": sum(item["n_off"] for item in summary.values()),
        "off_plain_rejected": sum(item["off_plain_rejected"] for item in summary.values()),
        "off_proposed_rejected": sum(item["off_proposed_rejected"] for item in summary.values()),
    }
    return {"sessions": summary, "totals": totals, "rows": rows,
            "scope": "development replay only; the three real sessions were used for round-1 "
                     "analysis and round-2 design. An independent unseen real session is still "
                     "required for generalisation claims."}


def evaluate_independent_real(predictor, cnn):
    """Independent physical session, discovered only after the round-2 rules were frozen.

    ``campaign3`` is a different board release/session with IQ hashes disjoint from the
    three development-replay sessions.  It was NOT used in round-1 analysis or round-2
    design/calibration, so it is the closest available zero-shot real check (BPSK only).
    """
    rows = []
    if not INDEPENDENT_ROOT.is_dir():
        return {"available": False, "rows": [], "summary": {}}
    for pattern in ("normal-*.iq", "off-*.iq"):
        for iq_path in sorted(INDEPENDENT_ROOT.glob(pattern)):
            meta_path = INDEPENDENT_ROOT / (iq_path.stem + ".json")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            truth = (meta.get("trainval") or {}).get("truth")
            demod = (meta.get("demod_result") or {}).get("result") or {}
            analysis = meta.get("analysis") or {}
            env_path = INDEPENDENT_ROOT / (iq_path.stem + ".environment.json")
            environment = {}
            if env_path.is_file():
                environment = json.loads(env_path.read_text(encoding="utf-8")).get("environment", {})
            iq = read_frame(iq_path)
            plain = cnn.predict_iq(iq)
            proposed = predictor.decide(iq)
            x, gate = preprocess_iq(iq)
            phase = None
            if gate["ok"]:
                z = x[0] + 1j * x[1]
                phase = float(np.degrees(np.angle(np.mean(z ** 2)) / 2.0))
            rows.append({"frame": iq_path.stem, "truth": truth,
                         "tx_config": (meta.get("demod_result") or {}).get("submitted", {}).get("config"),
                         "plain_label": plain["label"], "plain_reason": plain["reason"],
                         "proposed_label": proposed["label"],
                         "proposed_reason": proposed["reason"],
                         "arbitration_applied": proposed["arbitration_applied"],
                         "complex_rms": analysis.get("complex_rms"),
                         "phase_deg": phase,
                         "demod_ber": demod.get("ber"),
                         "environment_label": environment.get("label"),
                         "environment_reason": environment.get("reason")})
    normal = [row for row in rows if row["truth"] == "normal"]
    off = [row for row in rows if row["truth"] == "off"]
    summary = {
        "available": True,
        "session_path": str(INDEPENDENT_ROOT.relative_to(ROOT)).replace("\\", "/"),
        "release_id": json.loads((INDEPENDENT_ROOT / "normal-01.json").read_text(encoding="utf-8")).get("release_id"),
        "n_normal": len(normal), "n_off": len(off),
        "plain_bpsk": sum(1 for row in normal if row["plain_label"] == "BPSK"),
        "proposed_bpsk": sum(1 for row in normal if row["proposed_label"] == "BPSK"),
        "arbitration_applied": sum(1 for row in normal if row["arbitration_applied"]),
        "off_plain_rejected": sum(1 for row in off if row["plain_label"] == "UNKNOWN"),
        "off_proposed_rejected": sum(1 for row in off if row["proposed_label"] == "UNKNOWN"),
        "plain_failures": [row["frame"] for row in normal if row["plain_label"] != "BPSK"],
        "proposed_failures": [row["frame"] for row in normal if row["proposed_label"] != "BPSK"],
        "not_used_in_development": True,
        "scope": "independent physical BPSK session (single configuration); not a four-class "
                 "real accuracy and not ARM/board inference",
    }
    return {"available": True, "rows": rows, "summary": summary}


def measure_timing(cnn, predictor, frames, repeats=200, warmup=20):
    for index in range(min(warmup, len(frames))):
        cnn.predict_iq(frames[index])
    times_before = []
    for index in range(repeats):
        frame = frames[index % len(frames)]
        started = time.perf_counter()
        cnn.predict_iq(frame)
        times_before.append((time.perf_counter() - started) * 1000.0)
    for index in range(min(warmup, len(frames))):
        predictor.decide(frames[index])
    times_after = []
    for index in range(repeats):
        frame = frames[index % len(frames)]
        started = time.perf_counter()
        predictor.decide(frame)
        times_after.append((time.perf_counter() - started) * 1000.0)

    def summary(values):
        values = np.asarray(values)
        return {"median_ms": float(np.median(values)), "mean_ms": float(values.mean()),
                "p95_ms": float(np.percentile(values, 95)), "repeats": int(values.size)}

    return {"before_frozen_cnn": summary(times_before),
            "after_round2_predictor": summary(times_after),
            "host_only": True, "note": "host CPU, batch=1; NOT ARM and NOT board numbers"}


def model_sizes(predictor):
    cnn = ML / "artifacts" / "cnn" / "model.json"
    baseline = ML / "artifacts" / "baseline" / "model.json"
    rules = predictor.rules_path
    sizes = {name: path.stat().st_size for name, path in
             (("cnn_model_json", cnn), ("baseline_model_json", baseline),
              ("rule_config_json", rules))}
    sizes["total_bytes"] = int(sum(sizes.values()))
    sizes["cnn_parameters"] = 1628
    sizes["baseline_parameters"] = 36
    sizes["total_parameters"] = 1664
    sizes["note"] = ("round-1 CNN is unchanged; the 36-parameter baseline and the rule JSON "
                     "are the only additions (no retraining, no new learned parameters)")
    return sizes


def write_reports(sim, real, independent, timing, sizes):
    REPORTS.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "ml-modrec-round2-eval-v1",
        "generated_utc": utc_now(),
        "dataset": {"path": "ml_training/round2/datasets/sim-r2",
                    "manifest_sha256": __import__("hashlib").sha256(
                        (DATASET / "manifest.json").read_bytes()).hexdigest(),
                    "note": "test/gate_test were never used for calibration or design"},
        "model": sizes,
        "timing_host": timing,
        "sim_test": sim,
        "real_dev_replay": real,
        "real_independent_session": independent,
        "not_run": ["ARM/board latency, parity and deployment", "real four-class accuracy",
                    "further independent real sessions (only one BPSK session available)",
                    "RRC / other symbol rates", "hardware impairment modelling",
                    "large hyper-parameter search"],
    }
    (REPORTS / "round2_sim_metrics.json").write_text(json.dumps(document, indent=1),
                                                     encoding="utf-8")
    lines = ["# Round-2 simulated test (NEW seeds, not used for tuning)", "",
             "Dataset `sim-r2` manifest `%s`; test split 2,400 frames, 0 clipped." %
             document["dataset"]["manifest_sha256"], "",
             "4-class metric = pure class decision; gate rejection reported separately.", "",
             "## Confusion matrices", "",
             "### BEFORE (frozen round-1 CNN)", "",
             "| true \\ predicted | BPSK | QPSK | QAM16 | QAM64 |", "|---|---|---|---|---|"]
    for row, label in zip(sim["before"]["matrix"], CLASSES):
        lines.append("| %s | %s |" % (label, " | ".join(str(value) for value in row)))
    lines += ["", "### AFTER (round-2: CNN + BPSK/QPSK feature arbitration)", "",
              "| true \\ predicted | BPSK | QPSK | QAM16 | QAM64 |", "|---|---|---|---|---|"]
    for row, label in zip(sim["after"]["matrix"], CLASSES):
        lines.append("| %s | %s |" % (label, " | ".join(str(value) for value in row)))
    lines += ["", "## Per-class and summary", "",
              "| class | support | before P/R/F1 | after P/R/F1 |", "|---|---|---|---|"]
    for index, label in enumerate(CLASSES):
        support = sum(sim["before"]["matrix"][index])
        lines.append("| %s | %d | %.4f / %.4f / %.4f | %.4f / %.4f / %.4f |" % (
            label, support, sim["before"]["precision"][index], sim["before"]["recall"][index],
            sim["before"]["f1"][index], sim["after"]["precision"][index],
            sim["after"]["recall"][index], sim["after"]["f1"][index]))
    lines += ["", "| metric | before | after |", "|---|---|---|",
              "| accuracy | %.4f | %.4f |" % (sim["before_accuracy"], sim["after_accuracy"]),
              "| macro-F1 | %.4f | %.4f |" % (sim["before"]["macro_f1"], sim["after"]["macro_f1"]),
              "| arbitrated decisions changed | - | %d (+%d correct / -%d wrong) |" % (
                  sim["arbitration_changed"], sim["arbitration_changed_to_correct"],
                  sim["arbitration_changed_from_correct"]),
              "", "## Known-class false rejection (NEW test split)", "",
              "| gate | rejected known frames | rate | accuracy (rejections as errors) |",
              "|---|---|---|---|"]
    known = sim["gate_test"]["known_test"]
    total = sim["protocol"]["test_frames"]
    lines += ["| BEFORE (frozen round-1 gate) | %d | %.4f | %.4f |" % (
                  known["before_rejected"], known["before_rejected"] / total,
                  known["before_accuracy_with_gate"]),
              "| AFTER (round-2 gate) | %d | %.4f | %.4f |" % (
                  known["after_rejected"], known["after_rejected"] / total,
                  known["after_accuracy_with_gate"])]
    (REPORTS / "round2_sim_confusion.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    snr_lines = ["# Round-2 per-SNR (NEW test seeds)", "",
                 "| SNR dB | n | before acc | before macro-F1 | after acc | after macro-F1 |",
                 "|---|---|---|---|---|---|"]
    for snr, row in sim["per_snr"].items():
        snr_lines.append("| %s | %d | %.4f | %.4f | %.4f | %.4f |" % (
            snr, row["n"], row["before_accuracy"], row["before_macro_f1"],
            row["after_accuracy"], row["after_macro_f1"]))
    (REPORTS / "round2_sim_snr.md").write_text("\n".join(snr_lines) + "\n", encoding="utf-8")

    gate_lines = ["# Round-2 quality gate on NEW gate_test data", "",
                  "Known-class false rejection is measured on the NEW test split.", "",
                  "| kind | n | BEFORE rejected | BEFORE rate | AFTER rejected | AFTER rate |",
                  "|---|---|---|---|---|---|"]
    for kind in GATE_KINDS:
        before = sim["gate_test"]["before"][kind]
        after = sim["gate_test"]["after"][kind]
        gate_lines.append("| %s | %d | %d | %.4f | %d | %.4f |" % (
            kind, before["n"], before["rejected"], before["rejection_rate"],
            after["rejected"], after["rejection_rate"]))
    gate_lines += ["", "Rejection reasons (AFTER):"]
    for kind in GATE_KINDS:
        gate_lines.append("- %s: %s" % (kind, json.dumps(sim["gate_test"]["after"][kind]["reasons"])))
    known = sim["gate_test"]["known_test"]
    total = sim["protocol"]["test_frames"]
    gate_lines += ["", "Known false rejection: BEFORE %d/%d, AFTER %d/%d." % (
        known["before_rejected"], total, known["after_rejected"], total), "",
        "Known rejection reasons (AFTER): %s" % json.dumps(known["reasons_after"]), "",
        "Accepted known frames (AFTER): %d/%d, accuracy among accepted %.4f." % (
            known["accepted_after"], total, known["accepted_after_accuracy"]), "",
        "Known false rejection per SNR (AFTER):", "",
        "| SNR dB | n | before rejected | after rejected |", "|---|---|---|---|"]
    for snr, row in known["per_snr"].items():
        gate_lines.append("| %s | %d | %d | %d |" % (
            snr, row["n"], row["before_rejected"], row["after_rejected"]))
    gate_lines += ["", "Known false rejection per class (AFTER):", "",
                   "| class | n | before rejected | after rejected |", "|---|---|---|---|"]
    for label, row in known["per_class"].items():
        gate_lines.append("| %s | %d | %d | %d |" % (
            label, row["n"], row["before_rejected"], row["after_rejected"]))
    (REPORTS / "round2_gate.md").write_text("\n".join(gate_lines) + "\n", encoding="utf-8")

    real_lines = ["# Round-2 real dev replay (read-only physical frames)", "",
                  "**Scope**: the three real sessions were used in round-1 failure analysis and "
                  "round-2 design; this is development replay, not an independent generalisation "
                  "test. Truth only from `trainval.truth` + `demod_result.submitted.config`.", "",
                  "| session | normal | BEFORE BPSK | AFTER BPSK | arbitrated | OFF BEFORE rejected | OFF AFTER rejected |",
                  "|---|---|---|---|---|---|---|"]
    for session, item in real["sessions"].items():
        real_lines.append("| %s | %d | %d/%d | %d/%d | %d | %d/%d | %d/%d |" % (
            session, item["n_normal"], item["plain_bpsk"], item["n_normal"],
            item["proposed_bpsk"], item["n_normal"], item["arbitration_applied"],
            item["off_plain_rejected"], item["n_off"], item["off_proposed_rejected"],
            item["n_off"]))
    real_lines += ["", "OFF rejection reasons (AFTER): %s" % (
        json.dumps({session: item["off_proposed_reasons"]
                    for session, item in real["sessions"].items()})),
        "", "Failure frames (AFTER):"]
    failures = [row for row in real["rows"]
                if (row["truth"] == "normal" and row["proposed_label"] != "BPSK")
                or (row["truth"] == "off" and row["proposed_label"] != "UNKNOWN")]
    if failures:
        for row in failures:
            real_lines.append("- %s/%s: after=%s reason=%s" % (
                row["session"], row["frame"], row["proposed_label"],
                row["proposed_reason"]))
    else:
        real_lines.append("- none")
    (REPORTS / "round2_real.md").write_text("\n".join(real_lines) + "\n", encoding="utf-8")
    (REPORTS / "round2_real.json").write_text(json.dumps(real, indent=1), encoding="utf-8")

    independent_lines = ["# Round-2 independent physical session (read-only, NOT used in development)", "",
                         "Session: `%s` (release `%s`), 10 normal BPSK + 1 OFF frame, "
                         "TX config BPSK/3 MSym/s/RECT. IQ hashes are disjoint from the three "
                         "development-replay sessions; the rules were frozen before this session "
                         "was evaluated." % (
                             independent["summary"].get("session_path"),
                             independent["summary"].get("release_id")), "",
                         "| session | normal | BEFORE BPSK | AFTER BPSK | arbitrated | OFF BEFORE rejected | OFF AFTER rejected |",
                         "|---|---|---|---|---|---|---|",
                         "| campaign3 train-01 (independent) | %d | %d/%d | %d/%d | %d | %d/%d | %d/%d |" % (
                             independent["summary"].get("n_normal", 0),
                             independent["summary"].get("plain_bpsk", 0),
                             independent["summary"].get("n_normal", 0),
                             independent["summary"].get("proposed_bpsk", 0),
                             independent["summary"].get("n_normal", 0),
                             independent["summary"].get("arbitration_applied", 0),
                             independent["summary"].get("off_plain_rejected", 0),
                             independent["summary"].get("n_off", 0),
                             independent["summary"].get("off_proposed_rejected", 0),
                             independent["summary"].get("n_off", 0)),
                         "", "Per-frame results:", ""]
    for row in independent["rows"]:
        independent_lines.append(
            "- `%s` truth=%s rms=%s phase=%s | before=%s (%s) | after=%s (%s)" % (
                row["frame"], row["truth"], row["complex_rms"],
                None if row["phase_deg"] is None else round(row["phase_deg"], 2),
                row["plain_label"], row["plain_reason"],
                row["proposed_label"], row["proposed_reason"]))
    independent_lines += ["", "Scope: independent physical BPSK session only (single configuration; "
                          "not a four-class real accuracy and not ARM/board inference)."]
    (REPORTS / "round2_independent_real.md").write_text("\n".join(independent_lines) + "\n",
                                                        encoding="utf-8")
    (REPORTS / "round2_independent_real.json").write_text(json.dumps(independent, indent=1),
                                                          encoding="utf-8")
    return document


def main():
    cnn = load_model(ML / "artifacts" / "cnn" / "model.json")
    predictor = Round2Predictor()
    manifest = load_manifest(DATASET)
    val = load_split(DATASET, manifest, "val", labels=list(CLASSES))
    timing = measure_timing(cnn, predictor, list(val["X"]))
    sizes = model_sizes(predictor)
    sim = evaluate_sim(cnn, predictor)
    real = evaluate_real(predictor, cnn)
    independent = evaluate_independent_real(predictor, cnn)
    document = write_reports(sim, real, independent, timing, sizes)
    print(json.dumps({
        "sim_before_accuracy": sim["before_accuracy"],
        "sim_after_accuracy": sim["after_accuracy"],
        "sim_before_macro_f1": sim["before"]["macro_f1"],
        "sim_after_macro_f1": sim["after"]["macro_f1"],
        "arbitration_changed": sim["arbitration_changed"],
        "known_false_rejection_before": sim["gate_test"]["known_test"]["before_rejected"],
        "known_false_rejection_after": sim["gate_test"]["known_test"]["after_rejected"],
        "gate_before": {kind: sim["gate_test"]["before"][kind]["rejection_rate"]
                        for kind in GATE_KINDS},
        "gate_after": {kind: sim["gate_test"]["after"][kind]["rejection_rate"]
                       for kind in GATE_KINDS},
        "real_before": {session: item["plain_bpsk"] for session, item in real["sessions"].items()},
        "real_after": {session: item["proposed_bpsk"] for session, item in real["sessions"].items()},
        "independent_real": independent["summary"],
        "timing": timing, "sizes": sizes,
    }, indent=1))


if __name__ == "__main__":
    main()
