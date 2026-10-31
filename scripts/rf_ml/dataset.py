"""Standard-library RX manifest validator; pilot integrity is not full coverage."""
import argparse
import hashlib
import json
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
SPLITS = ("train", "val", "test")
ORIGINS = ("synthetic", "physical")
KNOWN_LABELS = ("TONE", "BPSK", "QPSK", "QAM16", "QAM64")
LABELS = KNOWN_LABELS + ("OTHER", "OFF")
OTHER_KINDS = ("NOISE", "CHIRP", "SQUARE", "MIXED")
RATES = (2000000, 3000000, 5000000)
SHAPINGS = ("RECT", "RRC0.35")
FRAME_FIELDS = frozenset((
    "capture_id", "session_id", "split", "origin", "label", "other_kind",
    "iq_path", "iq_sha256", "format", "samples", "release_id", "configuration",
    "tx_reference", "agc", "captured_at_utc", "completed_monotonic_s", "complete",
    "clipping_count", "exclusion_reasons", "augmentation_parent_id",
))
SESSION_FIELDS = frozenset(("session_id", "split", "origin", "split_frozen_at_utc"))


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple
    coverage_missing: tuple
    production_ready: bool
    valid_frames: int
    excluded_frames: int
    coverage_origin: str

    def to_dict(self):
        return dict(schema_version=SCHEMA_VERSION, valid=self.valid,
                    errors=list(self.errors), coverage_missing=list(self.coverage_missing),
                    production_ready=self.production_ready, valid_frames=self.valid_frames,
                    excluded_frames=self.excluded_frames, coverage_origin=self.coverage_origin)


def empty_manifest():
    return dict(schema_version=SCHEMA_VERSION, sessions=[], frames=[])


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must explicitly use UTC")
    return instant


def _fields(value, required, prefix, errors):
    if not isinstance(value, dict):
        errors.append(prefix + ": expected object")
        return False
    missing, extra = required - value.keys(), value.keys() - required
    if missing:
        errors.append(prefix + ": missing " + ", ".join(sorted(missing)))
    if extra:
        errors.append(prefix + ": unknown fields " + ", ".join(sorted(extra)))
    return not missing and not extra


def _usable(frame):
    return frame["complete"] and frame["clipping_count"] == 0 and not frame["exclusion_reasons"]


def training_frames(manifest, split):
    """Select usable non-OFF records; validate first, never train on test."""
    if split not in ("train", "val"):
        raise ValueError("training split must be train or val")
    return tuple(f for f in manifest["frames"] if f["split"] == split
                 and f["label"] != "OFF" and _usable(f))


def _cell(frame):
    label = frame["label"]
    if label in KNOWN_LABELS[1:]:
        tx = frame["tx_reference"]
        return label, None, tx["symbol_rate_hz"], tx["shaping"]
    return label, frame["other_kind"] if label == "OTHER" else None, None, None


def _coverage(frames, origin):
    cells = [("TONE", None, None, None), ("OFF", None, None, None)]
    cells += [("OTHER", kind, None, None) for kind in OTHER_KINDS]
    cells += [(label, None, rate, shape) for label in KNOWN_LABELS[1:]
              for rate in RATES for shape in SHAPINGS]
    grouped = defaultdict(lambda: defaultdict(int))
    for frame in frames:
        if frame["origin"] != origin or not _usable(frame) or frame["augmentation_parent_id"]:
            continue  # Augmentation cannot manufacture independent captured coverage.
        if frame["configuration"]["sample_rate_hz"] != 60000000 or frame["configuration"]["ratio"] != 0:
            continue
        grouped[(frame["split"], _cell(frame))][frame["session_id"]] += 1
    missing = []
    for split in SPLITS:
        for cell in cells:
            counts = grouped[(split, cell)]
            qualified = sum(n >= 10 for n in counts.values())
            if qualified < 3:
                missing.append(dict(split=split, label=cell[0], other_kind=cell[1],
                                    symbol_rate_hz=cell[2], shaping=cell[3],
                                    qualified_sessions=qualified, required_sessions=3,
                                    required_frames_per_session=10,
                                    session_frame_counts=dict(sorted(counts.items()))))
    return tuple(missing)


def validate_manifest(manifest, root, production=False, coverage_origin="physical"):
    """Verify actual IQ, provenance, split leakage and all missing coverage cells.

    Paths and resolved symlinks must remain inside root; reading IQ is bounded
    to 4096 samples. Physical and synthetic coverage are never combined.
    Excluded diagnostic records remain in the manifest but do not count.
    """
    errors, good, sessions, captures, hashes, seed_splits = [], [], {}, {}, set(), {}
    root = Path(root).resolve()
    if coverage_origin not in ORIGINS:
        errors.append("coverage_origin: unsupported origin")
    if not _fields(manifest, {"schema_version", "sessions", "frames"}, "manifest", errors):
        manifest = manifest if isinstance(manifest, dict) else {}
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("manifest: unsupported schema_version")
    session_records, records = manifest.get("sessions", []), manifest.get("frames", [])
    if not isinstance(session_records, list):
        errors.append("sessions: expected array")
        session_records = []
    if not isinstance(records, list):
        errors.append("frames: expected array")
        records = []
    for index, session in enumerate(session_records):
        prefix = "session[%d]" % index
        if not _fields(session, SESSION_FIELDS, prefix, errors):
            continue
        if not _text(session["session_id"]):
            errors.append(prefix + ": invalid session_id")
            continue
        if session["session_id"] in sessions:
            errors.append(prefix + ": duplicate session_id")
            continue
        if session["split"] not in SPLITS or session["origin"] not in ORIGINS:
            errors.append(prefix + ": invalid split or origin")
            continue
        try:
            frozen = _utc(session["split_frozen_at_utc"])
        except (ValueError, TypeError, OverflowError):
            errors.append(prefix + ": invalid frozen UTC timestamp")
            continue
        sessions[session["session_id"]] = session, frozen
    for index, frame in enumerate(records):
        prefix, before = "frame[%d]" % index, len(errors)
        if not _fields(frame, FRAME_FIELDS, prefix, errors):
            continue
        if not _text(frame["capture_id"]) or not _text(frame["session_id"]):
            errors.append(prefix + ": invalid capture_id/session_id")
            continue
        if frame["capture_id"] in captures:
            errors.append(prefix + ": duplicate capture_id")
        else:
            captures[frame["capture_id"]] = frame
        if frame["split"] not in SPLITS:
            errors.append(prefix + ": invalid split")
        if frame["origin"] not in ORIGINS:
            errors.append(prefix + ": invalid origin")
        session_pair = sessions.get(frame["session_id"])
        if session_pair is None:
            errors.append(prefix + ": session missing or invalid")
        elif any(frame[k] != session_pair[0][k] for k in ("split", "origin")):
            errors.append(prefix + ": session split/origin mismatch")
        try:
            captured = _utc(frame["captured_at_utc"])
            if session_pair is not None and captured < session_pair[1]:
                errors.append(prefix + ": capture precedes frozen split")
        except (ValueError, TypeError, OverflowError):
            errors.append(prefix + ": invalid captured UTC timestamp")
        if not _number(frame["completed_monotonic_s"]) or frame["completed_monotonic_s"] < 0:
            errors.append(prefix + ": invalid monotonic completion time")
        if not isinstance(frame["release_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", frame["release_id"]):
            errors.append(prefix + ": invalid release_id")
        if frame["label"] not in LABELS:
            errors.append(prefix + ": invalid label")
        if ((frame["label"] == "OTHER" and frame["other_kind"] not in OTHER_KINDS)
                or (frame["label"] != "OTHER" and frame["other_kind"] is not None)):
            errors.append(prefix + ": invalid other_kind")
        config = frame["configuration"]
        if _fields(config, {"sample_rate_hz", "ratio", "lo_hz", "bandwidth_hz"}, prefix + ".configuration", errors):
            for field, high in (("sample_rate_hz", 100000000), ("lo_hz", 6000000000), ("bandwidth_hz", 100000000)):
                if not _integer(config[field], 1, high):
                    errors.append(prefix + ": invalid configuration " + field)
            if not _integer(config["ratio"], 0, 4):
                errors.append(prefix + ": invalid ratio")
        agc = frame["agc"]
        if _fields(agc, {"mode", "gain_db"}, prefix + ".agc", errors):
            if not _text(agc["mode"]) or (agc["gain_db"] is not None and not _number(agc["gain_db"])):
                errors.append(prefix + ": invalid agc conditions")
        tx = frame["tx_reference"]
        if frame["label"] == "OFF":
            if tx is not None:
                errors.append(prefix + ": OFF must have null TX reference")
        elif _fields(tx, {"seed_family", "symbol_rate_hz", "shaping"}, prefix + ".tx_reference", errors):
            if not _text(tx["seed_family"]):
                errors.append(prefix + ": missing seed_family")
            elif frame["split"] in SPLITS:
                previous = seed_splits.setdefault(tx["seed_family"], frame["split"])
                if previous != frame["split"]:
                    errors.append(prefix + ": seed_family reused across split")
            if frame["label"] in KNOWN_LABELS[1:] and (tx["symbol_rate_hz"] not in RATES or tx["shaping"] not in SHAPINGS):
                errors.append(prefix + ": digital TX reference outside symbol rate/shaping domain")
            rate, shaping = tx["symbol_rate_hz"], tx["shaping"]
            if rate is not None and (type(rate) is not int or not _number(rate) or rate <= 0):
                errors.append(prefix + ": invalid TX reference symbol_rate_hz")
            if shaping is not None and not _text(shaping):
                errors.append(prefix + ": invalid TX reference shaping")
        parent = frame["augmentation_parent_id"]
        if parent is not None and not _text(parent):
            errors.append(prefix + ": invalid augmentation_parent_id")
        if type(frame["complete"]) is not bool or not _integer(frame["clipping_count"], 0, 8192):
            errors.append(prefix + ": invalid complete/clipping_count")
        reasons = frame["exclusion_reasons"]
        if not isinstance(reasons, list) or any(not _text(reason) for reason in reasons):
            errors.append(prefix + ": invalid exclusion reasons")
        elif not reasons and (frame["complete"] is not True or frame["clipping_count"] != 0):
            errors.append(prefix + ": incomplete/clipping frame needs exclusion reasons")
        if frame["format"] != "s16_iq_le" or not _integer(frame["samples"], 1, 4096):
            errors.append(prefix + ": invalid IQ format/samples")
        elif frame["complete"] is True and frame["samples"] != 4096:
            errors.append(prefix + ": complete IQ bytes require 4096 samples")
        digest = frame["iq_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(prefix + ": invalid IQ hash")
        else:
            if digest in hashes:
                errors.append(prefix + ": duplicate IQ SHA256")
            hashes.add(digest)
        name = frame["iq_path"]
        try:
            if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("unsafe path")
            path = (root / name).resolve()
            path.relative_to(root)
            if not path.is_file():
                raise ValueError("IQ file missing")
            if path.stat().st_size > 16384:
                raise ValueError("IQ bytes exceed 4096 samples")
            with path.open("rb") as stream:
                payload = stream.read(16385)
            if not _integer(frame["samples"], 1, 4096) or len(payload) != frame["samples"] * 4:
                errors.append(prefix + ": IQ bytes/sample count mismatch")
            if hashlib.sha256(payload).hexdigest() != digest:
                errors.append(prefix + ": IQ hash mismatch")
            if len(payload) % 2 == 0:
                values = [pair[0] for pair in struct.iter_unpack("<h", payload)]
                if any(value < -2048 or value > 2047 for value in values):
                    errors.append(prefix + ": IQ violates signed 12-bit range")
                if sum(value in (-2048, 2047) for value in values) != frame["clipping_count"]:
                    errors.append(prefix + ": clipping_count does not match IQ rail samples")
        except (OSError, ValueError, TypeError) as exc:
            errors.append(prefix + ": IQ path/bytes refused: " + str(exc))
        if len(errors) == before:
            good.append(frame)
    usable_ids = {frame["capture_id"] for frame in good if _usable(frame)}
    for frame in records:
        if not isinstance(frame, dict) or not _text(frame.get("augmentation_parent_id")):
            continue
        parent = captures.get(frame["augmentation_parent_id"])
        if parent is None or parent is frame:
            errors.append("augmentation: missing or self parent")
        elif any(frame.get(k) != parent.get(k) for k in ("session_id", "split", "origin", "label", "other_kind")):
            errors.append("augmentation: parent session/split/origin/label mismatch")
        elif parent.get("augmentation_parent_id") is not None:
            errors.append("augmentation: parent must be an original capture")
        elif (_text(frame.get("capture_id")) and frame["capture_id"] in usable_ids
              and parent["capture_id"] not in usable_ids):
            errors.append("augmentation: usable child has excluded or invalid parent")
    missing = _coverage(good, coverage_origin)
    ready = coverage_origin == "physical" and not errors and not missing
    return ValidationResult(not errors and (ready if production else True), tuple(errors), missing, ready,
                            sum(_usable(f) for f in good), sum(not _usable(f) for f in good), coverage_origin)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate")
    check.add_argument("manifest", type=Path)
    check.add_argument("--root", type=Path, help="IQ root; defaults to manifest directory")
    check.add_argument("--production", action="store_true", help="require all 90 coverage cells")
    check.add_argument("--coverage-origin", choices=ORIGINS, default="physical")
    for command in ("template", "schema"):
        commands.add_parser(command).add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        try:
            data = json.loads(args.manifest.read_text(encoding="utf-8"))
            result = validate_manifest(data, args.root or args.manifest.parent,
                                       args.production, args.coverage_origin)
            print(json.dumps(result.to_dict(), indent=2, allow_nan=False))
            return 0 if result.valid else 1
        except (OSError, ValueError) as exc:
            print(json.dumps({"valid": False, "errors": [str(exc)]}))
            return 1
    data = empty_manifest() if args.command == "template" else json.loads(
        Path(__file__).with_name("schema.json").read_text(encoding="utf-8"))
    text = json.dumps(data, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
