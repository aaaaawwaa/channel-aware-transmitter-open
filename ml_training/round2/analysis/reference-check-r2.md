# Independent reference check: sim-r2

Overall: **PASS** (22/22 checks passed)

| field | value |
|---|---|
| dataset dir | `<LOCAL_PATH>` |
| manifest sha256 | `83afaf8a9fe604114d3d1f260a4e1e445aaf7575ae9f26bff3d7bfbdd62e88e9` |
| config | `<LOCAL_PATH>` |
| config sha256 | `431c4e7b3ac53a6c91a8075816049945ab9f386476c71150dd0e8acffe2cbdde` |
| frames checked | 4800 |
| clipped frames | 226 |
| exact reconstructions | 4800 |
| elapsed | 6.67 s |

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
  "frames_checked": 4800,
  "clipping_headroom_sigma": 5.5,
  "peak_normalisation": "complex",
  "clipped_frames": 226,
  "clipped_by_split": {
    "train": 0,
    "val": 0,
    "test": 0,
    "gate_cal": 113,
    "gate_test": 113
  },
  "clipped_by_class": {
    "BPSK": 0,
    "QPSK": 0,
    "QAM16": 0,
    "QAM64": 0
  },
  "frames_by_class": {
    "BPSK": 1000,
    "QPSK": 1000,
    "QAM16": 1000,
    "QAM64": 1000
  },
  "clipped_ratio_by_class": {
    "BPSK": 0.0,
    "QPSK": 0.0,
    "QAM16": 0.0,
    "QAM64": 0.0
  },
  "gate_clipped_by_kind": {
    "OFF": 0,
    "NOISE": 26,
    "TONE": 0,
    "CLIP": 200
  },
  "reconstruction_checked": 4800,
  "snr_residual_checked": 4000,
  "snr_residual_max_rel_dev": 0.07043,
  "snr_residual_var_ratio_min": 0.92957,
  "snr_residual_var_ratio_max": 1.051114,
  "sigma_formula_max_abs_diff": 0.0,
  "duplicate_iq": {
    "cross_split": 0,
    "intra_split": 0,
    "unique_frames": 4800,
    "frames_hashed": 4800
  }
}
```
