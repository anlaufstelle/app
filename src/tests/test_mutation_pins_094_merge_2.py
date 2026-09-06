"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.client.merge`` (Batch ``merge_2``).

Quelle: Survivor-Triage des Moduls ``src/core/services/client/merge.py``
(Mutantenbaum ``mutants/src/core/services/client/merge.py``). Gepinnt wird das
triagierte, als killbar bestaetigte Set aus
der Survivor-Triage release-0.94 (Ledger des Meilensteins) (37 Mutanten, Herkunft: Survivor-Liste
zum Cut v0.91.0). Zwei Bloecke:

* :func:`~core.services.client.merge._validate_merge` — der komplette
  Meldungstext ALLER sieben Vorbedingungen. Die bestehenden Tests in
  ``test_client_merge_workflow.py::TestMergeValidation`` pruefen nur
  ``pytest.raises(ValidationError)``, nie die Nachricht. Das ist nicht bloss
  Kosmetik: die ``_( ... ) -> _(None)``-Mutanten (``mutmut_3``, ``_9``,
  ``_26``, ``_47``, ``_54``) erzeugen ein ``gettext_lazy(None)``, das erst beim
  Stringifizieren — genau dem Pfad, den ``core/views/client_merge.py`` fuer
  ``exc.messages[0]`` als Flash-Meldung nutzt — mit
  ``AttributeError: 'NoneType' object has no attribute 'replace'``
  hochgeht. Aus einer freundlichen 302-Fehlermeldung wuerde ein ungefangener
  500er. Die Varianten ``ValidationError(None)`` (``mutmut_8``, ``_25``,
  ``_31``, ``_46``, ``_53``) liefern statt der Erklaerung die Zeichenkette
  ``"None"``.
* :func:`~core.services.client.merge.get_pending_merge_request` — die drei
  Filter-Kwargs der Antragssuche (Einrichtung, Zieltyp, Ziel-ID) und die
  beiden Eager-Joins. Der ``target_id``-Mutant (``mutmut_12``) ist ein echter
  Funktionsfehler: ohne ihn liefert ``.first()`` unter
  ``Meta.ordering = ["-created_at"]`` den JUENGSTEN Merge-Antrag der
  Einrichtung — also den einer FREMDEN Person. Alle Bestandstests halten pro
  Einrichtung hoechstens EINEN PENDING-Merge-Antrag, dem faellt das nie auf.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); der
Sprachkontext wird bei Texten per ``translation.override("de")``
festgenagelt; KEINE Imports aus ``test_rls_functional``; Fixtures kommen aus
``src/tests/conftest.py`` bzw. werden als echte Model-Objekte angelegt (keine
Fakes). Jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der
beim Mutanten abweicht.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

from core.models import Client, LegalHold
from core.models.workitem import DeletionRequest
from core.services.client import request_client_deletion
from core.services.client.merge import (
    get_pending_merge_request,
    merge_clients,
    request_client_merge,
)

pytestmark = pytest.mark.django_db


def _identified(facility, user, pseudonym):
    """Eine echte, direkt zusammenfuehrbare Person (Stufe ``IDENTIFIED``)."""
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


@pytest.fixture
def merge_source(facility, staff_user):
    return _identified(facility, staff_user, "Pin-Merge-Quelle")


@pytest.fixture
def merge_target(facility, staff_user):
    return _identified(facility, staff_user, "Pin-Merge-Ziel")


@pytest.fixture
def merge_third(facility, staff_user):
    return _identified(facility, staff_user, "Pin-Merge-Dritte")


# ============================================================================
# _validate_merge — die Meldungstexte aller sieben Vorbedingungen (32 Mutanten)
# ============================================================================


class TestValidateMergeSelfMerge:
    """Pins: ``x__validate_merge__mutmut_3/4/5/6`` (Zeile 394).

    ``mutmut_3`` ersetzt den Meldungstext durch ``_(None)``, ``mutmut_4``
    umschliesst ihn mit den mutmut-Markern (``"XX...XX"``), ``mutmut_5``/
    ``mutmut_6`` schreiben ihn komplett klein bzw. komplett gross. Der
    Bestandstest ``TestMergeValidation.test_refuses_self_merge`` prueft nur den
    Ausnahmetyp und ueberlebt alle vier.

    Abweichender Wert: Original ``["Eine Person kann nicht mit sich selbst
    zusammengeführt werden."]``; ``mutmut_4`` liefert
    ``["XXEine Person kann nicht mit sich selbst zusammengeführt werden.XX"]``,
    ``mutmut_5`` die Kleinschreibung, ``mutmut_6`` die Grossschreibung, und
    ``mutmut_3`` wirft beim Stringifizieren ``AttributeError``.
    """

    def test_self_merge_message_is_exact(self, merge_source, lead_user):
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_source, lead_user)
            assert exc.value.messages == ["Eine Person kann nicht mit sich selbst zusammengeführt werden."]


class TestValidateMergeCrossFacility:
    """Pins: ``x__validate_merge__mutmut_8/9`` (Zeile 396).

    ``mutmut_8`` ersetzt den GESAMTEN ``_()``-Aufruf durch ``None``
    (``raise ValidationError(None)``), ``mutmut_9`` nur den Text (``_(None)``).

    Abweichender Wert: Original ``["Beide Personen müssen zur selben
    Einrichtung gehören."]``; ``mutmut_8`` liefert ``["None"]``, ``mutmut_9``
    wirft beim Stringifizieren ``AttributeError``.
    """

    def test_cross_facility_message_is_exact(self, merge_source, second_facility, lead_user):
        other = Client.objects.create(
            facility=second_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-Fremde-Einrichtung",
        )
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, other, lead_user)
            assert exc.value.messages == ["Beide Personen müssen zur selben Einrichtung gehören."]


class TestValidateMergeAnonymized:
    """Pin: ``x__validate_merge__mutmut_23`` (Zeile 401).

    Der Text der Anonymisierten-Sperre wird komplett grossgeschrieben. Der
    Bestandstest ``test_refuses_anonymized_participant`` prueft nur den Typ.

    Abweichender Wert: Original ``["Anonymisierte Personen können nicht
    zusammengeführt werden."]``; der Mutant liefert ``["ANONYMISIERTE PERSONEN
    KÖNNEN NICHT ZUSAMMENGEFÜHRT WERDEN."]``.
    """

    def test_anonymized_message_is_exact(self, merge_source, merge_target, lead_user):
        merge_target.is_active = False
        merge_target.save(update_fields=["is_active"])
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_target, lead_user)
            assert exc.value.messages == ["Anonymisierte Personen können nicht zusammengeführt werden."]


class TestValidateMergeAlreadyMerged:
    """Pins: ``x__validate_merge__mutmut_25/26/27/28/29`` (Zeile 403).

    ``mutmut_25`` = ``raise ValidationError(None)``, ``mutmut_26`` = ``_(None)``,
    ``mutmut_27`` = mutmut-Marker-Umschluss, ``mutmut_28``/``mutmut_29`` =
    Klein-/Grossschreibung. Fuer diesen Zweig gibt es ueberhaupt keinen
    Bestandstest: nach einem regulaeren Merge ist die Quelle zusaetzlich
    soft-geloescht, sodass immer schon der Papierkorb-Check davor auslaest.
    Hier traegt die Quelle den ``merged_into``-Verweis OHNE Papierkorb-Flag —
    der einzige Zustand, der die dritte Bedingung erreicht.

    Abweichender Wert: Original ``["Diese Person ist bereits
    zusammengeführt."]``; ``mutmut_25`` liefert ``["None"]``, ``mutmut_27``
    ``["XXDiese Person ist bereits zusammengeführt.XX"]``, ``mutmut_28``/``_29``
    die Klein-/Grossschreibung, ``mutmut_26`` wirft ``AttributeError``.
    """

    def test_already_merged_message_is_exact(self, merge_source, merge_target, merge_third, lead_user):
        merge_source.merged_into = merge_third
        merge_source.save(update_fields=["merged_into"])
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_target, lead_user)
            assert exc.value.messages == ["Diese Person ist bereits zusammengeführt."]


class TestValidateMergeLegalHold:
    """Pins: ``x__validate_merge__mutmut_31/33/34/35/36/37/38`` (Zeilen 406-409).

    ``mutmut_31`` ersetzt den gesamten ``_()``-Aufruf durch ``None``;
    ``mutmut_33/34/35`` mutieren die ERSTE, ``mutmut_36/37/38`` die ZWEITE
    Haelfte des verketteten Textes (Marker-Umschluss, Klein-, Grossschreibung).
    Ein Vergleich des VOLLEN, zusammengesetzten Strings trifft beide Haelften.
    Der Bestandstest ``test_refuses_participant_under_legal_hold`` prueft nur
    den Ausnahmetyp.

    Abweichender Wert: Original ``["Für eine der beiden Personen besteht eine
    Aufbewahrungssperre (Legal Hold) — Zusammenführen nicht möglich."]``;
    ``mutmut_31`` liefert ``["None"]``, ``mutmut_33`` schiebt ``XX``-Marker in
    die erste Haelfte, ``mutmut_36`` in die zweite, die uebrigen aendern die
    Gross-/Kleinschreibung je einer Haelfte.
    """

    def test_legal_hold_message_is_exact(self, merge_source, merge_target, lead_user):
        LegalHold.objects.create(
            facility=merge_source.facility,
            target_type="Client",
            target_id=merge_source.pk,
            reason="Gerichtsverfahren",
            created_by=lead_user,
        )
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_target, lead_user)
            assert exc.value.messages == [
                "Für eine der beiden Personen besteht eine Aufbewahrungssperre "
                "(Legal Hold) — Zusammenführen nicht möglich."
            ]


class TestValidateMergePendingRequest:
    """Pins: ``x__validate_merge__mutmut_46/47/48/49/50`` (Zeile 414).

    ``mutmut_46`` = ``raise ValidationError(None)``, ``mutmut_47`` = ``_(None)``,
    ``mutmut_48`` = Marker-Umschluss, ``mutmut_49``/``mutmut_50`` =
    Klein-/Grossschreibung. Der Bestandstest
    ``test_refuses_pending_deletion_request`` prueft nur den Ausnahmetyp.

    Abweichender Wert: Original ``["Für eine der beiden Personen läuft bereits
    ein Lösch- oder Zusammenführungs-Antrag."]``; ``mutmut_46`` liefert
    ``["None"]``, ``mutmut_48`` die ``XX``-Marker-Fassung, ``mutmut_49``/``_50``
    Klein-/Grossschreibung, ``mutmut_47`` wirft ``AttributeError``.
    """

    def test_pending_request_message_is_exact(self, merge_source, merge_target, staff_user, lead_user):
        request_client_deletion(merge_source, staff_user, reason="Antrag laeuft")
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_target, lead_user)
            assert exc.value.messages == [
                "Für eine der beiden Personen läuft bereits ein Lösch- oder Zusammenführungs-Antrag."
            ]


class TestValidateMergeChainGuard:
    """Pins: ``x__validate_merge__mutmut_53/54/55/56/57/58/59/60`` (Zeilen 428-431).

    ``mutmut_53`` ersetzt den gesamten ``_()``-Aufruf durch ``None``,
    ``mutmut_54`` den verketteten Inhalt (``_(None)``); ``mutmut_55/56/57``
    mutieren die ERSTE, ``mutmut_58/59/60`` die ZWEITE Haelfte des Textes. Der
    Vergleich des vollen Strings trifft alle acht.

    Aufbau: ``merge_third`` wandert per regulaerem Merge in ``merge_source`` —
    danach traegt ``merge_source`` einen noch rueckgaengig machbaren
    Tombstone (``merged_sources``, ``is_active=True``), und der Ketten-Guard
    verbietet den Folge-Merge ``merge_source -> merge_target``.

    Abweichender Wert: Original ``["Diese Person trägt eine noch rückgängig
    machbare Zusammenführung — erst aufheben oder den Ablauf der
    Papierkorb-Frist abwarten."]``; ``mutmut_53`` liefert ``["None"]``,
    ``mutmut_55``/``mutmut_58`` schieben ``XX``-Marker in die jeweilige
    Haelfte, ``mutmut_56/57/59/60`` aendern deren Gross-/Kleinschreibung,
    ``mutmut_54`` wirft beim Stringifizieren ``AttributeError``.
    """

    def test_chain_guard_message_is_exact(self, merge_source, merge_target, merge_third, lead_user):
        merge_clients(merge_third, merge_source, lead_user)
        merge_third.refresh_from_db()
        assert merge_third.merged_into_id == merge_source.pk
        assert merge_third.is_active is True

        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                merge_clients(merge_source, merge_target, lead_user)
            assert exc.value.messages == [
                "Diese Person trägt eine noch rückgängig machbare Zusammenführung — "
                "erst aufheben oder den Ablauf der Papierkorb-Frist abwarten."
            ]


# ============================================================================
# get_pending_merge_request — Filter-Klammer und Eager-Joins (5 Mutanten)
# ============================================================================


class TestGetPendingMergeRequestLookup:
    """Die Antragssuche der Quelle — Filter und Vorabladen."""

    def test_eager_loads_requester_and_merge_target(
        self, merge_source, merge_target, staff_user, django_assert_num_queries
    ):
        """Pins: ``x_get_pending_merge_request__mutmut_3/4`` (Zeile 138).

        ``mutmut_3`` streicht ``requested_by`` aus ``select_related``,
        ``mutmut_4`` streicht ``merge_into``. Beide veraendern den
        Rueckgabewert nicht — beobachtbar ist nur die Query-Zahl, und kein
        Bestandstest zaehlt hier Queries. Der Produktions-Docstring nennt nur
        das Ziel; die realen Anzeigeflaechen (Templates) zeigen aber
        Antragsteller UND Ziel — genau das ist die Zusicherung.

        Abweichender Wert: Original braucht fuer Lookup samt beider
        FK-Zugriffe GENAU 1 Query (ein SELECT mit zwei LEFT JOINs); unter
        ``mutmut_3`` laedt ``.requested_by``, unter ``mutmut_4``
        ``.merge_into`` per zusaetzlicher Lazy-Query nach — der
        ``django_assert_num_queries(1)``-Block schlaegt mit 2 Queries fehl.
        """
        dr = request_client_merge(merge_source, merge_target, staff_user, reason="Doppelt")

        with django_assert_num_queries(1):
            found = get_pending_merge_request(merge_source)
            assert found.pk == dr.pk
            assert found.requested_by == staff_user
            assert found.merge_into == merge_target

    def test_ignores_request_of_another_facility(self, merge_source, second_facility, foreign_client, foreign_staff):
        """Pin: ``x_get_pending_merge_request__mutmut_10`` (Zeile 132).

        Der Mutant streicht ``facility_id=client.facility_id`` ersatzlos. Der
        ``FacilityScopedManager`` von ``DeletionRequest`` filtert von sich aus
        NICHT nach Einrichtung (nur ``.for_facility()`` tut das) — der Kwarg
        ist die einzige Mandantengrenze dieser Query.

        Abweichender Wert: Original liefert ``None``; unter ``mutmut_10``
        liefert die Suche den PENDING-Merge-Antrag der FREMDEN Einrichtung
        (dessen ``target_id`` zufaellig auf unsere Person zeigt) und damit ein
        Objekt statt ``None``.
        """
        fremd = DeletionRequest.objects.create(
            facility=second_facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=merge_source.pk,
            reason="Fremder Antrag",
            requested_by=foreign_staff,
            merge_into=foreign_client,
        )
        assert fremd.status == DeletionRequest.Status.PENDING
        # Wirkungsnachweis: OHNE die Einrichtungs-Klammer ist die Zeile fuer
        # diese Verbindung sichtbar — der Filter-Kwarg ist der einzige
        # Unterschied zwischen Original und Mutant.
        assert (
            DeletionRequest.objects.filter(
                target_type=DeletionRequest.TargetType.CLIENT,
                target_id=merge_source.pk,
                status=DeletionRequest.Status.PENDING,
                merge_into__isnull=False,
            ).count()
            == 1
        )

        assert get_pending_merge_request(merge_source) is None

    def test_ignores_request_with_other_target_type(self, facility, merge_source, merge_target, staff_user):
        """Pin: ``x_get_pending_merge_request__mutmut_11`` (Zeile 133).

        Der Mutant streicht ``target_type=DeletionRequest.TargetType.CLIENT``.
        Der Bestandstest ``test_pending_lookup_ignores_plain_deletion_request``
        deckt nur den Fall ``merge_into IS NULL`` ab — eine Zeile mit fremdem
        ``target_type`` UND gesetztem ``merge_into`` verletzt keine Constraint
        (der partielle Unique laeuft ueber ``target_type``) und rutscht damit
        durch.

        Abweichender Wert: Original liefert ``None``; unter ``mutmut_11``
        liefert die Suche die ``EVENT``-Zeile — ein Loeschantrag fuer ein
        Ereignis wuerde als laufender Personen-Merge gedeutet.
        """
        fremd_typ = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=merge_source.pk,
            reason="Antrag anderen Typs",
            requested_by=staff_user,
            merge_into=merge_target,
        )
        assert fremd_typ.status == DeletionRequest.Status.PENDING
        # Wirkungsnachweis: OHNE die Zieltyp-Klammer bleibt genau diese Zeile
        # im Treffer-Set stehen.
        assert (
            DeletionRequest.objects.filter(
                facility_id=merge_source.facility_id,
                target_id=merge_source.pk,
                status=DeletionRequest.Status.PENDING,
                merge_into__isnull=False,
            ).count()
            == 1
        )

        assert get_pending_merge_request(merge_source) is None

    def test_discriminates_between_two_sources(self, facility, staff_user, merge_source, merge_target):
        """Pin: ``x_get_pending_merge_request__mutmut_12`` (Zeile 134).

        Der Mutant streicht ``target_id=client.pk`` — die Suche liefert dann
        unter ``Meta.ordering = ["-created_at"]`` per ``.first()`` den
        JUENGSTEN PENDING-Merge-Antrag der Einrichtung, unabhaengig von der
        uebergebenen Person. Das speist die Idempotenz-Pruefung in
        ``create_or_get_client_merge_request`` und alle Anzeigeflaechen. Alle
        Bestandstests halten pro Einrichtung hoechstens EINEN PENDING-Antrag.

        ``created_at`` ist ``auto_now_add``; der Test setzt den Zeitstempel des
        ZWEITEN Antrags per ``.update()`` bewusst in die Zukunft, damit die
        Sortierung deterministisch ist (kein Mikrosekunden-Gleichstand).

        Abweichender Wert: Original liefert fuer ``merge_source`` dessen
        eigenen Antrag; unter ``mutmut_12`` liefert es fuer BEIDE Personen den
        juengeren Antrag der zweiten Person.
        """
        zweite_quelle = _identified(facility, staff_user, "Pin-Merge-Quelle-B")
        zweites_ziel = _identified(facility, staff_user, "Pin-Merge-Ziel-B")

        dr_a = request_client_merge(merge_source, merge_target, staff_user, reason="Antrag A")
        dr_b = request_client_merge(zweite_quelle, zweites_ziel, staff_user, reason="Antrag B")
        DeletionRequest.objects.filter(pk=dr_b.pk).update(created_at=timezone.now() + timedelta(minutes=5))

        assert get_pending_merge_request(merge_source) == dr_a
        assert get_pending_merge_request(zweite_quelle) == dr_b
