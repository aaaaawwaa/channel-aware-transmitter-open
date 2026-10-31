"""Dataset manifest / npz loader for the synthetic 4-class dataset.

Contract: ``ml_training/docs/CONTRACTS.md`` section 7.  The loader never assumes a
file naming convention beyond the manifest's ``files`` list, so the data generator
can evolve the layout as long as the manifest stays contract-compliant.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

SCHEMA = "ml-modrec-synthetic-v1"
SPLITS = ("train", "val", "test", "gate_cal", "gate_test")
FRAME_ARRAYS = ("X", "frame_id", "session_id", "label", "seed_family",
                "symbol_seed", "noise_seed", "snr_db", "amplitude_codes",
                "phase_rad", "cfo_hz", "window_offset", "noise_sigma_codes",
                "iq_sha256", "clipped")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(dataset_dir):
    """Load and minimally validate ``<dataset_dir>/manifest.json``."""
    dataset_dir = Path(dataset_dir)
    path = dataset_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported dataset schema: %r" % (manifest.get("schema"),))
    if not manifest.get("files"):
        raise ValueError("manifest has no frame files")
    known = set(SPLITS)
    for entry in manifest["files"]:
        if entry.get("split") not in known:
            raise ValueError("manifest file has unknown split: %r" % (entry.get("split"),))
        if not (dataset_dir / entry["path"]).is_file():
            raise ValueError("manifest file is missing on disk: " + entry["path"])
    return manifest


def files_for_split(manifest, split, kinds=None):
    entries = [entry for entry in manifest["files"] if entry["split"] == split]
    if kinds is not None:
        wanted = set(kinds)
        entries = [entry for entry in entries if entry["label"] in wanted]
    if not entries:
        raise ValueError("no files for split %r" % (split,))
    return entries


def load_split(dataset_dir, manifest, split, labels=None, kinds=None,
               include_clipped=False):
    """Load one split (train/val/test/gate_cal/gate_test) into stacked arrays.

    ``labels`` filters 4-class labels (train/val/test); ``kinds`` filters quality-gate
    kinds (gate_cal/gate_test).  Frames flagged ``clipped`` are dropped unless
    ``include_clipped`` is true; the number dropped is recorded under ``_dropped``.
    """
    dataset_dir = Path(dataset_dir)
    entries = files_for_split(manifest, split, kinds=kinds)
    wanted = set(labels) if labels is not None else None
    buckets = {key: [] for key in FRAME_ARRAYS}
    dropped = 0
    for entry in entries:
        if wanted is not None and entry["label"] not in wanted:
            continue
        path = dataset_dir / entry["path"]
        with np.load(path, allow_pickle=False) as archive:
            if "X" not in archive:
                raise ValueError("npz missing X array: " + str(path))
            keep = np.ones(archive["X"].shape[0], dtype=bool)
            if not include_clipped and "clipped" in archive:
                keep = ~np.asarray(archive["clipped"], dtype=bool)
            dropped += int((~keep).sum())
            for key in FRAME_ARRAYS:
                if key in archive:
                    buckets[key].append(np.asarray(archive[key])[keep])
    if not buckets["X"]:
        raise ValueError("no usable frames for split %r" % (split,))
    stacked = {}
    for key in FRAME_ARRAYS:
        if buckets[key]:
            stacked[key] = np.concatenate(buckets[key], axis=0)
    stacked["_dropped"] = dropped
    stacked["_split"] = split
    return stacked


def verify_hashes(dataset_dir, manifest):
    """Verify every npz sha256 recorded in the manifest; return a report dict."""
    dataset_dir = Path(dataset_dir)
    mismatches = []
    checked = 0
    for entry in manifest["files"]:
        path = dataset_dir / entry["path"]
        actual = sha256_file(path)
        checked += 1
        if entry.get("sha256") and actual != entry["sha256"]:
            mismatches.append({"path": entry["path"], "expected": entry["sha256"],
                               "actual": actual})
    return {"checked": checked, "mismatches": mismatches,
            "ok": not mismatches}


def session_index(manifest):
    """Flatten the per-split sessions from the manifest into a lookup list."""
    sessions = []
    for split in SPLITS:
        block = manifest.get("splits", {}).get(split, {})
        for session in block.get("sessions", []):
            record = dict(session)
            record["split"] = split
            sessions.append(record)
    return sessions
