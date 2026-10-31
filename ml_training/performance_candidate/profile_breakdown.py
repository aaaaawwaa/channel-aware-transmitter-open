#!/usr/bin/env python3
"""Read-only timing breakdown of the current full path / modrec4 adapter.

Uses the immutable candidate release under evidence/ and existing frames.  Monkeypatches
runtime callables in memory only (no file changes) to count and time duplicate work.

Usage:
  python ml_training/performance_candidate/profile_breakdown.py
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_CANDIDATE = (ROOT / "evidence" / "rf-teaching" /
                     "s2-46db-modrec4-product-2026-10-21" / "candidate" / "releases" /
                     "v3" / "out" / "releases" / "099174be0cb38a139ea95926394fcf0e")
DEFAULT_FRAMES = (ROOT / "evidence" / "rf-teaching" /
                  "s2-46db-80mhz-narrowband-2026-10-21" / "raw")
sys.dont_write_bytecode = True
TAGS = ("nf-00-normal", "nf-01-narrowband", "nf-02-narrowband", "nf-03-narrowband")


class Meter:
    def __init__(self):
        self.count = Counter()
        self.seconds = defaultdict(float)

    def wrap(self, holder, name, label=None):
        original = getattr(holder, name)
        label = label or name

        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.count[label] += 1
                self.seconds[label] += time.perf_counter() - started

        setattr(holder, name, wrapped)
        return original


def load_frames(frames_dir, tags):
    import rf_modrec4_fullpath

    frames = {}
    for tag in tags:
        frame, envelope, environment = rf_modrec4_fullpath._load_frame(frames_dir, tag)
        frames[tag] = (frame, envelope, environment)
    return frames


def run_profile(candidate, frames_dir, tags):
    sys.path.insert(0, str(candidate))
    import rf_modrec4_preprocess as pre
    import rf_modrec4_spectral as spec
    import rf_modrec4_gate as gate
    meter = Meter()
    # Patch the low-level functions before the adapter/predictor bind them.
    meter.wrap(pre, "preprocess_iq")
    meter.wrap(spec, "spectral_features")
    meter.wrap(gate, "apply_gate")
    import rf_modrec4_predictor as predictor_mod
    meter.wrap(predictor_mod, "softmax")
    meter.wrap(predictor_mod, "top_scores")
    meter.wrap(predictor_mod.Round2Predictor, "decide", "predictor.decide")
    meter.wrap(predictor_mod.Round2Predictor, "classify", "predictor.classify")
    import rf_modrec4_numpy_runtime as runtime
    meter.wrap(runtime.ModRecModel, "forward", "cnn.forward")
    import rf_modrec4_baseline as baseline_mod
    meter.wrap(baseline_mod.FeatureBaseline, "scores", "baseline.scores")
    import rf_modrec4_adapter as adapter_mod
    meter.wrap(adapter_mod, "_before_decision")
    meter.wrap(adapter_mod.ModRec4Adapter, "contract_map", "adapter.contract_map")
    meter.wrap(adapter_mod.ModRec4Adapter, "_provenance", "adapter._provenance")
    meter.wrap(adapter_mod.ModRec4Adapter, "_passthrough", "adapter._passthrough")
    import rf_modrec4_fullpath
    meter.wrap(rf_modrec4_fullpath, "_load_frame", "frame.load")
    meter.wrap(rf_modrec4_fullpath, "_timed", "fullpath._timed")
    import rf_rx_analysis
    import rf_cnn_runtime
    import rf_bpsk_pipeline
    import rf_environment
    import merged_release
    meter.wrap(rf_rx_analysis, "summarize", "analysis.summarize")
    meter.wrap(rf_cnn_runtime, "load_model", "six.load_model")
    meter.wrap(rf_cnn_runtime.TinyIQCNNModel, "predict", "six.model.predict")
    meter.wrap(rf_bpsk_pipeline, "analyze_demod", "bpsk.analyze_demod")
    meter.wrap(rf_environment, "analyze_environment", "environment.analyze")
    meter.wrap(merged_release, "verify_release", "release.verify")
    frames = load_frames(frames_dir, tags)
    release = Path(candidate).resolve()
    manifest = merged_release.verify_release(release, require_software=True)
    artifact = manifest[merged_release.MODREC4_MODEL_KEY]

    per_frame = {}
    stage_ms = defaultdict(list)
    fullpath_run_ms = []
    for tag in tags:
        frame, envelope, environment = frames[tag]
        import numpy as np
        iq = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
        started = time.perf_counter()
        from rf_modrec4_adapter import ModRec4Adapter
        adapter = ModRec4Adapter.from_release(release, artifact)
        adapter_construct_ms = (time.perf_counter() - started) * 1000.0
        context = {"format": "s16_iq_le", "samples": frame.samples,
                   "sample_rate_hz": frame.sample_rate_hz,
                   "decimation": frame.decimation,
                   "symbol_rate_hz": envelope.submitted.config.symbol_rate_hz,
                   "shaping": envelope.submitted.config.shaping}
        before = dict(meter.count)
        before_s = dict(meter.seconds)
        started = time.perf_counter()
        result = adapter.evaluate_iq(iq, context=context, environment=environment,
                                     demod=None, six_class=None,
                                     frame_id=frame.capture_id)
        adapter_ms = (time.perf_counter() - started) * 1000.0
        calls = {label: meter.count[label] - before.get(label, 0)
                 for label in meter.count}
        times = {label: (meter.seconds[label] - before_s.get(label, 0.0)) * 1000.0
                 for label in meter.seconds}
        per_frame[tag] = {
            "adapter_construct_ms": round(adapter_construct_ms, 3),
            "adapter_evaluate_ms": round(adapter_ms, 3),
            "calls": calls,
            "call_ms": {key: round(value, 3) for key, value in times.items() if value},
            "result": {"status": result["status"],
                       "final_label": result["modulation"]["final_label"],
                       "reason": result["modulation"]["reason"]},
        }
        started = time.perf_counter()
        out = rf_modrec4_fullpath.run(frames_dir, tag, release)
        fullpath_run_ms.append((time.perf_counter() - started) * 1000.0)
        for stage, row in (out.get("stages") or {}).items():
            stage_ms[stage].append(float(row["seconds"]) * 1000.0)

    # cProfile one adapter evaluation.
    frame, envelope, environment = frames[tags[0]]
    import numpy as np
    iq = np.frombuffer(frame.payload, dtype="<i2").reshape(frame.samples, 2)
    from rf_modrec4_adapter import ModRec4Adapter
    adapter = ModRec4Adapter.from_release(release, artifact)
    context = {"format": "s16_iq_le", "samples": frame.samples,
               "sample_rate_hz": frame.sample_rate_hz, "decimation": frame.decimation,
               "symbol_rate_hz": envelope.submitted.config.symbol_rate_hz,
               "shaping": envelope.submitted.config.shaping}
    profiler = cProfile.Profile()
    profiler.enable()
    adapter.evaluate_iq(iq, context=context, environment=environment, frame_id=frame.capture_id)
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(18)
    profile_text = stream.getvalue()

    # Static duplicate call sites in the adapter.
    adapter_source = (candidate / "rf_modrec4_adapter.py").read_text(encoding="utf-8")
    static = {
        "preprocess_iq_occurrences": adapter_source.count("preprocess_iq("),
        "spectral_features_occurrences": adapter_source.count("spectral_features("),
        "decide_occurrences": adapter_source.count("self.predictor.decide("),
        "classify_occurrences": adapter_source.count("self.predictor.classify("),
        "before_decision_occurrences": adapter_source.count("_before_decision("),
        "contract_map_occurrences": adapter_source.count("self.contract_map()"),
        "passthrough_occurrences": adapter_source.count("self._passthrough("),
    }
    return {
        "schema": "ml-performance-profile-v1",
        "candidate": str(Path(candidate).relative_to(ROOT)).replace("\\", "/"),
        "frames_dir": str(Path(frames_dir).relative_to(ROOT)).replace("\\", "/"),
        "tags": list(tags),
        "per_frame": per_frame,
        "adapter_calls_per_frame": {tag: row["calls"] for tag, row in per_frame.items()},
        "adapter_call_ms_per_frame": {tag: row["call_ms"] for tag, row in per_frame.items()},
        "stage_ms": {stage: values for stage, values in stage_ms.items()},
        "fullpath_run_ms": fullpath_run_ms,
        "static_call_sites": static,
        "cprofile_top": profile_text,
        "redundancy_evidence": {
            "preprocess_per_frame": per_frame[tags[0]]["calls"].get("preprocess_iq"),
            "cnn_forward_per_frame": per_frame[tags[0]]["calls"].get("cnn.forward"),
            "baseline_per_frame": per_frame[tags[0]]["calls"].get("baseline.scores"),
            "spectral_per_frame": per_frame[tags[0]]["calls"].get("spectral_features"),
            "decide_per_frame": per_frame[tags[0]]["calls"].get("predictor.decide"),
            "classify_per_frame": per_frame[tags[0]]["calls"].get("predictor.classify"),
            "interpretation": (
                "The adapter calls _before_decision (preprocess+CNN+gate), decide "
                "(preprocess+CNN+spectral+maybe baseline), classify "
                "(preprocess+CNN+baseline), then preprocess+spectral again.  classify's "
                "only output consumer is pure_class_hint."),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--frames-dir", type=Path, default=DEFAULT_FRAMES)
    parser.add_argument("--tags", nargs="+", default=list(TAGS))
    parser.add_argument("--out", type=Path,
                        default=PACKAGE / "results" / "profile_before.json")
    args = parser.parse_args(argv)
    document = run_profile(args.candidate, args.frames_dir, args.tags)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1), encoding="utf-8")
    print(json.dumps({
        "candidate": document["candidate"],
        "stage_ms": {key: values for key, values in document["stage_ms"].items()},
        "calls": document["adapter_calls_per_frame"],
        "static": document["static_call_sites"],
        "redundancy": document["redundancy_evidence"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
