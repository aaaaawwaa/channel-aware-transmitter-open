"""One-command offline pipeline: generate -> train -> evaluate -> infer.

Examples (from the project root, using the task venv):

    ml_training\\.venv\\Scripts\\python.exe ml_training/run.py all
    ml_training\\.venv\\Scripts\\python.exe ml_training/run.py generate --variant smoke
    ml_training\\.venv\\Scripts\\python.exe ml_training/run.py train --variant smoke
    ml_training\\.venv\\Scripts\\python.exe ml_training/run.py evaluate
    ml_training\\.venv\\Scripts\\python.exe ml_training/run.py infer --iq <frame.s16>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.common import config as config_mod  # noqa: E402
from ml_training.common.dataset import load_manifest, load_split  # noqa: E402
from ml_training.common.iq import frame_bytes, read_frame  # noqa: E402


def _python():
    return sys.executable


def _run(command, cwd=None):
    print("[run.py] " + " ".join(str(part) for part in command))
    completed = subprocess.run(command, cwd=str(cwd) if cwd else None)
    if completed.returncode != 0:
        raise SystemExit("command failed with exit code %d" % (completed.returncode,))
    return completed.returncode


def cmd_generate(args):
    config, _ = config_mod.load_config(args.config)
    dataset_dir = args.dataset or config_mod.dataset_dir(config, args.variant)
    command = [_python(), str(PACKAGE_ROOT / "data" / "generate.py"),
               "--config", str(args.config or config_mod.DEFAULT_CONFIG),
               "--variant", args.variant, "--out", str(dataset_dir)]
    if args.force:
        command.append("--force")
    _run(command, cwd=ROOT)
    return dataset_dir


def cmd_train(args):
    config, _ = config_mod.load_config(args.config)
    dataset_dir = args.dataset or config_mod.dataset_dir(config, args.variant)
    command = [_python(), str(PACKAGE_ROOT / "training" / "train.py"),
               "--config", str(args.config or config_mod.DEFAULT_CONFIG),
               "--dataset", str(dataset_dir), "--variant", args.variant]
    if args.max_epochs is not None:
        command += ["--max-epochs", str(args.max_epochs)]
    _run(command, cwd=ROOT)
    return PACKAGE_ROOT / "artifacts"


def cmd_evaluate(args):
    config, _ = config_mod.load_config(args.config)
    dataset_dir = args.dataset or config_mod.dataset_dir(config, args.variant)
    artifacts = PACKAGE_ROOT / "artifacts"
    reports = PACKAGE_ROOT / "evaluation" / "reports"
    command = [_python(), str(PACKAGE_ROOT / "evaluation" / "evaluate_sim.py"),
               "--dataset", str(dataset_dir),
               "--model", str(artifacts / "cnn"),
               "--baseline", str(artifacts / "baseline"),
               "--out", str(reports)]
    _run(command, cwd=ROOT)
    real_command = [_python(), str(PACKAGE_ROOT / "evaluation" / "real_transfer.py"),
                    "--model", str(artifacts / "cnn"),
                    "--baseline", str(artifacts / "baseline"),
                    "--out", str(reports)]
    _run(real_command, cwd=ROOT)
    return reports


def cmd_infer(args):
    from ml_training.model.numpy_runtime import load_model, predict_file

    model_path = args.model or (PACKAGE_ROOT / "artifacts" / "cnn" / "model.json")
    model = load_model(model_path)
    if args.iq:
        result = predict_file(model, args.iq)
    else:
        config, _ = config_mod.load_config(args.config)
        dataset_dir = args.dataset or config_mod.dataset_dir(config, "formal")
        manifest = load_manifest(dataset_dir)
        split = load_split(dataset_dir, manifest, "test", labels=list(config_mod.CLASS_ORDER))
        sample = args.sample if args.sample is not None else 0
        result = model.predict_iq(split["X"][sample])
        result["source"] = {"dataset": str(dataset_dir), "split": "test",
                            "frame_id": str(split["frame_id"][sample]),
                            "true_label": str(split["label"][sample])}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def _write_sample_frame(dataset_dir):
    manifest = load_manifest(dataset_dir)
    split = load_split(dataset_dir, manifest, "test", labels=list(config_mod.CLASS_ORDER))
    target = PACKAGE_ROOT / "artifacts" / "infer-sample.s16"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(frame_bytes(split["X"][0]))
    return {"path": str(target), "frame_id": str(split["frame_id"][0]),
            "true_label": str(split["label"][0])}


def cmd_all(args):
    config, _ = config_mod.load_config(args.config)
    dataset_dir = config_mod.dataset_dir(config, args.variant)
    manifest_path = dataset_dir / "manifest.json"
    if args.force or not manifest_path.is_file():
        cmd_generate(args)
    else:
        print("[run.py] dataset already exists, skipping generation: " + str(manifest_path))
    cmd_train(args)
    cmd_evaluate(args)
    sample = _write_sample_frame(dataset_dir)
    infer_result = cmd_infer(argparse.Namespace(iq=sample["path"], model=None,
                                                config=args.config, dataset=None,
                                                sample=0))
    summary = {
        "dataset": str(dataset_dir),
        "artifacts": str(PACKAGE_ROOT / "artifacts"),
        "reports": str(PACKAGE_ROOT / "evaluation" / "reports"),
        "infer_sample": sample,
        "infer_result": infer_result,
    }
    summary_path = PACKAGE_ROOT / "artifacts" / "pipeline-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"pipeline_summary": str(summary_path),
                      "infer_label": infer_result.get("label"),
                      "true_label": sample["true_label"]}, indent=2))
    return summary


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "train", "evaluate", "all"):
        child = sub.add_parser(name)
        child.add_argument("--config", type=Path, default=None)
        child.add_argument("--variant", default="formal", choices=list(config_mod.VARIANTS))
        child.add_argument("--dataset", type=Path, default=None)
        child.add_argument("--force", action="store_true")
        child.add_argument("--max-epochs", type=int, default=None)
    infer = sub.add_parser("infer")
    infer.add_argument("--config", type=Path, default=None)
    infer.add_argument("--iq", type=Path, default=None)
    infer.add_argument("--model", type=Path, default=None)
    infer.add_argument("--dataset", type=Path, default=None)
    infer.add_argument("--sample", type=int, default=0)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"generate": cmd_generate, "train": cmd_train,
                "evaluate": cmd_evaluate, "infer": cmd_infer, "all": cmd_all}
    handlers[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
