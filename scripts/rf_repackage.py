"""Package a new ARM daemon with verified existing PL artifacts and current UI.

This records a daemon/software rebuild; it does not claim a new Vivado build.
"""
import argparse
import json
import shutil
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import merged_release as R
from scripts.package_merged_overlay import package_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--daemon", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--software-contract", choices=tuple(R.SOFTWARE_CONTRACTS),
                        default=R.CURRENT_SOFTWARE_CONTRACT)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--model-status", choices=("experimental", "production"), default="experimental")
    args = parser.parse_args()
    if bool(args.model) != bool(args.evaluation):
        parser.error("--model and --evaluation are required together")
    parent = R.verify_release(args.parent, require_software=True)
    build = json.loads(args.daemon.with_suffix(".build.json").read_text(encoding="utf-8"))
    if build["sha256"] != R.sha256_file(args.daemon):
        raise ValueError("daemon build receipt mismatch")
    args.output.mkdir(parents=True, exist_ok=True)
    hardware = args.output / "hardware"
    hardware.mkdir(exist_ok=True)
    receipt = json.loads((args.parent / "merged_build_receipt.json").read_text(encoding="utf-8"))
    for stem, old, name in (("bit", R.BIT_NAME, "host_bd_wrapper.bit"),
                            ("hwh", R.HWH_NAME, "host_bd.hwh")):
        target = hardware / name
        shutil.copyfile(args.parent / old, target)
        receipt[stem + "_source"] = str(target.resolve())
    receipt[R.SOFTWARE_CONTRACT_KEY] = args.software_contract
    receipt["software"] = R.software_digests(Path(__file__).resolve().parents[1], args.software_contract)
    receipt.pop(R.MODEL_KEY, None)
    if args.model:
        for source, name in ((args.model, R.MODEL_FILE), (args.evaluation, R.EVALUATION_FILE)):
            shutil.copyfile(source, args.output / name)
        receipt[R.MODEL_KEY] = {"schema_version": 1, "status": args.model_status,
            "model_file": R.MODEL_FILE, "model_sha256": R.sha256_file(args.output / R.MODEL_FILE),
            "evaluation_file": R.EVALUATION_FILE,
            "evaluation_sha256": R.sha256_file(args.output / R.EVALUATION_FILE)}
    receipt["software_rebuild"] = {
        "parent_release_id": parent["release_id"], "hardware_rebuilt": False,
        "daemon_rebuilt": True, "daemon_build_receipt_sha256":
        R.sha256_file(args.daemon.with_suffix(".build.json")),
    }
    path = args.output / "merged_build_receipt.json"
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    release = package_release(path, args.output, parent["daemon_version"],
                              parent["protocol_version"], args.daemon)
    R.verify_release(release, require_software=True)
    R.verify_panel_imports(release)
    archive = args.output / (release.name + ".tgz")
    with tarfile.open(str(archive), "w:gz") as bundle:
        for item in sorted(release.iterdir()):
            bundle.add(str(item), arcname="release/" + item.name)
        for name, relative in R.software_sources(args.software_contract).items():
            bundle.add(str(release / name), arcname=relative)
    print(release)
    print(archive)


if __name__ == "__main__":
    main()
