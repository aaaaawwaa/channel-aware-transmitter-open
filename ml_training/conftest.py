r"""pytest bootstrap: make the project root and ml_training importable.

Run tests from the project root, e.g.
    .\ml_training\.venv\Scripts\python.exe -m pytest ml_training\tests
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
