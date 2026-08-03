"""Case erhaelt ``document_type`` + ``data_json`` (M2 P5, Refs #1058).

Faelle bekommen dieselbe dynamische Feldmechanik wie Personen/Ereignisse
(Entscheid E4): ein optionaler ``DocumentType``-Verweis (fachlich
``entity_type=case``, erzwungen im Service-Layer) und ein
``data_json``-JSONField, dessen als ``is_encrypted`` markierte Slugs beim
Speichern Fernet-verschluesselt werden (``DynamicDataEncryptionMixin``, P4a).

Reine Spalten-Erweiterung — **kein** ``RunPython`` (Muster 0118, Client):

* ``data_json`` startet mit dem Feld-Default ``{}`` (leeres JSONB), der Bestand
  braucht also keinen Backfill.
* ``document_type`` ist ``null=True``; Bestands-Faelle bleiben typlos, bis ein
  Dokumentationstyp zugewiesen wird.

``on_delete=PROTECT`` spiegelt ``Client.document_type``/``Event.document_type``:
solange ein Fall auf den Typ zeigt, darf er nicht geloescht werden — sonst
verloere ``data_json`` seine Feld-Semantik (Slug -> Label/Sensitivity/
Verschluesselung). Der Anonymisierungs-Pfad (``_redact_cases_and_episodes``)
setzt den Verweis deshalb aktiv auf ``NULL``, damit anonymisierte Bestaende den
Typ nicht dauerhaft blockieren.

**Kein RLS-Update noetig**: ``core_case`` steht in ``DIRECT_TABLES`` von
``0047_postgres_rls_setup`` und traegt dort bereits eine zeilenbasierte
Facility-Policy (``facility_id::text = current_setting('app.current_facility_id')``).
RLS wirkt auf Zeilen, nicht auf Spalten — neue Spalten derselben Tabelle erben
den Schutz automatisch, es entsteht keine neue Tabelle und kein neuer
Join-Scope.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0118_client_dynamic_data")]

    operations = [
        migrations.AddField(
            model_name="case",
            name="data_json",
            field=models.JSONField(blank=True, default=dict, verbose_name="Daten (JSON)"),
        ),
        migrations.AddField(
            model_name="case",
            name="document_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cases",
                to="core.documenttype",
                verbose_name="Dokumentationstyp",
            ),
        ),
    ]
