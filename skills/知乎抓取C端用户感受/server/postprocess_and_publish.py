#!/usr/bin/env python3
"""Run mandatory Claude analysis before DingTalk publication."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main():
    subprocess.run([sys.executable, str(ROOT / "analyze_report.py")], check=True)
    subprocess.run([sys.executable, str(ROOT / "send_dingtalk.py")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
