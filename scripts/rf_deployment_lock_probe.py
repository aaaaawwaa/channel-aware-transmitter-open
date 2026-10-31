#!/usr/bin/env python3
"""Judge WHO holds the RF deployment lock -- never whether the rendezvous file exists.

/var/lock/rf-deployment-trial.lock is a flock(1) rendezvous file.  Both reviewed
deployment orchestrators open it with `exec 9>"$lock_file"` + `flock -n 9` and never
unlink it:

  evidence/rf-teaching/s2-46db-rollback-return-2026-10-18/stage/activate.sh:84-86
      command -v flock >/dev/null 2>&1 || { echo "REFUSE: flock(1) is missing"; exit 3; }
      exec 9>"$lock_file"
      flock -n 9 || { echo "REFUSE: another deployment holds $lock_file"; exit 3; }

  evidence/rf-teaching/s2-46db-rollback-return-2026-10-18/stage/rf_teaching_trial_rollback_after_accept_v3.sh:543-549
      for tool in flock sha256sum ...; do command -v "$tool" || guard_refuse missing_tool "$tool"; done
      if ! exec 9>"$lock_file"; then guard_refuse lock_open_failed "$lock_file"; fi
      flock -n 9 || guard_refuse lock_busy "$lock_file"

and /var/lock is /run/lock (tmpfs, cleared by a reboot).  A surviving file therefore
proves nothing: the 2026-10-18 campaign2 preflight failed B10 on exactly that file
while its own holder scan found no owner
(evidence/rf-teaching/s2/46db-trainval-campaign2-2026-10-18/preflight/pre-00-verdict.json).
The same privileged probe shape was already used on the real board by
evidence/rf-teaching/s2-46db-trial-exec-2026-10-17/work/board_readonly.py:134.

Verdicts (the `ok` of preflight item B10):
  LOCK_FREE      True   nobody holds it: the probe acquired and released the lock itself,
                        or the path does not exist (so no orchestrator can have it open)
  LOCK_HELD      False  another process holds it                          (named failure)
  LOCK_UNKNOWN   False  the probe could not decide -- flock(1) missing, no permission to
                        open the rendezvous file, no privilege, unexpected flock exit
                                                       (named failure, never a pass)
  NOT_EVALUATED  None   transcript without an ownership verdict (written before this fix
                        carries existence only): reported as not evaluated -- never a
                        silent pass, never a false alarm

The probe opens the rendezvous file for append only (no truncation, no mtime change, and
it does not create the path when it is absent), takes the lock inside a subshell so the
descriptor -- and the lock -- is dropped the moment that subshell exits, and never
unlinks anything.
"""
import shlex
import subprocess

LOCK_PATH = '/var/lock/rf-deployment-trial.lock'
LOCK_FREE = 'LOCK_FREE'
LOCK_HELD = 'LOCK_HELD'
LOCK_UNKNOWN = 'LOCK_UNKNOWN'
NOT_EVALUATED = 'NOT_EVALUATED'
STATES = (LOCK_FREE, LOCK_HELD, LOCK_UNKNOWN, NOT_EVALUATED)
#: Privilege prefix for the board: the rendezvous file is mode 600 root:root, and a
#: preflight that cannot read it must say LOCK_UNKNOWN instead of guessing.
SUDO = "sudo -S -p ''"
LEGACY_MARKERS = ('LOCK_PRESENT', 'LOCK_ABSENT')
#: Reasons that name an undecidable probe (never a pass, always a named failure).
UNKNOWN_REASONS = ('tool_missing', 'open_failed', 'privileged_probe_unavailable',
                   'unspecified_probe_failure')


class LockVerdict(object):
    """One verdict: `ok` is True only for LOCK_FREE, False for named failures, None
    for NOT_EVALUATED."""

    __slots__ = ('ok', 'state', 'reason', 'lines', 'path')

    def __init__(self, ok, state, reason, lines, path):
        self.ok = ok
        self.state = state
        self.reason = reason
        self.lines = list(lines)
        self.path = path

    def to_dict(self):
        return dict(ok=self.ok, state=self.state, reason=self.reason,
                    lines=self.lines, path=self.path)

    def __repr__(self):
        return 'LockVerdict(ok=%r, state=%r, reason=%r)' % (self.ok, self.state, self.reason)


def verdict_probe(path=LOCK_PATH):
    """POSIX-sh probe that decides ownership; it needs the privilege to open `path`.

    Prints exactly one verdict token plus a LOCK_SOURCE=... / LOCK_UNKNOWN_REASON=...
    line, so the transcript can be judged without guessing from flock(1)'s exit status
    alone (util-linux reports both contention and hard errors as exit 1; exits above 1
    are hard errors).
    """
    lock = shlex.quote(str(path))
    return (
        'lock=%(lock)s; '
        'if [ ! -e "$lock" ]; then echo LOCK_FREE; echo LOCK_SOURCE=file_absent; '
        'elif ! command -v flock >/dev/null 2>&1; then '
        'echo LOCK_UNKNOWN; echo LOCK_UNKNOWN_REASON=tool_missing; '
        'elif ! ( exec 9>>"$lock" ) 2>/dev/null; then '
        'echo LOCK_UNKNOWN; echo LOCK_UNKNOWN_REASON=open_failed; '
        'else ( exec 9>>"$lock"; flock -n 9 ); lock_rc=$?; '
        'case $lock_rc in '
        '0) echo LOCK_FREE; echo LOCK_SOURCE=flock_acquired ;; '
        '1) echo LOCK_HELD; echo LOCK_SOURCE=flock_contended ;; '
        '*) echo LOCK_UNKNOWN; echo LOCK_UNKNOWN_REASON=flock_exit_$lock_rc ;; '
        'esac; fi'
    ) % dict(lock=lock)


def lock_step(path=LOCK_PATH, privilege=SUDO, shell='/bin/sh'):
    """The complete `==LOCK==` section of the read-only preflight transcript.

    Reports existence for the record, then the ownership verdict from the privileged
    probe.  If the privileged probe produced no verdict at all (no sudo, refused
    password, killed probe, missing sh) the step says LOCK_UNKNOWN itself: a silent
    pass is impossible.

    The inner shell is named by absolute path so that a broken PATH is reported as
    "flock(1) missing" by the probe (which owns that reason) instead of failing the
    wrapper's own lookup -- the two failures must not be confused.
    """
    lock = shlex.quote(str(path))
    core = shlex.quote(verdict_probe(path))
    return (
        'echo ==LOCK==; '
        'lock=%(lock)s; '
        'if [ -e "$lock" ]; then echo LOCK_PRESENT; ls -la "$lock" 2>&1; '
        'else echo LOCK_ABSENT; fi; '
        'lock_out=$(%(priv)s %(shell)s -c %(core)s 2>&1); lock_rc=$?; '
        'printf "%%s\n" "$lock_out"; '
        'case "$lock_out" in '
        '*LOCK_FREE*|*LOCK_HELD*|*LOCK_UNKNOWN*) ;; '
        '*) echo LOCK_UNKNOWN; echo LOCK_UNKNOWN_REASON=privileged_probe_unavailable; '
        'echo LOCK_PROBE_RC=$lock_rc ;; esac; '
        'echo LOCK_DONE'
    ) % dict(lock=lock, priv=privilege, core=core, shell=shlex.quote(str(shell)))


def lock_section(text):
    """The rows between `==LOCK==` and the next section header or LOCK_DONE."""
    rows, active = [], False
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped == '==LOCK==':
            active = True
            continue
        if active and (line.lstrip().startswith('==') or stripped == 'LOCK_DONE'):
            break
        if active:
            rows.append(line)
    return rows


def _field(rows, name):
    prefix = name + '='
    for row in rows:
        if row.strip().startswith(prefix):
            return row.strip()[len(prefix):].strip()
    return None


def lock_verdict(text, path=LOCK_PATH):
    """Judge one transcript (the whole paths log, or just its LOCK section)."""
    rows = lock_section(text)
    if not rows:
        return LockVerdict(None, NOT_EVALUATED, 'lock_section_missing', rows, str(path))
    tokens = [row.strip() for row in rows]
    if LOCK_HELD in tokens:                      # a held lock outranks every other token
        return LockVerdict(False, LOCK_HELD,
                           _field(rows, 'LOCK_SOURCE') or 'lock_contended', rows, str(path))
    if LOCK_FREE in tokens:
        return LockVerdict(True, LOCK_FREE,
                           _field(rows, 'LOCK_SOURCE') or 'flock_acquired', rows, str(path))
    if LOCK_UNKNOWN in tokens:
        return LockVerdict(False, LOCK_UNKNOWN,
                           _field(rows, 'LOCK_UNKNOWN_REASON') or 'unspecified_probe_failure',
                           rows, str(path))
    if any(marker in tokens for marker in LEGACY_MARKERS):
        return LockVerdict(None, NOT_EVALUATED,
                           'legacy_transcript_without_ownership_verdict', rows, str(path))
    return LockVerdict(None, NOT_EVALUATED, 'unrecognized_lock_transcript', rows, str(path))


def lock_item(text, path=LOCK_PATH):
    """The preflight item: `ok` is True only for LOCK_FREE (None = NOT EVALUATED)."""
    return lock_verdict(text, path).to_dict()


def run_local_probe(path=LOCK_PATH, privileged=True, shell='/bin/sh', env=None):
    """Run the step on the local POSIX host; returns (verdict, transcript, exit code).

    Used by the tests and by any host-side audit of the same shell step that the board
    transcript carries.  `privileged=False` drops the sudo prefix (the caller already
    has the privilege).
    """
    script = lock_step(path, SUDO if privileged else '')
    completed = subprocess.run([shell, '-c', script], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, env=env)
    text = completed.stdout.decode('utf-8', 'replace')
    return lock_verdict(text, path), text, completed.returncode
