#!/usr/bin/env python3
"""B10 deployment-lock probe: verdict semantics, real holders, preflight wiring.

The defect this pins down: the 2026-10-18 campaign2 preflight judged B10 by
/var/lock/rf-deployment-trial.lock FILE EXISTENCE, but the two reviewed deployment
orchestrators open that file with flock(1) and never unlink it, so a surviving
rendezvous file is normal.  B10 must report WHO holds the lock.

Run with pytest (repo default) or standalone:
    python scripts/tests/test_rf_deployment_lock_probe.py
    python -m unittest scripts.tests.test_rf_deployment_lock_probe   # dotted form
The real-holder cases need a POSIX host with flock(1) and /bin/sh (Linux/WSL); they
skip elsewhere.  The wiring cases import the preflight probes, which need the repo's
host dependencies (numpy); they skip when those are unavailable.
"""
import hashlib
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rf_deployment_lock_probe import (  # noqa: E402
    LOCK_FREE, LOCK_HELD, LOCK_PATH, LOCK_UNKNOWN, NOT_EVALUATED, STATES, SUDO,
    lock_item, lock_section, lock_step, lock_verdict, run_local_probe, verdict_probe)

PROBES = (
    ROOT / 'evidence/rf-teaching/s2-46db-rehearsal-2026-10-18/work/preflight_probe.py',
    ROOT / 'evidence/rf-teaching/s2/46db-train-2026-10-18/work/preflight_probe.py',
    ROOT / 'evidence/rf-teaching/s2/46db-trainval-campaign2-2026-10-18/work/preflight_probe.py',
)
CAMPAIGN2_TRANSCRIPT = (ROOT / 'evidence/rf-teaching/s2/46db-trainval-campaign2-2026-10-18'
                        / 'preflight/pre-00-03-paths.log')
PRESENT_UNHELD = '==LOCK==\nLOCK_PRESENT\nLOCK_FREE\nLOCK_SOURCE=flock_acquired\nLOCK_DONE\n'
PRESENT_HELD = '==LOCK==\nLOCK_PRESENT\nLOCK_HELD\nLOCK_SOURCE=flock_contended\nLOCK_DONE\n'
ABSENT = '==LOCK==\nLOCK_ABSENT\nLOCK_FREE\nLOCK_SOURCE=file_absent\nLOCK_DONE\n'

POSIX_FLOCK = (os.name == 'posix' and shutil.which('flock') is not None
               and Path('/bin/sh').is_file())
needs_flock = unittest.skipUnless(POSIX_FLOCK, 'needs a POSIX host with flock(1) and /bin/sh')


def load_probe(path):
    """Import one preflight probe module from its path (skips if deps are missing)."""
    name = 'b10_probe_' + hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise unittest.SkipTest('%s: %s' % (path.name, exc))
    return module


class TestVerdictSemantics(unittest.TestCase):
    """One line of transcript in, one named verdict out.  Pure host-side parsing."""

    def test_absent_file_is_free(self):
        verdict = lock_verdict(ABSENT)
        self.assertIs(verdict.ok, True)
        self.assertEqual(verdict.state, LOCK_FREE)
        self.assertEqual(verdict.reason, 'file_absent')

    def test_present_but_unheld_is_free(self):
        """The campaign2 situation: the rendezvous file survives, nobody holds it."""
        verdict = lock_verdict(PRESENT_UNHELD)
        self.assertIs(verdict.ok, True)
        self.assertEqual(verdict.state, LOCK_FREE)
        self.assertEqual(verdict.reason, 'flock_acquired')

    def test_held_lock_is_a_named_failure(self):
        verdict = lock_verdict(PRESENT_HELD)
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_HELD)
        self.assertEqual(verdict.reason, 'flock_contended')

    def test_unknown_probe_is_a_named_failure(self):
        for reason in ('tool_missing', 'open_failed', 'privileged_probe_unavailable',
                       'flock_exit_2'):
            text = ('==LOCK==\nLOCK_PRESENT\nLOCK_UNKNOWN\n'
                    'LOCK_UNKNOWN_REASON=%s\nLOCK_DONE\n' % reason)
            verdict = lock_verdict(text)
            self.assertIs(verdict.ok, False, reason)
            self.assertEqual(verdict.state, LOCK_UNKNOWN, reason)
            self.assertEqual(verdict.reason, reason, reason)

    def test_unknown_without_a_reason_still_names_the_failure(self):
        verdict = lock_verdict('==LOCK==\nLOCK_UNKNOWN\nLOCK_DONE\n')
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_UNKNOWN)
        self.assertEqual(verdict.reason, 'unspecified_probe_failure')

    def test_a_held_lock_outranks_a_free_token(self):
        text = PRESENT_HELD.replace('LOCK_HELD\n', 'LOCK_HELD\nLOCK_FREE\n')
        verdict = lock_verdict(text)
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_HELD)

    def test_legacy_transcript_with_the_file_present_is_not_evaluated(self):
        """Existence-only transcripts must neither false-alarm nor silently pass."""
        verdict = lock_verdict('==LOCK==\nLOCK_PRESENT\n==UNITS==\nUNITS_DONE\n')
        self.assertIsNone(verdict.ok)
        self.assertEqual(verdict.state, NOT_EVALUATED)
        self.assertEqual(verdict.reason, 'legacy_transcript_without_ownership_verdict')

    def test_legacy_transcript_with_the_file_absent_is_not_evaluated(self):
        """The old predicate PASSED this one; it must not be a pass any more either."""
        verdict = lock_verdict('==LOCK==\nLOCK_ABSENT\n==UNITS==\nUNITS_DONE\n')
        self.assertIsNone(verdict.ok)
        self.assertEqual(verdict.state, NOT_EVALUATED)

    def test_transcript_without_a_lock_section_is_not_evaluated(self):
        for text in ('', '==UNITS==\nUNITS_DONE\n', None):
            verdict = lock_verdict(text)
            self.assertIsNone(verdict.ok)
            self.assertEqual(verdict.reason, 'lock_section_missing')

    def test_unrecognized_lock_transcript_is_not_evaluated(self):
        verdict = lock_verdict('==LOCK==\nLOCK_SOMETHING_ELSE\nLOCK_DONE\n')
        self.assertIsNone(verdict.ok)
        self.assertEqual(verdict.reason, 'unrecognized_lock_transcript')

    def test_ok_is_true_for_lock_free_and_nothing_else(self):
        """The whole contract in one table."""
        table = ((ABSENT, True, LOCK_FREE), (PRESENT_UNHELD, True, LOCK_FREE),
                 (PRESENT_HELD, False, LOCK_HELD),
                 ('==LOCK==\nLOCK_UNKNOWN\nLOCK_DONE\n', False, LOCK_UNKNOWN),
                 ('==LOCK==\nLOCK_PRESENT\nLOCK_DONE\n', None, NOT_EVALUATED))
        for text, ok, state in table:
            item = lock_item(text)
            self.assertIs(item['ok'], ok, state)
            self.assertEqual(item['state'], state)
            self.assertEqual(item['ok'] is True, state == LOCK_FREE)
        self.assertEqual(set(STATES),
                         {LOCK_FREE, LOCK_HELD, LOCK_UNKNOWN, NOT_EVALUATED})

    def test_the_item_carries_the_path_and_the_evidence_lines(self):
        item = lock_item(PRESENT_HELD, '/tmp/x.lock')
        self.assertEqual(item['path'], '/tmp/x.lock')
        self.assertIn('LOCK_HELD', item['lines'])
        self.assertEqual(lock_section(PRESENT_HELD),
                         ['LOCK_PRESENT', 'LOCK_HELD', 'LOCK_SOURCE=flock_contended'])

    def test_the_default_path_is_the_deployment_rendezvous_file(self):
        self.assertEqual(LOCK_PATH, '/var/lock/rf-deployment-trial.lock')
        self.assertIn(LOCK_PATH, lock_step())
        self.assertEqual(lock_item(PRESENT_UNHELD)['path'], LOCK_PATH)


class TestShellStep(unittest.TestCase):
    """Static properties of the shell text (they hold on every host)."""

    def test_the_probe_takes_the_lock_with_a_non_blocking_flock(self):
        probe = verdict_probe('/tmp/x.lock')
        self.assertIn('flock -n 9', probe)
        self.assertIn('exec 9>>"$lock"', probe)
        self.assertIn('LOCK_HELD', probe)
        self.assertIn('LOCK_UNKNOWN', probe)

    def test_the_probe_appends_and_never_truncates(self):
        probe = verdict_probe('/tmp/x.lock')
        self.assertNotIn('exec 9>"/tmp/x.lock"', probe)
        self.assertNotIn('9>"$lock"', probe)

    def test_the_probe_never_removes_the_rendezvous_file(self):
        text = verdict_probe() + lock_step()
        for forbidden in ('rm ', 'rm -', 'unlink', 'truncate', 'shred', 'mv '):
            self.assertNotIn(forbidden, text, forbidden)

    def test_the_step_reports_existence_and_then_the_ownership_verdict(self):
        step = lock_step()
        self.assertIn('echo ==LOCK==', step)
        self.assertIn('LOCK_PRESENT', step)
        self.assertIn('LOCK_ABSENT', step)
        self.assertIn('LOCK_DONE', step)

    def test_a_silent_privileged_probe_is_impossible(self):
        """No verdict from the privileged probe => LOCK_UNKNOWN, never a pass."""
        step = lock_step()
        self.assertIn('privileged_probe_unavailable', step)
        self.assertIn('case "$lock_out" in', step)

    def test_the_inner_shell_is_named_by_absolute_path(self):
        self.assertIn('/bin/sh -c', lock_step('/tmp/x.lock', "sudo -S -p ''", '/bin/sh'))


@unittest.skipUnless(POSIX_FLOCK, 'needs a POSIX host with flock(1) and /bin/sh')
class TestRealLocks(unittest.TestCase):
    """Real files, real holders, real failures -- the same step the transcript carries."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='rf-b10-test-'))
        self.holders = []

    def tearDown(self):
        for process in self.holders:
            try:                       # the whole session: flock(1) -c leaves a child
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                pass
            try:
                process.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(str(self.root), ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    def privilege(self):
        if os.geteuid() == 0 and shutil.which('sudo') is None:
            return ''
        return SUDO

    def probe(self, path):
        return run_local_probe(str(path), privileged=bool(self.privilege()))

    def grader_free(self, path):
        return subprocess.run(['flock', '-n', str(path), '-c', 'true'],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0

    def hold(self, argv, path, expect_ready_line=True, timeout=15.0):
        """Start a real holder and wait until an independent flock(1) cannot take it.

        Returns True only when the lock is observed held (and, if the holder announces
        itself on stdout, after that announcement).
        """
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, start_new_session=True)
        self.holders.append(process)
        announced = True
        if expect_ready_line:
            announced = process.stdout.readline().strip() == 'HOLDER_READY'
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.grader_free(path):
                return announced
            time.sleep(0.05)
        return False

    def lock_file(self, name='rf-deployment-trial.lock', content=b'', mode=0o600):
        path = self.root / name
        path.write_bytes(content)
        os.chmod(str(path), mode)
        return path

    # -- cases -----------------------------------------------------------------
    def test_absent_path_is_free_and_is_not_created(self):
        path = self.root / 'absent.lock'
        verdict, text, rc = self.probe(path)
        self.assertIs(verdict.ok, True)
        self.assertEqual(verdict.state, LOCK_FREE)
        self.assertEqual(verdict.reason, 'file_absent')
        self.assertIn('LOCK_ABSENT', text)
        self.assertFalse(path.exists(), 'the read-only probe must not create the file')

    def test_present_but_unheld_is_free_and_the_file_is_left_alone(self):
        path = self.lock_file(content=b'trial-rendezvous\n', mode=0o640)
        mode_before = os.stat(str(path)).st_mode & 0o777
        verdict, text, rc = self.probe(path)
        self.assertIs(verdict.ok, True)
        self.assertEqual(verdict.state, LOCK_FREE)
        self.assertEqual(verdict.reason, 'flock_acquired')
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b'trial-rendezvous\n')
        self.assertEqual(os.stat(str(path)).st_mode & 0o777, mode_before)

    def test_the_probe_releases_the_lock_it_takes(self):
        path = self.lock_file()
        first, _, _ = self.probe(path)
        self.assertEqual(first.state, LOCK_FREE)
        self.assertTrue(self.grader_free(path),
                        'the lock must be free again right after the probe')
        second, _, _ = self.probe(path)
        self.assertEqual(second.state, LOCK_FREE)
        self.assertEqual(second.reason, 'flock_acquired')

    def test_a_real_flock1_holder_process_is_reported_held(self):
        path = self.lock_file()
        self.assertTrue(self.hold(['flock', str(path), '-c', 'sleep 600'], path,
                                  expect_ready_line=False))
        verdict, text, rc = self.probe(path)
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_HELD)
        self.assertEqual(verdict.reason, 'flock_contended')
        self.assertIn('LOCK_PRESENT', text)
        self.assertIsNone(self.holders[0].poll(), 'the probe must not disturb the holder')

    def test_a_real_fcntl_holder_process_is_reported_held(self):
        """A second, independent holder mechanism: Python fcntl.flock."""
        path = self.lock_file()
        holder = ('import fcntl, sys, time\n'
                  'handle = open(sys.argv[1], "a+")\n'
                  'fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
                  'sys.stdout.write("HOLDER_READY\\n"); sys.stdout.flush()\n'
                  'time.sleep(600)\n')
        self.assertTrue(self.hold([sys.executable, '-c', holder, str(path)], path))
        verdict, text, rc = self.probe(path)
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_HELD)

    def test_the_orchestrator_lock_pattern_is_reported_held(self):
        """exec 9>file + flock -n 9, exactly as activate.sh:84-86 does it."""
        path = self.lock_file()
        pattern = 'exec 9>>"$1"; flock -n 9 || exit 3; echo HOLDER_READY; sleep 600'
        self.assertTrue(self.hold(['/bin/sh', '-c', pattern, 'sh', str(path)], path))
        verdict, text, rc = self.probe(path)
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_HELD)
        self.assertEqual(verdict.reason, 'flock_contended')

    def test_a_held_lock_stays_held_across_repeated_probes(self):
        path = self.lock_file()
        self.assertTrue(self.hold(['flock', str(path), '-c', 'sleep 600'], path,
                                  expect_ready_line=False))
        states = [self.probe(path)[0].state for _ in range(3)]
        self.assertEqual(states, [LOCK_HELD] * 3)
        self.assertFalse(self.grader_free(path), 'the holder must still hold it')

    def test_a_missing_flock_tool_is_unknown_not_free(self):
        path = self.lock_file(mode=0o600)
        env = dict(os.environ, PATH='/nonexistent-bin')
        script = lock_step(str(path), '')
        completed = subprocess.run(['/bin/sh', '-c', script], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, env=env)
        verdict = lock_verdict(completed.stdout, str(path))
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_UNKNOWN)
        self.assertEqual(verdict.reason, 'tool_missing')

    def test_an_unreadable_lock_file_is_unknown_not_free(self):
        path = self.lock_file(mode=0o000)
        os.chmod(str(self.root), 0o755)
        if os.geteuid() == 0:
            setpriv = shutil.which('setpriv')
            if setpriv is None:
                self.skipTest('setpriv(1) unavailable to drop privilege')
            privilege = (setpriv + ' --reuid=65534 --regid=65534 --clear-groups')
        else:
            privilege = ''
        completed = subprocess.run(['/bin/sh', '-c', lock_step(str(path), privilege)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True)
        verdict = lock_verdict(completed.stdout, str(path))
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_UNKNOWN)
        self.assertEqual(verdict.reason, 'open_failed')

    def test_an_unavailable_privilege_is_unknown_not_free(self):
        path = self.lock_file()
        shim = self.root / 'shim'
        shim.mkdir()
        (shim / 'sudo').write_text('#!/bin/sh\nexit 1\n')
        os.chmod(str(shim / 'sudo'), 0o755)
        env = dict(os.environ, PATH=str(shim) + ':' + os.environ.get('PATH', ''))
        completed = subprocess.run(['/bin/sh', '-c', lock_step(str(path), 'sudo -S -p ""')],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, env=env)
        verdict = lock_verdict(completed.stdout, str(path))
        self.assertIs(verdict.ok, False)
        self.assertEqual(verdict.state, LOCK_UNKNOWN)
        self.assertEqual(verdict.reason, 'privileged_probe_unavailable')


class TestCampaign2Evidence(unittest.TestCase):
    """Read-only regression over the preserved 2026-10-18 FAIL transcript."""

    def setUp(self):
        if not CAMPAIGN2_TRANSCRIPT.is_file():
            self.skipTest('campaign2 transcript not present')
        self.text = CAMPAIGN2_TRANSCRIPT.read_text(encoding='utf-8', errors='replace')

    def test_the_preserved_transcript_really_is_the_legacy_format(self):
        self.assertIn('LOCK_PRESENT', self.text)
        self.assertNotIn('LOCK_FREE', self.text)
        self.assertNotIn('LOCK_HELD', self.text)
        self.assertNotIn('LOCK_UNKNOWN', self.text)

    def test_the_old_predicate_false_alarms_on_it(self):
        """Documents the defect: the file exists, so the old probe said FAIL."""
        self.assertIs(('LOCK_ABSENT' in self.text), False)
        self.assertTrue('/var/lock/rf-deployment-trial.lock' in self.text)

    def test_the_new_verdict_reports_not_evaluated_instead_of_guessing(self):
        verdict = lock_verdict(self.text, LOCK_PATH)
        self.assertIsNone(verdict.ok)
        self.assertEqual(verdict.state, NOT_EVALUATED)
        self.assertEqual(verdict.reason, 'legacy_transcript_without_ownership_verdict')

    def test_the_same_situation_in_the_fixed_format_is_free(self):
        """File present + no holder, as the fixed probe records it."""
        verdict = lock_verdict('==LOCK==\nLOCK_PRESENT\n' + PRESENT_UNHELD.split('\n', 2)[2])
        self.assertIs(verdict.ok, True)
        self.assertEqual(verdict.state, LOCK_FREE)


class TestPreflightWiring(unittest.TestCase):
    """The three in-place preflight probes must use the shared verdict."""

    def source(self, path):
        self.assertTrue(path.is_file(), str(path))
        return path.read_text(encoding='utf-8')

    def test_no_probe_still_judges_the_lock_by_existence(self):
        for path in PROBES:
            source = self.source(path)
            self.assertNotIn("ok='LOCK_ABSENT' in paths_text", source, path.name)
            self.assertNotIn("items['B10_lock_absent']", source, path.name)

    def test_each_probe_imports_and_calls_the_shared_verdict(self):
        for path in PROBES:
            source = self.source(path)
            self.assertIn('from scripts.rf_deployment_lock_probe import', source, path.name)
            self.assertIn("items['B10_lock_free'] = lock_item(paths_text)", source, path.name)
            self.assertIn("PATHS = PATHS.replace('@LOCK_STEP@', lock_step())", source, path.name)
            self.assertIn('@LOCK_STEP@', source, path.name)

    def test_the_probe_still_stops_on_a_not_evaluated_item(self):
        """ok=None must remain a stop: no silent pass for an unverifiable log."""
        for path in PROBES:
            source = self.source(path)
            self.assertIn("if item.get('ok') is not True", source, path.name)

    def test_the_imported_probe_judges_with_the_shared_verdict(self):
        for path in PROBES:
            module = load_probe(path)
            self.assertTrue(hasattr(module, 'lock_item'), path.name)
            free = module.lock_item(PRESENT_UNHELD)
            self.assertIs(free['ok'], True, path.name)
            self.assertEqual(free['state'], LOCK_FREE, path.name)
            held = module.lock_item(PRESENT_HELD)
            self.assertIs(held['ok'], False, path.name)
            self.assertEqual(held['state'], LOCK_HELD, path.name)
            legacy = module.lock_item('==LOCK==\nLOCK_PRESENT\nLOCK_DONE\n')
            self.assertIsNone(legacy['ok'], path.name)
            self.assertEqual(legacy['state'], NOT_EVALUATED, path.name)

    def test_the_imported_probe_carries_the_privileged_step(self):
        for path in PROBES:
            module = load_probe(path)
            self.assertIn('echo ==LOCK==', module.PATHS, path.name)
            self.assertIn('flock -n 9', module.PATHS, path.name)
            self.assertIn('LOCK_UNKNOWN_REASON=privileged_probe_unavailable', module.PATHS,
                          path.name)
            self.assertIn(LOCK_PATH, module.PATHS, path.name)
            self.assertNotIn('@LOCK_STEP@', module.PATHS, path.name)

    def test_the_imported_probe_reports_not_evaluated_on_the_real_fail_log(self):
        if not CAMPAIGN2_TRANSCRIPT.is_file():
            self.skipTest('campaign2 transcript not present')
        text = CAMPAIGN2_TRANSCRIPT.read_text(encoding='utf-8', errors='replace')
        for path in PROBES:
            module = load_probe(path)
            item = module.lock_item(text)
            self.assertIsNone(item['ok'], path.name)
            self.assertEqual(item['state'], NOT_EVALUATED, path.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
