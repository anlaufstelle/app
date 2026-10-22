# ADR-025 — security guards for admin packages

This excerpt translates the updated guard coverage in [the German ADR-025](../../adr/025-csp-unsafe-eval-admin.md).

Admin HTML, including the anonymous login page, retains the CSP relaxation for Unfold. The markup guard scans both project templates and installed Django-admin/Unfold package templates. Fourteen specific path/construct exceptions justify twenty escaping findings; there is no blanket vendor exclusion. The CSRF guard scans project Python and Python in the same installed packages (currently no vendor exception). Missing or empty package inventories fail. The inline-script/Alpine guard remains limited to project templates; these static guards do not prove the semantic safety of every allowed output.

<!-- translation-source: docs/adr/025-csp-unsafe-eval-admin.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: fe70a356534b31dc8e45d6399c9d3efbb7770c5a -->
