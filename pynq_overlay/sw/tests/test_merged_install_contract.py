"""Source contract for the merged release install path.

The install path is the one place where a power loss can turn a verified release into a
half-written one, so its ORDER is the property under test, not just the presence of the
commands.  Two defects motivated these criteria, both found by review on 2026-09-28:

* the stage was verified by re-reading it, but nothing forced the data out of the page
  cache first, so a failed/never-flushed write still verified and then activated -- the
  E9 incident's 0-byte files were exactly this script's outputs;
* the read-only chmod ran AFTER the atomic rename, so a crash in that window left a
  writable release that the existence check then refuses forever (the id is
  content-derived, so a retry hits the same refusal).

`pynq-status.service` runs as root and imports from the release, and root ignores the
mode bits: without `-B` a plain start writes `__pycache__` into an "immutable" release
(observed on the board: one appeared 7 minutes after the 22:22 install).
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]


def _lines(name):
    return (SRC / name).read_text(encoding="utf-8").splitlines()


def _first(lines, predicate, what):
    for index, line in enumerate(lines):
        if predicate(line.strip()):
            return index
    raise AssertionError("install.sh no longer contains: %s" % what)


def _sync_after(lines, start):
    """Index of the first bare `sync` at or after `start`, or None."""
    return next((index for index in range(start, len(lines))
                 if lines[index].strip() == "sync"), None)


def test_install_orders_sync_verification_immutability_and_activation():
    lines = _lines("install.sh")

    copies = _first(lines, lambda t: t.startswith('install -m 644 "$OVERLAY_SRC/$BIT_NAME"'),
                    "the bit copy into the staging tree")
    sync_before_verify = _first(lines, lambda t: t == "sync",
                                "a sync before the staging verification")
    verify = _first(lines, lambda t: '"$STAGE/merged_release.py"' in t and "--require-software" in t,
                    "the staging verification (with --require-software)")
    read_only = _first(lines, lambda t: t.startswith("chmod -R a-w"),
                       "the read-only chmod")
    publish = _first(lines, lambda t: t.startswith('mv "$STAGE" "$RELEASE"'),
                     "the atomic publish")
    flip = _first(lines, lambda t: 'mv -Tf "$NEXT_CURRENT" "$DEST/current"' in t,
                  "the atomic current flip")

    assert copies < sync_before_verify, (
        "the staging verification must run after a sync, or it verifies page cache")
    assert sync_before_verify < verify, (
        "the sync must precede the verification, not follow it")
    assert verify < read_only, "the staging tree must be verified before it is sealed"
    assert read_only < publish, (
        "the read-only chmod must happen BEFORE the rename: publishing first leaves a "
        "writable release that the existence check refuses forever after a crash")
    assert publish < flip, "the release must exist before current points at it"
    assert _sync_after(lines, flip) is not None, (
        "the activation is not durable: a sync after the current flip is required")


def test_install_cleans_up_a_failed_staging_tree():
    lines = _lines("install.sh")
    assert any(line.strip().startswith("trap ") and 'rm -rf "$STAGE"' in line
               for line in lines), (
        "a failed install leaves a half-written staging tree behind")


def test_panel_service_does_not_write_bytecode_into_the_release():
    lines = _lines("pynq-status.service")
    exec_lines = [line for line in lines if line.startswith("ExecStart=")]
    assert exec_lines, "pynq-status.service has no ExecStart"
    assert any("/usr/bin/python3" in line and " -B " in line for line in exec_lines), (
        "the panel must run with -B (or set PYTHONDONTWRITEBYTECODE): it runs as root, "
        "so the release's read-only mode bits do not stop __pycache__ being written "
        "into the verified release")


def test_staging_verification_requires_the_attested_software_set():
    """`--require-software` is the difference between "the files I copied are intact" and
    "every file this release claims to carry is here and intact".  Without it an install
    that silently dropped a panel module (the E9 incident's 0-byte copies) would still
    publish a release whose manifest lists a file the board does not have."""
    lines = _lines("install.sh")
    # match the python invocation, not the copy line that names the same destination
    verify = _first(lines, lambda t: t.startswith("python3 ") and '"$STAGE/merged_release.py"' in t,
                    "the staging verification")
    assert "--require-software" in lines[verify], (
        "the staging verification must pass --require-software")


def test_units_are_installed_from_the_verified_release_not_the_upload_staging():
    """The units decide what runs at boot, so the bytes that reach /etc/systemd/system
    must be the bytes the staged release attested.  Two wrong sources were tried on
    2026-09-29: `$SRC` (an unhashed upload copy, the pre-existing arrangement) and
    `$STAGE`, which the atomic publish has ALREADY renamed to `$RELEASE` by this point --
    that attempt failed on the board with "cannot stat .../.staging.XXXX/pynq-status.service".
    `$RELEASE` is both attested and immutable.

    Since 2026-09-30 the units do not travel through `install -m 644 src dest` at all;
    they go through `install_unit_atomically` (see the next test), so the SOURCE is named
    at the call site rather than on an `install` line.
    """
    lines = _lines("install.sh")
    active = [line.strip() for line in lines if not line.strip().startswith("#")]
    calls = [line for line in active if line.startswith("install_unit_atomically ")]
    assert len(calls) == 2, calls
    for line in calls:
        assert '"$RELEASE/' in line, (
            "a systemd unit is installed from outside the published release: %s" % line)
    assert any('"$RELEASE/pynq-status.service"' in line for line in calls)
    assert any('"$RELEASE/$SIDECAR_UNIT_NAME"' in line for line in calls)
    # ...and nothing writes a unit straight to its destination, which would bypass the
    # atomic publish.  That is exactly the shape this file had until 2026-09-30, so the
    # assertion stays: the bypass, not the fix, is what has to be justified.
    direct = [line for line in active
              if line.startswith("install ") and "/etc/systemd/system/" in line]
    assert direct == [], (
        "a unit is written straight to its destination, non-atomically: %s" % direct)
    # ...and both units are carried into the stage in the first place, or the flag below
    # would either fail every install or attest files that never existed.
    assert any(line.startswith("install -m 644") and '"$STAGE/install.sh"' in line
               for line in active)
    assert any('--require-software' in line for line in active)


def test_a_unit_is_published_atomically_from_a_proven_intact_temp_file():
    """The measured defect (2026-09-29, delivery record §37): a deploy interrupted between
    creating and filling a unit left a 0-BYTE /etc/systemd/system/*.service, and systemd
    reads an EMPTY unit as MASKED -- so the service died silently and `systemctl start`
    stopped having any effect at all.  `install -m 644 src dest` creates dest and then
    fills it, so that empty window is inherent to it.

    The cure is to fill a temp file in the destination DIRECTORY and then rename it, so
    the destination itself only ever changes by one atomic rename.  Ordering is the
    property under test, as elsewhere in this file: the temp must be flushed, then proven
    non-empty AND byte-identical to the release's copy, BEFORE it can become a unit; and
    the published file must be proven non-empty after.

    This is a source contract, not a runtime proof: it pins the script's shape and order.
    `LoadState=masked` itself can only be observed on the board.
    """
    lines = _lines("install.sh")
    active = [line.strip() for line in lines if not line.strip().startswith("#")]

    # The temp must live in the destination directory: a temp under /tmp would turn the
    # rename into a cross-device COPY, which is not atomic and reopens the same window.
    temp_decl = _first(active, lambda text: text.startswith("unit_tmp="),
                       "the unit temp path")
    assert '"$unit_dest.tmp.' in active[temp_decl], (
        "the unit temp file is not created beside its destination, so the publish cannot "
        "be an atomic rename: %s" % active[temp_decl])

    fill = _first(active,
                  lambda text: text.startswith("install ") and '"$unit_tmp"' in text,
                  "the copy into the unit temp file")
    flushed = _sync_after(active, fill)
    intact = _first(active,
                    lambda text: "! cmp -s " in text and '"$unit_tmp"' in text,
                    "the byte-identity check on the temp file")
    publish = _first(active,
                     lambda text: text.startswith("mv -f ") and '"$unit_tmp"' in text,
                     "the atomic rename of the temp file onto the unit")
    non_empty = _first(active,
                       lambda text: text.startswith("if [ ! -s ")
                       and '"$unit_dest"' in text,
                       "the non-empty check on the published unit")

    assert flushed is not None and flushed < intact, (
        "the temp is proven before it is flushed, so the proof reads the page cache")
    assert fill < intact < publish, (
        "the temp must be proven intact BEFORE it can become a unit, or a truncated "
        "write is activated: fill=%d intact=%d publish=%d" % (fill, intact, publish))
    assert publish < non_empty, (
        "the published unit must be proven non-empty after the rename")
