#!/usr/bin/env python3
"""Diagnose the three real QAM16 -> QAM64 errors (read-only analysis).

Only reads evidence, the frozen model and hardware_amp60 payloads.  All
transform variants are explicitly diagnostic: they are NOT new real frames and
must not be used as independent acceptance.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evidence" / "rf-teaching" / "s2-46db-fourclass-real-rx-2026-10-22"
RUN = DATA / "run"
BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results"
AMP60 = ROOT / "ml_training" / "fourclass_test_vectors" / "hardware_amp60"
EVAL = RESULTS / "task68_eval" / "task68_result.json"

sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.features import extract_features, FEATURE_NAMES  # noqa: E402
from ml_training.round2.features import spectral_features  # noqa: E402
from ml_training.round2.predictor import CLASSES, Round2Predictor  # noqa: E402


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_iq(path):
    return np.frombuffer(Path(path).read_bytes(), dtype="<i2").reshape(-1, 2).copy()


def stats(iq):
    z = iq[:, 0].astype(np.float64) + 1j * iq[:, 1].astype(np.float64)
    centered = iq.astype(np.float64) - iq.astype(np.float64).mean(axis=0)
    c = centered[:, 0] + 1j * centered[:, 1]
    return {
        "samples": int(iq.shape[0]),
        "dtype": str(iq.dtype),
        "min_code": int(iq.min()),
        "max_code": int(iq.max()),
        "max_abs_code": int(np.abs(iq.astype(np.int32)).max()),
        "dc_i": float(iq[:, 0].mean()),
        "dc_q": float(iq[:, 1].mean()),
        "complex_rms_after_dc": float(np.sqrt(np.mean(np.abs(c) ** 2))),
        "mean_abs_i": float(np.mean(np.abs(iq[:, 0]))),
        "mean_abs_q": float(np.mean(np.abs(iq[:, 1]))),
        "unique_iq_pairs": int(len({tuple(v) for v in iq.tolist()})),
        "raw_z_mean_abs": float(abs(z.mean())),
    }


def percentile_summary(iq):
    vals = np.abs(iq.astype(np.float64))
    return {str(p): float(np.percentile(vals, p)) for p in (10, 25, 50, 75, 90, 99)}


def period_corr(real_iq, period_iq, conjugate=False):
    """Best normalized complex correlation over all period phases."""
    r = real_iq[:, 0].astype(np.float64) + 1j * real_iq[:, 1].astype(np.float64)
    p = period_iq[:, 0].astype(np.float64) + 1j * period_iq[:, 1].astype(np.float64)
    tiled = np.tile(p, 3)
    best = (-1.0, None)
    for lag in range(0, len(p)):
        z = tiled[lag:lag + 4096]
        if conjugate:
            z = np.conj(z)
        num = abs(np.vdot(z, r))
        den = np.sqrt(np.vdot(z, z).real * np.vdot(r, r).real)
        c = num / den if den else 0.0
        if c > best[0]:
            best = (float(c), lag)
    return {"corr": best[0], "lag": best[1]}


def conj_alignment(real_iq, period_iq):
    """Measure conjugate match, constant phase and residual phase drift."""
    r = real_iq[:, 0].astype(np.float64) + 1j * real_iq[:, 1].astype(np.float64)
    p = period_iq[:, 0].astype(np.float64) + 1j * period_iq[:, 1].astype(np.float64)
    tiled = np.tile(p, 3)
    best = (-1.0, None)
    for lag in range(0, len(p)):
        z = np.conj(tiled[lag:lag + 4096])
        c = abs(np.vdot(z, r)) / np.sqrt(np.vdot(z, z).real * np.vdot(r, r).real)
        if c > best[0]:
            best = (float(c), lag)
    corr, lag = best
    z = np.conj(tiled[lag:lag + 4096])
    phases = []
    for start in (0, 1024, 2048, 3072):
        seg_r = r[start:start + 1024]
        seg_z = z[start:start + 1024]
        g = np.vdot(seg_z, seg_r) / np.vdot(seg_z, seg_z)
        phases.append(float(np.angle(g)))
    phases = np.unwrap(np.asarray(phases))
    span = float(phases[-1] - phases[0])
    return {
        "corr": corr,
        "lag_samples": lag,
        "phase_rad": phases.tolist(),
        "phase_span_rad": span,
        "cfo_est_hz": float(span / (2 * np.pi * (3 * 1024 / 60e6))),
        "note": "phase/CFO estimate is diagnostic; not a truth or calibrated measurement",
    }


def channel_fit(real_iq, period_iq):
    """Fit r = a*conj(s) + b*s and report residual after conjugate alignment."""
    r = real_iq[:, 0].astype(np.float64) + 1j * real_iq[:, 1].astype(np.float64)
    r = r - r.mean()
    p = period_iq[:, 0].astype(np.float64) + 1j * period_iq[:, 1].astype(np.float64)
    p = p - p.mean()
    tiled = np.tile(p, 3)
    best = (-1.0, None)
    for lag in range(0, len(p)):
        z = np.conj(tiled[lag:lag + 4096])
        c = abs(np.vdot(z, r)) / np.sqrt(np.vdot(z, z).real * np.vdot(r, r).real)
        if c > best[0]:
            best = (float(c), lag)
    corr, lag = best
    z = np.conj(tiled[lag:lag + 4096])
    design = np.stack([z, np.conj(z)], axis=1)
    coef, _, _, _ = np.linalg.lstsq(design, r, rcond=None)
    fit = design @ coef
    err = r - fit
    return {
        "corr": corr,
        "lag_samples": lag,
        "abs_a": float(abs(coef[0])),
        "abs_b": float(abs(coef[1])),
        "image_rejection_db": float(20 * np.log10(max(abs(coef[0]), 1e-12) /
                                                   max(abs(coef[1]), 1e-12))),
        "residual_ratio": float(np.sqrt(np.mean(np.abs(err) ** 2)) /
                                 np.sqrt(np.mean(np.abs(fit) ** 2))),
        "note": "diagnostic widely-linear fit; not a calibrated IQ-imbalance measurement",
    }


def board_host_row(row):
    host = row.get("host") or {}
    board = (row.get("board_host") or {}).get("board") or {}
    diff = (row.get("board_host") or {}).get("differences") or {}
    return {
        "host_label": host.get("final_label"),
        "host_accepted": host.get("accepted"),
        "host_reason": host.get("reason"),
        "host_scores": host.get("scores"),
        "board_label": board.get("final_label"),
        "board_accepted": board.get("accepted"),
        "board_reason": board.get("reason"),
        "board_scores4": board.get("scores4"),
        "board_logits4": board.get("logits4"),
        "class_match": diff.get("class_match"),
        "acceptance_match": diff.get("acceptance_match"),
        "reason_match": diff.get("reason_match"),
    }


def transform_iq(iq, name):
    if name == "raw":
        return iq
    if name == "conj":
        return np.stack([iq[:, 0], -iq[:, 1]], axis=1)
    if name == "swap":
        return iq[:, ::-1]
    if name == "Ineg":
        return np.stack([-iq[:, 0], iq[:, 1]], axis=1)
    if name == "Qneg":
        return np.stack([iq[:, 0], -iq[:, 1]], axis=1)
    if name == "reverse":
        return iq[::-1]
    raise ValueError(name)


def main():
    results = RESULTS
    results.mkdir(parents=True, exist_ok=True)
    campaign = load_json(RUN / "fourclass.json")
    records = campaign["records"]
    eval_doc = load_json(EVAL)
    rows = eval_doc["frames"]
    by_frame = {Path(r.get("frame_id", "")).stem: r for r in rows}
    truth_doc = load_json(results / "tx_truth_side.json")
    truth_entries = []
    for f in truth_doc["frames"]:
        fi = f["frame_identity"]
        truth_entries.append((fi["capture_id"], fi["iq_sha256"], f["tx"]))
    truth_by_iq = {(c, h): tx for c, h, tx in truth_entries}
    amp_manifest = load_json(AMP60 / "outputs" / "manifest.json")
    amp_vectors = {v["truth_class"]: v for v in amp_manifest["vectors"]}
    periods = {m: read_iq(AMP60 / amp_vectors[m]["tx_payload"]["path"])
               for m in ("BPSK", "QPSK", "QAM16", "QAM64")}
    predictor = Round2Predictor()
    all_rows = []
    qam_rows = []
    for index, record in enumerate(records):
        tag = Path(record["iq_file"]).stem
        iq = read_iq(RUN / record["iq_file"])
        meta = load_json(RUN / (tag + ".json"))
        digest = sha256_file(RUN / record["iq_file"])
        tx = truth_by_iq[(record["capture_id"], digest)]
        gate_iq, gate = preprocess_iq(iq)
        feats = extract_features(gate_iq) if gate_iq is not None else None
        spec = spectral_features(gate_iq) if gate_iq is not None else None
        ev_row = by_frame.get(tag, {})
        board = board_host_row(ev_row) if ev_row else {}
        truth_class = tx["mode"] if tx["mode"] != "OFF" else "OFF"
        qam = truth_class in ("QAM16", "QAM64")
        corr = None
        alignment = None
        fit = None
        transforms = {}
        if qam:
            corr = {}
            for ideal_mode in ("QAM16", "QAM64"):
                corr[ideal_mode] = {
                    "normal": period_corr(iq, periods[ideal_mode], False),
                    "conj": period_corr(iq, periods[ideal_mode], True),
                }
            alignment = conj_alignment(iq, periods[truth_class])
            fit = channel_fit(iq, periods[truth_class])
            for name in ("raw", "conj", "swap", "Ineg", "Qneg", "reverse"):
                t = transform_iq(iq, name)
                d = predictor.decide(t)
                transforms[name] = {
                    "label": d.get("label"), "reason": d.get("reason"),
                    "scores": d.get("cnn_scores"),
                    "diagnostic_only": True,
                    "new_real_frame": False,
                }
            qam_rows.append({"frame": tag, "truth": truth_class,
                             "raw_scores": (board.get("host_scores") or []),
                             "transforms": transforms, "corr": corr})
        category = "ON" if truth_class != "OFF" else "OFF"
        conj_probe = None
        if category == "ON":
            conj_iq = transform_iq(iq, "conj")
            d = predictor.decide(conj_iq)
            conj_probe = {
                "label": d.get("label"),
                "scores": d.get("cnn_scores"),
                "diagnostic_only": True,
                "new_real_frame": False,
            }
        all_rows.append({
            "index": index,
            "frame": tag,
            "iq_file": record["iq_file"],
            "iq_sha256": digest,
            "capture_id": record["capture_id"],
            "request_id": meta.get("request_id"),
            "truth_class": truth_class,
            "truth_source": "independent_tx_config (runner mode/seed + IQ SHA)",
            "tx_mode": tx["mode"],
            "tx_seed": tx.get("seed"),
            "tx_amplitude": tx.get("amplitude"),
            "kind": category,
            "board_host": board,
            "old_six_class_label": (meta.get("model_result") or {}).get("label"),
            "environment": (meta.get("environment_result") or {}).get("label"),
            "environment_reason": (meta.get("environment_result") or {}).get("reason"),
            "demod_reason": ((meta.get("demod_result") or {}).get("result") or {}).get("reason"),
            "input_domain": {
                "required": (meta.get("modrec4_result") or {}).get("input_domain", {}).get("required"),
                "status": (meta.get("modrec4_result") or {}).get("input_domain", {}).get("status"),
                "reason": (meta.get("modrec4_result") or {}).get("input_domain", {}).get("reason"),
            },
            "stats": stats(iq),
            "abs_percentiles": percentile_summary(iq),
            "preprocess": {"gate": gate},
            "features": ({k: float(v) for k, v in zip(FEATURE_NAMES, feats)}
                         if feats is not None else None),
            "spectral": ({k: float(v) for k, v in spec.items()} if spec else None),
            "corr": corr,
            "conj_alignment": alignment,
            "channel_fit": fit,
            "conj_probe": conj_probe,
            "transforms": transforms,
        })
    # Ideal payloads: same frozen preprocessing/model, diagnostic reference.
    ideal_rows = []
    for mode in ("BPSK", "QPSK", "QAM16", "QAM64"):
        iq = read_iq(AMP60 / amp_vectors[mode]["reference_4096"]["path"])
        gate_iq, gate = preprocess_iq(iq)
        d = predictor.decide(iq)
        c = predictor.classify(iq)
        ideal_rows.append({
            "mode": mode,
            "raw_stats": stats(iq),
            "gate": gate,
            "decide": {"label": d.get("label"), "reason": d.get("reason"),
                       "scores": d.get("cnn_scores")},
            "classify": {"label": c.get("class_label"),
                         "scores": c.get("cnn_scores")},
            "features": ({k: float(v) for k, v in zip(FEATURE_NAMES, extract_features(gate_iq))}
                         if gate_iq is not None else None),
        })
    # Summaries: all official values are frozen raw results; transforms are diagnostic.
    on_rows = [r for r in all_rows if r["kind"] == "ON"]
    qam16_rows = [r for r in all_rows if r["truth_class"] == "QAM16"]
    qam64_rows = [r for r in all_rows if r["truth_class"] == "QAM64"]
    board_host = {
        "frames": len(all_rows),
        "class_match_true": sum(1 for r in all_rows
                                if (r.get("board_host") or {}).get("class_match") is True),
        "acceptance_match_true": sum(1 for r in all_rows
                                     if (r.get("board_host") or {}).get("acceptance_match") is True),
        "reason_match_true": sum(1 for r in all_rows
                                 if (r.get("board_host") or {}).get("reason_match") is True),
    }
    qam_summary = {}
    for label, subset in (("QAM16", qam16_rows), ("QAM64", qam64_rows)):
        raw_labels = [r["board_host"].get("host_label") for r in subset]
        qam_summary[label] = {
            "frames": len(subset),
            "raw_host_labels": raw_labels,
            "raw_qam16_scores": [r["board_host"].get("host_scores", [None, None, None, None])[2]
                                 if r["board_host"].get("host_scores") else None for r in subset],
            "raw_qam64_scores": [r["board_host"].get("host_scores", [None, None, None, None])[3]
                                 if r["board_host"].get("host_scores") else None for r in subset],
            "corr_truth_class_conj": [r["corr"][label]["conj"] if r["corr"] else None for r in subset],
            "corr_truth_class_normal": [r["corr"][label]["normal"] if r["corr"] else None for r in subset],
            "conj_labels": [r["transforms"]["conj"]["label"] for r in subset],
            "conj_qam16_scores": [r["transforms"]["conj"]["scores"][2] for r in subset],
            "conj_qam64_scores": [r["transforms"]["conj"]["scores"][3] for r in subset],
            "conj_alignment": [r["conj_alignment"] for r in subset],
            "channel_fit": [r["channel_fit"] for r in subset],
            "old_six_class": [r["old_six_class_label"] for r in subset],
        }
    diagnosis = {
        "schema": "ml-fourclass-qam16-qam64-diagnosis-v1",
        "source_frames": str(RUN),
        "truth_side": str(results / "tx_truth_side.json"),
        "truth_side_sha256": sha256_file(results / "tx_truth_side.json"),
        "task68_eval": str(EVAL),
        "task68_eval_sha256": sha256_file(EVAL),
        "model": eval_doc.get("summary", {}).get("model"),
        "board_host": board_host,
        "qam": qam_summary,
        "conj_all_on": {
            "description": "diagnostic conjugate probe; not new real data and not an accuracy claim",
            "correct": sum(1 for r in on_rows
                           if r["conj_probe"] and r["conj_probe"]["label"] == r["truth_class"]),
            "frames": len(on_rows),
            "labels": {r["truth_class"]: r["conj_probe"]["label"] for r in on_rows},
        },
        "frames": all_rows,
        "ideal_reference": ideal_rows,
        "diagnostic_transforms_are_not_real_frames": True,
        "notes": [
            "raw frozen result is authoritative; transforms are diagnostics only",
            "truth from runner TX mode/seed/amplitude and IQ SHA, never model/RX mode/six-class",
            "no training, threshold/model change, device access or new real frames",
        ],
    }
    (results / "diagnosis.json").write_text(
        json.dumps(diagnosis, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (results / "diagnosis_digest.json").write_text(
        json.dumps({"diagnosis_sha256": sha256_file(results / "diagnosis.json"),
                    "task68_eval_sha256": sha256_file(EVAL),
                    "truth_sha256": sha256_file(results / "tx_truth_side.json")},
                   indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "board_host": board_host,
        "qam": {k: {"frames": v["frames"], "raw": v["raw_host_labels"],
                    "conj": v["conj_labels"], "corr_conj": v["corr_truth_class_conj"],
                    "old_six": v["old_six_class"]}
                for k, v in qam_summary.items()},
    }, indent=1))


if __name__ == "__main__":
    main()
