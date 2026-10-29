"""One corpus, built once, shared by every module that needs rows.

Built in memory rather than read from `data/`. That directory is gitignored, so a
suite that read it would verify nothing on a fresh clone and would pass, which is
the normal case for a cloner rather than an edge one. Building it here costs a
fraction of a second and means the suite is always running against a corpus that
has just been made from the committed code.
"""

from __future__ import annotations

import functools

from gross_margin import discrepancies, ledger, lag
from gross_margin.generate import build


def fresh(with_defects: bool = True):
    """The construction, uncached and in one place.

    `corpus` caches this and `tests/test_fixture_integrity.py` compares against
    it, so the two cannot drift. They did drift once, on the day the ledger was
    added: the integrity check built its comparison corpus by hand, the hand
    built one had no ledger, and two checks failed naming a mutation that had not
    happened.
    """
    built = build()
    if with_defects:
        discrepancies.apply(built)
        ledger.post(built)
        ledger.inject(built)
    else:
        built.defects = {"requested": {}, "achieved": {}}
        ledger.post(built)
        built.gl_defects = {"requested": {}, "achieved": {}}
    return built


@functools.lru_cache(maxsize=2)
def corpus(with_defects: bool = True):
    # READ ONLY. Every caller gets the same object, so editing a row here changes
    # what unrelated checks see. A check that needs to mutate builds its own.
    # `tests/test_fixture_integrity.py` runs last and asserts this held.
    return fresh(with_defects)


@functools.lru_cache(maxsize=1)
def records():
    built = corpus(True)
    found, unmatched = lag.lag_records(built.invoices, built.credits)
    return found, unmatched
