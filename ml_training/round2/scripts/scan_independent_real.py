"""Scan the read-only evidence tree for physical BPSK sessions NOT used in round-1/2 development.

A candidate must be:
* a directory with ``normal-*.iq`` + ``normal-*.json`` frame metadata,
* ``fixture`` false / ``physical`` true / ``collected`` true,
* TX config BPSK / 3000000 / RECT,
* every IQ SHA-256 distinct from the campaign5 development replay hashes.

The output is used only to report whether an independent real session exists; nothing is
written outside ``ml_training/round2/analysis``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "evidence"
DEVELOPMENT_ROOTS = (
    ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / "campaign5-val-followup",
    ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18" / "campaign4",
)
SKIP_MARKERS = ("pytest-temp", "pytest-tmp", "\\temp\\", "sandbox", "__pycache__",
                "fixture-download", "identity-green", "identity-red", "pulse-green",
                "negative-", "remote-")
OUT = ROOT / "ml_training" / "round2" / "analysis" / "independent_real_scan.json"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main():
    development_hashes = set()
    for root in DEVELOPMENT_ROOTS:
        for iq in root.rglob("*.iq"):
            development_hashes.add(sha256_bytes(iq.read_bytes()))
    candidates = {}
    for meta_path in EVIDENCE.rglob("normal-*.json"):
        if meta_path.name.count(".") != 1:
            continue
        path_text = str(meta_path)
        if any(marker in path_text for marker in SKIP_MARKERS):
            continue
        directory = meta_path.parent
        if any(directory.is_relative_to(root) for root in DEVELOPMENT_ROOTS):
            continue
        meta = load_json(meta_path)
        if not isinstance(meta, dict):
            continue
        capture = meta.get("capture_status") or {}
        trainval = meta.get("trainval") or {}
        submitted = (meta.get("demod_result") or {}).get("submitted") or {}
        config = submitted.get("config") or {}
        origin = str(meta.get("origin") or "").lower()
        release_id = str(meta.get("release_id") or "")
        if ("stub" in origin or "not a physical capture" in origin
                or release_id.startswith("stubboard")):
            continue
        if not (capture.get("physical") is True
                and (meta.get("fixture") is False or meta.get("fixture") is None)
                and trainval.get("physical") is True
                and trainval.get("truth") in ("normal", "off")
                and config.get("mode") == "BPSK"
                and config.get("symbol_rate_hz") == 3_000_000
                and config.get("shaping") == "RECT"):
            continue
        key = str(directory)
        candidates.setdefault(key, {"directory": key, "frames": 0,
                                    "session_id": meta.get("session"),
                                    "release_id": meta.get("release_id"),
                                    "origin": meta.get("origin")})
        candidates[key]["frames"] += 1
    report = []
    for key, data in candidates.items():
        directory = Path(key)
        iq_paths = sorted(directory.glob("*.iq"))
        hashes = [sha256_bytes(path.read_bytes()) for path in iq_paths]
        overlap = len(set(hashes) & development_hashes)
        report.append({**data,
                       "iq_files": len(hashes),
                       "development_overlap": overlap,
                       "distinct_from_development": overlap == 0,
                       "first_iq": str(iq_paths[0].relative_to(ROOT)).replace("\\", "/") if iq_paths else None})
    report.sort(key=lambda item: (-item["distinct_from_development"], -item["iq_files"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"development_hashes": len(development_hashes),
                               "candidates": report}, indent=1), encoding="utf-8")
    print("development iq hashes:", len(development_hashes))
    print("candidates:", len(report))
    for item in report:
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    main()
