"""Loads the generated corpus into a DuckDB database as a raw landing layer.

Every column arrives as text, which is what the csv files hold and what an
external table over the landing bucket would give BigQuery. The casts belong in
the staging models and nowhere else, so this reads the header and nothing more.

    python3 scripts/load_duckdb.py
    python3 scripts/load_duckdb.py --corpus data --db build/gross_margin.duckdb

The table set comes from gross_margin.registry rather than from a glob over the
directory. A glob would load whatever happens to be sitting there, and an extra
file on one machine is how a model starts depending on something no other
machine has.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin.registry import ARTIFACTS

RAW_SCHEMA = "raw"


def table_name(csv_name: str) -> str:
    return csv_name[: -len(".csv")]


def load(corpus: Path, db_path: Path, schema: str = RAW_SCHEMA) -> dict[str, int]:
    import duckdb

    missing = [name for name in ARTIFACTS if not (corpus / name).exists()]
    if missing:
        raise SystemExit(
            f"{corpus} is missing {missing}. Run scripts/build_corpus.py first"
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    counts: dict[str, int] = {}
    try:
        con.execute(f"create schema if not exists {schema}")
        for csv_name, header in sorted(ARTIFACTS.items()):
            table = table_name(csv_name)
            columns = ", ".join(f"'{col}': 'VARCHAR'" for col in header)
            con.execute(f"drop table if exists {schema}.{table}")
            con.execute(
                f"create table {schema}.{table} as "
                f"select * from read_csv('{(corpus / csv_name).as_posix()}', "
                f"header = true, columns = {{{columns}}})"
            )
            got = [row[0] for row in con.execute(f"describe {schema}.{table}").fetchall()]
            if tuple(got) != header:
                raise SystemExit(
                    f"{schema}.{table} landed as {got}, the registry says {list(header)}"
                )
            counts[table] = con.execute(
                f"select count(*) from {schema}.{table}"
            ).fetchone()[0]
    finally:
        con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(REPO_ROOT / "data"))
    parser.add_argument("--db", default=str(REPO_ROOT / "build" / "gross_margin.duckdb"))
    args = parser.parse_args(argv)

    counts = load(Path(args.corpus), Path(args.db))
    for table, rows in counts.items():
        print(f"{RAW_SCHEMA}.{table:16s} {rows:>7d} rows")
    print(f"loaded {len(counts)} tables into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
