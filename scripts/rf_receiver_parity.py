"""All FIVE receive stages of one offline IQ set, as comparable JSON.

Why this exists: the candidate's ARM qualification compared only the ENVIRONMENT
analysis record (`rf_environment_arm_check.py` stores `environment`, and the
driver compares those records), so "host and ARM agree" was a statement about one
stage out of five.  This emits the base summary, the CNN result, the BPSK demod
(sync state, reference, error bits, BER), the constellation and the environment,
so the same comparison rule can be applied to the whole receive path -- including
the frames that REFUSE, whose rejection state is compared as well rather than
being filtered out for having no successful result.
"""
import argparse
import dataclasses
import hashlib
import json
import pathlib
import sys


def plain(value):
    """A canonical, comparison-friendly form of whatever a stage returned."""
    if dataclasses.is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # numpy scalars and arrays
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release-dir", required=True, type=pathlib.Path)
    p.add_argument("--iq-dir", required=True, type=pathlib.Path)
    p.add_argument("--output", required=True, type=pathlib.Path)
    a = p.parse_args(argv)

    release = a.release_dir.resolve()
    sys.path.insert(0, str(release))
    import numpy as np                                    # noqa: F401
    import merged_release as R
    import rf_cnn_runtime as CNN
    from rf_status_worker import RfStatusWorker
    from rf_rx_types import RxIqFrame, RxReadback
    from rf_rx_analysis import summarize
    from rf_bpsk_pipeline import DemodConfig, analyze_demod
    from rf_bpsk_receiver import BpskConfig
    from rf_rx_constellation import estimate_constellation
    from rf_environment import analyze_environment, FIXED_RF

    manifest = R.verify_release(release, require_software=True)
    worker = RfStatusWorker(release_directory=str(release))
    worker._ensure_model()
    worker._ensure_demod()
    worker._ensure_teaching()
    worker._ensure_environment()

    records, inputs = [], []
    for path in sorted(a.iq_dir.glob("*.iq")):
        raw = path.read_bytes()
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if len(raw) != 16384 or hashlib.sha256(raw).hexdigest() != meta["iq_sha256"]:
            raise ValueError("offline source IQ size/hash mismatch")
        frame = RxIqFrame(
            meta["capture_id"], raw, meta["samples"], meta["sample_rate_hz"],
            meta["ratio"], meta["daemon_timestamp_ns"], meta["connection_generation"],
            meta["config_revision"], meta["request_id"], meta["release_id"], meta["op"],
            meta["physical"], RxReadback(meta["pre_io_ok"]),
            RxReadback(meta["post_io_ok"], rx_armed=meta["post_rx_armed"],
                       tx_source_selected=meta["post_tx_source_selected"]),
            meta["completed_at_monotonic"])
        s = meta["demod_result"]["submitted"]
        config = DemodConfig(BpskConfig(**s["config"]), s["revision"], s["decim"],
                             s["rx_len"], s["input_fs_hz"])
        # Rebind only a private frame to the candidate identity; the source
        # metadata is untouched and this is never claimed as a physical frame.
        virtual = dataclasses.replace(frame, release_id=manifest["release_id"],
                                      capture_id="offline-parity-" + frame.capture_id)

        base = summarize(virtual)
        cnn = worker._predict_frame(virtual)
        if cnn is None or getattr(cnn, "reason", None) == "release_mismatch":
            raise ValueError("the CNN did not run for %s" % path.stem)
        envelope = analyze_demod(virtual, config)
        constellation = estimate_constellation(virtual, envelope)
        environment = analyze_environment(virtual, envelope, dict(FIXED_RF),
                                          worker._environment_baseline)
        records.append(dict(
            tag=path.stem, iq_sha256=meta["iq_sha256"],
            source_release=frame.release_id,
            base=plain(base),
            cnn=plain(cnn),
            bpsk=plain(envelope.result),
            constellation=plain(constellation),
            environment=plain(environment)))
        inputs.append(meta["iq_sha256"])

    result = dict(
        scope="offline exact-candidate five-stage parity; zero RF captures",
        release_id=manifest["release_id"], software=manifest["software"],
        baseline_sha256=worker._environment_baseline.sha256,
        native_cnn=CNN._CONV_LIB is not None,
        input_iq_sha256=sorted(inputs), records=records,
        stages=["base", "cnn", "bpsk", "constellation", "environment"])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False, sort_keys=True),
                        encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
