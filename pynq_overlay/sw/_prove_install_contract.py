#!/usr/bin/env python3
"""Red-green proof for the merged install-path contract.

Three mutations of the artefacts under test, applied in place with byte-exact restores
verified by md5:

  M1  the sync moves AFTER the staging verification  (the page-cache defect)
  M2  the read-only chmod moves AFTER the publish    (the writable-release defect)
  M3  the panel's `-B` is removed                    (bytecode written into the release)

Each must turn the matching criterion red; the suite is green again after restore.
"""
import hashlib
import os
import subprocess
import sys

WT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", ".."))
INSTALL = os.path.join(WT, "pynq_overlay", "sw", "install.sh")
UNIT = os.path.join(WT, "pynq_overlay", "sw", "pynq-status.service")
TEST = os.path.join("pynq_overlay", "sw", "tests", "test_merged_install_contract.py")

M1_FROM = 'sync\npython3 "$STAGE/merged_release.py" "$STAGE" >/dev/null\n'
M1_TO = 'python3 "$STAGE/merged_release.py" "$STAGE" >/dev/null\nsync\n'
M2_FROM = 'chmod -R a-w "$STAGE"\nmv "$STAGE" "$RELEASE"\n'
M2_TO = 'mv "$STAGE" "$RELEASE"\nchmod -R a-w "$RELEASE"\n'
M3_FROM = "/usr/bin/python3 -B -u "
M3_TO = "/usr/bin/python3 -u "


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def contract():
    p = subprocess.run([sys.executable, "-m", "pytest", TEST, "-q"],
                       cwd=WT, capture_output=True, text=True)
    out = p.stdout + p.stderr
    for line in out.splitlines():
        if "passed" in line or "failed" in line:
            return "%s | rc=%d" % (line.strip(), p.returncode)
    return "rc=%d" % p.returncode


def apply(path, frm, to):
    data = open(path, "rb").read()
    text = data.decode("utf-8")
    assert frm in text, "pattern not found in %s" % os.path.basename(path)
    open(path, "wb").write(text.replace(frm, to, 1).encode("utf-8"))


def main():
    originals = {path: open(path, "rb").read() for path in (INSTALL, UNIT)}
    before = {path: md5(path) for path in originals}

    print("baseline                            %s" % contract())
    try:
        apply(INSTALL, M1_FROM, M1_TO)
        print("M1 sync moved after verification    %s" % contract())
        open(INSTALL, "wb").write(originals[INSTALL])

        apply(INSTALL, M2_FROM, M2_TO)
        print("M2 chmod moved after publish        %s" % contract())
        open(INSTALL, "wb").write(originals[INSTALL])

        apply(UNIT, M3_FROM, M3_TO)
        print("M3 panel -B removed                 %s" % contract())
        open(UNIT, "wb").write(originals[UNIT])
    finally:
        for path, data in originals.items():
            open(path, "wb").write(data)

    ok = True
    for path, digest in before.items():
        after = md5(path)
        state = "IDENTICAL" if after == digest else "CHANGED!"
        ok = ok and after == digest
        print("restored %-28s %s -> %s  %s"
              % (os.path.basename(path), digest, after, state))
    print("baseline after restore              %s" % contract())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
