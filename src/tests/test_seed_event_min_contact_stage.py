"""Regression fuer #2270: Bulk-Seed muss die Mindestkontaktstufe (``DocumentType.
min_contact_stage``) genauso durchsetzen wie der reguläre Erfassungspfad.

``seed_events_bulk`` (``core.seed.events``) zog Dokumentationstyp, Anonymitaet
(15-%-Zufall) und Person bislang unabhaengig voneinander — ein Typ mit
``min_contact_stage`` (z. B. das Standardpaket-``beratungsgespraech``) konnte
so anonym oder mit einer Person unterhalb der Stufe erzeugt werden, obwohl
``core.services.events.crud._validate_contact_stage`` genau das fuer die
reguläre Erfassung verbietet.

Referenz statt Kopie: die generierten Ereignisse werden gegen die ECHTE
``_validate_contact_stage`` aus ``services/events/crud.py`` geprueft (nur
lesend importiert, nicht veraendert).
"""

import random
from collections import Counter

import pytest

from core.models import Client, DocumentType, Event
from core.seed import doc_types as seed_doc_types
from core.seed.events import seed_events_bulk
from core.seed.users import seed_users
from core.services.events.crud import _validate_contact_stage

# Default-Paket-Gewichte (Refs constants.py): kontakt=40 (unbeschraenkt),
# beratungsgespraech=8 (min_contact_stage=qualified, sensitivity=elevated).
# Mit events_per_facility=200 und NUR diesen beiden Typen im Ziehungstopf
# liegt der Erwartungswert bei ~33 beratungsgespraech-Ereignissen — bei einem
# festen Seed-Wert reproduzierbar weit ueber 0.
EVENTS_PER_FACILITY = 200
CFG = {"events_per_facility": EVENTS_PER_FACILITY, "zeitraum_days": 90}


def _load_default_doc_types(facility, *, keep_slugs):
    """Standardpaket laden, danach auf ``keep_slugs`` eindampfen (Perf).

    ``doc_types.seed_document_types`` legt den vollen Standardkatalog an —
    fuers Test-Tempo werden alle nicht benoetigten Typen wieder geloescht.
    ``DocumentTypeField`` ist eine Zwischentabelle mit FK auf den jeweiligen
    Typ (nicht auf die geteilten ``FieldTemplate``-Zeilen) — das Loeschen
    ueberschuessiger Typen reisst also keine ``FieldTemplate``-Vorlagen mit,
    die ``beratungsgespraech``/``kontakt`` weiter brauchen.
    """
    seed_doc_types.seed_document_types(facility, package="default")
    DocumentType.objects.filter(facility=facility).exclude(slug__in=keep_slugs).delete()


def _make_clients(facility, staff_user, *, n_identified, n_qualified):
    clients = []
    for i in range(n_identified):
        clients.append(
            Client.objects.create(
                facility=facility,
                pseudonym=f"Ident-{i}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                created_by=staff_user,
            )
        )
    for i in range(n_qualified):
        clients.append(
            Client.objects.create(
                facility=facility,
                pseudonym=f"Qual-{i}",
                contact_stage=Client.ContactStage.QUALIFIED,
                created_by=staff_user,
            )
        )
    return clients


@pytest.mark.django_db
class TestSeedEventMinContactStage:
    def test_restricted_type_never_anonymous_and_only_qualified_clients(self, facility):
        """Guard fuer #2270: gemischte Personenstufen + Standardpaket-Typ mit
        Mindeststufe — JEDES generierte ``beratungsgespraech``-Ereignis muss
        denselben Vertrag erfuellen wie ``create_event`` ihn erzwingt."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=6, n_qualified=6)
        _load_default_doc_types(facility, keep_slugs=["beratungsgespraech", "kontakt"])

        random.seed(2270)
        created = seed_events_bulk(facility, users, clients, CFG, package="default")
        assert created == EVENTS_PER_FACILITY

        restricted_type = DocumentType.objects.get(facility=facility, slug="beratungsgespraech")
        restricted_events = list(Event.objects.filter(facility=facility, document_type=restricted_type))
        # Erwartungswert ~33 bei diesem Zuschnitt (s.o.) — >=1 haelt den Test
        # robust gegen RNG-Drift bei kuenftigen Aufrufreihenfolge-Aenderungen,
        # waehrend die Schleife darunter WIRKLICH jedes erzeugte Ereignis prueft.
        assert len(restricted_events) >= 1, "beratungsgespraech muss bei diesem Zuschnitt zuverlaessig entstehen"

        for event in restricted_events:
            assert event.is_anonymous is False, (
                f"Ereignis {event.pk}: anonymes Ereignis fuer Typ mit min_contact_stage erzeugt"
            )
            assert event.client is not None, f"Ereignis {event.pk}: keine Person trotz min_contact_stage"
            assert event.client.contact_stage == Client.ContactStage.QUALIFIED, (
                f"Ereignis {event.pk}: Person mit Stufe {event.client.contact_stage!r} "
                f"unterhalb der geforderten Stufe 'qualified'"
            )
            # Direkter Abgleich gegen den reguraeren Vertrag (nur lesend
            # referenziert) — darf fuer keines der generierten Ereignisse
            # feuern.
            _validate_contact_stage(event.document_type, event.client, event.is_anonymous)

    def test_unrestricted_type_stays_anonymous_capable(self, facility):
        """Typen OHNE ``min_contact_stage`` (``kontakt``) bleiben weiterhin
        anonym erzeugbar — die Einschraenkung darf nicht ueber den ganzen
        Bulk-Pfad hinweg greifen."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=6, n_qualified=6)
        _load_default_doc_types(facility, keep_slugs=["beratungsgespraech", "kontakt"])

        random.seed(2270)
        seed_events_bulk(facility, users, clients, CFG, package="default")

        unrestricted_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        unrestricted_events = Event.objects.filter(facility=facility, document_type=unrestricted_type)
        assert unrestricted_events.filter(is_anonymous=True).exists(), (
            "kontakt (kein min_contact_stage) muss bei ~15%-Anonymquote und "
            f"{EVENTS_PER_FACILITY} Ereignissen mindestens ein anonymes Ereignis enthalten"
        )

    def test_no_eligible_person_skips_restricted_type_but_keeps_unrestricted(self, facility):
        """Fall 'keine passende Person': alle Clients unterhalb der
        Mindeststufe — der Typ mit ``min_contact_stage`` wird uebersprungen,
        der unbeschraenkte Typ bleibt unangetastet erzeugbar."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=8, n_qualified=0)
        _load_default_doc_types(facility, keep_slugs=["beratungsgespraech", "kontakt"])

        random.seed(2270)
        created = seed_events_bulk(facility, users, clients, CFG, package="default")
        assert created == EVENTS_PER_FACILITY

        restricted_type = DocumentType.objects.get(facility=facility, slug="beratungsgespraech")
        assert not Event.objects.filter(facility=facility, document_type=restricted_type).exists(), (
            "ohne qualifizierte Person darf beratungsgespraech NICHT erzeugt werden"
        )
        unrestricted_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        assert Event.objects.filter(facility=facility, document_type=unrestricted_type).exists()

    def test_no_eligible_person_and_only_restricted_type_returns_zero(self, facility):
        """Bleibt nach dem Ausschluss KEIN Typ mehr uebrig, liefert der
        Bulk-Pfad explizit 0 (Fallback dokumentiert) statt eine unzulaessige
        Kombination zu erzeugen."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=8, n_qualified=0)
        _load_default_doc_types(facility, keep_slugs=["beratungsgespraech"])

        random.seed(2270)
        created = seed_events_bulk(facility, users, clients, CFG, package="default")
        assert created == 0
        assert not Event.objects.filter(facility=facility).exists()

    def test_bulk_seed_is_deterministic_for_a_fixed_seed_value(self, facility):
        """Gate-Pflicht (Standardblock Punkt 69/Briefing): zweimaliger Lauf mit
        identischem ``random.seed`` liefert ein identisches Multiset an
        Ereignissen. Deckt in einem Rutsch auch #2271 ab (``search_text`` ist
        Teil des Vergleichs).

        Verglichen wird ueber ``collections.Counter`` statt Ist-Reihenfolge:
        die Ereignis-PK ist ein zufaelliges UUID (``default=uuid.uuid4``, je
        ``Event()``-Konstruktion neu gewuerfelt, NICHT vom ``random``-Modul
        gespeist) und die DB-Rueckgabereihenfolge ohne ``ORDER BY`` ist nicht
        garantiert — ein Vergleich nach PK-Reihenfolge waere blind fuer echte
        Abweichungen und faelschlich rot bei blosser Retrieval-Umsortierung.
        ``occurred_at`` haengt an ``timezone.now()`` zum jeweiligen Aufrufzeit-
        punkt und bleibt deshalb bewusst aussen vor. Verschluesselte Feldwerte
        (z. B. ``thema`` bei ``beratungsgespraech``) tragen einen von Fernet
        PRO Aufruf neu gewuerfelten Nonce/Zeitstempel (nicht vom ``random``-
        Modul gespeist) — sie werden auf einen Encrypted-Marker normalisiert,
        sonst waere der Vergleich blind fuer eine echte Content-Abweichung
        UND faelschlich rot wegen der Verschluesselung selbst.
        """
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=6, n_qualified=6)
        _load_default_doc_types(facility, keep_slugs=["beratungsgespraech", "kontakt"])

        encrypted_slugs_by_dt = {
            dt.pk: {
                dtf.field_template.slug
                for dtf in dt.fields.select_related("field_template")
                if dtf.field_template.is_encrypted
            }
            for dt in DocumentType.objects.filter(facility=facility)
        }

        def _hashable(value):
            if isinstance(value, list):
                return tuple(_hashable(v) for v in value)
            if isinstance(value, dict):
                return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
            return value

        def _normalize_data_json(document_type_id, data_json):
            enc_slugs = encrypted_slugs_by_dt[document_type_id]
            return tuple(sorted((k, "<encrypted>" if k in enc_slugs else _hashable(v)) for k, v in data_json.items()))

        def _snapshot():
            counter = Counter()
            for event in Event.objects.filter(facility=facility):
                key = (
                    event.document_type_id,
                    event.client_id,
                    event.is_anonymous,
                    event.search_text,
                    _normalize_data_json(event.document_type_id, event.data_json),
                )
                counter[key] += 1
            return counter

        random.seed(2270)
        seed_events_bulk(facility, users, clients, CFG, package="default")
        first_run = _snapshot()
        assert sum(first_run.values()) == EVENTS_PER_FACILITY

        Event.objects.filter(facility=facility).delete()
        random.seed(2270)
        seed_events_bulk(facility, users, clients, CFG, package="default")
        second_run = _snapshot()

        assert first_run == second_run

    def test_every_eligible_package_type_appears_at_least_once_regardless_of_seed(self, facility):
        """Ruling Fix-Runde 1 (Orchestrator, Nachtrag zu #2270): Typabdeckung
        ist STRUKTURELL garantiert — nicht nur eine Eigenschaft eines
        guenstigen ``random.seed``-Werts. Macht
        ``test_seed.py::TestSharedMediumSeed::test_seed_medium_zweigstelle_nord_hat_ereignisse_je_paket_typ``
        unabhaengig vom fest codierten Seed 42 in ``management/commands/seed.py``:
        vorher war ``hausverbot`` (Gewicht 0,2 von ~99,2 im Standard-/
        Beratungsstelle-Paket) nur mit GLUECK bei einem bestimmten Seed
        dabei. Zwei verschiedene, willkuerliche Seed-Werte pruefen das —
        JEDER im Standardpaket erzeugbare Typ (alle haben hier eine
        zulaessige Person, da 6 Clients auf Stufe 'qualified' stehen) muss
        in JEDEM Lauf mindestens einmal vorkommen."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        clients = _make_clients(facility, staff_user, n_identified=6, n_qualified=6)
        # VOLLER Standardkatalog (nicht auf zwei Typen eingedampft) — die
        # Abdeckungszusage gilt fuer ALLE Typen des Pakets.
        seed_doc_types.seed_document_types(facility, package="default")
        expected_slugs = set(
            DocumentType.objects.filter(facility=facility, entity_type=DocumentType.EntityType.EVENT).values_list(
                "slug", flat=True
            )
        )
        assert len(expected_slugs) > 1, "Testaufbau: Standardkatalog muss mehrere Event-Typen liefern"

        for seed_value in (2270, 987654):
            Event.objects.filter(facility=facility).delete()
            random.seed(seed_value)
            created = seed_events_bulk(facility, users, clients, CFG, package="default")
            assert created == EVENTS_PER_FACILITY

            created_slugs = set(Event.objects.filter(facility=facility).values_list("document_type__slug", flat=True))
            missing = expected_slugs - created_slugs
            assert not missing, f"Seed {seed_value}: Typ(en) ohne jedes Ereignis: {sorted(missing)}"
