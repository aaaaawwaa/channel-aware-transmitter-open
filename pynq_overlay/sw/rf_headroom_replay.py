"""Offline replay of a saved HEADROOM-1 research frame through the frozen analyses.

A research frame is 4096 samples of undecimated s16 IQ at 60 MHz carrying the
same BPSK 3 Msym/s RECT waveform the teaching contract uses, transmitted at
46 dB instead of 40 dB.  Nothing here touches hardware or a daemon: the frame is
rebuilt from the bytes that were saved before any analysis ran, and every result
is labelled a replay.

Two legs matter, and they answer different questions:

* The BPSK / constellation leg is the measurement.  It reports whether the
  weaker 46 dB link still recovered the reference, how many bits were compared,
  how many were wrong, and how many symbols the constellation had.
* The environment leg must refuse.  `rf_environment` is the 40 dB window
  classifier and a 46 dB frame is outside its declared domain, so it answers
  `INVALID / RF_DOMAIN_MISMATCH`.  That refusal is the correct outcome and is
  reported as one: it is what shows the research transaction cannot leak into
  the teaching classification.

An OFF attempt has no source, so its replay must show no synchronization, no
constellation points and no bit errors.  `off_violation` is that check.
"""
from dataclasses import dataclass, asdict
import hashlib

from rf_bpsk_pipeline import analyze_demod, freeze_demod_config
from rf_rx_constellation import estimate_constellation
from rf_rx_types import RxIqFrame, RxReadback

VERSION = 'headroom-replay-v1'

# The research waveform is the teaching waveform at a different attenuation, so
# the replay freezes the same acquisition the plan's `headroom.begin` asks for.
RESEARCH_MODE = 'BPSK'
RESEARCH_SYMRATE_HZ = 3000000
RESEARCH_SHAPING = 'RECT'
# The generator's default PRBS7 seed, and the seed the plan fixes for every
# transmitting attempt (`ScenarioConfig.prbs7_seed`).
REFERENCE_SEED = 1
CAPTURE_SAMPLES = 4096
SAMPLE_RATE_HZ = 60000000
IQ_BYTES = CAPTURE_SAMPLES * 4
RAIL_LOW, RAIL_HIGH = -2048, 2047


@dataclass(frozen=True)
class ReplayResult:
    """One replayed frame.  `replay` is always True; this is never a live capture."""
    capture_id: str
    source_sha256: str
    version: str = VERSION
    replay: bool = True
    carrier_sync: bool = False
    timing_sync: bool = False
    reference_sync: object = None
    bit_errors: object = None
    compared_bits: object = None
    ber: object = None
    symbol_count: int = 0
    constellation_reason: str = ''
    environment_label: str = ''
    environment_reason: str = ''
    rail_count: int = 0
    demod_reason: str = ''
    analysis: object = None

    def to_dict(self):
        return dict(self.__dict__)


def research_demod(revision=0):
    """The frozen acquisition the research waveform is defined against.

    `reference_enabled` is True with the fixed seed: the research payload is the
    PRBS7 sequence, so a reference comparison is available and a BER means
    something.  This is the one place the replay's acquisition is decided.
    """
    return freeze_demod_config(
        dict(expected_mode=RESEARCH_MODE, symrate_hz=RESEARCH_SYMRATE_HZ,
             shaping=RESEARCH_SHAPING, reference_enabled=True,
             reference_seed=REFERENCE_SEED, decim=1, rx_len=CAPTURE_SAMPLES,
             input_fs_hz=SAMPLE_RATE_HZ), revision)


def to_rx_frame(payload, capture_id, release_id, request_id=0, timestamp_ns=0,
                samples=CAPTURE_SAMPLES, sample_rate_hz=SAMPLE_RATE_HZ, ratio=0):
    """Rebuild the immutable frame the frozen analyses take, from saved bytes.

    The identity is synthetic and says so (`capture_id` is a replay id and the
    readbacks are absent, not zeros a reader could take for measurements).  What
    is real is the payload and the release the study was recorded under: those
    are the two things the analyses actually read.
    """
    if type(payload) is not bytes or len(payload) != samples * 4:
        raise ValueError('a replay needs the exact saved %d-byte payload'
                         % (samples * 4))
    if not isinstance(capture_id, str) or not capture_id:
        raise ValueError('a replay needs its own capture id')
    if not isinstance(release_id, str) or not release_id:
        raise ValueError('a replay needs the release the study was recorded under')
    # No PL readback was taken for a replay, and `RxReadback.io_ok` is a bool, so
    # it is False -- the honest value for "there was no readback", never a True
    # that a consumer could read as evidence the part answered.
    absent = RxReadback(io_ok=False)
    return RxIqFrame(capture_id, payload, samples, sample_rate_hz, ratio,
                     int(timestamp_ns), 0, 0, int(request_id), release_id,
                     'headroom.capture', False, absent, absent, 0.0)


def count_rails(payload):
    """The rail count, recounted from the bytes and never taken from a header."""
    import struct
    return sum(1 for (value,) in struct.iter_unpack('<h', payload)
               if value in (RAIL_LOW, RAIL_HIGH))


def rf_config_from_readback(readback):
    """The classifier's RF config, taken from the frame's own native readback.

    `rf_environment._domain_error` compares the config it is HANDED against its
    fixed set (the 46 dB single source since 2026-10-17), so the domain a frame
    was taken in has to come from that frame.  Deriving it from the native
    readback is the only honest source: the values are what the daemon reported,
    not what the study intended.

    The readback reports the receiver and transmitter sides separately; the
    classifier's `lo_hz` / `sample_rate_hz` are the receiver's, which is the side
    that produced these bytes.
    """
    pl = readback.get('pl')
    if type(readback) is not dict or type(pl) is not dict:
        raise ValueError('a research readback is required to fix the RF domain')
    values = dict(lo_hz=readback.get('rx_lo_hz'),
                  sample_rate_hz=readback.get('rx_sample_rate_hz'),
                  rx_bandwidth_hz=readback.get('rx_bandwidth_hz'),
                  tx_bandwidth_hz=readback.get('tx_bandwidth_hz'),
                  attenuation_mdB=readback.get('attenuation_mdB'),
                  phy_mode_1r1t=readback.get('phy_mode_1r1t'),
                  idelay_en=pl.get('idelay_en'), idelay_tap=pl.get('idelay_tap'))
    if any(type(value) is not int for value in values.values()):
        raise ValueError('the readback does not carry the fixed RF fields')
    return values


def replay(payload, capture_id, release_id, rf_config, request_id=0,
           timestamp_ns=0, samples=CAPTURE_SAMPLES, sample_rate_hz=SAMPLE_RATE_HZ,
           ratio=0):
    """Replay one saved frame; never raises for a refusal, reports it.

    `rf_config` is REQUIRED and must be the domain the frame was actually taken
    in (`rf_config_from_readback`).  It is not defaulted to the fixed domain:
    that default would hand the classifier a domain the frame was not taken in
    and the refusal below would silently stop happening.

    The environment leg returns a domain refusal as `environment_label='INVALID'`,
    `environment_reason='RF_DOMAIN_MISMATCH'` rather than raising, because it is
    the correct answer rather than a failure: the domain is whatever the readback
    reports, and only the fixed set is accepted.
    """
    import rf_environment as ENV

    frame = to_rx_frame(payload, capture_id, release_id, request_id, timestamp_ns,
                        samples, sample_rate_hz, ratio)
    digest = hashlib.sha256(payload).hexdigest()
    rails = count_rails(payload)
    envelope = analyze_demod(frame, research_demod(0))
    result = envelope.result
    constellation = estimate_constellation(frame, envelope)
    environment = ENV.analyze_environment(frame, envelope, dict(rf_config))
    return ReplayResult(
        capture_id=frame.capture_id, source_sha256=digest,
        carrier_sync=bool(result.carrier_sync), timing_sync=bool(result.timing_sync),
        reference_sync=result.reference_sync, bit_errors=result.bit_errors,
        compared_bits=result.compared_bits, ber=result.ber,
        symbol_count=len(constellation.points),
        constellation_reason=constellation.reason,
        environment_label=environment.label, environment_reason=environment.reason,
        rail_count=rails, demod_reason=result.reason,
        analysis=dict(version=VERSION, release_id=release_id,
            source_sha256=digest, frame=asdict(frame),
            demod=asdict(envelope), constellation=asdict(constellation),
            environment=environment.to_dict()))


def off_violation(summary):
    """Why an OFF attempt's replay is not a clean OFF, or None if it is clean.

    An OFF capture has no source, so any of these means the transmitter was not
    actually off: the receiver locked onto something, symbols were recovered, or
    the frame carries rails.  This is the production OFF guard the controller
    runs -- the same call the tests' stand-in makes, on real bytes.
    """
    if summary.rail_count:
        return 'the OFF frame carries %d rails' % summary.rail_count
    if summary.carrier_sync or summary.timing_sync:
        return 'the OFF frame synchronized'
    if (summary.reference_sync is True or summary.bit_errors is not None
            or summary.ber is not None or (summary.compared_bits is not None
            and summary.compared_bits > 0)):
        return 'the OFF frame recovered reference bits'
    if summary.symbol_count:
        return 'the OFF frame produced %d constellation symbols' % summary.symbol_count
    return None
