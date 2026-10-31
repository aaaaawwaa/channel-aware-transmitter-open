"""Run the five-stage parity check on the host and on the board, then compare.

The comparison rule is the project's existing one (rf_teaching_verify.compare):
identical key sets, equal-length element-wise lists, every float finite and within
1e-6, everything else equal in type and value.  It is applied to EVERY stage,
including the frames whose BPSK stage refused -- a rejection is a result, and the
two platforms have to refuse the same way, not merely both produce something.
"""
import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import merged_release as R
from scripts.rf_teaching_deploy import load_helper, _run
from scripts.rf_teaching_verify import compare

STAGES = ("base", "cnn", "bpsk", "constellation", "environment")
TAGS = ("compound", "narrowband", "normal", "pulse")


def compare_results(expected, arm, manifest, expected_baseline=None):
    """Check exact four-input/five-stage saved evidence against a verified manifest.

    This is the driver's comparison path, usable without host or board runs.
    Backend identity is the intentional platform difference; measured floats
    retain ``compare``'s finite 1e-6 tolerance throughout the complete records.
    ``expected_baseline`` is the verified candidate's canonical baseline SHA,
    distinct from the manifest's raw baseline file SHA; saved-only callers may
    omit it and check agreement and presence without that extra attestation.
    """
    mismatches = []
    for name, document, native in (("host", expected, False), ("arm", arm, True)):
        for field in ("release_id", "software"):
            mismatches += compare(manifest[field], document.get(field), name + "." + field)
        mismatches += compare(list(STAGES), document.get("stages"), name + ".stages")
        if document.get("native_cnn") is not native:
            mismatches.append(name + ".native_cnn")
        if not isinstance(document.get("baseline_sha256"), str) or not document["baseline_sha256"]:
            mismatches.append(name + ".baseline_sha256")
        if expected_baseline is not None:
            mismatches += compare(expected_baseline, document.get("baseline_sha256"),
                                  name + ".baseline_sha256")

        records = document.get("records")
        if not isinstance(records, list):
            mismatches.append(name + ".records")
            continue
        if len(records) != len(TAGS):
            mismatches.append(name + ".records.length")
        tags, hashes = [], []
        for i, record in enumerate(records):
            path = "%s.records[%d]" % (name, i)
            if not isinstance(record, dict):
                mismatches.append(path)
                continue
            tags.append(record.get("tag"))
            hashes.append(record.get("iq_sha256"))
            if not isinstance(record.get("source_release"), str) or not record["source_release"]:
                mismatches.append(path + ".source_release")
            for stage in STAGES:
                if not isinstance(record.get(stage), dict) or not record[stage]:
                    mismatches.append(path + "." + stage)
        if (not all(isinstance(tag, str) for tag in tags)
                or len(tags) != len(TAGS) or set(tags) != set(TAGS)):
            mismatches.append(name + ".records.tags")
        if (not all(isinstance(digest, str) and digest for digest in hashes)
                or len(hashes) != len(TAGS) or len(set(hashes)) != len(TAGS)):
            mismatches.append(name + ".records.iq_sha256")
        else:
            mismatches += compare(sorted(hashes), document.get("input_iq_sha256"),
                                  name + ".input_iq_sha256")

    # Compare headers and whole records, so source metadata, unknown keys,
    # record counts, list lengths, and every refused-stage result are checked.
    headers = [{k: v for k, v in document.items() if k not in ("native_cnn", "records")}
               for document in (expected, arm)]
    mismatches += compare(headers[0], headers[1], "payload")
    mismatches += compare(expected.get("records"), arm.get("records"), "records")
    per_stage = {stage: sum(stage in path.split(".") for path in mismatches)
                 for stage in STAGES}
    host_records, arm_records = expected.get("records"), arm.get("records")
    compared_inputs = (min(len(host_records), len(arm_records))
                       if isinstance(host_records, list) and isinstance(arm_records, list) else 0)
    return dict(parity_mismatches=mismatches, mismatches_per_stage=per_stage,
                compared_inputs=compared_inputs,
                native_cnn=arm.get("native_cnn"),
                baseline_sha256=arm.get("baseline_sha256"),
                passed=not mismatches)


def run(candidate, iq_dir, output):
    candidate = pathlib.Path(candidate).resolve()
    iq_dir = pathlib.Path(iq_dir).resolve()
    output = pathlib.Path(output).resolve()
    manifest = R.verify_release(candidate, require_software=True)
    if manifest["software_contract"] != "cnn-bpsk-environment-v1":
        raise ValueError("S2 candidate required")
    output.mkdir(parents=True, exist_ok=False)
    runner = ROOT / "scripts/rf_receiver_parity.py"

    # 1. Host side, against the same IQ the ARM side will read.
    host = subprocess.run(
        [sys.executable, "-B", str(runner), "--release-dir", str(candidate),
         "--iq-dir", str(iq_dir), "--output", str(output / "host.json")],
        capture_output=True, text=True, timeout=600)
    (output / "host.log").write_text(host.stdout + host.stderr, encoding="utf-8")
    if host.returncode:
        raise RuntimeError("host parity run failed: " + host.stderr[-2000:])

    receipt = dict(
        release_id=manifest["release_id"], candidate=str(candidate),
        scope="offline exact-candidate five-stage parity on host and ARM; "
              "installed board software untouched; zero RF captures",
        runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
        software=manifest["software"], stages=list(STAGES),
        passed=False, formal_physical="NOT_RUN", human_acceptance="NOT_RUN")

    remote = "/tmp/rf-receiver-parity-" + uuid.uuid4().hex
    client = None
    try:
        client = load_helper().connect()
        with client.open_sftp() as s:
            s.mkdir(remote)
            s.mkdir(remote + "/release")
            s.mkdir(remote + "/inputs")
            s.put(str(runner), remote + "/runner.py")
            for p in sorted(candidate.iterdir()):
                if p.is_symlink() or not p.is_file():
                    raise ValueError("candidate must be flat regular files")
                s.put(str(p), remote + "/release/" + p.name)
            for p in sorted(iq_dir.iterdir()):
                s.put(str(p), remote + "/inputs/" + p.name)
        command = ("python3 -B " + shlex.quote(remote + "/runner.py")
                   + " --release-dir " + remote + "/release"
                   + " --iq-dir " + remote + "/inputs"
                   + " --output " + remote + "/arm.json")
        receipt["exit_code"] = _run(client, load_helper(), command,
                                    output / "arm.log", timeout=600)
        with client.open_sftp() as s:
            s.get(remote + "/arm.json", str(output / "arm.json"))
        expected = json.loads((output / "host.json").read_text(encoding="utf-8"))
        arm = json.loads((output / "arm.json").read_text(encoding="utf-8"))
        canonical_baseline = json.loads((candidate / "rf_environment_baseline.json").read_text(
            encoding="utf-8"))["sha256"]
        receipt.update(compare_results(expected, arm, manifest, expected_baseline=canonical_baseline))
        receipt["passed"] = bool(receipt.get("exit_code") == 0 and receipt["passed"])
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                receipt["close_error"] = str(exc)
        receipt["evidence_sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.is_file()}
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", required=True, type=pathlib.Path)
    p.add_argument("--iq-dir", required=True, type=pathlib.Path)
    p.add_argument("--output", required=True, type=pathlib.Path)
    a = p.parse_args()
    r = run(a.candidate, a.iq_dir, a.output)
    print(json.dumps({k: v for k, v in r.items() if k != "evidence_sha256"}, indent=2))
    raise SystemExit(0 if r["passed"] else 1)
