"""Start here: python run.py demo

No install needed for the local pipeline. It only uses the Python standard library.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from careops.cli import main  # noqa: E402

if __name__ == "__main__":
    main(root=ROOT)
