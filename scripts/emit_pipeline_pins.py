"""Writes the pipeline's dependency pins into a file the Terraform can read.

    python3 scripts/emit_pipeline_pins.py

The Composer environment installs the project's dependencies on its image, so
the scheduler and the pipeline have to agree about which pins they run. The
first version of `infra/composer.tf` parsed `requirements.txt` with a Terraform
for expression over `split` and `trimspace`. It worked and it was too clever to
leave in a file somebody has to review under pressure.

So it follows the route `scripts/emit_access_rules.py` already established in
this repository. A script reads the source of truth and writes a committed JSON
file, the Terraform reads that file with jsondecode, and a check fails when the
two disagree. The pins stay declared in exactly one place.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "requirements.txt"
TARGET = REPO_ROOT / "infra" / "pipeline-pins.json"


def pins() -> dict[str, str]:
    """Every `name==version` line in requirements.txt, as a mapping.

    A line that declares something other than an exact pin is a failure rather
    than something to skip. A floated dependency on the scheduler image is how
    the environment and the pipeline end up on different versions of dbt with
    nothing to say so.
    """
    found: dict[str, str] = {}
    for number, line in enumerate(
        REQUIREMENTS.read_text(encoding="utf-8").splitlines(), start=1
    ):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "==" not in text:
            raise SystemExit(
                f"{REQUIREMENTS.name} line {number} is not an exact pin: {text!r}"
            )
        name, _, version = text.partition("==")
        found[name.strip()] = version.strip()
    if not found:
        raise SystemExit(f"{REQUIREMENTS.name} declares no pins")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed file is not what this would write",
    )
    args = parser.parse_args(argv)

    # The leading comment Terraform cannot carry. A reader who opens the json
    # should learn in one line that editing it is the wrong move.
    payload = {
        "generated_by": "python3 scripts/emit_pipeline_pins.py",
        "source": "requirements.txt",
        "pypi_packages": {name: f"=={version}" for name, version in pins().items()},
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            sys.stderr.write(
                f"{TARGET.name} is not what requirements.txt would produce. "
                "Run scripts/emit_pipeline_pins.py\n"
            )
            return 1
        print(f"{TARGET.name} agrees with requirements.txt")
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(REPO_ROOT)} with {len(payload['pypi_packages'])} pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
