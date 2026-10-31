#!/bin/sh
# Install one validated, immutable merged release. Both services must be stopped.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
DEST=$(printf %s "${LEDBTN_DEST:-/home/xilinx/overlays/awg_merged}")
if [ "$DEST" != /home/xilinx/overlays/awg_merged ]; then
    echo "REFUSE: service units require /home/xilinx/overlays/awg_merged" >&2
    exit 1
fi
OVERLAY_SRC=${LEDBTN_SOURCE:?set LEDBTN_SOURCE to a verified local release directory}
BIT_NAME=awg_merged.bit
HWH_NAME=awg_merged.hwh
SIDECAR_UNIT_NAME=rf-daemon.service

RELEASE_ID=$(python3 "$SRC/merged_release.py" "$OVERLAY_SRC")
SOFTWARE_CONTRACT=$(python3 "$SRC/merged_release.py" --software-contract "$OVERLAY_SRC")
for unit in pynq-status.service rf-daemon.service; do
    if systemctl is-active --quiet "$unit"; then
        echo "REFUSE: stop $unit before release activation" >&2
        exit 1
    fi
done
if [ ! -f "$OVERLAY_SRC/merged_build_receipt.json" ]; then
    echo "REFUSE: merged_build_receipt.json is required" >&2
    exit 1
fi
mkdir -p "$DEST/releases"
RELEASE="$DEST/releases/$RELEASE_ID"
if [ -e "$RELEASE" ] || [ -L "$RELEASE" ]; then
    echo "REFUSE: immutable release already exists: $RELEASE" >&2
    exit 1
fi
STAGE=$(mktemp -d "$DEST/releases/.staging.XXXXXXXX")
# A failed install must not leave a half-written staging tree behind: the release id is
# content-derived, so a stale stage is never reused -- it only accumulates.
trap 'rm -rf "$STAGE"' EXIT INT TERM
install -m 644 "$OVERLAY_SRC/$BIT_NAME" "$STAGE/$BIT_NAME"
install -m 644 "$OVERLAY_SRC/$HWH_NAME" "$STAGE/$HWH_NAME"
install -m 755 "$OVERLAY_SRC/rf_daemon" "$STAGE/rf_daemon"
install -m 644 "$OVERLAY_SRC/merged_release_manifest.json" "$STAGE/merged_release_manifest.json"
install -m 644 "$OVERLAY_SRC/merged_build_receipt.json" "$STAGE/merged_build_receipt.json"
install -m 644 "$SRC/merged_release.py" "$STAGE/merged_release.py"
for module in pynq_status.py pynq_lcd.py pynq_lcd_ui.py pynq_touch.py touch_demo.py \
    awg_ctl.py awg_bridge.py ui_model.py ui_view.py ui_units.py rf_iq_signal.py panel_preview.py \
    glyph_check.py font_probe.py p7_e2e.py awg_ddr_pp.py awg_ddr_ring.py \
    feeder_decision.py ring_thread.py p5b_ringtest.py; do
    install -m 644 "$SRC/$module" "$STAGE/$module"
done
for module in rf_status_client.py rf_status_worker.py rf_tx_waveform.py; do
    install -m 644 "$SRC/$module" "$STAGE/$module"
done
if [ "$SOFTWARE_CONTRACT" != legacy-v1 ]; then
    for module in rf_rx_types.py rf_rx_analysis.py; do
        install -m 644 "$SRC/$module" "$STAGE/$module"
    done
fi
if [ "$SOFTWARE_CONTRACT" = cnn-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-teaching-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-environment-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
    install -m 644 "$SRC/rf_cnn_runtime.py" "$STAGE/rf_cnn_runtime.py"
    install -m 644 "$SRC/rf_cnn_conv.so" "$STAGE/rf_cnn_conv.so"
    install -m 644 "$SRC/rf_cnn_conv.c" "$STAGE/rf_cnn_conv.c"
fi
if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-teaching-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-environment-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
    install -m 644 "$SRC/rf_bpsk_receiver.py" "$STAGE/rf_bpsk_receiver.py"
    install -m 644 "$SRC/rf_bpsk_pipeline.py" "$STAGE/rf_bpsk_pipeline.py"
fi
if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-teaching-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-environment-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
    for module in rf_teaching_profile.py rf_rx_constellation.py rf_experiment_record.py; do
        install -m 644 "$SRC/$module" "$STAGE/$module"
    done
fi
if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-environment-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
    for module in rf_environment.py rf_experiment_scenarios.py rf_environment_baseline.json; do
        install -m 644 "$SRC/$module" "$STAGE/$module"
    done
fi
if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
    # The additive modrec4 contract inherits the whole environment/BPSK stack and
    # adds its adapter, pure-numpy runtime, full-path harness and three model JSONs.
    # Every file below is in MODREC4_SOFTWARE_SOURCES (or the modrec4 artifact) and
    # is therefore checked again by --require-software after the copy.
    for module in rf_modrec4_adapter.py rf_modrec4_input_domain.py \
        rf_modrec4_fullpath.py rf_modrec4_baseline.py rf_modrec4_features.py \
        rf_modrec4_gate.py rf_modrec4_iq.py rf_modrec4_numpy_runtime.py \
        rf_modrec4_predictor.py rf_modrec4_preprocess.py rf_modrec4_spectral.py; do
        install -m 644 "$SRC/$module" "$STAGE/$module"
    done
    for artifact in modrec4_cnn_model.json modrec4_baseline_model.json \
        modrec4_rule_config.json; do
        install -m 644 "$OVERLAY_SRC/$artifact" "$STAGE/$artifact"
    done
    if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
        for module in rf_modem_result.py rf_qpsk_receiver.py rf_qpsk_pipeline.py; do
            install -m 644 "$SRC/$module" "$STAGE/$module"
        done
    fi
    if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
        for module in rf_rrc_receiver.py rf_rrc_pipeline.py; do
            install -m 644 "$SRC/$module" "$STAGE/$module"
        done
    fi
    if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-v1 ] || [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
        for module in rf_qam16_receiver.py rf_qam16_pipeline.py; do
            install -m 644 "$SRC/$module" "$STAGE/$module"
        done
    fi
    if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-modrec4-qpsk-rrc-qam16-qam64-v1 ]; then
        for module in rf_qam64_receiver.py rf_qam64_pipeline.py; do
            install -m 644 "$SRC/$module" "$STAGE/$module"
        done
    fi
fi
# The research plan/controller travels only with its own contract.  Installing it
# into a 40 dB release would put a 46 dB transaction on a board whose daemon does
# not implement it -- and the attestation would then be describing software that
# cannot run.
if [ "$SOFTWARE_CONTRACT" = cnn-bpsk-headroom-v1 ]; then
    install -m 644 "$SRC/rf_headroom.py" "$STAGE/rf_headroom.py"
    install -m 644 "$SRC/rf_headroom_replay.py" "$STAGE/rf_headroom_replay.py"
fi
for artifact in $(python3 "$SRC/merged_release.py" --artifact-files "$OVERLAY_SRC"); do
    install -m 644 "$OVERLAY_SRC/$artifact" "$STAGE/$artifact"
done
for module in ddr_e2e.py check_play_userbuf.py prove_play_read.py; do
    install -m 644 "$SRC/tests/$module" "$STAGE/$module"
done
install -m 644 "$SRC/../../sdk/src/sincos_q15.h" "$STAGE/sincos_q15.h"
# The two systemd units and this installer travel in the release as well: they are part of
# what a running release executes, so the receipt attests them and the --require-software
# check below fails the install if any attested file did not arrive intact.  The sidecar
# unit is uploaded next to the staging directory rather than inside it, hence the fallback.
install -m 644 "$SRC/install.sh" "$STAGE/install.sh"
install -m 644 "$SRC/pynq-status.service" "$STAGE/pynq-status.service"
SIDECAR_UNIT="$SRC/$SIDECAR_UNIT_NAME"
[ -f "$SIDECAR_UNIT" ] || SIDECAR_UNIT="$SRC/../../rf_overlay/sw/$SIDECAR_UNIT_NAME"
install -m 644 "$SIDECAR_UNIT" "$STAGE/$SIDECAR_UNIT_NAME"
# Durability ordering, deliberately.  `sync` BEFORE the verification so the check reads
# what actually reached the medium: a write that failed, or never left the page cache,
# then surfaces as a sync/verify error instead of as an accepted install whose files are
# 0-byte after the next power loss -- the E9 incident's symptom, where this script's
# output files were the ones found empty while the read-back verification had passed
# (it read through the page cache).
sync
python3 -B "$STAGE/merged_release.py" --require-software --check-panel "$STAGE" >/dev/null
# Read-only BEFORE the rename: the rename is the atomic publish, so it must publish an
# already-immutable directory.  Doing it after publish left a window in which a crash
# left a writable release that the existence check then refuses forever (the id is
# content-derived, so a retry produces the same id and hits the same refusal).
chmod -R a-w "$STAGE"
mv "$STAGE" "$RELEASE"
sync
NEXT_CURRENT="$DEST/.current.next.$$"
ln -s "releases/$RELEASE_ID" "$NEXT_CURRENT"
mv -Tf "$NEXT_CURRENT" "$DEST/current"
# The activation itself has to be on the medium, not only in the page cache.
sync
install -d -o xilinx -g xilinx -m 755 /home/xilinx/waveforms
# Install the units from the published release, not from $SRC and not from $STAGE: $SRC is
# an unhashed upload copy, and $STAGE has already been renamed to $RELEASE by the atomic
# publish above (taking it from $STAGE is what broke the 2026-09-29 first attempt).  These
# are the bytes --require-software checked, and the release directory is immutable.
# Publish the units ATOMICALLY, for the same reason the release directory and the
# `current` symlink are published atomically.  `install -m 644 src /etc/systemd/system/
# x.service` CREATES the destination and then fills it, so an interruption (or a full
# disk) leaves a 0-BYTE file -- and systemd reads an EMPTY unit file as MASKED, so both
# services die silently and `systemctl start` stops having any effect at all.  That is
# measured rather than hypothetical: a deploy interrupted on 2026-09-29 left exactly
# those two empty files and the panel stayed dead until they were rewritten (delivery
# record §37).
#
# The temporary file lives in the destination DIRECTORY, so the rename is atomic on the
# same filesystem; it is synced and proven non-empty AND byte-identical to the release's
# copy BEFORE it can become a unit, so a truncated write can never be activated.
install_unit_atomically() {
    unit_src=$1
    unit_dest=$2
    unit_tmp="$unit_dest.tmp.$$"

    rm -f "$unit_tmp"
    install -m 644 "$unit_src" "$unit_tmp"
    sync
    if [ ! -s "$unit_tmp" ] || ! cmp -s "$unit_src" "$unit_tmp"; then
        rm -f "$unit_tmp"
        echo "REFUSE: unit did not arrive intact: $unit_dest" >&2
        exit 1
    fi
    mv -f "$unit_tmp" "$unit_dest"
    # The activation itself has to be on the medium, not only in the page cache.
    sync
    if [ ! -s "$unit_dest" ]; then
        echo "REFUSE: unit is empty after activation: $unit_dest" >&2
        exit 1
    fi
}
install_unit_atomically "$RELEASE/pynq-status.service" /etc/systemd/system/pynq-status.service
install_unit_atomically "$RELEASE/$SIDECAR_UNIT_NAME" "/etc/systemd/system/$SIDECAR_UNIT_NAME"
systemctl daemon-reload
echo "Installed $RELEASE; services remain stopped."
