"""Offline test for the lenient capture used by the board build driver.

The board's toolchain emits non-UTF-8 bytes in diagnostics (measured 2026-10-20: byte
0xA1 in `make` output).  A strict `text=True` decode raised inside subprocess's reader
thread and lost a build that had already started on the board.  These tests run REAL local
processes (no board) that emit such bytes and assert the two things that matter:

* no exception, output is text with replacement characters;
* every token the driver parses is unchanged: `BUILD_RC=0`, `vendor integrity: 10 files
  passed`, md5sum lines and the 32-hex section hashes.
"""
import re
import sys

from scripts import rf_build_daemon as B

INVALID = b'gcc: warning \xa1\xa1 unexpected locale bytes\n'


def run_snippet(source):
    """Run a python snippet that writes raw bytes to stdout/stderr."""
    return B.capture([sys.executable, '-c', source])


def write_bytes_snippet(stdout=b'', stderr=b'', exit_code=0):
    parts = []
    if stdout:
        parts.append('sys.stdout.buffer.write(%r)' % (stdout,))
    if stderr:
        parts.append('sys.stderr.buffer.write(%r)' % (stderr,))
    if exit_code:
        parts.append('sys.exit(%d)' % exit_code)
    return 'import sys; ' + '; '.join(parts)


def test_non_utf8_stdout_does_not_raise_and_is_still_text():
    proc = run_snippet(write_bytes_snippet(INVALID + b'BUILD_RC=0\n'))
    assert proc.returncode == 0
    assert isinstance(proc.stdout, str)
    assert 'BUILD_RC=0' in proc.stdout
    assert '\ufffd' in proc.stdout, 'invalid bytes become replacement characters'
    assert proc.stderr == ''


def test_non_utf8_stderr_does_not_raise():
    proc = run_snippet(write_bytes_snippet(stderr=INVALID))
    assert proc.returncode == 0
    assert isinstance(proc.stderr, str)
    assert '\ufffd' in proc.stderr


def test_a_nonzero_exit_code_is_still_reported():
    proc = run_snippet(write_bytes_snippet(INVALID + b'BUILD_RC=2\n', exit_code=7))
    assert proc.returncode == 7
    assert 'BUILD_RC=2' in proc.stdout


def test_every_parsed_token_survives_a_dirty_log():
    """The exact tokens the build driver looks for, in a log full of invalid bytes."""
    payload = (b'gcc: \xa1\xa1\xa1\n'
               b'BUILD_RC=0\n'
               b'vendor integrity: 10 files passed\n'
               b'3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c  rf_core.o\n')
    proc = run_snippet("import sys; sys.stdout.buffer.write(%r)" % payload)
    lines = proc.stdout.splitlines()
    assert 'BUILD_RC=0' in lines
    assert 'vendor integrity: 10 files passed' in lines
    assert re.search(r'\b([0-9a-f]{32})\b', proc.stdout).group(1) == \
        '3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c'
    assert not [line for line in lines if 'BUILD_RC=0' not in line
                and 'vendor integrity' not in line
                and not re.search(r'\b[0-9a-f]{32}\b', line)
                and 'gcc:' not in line]


def test_the_driver_no_longer_uses_a_strict_text_decode():
    """No `subprocess.run(..., text=True)` may come back into the build driver."""
    import ast
    import inspect
    source = inspect.getsource(B)
    tree = ast.parse(source)
    strict = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', None) == 'run':
            for keyword in node.keywords:
                if keyword.arg == 'text':
                    strict.append(ast.dump(keyword.value))
    assert strict == [], 'a strict text decode must not come back: %s' % strict
    assert "errors=\"replace\"" in source or "errors='replace'" in source
