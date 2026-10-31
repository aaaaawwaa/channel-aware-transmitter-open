# SYNTHETIC_FIXTURE

This directory is **not real RF data** and must not enter TRAIN or be reported as a real
capture. `make_fixtures.py` creates a tiny 3-frame fixture:

- `01-off-first`: valid OFF domain (zero IQ; model gate rejects);
- `02-bpsk-2`: valid BPSK using the existing frozen ideal BPSK reference IQ;
- `03-invalid-digest`: deliberately invalid IQ length, used only to verify invalid-input
  handling.

The fixture truth/plan are explicitly marked `SYNTHETIC_FIXTURE`; they validate the
entry interface only. The real new-seed batch remains `raw/` under the evidence dataset
and is not generated here.

TX evidence shape follows production:

- `tx_load_evidence.worker_args` = source parameters
  (`mode/seed/amplitude/filter/rolloff/sample_rate_hz/symbol_rate_hz/waveform`);
- `tx_load_evidence.tx_load_metadata` = daemon protocol metadata
  (`samples/bytes/format/direction/wrap`).

`integration_build_runner.py` is the end-to-end test: real `build_tx_waveform` → real
runner recording methods → evaluator; only the transport response is fake. It covers
valid ON, valid OFF, payload SHA mismatch and worker_args mismatch.

