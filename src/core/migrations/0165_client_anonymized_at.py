"""Terminal-Marker der Personen-Tilgung + Backfill des Bestands (Refs #2013).

``anonymize_client`` aendert ``deleted_at`` nicht. Ein bereits getilgter
Tombstone erfuellt ``is_deleted=True, deleted_at < cutoff`` also dauerhaft
weiter, und ``anonymize_eligible_soft_deleted_clients`` selektierte ihn in
JEDEM naechtlichen Lauf erneut — pro Tombstone und Nacht eine weitere,
HMAC-verkettete ``CLIENT_ANONYMIZED``-Zeile im append-only AuditLog. ``Case``
hat gegen genau das seit 0159 ``anonymized_at``; die Person bekommt hier ihr
Gegenstueck.

**Backfill.** Ohne ihn liefe der Bestand nach dem Deploy noch genau ein
letztes Mal durch die Tilgung — eine ueberfluessige Auditzeile je
Alt-Tombstone.

**Warum das Praefix allein NICHT als Erkennungsmerkmal taugt** (Cross-Review):
``Gelöscht-`` ist fuer Benutzereingaben nicht reserviert —
``ClientForm.clean_pseudonym`` (``core/forms/clients.py``) prueft nur die
Eindeutigkeit. Eine **lebende** Person darf also legitim ``Gelöscht-Test``
heissen. Wuerde der Backfill sie stempeln, naehme er sie nach ihrer spaeteren,
regulaeren Loeschung dauerhaft aus dem Nachtlauf: ihre personenbezogenen Daten
wuerden nie getilgt. Das waere ein Art.-17-Verstoss durch eine Migration, die
eigentlich nur Auditrauschen vermeiden soll.

Erkannt wird deshalb **exakt das Ergebnis von ``_redact_client_identity``**,
und zwar an zwei Merkmalen zugleich:

1. ``is_active=False`` — das setzt ausser der Redaktion nur
   ``k_anonymize_client``, und jene vergibt das Praefix ``anon-``, nicht
   ``Gelöscht-``. Kein Formular- oder Service-Pfad schreibt ``is_active``
   sonst.
2. ``pseudonym == "Gelöscht-" || <erste 8 Zeichen der eigenen UUID>`` — genau
   die Zeichenkette, die ``_redact_client_identity`` baut. Ein frei gewaehltes
   Pseudonym trifft das nicht, weil es die eigene Primaerschluessel-UUID
   enthalten muesste.

Die Fehlerrichtung ist bewusst asymmetrisch gewaehlt: **im Zweifel NICHT
stempeln**. Ein fehlender Stempel kostet eine einzige ueberfluessige
Auditzeile im naechsten Lauf (danach setzt ``_redact_client_identity`` ihn
selbst); ein faelschlich gesetzter Stempel bedeutete, dass echte Daten nie
geloescht werden.

Der Zeitstempel wird auf ``deleted_at`` gesetzt, ersatzweise auf
``updated_at`` — die Redaktion ist der letzte Schreibzugriff auf so eine
Zeile, und ``deleted_at`` fehlt genau dann, wenn die Bridge OHNE Papierkorb
getilgt hat. Beides ist eine Naeherung an einen historischen Zeitpunkt, den
niemand mehr exakt kennt; fuer die Filterwirkung zaehlt allein, dass der Wert
nicht NULL ist.

Idempotent: ein zweiter Lauf findet keine passende Zeile ohne Stempel mehr.
``reverse_code`` ist ein No-op — der Rueckwaertslauf entfernt die Spalte
ohnehin vollstaendig.

**RLS:** ``core_client`` traegt eine ``facility_isolation``-Policy mit
``FORCE ROW LEVEL SECURITY`` (Migration ``0047``). Migrationen laufen laut
[ADR-020](../../../docs/adr/020-three-role-postgres-model.md) auf der
Admin-Rolle (``NOSUPERUSER BYPASSRLS``); ``assert_migration_role_bypasses_rls``
(Refs #1746) haelt fail-loud fest, dass das auch tatsaechlich so ist — sonst
traefe der Backfill ohne gesetztes ``app.current_facility_id`` still null
Zeilen und die Migration gaelte faelschlich als erfolgreich.
"""

from django.db import migrations, models
from django.db.models import CharField, F, Value
from django.db.models.functions import Cast, Coalesce, Concat, Substr

from core.utils.migration_guards import assert_migration_role_bypasses_rls

#: Praefix, das ``_redact_client_identity`` beim Tilgen setzt.
_REDACTED_PSEUDONYM_PREFIX = "Gelöscht-"

#: ``_redact_client_identity`` baut ``f"Gelöscht-{str(client.pk)[:8]}"`` — die
#: ersten 8 Zeichen der (in Postgres wie in Python gleich formatierten)
#: UUID-Textdarstellung.
_REDACTED_PK_CHARS = 8


def backfill_anonymized_at(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    Client = apps.get_model("core", "Client")
    Client.objects.filter(
        # Beide Merkmale zusammen — siehe Modul-Docstring: das Praefix allein
        # trifft auch legitime Pseudonyme lebender Personen.
        is_active=False,
        anonymized_at__isnull=True,
    ).annotate(
        _redacted_pseudonym=Concat(
            Value(_REDACTED_PSEUDONYM_PREFIX),
            Substr(Cast("id", output_field=CharField()), 1, _REDACTED_PK_CHARS),
            output_field=CharField(),
        )
    ).filter(pseudonym=F("_redacted_pseudonym")).update(
        anonymized_at=Coalesce(F("deleted_at"), F("updated_at"))
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0164_user_access_audit_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="client",
            name="anonymized_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Anonymisiert am"),
        ),
        migrations.RunPython(backfill_anonymized_at, migrations.RunPython.noop),
    ]
