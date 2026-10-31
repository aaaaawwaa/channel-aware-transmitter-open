# Independent reference check: sim-smoke

Overall: **PASS** (22/22 checks passed)

| field | value |
|---|---|
| dataset dir | `ml_training/datasets/sim-smoke` |
| manifest sha256 | `d932e887e18a8c68e2f58d4231b54785588350cdb675d20b83fc60a7ee88a6b7` |
| config | `<LOCAL_PATH>` |
| config sha256 | `c34ba443065ffc6a8ba2bd0ed6285eac68f5a5d81030b0482d934e518848cc78` |
| frames checked | 280 |
| clipped frames | 24 |
| exact reconstructions | 280 |
| elapsed | 0.78 s |

## Checks

| check | result | detail / failures |
|---|---|---|
| `manifest_top_level` | PASS | required frozen top-level manifest fields present |
| `config_identity` | PASS | config file exists and sha256 matches manifest |
| `waveform_frozen` | PASS | waveform block and clipping headroom match frozen contract/config |
| `split_plan` | PASS | split/session/file plan matches frozen config; families disjoint |
| `npz_file_sha256` | PASS | every npz sha256 matches the manifest |
| `npz_arrays` | PASS | required arrays, dtypes and shapes present |
| `frame_metadata` | PASS | frame_id/session_id/label/seed_family metadata consistent |
| `exact_frame_reconstruction` | PASS | every frame rebuilt byte-for-byte from recorded seeds/params |
| `iq_sha256` | PASS | per-frame iq_sha256 matches stored bytes |
| `clipping_flags` | PASS | clipped flag matches pre-saturation out-of-range detection |
| `parameter_ranges` | PASS | per-frame parameters within frozen ranges / sigma formula exact |
| `amplitude_safety_cap` | PASS | amplitude_codes respects the frozen clipping-safety cap |
| `snr_residual` | PASS | measured residual noise variance matches sigma (quantisation aware) |
| `snr_grid` | PASS | test snr grid exact; train/val snr within uniform range |
| `seed_uniqueness_and_duplicates` | PASS | RNG seeds unique per frame; no cross/intra split duplicate IQ |
| `gate_clip_rails` | PASS | gate CLIP frames saturated, flagged and contain rail codes |
| `rotated_component_bound` | PASS | after receiver phase/CFO rotation max(|I|,|Q|) <= amplitude_codes everywhere (complex-envelope bound, +1 code) |
| `clipped_statistics` | PASS | clipped counts match the stored frames |
| `summary_counts` | PASS | frame counts, per-split totals and file entries match frozen plan |
| `bit_mapping_vs_contract_and_product` | PASS | all bit patterns match the frozen contract/product mapper |
| `noiseless_quantization` | PASS | clean max|I+jQ| == amplitude_codes; rotated component peak <= amplitude; np.rint ties-to-even correct |
| `constellation_power_coefficients` | PASS | analytic c matches measured (synthetic probes + dataset aggregate) |

## Numeric stats

```json
{
  "frames_checked": 280,
  "clipping_headroom_sigma": 5.5,
  "peak_normalisation": "complex",
  "clipped_frames": 24,
  "clipped_by_split": {
    "train": 0,
    "val": 0,
    "test": 0,
    "gate_cal": 11,
    "gate_test": 13
  },
  "clipped_by_class": {
    "BPSK": 0,
    "QPSK": 0,
    "QAM16": 0,
    "QAM64": 0
  },
  "frames_by_class": {
    "BPSK": 50,
    "QPSK": 50,
    "QAM16": 50,
    "QAM64": 50
  },
  "clipped_ratio_by_class": {
    "BPSK": 0.0,
    "QPSK": 0.0,
    "QAM16": 0.0,
    "QAM64": 0.0
  },
  "gate_clipped_by_kind": {
    "OFF": 0,
    "NOISE": 4,
    "TONE": 0,
    "CLIP": 20
  },
  "reconstruction_checked": 280,
  "snr_residual_checked": 200,
  "snr_residual_max_rel_dev": 0.044469,
  "snr_residual_var_ratio_min": 0.963696,
  "snr_residual_var_ratio_max": 1.044469,
  "sigma_formula_max_abs_diff": 0.0,
  "duplicate_iq": {
    "cross_split": 0,
    "intra_split": 0,
    "unique_frames": 280,
    "frames_hashed": 280
  }
}
```
