#!/bin/sh
# Activate a verified S1 package; revert previous release/units if activation fails.
set -eu
stage=$1
expected=$2
parent=$3
archive_sha=$4
dest=/home/xilinx/overlays/awg_merged
case "$stage" in /tmp/rf-teaching-install-*) ;; *) exit 2;; esac
python3 -B -c 'import re,sys; assert all(re.fullmatch("[0-9a-f]{32}",s) for s in sys.argv[1:3]); assert re.fullmatch("[0-9a-f]{64}",sys.argv[3])' "$expected" "$parent" "$archive_sha"
old=$(readlink -f "$dest/current")
test "$old" = "$dest/releases/$parent"
test ! -e "$dest/releases/$expected"
test ! -L "$dest/releases/$expected"
python3 -B "$stage/install_state.py" "$parent" "$stage/pre-state.json"
python3 -B -c 'import hashlib,pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); assert hashlib.sha256(p.read_bytes()).hexdigest()==sys.argv[2]; assert shutil.disk_usage(sys.argv[3]).free>max(67108864,p.stat().st_size*5)' "$stage/candidate.tgz" "$archive_sha" "$dest"
mkdir "$stage/unpacked"
tar -xzf "$stage/candidate.tgz" -C "$stage/unpacked"
src="$stage/unpacked"
actual=$(python3 -B "$src/release/merged_release.py" "$src/release" --require-software --check-panel)
test "$actual" = "$expected"
# Validate the inner installer's relative source tree BEFORE stopping services.
# A release-only archive verifies correctly but cannot execute the installer.
python3 -B - "$src" <<'PY'
import importlib.util
from pathlib import Path
import sys

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "install_layout_release", root / "release/merged_release.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
manifest = verifier.verify_release(root / "release", require_software=True)
for name, relative in verifier.software_sources(manifest["software_contract"]).items():
    source = root / relative
    if not source.is_file() or verifier.sha256_file(source) != manifest["software"][name]:
        raise SystemExit("REFUSE INSTALL_LAYOUT: missing or mismatched " + relative)
PY
cp /etc/systemd/system/pynq-status.service "$stage/pynq-status.before"
cp /etc/systemd/system/rf-daemon.service "$stage/rf-daemon.before"
changed=0
rollback() {
    rc=$1
    trap - EXIT INT TERM
    if [ "$rc" -ne 0 ] && [ "$changed" -eq 1 ]; then
        echo 'INSTALL FAILED: restoring previous release and service units'
        systemctl stop rf-daemon pynq-status || true
        ln -s "$old" "$dest/.rollback.$$"
        mv -Tf "$dest/.rollback.$$" "$dest/current"
        for unit in pynq-status rf-daemon; do
            install -m 644 "$stage/$unit.before" "/etc/systemd/system/$unit.service.rollback.$$"
            mv -f "/etc/systemd/system/$unit.service.rollback.$$" "/etc/systemd/system/$unit.service"
        done
        sync
        systemctl daemon-reload
        systemctl start pynq-status || true
        systemctl start rf-daemon || true
        python3 -B "$stage/install_state.py" "$parent" "$stage/rollback-state.json" || true
    fi
    exit "$rc"
}
trap 'rollback $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
changed=1
echo 'INSTALL BEGIN: stopping existing PL owner and sidecar'
systemctl stop rf-daemon pynq-status
LEDBTN_SOURCE="$src/release" sh "$src/pynq_overlay/sw/install.sh"
test "$(readlink -f "$dest/current")" = "$dest/releases/$expected"
systemctl start pynq-status
systemctl start rf-daemon
systemctl is-active --quiet pynq-status
systemctl is-active --quiet rf-daemon
test "$(cat /run/pynq-status/active-release)" = "$dest/releases/$expected"
python3 -B "$dest/current/merged_release.py" "$dest/current" --require-software --check-panel
python3 -B "$stage/install_state.py" "$expected" "$stage/post-state.json"
echo INSTALL_ACCEPTED
