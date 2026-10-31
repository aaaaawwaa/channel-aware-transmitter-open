# Independent reference check: sim-v1

Overall: **PASS** (22/22 checks passed)

| field | value |
|---|---|
| dataset dir | `ml_training/datasets/sim-v1` |
| manifest sha256 | `9f70a0c8c5e3b5a1395617d71f860fd60c2bd3462511efa5330dea6f310a3ecd` |
| config | `<LOCAL_PATH>` |
| config sha256 | `c34ba443065ffc6a8ba2bd0ed6285eac68f5a5d81030b0482d934e518848cc78` |
| frames checked | 9200 |
| clipped frames | 224 |
| exact reconstructions | 9200 |
| elapsed | 11.64 s |

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
  "frames_checked": 9200,
  "clipping_headroom_sigma": 5.5,
  "peak_normalisation": "complex",
  "clipped_frames": 224,
  "clipped_by_split": {
    "train": 0,
    "val": 0,
    "test": 0,
    "gate_cal": 112,
    "gate_test": 112
  },
  "clipped_by_class": {
    "BPSK": 0,
    "QPSK": 0,
    "QAM16": 0,
    "QAM64": 0
  },
  "frames_by_class": {
    "BPSK": 2100,
    "QPSK": 2100,
    "QAM16": 2100,
    "QAM64": 2100
  },
  "clipped_ratio_by_class": {
    "BPSK": 0.0,
    "QPSK": 0.0,
    "QAM16": 0.0,
    "QAM64": 0.0
  },
  "gate_clipped_by_kind": {
    "OFF": 0,
    "NOISE": 24,
    "TONE": 0,
    "CLIP": 200
  },
  "reconstruction_checked": 9200,
  "snr_residual_checked": 8400,
  "snr_residual_max_rel_dev": 0.066426,
  "snr_residual_var_ratio_min": 0.933574,
  "snr_residual_var_ratio_max": 1.059585,
  "sigma_formula_max_abs_diff": 0.0,
  "duplicate_iq": {
    "cross_split": 0,
    "intra_split": 0,
    "unique_frames": 9200,
    "frames_hashed": 9200
  }
}
```
