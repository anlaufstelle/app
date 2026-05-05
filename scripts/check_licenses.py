#!/usr/bin/env python3
"""CI license check: verify every installed dependency carries a license
that is compatible with the AGPLv3 distribution of Anlaufstelle.

Run via ``python scripts/check_licenses.py`` after the production
dependencies are installed. The check uses ``pip-licenses`` under the
hood; install it ad-hoc in CI (see .github/workflows/lint.yml). Refs #839.

Allowlist principles
--------------------

We accept license strings that grant a permissive grant compatible with
AGPLv3, *or* explicit (L)GPL/MPL-2.0 grants that the FSF lists as
GPLv3-compatible. License strings produced by ``pip-licenses`` are
PyPI-supplied and not always SPDX-clean; we therefore work with a
deny-by-default allowlist of strings plus per-package overrides for
known-good packages whose metadata is unusual but verified.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Licenses that any package may declare without further review.
ALLOWED_LICENSES: frozenset[str] = frozenset(
    {
        # MIT family
        "MIT",
        "MIT License",
        "MIT-CMU",
        "MIT AND PSF-2.0",
        "DFSG approved; MIT License",
        # BSD family
        "BSD",
        "BSD License",
        "BSD-2-Clause",
        "BSD-3-Clause",
        # Apache 2.0 (and dual-license variants)
        "Apache 2.0",
        "Apache-2.0",
        "Apache Software License",
        "Apache Software License; BSD License",
        "Apache-2.0 OR BSD-2-Clause",
        "Apache-2.0 OR BSD-3-Clause",
        # ISC
        "ISC",
        "ISC License (ISCL)",
        # PSF
        "PSF-2.0",
        "Python Software Foundation License",
        # LGPL — AGPLv3-compatible
        "LGPL",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "GNU Lesser General Public License v3 or later (LGPLv3+)",
        # MPL 2.0 — GPLv3-compatible per FSF
        "MPL-2.0",
        "Mozilla Public License 2.0 (MPL 2.0)",
        # Public domain
        "Unlicense",
        # AGPL itself (Anlaufstelle is AGPLv3)
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "GNU Affero General Public License v3 or later (AGPLv3+)",
    }
)

# Known-good packages whose pip-licenses string does not fit the simple
# allowlist but whose actual license has been verified manually. Keep
# this list short and document the source.
PACKAGE_OVERRIDES: dict[str, str] = {
    # tri-licensed: GPLv2+ / LGPLv2+ / MPL 1.1 — we use the LGPLv2+ branch,
    # which is GPLv3-compatible. https://github.com/Kozea/Pyphen/blob/master/LICENSE
    "pyphen": (
        "GNU General Public License v2 or later (GPLv2+); "
        "GNU Lesser General Public License v2 or later (LGPLv2+); "
        "Mozilla Public License 1.1 (MPL 1.1)"
    ),
    # qrcode is BSD-3-Clause; the "Other/Proprietary License" suffix is a
    # PyPI metadata artefact. https://github.com/lincolnloop/python-qrcode
    "qrcode": "BSD License; Other/Proprietary License",
    # text-unidecode is dual GPLv2+/Artistic — GPLv2+ option is AGPLv3-compat.
    # https://github.com/kmike/text-unidecode/blob/master/LICENSE
    "text-unidecode": (
        "Artistic License; GNU General Public License (GPL); "
        "GNU General Public License v2 or later (GPLv2+)"
    ),
}


def collect_packages() -> list[dict[str, str]]:
    """Run ``pip-licenses --format=json`` and return its rows."""
    raw = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--format=json",
            "--with-system",
        ],
        cwd=ROOT,
    )
    return json.loads(raw)


def main() -> int:
    rows = collect_packages()
    failures: list[str] = []
    for row in rows:
        name = row["Name"]
        license_text = row["License"].strip()
        override = PACKAGE_OVERRIDES.get(name)
        if override and override == license_text:
            continue
        if license_text in ALLOWED_LICENSES:
            continue
        failures.append(f"{name} ({row.get('Version', '?')}): {license_text!r}")

    if failures:
        print("Disallowed or unknown licenses:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nIf the license is GPLv3-/AGPLv3-compatible, add it to "
            "ALLOWED_LICENSES or PACKAGE_OVERRIDES with a source link.",
            file=sys.stderr,
        )
        return 1
    print(f"All {len(rows)} dependencies match the AGPLv3-compatible allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
