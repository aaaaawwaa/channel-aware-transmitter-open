"""S2 is attested separately; S1 and its production CNN remain usable."""
from pathlib import Path
import re
import pytest
from test_rf_model_release import R, _release

CONTRACT = 'cnn-bpsk-environment-v1'
NAMES = ('rf_environment.py', 'rf_experiment_scenarios.py',
         'rf_environment_baseline.json')


def test_environment_contract_contains_full_teaching_and_diagnostic_dependencies():
    assert set(R.software_sources(CONTRACT)) == set(
        R.software_sources('cnn-bpsk-teaching-v1')) | set(NAMES)


HEADROOM = 'cnn-bpsk-headroom-v1'
ROOT = Path(__file__).resolve().parents[3]


RESEARCH_ONLY = {'rf_headroom.py', 'rf_headroom_replay.py'}


def test_headroom_contract_adds_only_the_research_module_to_environment():
    """The research contract is additive: the 40 dB contract is untouched."""
    assert set(R.software_sources(HEADROOM)) == set(
        R.software_sources(CONTRACT)) | RESEARCH_ONLY
    assert set(R.software_sources(CONTRACT)) == (
        set(R.software_sources('cnn-bpsk-teaching-v1')) | set(NAMES))
    # And the research-only files are absent from every older contract, so a
    # 40 dB release cannot claim the 46 dB transaction by shipping the modules.
    for older in ('cnn-bpsk-environment-v1', 'cnn-bpsk-teaching-v1',
                  'cnn-bpsk-v1', 'cnn-v1', 'rx-v1', 'legacy-v1'):
        assert not RESEARCH_ONLY & set(R.software_sources(older)), older


def test_the_research_contract_keeps_the_production_model_usable():
    assert HEADROOM in R.MODEL_CONTRACTS
    assert R.model_capability({'software_contract': HEADROOM, 'model_artifact': {
        'status': 'production'}}) is True
    assert R.model_capability({'software_contract': HEADROOM, 'model_artifact': {
        'status': 'experimental'}}) is False
    # An unlisted contract gains nothing by naming a production artifact.
    assert R.model_capability({'software_contract': 'cnn-bpsk-headroom-v2',
                               'model_artifact': {'status': 'production'}}) is False


def test_the_installer_stages_every_module_the_research_contract_attests():
    """An attested file the installer never copies fails the install's own check.

    Parsed from the installer rather than asserted by name: a module added to the
    contract and forgotten in the installer is exactly the drift this catches.
    """
    import re
    import shlex
    text = (ROOT / 'pynq_overlay/sw/install.sh').read_text(encoding='utf-8')
    routes = {}
    for match in re.finditer(r'for module in (.*?); do\s*(.*?)\bdone', text, re.S):
        names, body = match.groups()
        command = shlex.split(body.strip())
        assert command[:3] == ['install', '-m', '644']
        for name in shlex.split(names.replace('\\\n', ' ')):
            routes[command[4].replace('$module', name).replace('$STAGE/', '')] = (
                command[3].replace('$module', name).replace('$SRC', 'pynq_overlay/sw'))
    for source, destination in re.findall(
            r'install -m 644 "(\$SRC[^"\n]+)" "\$STAGE/([^"\n]+)"', text):
        if '$module' not in source:
            routes[destination] = source.replace('$SRC', 'pynq_overlay/sw')
    routes['rf-daemon.service'] = 'rf_overlay/sw/rf-daemon.service'
    for name, relative in R.software_sources(HEADROOM).items():
        assert name in routes, 'missing installer destination: ' + name
        assert (ROOT / routes[name]).is_file(), 'installer route does not resolve: ' + name


def _research_install_guard(text, module='rf_headroom.py'):
    """The conditions on the `if` that installs `module`.

    Shared by the real assertion and its negative control, so the control
    exercises the predicate that actually runs rather than a paraphrase of it.
    Walks up past the guard's other install lines, so a guard that stages more
    than one module is still found by the module it stages.
    """
    lines = text.splitlines()
    indexes = [n for n, line in enumerate(lines) if module in line]
    assert len(indexes) == 1, indexes
    index = indexes[0]
    indent = len(lines[index]) - len(lines[index].lstrip())
    assert indent > 0, 'the research module is not installed inside any guard'
    guard = None
    for line in reversed(lines[:index]):
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) < indent:
            guard = line
            break
    assert guard is not None, 'the install is not inside a guard'
    assert guard.strip().startswith('if [ "$SOFTWARE_CONTRACT" '), guard
    return re.findall(r'\$SOFTWARE_CONTRACT"?\s*=\s*([A-Za-z0-9._-]+)', guard)


@pytest.mark.parametrize('module', ['rf_headroom.py', 'rf_headroom_replay.py'])
def test_the_installer_routes_the_research_module_only_to_its_own_contract(module):
    """The guard that installs each research module names exactly one contract.

    Asserting on the guard's whole condition list rather than on a window of
    text: a second `|| [ "$SOFTWARE_CONTRACT" = <older> ]` branch is the same
    defect as a second `if`, and a window matching only the `if` form cannot
    see it.
    """
    text = (ROOT / 'pynq_overlay/sw/install.sh').read_text(encoding='utf-8')
    assert _research_install_guard(text, module) == ['cnn-bpsk-headroom-v1']


def test_the_installer_route_check_can_fail():
    """Negative control: the same predicate must reject an added `||` branch.

    A guard test that cannot fail is worse than none -- this one exists because
    the version it replaced could not fail for the reason it named.
    """
    text = (ROOT / 'pynq_overlay/sw/install.sh').read_text(encoding='utf-8')
    tampered = text.replace(
        'if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ]; then',
        'if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ] '
        '|| [ "$SOFTWARE_CONTRACT" = cnn-bpsk-environment-v1 ]; then')
    assert tampered != text, 'the guard this control tampers with is gone'
    assert _research_install_guard(tampered) == ['cnn-bpsk-headroom-v1',
                                                 'cnn-bpsk-environment-v1']


@pytest.mark.parametrize('missing', NAMES)
def test_environment_release_cannot_verify_missing_diagnostic_or_calibration(tmp_path, missing):
    tree = tmp_path / 'release'
    _release(tree, contract=CONTRACT)
    (tree / missing).unlink()
    with pytest.raises((ValueError, OSError)):
        R.verify_release(tree, require_software=True)


def test_production_cnn_capability_survives_environment_contract():
    assert R.model_capability({'software_contract': CONTRACT,
                              'model_artifact': {'status': 'production'}})
    assert not R.model_capability({'software_contract': CONTRACT,
                                  'model_artifact': {'status': 'experimental'}})


def test_s1_contract_does_not_gain_unattested_environment_files():
    assert not set(NAMES).intersection(R.software_sources('cnn-bpsk-teaching-v1'))


def test_actual_environment_engines_load_only_from_complete_candidate(tmp_path):
    import shutil
    import subprocess
    import sys
    from test_rf_model_release import _save_identity
    root=Path(__file__).resolve().parents[3]
    tree=tmp_path/'release';m,r=_release(tree,contract=CONTRACT,artifact=True)
    for name,relative in R.software_sources(CONTRACT).items():
        shutil.copyfile(root/relative,tree/name)
        m['software'][name]=R.sha256_file(tree/name)
    # The shipped baseline is the 46 dB provisional development bootstrap (M2
    # bootstrap): one real 46 dB normal frame, n=1, calibration_status='development'.
    # It loads in the current single domain, so the candidate carries it verbatim.  It
    # is NOT a formal acceptance baseline: that one has to be fitted from a real 46 dB
    # TRAIN campaign (20 normal frames from two sessions, M5/M6), which a single-frame
    # file can never be.  The tamper control below proves the engine still fails closed.
    import json
    import rf_environment as ENV
    stored=json.loads((tree/'rf_environment_baseline.json').read_text(encoding='utf-8'))
    loaded=ENV.EnvironmentBaseline.from_dict(stored)
    assert loaded.calibration_status=='development'
    assert loaded.calibration_status!='frozen'
    assert len(loaded.sources)==1
    assert dict(loaded.rf_config)==dict(ENV.FIXED_RF)
    _save_identity(tree,m,r)
    script="""import sys,site
sys.path.append(site.getusersitepackages());sys.path.insert(0,sys.argv[1])
from rf_status_worker import RfStatusWorker
w=RfStatusWorker(release_directory=sys.argv[1])
w._ensure_model();w._ensure_demod();w._ensure_teaching();w._ensure_environment()
assert w.model_status()['available'],w.model_status()
assert w.demod_status()['available'],w.demod_status()
assert w.teaching_status()['available'],w.teaching_status()
assert w.environment_status()['available'],w.environment_status()
"""
    run=subprocess.run([sys.executable,'-I','-B','-c',script,str(tree)],
        cwd=tree,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert run.returncode==0,run.stderr
    # Fail closed: the same isolated engine must refuse a tampered carried baseline, and
    # must name the production refusal instead of quietly reporting the engine available.
    tampered=dict(stored,sha256='0'*64)
    (tree/'rf_environment_baseline.json').write_text(json.dumps(tampered),encoding='utf-8')
    m['software']['rf_environment_baseline.json']=R.sha256_file(tree/'rf_environment_baseline.json')
    _save_identity(tree,m,r)
    probe="""import sys,site
sys.path.append(site.getusersitepackages());sys.path.insert(0,sys.argv[1])
from rf_status_worker import RfStatusWorker
w=RfStatusWorker(release_directory=sys.argv[1])
w._ensure_model();w._ensure_demod();w._ensure_teaching();w._ensure_environment()
status=w.environment_status()
print(status['available'],status['reason'])
assert status['available'] is False,status
assert 'baseline canonical SHA mismatch' in status['reason'],status
assert w.model_status()['available'] and w.demod_status()['available']
assert w.teaching_status()['available']
"""
    broken=subprocess.run([sys.executable,'-I','-B','-c',probe,str(tree)],
        cwd=tree,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert broken.returncode==0,broken.stderr
