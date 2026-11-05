#!/usr/bin/env python3
"""Offline public retrieval gate using fictional test fixtures, not a SAP corpus benchmark."""
import subprocess
import sys
from pathlib import Path
if __name__ == '__main__':
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q',
        'tests/test_build_index.py', 'tests/test_tokenize.py'], cwd=Path(__file__).resolve().parents[1]))
