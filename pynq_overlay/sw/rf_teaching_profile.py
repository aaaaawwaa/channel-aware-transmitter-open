"""One immutable, explicitly selected teaching domain; no hardware operations.

ATTENUATION_MDB is the single source of the RF attenuation this domain runs at.
Client gates, panel drafts, the environment acceptance declaration and the board
session plan read it from here instead of carrying a private literal, so the
domain cannot fork into two values.  Any other value is refused, never coerced.
"""
from dataclasses import asdict, dataclass

# The one RF attenuation (mdB) every teaching/environment surface must agree on.
ATTENUATION_MDB = 46000


@dataclass(frozen=True)
class TeachingCapability:
    device: str = 'AD9361'
    mode: str = 'BPSK'
    symbol_rate_hz: int = 3000000
    shaping: str = 'RECT'
    input_fs_hz: int = 60000000
    decim: int = 1
    rx_len: int = 4096
    reference_enabled: bool = False
    reference_seed: int = 1
    tx_amplitude: int = 60
    rf_parameters: tuple = (('lo_hz',80000000),('sample_rate_hz',60000000),
        ('rx_bandwidth_hz',30000000),('tx_bandwidth_hz',30000000),
        ('attenuation_mdB',ATTENUATION_MDB),('phy_mode_1r1t',0),('idelay_en',1),('idelay_tap',29))

    def to_dict(self):
        return asdict(self)


def teaching_capability():
    return TeachingCapability()


def fill_drafts(app):
    """Use existing draft setters; refuse a running/incompatible applied setup."""
    if app.rf_transmitting() or app._rf_inflight_action is not None:
        return '请先停止并等待当前操作完成'
    if app.rx_capture.get('state') in ('queued','running','cancelling'):
        return '请先结束当前捕获'
    cap=teaching_capability()
    applied=app._rf_applied_parameters.get('sample_rate_hz')
    if applied is not None and (applied % cap.symbol_rate_hz or
                               not 2 <= applied//cap.symbol_rate_hz <= 32):
        return '请先在高级参数页配置60MHz采样率，再填写教学草稿'
    app.select_device(cap.device)
    actions=[(app.set_rf_parameter,key,value) for key,value in cap.rf_parameters]
    actions += [(app.set_rf_signal_kind,'digital',None)]
    actions += [(app.set_rf_digital_parameter,key,value) for key,value in
        (('mode',cap.mode),('filter',cap.shaping),('rolloff','0.35'),
         ('seed',1),('symbol_rate_hz',cap.symbol_rate_hz),('amplitude',cap.tx_amplitude))]
    actions += [(app.set_rx_demod_setting,key,value) for key,value in
        (('expected_mode',cap.mode),('symrate_hz',cap.symbol_rate_hz),('shaping',cap.shaping),
         ('input_fs_hz',cap.input_fs_hz),('decim',cap.decim),('rx_len',cap.rx_len),
         ('reference_enabled',cap.reference_enabled),('reference_seed',cap.reference_seed))]
    for setter,key,value in actions:
        why=setter(key) if value is None else setter(key,value)
        if why is not None:
            return why
    return None
