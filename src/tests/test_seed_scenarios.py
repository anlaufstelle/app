"""Tests for the shared demo scenario catalog and ``--scale demo`` (Refs #2279).

Deckt die Auftragspunkte aus dem Ticket ab: Szenariovollständigkeit
(Katalog + tatsächlich angelegte Facilities), fachliche Konsistenz (Paket ↔
Facility ↔ Typabdeckung), Einrichtungstrennung (keine Vermischung von Klient*
-innen/Fällen/Events zwischen den sechs Bereichen, Demo-Rollen entsprechen den
tatsächlich angelegten Konten), Idempotenz und Wiederherstellung nach einem
Reset (``--flush --scale demo``, wie ``dev-ops/deploy/demo-reseed.sh`` es
stündlich aufruft).
"""

import pytest
from django.core.management import call_command

# Refs #610: seed_events_bulk/attach_files_to_counseling_events brauchen
# libmagic fuer Datei-Uploads — ohne lauffaehige Bibliothek ist der Command
# insgesamt nicht nutzbar (gleiches Skip-Muster wie test_seed.py).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffähig ({_libmagic_exc}) — Seed-Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import Case, Client, DocumentType, Event, Facility, User  # noqa: E402
from core.packages.catalog import list_packages  # noqa: E402
from core.seed.scenarios import SCENARIOS, DemoScenario, get_scenario, scenario_keys  # noqa: E402

EXPECTED_KEYS = {
    "kontaktladen",
    "beratungsstelle",
    "solo",
    "awareness",
    "jugendarbeit",
    "frauenhaus",
}


# ---------------------------------------------------------------------------
# Szenariovollständigkeit (Katalog selbst, ohne DB)
# ---------------------------------------------------------------------------


def test_scenario_catalog_has_exactly_the_six_ticket_areas():
    """Der Katalog deckt genau die sechs im Ticket genannten Bereiche ab."""
    assert set(scenario_keys()) == EXPECTED_KEYS
    assert len(SCENARIOS) == 6


def test_scenario_keys_are_unique():
    assert len(scenario_keys()) == len(set(scenario_keys()))


def test_scenario_facility_names_are_unique_and_not_the_legacy_zweigstelle_names():
    """Aussagekräftige Namen statt bloß „Zweigstelle Nord/Süd" (Ticket-Punkt 3)."""
    names = [scenario.facility_name for scenario in SCENARIOS]
    assert len(names) == len(set(names)), "Facility-Namen müssen eindeutig sein."
    legacy_names = {"Zweigstelle Nord", "Zweigstelle Süd", "Außenstelle Ost", "Außenstelle West"}
    assert not (set(names) & legacy_names), "Szenario-Namen dürfen nicht die alten Platzhalternamen wiederverwenden."


def test_get_scenario_returns_matching_entry():
    for scenario in SCENARIOS:
        assert get_scenario(scenario.key) is scenario


def test_get_scenario_unknown_key_raises_keyerror():
    with pytest.raises(KeyError):
        get_scenario("does-not-exist")


def test_scenario_solo_builds_on_beratungsstelle_package_but_has_its_own_facility():
    """Ticket: Solo baut fachlich auf der Beratungsstelle auf, braucht aber
    eine eigenständige Beispieleinrichtung."""
    solo = get_scenario("solo")
    beratungsstelle = get_scenario("beratungsstelle")
    assert solo.package == beratungsstelle.package == "beratungsstelle"
    assert solo.facility_name != beratungsstelle.facility_name
    assert solo.solo is True
    assert beratungsstelle.solo is False


# ---------------------------------------------------------------------------
# Fachliche Konsistenz (Katalog ↔ Paket-Katalog, ohne DB)
# ---------------------------------------------------------------------------


def test_scenario_packages_exist_in_package_catalog():
    available = set(list_packages())
    for scenario in SCENARIOS:
        assert scenario.package in available, f"{scenario.key}: Paket {scenario.package!r} fehlt im Katalog."


def test_scenario_demo_roles_are_valid_user_roles():
    valid_roles = set(User.Role.values)
    for scenario in SCENARIOS:
        assert scenario.demo_roles, f"{scenario.key}: mindestens eine Demo-Rolle noetig."
        assert set(scenario.demo_roles) <= valid_roles, scenario.key
        # SUPER_ADMIN ist installationsweit, nicht an eine Demo-Facility
        # gebunden (Refs #867) — kein Szenario darf ihn als Login anbieten.
        assert User.Role.SUPER_ADMIN not in scenario.demo_roles, scenario.key


def test_scenario_solo_offers_only_the_roles_it_actually_seeds():
    """Ticket: keine künstlichen Schichtübergaben — die Auswahlseite (#2285)
    darf für Solo keine ``lead``/``assistant``-Rolle anbieten, die es dort
    gar nicht gibt."""
    solo = get_scenario("solo")
    assert set(solo.demo_roles) == {User.Role.STAFF, User.Role.FACILITY_ADMIN}


def test_scenario_is_frozen_dataclass():
    """Der Katalog ist eine feste Konfiguration, kein Laufzeitzustand."""
    scenario = SCENARIOS[0]
    with pytest.raises(Exception):  # noqa: B017 — dataclasses.FrozenInstanceError ist eine AttributeError-Unterklasse
        scenario.key = "anders"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Fixtures: einmalig seeden, mehrere Tests lesen (Laufzeit, analog
# ``_medium_seed``/``_large_seed`` in test_seed.py).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _demo_seed(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)
        call_command("seed", scale="demo")


@pytest.fixture(scope="class")
def _demo_seed_class(_demo_seed, django_db_blocker):
    yield
    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)


@pytest.mark.django_db
class TestSeedDemoScale:
    """Alle Assertions gegen EINEN gemeinsamen ``--scale demo``-Lauf."""

    def test_creates_exactly_the_six_scenario_facilities(self, _demo_seed_class):
        expected_names = {scenario.facility_name for scenario in SCENARIOS}
        actual_names = set(Facility.objects.values_list("name", flat=True))
        assert actual_names == expected_names
        assert Facility.objects.count() == 6

    def test_every_facility_uses_its_scenario_package(self, _demo_seed_class):
        """Fachliche Konsistenz: die je Facility angelegten DocumentTypes
        stammen aus dem im Katalog hinterlegten Paket — Stichprobe über einen
        paket-eindeutigen Slug (Refs #2148 R-L5c-2: Slug ist der Loader-Key).
        """
        beratungsstelle_facility = Facility.objects.get(name=get_scenario("beratungsstelle").facility_name)
        kontaktladen_facility = Facility.objects.get(name=get_scenario("kontaktladen").facility_name)
        beratungsstelle_slugs = set(
            DocumentType.objects.filter(facility=beratungsstelle_facility).values_list("slug", flat=True)
        )
        kontaktladen_slugs = set(
            DocumentType.objects.filter(facility=kontaktladen_facility).values_list("slug", flat=True)
        )
        # beratungsstelle.yaml ist Ersatz, kein Zusatz (docs/packages-beratungsstelle.md):
        # der Standardkatalog-Typ "spritzentausch" darf dort NICHT vorkommen.
        assert "spritzentausch" not in beratungsstelle_slugs
        assert "kurzberatung" in beratungsstelle_slugs
        # Kontaktladen (Paket "default") hat umgekehrt den Standardkatalog.
        assert "spritzentausch" in kontaktladen_slugs
        assert "kurzberatung" not in kontaktladen_slugs

    def test_solo_facility_has_reduced_user_set(self, _demo_seed_class):
        """Ticket: eigenständige Solo-Daten statt Team-Volumen — genau zwei
        Konten (Fachkraft + Vertretung mit Freigabebefugnis), keine
        lead/assistant-Konten."""
        solo_facility = Facility.objects.get(name=get_scenario("solo").facility_name)
        users = list(User.objects.filter(facility=solo_facility))
        assert len(users) == 2
        roles = {u.role for u in users}
        assert roles == {User.Role.STAFF, User.Role.FACILITY_ADMIN}
        admin_users = [u for u in users if u.role == User.Role.FACILITY_ADMIN]
        assert admin_users[0].can_confirm_deletion is True, "Vertretung muss Loeschungen freigeben koennen."

    def test_non_solo_facilities_keep_the_full_six_user_team(self, _demo_seed_class):
        for scenario in SCENARIOS:
            if scenario.solo:
                continue
            facility = Facility.objects.get(name=scenario.facility_name)
            assert User.objects.filter(facility=facility).count() == 6, scenario.key

    def test_solo_facility_has_smaller_data_volume_than_team_facilities(self, _demo_seed_class):
        """Ticket: Solo braucht eigenständige (plausibel kleinere) Daten,
        keine künstlich aufgeblasene Team-Menge."""
        solo_facility = Facility.objects.get(name=get_scenario("solo").facility_name)
        team_facility = Facility.objects.get(name=get_scenario("beratungsstelle").facility_name)
        assert (
            Client.objects.filter(facility=solo_facility).count()
            < Client.objects.filter(facility=team_facility).count()
        )
        assert (
            Event.objects.filter(facility=solo_facility).count() < Event.objects.filter(facility=team_facility).count()
        )

    def test_every_facility_has_at_least_one_event_per_own_event_document_type(self, _demo_seed_class):
        """Zusage aus Lane F-1 (Refs #2270/#2279-Zusatz): je Einrichtung ×
        Paket-Typ mindestens ein Ereignis — gilt unverändert für alle sechs
        Szenario-Facilities, auch wenn zwei davon (Solo/Beratungsstelle)
        dasselbe Fachpaket teilen."""
        for scenario in SCENARIOS:
            facility = Facility.objects.get(name=scenario.facility_name)
            event_doc_types = DocumentType.objects.filter(facility=facility, entity_type=DocumentType.EntityType.EVENT)
            assert event_doc_types.exists(), scenario.key
            for doc_type in event_doc_types:
                assert Event.objects.filter(facility=facility, document_type=doc_type).exists(), (
                    f"{scenario.key}: Typ {doc_type.slug!r} ohne Ereignis."
                )

    def test_facility_separation_no_cross_facility_leakage(self, _demo_seed_class):
        """Einrichtungstrennung: Summe der Klient*innen/Fälle/Ereignisse je
        Facility ergibt genau die Gesamtzahl — keine Zeile gehört zu keiner
        oder zu mehr als einer Facility."""
        facilities = list(Facility.objects.filter(name__in=[s.facility_name for s in SCENARIOS]))
        assert len(facilities) == 6
        client_sum = sum(Client.objects.filter(facility=f).count() for f in facilities)
        case_sum = sum(Case.objects.filter(facility=f).count() for f in facilities)
        event_sum = sum(Event.objects.filter(facility=f).count() for f in facilities)
        assert client_sum == Client.objects.filter(facility__in=facilities).count()
        assert case_sum == Case.objects.filter(facility__in=facilities).count()
        assert event_sum == Event.objects.filter(facility__in=facilities).count()
        # Jeder Client/Case/Event gehört zu genau EINER Facility (FK, kein
        # geteilter Datensatz) — Pseudonym-Kollisionen ZWISCHEN Facilities
        # sind dagegen fachlich normal und erlaubt: ``seed_clients_bulk``
        # zieht je Facility unabhängig aus demselben Spitznamen-Pool
        # (``core/seed/constants.py:SPITZNAMEN``), Eindeutigkeit gilt nur
        # innerhalb einer Facility (``clients.py``: Dedup-Set ist je Facility
        # gefiltert), nicht global.

    def test_demo_roles_match_actually_seeded_roles_per_facility(self, _demo_seed_class):
        """Fachliche Konsistenz zwischen Katalog-Metadaten und Ist-Daten:
        ``demo_roles`` darf keine Rolle versprechen, die in der zugehörigen
        Facility gar nicht existiert (sonst böte #2285 einen toten Login an)."""
        for scenario in SCENARIOS:
            facility = Facility.objects.get(name=scenario.facility_name)
            actual_roles = set(User.objects.filter(facility=facility).values_list("role", flat=True))
            assert actual_roles == set(scenario.demo_roles), scenario.key


# ---------------------------------------------------------------------------
# Idempotenz und Wiederherstellung nach Reset (eigene, kleinere Läufe)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_demo_is_idempotent():
    """Zweiter Lauf ohne --flush verändert Facilities/Users/Events nicht
    (analog den bestehenden Idempotenz-Tests in test_seed.py, hier für
    ``demo``). ``Event`` ist zielzahl-gebunden (``seed_events_bulk`` bricht
    ab, sobald ``existing_count >= target``) und damit robust idempotent.

    Bewusst NICHT geprüft: die ``Client``-Zahl — vorbestehendes, dokumentiertes
    Verhalten (siehe ``test_seed_medium_doppellauf_ist_idempotent_fuer_zweigstelle_nord``
    in ``test_seed.py``): ``seed_clients_bulk`` mischt ihren Pseudonym-Pool bei
    jedem Lauf neu und dedupliziert nur gegen bereits VORHANDENE Namen: weil
    andere Seed-Schritte zwischen zwei Läufen unterschiedlich viele
    ``random.*()``-Aufrufe konsumieren, driftet der Zufallsstand vor dem
    Mischen auseinander und ein zweiter Lauf kann zusätzliche Personen
    anlegen. Nicht Teil von #2279 (``clients.py`` ist nicht in der
    Besitzliste dieser Lane) — hier nur reproduziert, nicht "gefixt"."""
    call_command("seed", scale="demo")
    facilities_first = Facility.objects.count()
    users_first = User.objects.count()
    events_first = Event.objects.count()

    call_command("seed", scale="demo")

    assert Facility.objects.count() == facilities_first
    assert User.objects.count() == users_first
    assert Event.objects.count() == events_first


@pytest.mark.django_db
def test_seed_demo_flush_reseed_is_deterministic():
    """Determinismus-Beleg: zwei UNABHÄNGIGE ``--flush --scale demo``-Läufe
    (jeweils von einer geleerten DB aus) liefern je Szenario-Facility
    identische Zählwerte für Konten, Personen, Ereignisse und Fälle — genau
    die Eigenschaft, auf der der stündliche Demo-Reset beruht (Ticket-
    Punkt 7: nach Reset sind alle Bereiche wieder vollständig UND
    gleichwertig nutzbar).

    Bewusst ANDERS als ``test_seed_demo_is_idempotent`` oben: dieser Test
    prüft NICHT den bekannten, in ``test_seed.py``
    (``test_seed_medium_doppellauf_ist_idempotent_fuer_zweigstelle_nord``)
    dokumentierten Random-Drift, der nur bei einem ZWEITEN Lauf OHNE
    ``--flush`` auf denselben (nicht geleerten) Bestand auftritt — dort
    weichen die Zufallsaufrufe zwischen den Läufen ab, weil idempotente
    Schritte beim zweiten Mal Arbeit überspringen. Hier startet JEDER Lauf
    von einer geleerten DB (``--flush``): ``random.seed(42)`` läuft beide
    Male durch dieselbe Aufrufsequenz, inklusive ``seed_clients_bulk``s
    Pseudonym-Shuffle — die ``Client``-Zahl ist deshalb hier (anders als im
    No-Flush-Fall) ebenfalls deterministisch und wird bewusst MIT geprüft
    (empirisch verifiziert: 99/99 Clients, 660/660 Events über beide Läufe
    identisch, siehe Bericht Fix-Runde 1).
    """
    from core.models import Case

    def _counts_per_facility() -> dict[str, tuple[int, int, int, int]]:
        return {
            facility.name: (
                User.objects.filter(facility=facility).count(),
                Client.objects.filter(facility=facility).count(),
                Event.objects.filter(facility=facility).count(),
                Case.objects.filter(facility=facility).count(),
            )
            for facility in Facility.objects.all()
        }

    call_command("seed", scale="demo", flush=True)
    facilities_first = set(Facility.objects.values_list("name", flat=True))
    counts_first = _counts_per_facility()

    call_command("seed", scale="demo", flush=True)
    facilities_second = set(Facility.objects.values_list("name", flat=True))
    counts_second = _counts_per_facility()

    assert facilities_first == facilities_second == {scenario.facility_name for scenario in SCENARIOS}
    assert counts_first == counts_second, (
        f"Zählwerte (Users, Clients, Events, Cases) je Facility weichen zwischen zwei "
        f"unabhängigen --flush --scale demo-Läufen ab: {counts_first} vs. {counts_second}"
    )


@pytest.mark.django_db
def test_seed_demo_flush_restores_all_scenarios():
    """Wiederherstellung nach Reset: genau der Aufruf, den
    ``dev-ops/deploy/demo-reseed.sh`` stündlich fährt. Nach dem Reset sind
    wieder ALLE sechs Bereiche vollständig nutzbar, mit frischen Zeilen."""
    call_command("seed", scale="demo")
    first_user_ids = set(User.objects.values_list("id", flat=True))
    first_client_ids = set(Client.objects.values_list("id", flat=True))

    call_command("seed", scale="demo", flush=True)

    assert Facility.objects.count() == 6
    for scenario in SCENARIOS:
        assert Facility.objects.filter(name=scenario.facility_name).exists(), scenario.key
    second_user_ids = set(User.objects.values_list("id", flat=True))
    second_client_ids = set(Client.objects.values_list("id", flat=True))
    # Flush + Neuanlage: neue Primary Keys, keine liegengebliebenen Zeilen.
    assert first_user_ids.isdisjoint(second_user_ids)
    assert first_client_ids.isdisjoint(second_client_ids)


@pytest.mark.django_db
def test_seed_demo_visitor_selection_does_not_trigger_a_reseed():
    """Ticket-Punkt 7: Besucherauswahl löst KEINEN globalen Reseed aus —
    hier als Vertrag geprüft: das bloße Nachschlagen eines Szenarios
    (``get_scenario``, wie #2285 es für die Auswahlseite täte) schreibt
    nichts in die Datenbank."""
    call_command("seed", scale="demo")
    events_before = Event.objects.count()
    facilities_before = Facility.objects.count()

    for scenario in SCENARIOS:
        get_scenario(scenario.key)

    assert Event.objects.count() == events_before
    assert Facility.objects.count() == facilities_before


# ---------------------------------------------------------------------------
# Kompatibilität mit bestehenden DEV-/Test-Skalen (Regressionsanker)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_demo_choice_is_additive_small_scale_still_default():
    """``--scale demo`` ist eine zusätzliche Wahlmöglichkeit — ohne
    ``--scale`` bleibt ``small`` unverändert der Default (Ticket-Punkt 3:
    bestehende Skalen kompatibel halten)."""
    call_command("seed")
    assert Facility.objects.count() == 1
    assert Facility.objects.filter(name="Hauptstelle").exists()


def test_demo_scenario_type_is_exported_for_lane_f3():
    """Schnittstellen-Vertrag für #2285 (Lane F-3, hier nur dokumentiert):
    ``DemoScenario`` ist ein oeffentlich importierbarer Typ mit den fuer die
    Auswahlseite benoetigten Feldern."""
    scenario = SCENARIOS[0]
    assert isinstance(scenario, DemoScenario)
    for field in ("key", "display_name", "description", "package", "facility_name", "demo_roles", "intro_hint"):
        assert hasattr(scenario, field)
