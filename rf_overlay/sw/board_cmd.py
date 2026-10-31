"""board_cmd.py -- drive board_app over the paramiko helper.

Deliberately not ssh/scp: with no sshpass/plink installed, a non-interactive
ssh on this machine pops a graphical credential dialog and hangs.  The
ac820-pynq skill's pynq_ssh.py uses paramiko with the password passed
directly, which does not.

One process per call.  There is no session and there cannot be one: pynq_ssh.py
has no stdin, so nothing can be fed to a persistent process, and board_app's
`phy` is a process-local static that only `init` ever creates.  That is why the
phy-dependent knobs are keyword/value pairs on `init` rather than subcommands --
see rf_main.c's R7 note.  `phase_inc` and `status` need no phy and stay
standalone; the AD9361 keeps transmitting after the init process exits, because
it is hardware and only the driver's in-RAM state dies with the process.
"""
import os
import re
import subprocess
import sys

HELPER = os.path.expanduser(
    "~/.claude/skills/ac820-pynq/scripts/pynq_ssh.py")

# pynq_ssh.py connects per invocation and `init` runs a full ad9361_init, which
# includes a VCO calibration.  The helper's own default is 300 s; keep it.
TIMEOUT_S = 300

# The board's exit status is recovered through a marker appended to the remote
# command, because pynq_ssh.py NEVER CALLS recv_exit_status(): it reads
# stdout/stderr, prints them, and closes (pynq_ssh.py:36-47).  So the remote
# status is discarded inside the helper, and subprocess.run's returncode is the
# exit status of the LOCAL `python pynq_ssh.py` process -- 0 whenever the helper
# ran at all.  Branching on it is dead code.
#
# That matters because rf_main.c encodes real failures in its exit status: exit 2
# on a malformed numeric argument (parse_dec_u64, the M1 fix), exit 1 when the
# `phy_mode` guard fires or a trace flush fails (I2).  M1's stated rationale is
# that "Task 5 drives this interface from a script where a typo would otherwise
# pass unnoticed" -- which requires this script to be able to see the status.
#
# `$?` is the pipeline's status, i.e. sudo/the app's, so a sudo failure is caught
# too.  The helper prints a `$ <cmd>` banner containing this string as a
# substring, hence the anchored match on a whole line.
_RC_MARK = "__BOARD_RC="
_RC_RE = re.compile(r"^" + re.escape(_RC_MARK) + r"(\d+)$", re.M)


class BoardCmd(object):
    def __init__(self, app_path, host=None):
        self.app = app_path
        self.host = host

    def cmd(self, args):
        """Run `sudo <app> <args>` on the board and return stdout.

        stdout only, with the exit-status marker stripped.  pynq_ssh.py prints a
        banner per command into the same stream, so the return value is banner +
        program stdout; callers that parse it must line-scan for their own key
        rather than assume a shape.  A failure message goes to stderr and is
        echoed here, but the return stays stdout-only so that "the command
        failed" and "the command succeeded and printed nothing" are the same
        empty string -- a caller that cannot tell them apart is a caller that is
        not judging anything.

        A non-zero exit from the board call means the command did not do what it
        was asked (rf_main.c exits 2 on a malformed numeric argument, 1 when its
        `phy_mode` guard or a trace flush fails), so it is reported loudly here.
        The marker is stripped before returning so that consumers parsing the
        LAST token of a line -- audit_report.py reads `audit 0x%03X = 0x%02X`
        that way -- never see it.

        `host` reaches the helper as PYNQ_HOST in the child environment.
        pynq_ssh.py reads PYNQ_HOST at import time and carries no host argument
        on the command line, so without this the address was decoration: a
        `--board` of 192.0.2.10 or a typo'd address ran on the helper's own
        default (192.0.2.10) and the operator would have attributed the
        verdicts to the machine they named.
        """
        if self.host is None:
            return ""
        full = "echo $PYNQ_PASSWORD | sudo -S %s %s; echo %s$?" % (
            self.app, args, _RC_MARK)
        p = subprocess.run([sys.executable, HELPER, "run", full],
                           capture_output=True, text=True, timeout=TIMEOUT_S,
                           env=dict(os.environ, PYNQ_HOST=self.host))
        out = p.stdout
        rc = None
        hits = _RC_RE.findall(out)
        if hits:
            rc = int(hits[-1])
            out = _RC_RE.sub("", out)
        if rc:
            print("[board] %s -> rc=%d\n%s" % (args, rc, p.stderr))
        elif rc is None:
            # No marker: the remote command never ran to completion (ssh died,
            # sudo never got a password, the helper itself failed).  That is not
            # "the command succeeded" and must not be silent.
            print("[board] %s -> no exit status from the board "
                  "(helper rc=%d)\n%s" % (args, p.returncode, p.stderr))
        return out