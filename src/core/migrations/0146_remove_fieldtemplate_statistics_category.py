"""``FieldTemplate.statistics_category`` entfernen (E3, Refs #1849, ADR-040-Muster).

Das Feld „Statistik-Kategorie" war ein in der Konfigurations-UI editierbares
Freitextfeld, das **von nichts gelesen** wurde: kein Berichts-Backend, keine
Statistik-Ansicht, keine Auswertung — es wanderte ausschliesslich in den
Einrichtungs-Export. Eine Administratorin trug dort eine Zuordnung ein, die
nichts bewirkte. Exakt das Muster, das bei ``DocumentType.category`` zur
Entfernung gefuehrt hat ([ADR-040](../../../docs/adr/040-documenttype-category-entfernt.md),
Migration ``0134``); Entscheid E-C in Konzept
#1847.

An seine Stelle tritt ``FieldTemplate.report_key`` (Migration ``0144``) — die
Zuordnung, die tatsaechlich etwas bewirkt und deren Werteraum aus den
Backend-Deklarationen kommt.

**Kein Datenrettungsschritt.** Ein Uebernehmen der Alt-Werte nach ``report_key``
waere falsch: die Freitexte sind einrichtungseigene Label ohne Bezug zum
Registry-Vokabular; sie zu uebernehmen erzeugte flaechendeckend unbekannte
Schluessel und damit einen Vollstaendigkeitshinweis, der nur den Umzug meldet.
Den Bestand fuellt stattdessen ``0145_backfill_report_key`` ueber die
Paket-Slugs.

**Reverse ist strukturell, nicht inhaltlich** (Muster ``0134``): das Feld wird
mit seinem Default (leer) wieder angelegt, die urspruenglichen Texte sind dann
verloren. Der Rueckweg existiert, damit ein Deploy-Rollback nicht am Schema
haengenbleibt — nicht als Datenrettung.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0145_backfill_report_key"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="fieldtemplate",
            name="statistics_category",
        ),
    ]
