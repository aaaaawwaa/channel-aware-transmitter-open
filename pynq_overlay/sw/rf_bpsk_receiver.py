"""Finite, feed-forward BPSK/RECT receiver; no model, IO or TX state access.

Supported: 4096 signed12 IQ samples, 60 MSPS, D1, 3 MSym/s.
The first 63 decisions align PRBS; only the remaining decisions count in BER.
Synchronization is an estimate within one snapshot, not a streaming lock.
"""
from dataclasses import asdict, dataclass
from typing import Optional, Tuple

import numpy as np

from rf_rx_types import RxIqFrame

VERSION = 'bpsk-rect-snapshot-v2'
ALIGNMENT_BITS = 63
MIN_COMPARE_BITS = 64
SPS = 20
MAX_CFO_HZ = 100000


@dataclass(frozen=True)
class BpskConfig:
    mode: str = 'BPSK'
    symbol_rate_hz: int = 3000000
    shaping: str = 'RECT'
    reference_prbs7_seed: Optional[int] = None


@dataclass(frozen=True)
class BpskResult:
    capture_id: str
    release_id: Optional[str]
    config_revision: int
    connection_generation: int
    completed_at_monotonic: float
    algorithm_version: str = VERSION
    reason: str = ''
    carrier_sync: bool = False
    timing_sync: bool = False
    reference_sync: Optional[bool] = None
    cfo_hz: Optional[float] = None
    carrier_coherence: Optional[float] = None
    timing_quality: Optional[float] = None
    timing_offset_samples: Optional[int] = None
    phase_mod_pi: Optional[float] = None
    total_symbols: int = 0
    raw_symbols: int = 0
    discarded_edge_symbols: int = 2
    alignment_bits: int = 0
    compared_bits: Optional[int] = None
    bit_errors: Optional[int] = None
    ber: Optional[float] = None
    reference_offset: Optional[int] = None
    polarity: Optional[int] = None
    reference_correlation: Optional[float] = None
    reference_gap: Optional[float] = None
    comparison_start_sample: Optional[int] = None
    comparison_end_sample_exclusive: Optional[int] = None
    bits_preview: Tuple[int, ...] = ()

    def to_dict(self):
        return asdict(self)


def _reference(seed):
    state, out = seed, []
    for _ in range(127):
        out.append(1 - 2*((state >> 6) & 1))
        state = ((state << 1) & 127) | (((state >> 6) ^ (state >> 5)) & 1)
    return np.asarray(out)


def _reference_correlations(observed, reference):
    """All 127 shifts and 63 alignment bits, with the same integer sums."""
    indices=(np.arange(127)[:,None]+np.arange(ALIGNMENT_BITS))%127
    return np.mean(observed*reference[indices],axis=1)


def receive_bpsk(frame, config):
    """Return an immutable estimate; refusals never carry a previous BER.

    Finite signed12 bytes cannot encode NaN. Invalid frame/config objects are
    programming errors; unsupported acquisition settings are explicit refusals.
    """
    if not isinstance(frame, RxIqFrame) or not isinstance(config, BpskConfig):
        raise TypeError('expected RxIqFrame and BpskConfig')
    data = dict(capture_id=frame.capture_id, release_id=frame.release_id,
                config_revision=frame.config_revision,
                connection_generation=frame.connection_generation,
                completed_at_monotonic=frame.completed_at_monotonic,
                reference_sync=False if config.reference_prbs7_seed is not None else None)

    def result(reason='', **updates):
        return BpskResult(**dict(data, reason=reason, **updates))

    if (config.mode != 'BPSK' or config.shaping != 'RECT'
            or type(config.symbol_rate_hz) is not int or config.symbol_rate_hz != 3000000
            or frame.samples != 4096 or frame.ratio != 0 or frame.sample_rate_hz != 60000000):
        return result('UNSUPPORTED')
    seed = config.reference_prbs7_seed
    if seed is not None and (type(seed) is not int or not 1 <= seed <= 127):
        return result('INVALID_REFERENCE')
    iq = np.frombuffer(frame.payload, dtype='<i2').reshape(-1, 2)
    if np.any(iq < -2048) or np.any(iq > 2047):
        return result('INVALID_IQ')
    if np.any(iq == -2048) or np.any(iq == 2047):
        return result('CLIPPED')
    z = iq[:, 0].astype(float) + 1j*iq[:, 1].astype(float)
    z = z - np.mean(z)
    power = float(np.mean(np.abs(z)**2))
    if power < 1.0:
        return result('NO_ENERGY')
    # Squaring removes BPSK signs. Search all bins, then refuse out-of-domain
    # estimates rather than projecting an out-of-domain signal onto the edge.
    square = z*z
    spectrum = np.abs(np.fft.fft(square*np.hanning(len(z))))
    coarse = float(np.fft.fftfreq(len(z), 1./frame.sample_rate_hz)[np.argmax(spectrum)])/2
    n = np.arange(len(z), dtype=float)
    residual = square*np.exp(-4j*np.pi*coarse*n/frame.sample_rate_hz)
    # Filtered symbol transitions pass through zero. Unwrapping each noisy sample
    # accumulates whole cycles at those crossings, corrupting every later sample
    # despite a weighted fit. Average complex residuals before taking phase.
    block_size = 64
    block = residual.reshape(-1, block_size).mean(axis=1)
    positions = n.reshape(-1, block_size).mean(axis=1)
    phase = np.unwrap(np.angle(block))
    weights = np.abs(block)
    center = np.sum(weights*positions)/np.sum(weights)
    phase_center = np.sum(weights*phase)/np.sum(weights)
    slope = np.sum(weights*(positions-center)*(phase-phase_center))/np.sum(weights*(positions-center)**2)
    cfo = float(coarse + slope*frame.sample_rate_hz/(4*np.pi))
    if abs(cfo) > MAX_CFO_HZ+1000:
        return result('CFO_OUT_OF_RANGE', cfo_hz=cfo)
    corrected = z*np.exp(-2j*np.pi*cfo*n/frame.sample_rate_hz)
    moment = np.mean(corrected*corrected)
    coherence = float(abs(moment)/power)
    data.update(cfo_hz=cfo, carrier_coherence=coherence)
    if coherence < .70:
        return result('CARRIER_NOT_SYNCED')
    theta = float(np.angle(moment)/2)
    corrected *= np.exp(-1j*theta)
    # Same number of symbols per candidate; neither reference nor BER is used
    # to select the sampling phase. Discard an integral symbol at each edge.
    count = (len(z)-(SPS-1))//SPS
    candidates = [corrected[t:t+count*SPS].reshape(count,SPS).mean(axis=1)[1:-1]
                  for t in range(SPS)]
    energies = [float(np.mean(np.abs(v)**2)) for v in candidates]
    offset = int(np.argmax(energies))
    symbols = candidates[offset]
    quality = energies[offset]/power
    data.update(carrier_sync=True, phase_mod_pi=theta, timing_offset_samples=offset,
                timing_quality=quality, total_symbols=len(symbols), raw_symbols=count)
    if quality < .55 or len(symbols) < ALIGNMENT_BITS+MIN_COMPARE_BITS:
        return result('TIMING_NOT_SYNCED')
    decisions = (symbols.real < 0).astype(int)
    data.update(timing_sync=True, bits_preview=tuple(int(v) for v in decisions[:64]))
    if seed is None:
        return result('REFERENCE_NOT_PROVIDED')
    ref = _reference(seed)
    observed = 1-2*decisions[:ALIGNMENT_BITS]
    correlations = _reference_correlations(observed, ref)
    order = np.argsort(np.abs(correlations))
    best = int(order[-1])
    correlation = float(abs(correlations[best]))
    gap = correlation-float(abs(correlations[order[-2]]))
    data.update(alignment_bits=ALIGNMENT_BITS, reference_correlation=correlation, reference_gap=gap)
    if correlation < .80 or gap < .20:
        return result('REFERENCE_NOT_SYNCED')
    polarity = 1 if correlations[best] >= 0 else -1
    measured = (1-2*decisions[ALIGNMENT_BITS:])*polarity
    expected = ref[(np.arange(ALIGNMENT_BITS,len(decisions))+best)%127]
    errors = int(np.count_nonzero(measured != expected))
    return result(reference_sync=True, reference_offset=best, polarity=polarity,
                  compared_bits=len(measured), bit_errors=errors, ber=errors/len(measured),
                  comparison_start_sample=offset+(ALIGNMENT_BITS+1)*SPS,
                  comparison_end_sample_exclusive=offset+(count-1)*SPS)
