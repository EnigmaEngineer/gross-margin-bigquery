"""Writes the access rule list into a file the Terraform can read.

    python3 scripts/emit_access_rules.py

Terraform cannot import a Python module, so the choice was between a generated
file and writing the roles out a second time in HCL. Two copies of an access
rule that have to agree is the failure the rule list exists to prevent, so
`infra/access.tf` reads this file with jsondecode and loops over it.

The generated file is committed. A generated artefact in version control is
worth a word of defence. It is here because the Terraform has to be readable
and reviewable on its own, and because a reviewer looking at the plan wants to
see the predicates rather than the code that made them.
`tests/test_access.py` fails when the committed file and the rule list
disagree, which is what stops the copy from going stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gross_margin import access

DEFAULT_OUT = REPO_ROOT / "infra" / "access-rules.json"


def rendered() -> str:
    return json.dumps(access.rules_as_json(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the file on disk is not what the rule list produces",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    text = rendered()

    if args.check:
        if not out.exists():
            print(f"{out} does not exist")
            return 1
        if out.read_text(encoding="utf-8") != text:
            print(f"{out} is not what gross_margin.access produces")
            return 1
        print(f"{out} matches the rule list")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    rules = access.rules_as_json()
    print(f"roles                {len(rules['roles'])}")
    print(f"row access policies  {len(rules['row_access_policies'])}")
    print(f"tagged columns       {len(rules['tagged_columns'])}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
