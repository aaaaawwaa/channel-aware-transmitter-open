"""Offline proof that the 12-frame telemetry upload set covers its import closure.

The 2026-10-19 single authorized run
(evidence/rf-teaching/s2-46db-telemetry-12-2026-10-19) died before the first session
existed with "ModuleNotFoundError: No module named 'scripts'": SOURCES in
scripts/rf_environment_telemetry_run.py omitted rf_environment_pilot.py, while
rf_environment_board_session.py imports it at module import time, and
telemetry_session_class()'s workstation fallback replaced the real first error with its
own.  Both halves are pinned here, host-only and zero-board:

1. the upload set must carry every sibling module that the uploaded modules import at
   module import time (closure derived statically with ast -- no board module is
   imported, no socket, no Overlay);
2. the closure check is not vacuous: it is shown to flag the missing pilot when that
   source is removed, so this file is red on the defect;
3. telemetry_session_class() must report the original ModuleNotFoundError (or chain it
   explicitly) instead of the fallback's error, reproduced in a clean directory with
   "python -S" exactly like the board's upload directory.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _entry in (str(ROOT), str(ROOT / 'scripts')):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from scripts import rf_environment_telemetry_run as RUN    # noqa: E402
from scripts import rf_environment_telemetry_session as T  # noqa: E402

SCRIPTS = ROOT / 'scripts'
CONVICTED_MISSING = 'rf_environment_pilot.py'
CONVICTED_MODULE = 'rf_environment_pilot'

BOARD_LIKE_PROBE = r'''
import json
import rf_environment_telemetry_session as T
try:
    session_class = T.telemetry_session_class()
except BaseException as exc:                       # report, never mask, never re-raise
    cause = exc.__cause__
    print(json.dumps(dict(ok=False, error_type=type(exc).__name__, error=str(exc),
                          cause_type=None if cause is None else type(cause).__name__,
                          cause=None if cause is None else str(cause))))
else:
    print(json.dumps(dict(ok=True, name=session_class.__name__)))
'''


def _is_main_guard(test):
    """True for the top-level if __name__ == '__main__': statement."""
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == '__name__' and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == '__main__')


def module_level_imports(path):
    """Top-level module names imported while the module is being imported.

    Function/lambda bodies are skipped (they do not run at import time).  The body of
    the __main__ guard is skipped as well: the telemetry run imports the session modules
    as a library, so their CLI block never executes.  Everything else at module level --
    including imports inside try/except and other if/with/for -- does run on import and
    is therefore collected.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names = set()

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            pass

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_Lambda(self, node):
            pass

        def visit_If(self, node):
            if _is_main_guard(node.test):
                return
            self.generic_visit(node)

        def visit_Import(self, node):
            for alias in node.names:
                names.add(alias.name.split('.')[0])

        def visit_ImportFrom(self, node):
            if node.level == 0 and node.module:
                names.add(node.module.split('.')[0])

    Visitor().visit(tree)
    return names


def closure_gaps(sources, scripts=SCRIPTS):
    """(importer, missing) pairs the upload set does not carry.

    A name is a closure member only when it resolves to a sibling scripts/<name>.py:
    stdlib, product/board modules on the board's own PYTHONPATH and the scripts package
    prefix itself are not upload-set members.  A declared source that is not on disk at
    all is reported as (name, name).
    """
    uploaded = set(sources)
    seen = set()
    pending = [(name, scripts / name) for name in sources]
    gaps = []
    while pending:
        name, path = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if not path.is_file():
            gaps.append((name, name))
            continue
        for imported in sorted(module_level_imports(path)):
            sibling = scripts / (imported + '.py')
            if not sibling.is_file():
                continue
            if sibling.name not in uploaded:
                gaps.append((path.name, sibling.name))
            else:
                pending.append((sibling.name, sibling))
    return tuple(sorted(gaps))


def _board_like(directory, sources):
    """A clean directory holding exactly the upload set, byte for byte."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in sources:
        (directory / name).write_bytes((SCRIPTS / name).read_bytes())
    return directory


def _run_board_like_probe(directory):
    """Import the uploaded set exactly as the board does: cwd on sys.path, no site."""
    completed = subprocess.run(
        [sys.executable, '-S', '-c', BOARD_LIKE_PROBE], cwd=str(directory),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
    return json.loads(completed.stdout.decode('utf-8').strip().splitlines()[-1])


# ------------------------------------------------- 1. upload-set closure coverage

def test_the_upload_set_carries_the_convicted_missing_module():
    assert CONVICTED_MISSING in RUN.SOURCES


def test_the_upload_set_covers_the_module_level_import_closure():
    gaps = closure_gaps(RUN.SOURCES)
    assert gaps == (), (
        'the upload set does not cover the module-level import closure of the modules '
        'it uploads; the board would die on import: %r' % (gaps,))


def test_the_closure_check_is_not_vacuous_it_flags_the_missing_pilot():
    """The negative control: remove the just-added source and the check must go red."""
    without = tuple(name for name in RUN.SOURCES if name != CONVICTED_MISSING)
    assert CONVICTED_MISSING not in without
    gaps = closure_gaps(without)
    assert (CONVICTED_MISSING, CONVICTED_MISSING) not in gaps   # it is on disk here
    assert ('rf_environment_board_session.py', CONVICTED_MISSING) in gaps


def test_the_closure_does_not_require_imports_inside_the_main_guard():
    # rf_environment_board_session imports rf_environment_acceptance only inside its
    # __main__ guard; the telemetry run imports that module as a library, so the CLI-only
    # import never executes and is not an upload-set member.
    assert 'rf_environment_acceptance.py' not in RUN.SOURCES
    assert closure_gaps(RUN.SOURCES) == ()


# ------------------------------------------------- 2. the fallback must not mask

def test_fallback_reports_the_original_import_error(tmp_path):
    without = tuple(name for name in RUN.SOURCES if name != CONVICTED_MISSING)
    probe = _run_board_like_probe(_board_like(tmp_path / 'board-without-pilot', without))
    assert probe['ok'] is False
    assert probe['error_type'] == 'ModuleNotFoundError'
    assert CONVICTED_MODULE in probe['error'], (
        'the real first error was replaced by the workstation fallback: %r' % (probe,))
    assert probe['cause'] is not None and 'scripts' in probe['cause'], (
        'the failed fallback is not chained as the explicit cause: %r' % (probe,))


def test_the_declared_upload_set_imports_cleanly_on_a_board_like_directory(tmp_path):
    probe = _run_board_like_probe(_board_like(tmp_path / 'board-with-upload-set',
                                              RUN.SOURCES))
    assert probe['ok'] is True, probe
    assert probe['name'] == 'TelemetrySession'


# ------------------------------------------------- 3. the declared sources are real

def test_every_declared_source_exists_and_is_hashed_by_the_declaration():
    for name in RUN.SOURCES:
        assert (SCRIPTS / name).is_file(), name
    assert len(set(RUN.SOURCES)) == len(RUN.SOURCES)
    assert T.APPROVED_RELEASE == '03b9b883a61f29605be2c1f3817024d0'
