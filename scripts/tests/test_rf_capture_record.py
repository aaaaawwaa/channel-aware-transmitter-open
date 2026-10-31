"""Tests for the unified capture record and its byte-checkable replay."""
import json
import shutil
from pathlib import Path

import pytest

from scripts import rf_capture_record as rec

CAPTURES = (Path(__file__).resolve().parents[2]
            / 'evidence/rf-teaching/s1/board-pilot-2026-10-04-v1')
DEMO = (Path(__file__).resolve().parents[2]
        / 'evidence/rf-teaching/s2-46db-observer-next-2026-10-19/demo')


def write_tag(tmp_path, tag):
    return rec.write_bundle(CAPTURES, tag, tmp_path / 'records')


def test_write_verify_and_replay_a_synced_capture(tmp_path):
    record = write_tag(tmp_path, 's00-on-00')
    assert record['schema'] == 'rf-capture-record-v1'
    assert record['status'] == 'PASS'
    bundle = tmp_path / 'records' / 's00-on-00'
    assert (bundle / 'record.json').is_file()
    assert (bundle / 'frame.iq').is_file()
    assert (bundle / 'metadata.json').is_file()
    verdict = rec.verify_bundle(bundle)
    assert verdict['passed'] is True, verdict['failures']
    replay = rec.replay_bundle(bundle)
    assert replay['passed'] is True and replay['mismatches'] == []
    assert replay['replayed_reference_sync'] is True


def test_a_failed_demod_is_recorded_as_failed_and_its_replay_reproduces_it(tmp_path):
    record = write_tag(tmp_path, 's00-off-10')
    assert record['status'] == 'FAILED'
    assert record['status_reason'].startswith('demod_reason=')
    replay = rec.replay_bundle(tmp_path / 'records' / 's00-off-10')
    assert replay['passed'] is True
    assert replay['status'] == 'FAILED'
    assert replay['replayed_reason'] == 'CARRIER_NOT_SYNCED'
    assert replay['replayed_reference_sync'] is False


def test_a_tampered_frame_is_caught_by_verify_and_replay(tmp_path):
    write_tag(tmp_path, 's00-on-00')
    frame = tmp_path / 'records' / 's00-on-00' / 'frame.iq'
    raw = frame.read_bytes()
    frame.write_bytes(raw[:100] + bytes([raw[100] ^ 0xFF]) + raw[101:])
    assert rec.verify_bundle(frame.parent)['failures'] == ['FRAME_IQ_HASH_MISMATCH']
    assert rec.replay_bundle(frame.parent)['passed'] is False


def test_a_tampered_metadata_file_is_caught(tmp_path):
    write_tag(tmp_path, 's00-on-00')
    meta = tmp_path / 'records' / 's00-on-00' / 'metadata.json'
    meta.write_bytes(meta.read_bytes() + b'\n')
    assert 'METADATA_HASH_MISMATCH' in rec.verify_bundle(meta.parent)['failures']


def test_an_existing_bundle_is_never_overwritten(tmp_path):
    write_tag(tmp_path, 's00-on-00')
    with pytest.raises(rec.DemoError) as caught:
        write_tag(tmp_path, 's00-on-00')
    assert caught.value.code == 'RECORD_EXISTS'


def test_a_missing_capture_is_a_named_error(tmp_path):
    with pytest.raises(rec.DemoError) as caught:
        rec.write_bundle(tmp_path, 'nope', tmp_path / 'records')
    assert caught.value.code == 'MISSING_CAPTURE'


def test_replay_all_reports_failure_records_separately(tmp_path):
    for tag in ('s00-on-00', 's00-off-10', 's01-off-10'):
        write_tag(tmp_path, tag)
    report = rec.replay_all(tmp_path / 'records')
    assert report['schema'] == 'rf-capture-replay-v1'
    assert report['passed'] is True
    assert report['record_count'] == 3
    assert report['failure_records_replayed'] == 2
    assert report['failed'] == []


def test_replay_all_on_an_empty_root_is_a_named_error(tmp_path):
    with pytest.raises(rec.DemoError) as caught:
        rec.replay_all(tmp_path)
    assert caught.value.code == 'NO_RECORDS'
