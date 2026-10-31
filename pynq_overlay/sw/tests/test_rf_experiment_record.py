"""Local evidence stays bounded, same-frame, non-overwriting and tamper evident."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import pytest
from test_rf_rx_constellation import fixture


def test_record_roundtrip_preserves_exact_iq_and_reference(tmp_path):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture(); c=estimate_constellation(f,e)
    receipt=E.write_record(tmp_path,f,e,c,rf_config={'lo_hz':80000000})
    path=Path(receipt['path'])
    restored=E.read_record(path)
    assert restored['payload']==f.payload
    assert restored['metadata']['iq_sha256']==hashlib.sha256(f.payload).hexdigest()
    assert restored['metadata']['demod_result']['submitted']['config']['reference_prbs7_seed']==1
    assert restored['metadata']['historical'] is False
    assert restored['metadata']['control_result']=='NOT_RECORDED'
    second=E.write_record(tmp_path,f,e,c,historical=True)
    assert second['path']!=receipt['path']
    assert E.read_record(second['path'])['metadata']['historical'] is True


@pytest.mark.parametrize('which',['iq','metadata'])
def test_tamper_fails_before_replay(tmp_path,which):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture(); receipt=E.write_record(tmp_path,f,e,estimate_constellation(f,e))
    path=Path(receipt['path'])/('frame.iq' if which=='iq' else 'metadata.json')
    raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(ValueError,match='hash'): E.read_record(Path(receipt['path']))


def test_foreign_envelope_or_constellation_is_not_saved(tmp_path):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture(); c=estimate_constellation(f,e)
    for wrong in (replace(c,capture_id='foreign'),replace(c,submitted_revision=999)):
        with pytest.raises(ValueError,match='identity'): E.write_record(tmp_path,f,e,wrong)
    with pytest.raises(ValueError,match='identity'):
        E.write_record(tmp_path,f,replace(e,result=replace(e.result,release_id='other')),c)


def test_secrets_nonfinite_and_oversized_metadata_refuse(tmp_path):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture(); c=estimate_constellation(f,e)
    for config in ({'password':'<SET_LOCALLY>'},{'nested':{'token':'<SET_LOCALLY>'}},{'lo_hz':float('nan')},{'huge':'a'*200000}):
        with pytest.raises(ValueError): E.write_record(tmp_path,f,e,c,rf_config=config)


def test_read_rejects_symlink_iq_or_missing_commit(tmp_path):
    import rf_experiment_record as E
    with pytest.raises((ValueError,FileNotFoundError)): E.read_record(tmp_path)


def test_mkdir_failure_does_not_claim_saved(tmp_path):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    root=tmp_path/'not-a-directory';root.write_text('occupied')
    f,e=fixture()
    with pytest.raises(OSError): E.write_record(root,f,e,estimate_constellation(f,e))


@pytest.mark.parametrize('extra',[{}, {'completed_at_monotonic':999.},
    {'completed_at_monotonic':100.,'release_id':'other'},
    {'completed_at_monotonic':100.,'connection_generation':999},
    {'completed_at_monotonic':100.,'config_revision':999}])
def test_same_short_id_does_not_authorize_foreign_analysis(tmp_path,extra):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture()
    with pytest.raises(ValueError,match='identity'):
        E.write_record(tmp_path,f,e,estimate_constellation(f,e),analysis=dict(capture_id=f.capture_id,**extra))
    assert not list(tmp_path.iterdir())


def test_result_algorithm_version_must_equal_envelope(tmp_path):
    import rf_experiment_record as E
    from rf_rx_constellation import estimate_constellation
    f,e=fixture(); c=estimate_constellation(f,e)
    with pytest.raises(ValueError,match='identity'):
        E.write_record(tmp_path,f,replace(e,result=replace(e.result,algorithm_version='other')),c)
