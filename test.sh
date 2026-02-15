#!/usr/bin/env bash
set -euo pipefail

# Run local unit tests for bms parsing/protocol logic.
python3 -m unittest discover -s tests -p 'test_*.py' -v
