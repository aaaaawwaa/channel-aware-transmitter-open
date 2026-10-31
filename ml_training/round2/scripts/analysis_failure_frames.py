"""Round-2 failure analysis: compare feature baseline and CNN on the real failure frames.

Read-only over the existing physical evidence.  Produces
``ml_training/round2/analysis/failure_frames.json`` and ``.md``.

The comparison deliberately reports, for every real BPSK/off frame:
* input statistics (complex RMS, I/Q RMS, phase from E[z^2], carrier-coherence SNR),
* the 8 interpretable baseline features,
* feature-baseline scores and the final CNN scores/gate decision,
* the existing environment-rule label (context only; never used as a label).

Round-1 artefacts are not modified.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common.iq import read_frame  # noqa: E402
from ml_training.common.preprocess import preprocess_iq  # noqa: E402
from ml_training.model.baseline import load_baseline  # noqa: E402
from ml_training.model.features import FEATURE_NAMES, extract_features  # noqa: E402
from ml_training.model.numpy_runtime import load_model  # noqa: E402

ML = ROOT / "ml_training"
EVIDENCE = ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18"
CANONICAL = EVIDENCE / "campaign5-val-followup" / "download"
SESSIONS = ("train-01", "train-02", "val-01")
CLASSES = ("BPSK", "QPSK", "QAM16", "QAM64")
OUT = ML / "round2" / "analysis"


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _snr_db_from_coherence(coherence):
    if coherence is None:
        return None
    coherence = float(coherence)
    coherence = min(max(coherence, 1e-9), 1.0 - 1e-9)
    return float(10.0 * np.log10(coherence / (1.0 - coherence)))


def frame_record(session, stem, cnn, baseline):
    meta = _load_json(CANONICAL / session / (stem + ".json"))
    env_path = CANONICAL / session / (stem + ".environment.json")
    environment = _load_json(env_path).get("environment", {}) if env_path.is_file() else {}
    truth = meta.get("trainval", {}).get("truth")
    config = meta.get("demod_result", {}).get("submitted", {}).get("config", {})
    analysis = meta.get("analysis", {})
    demod = meta.get("demod_result", {}).get("result", {})
    iq = read_frame(CANONICAL / session / (stem + ".iq"))
    x, gate = preprocess_iq(iq)
    phase_deg = float(np.degrees(np.angle(np.mean((x[0] + 1j * x[1]) ** 2)) / 2.0))
    features = extract_features(x)
    cnn_result = cnn.predict_iq(iq)
    baseline_result = baseline.predict_iq(iq)
    return {
        "session": session,
        "frame": stem,
        "truth": truth,
        "tx_config": config,
        "clipping_count": analysis.get("clipping_count"),
        "i_rms": analysis.get("i_rms"),
        "q_rms": analysis.get("q_rms"),
        "complex_rms": analysis.get("complex_rms"),
        "i_dbfs": analysis.get("i_dbfs"),
        "q_dbfs": analysis.get("q_dbfs"),
        "phase_deg": phase_deg,
        "cfo_hz": demod.get("cfo_hz"),
        "carrier_coherence": demod.get("carrier_coherence"),
        "snr_db_from_coherence": _snr_db_from_coherence(demod.get("carrier_coherence")),
        "demod_ber": demod.get("ber"),
        "features": {name: float(value) for name, value in zip(FEATURE_NAMES, features)},
        "cnn_label": cnn_result["label"],
        "cnn_reason": cnn_result["reason"],
        "cnn_score": cnn_result["score"],
        "cnn_margin": cnn_result["margin"],
        "cnn_scores": cnn_result["scores"],
        "baseline_label": baseline_result["label"],
        "baseline_score": baseline_result["score"],
        "baseline_scores": baseline_result["scores"],
        "environment_label": environment.get("label"),
        "environment_reason": environment.get("reason"),
    }


def aggregate(rows):
    result = {}
    for session in SESSIONS:
        normal = [row for row in rows if row["session"] == session and row["truth"] == "normal"]
        off = [row for row in rows if row["session"] == session and row["truth"] == "off"]
        if not normal:
            continue
        env = Counter((row["environment_label"], row["environment_reason"]) for row in normal)
        result[session] = {
            "n_normal": len(normal),
            "n_off": len(off),
            "cnn_bpsk": sum(1 for row in normal if row["cnn_label"] == "BPSK"),
            "cnn_bpsk_rate": sum(1 for row in normal if row["cnn_label"] == "BPSK") / len(normal),
            "baseline_bpsk": sum(1 for row in normal if row["baseline_label"] == "BPSK"),
            "baseline_bpsk_rate": sum(1 for row in normal if row["baseline_label"] == "BPSK") / len(normal),
            "complex_rms_mean": float(np.mean([row["complex_rms"] for row in normal])),
            "phase_deg_mean": float(np.mean([row["phase_deg"] for row in normal])),
            "snr_db_from_coherence_mean": float(np.mean([row["snr_db_from_coherence"] for row in normal])),
            "cfo_hz_mean": float(np.mean([row["cfo_hz"] for row in normal])),
            "environment_labels": {("%s/%s" % key): value for key, value in env.items()},
            "off_cnn_rejected": sum(1 for row in off if row["cnn_label"] == "UNKNOWN"),
            "off_cnn_reason": Counter(row["cnn_reason"] for row in off if row["cnn_label"] == "UNKNOWN"),
        }
    return result


def feature_comparison(rows):
    failure = [row for row in rows if row["session"] == "train-02" and row["truth"] == "normal"]
    success = [row for row in rows if row["session"] in ("train-01", "val-01") and row["truth"] == "normal"]
    table = {}
    for name in FEATURE_NAMES:
        fail_values = np.asarray([row["features"][name] for row in failure])
        succ_values = np.asarray([row["features"][name] for row in success])
        table[name] = {
            "failure_mean": float(fail_values.mean()),
            "success_mean": float(succ_values.mean()),
            "delta": float(fail_values.mean() - succ_values.mean()),
            "delta_relative": float((fail_values.mean() - succ_values.mean())
                                    / (abs(succ_values.mean()) + 1e-12)),
        }
    return table


def main():
    cnn = load_model(ML / "artifacts" / "cnn" / "model.json")
    baseline = load_baseline(ML / "artifacts" / "baseline" / "model.json")
    rows = []
    for session in SESSIONS:
        for pattern in ("normal-*.json", "off-*.json"):
            for meta_path in sorted((CANONICAL / session).glob(pattern)):
                if meta_path.name.count(".") != 1:
                    continue
                rows.append(frame_record(session, meta_path.stem, cnn, baseline))
    sessions = aggregate(rows)
    comparison = feature_comparison(rows)
    document = {
        "schema": "ml-modrec-round2-failure-analysis-v1",
        "generated_utc": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "round1_artifacts_read_only": {
            "cnn_model_sha256": "25a65fb1e6a7bb606d7611c473d166b8b633256b3a72dcadd434ed83a38c49cc",
            "baseline_model_sha256": "caa8258916268892bad968e1240520da0c03ab825839fc2f39c5bb534c4e3014",
        },
        "source": {
            "canonical_root": str(CANONICAL.relative_to(ROOT)).replace("\\", "/"),
            "label_source": "trainval.truth + demod_result.submitted.config (NOT algorithm outputs)",
            "environment_label_use": "context only, never a training/evaluation label",
            "read_only": True,
        },
        "sessions": sessions,
        "feature_comparison_failure_vs_success": comparison,
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "failure_frames.json").write_text(json.dumps(document, indent=1, ensure_ascii=False),
                                             encoding="utf-8")
    lines = []
    lines.append("# Round-2 failure-frame comparison (baseline vs CNN)")
    lines.append("")
    lines.append("Read-only analysis of the real physical frames; round-1 artefacts untouched.")
    lines.append("Truth only from `trainval.truth` + `demod_result.submitted.config`; environment-rule")
    lines.append("labels are context only (never used as labels).")
    lines.append("")
    lines.append("## Per-session summary")
    lines.append("")
    lines.append("| session | normal | CNN BPSK | baseline BPSK | complex_rms | phase_deg | SNR(coherence) | env labels | OFF rejected |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for session, data in sessions.items():
        env_text = ", ".join("%s:%d" % (key, value) for key, value in data["environment_labels"].items())
        lines.append("| %s | %d | %d/%d | %d/%d | %.1f | %.2f | %.2f dB | %s | %d/%d |" % (
            session, data["n_normal"], data["cnn_bpsk"], data["n_normal"],
            data["baseline_bpsk"], data["n_normal"], data["complex_rms_mean"],
            data["phase_deg_mean"], data["snr_db_from_coherence_mean"], env_text,
            data["off_cnn_rejected"], data["n_off"]))
    lines.append("")
    lines.append("## train-02 per-frame CNN / baseline output")
    lines.append("")
    lines.append("| frame | rms | phase | env label | CNN label | CNN scores (B/QP/16/64) | baseline label | baseline max |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in rows:
        if row["session"] != "train-02" or row["truth"] != "normal":
            continue
        cnn_scores = ", ".join("%.3f" % value for value in row["cnn_scores"])
        baseline_scores = ", ".join("%.3f" % value for value in row["baseline_scores"])
        lines.append("| %s | %.1f | %.2f | %s/%s | %s | [%s] | %s [%s] | %.3f |" % (
            row["frame"], row["complex_rms"], row["phase_deg"],
            row["environment_label"], row["environment_reason"], row["cnn_label"], cnn_scores,
            row["baseline_label"], baseline_scores, row["baseline_score"]))
    lines.append("")
    lines.append("## Feature comparison: failure (train-02) vs success (train-01+val-01) frames")
    lines.append("")
    lines.append("| feature | failure mean | success mean | delta | relative |")
    lines.append("|---|---|---|---|---|")
    for name, values in comparison.items():
        lines.append("| %s | %.4f | %.4f | %+.4f | %+.3f |" % (
            name, values["failure_mean"], values["success_mean"],
            values["delta"], values["delta_relative"]))
    lines.append("")
    lines.append("## Reading")
    lines.append("")
    lines.append("- 10/10 train-02 frames are high-confidence QPSK from the CNN (not gate rejections),")
    lines.append("  while the interpretable baseline labels all 30 normal frames BPSK. The failure is")
    lines.append("  therefore a **CNN decision-boundary** issue, not a missing-class or gate issue.")
    lines.append("- The failure session shares one carrier phase (~63 deg) and has lower SNR / complex RMS")
    lines.append("  than the two success sessions; the environment rule independently flags 9/10 failure")
    lines.append("  frames as PULSE/SPARSE_AMPLITUDE_OUTLIERS (context only).")
    lines.append("- Round 1 already trained with uniform random phase; the issue is not absence of phase")
    lines.append("  randomization but the CNN's phase-dependent decision surface plus session-correlated")
    lines.append("  phase (and unmodelled distortion).")
    (OUT / "failure_frames.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT / "failure_frames.json")
    print("sessions:", json.dumps(sessions, indent=1))
    print("feature deltas:")
    for name, values in comparison.items():
        print("  %-20s failure=%.4f success=%.4f delta=%+.4f" % (
            name, values["failure_mean"], values["success_mean"], values["delta"]))


if __name__ == "__main__":
    main()
