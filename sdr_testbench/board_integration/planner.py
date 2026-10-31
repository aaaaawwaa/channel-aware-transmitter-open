"""Pure, offline planner for the PlutoSDR <-> board 433.9 MHz diagnostic.

Nothing in this module opens a serial port, a socket, libiio, GNU Radio or a
VISA resource.  The hardware executor lives in `board_integration.py` and
imports this module; that separation is what makes `--dry-run` provably
hardware-free.

Domain boundary: this is a NEW 433.9 MHz diagnostic, not the frozen 80 MHz S2
domain.  The board attenuation is pinned to the frozen 46000 mdB value, but no
S2 baseline, threshold, nonce or PASS claim is reused here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

SCHEMA = 'pluto-board-integration-v1'
BOARD_LO_HZ = 433900000
BOARD_FS_HZ = 60000000
BOARD_RX_BW_HZ = 30000000
BOARD_TX_BW_HZ = 30000000
BOARD_ATTENUATION_MDB = 46000
BOARD_SAFE_ATTENUATION_MDB = 89750
BOARD_BRAM_WORDS = 4096
FROZEN_AMPLITUDE_CODE = 60

PLUTO_FS_HZ = 2400000
PLUTO_BW_HZ = 1500000
PLUTO_TX_ATTENUATION_DB = 60.0
PLUTO_AMPLITUDE = 0.10
PLUTO_RX_GAIN_DB = 0.0
PLUTO_DURATION_S = 2.0
PLUTO_CAPTURE_SAMPLES = 262144
PLUTO_MAX_AMPLITUDE = 0.25
PLUTO_MIN_TX_ATTENUATION_DB = 60.0
PLUTO_SAFE_ATTENUATION_DB = 89.75
PLUTO_SAFE_HARDWARE_GAIN_DB = -89.75

# --- Minimal Stage 0: Pluto TX -> Pluto RX self-loop (Pluto only) -----------
#
# Stage 0 proves the SDR's own transmit/receive chain before any board is
# touched.  It is not the 80 MHz S2 domain and it never opens COM11, arms a
# board, uses a token or generates a nonce.
STAGE0_SCHEMA = 'pluto-stage0-selfloop-v1'
STAGE0_LO_HZ = 433900000
STAGE0_FS_HZ = 2400000
STAGE0_BANDWIDTH_HZ = 1500000
STAGE0_TONE_HZ = 100000.0
STAGE0_AMPLITUDE = 0.10
STAGE0_TX_ATTENUATION_DB = 60.0
#: libiio's hardwaregain is written and read as the NEGATIVE of the requested
#: attenuation ("-60.000000 dB").  Both signs are named so neither phase can
#: compare a readback against the positive attenuation by accident.
STAGE0_TX_HARDWARE_GAIN_DB = -60.0
STAGE0_CEILING_DB = 89.75
STAGE0_CEILING_HARDWARE_GAIN_DB = -89.75
STAGE0_RX_GAIN_DB = 0.0
STAGE0_CAPTURE_SAMPLES = 262144
STAGE0_TX_BUDGET_S = 2.0
STAGE0_TX_GUARD_S = 0.05
STAGE0_WINDOW_NAMES = ('OFF_PRE', 'TONE', 'OFF_POST')
STAGE0_BAND_HZ = 20000.0
STAGE0_PEAK_TOLERANCE_HZ = 2000.0
STAGE0_MIN_PEAK_TO_MEDIAN_DB = 20.0
STAGE0_MIN_ON_OFF_DB = 20.0
STAGE0_NEAR_FULL_SCALE = 0.99
STAGE0_DIGITAL_FULL_SCALE_CODE = 2047
#: The 2 s budget bounds the modulated TONE graph only.  The TX LO is enabled
#: from the pre-phase until the post-phase readback, so it is on in all three
#: windows even though only TONE transmits a useful signal.
STAGE0_TX_BUDGET_SCOPE = 'modulated TONE graph only'
#: Stage 0 never writes the RX rf_bandwidth: the real GNU Radio 3.10 iio source
#: exposes no set_bandwidth (only the sink does), so the RX keeps the device's
#: current value and the report states that instead of claiming a 1.5 MHz write.
STAGE0_RX_BANDWIDTH_POLICY = ('not set by Stage 0: the RX keeps the device '
                              'current rf_bandwidth')
#: task-6 (user-approved 2026-10-19): the ON/OFF gate compares the TONE peak
#: bin and its +-4 Hann-mainlobe neighbours (9 bins) against the *same bin
#: indices* in both OFF windows; the threshold stays STAGE0_MIN_ON_OFF_DB.
#: The old 20 kHz (546-bin) band mean is kept as information only, because a
#: CW tone in ~9 bins carries a fixed 10log10(546/9)=17.83 dB integration
#: penalty under that metric (see the task-4 attribution evidence).
STAGE0_ON_OFF_METRIC = 'matched-narrowband-mainlobe-9-bin'
STAGE0_ON_OFF_HALF_BINS = 4

BPSK_SYMBOL_RATE_HZ = 3000000
BPSK_SPs = 20
BPSK_PLUTO_FS_HZ = 6000000
BPSK_PLUTO_SPs = 2
BPSK_SEED = 1
BPSK_ALIGNMENT_BITS = 63

SMOKE_WINDOWS = 4
MAX_SESSIONS = 3
MAX_WINDOWS_TOTAL = 12
MAX_BOARD_INITS = 3
MAX_TX_STARTS_TOTAL = 6


class PlanError(ValueError):
    """A configuration, budget or parameter value this tool refuses."""


def _require(condition, message):
    if not condition:
        raise PlanError(message)


def load_config(path) -> Dict:
    path = Path(path)
    with path.open('r', encoding='utf-8') as handle:
        cfg = json.load(handle)
    if not isinstance(cfg, dict):
        raise PlanError('configuration must be a JSON object')
    return cfg


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_config(cfg: Dict) -> None:
    """Refuse anything that would leave the reviewed smoke domain.

    The checks are intentionally narrow: this is a diagnostic, not a general
    frequency/sample-rate tool.  A caller who wants another domain must create a
    new, reviewed configuration rather than passing it here.
    """
    _require(isinstance(cfg, dict), 'configuration must be an object')
    _require(cfg.get('schema') == SCHEMA, 'schema must be ' + SCHEMA)
    board = cfg.get('board')
    pluto = cfg.get('pluto')
    bpsk = cfg.get('bpsk')
    gates = cfg.get('gates')
    _require(isinstance(board, dict), 'board section missing')
    _require(isinstance(pluto, dict), 'pluto section missing')
    _require(isinstance(bpsk, dict), 'bpsk section missing')
    _require(isinstance(gates, dict), 'gates section missing')

    # --- board: the frozen 46 dB gate and the 60 MSPS data path.
    _require(board.get('lo_hz') == BOARD_LO_HZ,
             'board lo_hz must be the diagnostic domain %d Hz' % BOARD_LO_HZ)
    _require(board.get('sample_rate_hz') == BOARD_FS_HZ,
             'board sample_rate_hz must remain 60000000 (the PL/FIR chain is fixed)')
    _require(board.get('rx_bandwidth_hz') == BOARD_RX_BW_HZ,
             'board rx_bandwidth_hz must be 30000000')
    _require(board.get('tx_bandwidth_hz') == BOARD_TX_BW_HZ,
             'board tx_bandwidth_hz must be 30000000')
    _require(board.get('attenuation_mdb') == BOARD_ATTENUATION_MDB,
             'board attenuation_mdb must stay at the frozen 46000; got %r'
             % (board.get('attenuation_mdb'),))
    _require(board.get('phy_mode_1r1t') == 0, 'board phy_mode_1r1t must be 0')
    _require(board.get('idelay_en') == 1, 'board idelay_en must be 1')
    _require(board.get('idelay_tap') == 29, 'board idelay_tap must be 29')
    amp = board.get('amplitude')
    _require(isinstance(amp, int) and not isinstance(amp, bool) and
             FROZEN_AMPLITUDE_CODE <= amp <= 2047,
             'board amplitude must be an integer >= the frozen code 60 and <= 2047')
    _require(board.get('bram_words') == BOARD_BRAM_WORDS,
             'board bram_words must be 4096')
    _require(board.get('max_samples') == BOARD_BRAM_WORDS,
             'board max_samples must be 4096')
    for key in ('tone_bins', 'tone_nominal_hz'):
        values = board.get(key)
        _require(isinstance(values, list) and len(values) == 2,
                 'board %s must contain exactly two entries' % key)
    _require(board['tone_bins'] == [7, 14],
             'board tone_bins must be the two periodic bins [7, 14]')
    _require(board['tone_nominal_hz'] == [100000, 200000],
             'board tone_nominal_hz must be [100000, 200000]')

    # --- Pluto: bounded smoke settings; no firmware change is implied.
    _require(isinstance(pluto.get('serial'), str) and
             8 <= len(pluto['serial']) <= 64 and
             all(c in '0123456789abcdefABCDEF' for c in pluto['serial']),
             'pluto serial must be the immutable hex serial (8..64 characters)')
    _require(pluto.get('center_hz') == BOARD_LO_HZ,
             'pluto center_hz must match the 433900000 diagnostic LO')
    _require(pluto.get('sample_rate') == PLUTO_FS_HZ,
             'pluto sample_rate must be 2400000 for the tone smoke')
    _require(pluto.get('bandwidth_hz') == PLUTO_BW_HZ,
             'pluto bandwidth_hz must be 1500000')
    tones = pluto.get('tone_hz')
    _require(isinstance(tones, list) and tones == [100000, 200000],
             'pluto tone_hz must be [+100000, +200000]')
    pamp = pluto.get('amplitude')
    _require(_number(pamp) and 0 < pamp <= PLUTO_MAX_AMPLITUDE,
             'pluto amplitude must be >0 and <=0.25')
    atten = pluto.get('tx_attenuation_db')
    _require(_number(atten) and PLUTO_MIN_TX_ATTENUATION_DB <= atten <= 89.75,
             'pluto tx_attenuation_db must be >=60 and <=89.75; got %r' % (atten,))
    gain = pluto.get('rx_gain_db')
    _require(_number(gain) and 0 <= gain <= 10,
             'pluto rx_gain_db must be a fixed low manual gain 0..10')
    _require(pluto.get('gain_mode') == 'manual', 'pluto gain_mode must be manual')
    dur = pluto.get('duration_s')
    _require(_number(dur) and 0 < dur <= 5, 'pluto duration_s must be 0..5')
    _require(pluto.get('capture_samples') == PLUTO_CAPTURE_SAMPLES,
             'pluto capture_samples must be 262144')
    _require(pluto.get('tx_attenuation_ceiling_db') == 89.75,
             'pluto tx_attenuation_ceiling_db must be 89.75')

    # --- BPSK contract (preparation only; not enabled by the tone smoke).
    _require(bpsk.get('symbol_rate_hz') == BPSK_SYMBOL_RATE_HZ,
             'bpsk symbol_rate_hz must be 3000000 (the board receiver domain)')
    _require(bpsk.get('board_sample_rate_hz') == BOARD_FS_HZ,
             'bpsk board_sample_rate_hz must be 60000000')
    _require(bpsk.get('board_sps') == BPSK_SPs,
             'bpsk board_sps must be 20')
    _require(bpsk.get('pluto_sample_rate_hz') == BPSK_PLUTO_FS_HZ,
             'bpsk pluto_sample_rate_hz must be 6000000 for 3 MSym/s at sps=2')
    _require(bpsk.get('pluto_sps') == BPSK_PLUTO_SPs,
             'bpsk pluto_sps must be 2')
    _require(bpsk.get('shaping') == 'RECT',
             'bpsk shaping must be RECT; the board receiver supports RECT only')
    _require(bpsk.get('differential_encoding') is False,
             'bpsk differential_encoding must be false; neither end implements it')
    _require(bpsk.get('prbs7_seed') == BPSK_SEED,
             'bpsk prbs7_seed must be 1 for the first attempt')
    _require(bpsk.get('alignment_bits') == BPSK_ALIGNMENT_BITS,
             'bpsk alignment_bits must be 63')
    _require(bpsk.get('max_cfo_hz') == 100000,
             'bpsk max_cfo_hz must be the receiver limit 100000')
    _require(bpsk.get('usable_without_board_change') is True,
             'bpsk must declare that it needs no board receiver change (or say false)')

    # --- gates and budget ceilings.
    _require(gates.get('require_hardware_verified') is True,
             'gates.require_hardware_verified must be true')
    _require(gates.get('require_pre_tx_readback') is True,
             'gates.require_pre_tx_readback must be true')
    _require(gates.get('require_post_tx_readback') is True,
             'gates.require_post_tx_readback must be true')
    _require(gates.get('require_board_tx_off_in_b') is True,
             'gates.require_board_tx_off_in_b must be true')
    _require(gates.get('require_no_headroom_ops') is True,
             'gates.require_no_headroom_ops must be true')
    _require(gates.get('require_path_confirmed') is True,
             'gates.require_path_confirmed must be true')
    _require(gates.get('stop_on_clipping') is True,
             'gates.stop_on_clipping must be true')
    _require(gates.get('stop_on_any_readback_mismatch') is True,
             'gates.stop_on_any_readback_mismatch must be true')
    budget_cfg = cfg.get('budget')
    _require(isinstance(budget_cfg, dict), 'budget section missing')
    _require(budget_cfg.get('smoke_windows') == SMOKE_WINDOWS,
             'budget.smoke_windows must be 4')
    _require(budget_cfg.get('max_sessions') == MAX_SESSIONS,
             'budget.max_sessions must be 3')
    _require(budget_cfg.get('max_windows_total') == MAX_WINDOWS_TOTAL,
             'budget.max_windows_total must be 12')
    _require(budget_cfg.get('max_board_inits') == MAX_BOARD_INITS,
             'budget.max_board_inits must be 3')
    _require(budget_cfg.get('max_tx_starts_total') == MAX_TX_STARTS_TOTAL,
             'budget.max_tx_starts_total must be 6')
    _require(budget_cfg.get('includes_smoke_session_in_extension') is True,
             'budget.includes_smoke_session_in_extension must be true')


def nearest_board_tone_bin(sample_rate_hz: int, samples: int, nominal_hz: float) -> Tuple[int, float]:
    """Return the periodic bin nearest the nominal offset and its exact frequency."""
    if sample_rate_hz <= 0 or samples <= 0:
        raise PlanError('sample_rate/samples must be positive')
    k = int(round(float(nominal_hz) * samples / sample_rate_hz))
    if abs(k) >= samples // 2:
        raise PlanError('nominal tone is outside Nyquist')
    return k, k * float(sample_rate_hz) / float(samples)


def _quantize_complex(z: np.ndarray, limit: int = 2047) -> np.ndarray:
    iq = np.empty((len(z), 2), dtype='<i2')
    iq[:, 0] = np.rint(np.real(z)).astype(np.int32)
    iq[:, 1] = np.rint(np.imag(z)).astype(np.int32)
    peak = int(np.max(np.abs(iq))) if len(iq) else 0
    if peak > limit:
        raise PlanError('generated IQ exceeds signed 12-bit range: %d' % peak)
    return iq


def tone_payload(sample_rate_hz: int, samples: int, bin_index: int,
                 amplitude: int) -> Tuple[bytes, Dict]:
    """One periodic complex tone for the board TX BRAM.

    wrap=true replay is periodic only when the tone uses an integer number of
    cycles in samples; that is exactly what bin_index selects.
    """
    if not isinstance(samples, int) or not 1 <= samples <= BOARD_BRAM_WORDS:
        raise PlanError('samples must be 1..4096')
    if not isinstance(bin_index, int) or abs(bin_index) >= samples // 2:
        raise PlanError('bin_index outside the periodic frame')
    if not isinstance(amplitude, int) or not 1 <= amplitude <= 2047:
        raise PlanError('amplitude must be an integer 1..2047')
    t = np.arange(samples, dtype=float)
    z = float(amplitude) * np.exp(2j * np.pi * bin_index * t / samples)
    iq = _quantize_complex(z)
    offset_hz = bin_index * float(sample_rate_hz) / float(samples)
    meta = {
        'samples': samples,
        'bytes': samples * 4,
        'format': 's16_iq_le',
        'direction': 'tx',
        'wrap': True,
        'bin_index': bin_index,
        'offset_hz': offset_hz,
        'peak_code': int(amplitude),
        'periodic': True,
    }
    return iq.tobytes(), meta


def prbs7_bits(seed: int, count: int) -> List[int]:
    """Same PRBS7 as pynq_overlay/sw/rf_iq_signal.py and the board receiver.

    RTL bit 6 is the output; feedback is bit6 XOR bit5.  The first 63 bits are
    the receiver's alignment window, so the TX and RX must use this exact order.
    """
    if not isinstance(seed, int) or not 1 <= seed <= 127:
        raise PlanError('PRBS7 seed must be an integer 1..127')
    if not isinstance(count, int) or count < 0:
        raise PlanError('PRBS7 count must be a non-negative integer')
    state = seed
    out = []
    for _ in range(count):
        out.append((state >> 6) & 1)
        state = ((state << 1) & 0x7F) | (((state >> 6) & 1) ^ ((state >> 5) & 1))
    return out


def bpsk_rect_payload(seed: int, sample_rate_hz: int, symbol_rate_hz: int,
                      amplitude: float, symbols: int = 127) -> Tuple[bytes, Dict]:
    """Absolute (non-differential) BPSK/RECT, peak-normalised to amplitude.

    amplitude is a fraction of the signed-12-bit full scale (0..1).  The board
    receiver resolves absolute polarity against its PRBS7 reference, so no
    differential encoder is applied on either side.
    """
    if not isinstance(sample_rate_hz, int) or sample_rate_hz <= 0:
        raise PlanError('sample_rate_hz must be a positive integer')
    if not isinstance(symbol_rate_hz, int) or symbol_rate_hz <= 0:
        raise PlanError('symbol_rate_hz must be a positive integer')
    if sample_rate_hz % symbol_rate_hz != 0:
        raise PlanError('sample_rate_hz must be an integer multiple of symbol_rate_hz')
    sps = sample_rate_hz // symbol_rate_hz
    if not 1 <= sps <= 32:
        raise PlanError('samples per symbol must be 1..32')
    if not _number(amplitude) or not 0 < amplitude <= 1:
        raise PlanError('amplitude must be a fraction in (0, 1]')
    bits = prbs7_bits(seed, symbols)
    values = np.array([1.0 - 2.0 * b for b in bits], dtype=float)
    code = int(round(amplitude * 2047.0))
    shaped = np.repeat(values * code, sps)
    z = shaped.astype(float) + 0j
    iq = _quantize_complex(z)
    meta = {
        'samples': len(iq),
        'bytes': len(iq) * 4,
        'format': 's16_iq_le',
        'direction': 'tx',
        'wrap': True,
        'mode': 'BPSK',
        'shaping': 'RECT',
        'differential_encoding': False,
        'prbs7_seed': seed,
        'symbols': symbols,
        'symbol_rate_hz': symbol_rate_hz,
        'sample_rate_hz': sample_rate_hz,
        'samples_per_symbol': sps,
        'amplitude_fraction': float(amplitude),
        'peak_code': code,
    }
    return iq.tobytes(), meta


def clipping_evidence(iq, near_full_scale: Optional[float] = None) -> Dict:
    """Computed digital-clipping evidence from the samples (Stage 0 formula).

    Shared by the Stage 0 three-window analysis and by the tone analysis used
    for the board-integration directions, so both report the identical proof:
    abs_max_iq, the near-full-scale fraction (0.99 by default), the hard-clip
    fraction, the remaining headroom and the boolean clipped flag.  Nothing is
    inferred from a log string.
    """
    z = np.asarray(iq, dtype=np.complex128).ravel()
    near = (STAGE0_NEAR_FULL_SCALE if near_full_scale is None
            else float(near_full_scale))
    if len(z) == 0:
        return {'abs_max_iq': 0.0, 'near_full_scale': float(near),
                'full_scale_fraction': 0.0, 'hard_clip_fraction': 0.0,
                'full_scale_headroom_db': None, 'clipped': False}
    magnitude = np.maximum(np.abs(z.real), np.abs(z.imag))
    abs_max = float(magnitude.max())
    return {
        'abs_max_iq': abs_max,
        'near_full_scale': float(near),
        'full_scale_fraction': float(np.mean(magnitude >= near)),
        'hard_clip_fraction': float(np.mean(magnitude >= 1.0)),
        'full_scale_headroom_db': (None if abs_max <= 0.0
                                   else -20.0 * math.log10(abs_max)),
        'clipped': bool(abs_max >= 1.0),
    }


def analyse_tone(iq, sample_rate_hz: float, expected_offset_hz: float,
                 tolerance_hz: Optional[float] = None) -> Dict:
    """Offline tone analysis: peak bin, offset, ratio and clipping evidence."""
    z = np.asarray(iq, dtype=np.complex128)
    if len(z) < 4096:
        raise PlanError('at least 4096 samples are required for tone analysis')
    y = z[-min(len(z), 65536):]
    spectrum = np.abs(np.fft.fftshift(np.fft.fft((y - y.mean()) * np.hanning(len(y))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / sample_rate_hz))
    index = int(np.argmax(spectrum))
    peak = float(freqs[index])
    median = float(np.median(spectrum))
    ratio = 10.0 * math.log10((float(spectrum[index]) + 1e-30) /
                              (median + 1e-30))
    if tolerance_hz is None:
        tolerance_hz = 2.0 * float(sample_rate_hz) / len(y)
    return {
        'samples': int(len(z)),
        'peak_offset_hz': peak,
        'expected_offset_hz': float(expected_offset_hz),
        'delta_hz': peak - float(expected_offset_hz),
        'tolerance_hz': float(tolerance_hz),
        'peak_ok': abs(peak - float(expected_offset_hz)) <= float(tolerance_hz),
        'peak_to_median_db': ratio,
        **clipping_evidence(z),
    }


def _get(mapping: Dict, *names, default=None):
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def check_configure_readback(readback: Dict, cfg: Dict) -> List[str]:
    """Gate the rf.init/rf.configure response against the domain.

    The daemon's response is taken from the part (readback_result), not from
    the request; the gate therefore refuses a zero/default value that merely
    mirrors what was asked for.
    """
    if not isinstance(readback, dict):
        return ['readback is not an object']
    board = cfg['board']
    problems = []
    expected = [
        ('lo_hz', board['lo_hz'], 'lo_hz'),
        ('sample_rate_hz', board['sample_rate_hz'], 'sample_rate_hz'),
        ('rx_bandwidth_hz', board['rx_bandwidth_hz'], 'rx_bandwidth_hz'),
        ('tx_bandwidth_hz', board['tx_bandwidth_hz'], 'tx_bandwidth_hz'),
        ('attenuation_mb', board['attenuation_mdb'], 'attenuation_mdB'),
        ('phy_mode_1r1t', board['phy_mode_1r1t'], 'phy_mode_1r1t'),
    ]
    for key, want, label in expected:
        got = _get(readback, key, 'attenuation_mdB', 'attenuation_mdb')
        if got != want:
            problems.append('%s readback %r != required %r' % (label, got, want))
    if _get(readback, 'hardware_verified') is not True:
        problems.append('hardware_verified is not true')
    product = _get(readback, 'product_id')
    if not isinstance(product, int) or (product & 0xF8) != 0x08:
        problems.append('product_id %r does not match AD936x mask 0xF8 == 0x08' % (product,))
    return problems


def check_safe_stop(status: Dict, cfg: Dict) -> List[str]:
    """Gate the post-TX state: ceiling attenuation, TX source off, RX not armed."""
    if not isinstance(status, dict):
        return ['stop state is not an object']
    problems = []
    atten = _get(status, 'attenuation_mdb', 'attenuation_mdB', 'attenuation_mb')
    if atten != BOARD_SAFE_ATTENUATION_MDB:
        problems.append('post-stop attenuation %r != ceiling %d'
                        % (atten, BOARD_SAFE_ATTENUATION_MDB))
    if _get(status, 'hardware_verified') is not True:
        problems.append('post-stop hardware_verified is not true')
    if _get(status, 'io_ok') is not True:
        problems.append('post-stop io_ok is not true')
    if _get(status, 'tx_source_selected') is not False:
        problems.append('post-stop tx source is not confirmed off')
    if _get(status, 'rx_armed') is not False:
        problems.append('post-stop RX is not confirmed disarmed')
    return problems


def _per_session(direction: str) -> Dict:
    if direction == 'A':
        return dict(board_inits=1, board_configures=3, board_tx_loads=2,
                    board_tx_starts=2, board_tx_seconds=4.0,
                    board_rx_frames=0, pluto_rx_windows=4,
                    pluto_rx_seconds=8.0, pluto_tx_starts=0,
                    pluto_tx_seconds=0.0, pluto_control_windows=4)
    if direction == 'B':
        return dict(board_inits=1, board_configures=1, board_tx_loads=0,
                    board_tx_starts=0, board_tx_seconds=0.0,
                    board_rx_frames=4, pluto_rx_windows=0,
                    pluto_rx_seconds=0.0, pluto_tx_starts=2,
                    pluto_tx_seconds=4.0, pluto_control_windows=4)
    raise PlanError('direction must be A or B')


def budget(direction: str, sessions: int = 1, windows_per_session: int = SMOKE_WINDOWS,
           extended: bool = False) -> Dict:
    """Count every resource this tool will consume, once and explicitly.

    A 3-session extension CONTAINS the 4-window smoke as its first session.
    The counters are separate for board initialisations, board frames, SDR
    windows and TX starts, so a 12-window plan cannot be reported as 12 board
    frames by accident.
    """
    if windows_per_session != SMOKE_WINDOWS:
        raise PlanError('a session is exactly 4 windows; refusing %r'
                        % (windows_per_session,))
    if sessions not in (1, 3):
        raise PlanError('sessions must be 1 (smoke) or 3 (extension)')
    if sessions == 3 and not extended:
        raise PlanError('3 sessions/12 windows requires --extended')
    per = _per_session(direction)
    out = dict(direction=direction, sessions=sessions,
               windows_per_session=windows_per_session,
               windows_total=sessions * windows_per_session,
               includes_smoke_session=(sessions == 3),
               added_sessions=(2 if sessions == 3 else 0),
               added_windows=(8 if sessions == 3 else 0))
    for key, value in per.items():
        out[key] = value * sessions
    out['tx_starts_total'] = out['board_tx_starts'] + out['pluto_tx_starts']
    out['rf_on_seconds_total'] = out['board_tx_seconds'] + out['pluto_tx_seconds']
    if out['windows_total'] > MAX_WINDOWS_TOTAL:
        raise PlanError('window budget exceeded')
    if out['board_inits'] > MAX_BOARD_INITS:
        raise PlanError('board initialization budget exceeded')
    if out['tx_starts_total'] > MAX_TX_STARTS_TOTAL:
        raise PlanError('TX start budget exceeded')
    return out


def build_windows(direction: str, cfg: Dict) -> List[Dict]:
    """OFF -> tone1 -> tone2 -> OFF, with explicit per-window roles."""
    board = cfg['board']
    pluto = cfg['pluto']
    tone1 = nearest_board_tone_bin(board['sample_rate_hz'], board['bram_words'],
                                   board['tone_nominal_hz'][0])[1]
    tone2 = nearest_board_tone_bin(board['sample_rate_hz'], board['bram_words'],
                                   board['tone_nominal_hz'][1])[1]
    if direction == 'A':
        return [
            dict(index=0, name='OFF_PRE', board_tx=False, board_tone_hz=None,
                 board_role='tx_off', pluto_role='rx', seconds=pluto['duration_s'],
                 note='Pluto RX sees the board at TX off; no board transmit.'),
            dict(index=1, name='TONE1', board_tx=True, board_tone_hz=tone1,
                 board_role='tx_on', pluto_role='rx', seconds=pluto['duration_s'],
                 note='board TX1 tone at periodic bin 7; Pluto RX manual gain.'),
            dict(index=2, name='TONE2', board_tx=True, board_tone_hz=tone2,
                 board_role='tx_on', pluto_role='rx', seconds=pluto['duration_s'],
                 note='board TX1 tone at periodic bin 14; Pluto RX manual gain.'),
            dict(index=3, name='OFF_POST', board_tx=False, board_tone_hz=None,
                 board_role='safe_stop', pluto_role='rx', seconds=pluto['duration_s'],
                 note='Post-window OFF; board TX already stopped and at ceiling.'),
        ]
    if direction == 'B':
        return [
            dict(index=0, name='OFF_PRE', board_tx=False, board_tone_hz=None,
                 board_role='rx', pluto_role='tx_off', seconds=0.0,
                 note='Board RX-only frame with Pluto TX off.'),
            dict(index=1, name='TONE1', board_tx=False, board_tone_hz=pluto['tone_hz'][0],
                 board_role='rx', pluto_role='tx_on', seconds=pluto['duration_s'],
                 note='Pluto TX +100 kHz; board RX-only; board TX stays off.'),
            dict(index=2, name='TONE2', board_tx=False, board_tone_hz=pluto['tone_hz'][1],
                 board_role='rx', pluto_role='tx_on', seconds=pluto['duration_s'],
                 note='Pluto TX +200 kHz; board RX-only; board TX stays off.'),
            dict(index=3, name='OFF_POST', board_tx=False, board_tone_hz=None,
                 board_role='rx', pluto_role='tx_off', seconds=0.0,
                 note='Post-window OFF; Pluto TX already stopped.'),
        ]
    raise PlanError('direction must be A or B')


def level_budget(cfg: Dict) -> Dict:
    """Uncalibrated bound, not a measurement.

    Reference point: the ADI FMCOMMS3/AD9361 full-scale CW output is about
    +8 dBm at a favourable operating point; this planner uses +10 dBm as a
    conservative upper bound.  Cable/connector loss is assumed 0 dB, so every
    margin below is a lower bound.  No dBm figure here is measured.
    """
    board = cfg['board']
    pluto = cfg['pluto']
    tx_max_dbm = 10.0
    rx_max_dbm = 2.5
    board_amp_db = 20.0 * math.log10(float(board['amplitude']) / 2047.0)
    pluto_amp_db = 20.0 * math.log10(float(pluto['amplitude']))
    a_conservative = tx_max_dbm - board['attenuation_mdb'] / 1000.0
    a_actual = a_conservative + board_amp_db
    b_conservative = tx_max_dbm - float(pluto['tx_attenuation_db'])
    b_actual = b_conservative + pluto_amp_db
    return {
        'scope': 'calculated bounds from referenced full-scale output, not measured dBm',
        'reference_tx_max_dbm': tx_max_dbm,
        'rx_absolute_max_dbm': rx_max_dbm,
        'cable_loss_assumed_db': 0.0,
        'board_tx_to_pluto_rx': {
            'hardware_attenuation_db': board['attenuation_mdb'] / 1000.0,
            'digital_backoff_db': board_amp_db,
            'worst_case_input_dbm': a_conservative,
            'configured_input_dbm': a_actual,
            'margin_worst_case_db': rx_max_dbm - a_conservative,
            'margin_configured_db': rx_max_dbm - a_actual,
        },
        'pluto_tx_to_board_rx': {
            'hardware_attenuation_db': float(pluto['tx_attenuation_db']),
            'digital_backoff_db': pluto_amp_db,
            'worst_case_input_dbm': b_conservative,
            'configured_input_dbm': b_actual,
            'margin_worst_case_db': rx_max_dbm - b_conservative,
            'margin_configured_db': rx_max_dbm - b_actual,
        },
        'pluto_self_loopback': {
            'hardware_attenuation_db': float(pluto['tx_attenuation_db']),
            'digital_backoff_db': pluto_amp_db,
            'worst_case_input_dbm': b_conservative,
            'configured_input_dbm': b_actual,
            'margin_worst_case_db': rx_max_dbm - b_conservative,
            'margin_configured_db': rx_max_dbm - b_actual,
        },
    }


def capability_matrix() -> Dict:
    return {
        'board_lo_433_9_mhz': {
            'allowed': True,
            'why': 'rf.init/rf.configure accept any positive lo_hz; only headroom.* pins 80 MHz',
            'evidence': ['rf_overlay/sw/board_app/rf_daemon.c:1142-1148',
                         'rf_overlay/sw/board_app/rf_core.c:855-857',
                         'rf_overlay/sw/board_app/rf_core.c:975-979',
                         'rf_overlay/sw/board_app/rf_headroom.c:14',
                         'rf_overlay/sw/board_app/rf_daemon.c:2082'],
        },
        'board_tone_100_200_khz': {
            'allowed': True,
            'why': 'tx.load accepts arbitrary 1..4096 IQ; exact periodic tones are bins 7/14',
            'actual_hz': [102539.0625, 205078.125],
            'evidence': ['rf_overlay/sw/board_app/rf_daemon.c:1291-1319',
                         'rf_overlay/sw/board_app/platform_rf.h:77'],
        },
        'board_sample_rate_2_4_msps': {
            'allowed': False,
            'why': 'the PL/FIR chain and readback are fixed for 60 MSPS; do not change it in this diagnostic',
            'evidence': ['rf_overlay/sw/board_app/rf_core.c:900-901',
                         'rf_overlay/sw/board_app/rf_core.c:982-983'],
        },
        'headroom_ops_at_433_9': {
            'allowed': False,
            'why': 'headroom.begin pins 80 MHz/60 MSPS/30 MHz/46 dB; this diagnostic must not use headroom.*',
            'evidence': ['rf_overlay/sw/board_app/rf_headroom.c:14',
                         'rf_overlay/sw/board_app/rf_daemon.c:2082'],
        },
        'original_s2_80_mhz_with_pluto': {
            'allowed': False,
            'why': 'standard Pluto covers 325-3800 MHz; 80 MHz is outside and no firmware change is authorized',
            'evidence': ['sdr_testbench/README.md',
                         'skills/pluto-gnuradio-testbench/SKILL.md'],
        },
        'bpsk_3msym_from_pluto_template_2_4msps': {
            'allowed': False,
            'why': '2.4 MSPS cannot carry 3 MSym/s; the maintained radio_test validate() caps at 3 MSPS',
            'evidence': ['sdr_testbench/config/pluto.json',
                         'sdr_testbench/radio_test.py:20-33',
                         'pynq_overlay/sw/rf_bpsk_receiver.py:96-99'],
        },
        'bpsk_3msym_from_pluto_at_6msps': {
            'allowed': 'unverified',
            'why': '6 MSPS gives sps=2 and no board receiver change, but USB2 throughput and TX FIR are not qualified',
            'evidence': ['pynq_overlay/sw/rf_bpsk_receiver.py:3',
                         'pynq_overlay/sw/rf_bpsk_receiver.py:17-18',
                         'pynq_overlay/sw/rf_bpsk_receiver.py:96-99',
                         'pynq_overlay/sw/rf_iq_signal.py:50-61',
                         'pynq_overlay/sw/rf_iq_signal.py:131-132'],
        },
        'differential_encoding': {
            'allowed': False,
            'why': 'neither the board receiver nor the frozen generator implements it; keep absolute BPSK with polarity search',
            'evidence': ['pynq_overlay/sw/rf_bpsk_receiver.py:65-70',
                         'pynq_overlay/sw/rf_bpsk_receiver.py:155-175',
                         'pynq_overlay/sw/rf_iq_signal.py:88-89'],
        },
        'rrc_shaping': {
            'allowed': False,
            'why': 'the board receiver only accepts shaping=RECT',
            'evidence': ['pynq_overlay/sw/rf_bpsk_receiver.py:96-99'],
        },
    }


def _plan_gates(direction: str) -> Dict:
    """Per-direction gate text; direction B is gated by the captured board IQ."""
    gates = {
        'preflight': ('Pluto TX safe-off write + readback (writes hardwaregain=-89.75 '
                      'and powerdown=1; not read-only); with --token-file it also gates '
                      'the board idle state: online, not stale, io_ok, TX source off, '
                      'RX disarmed'),
        'pre_tx_readback': ('rf.configure response attenuation_mdB == 46000 and '
                            'hardware_verified true'),
        'stop_on': ['any readback mismatch', 'CLIPPED/INVALID_IQ',
                    'budget exceeded', 'CLEANUP_FAILED'],
    }
    if direction == 'A':
        gates['post_tx_readback'] = ('rf.configure(89750) response and PL status: '
                                     'attenuation 89750, TX off, RX disarmed')
        gates['stop_on'].append('peak not following')
        return gates
    gates['board_iq_criteria'] = (
        '4 board IQ frames; each ON window vs the OFF windows in the SAME FFT bin '
        '>= 10 dB (common LO offset cancels); two ON peaks 7 bins '
        '(102539.0625 Hz) apart +/-1 bin; no clipped frame; board TX source false '
        'before/after every window and at the final poll; Pluto final safe-off '
        'readback -89.75 dB / powerdown=1')
    gates['pluto_tx_lifecycle'] = (
        'on any board-capture error, communicate timeout, ready timeout or '
        'non-zero child exit: stop the Pluto TX child (terminate -> kill), then '
        'independent libiio safe-off readback; unconfirmed => UNKNOWN/'
        'cleanup_required and the session stops (no next TONE)')
    gates['budget'] = ('1 session / 1 board init / 1 configure / 4 captures / '
                       '<=2 Pluto TX starts / <=4.0 s cumulative Pluto TX / '
                       '0 board TX starts')
    gates['stop_on'] += ['Pluto TX child timeout/nonzero exit',
                         'safe-off readback unconfirmed (UNKNOWN)']
    return gates


def build_plan(direction: str, cfg: Dict, sessions: int = 1,
               extended: bool = False) -> Dict:
    validate_config(cfg)
    windows = build_windows(direction, cfg)
    b = budget(direction, sessions=sessions, extended=extended)
    wiring = {
        'A': 'board TX1 (AD-FMCOMMS3-EBZ) -> 50 ohm coax -> Pluto RX; Pluto TX and scope disconnected',
        'B': 'Pluto TX -> 50 ohm coax -> board RX1; board TX1 open; board TX software off and verified',
    }[direction]
    return {
        'schema': SCHEMA,
        'mode': 'dry-run',
        'hardware_accessed': False,
        'direction': direction,
        'wiring': wiring,
        'windows': windows,
        'budget': b,
        'level_budget': level_budget(cfg),
        'capability': capability_matrix(),
        'gates': _plan_gates(direction),
        'commands': {
            'help': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --help',
            'self_test': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --self-test',
            'dry_run_a': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --dry-run --direction A',
            'dry_run_b': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --dry-run --direction B',
            'execute_a': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --execute --direction A --path-confirmed "<actual wiring>" --token-file <local token> --board-port 5000',
            'execute_b': r'& "<LOCAL_PATH>" -X utf8 -B sdr_testbench\board_integration\board_integration.py --execute --direction B --path-confirmed "<actual wiring>" --token-file <local token> --board-port 5000',
        },
        'bpsk': {
            'status': 'preparation only; not part of the tone smoke',
            'verdict': 'feasible without board receiver change ONLY at Pluto 6 MSPS, sps=2; USB2 transport unverified',
            'not_feasible': 'the 2.4 MSPS template cannot carry 3 MSym/s; a lower symbol rate would require a board receiver change + review + deploy',
        },
    }


def validate_stage0_config(cfg: Dict) -> None:
    """Refuse any Stage 0 parameter that is not the reviewed self-loop set."""
    _require(isinstance(cfg, dict), 'stage0 configuration must be an object')
    _require(cfg.get('schema') == STAGE0_SCHEMA, 'schema must be ' + STAGE0_SCHEMA)
    serial = cfg.get('serial')
    _require(isinstance(serial, str) and 8 <= len(serial) <= 64 and
             all(c in '0123456789abcdefABCDEF' for c in serial),
             'stage0 serial must be the immutable hex serial (8..64 characters)')
    _require(cfg.get('center_hz') == STAGE0_LO_HZ,
             'stage0 center_hz must be 433900000')
    _require(cfg.get('sample_rate') == STAGE0_FS_HZ,
             'stage0 sample_rate must be 2400000')
    _require(cfg.get('bandwidth_hz') == STAGE0_BANDWIDTH_HZ,
             'stage0 bandwidth_hz must be 1500000')
    _require(_number(cfg.get('tone_hz')) and float(cfg['tone_hz']) == STAGE0_TONE_HZ,
             'stage0 tone_hz must be exactly +100000')
    amplify = cfg.get('amplitude')
    _require(_number(amplify) and float(amplify) == STAGE0_AMPLITUDE,
             'stage0 amplitude must be exactly 0.1')
    attenuation = cfg.get('tx_attenuation_db')
    _require(_number(attenuation) and float(attenuation) == STAGE0_TX_ATTENUATION_DB,
             'stage0 tx_attenuation_db must be exactly 60 dB')
    _require(cfg.get('tx_attenuation_ceiling_db') == STAGE0_CEILING_DB,
             'stage0 tx_attenuation_ceiling_db must be 89.75')
    gain = cfg.get('rx_gain_db')
    _require(_number(gain) and float(gain) == STAGE0_RX_GAIN_DB,
             'stage0 rx_gain_db must be 0 dB')
    _require(cfg.get('gain_mode') == 'manual', 'stage0 gain_mode must be manual')
    duration = cfg.get('duration_s')
    _require(_number(duration) and 0 < float(duration) <= STAGE0_TX_BUDGET_S,
             'stage0 duration_s must be >0 and <= the 2 s TX budget')
    _require(cfg.get('capture_samples') == STAGE0_CAPTURE_SAMPLES,
             'stage0 capture_samples must be 262144')
    _require(_number(cfg.get('tx_budget_s')) and
             float(cfg['tx_budget_s']) == STAGE0_TX_BUDGET_S,
             'stage0 tx_budget_s must be 2.0')
    guard = cfg.get('tx_guard_s')
    _require(_number(guard) and 0 <= float(guard) < STAGE0_TX_BUDGET_S,
             'stage0 tx_guard_s must be >=0 and < the 2 s budget')
    windows = cfg.get('windows')
    _require(isinstance(windows, list) and
             tuple(windows) == STAGE0_WINDOW_NAMES,
             'stage0 windows must be OFF_PRE, TONE, OFF_POST in that order')


def stage0_tx_digital_evidence(cfg: Dict) -> Dict:
    """Calculated TX-side clipping evidence; never a text scan."""
    amplitude = float(cfg['amplitude'])
    peak_code = int(round(amplitude * STAGE0_DIGITAL_FULL_SCALE_CODE))
    return {
        'amplitude': amplitude,
        'digital_full_scale_code': STAGE0_DIGITAL_FULL_SCALE_CODE,
        'peak_code': peak_code,
        'backoff_db': 20.0 * math.log10(amplitude) if amplitude > 0 else None,
        'clipping_possible': bool(amplitude > 1.0),
        'why': ('the GR tone source is bounded by the configured amplitude '
                '(%.3f of full scale), so the TX digital path cannot reach '
                'full scale; the RX windows are still measured for it' % amplitude),
    }


def stage0_plan(cfg: Dict) -> Dict:
    """Pure Stage 0 plan: three windows, one TX start, <=2 s of transmit."""
    validate_stage0_config(cfg)
    duration = float(cfg['duration_s'])
    guard = float(cfg.get('tx_guard_s', STAGE0_TX_GUARD_S))
    budget = {
        'tx_starts': 1,
        'tx_seconds': duration,
        'tone_seconds': min(duration, STAGE0_TX_BUDGET_S - guard),
        'tx_budget_s': STAGE0_TX_BUDGET_S,
        'tx_guard_s': guard,
        'tx_budget_ok': bool(duration <= STAGE0_TX_BUDGET_S),
        'rx_windows': len(STAGE0_WINDOW_NAMES),
        'rx_seconds': duration * len(STAGE0_WINDOW_NAMES),
        'capture_samples': int(cfg['capture_samples']),
    }
    windows = [
        {'name': 'OFF_PRE', 'index': 0, 'rx_only': True, 'tx_on': False,
         'seconds': duration,
         'note': 'receive only; the TX LO is already enabled at the verified '
                 '433.9 MHz, but no TX graph is started'},
        {'name': 'TONE', 'index': 1, 'rx_only': False, 'tx_on': True,
         'seconds': min(duration, STAGE0_TX_BUDGET_S - guard),
         'note': 'the single TX start, run for min(duration_s, budget-guard); '
                 'the RX window is captured in the same graph'},
        {'name': 'OFF_POST', 'index': 2, 'rx_only': True, 'tx_on': False,
         'seconds': duration,
         'note': 'receive only, after the TX graph is stopped and released; '
                 'the TX LO is still enabled'},
    ]
    return {
        'schema': STAGE0_SCHEMA,
        'mode': 'stage0-dry-run',
        'hardware_accessed': False,
        'pluto_only': True,
        'board_actions': 0,
        'com11': False,
        'token_required': False,
        'nonce_generated': False,
        'wiring': ('Pluto TX SMA -> one 50 ohm coax -> Pluto RX SMA; '
                   'oscilloscope and board disconnected'),
        'parameters': {
            'serial': cfg['serial'], 'lo_hz': cfg['center_hz'],
            'sample_rate_hz': cfg['sample_rate'],
            'bandwidth_hz': cfg['bandwidth_hz'],
            'tone_hz': float(cfg['tone_hz']),
            'amplitude': float(cfg['amplitude']),
            'tx_attenuation_db': float(cfg['tx_attenuation_db']),
            'tx_attenuation_ceiling_db': float(cfg['tx_attenuation_ceiling_db']),
            'rx_gain_db': float(cfg['rx_gain_db']),
            'gain_mode': cfg['gain_mode'],
        },
        'windows': windows,
        'budget': budget,
        'tx_budget_scope': STAGE0_TX_BUDGET_SCOPE,
        'lo_on_windows': list(STAGE0_WINDOW_NAMES),
        'rx_bandwidth_policy': STAGE0_RX_BANDWIDTH_POLICY,
        'tx_digital_evidence': stage0_tx_digital_evidence(cfg),
        'usb_ownership': {
            'second_context_during_stream': False,
            'phases': [
                'libiio_pre: TX LO powerdown=1 (LO off), write and verify the '
                'TX LO frequency 433900000 and hardwaregain=-60, read both back, '
                'then powerdown=0 (enable) and read that back',
                'gnuradio_off_pre: receive-only graph, no TX block started',
                'gnuradio_tone: one graph with the TX sink and the RX source; '
                'exactly one TX start, stopped at min(duration_s, budget-guard) '
                'within the 2 s budget',
                'gnuradio_off_post: receive-only graph, TX graph already stopped',
                'libiio_post: reopen libiio only after GNU Radio released USB; '
                'write hardwaregain=-89.75 and powerdown=1, read each one back '
                'twice',
            ],
        },
        'gates': {
            'pre_tx_readback': {
                'attenuation_db': STAGE0_TX_ATTENUATION_DB,
                'lo_powerdown': '1',
                'before_lo_enable': True,
                'lo_frequency_hz': STAGE0_LO_HZ,
                'lo_frequency_verified_before_enable': True,
                'note': ('the target LO frequency 433900000 Hz and the target '
                         'attenuation are written and read back while the TX LO '
                         'is still powered down, so the OFF_PRE window cannot '
                         'inherit the previous LO (the real receipt showed '
                         '2.45 GHz); only then is the LO enabled, following the '
                         'verified GNU Radio order (frequency -> samplerate -> '
                         'bandwidth -> attenuation -> filter)'),
            },
            'post_stop_readback': {
                'attenuation_db': STAGE0_CEILING_DB,
                'lo_powerdown': '1',
                'note': 'after the graph is stopped and USB released',
            },
            'rx_clipping': {
                'criterion': 'computed abs-max and near-full-scale fraction of the '
                             'captured IQ; a string match on "CLIPPED" is not a gate',
                'near_full_scale': STAGE0_NEAR_FULL_SCALE,
            },
            'matched_narrowband_on_off': {
                'metric': STAGE0_ON_OFF_METRIC,
                'half_bins': STAGE0_ON_OFF_HALF_BINS,
                'nbins': 2 * STAGE0_ON_OFF_HALF_BINS + 1,
                'threshold_db': STAGE0_MIN_ON_OFF_DB,
                'note': ('task-6 gate: the TONE peak bin +-4 Hann-mainlobe bins '
                         '(9 bins); the same bin indices are averaged in both '
                         'OFF windows; gate = min(tone/off_pre, tone/off_post) '
                         '>= threshold'),
            },
            'target_band': {
                'center_hz': STAGE0_TONE_HZ, 'width_hz': STAGE0_BAND_HZ,
                'min_on_off_db': STAGE0_MIN_ON_OFF_DB,
                'legacy_information_only': True,
                'note': ('legacy 20 kHz band mean; kept as information only and '
                         'NOT a gate (task-4 attribution: 546-bin mean carries a '
                         'fixed 17.83 dB integration penalty for a CW mainlobe)'),
            },
            'peak': {'tolerance_hz': STAGE0_PEAK_TOLERANCE_HZ,
                     'min_peak_to_median_db': STAGE0_MIN_PEAK_TO_MEDIAN_DB},
            'budget': {'tx_starts': 1, 'max_tx_seconds': STAGE0_TX_BUDGET_S},
        },
        'stop_on': ['any readback mismatch', 'computed digital full scale in any window',
                    'peak outside the tolerance',
                    'matched-narrowband ON/OFF delta below %d dB'
                    % int(STAGE0_MIN_ON_OFF_DB), 'TX budget exceeded',
                    'GNU Radio error or USB error', 'CLEANUP_FAILED'],
        'outputs': ['stage0.json', 'OFF_PRE/iq.npy', 'TONE/iq.npy',
                    'OFF_POST/iq.npy', 'per-window result.json'],
    }


def analyse_stage0_window(iq, sample_rate_hz: float, target_offset_hz: float,
                          band_hz: float = STAGE0_BAND_HZ,
                          near_full_scale: float = STAGE0_NEAR_FULL_SCALE) -> Dict:
    """Offline window analysis: peak, target band power and clipping evidence.

    Clipping is *computed* from the samples: the maximum absolute component,
    the fraction of samples at or above the near-full-scale threshold and the
    remaining headroom to digital full scale.  No log string is consulted.
    """
    z = np.asarray(iq, dtype=np.complex128).ravel()
    if len(z) < 4096:
        raise PlanError('stage0 analysis needs at least 4096 samples')
    y = z[-min(len(z), 65536):]
    spectrum = np.abs(np.fft.fftshift(np.fft.fft((y - y.mean()) * np.hanning(len(y))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / float(sample_rate_hz)))
    index = int(np.argmax(spectrum))
    peak = float(freqs[index])
    median = float(np.median(spectrum))
    peak_to_median = 10.0 * math.log10((float(spectrum[index]) + 1e-30) /
                                       (median + 1e-30))
    mask = np.abs(freqs - float(target_offset_hz)) <= float(band_hz) / 2.0
    band_power = float(np.mean(spectrum[mask])) if bool(mask.any()) else 0.0
    evidence = clipping_evidence(z, near_full_scale)
    abs_max = evidence['abs_max_iq']
    rms = float(np.sqrt(np.mean(np.abs(z) ** 2)))
    return {
        'samples': int(len(z)),
        'expected_offset_hz': float(target_offset_hz),
        'tolerance_hz': STAGE0_PEAK_TOLERANCE_HZ,
        'peak_offset_hz': peak,
        'delta_hz': peak - float(target_offset_hz),
        'peak_ok': abs(peak - float(target_offset_hz)) <= STAGE0_PEAK_TOLERANCE_HZ,
        'peak_to_median_db': peak_to_median,
        'target_band_center_hz': float(target_offset_hz),
        'target_band_width_hz': float(band_hz),
        'target_band_power': band_power,
        'target_band_power_db': 10.0 * math.log10(band_power + 1e-30),
        'rms': rms,
        **evidence,
        'crest_factor_db': (None if abs_max <= 0.0 or rms <= 0.0
                            else 20.0 * math.log10(abs_max / rms)),
        'all_zero': bool(np.all(z == 0)),
    }


def _stage0_hann_spectrum(iq, sample_rate_hz: float):
    """The shared Stage 0 spectral estimator: last 65536 samples, Hann, detrend."""
    z = np.asarray(iq, dtype=np.complex128).ravel()
    y = z[-min(len(z), 65536):]
    spectrum = np.abs(np.fft.fftshift(np.fft.fft(
        (y - y.mean()) * np.hanning(len(y))))) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / float(sample_rate_hz)))
    return freqs, spectrum


def matched_narrowband_on_off(tone_iq, off_pre_iq, off_post_iq,
                              sample_rate_hz: float,
                              half_bins: int = STAGE0_ON_OFF_HALF_BINS) -> Dict:
    """task-6 ON/OFF gate: TONE peak bin +-half_bins vs the same OFF bins.

    The TONE peak bin defines one matched narrow band of
    ``2*half_bins + 1`` Hann-mainlobe bins; the *same bin indices* are then
    averaged in both OFF windows.  The gate is
    ``min(tone/off_pre, tone/off_post) >= STAGE0_MIN_ON_OFF_DB``.
    """
    freqs, tone = _stage0_hann_spectrum(tone_iq, sample_rate_hz)
    _, off_pre = _stage0_hann_spectrum(off_pre_iq, sample_rate_hz)
    _, off_post = _stage0_hann_spectrum(off_post_iq, sample_rate_hz)
    peak_index = int(np.argmax(tone))
    half = max(0, int(half_bins))
    lo = max(0, peak_index - half)
    hi = min(len(tone), peak_index + half + 1)
    tone_mean = float(np.mean(tone[lo:hi]))
    off_pre_mean = float(np.mean(off_pre[lo:hi]))
    off_post_mean = float(np.mean(off_post[lo:hi]))
    worst_off = max(off_pre_mean, off_post_mean)
    delta = 10.0 * math.log10((tone_mean + 1e-300) / (worst_off + 1e-300))
    return {
        'metric': STAGE0_ON_OFF_METRIC,
        'metric_note': ('TONE peak bin +-%d Hann-mainlobe bins (%d bins); the '
                        'same bin indices are averaged in both OFF windows; '
                        'gate = min(tone/off_pre, tone/off_post)' %
                        (half, hi - lo)),
        'half_bins': half,
        'nbins': int(hi - lo),
        'peak_bin_index': peak_index,
        'peak_offset_hz': float(freqs[peak_index]),
        'tone_match_mean': tone_mean,
        'off_pre_match_mean': off_pre_mean,
        'off_post_match_mean': off_post_mean,
        'worst_off_match_mean': worst_off,
        'delta_db': delta,
        'threshold_db': STAGE0_MIN_ON_OFF_DB,
        'passed': bool(delta >= STAGE0_MIN_ON_OFF_DB),
    }


def evaluate_stage0_windows(windows: Dict, narrowband: Optional[Dict] = None) -> Dict:
    """Gate the three Stage 0 windows together.

    The ON/OFF gate is the task-6 matched narrowband metric (``narrowband``
    from ``matched_narrowband_on_off``).  The old 20 kHz band-mean delta is
    still computed but is kept only as information and never gates.
    """
    problems = []
    missing = [name for name in STAGE0_WINDOW_NAMES if name not in (windows or {})]
    if missing:
        return {'passed': False,
                'problems': ['missing window(s): ' + ', '.join(missing)],
                'on_off_comparison': None}
    for name in STAGE0_WINDOW_NAMES:
        if windows[name].get('clipped'):
            problems.append('%s reached digital full scale (abs max %.6f, '
                            'near-full-scale fraction %.6f)'
                            % (name, windows[name].get('abs_max_iq', -1.0),
                               windows[name].get('full_scale_fraction', -1.0)))
    tone = windows['TONE']
    if not tone.get('peak_ok'):
        problems.append('TONE peak %.1f Hz is outside %.1f Hz of the target'
                        % (tone.get('peak_offset_hz', float('nan')),
                           STAGE0_PEAK_TOLERANCE_HZ))
    if tone.get('peak_to_median_db', 0.0) < STAGE0_MIN_PEAK_TO_MEDIAN_DB:
        problems.append('TONE peak-to-median %.3f dB is below %.1f dB'
                        % (tone.get('peak_to_median_db', 0.0),
                           STAGE0_MIN_PEAK_TO_MEDIAN_DB))
    # Legacy 20 kHz band mean: information only, never a gate.
    on_db = tone.get('target_band_power_db', -300.0)
    off_pre = windows['OFF_PRE'].get('target_band_power_db', -300.0)
    off_post = windows['OFF_POST'].get('target_band_power_db', -300.0)
    worst_off = max(off_pre, off_post)
    legacy_delta = on_db - worst_off
    legacy = {
        'legacy_band_nbins_note': ('20 kHz band mean (546 bins at 65536-point '
                                   'FFT); legacy information only, NOT a gate'),
        'legacy_band_delta_db': legacy_delta,
        'legacy_band_passed': bool(legacy_delta >= STAGE0_MIN_ON_OFF_DB),
        'legacy_band_note': ('information only; not a gate (task-4 attribution: '
                             'the 546-bin band mean carries a fixed 17.83 dB '
                             'integration penalty against a 9-bin CW mainlobe)'),
    }
    if not isinstance(narrowband, dict) or 'delta_db' not in narrowband:
        problems.append('matched-narrowband ON/OFF evidence is missing')
        comparison = dict(legacy)
        comparison.update({'metric': STAGE0_ON_OFF_METRIC, 'nbins': None,
                           'threshold_db': STAGE0_MIN_ON_OFF_DB,
                           'delta_db': None, 'passed': False})
    else:
        comparison = dict(narrowband)
        comparison['threshold_db'] = STAGE0_MIN_ON_OFF_DB
        comparison['passed'] = bool(
            narrowband.get('delta_db', -300.0) >= STAGE0_MIN_ON_OFF_DB)
        comparison.update(legacy)
        if not comparison['passed']:
            problems.append(
                'matched narrowband ON/OFF delta %.3f dB is below %.1f dB'
                % (narrowband.get('delta_db', float('nan')),
                   STAGE0_MIN_ON_OFF_DB))
    return {'passed': not problems, 'problems': problems,
            'on_off_comparison': comparison}


def stage0_self_test(cfg: Optional[Dict] = None) -> Dict:
    """Offline self-test of the Stage 0 planner and analysis; no hardware."""
    cfg = cfg or load_config(Path(__file__).resolve().parent / 'config' /
                             'stage0_selfloop.json')
    checks = []
    validate_stage0_config(cfg)
    checks.append(('stage0-config', 'ok'))
    plan = stage0_plan(cfg)
    checks.append(('stage0-windows', 'ok' if [w['name'] for w in plan['windows']] ==
                   list(STAGE0_WINDOW_NAMES) else 'FAIL'))
    checks.append(('stage0-tx-budget', 'ok' if plan['budget']['tx_starts'] == 1 and
                   plan['budget']['tx_seconds'] <= STAGE0_TX_BUDGET_S else 'FAIL'))
    tx = plan['tx_digital_evidence']
    checks.append(('stage0-digital-evidence', 'ok' if tx['peak_code'] == 205 and
                   tx['clipping_possible'] is False else 'FAIL'))
    checks.append(('stage0-gain-signs', 'ok' if (
        STAGE0_TX_HARDWARE_GAIN_DB == -STAGE0_TX_ATTENUATION_DB and
        STAGE0_CEILING_HARDWARE_GAIN_DB == -STAGE0_CEILING_DB) else 'FAIL'))
    samples = 65536
    t = np.arange(samples, dtype=float) / float(cfg['sample_rate'])
    on = (0.5 * np.exp(2j * np.pi * float(cfg['tone_hz']) * t)).astype(np.complex64)
    off = np.zeros(samples, dtype=np.complex64)
    tone = analyse_stage0_window(on, float(cfg['sample_rate']), float(cfg['tone_hz']))
    quiet = analyse_stage0_window(off, float(cfg['sample_rate']), float(cfg['tone_hz']))
    checks.append(('stage0-analysis', 'ok' if tone['peak_ok'] and
                   tone['peak_to_median_db'] > STAGE0_MIN_PEAK_TO_MEDIAN_DB and
                   tone['clipped'] is False else 'FAIL'))
    verdict = evaluate_stage0_windows(
        {'OFF_PRE': quiet, 'TONE': tone, 'OFF_POST': quiet},
        narrowband=matched_narrowband_on_off(on, off, off,
                                             float(cfg['sample_rate'])))
    checks.append(('stage0-on-off-gate', 'ok' if verdict['passed'] else 'FAIL'))
    clipped = analyse_stage0_window(np.ones(samples, dtype=np.complex64),
                                    float(cfg['sample_rate']), float(cfg['tone_hz']))
    checks.append(('stage0-clipping-computed', 'ok' if clipped['clipped'] and
                   clipped['full_scale_fraction'] > 0.0 else 'FAIL'))
    ok = all(status == 'ok' for _, status in checks)
    return {'hardware_accessed': False, 'passed': ok, 'checks': checks,
            'plan': plan}


def self_test(cfg: Optional[Dict] = None) -> Dict:
    cfg = cfg or load_config(Path(__file__).resolve().parent / 'config' / 'diagnostic.json')
    checks = []
    validate_config(cfg)
    checks.append(('config', 'ok'))
    k, actual = nearest_board_tone_bin(60000000, 4096, 100000)
    checks.append(('tone-bin-7', 'ok' if (k, round(actual, 6)) == (7, 102539.0625) else 'FAIL'))
    payload, meta = tone_payload(60000000, 4096, 7, 60)
    iq = np.frombuffer(payload, dtype='<i2').reshape(-1, 2)
    z = (iq[:, 0] + 1j * iq[:, 1]).astype(np.complex64) / 2048.0
    tone = analyse_tone(z, 60000000.0, actual)
    checks.append(('tone-fft', 'ok' if tone['peak_ok'] and tone['peak_to_median_db'] > 40 else 'FAIL'))
    p2, m2 = bpsk_rect_payload(1, 6000000, 3000000, 0.1)
    checks.append(('bpsk-payload', 'ok' if m2['samples_per_symbol'] == 2 and
                   m2['differential_encoding'] is False else 'FAIL'))
    a = budget('A', 1)
    b = budget('B', 1)
    checks.append(('budget-smoke', 'ok' if a['windows_total'] == 4 and
                   b['windows_total'] == 4 else 'FAIL'))
    a3 = budget('A', 3, extended=True)
    checks.append(('budget-extension', 'ok' if a3['windows_total'] == 12 and
                   a3['includes_smoke_session'] else 'FAIL'))
    ok = all(status == 'ok' for _, status in checks)
    return {'hardware_accessed': False, 'passed': ok, 'checks': checks,
            'level_budget': level_budget(cfg)}