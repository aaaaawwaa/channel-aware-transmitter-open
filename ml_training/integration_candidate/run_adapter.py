#!/usr/bin/env python3
"""CLI entry point for the integration adapter prototype.

Examples::

    python ml_training/integration_candidate/run_adapter.py --frame <frames/...iq> \
        --context <input_context.json> --environment <environment.json> \
        --demod <metadata.json> --out result.json
    python ml_training/integration_candidate/run_adapter.py --all
    python ml_training/integration_candidate/run_adapter.py --negative-cases
    python ml_training/integration_candidate/run_adapter.py --check

Only host-side code; no device access, no product runtime change, no deployment.
The input context is capture/configuration metadata supplied by the caller; if
``--context`` is omitted the CLI uses the declared package context
(60 MSPS / decim 1 / 3 MSym/s / RECT / s16_iq_le / 4096 samples) and labels it as
``cli-default-declared-contract`` in the printed summary.  The adapter itself requires an
explicit context; ``--no-context`` demonstrates the unavailable path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ml_training.integration_candidate.input_domain import FROZEN_INPUT_CONTEXT  # noqa: E402
from ml_training.integration_candidate.modrec4_adapter import ModRec4Adapter  # noqa: E402

ARM_PROBE = ROOT / "ml_training" / "arm_probe"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_context(args):
    if args.no_context:
        return None, "none (negative demonstration)"
    if args.context:
        return load_json(args.context), args.context
    return dict(FROZEN_INPUT_CONTEXT), "cli-default-declared-contract"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", help="one .iq frame path")
    parser.add_argument("--all", action="store_true", help="evaluate all arm_probe frames")
    parser.add_argument("--check", action="store_true", help="run host consistency check")
    parser.add_argument("--negative-cases", action="store_true",
                        help="run the direct input-domain negative cases")
    parser.add_argument("--context", help="input-domain context JSON (capture metadata)")
    parser.add_argument("--no-context", action="store_true",
                        help="omit context to exercise the unavailable path")
    parser.add_argument("--environment", help="optional environment JSON (passthrough)")
    parser.add_argument("--demod", help="optional demod JSON (passthrough)")
    parser.add_argument("--six-class", help="optional existing six-class result JSON (passthrough)")
    parser.add_argument("--out", default="adapter_result.json")
    args = parser.parse_args(argv)

    if args.check:
        from ml_training.integration_candidate import consistency_check

        return consistency_check.main()
    if args.negative_cases:
        from ml_training.integration_candidate import negative_cases

        return negative_cases.main()

    adapter = ModRec4Adapter()
    context, context_source = resolve_context(args)
    environment = load_json(args.environment) if args.environment else None
    demod = load_json(args.demod) if args.demod else None
    six_class = load_json(args.six_class) if args.six_class else None
    if args.all:
        manifest = load_json(ARM_PROBE / "input_manifest.json")
        results = []
        for entry in manifest["frames"]:
            result = adapter.evaluate_frame(ARM_PROBE / entry["path"], context=context,
                                            environment=environment, demod=demod,
                                            six_class=six_class)
            result["frame_id"] = entry["id"]
            results.append(result)
        document = {"schema": "ml-modrec-integration-candidate-manifest-v1",
                    "input_context_source": context_source,
                    "frames": len(results), "results": results}
        Path(args.out).write_text(json.dumps(document, indent=1), encoding="utf-8")
        print("[adapter] evaluated %d frames (context=%s) -> %s"
              % (len(results), context_source, args.out))
        return 0
    if not args.frame:
        parser.error("one of --frame/--all/--check/--negative-cases is required")
    result = adapter.evaluate_frame(args.frame, context=context, environment=environment,
                                    demod=demod, six_class=six_class)
    document = {
        "schema": "ml-modrec-integration-candidate-output-v1",
        "input_context_source": context_source,
        "result": result,
        "product_fields": adapter.to_product_fields(result),
    }
    Path(args.out).write_text(json.dumps(document, indent=1), encoding="utf-8")
    print("[adapter] status=%s final_label=%s source=%s accepted=%s reason=%r -> %s"
          % (result["status"], result["modulation"]["final_label"],
             result["modulation"]["final_label_source"],
             result["modulation"]["accepted"], result["modulation"]["reason"], args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
