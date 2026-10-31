"""Real-campaign source configuration.

Each source names an existing evidence directory and a parser kind.  The importer never copies
or modifies the original IQ; it only indexes paths and digests.  Synthetic fixtures are a
separate source and are always marked ``synthetic=true``.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE / "config" / "sources.json"

REQUIRED_SOURCE_FIELDS = ("source_id", "kind", "root", "synthetic")


def load_sources(path=None):
    path = Path(path) if path is not None else DEFAULT_CONFIG
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "ml-real-campaign-sources-v1":
        raise ValueError("unsupported sources schema: %r" % document.get("schema"))
    sources = document.get("sources") or []
    if not sources:
        raise ValueError("sources configuration has no sources")
    seen = set()
    for source in sources:
        for field in REQUIRED_SOURCE_FIELDS:
            if field not in source:
                raise ValueError("source missing %s: %r" % (field, source))
        if source["source_id"] in seen:
            raise ValueError("duplicate source_id %r" % source["source_id"])
        seen.add(source["source_id"])
        if not (ROOT / source["root"]).is_dir():
            raise ValueError("source root missing for %s: %s"
                             % (source["source_id"], source["root"]))
    return document


def resolve(path):
    return ROOT / path
