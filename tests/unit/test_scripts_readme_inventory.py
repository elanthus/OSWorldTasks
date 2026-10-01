"""scripts/README.md lists exactly the scripts that exist, once each."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_rows_match_scripts_directory() -> None:
    text = (ROOT / "scripts/README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+\.py)` \|", text, flags=re.MULTILINE)
    assert len(rows) == len(set(rows)), "duplicate script rows"
    present = {path.name for path in (ROOT / "scripts").glob("*.py") if path.name != "__init__.py"}
    assert set(rows) == present
