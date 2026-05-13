# Third-Party License Inventory

Anlaufstelle is licensed under [AGPLv3](LICENSE). All Python dependencies must
carry a license that is compatible with AGPLv3. This file documents the
license-allowlist policy and how to inspect the active inventory.

## Allowlist Policy

CI enforces the policy via [`scripts/check_licenses.py`](scripts/check_licenses.py)
(wired into [`.github/workflows/lint.yml`](.github/workflows/lint.yml) —). A build fails if
any installed package declares a license that is neither on the global
allowlist nor on the per-package override list.

**Globally allowed license families** (AGPLv3-compatible):

| Family | Examples |
|---|---|
| MIT | `MIT`, `MIT License`, `MIT-CMU`, `MIT AND PSF-2.0` |
| BSD | `BSD`, `BSD-2-Clause`, `BSD-3-Clause`, `BSD License` |
| Apache 2.0 | `Apache-2.0`, `Apache Software License`, `Apache-2.0 OR BSD-3-Clause` |
| ISC | `ISC`, `ISC License (ISCL)` |
| PSF | `PSF-2.0`, `Python Software Foundation License` |
| LGPL v3 | `LGPL-3.0-only`, `LGPLv3+` |
| MPL 2.0 | `MPL-2.0` (FSF: GPLv3-compatible) |
| Public domain | `Unlicense` |
| AGPL v3 | self |

**Per-package overrides** for known-good packages whose PyPI metadata is
unusual but whose actual upstream license has been verified manually:

- `pyphen` — tri-licensed GPLv2+ / LGPLv2+ / MPL 1.1; we use the LGPLv2+
  branch. ([source](https://github.com/Kozea/Pyphen/blob/master/LICENSE))
- `qrcode` — BSD-3-Clause; the `Other/Proprietary License` suffix in the
  PyPI metadata is an artefact.
  ([source](https://github.com/lincolnloop/python-qrcode))
- `text-unidecode` — dual GPLv2+ / Artistic; the GPLv2+ branch is
  AGPLv3-compatible.
  ([source](https://github.com/kmike/text-unidecode/blob/master/LICENSE))

Adding a license to the allowlist or a package to the override list requires
linking the upstream license text in the source comment in
`scripts/check_licenses.py`.

## Generating the Live Inventory

The full per-package inventory is **not** committed because it changes with
every dependency bump and would cause PR churn. Regenerate it locally:

```bash
.venv/bin/pip install pip-licenses
.venv/bin/pip-licenses --format=markdown --with-system --with-urls > THIRDPARTY-LICENSES-LOCAL.md
```

CI also exposes the inventory: the `licenses` job in [`lint.yml`](.github/workflows/lint.yml)
prints a summary and uploads the JSON/Markdown reports as build artefacts.

## Disallowed Categories

Anything that is **not** on the allowlist fails the CI step. In particular:

- **Pure GPLv2-only** (without "or later") — incompatible with GPLv3/AGPLv3.
- **Proprietary / Commercial** — incompatible with redistribution under AGPLv3.
- **CC-BY-NC** or other non-commercial creative-commons — non-free per FSF.
- **Unknown / missing** license metadata — must be resolved before merging.
