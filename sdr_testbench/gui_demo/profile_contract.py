"""Shared display/TX contract. Importing this module never accesses hardware."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PROFILE = HERE / 'pluto_bpsk.json'


def _radio():
    parent = str(HERE.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    import radio_test
    return radio_test


def validate_profile(config):
    _radio().validate_bpsk_tx(config, 'offline profile validation; no hardware')
    return config


def load_profile(path=PROFILE):
    return validate_profile(json.loads(Path(path).read_text(encoding='utf-8')))


def preview_samples(config):
    validate_profile(config)
    samples, _ = _radio().bpsk_tx_payload(config)
    return samples


def preview_symbols(seed=1):
    config = dict(load_profile(), prbs7_seed=int(seed))
    samples = preview_samples(config)[::2]
    scale = abs(samples[0])
    return [x / scale for x in samples]


def tx_command(config, wiring, board_tx_off, config_path=PROFILE,
               stop_path=None, summary_path=None):
    validate_profile(config)
    if not isinstance(wiring, str) or not wiring.strip():
        raise ValueError('请填写本次真实接线，例如 Pluto TX -> board RX1')
    if board_tx_off is not True:
        raise ValueError('请先确认板卡TX已关闭，且新接线为外部SDR到板卡RX')
    args = [sys.executable, '-B', '-u', str(HERE / 'tx_worker.py'),
            'tx-bpsk', '--config', str(config_path), '--path-confirmed', wiring.strip()]
    if stop_path:
        args += ['--stop-file', str(stop_path)]
    if summary_path:
        args += ['--summary-file', str(summary_path)]
    return args


def receipt_state(receipt):
    if receipt.get('tx_powerdown_readback') != '1':
        return 'CLEANUP_UNVERIFIED'
    if receipt.get('status') == 'CLEANUP_FAILED':
        return 'CLEANUP_UNVERIFIED'
    if receipt.get('status') != 'TX_BPSK_STREAM_COMPLETED_RF_UNVERIFIED':
        return 'STOPPED_WITH_ERROR'
    stream = receipt.get('stream') or {}
    seconds = receipt.get('stream_seconds')
    if (stream.get('stream_stop_confirmed') is not True
            or not isinstance(seconds, (int, float)) or not 0 < seconds <= 2.0):
        return 'STOPPED_WITH_ERROR'
    return 'TX_FINISHED_RF_UNVERIFIED'
