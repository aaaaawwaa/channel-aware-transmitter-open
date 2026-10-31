"""Declared finite TX experiments; truth never enters receive diagnostics.

x=A*s+j: only the desired normalised BPSK waveform is multiplied by A.
Interference is expressed in fixed DAC code units and never globally scaled.
These declarations establish reproducibility, not physical RX qualification.
"""
from dataclasses import dataclass
import math

import numpy as np

from rf_iq_signal import prbs7_bits

VERSION = 'rf-controlled-scenarios-v1'
SAMPLE_RATE_HZ = 60000000
PERIOD_SAMPLES = 2540
KINDS = ('normal', 'narrowband', 'pulse', 'compound', 'off')


@dataclass(frozen=True)
class FrozenFields:
    values: tuple

    def __post_init__(self):
        if (type(self.values) is not tuple or len(self.values)>48
                or any(type(pair) is not tuple or len(pair)!=2
                       or type(pair[0]) is not str for pair in self.values)):
            raise ValueError('fields must be bounded immutable key/value pairs')
        if len({k for k,v in self.values})!=len(self.values):
            raise ValueError('duplicate field keys forbidden')
        for _,value in self.values:
            if type(value) not in (str,int,float,bool) or (type(value) is float
                    and not math.isfinite(value)) or (type(value) is str and len(value)>256):
                raise ValueError('fields require finite bounded scalar values')

    def to_dict(self):
        return dict(self.values)


@dataclass(frozen=True)
class ScenarioConfig:
    kind: str = 'normal'
    amplitude: int = 60
    prbs7_seed: int = 1
    tone_amplitude: object = None
    tone_frequency_hz: float = 6000000
    tone_phase_rad: float = 0.
    pulse_amplitude: object = None
    pulse_start: int = 600
    pulse_width: int = 80
    pulse_phase_rad: float = 0.
    seed: int = 1


@dataclass(frozen=True)
class ScenarioFrame:
    payload: bytes
    metadata: FrozenFields
    declaration: FrozenFields

    def metadata_dict(self):
        return self.metadata.to_dict()

    def declaration_dict(self):
        return self.declaration.to_dict()

    def to_dict(self):
        return dict(metadata=self.metadata_dict(), declaration=self.declaration_dict())


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s must be an integer %d..%d' % (name,low,high))
    return value


def _finite(value, name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError('%s must be a finite number' % name)
    return float(value)


def generate_scenario(config):
    """Return immutable signed12 bytes and an independently stored declaration."""
    if not isinstance(config, ScenarioConfig):
        raise TypeError('expected frozen ScenarioConfig')
    if config.kind not in KINDS:
        raise ValueError('unsupported controlled scenario')
    amplitude = _integer(config.amplitude,'amplitude',0,100)
    prbs_seed = _integer(config.prbs7_seed,'prbs7_seed',1,127)
    seed = _integer(config.seed,'seed',0,2147483647)
    # Compound development preset reserves headroom; RX clipping still rejects.
    default_j = 15 if config.kind=='compound' else 40
    tone_amp = _integer(default_j if config.tone_amplitude is None else config.tone_amplitude,
                        'tone_amplitude',0,100)
    pulse_amp = _integer(default_j if config.pulse_amplitude is None else config.pulse_amplitude,
                         'pulse_amplitude',0,100)
    frequency = _finite(config.tone_frequency_hz,'tone_frequency_hz')
    if not 0 < abs(frequency) < SAMPLE_RATE_HZ/2:
        raise ValueError('tone frequency must be nonzero and inside Nyquist')
    tone_phase = _finite(config.tone_phase_rad,'tone_phase_rad')
    pulse_phase = _finite(config.pulse_phase_rad,'pulse_phase_rad')
    start = _integer(config.pulse_start,'pulse_start',0,PERIOD_SAMPLES-1)
    width = _integer(config.pulse_width,'pulse_width',1,PERIOD_SAMPLES)
    if start+width > PERIOD_SAMPLES:
        raise ValueError('pulse must fit wholly inside its declared period')
    tone_used = tone_amp if config.kind in ('narrowband','compound') else 0
    pulse_used = pulse_amp if config.kind in ('pulse','compound') else 0
    if config.kind!='off' and amplitude+tone_used+pulse_used > 100:
        raise ValueError('conservative component peak bound exceeds physical code 100')
    tone_bin = int(np.rint(frequency*PERIOD_SAMPLES/SAMPLE_RATE_HZ))
    if tone_bin==0 or abs(tone_bin)>=PERIOD_SAMPLES//2:
        raise ValueError('frequency rounding reaches DC or Nyquist')
    actual_frequency = tone_bin*SAMPLE_RATE_HZ/PERIOD_SAMPLES
    s = np.repeat(1-2*np.asarray(prbs7_bits(prbs_seed,127)),20)
    n = np.arange(PERIOD_SAMPLES)
    j = tone_used*np.exp(1j*(2*np.pi*tone_bin*n/PERIOD_SAMPLES+tone_phase))
    j = j.astype(complex)
    j[start:start+width] += pulse_used*np.exp(1j*pulse_phase)
    z = amplitude*s+j if config.kind!='off' else np.zeros(PERIOD_SAMPLES,complex)
    quantized = np.rint(np.column_stack((z.real,z.imag)))
    if np.any(quantized < -2048) or np.any(quantized > 2047):
        raise ValueError('signed12 overflow; automatic scaling forbidden')
    if np.any(np.abs(quantized)>100):
        raise ValueError('physical conservative composite code limit exceeded')
    payload = quantized.astype('<i2').tobytes()
    metadata = FrozenFields(tuple(dict(samples=PERIOD_SAMPLES,bytes=len(payload),
        format='s16_iq_le',direction='tx',wrap=True).items()))
    declaration = FrozenFields(tuple(dict(version=VERSION,truth=config.kind,
        formula='x=A*s+j',sample_rate_hz=SAMPLE_RATE_HZ,symbol_rate_hz=3000000,
        shaping='RECT',period_samples=PERIOD_SAMPLES,period_symbols=127,
        amplitude=amplitude,prbs7_seed=prbs_seed,seed=seed,
        tone_amplitude=tone_used,tone_frequency_requested_hz=frequency,
        tone_frequency_actual_hz=actual_frequency,tone_bin=tone_bin,
        tone_phase_rad=tone_phase,pulse_amplitude=pulse_used,pulse_start=start,
        pulse_width=width,pulse_period_samples=PERIOD_SAMPLES,
        pulse_phase_rad=pulse_phase,physical_max_code=100,
        quantization='numpy.rint / signed12 / no global scaling').items()))
    return ScenarioFrame(payload,metadata,declaration)
