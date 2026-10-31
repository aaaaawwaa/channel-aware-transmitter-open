#!/usr/bin/env python3
"""Package one completed Vivado build into a verified immutable local release."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

try:
    from scripts.merged_release import (
        ADDRESS_PINS, BIT_NAME, HWH_NAME, DAEMON_NAME, MANIFEST_NAME, SOFTWARE_KEY,
        parse_hwh_addresses, release_identity, sha256_file, validate_address_contract,
        validate_software_map, verify_release, SOFTWARE_SOURCES, verify_panel_imports,
        SOFTWARE_CONTRACT_KEY, MODEL_KEY, MODREC4_MODEL_KEY, software_sources,
        validate_software_contract, verify_model_artifact,
        verify_modrec4_artifact,
    )
except ModuleNotFoundError:
    from merged_release import (
        ADDRESS_PINS, BIT_NAME, HWH_NAME, DAEMON_NAME, MANIFEST_NAME, SOFTWARE_KEY,
        parse_hwh_addresses, release_identity, sha256_file, validate_address_contract,
        validate_software_map, verify_release, SOFTWARE_SOURCES, verify_panel_imports,
        SOFTWARE_CONTRACT_KEY, MODEL_KEY, MODREC4_MODEL_KEY, software_sources,
        validate_software_contract, verify_model_artifact,
        verify_modrec4_artifact,
    )

RECEIPT_NAME = "merged_build_receipt.json"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def load_receipt(path: Path) -> dict:
    path = Path(path)
    if path.name != RECEIPT_NAME or not path.is_file():
        raise ValueError("missing merged build receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid merged build receipt: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid merged build receipt schema")
    if not isinstance(value.get("build_id"), str) or not _ID.fullmatch(value["build_id"]):
        raise ValueError("invalid receipt build_id")
    sources = []
    for stem, filename in (("bit", "host_bd_wrapper.bit"), ("hwh", "host_bd.hwh")):
        source = value.get(stem + "_source")
        digest = value.get(stem + "_sha256")
        if not isinstance(source, str) or not Path(source).is_absolute() or Path(source).name != filename:
            raise ValueError(f"invalid receipt {stem} source")
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError(f"invalid receipt {stem} hash")
        source_path = Path(source)
        if not source_path.is_file() or sha256_file(source_path) != digest:
            raise ValueError(f"receipt {stem} hash mismatch")
        sources.append(source_path.resolve())
    if sources[0] == sources[1]:
        raise ValueError("receipt bit/HWH source collision")
    addresses = parse_hwh_addresses(sources[1])
    validate_address_contract(addresses)
    if value.get(SOFTWARE_KEY) is not None:
        validate_software_contract(value[SOFTWARE_KEY], value.get(SOFTWARE_CONTRACT_KEY))
    elif value.get(SOFTWARE_CONTRACT_KEY) is not None or value.get(MODEL_KEY) is not None:
        raise ValueError("software contract requires software map")
    verify_model_artifact(path.parent, value.get(MODEL_KEY), value.get(SOFTWARE_CONTRACT_KEY))
    verify_modrec4_artifact(path.parent, value.get(MODREC4_MODEL_KEY))
    return value


def package_release(receipt_path: Path, output_root: Path,
                    daemon_version: str, protocol_version: int,
                    daemon_source: Path) -> Path:
    receipt = load_receipt(receipt_path)
    if not isinstance(daemon_version, str) or not _ID.fullmatch(daemon_version):
        raise ValueError("invalid daemon_version")
    if type(protocol_version) is not int or protocol_version != 1:
        raise ValueError("invalid protocol_version")
    daemon_source = Path(daemon_source)
    if not daemon_source.is_file():
        raise ValueError("missing daemon binary")
    daemon_hash = sha256_file(daemon_source)
    software = receipt.get(SOFTWARE_KEY)
    contract = receipt.get(SOFTWARE_CONTRACT_KEY)
    artifact = receipt.get(MODEL_KEY)
    modrec4_artifact = receipt.get(MODREC4_MODEL_KEY)
    if software is not None:
        validate_software_map(software)
    release_id = release_identity(
        receipt["build_id"], receipt["bit_sha256"], receipt["hwh_sha256"],
        daemon_version, protocol_version, daemon_hash, software,
        software_contract=contract, model_artifact=artifact,
        modrec4_artifact=modrec4_artifact)
    output_root = Path(output_root)
    release = output_root / "releases" / release_id
    if release.exists():
        try:
            existing = verify_release(release, require_software=software is not None)
        except (ValueError, OSError) as exc:
            raise ValueError(f"existing release invalid: {exc}") from exc
        if existing["build_id"] != receipt["build_id"] or (
            existing["bit_sha256"], existing["hwh_sha256"],
            existing["daemon_sha256"], existing["daemon_version"],
            existing["protocol_version"]
        ) != (
            receipt["bit_sha256"], receipt["hwh_sha256"], daemon_hash,
            daemon_version, protocol_version
        ):
            raise ValueError("existing release identity mismatch")
        if existing.get(SOFTWARE_KEY) != software:
            raise ValueError("existing release software map mismatch")
        return release
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".merged-stage-", dir=output_root))
    try:
        bit = stage / BIT_NAME
        hwh = stage / HWH_NAME
        shutil.copyfile(receipt["bit_source"], bit)
        shutil.copyfile(receipt["hwh_source"], hwh)
        shutil.copyfile(receipt_path, stage / RECEIPT_NAME)
        shutil.copyfile(daemon_source, stage / DAEMON_NAME)
        if software is not None:
            root = Path(__file__).resolve().parents[1]
            for name, relative in software_sources(contract or "legacy-v1").items():
                shutil.copyfile(root / relative, stage / name)
        if artifact is not None:
            for stem in ("model", "evaluation"):
                name = artifact[stem + "_file"]
                shutil.copyfile(Path(receipt_path).parent / name, stage / name)
        if modrec4_artifact is not None:
            for stem in ("cnn", "baseline", "rule"):
                name = modrec4_artifact[stem + "_file"]
                shutil.copyfile(Path(receipt_path).parent / name, stage / name)
        if sha256_file(stage / DAEMON_NAME) != daemon_hash:
            raise ValueError("daemon source changed while copying")
        if sha256_file(bit) != receipt["bit_sha256"] or sha256_file(hwh) != receipt["hwh_sha256"]:
            raise ValueError("receipt source changed while copying")
        addresses = parse_hwh_addresses(hwh)
        validate_address_contract(addresses)
        manifest = {
            "schema_version": 1,
            "release_id": release_id,
            "build_id": receipt["build_id"],
            "bit_file": BIT_NAME,
            "bit_sha256": receipt["bit_sha256"],
            "hwh_file": HWH_NAME,
            "hwh_sha256": receipt["hwh_sha256"],
            "daemon_version": daemon_version,
            "daemon_file": DAEMON_NAME,
            "daemon_sha256": daemon_hash,
            "protocol_version": protocol_version,
            "address_contract": addresses,
        }
        if software is not None:
            manifest[SOFTWARE_KEY] = software
        if contract is not None:
            manifest[SOFTWARE_CONTRACT_KEY] = contract
        if artifact is not None:
            manifest[MODEL_KEY] = artifact
        if modrec4_artifact is not None:
            manifest[MODREC4_MODEL_KEY] = modrec4_artifact
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verify_release(stage, require_software=software is not None)
        if software is not None:
            verify_panel_imports(stage)
        release.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, release)
        for item in release.iterdir():
            item.chmod(0o444)
        release.chmod(0o555)
        return release
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--daemon-version", default="1.0.0")
    parser.add_argument("--daemon", required=True, type=Path)
    parser.add_argument("--protocol-version", type=int, default=1)
    args = parser.parse_args()
    release = package_release(
        args.receipt, args.output, args.daemon_version, args.protocol_version, args.daemon)
    print(release)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


