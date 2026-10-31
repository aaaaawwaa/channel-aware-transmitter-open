"""The fifth rollback-parent route: the 2026-10-18 evidence layout.

1a30f4b7 is the release the 2026-10-18 non-emitting trial installed and accepted on the
board -- the reviewed installer, a bounded on-board acceptance, an independent read-only
read-back and its repeat after the staging cleanup -- so it has no INSTALLED_VERIFIED
receipt, and the trial documents must not be renamed into one.  The package records the
same chain in a different layout (bundle/ trial/ readback/ preflight/, a 17-item preflight
document beside each raw board reading, no host receipt and no read-back summaries), so
verify_installed_parent_via_trial_acceptance_evidence_v2 rebuilds it from that layout
while every identity and digest is still compared with the candidate directory
R.verify_release has just verified.

These tests prove the real 2026-10-18 chain is accepted, that the format has to be named
and each entry refuses the other package, that both deployment gates can use it, that it
cannot stand in for an INSTALLED_VERIFIED receipt, that both formats are decided by the
same read-back predicate, and that every evidence step -- identity, digests, uniqueness of
the terminal marker, rollback markers, the independent read-back, the raw board readings,
the staged, fetched and downloaded bytes, the receipt-equivalent and the evidence index --
is load-bearing, including against a self-consistent rewrite that rebinds the index.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import merged_release as R          # noqa: E402
from scripts import rf_environment_deploy as E   # noqa: E402
from scripts import rf_headroom_deploy as D      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RELEASE = '1a30f4b7b9aebe27965fcdd6dcf7634e'
CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-arm-check-new-2026-10-18/candidate/'
             'releases' / RELEASE)
EVIDENCE = ROOT / 'evidence/rf-teaching/s2-46db-install-new-2026-10-18'
OLD_EVIDENCE = ROOT / D.TRIAL_EVIDENCE
TRIAL_PARENT = 'e4dae554182e8c9f5f963c1d5040913a'
OLD_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-m2-bootstrap-2026-10-17/candidate/'
                 'releases' / TRIAL_PARENT)
ACCEPTED = 'TRIAL_ACCEPTED release=' + RELEASE
FORMAT = D.TRIAL_FORMAT_2026_10_18
OLD_FORMAT = D.TRIAL_FORMAT_2026_10_17
HEX = 'f' * 64

# A release this trial never installed, and a release another route installed.
OTHER = 'ad05576aff6b9bf2b6b495347a6269ec'
OTHER_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-46db-candidate-b-2026-10-17/candidate/'
                   'releases' / OTHER)
ACTIVATED = 'e66197ed79131b73aa4f47804487c053'
ACTIVATED_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-headroom-candidate2-2026-10-17/'
                       'candidate/releases' / ACTIVATED)

# Gate inputs of a qualified HEADROOM-1 candidate and of a qualified environment candidate.
HEADROOM = 'b82b141e355b4cb1833969383a409c79'
HEADROOM_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                      'candidate-final/releases' / HEADROOM)
HEADROOM_ARM = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                       'arm-runtime/receipt.json')
HEADROOM_CHECKPOINT = ROOT / ('evidence/rf-teaching/s2/executable-candidate-2026-10-12/'
                              'development-checkpoint.json')
ENV_CANDIDATE = (ROOT / 'evidence/rf-teaching/s2-candidate-environment-2026-10-15/'
                 'candidate/releases/399b300e59b8d00e41d43c8123884c7f')
ENV_ARM = ROOT / 'evidence/rf-teaching/s2-arm-qualification-2026-10-15/arm-run/receipt.json'
ENV_CLOSURE = ROOT / 'evidence/rf-teaching/s1/closure-2026-10-04/receipt.json'


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def _record(path, marker):
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        if line.startswith(marker + ' '):
            return json.loads(line[len(marker) + 1:])
    raise AssertionError('no %s record in %s' % (marker, path))


def parent_of(candidate=CANDIDATE):
    return R.verify_release(Path(candidate), require_software=True)


def prove(directory, candidate=CANDIDATE):
    return D.verify_installed_parent_via_trial_acceptance_evidence_v2(
        parent_of(candidate), directory, candidate)


def copy_evidence(tmp_path):
    """The whole 2026-10-18 package, byte for byte.

    The evidence index binds every file of the package, so a partial copy would be
    refused by the binding rather than by the check under test.
    """
    directory = Path(tmp_path) / 'trial'
    shutil.copytree(EVIDENCE, directory)
    return directory


def _evidence_names(directory):
    return sorted(path.relative_to(directory).as_posix()
                  for path in Path(directory).rglob('*') if path.is_file())


def rebind_index(directory):
    """Rehash the evidence index over the edited tree: the self-consistent forgery.

    A forger who edits a document has to do at least this much.  The route still refuses,
    because the values it compares are read from the verified candidate.
    """
    directory = Path(directory)
    index_path = directory / D.TRIAL_V2_INDEX
    index = {}
    for path in sorted(directory.rglob('*')):
        if path.is_file() and path != index_path:
            name = path.relative_to(directory).as_posix()
            key = 'evidence/rf-teaching/' + directory.name + '/' + name
            index[key] = dict(bytes=path.stat().st_size, sha256=D.sha(path))
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + chr(10),
                          encoding='utf-8')
    return directory


def mutate_json(directory, relative, mutate):
    path = Path(directory) / relative
    before = path.read_bytes()
    document = _load(path)
    mutate(document)
    _write(path, document)
    assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return path


def mutate_text(directory, relative, mutate):
    path = Path(directory) / relative
    before = path.read_bytes()
    path.write_text(mutate(path.read_text(encoding='utf-8')), encoding='utf-8')
    assert path.read_bytes() != before, 'the mutation changed nothing in ' + relative
    return path


def _set(path, value):
    def mutate(document):
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return mutate


def sync_summary(directory):
    """Rewrite the package's own acceptance summary from the (edited) report."""
    directory = Path(directory)
    acceptance = _load(directory / 'readback/board-download/acceptance-report.json')
    details = {item.get('name'): (item.get('detail') or {})
               for item in acceptance.get('checks') or []}
    lines = ['schema= %s expected_release= %s ok= %s'
             % (acceptance.get('schema'), acceptance.get('expected_release'),
                acceptance.get('ok'))]
    for name, key in (('a', 'a_release_identity'), ('b', 'b_domain_constants'),
                      ('c', 'c_baseline_load_not_formal'), ('d', 'd_services'),
                      ('e', 'e_rf_closed')):
        detail = details[key]
        lines.append(name + ': ' + json.dumps(detail.get('constants', detail)))
    frame = details['f_archived_iq_smoke']
    for key in ('frame', 'demod', 'environment'):
        lines.append('f.' + key + ': ' + json.dumps(frame.get(key)))
    lines.append('boot_id= ' + str(acceptance.get('state', {}).get('boot_id')))
    (directory / 'logs/07-acceptance-summary.txt').write_text(chr(10).join(lines) + chr(10),
                                                              encoding='utf-8')
    return directory

def _paths_sections(path):
    """One raw paths reading, as an ordered list of (name, lines)."""
    sections = []
    current = None
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        if len(line) > 4 and line.startswith('==') and line.endswith('=='):
            current = [line[2:-2], []]
            sections.append(current)
        elif current is not None and line.endswith('_DONE'):
            current = None
        elif current is not None and line != '':
            current[1].append(line)
    return sections


def edit_paths_record(directory, tag, mutate):
    """Edit one raw paths reading in place, so its report can be kept equal to it."""
    directory = Path(directory)
    folder = directory / ('preflight' if tag.startswith('pre-') else 'readback')
    path = folder / (tag + '-03-paths.log')
    sections = _paths_sections(path)
    view = dict(sections)
    mutate(view)
    # An edit may replace a section or mutate it in place; either way the file is
    # rewritten from what the caller left behind.
    for name, lines in sections:
        if name in view and view[name] is not lines:
            lines[:] = view[name]
    text = []
    for name, lines in sections:
        text.append('==' + name + '==')
        text.extend(lines)
        text.append(name + '_DONE')
    path.write_text(chr(10).join(text) + chr(10), encoding='utf-8')
    return path


def sync_document_from_raw(directory, tag):
    """Rewrite one report from its (edited) raw board records.

    A forger who edits the board's own logs has to edit the report that restates them as
    well: the route compares the two, and only then compares the values with the candidate.
    """
    directory = Path(directory)
    folder = directory / ('preflight' if tag.startswith('pre-') else 'readback')
    document_path = folder / (tag + '-preflight.json')
    document = _load(document_path)
    document['snapshot'] = _record(folder / (tag + '-01-snapshot.log'), 'DEPLOY_SNAPSHOT')
    document['state'] = _record(folder / (tag + '-02-state.log'), 'PREFLIGHT_STATE')
    units = {}
    for name, unit in (document['snapshot'].get('units') or {}).items():
        fields = {}
        for line in str(unit.get('out') or '').splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                fields[key.strip()] = value.strip()
        units[name] = fields
    document['items']['B5_services'] = dict(document['items']['B5_services'], services=units)
    paths = dict(_paths_sections(folder / (tag + '-03-paths.log')))
    lock = paths.get('LOCK') or []
    document['items']['B10_lock_free'] = dict(
        document['items']['B10_lock_free'],
        state='LOCK_FREE' if 'LOCK_FREE' in lock else 'LOCK_BUSY',
        reason=next((line.split('=', 1)[1] for line in lock
                     if line.startswith('LOCK_SOURCE=')), ''))
    document['items']['B9_no_inflight'] = dict(
        document['items']['B9_no_inflight'],
        units=chr(10).join(paths.get('UNITS') or []), rf_jobs=[],
        unrelated_jobs=chr(10).join(paths.get('JOBS') or []))
    document['items']['B11_tmp_clean'] = dict(
        document['items']['B11_tmp_clean'], excused=list(paths.get('TMP_RESIDUE') or []),
        residue=[], listing=list(paths.get('TMP') or []))
    _write(document_path, document)
    return document


def sync_facts(directory):
    """Rebuild the recorded decision facts from the (edited) reports, as a forger would."""
    directory = Path(directory)
    facts = {}
    for tag in ('pre-00', 'pre-01', 'post-00', 'post-01'):
        folder = directory / ('preflight' if tag.startswith('pre-') else 'readback')
        path = folder / (tag + '-preflight.json')
        if path.is_file():
            facts[tag] = D._trial_v2_facts(_load(path))
    _write(directory / D.TRIAL_V2_FACTS, facts)
    return directory


def forged_acceptance(directory, mutate):
    """Edit the acceptance report and both records that restate it.

    The unit printed the report into the journal and the package wrote its own summary, so
    a report edit only reaches the content checks if those two are edited too.
    """
    directory = Path(directory)
    path = directory / 'readback/board-download/acceptance-report.json'
    report = _load(path)
    mutate(report)
    _write(path, report)
    journal_path = directory / 'logs/dispatch-journal.log'
    lines = journal_path.read_text(encoding='utf-8').splitlines()
    payload = [line.partition('] pynq ')[2].partition(': ')[2]
               if ('[%' not in line and '] pynq ' in line and line.startswith('[')) else ''
               for line in lines]
    check = next(index for index, line in enumerate(payload)
                 if line.startswith('TRIAL_CHECK_BEGIN'))
    begin = next(index for index in range(check, len(payload)) if payload[index].strip() == '{')
    end = next(index for index in range(len(payload) - 1, begin, -1)
               if payload[index].strip() == '}')
    prefix = lines[begin].partition(': ')[0] + ': '
    printed = json.dumps(report, indent=2, sort_keys=True).splitlines()
    journal_path.write_text(chr(10).join(lines[:begin]
                                         + [prefix + line for line in printed]
                                         + lines[end + 1:]) + chr(10), encoding='utf-8')
    sync_summary(directory)
    return directory


def restage_payload(directory):
    """Rebuild every record that carries the staged payload's digests.

    A payload edit that is meant to be judged on its content has to be staged
    consistently first: the local map, the board's two re-reads, the intent, the dispatch
    intent and the staging summaries all restate the same digests.
    """
    directory = Path(directory)
    stage = directory / 'trial/stage'
    maps = ('stage-local-sha256.json', 'staged-sha256.json')
    files = {path.relative_to(stage).as_posix(): D.sha(path)
             for path in sorted(stage.rglob('*'))
             if path.is_file() and path.name not in maps}
    _write(stage / 'stage-local-sha256.json', files)
    staged = _load(directory / 'trial/staged-sha256.json')
    staged['files'] = files
    _write(directory / 'trial/staged-sha256.json', staged)
    for relative, marker, count in (('logs/stage-binding.log', 'STAGE_BINDING', None),
                                    ('logs/upload-verify.log', 'STAGE_VERIFIED', 'count')):
        path = directory / relative
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        record = _record(path, marker)
        record['files'] = files
        if count:
            record[count] = len(files)
        for index, line in enumerate(lines):
            if line.startswith(marker + ' '):
                lines[index] = marker + ' ' + json.dumps(record)
                break
        path.write_text(chr(10).join(lines) + chr(10), encoding='utf-8')
    trial = _load(directory / 'trial/trial.json')
    trial['files'] = files
    _write(directory / 'trial/trial.json', trial)
    intent = _load(directory / 'trial/dispatch-intent.json')
    intent['staged']['files'] = files
    _write(directory / 'trial/dispatch-intent.json', intent)
    summary = _load(directory / 'logs/dispatch-summary.json')
    summary['intent'] = intent
    _write(directory / 'logs/dispatch-summary.json', summary)
    local = _load(directory / 'logs/02-stage-local.log')
    local['files'] = len(files)
    local['archive_sha'] = files['candidate.tgz']
    _write(directory / 'logs/02-stage-local.log', local)
    upload = _load(directory / 'logs/03-upload.log')
    upload['staged'] = len(files)
    _write(directory / 'logs/03-upload.log', upload)
    return directory

def edit_state_record(directory, tag, mutate):
    """Edit one raw PREFLIGHT_STATE record in place, keeping its JSON shape."""
    directory = Path(directory)
    folder = directory / ('preflight' if tag.startswith('pre-') else 'readback')
    path = folder / (tag + '-02-state.log')
    text = path.read_text(encoding='utf-8')
    state = _record(path, 'PREFLIGHT_STATE')
    mutate(state)
    path.write_text('PREFLIGHT_STATE ' + json.dumps(state) + chr(10), encoding='utf-8')
    assert path.read_text(encoding='utf-8') != text
    return path


def edit_snapshot_record(directory, tag, mutate):
    directory = Path(directory)
    folder = directory / ('preflight' if tag.startswith('pre-') else 'readback')
    path = folder / (tag + '-01-snapshot.log')
    snapshot = _record(path, 'DEPLOY_SNAPSHOT')
    mutate(snapshot)
    path.write_text('DEPLOY_SNAPSHOT ' + json.dumps(snapshot) + chr(10), encoding='utf-8')
    return path


def expect_refusal(tmp_path, relative, mutate, needle, *, text=False, rebind=True,
                   sync=(), state=None, snapshot=None, paths=None):
    """Mutate one artefact of an isolated copy, rebind what a forger would rebind, and
    require the named refusal."""
    directory = copy_evidence(tmp_path)
    if relative is not None:
        if text:
            mutate_text(directory, relative, mutate)
        else:
            mutate_json(directory, relative, mutate)
    for tag in sync:
        sync_document_from_raw(directory, tag)
    if state is not None:
        tag, mutate_state = state
        edit_state_record(directory, tag, mutate_state)
        sync_document_from_raw(directory, tag)
    if snapshot is not None:
        tag, mutate_snapshot = snapshot
        edit_snapshot_record(directory, tag, mutate_snapshot)
        sync_document_from_raw(directory, tag)
    if paths is not None:
        tag, mutate_paths = paths
        edit_paths_record(directory, tag, mutate_paths)
        sync_document_from_raw(directory, tag)
    if state is not None or snapshot is not None or paths is not None or sync:
        sync_facts(directory)
    if rebind:
        rebind_index(directory)
    with pytest.raises(ValueError, match=needle):
        prove(directory)
    return directory

def test_the_real_2026_10_18_trial_chain_is_accepted():
    proof = prove(EVIDENCE)
    assert proof['release_id'] == RELEASE
    assert proof['source'] == D.TRIAL_V2_SOURCE
    assert proof['source'] != D.INSTALLER_RECEIPT_OUTCOME
    assert proof['trial_format'] == FORMAT
    assert proof['software_contract'] == 'cnn-bpsk-environment-v1'
    assert proof['software_modules'] == 44
    assert proof['trial_parent'] == TRIAL_PARENT
    assert set(proof['evidence_sha256']) == set(D.TRIAL_V2_FILES)
    for name in ('logs/dispatch-journal.log', 'readback/post-01-preflight.json',
                 'logs/13-release-listing.log', 'logs/15-evidence-sha256.json',
                 'bundle/bundle-receipt.json'):
        assert proof['evidence_sha256'][name] == D.sha(EVIDENCE / name)


def test_the_route_never_writes_renames_or_upgrades_a_receipt():
    before = {name: D.sha(EVIDENCE / name) for name in _evidence_names(EVIDENCE)}
    prove(EVIDENCE)
    after = {name: D.sha(EVIDENCE / name) for name in _evidence_names(EVIDENCE)}
    assert before == after
    for relative in ('trial/trial.json', 'readback/board-download/acceptance-report.json',
                     'logs/15-evidence-sha256.json'):
        assert 'INSTALLED_VERIFIED' not in (EVIDENCE / relative).read_text(encoding='utf-8')


def test_the_2026_10_17_entry_refuses_the_2026_10_18_package():
    with pytest.raises(ValueError, match='trial acceptance evidence trial.json missing'):
        D.verify_installed_parent_via_trial_acceptance_evidence(parent_of(), EVIDENCE,
                                                                CANDIDATE)


def test_the_2026_10_18_entry_refuses_the_2026_10_17_package():
    with pytest.raises(ValueError, match='trial acceptance evidence v2 .* missing'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(
            R.verify_release(OLD_CANDIDATE, require_software=True), OLD_EVIDENCE,
            OLD_CANDIDATE)


def test_headroom_gate_binds_the_new_format_parent():
    gate = D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                          HEADROOM_CHECKPOINT, None, EVIDENCE, FORMAT)
    assert gate['release_id'] == HEADROOM
    assert gate['parent_release'] == RELEASE
    assert gate['parent_trial_acceptance'] == str(EVIDENCE.resolve())
    assert gate['parent_trial_parent'] == TRIAL_PARENT
    assert set(gate['parent_trial_acceptance_sha256']) == set(D.TRIAL_V2_FILES)
    assert 'parent_install_sha256' not in gate
    assert 'parent_activation_sha256' not in gate


def test_headroom_gate_refuses_the_new_package_without_the_format():
    with pytest.raises(ValueError, match='trial acceptance evidence trial.json missing'):
        D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                       HEADROOM_CHECKPOINT, None, EVIDENCE)
    with pytest.raises(ValueError, match='trial acceptance evidence trial.json missing'):
        D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                       HEADROOM_CHECKPOINT, None, EVIDENCE, OLD_FORMAT)


def test_an_unknown_evidence_format_is_refused():
    with pytest.raises(ValueError, match='unknown trial acceptance evidence format'):
        D.verify_gates(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                       HEADROOM_CHECKPOINT, None, EVIDENCE, '2026-10-19')


def test_environment_gate_binds_the_new_format_parent():
    gate = E.verify_gates(ENV_CANDIDATE, ENV_CLOSURE, ENV_ARM, CANDIDATE, None, None, None,
                          EVIDENCE, FORMAT)
    assert gate['release_id'] == '399b300e59b8d00e41d43c8123884c7f'
    assert gate['parent_release'] == RELEASE
    assert gate['contract'] == E.CONTRACT
    assert gate['parent_trial_acceptance'] == str(EVIDENCE.resolve())
    assert set(gate['parent_trial_acceptance_sha256']) == set(D.TRIAL_V2_FILES)


def test_both_formats_are_decided_by_the_same_read_back_predicate(tmp_path):
    """The same field-level lie refuses in both formats, by the same named check.

    The frozen route and the 2026-10-18 route hand the same summary shape to one shared
    predicate, so a layout change cannot silently drop a read-back check.
    """
    old = Path(tmp_path) / 'old'
    shutil.copytree(OLD_EVIDENCE, old)
    mutate_json(old, 'logs/readback-summary.json', _set(('state', 'software_digest'), HEX))
    with pytest.raises(ValueError, match='disagrees with the verified release'):
        D.verify_installed_parent_via_trial_acceptance_evidence(
            R.verify_release(OLD_CANDIDATE, require_software=True), old, OLD_CANDIDATE)
    directory = expect_refusal(tmp_path / 'new', 'readback/post-01-preflight.json',
                               _set(('state', 'software_digest'), HEX),
                               'post-01 read-back disagrees with the verified release',
                               state=('post-01',
                                      lambda state: state.update(software_digest=HEX)))
    assert directory.is_dir()


# --------------------------------------------------------------------------- #
# It can never stand in for an INSTALLED_VERIFIED receipt.
# --------------------------------------------------------------------------- #

def test_the_installer_route_still_needs_its_receipt():
    with pytest.raises(ValueError, match='installation receipt'):
        D.verify_parent(CANDIDATE, None)
    with pytest.raises(ValueError, match='installation receipt'):
        D.verify_parent(CANDIDATE, None, activation_evidence=None)


def test_a_release_the_trial_never_installed_is_refused():
    with pytest.raises(ValueError, match='trial acceptance evidence v2'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(
            parent_of(OTHER_CANDIDATE), EVIDENCE, OTHER_CANDIDATE)


def test_a_release_another_route_installed_is_not_a_trial_parent():
    with pytest.raises(ValueError, match='trial acceptance evidence v2'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(
            parent_of(ACTIVATED_CANDIDATE), EVIDENCE, ACTIVATED_CANDIDATE)


def test_the_route_needs_its_candidate_directory():
    with pytest.raises(ValueError, match='requires the parent candidate'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(parent_of(), EVIDENCE)
    with pytest.raises(ValueError, match='trial acceptance evidence v2 directory'):
        D.verify_installed_parent_via_trial_acceptance_evidence_v2(
            parent_of(), Path(EVIDENCE).parent / 'absent', CANDIDATE)


def test_the_proof_is_not_an_installation_receipt():
    proof = prove(EVIDENCE)
    assert 'INSTALLED_VERIFIED' not in json.dumps(proof)
    assert proof['source'] == D.TRIAL_V2_SOURCE
    receipt = _load(EVIDENCE / 'bundle/bundle-receipt.json')
    assert receipt.get('installed') is False


# --------------------------------------------------------------------------- #
# Negative battery 1: identity and digests.
# --------------------------------------------------------------------------- #

def test_intent_that_does_not_name_this_release_is_refused(tmp_path):
    cases = (
        ('trial/trial.json', _set(('release_id',), OTHER), 'intent does not name this release'),
        ('trial/trial.json', _set(('parent_release',), RELEASE),
         'intent does not name this release'),
        ('trial/trial.json', _set(('acceptance_budget_seconds',), 0),
         'intent does not name this release'),
        ('trial/trial.json', _set(('unit',), 'rf-trial-other'),
         'intent does not name this release'),
        ('trial/trial.json', _set(('archive_sha',), HEX),
         'intent does not name this release'),
        ('trial/trial.json', _set(('boot_id',), 'nope'),
         'intent does not name this release'),
        ('trial/trial.json', _set(('reviewed', 'acceptance_sha256'), HEX),
         'intent does not name this release'),
        ('trial/trial.json', _set(('alias',), '/tmp/other'),
         'intent and stage paths disagree'),
    )
    for index, (relative, mutate, needle) in enumerate(cases):
        expect_refusal(tmp_path / str(index), relative, mutate, needle)

def test_acceptance_report_that_contradicts_the_release_is_refused(tmp_path):
    cases = (
        (_set(('ok',), False), 'acceptance report unverified'),
        (_set(('schema',), 'environment-trial-acceptance-v0'),
         'acceptance report unverified'),
        (_set(('expected_release',), OTHER), 'acceptance report unverified'),
        (_set(('checks',), []), 'acceptance report unverified'),
    )
    for index, (mutate, needle) in enumerate(cases):
        expect_refusal(tmp_path / str(index),
                       'readback/board-download/acceptance-report.json', mutate, needle,
                       sync=('post-00',))

def test_acceptance_state_that_does_not_name_this_release_is_refused(tmp_path):
    """The state block is checked against the release, not against the report's own copy."""
    for index, mutate in enumerate((
            lambda report: report['state'].update(release_id_from_name=OTHER),
            lambda report: report['state'].update(
                release_path=D.CURRENT_RELEASE_PATH + '/other'),
            lambda report: report['state'].update(expected_release=OTHER),
            lambda report: report['state'].update(
                boot_id='00000000-0000-0000-0000-000000000000'))):
        directory = forged_acceptance(copy_evidence(tmp_path / str(index)), mutate)
        rebind_index(directory)
        with pytest.raises(ValueError, match='acceptance state does not name this release'):
            prove(directory)

def test_a_self_consistent_acceptance_rewrite_is_refused(tmp_path):
    """Every restatement is rewritten and the index rebound; the candidate still wins.

    The report, the copy the unit printed into the journal and the package's own summary
    all claim a forged software digest, a forged module count and a forged baseline
    canonical digest, and the index binds the edited bytes -- and the route refuses,
    because those values are read from the verified candidate directory.
    """
    def forge(report):
        for item in report['checks']:
            if item['name'] == 'a_release_identity':
                item['detail']['software_digest'] = HEX
                item['detail']['software_modules'] = 46
            if item['name'] == 'c_baseline_load_not_formal':
                item['detail']['canonical_sha256'] = HEX
    directory = forged_acceptance(copy_evidence(tmp_path), forge)
    acceptance = _load(directory / 'readback/board-download/acceptance-report.json')
    details = {item['name']: item['detail'] for item in acceptance['checks']}
    assert details['a_release_identity']['software_digest'] == HEX
    assert details['a_release_identity']['software_modules'] == 46
    assert details['c_baseline_load_not_formal']['canonical_sha256'] == HEX
    journal = (directory / 'logs/dispatch-journal.log').read_text(encoding='utf-8')
    assert HEX in journal
    rebind_index(directory)
    with pytest.raises(ValueError, match='acceptance report disagrees with the release'):
        prove(directory)

def test_release_tree_that_differs_from_the_candidate_is_refused(tmp_path):
    digest = D.sha(CANDIDATE / 'rf_daemon')
    expect_refusal(tmp_path, 'logs/13-release-listing.log',
                   lambda text: text.replace(digest, HEX),
                   'installed release tree is not the candidate tree', text=True)
    expect_refusal(tmp_path / 'missing-line', 'logs/13-release-listing.log',
                   lambda text: chr(10).join(line for line in text.splitlines()
                                             if 'rf_model.json' not in line) + chr(10),
                   'installed release tree is not the candidate tree', text=True)


def test_release_listing_record_that_denies_the_candidate_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/13-release-listing.json',
                   _set(('identical',), False), 'release listing record unverified')
    expect_refusal(tmp_path / 'b', 'logs/13-release-listing.json',
                   _set(('manifest_sha256',), HEX), 'release listing record unverified')


def test_installed_daemon_read_back_that_differs_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/08-daemon-check.log',
                   lambda text: text.replace(
                       '8d5aaf055a121f5d0650fda967c8c8d9a9599de2aafbdff090751d604d790984', HEX),
                   'installed daemon read-back unverified', text=True)


def test_fetch_payload_that_differs_from_disk_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/fetch.log',
                   lambda text: text.replace('"post-state.json": "', '"post-state.json": "AA', 1),
                   'fetch payload', text=True)


def test_board_download_record_that_differs_is_refused(tmp_path):
    expect_refusal(tmp_path, 'readback/board-download/board-download-manifest.json',
                   _set(('files', 'post-state.json', 'sha256'), HEX),
                   'board download differs: post-state.json')


def test_a_stale_evidence_index_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    mutate_json(directory, 'logs/dispatch-summary.json', _set(('unit_journal_lines',), 1))
    with pytest.raises(ValueError, match='artifact digest differs'):
        prove(directory)


def test_an_acceptance_edit_without_the_journal_copy_is_refused(tmp_path):
    """The journal is a second, independent copy of the report the unit printed."""
    directory = copy_evidence(tmp_path)
    def forge(report):
        for item in report['checks']:
            if item['name'] == 'a_release_identity':
                item['detail']['software_digest'] = HEX
    mutate_json(directory, 'readback/board-download/acceptance-report.json', forge)
    sync_summary(directory)
    rebind_index(directory)
    with pytest.raises(ValueError, match='journal and downloaded acceptance report disagree'):
        prove(directory)

# --------------------------------------------------------------------------- #
# Negative battery 2: the journal, the rollback markers and the budget.
# --------------------------------------------------------------------------- #

def test_terminal_marker_that_is_missing_repeated_or_foreign_is_refused(tmp_path):
    cases = (
        (lambda text: text.replace(ACCEPTED, 'TRIAL_PENDING'),
         'terminal marker missing, repeated or not this release'),
        (lambda text: text.replace(ACCEPTED, 'TRIAL_ACCEPTED release=' + OTHER),
         'terminal marker missing, repeated or not this release'),
        (lambda text: text + ACCEPTED + chr(10),
         'terminal marker missing, repeated or not this release'),
        (lambda text: text.replace('INSTALL_ACCEPTED', 'INSTALL_PENDING'),
         'terminal marker missing, repeated or not this release'),
        (lambda text: text.replace('TRIAL_ACCEPTANCE PASS', 'TRIAL_ACCEPTANCE FAIL'),
         'terminal marker missing, repeated or not this release'),
        (lambda text: text.replace('] pynq sh[3841]: ' + ACCEPTED,
                                   '] pynq sh[3841] ' + ACCEPTED),
         'receipt-equivalent verbatim'),
        (lambda text: text.replace('TRIAL_CHECK_BEGIN seconds=120',
                                   'TRIAL_CHECK_BEGIN seconds=60'),
         'bounded acceptance budget changed'),
    )
    for index, (mutate, needle) in enumerate(cases):
        expect_refusal(tmp_path / str(index), 'logs/dispatch-journal.log', mutate, needle,
                       text=True)

def test_a_rolled_back_recovered_or_failed_trial_is_refused(tmp_path):
    for marker in ('TRIAL_ROLLED_BACK', 'TRIAL_RECOVERY_REQUIRED', 'TRIAL_FAILED'):
        expect_refusal(tmp_path / marker, 'logs/dispatch-journal.log',
                       lambda text, marker=marker: text + marker + chr(10),
                       'records a failed or rolled-back trial', text=True)
    for marker in ('rollback-state.json', 'trial-rollback-state.json'):
        expect_refusal(tmp_path / ('rollback-' + marker),
                       'readback/board-download/board-download-manifest.json',
                       _set(('files', marker), dict(bytes=1, sha256=HEX)),
                       'records a rollback state')


def test_dispatch_that_is_not_the_one_recorded_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/dispatch.result.json', _set(('exit_code',), 1),
                   'dispatch did not succeed exactly once')
    expect_refusal(tmp_path / 'b', 'logs/dispatch-summary.json',
                   _set(('unit',), 'Result=failed'), 'dispatch did not succeed exactly once')
    expect_refusal(tmp_path / 'c', 'trial/dispatch-intent.json',
                   _set(('policy',), 'retry until accepted'), 'dispatch intent unverified')
    expect_refusal(tmp_path / 'd', 'logs/dispatch-unit.result.json',
                   _set(('exit_code',), 1), 'dispatch did not succeed exactly once')


# --------------------------------------------------------------------------- #
# Negative battery 3: the independent read-back and its raw board readings.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('name', [
    'readback/post-00-preflight.json',
    'readback/post-01-preflight.json',
    'readback/post-00-01-snapshot.log',
    'readback/post-01-02-state.log',
    'preflight/pre-01-preflight.json',
    'logs/12-readback-facts.json',
    'logs/13-release-listing.log',
    'logs/fetch.log',
    'bundle/bundle-receipt.json',
    'logs/07-acceptance-summary.txt',
    'logs/16-parent-rollback-target.log',
])
def test_a_missing_evidence_document_is_refused_by_name(tmp_path, name):
    directory = copy_evidence(tmp_path)
    (directory / name).unlink()
    with pytest.raises(ValueError,
                       match=name.split('/')[-1].replace('.', r'\.') + ' missing'):
        prove(directory)


def test_read_back_that_disagrees_with_the_release_is_refused(tmp_path):
    """A consistent rewrite of a raw board reading, its report and the facts refuses.

    The forged value is present in the raw log, in the report that restates it and in the
    recorded facts, so nothing but the comparison with the verified candidate can refuse.
    """
    expect_refusal(tmp_path / 'domain', 'readback/post-01-preflight.json',
                   _set(('state', 'client_ATTENUATION_MDB'), 40000),
                   'post-01 read-back domain disagrees with the release',
                   state=('post-01', lambda state: state.update(
                       client_ATTENUATION_MDB=40000)))
    expect_refusal(tmp_path / 'services', 'readback/post-01-preflight.json',
                   _set(('state', 'services', 'rf-daemon', 'NRestarts'), '1'),
                   'post-01 read-back disagrees with the verified release',
                   state=('post-01', lambda state: state['services']['rf-daemon']
                          .update(NRestarts='1')))
    expect_refusal(tmp_path / 'baseline', 'readback/post-01-preflight.json',
                   _set(('state', 'baseline', 'canonical_sha256'), HEX),
                   'post-01 read-back baseline disagrees with the release',
                   state=('post-01', lambda state: state['baseline'].
                          __setitem__('canonical_sha256', HEX)))
    expect_refusal(tmp_path / 'identity', 'readback/post-00-preflight.json',
                   _set(('state', 'software_modules'), 46),
                   'post-00 read-back disagrees with the verified release',
                   state=('post-00', lambda state: state.update(software_modules=46)))
    expect_refusal(tmp_path / 'faults', 'readback/post-01-04-dmesg.log',
                   lambda text: text + '[  100.0] mmc0: error -110' + chr(10),
                   'reports kernel storage faults', text=True)

def test_read_back_that_is_not_the_boards_own_reading_is_refused(tmp_path):
    expect_refusal(tmp_path, 'readback/post-00-preflight.json',
                   _set(('snapshot', 'current_release'), OTHER),
                   'post-00 report disagrees with the raw board reading')
    expect_refusal(tmp_path / 'b', 'readback/post-00-preflight.json',
                   _set(('state', 'software_digest'), OTHER),
                   'post-00 report disagrees with the raw board reading')
    expect_refusal(tmp_path / 'c', 'readback/post-00-preflight.json',
                   _set(('items', 'B5_services', 'services', 'rf-daemon', 'NRestarts'), '2'),
                   'post-00 services disagree with the raw reading')


def test_read_back_in_the_wrong_order_of_the_trial_is_refused(tmp_path):
    """The two read-backs are told apart by the staging paths the board itself listed.

    The first runs before the cleanup and must find both paths; the second runs after it
    and must find neither.  The rewrite here removes them from the listing, so only the
    order check can refuse.
    """
    def drop_staging_paths(sections):
        sections['TMP_RESIDUE'].clear()
        sections['TMP'] = [line for line in sections['TMP'] if 'rf-' not in line]
    expect_refusal(tmp_path / 'first', None, None,
                   'staging paths prove the read-back was taken at the wrong point',
                   paths=('post-00', drop_staging_paths))

    def add_staging_paths(sections):
        sections['TMP'].append('lrwxrwxrwx  1 root root 52 Dec  4 19:29 '
                               'rf-teaching-install-trial-s2-46db-1a30f4b7 -> /nowhere')
        sections['TMP_RESIDUE'].append('/tmp/rf-teaching-install-trial-s2-46db-1a30f4b7')
    expect_refusal(tmp_path / 'second', None, None,
                   'staging paths prove the read-back was taken at the wrong point',
                   paths=('post-01', add_staging_paths))

    def leave_residue(sections):
        sections['TMP_RESIDUE'].append('/tmp/rf-teaching-install-trial-someone-else')
        sections['TMP'].append('drwxrwxr-x  2 xilinx xilinx 4096 Dec  4 19:30 '
                               'rf-teaching-install-trial-someone-else')
    expect_refusal(tmp_path / 'residue', None, None,
                   'left another trial behind', paths=('post-01', leave_residue))

def test_recorded_facts_that_differ_from_the_reading_are_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/12-readback-facts.json',
                   _set(('post-01', 'software_digest'), HEX),
                   'recorded facts disagree with the read-back')
    expect_refusal(tmp_path / 'b', 'logs/12-readback-facts.json',
                   _set(('post-01', 'failures'), ['x']),
                   'recorded facts disagree with the read-back')
    expect_refusal(tmp_path / 'c', 'logs/12-readback-facts.json',
                   _set(('pre-01', 'release_id'), OTHER),
                   'recorded facts disagree with the pre read-back')

def test_pre_read_back_that_is_not_the_replaced_release_is_refused(tmp_path):
    expect_refusal(tmp_path, 'preflight/pre-01-preflight.json',
                   _set(('items', 'B1_release_identity', 'verified'), OTHER),
                   'pre read-back does not name the release it replaced',
                   state=('pre-01', lambda state: state.update(release_id=OTHER)))
    expect_refusal(tmp_path / 'b', 'preflight/pre-01-preflight.json',
                   _set(('items', 'B1_release_identity', 'active_release'), OTHER),
                   'pre read-back does not name the release it replaced',
                   state=('pre-01', lambda state: state.update(
                       release=OTHER, active_release=OTHER)))

# --------------------------------------------------------------------------- #
# Negative battery 4: the staged, downloaded and cleaned-up payload.
# --------------------------------------------------------------------------- #

def test_staged_payload_that_changed_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'trial/stage/acceptance.py'
    path.write_bytes(path.read_bytes() + b'# tampered' + bytes([10]))
    with pytest.raises(ValueError, match='stage does not match its local map'):
        prove(directory)
    directory = copy_evidence(tmp_path / 'rebound')
    path = directory / 'trial/stage/acceptance.py'
    path.write_bytes(path.read_bytes() + b'# tampered' + bytes([10]))
    _write(directory / 'trial/stage/stage-local-sha256.json',
           {name: D.sha(directory / 'trial/stage' / name) for name in D.TRIAL_STAGE_FILES})
    rebind_index(directory)
    with pytest.raises(ValueError, match='board stage differs from the local bytes'):
        prove(directory)


def test_board_stage_re_read_that_differs_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/upload-verify.log',
                   lambda text: text.replace('"count": 8', '"count": 7'),
                   'staged payload unverified', text=True)
    expect_refusal(tmp_path / 'binding', 'logs/stage-binding.log',
                   lambda text: text.replace('"alias_is_symlink": true',
                                             '"alias_is_symlink": false'),
                   'staged payload unverified', text=True)


def test_prepared_payload_that_differs_from_the_stage_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    path = directory / 'bundle/candidate.tgz'
    path.write_bytes(path.read_bytes() + b'x')
    rebind_index(directory)
    with pytest.raises(ValueError, match='prepared payload differs from the stage'):
        prove(directory)


def test_bundle_receipt_that_denies_the_candidate_is_refused(tmp_path):
    expect_refusal(tmp_path, 'bundle/bundle-receipt.json', _set(('installed',), True),
                   'bundle receipt unverified')
    expect_refusal(tmp_path / 'b', 'bundle/bundle-receipt.json',
                   _set(('parent_release',), OTHER), 'bundle receipt unverified')
    expect_refusal(tmp_path / 'c', 'logs/01-verify-bundle.json',
                   _set(('verify_bundle',), 'FAIL'), 'bundle build record unverified')


def test_cleanup_that_left_the_trial_behind_is_refused(tmp_path):
    expect_refusal(tmp_path, 'readback/cleanup.json', _set(('exit_code',), 1),
                   'cleanup record unverified')
    expect_refusal(tmp_path / 'b', 'readback/cleanup.json',
                   _set(('report', 'upload_removed'), False), 'cleanup record unverified')
    expect_refusal(tmp_path / 'c', 'readback/cleanup.json',
                   _set(('report', 'tmp_rf_residue'), ['/tmp/rf-teaching-install-other']),
                   'cleanup record unverified')


def test_parent_rollback_target_that_is_not_intact_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/16-parent-rollback-target.log',
                   lambda text: text.replace('PARENT_VERIFY_RC=0', 'PARENT_VERIFY_RC=1'),
                   'parent rollback target is not a verified directory', text=True)
    expect_refusal(tmp_path / 'b', 'logs/16-parent-rollback-target.log',
                   lambda text: text.replace('PARENT_REAL_DIR', 'PARENT_MISSING'),
                   'parent rollback target is not a verified directory', text=True)


def test_candidate_pins_that_disagree_are_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/00-candidate-pins.json', _set(('ok',), False),
                   'candidate pins unverified')


def test_acceptance_summary_that_contradicts_the_report_is_refused(tmp_path):
    expect_refusal(tmp_path, 'logs/07-acceptance-summary.txt',
                   lambda text: text.replace('9496eb407b92ca1039b65c280694086f2ca0f50ea635d426'
                                             '82a1f21fa2942727', HEX),
                   'acceptance summary unverified', text=True)


# --------------------------------------------------------------------------- #
# Negative battery 5: the evidence index and the archived frame.
# --------------------------------------------------------------------------- #

def test_evidence_index_that_does_not_bind_every_artefact_is_refused(tmp_path):
    directory = copy_evidence(tmp_path)
    index = _load(directory / D.TRIAL_V2_INDEX)
    index.pop(sorted(index)[0])
    _write(directory / D.TRIAL_V2_INDEX, index)
    with pytest.raises(ValueError, match='index does not bind every artefact'):
        prove(directory)
    directory = copy_evidence(tmp_path / 'outside')
    index = _load(directory / D.TRIAL_V2_INDEX)
    index['evidence/rf-teaching/other-package/logs/x.json'] = dict(bytes=1, sha256=HEX)
    _write(directory / D.TRIAL_V2_INDEX, index)
    with pytest.raises(ValueError, match='index binds a file outside the package'):
        prove(directory)
    directory = copy_evidence(tmp_path / 'self')
    rebind_index(directory)
    index = _load(directory / D.TRIAL_V2_INDEX)
    index['evidence/rf-teaching/' + directory.name + '/' + D.TRIAL_V2_INDEX] = dict(
        bytes=1, sha256=HEX)
    _write(directory / D.TRIAL_V2_INDEX, index)
    with pytest.raises(ValueError, match='index does not bind every artefact'):
        prove(directory)


def test_frame_that_is_not_the_archived_baseline_frame_is_refused(tmp_path):
    """A frame that is restaged consistently but is not the baseline's archived frame.

    The header is a staged payload member, so the forgery rebuilds the local map, the
    board's two re-reads, the intent and the dispatch intent first; only then can the
    frame's own content checks be the thing that refuses.
    """
    def restaged(case, relative, mutate, needle):
        directory = copy_evidence(case)
        mutate_json(directory, relative, mutate)
        restage_payload(directory)
        rebind_index(directory)
        with pytest.raises(ValueError, match=needle):
            prove(directory)
        return directory
    restaged(tmp_path / 'late', 'trial/stage/frames/attempt-2/header.json',
             _set(('header', 'timestamp_ns'), 99999999999999999),
             'archived frame is not the release baseline frame')
    restaged(tmp_path / 'session', 'trial/stage/frames/attempt-2/header.json',
             _set(('header', 'headroom', 'session_id'), OTHER),
             'archived frame is not the release baseline frame')
    restaged(tmp_path / 'digest', 'trial/stage/frames/attempt-2/header.json',
             _set(('header', 'headroom', 'iq_sha256'), HEX),
             'archived frame is not the release baseline frame')

def test_frame_that_the_acceptance_did_not_use_is_refused(tmp_path):
    """The acceptance's own frames must be the digest the journal records it used."""
    def forge(report):
        for item in report['checks']:
            if item['name'] == 'f_archived_iq_smoke':
                item['detail']['demod']['bit_errors'] = 3
    directory = forged_acceptance(copy_evidence(tmp_path), forge)
    rebind_index(directory)
    with pytest.raises(ValueError, match='archived-frame smoke test unverified'):
        prove(directory)

# --------------------------------------------------------------------------- #
# The gates and the CLI.
# --------------------------------------------------------------------------- #

def test_prepare_and_verify_bundle_round_trip_on_the_new_format_parent(tmp_path):
    directory = copy_evidence(tmp_path / 'evidence')
    output = tmp_path / 'bundle'
    receipt = D.prepare(HEADROOM_CANDIDATE, HEADROOM_ARM, CANDIDATE, None,
                        HEADROOM_CHECKPOINT, output, None, directory, FORMAT)
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_trial_acceptance'] == str(directory.resolve())
    assert receipt['parent_trial_parent'] == TRIAL_PARENT
    assert receipt['parent_trial_acceptance_format'] == FORMAT
    assert receipt['parent_install'] is None and receipt['parent_activation'] is None
    verified = D.verify_bundle(output)
    assert verified['parent_release'] == RELEASE
    assert verified['parent_trial_acceptance_sha256'] == receipt[
        'parent_trial_acceptance_sha256']


def test_the_cli_refuses_the_format_flag_without_the_evidence(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--parent-trial-acceptance-format', FORMAT, '--output', str(tmp_path / 'bundle')],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 2
    assert '--parent-trial-acceptance-format needs --parent-trial-acceptance' in completed.stderr
    assert not (tmp_path / 'bundle').exists()


def test_the_headroom_cli_prepares_against_the_new_format_parent(tmp_path):
    output = tmp_path / 'bundle'
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--parent-trial-acceptance', str(EVIDENCE),
         '--parent-trial-acceptance-format', FORMAT, '--output', str(output)],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = _load(output / 'bundle-receipt.json')
    assert receipt['parent_release'] == RELEASE
    assert receipt['parent_trial_acceptance_format'] == FORMAT
    assert D.verify_bundle(output)['parent_release'] == RELEASE


def test_the_cli_refuses_the_old_format_for_the_new_package(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/rf_headroom_deploy.py'), 'prepare',
         '--candidate', str(HEADROOM_CANDIDATE), '--arm', str(HEADROOM_ARM),
         '--parent-candidate', str(CANDIDATE), '--checkpoint', str(HEADROOM_CHECKPOINT),
         '--parent-trial-acceptance', str(EVIDENCE),
         '--parent-trial-acceptance-format', OLD_FORMAT, '--output', str(tmp_path / 'bundle')],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert 'trial acceptance evidence trial.json missing' in (completed.stdout + completed.stderr)

