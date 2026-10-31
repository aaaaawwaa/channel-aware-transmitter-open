# Evaluation test fixtures (synthetic, tiny)

Everything here is **test fixture data**. None of it is a trained model, real RF
evidence or an acceptance artefact.

Regenerate after editing `_build_fixtures.py`:

```
.\ml_training\.venv\Scripts\python.exe ml_training\evaluation\tests\fixtures\_build_fixtures.py
```

Contents:

| path | purpose |
|---|---|
| `sim-small/` | tiny contract-shaped synthetic dataset (`manifest.json`, `frames/test-*.npz`, `gate/gate_test-*.npz`). 4 test frames per class (one clipped per class) and 3 gate_test frames per kind; CLIP frames carry signed-12-bit overflow on purpose. |
| `fake_model/model.json` | random **untrained** weights with the contractual tensor shapes (head `[4,16]`, conv1 `[8,2,7]`, conv2 `[16,8,5]`, conv3 `[16,16,3]`). Used only to check that the evaluation tools really call the Lead's `ml_training/model/numpy_runtime.py`; never used for reported metrics. |
| `real-evidence/` | fake real-evidence tree (synthetic IQ bytes, real-evidence-shaped JSON) covering: clean normal frames, one clipped frame, one duplicate `iq_sha256` across sessions and one in `campaign4/`, one frame without `trainval.truth` (board algorithm output present), one config mismatch, one JSON/IQ hash mismatch, one unique non-canonical campaign4 frame, OFF frames. |

Tests also parse one **real** frame JSON read-only (no inference) when the
canonical evidence tree exists in the workspace; that test skips otherwise.
