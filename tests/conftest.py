"""Test setup: a throwaway data directory and no network services.

The county profile is chosen at import time (COUNTY, default from config.py),
so county-specific cases are skipped when another county is loaded. CI runs
the suite once per county profile in the repo.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="dispatch-test-"))
os.environ.setdefault("BUNDLE_DIR", os.path.join(os.environ["DATA_DIR"], "no-bundle"))
os.environ.setdefault("OLLAMA_URL", "http://127.0.0.1:9")      # nothing listens: LLM paths aren't used in tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from dispatch import config, db  # noqa: E402

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
db.init()


def county(key: str):
    """Mark a test as only meaningful for one county profile."""
    return pytest.mark.skipif(config.PROFILE.key != key, reason=f"needs COUNTY={key}")
