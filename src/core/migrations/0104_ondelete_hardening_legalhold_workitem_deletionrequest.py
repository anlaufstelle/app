"""Datenintegritaets-Haertung der Compliance-FKs auf User (Refs #1347).

Drei Foreign Keys auf ``settings.AUTH_USER_MODEL`` trugen bislang
``on_delete=CASCADE`` — eine harte User-Loeschung (Django-Admin/Shell,
kuenftige DSGVO-Erasure; ueber die App selbst gibt es aktuell keinen
Hard-Delete-View) haette diese Compliance-Objekte mitgerissen:

* ``LegalHold.created_by`` (DAT-04): jetzt ``PROTECT`` — ein Legal Hold
  ist der Nachweis einer Aufbewahrungspflicht (Spoliation-Schutz) und
  darf nicht verschwinden, nur weil der Ersteller-Account geloescht wird.
* ``WorkItem.created_by`` / ``DeletionRequest.requested_by`` (DAT-04):
  jetzt ``SET_NULL`` (beide Felder ``null=True``) — die fachliche
  Aufgaben- bzw. 4-Augen-Loeschantrags-Historie soll den User ueberleben,
  analog zum bereits bestehenden Muster bei ``Case.created_by``/
  ``Event.created_by``/``Client.created_by``.

DAT-02: ``LegalHold`` bekommt eine Partial-Unique gegen doppelte AKTIVE
Holds auf demselben (facility, target_type, target_id) — analog
``unique_active_retention_proposal``/``unique_pending_deletion_request``.

DAT-03 (Defense-in-Depth): Django bildet ``on_delete`` NUR im
Python-Collector ab — die von Django erzeugten FK-Constraints sind auf
Postgres-Ebene immer ``NO ACTION`` (siehe ``core_legalhold.created_by``
etc. vor dieser Migration; der Docstring in
``src/tests/test_cases_cascade.py`` bezeichnete das bislang irrefuehrend
als "DB-Level-Cascade-Vertrag" — korrigiert). Fuer genau diese drei
Compliance-FKs zieht diese Migration echte ``ON DELETE``-Constraints auf
DB-Ebene nach (RESTRICT bzw. SET NULL), damit der Schutz auch Raw-SQL-
und ORM-Bypass-Pfade abdeckt. Eine Umstellung ALLER ~60 FKs im Projekt
ist bewusst NICHT Teil dieser Migration (Konstraint-Namen-Ermittlung,
Deferrable-Semantik je FK) — siehe Follow-up-Empfehlung unter
#1350. Die neuen Constraints uebernehmen dieselbe
``DEFERRABLE INITIALLY DEFERRED``-Konvention, die Django fuer alle
FK-Constraints verwendet (Konsistenz zum Rest des Schemas); reverse_sql
stellt exakt den vorherigen ``NO ACTION``-Zustand wieder her.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0103_alter_auditlog_action"),
    ]

    operations = [
        migrations.AlterField(
            model_name="deletionrequest",
            name="requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deletion_requests",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Beantragt von",
            ),
        ),
        migrations.AlterField(
            model_name="legalhold",
            name="created_by",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="legal_holds",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Erstellt von",
            ),
        ),
        migrations.AlterField(
            model_name="workitem",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_work_items",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Erstellt von",
            ),
        ),
        migrations.AddConstraint(
            model_name="legalhold",
            constraint=models.UniqueConstraint(
                condition=models.Q(("dismissed_at__isnull", True)),
                fields=("facility", "target_type", "target_id"),
                name="unique_active_legal_hold",
            ),
        ),
        # DAT-03: echte DB-ON-DELETE-Constraints fuer die drei Compliance-FKs
        # (Defense-in-Depth, siehe Modul-Docstring). Konstraint-Namen sind
        # Djangos deterministischer Hash aus Tabelle+Spalte — stabil ueber
        # alle Klone dieser Migrationshistorie.
        migrations.RunSQL(
            sql=("ALTER TABLE core_legalhold DROP CONSTRAINT core_legalhold_created_by_id_64b92dd8_fk_core_user_id;"),
            reverse_sql=(
                "ALTER TABLE core_legalhold DROP CONSTRAINT core_legalhold_created_by_id_64b92dd8_fk_core_user_id;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_legalhold "
                "ADD CONSTRAINT core_legalhold_created_by_id_64b92dd8_fk_core_user_id "
                "FOREIGN KEY (created_by_id) REFERENCES core_user (id) "
                "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;"
            ),
            reverse_sql=(
                "ALTER TABLE core_legalhold "
                "ADD CONSTRAINT core_legalhold_created_by_id_64b92dd8_fk_core_user_id "
                "FOREIGN KEY (created_by_id) REFERENCES core_user (id) "
                "DEFERRABLE INITIALLY DEFERRED;"
            ),
        ),
        migrations.RunSQL(
            sql=("ALTER TABLE core_workitem DROP CONSTRAINT core_workitem_created_by_id_6b59951e_fk_core_user_id;"),
            reverse_sql=(
                "ALTER TABLE core_workitem DROP CONSTRAINT core_workitem_created_by_id_6b59951e_fk_core_user_id;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_workitem "
                "ADD CONSTRAINT core_workitem_created_by_id_6b59951e_fk_core_user_id "
                "FOREIGN KEY (created_by_id) REFERENCES core_user (id) "
                "ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;"
            ),
            reverse_sql=(
                "ALTER TABLE core_workitem "
                "ADD CONSTRAINT core_workitem_created_by_id_6b59951e_fk_core_user_id "
                "FOREIGN KEY (created_by_id) REFERENCES core_user (id) "
                "DEFERRABLE INITIALLY DEFERRED;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_deletionrequest "
                "DROP CONSTRAINT core_deletionrequest_requested_by_id_9dcf16ea_fk_core_user_id;"
            ),
            reverse_sql=(
                "ALTER TABLE core_deletionrequest "
                "DROP CONSTRAINT core_deletionrequest_requested_by_id_9dcf16ea_fk_core_user_id;"
            ),
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_deletionrequest "
                "ADD CONSTRAINT core_deletionrequest_requested_by_id_9dcf16ea_fk_core_user_id "
                "FOREIGN KEY (requested_by_id) REFERENCES core_user (id) "
                "ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;"
            ),
            reverse_sql=(
                "ALTER TABLE core_deletionrequest "
                "ADD CONSTRAINT core_deletionrequest_requested_by_id_9dcf16ea_fk_core_user_id "
                "FOREIGN KEY (requested_by_id) REFERENCES core_user (id) "
                "DEFERRABLE INITIALLY DEFERRED;"
            ),
        ),
    ]
