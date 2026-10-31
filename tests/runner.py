"""A small collector so the suite needs no test framework in requirements.

Every module here exposes functions named `check_*`. They raise on failure and
return nothing on success.

Three rules the collector itself has to follow.

It refuses to exit zero having run nothing. A runner that reports success on an
empty collection turns every later mistake into a silent pass.

It treats an error during import as a failure rather than letting it escape. A
module that fails to import contributes zero checks, and zero checks that all pass
is the same output as a clean run.

It prints how many checks it ran. A count is the only way a reader can tell a
clean run from a run that found nothing to look at.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULES = [
    "tests.test_calendar",
    "tests.test_money",
    "tests.test_registry",
    "tests.test_generate",
    "tests.test_discrepancies",
    "tests.test_lag",
    "tests.test_manifest",
    "tests.test_infra",
    "tests.test_transform",
    "tests.test_allocation",
    "tests.test_ledger",
    "tests.test_recon",
    "tests.test_reopen",
    "tests.test_access",
    "tests.test_readme",
    # Last on purpose. It checks that nothing above it edited the shared corpus.
    "tests.test_fixture_integrity",
]


def collect(module_names=None):
    found = []
    broken = []
    for name in module_names or MODULES:
        try:
            mod = importlib.import_module(name)
        except BaseException:
            broken.append((name, traceback.format_exc()))
            continue
        for attr in sorted(dir(mod)):
            if attr.startswith("check_"):
                found.append((name, attr, getattr(mod, attr)))
    return found, broken


def run(module_names=None) -> int:
    cases, broken = collect(module_names)
    failures = []
    for module, name, fn in cases:
        try:
            fn()
        except BaseException:
            failures.append((module, name, traceback.format_exc()))

    for module, tb in broken:
        print(f"IMPORT FAILED {module}\n{tb}")
    for module, name, tb in failures:
        print(f"FAIL {module}.{name}\n{tb}")

    print(f"{len(cases) - len(failures)} of {len(cases)} checks passed")
    if not cases:
        print("no checks were collected, which is a failure and not a clean run")
        return 2
    if broken:
        return 2
    return 1 if failures else 0
