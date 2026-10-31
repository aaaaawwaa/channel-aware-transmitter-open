#!/usr/bin/env python3
"""Full product analysis path on existing IQ, for host/ARM resource qualification.

Runs the actual candidate release modules, not a standalone probe:
  RF RX analysis -> six-class CNN -> BPSK demod -> environment -> modrec4 adapter.
It reads one already-recorded frame (IQ + JSON + environment JSON), never opens
serial/socket/GNU Radio/libiio and performs no RF action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load_frame(frame_dir, tag):
    from rf_rx_types import RxIqFrame, RxReadback
    from rf_bpsk_pipeline import BpskResult, BpskEnvelope, BpskConfig, DemodConfig
    raw = Path(frame_dir)
    meta = json.loads((raw / (tag + '.json')).read_text(encoding='utf-8'))
    env = json.loads((raw / (tag + '.environment.json')).read_text(encoding='utf-8'))
    dr = meta['demod_result']
    res = dr['result']; sub = dr['submitted']; cfg = sub['config']
    frame = RxIqFrame(
        capture_id=meta['capture_id'], payload=(raw / (tag + '.iq')).read_bytes(),
        samples=meta['samples'], sample_rate_hz=meta['sample_rate_hz'], ratio=meta['ratio'],
        daemon_timestamp_ns=meta['daemon_timestamp_ns'],
        connection_generation=meta['connection_generation'],
        config_revision=meta['config_revision'], request_id=meta['request_id'],
        release_id=meta['release_id'], op=meta['op'], physical=meta['physical'],
        pre_readback=RxReadback(**meta['pre_readback']),
        post_readback=RxReadback(**meta['post_readback']),
        completed_at_monotonic=meta['completed_at_monotonic'])
    result = BpskResult(**{k: res[k] for k in res
                           if k in BpskResult.__dataclass_fields__ and k != 'bits_preview'},
                        bits_preview=tuple(res.get('bits_preview', ())))
    config = BpskConfig(mode=cfg['mode'], symbol_rate_hz=cfg['symbol_rate_hz'],
                        shaping=cfg['shaping'],
                        reference_prbs7_seed=cfg.get('reference_prbs7_seed'))
    submitted = DemodConfig(config=config, revision=sub['revision'], decim=sub['decim'],
                            rx_len=sub['rx_len'], input_fs_hz=sub['input_fs_hz'])
    envelope = BpskEnvelope(result=result, submitted=submitted,
                            algorithm_version=dr['algorithm_version'])
    return frame, envelope, env


def _timed(fn, out, stage):
    start = time.perf_counter()
    value = fn()
    out[stage] = {'seconds': time.perf_counter() - start}
    return value


def run(frame_dir, tag, release):
    import numpy as np
    import rf_rx_analysis
    import rf_cnn_runtime
    import rf_bpsk_pipeline
    import rf_environment
    import rf_modrec4_adapter
    import merged_release

    release = Path(release).resolve()
    manifest = merged_release.verify_release(release, require_software=True)
    frame, envelope, env_doc = _load_frame(frame_dir, tag)
    timings = {}

    analysis = _timed(lambda: rf_rx_analysis.summarize(frame), timings, 'analysis')
    model = rf_cnn_runtime.load_model(release / manifest['model_artifact']['model_file'],
                                      manifest['model_artifact']['model_sha256'])
    iq = np.frombuffer(frame.payload, dtype='<i2').reshape(frame.samples, 2)
    six = _timed(lambda: model.predict(iq, input_fs_hz=frame.sample_rate_hz,
                                       decim=frame.decimation), timings, 'six_class')
    demod = _timed(lambda: rf_bpsk_pipeline.analyze_demod(frame, envelope.submitted),
                   timings, 'bpsk')
    baseline_doc = json.loads((release / 'rf_environment_baseline.json').read_text(
        encoding='utf-8'))
    baseline = rf_environment.EnvironmentBaseline.from_dict(baseline_doc)
    rf_config = env_doc.get('rf_config') or (env_doc.get('scenario_declaration') or {}).get('rf_config')
    environment = _timed(lambda: rf_environment.analyze_environment(
        frame, demod, rf_config, baseline), timings, 'environment')
    artifact = manifest[merged_release.MODREC4_MODEL_KEY]
    adapter = rf_modrec4_adapter.ModRec4Adapter.from_release(release, artifact)
    cfg = envelope.submitted.config
    context = {'format': 's16_iq_le', 'samples': frame.samples,
               'sample_rate_hz': frame.sample_rate_hz, 'decimation': frame.decimation,
               'symbol_rate_hz': cfg.symbol_rate_hz, 'shaping': cfg.shaping}
    modrec4 = _timed(lambda: adapter.evaluate_iq(
        iq, context=context, environment=environment, demod=demod,
        six_class=six, frame_id=frame.capture_id), timings, 'modrec4')
    if isinstance(modrec4, dict):
        modrec4['frame_identity'] = {
            'capture_id': frame.capture_id, 'request_id': frame.request_id,
            'daemon_timestamp_ns': frame.daemon_timestamp_ns,
            'release_id': frame.release_id,
            'connection_generation': frame.connection_generation,
            'config_revision': frame.config_revision,
            'iq_sha256': hashlib.sha256(frame.payload).hexdigest(),
        }
    total = sum(v['seconds'] for v in timings.values())
    return {
        'schema': 'rf-modrec4-fullpath-v1',
        'release_id': manifest['release_id'],
        'frame': {'capture_id': frame.capture_id, 'samples': frame.samples,
                  'sample_rate_hz': frame.sample_rate_hz, 'decimation': frame.decimation},
        'stages': timings, 'total_seconds': total,
        'six_class': six, 'environment': environment,
        'modrec4': modrec4, 'analysis_keys': sorted(analysis.keys()) if isinstance(analysis, dict) else None,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frame-dir', required=True, type=Path)
    p.add_argument('--tag', required=True)
    p.add_argument('--release', type=Path, default=ROOT)
    p.add_argument('--output', type=Path)
    a = p.parse_args(argv)
    out = run(a.frame_dir, a.tag, a.release)
    text = json.dumps(out, indent=2, default=str)
    if a.output:
        a.output.write_text(text + '\n', encoding='utf-8')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
