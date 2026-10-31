"""The retained 40 dB development pilots are historical evidence, not the domain.

Retained actual IQ was used for this regression, never counted as fresh TEST.
Since the 2026-10-17 migration the teaching/environment domain is the single-source
46 dB, so calibrating a baseline from these 40 dB pilots is refused by name instead
of relabelling them: the archived measurements stay in the 40 dB evidence and in
the preserved historical code.  The original tone-residual regression is kept below as
an explicit, guarded skip so the assertion stays visible for the M11 re-sign or a
historical interpreter.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.rf_environment_dataset import calibrate_s1
from scripts.rf_bpsk_replay import frame_from_metadata
from rf_environment import EnvironmentBaseline, analyze_environment, FIXED_RF
from rf_bpsk_pipeline import DemodConfig, analyze_demod
from rf_bpsk_receiver import BpskConfig

ROOT = Path(__file__).resolve().parents[3]
PILOT = ROOT / 'evidence/rf-teaching/s1/board-pilot-2026-10-04-v1'
PILOTS = [ROOT / ('evidence/rf-teaching/s2/board-pilot-2026-10-05-v%d' % n)
          for n in (1, 2)]


def test_the_retained_40db_pilots_cannot_be_replayed_as_the_current_domain(tmp_path):
    for pilot in [PILOT] + PILOTS:
        assert pilot.is_dir(), pilot
    output = tmp_path / 'calibration'
    with pytest.raises(ValueError, match='historical 40 dB domain'):
        calibrate_s1(PILOT, output)
    assert not output.exists(), 'a refusal must write nothing'


@pytest.mark.skip(reason='historical 40 dB tone-residual regression: calibrate_s1 refuses '
                         'the 40 dB domain in this tree.  Kept visible for the M11 re-sign '
                         'or a historical interpreter; the refusal test above fails first '
                         'if this skip ever becomes stale.')
def test_real_development_tone_residuals_do_not_become_pulses(tmp_path):
    """Restored from HEAD~1: real 40 dB pilot tone residuals must not become PULSE."""
    calibrate_s1(PILOT, tmp_path / 'calibration')
    baseline = EnvironmentBaseline.from_dict(
        json.loads((tmp_path / 'calibration/baseline.json').read_text(encoding='utf-8')))
    observed = []
    for pilot in PILOTS:
        for row in json.loads((pilot / 'result.json').read_text(encoding='utf-8'))['records']:
            tag = row['tag']
            meta = json.loads((pilot / (tag + '.json')).read_text(encoding='utf-8'))
            frame = frame_from_metadata((pilot / (tag + '.iq')).read_bytes(), meta)
            submitted = meta['demod_result']['submitted']
            cfg = DemodConfig(BpskConfig(**submitted['config']), submitted['revision'],
                              submitted['decim'], submitted['rx_len'], submitted['input_fs_hz'])
            result = analyze_environment(frame, analyze_demod(frame, cfg), dict(FIXED_RF),
                                         baseline)
            observed.append((row['truth'], result.label))
    expected = {'normal': 'CLEAR', 'narrowband': 'NARROWBAND', 'pulse': 'PULSE',
                'compound': 'UNKNOWN', 'domain': 'INVALID'}
    assert all(label == expected[kind] for kind, label in observed if kind in expected), observed
    assert all(label != 'CLEAR' for kind, label in observed if kind == 'off')
