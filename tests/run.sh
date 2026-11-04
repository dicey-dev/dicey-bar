#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B scripts/version.py check
