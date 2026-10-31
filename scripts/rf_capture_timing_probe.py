"""Finite instrumented physical capture evidence; not formal TEST.

Clock brackets describe control API calls, with observation overhead. They do
not describe RF settling, sample edges, or gain during the captured window.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
FIXED_RF=dict(lo_hz=80000000,sample_rate_hz=60000000,rx_bandwidth_hz=30000000,
    tx_bandwidth_hz=30000000,attenuation_mdB=40000,phy_mode_1r1t=0,idelay_en=1,idelay_tap=29)
TIMING_SCOPE='monotonic_api_brackets_not_sample_edges'
TIME_FIELDS=('tx_on_begin_ns','tx_on_end_ns','rx_arm_begin_ns','rx_arm_end_ns',
    'drain_begin_ns','drain_end_ns','tx_off_begin_ns','tx_off_end_ns')
IDENTITY=('capture_id','connection_generation','config_revision','request_id','daemon_timestamp_ns')
# The bounded single-variable comparison: the same RF/scene/receive configuration
# at a zero and a one-AGC-update settling wait.  The ceiling matches
# RF_SETTLE_WAIT_MAX_US in rf_daemon.h and SETTLE_WAIT_MAX_US in the client.
SETTLE_WAIT_LEVELS=(0,1000)
SETTLE_WAIT_MAX_US=5000
# The pre-declared per-frame DEADLINE for a wait's measured duration.  The request
# is capped at SETTLE_WAIT_MAX_US by the daemon, but the MEASURED wait is whatever
# the scheduler delivered, and those are different quantities: an overshoot is
# allowed and REPORTED (settle_deviation_ns) rather than rounded away, up to this
# deadline.  Beyond it the frame is not that level, and the comparison stops --
# a sleep that overshot its own ceiling by that much is not a measurement of the
# settling behaviour, it is a measurement of the machine's load.
SETTLE_WAIT_DEADLINE_NS=SETTLE_WAIT_MAX_US*1000
# What the experiment is APPROVED to be.  The frame cap is a property of the
# approved design, not something derived from whatever a caller passes in: a
# third level, a missing zero, a swapped-out 1000 or a duplicate must be refused
# BEFORE a session exists, because a session is hardware.
APPROVED_WAIT_LEVELS=frozenset((0,1000))
APPROVED_SESSIONS=2
APPROVED_FRAMES=APPROVED_SESSIONS*2*len(APPROVED_WAIT_LEVELS)
# The permitted level SET and the execution ORDER are separate on purpose.
# Session 0 runs low-then-high and session 1 high-then-low, so a systematic drift
# across a session's life (warm-up, thermal, gain history) does not line up with
# one wait level and masquerade as a wait effect.  Eight frames cannot settle that
# question either way -- the ordering limitation is reported, not removed.
SESSION_ORDER=((0,1000),(1000,0))


def window_statistics(payload):
    if type(payload) is not bytes or len(payload)!=16384:raise ValueError('exact 4096-sample raw payload required')
    iq=np.frombuffer(payload,dtype='<i2').reshape(-1,2)
    if np.any(iq<-2048) or np.any(iq>2047):raise ValueError('invalid signed12 IQ')
    rails=(iq==-2048)|(iq==2047)
    return dict(i_min=int(iq[:,0].min()),i_max=int(iq[:,0].max()),q_min=int(iq[:,1].min()),
        q_max=int(iq[:,1].max()),rail_components=int(rails.sum()),rail_samples=int(rails.any(axis=1).sum()),
        raw_complex_rms=float(np.sqrt(np.mean(np.sum(iq.astype(np.float64)**2,axis=1)))),
        samples=4096,scope='signed12_raw_components_not_dc_removed')


def _json(path):
    if path.is_symlink() or not path.is_file() or not 0<path.stat().st_size<=2*1024*1024:
        raise ValueError('bounded regular diagnostic JSON required')
    value=json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not dict:raise ValueError('diagnostic object required')
    return value


def _same_iq_replay(record_path,baseline):
    """The EXISTING offline replay, reused rather than reimplemented.

    It re-derives the BPSK demodulation, the constellation and the environment
    from the saved IQ and compares them against what the record stored, against
    the frozen baseline.  Imported lazily so a caller that only validates a
    timing trace does not drag the whole DSP chain in, and so a test can replace
    exactly this boundary.  A frame that did not sync replays to no constellation
    points and no BER -- reproducing the REFUSAL is what a pass means here, not
    a successful demodulation."""
    root=Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0,str(root))
    from scripts.rf_environment_verify import verify_record
    return verify_record(record_path,baseline)


def _local_record(record,records_root):
    """Where the saved record can be READ from.

    On the board that is the path the sidecar carries.  A host that downloaded
    the evidence holds it under its own `records/` directory instead, so it passes
    that root and the record is located by its record_id -- the same identity the
    receipt was written with, so repointing cannot silently pick a different
    frame's record."""
    if records_root is None:
        return record.get('path')
    rid=record.get('record_id')
    if (type(rid) is not str or not rid or rid in ('.','..')
            or '/' in rid or '\\' in rid):
        raise ValueError('the record names no usable record_id')
    return Path(records_root)/rid


def validate_diagnostic(row,output,release,kind,settle_wait_us=None,baseline=None,
                        records_root=None):
    """Cross-check persisted trace, header/handoff and original IQ independently.

    `settle_wait_us=None` validates a frame from the minor-4 daemon, which cannot
    report a settling wait at all -- the shape the already-archived four frames
    have.  An int validates a frame from the minor-5 daemon, which always reports
    the field, including a zero wait.  The two shapes are kept apart on purpose:
    accepting one as the other would let a frame without a provable wait pass as
    a waiting frame.
    """
    daemon,minor=('1.0.2',4) if settle_wait_us is None else ('1.0.3',5)
    try:
        if type(row) is not dict or row.get('release_id')!=release or row.get('egress_safe') is not True:
            raise ValueError('safe same-release capture required')
        tag=Path(row['iq_file']).stem;output=Path(output)
        if not tag or len(tag)>128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in tag):
            raise ValueError('bounded diagnostic tag required')
        expected={'iq_file':tag+'.iq','metadata_file':tag+'.json','environment_file':tag+'.environment.json','timing_file':tag+'.timing.json'}
        if any(row.get(k)!=v for k,v in expected.items()):raise ValueError('diagnostic artifact path mismatch')
        raw_path=output/(tag+'.iq')
        if raw_path.is_symlink() or not raw_path.is_file() or raw_path.stat().st_size!=16384:
            raise ValueError('original bounded raw IQ required')
        raw=raw_path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        meta=_json(output/(tag+'.json'));side=_json(output/(tag+'.environment.json'));diag=_json(output/(tag+'.timing.json'))
        identity=tuple(meta[k] for k in IDENTITY)
        if (not all(v is not None for v in identity) or meta.get('release_id')!=release
                or any(type(meta[k]) is not int or meta[k]<0 for k in IDENTITY[1:])
                or row.get('capture_id')!=identity[0] or any(value.get('iq_sha256')!=digest for value in (row,meta,side,diag))
                or diag.get('frame')!={k:meta[k] for k in IDENTITY}
                or side.get('capture_id')!=identity[0] or side.get('release_id')!=release
                or side.get('environment')!=row.get('environment') or side.get('rf_config')!=FIXED_RF
                or diag.get('rf_config')!=FIXED_RF or diag.get('scope')!=TIMING_SCOPE):
            raise ValueError('diagnostic identity, original IQ or fixed RF mismatch')
        if meta['request_id']>0x7FFFFFFF or meta['daemon_timestamp_ns']>0xFFFFFFFFFFFFFFFF:
            raise ValueError('original wire identity exceeds protocol bounds')
        scene=side.get('scenario_declaration') or {}
        if scene.get('truth')!=kind or scene.get('prbs7_seed')!=1 or scene.get('amplitude')!=60:
            raise ValueError('actual scene declaration mismatch')
        # The DISPLAYED environment and the SAVED experiment are evidence, not
        # decoration: a frame whose result never reached the screen, or whose
        # record disagrees with the sidecar, is not a frame this comparison can
        # use.  The record is re-verified through the existing reader rather than
        # trusted, so a record whose bytes were truncated or edited after saving
        # fails here instead of passing on the probe's own partial checks.
        display=side.get('display_environment')
        declared=side.get('environment') or {}
        if (type(display) is not dict or display.get('current') is not True
                or display.get('capture_id')!=identity[0]
                or display.get('label')!=declared.get('label')
                or display.get('reason')!=declared.get('reason')):
            raise ValueError('the displayed environment is not this frame')
        record=side.get('record')
        if type(record) is not dict or record.get('capture_id')!=identity[0]:
            raise ValueError('a saved experiment record for this frame is required')
        local_rec=Path(__file__).resolve().parents[1]/'pynq_overlay/sw'
        if (local_rec/'rf_experiment_record.py').is_file() and str(local_rec) not in sys.path:
            sys.path.insert(0,str(local_rec))
        from rf_experiment_record import read_record
        try:
            saved=read_record(_local_record(record,records_root))
        except Exception as exc:
            # Any way the reader can fail means the same thing to a caller: this
            # record is not acceptable evidence.  Converted so a missing or
            # truncated file is a validation failure like every other one, rather
            # than a different exception type escaping the checker.
            raise ValueError('the saved experiment record could not be verified: %s'
                             % (exc,)) from exc
        if (saved['receipt'].get('iq_sha256')!=digest
                or saved['receipt'].get('capture_id')!=identity[0]
                or saved['receipt'].get('bytes')!=len(raw)
                or saved['metadata'].get('capture_id')!=identity[0]):
            raise ValueError('the saved experiment record does not match this frame')
        # The record is also REPLAYED from its own IQ.  A record that merely
        # survives a hash check has not been shown to describe the same physics.
        replay=None
        if baseline is not None:
            try:
                replay=_same_iq_replay(_local_record(record,records_root),baseline)
            except Exception as exc:
                raise ValueError('the same-IQ replay could not be performed: %s'
                                 % (exc,)) from exc
            if replay.get('passed') is not True:
                raise ValueError('the same-IQ replay did not reproduce this frame: %s'
                                 % (replay.get('mismatches'),))
            if replay.get('label')!=declared.get('label'):
                raise ValueError('the replayed environment label disagrees with the record')
        required=dict(samples=4096,bytes=16384,sample_rate_hz=60000000,ratio=0,decimation=1,output_fs_hz=60000000,
            physical=True,op='loopback.physical.capture',pre_io_ok=True,post_io_ok=True,post_rx_armed=False,post_tx_source_selected=False)
        handoff=meta.get('handoff_frame') or {}
        if (any(meta.get(k)!=v for k,v in required.items())
                or any(type(meta.get(k)) is not int for k,v in required.items() if type(v) is int)
                or any(type(meta.get(k)) is not bool for k,v in required.items() if type(v) is bool)
                or meta.get('handoff_frame_matches') is not True
                or any(handoff.get(k)!=meta[k] for k in (*IDENTITY,*required))):
            raise ValueError('original acquisition header/handoff/closed paths mismatch')
        status=meta.get('capture_status') or {}
        if status.get('state')!='done' or status.get('close_confirmed') is not True or status.get('cancelled') is not False:
            raise ValueError('capture did not close successfully')
        info=diag.get('timing') or {};trace=info.get('last_capture_timing') or {}
        if (info.get('release_id')!=release or info.get('daemon')!=daemon
                or info.get('protocol_major')!=1 or info.get('protocol_minor')!=minor or info.get('reason') is not None
                or trace.get('available') is not True or trace.get('scope')!=TIMING_SCOPE
                or trace.get('capture_request_id')!=meta['request_id'] or trace.get('capture_timestamp_ns')!=meta['daemon_timestamp_ns']
                or trace.get('samples')!=4096 or trace.get('ratio')!=0):
            raise ValueError('same-frame instrumented timing required')
        if any(type(trace.get(k)) is not int for k in ('capture_request_id','capture_timestamp_ns','samples','ratio')):
            raise ValueError('timing identity fields must be integers')
        times=[trace.get(k) for k in TIME_FIELDS]
        if (any(type(t) is not int or not 0<t<=0xFFFFFFFFFFFFFFFF for t in times)
                or times!=sorted(times) or meta['daemon_timestamp_ns']<times[-1]):
            raise ValueError('invalid or unordered API clock brackets')
        # Validate the independent idle observations through the production pure
        # schema validator; this constructs neither a client nor a hardware owner.
        local_sw=Path(__file__).resolve().parents[1]/'pynq_overlay/sw'
        if (local_sw/'rf_status_client.py').is_file() and str(local_sw) not in sys.path:
            sys.path.insert(0,str(local_sw))
        from rf_status_client import _gain_observation,StatusProtocolError
        for name in ('before_gain','after_gain'):
            try:gain=_gain_observation(diag.get(name) or {})
            except StatusProtocolError as exc:raise ValueError('invalid independent idle gain') from exc
            if (gain['release_id']!=release or gain['daemon']!=daemon or gain['protocol_minor']!=minor
                    or gain['gain_observation'] is None):raise ValueError('independent idle gain unavailable')
            raw_gain=gain['gain_observation']
            if any(type(raw_gain.get(k)) is not int or not 0<raw_gain[k]<=0xFFFFFFFFFFFFFFFF
                    for k in ('started_at_monotonic_ns','finished_at_monotonic_ns')):
                raise ValueError('idle gain timing unavailable')
            if (name=='before_gain' and raw_gain['finished_at_monotonic_ns']>times[0]
                    or name=='after_gain' and raw_gain['started_at_monotonic_ns']<meta['daemon_timestamp_ns']):
                raise ValueError('idle gain observation is not before/after this capture')
        # The settling wait is minor 5 and must be PROVABLE: the row, the sidecar
        # and the trace all have to agree with the request, and the bracket has to
        # sit inside the TX-on..RX-arm gap it was taken from.
        #
        # Every value is type-checked with `type(x) is int` rather than compared
        # with `!=`: `False != 0` is False and `0.0 != 0` is False, so an equality
        # test accepts a boolean or a float as a wait.
        def wait_field(where,name):
            value=where.get(name)
            if type(value) is not int or value<0:
                raise ValueError('%s must be a non-negative integer' % name)
            return value
        if settle_wait_us is None:
            if any(k in trace for k in ('settle_wait_requested_us','settle_begin_ns','settle_end_ns')):
                raise ValueError('a minor-4 frame must not report a settling wait')
            settle_actual=0
        else:
            if (wait_field(row,'settle_wait_requested_us')!=settle_wait_us
                    or wait_field(diag,'settle_wait_requested_us')!=settle_wait_us
                    or wait_field(trace,'settle_wait_requested_us')!=settle_wait_us):
                raise ValueError('settling wait request does not match this frame')
            if settle_wait_us:
                if 'settle_begin_ns' not in trace or 'settle_end_ns' not in trace:
                    raise ValueError('a requested wait must report BOTH brackets')
                begin=wait_field(trace,'settle_begin_ns');end=wait_field(trace,'settle_end_ns')
                if begin<=0 or end<begin or not times[1]<=begin<=end<=times[2]:
                    raise ValueError('settle bracket is outside the TX-on..RX-arm gap')
                settle_actual=end-begin
            else:
                if 'settle_begin_ns' in trace or 'settle_end_ns' in trace:
                    raise ValueError('a zero settling wait must not report a bracket')
                settle_actual=0
        statistics=window_statistics(raw)
        if diag.get('statistics')!=statistics:raise ValueError('raw statistics mismatch')
        return dict(identity=identity,iq_sha256=digest,statistics=statistics,
            settle_wait_requested_us=settle_wait_us or 0,settle_actual_ns=settle_actual,
            replay=replay,
            tx_to_rx_arm_lower_ns=max(0,times[2]-times[1]),tx_to_rx_arm_upper_ns=times[3]-times[0],
            tx_source_api_span_ns=times[-1]-times[0],drain_api_span_ns=times[5]-times[4],
            capture_window_ns=4096/60000000*1e9,scope=TIMING_SCOPE)
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('incomplete timing evidence') from exc


def campaign(factory,output,release):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'result.json'
    if path.exists():raise FileExistsError(str(path))
    rows,sessions,attempts,seen_identity,seen_iq=[],[],[],set(),set()
    cause=None
    for index,seed in enumerate((1,37)):
        session=None;owned=closed=ack=False;error=cleanup_error=close_error=None
        try:
            session=factory();initial=session.initial()
            if initial.get('safe') is not True or initial.get('release_id')!=release:
                raise RuntimeError('initial_not_safe_or_wrong_release')
            owned=True;session.setup(seed)
            for kind in ('normal','narrowband'):
                session.scene(kind);tag='s%02d-%s-00'%(index,kind)
                attempts.append(dict(tag=tag,truth=kind,session_index=index,reference_seed=seed))
                row=session.capture(tag,True)
                row.update(tag=tag,truth=kind,session_index=index,reference_seed=seed,
                    actual_scene_prbs7_seed=1,split='development-timing',origin='new instrumented physical TX1-RX1 capture')
                rows.append(row)
                checked=validate_diagnostic(row,output,release,kind);row['diagnostic_check']=checked
                identity=tuple(checked['identity']);digest=checked['iq_sha256']
                if identity in seen_identity or digest in seen_iq:raise ValueError('duplicate original frame')
                seen_identity.add(identity);seen_iq.add(digest)
                if checked['statistics']['rail_components'] or (row.get('environment') or {}).get('reason')=='CLIPPED':
                    cause='CLIPPED';break
        except Exception as exc:cause=type(exc).__name__;error=str(exc)
        finally:
            if owned:
                try:
                    cleanup=session.cleanup();closed=cleanup.get('safe') is True;ack=cleanup.get('disarm_ack') is True
                except Exception as exc:cleanup_error=str(exc)
            if session is not None:
                try:session.close()
                except Exception as exc:close_error=str(exc)
            if cause is None and (cleanup_error or close_error):cause='cleanup_unconfirmed'
            sessions.append(dict(index=index,reference_seed=seed,owned=owned,closed=closed,disarm_ack=ack,
                error=error,cleanup_error=cleanup_error,close_error=close_error,cause=cause))
        if cause is not None or error is not None:break
        if not closed or not ack:cause='close_unconfirmed';break
    result=dict(expected_release=release,scope='instrumented API brackets; not sample edges or AGC trajectory',
        split='development-timing',records=rows,sessions=sessions,attempts=attempts,stop_cause=cause,
        passed=cause is None and len(rows)==4 and len(sessions)==2 and all(s['closed'] and s['disarm_ack']
            and not any(s.get(k) for k in ('error','cleanup_error','close_error')) for s in sessions),
        formal_physical='NOT_RUN',human_acceptance='NOT_RUN',s2_stage_passed=False)
    with path.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    return result


def wait_campaign(factory,output,release,levels=SETTLE_WAIT_LEVELS,baseline=None):
    """The bounded single-variable comparison: two sessions x two scenes x the
    declared settling-wait levels.  The ONLY thing that varies across a scene is
    the wait, and every frame carries the trace that proves which wait it had.

    `baseline` is REQUIRED: every frame is replayed from its own IQ against the
    frozen environmental baseline, so a frame is accepted only when the physics
    it claims can be re-derived from the bytes that were saved.  A campaign
    without a baseline would be reporting frames nobody re-checked."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    path=output/'result.json'
    if path.exists():raise FileExistsError(str(path))
    if baseline is None:
        raise ValueError('the settle comparison requires the frozen baseline for '
                         'per-frame same-IQ replay; none was supplied')
    # Refused BEFORE any factory call: a session is hardware, and an unapproved
    # design must not reach a part to discover that it is unapproved.  So this is
    # a set comparison against the approved set, NOT a range/sorted check -- a
    # range check happily accepted (0,1000,2000) and produced twelve frames.
    if (type(levels) is not tuple or len(levels)!=len(APPROVED_WAIT_LEVELS)
            or any(type(v) is not int for v in levels)
            or frozenset(levels)!=APPROVED_WAIT_LEVELS):
        raise ValueError(
            'the settle comparison is approved for exactly the levels %s, one '
            'frame per level per scene, %d sessions and %d frames; %r is a '
            'different experiment and is not approved'
            % (sorted(APPROVED_WAIT_LEVELS),APPROVED_SESSIONS,APPROVED_FRAMES,levels))
    levels=list(levels)
    rows,sessions,attempts,seen_identity,seen_iq=[],[],[],set(),set()
    cause=None
    for index,seed in enumerate((1,37)):
        session=None;owned=closed=ack=False;error=cleanup_error=close_error=None
        try:
            session=factory();initial=session.initial()
            if initial.get('safe') is not True or initial.get('release_id')!=release:
                raise RuntimeError('initial_not_safe_or_wrong_release')
            owned=True;session.setup(seed)
            for kind in ('normal','narrowband'):
                session.scene(kind)
                for level in SESSION_ORDER[index]:
                    tag='s%02d-%s-w%04d'%(index,kind,level)
                    attempts.append(dict(tag=tag,truth=kind,session_index=index,
                        reference_seed=seed,settle_wait_us=level))
                    row=session.capture(tag,True,level)
                    row.update(tag=tag,truth=kind,session_index=index,reference_seed=seed,
                        settle_wait_us=level,actual_scene_prbs7_seed=1,split='development-settle',
                        origin='new instrumented physical TX1-RX1 capture with a settling wait')
                    rows.append(row)
                    checked=validate_diagnostic(row,output,release,kind,level,baseline)
                    row['diagnostic_check']=checked
                    # Marked only once every check passed.  A frame that failed
                    # validation stays in `records` -- its evidence is retained and
                    # its error is in the session -- but it must not count as one of
                    # the approved frames.
                    row['validated']=True
                    row['replay_label']=checked['replay']['label']
                    identity=tuple(checked['identity']);digest=checked['iq_sha256']
                    if identity in seen_identity or digest in seen_iq:raise ValueError('duplicate original frame')
                    seen_identity.add(identity);seen_iq.add(digest)
                    # Requested and ACTUAL, both raw, plus their signed deviation.
                    # A wait is allowed to OVERSHOOT -- the sleep is the scheduler's
                    # to deliver, not the requester's to command -- but the raw
                    # deviation is recorded rather than rounded to the request, and
                    # an overshoot past the pre-declared deadline is not this level.
                    actual=checked['settle_actual_ns']
                    requested_ns=level*1000
                    row['settle_actual_ns']=actual
                    row['settle_deviation_ns']=actual-requested_ns
                    row['settle_deadline_ns']=SETTLE_WAIT_DEADLINE_NS
                    row['instrumentation_clock_reads']=10 if level else 8
                    if checked['statistics']['rail_components'] or (row.get('environment') or {}).get('reason')=='CLIPPED':
                        cause='CLIPPED';break
                    if level and actual < requested_ns:
                        # A wait that came up SHORT is not that level.  The original
                        # IQ and trace stay on disk, the level is marked unqualified
                        # and the comparison STOPS: rounding it up into a usable
                        # sample is exactly the false measurement this avoids.
                        row['settle_qualified']=False
                        cause='WAIT_SHORT';break
                    if actual > SETTLE_WAIT_DEADLINE_NS:
                        # Same rule from the other side: the wait is real, it is on
                        # disk, and it is reported -- it is just not a sample of the
                        # level that was asked for.
                        row['settle_qualified']=False
                        cause='WAIT_OVER_DEADLINE';break
                    row['settle_qualified']=True
                if cause is not None:break
        except Exception as exc:cause=type(exc).__name__;error=str(exc)
        finally:
            if owned:
                try:
                    cleanup=session.cleanup();closed=cleanup.get('safe') is True;ack=cleanup.get('disarm_ack') is True
                except Exception as exc:cleanup_error=str(exc)
            if session is not None:
                try:session.close()
                except Exception as exc:close_error=str(exc)
            if cause is None and (cleanup_error or close_error):cause='cleanup_unconfirmed'
            sessions.append(dict(index=index,reference_seed=seed,owned=owned,closed=closed,disarm_ack=ack,
                error=error,cleanup_error=cleanup_error,close_error=close_error,cause=cause))
        if cause is not None or error is not None:break
        if not closed or not ack:cause='close_unconfirmed';break
    evidence_complete=(cause is None and len(rows)==APPROVED_FRAMES
        and all(r.get('validated') is True for r in rows)
        and len(sessions)==APPROVED_SESSIONS
        and all(s['closed'] and s['disarm_ack']
            and not any(s.get(k) for k in ('error','cleanup_error','close_error')) for s in sessions))
    # Complete evidence and a VALID comparison are reported separately: eight
    # frames whose waits all came up short are complete evidence and a worthless
    # comparison, and collapsing the two would let the second hide behind the first.
    levels_seen=sorted({r['settle_wait_requested_us'] for r in rows})
    comparison_valid=evidence_complete and levels_seen==sorted(APPROVED_WAIT_LEVELS)
    result=dict(expected_release=release,
        scope='bounded single-variable settling wait; API brackets not sample edges',
        split='development-settle',levels=sorted(APPROVED_WAIT_LEVELS),
        approved_frames=APPROVED_FRAMES,approved_sessions=APPROVED_SESSIONS,
        session_order=[list(o) for o in SESSION_ORDER],
        ordering_limitation=('two sessions, low-then-high then high-then-low; eight '
            'frames cannot separate a wait effect from drift across a session'),
        records=rows,sessions=sessions,attempts=attempts,stop_cause=cause,
        evidence_complete=evidence_complete,comparison_valid=comparison_valid,
        passed=evidence_complete,
        formal_physical='NOT_RUN',human_acceptance='NOT_RUN',s2_stage_passed=False)
    with path.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,allow_nan=False)
    return result


def live_factory(expected,output):
    from rf_environment_board_session import EnvironmentSession
    class TimedClient:
        def __init__(self,inner,wait):self.inner=inner;self.wait=wait
        def __getattr__(self,name):return getattr(self.inner,name)
        def capture(self,args,physical=False):
            args=dict(args)
            if physical:
                args['observe_timing']=True
                # The wait is announced per frame by the session below; a zero
                # wait sends no field at all, so the zero level stays the plain
                # instrumented request it has always been.
                if self.wait[0]:args['settle_wait_us']=self.wait[0]
            return self.inner.capture(args,physical=physical)
    class TimingSession(EnvironmentSession):
        def __init__(self):
            super().__init__(expected,output)
            self._wait=[0]
            self.client.client=TimedClient(self.client.client,self._wait)
        def capture(self,tag,physical,settle_wait_us=0):
            self._wait[0]=settle_wait_us
            diag=dict(scope=TIMING_SCOPE,settle_wait_requested_us=settle_wait_us,
                instrumentation=('8 clock reads; no inserted settling wait' if not settle_wait_us
                    else '10 clock reads; %d us requested settling wait before RX prepare'%settle_wait_us))
            try:
                diag['before_gain']=self.client.observe_rx_gain()
                row=super().capture(tag,physical)
                frame=self.client.frame
                diag['frame']={k:frame.to_dict()[k] for k in IDENTITY}
                diag['iq_sha256']=hashlib.sha256(frame.payload).hexdigest()
                diag['statistics']=window_statistics(frame.payload)
                diag['rf_config']=dict(self.app._rx_record_rf_config)
                diag['timing']=self.client.observe_capture_timing(frame)
                diag['after_gain']=self.client.observe_rx_gain()
                row['timing_file']=tag+'.timing.json'
                row['settle_wait_requested_us']=settle_wait_us
                return row
            except Exception as exc:diag.update(error_type=type(exc).__name__,error=str(exc));raise
            finally:
                with (self.output/(tag+'.timing.json')).open('x',encoding='utf-8') as f:json.dump(diag,f,indent=2,allow_nan=False)
    return TimingSession


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cable-confirmed',required=True,action='store_true')
    parser.add_argument('--wait-levels',default=None,
        help='comma-separated bounded settling-wait levels, e.g. 0,1000.  Omit to '
             'run the original four-frame timing campaign, which stays unchanged.')
    parser.add_argument('--baseline',default=None,type=Path,
        help='the frozen environmental baseline every frame is replayed against.  '
             'Required with --wait-levels: the settle comparison accepts no frame '
             'that has not been replayed from its own IQ.')
    args=parser.parse_args()
    if args.wait_levels is None:
        result=campaign(live_factory(args.expected_release,args.output),args.output,args.expected_release)
    else:
        if args.baseline is None:
            parser.error('--wait-levels requires --baseline: every settle frame is '
                         'replayed from its own IQ before it counts')
        try:levels=tuple(int(v) for v in args.wait_levels.split(','))
        except ValueError:parser.error('--wait-levels takes comma-separated integers')
        result=wait_campaign(live_factory(args.expected_release,args.output),args.output,
            args.expected_release,levels,args.baseline)
    print(json.dumps({k:v for k,v in result.items() if k not in ('records','attempts')},indent=2))
    raise SystemExit(0 if result['passed'] else 1)
