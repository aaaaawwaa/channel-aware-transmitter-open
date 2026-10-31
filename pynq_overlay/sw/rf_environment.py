"""Receive-only finite-window rules with frozen, provenance-bound normal baseline.

All features are dimensionless except code RMS, frequency, sample positions and
the explicit 68.27 microsecond observation window. No calibrated confidence,
SNR, front-end linearity or continuous environmental status is asserted.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math

import numpy as np

from rf_bpsk_pipeline import BpskEnvelope
from rf_bpsk_receiver import VERSION as BPSK_VERSION
from rf_rx_types import RxIqFrame
from rf_teaching_profile import teaching_capability

VERSION = 'rf-window-environment-v4'
BASELINE_SCHEMA = 1
# Not a second copy of the domain: the teaching profile's single source decides the
# attenuation, so a frozen baseline built at another value is refused in __post_init__.
FIXED_RF = tuple(teaching_capability().rf_parameters)
IDENTITY_FIELDS = ('capture_id','release_id','connection_generation',
                   'config_revision','completed_at_monotonic')
FEATURE_NAMES = ('window_us','rms_codes','crest_ratio','q999_ratio',
    'tone_concentration','tone_excess','tone_frequency_hz','pulse_sample_fraction',
    'pulse_longest_samples','pulse_start_sample','pulse_end_sample_exclusive',
    'sign_balance')


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _frozen_pairs(value):
    return (type(value) is tuple and all(type(pair) is tuple and len(pair)==2
            and type(pair[0]) is str for pair in value)
            and len({pair[0] for pair in value})==len(value))


@dataclass(frozen=True)
class EnvironmentResult:
    capture_id: str
    release_id: object
    connection_generation: int
    config_revision: int
    completed_at_monotonic: float
    submitted_revision: int
    algorithm_version: str
    environment_version: str
    baseline_sha256: object
    label: str
    reason: str
    features: tuple = ()
    rule_evidence: tuple = ()

    def to_dict(self):
        out = {name:getattr(self,name) for name in self.__dataclass_fields__
               if name not in ('features','rule_evidence')}
        out.update(features=dict(self.features),rule_evidence=dict(self.rule_evidence))
        return out


@dataclass(frozen=True)
class EnvironmentBaseline:
    spectrum_envelope: tuple
    thresholds: tuple
    sources: tuple
    rf_config: tuple = FIXED_RF
    calibration_status: str = 'development'
    environment_version: str = VERSION
    algorithm_version: str = BPSK_VERSION
    schema_version: int = BASELINE_SCHEMA
    _sha256_cache: str = field(init=False,repr=False,compare=False)

    def __post_init__(self):
        if self.environment_version!=VERSION or self.algorithm_version!=BPSK_VERSION \
                or type(self.schema_version) is not int or self.schema_version!=BASELINE_SCHEMA:
            raise ValueError('unsupported frozen baseline version')
        if type(self.spectrum_envelope) is not tuple or len(self.spectrum_envelope)!=4096:
            raise ValueError('baseline requires 4096 frozen local spectrum values')
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1
               for v in self.spectrum_envelope):
            raise ValueError('baseline spectrum must be finite normalised power')
        if not _frozen_pairs(self.rf_config) or dict(self.rf_config)!=dict(FIXED_RF):
            raise ValueError('baseline RF domain mismatch')
        required = {'tone_concentration','tone_excess','tone_weak_excess','pulse_crest','pulse_outlier',
                    'minimum_rms_codes','pulse_max_fraction','pulse_min_samples',
                    'pulse_after_tone_min_samples','pulse_after_tone_min_density',
                    'pulse_plateau_outlier','pulse_plateau_min_samples','pulse_plateau_min_density'}
        if not _frozen_pairs(self.thresholds) or set(dict(self.thresholds))!=required:
            raise ValueError('baseline threshold schema mismatch')
        t=dict(self.thresholds)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in t.values()):
            raise ValueError('baseline thresholds must be positive finite numbers')
        if not 0<t['tone_concentration']<=1 or not 0<t['tone_excess']<=1 \
                or not 0<t['pulse_max_fraction']<=.1 or not 1<=t['pulse_min_samples']<=4096 \
                or not 1<=t['pulse_after_tone_min_samples']<=4096 \
                or not 0<t['pulse_after_tone_min_density']<=1:
            raise ValueError('baseline threshold bounds mismatch')
        if not 1<=t['pulse_plateau_min_samples']<=4096 or not 0<t['pulse_plateau_min_density']<=1:
            raise ValueError('baseline plateau bounds mismatch')
        if self.calibration_status not in ('development','frozen'):
            raise ValueError('unknown baseline calibration status')
        if type(self.sources) is not tuple or not self.sources:
            raise ValueError('baseline needs frozen TRAIN provenance')
        for source in self.sources:
            if not _frozen_pairs(source):
                raise ValueError('baseline source must be frozen')
            _validate_source(dict(source))
        # All public fields are immutable, validated scalar tuples. Hash once on
        # construction; dataclasses.replace reconstructs and rehashes changed data.
        object.__setattr__(self,'_sha256_cache',_digest(self._body()))

    def _body(self):
        return dict(schema_version=self.schema_version,environment_version=self.environment_version,
            algorithm_version=self.algorithm_version,calibration_status=self.calibration_status,
            rf_config=dict(self.rf_config),spectrum_envelope=list(self.spectrum_envelope),
            thresholds=dict(self.thresholds),sources=[dict(s) for s in self.sources])

    @property
    def sha256(self):
        return self._sha256_cache

    def to_dict(self):
        return dict(self._body(),sha256=self.sha256)

    @classmethod
    def from_dict(cls,value):
        if type(value) is not dict:
            raise ValueError('baseline must be JSON object')
        expected = {'schema_version','environment_version','algorithm_version','calibration_status',
                    'rf_config','spectrum_envelope','thresholds','sources','sha256'}
        if set(value)!=expected:
            raise ValueError('baseline schema fields mismatch')
        try:
            result=cls(tuple(value['spectrum_envelope']),tuple(sorted(value['thresholds'].items())),
                tuple(tuple(sorted(s.items())) for s in value['sources']),
                tuple(sorted(value['rf_config'].items())),value['calibration_status'],
                value['environment_version'],value['algorithm_version'],value['schema_version'])
        except (TypeError,AttributeError,KeyError) as exc:
            raise ValueError('malformed baseline') from exc
        if value['sha256']!=result.sha256:
            raise ValueError('baseline canonical SHA mismatch')
        return result


def _validate_source(source):
    if set(source)!={'iq_sha256','session_id','source_id','split'} or source.get('split')!='train':
        raise ValueError('baseline may use only explicitly grouped TRAIN IQ')
    sha=source.get('iq_sha256')
    if type(sha) is not str or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('source needs full IQ SHA256')
    if any(type(source.get(k)) is not str or not source[k] or len(source[k])>256
           for k in ('session_id','source_id')):
        raise ValueError('source needs bounded session/source identifiers')


def _domain_error(frame,envelope,rf_config):
    if envelope.algorithm_version!=BPSK_VERSION or envelope.result.algorithm_version!=BPSK_VERSION:
        return 'ALGORITHM_MISMATCH'
    if any(getattr(frame,k)!=getattr(envelope.result,k) for k in IDENTITY_FIELDS):
        return 'IDENTITY_MISMATCH'
    if (not frame.release_id or type(frame.connection_generation) is not int
            or type(frame.config_revision) is not int
            or type(frame.completed_at_monotonic) not in (int,float)
            or not math.isfinite(frame.completed_at_monotonic)):
        return 'INVALID_IDENTITY'
    submitted=envelope.submitted
    if type(submitted.revision) is not int or submitted.revision<0:
        return 'INVALID_SUBMITTED_REVISION'
    if (submitted.decim!=frame.decimation or submitted.rx_len!=frame.samples
            or submitted.input_fs_hz!=frame.sample_rate_hz):
        return 'ACQUISITION_MISMATCH'
    config=submitted.config
    if (frame.samples!=4096 or frame.ratio!=0 or frame.sample_rate_hz!=60000000
            or config.mode!='BPSK' or config.shaping!='RECT'
            or type(config.symbol_rate_hz) is not int or config.symbol_rate_hz!=3000000):
        return 'UNSUPPORTED_DOMAIN'
    if type(rf_config) is not dict or any(type(rf_config.get(k)) is not int
            or rf_config[k]!=v for k,v in FIXED_RF):
        return 'RF_DOMAIN_MISMATCH'
    return None


def _features(frame):
    iq=np.frombuffer(frame.payload,dtype='<i2').reshape(-1,2)
    if np.any(iq<-2048) or np.any(iq>2047):
        return None,None,None,'INVALID_IQ'
    if np.any(iq==-2048) or np.any(iq==2047):
        return None,None,None,'CLIPPED'
    z=iq[:,0].astype(float)+1j*iq[:,1].astype(float)
    z-=np.mean(z)
    magnitude=np.abs(z)
    rms=float(np.sqrt(np.mean(magnitude*magnitude)))
    if rms<1.:
        return None,None,None,'NO_ENERGY'
    median=float(np.median(magnitude))
    if median<1.:
        return None,None,None,'LOW_ENERGY'
    normalized=magnitude/median
    power=np.abs(np.fft.fft(z*np.hanning(len(z))))**2
    power[0]=0.
    power/=float(np.sum(power))
    local=sum(np.roll(power,k) for k in range(-2,3))
    features=dict(window_us=frame.samples/frame.output_fs_hz*1e6,rms_codes=rms,
        crest_ratio=float(np.max(normalized)),q99_ratio=float(np.quantile(normalized,.99)),
        q999_ratio=float(np.quantile(normalized,.999)),
        tone_concentration=float(np.max(local)))
    return features,local,normalized,None


def _reliable_bpsk(envelope):
    r=envelope.result
    bits=r.bits_preview
    balance=sum(bits)/len(bits) if bits else .5
    reliable=(r.carrier_sync and r.timing_sync and len(bits)>=32
              and .15<=balance<=.85 and r.carrier_coherence is not None
              and r.carrier_coherence>=.70 and r.timing_quality is not None
              and r.timing_quality>=.55)
    return bool(reliable),float(balance)


def fit_baseline(frames,envelopes,rf_config,sources,*,calibration_status='development'):
    """Fit a normal envelope from declared TRAIN only; VAL/TEST remain external."""
    if not frames or not len(frames)==len(envelopes)==len(sources):
        raise ValueError('nonempty corresponding TRAIN frames/envelopes/sources required')
    spectral=[];crest=[];q99=[];q999=[];rms=[]; frozen_sources=[]
    for frame,envelope,source in zip(frames,envelopes,sources):
        _validate_source(source)
        if source['iq_sha256']!=hashlib.sha256(frame.payload).hexdigest():
            raise ValueError('TRAIN source IQ SHA mismatch')
        why=_domain_error(frame,envelope,rf_config)
        if why or not _reliable_bpsk(envelope)[0]:
            raise ValueError('unqualified normal TRAIN frame: %s' % (why or 'BPSK_NOT_RELIABLE'))
        f,p,_,why=_features(frame)
        if why or f['rms_codes']<16:
            raise ValueError('invalid normal TRAIN energy/rail values')
        spectral.append(p);crest.append(f['crest_ratio']);q99.append(f['q99_ratio'])
        q999.append(f['q999_ratio']);rms.append(f['rms_codes'])
        frozen_sources.append(tuple(sorted(source.items())))
    # Five-bin envelope absorbs periodic normal PRBS lines and modest CFO bin movement.
    upper=np.max(np.asarray(spectral),axis=0)
    upper=np.maximum.reduce([np.roll(upper,k) for k in range(-8,9)])
    thresholds=dict(tone_concentration=max(.15,min(.95,float(np.max(upper))*1.8+.025)),
        tone_excess=.18,tone_weak_excess=.025,pulse_crest=max(1.20,max(crest)+.08),
        pulse_outlier=max(1.30,max(q999)+.10),minimum_rms_codes=16.,
        pulse_max_fraction=.1,pulse_min_samples=3.,
        pulse_after_tone_min_samples=8.,pulse_after_tone_min_density=.30,
        pulse_plateau_outlier=max(1.30,max(q99)+.08),
        pulse_plateau_min_samples=16.,pulse_plateau_min_density=.35)
    return EnvironmentBaseline(tuple(float(v) for v in upper),tuple(sorted(thresholds.items())),
        tuple(frozen_sources),FIXED_RF,calibration_status)


def _local_event_group(indices,minimum_count,minimum_density,maximum_span=256):
    """Return one supported group; counts and density must refer to that group."""
    groups=np.split(indices,np.flatnonzero(np.diff(indices)>32)+1) if len(indices) else []
    supported=[group for group in groups if (maximum_span is None or group[-1]-group[0]+1<=maximum_span)
        and len(group)>=minimum_count
        and len(group)/float(group[-1]-group[0]+1)>=minimum_density]
    return max(supported,key=len) if supported else None


def analyze_environment(frame,envelope,rf_config,baseline=None):
    """Diagnose this receive window; TX truth/reference seed has no authority."""
    if not isinstance(frame,RxIqFrame) or not isinstance(envelope,BpskEnvelope):
        raise TypeError('expected immutable RxIqFrame and BpskEnvelope')
    base=dict((k,getattr(frame,k)) for k in IDENTITY_FIELDS)
    if type(base['completed_at_monotonic']) not in (int,float) \
            or not math.isfinite(base['completed_at_monotonic']):
        base['completed_at_monotonic']=None
    base.update(submitted_revision=envelope.submitted.revision,
        algorithm_version=BPSK_VERSION,environment_version=VERSION,
        baseline_sha256=baseline.sha256 if isinstance(baseline,EnvironmentBaseline) else None)
    def done(label,reason,features=None,rules=None):
        return EnvironmentResult(**dict(base,label=label,reason=reason,
            features=tuple(sorted((features or {}).items())),
            rule_evidence=tuple(sorted((rules or {}).items()))))
    why=_domain_error(frame,envelope,rf_config)
    if why: return done('INVALID',why)
    f,local,normalized,why=_features(frame)
    if why: return done('INVALID',why)
    if f['rms_codes']<16.: return done('INVALID','LOW_ENERGY',f)
    if baseline is None: return done('UNKNOWN','BASELINE_MISSING',f)
    if not isinstance(baseline,EnvironmentBaseline): return done('INVALID','BASELINE_INVALID',f)
    t=dict(baseline.thresholds)
    if f['rms_codes']<t['minimum_rms_codes']: return done('INVALID','LOW_ENERGY',f)
    excess=local-np.asarray(baseline.spectrum_envelope)
    bin_index=int(np.argmax(excess))
    f.update(tone_excess=float(excess[bin_index]),
        tone_frequency_hz=float(np.fft.fftfreq(4096,1./60000000)[bin_index]))
    narrow=bool(local[bin_index]>t['tone_concentration'] and excess[bin_index]>t['tone_excess'])
    weak_tone=bool(excess[bin_index]>t['tone_weak_excess'])
    pulse_normalized=normalized
    if weak_tone:
        # Remove only the measured sinusoid before looking for sparse amplitude events.
        # A beating tone otherwise creates short peaks that masquerade as impulses.
        iq=np.frombuffer(frame.payload,dtype='<i2').reshape(-1,2)
        z=iq[:,0].astype(float)+1j*iq[:,1].astype(float);z-=np.mean(z)
        nfft=4096*16
        fine=np.abs(np.fft.fft(z*np.hanning(4096),nfft))**2
        bins=(bin_index*16+np.arange(-48,49))%nfft
        chosen=int(bins[np.argmax(fine[bins])])
        carrier=np.exp(2j*np.pi*chosen*np.arange(4096)/nfft)
        residual=z-np.mean(z*np.conjugate(carrier))*carrier
        magnitude=np.abs(residual);median=float(np.median(magnitude))
        if median<1.:return done('UNKNOWN','TONE_WITHOUT_RELIABLE_RESIDUAL',f)
        pulse_normalized=magnitude/median
    outliers=pulse_normalized>t['pulse_outlier']
    indices=np.flatnonzero(outliers)
    edges=np.diff(np.concatenate(([False],outliers,[False])).astype(int))
    starts=np.flatnonzero(edges==1);ends=np.flatnonzero(edges==-1)
    longest=int(np.max(ends-starts)) if len(starts) else 0
    share=float(len(indices)/4096)
    f.update(pulse_sample_fraction=share,pulse_longest_samples=longest,
        pulse_start_sample=int(indices[0]) if len(indices) else None,
        pulse_end_sample_exclusive=int(indices[-1])+1 if len(indices) else None)
    # Normal capture noise can interrupt a real 80-sample event. Group nearby outliers
    # without letting separated peaks anywhere in the frame become one pulse.
    # Sinusoid fitting leaves dispersed residual peaks on the actual RF chain.
    # Require a dense local event after that subtraction; original amplitude
    # events retain their independently calibrated crest/outlier criterion.
    minimum_count=t['pulse_after_tone_min_samples'] if weak_tone else t['pulse_min_samples']
    minimum_density=t['pulse_after_tone_min_density'] if weak_tone else 0.
    best=_local_event_group(indices,minimum_count,minimum_density)
    local_count=len(best) if best is not None else 0
    local_span=int(best[-1]-best[0]+1) if best is not None else 0
    local_density=local_count/float(local_span) if local_span else 0.
    f.update(pulse_local_span_samples=local_span,pulse_local_density=local_density)
    pulse_crest=float(np.max(pulse_normalized))
    f['pulse_crest_ratio']=pulse_crest
    pulse=bool(pulse_crest>t['pulse_crest'] and 0<share<=t['pulse_max_fraction']
               and local_count>=minimum_count)
    # A real pulse can create a sustained moderate plateau while only a few
    # samples cross the extreme crest threshold. Require a dense, longer local
    # event against the normal TRAIN q99 envelope; do not apply this to tone fits.
    plateau_mask=normalized>t['pulse_plateau_outlier']
    plateau_indices=np.flatnonzero(plateau_mask)
    plateau_group=_local_event_group(plateau_indices,t['pulse_plateau_min_samples'],
                                      t['pulse_plateau_min_density']) if not weak_tone else None
    plateau_share=float(len(plateau_indices)/4096)
    plateau_count=len(plateau_group) if plateau_group is not None else 0
    plateau_span=int(plateau_group[-1]-plateau_group[0]+1) if plateau_group is not None else 0
    plateau_density=plateau_count/float(plateau_span) if plateau_span else 0.
    plateau=bool(plateau_group is not None and 0<plateau_share<=t['pulse_max_fraction'])
    plateau_groups=np.split(plateau_indices,np.flatnonzero(np.diff(plateau_indices)>32)+1) \
        if len(plateau_indices) else []
    wide_plateau=any(group[-1]-group[0]+1>256
        and len(group)>=t['pulse_plateau_min_samples']
        and len(group)/float(group[-1]-group[0]+1)>=t['pulse_plateau_min_density']
        for group in plateau_groups)
    plateau_outside=bool(not weak_tone and (plateau_share>t['pulse_max_fraction'] or wide_plateau))
    pulse=pulse or plateau
    f.update(pulse_plateau_count=plateau_count,pulse_plateau_span_samples=plateau_span,
             pulse_plateau_density=plateau_density,pulse_plateau_sample_fraction=plateau_share)
    reliable,balance=_reliable_bpsk(envelope);f['sign_balance']=balance
    rules=dict(narrowband_triggered=narrow,weak_spectral_excess=weak_tone,
        pulse_triggered=pulse,bpsk_reliable=reliable,pulse_local_count=local_count,
        pulse_local_span_samples=local_span,pulse_local_density=local_density,
        pulse_min_samples=minimum_count,pulse_min_density=minimum_density,
        pulse_plateau_triggered=plateau,pulse_plateau_threshold=t['pulse_plateau_outlier'],
        pulse_plateau_outside_support=plateau_outside,
        pulse_plateau_min_samples=t['pulse_plateau_min_samples'],
        pulse_plateau_min_density=t['pulse_plateau_min_density'],
        spectral_concentration_threshold=t['tone_concentration'],
        spectral_excess_threshold=t['tone_excess'],pulse_crest_threshold=t['pulse_crest'],
        pulse_outlier_threshold=t['pulse_outlier'],pulse_fraction_limit=t['pulse_max_fraction'])
    if narrow and pulse: return done('UNKNOWN','COMPOUND_RULES',f,rules)
    if narrow: return done('NARROWBAND','SPECTRAL_EXCESS',f,rules)
    if weak_tone:return done('UNKNOWN','WEAK_SPECTRAL_ANOMALY',f,rules)
    if plateau_outside:return done('UNKNOWN','AMPLITUDE_OUTSIDE_RULE_SUPPORT',f,rules)
    if pulse: return done('PULSE','SPARSE_AMPLITUDE_OUTLIERS',f,rules)
    if not reliable: return done('UNKNOWN','BPSK_NOT_RELIABLE',f,rules)
    if share>t['pulse_max_fraction'] or f['crest_ratio']>t['pulse_crest']:
        return done('UNKNOWN','AMPLITUDE_OUTSIDE_RULE_SUPPORT',f,rules)
    return done('CLEAR','NO_COVERED_ANOMALY_IN_WINDOW',f,rules)
