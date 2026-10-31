"""Real release-local worker loading across the additive modrec4 contract.

No RF, no client, no board: the worker's own `_ensure_*` methods verify the
candidate release exactly as `LiveUiSession` starts it.  The pre-fix worker is
loaded from the installed ad37 release, so the red condition is a real old
artifact rather than a monkeypatched available flag.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
RELEASE = (ROOT / "evidence/rf-teaching/s2-46db-modrec4-product-2026-10-21/"
           "candidate/releases/v11/out/releases/ad37bad4f6b0a6e3ba707a95be7fc196")
CURRENT = pathlib.Path(os.environ.get("RF_WORKER_TEST_SOURCE",
                                      ROOT / "pynq_overlay/sw/rf_status_worker.py"))


def _run(worker_source, release):
    script = r'''
import importlib.util, json, sys
from pathlib import Path
release = Path(sys.argv[1]); source = Path(sys.argv[2])
sys.path.insert(0, str(release))
spec = importlib.util.spec_from_file_location("loaded_worker", str(source))
module = importlib.util.module_from_spec(spec)
sys.modules["loaded_worker"] = module
spec.loader.exec_module(module)
worker = module.RfStatusWorker(object(), release_directory=str(release))
for name in ("_ensure_demod", "_ensure_model", "_ensure_environment",
             "_ensure_teaching", "_ensure_modrec4"):
    getattr(worker, name)()
print(json.dumps({
    "demod": worker.demod_status(), "model": worker.model_status(),
    "environment": worker.environment_status(),
    "teaching": worker.teaching_status(), "modrec4": worker.modrec4_status(),
}, allow_nan=False))
'''
    return subprocess.run([sys.executable, "-B", "-c", script, str(release), str(worker_source)],
                          check=True, capture_output=True, text=True)


def test_old_installed_worker_does_not_enable_bpsk_on_modrec4():
    old = RELEASE / "rf_status_worker.py"
    payload = json.loads(_run(old, RELEASE).stdout)
    assert payload["demod"]["available"] is False
    assert payload["model"]["available"] is False
    assert payload["environment"]["available"] is False
    assert payload["teaching"]["available"] is False
    # The additive adapter is already present in ad37; this is not the gap.
    assert payload["modrec4"]["available"] is True


def test_current_worker_really_loads_all_inherited_capabilities():
    current = CURRENT
    payload = json.loads(_run(current, RELEASE).stdout)
    for key in ("demod", "model", "environment", "teaching", "modrec4"):
        assert payload[key]["available"] is True, (key, payload[key])
    assert payload["model"]["production"] is True
    assert payload["modrec4"]["release_id"] == RELEASE.name
    assert payload["modrec4"]["artifact_status"] == "experimental"
