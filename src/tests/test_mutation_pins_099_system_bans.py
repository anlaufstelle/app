"""Pin-Tests der Welle L (0.99, Refs #2174, Refs #2193) — Testlücken in
``core.services.system.bans``, Batch ``system-2``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-system-2.json`` (43
Survivors über ``_user_may_see_grund``, ``_collect_active_bans``,
``_project_ban_data``, ``get_active_bans`` und ``get_active_bans_for_client``).
24 der 43 Mutanten sind nachweislich equivalent oder reine
Logging-Textvarianten ohne Leser (siehe
``welle-l-0.99/reports/m-pins-system-2-report.md``); die verbleibenden 19
echten Testlücken werden hier gepinnt (teils mehrere Mutanten je Test, wenn
dieselbe Beobachtung sie alle tötet):

* **``x__collect_active_bans__mutmut_17``**: aus ``continue`` wird ``break``
  — ein inaktives Event VOR einem aktiven (Sortierung ``-occurred_at``)
  lässt die gesamte Schleife abbrechen und verschluckt das aktive
  Hausverbot.
* **``x__project_ban_data__mutmut_5``/``…_13``**: der ``default``-Wert von
  ``safe_decrypt`` für ``aktiv`` kippt von ``False`` (fail-closed) auf den
  wahrheitswertigen Platzhalter ``"[verschlüsselt]"`` bzw. auf ``True`` — ein
  defektes ``aktiv``-Token würde fälschlich als aktives Hausverbot gelten.
* **``x__project_ban_data__mutmut_30``**: die Initialisierung ``bis_date =
  None`` wird zu ``bis_date = ""`` — ohne (oder mit defektem) ``bis``-Datum
  liefert die Funktion einen Leerstring statt ``None``.
* **``x__project_ban_data__mutmut_34``**: ``bis_date < today`` wird zu
  ``bis_date <= today`` — ein Hausverbot mit ``bis == heute`` gilt einen Tag
  zu früh als abgelaufen und verschwindet an seinem letzten gültigen Tag.
* **``x__project_ban_data__mutmut_46``/``…_48``/``…_56``**: der
  ``default``-Wert von ``safe_decrypt`` für ``grund`` kippt von ``""`` auf
  ``None``/``"[verschlüsselt]"``/``"XXXX"`` — ein defektes ``grund``-Token
  zeigt einer berechtigten Rolle Ersatztext statt eines Leerstrings.
* **``x__project_ban_data__mutmut_50``/``…_52``/``…_55``**: der
  ``.get("grund", …)``-Fallback bei fehlendem Schlüssel kippt von ``""`` auf
  ``None``/``"XXXX"`` — fehlt der ``grund``-Key ganz, liefert die Funktion
  keinen Leerstring mehr.
* **``x_get_active_bans__mutmut_4``**: der ``system_type="ban"``-Filter auf
  ``DocumentType`` entfällt — Events FREMDER Dokumenttypen mit zufällig
  passender ``data_json``-Form würden als Hausverbot gezeigt.
* **``x_get_active_bans__mutmut_5``**: der ``is_active=True``-Filter auf
  ``DocumentType`` entfällt — ein deaktivierter Hausverbot-Dokumenttyp würde
  weiter ausgewertet.
* **``x_get_active_bans__mutmut_19``**: der ``document_type__in``-Filter auf
  ``Event`` entfällt — Events fremder Dokumenttypen fließen in die
  Auswertung ein, obwohl ``ban_doc_types`` korrekt nur den echten
  Hausverbot-Typ enthält.
* **``x_get_active_bans_for_client__mutmut_5``/``…_22``**: dieselben zwei
  Lücken (``system_type``-/``document_type__in``-Filter) auf der
  Pro-Klient-Variante.
* **``x_get_active_bans_for_client__mutmut_21``**: der ``client=client``-
  Filter entfällt — ``get_active_bans_for_client`` würde Hausverbote ANDERER
  Klientinnen und Klienten derselben Einrichtung einblenden
  (Klienten-übergreifendes Datenleck).
* **``x_get_active_bans_for_client__mutmut_23``**: der
  ``is_deleted=False``-Filter entfällt — ein bereits gelöschtes
  Hausverbot-Event würde weiter als aktives Verbot gezeigt.
* **``x_get_active_bans_for_client__mutmut_38``**: ``include_client_in_result``
  kippt von ``False`` auf ``True`` — jeder Eintrag bekäme ungewollt einen
  ``"client"``-Schlüssel.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system import get_active_bans, get_active_bans_for_client
from core.services.system.bans import _project_ban_data

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def doc_type_hausverbot(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )


# ---------------------------------------------------------------------
# _collect_active_bans
# ---------------------------------------------------------------------


def test_get_active_bans_ueberspringt_inaktives_event_statt_abzubrechen(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x__collect_active_bans__mutmut_17``: Events sind nach
    ``-occurred_at`` sortiert (neuestes zuerst). Folgt auf ein INAKTIVES
    Event ein AELTERES AKTIVES Event, muss die Schleife weiterlaufen
    (``continue``) statt komplett abzubrechen (``break``) — sonst
    verschwindet ein tatsächlich aktives Hausverbot spurlos."""
    now = timezone.now()
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=now,
        data_json={"grund": "Aufgehoben", "bis": "", "aktiv": False},
        created_by=staff_user,
    )
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=now - timedelta(days=1),
        data_json={"grund": "Bedrohung", "bis": "", "aktiv": True},
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert len(bans) == 1
    assert bans[0]["grund"] == "Bedrohung"


# ---------------------------------------------------------------------
# _project_ban_data
# ---------------------------------------------------------------------


def test_project_ban_data_haelt_bei_defektem_aktiv_token_fail_closed():
    """Pins ``x__project_ban_data__mutmut_5``/``…_13``: der ``default``-Wert
    von ``safe_decrypt`` für ``aktiv`` muss ``False`` bleiben. Ein defektes
    (nicht entschlüsselbares) ``aktiv``-Token darf NICHT als aktives
    Hausverbot gewertet werden (fail-closed statt fail-open)."""
    data = {
        "aktiv": {"__encrypted__": True, "value": "invalid-token"},
        "bis": "",
        "grund": "",
    }

    result = _project_ban_data(data, today=date.today(), may_see_grund=False)

    assert result == (False, None, "")


def test_project_ban_data_liefert_none_ohne_gueltiges_bis_datum():
    """Pins ``x__project_ban_data__mutmut_30``: die Initialisierung
    ``bis_date = None`` darf nicht zu ``""`` mutieren — weder ohne
    ``bis``-Wert noch bei einem defekt gespeicherten (nicht parsbaren)
    Datum bleibt ``bis_date`` sonst der Vertrag ``date | None``."""
    ohne_bis = _project_ban_data(
        {"aktiv": True, "bis": "", "grund": "Unbefristet"}, today=date.today(), may_see_grund=True
    )
    assert ohne_bis[1] is None

    mit_kaputtem_datum = _project_ban_data(
        {"aktiv": True, "bis": "kein-datum", "grund": "Unbefristet"}, today=date.today(), may_see_grund=True
    )
    assert mit_kaputtem_datum[1] is None


def test_project_ban_data_bleibt_am_bis_datum_selbst_aktiv():
    """Pins ``x__project_ban_data__mutmut_34``: ``bis_date < today`` wird zu
    ``bis_date <= today`` — ein Hausverbot mit ``bis == heute`` muss an
    diesem Tag noch als aktiv gelten (Prio 1: verbirgt sonst ein aktives
    Hausverbot einen Tag zu früh)."""
    today = date.today()

    result = _project_ban_data(
        {"aktiv": True, "bis": today.isoformat(), "grund": "Bedrohung"}, today=today, may_see_grund=True
    )

    assert result[0] is True
    assert result[2] == "Bedrohung"


def test_project_ban_data_grund_bleibt_leer_bei_defektem_token():
    """Pins ``x__project_ban_data__mutmut_46``/``…_48``/``…_56``: der
    ``default``-Wert von ``safe_decrypt`` für ``grund`` muss ``""`` bleiben
    — ein defektes ``grund``-Token darf einer berechtigten Rolle keinen
    Ersatztext (``None``/``"[verschlüsselt]"``/``"XXXX"``) zeigen."""
    data = {
        "aktiv": True,
        "bis": "",
        "grund": {"__encrypted__": True, "value": "invalid-token"},
    }

    result = _project_ban_data(data, today=date.today(), may_see_grund=True)

    assert result[2] == ""


def test_project_ban_data_grund_bleibt_leer_ohne_grund_feld():
    """Pins ``x__project_ban_data__mutmut_50``/``…_52``/``…_55``: der
    ``.get("grund", …)``-Fallback bei fehlendem Schlüssel muss ``""``
    bleiben statt ``None``/``"XXXX"``."""
    data = {"aktiv": True, "bis": ""}  # kein "grund"-Key

    result = _project_ban_data(data, today=date.today(), may_see_grund=True)

    assert result[2] == ""


# ---------------------------------------------------------------------
# get_active_bans
# ---------------------------------------------------------------------


def test_get_active_bans_ignoriert_dokumenttypen_ohne_system_type_ban(facility, client_identified, staff_user):
    """Pins ``x_get_active_bans__mutmut_4``: der ``system_type="ban"``-Filter
    auf ``DocumentType`` darf nicht entfallen — ein Event eines FREMDEN
    (aktiven) Dokumenttyps mit zufällig ban-förmiger ``data_json`` darf nicht
    als Hausverbot erscheinen."""
    sonstiger_typ = DocumentType.objects.create(facility=facility, name="Sonstiges (nicht Hausverbot)", is_active=True)
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=sonstiger_typ,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Verwechslung"},
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert bans == []


def test_get_active_bans_ignoriert_deaktivierten_hausverbot_dokumenttyp(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans__mutmut_5``: der ``is_active=True``-Filter
    auf ``DocumentType`` darf nicht entfallen — ein deaktivierter
    Hausverbot-Dokumenttyp darf keine Bans mehr liefern."""
    doc_type_hausverbot.is_active = False
    doc_type_hausverbot.save(update_fields=["is_active"])
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Deaktivierter Typ"},
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert bans == []


def test_get_active_bans_ignoriert_events_fremder_dokumenttypen(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans__mutmut_19``: der
    ``document_type__in=ban_doc_types``-Filter auf ``Event`` darf nicht
    entfallen — obwohl ein echter Hausverbot-Dokumenttyp existiert, darf ein
    Event eines FREMDEN Dokumenttyps nicht in die Auswertung einfließen."""
    sonstiger_typ = DocumentType.objects.create(facility=facility, name="Sonstiges (Events)", is_active=True)
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=sonstiger_typ,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Verwechslung Event"},
        created_by=staff_user,
    )

    bans = get_active_bans(facility, user=staff_user)

    assert bans == []


# ---------------------------------------------------------------------
# get_active_bans_for_client
# ---------------------------------------------------------------------


def test_get_active_bans_for_client_ignoriert_dokumenttypen_ohne_system_type_ban(
    facility, client_identified, staff_user
):
    """Pins ``x_get_active_bans_for_client__mutmut_5``: dieselbe
    ``system_type="ban"``-Lücke wie ``x_get_active_bans__mutmut_4``, hier auf
    der Pro-Klient-Variante."""
    sonstiger_typ = DocumentType.objects.create(facility=facility, name="Sonstiges (Klient)", is_active=True)
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=sonstiger_typ,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Verwechslung Klient"},
        created_by=staff_user,
    )

    bans = get_active_bans_for_client(client_identified, user=staff_user)

    assert bans == []


def test_get_active_bans_for_client_zeigt_keine_verbote_anderer_klienten(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans_for_client__mutmut_21`` (Prio 1): der
    ``client=client``-Filter darf nicht entfallen — ein Hausverbot EINES
    ANDEREN Klienten derselben Einrichtung darf niemals in der Antwort für
    ``client_identified`` auftauchen (klientenübergreifendes Datenleck)."""
    anderer_client = Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Test-ID-99",
        created_by=staff_user,
    )
    Event.objects.create(
        facility=facility,
        client=anderer_client,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Fremdverbot"},
        created_by=staff_user,
    )

    bans = get_active_bans_for_client(client_identified, user=staff_user)

    assert bans == []


def test_get_active_bans_for_client_ignoriert_events_fremder_dokumenttypen(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans_for_client__mutmut_22``: dieselbe
    ``document_type__in``-Lücke wie ``x_get_active_bans__mutmut_19``, hier
    auf der Pro-Klient-Variante."""
    sonstiger_typ = DocumentType.objects.create(facility=facility, name="Sonstiges (Klient-Events)", is_active=True)
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=sonstiger_typ,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Verwechslung Klient-Event"},
        created_by=staff_user,
    )

    bans = get_active_bans_for_client(client_identified, user=staff_user)

    assert bans == []


def test_get_active_bans_for_client_ignoriert_geloeschte_events(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans_for_client__mutmut_23``: der
    ``is_deleted=False``-Filter darf nicht entfallen — ein bereits
    gelöschtes Hausverbot-Event darf nicht mehr als aktives Verbot
    erscheinen."""
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Gelöschtes Verbot"},
        created_by=staff_user,
        is_deleted=True,
    )

    bans = get_active_bans_for_client(client_identified, user=staff_user)

    assert bans == []


def test_get_active_bans_for_client_liefert_keinen_client_schluessel(
    facility, client_identified, staff_user, doc_type_hausverbot
):
    """Pins ``x_get_active_bans_for_client__mutmut_38``:
    ``include_client_in_result=False`` darf nicht auf ``True`` kippen — die
    Pro-Klient-Variante liefert bewusst KEINEN ``"client"``-Schlüssel je
    Eintrag (der Aufrufer kennt den Klienten bereits)."""
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_hausverbot,
        occurred_at=timezone.now(),
        data_json={"aktiv": True, "bis": "", "grund": "Aktives Verbot"},
        created_by=staff_user,
    )

    bans = get_active_bans_for_client(client_identified, user=staff_user)

    assert len(bans) == 1
    assert "client" not in bans[0]
