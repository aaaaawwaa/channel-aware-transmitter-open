"""Grouping, duplicate-IQ detection and cross-split leakage checks.

The originals are never copied: only paths and digests are grouped.  A duplicate group is any
IQ digest seen more than once; it becomes a TRAIN/VAL/TEST leakage finding when the group spans
more than one evaluation split.
"""
from __future__ import annotations

from collections import Counter, defaultdict

EVAL_SPLITS = ("TRAIN", "VAL", "TEST")


def _compact(record):
    return {
        "frame_id": record["frame_id"],
        "source_id": record["source_id"],
        "session_id": record["session_id"],
        "split": record["split"],
        "kind": record["kind"],
        "truth_class": record["truth_class"],
        "synthetic": record["synthetic"],
        "status": record["status"],
        "iq_path": record["iq_path"],
        "iq_sha256": record["iq_sha256"],
    }


def analyze(records):
    by_digest = defaultdict(list)
    for record in records:
        if record.get("iq_sha256"):
            by_digest[record["iq_sha256"]].append(record)
    duplicate_groups = []
    leakage_groups = []
    for digest, group in by_digest.items():
        if len(group) < 2:
            continue
        splits = sorted({record["split"] for record in group
                         if record["split"] in EVAL_SPLITS})
        sessions = sorted({record["session_key"] for record in group})
        entry = {
            "iq_sha256": digest,
            "count": len(group),
            "splits": splits,
            "sessions": sessions,
            "cross_split": len(splits) > 1,
            "cross_session": len(sessions) > 1,
            "records": [_compact(record) for record in group],
        }
        duplicate_groups.append(entry)
        if entry["cross_split"]:
            leakage_groups.append(entry)
    sessions = defaultdict(lambda: {
        "session_key": None, "source_id": None, "session_id": None, "split": None,
        "frames": 0, "clean": 0, "quality": 0, "stress": 0, "other": 0,
        "invalid": 0, "synthetic": False, "release_ids": set(), "boot_id": None,
        "map": [],
    })
    for record in records:
        group = sessions[record["session_key"]]
        group["session_key"] = record["session_key"]
        group["source_id"] = record["source_id"]
        group["session_id"] = record["session_id"]
        group["split"] = record["split"]
        group["synthetic"] = record["synthetic"]
        group["frames"] += 1
        group[record["kind"] if record["kind"] in
              ("clean", "quality", "stress") else "other"] += 1
        if record["status"] != "OK":
            group["invalid"] += 1
        release = record.get("identity", {}).get("release_id")
        if release:
            group["release_ids"].add(release)
        boot = record.get("identity", {}).get("boot_id")
        if boot:
            group["boot_id"] = boot
        group["map"].append(record["frame_id"])
    session_list = []
    for key in sorted(sessions):
        group = sessions[key]
        group["release_ids"] = sorted(group["release_ids"])
        group["release_consistent"] = len(group["release_ids"]) <= 1
        session_list.append(group)
    real_duplicates = [group for group in duplicate_groups
                       if not all(record["synthetic"] for record in group["records"])]
    real_leakage = [group for group in leakage_groups
                    if not all(record["synthetic"] for record in group["records"])]
    return {
        "schema": "ml-real-campaign-grouping-v1",
        "duplicate_groups": duplicate_groups,
        "leakage_groups": leakage_groups,
        "duplicate_group_count": len(duplicate_groups),
        "leakage_group_count": len(leakage_groups),
        "real_duplicate_group_count": len(real_duplicates),
        "real_leakage_group_count": len(real_leakage),
        "sessions": session_list,
        "session_count": len(session_list),
        "status": "PASS" if not leakage_groups else "FAIL",
        "real_status": "PASS" if not real_leakage else "FAIL",
        "note": ("duplicate IQ is not automatically data leakage unless it spans evaluation "
                 "splits; real and synthetic findings are reported separately"),
    }


def split_counts(records):
    counter = Counter()
    for record in records:
        counter[(record["split"], record["kind"])] += 1
    return [{"split": split, "kind": kind, "count": count}
            for (split, kind), count in sorted(counter.items())]
