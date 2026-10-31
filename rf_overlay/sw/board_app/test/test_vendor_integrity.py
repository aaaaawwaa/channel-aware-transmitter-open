"""Verify the checked-in vendor sources without rewriting line endings."""
import hashlib
from pathlib import Path

vendor = Path(__file__).resolve().parents[1] / "vendor"
entries = (vendor / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
count = 0
for entry in entries:
    if not entry.strip() or entry.lstrip().startswith("#"):
        continue
    count += 1
    expected, name = entry.split(None, 1)
    path = vendor / name.strip().lstrip("*")
    assert path.resolve().parent == vendor.resolve(), name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == expected, "vendor digest mismatch: " + name
print("vendor integrity: %d files passed" % count)
