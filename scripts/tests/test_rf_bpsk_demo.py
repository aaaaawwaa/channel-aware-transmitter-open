"""Tests for the fixed-condition offline BPSK demo (frozen engine, saved IQ only)."""
import json
import shutil
from pathlib import Path

import pytest

from scripts import rf_bpsk_demo as demo

CAPTURES = (Path(__file__).resolve().parents[2]
            / 'evidence/rf-teaching/s1/board-pilot-2026-10-04-v1')


def copy_capture(tmp_path, tag):
    for suffix in ('.iq', '.json'):
        shutil.copy2(CAPTURES / (tag + suffix), tmp_path / (tag + suffix))
    return tmp_path


def test_every_saved_frame_reproduces_its_recorded_result():
    report = demo.run_demo(CAPTURES)
    assert report['schema'] == 'rf-bpsk-offline-demo-v1'
    assert report['passed'] is True, report['failed']
    assert report['frame_count'] == 33
    assert all(frame['mismatches'] == [] for frame in report['frames'])
    assert all(frame['max_float_error'] <= demo.TOLERANCE for frame in report['frames'])
    assert report['scope'].startswith('offline replay of saved IQ')
    assert any('not a new' in line for line in report['limitations'])


def test_a_synced_frame_reports_reference_sync_and_zero_errors():
    report = demo.run_demo(CAPTURES, tags=['s00-on-00'])
    frame = report['frames'][0]
    assert frame['ok'] is True
    assert frame['observables']['reference_sync'] is True
    assert frame['observables']['bit_errors'] == 0
    assert frame['observables']['compared_bits'] == 138
    assert frame['observables']['alignment_bits'] == 63


def test_a_reference_off_frame_has_no_ber_and_is_still_reproducible():
    report = demo.run_demo(CAPTURES, tags=['s00-off-10'])
    frame = report['frames'][0]
    assert frame['ok'] is True
    assert frame['observables']['reference_sync'] is False
    assert frame['observables']['bit_errors'] is None
    assert frame['observables']['compared_bits'] is None


def test_the_tag_filter_selects_exactly_the_requested_frames():
    report = demo.run_demo(CAPTURES, tags=['s01-on-03', 's02-off-10'])
    assert [frame['tag'] for frame in report['frames']] == ['s01-on-03', 's02-off-10']


def test_a_tampered_iq_is_a_named_failure_not_a_pass(tmp_path):
    copy_capture(tmp_path, 's00-on-00')
    raw = (tmp_path / 's00-on-00.iq').read_bytes()
    (tmp_path / 's00-on-00.iq').write_bytes(raw[:100] + bytes([raw[100] ^ 0xFF])
                                             + raw[101:])
    report = demo.run_demo(tmp_path, tags=['s00-on-00'])
    assert report['passed'] is False
    assert report['frames'][0]['error'] == 'IQ_HASH_MISMATCH'


def test_missing_metadata_is_a_named_failure(tmp_path):
    copy_capture(tmp_path, 's00-on-00')
    (tmp_path / 's00-on-00.json').unlink()
    report = demo.run_demo(tmp_path, tags=['s00-on-00'])
    assert report['passed'] is False
    assert report['frames'][0]['error'] == 'MISSING_METADATA'


def test_a_capture_outside_the_fixed_condition_is_refused(tmp_path):
    copy_capture(tmp_path, 's00-on-00')
    meta = json.loads((tmp_path / 's00-on-00.json').read_text(encoding='utf-8'))
    meta['sample_rate_hz'] = 60000001
    (tmp_path / 's00-on-00.json').write_text(json.dumps(meta), encoding='utf-8')
    report = demo.run_demo(tmp_path, tags=['s00-on-00'])
    assert report['passed'] is False
    assert report['frames'][0]['error'] == 'OUT_OF_DEMO_CONDITION'


def test_a_missing_capture_is_a_named_failure(tmp_path):
    report = demo.run_demo(tmp_path, tags=['s00-on-00'])
    assert report['passed'] is False
    assert report['frames'][0]['error'] == 'MISSING_IQ'


def test_an_empty_directory_is_a_named_error():
    with pytest.raises(demo.DemoError) as caught:
        demo.run_demo(Path(__file__).resolve().parent)
    assert caught.value.code == 'NO_CAPTURES'


def test_the_demo_is_deterministic():
    first = demo.run_demo(CAPTURES, tags=['s00-on-00', 's02-off-10'])
    second = demo.run_demo(CAPTURES, tags=['s00-on-00', 's02-off-10'])
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_recorded_result_comparison_uses_the_reviewed_tolerance():
    replayed = {'a': 1.0, 'b': 2.0, 'ok': True}
    recorded = {'a': 1.0 + 1e-9, 'b': 2.0 + 1e-3, 'ok': True}
    mismatches, max_error = demo.compare_results(replayed, recorded)
    assert mismatches == ['b']
    assert max_error == pytest.approx(1e-3)


def test_the_cli_passes_on_the_saved_set(tmp_path):
    out = tmp_path / 'demo.json'
    assert demo.main([str(CAPTURES), '--tag', 's00-on-00', '--output', str(out)]) == 0
    report = json.loads(out.read_text(encoding='utf-8'))
    assert report['passed'] is True and report['engine_version'] == demo.VERSION


def test_the_cli_returns_two_for_an_unusable_directory(tmp_path):
    assert demo.main([str(tmp_path)]) == 2


def test_the_module_never_touches_a_board_or_the_network():
    import inspect
    source = inspect.getsource(demo)
    for forbidden in ('paramiko', 'pynq_ssh', 'socket', 'subprocess', 'requests',
                      'rf_teaching_deploy'):
        assert forbidden not in source
