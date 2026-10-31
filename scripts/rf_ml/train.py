"""Task 8: fixed TinyIQCNN training (CPU, val-only tuning, no test contact).

Classes: TONE/BPSK/QPSK/QAM16/QAM64/OTHER.  Input float32 [batch,2,4096]:
per-component DC removal, shared complex-RMS normalisation, gate rejection for
zero-energy / clipped / wrong-length.  Augmentation only touches train.
Epoch and max-softmax/margin thresholds are chosen on val; the test split is
never read here.  Every RNG is seeded and recorded; num_workers=0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SW_DIR = ROOT / "pynq_overlay" / "sw"
if str(SW_DIR) not in sys.path:
    sys.path.append(str(SW_DIR))

from . import dataset as manifest_contract  # noqa: E402

CLASS_ORDER = ("TONE", "BPSK", "QPSK", "QAM16", "QAM64", "OTHER")
LABEL_TO_INDEX = {label: i for i, label in enumerate(CLASS_ORDER)}
PREPROCESSING = "dc-complex-rms-v1"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_provenance(metadata, checkpoint, checkpoint_path):
    """Return whether a receipt binds these bytes to a complete ancestry."""
    expected = metadata.get("model_checkpoint_sha256")
    if expected is not None and expected != _sha(checkpoint_path):
        raise ValueError("checkpoint SHA256 mismatch training receipt")
    provenance = metadata.get("provenance")
    if provenance is None:
        return False
    if (not isinstance(provenance, dict) or provenance != checkpoint.get("provenance")
            or provenance.get("training_manifest_sha256") != metadata.get("manifest_sha256")
            or type(provenance.get("ancestry_complete")) is not bool
            or not isinstance(provenance.get("ancestors"), list)):
        raise ValueError("checkpoint provenance mismatch training receipt")
    ancestors = provenance["ancestors"]
    for ancestor in ancestors:
        if (not isinstance(ancestor, dict) or not isinstance(ancestor.get("manifest"), str)
                or not ancestor["manifest"] or any(not isinstance(ancestor.get(k), str)
                or not re.fullmatch(r"[0-9a-f]{64}", ancestor[k])
                for k in ("manifest_sha256", "checkpoint_sha256"))):
            raise ValueError("invalid initializer ancestor provenance")
    initializer = provenance.get("initializer_sha256")
    if initializer != (ancestors[0]["checkpoint_sha256"] if ancestors else None):
        raise ValueError("initializer SHA256 mismatch ancestry")
    return expected is not None and provenance["ancestry_complete"]


def ancestry_manifests(provenance):
    """Resolve and validate every recorded initialization input manifest."""
    for ancestor in provenance.get("ancestors", []):
        path = Path(ancestor["manifest"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or _sha(path) != ancestor["manifest_sha256"]:
            raise ValueError("ancestor manifest missing or SHA256 mismatch")
        data = _load_manifest(path, Path(ancestor.get("root", path.parent)))
        yield data


def reject_seen_test(training, test_frames, context="ancestor"):
    source = [f for f in training["frames"] if f["split"] in ("train", "val")]
    sessions = {f["session_id"] for f in source}
    hashes = {f["iq_sha256"] for f in source}
    seeds = {f["tx_reference"]["seed_family"] for f in source if f["tx_reference"]}
    for frame in test_frames:
        seed = frame["tx_reference"]["seed_family"] if frame["tx_reference"] else None
        if frame["session_id"] in sessions or frame["iq_sha256"] in hashes or seed in seeds:
            raise ValueError(context + " leaks training/validation session, IQ or seed")


def build_network():
    """The plan's fixed network (verbatim): 1662 params, ~1.278M MACs."""
    import torch
    import torch.nn as nn

    class TinyIQCNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv1d(2, 8, 7, stride=2, padding=3), nn.ReLU(),
                nn.Conv1d(8, 16, 5, stride=2, padding=2), nn.ReLU(),
                nn.Conv1d(16, 16, 3, stride=2, padding=1), nn.ReLU())
            self.head = nn.Linear(16, 6)

        def forward(self, x):
            return self.head(self.body(x).mean(dim=-1))

    return TinyIQCNN()


def preprocess(iq_int16):
    """Return (x float32 [2,4096] | None, gate dict).

    gate: {ok, reason, rms, peaks}.  Wrong length raises ValueError.
    Zero energy and rail samples are quality rejects. Validate before casting
    so an out-of-range or fractional sample cannot wrap into a valid code.
    """
    arr = np.asarray(iq_int16)
    if arr.shape != (4096, 2):
        raise ValueError("IQ must be 4096 s16 I/Q pairs")
    if (arr.dtype.kind not in "iuf" or not np.all(np.isfinite(arr))
            or np.any(arr != np.floor(arr)) or np.any(arr < -2048) or np.any(arr > 2047)):
        raise ValueError("IQ must contain finite integral signed 12-bit codes")
    peaks = int(np.max(np.abs(arr.astype(np.float64))))
    if np.any((arr == -2048) | (arr == 2047)):
        return None, {"ok": False, "reason": "clipping", "rms": 0.0, "peaks": peaks}
    x = arr.astype(np.float32)
    x0 = x - x.mean(axis=0, keepdims=True)          # per-component DC removal
    rms = float(np.sqrt(np.mean(np.sum(x0.astype(np.float64) ** 2, axis=1))))
    if rms <= 1e-6:
        return None, {"ok": False, "reason": "no_energy", "rms": rms, "peaks": int(np.max(np.abs(x)))}
    x = x0 / rms                                     # shared complex RMS
    peaks = int(np.max(np.abs(x0)))
    gate = {"ok": True, "reason": "", "rms": rms, "peaks": peaks}
    # network input contract: channels first [2, 4096] float32
    return np.ascontiguousarray(x.T, dtype=np.float32), gate


def _load_manifest(path, root):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    origin = next((s.get("origin", "physical") for s in data.get("sessions", [])), "physical")
    result = manifest_contract.validate_manifest(data, root if root else Path(path).parent,
                                                 production=False, coverage_origin=origin)
    if not result.valid:
        raise ValueError("manifest invalid: " + json.dumps(result.errors[:8], ensure_ascii=False))
    return data


def _samples_of(frame, root):
    p = (root / frame["iq_path"]).resolve()
    raw = p.read_bytes()
    iq = np.frombuffer(raw, dtype="<i2").reshape(-1, 2)
    return preprocess(iq)


def _dataset(manifest, root, split, labels, augment_ok=False):
    xs, ys = [], []
    for frame in manifest_contract.training_frames(manifest, split):
        if labels is not None and frame["label"] not in labels:
            continue
        if frame["augmentation_parent_id"] and not augment_ok:
            continue
        x, gate = _samples_of(frame, root)
        if gate["ok"]:
            xs.append(x)
            ys.append(LABEL_TO_INDEX[frame["label"]])
    return xs, ys


def _off_rms(manifest, root, splits):
    values = []
    for split in splits:
        for frame in manifest["frames"]:
            if (frame["split"] != split or frame["label"] != "OFF"
                    or not manifest_contract._usable(frame) or frame["augmentation_parent_id"]):
                continue
            x, gate = _samples_of(frame, root)
            values.append(gate["rms"])
    return values


def _macro_f1(confusion, n_classes):
    recall = []
    for c in range(n_classes):
        denom = confusion[c].sum()
        recall.append(float(confusion[c, c] / denom) if denom else 0.0)
    precision = []
    for c in range(n_classes):
        denom = confusion[:, c].sum()
        precision.append(float(confusion[c, c] / denom) if denom else 0.0)
    f1s = []
    for r, p in zip(recall, precision):
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return float(np.mean(f1s)), recall, precision


def select_thresholds(net, manifest, root, energy_floor_db):
    """Freeze rejection thresholds using val only, including rejected known IQ.

    The objective equally weights five-class macro-F1 and OTHER/OFF rejection.
    UNKNOWN is always a full false negative for a known label.
    """
    import torch
    records = [f for f in manifest["frames"] if f["split"] == "val"
               and not f["augmentation_parent_id"]]
    labels, probabilities, gates = [], [], []
    for frame in records:
        labels.append(frame["label"])
        if not manifest_contract._usable(frame):
            gates.append(False)
            probabilities.append(np.zeros(6))
            continue
        x, gate = _samples_of(frame, root)
        ok = gate["ok"] and 20 * np.log10(gate["rms"] + 1e-30) >= energy_floor_db
        gates.append(ok)
        if ok:
            with torch.no_grad():
                probabilities.append(torch.softmax(net(torch.from_numpy(x[None])), dim=1).numpy()[0])
        else:
            probabilities.append(np.zeros(6))
    labels, probabilities = np.asarray(labels), np.asarray(probabilities)
    if not len(labels) or not np.any(labels == "OTHER") or not np.any(labels == "OFF"):
        raise ValueError("val requires OTHER and OFF rejection populations")
    order = np.argsort(probabilities, axis=1)[:, ::-1]
    top1 = probabilities[np.arange(len(labels)), order[:, 0]]
    top2 = probabilities[np.arange(len(labels)), order[:, 1]]
    best, best_score = None, -1.0
    for pmin in np.arange(0.50, 1.0, 0.02):
        for margin in np.arange(0.0, 0.41, 0.05):
            accept = np.asarray(gates) & (top1 >= pmin) & (top1 - top2 >= margin) & (order[:, 0] != 5)
            predictions = np.where(accept, order[:, 0], 6)
            confusion = np.zeros((7, 7), dtype=np.int64)
            for label, prediction in zip(labels, predictions):
                confusion[LABEL_TO_INDEX.get(label, 6), prediction] += 1
            f1, _, _ = _macro_f1(confusion, 5)
            unknown = float(np.mean(predictions[labels == "OTHER"] == 6))
            off = float(np.mean(predictions[labels == "OFF"] == 6))
            score = (f1 + unknown + off) / 3
            if score > best_score:
                best_score = score
                best = dict(max_softmax=float(pmin), margin=float(margin), val_macro_f1=f1,
                            val_unknown_rejection=unknown, val_off_rejection=off,
                            val_unknown_on_known=int(confusion[:5, 6].sum()),
                            objective="mean(known_macro_f1, OTHER_rejection, OFF_rejection)")
    return best


def train(manifest_path, output_dir, initialize=None, split="train", val_split="val",
          max_epochs=50, seed=20261001, lr=1e-3, weight_decay=1e-4, batch_size=32,
          epochs_per_report=None, root=None):
    """Train the fixed CNN with val-only tuning.  The test split is forbidden."""
    if split != "train" or val_split != "val":
        raise ValueError("training requires distinct train and val splits; test is held out")
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    root = Path(root) if root else Path(manifest_path).parent
    manifest = _load_manifest(manifest_path, root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)

    # RNG bookkeeping (fixed CPU path).
    rng_state = {"python_seed": seed, "numpy_seed": seed, "torch_seed": seed,
                 "dataloader_shuffle_seed": seed + 1, "augmentation": "manifest train only",
                 "torch_manual_seed": seed, "num_workers": 0}
    import random
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)  # deterministic single-thread CPU path
    rng_state["threads"] = 1

    labels = tuple(LABEL_TO_INDEX)  # six known classes; OFF is never a training label
    xs, ys = _dataset(manifest, root, split, labels, augment_ok=True)
    if not xs:
        raise ValueError("no usable training frames for split " + split)
    tx, ty = _dataset(manifest, root, val_split, labels, augment_ok=False)
    if not tx:
        raise ValueError("no usable validation frames for split " + val_split)

    counts = np.bincount(ys, minlength=len(CLASS_ORDER)).astype(np.float64)
    weights = torch.tensor(1.0 / (counts + 1e-6), dtype=torch.float32)
    weights = weights / weights.sum() * len(CLASS_ORDER)
    loader = DataLoader(TensorDataset(torch.from_numpy(np.stack(xs)), torch.tensor(ys)),
                        batch_size=batch_size, shuffle=True, num_workers=0,
                        generator=torch.Generator().manual_seed(seed + 1))

    net = build_network()
    provenance = dict(training_manifest_sha256=_sha(manifest_path), initializer_sha256=None,
                      ancestors=[], ancestry_complete=True)
    if initialize:
        initial_path = Path(initialize)
        ckpt = torch.load(initial_path, map_location="cpu", weights_only=True)
        if ckpt.get("preprocessing") != PREPROCESSING or ckpt.get("class_order") != list(CLASS_ORDER):
            raise ValueError("initial checkpoint preprocessing/classes mismatch")
        initial_metadata = json.loads(initial_path.with_name("metadata.json").read_text(encoding="utf-8"))
        complete = validate_provenance(initial_metadata, ckpt, initial_path)
        initial_manifest = Path(initial_metadata["manifest"])
        if not initial_manifest.is_absolute():
            initial_manifest = ROOT / initial_manifest
        if _sha(initial_manifest) != initial_metadata["manifest_sha256"]:
            raise ValueError("initializer manifest SHA256 mismatch")
        parent = dict(manifest=str(initial_manifest.resolve()),
                      root=str(Path(initial_metadata.get("root", initial_manifest.parent)).resolve()),
                      manifest_sha256=initial_metadata["manifest_sha256"], checkpoint_sha256=_sha(initial_path))
        provenance.update(initializer_sha256=parent["checkpoint_sha256"],
                          ancestors=[parent] + initial_metadata.get("provenance", {}).get("ancestors", []),
                          ancestry_complete=complete)
        test_frames = [f for f in manifest["frames"] if f["split"] == "test"]
        for ancestral_data in ancestry_manifests(provenance):
            reject_seen_test(ancestral_data, test_frames)
        net.load_state_dict(ckpt["model_state_dict"])
        if any(not bool(torch.isfinite(p).all()) for p in net.parameters()):
            raise ValueError("initial checkpoint contains non-finite weights")
        rng_state["initialize"] = str(initialize)
    optim = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=weight_decay)
    criterion = torch.nn.CrossEntropyLoss(weight=weights)

    Xv = torch.from_numpy(np.stack(tx))
    Yv = torch.tensor(ty)
    best, best_state, history = -1.0, None, []
    for epoch in range(1, max_epochs + 1):
        net.train()
        total, correct, loss_sum = 0, 0, 0.0
        for xb, yb in loader:
            optim.zero_grad()
            out = net(xb)
            loss = criterion(out, yb)
            loss.backward()
            optim.step()
            loss_sum += float(loss.detach()) * xb.size(0)
            correct += int((out.argmax(dim=1) == yb).sum())
            total += xb.size(0)
        net.eval()
        with torch.no_grad():
            outv = net(Xv)
        pred = outv.argmax(dim=1).numpy()
        conf = np.zeros((len(CLASS_ORDER), len(CLASS_ORDER)), dtype=np.int64)
        for y, p in zip(Yv.numpy(), pred):
            conf[y, p] += 1
        macro_f1, recall, precision = _macro_f1(conf, len(CLASS_ORDER))
        history.append({"epoch": epoch, "train_loss": loss_sum / max(total, 1),
                        "train_acc": correct / max(total, 1),
                        "val_macro_f1": macro_f1, "val_recall": recall})
        if macro_f1 > best:
            best = macro_f1
            best_state = {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}
        if epochs_per_report and epoch % epochs_per_report == 0:
            print("epoch %3d loss %.4f train_acc %.3f val_macro_f1 %.3f" %
                  (epoch, history[-1]["train_loss"], history[-1]["train_acc"], macro_f1))
    if best_state is None:
        raise RuntimeError("no epoch completed")
    import io
    buf = io.BytesIO()
    torch.save({"model_state_dict": best_state,
                "preprocessing": PREPROCESSING,
                "class_order": list(CLASS_ORDER),
                 "best_val_macro_f1": best,
                 "provenance": provenance,
                "seed": seed}, buf)
    (output_dir / "model.pt").write_bytes(buf.getvalue())

    # Thresholds and energy floor: val + train OFF only (never test).
    net.load_state_dict(best_state)
    net.eval()
    off_rms = _off_rms(manifest, root, (split, val_split))
    if not off_rms:
        raise ValueError("train/val require valid OFF background frames for the energy floor")
    off_db = 20.0 * np.log10(np.asarray(off_rms, dtype=np.float64) + 1e-30)
    energy_floor_db = float(np.percentile(off_db, 95)) + 3.0
    best_thresh = select_thresholds(net, manifest, root, energy_floor_db)
    (output_dir / "thresholds.json").write_text(json.dumps({
        "energy_floor_db": energy_floor_db, "selection": best_thresh,
        "off_rms_count": len(off_rms), "off_p95_db": float(np.percentile(off_db, 95)),
    }, indent=2), encoding="utf-8")

    digest = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
    metadata = {
        "model": "tiny_iq_cnn", "class_order": list(CLASS_ORDER), "params": 1662,
        "macs": 1_277_952, "input": [2, 4096], "preprocessing": PREPROCESSING,
        "manifest": str(manifest_path), "manifest_sha256": digest,
        "root": str(root.resolve()),
        "origin": manifest["sessions"][0]["origin"] if manifest["sessions"] else "unknown",
        "train_split": split, "val_split": val_split,
        "train_frames": len(xs), "val_frames": len(tx),
        "best_val_macro_f1": best, "best_epoch": next(h["epoch"] for h in history if h["val_macro_f1"] == best),
        "max_epochs": max_epochs, "lr": lr, "weight_decay": weight_decay, "batch_size": batch_size,
        "thresholds": json.loads((output_dir / "thresholds.json").read_text(encoding="utf-8")),
        "rng": rng_state, "initialize": str(initialize) if initialize else None,
        "provenance": provenance, "model_checkpoint_sha256": _sha(output_dir / "model.pt"),
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "torch": torch.__version__},
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "curves.json").write_text(json.dumps(history, indent=1), encoding="utf-8")
    return metadata


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--initialize", type=Path, default=None)
    p.add_argument("--max-epochs", type=int, default=50)
    p.add_argument("--seed", type=int, default=20261001)
    p.add_argument("--root", type=Path, default=None)
    a = p.parse_args(argv)
    meta = train(str(a.manifest), a.output, initialize=str(a.initialize) if a.initialize else None,
                 max_epochs=a.max_epochs, seed=a.seed, root=a.root)
    print(json.dumps({"best_val_macro_f1": meta["best_val_macro_f1"],
                      "best_epoch": meta["best_epoch"], "train_frames": meta["train_frames"],
                      "val_frames": meta["val_frames"]}, indent=2))


if __name__ == "__main__":
    main()
