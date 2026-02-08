"""Shared test fixtures for Cadence."""

import sys
from pathlib import Path

# Ensure src/backend/ is on the path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "backend"))
