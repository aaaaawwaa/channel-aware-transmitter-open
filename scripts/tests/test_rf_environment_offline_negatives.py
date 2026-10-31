"""Tests for the 12 offline S2 environment negative controls (N1..N12).

No board, no network, no RF, no new physical frame: every case is a mathematical replay
of one sealed real IQ frame (plus four declared constructed payloads: N4/N9/N11/N12,
declared in `data_sources.constructed`) judged by the production functions.  Three
domain-check tests open a production refusal and require the corresponding case to fail,
so the cases are proven sensitive to the rules they claim; the campaign-level tests below
them pin the offline-rejection wiring of the formal entry.

2026-10-17 M2 bootstrap: the product baseline file is now the 46 dB *provisional*
development baseline (one real 46 dB normal frame, n=1, not_for_formal_acceptance), so
the campaign runs again in the current single domain.  The provisional labels cannot live
in the baseline file -- the frozen schema admits exactly the eight body keys plus sha256
-- so the receipt records them and the loader binds the provenance; the campaign must
still refuse or named-reject all twelve cases and never judge one CLEAR.  The historical
40 dB/v3 receipt and its baseline bytes stay frozen evidence: the receipt's own digest is
pinned here, its baseline is archived byte-identically, and the archived body is still
refused by name.  The provisional baseline is loadable but is explicitly *not* a formal
acceptance baseline: the formal one still has to be fitted from a real 46 dB TRAIN
campaign (M5/M6) and re-signed.
"""
import ast
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'pynq_overlay' / 'sw')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_acceptance as ACC       # noqa: E402
from scripts import rf_environment_offline_negatives as N  # noqa: E402
import rf_environment as ENV                               # noqa: E402
import rf_rx_analysis as RXA                               # noqa: E402
import ui_model as UI                                      # noqa: E402

FROZEN_BASELINE_SHA256 = '64a00c6a2975252ff2c0fa055318b06912084b07dc5db759b784e95b131501a3'
# The delivered 40 dB receipt and its baseline bytes are frozen history: the receipt file
# digest and the archived byte-identical baseline are both pinned.
FROZEN_RECEIPT_FILE_SHA256 = '3ca4b1f9d184a1a3a0a2e7f30cce6e75c63f464b484b15650fa60e03403c3066'
ARCHIVED_40DB_BASELINE = (ROOT / 'evidence' / 'rf-teaching' /
                          's2-46db-m2-bootstrap-2026-10-17' / 'work' / 'baseline-archive' /
                          'rf_environment_baseline.v3-40db.721baa769743.json')
M2_RECEIPT = (ROOT / 'evidence' / 'rf-teaching' / 's2-46db-m2-bootstrap-2026-10-17' /
              'work' / 'offline-negatives-receipt.json')
FROZEN_LABELS = ('CLEAR', 'NARROWBAND', 'PULSE', 'UNKNOWN', 'INVALID')
VERDICT_KINDS = ('REJECTED', 'STALE', 'UNKNOWN', 'INVALID')
EVIDENCE_RECEIPT = (ROOT / 'evidence' / 'rf-teaching' /
                    's2-offline-negatives-2026-10-14' / 'receipt.json')
MODULE_PATH = ROOT / 'scripts' / 'rf_environment_offline_negatives.py'
FROZEN_THRESHOLD_LITERALS = ('1.5921870687410116', '1.514364175465688',
                             '1.3869868475054137')
FORBIDDEN_IMPORTS = ('socket', 'paramiko', 'requests', 'urllib', 'http', 'ftplib',
                     'telnetlib', 'scp', 'serial', 'subprocess', 'pynq')
COMPARED_KEYS = ('producer', 'baseline', 'freeze', 'sealed', 'control', 'cases',
                 'counts', 'passed', 'failed_ids', 'cleared_ids', 'data_sources')


@pytest.fixture(scope='module')
def receipt():
    """The delivered 40 dB receipt, kept as frozen historical evidence.

    It was produced before the 46 dB migration and is never rewritten; the campaign now
    runs live in the current domain through the `live` fixture below.
    """
    return json.loads(EVIDENCE_RECEIPT.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def live():
    """N1..N12 re-run against the shipped provisional 46 dB baseline (production code)."""
    return N.run()


def by_id(value):
    return {case['id']: case for case in value['cases']}


def compared(value):
    return {key: value[key] for key in COMPARED_KEYS}


def test_receipt_carries_exactly_the_twelve_frozen_cases_in_order(receipt):
    assert [case['id'] for case in receipt['cases']] == list(N.CASE_IDS)
    assert len(receipt['cases']) == 12
    assert receipt['counts']['cases'] == 12
    assert receipt['contract'].endswith('#2.1')
    for case in receipt['cases']:
        assert case['expected'] == N.CASE_EXPECTED[case['id']]
    assert 'offline mathematical replay' in receipt['scope']
    assert all(word in receipt['scope'] for word in ('no board', 'no network', 'no RF'))


@pytest.mark.parametrize('case_id', N.CASE_IDS)
def test_case_is_refused_or_named_and_never_clear(receipt, case_id):
    case = by_id(receipt)[case_id]
    assert case['expectation_met'] is True, case
    assert case['passed'] is True
    assert case['cleared'] is False
    assert case['verdict_kind'] in VERDICT_KINDS
    assert case['diagnosis']['label'] in FROZEN_LABELS
    assert case['adjudication_accepted'] in (True, False)
    # The contract wording: refused, or diagnosed UNKNOWN/INVALID - never CLEAR.
    refused = any(check['accepted'] is False for check in case['checks'])
    assert refused or case['diagnosis']['label'] in ('UNKNOWN', 'INVALID')
    assert case['cleared'] == (case['adjudication_accepted']
                               and case['diagnosis']['label'] == 'CLEAR')
    # Every case names the production function that decided it and its raw outcome.
    assert case['checks']
    for check in case['checks']:
        assert check['production']
        assert check['outcome']
        assert check['accepted'] in (True, False)
    if case['verdict_kind'] in ('UNKNOWN', 'INVALID'):
        assert case['verdict_reason'] == case['diagnosis']['reason']
    assert len(case['input']['iq_sha256']) == 64
    assert int(case['input']['iq_sha256'], 16) >= 0
    assert case['input']['iq_bytes'] > 0


def test_no_case_is_ever_judged_clear(receipt):
    assert receipt['passed'] is True
    assert receipt['counts'] == {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}
    assert receipt['cleared_ids'] == []
    assert receipt['failed_ids'] == []
    assert all(case['cleared'] is False for case in receipt['cases'])
    assert all(case['diagnosis']['label'] != 'CLEAR'
               or case['adjudication_accepted'] is False
               for case in receipt['cases'])


def test_uninjected_control_is_admitted_and_clear_so_the_check_is_not_vacuous(receipt):
    control = receipt['control']
    assert control['accepted'] is True
    assert control['cleared'] is True
    assert control['passed'] is True
    assert control['diagnosis'] == {'label': 'CLEAR',
                                    'reason': 'NO_COVERED_ANOMALY_IN_WINDOW',
                                    'environment_version': 'rf-window-environment-v3',
                                    'algorithm_version': 'bpsk-rect-snapshot-v2',
                                    'baseline_sha256': FROZEN_BASELINE_SHA256,
                                    'submitted_revision': 1}
    assert control['checks'][0]['outcome'] == 'ACCEPTED'
    assert control['input']['source'] == 'sealed real IQ (read-only)'


def test_baseline_and_freeze_are_the_frozen_ones(receipt):
    assert receipt['baseline']['canonical_sha256'] == FROZEN_BASELINE_SHA256
    assert receipt['freeze']['baseline_sha256'] == FROZEN_BASELINE_SHA256
    assert receipt['baseline']['environment_version'] == 'rf-window-environment-v3'
    assert receipt['baseline']['algorithm_version'] == 'bpsk-rect-snapshot-v2'
    assert receipt['baseline']['source_count'] == 20
    assert receipt['baseline']['calibration_status'] == 'development'
    # The declaration is the 40 dB/v3 round; the migrated single domain is
    # ENV.VERSION, which is exactly why the re-run above refuses it.
    assert receipt['freeze']['environment_version'] == 'rf-window-environment-v3'
    assert receipt['freeze']['environment_version'] != ENV.VERSION
    assert receipt['freeze']['algorithm_version'] == 'bpsk-rect-snapshot-v2'
    assert receipt['freeze']['expected_frames'] == 108
    assert receipt['freeze']['reference_seeds'] == [1, 37, 73]
    software = receipt['freeze']['software']
    assert set(software) == set(N.SOFTWARE_NAMES)
    # These are the digests the 40 dB round bound.  The migrated files have their own
    # digests: a re-signed declaration is the M11 step that rebinds them.  The
    # baseline file itself is untouched, so its bytes are still asserted here.
    assert all(isinstance(digest, str) and len(digest) == 64 for digest in software.values())
    # The baseline those bytes belong to is archived byte-identically by the M2
    # bootstrap; the live product file is now the provisional 46 dB one below.
    assert ARCHIVED_40DB_BASELINE.is_file(), ARCHIVED_40DB_BASELINE
    assert receipt['baseline']['file_sha256'] == N._file_sha256(ARCHIVED_40DB_BASELINE)
    assert receipt['baseline']['file_sha256'] != N._file_sha256(N.BASELINE_PATH)


def test_sealed_data_source_is_recorded_with_digests(receipt):
    sealed = receipt['sealed']
    assert sealed['tag'] == 's00-normal-00'
    assert sealed['iq_bytes'] == 16384
    assert 'board-pilot-2026-10-05-v2' in sealed['paths']['iq']
    for name, digest in sealed['digests'].items():
        path = ROOT / sealed['paths'][name]
        assert digest == N._file_sha256(path), name
    assert sealed['digests']['iq'] == sealed['iq_sha256']
    assert sealed['statement'].startswith('real physical')
    real = receipt['data_sources']['real']
    assert [entry['tag'] for entry in real] == ['s00-normal-00']
    constructed = {entry['case'] for entry in receipt['data_sources']['constructed']}
    assert constructed == {'N4', 'N9', 'N11', 'N12'}
    for entry in receipt['data_sources']['constructed']:
        assert entry['how']


def test_receipt_is_json_serializable_and_bounded(receipt):
    encoded = json.dumps(receipt, allow_nan=False, sort_keys=True)
    assert '"CLEAR"' in encoded
    assert len(encoded) < 200000
    round_trip = json.loads(encoded)
    assert round_trip['cases'] == receipt['cases']


def test_the_archived_40db_baseline_is_still_refused():
    """The replaced 40 dB/v3 bytes stay archived and stay refused, by name.

    Nothing re-signs or relabels them; a formal 46 dB baseline is a different file.
    """
    assert ARCHIVED_40DB_BASELINE.is_file(), ARCHIVED_40DB_BASELINE
    stored = json.loads(ARCHIVED_40DB_BASELINE.read_text(encoding='utf-8'))
    assert stored['rf_config']['attenuation_mdB'] == 40000
    assert stored['environment_version'] == 'rf-window-environment-v3'
    with pytest.raises(ValueError, match='unsupported frozen baseline version'):
        ENV.EnvironmentBaseline.from_dict(stored)


def test_the_live_set_runs_on_the_provisional_baseline_and_never_clears(live):
    """The provisional bootstrap loads, labels itself, and still clears no negative case."""
    assert live['passed'] is True
    assert live['counts'] == {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}
    assert live['cleared_ids'] == []
    assert live['failed_ids'] == []
    assert live['control']['passed'] is True
    assert live['control']['diagnosis']['label'] == 'CLEAR'
    baseline = live['baseline']
    assert baseline['file_sha256'] == N._file_sha256(N.BASELINE_PATH)
    assert baseline['canonical_sha256'] == N.PROVISIONAL_CANONICAL_SHA256
    assert baseline['environment_version'] == ENV.VERSION
    assert baseline['algorithm_version'] == 'bpsk-rect-snapshot-v2'
    assert baseline['calibration_status'] == 'development'
    assert baseline['source_count'] == 1
    # The 8+1 key frozen schema cannot carry the provisional labels, so the receipt is
    # where they are recorded -- and the loader binds the provenance that backs them.
    assert baseline['provisional'] is True
    assert baseline['formal_acceptance_baseline'] is False
    assert baseline['n'] == 1
    assert baseline['not_for_formal_acceptance'] is True
    assert baseline['source_frame_sha256'] == N.PROVISIONAL_SOURCE_FRAME_IQ_SHA256
    assert live['freeze']['baseline_sha256'] == N.PROVISIONAL_CANONICAL_SHA256
    assert live['freeze']['environment_version'] == ENV.VERSION
    assert live['freeze']['expected_frames'] == 108


def test_a_drifted_or_annotated_baseline_file_refuses_by_name(tmp_path):
    """Fail closed: the campaign binds the pinned provenance, not just any parseable file."""
    value = json.loads(N.BASELINE_PATH.read_text(encoding='utf-8'))
    drifted = tmp_path / 'drifted.json'
    drifted.write_text(json.dumps(dict(value, calibration_status='frozen')), encoding='utf-8')
    with pytest.raises(ValueError, match='baseline canonical SHA mismatch'):
        N.load_baseline(drifted)
    annotated = tmp_path / 'annotated.json'
    annotated.write_text(json.dumps(dict(value, provisional=True)), encoding='utf-8')
    with pytest.raises(ValueError, match='baseline schema fields mismatch'):
        N.load_baseline(annotated)
    rehearsal = tmp_path / 'rehearsal.json'
    rehearsal.write_text(json.dumps(dict(value, calibration_status='frozen',
                                         sha256='0' * 64)), encoding='utf-8')
    with pytest.raises(ValueError):
        N.load_baseline(rehearsal)


def test_second_run_reproduces_every_case(live):
    """Restored from HEAD~1: two runs of the live set agree on every compared field.

    It was dropped together with the three mutation tests during the 46 dB migration; the
    mutant matrix came back in the current domain, so the determinism check comes back too.
    """
    again = N.run()
    assert compared(again) == compared(live)
    assert again['generated_utc'] and live['generated_utc']


def test_the_frozen_40db_receipt_is_byte_pinned_and_the_m2_receipt_is_live_output(live):
    """The 40 dB receipt is frozen history; the shipped M2 receipt is this module's output."""
    assert EVIDENCE_RECEIPT.is_file(), EVIDENCE_RECEIPT
    assert N._file_sha256(EVIDENCE_RECEIPT) == FROZEN_RECEIPT_FILE_SHA256
    delivered = json.loads(EVIDENCE_RECEIPT.read_text(encoding='utf-8'))
    assert delivered['producer']['file_sha256'] != N._file_sha256(MODULE_PATH)
    assert delivered['counts'] == live['counts']
    assert M2_RECEIPT.is_file(), M2_RECEIPT
    current = json.loads(M2_RECEIPT.read_text(encoding='utf-8'))
    assert current['producer']['file_sha256'] == N._file_sha256(MODULE_PATH)
    assert current['counts'] == live['counts']
    assert current['baseline']['canonical_sha256'] == live['baseline']['canonical_sha256']
    assert current['baseline']['provisional'] is True
    assert current['passed'] is True
    assert ACC.load_offline_rejection(M2_RECEIPT) == live['counts']


def test_module_uses_production_decisions_not_private_thresholds():
    text = MODULE_PATH.read_text(encoding='utf-8')
    for literal in FROZEN_THRESHOLD_LITERALS:
        assert literal not in text, literal
    tree = ast.parse(text)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert not (imported & set(FORBIDDEN_IMPORTS)), imported & set(FORBIDDEN_IMPORTS)
    for required in ('rf_environment', 'rf_bpsk_pipeline', 'rf_bpsk_receiver',
                     'rf_rx_analysis', 'ui_model', 'scripts'):
        assert required in imported


@pytest.mark.parametrize('mutation,expected_failed,expected_cleared', [
    ('formal-gate', ['N1', 'N3', 'N4', 'N5', 'N6', 'N7', 'N10', 'N12'], ['N1', 'N7']),
    ('snapshot-window', ['N2'], ['N2']),
    ('domain-check', ['N3', 'N5'], [])])
def test_the_mutants_are_caught_by_the_provisional_set(monkeypatch, mutation,
                                                       expected_failed, expected_cleared):
    """Re-proved on the live provisional set: each mutant makes named cases fail or clear.

    The mutants are the reviewers' own three; the expected id sets are the raw production
    outputs recorded in the M2 evidence area (work/mutation-matrix.json), so a mutant that
    stops being caught fails here instead of passing silently.
    """
    calls = {'formal-gate': 0, 'snapshot-window': 0, 'domain-check': 0}
    if mutation == 'formal-gate':
        def accept(row, tag, kind, seed, output, release, freeze):
            calls['formal-gate'] += 1
            return (tuple(row.get(name) for name in ACC.IDENTITY),
                    row.get('iq_sha256'))
        monkeypatch.setattr(ACC, '_validate_frame', accept)
    elif mutation == 'snapshot-window':
        def fresh(*args, **kwargs):
            calls['snapshot-window'] += 1
            return True
        monkeypatch.setattr(RXA, 'snapshot_is_fresh', fresh)
        monkeypatch.setattr(UI, 'snapshot_is_fresh', fresh)
    else:
        def no_domain_error(*args, **kwargs):
            calls['domain-check'] += 1
            return None
        monkeypatch.setattr(ENV, '_domain_error', no_domain_error)
    result = N.run()
    # Anti-vacuity: a mutant nothing calls proves nothing.  The archived 40 dB baseline
    # (the pre-M2 run) raised before any rule was reached and left these counters at
    # 0/0/0 while the assertions still passed -- that is the defect this guard closes.
    assert calls[mutation] > 0, ('the mutated rule %r was never called, so this mutant '
                                 'is vacuous' % mutation)
    assert result['passed'] is False
    assert result['failed_ids'] == expected_failed
    assert result['cleared_ids'] == expected_cleared
    assert result['counts']['cleared'] == len(expected_cleared)


# --- the measured set is what the formal campaign records (freeze spec 2.1) -------------

def test_offline_rejection_default_stays_not_run():
    """No offline set supplied -> the historic marker, never a silent pass."""
    assert ACC.offline_rejection_result(None) == 'NOT_RUN'


def test_offline_rejection_records_the_measured_counts():
    measured = {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}
    assert ACC.offline_rejection_result(measured) == measured


def test_offline_rejection_refuses_a_failing_or_malformed_set():
    for bad in ({'cases': 12, 'passed': 11, 'failed': 1, 'cleared': 0},
                {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 1},
                {'cases': 12, 'passed': 12},
                'NOT_RUN',
                {'cases': '12', 'passed': 12, 'failed': 0, 'cleared': 0}):
        with pytest.raises(ValueError):
            ACC.offline_rejection_result(bad)


def test_campaign_carries_the_offline_rejection_parameter():
    """The formal campaign must accept the measured set explicitly (no hidden default)."""
    import inspect
    signature = inspect.signature(ACC.campaign)
    assert 'offline_rejection' in signature.parameters
    assert signature.parameters['offline_rejection'].default is None


def test_the_delivered_receipt_counts_are_what_the_campaign_would_record():
    """The shipped receipt must be acceptable to the production gate as-is."""
    shipped = json.loads(EVIDENCE_RECEIPT.read_text(encoding='utf-8'))
    assert ACC.offline_rejection_result(shipped['counts']) == shipped['counts']


FROZEN_FREEZE_DECLARATION = (ROOT / 'evidence' / 'rf-teaching' /
                             's2-freeze-declaration-2026-10-14' / 'freeze-declaration.json')
BOARD_SESSION_SOURCE = ROOT / 'scripts' / 'rf_environment_board_session.py'


class _RefusingSession:
    """A session that fails immediately: the campaign must never reach hardware."""

    def initial(self):
        raise RuntimeError('offline test session: no board, no RF')

    def close(self):
        return None


def _frozen_declaration():
    return json.loads(FROZEN_FREEZE_DECLARATION.read_text(encoding='utf-8'))


def test_campaign_records_the_offline_rejection_it_was_given(tmp_path):
    """M6 regression: the campaign must USE the value, not silently keep 'NOT_RUN'."""
    freeze = _current_declaration()
    measured = {'cases': 12, 'passed': 12, 'failed': 0, 'cleared': 0}
    result = ACC.campaign(lambda: _RefusingSession(), tmp_path,
                          freeze['expected_release'], freeze, offline_rejection=measured)
    assert result['offline_rejection'] == measured
    assert json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))['offline_rejection'] == measured
    assert result['formal_rf_passed'] is False


def test_campaign_refuses_a_failing_offline_set_before_any_session(tmp_path):
    freeze = _current_declaration()
    calls = []
    def factory():
        calls.append(1)
        return _RefusingSession()
    with pytest.raises(ValueError):
        ACC.campaign(factory, tmp_path, freeze['expected_release'], freeze,
                     offline_rejection={'cases': 12, 'passed': 0, 'failed': 12, 'cleared': 0})
    assert calls == []


def test_load_offline_rejection_reads_the_shipped_receipt():
    shipped = json.loads(EVIDENCE_RECEIPT.read_text(encoding='utf-8'))
    assert ACC.load_offline_rejection(EVIDENCE_RECEIPT) == shipped['counts']
    # And the current provisional-set receipt is accepted by the same production gate.
    current = json.loads(M2_RECEIPT.read_text(encoding='utf-8'))
    assert ACC.load_offline_rejection(M2_RECEIPT) == current['counts']
    assert ACC.offline_rejection_result(current['counts']) == current['counts']


def test_load_offline_rejection_refuses_missing_or_malformed(tmp_path):
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(tmp_path / 'absent.json')
    broken = tmp_path / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(broken)
    nocounts = tmp_path / 'nocounts.json'
    nocounts.write_text(json.dumps({'passed': True}), encoding='utf-8')
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(nocounts)
    failing = tmp_path / 'failing.json'
    failing.write_text(json.dumps({'counts': {'cases': 12, 'passed': 0, 'failed': 12, 'cleared': 0}}),
                       encoding='utf-8')
    with pytest.raises(ValueError):
        ACC.load_offline_rejection(failing)


def test_the_formal_entry_wires_the_measured_receipt():
    """The 108-frame entry (board session) must carry the measured set and refuse without it.

    Static by necessity: that module imports board-only packages, so it cannot be
    imported here; the text below is the wiring the reviewer required."""
    source = BOARD_SESSION_SOURCE.read_text(encoding='utf-8')
    assert 'load_offline_rejection(a.offline_rejection)' in source
    assert 'offline_rejection=load_offline_rejection' in source
    assert 'a formal TEST run requires --offline-rejection' in source
    assert "'--offline-rejection'" in source


def _current_declaration():
    """A freeze declaration in the current single domain, for wiring tests only.

    It binds no baseline bytes (the M6 wiring under test never loads one).
    """
    from rf_experiment_scenarios import ScenarioConfig, generate_scenario
    import rf_bpsk_receiver as BPSK
    return dict(expected_release='release', environment_version=ENV.VERSION,
        baseline_sha256='b' * 64, algorithm_version=BPSK.VERSION,
        fixed_rf=dict(ACC.FIXED_RF), software={name: 'a' * 64 for name in N.SOFTWARE_NAMES},
        scene_declarations={kind: generate_scenario(ScenarioConfig(kind=kind)).declaration_dict()
                            for kind in N.SCENE_KINDS},
        rx_config=dict(ACC.FIXED_RX), reference_seeds=[1, 37, 73], actual_scene_prbs7_seed=1,
        split='test', expected_frames=108, calibration_iq_sha256=[])


def test_the_frozen_40db_declaration_is_refused_by_the_current_domain():
    """The declaration on disk binds the historical 40 dB domain; it stays refused."""
    freeze = _frozen_declaration()
    assert freeze['fixed_rf']['attenuation_mdB'] == 40000
    with pytest.raises(ValueError, match='domain/version mismatch'):
        ACC.validate_freeze(freeze, freeze['expected_release'])
