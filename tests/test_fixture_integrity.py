"""Runs last, and asserts the shared corpus is still the one it started as.

`tests/corpus_fixture.py` caches one built corpus and hands the same object to
every module that reads rows. That is fast and it is only safe while nothing
mutates it. Nothing does today. The invariant is worth a check rather than a
comment, because the first module that edits a row in place would break several
unrelated checks in ways that read as defects in the code under test.
"""

from __future__ import annotations

import hashlib
import json

from gross_margin import discrepancies
from gross_margin.generate import build
from tests.corpus_fixture import corpus


def _digest(built) -> str:
    payload = json.dumps(built.tables(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def check_the_shared_corpus_was_not_mutated_by_an_earlier_module():
    fresh = build()
    discrepancies.apply(fresh)
    if _digest(corpus(True)) != _digest(fresh):
        raise AssertionError(
            "the cached corpus no longer matches a fresh build at the same seed, so "
            "an earlier check edited rows other checks are reading"
        )


def check_the_shared_clean_corpus_was_not_mutated_either():
    fresh = build()
    if _digest(corpus(False)) != _digest(fresh):
        raise AssertionError("the cached clean corpus was edited in place")
