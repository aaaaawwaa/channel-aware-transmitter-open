#!/bin/sh
# Extend the existing installer's rollback transaction through board acceptance.
# Run under a board-owned systemd job, not a host SSH lifetime.
#
# Two process levels are deliberate.  The child runs the base installer and the
# bounded acceptance in ONE shell, so the base installer's EXIT rollback trap is
# still armed for acceptance failure.  The parent validates, starts the child, and
# then ALWAYS reads the board back independently before printing one terminal
# marker.  A base-installer `exit` therefore can no longer bypass the terminal
# marker, and no second rollback is attempted after the child's one rollback.
set -eu

refuse_trial() {
    printf 'trial refused: %s\n' "$1" >&2
    exit 2
}

[ "$#" -ge 6 ] || refuse_trial 'stage release parent archive_sha seconds check-command [args...] required'
_trial_stage=$1
_trial_expected=$2
_trial_parent=$3
_trial_archive=$4
_trial_seconds=$5
shift 5
case "$_trial_seconds" in ''|*[!0-9]*) refuse_trial 'seconds must be an integer from 1 to 300';; esac
if ! [ "$_trial_seconds" -ge 1 ] || ! [ "$_trial_seconds" -le 300 ]; then
    refuse_trial 'seconds must be an integer from 1 to 300'
fi
[ -r "$_trial_stage/install.sh" ] || refuse_trial 'verified base install.sh required'
[ -r "$_trial_stage/install_state.py" ] || refuse_trial 'base state checker required'
[ ! -e "$_trial_stage/trial-state.json" ] || refuse_trial 'trial output already exists'
[ ! -e "$_trial_stage/trial-rollback-state.json" ] || refuse_trial 'rollback output already exists'
command -v timeout >/dev/null 2>&1 || refuse_trial 'timeout is required before installation'
timeout --kill-after=2s 1s true >/dev/null 2>&1 || refuse_trial 'timeout must support --kill-after'
command -v python3 >/dev/null 2>&1 || refuse_trial 'python3 is required before installation'
command -v "$1" >/dev/null 2>&1 || refuse_trial 'acceptance command is unavailable'

# Child: base install and acceptance share one shell, so the installer's EXIT trap
# (its reviewed rollback) covers both stages.  The child never prints a terminal
# marker; the parent reads the board back and decides the marker.
if [ "${RF_TRIAL_CHILD:-0}" = 1 ]; then
    # POSIX `sh` (dash) does not pass positional arguments through `.`, so
    # save the acceptance command and install the four base-installer arguments
    # in THIS shell before dot-sourcing.  The base EXIT trap then remains armed.
    _accept_args="$_trial_stage/.trial-accept-args.$$"
    : > "$_accept_args"
    for _arg in "$@"; do printf '%s\n' "$_arg" >> "$_accept_args"; done
    set -- "$_trial_stage" "$_trial_expected" "$_trial_parent" "$_trial_archive"
    . "$_trial_stage/install.sh"
    set --
    while IFS= read -r _arg; do set -- "$@" "$_arg"; done < "$_accept_args"
    rm -f "$_accept_args"
    printf 'TRIAL_CHECK_BEGIN seconds=%s\n' "$_trial_seconds"
    timeout --kill-after=2s "${_trial_seconds}s" "$@"
    python3 -B "$_trial_stage/install_state.py" "$_trial_expected" \
        "$_trial_stage/trial-state.json"
    exit 0
fi

# Parent: no direct board mutation.  It starts one child and then independently
# reads back either the candidate state or the parent state.
set +e
RF_TRIAL_CHILD=1 sh "$0" "$_trial_stage" "$_trial_expected" "$_trial_parent" \
    "$_trial_archive" "$_trial_seconds" "$@"
_child_rc=$?
set -e

if [ "$_child_rc" -eq 0 ]; then
    # A zero child exit is not proof: read the child's final state file
    # independently; do not rewrite it (the base state checker creates files with x).
    if python3 -B - "$_trial_stage/trial-state.json" "$_trial_expected" <<'PY'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if state.get("release_id") == sys.argv[2]
                 and state.get("safe") is True else 1)
PY
    then
        printf 'TRIAL_ACCEPTED release=%s\n' "$_trial_expected"
        exit 0
    fi
    printf 'TRIAL_RECOVERY_REQUIRED reason=accepted_state_unverified\n'
    exit 1
fi

# The child's base installer owns the single rollback.  Never invoke another one
# here: read the parent back independently and name the terminal state.
if python3 -B "$_trial_stage/install_state.py" "$_trial_parent" \
        "$_trial_stage/trial-rollback-state.json"; then
    printf 'TRIAL_ROLLED_BACK parent=%s\n' "$_trial_parent"
else
    printf 'TRIAL_RECOVERY_REQUIRED parent=%s\n' "$_trial_parent"
fi
exit "$_child_rc"
