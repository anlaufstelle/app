# Third-Party License Inventory

Anlaufstelle is licensed under [AGPLv3](LICENSE). All Python dependencies must
carry a license that is compatible with AGPLv3. This file documents the
license-allowlist policy and how to inspect the active inventory.

## Allowlist Policy

CI enforces the policy via [`scripts/check_licenses.py`](scripts/check_licenses.py)
(wired into [`.github/workflows/lint.yml`](.github/workflows/lint.yml) — Refs
#839). A build fails if
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

## Vendored JavaScript

`scripts/check_licenses.py` only scans Python (pip) metadata; it does not
cover JavaScript, fonts or icons. Vendored JS files (checked into `src/static/js/`, not
installed via a package manager) and the self-hosted fonts and icons are documented here
manually. `make verify-vendor-js-sync` (`scripts/verify_vendor_js_sync.py`)
checks, per shipped work, that the table row exists with the exact version,
license identifier, upstream URL, vendored path and license text path, that the
license text carries its obligatory parts (no placeholder), and that no
unregistered file ships under `src/static/js/vendor/`, `src/static/fonts/` or
`src/static/icons/`. Font files are pinned by SHA-256, so a replaced font forces
a review of its version and license row.

| Package | Version | License | Upstream | Vendored Path | License text |
|---|---|---|---|---|---|
| htmx | 2.0.11 | 0BSD | <https://github.com/bigskysoftware/htmx> | `src/static/js/htmx.min.js` | `src/static/js/licenses/LICENSE-htmx.txt` |
| Alpine.js CSP | 3.17.4 | MIT | <https://github.com/alpinejs/alpine> | `src/static/js/alpine-csp.min.js` | `src/static/js/licenses/LICENSE-alpine.txt` |
| Chart.js | 4.5.1 | MIT | <https://github.com/chartjs/Chart.js> | `src/static/js/chart.min.js` | `src/static/js/licenses/LICENSE-chartjs.txt` |
| Dexie.js | 4.4.6 | Apache-2.0 | <https://github.com/dexie/Dexie.js> | `src/static/js/dexie.min.js` | `src/static/js/licenses/LICENSE-dexie.txt`, `src/static/js/licenses/NOTICE-dexie.txt` |
| SortableJS | 1.15.7 | MIT | <https://github.com/SortableJS/Sortable> | `src/static/js/vendor/sortable.min.js` | `src/static/js/licenses/LICENSE-sortablejs.txt` |
| axe-core | 4.13.0 | MPL-2.0 | <https://github.com/dequelabs/axe-core> | `src/static/js/vendor/axe.min.js` | license notice in the file header |
| DM Sans | 4.004 | SIL OFL 1.1 | <https://github.com/googlefonts/dm-fonts> | `src/static/fonts/dmsans-*.woff2` | `src/static/fonts/LICENSE-DM-fonts.txt` |
| DM Mono | 1.000 | SIL OFL 1.1 | <https://github.com/googlefonts/dm-mono> | `src/static/fonts/dmmono-*.woff2` | `src/static/fonts/LICENSE-DM-fonts.txt` |
| Heroicons | 2.0.18 | MIT | <https://github.com/tailwindlabs/heroicons> | `src/static/icons/*.svg` (and the PNG renderings), `src/templates/auth/login.html` | `src/static/icons/LICENSE-heroicons.txt` |

The Dexie `NOTICE` file is shipped unchanged next to the Apache-2.0 text. The
axe-core build carries its MPL-2.0 notice in its header. The Alpine.js and
SortableJS license texts are copied unchanged from the upstream repositories at
the vendored tags (source URL in the first line of each file). The Heroicons
"home" path in the SVG icons equals `optimized/24/outline/home.svg` at tag
v2.0.18; its license text is the one of that tag.

## Disallowed Categories

Anything that is **not** on the allowlist fails the CI step. In particular:

- **Pure GPLv2-only** (without "or later") — incompatible with GPLv3/AGPLv3.
- **Proprietary / Commercial** — incompatible with redistribution under AGPLv3.
- **CC-BY-NC** or other non-commercial creative-commons — non-free per FSF.
- **Unknown / missing** license metadata — must be resolved before merging.
