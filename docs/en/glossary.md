# Glossary — Domain Terms (DE↔EN)

> Bilingual glossary of domain-specific terms used in Anlaufstelle.
> German terms appear in the codebase, database, and German documentation.
> English terms are used in the UI (when set to English) and English documentation.

---

| Deutsch | English | Definition |
|---------|---------|------------|
| Alterscluster | Age cluster | Coarse age group for a person (e.g. under 18, 18–26, 27+, unknown). Configurable per facility. Used for statistics without requiring an exact date of birth. |
| Anlaufstelle | Drop-in center | Name of the application. Also colloquial for the facility itself — the place where people come for help. |
| Arbeitsinfo | Work item | Collective term for notes and tasks — operational entries that are separate from case documentation. See: WorkItem. |
| Audit-Trail | Audit trail | Immutable log of all security-relevant actions in the system: access, changes, deletions, login attempts. Serves GDPR compliance and traceability. See: AuditLog. |
| AuditLog | Audit log | Technical term for the audit trail. A dedicated entity stored separately from case documentation. Immutable (append-only). |
| Benannter Zeitfilter | Named time filter | A saved work-time window with a label (e.g. "Night shift 21:30–09:00"). Used as a quick filter on the dashboard and in statistics. Pure UI configuration, not a data structure. See: TimeFilter. |
| Case | Case | A case — a bracket around related work with a person. Contains episodes, assignments, and optionally outcome goals. |
| Chronik | Timeline | The chronological sequence of all events documented for a person. The timeline is the primary view of a person in Anlaufstelle. |
| Client | Client | A person in the system. Managed under a pseudonym. Has a contact level that determines their lifecycle in the system. |
| DocumentType | Document type | A configurable category of events (e.g. "Contact", "Crisis counseling", "Needle exchange"). Defines fields, sensitivity, and retention period. |
| DocumentTypeField | Document type field | Association of a field template (FieldTemplate) with a document type (DocumentType). Determines field order in the form and allows reuse of field templates across multiple types. |
| Domänenbibliothek | Domain library | Preconfigured set of document types for a specific facility type (e.g. "Low-threshold addiction services"). Seed data loaded during initial setup. |
| DSGVO | GDPR | General Data Protection Regulation — the European regulation for the protection of personal data. Together with social data protection (SGB X), the central legal framework for Anlaufstelle. |
| Einrichtung | Facility | A concrete site or location. The primary scope boundary for staff members. All entities have a foreign key to Facility. |
| Episode | Episode | A distinguishable phase within a case: e.g. a crisis phase, a referral process. |
| Ereignis | Event | The central building block of documentation. A timestamped entry recording an occurrence. Belongs to a document type and optionally to a person. See: Event. |
| Event | Event | Technical term for an occurrence record (Ereignis). See: Ereignis. |
| Facility | Facility | Technical term for an institution or site (Einrichtung). The primary scope boundary for staff. All entities carry a foreign key to Facility. |
| FieldTemplate | Field template | Defines a field within a document type: name, data type, required flag, options, encryption, statistics mapping. |
| Hausverbot | Site ban | A ban prohibiting a person from entering a facility, with reason, validity period, and issuer. Modeled in Anlaufstelle as a document type in the "Administration" category. |
| Inbox | Inbox | Personal overview of all open notes and due tasks for the logged-in staff member. Part of the operations layer. |
| JSONB | JSONB | PostgreSQL data type for binary JSON. Used in Anlaufstelle to store event field values together with the event record. Indexable (GIN index), queryable, performant. |
| Kontaktstufe | Contact level | Three-tier model describing a person's identification status in the system: anonymous (count only), identified (pseudonym), qualified (counseling process). Determines access rights, permitted document types, and retention periods. |
| Milestone | Milestone | A concrete step toward an outcome goal. |
| Organisation | Organization | The carrier or parent entity — the top level of the hierarchy. In v1.0 exactly one organization exists, created automatically and hidden in the UI. Serves as a prepared scope for future multi-carrier support. |
| Outcome | Outcome | The result of working with a person. Not the activity ("347 contacts") but the change ("stable housing situation achieved"). |
| OutcomeGoal | Outcome goal | What should be achieved through the work with a person. Assigned to a case. |
| Pseudonym | Pseudonym | A name assigned by the team to identify a person in the system. Primary identifier in Anlaufstelle. The mapping to the real name exists only in staff knowledge, not in the system. |
| Role | Role | Determines which actions a user may perform. Four roles: Admin (system control), Lead (professional leadership), Staff (case worker), Assistant (support). |
| Scope | Scope | Visibility boundary. Determines which data is accessible to a user — based on facility, role, and contact level. |
| Sensitivität | Sensitivity | Classification of a document type or field regarding its protection level. Controls which roles may access field values. Configurable per document type and per field (`FieldTemplate.sensitivity`). Independent of field encryption (`is_encrypted`). |
| TimeFilter | Time filter | Technical term for a named time filter. Belongs to a facility and defines a time window (start time, end time) with a label. |
| User | User | A staff member — a person who works with the system. Has credentials and a role assignment within a facility. |
| WorkItem | Work item | An operational entry (note or task) with its own lifecycle and optional priority. Separate from case documentation. |
| Zeitstrom | Timeline stream | The chronological flow of all events, unfiltered or filtered by time period, person, or document type. The core metaphor of documentation in Anlaufstelle. |

<!-- translation-source: docs/fachkonzept-anlaufstelle.md (chapter 14) -->
<!-- translation-version: v0.9.0 -->
<!-- translation-date: 2026-03-28 -->
<!-- source-hash: d749493 -->
