# Shelter package — free text and standard fields

This excerpt translates the text-field boundary in the [German shelter package guide](../packages-frauenhaus.md).

The package provides eight event types and four standard types for person, case, episode and task (twelve types in total).

Event fields `ziel` and `notiz` are unencrypted and visible to all roles. Their guidance says not to enter addresses, real names or procedural data. Threat and support details belong in the encrypted `notiz-gefaehrdung` and `notiz-begleitung` fields. The package also provides normal person/case/episode/task types: `client-notes` and `standard-description` are encrypted and elevated (hidden from assistants), while `workitem-details` is encrypted and normal (readable by assistants). This depends on unchanged field configuration. Pseudonyms and titles remain plaintext: keep them short, without real names or GDPR Art. 9 data. Offline tasks have no detail field; add details online. See the [security notes](security-notes.md) for the remaining boundary.

<!-- translation-source: docs/packages-frauenhaus.md -->
<!-- translation-version: v0.99.8 -->
<!-- source-hash: 1ccfecf43bb5e034ac7b1fb3db76e429691a0c25 -->
<!-- translation-date: 2026-10-26 -->
