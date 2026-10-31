"""Export identity and independent evaluation gates; CPU checkpoint is host-only."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pynq_overlay' / 'sw'))


@pytest.fixture
def export_case(tmp_path):
    import torch
    from scripts.rf_ml.train import CLASS_ORDER, build_network
    model = tmp_path / 'candidate'; model.mkdir()
    training_manifest = tmp_path / 'training-manifest.json'
    evaluation_manifest = tmp_path / 'evaluation-manifest.json'
    training_manifest.write_text('{"schema_version":1,"sessions":[],"frames":[]}')
    evaluation_manifest.write_text('{"schema_version":1,"sessions":[],"frames":[]}')
    training_hash = hashlib.sha256(training_manifest.read_bytes()).hexdigest()
    evaluation_hash = hashlib.sha256(evaluation_manifest.read_bytes()).hexdigest()
    torch.save(dict(model_state_dict=build_network().state_dict(), preprocessing='dc-complex-rms-v1',
                    class_order=list(CLASS_ORDER), manifest_sha256=training_hash), model / 'model.pt')
    meta = dict(preprocessing='dc-complex-rms-v1', class_order=list(CLASS_ORDER),
                manifest_sha256=training_hash, manifest=str(training_manifest))
    (model / 'metadata.json').write_text(json.dumps(meta))
    (model / 'thresholds.json').write_text(json.dumps(dict(energy_floor_db=0,
        selection=dict(max_softmax=.5, margin=.1))))
    report = dict(schema_version=1, preprocessing='dc-complex-rms-v1', origin='synthetic', split='test',
        model_checkpoint_sha256=hashlib.sha256((model/'model.pt').read_bytes()).hexdigest(),
        training_manifest_sha256=training_hash, evaluation_manifest_sha256=evaluation_hash,
        manifest=str(evaluation_manifest), lineage_verified=True, production_ready=False, acceptance=False,
        supported_domain=dict(input_fs_hz=60000000, decim=[1],
            symbol_rates_hz=[2000000, 3000000, 5000000], shaping=['RECT', 'RRC0.35']),
        macro_f1=.95, unknown_rejection_rate=.95, off_rejection_rate=.95,
        per_class_recall={c: .95 for c in CLASS_ORDER[:-1]},
        class_sessions={c: 3 for c in CLASS_ORDER}, class_frames={c: 30 for c in CLASS_ORDER},
        off_sessions=3, off_frames=30, thresholds=dict(max_softmax=.5, margin=.1), energy_floor_db=0)
    evaluation = tmp_path / 'evaluation.json'; evaluation.write_text(json.dumps(report))
    return model, evaluation, tmp_path / 'export', report


def test_experimental_export_reuses_release_contract_and_runtime(export_case):
    from scripts.rf_ml.export import export_model
    from merged_release import verify_model_artifact
    from rf_cnn_runtime import load_model
    model, evaluation, output, _ = export_case
    artifact = export_model(model, evaluation, output, experimental=True)
    assert artifact['status'] == 'experimental'
    verify_model_artifact(output, artifact, 'cnn-v1')
    runtime = load_model(output / artifact['model_file'], artifact['model_sha256'])
    assert runtime.forward(np.zeros((2,4096), np.float32)).shape == (6,)
    assert sum(p.stat().st_size for p in output.iterdir()) <= 128*1024
    with pytest.raises((FileExistsError, ValueError)): export_model(model, evaluation, output, experimental=True)


@pytest.mark.parametrize('corruption', ['old_preprocess', 'checkpoint_hash', 'training_hash', 'split', 'domain', 'nan'])
def test_export_refuses_unbound_or_invalid_evidence(export_case, corruption):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, report = export_case
    if corruption == 'old_preprocess':
        meta = json.loads((model/'metadata.json').read_text()); meta['preprocessing'] = 'old-rms'
        (model/'metadata.json').write_text(json.dumps(meta))
    if corruption == 'checkpoint_hash': report['model_checkpoint_sha256'] = '0'*64
    if corruption == 'training_hash': report['training_manifest_sha256'] = '0'*64
    if corruption == 'split': report['split'] = 'val'
    if corruption == 'domain': report['supported_domain']['decim'] = [2]
    if corruption == 'nan': report['macro_f1'] = float('nan')
    evaluation.write_text(json.dumps(report))
    with pytest.raises(ValueError): export_model(model, evaluation, output, experimental=True)
    assert not output.exists()


@pytest.mark.parametrize('gate', ['synthetic', 'macro_f1', 'recall', 'unknown', 'off', 'sessions', 'frames'])
def test_production_export_requires_all_acceptance_gates(export_case, gate):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, report = export_case
    report['origin'] = 'cabled'
    if gate == 'synthetic': report['origin'] = 'synthetic'
    if gate == 'macro_f1': report['macro_f1'] = .89
    if gate == 'recall': report['per_class_recall']['TONE'] = .79
    if gate == 'unknown': report['unknown_rejection_rate'] = .89
    if gate == 'off': report['off_rejection_rate'] = .89
    if gate == 'sessions': report['class_sessions']['BPSK'] = 2
    if gate == 'frames': report['class_frames']['OTHER'] = 29
    evaluation.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='production'): export_model(model, evaluation, output)


@pytest.mark.parametrize('which', ['training', 'evaluation'])
def test_export_checks_actual_manifest_identity(export_case, which):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, report = export_case
    path = Path(report['manifest']) if which == 'evaluation' else Path(json.loads((model/'metadata.json').read_text())['manifest'])
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='manifest.*hash'): export_model(model, evaluation, output, experimental=True)
    assert not output.exists()


def test_production_cannot_use_fabricated_coverage_flags(export_case):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, report = export_case
    report.update(origin='cabled', production_ready=True, acceptance=True)
    evaluation.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='production.*coverage'): export_model(model, evaluation, output)




def _full_coverage_matrix(root, splits):
    """Build coverage-complete physical manifest records (mini IQ files)."""
    import copy, struct
    cells = [("TONE", None, None, None), ("OFF", None, None, None)]
    cells += [("OTHER", k, None, None) for k in ("NOISE", "CHIRP", "SQUARE", "MIXED")]
    cells += [(label, None, rate, shape) for label in ("BPSK", "QPSK", "QAM16", "QAM64")
              for rate in (2000000, 3000000, 5000000) for shape in ("RECT", "RRC0.35")]
    records, sessions = [], {}
    base = None
    for split in splits:
        for ci, (label, kind, rate, shape) in enumerate(cells):
            for session_no in range(3):
                session = "%s-cell%d-session%d" % (split, ci, session_no)
                sessions[session] = {"session_id": session, "split": split,
                                     "origin": "physical", "split_frozen_at_utc": "2026-10-02T01:00:00Z"}
                for i in range(10):
                    number = len(records) + 1
                    name = "cov-%d.iq" % number
                    payload = struct.pack("<4h", number % 1024, number // 1024, 5, -5) + bytes(16376)
                    (root / name).write_bytes(payload)
                    records.append({
                        "capture_id": "c%d" % number, "session_id": session, "split": split,
                        "origin": "physical", "label": label, "other_kind": kind,
                        "iq_path": name, "iq_sha256": hashlib.sha256(payload).hexdigest(),
                        "format": "s16_iq_le", "samples": 4096, "release_id": "a" * 32,
                        "configuration": {"sample_rate_hz": 60000000, "ratio": 0,
                                          "lo_hz": 80000000, "bandwidth_hz": 30000000},
                        "tx_reference": None if label == "OFF" else {
                            "seed_family": session, "symbol_rate_hz": rate, "shaping": shape},
                        "agc": {"mode": "slow_attack", "gain_db": None},
                        "captured_at_utc": "2026-10-02T01:00:01Z", "completed_monotonic_s": 42.0,
                        "complete": True, "clipping_count": 0, "exclusion_reasons": [],
                        "augmentation_parent_id": None})
    return {"schema_version": 1, "sessions": list(sessions.values()), "frames": records}


def _production_report(model, evaluation_path, training_hash, evaluation_hash, root_eval):
    from scripts.rf_ml.train import CLASS_ORDER
    return dict(schema_version=1, preprocessing="dc-complex-rms-v1", origin="cabled", split="test",
        model_checkpoint_sha256=hashlib.sha256((model / "model.pt").read_bytes()).hexdigest(),
        training_manifest_sha256=training_hash, evaluation_manifest_sha256=evaluation_hash,
        manifest=str(evaluation_path), root=str(root_eval),
        lineage_verified=True, production_ready=True, acceptance=True, model_capability=True,
        status="production", model_status="production",
        supported_domain=dict(input_fs_hz=60000000, decim=[1],
            symbol_rates_hz=[2000000, 3000000, 5000000], shaping=["RECT", "RRC0.35"]),
        macro_f1=.95, unknown_rejection_rate=.95, off_rejection_rate=.95,
        per_class_recall={c: .95 for c in CLASS_ORDER[:-1]},
        class_sessions={c: 3 for c in CLASS_ORDER}, class_frames={c: 30 for c in CLASS_ORDER},
        off_sessions=3, off_frames=30, thresholds=dict(max_softmax=.5, margin=.1), energy_floor_db=0)


def test_production_export_accepts_no_test_contact_training_manifest(tmp_path):
    """Amendment-4: train/val-only training manifest (no test contact) with complete
    train/val coverage and a full evaluation manifest must export as production."""
    import torch
    from scripts.rf_ml.export import export_model
    from scripts.rf_ml.train import CLASS_ORDER, build_network
    root_train = tmp_path / "train-data"; root_train.mkdir()
    root_eval = tmp_path / "eval-data"; root_eval.mkdir()
    training_data = _full_coverage_matrix(root_train, ("train", "val"))
    evaluation_data = _full_coverage_matrix(root_eval, ("train", "val", "test"))
    training_path = tmp_path / "training-manifest.json"
    evaluation_path = tmp_path / "evaluation-manifest.json"
    training_path.write_text(json.dumps(training_data))
    evaluation_path.write_text(json.dumps(evaluation_data))
    training_hash = hashlib.sha256(training_path.read_bytes()).hexdigest()
    evaluation_hash = hashlib.sha256(evaluation_path.read_bytes()).hexdigest()
    model = tmp_path / "candidate"; model.mkdir()
    torch.save(dict(model_state_dict=build_network().state_dict(),
                    preprocessing="dc-complex-rms-v1", class_order=list(CLASS_ORDER),
                    manifest_sha256=training_hash), model / "model.pt")
    (model / "metadata.json").write_text(json.dumps(dict(
        preprocessing="dc-complex-rms-v1", class_order=list(CLASS_ORDER),
        manifest_sha256=training_hash, manifest=str(training_path), root=str(root_train))))
    (model / "thresholds.json").write_text(json.dumps(
        dict(energy_floor_db=0, selection=dict(max_softmax=.5, margin=.1))))
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps(_production_report(
        model, evaluation_path, training_hash, evaluation_hash, root_eval)))
    artifact = export_model(model, evaluation, tmp_path / "export")
    assert artifact["status"] == "production"


def test_production_export_refuses_incomplete_trainval_no_test_manifest(tmp_path):
    """A train/val-only training manifest missing one cell must be refused."""
    import torch
    from scripts.rf_ml.export import export_model
    from scripts.rf_ml.train import CLASS_ORDER, build_network
    root_train = tmp_path / "train-data"; root_train.mkdir()
    root_eval = tmp_path / "eval-data"; root_eval.mkdir()
    training_data = _full_coverage_matrix(root_train, ("train", "val"))
    evaluation_data = _full_coverage_matrix(root_eval, ("train", "val", "test"))
    training_data["frames"].pop()  # drop one frame -> that cell loses a qualified session
    training_path = tmp_path / "training-manifest.json"
    evaluation_path = tmp_path / "evaluation-manifest.json"
    training_path.write_text(json.dumps(training_data))
    evaluation_path.write_text(json.dumps(evaluation_data))
    training_hash = hashlib.sha256(training_path.read_bytes()).hexdigest()
    evaluation_hash = hashlib.sha256(evaluation_path.read_bytes()).hexdigest()
    model = tmp_path / "candidate"; model.mkdir()
    torch.save(dict(model_state_dict=build_network().state_dict(),
                    preprocessing="dc-complex-rms-v1", class_order=list(CLASS_ORDER),
                    manifest_sha256=training_hash), model / "model.pt")
    (model / "metadata.json").write_text(json.dumps(dict(
        preprocessing="dc-complex-rms-v1", class_order=list(CLASS_ORDER),
        manifest_sha256=training_hash, manifest=str(training_path), root=str(root_train))))
    (model / "thresholds.json").write_text(json.dumps(
        dict(energy_floor_db=0, selection=dict(max_softmax=.5, margin=.1))))
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(json.dumps(_production_report(
        model, evaluation_path, training_hash, evaluation_hash, root_eval)))
    with pytest.raises(ValueError, match="train/val"):
        export_model(model, evaluation, tmp_path / "export")


def test_experimental_refuses_hash_valid_malformed_manifest(export_case):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, report = export_case
    manifest = Path(report['manifest']); manifest.write_text('{"schema_version":2,"sessions":[],"frames":[]}')
    report['evaluation_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    evaluation.write_text(json.dumps(report))
    with pytest.raises(ValueError, match='manifest.*integrity'): export_model(model, evaluation, output, experimental=True)


@pytest.mark.parametrize('field', ['energy_floor_db', 'max_softmax', 'margin'])
def test_export_refuses_boolean_thresholds(export_case, field):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, _ = export_case
    thresholds = json.loads((model/'thresholds.json').read_text())
    if field == 'energy_floor_db': thresholds[field] = True
    else: thresholds['selection'][field] = True
    (model/'thresholds.json').write_text(json.dumps(thresholds))
    with pytest.raises(ValueError): export_model(model, evaluation, output, experimental=True)


@pytest.mark.parametrize('field', ['energy_floor_db', 'max_softmax', 'margin'])
def test_export_rejects_threshold_changes_after_evaluation(export_case, field):
    from scripts.rf_ml.export import export_model
    model, evaluation, output, _ = export_case
    path = model / 'thresholds.json'; thresholds = json.loads(path.read_text())
    if field == 'energy_floor_db': thresholds[field] += 1
    else: thresholds['selection'][field] += .01
    path.write_text(json.dumps(thresholds))
    with pytest.raises(ValueError, match='evaluated threshold'): export_model(model, evaluation, output, experimental=True)


def test_benchmark_reports_hot_latency_memory_and_identity(export_case):
    from scripts.rf_ml.export import export_model
    from scripts.rf_ml.benchmark import benchmark
    model, evaluation, output, _ = export_case
    artifact = export_model(model, evaluation, output, experimental=True)
    iq = output.parent / 'iq.bin'
    iq.write_bytes(np.random.RandomState(3).randint(-100,100,(4096,2)).astype('<i2').tobytes())
    result = benchmark(output / artifact['model_file'], artifact['model_sha256'], iq, frames=100)
    assert result['frames'] == 100 and result['warmup_frames'] >= 1
    assert result['hot_p95_ms'] > 0 and result['incremental_peak_rss_bytes'] >= 0
    assert result['model_sha256'] == artifact['model_sha256'] and result['numpy_version'] == np.__version__
    assert type(result['resource_acceptance']) is bool
