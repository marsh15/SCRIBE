"""Vercel Python entry. Keep thin: the app lives in intelligence/src/scribe_intel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "intelligence" / "src"))

from scribe_intel.app import app  # noqa: E402,F401
