"""Backfill des ``report_key`` auf Typ- und Feld-Ebene (E3, Refs #1849).

Ohne diesen Backfill aendert die Umstellung des Erstschluessels **still die
Zahlen**: jeder Bestandstyp verloere seine Berichtszuordnung, sobald die
Backends nicht mehr ueber ``system_type`` binden. Die Regel ist deshalb
vollstaendig (Konzept
#1847 §E3.2/E3.3), nicht
nur „gesetzte Werte uebernehmen".

**Typ-Ebene** (``core_documenttype``), Reihenfolge tragend:

1. ``system_type`` ∈ den sieben Berichts-Systemtypen → gleichnamiger
   ``report_key``.
2. ``system_type`` ∈ {``note``, ``ban``} → Sentinel ``excluded``. Beide sind
   heute bewusst **nicht** zugeordnet; ohne den Sentinel fielen sie nach der
   Umstellung unter „keine Aussage getroffen" und erzeugten den Dauerhinweis aus
   #1819 erneut.
3. ``system_type`` leer (NULL **oder** Leerstring) UND ``slug`` ∈ der Umkehrung
   der Paket-Slug-Konvention → Umkehrwert. Das ist der heutige
   Slug-Zweitschluessel-Pfad (#1741):
   ohne diese Zeile fielen alle bislang slug-gemappten Typen nach der
   Abschaltung des Zweitschluessels still aus dem Bericht. Der Leerstring MUSS
   mitlaufen — ``system_type`` ist ``blank=True`` und exportseitig ist ``""``
   identisch mit NULL.
4. Alles Uebrige bleibt NULL — die echte Luecke, die der Vollstaendigkeitshinweis
   melden soll.

**Feld-Ebene** (``core_fieldtemplate``): die fuenf Paket-Slugs, an denen heute
Minutensummen, Spritzensaldo und Options-Verteilung haengen, bekommen den
Feld-Berichtsschluessel ihrer E1-Deklaration; alles Uebrige bleibt NULL.

**Kein ``entity_type``-Filter.** Ein Personen-/Fall-Typ mit gesetztem
``system_type`` bekommt hier ebenfalls einen ``report_key`` — konzepttreu und
folgenlos: alle Backends zaehlen ``Event``-Zeilen, die Wirkung beschraenkt sich
auf die Anzeige. Ein Filter waere eine zusaetzliche Sonderregel ohne Gegenwert.

**Geltungsbereich.** ``v0.80.0`` ist oeffentlich released — die Migration muss
fuer **beliebige Fremdbestaende** korrekt sein, nicht nur fuer die eigenen
Instanzen. Deshalb die vollstaendige Regel statt „bei uns gibt es nur
Paket-Typen".

**Idempotenz.** Jeder Schritt filtert auf einen leeren ``report_key`` (NULL oder
Leerstring) und laesst gesetzte Werte unangetastet. Ein zweiter Lauf aendert
nichts — dieselbe Zusage, die der Paket-Loader fuer ``default.yaml`` braucht
(dessen ``report_key``-Werte deckungsgleich mit dem Ergebnis dieses Backfills
sind, sonst waere der naechste Ladelauf ein E13-Konflikt).

**Roh per ``.update()``**, nicht ueber ``save()``: der historische
Modellzustand aus ``apps.get_model`` traegt die ``save()``-Logik ohnehin nicht,
und ein Zeilen-Roundtrip ueber das ORM waere bei Fremdbestaenden beliebiger
Groesse eine unnoetige Last.

**RLS.** ``core_documenttype`` und ``core_fieldtemplate`` tragen die
``facility_isolation``-Policy mit ``FORCE ROW LEVEL SECURITY`` (Migration
``0047``). Ohne ``assert_migration_role_bypasses_rls`` (Refs #1746) traefe der
Backfill unter der App-Rolle ohne gesetztes ``app.current_facility_id`` **still
0 Zeilen** — die Migration gaelte als erfolgreich, und nach der Abschaltung des
Zweitschluessels fielen ALLE Typen aus dem Bericht. Der Guard steht deshalb als
erste Zeile.

**Reverse ist ein bewusster No-op** (Begruendung wie ``0132``): welche Zeile
ihren ``report_key`` aus diesem Backfill und welche aus einer spaeteren
Bearbeitung hat, ist nachtraeglich nicht unterscheidbar. Ein pauschales NULLen
loeschte im Rueckweg echte Nutzereingaben; ein Rollback des Deploys laesst die
Werte deshalb einfach stehen (fail-safe: sie sind ohne die Umstellung
wirkungslos).
"""

from django.db import migrations
from django.db.models import Q

from core.utils.migration_guards import assert_migration_role_bypasses_rls

# Die sieben Systemtypen, die heute in eine Berichtskategorie fallen
# (``CATEGORY_MAP`` in ``core.reporting.backends.sample_report_1``). Bewusst als
# Literale und NICHT aus dem Reporting-Modul importiert: eine Migration
# beschreibt einen historischen Stand und darf nicht mitwandern, wenn ein
# Backend spaeter einen Schluessel umbenennt.
_CATEGORY_KEYS = (
    "contact",
    "crisis",
    "medical",
    "needle_exchange",
    "accompaniment",
    "counseling",
    "referral",
)

# Bewusst ausgeschlossen: vollstaendige Aussage „gehoert in keinen Bericht".
_EXCLUDED_SYSTEM_TYPES = ("note", "ban")
_EXCLUDED = "excluded"

# Umkehrung der Paket-Slug-Konvention (Zweitschluessel aus #1741). Nur die
# sieben Slugs, deren ``system_type`` ohnehin deklariert ist — ``notiz`` und
# ``hausverbot`` fehlen aus demselben Grund, aus dem der Zweitschluessel sie nie
# hereinholen durfte.
_SLUG_REVERSE = {
    "kontakt": "contact",
    "krisengespraech": "crisis",
    "beratungsgespraech": "counseling",
    "medizinische-versorgung": "medical",
    "spritzentausch": "needle_exchange",
    "begleitung": "accompaniment",
    "vermittlung": "referral",
}

# Feld-Ebene: Paket-Slug → Feld-Berichtsschluessel der E1-Deklarationen
# (``counseling_hours``, ``needle_exchange_balance``, ``service_distribution``).
_FIELD_SLUG_KEYS = {
    "dauer": "dauer_minuten",
    "ausgabe": "ausgabe_stueck",
    "rueckgabe": "rueckgabe_stueck",
    "leistungen": "leistungen",
    "art-der-krise": "art_der_krise",
}

# „Kein Berichtsschluessel gesetzt" — NULL und Leerstring gleichermassen.
_LEER = Q(report_key__isnull=True) | Q(report_key="")


def backfill_report_key(apps, schema_editor):
    assert_migration_role_bypasses_rls(schema_editor)
    DocumentType = apps.get_model("core", "DocumentType")
    FieldTemplate = apps.get_model("core", "FieldTemplate")

    offen = DocumentType.objects.filter(_LEER)
    for key in _CATEGORY_KEYS:
        offen.filter(system_type=key).update(report_key=key)
    offen.filter(system_type__in=_EXCLUDED_SYSTEM_TYPES).update(report_key=_EXCLUDED)
    ohne_system_type = offen.filter(Q(system_type__isnull=True) | Q(system_type=""))
    for slug, key in _SLUG_REVERSE.items():
        ohne_system_type.filter(slug=slug).update(report_key=key)

    offene_vorlagen = FieldTemplate.objects.filter(_LEER)
    for slug, key in _FIELD_SLUG_KEYS.items():
        offene_vorlagen.filter(slug=slug).update(report_key=key)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0144_report_key"),
    ]

    operations = [
        migrations.RunPython(backfill_report_key, migrations.RunPython.noop),
    ]
