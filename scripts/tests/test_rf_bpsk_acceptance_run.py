"""Tests for the offline acceptance runner over the approved BPSK criteria."""
import json
import shutil
from pathlib import Path

from scripts import rf_bpsk_acceptance_run as acc

ROOT = Path(__file__).resolve().parents[2]
CAPTURES = ROOT / 'evidence/rf-teaching/s1/board-pilot-2026-10-04-v1'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-observer-next-2026-10-19/'
             'candidate/releases/3f84f6a189e833b6986f4c819ff89be0')


def copy_capture(tmp_path, tag):
    for suffix in ('.iq', '.json'):
        shutil.copy2(CAPTURES / (tag + suffix), tmp_path / (tag + suffix))
    return tmp_path


def test_single_frame_acceptance_passes_with_the_approved_facts():
    report = acc.run_single(CAPTURES, 's00-on-00')
    assert report['schema'] == 'rf-bpsk-acceptance-run-v1'
    assert report['verdict'] == 'PASS'
    names = {check['name'] for check in report['checks']}
    assert {'reference_sync', 'bit_errors_zero', 'compared_bits_138',
            'total_symbols_201', 'constellation_points_128',
            'replay_matches_recorded'} <= names
    assert all(check['ok'] for check in report['checks'])
    assert any(row.get('sha256') for row in report['evidence'])
    assert report['limitations']


def test_statistics_acceptance_matches_the_s1_numbers():
    report = acc.run_statistics(CAPTURES, expect_synced=30, expect_compared=4140)
    assert report['verdict'] == 'PASS'
    assert report['reference_frames'] == 30
    assert report['total_bit_errors'] == 0
    assert report['total_compared_bits'] == 4140
    assert report['reference_off_frames'] == 3


def test_statistics_with_no_reference_frame_is_unknown(tmp_path):
    copy_capture(tmp_path, 's00-off-10')
    report = acc.run_statistics(tmp_path)
    assert report['verdict'] == 'UNKNOWN'
    assert 'INSUFFICIENT_EVIDENCE' in report['reason']


def test_a_tampered_capture_fails_single_acceptance(tmp_path):
    copy_capture(tmp_path, 's00-on-00')
    iq = tmp_path / 's00-on-00.iq'
    raw = iq.read_bytes()
    iq.write_bytes(raw[:50] + bytes([raw[50] ^ 0xFF]) + raw[51:])
    report = acc.run_single(tmp_path, 's00-on-00')
    assert report['verdict'] == 'FAIL'
    assert report['checks'][0]['name'] == 'capture_readable'
    assert report['checks'][0]['ok'] is False


def test_a_missing_directory_fails_single_acceptance(tmp_path):
    report = acc.run_single(tmp_path / 'nope', 's00-on-00')
    assert report['verdict'] == 'FAIL'


def test_candidate_acceptance_passes_on_the_approved_candidate():
    report = acc.run_candidate(CANDIDATE)
    assert report['verdict'] == 'PASS', [c for c in report['checks']
                                         if not c['ok']]
    assert report['release_id'] == '3f84f6a189e833b6986f4c819ff89be0'
    names = {check['name'] for check in report['checks']}
    assert {'release_verifiable', 'panel_imports', 'daemon_version',
            'baseline_sources', 'baseline_file_sha256',
            'baseline_canonical_sha256'} <= names


def test_candidate_acceptance_fails_on_a_missing_release(tmp_path):
    report = acc.run_candidate(tmp_path / 'nope')
    assert report['verdict'] == 'FAIL'
    assert report['checks'][0]['name'] == 'release_dir_present'


def test_cli_exit_codes_and_json_output(tmp_path):
    out = tmp_path / 'single.json'
    assert acc.main(['single', '--directory', str(CAPTURES), '--tag', 's00-off-10',
                     '--json', str(out)]) == 1
    assert json.loads(out.read_text(encoding='utf-8'))['verdict'] == 'FAIL'
    out2 = tmp_path / 'stats.json'
    assert acc.main(['statistics', '--directory', str(CAPTURES),
                     '--json', str(out2)]) == 0
    assert json.loads(out2.read_text(encoding='utf-8'))['verdict'] == 'PASS'
