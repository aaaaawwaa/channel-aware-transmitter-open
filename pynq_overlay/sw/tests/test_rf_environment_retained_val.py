"""The retained 40 dB S1 VAL is historical evidence, not the current domain.

Since the 2026-10-17 migration the teaching/environment domain is the single-source
46 dB, so the S1-only calibration entry refuses to fit a baseline from the 40 dB
campaign instead of stamping the current domain on those frames.  The recorded VAL
measurement stays visible below as an explicit, guarded skip (its 40 dB domain is no
longer constructible in this tree), and the split-leak rule it carried at the
`rf_environment_offline.verify` layer -- a different check from the dataset lineage
rule in `rf_environment_dataset.validate_splits` -- is re-proved here on a synthetic
CURRENT-domain baseline.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts.rf_environment_dataset import calibrate_s1      # noqa: E402
from scripts.rf_environment_offline import verify            # noqa: E402
import rf_environment as ENV                                 # noqa: E402
import rf_teaching_profile as PROFILE                        # noqa: E402

PILOT = ROOT / 'evidence/rf-teaching/s1/board-pilot-2026-10-04-v1'
HISTORICAL_40DB_SKIP = pytest.mark.skip(
    reason='historical 40 dB replay: the product teaching profile is the single 46 dB '
           'source and scripts.rf_environment_dataset.calibrate_s1 refuses to fit a 40 dB '
           'campaign in this domain.  The assertions stay visible for the M11 re-sign or a '
           'historical interpreter; test_the_historical_40db_replay_is_still_unavailable '
           'fails first if this skip ever becomes stale.')


def test_s1_calibration_refuses_to_relabel_the_retained_40db_campaign(tmp_path):
    """A 40 dB campaign may not become a 46 dB baseline, however convenient."""
    assert PILOT.is_dir(), PILOT
    with pytest.raises(ValueError) as refused:
        calibrate_s1(PILOT, tmp_path / 'calibration')
    message = str(refused.value)
    assert 'historical 40 dB domain' in message, message
    assert '46 dB calibration entry' in message, message
    assert not (tmp_path / 'calibration').exists(), 'a refusal must write nothing'


def test_the_historical_40db_replay_is_still_unavailable(tmp_path):
    """Guard for the skip below: it is current only while 46 dB is the domain."""
    assert dict(PROFILE.teaching_capability().rf_parameters)['attenuation_mdB'] == 46000
    assert ENV.FIXED_RF == tuple(PROFILE.teaching_capability().rf_parameters)
    with pytest.raises(ValueError, match='historical 40 dB domain'):
        calibrate_s1(PILOT, tmp_path / 'calibration')


@HISTORICAL_40DB_SKIP
def test_real_normal_val_and_derived_anomalies(tmp_path):
    """Restored from HEAD~1: the recorded VAL counts of the real 40 dB campaign."""
    calibrate_s1(PILOT, tmp_path / 'calibration')
    result = verify(PILOT, tmp_path / 'calibration/baseline.json', tmp_path / 'validation')
    counts = result['counts']
    assert counts['normal']['correct'] == 10
    assert counts['narrowband']['correct'] >= 27, counts['narrowband']
    assert counts['pulse']['correct'] >= 27, counts['pulse']
    assert counts['compound']['labels']['CLEAR'] == 0, counts['compound']
    assert counts['off']['labels']['CLEAR'] == 0
    assert result['formal_test'] == 'NOT_RUN'


def _synthetic_baseline(sources):
    """A CURRENT-domain (46 dB/v4) baseline body with declared TRAIN provenance."""
    thresholds = dict(tone_concentration=.5, tone_excess=.2, tone_weak_excess=.05,
                      pulse_crest=1.5, pulse_outlier=1.6, minimum_rms_codes=16.,
                      pulse_max_fraction=.05, pulse_min_samples=4.,
                      pulse_after_tone_min_samples=8., pulse_after_tone_min_density=.3,
                      pulse_plateau_outlier=1.5, pulse_plateau_min_samples=16.,
                      pulse_plateau_min_density=.35)
    return ENV.EnvironmentBaseline(tuple([0.0] * 4096), tuple(sorted(thresholds.items())),
                                   tuple(tuple(sorted(row.items())) for row in sources))


def _synthetic_pilot(tmp_path, payload, digest):
    pilot = tmp_path / 'pilot'
    pilot.mkdir()
    (pilot / 's02-on-00.iq').write_bytes(payload)
    (pilot / 's02-on-00.json').write_text(json.dumps({'iq_sha256': digest}),
                                          encoding='utf-8')
    (pilot / 'result.json').write_text(json.dumps({'records': [
        dict(tag='s02-on-00', on=True, iq_sha256=digest)]}), encoding='utf-8')
    return pilot


def test_baseline_containing_val_source_cannot_relabel_same_iq_as_holdout(tmp_path):
    """`verify` must refuse a baseline whose TRAIN provenance is the VAL holdout's IQ.

    This is the baseline-source leakage check that lives in rf_environment_offline.verify
    (it feeds the baseline provenance into the shared split validator); the dataset
    lineage rule alone does not cover it.
    """
    payload = bytes(range(256)) * 64        # 16384 bytes, deterministic
    digest = hashlib.sha256(payload).hexdigest()
    pilot = _synthetic_pilot(tmp_path, payload, digest)
    claimed = dict(source_id='s1-s02-on-00', session_id='s1-s02', split='train',
                   iq_sha256=digest)
    leaked = tmp_path / 'leaked.json'
    leaked.write_text(json.dumps(_synthetic_baseline([claimed]).to_dict()),
                      encoding='utf-8')
    with pytest.raises(ValueError, match='leakage across splits'):
        verify(pilot, leaked, tmp_path / 'invalid-validation')
    assert not (tmp_path / 'invalid-validation').exists(), 'a refusal must write nothing'
    # Positive control: a baseline that does not claim that IQ gets past the leakage
    # check (and fails later on the intentionally minimal metadata), so the rejection
    # above is the leak and not the harness.
    clean = tmp_path / 'clean.json'
    clean.write_text(json.dumps(_synthetic_baseline(
        [dict(source_id='s1-other-00', session_id='s1-other', split='train',
              iq_sha256='a' * 64)]).to_dict()), encoding='utf-8')
    with pytest.raises(KeyError):
        verify(pilot, clean, tmp_path / 'clean-validation')
