"""Resource timing must execute actual synchronized symbols, never short circuits."""
from dataclasses import replace
from pathlib import Path
import sys
import pytest
from test_rf_rx_constellation import fixture
from rf_rx_constellation import estimate_constellation
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))


def test_benchmark_import_has_no_board_selection_or_file_io():
    from scripts import rf_bpsk_res_bench as bench
    assert callable(bench.validate_teaching_result)


@pytest.mark.parametrize('change',['carrier','timing','empty','short','count','reason'])
def test_incomplete_constellation_cannot_count_as_full_resource_path(change):
    from scripts.rf_bpsk_res_bench import validate_teaching_result
    f,e=fixture();c=estimate_constellation(f,e)
    if change=='carrier':e=replace(e,result=replace(e.result,carrier_sync=False))
    elif change=='timing':e=replace(e,result=replace(e.result,timing_sync=False))
    elif change=='empty':c=replace(c,points=())
    elif change=='short':c=replace(c,points=c.points[:1])
    elif change=='count':c=replace(c,symbol_count=200)
    else:c=replace(c,reason='INVALID_ESTIMATE')
    with pytest.raises(ValueError,match='full synchronized'):
        validate_teaching_result(e,c)


def test_full_real_constellation_qualifies():
    from scripts.rf_bpsk_res_bench import validate_teaching_result
    f,e=fixture();validate_teaching_result(e,estimate_constellation(f,e))
