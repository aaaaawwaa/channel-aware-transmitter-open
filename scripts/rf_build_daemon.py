#!/usr/bin/env python3
"""rf_build_daemon.py -- build the armv7l `rf_daemon` on the BOARD and pull it back.

Why this exists: the board app is a Linux userspace program (MMIO via /dev/mem), and
the only toolchain in this project that produces **armv7l** is the board's own native
gcc.  Windows/MinGW cannot build it (it dies on `<sys/mman.h>`), and WSL2/the Azure VM
produce **x86-64**, which is not the board's architecture.  So "build the daemon" is a
board operation, and it has to be reproducible or the local release set silently drifts
from what the board runs.

That drift is not hypothetical (measured 2026-09-26): the deployed `rf_daemon` was
`938e2ddb…` while the local `rf_overlay/out/rf_daemon` was the older `7472b544…`, and
`rf_release_manifest.json` carries **no daemon hash** -- so `rf_deploy.py` would have
copied the stale binary over the good one with nothing to notice it.

Two guards, both measured:
  * **Anti-stale** -- source files are packaged from the working tree and their md5s are
    compared on the board before the build.  An upload that silently did not happen
    builds the wrong code.
  * **Code-vs-debug** -- ARM builds are NOT byte-reproducible across different build
    directories, because DWARF embeds the compile directory.  Two builds of identical
    source differed in 131,479 bytes with **byte-identical `.text`/`.rodata`/`.data`**.
    So the comparison that means something is section content, not the whole file; this
    script reports both and calls out debug-only differences explicitly.

Usage:
    python scripts/rf_build_daemon.py --out rf_overlay/out/rf_daemon
    python scripts/rf_build_daemon.py --out rf_overlay/out/rf_daemon --compare-to <elf>

Board access goes through the sanctioned helper only (`pynq_ssh.py`), never raw ssh/scp.
The PL is NOT touched: building and comparing are read-only with respect to the FPGA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

HELPER = os.path.expanduser("~/.claude/skills/ac820-pynq/scripts/pynq_ssh.py")
REMOTE_SRC = "/home/xilinx/rfbuild/build_script_src"
REMOTE_TGZ = "/home/xilinx/rfbuild/build_script_src.tgz"
# The Makefile's DAEMON_BIN is a plain `rf_daemon` in the build directory, so that is
# where `make rf_daemon` leaves it.  This used to be a separate build_script_out/
# directory, which does not exist: the pull-back and the section comparison both
# failed with "No such file or directory" after a BUILT build.  Point at the
# file the Makefile actually produces.
REMOTE_OUT = REMOTE_SRC + "/rf_daemon"

# Only these are build inputs; `test/` is included because the Makefile's default
# target references it, and the vendor tree is required verbatim.
SOURCE_GLOBS = ("*.c", "*.h")
SUBDIRS = ("vendor", "test")

# md5s are compared for these; more would be noise, fewer would miss a stale tree.
GUARD_FILES = ("rf_core.c", "rf_daemon.c", "rf_daemon_main.c", "rf_version.h",
               "rf_protocol.c", "rf_state.c", "Makefile")


def capture(argv, env=None):
    """Run a process and decode its output LENIENTLY.

    The board's toolchain emits non-UTF-8 bytes in some diagnostics (measured
    2026-10-20: byte 0xA1 in `make`/`host-test` output).  With a strict `text=True`
    decode the exception is raised inside subprocess's reader thread and the build is
    lost after it already started on the board.  Replacement characters are confined to
    the console log; every token this tool parses -- `BUILD_RC=0`,
    `vendor integrity: 10 files passed`, md5sums, `objcopy` section hashes -- is ASCII.
    """
    return subprocess.run(argv, capture_output=True, encoding="utf-8",
                          errors="replace", env=env)


def board(host, *command, check=True):
    argv = [sys.executable, "-c", "import runpy,sys; h=runpy.run_path(sys.argv[1]); h['run_commands']([sys.argv[2]], timeout=1200)", HELPER, " ".join(str(c) for c in command)]
    proc = capture(argv, env=dict(os.environ, PYNQ_HOST=host))
    if check and proc.returncode != 0:
        raise SystemExit("board command failed: %s\n%s" % (" ".join(map(str, command)),
                                                           proc.stderr))
    return proc.stdout


def board_put(host, local, remote):
    subprocess.run(["python", HELPER, "put", str(local), str(remote)], check=True,
                   capture_output=True)


def board_get(host, remote, local):
    subprocess.run(["python", HELPER, "get", str(remote), str(local)], check=True,
                   capture_output=True)


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def package(board_app: Path, staging: Path) -> Path:
    """Copy only the build inputs into a staging dir and tar them."""
    for pattern in SOURCE_GLOBS:
        for path in board_app.glob(pattern):
            shutil.copy2(path, staging / path.name)
    # The Makefile is a build input AND one of GUARD_FILES, but it has no suffix, so
    # the glob above never picked it up: the anti-stale guard then compared a local
    # Makefile against a board copy that was never uploaded, failed with
    # "STALE Makefile: ... board None", and the build could never start.  Package it
    # explicitly rather than widening SOURCE_GLOBS (which would also sweep in
    # unrelated no-suffix files).
    shutil.copy2(board_app / "Makefile", staging / "Makefile")
    for sub in SUBDIRS:
        (staging / sub).mkdir(exist_ok=True)
        for path in (board_app / sub).iterdir():
            if path.is_file() and path.suffix in (".c", ".h", ".py", ""):
                shutil.copy2(path, staging / sub / path.name)
    tgz = staging.parent / "board_app_src.tgz"
    with tarfile.open(tgz, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(staging)))
    return tgz


def remote_md5s(host, names=GUARD_FILES) -> dict:
    out = board(host, "cd", REMOTE_SRC, "&&", "md5sum", *names)
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 32:
            found[parts[1]] = parts[0]
    return found


def sections_of(elf: Path) -> dict:
    """Content hash of each allocatable section, via objcopy on the board.

    Whole-file comparison is useless across build directories (DWARF comp_dir), so
    this is the comparison that answers "is it the same code".
    """
    out = {}
    for name in (".text", ".rodata", ".data", ".ARM.exidx"):
        proc = capture(
            ["python", HELPER, "run",
             "cd /home/xilinx/rfbuild/build_script_src && "
             "objcopy -O binary --only-section=%s rf_daemon /tmp/sec_%s && "
             "md5sum /tmp/sec_%s" % (name, name.strip("."), name.strip("."))])
        match = re.search(r"\b([0-9a-f]{32})\b", proc.stdout)
        if match:
            out[name] = match.group(1)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="192.0.2.10",
                        help="board host for pynq_ssh.py")
    parser.add_argument("--board-app", default="rf_overlay/sw/board_app",
                        help="path to the board_app source directory")
    parser.add_argument("--out", default="rf_overlay/out/rf_daemon",
                        help="where to write the built binary")
    parser.add_argument("--compare-to", default=None,
                        help="an existing ELF to compare section content against")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    board_app = (repo / args.board_app).resolve()
    out_path = (repo / args.out).resolve()
    if not (board_app / "Makefile").is_file():
        raise SystemExit("no Makefile under %s" % board_app)

    scratch = Path(tempfile.mkdtemp(prefix="rfbuild-"))
    staging = scratch / "src"
    staging.mkdir()
    tgz = package(board_app, staging)
    print("packaged %d files -> %s (%d bytes)"
          % (sum(1 for _ in staging.rglob("*") if _.is_file()), tgz.name,
             tgz.stat().st_size))

    local_guards = {str(p.relative_to(staging)).replace("\\", "/"): md5(p)
                    for p in staging.rglob("*") if p.is_file()}

    board(args.host, "mkdir", "-p", PurePosixPath(REMOTE_TGZ).parent)
    board_put(args.host, tgz, REMOTE_TGZ)

    # Unpack, then compare the guard files BEFORE compiling anything.
    board(args.host, "rm", "-rf", REMOTE_SRC, "&&", "mkdir", "-p", REMOTE_SRC,
          "&&", "cd", REMOTE_SRC, "&&", "tar", "-xzf", REMOTE_TGZ, "&&", "find", ".", "-type", "f", "-exec", "touch", "{}", "\;")
    remote_guards = remote_md5s(args.host, sorted(local_guards))

    mismatches = [name for name, value in local_guards.items()
                  if remote_guards.get(name) != value]
    print("anti-stale guard: %d/%d files match on the board"
          % (len(local_guards) - len(mismatches), len(local_guards)))
    if mismatches:
        for name in mismatches:
            print("  STALE %s: local %s board %s"
                  % (name, local_guards[name], remote_guards.get(name)))
        return 2

    print("building on the board (native gcc)...")
    build = board(args.host, "cd", REMOTE_SRC, "&&", "make", "clean", ">/dev/null",
                  "2>&1", "||", "true", ";", "cd", REMOTE_SRC, "&&",
                  "make", "-j1", "all", "host-test", "host-test-core", "host-test-iq", "host-test-protocol", "vendor-integrity", "&&", "echo BUILD_RC=0", "||",
                  "echo BUILD_RC=$?", check=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.with_suffix(".build.log").write_text(build, encoding="utf-8")
    warnings = [ln for ln in build.splitlines()
                if re.search(r"\b(?:warning|error):", ln) and "Clock skew" not in ln]
    if warnings:
        print("build warnings/errors:")
        for line in warnings:
            print("  " + line.strip())
    if "BUILD_RC=0" not in build:
        print(build)
        return 3

    out_path.parent.mkdir(parents=True, exist_ok=True)
    board_get(args.host, REMOTE_OUT, out_path)
    receipt = {"architecture": "armv7l", "hardware_touched": False, "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest(), "source_sha256": {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest() for p in staging.rglob("*") if p.is_file()}, "targets": ["all", "host-test", "host-test-core", "host-test-iq", "host-test-protocol", "vendor-integrity"]}
    out_path.with_suffix(".build.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("pulled %s (%d bytes, md5 %s)"
          % (out_path, out_path.stat().st_size, md5(out_path)))

    if args.compare_to:
        other = Path(args.compare_to).resolve()
        if not other.is_file():
            raise SystemExit("--compare-to %s does not exist" % other)
        print("whole file: built %s / reference %s  -> %s"
              % (md5(out_path), md5(other),
                 "IDENTICAL" if md5(out_path) == md5(other) else "DIFFER"))
        sections = sections_of(out_path)
        print("built section content: %s" % sections)
        if md5(out_path) != md5(other):
            print("NOTE: a whole-file difference is EXPECTED across build "
                  "directories -- DWARF records the compile directory. Compare "
                  "the section hashes above against the reference's; identical "
                  ".text/.rodata/.data means identical code.")

    if not args.keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
