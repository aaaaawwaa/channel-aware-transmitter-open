"""Bounded real BPSK symbols derived from an attested same-frame estimate.

No IO, carrier search, reference search, ideal-point substitution or EVM claim.
"""
from dataclasses import asdict, dataclass
import math
from typing import Optional, Tuple
import numpy as np
from rf_bpsk_pipeline import BpskEnvelope
from rf_bpsk_receiver import VERSION
from rf_rx_types import RxIqFrame

MAX_POINTS = 128


@dataclass(frozen=True)
class RxConstellation:
    capture_id: str
    release_id: Optional[str]
    connection_generation: int
    config_revision: int
    completed_at_monotonic: float
    submitted_revision: int
    algorithm_version: str = VERSION
    reason: str = ''
    normalization: str = 'symbol_rms'
    symbol_count: int = 0
    points: Tuple[Tuple[float, float], ...] = ()

    def to_dict(self):
        return asdict(self)


def estimate_constellation(frame, envelope):
    if not isinstance(frame, RxIqFrame) or not isinstance(envelope, BpskEnvelope):
        raise TypeError('expected immutable IQ and BpskEnvelope')
    identity = dict(capture_id=frame.capture_id, release_id=frame.release_id,
        connection_generation=frame.connection_generation, config_revision=frame.config_revision,
        completed_at_monotonic=frame.completed_at_monotonic,
        submitted_revision=envelope.submitted.revision)
    def reject(reason):
        return RxConstellation(**identity, reason=reason)
    r=envelope.result; submitted=envelope.submitted; cfg=submitted.config
    if (envelope.algorithm_version != VERSION or r.algorithm_version != VERSION
            or any(getattr(r,k)!=identity[k] for k in identity if k!='submitted_revision')):
        return reject('RESULT_MISMATCH')
    if (cfg.mode!='BPSK' or cfg.shaping!='RECT' or cfg.symbol_rate_hz!=3000000
            or frame.samples!=4096 or frame.ratio!=0 or frame.sample_rate_hz!=60000000
            or submitted.decim!=frame.decimation or submitted.rx_len!=frame.samples
            or submitted.input_fs_hz!=frame.sample_rate_hz):
        return reject('UNSUPPORTED')
    if r.carrier_sync is not True or r.timing_sync is not True:
        return reject(r.reason or 'NOT_SYNCED')
    offset, count = r.timing_offset_samples, r.raw_symbols
    if (type(offset) is not int or not 0<=offset<20 or type(count) is not int
            or count!=(4096-19)//20 or type(r.total_symbols) is not int
            or r.total_symbols!=count-2
            or any(type(v) not in (int,float) or not math.isfinite(v)
                   for v in (r.cfo_hz,r.phase_mod_pi))):
        return reject('INVALID_ESTIMATE')
    iq=np.frombuffer(frame.payload,dtype='<i2').reshape(-1,2)
    if np.any(iq<=-2048) or np.any(iq>=2047):
        return reject('INVALID_IQ')
    z=iq[:,0].astype(float)+1j*iq[:,1].astype(float)
    z-=np.mean(z)
    z*=np.exp(-2j*np.pi*r.cfo_hz*np.arange(len(z))/frame.sample_rate_hz)
    z*=np.exp(-1j*r.phase_mod_pi)
    values=z[offset:offset+count*20].reshape(count,20).mean(axis=1)[1:-1]
    scale=float(np.sqrt(np.mean(abs(values)**2)))
    if not math.isfinite(scale) or scale<=0:
        return reject('INVALID_ESTIMATE')
    points=tuple((float(v.real/scale),float(v.imag/scale)) for v in values[:MAX_POINTS])
    if any(not math.isfinite(x) or not math.isfinite(y) for x,y in points):
        return reject('INVALID_ESTIMATE')
    return RxConstellation(**identity,symbol_count=len(values),points=points)
