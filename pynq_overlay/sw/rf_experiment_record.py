"""Explicit bounded local evidence; no hardware, network or capture side effects.

A final receipt is the commit marker. Partial writes are never readable records.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import uuid

from rf_bpsk_pipeline import BpskEnvelope
from rf_rx_constellation import RxConstellation
from rf_rx_types import RxIqFrame

MAX_METADATA_BYTES = 131072
MAX_IQ_BYTES = 16384


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _summary(value):
    return value.to_dict() if value is not None and hasattr(value,'to_dict') else value


def _check_secrets(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if not isinstance(key,str) or any(word in key.lower() for word in ('password','token','credential')):
                raise ValueError('credential data not permitted in records')
            _check_secrets(item)
    elif isinstance(value,(list,tuple)):
        for item in value: _check_secrets(item)


def write_record(root_dir, frame, envelope, constellation, analysis=None,
                 model_result=None, rf_config=None, historical=False,
                 environment=None, scenario_declaration=None):
    """Save one frozen experiment under a unique ID; raise on incomplete saving.

    Caller submits this on a save thread, and captures the explicit historical flag
    before submission. This function never renews or reconstructs a live session.
    """
    if (not isinstance(frame,RxIqFrame) or not isinstance(envelope,BpskEnvelope)
            or not isinstance(constellation,RxConstellation) or type(historical) is not bool):
        raise TypeError('frozen frame, envelope, constellation and explicit bool required')
    keys=('capture_id','release_id','connection_generation','config_revision','completed_at_monotonic')
    if (any(getattr(envelope.result,k)!=getattr(frame,k) or getattr(constellation,k)!=getattr(frame,k) for k in keys)
            or constellation.submitted_revision!=envelope.submitted.revision
            or envelope.algorithm_version!=constellation.algorithm_version
            or envelope.result.algorithm_version!=envelope.algorithm_version):
        raise ValueError('record identity mismatch')
    for value in (analysis,model_result):
        if value is not None:
            summary=_summary(value)
            if not isinstance(summary,dict) or summary.get('capture_id')!=frame.capture_id:
                raise ValueError('analysis/model identity mismatch')
            if ('completed_at_monotonic' not in summary
                    or summary['completed_at_monotonic']!=frame.completed_at_monotonic
                    or any(k in summary and summary[k]!=getattr(frame,k) for k in keys)):
                raise ValueError('analysis/model identity mismatch')
    raw=frame.payload
    if environment is not None:
        from rf_environment import EnvironmentResult, VERSION
        if (not isinstance(environment,EnvironmentResult)
                or any(getattr(environment,k)!=getattr(frame,k) for k in keys)
                or environment.submitted_revision!=envelope.submitted.revision
                or environment.algorithm_version!=envelope.algorithm_version
                or environment.environment_version!=VERSION):
            raise ValueError('environment record identity mismatch')
    if not 4<=len(raw)<=MAX_IQ_BYTES:
        raise ValueError('IQ length outside record limit')
    metadata=dict(frame.to_dict(),format='s16_iq_le',iq_sha256=_sha(raw),
        schema_version=1,historical=historical,scope='finite snapshot experiment record',
        demod_result=asdict(envelope),constellation=constellation.to_dict(),
        analysis=_summary(analysis),model_result=_summary(model_result),
        rf_config=dict(rf_config or {}),control_result='NOT_RECORDED')
    if environment is not None or scenario_declaration is not None:
        metadata.update(environment=_summary(environment),
                        scenario_declaration=scenario_declaration)
    _check_secrets(metadata)
    data=json.dumps(metadata,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')
    if len(data)>MAX_METADATA_BYTES:
        raise ValueError('metadata length outside record limit')
    root=Path(root_dir)
    root.mkdir(parents=True,exist_ok=True)
    # Neither capture ID nor any input metadata is interpolated into a pathname.
    folder=root/('experiment-'+uuid.uuid4().hex)
    folder.mkdir(exist_ok=False)
    for name,content in (('frame.iq',raw),('metadata.json',data)):
        tmp=folder/(name+'.partial')
        with tmp.open('xb') as stream: stream.write(content)
        tmp.rename(folder/name)
    receipt=dict(schema_version=1,record_id=folder.name,capture_id=frame.capture_id,
        iq_sha256=_sha(raw),metadata_sha256=_sha(data),bytes=len(raw),
        path=str(folder.resolve()),historical=historical)
    marker=json.dumps(receipt,allow_nan=False,indent=2).encode('utf-8')
    tmp=folder/'receipt.json.partial'
    with tmp.open('xb') as stream: stream.write(marker)
    tmp.rename(folder/'receipt.json')
    return receipt


def _read_bounded(path, limit):
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or symbolic record component')
    with path.open('rb') as stream:
        data=stream.read(limit+1)
    if len(data)>limit:
        raise ValueError('record component too large')
    return data


def read_record(directory):
    """Return verified history bytes, never insert a result into current UI state."""
    folder=Path(directory)
    receipt=json.loads(_read_bounded(folder/'receipt.json',8192).decode('utf-8'))
    raw=_read_bounded(folder/'frame.iq',MAX_IQ_BYTES)
    data=_read_bounded(folder/'metadata.json',MAX_METADATA_BYTES)
    if _sha(raw)!=receipt.get('iq_sha256') or _sha(data)!=receipt.get('metadata_sha256'):
        raise ValueError('record hash mismatch')
    metadata=json.loads(data.decode('utf-8'))
    if (metadata.get('iq_sha256')!=_sha(raw) or metadata.get('bytes')!=len(raw)
            or metadata.get('capture_id')!=receipt.get('capture_id')
            or metadata.get('format')!='s16_iq_le' or receipt.get('schema_version')!=1):
        raise ValueError('record identity or format mismatch')
    return dict(payload=raw,metadata=metadata,receipt=receipt)
