"""The API contract is pinned: any change to it shows up as a snapshot diff.

FastAPI derives the schema from signatures, so a refactor can change the API
without anyone meaning to (a return annotation becomes a response model, say).
If the change is intended, regenerate the snapshot and commit it with the PR:

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_openapi.py
"""

import json
import os
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).parent / "openapi.json"


def test_openapi_schema_matches_snapshot():
    current = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(current)
    assert SNAPSHOT.exists(), "no snapshot yet: run with UPDATE_SNAPSHOTS=1"
    assert current == SNAPSHOT.read_text(), (
        "the API schema changed; if that's intended, run with UPDATE_SNAPSHOTS=1"
    )
