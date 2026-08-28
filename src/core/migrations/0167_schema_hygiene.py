"""Schema-Hygiene-Buendel: Name-Unique, Cascades, visited_at, is_default (Refs #2068).

Fuenf voneinander unabhaengige Befunde, die alle am Schema haengen. Sie laufen
bewusst in EINER Migration zusammen: einzeln waeren es fuenf Migrationen mit je
einer Zeile, und der Preflight-Guard aus (a) muesste trotzdem vor allen anderen
Operationen stehen.

**(a) ``Facility.name`` UNIQUE.** Vier Management-Kommandos adressieren die
Einrichtung ueber ``--facility <name>``:
``generate_dsgvo_package`` per ``.get(name=…)`` — bei einem Doppelnamen ein
``MultipleObjectsReturned``-Absturz —, ``enforce_retention``,
``detect_breaches`` und ``create_statistics_snapshots`` per
``.filter(name=…)``: die verarbeiteten still ALLE Treffer, also auch die nicht
gemeinte Einrichtung. Beim Aufbewahrungs-Kommando heisst das im schlimmsten
Fall: Loeschfristen einer fremden Einrichtung durchgesetzt. Ein globales UNIQUE
heilt alle vier auf einmal; ein je-Organisation-UNIQUE keines davon, weil keins
der Kommandos einen Organisations-Kontext kennt.

:func:`guard_no_duplicate_facility_names` laeuft als erste Operation. Ohne ihn
scheiterte die Migration an der rohen Postgres-Meldung ("could not create
unique index … Key (name)=(…) is duplicated") — die nennt genau EINEN
Beispielnamen und verschweigt, wie viele Einrichtungen betroffen sind. Betreiber
muessen hier eine fachliche Entscheidung treffen (umbenennen — welche der
beiden?), und dafuer brauchen sie die vollstaendige Liste.

**(b) ``QuickTemplate.document_type`` CASCADE → PROTECT.** Reine
Zustands-Operation (``on_delete`` lebt in Python, nicht im Schema). Die
zugehoerige Lösch-Blocker-Zeile sitzt in
``services/config/delete_blockers.delete_document_type``.

**(c) ``RectificationRequest.target_client`` CASCADE → PROTECT.** Ebenfalls
reiner Zustand. Der DSGVO-Loeschpfad ist nicht betroffen: Art. 17 laeuft ueber
Soft-Delete + ``anonymize_client`` (UPDATE), nicht ueber ein hartes DELETE.

**(d) ``RecentClientVisit.visited_at``**: ``auto_now`` → ``default``. Der
Stempel wird jetzt von ``RecentClientVisitManager.update_or_create``
kontrolliert und gedrosselt gesetzt. Bestandszeilen behalten ihren Wert — die
Spalte selbst aendert sich nicht (NOT NULL DateTime bleibt), Django schreibt
nur den Default-Ausdruck in den Modellzustand.

**(e) ``TimeFilter.is_default`` entfaellt** (echtes ``DROP COLUMN``). Das Feld
wurde ausschliesslich vom Startbestand geschrieben und nirgends gelesen;
``current_tally_context`` faellt ausserhalb jeder Schicht auf den vollen Tag
zurueck, nicht auf die markierte Schicht. Der Datenverlust ist die Markierung
selbst — sie trug keine Wirkung, und der Reverse legt die Spalte mit ihrem
Default ``False`` wieder an. Wer die Vorauswahl fachlich will, braucht ein
Feature mit eigenem Entwurf, nicht dieses Feld.

**(f)** UUIDv4 → UUIDv7 ist im Issue eine reine Feststellung: die
Zufalls-UUIDs streuen Index-Einfuegungen ueber den ganzen B-Tree, waehrend v7
zeitlich sortiert. Ein Wechsel lohnt allenfalls fuer NEUE Hot-Tables und ist
hier ausdruecklich **nicht** umgesetzt — ein Umbau der bestehenden PKs waere
eine Datenmigration ueber das gesamte Schema ohne fachlichen Gegenwert.
"""

from django.db import migrations, models
from django.utils import timezone

import django.db.models.deletion

from core.utils.migration_guards import assert_migration_role_bypasses_rls


def guard_no_duplicate_facility_names(apps, schema_editor):
    """Vorwaerts-Preflight fuer (a): meldet Doppelnamen fail-loud im Klartext.

    ``assert_migration_role_bypasses_rls`` als erste Zeile (Konvention seit
    0123, Refs #1746). ``core_facility`` traegt selbst keine
    ``facility_isolation``-Policy — die Assertion steht trotzdem, weil sie die
    Rollen-Erwartung der gesamten Migration dokumentiert: das anschliessende
    ``DROP COLUMN`` aus (e) und der UNIQUE-Aufbau brauchen die Migrationsrolle,
    und ein Lauf unter der App-Rolle soll hier scheitern, nicht auf halber
    Strecke.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT name, count(*) FROM core_facility GROUP BY name HAVING count(*) > 1 ORDER BY name",
        )
        duplicates = cursor.fetchall()
    if duplicates:
        details = ", ".join(f"'{name}' ({count}x)" for name, count in duplicates)
        raise RuntimeError(
            f"Einrichtungsnamen sind nicht eindeutig ({details}). Vier Management-Kommandos "
            "(enforce_retention, detect_breaches, create_statistics_snapshots, generate_dsgvo_package) "
            "adressieren die Einrichtung ueber --facility <name> und treffen bei Doppelnamen die "
            "falsche oder gleich alle. Vor dem erneuten Migrieren die betroffenen Einrichtungen "
            "eindeutig benennen (Django-Admin oder manage.py shell); danach laeuft die Migration durch. "
            "Refs #2068."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0166_event_composite_fk"),
    ]

    operations = [
        # MUSS die erste Operation bleiben: nur vor dem UNIQUE-Aufbau kann der
        # Preflight die Bestandslage ueberhaupt noch melden. Position per Test
        # gepinnt.
        migrations.RunPython(guard_no_duplicate_facility_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="facility",
            name="name",
            field=models.CharField(max_length=200, unique=True, verbose_name="Name"),
        ),
        migrations.AlterField(
            model_name="quicktemplate",
            name="document_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="quick_templates",
                to="core.documenttype",
                verbose_name="Dokumentationstyp",
            ),
        ),
        migrations.AlterField(
            model_name="rectificationrequest",
            name="target_client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rectification_requests",
                to="core.client",
                verbose_name="Betroffene Person",
            ),
        ),
        migrations.AlterField(
            model_name="recentclientvisit",
            name="visited_at",
            field=models.DateTimeField(default=timezone.now),
        ),
        migrations.RemoveField(
            model_name="timefilter",
            name="is_default",
        ),
    ]
