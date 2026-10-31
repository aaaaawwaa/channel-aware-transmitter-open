"""Compatibility entry point; release verification has one canonical implementation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pynq_overlay.sw.merged_release import *

if __name__ == "__main__":
    raise SystemExit(main())
