# Configuration packages — standard types

This excerpt covers the standard types in the [German package reference](../packages.md).

The five YAML packages (`default`, `beratungsstelle`, `awareness`, `frauenhaus`, `jugendarbeit`) provide respectively 13, 12, 11, 12 and 12 documentation types, including one each for person, case, episode and task. Package format remains schema v1; the separate [facility export](facility-export-schema.md) uses schema v2.

| Entity | Standard field | Field sensitivity | Encrypted | Assistant reads |
|---|---|---|---|---|
| Person | `client-notes` | elevated | yes | no |
| Case | `standard-description` | elevated | yes | no |
| Episode | `standard-description` | elevated | yes | no |
| Task | `workitem-details` | normal | yes | yes |

The types have normal sensitivity. Effective visibility follows the higher type/field level and role; encryption depends on the current field configuration. `load_package --package <name>` loads the standard types. One active type is preselected; several require a choice. No type means no free-text detail field. Losing populated fields requires confirmation; a type switch is refused if original values are not visible. Offline tasks have no detail field; add details online. Pseudonyms and titles remain plaintext: keep them short, without real names or GDPR Art. 9 data.

<!-- translation-source: docs/packages.md -->
<!-- translation-version: v0.99.8 -->
<!-- source-hash: cdd92562b03dae0a7256ea20e4365760bb0d1dfa -->
<!-- translation-date: 2026-10-26 -->
