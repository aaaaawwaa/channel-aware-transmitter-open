"""Export host-only Torch checkpoints into bounded, data-only release artifacts."""
import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

SW_DIR = Path(__file__).resolve().parents[2] / 'pynq_overlay' / 'sw'
if str(SW_DIR) not in sys.path:
    sys.path.append(str(SW_DIR))
from rf_cnn_runtime import CLASSES, PREPROCESSING, WEIGHT_SHAPES, validate_model
from merged_release import MODEL_FILE, EVALUATION_FILE, verify_model_artifact
from .dataset import validate_manifest


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    with Path(path).open('rb') as source:
        raw = source.read(64*1024*1024+1)
    if len(raw) > 64*1024*1024:
        raise ValueError('JSON evidence exceeds 64 MiB')
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict):
        raise ValueError('evidence must be a JSON object')
    return data


def export_model(model_dir, evaluation_path, output_dir, experimental=False):
    """Strict identity/schema gates also apply to explicit experimental exports."""
    import torch
    model_dir, output_dir = Path(model_dir), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError('export destination exists')
    metadata = _read(model_dir / 'metadata.json')
    report = _read(evaluation_path)
    thresholds = _read(model_dir / 'thresholds.json')
    checkpoint_path = model_dir / 'model.pt'
    if checkpoint_path.stat().st_size > 2*1024*1024:
        raise ValueError('checkpoint exceeds fixed-network size bound')
    checkpoint_hash = _sha(checkpoint_path)
    checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    for value in (metadata, checkpoint, report):
        if not isinstance(value, dict) or value.get('preprocessing') != PREPROCESSING:
            raise ValueError('unsupported checkpoint/evidence preprocessing; retrain with complex RMS')
    if (metadata.get('class_order') != list(CLASSES) or checkpoint.get('class_order') != list(CLASSES)
            or report.get('model_checkpoint_sha256') != checkpoint_hash):
        raise ValueError('checkpoint class/hash identity mismatch')
    training_hash = metadata.get('manifest_sha256', metadata.get('training_manifest_sha256'))
    if (report.get('training_manifest_sha256') != training_hash
            or checkpoint.get('manifest_sha256', training_hash) != training_hash):
        raise ValueError('training manifest hash identity mismatch')
    if type(report.get('schema_version')) is not int or report['schema_version'] != 1:
        raise ValueError('invalid evaluation schema')
    if report.get('split') != 'test' or report.get('origin') not in ('synthetic', 'cabled'):
        raise ValueError('export requires independent test evaluation')
    manifests = []
    for evidence, expected_hash, kind in ((metadata, training_hash, 'training'),
            (report, report.get('evaluation_manifest_sha256'), 'evaluation')):
        try:
            manifest_path = Path(evidence['manifest'])
            if _sha(manifest_path) != expected_hash:
                raise ValueError(kind + ' manifest hash mismatch')
            manifest = _read(manifest_path)
        except (KeyError, TypeError, OSError) as exc:
            raise ValueError(kind + ' manifest hash cannot be verified') from exc
        manifest_root = Path(evidence.get('root', manifest_path.parent))
        origin = 'physical' if report['origin'] == 'cabled' else 'synthetic'
        result = validate_manifest(manifest, manifest_root, coverage_origin=origin)
        if not result.valid:
            raise ValueError(kind + ' manifest integrity failed: ' + json.dumps(result.errors[:3]))
        manifests.append((manifest, manifest_root))
    if not experimental:
        if any(report.get(key) is not True for key in ('acceptance', 'production_ready', 'lineage_verified')):
            raise ValueError('production acceptance/coverage/lineage gates not verified')
        if report['origin'] != 'cabled':
            raise ValueError('production requires cabled independent evaluation')
        for manifest, root in manifests:
            # No-test-contact training manifests (deliberately train/val only, a
            # stronger guarantee than coverage) need complete train/val coverage
            # instead of the full 90-cell production matrix; evaluation manifests
            # must always pass the full production matrix.
            has_test = any(frame['split'] == 'test' for frame in manifest['frames'])
            result = validate_manifest(manifest, root, production=has_test,
                                       coverage_origin='physical')
            if not result.valid:
                raise ValueError('production data coverage/integrity failed: ' +
                    json.dumps(list(result.errors)[:3] + list(result.coverage_missing)[:3]))
            if not has_test:
                missing_trainval = [cell for cell in result.coverage_missing
                                    if cell['split'] in ('train', 'val')]
                if missing_trainval:
                    raise ValueError('production training coverage missing train/val cells: ' +
                        json.dumps(missing_trainval[:3]))
    selection = thresholds.get('selection', {})
    floor = thresholds.get('energy_floor_db')
    if type(floor) not in (int, float) or not math.isfinite(floor):
        raise ValueError('invalid energy threshold')
    evaluated_selection = report.get('thresholds')
    if (not isinstance(selection, dict) or not isinstance(evaluated_selection, dict)
            or any(selection.get(key) != evaluated_selection.get(key) for key in ('max_softmax', 'margin'))
            or report.get('energy_floor_db') != floor
            or ('thresholds' in metadata and metadata['thresholds'] != thresholds)):
        raise ValueError('evaluated threshold identity mismatch')
    try:
        energy = 10.0 ** (floor / 20.0)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('invalid energy threshold') from exc
    mapping = {'conv1': 'body.0', 'conv2': 'body.2', 'conv3': 'body.4', 'head': 'head'}
    state = checkpoint.get('model_state_dict')
    expected_keys = {mapping[k.split('.')[0]]+'.'+k.split('.')[1] for k in WEIGHT_SHAPES}
    if not isinstance(state, dict) or set(state) != expected_keys:
        raise ValueError('invalid fixed network state')
    weights = {}
    for name, shape in WEIGHT_SHAPES.items():
        key = mapping[name.split('.')[0]] + '.' + name.split('.')[1]
        tensor = state[key]
        if (not isinstance(tensor, torch.Tensor) or tuple(tensor.shape) != shape
                or tensor.dtype != torch.float32 or not bool(torch.isfinite(tensor).all())):
            raise ValueError('invalid checkpoint weight shape/type/values')
        weights[name] = dict(shape=list(shape), values=tensor.detach().cpu().reshape(-1).tolist())
    status = 'experimental' if experimental else 'production'
    document = dict(schema_version=1, network='tiny-iq-cnn-v1', preprocessing=PREPROCESSING,
        input_shape=[2,4096], classes=list(CLASSES), weights=weights, status=status,
        supported_domain=report.get('supported_domain'),
        thresholds=dict(max_softmax=selection.get('max_softmax'), margin=selection.get('margin'),
                        min_complex_rms=energy), training_manifest_sha256=training_hash,
        evaluation_manifest_sha256=report.get('evaluation_manifest_sha256'),
        model_checkpoint_sha256=checkpoint_hash)
    validate_model(document)
    evaluation = dict(schema_version=1, split='test', origin=report['origin'],
        supported_domain=document['supported_domain'], training_manifest_sha256=training_hash,
        evaluation_manifest_sha256=document['evaluation_manifest_sha256'],
        macro_f1=report.get('macro_f1'), class_recall=report.get('per_class_recall'),
        unknown_rejection=report.get('unknown_rejection_rate'), off_rejection=report.get('off_rejection_rate'),
        class_sessions=report.get('class_sessions'), class_frames=report.get('class_frames'),
        off_sessions=report.get('off_sessions'), off_frames=report.get('off_frames'),
        source_report_sha256=_sha(evaluation_path), model_checkpoint_sha256=checkpoint_hash)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.rf-export-', dir=str(output_dir.parent)))
    try:
        (stage / MODEL_FILE).write_text(json.dumps(document, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        evaluation['model_sha256'] = _sha(stage / MODEL_FILE)
        (stage / EVALUATION_FILE).write_text(json.dumps(evaluation, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        artifact = dict(schema_version=1, status=status, model_file=MODEL_FILE,
            model_sha256=_sha(stage / MODEL_FILE), evaluation_file=EVALUATION_FILE,
            evaluation_sha256=_sha(stage / EVALUATION_FILE))
        verify_model_artifact(stage, artifact, 'cnn-v1')
        (stage / 'model_artifact.json').write_text(json.dumps(artifact, indent=2), encoding='utf-8')
        if sum(p.stat().st_size for p in stage.iterdir()) > 128*1024:
            raise ValueError('export package exceeds 128 KiB')
        stage.rename(output_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--evaluation', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--experimental', action='store_true')
    args = parser.parse_args(argv)
    artifact = export_model(args.model, args.evaluation, args.output, args.experimental)
    print(json.dumps(artifact, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
