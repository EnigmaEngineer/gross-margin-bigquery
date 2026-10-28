"""The registry against what the build actually writes, from an empty directory.

This module is the answer to a defect found elsewhere in this portfolio. An
artifact entered a release registry without entering the script that builds it,
and a suite of nearly three hundred checks passed for two days, because every
machine running the suite already held the file on disk from the day it was made.
The defect is only observable from a directory that has never had the data.

So these checks build into a fresh temporary directory every time. The expensive
and durable version of this is a continuous integration job that builds from
empty, and that needs a workflow file this repo does not have yet.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from gross_margin.generate import build, write
from gross_margin.registry import ARTIFACTS, MANIFEST, expected_files
from tests.corpus_fixture import corpus


def check_every_registered_artifact_has_a_writer():
    """Static, and it costs nothing.

    Adding a name to the registry without adding it to `Corpus.tables` is the way
    the original defect happened. This fails on that edit alone, with no corpus
    and no files.
    """
    written = set(corpus(False).tables())
    registered = set(ARTIFACTS)
    missing = sorted(registered - written)
    extra = sorted(written - registered)
    if missing:
        raise AssertionError(f"registered with nothing to write them: {missing}")
    if extra:
        raise AssertionError(f"written but not registered: {extra}")


def check_a_build_into_an_empty_directory_produces_exactly_the_registry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "corpus"
        write(corpus(False), out)
        on_disk = sorted(p.name for p in out.iterdir())
        if on_disk != sorted(expected_files()):
            raise AssertionError(
                f"an empty directory build produced {on_disk}, "
                f"expected {sorted(expected_files())}"
            )


def check_each_written_header_matches_the_registry():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "corpus"
        write(corpus(False), out)
        for name, header in ARTIFACTS.items():
            first = (out / name).read_text(encoding="utf-8").split("\n", 1)[0]
            if tuple(first.split(",")) != header:
                raise AssertionError(
                    f"{name} header is {first}, registry says {','.join(header)}"
                )


def check_the_manifest_is_not_listed_as_a_table():
    """It holds no rows, so giving it a header would be inventing one."""
    if MANIFEST in ARTIFACTS:
        raise AssertionError(f"{MANIFEST} is in ARTIFACTS and it is not a table")
    if MANIFEST not in expected_files():
        raise AssertionError(f"{MANIFEST} is not in the expected file list")
