"""Client erhaelt ``document_type`` + ``data_json`` (M2 P4b, Refs #1058).

Personen bekommen dieselbe dynamische Feldmechanik wie Ereignisse (Entscheid
E4): ein optionaler ``DocumentType``-Verweis (fachlich ``entity_type=client``,
erzwungen im Service-Layer) und ein ``data_json``-JSONField, dessen als
``is_encrypted`` markierte Slugs beim Speichern Fernet-verschluesselt werden
(``DynamicDataEncryptionMixin``, P4a).

Reine Spalten-Erweiterung — **kein** ``RunPython``:

* ``data_json`` startet mit dem Feld-Default ``{}`` (leeres JSONB), der Bestand
  braucht also keinen Backfill.
* ``document_type`` ist ``null=True``; Bestands-Personen bleiben typlos, bis
  ein Profil-Typ zugewiesen wird.

``on_delete=PROTECT`` spiegelt ``Event.document_type``: solange eine Person auf
den Typ zeigt, darf er nicht geloescht werden — sonst verloere ``data_json``
seine Feld-Semantik (Slug -> Label/Sensitivity/Verschluesselung). Der
Anonymisierungs-Pfad (``_redact_client_identity`` / ``k_anonymize_client``)
setzt den Verweis deshalb aktiv auf ``NULL``, damit anonymisierte Bestaende den
Typ nicht dauerhaft blockieren.

**Kein RLS-Update noetig**: ``core_client`` steht in ``DIRECT_TABLES`` von
``0047_postgres_rls_setup`` und traegt dort bereits eine zeilenbasierte
Facility-Policy (``facility_id::text = current_setting('app.current_facility_id')``).
RLS wirkt auf Zeilen, nicht auf Spalten — neue Spalten derselben Tabelle erben
den Schutz automatisch, es entsteht keine neue Tabelle und kein neuer
Join-Scope.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0117_documenttype_slug_entity_type")]

    operations = [
        migrations.AddField(
            model_name="client",
            name="data_json",
            field=models.JSONField(blank=True, default=dict, verbose_name="Daten (JSON)"),
        ),
        migrations.AddField(
            model_name="client",
            name="document_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clients",
                to="core.documenttype",
                verbose_name="Dokumentationstyp",
            ),
        ),
    ]
