"""Configuration loading for the first offline round.

The JSON file is the single source of truth for data/model/gate settings.  Its
SHA-256 is written into the dataset manifest and model metadata so every
artefact is tied to the exact configuration that produced it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "first_round.json"

VARIANTS = ("formal", "smoke")
CLASS_ORDER = ("BPSK", "QPSK", "QAM16", "QAM64")
GATE_KINDS = ("OFF", "NOISE", "TONE", "CLIP")
PREPROCESSING = "dc-complex-rms-v1"


def load_config(path=None):
    """Return (config_dict, sha256_hex_of_raw_bytes)."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    raw = config_path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    return document, hashlib.sha256(raw).hexdigest()


def check_variant(variant):
    if variant not in VARIANTS:
        raise ValueError("variant must be one of %s" % (", ".join(VARIANTS),))
    return variant


def dataset_settings(config, variant="formal"):
    """The dataset settings block for the requested variant (formal/smoke)."""
    check_variant(variant)
    return dict(config["dataset"][check_variant(variant)])


def waveform_settings(config):
    return dict(config["dataset"]["waveform"])


def impairment_settings(config):
    return dict(config["dataset"]["impairment"])


def model_settings(config, variant="formal"):
    """Model settings with the per-variant smoke overrides applied."""
    check_variant(variant)
    settings = dict(config["model"])
    settings.pop("smoke", None)
    if variant == "smoke":
        settings.update(config["model"].get("smoke", {}))
    return settings


def baseline_settings(config, variant="formal"):
    check_variant(variant)
    settings = dict(config["baseline"])
    settings.pop("smoke", None)
    if variant == "smoke":
        settings.update(config["baseline"].get("smoke", {}))
    return settings


def dataset_id(config, variant="formal"):
    return str(dataset_settings(config, variant)["dataset_id"])


def dataset_dir(config, variant="formal", root=None):
    base = Path(root) if root is not None else PACKAGE_ROOT / "datasets"
    return base / dataset_id(config, variant)


def labels():
    return list(CLASS_ORDER)
