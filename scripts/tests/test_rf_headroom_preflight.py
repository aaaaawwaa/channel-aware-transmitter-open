"""Preflight must reject incomplete observations before any upload."""
import copy
import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import rf_headroom_deploy as D


def valid_snapshot():
    return dict(current_release=D.ROLLBACK_PARENT,
                active_release='/home/xilinx/overlays/awg_merged/releases/' + D.ROLLBACK_PARENT,
                boot_id='236dbcc4-a6db-a1ca-a1d5-d5bc61955d45', uptime_seconds=1000.0,
                units={name: dict(rc=0, err='', out='ActiveState=active\nSubState=running\nMainPID=123\nNRestarts=0')
                       for name in ('pynq-status', 'rf-daemon')},
                kernel_storage='', mounts='/dev/mmcblk0p2 / ext4 rw,relatime 0 0\n/dev/mmcblk0p1 /boot vfat rw 0 0',
                commands={'kernel_storage': dict(rc=0, out='', err=''),
                          'mounts': dict(rc=0, out='/dev/mmcblk0p2 / ext4 rw,relatime 0 0\n/dev/mmcblk0p1 /boot vfat rw 0 0', err='')})


@pytest.mark.parametrize('fault', ['outer_rc', 'kernel_missing', 'kernel_none', 'kernel_command_failed',
                                    'units_missing', 'unit_failed', 'unit_stopped', 'root_ro',
                                    'mounts_failed', 'boot_missing', 'uptime_nan', 'release_mismatch'])
def test_incomplete_snapshot_is_rejected(monkeypatch, tmp_path, fault):
    data = valid_snapshot()
    rc = 0
    if fault == 'outer_rc': rc = 1
    elif fault == 'kernel_missing': del data['kernel_storage']
    elif fault == 'kernel_none': data['kernel_storage'] = None
    elif fault == 'kernel_command_failed': data['commands']['kernel_storage']['rc'] = 1
    elif fault == 'units_missing': data['units'] = {}
    elif fault == 'unit_failed': data['units']['rf-daemon']['rc'] = 1
    elif fault == 'unit_stopped': data['units']['rf-daemon']['out'] = 'ActiveState=inactive\nSubState=dead\nMainPID=0\nNRestarts=0'
    elif fault == 'root_ro': data['mounts'] = data['mounts'].replace('rw,relatime', 'ro,relatime'); data['commands']['mounts']['out'] = data['mounts']
    elif fault == 'mounts_failed': data['commands']['mounts']['rc'] = None
    elif fault == 'boot_missing': del data['boot_id']
    elif fault == 'uptime_nan': data['uptime_seconds'] = float('nan')
    elif fault == 'release_mismatch': data['active_release'] = '/home/xilinx/overlays/awg_merged/releases/' + 'f' * 32
    def run(client, helper, command, log, timeout):
        Path(log).write_text('DEPLOY_SNAPSHOT ' + json.dumps(data) + '\n', encoding='utf8')
        return rc
    monkeypatch.setattr(D, '_run', run)
    snapshot, faults = D.board_snapshot(None, None, tmp_path, D.AttemptJournal(tmp_path / 'journal'), 'pre')
    assert snapshot == {}, fault


def test_kernel_snapshot_cannot_hide_early_fault_by_tail():
    assert 'tail -n' not in D.SNAPSHOT
    assert '| grep' not in D.SNAPSHOT, 'pipeline masks dmesg failures'


def test_complete_snapshot_preserves_subcommand_results(monkeypatch, tmp_path):
    data = valid_snapshot()
    def run(client, helper, command, log, timeout):
        Path(log).write_text('DEPLOY_SNAPSHOT ' + json.dumps(data), encoding='utf8')
        return 0
    monkeypatch.setattr(D, '_run', run)
    snapshot, faults = D.board_snapshot(None, None, tmp_path, D.AttemptJournal(tmp_path / 'journal'), 'pre')
    assert snapshot == data
    assert faults == []
