"""Install-path contract for the additive modrec4 software contract.

The 2026-10-22 real trial failed because the base installer's contract branches
stopped at cnn-bpsk-headroom-v1 and therefore never copied rf_bpsk_pipeline.py
or the modrec4 stack; --require-software then refused the install.  This test
pins the complete branch, not a single file.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
DEFAULT = HERE.parent / "install.sh"
MODREC4_MODULES = (
    "rf_modrec4_adapter.py", "rf_modrec4_input_domain.py", "rf_modrec4_fullpath.py",
    "rf_modrec4_baseline.py", "rf_modrec4_features.py", "rf_modrec4_gate.py",
    "rf_modrec4_iq.py", "rf_modrec4_numpy_runtime.py", "rf_modrec4_predictor.py",
    "rf_modrec4_preprocess.py", "rf_modrec4_spectral.py",
)
MODREC4_MODELS = (
    "modrec4_cnn_model.json", "modrec4_baseline_model.json", "modrec4_rule_config.json",
)
QPSK_MODULES = ("rf_modem_result.py", "rf_qpsk_receiver.py", "rf_qpsk_pipeline.py")
RRC_MODULES = ("rf_rrc_receiver.py", "rf_rrc_pipeline.py")
NEW_CONTRACT = "cnn-bpsk-modrec4-qpsk-v1"
RRC_CONTRACT = "cnn-bpsk-modrec4-qpsk-rrc-v1"


def _text():
    path = Path(os.environ.get("INSTALL_SH_PATH", DEFAULT))
    return path.read_text(encoding="utf-8")


def test_modrec4_contract_reaches_every_inherited_branch_and_full_stack():
    text = _text()
    assert text.count('"$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1') >= 5, (
        "modrec4 must be present in the CNN, BPSK, teaching, environment and its own branches")
    for name in MODREC4_MODULES + MODREC4_MODELS:
        assert name in text, "modrec4 install branch omits %s" % name
    # The new contract also inherits these branches; a fix that only adds
    # rf_bpsk_pipeline.py is exactly the failed 2026-10-22 shape.
    for name in ("rf_bpsk_pipeline.py", "rf_bpsk_receiver.py", "rf_cnn_runtime.py",
                 "rf_teaching_profile.py", "rf_environment.py",
                 "rf_environment_baseline.json"):
        assert name in text, "modrec4 install path omits inherited %s" % name
    # The complete branch must be installed before the staging sync/verification;
    # otherwise --require-software could inspect a tree without the new files.
    branch = text.index("# The additive modrec4 contract")
    assert branch < text.index('sync\npython3 -B "$STAGE/merged_release.py" --require-software')
    # 2026-10-22 real install: the three model JSONs exist at the release root
    # ($OVERLAY_SRC), not in the pynq_overlay/sw source tree ($SRC). The six-class
    # artifact loop already used $OVERLAY_SRC and must stay unchanged.
    branch_text = text[branch:text.index('# The research plan/controller', branch)]
    assert 'install -m 644 "$OVERLAY_SRC/$artifact" "$STAGE/$artifact"' in branch_text
    assert 'install -m 644 "$SRC/$artifact" "$STAGE/$artifact"' not in branch_text


def test_every_modrec4_manifest_software_name_is_present_in_install_path():
    import merged_release as R
    text = _text()
    names = sorted(R.software_sources("cnn-bpsk-modrec4-v1"))
    missing = [name for name in names if name not in text]
    assert missing == [], "install.sh has no copy path for: %s" % missing


def test_legacy_contracts_keep_their_branch_conditions():
    text = _text()
    for contract in ("legacy-v1", "cnn-v1", "cnn-bpsk-v1", "cnn-bpsk-teaching-v1",
                     "cnn-bpsk-environment-v1", "cnn-bpsk-headroom-v1"):
        if contract == "legacy-v1":
            continue
        assert ('SOFTWARE_CONTRACT" = %s' % contract) in text, (
            "existing contract branch disappeared: %s" % contract)



def test_new_qpsk_contract_is_additive_58_and_install_copies_all_members():
    import merged_release as R
    text = _text()
    old = R.software_sources("cnn-bpsk-modrec4-v1")
    new = R.software_sources(NEW_CONTRACT)
    assert len(old) == 55 and len(new) == 58
    assert set(old) < set(new)
    assert set(new) - set(old) == set(QPSK_MODULES)
    assert text.count('"$SOFTWARE_CONTRACT" = %s' % NEW_CONTRACT) >= 5
    branch = text.index('if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ]')
    branch_text = text[branch:text.index('# The research plan/controller', branch)]
    for name in QPSK_MODULES:
        assert name in branch_text
    assert 'for module in rf_modem_result.py rf_qpsk_receiver.py rf_qpsk_pipeline.py; do' in branch_text
    assert 'install -m 644 "$SRC/$module" "$STAGE/$module"' in branch_text
    for name in sorted(new):
        assert name in text, "install.sh has no copy path for new contract member %s" % name


def test_bundle_accepts_both_contracts_and_members_follow_candidate_contract():
    sys.path.insert(0, str(HERE.parents[2] / "scripts"))
    import rf_modrec4_bundle as B
    assert set(B.CANDIDATE_CONTRACTS) == {"cnn-bpsk-modrec4-v1", NEW_CONTRACT,
                                          RRC_CONTRACT}
    acceptance = B._acceptance_script("candidate", "parent")
    preflight = B._preflight_script("candidate", "parent", "0" * 64, "trial")
    for text in (acceptance, preflight):
        assert "cnn-bpsk-modrec4-v1" in text and NEW_CONTRACT in text
    assert "_members(candidate, cm['software_contract'])" in Path(
        HERE.parents[2] / "scripts" / "rf_modrec4_bundle.py").read_text(encoding="utf-8")



def test_new_rrc_contract_is_additive_60_and_install_copies_all_members():
    import merged_release as R
    text = _text()
    old = R.software_sources("cnn-bpsk-modrec4-qpsk-v1")
    new = R.software_sources(RRC_CONTRACT)
    assert len(old) == 58 and len(new) == 60
    assert set(old) < set(new)
    assert set(new) - set(old) == set(RRC_MODULES)
    assert text.count('"$SOFTWARE_CONTRACT" = %s' % RRC_CONTRACT) >= 6
    branch = text.index('if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ]')
    branch_text = text[branch:text.index('# The research plan/controller', branch)]
    for name in RRC_MODULES + QPSK_MODULES:
        assert name in branch_text
    for name in sorted(new):
        assert name in text, "install.sh has no copy path for RRC contract member %s" % name


def test_parent_qpsk_release_still_verifies_and_old_maps_unchanged():
    import merged_release as R
    release = (HERE.parents[2] / "evidence/rf-teaching/"
               "s2-46db-p3-qpsk-candidate-2026-10-23/candidate/release")
    if not release.is_dir():
        import pytest
        pytest.skip("parent QPSK evidence release not present in this checkout")
    m = R.verify_release(release, require_software=True)
    assert m["software_contract"] == NEW_CONTRACT
    assert len(m["software"]) == 58
    assert set(R.software_sources(RRC_CONTRACT)) - set(m["software"]) \
        == set(RRC_MODULES)
    assert len(R.software_sources("cnn-bpsk-modrec4-v1")) == 55


def test_parent_a_old_contract_release_still_verifies():
    import pytest
    import merged_release as R
    release = (HERE.parents[2] / "evidence/rf-teaching/"
               "s2-46db-candidate-a-engineering-2026-10-23/candidate/release")
    if not release.is_dir():
        pytest.skip("parent A evidence release not present in this checkout")
    m = R.verify_release(release, require_software=True)
    assert m["software_contract"] == "cnn-bpsk-modrec4-v1"
    assert len(m["software"]) == 55
    assert set(R.software_sources("cnn-bpsk-modrec4-qpsk-v1")) - set(m["software"]) \
        == {"rf_modem_result.py", "rf_qpsk_receiver.py", "rf_qpsk_pipeline.py"}



def test_new_contract_software_map_hashes_every_member():
    import merged_release as R
    root = HERE.parents[2]
    digests = R.software_digests(root, NEW_CONTRACT)
    assert len(digests) == 58
    assert R.validate_software_contract(digests, NEW_CONTRACT) == NEW_CONTRACT
    assert "rf_qpsk_pipeline.py" in digests
    old = R.software_digests(root, "cnn-bpsk-modrec4-v1")
    assert len(old) == 55
    assert R.validate_software_contract(old, "cnn-bpsk-modrec4-v1") == "cnn-bpsk-modrec4-v1"


def test_new_rrc_contract_software_map_hashes_every_member():
    import merged_release as R
    root = HERE.parents[2]
    digests = R.software_digests(root, RRC_CONTRACT)
    assert len(digests) == 60
    assert R.validate_software_contract(digests, RRC_CONTRACT) == RRC_CONTRACT
    for name in RRC_MODULES:
        assert name in digests
