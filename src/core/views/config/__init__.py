"""Config-CRUD-Views für die Formular-Konfiguration (M0, Refs #1056).

Plain ``View``-CBVs unter ``/config/`` für DocumentType (P5a) und FieldTemplate
(P5b). Zugriff ausschließlich für die Anwendungsbetreuung
(``FacilityAdminRequiredMixin``, Exakt-Match — kein super_admin); alle
Mutationen laufen über :mod:`core.services.config` (Audit inklusive).
"""
