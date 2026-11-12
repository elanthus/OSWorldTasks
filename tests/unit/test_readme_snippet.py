"""The README's "Start here" usage snippet is executed, so it cannot drift from the code."""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"
_BLOCK = re.compile(
    r"<!-- readme-snippet:start -->\s*```python\n(?P<code>.*?)```\s*<!-- readme-snippet:end -->",
    re.DOTALL,
)
START_HERE_PATHS = (
    "pixelgym/env.py",
    "pixelgym/actions.py",
    "pixelgym/evaluator.py",
    "pixelgym/task_spec.py",
    "pixelgym/backends/",
)


def _snippet() -> str:
    matches = _BLOCK.findall(README.read_text(encoding="utf-8"))
    assert len(matches) == 1, "README must contain exactly one marked usage snippet"
    return str(matches[0])


def test_snippet_is_eight_lines() -> None:
    assert len(_snippet().splitlines()) == 8


def test_snippet_runs() -> None:
    exec(compile(_snippet(), str(README), "exec"), {"__name__": "readme_snippet"})  # noqa: S102


def test_start_here_names_the_core_files_and_they_exist() -> None:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Start here")
    section = text[start : text.index("\n## ", start + 1)]
    root = README.parent
    for path in START_HERE_PATHS:
        assert f"({path})" in section, path
        assert (root / path).exists(), path
