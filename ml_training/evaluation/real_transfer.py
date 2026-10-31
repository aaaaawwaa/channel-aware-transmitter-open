#!/usr/bin/env python
"""Read-only real-IQ inventory and BPSK transfer evaluation (CONTRACTS §11).

This script ONLY reads existing physical evidence frames; it never writes, moves or
deletes anything under ``evidence/``.  Ground truth comes exclusively from

* ``trainval.truth`` (``normal`` / ``off``), and
* ``demod_result.submitted.config`` (``mode=BPSK``, ``symbol_rate_hz=3000000``,
  ``shaping=RECT``).

Board algorithm outputs (``trainval.label`` / ``trainval.reason``,
``model_result.*``, ``demod_result.result.*``, environment labels) are recorded for
transparency but are **never** used as labels.

Scope/anti-double-counting rules:
* canonical sessions: ``campaign5-val-followup/download/{train-01,train-02,val-01}``;
* ``campaign4/download`` is scanned for duplicate mapping only; equal ``iq_sha256``
  frames are counted ONCE at their first canonical path;
* clipped frames (``analysis.clipping_count>0`` or ``output_clipped=true``) are
  excluded and listed separately;
* no four-class real overall accuracy is computed (real truth exists only for the
  single BPSK-normal configuration and OFF scenes).

Outputs (under ``--out``): ``real_inventory.json``, ``real_bpsk.md``,
``real_failures.json``.  Exit code 0 on success.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter, OrderedDict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml_training.evaluation import _common as C  # noqa: E402

EVIDENCE_CAMPAIGNS = PROJECT_ROOT / "evidence" / "rf-teaching" / "codex-s2-takeover-2026-10-18"
CANONICAL_ROOT = EVIDENCE_CAMPAIGNS / "campaign5-val-followup" / "download"
CAMPAIGN4_ROOT = EVIDENCE_CAMPAIGNS / "campaign4" / "download"
CANONICAL_SESSIONS = ("train-01", "train-02", "val-01")
INDEPENDENT_SESSION = "val-01"
TRUTH_VALUES = ("normal", "off")
REQUIRED_CONFIG = OrderedDict([
    ("mode", "BPSK"),
    ("symbol_rate_hz", 3000000),
    ("shaping", "RECT"),
])
SESSION_MANIFEST_SCHEMA = "rf-environment-session-manifest-v1"
DEFAULT_OUT = PROJECT_ROOT / "ml_training" / "evaluation" / "reports"
IQ_INPUT_FS_HZ = 60_000_000.0
ANALYSIS_FIELDS = (
    "valid", "samples", "complex_rms", "i_rms", "q_rms", "complex_dbfs",
    "i_dbfs", "q_dbfs", "peak_hz", "occupied_bandwidth_hz", "clipping_count",
    "output_clipped", "no_energy", "input_fs_hz", "decimation", "analysis_fs_hz",
)
ENVIRONMENT_FEATURE_FIELDS = (
    "rms_codes", "crest_ratio", "q999_ratio", "q99_ratio", "sign_balance",
    "tone_concentration", "tone_excess", "tone_frequency_hz", "window_us",
)

NOT_RUN = list(C.HOST_NOT_RUN) + [
    "four_class_real_overall_accuracy: NOT_RUN - not computed and prohibited by CONTRACTS §11; "
    "real truth exists only for the single BPSK-normal configuration and OFF scenes.",
    "real_qpsk_qam16_qam64_frames: NOT_RUN - the canonical real sessions contain no QPSK/QAM16/"
    "QAM64 ground-truth frames.",
    "real_quality_gate_population: NOT_RUN - no real gate_test population with OFF/NOISE/TONE/CLIP "
    "was collected; only OFF frames exist and are reported as OFF gate behaviour.",
    "interference_physical_scenarios: NOT_RUN - no canonical per-frame manifest/receipt covering "
    "an interference physical scenario was included; such scenarios are absent from the canonical "
    "session set and are NOT_RUN, not silently folded into the BPSK transfer numbers.",
    "gate_threshold_selection_on_real_data: NOT_RUN - forbidden by CONTRACTS §11; thresholds are "
    "selected on simulation val + gate_cal only, and val-01 is untouched by any selection.",
    "demod_ber_derived_accuracy: NOT_RUN - demod BER exists on the board path but is not a model "
    "metric and is not used as a label or accuracy here.",
    "arm_inference: NOT_RUN - see host-vs-ARM statement.",
    "board_hardware_accuracy: NOT_RUN - see host-vs-ARM statement.",
]

LIMITATIONS = [
    "Small real sample: 3 sessions x (10 normal BPSK + 1 off) = 30 normal + 3 OFF counted frames; "
    "one frame per listed file, no augmentation and no reuse.",
    "Real BPSK has exactly ONE configuration in scope: fs=60 MSPS, BPSK 3 MSym/s RECT, LO=80 MHz, "
    "attenuation 46000 mdB, 4096-sample snapshot. Other symbol rates/shapings are NOT_RUN (no data).",
    "OFF frames carry trainval.physical=false (no-signal scene); they are real board captures used "
    "only for OFF gate behaviour, never as class truth and never with a fake class label.",
    "Board algorithm labels differ from the contract truth (several normal frames are labelled "
    "UNKNOWN/PULSE/CLEAR by the board algorithm); those outputs are ignored here, not converted "
    "into labels.",
    "No clipped real frames were present in the canonical sessions, so real clipping rejection is "
    "NOT_RUN (simulation covers the CLIP gate kind).",
    "Zero-shot transfer only: no real frame was used for training, validation or threshold "
    "selection (CONTRACTS §11); val-01 is the untouched independent real session.",
    "Inference ran on the host CPU with the numpy runtime; these are NOT ARM numbers and NOT board "
    "numbers.",
    "campaign4 and campaign5 duplicate frames were deduplicated by iq_sha256; the one unique "
    "campaign4 val-01 frame lies outside the canonical campaign5 session set and is excluded "
    "(listed with its reason), not counted.",
    "train-02 failure-mode root cause is NOT_ESTABLISHED: the session was captured under the same "
    "requested RF domain as train-01/val-01, but its input diagnostics differ (lower RMS, different "
    "carrier phase, lower coherence-derived SNR, environment rule flags it as PULSE/SPARSE_AMPLITUDE_"
    "OUTLIERS). Read-only diagnostics plus the Lead controlled rotation/amplitude/AWGN probes are "
    "correlational evidence, not a proven cause.",
    "The quality gate does NOT reject the train-02 high-confidence QPSK misclassifications (all 10 "
    "are accepted as QPSK above max_softmax); rejecting unknown/distorted input is future work.",
]

NOT_RUN = list(C.HOST_NOT_RUN) + [
    "four_class_real_overall_accuracy: NOT_RUN - not computed and prohibited by CONTRACTS §11; "
    "real truth exists only for the single BPSK-normal configuration and OFF scenes.",
    "real_qpsk_qam16_qam64_frames: NOT_RUN - the canonical real sessions contain no QPSK/QAM16/"
    "QAM64 ground-truth frames.",
    "real_quality_gate_population: NOT_RUN - no real gate_test population with OFF/NOISE/TONE/CLIP "
    "was collected; only OFF frames exist and are reported as OFF gate behaviour.",
    "interference_physical_scenarios: NOT_RUN - no canonical per-frame manifest/receipt covering "
    "an interference physical scenario was included; such scenarios are absent from the canonical "
    "session set and are NOT_RUN, not silently folded into the BPSK transfer numbers.",
    "gate_threshold_selection_on_real_data: NOT_RUN - forbidden by CONTRACTS §11; thresholds are "
    "selected on simulation val + gate_cal only, and val-01 is untouched by any selection.",
    "demod_ber_derived_accuracy: NOT_RUN - demod BER exists on the board path but is not a model "
    "metric and is not used as a label or accuracy here.",
    "train_02_root_cause: NOT_RUN - NOT_ESTABLISHED; only read-only diagnostics plus the Lead "
    "controlled rotation/amplitude/AWGN probes exist, no causal proof.",
    "arm_inference: NOT_RUN - see host-vs-ARM statement.",
    "board_hardware_accuracy: NOT_RUN - see host-vs-ARM statement.",
]

LEAD_DIAGNOSTICS = OrderedDict([
    ("attribution", "Lead diagnostic (host numpy, not ARM); merged read-only into this report. "
                    "It is diagnostic evidence, not a label and not an acceptance result."),
    ("prediction_distribution", OrderedDict([
        ("train-01", "10/10 BPSK"),
        ("train-02", "0/10 BPSK - all 10 predicted QPSK (scores 0.53-0.88, margin 0.06-0.75)"),
        ("val-01", "10/10 BPSK"),
        ("baseline_all_sessions", "30/30 BPSK"),
    ])),
    ("session_physical_differences_read_only", [
        OrderedDict([("session", "train-01"), ("complex_rms", 570.0),
                     ("phase_deg", -5.2), ("snr_est_db", 16.1),
                     ("environment_rule",
                      "7 CLEAR + 3 UNKNOWN(AMPLITUDE_OUTSIDE_RULE_SUPPORT)")]),
        OrderedDict([("session", "train-02"), ("complex_rms", 322.0),
                     ("phase_deg", 62.8), ("snr_est_db", 10.4),
                     ("environment_rule",
                      "9/10 PULSE/SPARSE_AMPLITUDE_OUTLIERS + 1 UNKNOWN"
                      "(AMPLITUDE_OUTSIDE_RULE_SUPPORT)")]),
        OrderedDict([("session", "val-01"), ("complex_rms", 620.0),
                     ("phase_deg", 1.7), ("snr_est_db", 16.6),
                     ("environment_rule", "9 CLEAR + 1 UNKNOWN")]),
    ]),
    ("controlled_diagnostics", [
        "Rotating a correctly classified train-01 frame about the origin by +60 deg makes the CNN "
        "predict QPSK; +68 deg and +90 deg flip back to BPSK.",
        "Scaling the amplitude to the train-02 RMS (322) does not change the prediction.",
        "train-01 + AWGN matched to 10.4 dB / 63 deg does NOT reproduce the QPSK prediction, so "
        "phase alone plus AWGN does not explain the failure; additional unmodeled distortion is "
        "implicated (consistent with the environment PULSE rule label).",
    ]),
    ("interpretation",
     "Root cause NOT_ESTABLISHED. Evidence supports a session-correlated carrier phase (the 10 "
     "train-02 frames share roughly the same phase) interacting with a phase-dependent decision-"
     "boundary pit near 55-65 deg in the small CNN; the feature baseline's cumulative-moment "
     "features are more robust to this distortion. The gate accepts these misclassifications with "
     "high confidence, so they are not caught by rejection."),
    ("truth_unchanged", "Truth is unchanged: TX config BPSK/3 MSym/s/RECT and demod BER=0 from "
                        "demod_result.submitted.config/result (diagnostics, not labels)."),
])

STATEMENTS = OrderedDict([
    ("read_only_evidence", "evidence/ was opened read-only; no file under evidence/ was created, "
                           "modified, moved or deleted by this script."),
    ("label_source", "truth = trainval.truth (normal/off); transmission config = "
                     "demod_result.submitted.config (BPSK/3Msym/s/RECT). No demod/model/environment "
                     "output was used as a label."),
    ("four_class_real_accuracy", "NOT_RUN / prohibited: a four-class real overall accuracy cannot "
                                 "be computed from BPSK-only real truth and is not reported."),
    ("single_real_config", "The only real BPSK configuration is 3 MSym/s RECT at 60 MSPS with a "
                           "4096-sample snapshot; it is a single configuration, not a coverage sweep."),
    ("interference_scenarios", "Physical interference/impairment scenarios, if any exist elsewhere, "
                               "are NOT included: no canonical per-frame manifest/evidence supports "
                               "them in the scanned roots, so interference transfer is NOT_RUN."),
    ("host_vs_arm", "All inference is host CPU numpy; these results are NOT ARM and NOT board "
                    "measurements and must not be reported as such."),
    ("dedupe_rule", "Frames are deduplicated by iq_sha256; equal hashes across campaign4/campaign5 "
                    "are counted once at the first canonical path."),
    ("independence", "val-01 is the independent real session that participated in no threshold "
                     "selection or tuning; it is reported first."),
])


class RealEvidenceError(C.EvaluationError):
    """The real evidence tree is missing or inconsistent beyond per-frame exclusion."""


def assert_unique_counted(records):
    """Guard against counting the same iq_sha256 twice (CONTRACTS §11).

    Raises ``RealEvidenceError`` if a duplicate digest appears in the counted set;
    returns the number of unique digests.  This is the anti-double-counting guard:
    a report that counted a duplicate would fail instead of silently inflating n.
    """
    seen = {}
    for record in records:
        digest = record.get("iq_sha256")
        if not digest:
            raise RealEvidenceError("counted frame has no iq_sha256: %r" % (record.get("path"),))
        if digest in seen:
            raise RealEvidenceError(
                "duplicate iq_sha256 counted twice: %s and %s (digest %s); duplicate "
                "frames must be counted once (CONTRACTS §11)"
                % (seen[digest].get("path"), record.get("path"), digest))
        seen[digest] = record
    return len(seen)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only real BPSK transfer evaluation (host CPU numpy; NOT ARM/board).")
    parser.add_argument("--model", required=True,
                        help="CNN artefact directory (contains model.json) or model.json path")
    parser.add_argument("--baseline", required=True,
                        help="feature-baseline artefact directory (contains model.json) or model.json path")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="output directory for real_inventory.json / real_bpsk.md / real_failures.json")
    parser.add_argument("--canonical-root", default=str(CANONICAL_ROOT),
                        help="read-only canonical sessions root (campaign5 .../download)")
    parser.add_argument("--campaign4-root", default=str(CAMPAIGN4_ROOT),
                        help="read-only campaign4 download root used for duplicate mapping only")
    return parser.parse_args(argv)


def _analysis_summary(analysis):
    """Input-analysis statistics from the frame JSON (diagnostics, never labels)."""
    analysis = analysis or {}
    return OrderedDict((key, analysis.get(key)) for key in ANALYSIS_FIELDS)


def _demod_diagnostics(json_document):
    """Board demodulator diagnostics (diagnostic evidence, never a label).

    ``phase_mod_pi`` is converted to degrees; the Lead diagnostic convention uses
    ``degrees(value)`` for this field and the observed session means agree.
    ``snr_est_db_from_coherence`` is derived from carrier coherence only
    (10*log10(1/(1-coherence))) and is an estimate, not a measurement.
    """
    result = ((json_document.get("demod_result") or {}).get("result") or {})
    phase = result.get("phase_mod_pi")
    phase_deg = None
    if isinstance(phase, (int, float)) and math.isfinite(float(phase)):
        phase_deg = math.degrees(float(phase)) % 360.0
        if phase_deg > 180.0:
            phase_deg -= 360.0
    coherence = result.get("carrier_coherence")
    snr_est = None
    if isinstance(coherence, (int, float)) and 0.0 < float(coherence) < 1.0:
        snr_est = 10.0 * math.log10(1.0 / max(1e-12, 1.0 - float(coherence)))
    return OrderedDict([
        ("phase_deg", phase_deg),
        ("phase_source", "demod_result.result.phase_mod_pi"),
        ("cfo_hz", result.get("cfo_hz")),
        ("carrier_coherence", coherence),
        ("timing_quality", result.get("timing_quality")),
        ("snr_est_db_from_coherence", snr_est),
        ("ber", result.get("ber")),
        ("reference_sync", result.get("reference_sync")),
        ("carrier_sync", result.get("carrier_sync")),
        ("timing_sync", result.get("timing_sync")),
        ("algorithm_version", ((json_document.get("demod_result") or {})
                               .get("algorithm_version"))),
        ("note", "demodulator diagnostics only; never used as a label"),
    ])


def _environment_diagnostics(environment_path):
    """Read-only board environment-rule output (diagnostic only, never a label)."""
    environment_path = Path(environment_path)
    if not environment_path.is_file():
        return None
    try:
        document = _read_json(environment_path, "environment")
    except RealEvidenceError:
        return None
    environment = document.get("environment") or {}
    features = environment.get("features") or {}
    return OrderedDict([
        ("label", environment.get("label")),
        ("reason", environment.get("reason")),
        ("features", OrderedDict((key, features.get(key))
                                 for key in ENVIRONMENT_FEATURE_FIELDS)),
        ("environment_version", document.get("environment_version")),
        ("algorithm_version", environment.get("algorithm_version")),
        ("note", "board environment rule output: diagnostic evidence only, never a label"),
    ])


def _iq_diagnostics(iq):
    """Read-only input statistics for failure-mode analysis (never a label).

    These are properties of the captured IQ only: DC, complex RMS, amplitude
    distribution, a BPSK-specific CFO estimate (x^2 modulation removal), the
    fourth-order moment after CFO de-rotation, spectral peak and a rough
    peak-to-median ratio.  They are diagnostic evidence, not ground truth.
    """
    codes = np.asarray(iq, dtype=np.float64)
    if codes.shape != (C.SAMPLES, 2):
        return None
    i = codes[:, 0]
    q = codes[:, 1]
    x = i + 1j * q
    dc = complex(float(i.mean()), float(q.mean()))
    centered = x - dc
    power = float(np.mean(np.abs(centered) ** 2))
    rms = math.sqrt(power) if power > 0 else 0.0
    amplitude = np.abs(centered)
    saturated = int(np.count_nonzero((codes <= C.MIN_CODE) | (codes >= C.MAX_CODE)))
    count = codes.shape[0]
    window = np.hanning(count)
    freqs = np.fft.fftfreq(count, d=1.0 / IQ_INPUT_FS_HZ)
    # Fourth-order moment without CFO de-rotation: the demodulator CFO on these
    # captures is of order 10 Hz (see demod_diagnostics.cfo_hz), so x^4 stays coherent.
    fourth = np.mean(centered ** 4)
    c40 = float(abs(fourth) / (power * power)) if power > 0 else None
    spectrum = np.fft.fft(centered * window)
    magnitude = np.abs(spectrum)
    magnitude[0] = 0.0
    peak_index = int(np.argmax(magnitude))
    median = float(np.median(magnitude))
    peak_to_median = float(20.0 * math.log10((magnitude[peak_index] + 1e-12)
                                              / (median + 1e-12)))
    return OrderedDict([
        ("input_fs_hz", IQ_INPUT_FS_HZ),
        ("complex_rms_codes", rms),
        ("dc_i", float(dc.real)), ("dc_q", float(dc.imag)),
        ("amplitude_mean", float(amplitude.mean())),
        ("amplitude_p95", float(np.percentile(amplitude, 95))),
        ("amplitude_peak", float(amplitude.max())),
        ("saturated_count", saturated),
        ("c40_abs", c40),
        ("spectral_peak_hz", float(freqs[peak_index])),
        ("peak_to_median_db", peak_to_median),
        ("estimator_note", "input statistics only (not a label); c40 = |mean(x^4)| / "
                           "(mean|x|^2)^2 without CFO de-rotation; the board demodulator "
                           "CFO on these captures is of order 10 Hz"),
    ])


# ---------------------------------------------------------------------------
# evidence discovery / truth extraction
# ---------------------------------------------------------------------------
def _read_json(path, label):
    try:
        return C.load_json_file(path)
    except FileNotFoundError:
        raise RealEvidenceError("%s metadata is missing: %s" % (label, path)) from None
    except (OSError, ValueError) as exc:
        raise RealEvidenceError("%s metadata is unreadable (%s): %s"
                                % (label, exc, path)) from exc


def read_session_manifest(session_dir, session):
    path = Path(session_dir) / "session-manifest.json"
    manifest = _read_json(path, "session manifest")
    if manifest.get("schema") != SESSION_MANIFEST_SCHEMA:
        raise RealEvidenceError("session %s manifest schema %r is not %r"
                                % (session, manifest.get("schema"), SESSION_MANIFEST_SCHEMA))
    record = manifest.get("session") or {}
    if record.get("session_id") not in (None, session):
        raise RealEvidenceError("session %s manifest session_id %r does not match directory"
                                % (session, record.get("session_id")))
    slots = manifest.get("slots")
    if not isinstance(slots, list) or not slots:
        raise RealEvidenceError("session %s manifest has no slots" % (session,))
    if manifest.get("frame_count") not in (None, len(slots)):
        raise RealEvidenceError("session %s manifest frame_count %r != slots %d"
                                % (session, manifest.get("frame_count"), len(slots)))
    return manifest


def canonical_session_entries(session, session_dir, manifest):
    entries = []
    for slot in manifest["slots"]:
        for key in ("tag", "iq", "metadata"):
            if not slot.get(key):
                raise RealEvidenceError("session %s manifest slot misses %r" % (session, key))
        entries.append(OrderedDict([
            ("canonical", True),
            ("source", "campaign5"),
            ("session", session),
            ("tag", str(slot["tag"])),
            ("frame_id", slot.get("frame_id") or "%s-%s" % (session, slot["tag"])),
            ("position", slot.get("position")),
            ("scenario", slot.get("scenario")),
            ("iq_path", Path(session_dir) / slot["iq"]),
            ("json_path", Path(session_dir) / slot["metadata"]),
            ("environment_path", (Path(session_dir) / slot["metadata"]).with_name(
                Path(slot["metadata"]).stem + ".environment.json")),
            ("manifest_iq_sha256", slot.get("iq_sha256")),
            ("manifest_clipping_count", slot.get("clipping_count")),
        ]))
    return entries


def campaign4_entries(campaign4_root, sessions):
    entries = []
    root = Path(campaign4_root)
    if not root.is_dir():
        return entries
    for session in sessions:
        session_dir = root / session
        if not session_dir.is_dir():
            continue
        for iq_path in sorted(session_dir.glob("*.iq")):
            entries.append(OrderedDict([
                ("canonical", False),
                ("source", "campaign4"),
                ("session", session),
                ("tag", iq_path.stem),
                ("position", None),
                ("scenario", None),
                ("iq_path", iq_path),
                ("json_path", iq_path.with_suffix(".json")),
                ("manifest_iq_sha256", None),
                ("manifest_clipping_count", None),
            ]))
    return entries


def _algorithm_outputs(json_document):
    """Board algorithm labels, recorded for transparency only - never used as truth."""
    trainval = json_document.get("trainval") or {}
    return OrderedDict([
        ("trainval.label", trainval.get("label")),
        ("trainval.reason", trainval.get("reason")),
        ("model_result.label", (json_document.get("model_result") or {}).get("label")),
        ("demod_result.result.label",
         ((json_document.get("demod_result") or {}).get("result") or {}).get("label")),
        ("environment.label", json_document.get("environment_label")),
    ])


def extract_frame_truth(json_document):
    """Extract contract truth/config from one real frame JSON.

    Only ``trainval.truth`` and ``demod_result.submitted.config`` are used.  Board
    algorithm outputs are returned separately (for transparency) and never promoted
    to labels.
    """
    if not isinstance(json_document, dict):
        raise C.TruthUnavailable("frame metadata is not a JSON object")
    trainval = json_document.get("trainval")
    if not isinstance(trainval, dict):
        raise C.TruthUnavailable(
            "trainval.truth is missing; algorithm outputs (model_result/demod_result/"
            "environment) are not acceptable labels (CONTRACTS §11)")
    if "truth" not in trainval:
        raise C.TruthUnavailable(
            "trainval.truth is missing; algorithm outputs (model_result/demod_result/"
            "environment) are not acceptable labels (CONTRACTS §11)")
    truth = trainval.get("truth")
    if truth not in TRUTH_VALUES:
        raise C.TruthUnavailable("trainval.truth=%r is not one of %r"
                                 % (truth, list(TRUTH_VALUES)))
    submitted = (json_document.get("demod_result") or {}).get("submitted") or {}
    config = submitted.get("config") or {}
    config_mismatch = [key for key, expected in REQUIRED_CONFIG.items()
                       if config.get(key) != expected]
    analysis = json_document.get("analysis") or {}
    clipping_count = analysis.get("clipping_count")
    if clipping_count is None:
        clipping_count = trainval.get("clipping_count", 0)
    try:
        clipping_count = int(clipping_count or 0)
    except (TypeError, ValueError):
        clipping_count = -1
    output_clipped = bool(analysis.get("output_clipped", False))
    algorithm_outputs = _algorithm_outputs(json_document)
    actual_rf = trainval.get("actual_rf") or {}
    rx_gain = (((actual_rf.get("rx_gain_observation") or {}).get("gain_observation"))
               or None)
    return OrderedDict([
        ("truth", truth),
        ("scenario", trainval.get("scenario")),
        ("trainval_physical", trainval.get("physical")),
        ("config", config),
        ("config_expected", REQUIRED_CONFIG),
        ("config_ok", not config_mismatch),
        ("config_mismatch_fields", config_mismatch),
        ("clipping_count", clipping_count),
        ("output_clipped", output_clipped),
        ("clipped", bool(clipping_count > 0 or output_clipped)),
        ("json_iq_sha256", json_document.get("iq_sha256")),
        ("analysis", _analysis_summary(analysis)),
        ("demod_diagnostics", _demod_diagnostics(json_document)),
        ("requested_rf", trainval.get("requested_rf")),
        ("rx_gain", OrderedDict(rx_gain) if rx_gain else None),
        ("board_algorithm_labels_ignored", algorithm_outputs),
    ])


# ---------------------------------------------------------------------------
# inventory collection: dedupe + truth/config/clipping validation
# ---------------------------------------------------------------------------
def collect_inventory(canonical_root=CANONICAL_ROOT, campaign4_root=CAMPAIGN4_ROOT,
                      sessions=CANONICAL_SESSIONS):
    """Read-only inventory: canonical frames, dedupe mapping, rejected/excluded frames.

    Returns a dict with ``frames`` (canonical, incl. rejected), ``duplicates``,
    ``excluded`` (non-canonical unique frames), ``canonical_manifests`` and
    ``summary``.  No model inference is performed here.
    """
    canonical_root = Path(canonical_root)
    if not canonical_root.is_dir():
        raise RealEvidenceError("canonical evidence root is missing: %s" % canonical_root)
    manifests = OrderedDict()
    canonical = []
    for session in sessions:
        session_dir = canonical_root / session
        if not session_dir.is_dir():
            raise RealEvidenceError("canonical session directory is missing: %s" % session_dir)
        manifest = read_session_manifest(session_dir, session)
        manifests[session] = OrderedDict([
            ("path", C.relpath(session_dir / "session-manifest.json")),
            ("sha256", C.sha256_file(session_dir / "session-manifest.json")),
            ("frame_count", len(manifest["slots"])),
            ("normal_count", manifest.get("normal_count")),
            ("off_count", manifest.get("off_count")),
            ("physical", manifest.get("physical")),
            ("collected", manifest.get("collected")),
            ("fixture", manifest.get("fixture")),
            ("release_id", manifest.get("release_id")),
            ("environment_version", manifest.get("environment_version")),
            ("domain", manifest.get("domain")),
            ("provenance", manifest.get("provenance")),
        ])
        canonical.extend(canonical_session_entries(session, session_dir, manifest))

    seen = OrderedDict()          # iq_sha256 -> first entry
    duplicates = []               # (duplicate entry, first entry)
    counted = []
    canonical_rejected = []
    for entry in canonical:
        iq_path = entry["iq_path"]
        if not iq_path.is_file():
            raise RealEvidenceError("canonical IQ frame is missing: %s" % iq_path)
        raw = iq_path.read_bytes()
        if len(raw) != C.FRAME_BYTES:
            raise RealEvidenceError("canonical IQ frame %s is %d bytes, expected %d"
                                    % (iq_path, len(raw), C.FRAME_BYTES))
        entry["iq_sha256"] = C.sha256_bytes(raw)
        entry["iq_bytes"] = raw
        if entry["manifest_iq_sha256"] and entry["manifest_iq_sha256"] != entry["iq_sha256"]:
            raise RealEvidenceError(
                "canonical frame %s iq_sha256 does not match its session manifest "
                "(manifest %s, file %s); evidence integrity failure"
                % (iq_path, entry["manifest_iq_sha256"], entry["iq_sha256"]))
        digest = entry["iq_sha256"]
        if digest in seen:
            duplicates.append({"first": seen[digest], "duplicate": entry})
            entry["_duplicate_of"] = seen[digest]
            canonical_rejected.append(entry)
            continue
        seen[digest] = entry
        counted.append(entry)

    # campaign4: duplicate mapping only; unique frames are excluded (non-canonical).
    excluded = []
    campaign4_count = 0
    for entry in campaign4_entries(campaign4_root, sessions):
        campaign4_count += 1
        iq_path = entry["iq_path"]
        if not iq_path.is_file():
            continue
        raw = iq_path.read_bytes()
        if len(raw) != C.FRAME_BYTES:
            excluded.append(OrderedDict([
                ("path", C.relpath(iq_path)),
                ("iq_sha256", None),
                ("reason", "iq_size_%d_bytes_not_%d" % (len(raw), C.FRAME_BYTES)),
                ("source", "campaign4"),
            ]))
            continue
        entry["iq_sha256"] = C.sha256_bytes(raw)
        entry["iq_bytes"] = raw
        digest = entry["iq_sha256"]
        if digest in seen:
            duplicates.append({"first": seen[digest], "duplicate": entry})
            continue
        seen[digest] = entry
        metadata = None
        metadata_error = None
        try:
            document = _read_json(entry["json_path"], "campaign4 frame")
            metadata = extract_frame_truth(document)
        except Exception as exc:  # noqa: BLE001 - recorded as exclusion reason
            metadata_error = "%s: %s" % (type(exc).__name__, exc)
        record = OrderedDict([
            ("path", C.relpath(iq_path)),
            ("source", "campaign4"),
            ("session", entry["session"]),
            ("tag", entry["tag"]),
            ("iq_sha256", entry["iq_sha256"]),
            ("reason", "unique frame outside the canonical campaign5 session set; not counted "
                       "(CONTRACTS §11 canonical roots)"),
            ("metadata_error", metadata_error),
        ])
        if metadata is not None:
            record["truth"] = metadata["truth"]
            record["config"] = metadata["config"]
            record["config_ok"] = metadata["config_ok"]
            record["clipped"] = metadata["clipped"]
            record["clipping_count"] = metadata["clipping_count"]
            record["trainval_physical"] = metadata["trainval_physical"]
        excluded.append(record)

    # validate counted canonical frames; build per-frame records
    frames = []
    accepted = []
    for entry in canonical:
        raw = entry.get("iq_bytes")
        if raw is None:  # duplicate entry has no cached bytes? cache it for consistency
            entry["iq_bytes"] = entry["iq_path"].read_bytes()
            raw = entry["iq_bytes"]
        record = OrderedDict([
            ("path", C.relpath(entry["iq_path"])),
            ("session", entry["session"]),
            ("tag", entry["tag"]),
            ("frame_id", entry.get("frame_id")),
            ("position", entry["position"]),
            ("scenario", entry["scenario"]),
            ("iq_sha256", entry["iq_sha256"]),
            ("iq_bytes", len(raw)),
            ("samples", C.SAMPLES),
            ("truth", None),
            ("truth_source", "trainval.truth"),
            ("config", None),
            ("config_ok", None),
            ("clipped", None),
            ("clipping_count", None),
            ("output_clipped", None),
            ("trainval_physical", None),
            ("board_algorithm_labels_ignored", None),
            ("analysis", None),
            ("demod_diagnostics", None),
            ("environment", None),
            ("iq_diagnostics", None),
            ("requested_rf", None),
            ("rx_gain", None),
            ("status", None),
            ("rejection_reasons", []),
            ("predicted_label", None), ("score", None), ("margin", None),
            ("reason", None), ("gate_reason", None), ("scores", None), ("gate", None),
            ("baseline_predicted_label", None), ("baseline_score", None),
            ("baseline_margin", None), ("baseline_reason", None),
            ("baseline_gate_reason", None),
            ("baseline_scores", None), ("baseline_gate", None),
        ])
        if entry.get("_duplicate_of") is not None:
            record["status"] = "rejected"
            record["rejection_reasons"] = ["duplicate_iq_sha256"]
            record["duplicate_of"] = C.relpath(entry["_duplicate_of"]["iq_path"])
            frames.append(record)
            continue
        reasons = []
        document = None
        try:
            document = _read_json(entry["json_path"], "canonical frame")
            metadata = extract_frame_truth(document)
        except C.TruthUnavailable as exc:
            metadata = None
            if document is not None:
                # keep the ignored board outputs visible even for a rejected frame
                record["board_algorithm_labels_ignored"] = _algorithm_outputs(document)
            message = str(exc)
            reasons.append("truth_missing" if "missing" in message
                           else "truth_outside_canonical")
        if metadata is not None:
            record["truth"] = metadata["truth"]
            record["scenario"] = metadata["scenario"]
            record["trainval_physical"] = metadata["trainval_physical"]
            record["config"] = metadata["config"]
            record["config_ok"] = metadata["config_ok"]
            record["clipped"] = metadata["clipped"]
            record["clipping_count"] = metadata["clipping_count"]
            record["output_clipped"] = metadata["output_clipped"]
            record["board_algorithm_labels_ignored"] = metadata["board_algorithm_labels_ignored"]
            if not metadata["config_ok"]:
                reasons.append("config_mismatch:" + ",".join(metadata["config_mismatch_fields"]))
            if metadata["json_iq_sha256"] and metadata["json_iq_sha256"] != entry["iq_sha256"]:
                reasons.append("json_iq_sha256_mismatch")
            if metadata["clipped"]:
                reasons.append("clipping")
        if reasons:
            record["status"] = "rejected"
            record["rejection_reasons"] = reasons
            frames.append(record)
            continue
        record["status"] = "counted"
        # read-only input diagnostics for the failure-mode analysis (never labels)
        try:
            iq = np.frombuffer(entry["iq_bytes"], dtype="<i2").reshape(C.SAMPLES, 2)
            record["iq_diagnostics"] = _iq_diagnostics(iq)
        except Exception as exc:  # noqa: BLE001 - diagnostics are non-fatal
            record["iq_diagnostics"] = OrderedDict([("error", "%s: %s"
                                                     % (type(exc).__name__, exc))])
        if metadata is not None:
            record["analysis"] = metadata.get("analysis")
            record["demod_diagnostics"] = metadata.get("demod_diagnostics")
            record["requested_rf"] = metadata.get("requested_rf")
            record["rx_gain"] = metadata.get("rx_gain")
        if entry.get("environment_path") is not None:
            record["environment"] = _environment_diagnostics(entry["environment_path"])
        frames.append(record)
        accepted.append((record, entry))

    duplicate_table = OrderedDict()
    for pair in duplicates:
        first = pair["first"]
        duplicate = pair["duplicate"]
        digest = duplicate["iq_sha256"]
        table = duplicate_table.setdefault(digest, OrderedDict([
            ("iq_sha256", digest),
            ("canonical_path", C.relpath(first["iq_path"])),
            ("canonical_session", first["session"]),
            ("counted_copies", 1),
            ("duplicate_paths", []),
        ]))
        table["duplicate_paths"].append(OrderedDict([
            ("path", C.relpath(duplicate["iq_path"])),
            ("source", duplicate.get("source")),
            ("session", duplicate.get("session")),
            ("tag", duplicate.get("tag")),
        ]))
    assert_unique_counted([record for record, _entry in accepted])

    per_session = OrderedDict()
    for session in sessions:
        session_frames = [record for record in frames if record["session"] == session]
        per_session[session] = OrderedDict([
            ("n_canonical_frames", len(session_frames)),
            ("n_counted", sum(1 for record in session_frames if record["status"] == "counted")),
            ("n_rejected", sum(1 for record in session_frames if record["status"] == "rejected")),
            ("n_normal", sum(1 for record in session_frames
                             if record["status"] == "counted" and record["truth"] == "normal")),
            ("n_off", sum(1 for record in session_frames
                          if record["status"] == "counted" and record["truth"] == "off")),
            ("n_clipped", sum(1 for record in session_frames if record["clipped"])),
        ])

    summary = OrderedDict([
        ("canonical_root", C.relpath(canonical_root)),
        ("campaign4_root", C.relpath(campaign4_root)),
        ("sessions", list(sessions)),
        ("n_canonical_frames", len(frames)),
        ("n_counted_frames", len(accepted)),
        ("n_rejected_frames", len(frames) - len(accepted)),
        ("n_duplicates_mapped", len(duplicates)),
        ("n_duplicate_unique_hashes", len(duplicate_table)),
        ("n_campaign4_entries_scanned", campaign4_count),
        ("n_excluded_non_canonical", len(excluded)),
        ("per_session", per_session),
    ])

    return OrderedDict([
        ("manifests", manifests),
        ("frames", frames),
        ("accepted", accepted),
        ("duplicates", list(duplicate_table.values())),
        ("excluded", excluded),
        ("summary", summary),
        ("inventory_sha256_scope", "sha256 over every canonical .iq file; duplicates counted once"),
    ])


# ---------------------------------------------------------------------------
# inference + per-session metrics
# ---------------------------------------------------------------------------
def _infer(entries, predictor, prefix):
    for record, entry in entries:
        iq = C.read_iq_frame(entry["iq_path"])
        prediction = predictor.predict_iq(iq)
        if prefix == "baseline_":
            record["baseline_predicted_label"] = prediction["label"]
            record["baseline_score"] = prediction["score"]
            record["baseline_margin"] = prediction["margin"]
            record["baseline_reason"] = prediction["reason"]
            record["baseline_gate_reason"] = prediction["reason"]
            record["baseline_scores"] = prediction["scores"]
            record["baseline_gate"] = prediction["gate"]
        else:
            record["predicted_label"] = prediction["label"]
            record["score"] = prediction["score"]
            record["margin"] = prediction["margin"]
            record["reason"] = prediction["reason"]
            record["gate_reason"] = prediction["reason"]
            record["scores"] = prediction["scores"]
            record["gate"] = prediction["gate"]


def _frame_prediction(record, prefix=""):
    label = record.get(prefix + "predicted_label")
    return OrderedDict([
        ("label", label),
        ("reason", record.get(prefix + "reason")),
        ("score", record.get(prefix + "score")),
        ("margin", record.get(prefix + "margin")),
        ("scores", record.get(prefix + "scores")),
        ("gate", record.get(prefix + "gate")),
    ])


def _is_rejected(prediction):
    return C.prediction_is_rejected(prediction)


def _metric_block(records, prefix=""):
    """Metrics over counted real frames for one predictor."""
    normals = [record for record in records if record["truth"] == "normal"]
    offs = [record for record in records if record["truth"] == "off"]
    normal_predictions = [_frame_prediction(record, prefix) for record in normals]
    off_predictions = [_frame_prediction(record, prefix) for record in offs]
    recognized = sum(1 for prediction in normal_predictions
                     if prediction["label"] == "BPSK")
    rejected_normals = sum(1 for prediction in normal_predictions
                           if _is_rejected(prediction))
    destinations = Counter()
    failures = []
    for record, prediction in zip(normals, normal_predictions):
        label = prediction["label"]
        if label == "BPSK":
            continue
        if _is_rejected(prediction):
            destinations["UNKNOWN"] += 1
        else:
            destinations[str(label)] += 1
        failures.append(OrderedDict([
            ("path", record["path"]),
            ("session", record["session"]),
            ("tag", record["tag"]),
            ("frame_id", record.get("frame_id")),
            ("truth", record["truth"]),
            ("config", record.get("config")),
            ("predicted_label", label),
            ("score", prediction["score"]),
            ("margin", prediction["margin"]),
            ("scores", prediction["scores"]),
            ("reason", prediction["reason"]),
            ("gate", prediction["gate"]),
            ("kind", "rejected_unknown" if _is_rejected(prediction) else "misclassified"),
            ("phase_deg", (record.get("demod_diagnostics") or {}).get("phase_deg")),
            ("complex_rms", (record.get("analysis") or {}).get("complex_rms")),
            ("env_label", (record.get("environment") or {}).get("label")),
            ("env_reason", (record.get("environment") or {}).get("reason")),
            ("iq_diagnostics", record.get("iq_diagnostics")),
            ("analysis", record.get("analysis")),
            ("demod_diagnostics", record.get("demod_diagnostics")),
            ("environment", record.get("environment")),
            ("requested_rf", record.get("requested_rf")),
            ("board_algorithm_labels_ignored",
             record.get("board_algorithm_labels_ignored")),
        ]))
    off_rejected = sum(1 for prediction in off_predictions if _is_rejected(prediction))
    off_accepted = Counter(str(prediction["label"]) for prediction in off_predictions
                           if not _is_rejected(prediction))
    off_reasons = Counter(str(prediction["reason"] or "unknown_reason")
                          for prediction in off_predictions if _is_rejected(prediction))
    scores = [prediction["score"] for prediction in normal_predictions
              if prediction["score"] is not None]
    margins = [prediction["margin"] for prediction in normal_predictions
               if prediction["margin"] is not None]
    return OrderedDict([
        ("n_frames", len(records)),
        ("n_normal", len(normals)),
        ("n_off", len(offs)),
        ("bpsk_recognized", recognized),
        ("bpsk_recognition_rate", (recognized / len(normals)) if normals else None),
        ("rejected_normal", rejected_normals),
        ("normal_rejection_rate", (rejected_normals / len(normals)) if normals else None),
        ("misclassified_destinations", OrderedDict(sorted(destinations.items()))),
        ("off_rejected", off_rejected),
        ("off_rejection_rate", (off_rejected / len(offs)) if offs else None),
        ("off_false_accept_labels", OrderedDict(sorted(off_accepted.items()))),
        ("off_reject_reasons", OrderedDict(sorted(off_reasons.items()))),
        ("score_stats", OrderedDict([
            ("n", len(scores)),
            ("min", min(scores) if scores else None),
            ("mean", (sum(scores) / len(scores)) if scores else None),
            ("max", max(scores) if scores else None),
            ("margin_mean", (sum(margins) / len(margins)) if margins else None),
        ])),
        ("failure_count", len(failures)),
        ("failures", failures),
    ])


def _session_metrics(records, sessions, prefix=""):
    blocks = OrderedDict()
    for session in sessions:
        session_records = [record for record in records if record["session"] == session]
        blocks[session] = _metric_block(session_records, prefix)
    return blocks


def _predictor_metrics(records, sessions, predictor_name, prefix=""):
    sessions_block = _session_metrics(records, sessions, prefix)
    overall = _metric_block(records, prefix)
    return OrderedDict([
        ("predictor", predictor_name),
        ("gate", "quality gate applied" if prefix == "" else
                 "no rejection gate in CONTRACTS §9 (feature baseline)"),
        ("sessions", sessions_block),
        (INDEPENDENT_SESSION + "_independent", sessions_block.get(INDEPENDENT_SESSION)),
        ("overall", overall),
        ("definition", "BPSK recognition rate = counted normal-truth frames predicted BPSK / "
                       "counted normal-truth frames. Normal rejection rate counts UNKNOWN. "
                       "OFF rejection rate counts UNKNOWN on OFF-truth frames. No four-class "
                       "real overall accuracy is computed."),
    ])


def _aggregate_diagnostics(values):
    values = [float(value) for value in values if value is not None]
    if not values:
        return None
    return OrderedDict([
        ("n", len(values)), ("mean", sum(values) / len(values)),
        ("min", min(values)), ("max", max(values)),
    ])


def _session_diagnostics(records):
    """Read-only per-session input diagnostics (never labels)."""
    diagnostics = OrderedDict()
    for session in CANONICAL_SESSIONS:
        normals = [record for record in records
                   if record["session"] == session and record["truth"] == "normal"
                   and record["status"] == "counted"]
        iq = OrderedDict()
        for key in ("complex_rms_codes", "c40_abs", "spectral_peak_hz",
                    "peak_to_median_db", "amplitude_mean", "saturated_count"):
            iq[key] = _aggregate_diagnostics(
                (record.get("iq_diagnostics") or {}).get(key) for record in normals)
        board = OrderedDict()
        for key in ("complex_rms", "peak_hz", "occupied_bandwidth_hz"):
            board[key] = _aggregate_diagnostics(
                (record.get("analysis") or {}).get(key) for record in normals)
        demod = OrderedDict()
        for key in ("phase_deg", "cfo_hz", "carrier_coherence",
                    "snr_est_db_from_coherence"):
            demod[key] = _aggregate_diagnostics(
                (record.get("demod_diagnostics") or {}).get(key) for record in normals)
        first = normals[0] if normals else None
        board_labels = Counter(
            str(((record.get("board_algorithm_labels_ignored") or {})
                 .get("trainval.label"))) for record in normals)
        env_labels = Counter(
            str(((record.get("environment") or {}).get("label"))) for record in normals)
        env_reasons = Counter(
            str(((record.get("environment") or {}).get("reason"))) for record in normals)
        diagnostics[session] = OrderedDict([
            ("n_normal", len(normals)),
            ("iq_diagnostics", iq),
            ("board_analysis_diagnostics", board),
            ("demod_diagnostics", demod),
            ("environment_rule_labels_ignored",
             OrderedDict(sorted(env_labels.items()))),
            ("environment_rule_reasons_ignored",
             OrderedDict(sorted(env_reasons.items()))),
            ("requested_rf", (first or {}).get("requested_rf")),
            ("rx_gain_observation", (first or {}).get("rx_gain")),
            ("board_reference_labels_ignored",
             OrderedDict(sorted(board_labels.items()))),
            ("note", "Input/demod/environment diagnostics only. The board algorithm labels "
                     "and environment-rule labels are recorded as ignored reference "
                     "diagnostics, never used as truth."),
        ])
    return diagnostics


def _comparison(cnn_metrics, baseline_metrics):
    cnn_overall = cnn_metrics["overall"]
    baseline_overall = baseline_metrics["overall"]
    return OrderedDict([
        ("valid_independent_session", INDEPENDENT_SESSION),
        ("overall_bpsk_recognition_rate", OrderedDict([
            ("cnn", cnn_overall["bpsk_recognition_rate"]),
            ("baseline", baseline_overall["bpsk_recognition_rate"]),
            ("delta_cnn_minus_baseline",
             (cnn_overall["bpsk_recognition_rate"] - baseline_overall["bpsk_recognition_rate"])
             if (cnn_overall["bpsk_recognition_rate"] is not None
                 and baseline_overall["bpsk_recognition_rate"] is not None) else None),
        ])),
        ("overall_normal_rejection_rate", OrderedDict([
            ("cnn", cnn_overall["normal_rejection_rate"]),
            ("baseline", baseline_overall["normal_rejection_rate"]),
        ])),
        ("note", "Feature baseline accepts every frame (no gate); its 'rejection' is only a "
                 "label outside the four classes, which its contract does not produce."),
    ])


def run_transfer(model_predictor, baseline_predictor, out_dir,
                 canonical_root=CANONICAL_ROOT, campaign4_root=CAMPAIGN4_ROOT):
    """Collect inventory, run both predictors, write the three outputs."""
    out_dir = Path(out_dir)
    inventory = collect_inventory(canonical_root, campaign4_root)
    accepted = inventory["accepted"]
    _infer(accepted, model_predictor, prefix="")
    _infer(accepted, baseline_predictor, prefix="baseline_")
    counted_records = [record for record in inventory["frames"]
                       if record["status"] == "counted"]
    sessions = tuple(CANONICAL_SESSIONS)
    cnn_metrics = _predictor_metrics(counted_records, sessions, "cnn", prefix="")
    baseline_metrics = _predictor_metrics(counted_records, sessions, "baseline",
                                          prefix="baseline_")

    off_false_accepts = OrderedDict()
    for prefix, name in (("", "cnn"), ("baseline_", "baseline")):
        records = []
        for record in counted_records:
            if record["truth"] != "off":
                continue
            prediction = _frame_prediction(record, prefix)
            if _is_rejected(prediction):
                continue
            records.append(OrderedDict([
                ("path", record["path"]),
                ("session", record["session"]),
                ("tag", record["tag"]),
                ("truth", record["truth"]),
                ("predicted_label", prediction["label"]),
                ("score", prediction["score"]),
                ("margin", prediction["margin"]),
                ("reason", prediction["reason"]),
            ]))
        off_false_accepts[name] = records

    summary = inventory["summary"]
    frames_document = inventory["frames"]
    document = OrderedDict([
        ("schema", C.EVAL_SCHEMA_REAL),
        ("generated_utc", C.utc_now()),
        ("host", C.host_environment()),
        ("model", model_predictor.info),
        ("baseline", baseline_predictor.info),
        ("scope", OrderedDict([
            ("evidence_root", C.relpath(EVIDENCE_CAMPAIGNS)),
            ("canonical_root", C.relpath(canonical_root)),
            ("campaign4_root", C.relpath(campaign4_root)),
            ("canonical_sessions", list(sessions)),
            ("independent_session", INDEPENDENT_SESSION),
            ("read_only", True),
            ("frame_bytes", C.FRAME_BYTES),
            ("samples", C.SAMPLES),
            ("truth_source", "trainval.truth (normal/off)"),
            ("config_source", "demod_result.submitted.config (BPSK/3000000/RECT)"),
            ("allowed_truth_values", list(TRUTH_VALUES)),
            ("required_config", REQUIRED_CONFIG),
            ("dedupe", "iq_sha256; first canonical path wins; campaign4 duplicates mapped only"),
             ("clipping_exclusion", "analysis.clipping_count>0 or analysis.output_clipped=true"),
        ])),
        ("canonical_manifests", inventory["manifests"]),
        ("summary", summary),
        ("frames", frames_document),
        ("duplicates", inventory["duplicates"]),
        ("excluded_non_canonical", inventory["excluded"]),
        ("rejected_frames", [OrderedDict([
            ("path", record["path"]), ("session", record["session"]),
            ("tag", record["tag"]), ("iq_sha256", record["iq_sha256"]),
            ("clipping_count", record["clipping_count"]),
            ("output_clipped", record["output_clipped"]),
            ("rejection_reasons", record["rejection_reasons"]),
            ("duplicate_of", record.get("duplicate_of")),
        ]) for record in frames_document if record["status"] != "counted"]),
        ("sessions", OrderedDict([
            ("cnn", cnn_metrics),
            ("baseline", baseline_metrics),
        ])),
        ("off_false_accepts", off_false_accepts),
        ("session_diagnostics", _session_diagnostics(frames_document)),
        ("lead_diagnostics", LEAD_DIAGNOSTICS),
        ("comparison", _comparison(cnn_metrics, baseline_metrics)),
        ("statements", STATEMENTS),
        ("not_run", NOT_RUN),
        ("limitations", LIMITATIONS),
    ])

    failures_document = OrderedDict([
        ("schema", C.EVAL_SCHEMA_REAL_FAILURES),
        ("generated_utc", C.utc_now()),
        ("host", C.host_environment()),
        ("definition",
         "CNN failure = (a) a counted normal-truth BPSK frame whose predicted label is not "
         "BPSK (misclassified or rejected UNKNOWN), or (b) a counted OFF-truth frame whose "
         "predicted label is a known class (false accept). The feature baseline has no gate, "
         "so its OFF behaviour is descriptive only."),
        ("sessions", list(sessions)),
        ("independent_session", INDEPENDENT_SESSION),
        ("cnn", OrderedDict([
            ("n_frames_eligible", cnn_metrics["overall"]["n_frames"]),
            ("failure_count", cnn_metrics["overall"]["failure_count"]),
            ("failures", cnn_metrics["overall"]["failures"]),
        ])),
        ("baseline", OrderedDict([
            ("n_frames_eligible", baseline_metrics["overall"]["n_frames"]),
            ("failure_count", baseline_metrics["overall"]["failure_count"]),
            ("failures", baseline_metrics["overall"]["failures"]),
        ])),
        ("off_false_accepts", off_false_accepts),
        ("session_diagnostics", _session_diagnostics(inventory["frames"])),
        ("lead_diagnostics", LEAD_DIAGNOSTICS),
        ("not_run", NOT_RUN),
        ("limitations", LIMITATIONS),
    ])

    out_dir.mkdir(parents=True, exist_ok=True)
    C.write_json(out_dir / "real_inventory.json", document)
    C.write_text(out_dir / "real_bpsk.md", render_real_md(document))
    C.write_json(out_dir / "real_failures.json", failures_document)
    return document, failures_document


# ---------------------------------------------------------------------------
# markdown renderer
# ---------------------------------------------------------------------------
def _session_rows(metrics, sessions=None, prefix_name=""):
    rows = []
    for session in (sessions if sessions is not None else CANONICAL_SESSIONS):
        block = metrics["sessions"][session]
        destinations = ", ".join("%s:%d" % pair
                                 for pair in block["misclassified_destinations"].items()) or "-"
        reasons = ", ".join("%s:%d" % pair
                            for pair in block["off_reject_reasons"].items()) or "-"
        rows.append([
            session + (" (independent)" if session == INDEPENDENT_SESSION else ""),
            block["n_normal"], block["n_off"],
            "%d/%d" % (block["bpsk_recognized"], block["n_normal"]),
            C.fmt(block["bpsk_recognition_rate"]),
            C.fmt(block["normal_rejection_rate"]), destinations,
            "%d/%d" % (block["off_rejected"], block["n_off"]),
            reasons,
        ])
    return rows


def _summary_rows(cnn_metrics, baseline_metrics):
    rows = []
    for session in CANONICAL_SESSIONS:
        cnn = cnn_metrics["sessions"][session]
        baseline = baseline_metrics["sessions"][session]
        rows.append([
            session + (" (independent)" if session == INDEPENDENT_SESSION else ""),
            cnn["n_normal"],
            C.fmt(cnn["bpsk_recognition_rate"]), C.fmt(baseline["bpsk_recognition_rate"]),
            C.fmt(cnn["normal_rejection_rate"]),
            "%d/%d" % (cnn["off_rejected"], cnn["n_off"]),
            "%d/%d" % (baseline["off_rejected"], baseline["n_off"]),
        ])
    cnn = cnn_metrics["overall"]
    baseline = baseline_metrics["overall"]
    rows.append(["ALL sessions", cnn["n_normal"],
                 C.fmt(cnn["bpsk_recognition_rate"]),
                 C.fmt(baseline["bpsk_recognition_rate"]),
                 C.fmt(cnn["normal_rejection_rate"]),
                 "%d/%d" % (cnn["off_rejected"], cnn["n_off"]),
                 "%d/%d" % (baseline["off_rejected"], baseline["n_off"])])
    return rows


def _failure_rows(failures):
    return [[item["session"], item["tag"], item["truth"], item["predicted_label"],
             C.fmt(item["score"]), C.fmt(item["margin"]), item["reason"] or "-",
             item["kind"]] for item in failures]


def _scores_text(scores):
    if not scores:
        return "-"
    return "/".join("%.3f" % float(value) for value in scores)


def _failure_mode_lines(document):
    """Failure-mode analysis section for the report (diagnostics only, never labels)."""
    frames = document["frames"]
    diagnostics = document.get("session_diagnostics") or {}
    model_thresholds = (document.get("model") or {}).get("thresholds") or {}
    train02 = [record for record in frames
               if record["session"] == "train-02" and record["truth"] == "normal"
               and record["status"] == "counted"]
    lines = ["## Failure mode analysis - train-02 (CNN)", ""]
    if train02:
        predicted = Counter(str(record["predicted_label"]) for record in train02)
        scores = [record["score"] for record in train02 if record["score"] is not None]
        margins = [record["margin"] for record in train02 if record["margin"] is not None]
        lines.append(
            "%d counted train-02 normal-truth frames; predicted labels: %s. "
            "score %.3f-%.3f (mean %.3f), margin %.3f-%.3f (mean %.3f); CNN "
            "max_softmax threshold %s. All frames are ACCEPTED (none rejected): this is a "
            "systematic session-level misclassification, not a quality-gate rejection."
            % (len(train02),
               ", ".join("%s:%d" % pair for pair in sorted(predicted.items())),
               min(scores), max(scores), sum(scores) / len(scores),
               min(margins), max(margins), sum(margins) / len(margins),
               C.fmt(model_thresholds.get("max_softmax"))))
        lines.append("")
        rows = []
        for record in train02:
            environment = record.get("environment") or {}
            lines_env = environment.get("features") or {}
            rows.append([
                record.get("frame_id"), record.get("predicted_label"),
                _scores_text(record.get("scores")), C.fmt(record.get("score")),
                C.fmt(record.get("margin")),
                C.fmt((record.get("demod_diagnostics") or {}).get("phase_deg")),
                C.fmt((record.get("analysis") or {}).get("complex_rms")),
                environment.get("label"), environment.get("reason"),
            ])
        lines.append(C.md_table(["frame_id", "pred", "scores (4)", "score", "margin",
                                 "phase_deg", "complex_rms", "env_label", "env_reason"],
                                rows))
        lines.append("")
    else:
        lines.append("No counted train-02 normal-truth frames in this run.")
        lines.append("")
    lines += ["Session input diagnostics (read-only statistics, never labels):", ""]
    comparison_rows = []
    for session in CANONICAL_SESSIONS:
        block = diagnostics.get(session) or {}
        iq = block.get("iq_diagnostics") or {}
        demod = block.get("demod_diagnostics") or {}
        board = block.get("board_analysis_diagnostics") or {}

        def _mean(container, key):
            record = container.get(key)
            return record.get("mean") if isinstance(record, dict) else None

        env_labels = block.get("environment_rule_labels_ignored") or {}
        comparison_rows.append([
            session, block.get("n_normal"),
            C.fmt(_mean(board, "complex_rms")),
            C.fmt(_mean(demod, "phase_deg")),
            C.fmt(_mean(demod, "snr_est_db_from_coherence")),
            C.fmt(_mean(demod, "cfo_hz")),
            C.fmt(_mean(iq, "c40_abs")),
            C.fmt(_mean(iq, "peak_to_median_db")),
            ", ".join("%s:%d" % pair for pair in sorted(env_labels.items())) or "-",
        ])
    lines.append(C.md_table(["session", "n", "complex_rms (analysis)", "phase_deg",
                             "SNR est (coherence, dB)", "cfo_hz", "c40_abs",
                             "peak/median (dB)", "environment-rule labels (ignored)"],
                            comparison_rows))
    lines.append("")
    lead = document.get("lead_diagnostics") or {}
    lines += ["### Lead diagnostic (host numpy, not ARM)", ""]
    lines.append(str(lead.get("attribution", "")))
    lines.append("")
    distribution = lead.get("prediction_distribution") or {}
    if distribution:
        lines.append("Predictions: " + "; ".join("%s: %s" % pair
                                                 for pair in distribution.items()))
        lines.append("")
    physical = lead.get("session_physical_differences_read_only") or []
    if physical:
        lines.append("Read-only session differences (Lead):")
        for item in physical:
            lines.append("- %s: complex_rms~%s, phase~%s deg, SNR~%s dB, environment rule: %s"
                         % (item.get("session"), item.get("complex_rms"),
                            item.get("phase_deg"), item.get("snr_est_db"),
                            item.get("environment_rule")))
        lines.append("")
    controlled = lead.get("controlled_diagnostics") or []
    if controlled:
        lines.append("Controlled diagnostics (not tuning):")
        lines += ["- %s" % item for item in controlled]
        lines.append("")
    if lead.get("interpretation"):
        lines.append("Interpretation: %s" % lead.get("interpretation"))
        lines.append("")
    if lead.get("truth_unchanged"):
        lines.append("Truth unchanged: %s" % lead.get("truth_unchanged"))
        lines.append("")
    lines.append("**Root cause: NOT_ESTABLISHED.** The session comparison and controlled probes are "
                 "correlational; only 10 train-02 frames exist. The CNN accepts these QPSK outputs "
                 "with high confidence, so the quality gate does not catch this failure mode; "
                 "rejecting unknown/distorted input is future work.")
    lines.append("")
    return lines


def render_real_md(document):
    summary = document["summary"]
    cnn = document["sessions"]["cnn"]
    baseline = document["sessions"]["baseline"]
    duplicates = document["duplicates"]
    excluded = document["excluded_non_canonical"]
    lines = [
        "# Real BPSK transfer evaluation (read-only physical evidence)",
        "",
        "Generated: %s" % document["generated_utc"],
        "",
        "> **Scope warning.** This report evaluates the single real BPSK configuration "
        "(3 MSym/s RECT, 60 MSPS) with zero-shot transfer of a model trained on the "
        "**simulated** dataset. It does **NOT** compute a four-class real accuracy, and its "
        "numbers are **host CPU numpy inference - NOT ARM and NOT board numbers**.",
        "",
        "## Contract statements",
        "",
    ]
    lines += ["- **%s**: %s" % (key, value) for key, value in document["statements"].items()]
    lines += [
        "",
        "## Inventory / dedupe",
        "",
        "- canonical root: `%s`" % document["scope"]["canonical_root"],
        "- canonical sessions: %s" % ", ".join(document["scope"]["canonical_sessions"]),
        "- canonical frames: %d (counted %d, rejected %d)"
        % (summary["n_canonical_frames"], summary["n_counted_frames"],
           summary["n_rejected_frames"]),
        "- duplicates mapped by iq_sha256: %d frame(s) over %d unique hash(es); each counted once"
        % (summary["n_duplicates_mapped"], summary["n_duplicate_unique_hashes"]),
        "- non-canonical unique frames excluded: %d" % summary["n_excluded_non_canonical"],
        "- clipped frames excluded and listed separately: %d"
        % sum(1 for record in document["rejected_frames"]
              if "clipping" in record["rejection_reasons"]),
        "",
        "Per-session counts:",
        "",
        C.md_table(
            ["session", "canonical frames", "counted", "rejected", "normal", "off", "clipped"],
            [[session, record["n_canonical_frames"], record["n_counted"],
              record["n_rejected"], record["n_normal"], record["n_off"], record["n_clipped"]]
             for session, record in summary["per_session"].items()]),
        "",
        "## val-01 - independent untouched real session (primary result)",
        "",
        "val-01 participated in no threshold selection or tuning; it is the first reported "
        "transfer session.",
        "",
        C.md_table(["session", "normal frames", "off frames", "BPSK recognized", "recognition rate",
                    "normal rejection rate", "misclassified/rejected destinations",
                    "OFF rejected", "OFF rejection reasons"],
                   _session_rows(cnn, sessions=(INDEPENDENT_SESSION,))),
        "",
        "val-01 CNN failures: %d. val-01 baseline failures: %d."
        % (cnn["sessions"][INDEPENDENT_SESSION]["failure_count"],
           baseline["sessions"][INDEPENDENT_SESSION]["failure_count"]),
        "",
        "## train-01 and train-02",
        "",
        "These sessions are reported separately from val-01 (they are not the independent "
        "session, even though the model never trained on any real frame).",
        "",
        "### CNN",
        "",
        C.md_table(["session", "normal", "off", "BPSK recognized", "recognition rate",
                    "normal rejection rate", "destinations", "OFF rejected", "OFF reasons"],
                   _session_rows(cnn, sessions=("train-01", "train-02"))),
        "",
        "### Feature baseline",
        "",
        C.md_table(["session", "normal", "off", "BPSK recognized", "recognition rate",
                    "normal rejection rate", "destinations", "OFF rejected", "OFF reasons"],
                   _session_rows(baseline, sessions=("train-01", "train-02"))),
        "",
        "## OFF gate behaviour (all sessions)",
        "",
        C.md_table(["session", "normal", "off", "BPSK recognized", "recognition rate",
                    "normal rejection rate", "OFF rejected", "OFF rejection reasons"],
                   _session_rows(cnn)),
        "",
        "OFF false accepts by CNN: %s."
        % (", ".join(record["path"] for record in document["off_false_accepts"]["cnn"]) or "none"),
        "OFF false accepts by baseline (descriptive; the baseline has no gate): %s."
        % (", ".join(record["path"] for record in document["off_false_accepts"]["baseline"])
           or "none"),
        "",
        "## CNN vs feature baseline summary",
        "",
        C.md_table(["session", "normal frames", "CNN BPSK recognition", "baseline BPSK recognition",
                    "CNN normal rejection", "CNN OFF rejected", "baseline OFF rejected"],
                   _summary_rows(cnn, baseline)),
        "",
        "## Failure cases (CNN)",
        "",
    ]
    failures = cnn["overall"]["failures"]
    if failures:
        lines.append(C.md_table(["session", "tag", "truth", "predicted", "score", "margin",
                                 "reason", "kind"], _failure_rows(failures)))
    else:
        lines.append("No CNN failure on the counted real frames.")
    lines += [
        "",
        "Baseline failures (descriptive): %d." % baseline["overall"]["failure_count"],
        "",
    ]
    lines += _failure_mode_lines(document)
    lines += [
        "## Duplicate mapping (campaign4 -> campaign5)",
        "",
    ]
    if duplicates:
        lines.append(C.md_table(
            ["iq_sha256", "counted at canonical path", "duplicate copies (paths)"],
            [[record["iq_sha256"], record["canonical_path"],
              ", ".join(item["path"] for item in record["duplicate_paths"])]
             for record in duplicates]))
    else:
        lines.append("No duplicate frames were found.")
    lines += [
        "",
        "Duplicate copies are NOT counted a second time; the first canonical path is counted.",
        "",
        "Non-canonical unique frames excluded:",
        "",
    ]
    if excluded:
        lines.append(C.md_table(
            ["path", "session", "tag", "reason", "truth (read-only)", "clipped"],
            [[record["path"], record["session"], record["tag"], record["reason"],
              record.get("truth", "-"), record.get("clipped", "-")] for record in excluded]))
    else:
        lines.append("None.")
    lines += [
        "",
        "## NOT_RUN",
        "",
    ]
    lines += ["- %s" % item for item in document["not_run"]]
    lines += ["", "## Limitations", ""]
    lines += ["- %s" % item for item in document["limitations"]]
    return "\n".join(lines) + "\n"


def _print_summary(document):
    summary = document["summary"]
    cnn = document["sessions"]["cnn"]
    baseline = document["sessions"]["baseline"]
    print("canonical frames=%d counted=%d rejected=%d duplicates_mapped=%d excluded=%d"
          % (summary["n_canonical_frames"], summary["n_counted_frames"],
             summary["n_rejected_frames"], summary["n_duplicates_mapped"],
             summary["n_excluded_non_canonical"]))
    for session in CANONICAL_SESSIONS:
        block = cnn["sessions"][session]
        print("%s CNN BPSK=%d/%d recognition=%.4f normal-reject=%.4f off-reject=%d/%d"
              % (session, block["bpsk_recognized"], block["n_normal"],
                 block["bpsk_recognition_rate"] or 0.0,
                 block["normal_rejection_rate"] or 0.0,
                 block["off_rejected"], block["n_off"]))
    print("overall CNN    BPSK=%d/%d recognition=%.4f"
          % (cnn["overall"]["bpsk_recognized"], cnn["overall"]["n_normal"],
             cnn["overall"]["bpsk_recognition_rate"] or 0.0))
    print("overall baseline BPSK=%d/%d recognition=%.4f"
          % (baseline["overall"]["bpsk_recognized"], baseline["overall"]["n_normal"],
             baseline["overall"]["bpsk_recognition_rate"] or 0.0))
    print("val-01 (independent) CNN recognition=%s"
          % C.fmt(cnn["sessions"][INDEPENDENT_SESSION]["bpsk_recognition_rate"]))
    print("HOST CPU NUMPY INFERENCE - NOT ARM / NOT BOARD NUMBERS")


def main(argv=None):
    args = parse_args(argv)
    try:
        cnn_predictor = C.load_cnn_predictor(args.model)
        baseline_predictor = C.load_baseline_predictor(args.baseline)
    except C.ArtifactMissing as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    try:
        document, _ = run_transfer(cnn_predictor, baseline_predictor, Path(args.out),
                                   canonical_root=Path(args.canonical_root),
                                   campaign4_root=Path(args.campaign4_root))
    except (C.EvaluationError, C.DatasetError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    _print_summary(document)
    print("wrote: %s" % (Path(args.out) / "real_inventory.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
