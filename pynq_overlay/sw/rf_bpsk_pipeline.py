"""Pure frozen handoff from explicit RX settings to the finite BPSK engine."""
from dataclasses import dataclass

from rf_bpsk_receiver import BpskConfig, BpskResult, VERSION, receive_bpsk
from rf_rx_types import RxIqFrame


@dataclass(frozen=True)
class DemodConfig:
    config: BpskConfig
    revision: int
    decim: int
    rx_len: int
    input_fs_hz: int


@dataclass(frozen=True)
class BpskEnvelope:
    result: BpskResult
    submitted: DemodConfig
    algorithm_version: str


def freeze_demod_config(draft, revision):
    """Copy RX intent only; disabled reference does not inspect a TX seed."""
    if not isinstance(draft, dict) or type(revision) is not int or revision < 0:
        raise ValueError('explicit RX draft and nonnegative revision required')
    enabled = draft.get('reference_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('reference_enabled must be boolean')
    seed = draft.get('reference_seed') if enabled else None
    if enabled and (type(seed) is not int or not 1 <= seed <= 127):
        raise ValueError('reference seed must be integer 1..127')
    fields = {name: draft.get(name) for name in ('decim', 'rx_len', 'input_fs_hz')}
    if any(type(value) is not int or value <= 0 for value in fields.values()):
        raise ValueError('explicit integer acquisition settings required')
    if fields['decim'] not in (1, 2, 4, 8, 16) or fields['rx_len'] > 4096:
        raise ValueError('invalid acquisition settings')
    rate = draft.get('symrate_hz')
    mode, shaping = draft.get('expected_mode'), draft.get('shaping', 'RECT')
    if type(rate) is not int or rate <= 0 or not isinstance(mode, str) \
            or not isinstance(shaping, str):
        raise ValueError('explicit RX modulation settings required')
    return DemodConfig(BpskConfig(mode, rate, shaping, seed), revision, **fields)


def failed_demod(frame, submitted, reason='DEMOD_FAILED'):
    """Bind one failure to its frame; never inherit measurements from another."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, DemodConfig):
        raise TypeError('expected frozen frame and DemodConfig')
    result = BpskResult(capture_id=frame.capture_id, release_id=frame.release_id,
                       config_revision=frame.config_revision,
                       connection_generation=frame.connection_generation,
                       completed_at_monotonic=frame.completed_at_monotonic,
                       reason=str(reason), reference_sync=(False if
                       submitted.config.reference_prbs7_seed is not None else None))
    return BpskEnvelope(result, submitted, VERSION)


def analyze_demod(frame, submitted):
    """Validate submitted acquisition against this frame before pure calculation."""
    if not isinstance(frame, RxIqFrame) or not isinstance(submitted, DemodConfig):
        raise TypeError('expected frozen frame and DemodConfig')
    if (submitted.decim != frame.decimation or submitted.rx_len != frame.samples
            or submitted.input_fs_hz != frame.sample_rate_hz):
        return failed_demod(frame, submitted, 'ACQUISITION_MISMATCH')
    return BpskEnvelope(receive_bpsk(frame, submitted.config), submitted, VERSION)
