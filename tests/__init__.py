"""Test tree. Present only so `tests.support` is importable as a package from
the repository root (`scripts/golden_trajectory.py` and the unit suite both
import it). `[tool.setuptools.packages.find]` includes only `pixelgym*`, so
nothing here is ever packaged or installed.

There is deliberately no `__init__.py` in `tests/unit/` or `tests/integration/`,
so pytest imports those test modules by bare basename with rootdir-relative
path insertion.
"""
