"""The S1 contract carries all dependencies and loads isolated real engines."""
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from test_rf_model_release import R, _release, _save_identity, _set_model_status, _rehash_artifact

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
NEW='cnn-bpsk-teaching-v1'
NAMES=('rf_teaching_profile.py','rf_rx_constellation.py','rf_experiment_record.py')


@pytest.mark.parametrize('missing',NAMES)
def test_contract_requires_each_teaching_module(tmp_path,missing):
    tree=tmp_path/'release'
    m,_=_release(tree,contract=NEW)
    assert set(m['software'])==set(R.software_sources('cnn-bpsk-v1'))|set(NAMES)
    (tree/missing).unlink()
    with pytest.raises((ValueError,OSError)):R.verify_release(tree,require_software=True)


def test_unknown_module_and_changed_hash_are_refused(tmp_path):
    tree=tmp_path/'release';_release(tree,contract=NEW)
    (tree/'intruder.py').write_text('unattested')
    with pytest.raises(ValueError):R.verify_release(tree,require_software=True)
    (tree/'intruder.py').unlink()
    (tree/NAMES[0]).write_text('changed')
    with pytest.raises(ValueError):R.verify_release(tree,require_software=True)


@pytest.mark.parametrize('contract',['cnn-v1','cnn-bpsk-v1',NEW])
def test_actual_panel_and_engine_loading_are_isolated(tmp_path,contract):
    tree=tmp_path/'release';m,r=_release(tree,contract=contract,artifact=True)
    _set_model_status(tree,'production');m['model_artifact']['status']='production'
    _rehash_artifact(tree,m,r)
    for name,path in R.software_sources(contract).items():
        shutil.copyfile(ROOT/path,tree/name);m['software'][name]=R.sha256_file(tree/name)
    _save_identity(tree,m,r)
    verified=R.verify_release(tree,require_software=True)
    assert R.model_capability(verified)
    R.verify_panel_imports(tree)
    script="""import sys,site
sys.path.append(site.getusersitepackages());sys.path.insert(0,sys.argv[1])
from rf_status_worker import RfStatusWorker
w=RfStatusWorker(release_directory=sys.argv[1])
w._ensure_model();w._ensure_demod();w._ensure_teaching()
assert w.model_status()['available'],w.model_status()
assert w.model_status()['production'],w.model_status()
assert w.demod_status()['available']==(sys.argv[2]!='cnn-v1'),w.demod_status()
assert w.teaching_status()['available']==(sys.argv[2]=='cnn-bpsk-teaching-v1'),w.teaching_status()
"""
    run=subprocess.run([sys.executable,'-I','-B','-c',script,str(tree),contract],
        cwd=tree,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr
    if contract!=NEW:assert not any((tree/n).exists() for n in NAMES)


def test_installer_contains_new_branch_and_modules():
    text=(ROOT/'pynq_overlay/sw/install.sh').read_text(encoding='utf-8')
    assert NEW in text
    assert all(n in text for n in NAMES)


def test_worker_cannot_load_teaching_from_unattested_checkout(tmp_path):
    import rf_status_worker as W
    tree=tmp_path/'release';m,r=_release(tree,contract=NEW)
    worker=W.RfStatusWorker(release_directory=tree)
    worker._ensure_teaching()
    assert not worker.teaching_status()['available']

