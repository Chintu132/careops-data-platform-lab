"""Run every check in one go: python scripts/check.py

Same steps the GitHub workflow runs. Nothing here touches AWS.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STEPS = [
    [sys.executable, "-m", "compileall", "-q", "src", "run.py", "tests", "scripts"],
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    [sys.executable, "run.py", "demo"],
    [sys.executable, "run.py", "replay-check"],
]


def main() -> int:
    for step in STEPS:
        print(f"\n$ {' '.join(step[1:])}", flush=True)
        result = subprocess.run(step, cwd=ROOT)
        if result.returncode != 0:
            print("\nFAILED at the step above.")
            return result.returncode
    print("\nPASS: all checks finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
