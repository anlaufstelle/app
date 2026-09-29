"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.client.merge`` (Batch ``merge-b``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@8357c69c (frischer Mutantenbaum), Batch
``merge-b`` (56 Survivor-Mutanten in sechs Funktionen: ``approve_client_merge``
21, ``reject_client_merge`` 16, ``merge_clients`` 10,
``create_or_get_client_merge_request`` 4, ``_execute_merge`` 3,
``_adopt_missing_data`` 2). Batch ``merge-a`` (``unmerge_client`` derselben
Datei) gehoert einem anderen Batch-Agenten und wird hier NICHT angefasst.

Vier wiederkehrende Mutationsachsen in diesem Batch:

* **Vier-Augen-/Zustands-Meldungen auf ``None``** (``ValidationError(None)``
  bzw. ``ValidationError(_(None))`` — beide werten zur Laufzeit identisch
  aus): die jeweilige ``if``-Bedingung bleibt unveraendert (kein Bypass), nur
  der Meldungstext wird woertlich ``"None"`` statt des deutschen Fehlertexts.
  Mitgetoetet werden reine Gross-/Kleinschreibungs- bzw. ``"XX...XX"``-Wrap-
  Varianten DESSELBEN Wortlauts (reine i18n-Textvarianten ohne Hash-/
  Schluessel-/Vergleichsrolle, docs/testing/mutation-testing.md) — der exakte
  ``==``-Vergleich derselben Pin-Testfunktion toetet sie im selben Zug mit
  (im Kill-Nachweis einzeln verifiziert, keine eigenen Pins noetig).
* **``facility`` aus dem TOCTOU-Reload entfernt** (``x_approve_client_merge
  __mutmut_17``, ``x_reject_client_merge__mutmut_17``): ``select_for_update()
  .get(pk=deletion_request.pk, facility=deletion_request.facility)`` ->
  ``get(pk=deletion_request.pk, )``. Der Filter ist SELBSTREFERENZIELL (das
  Attribut kommt vom selben, bereits geladenen Objekt) — er faengt keine
  fremde Zeile, sondern eine STALE In-Memory-Instanz, deren ``facility``
  nach dem Laden manuell veraendert wurde, ohne dass sich die echte Zeile
  aenderte (das Feld ist unveraenderlich, kein Service-Pfad schreibt es um).
  Ohne den Filter laedt ``.get(pk=...)`` trotzdem die ECHTE Zeile und
  ignoriert die veraltete Annahme des Aufrufers — Prio 1, gepinnt ueber eine
  gezielt veraltete ``facility``-Annahme.
* **Der IntegrityError-Backstop in ``create_or_get_client_merge_request``**
  (Muster #2015, ``x_..._mutmut_29/_30/_33/_34``): der Insert unter dem
  partiellen Unique-Index kollidiert bei einem echten Rennen; der Re-Lookup
  im ``except``-Zweig muss GENAU den Gewinner finden und nur bei PASSENDEM
  Ziel idempotent zurueckgeben, bei ABWEICHENDEM Ziel den echten Konflikt
  weiterreichen.

``_execute_merge`` ist in diesem Batch VOLLSTAENDIG ``equivalent`` (alle drei
Survivor: ``_23``, ``_45``, ``_143``), ebenso ``_adopt_missing_data__mutmut_
55/_57`` — Begruendungen mit Invarianten-Beleg aus dem Code selbst (nicht nur
behauptet) in ``TestEquivalentMutantsDocumented`` unten. ``x__execute_merge
__mutmut_23`` (``values_list("pk", flat=True)`` -> ``values_list(flat=True)``)
sah zunaechst wie eine echte Testluecke aus (fehlendes Feldargument) — der
Kill-Nachweis widerlegte das: Djangos ``QuerySet.values_list`` faellt bei
``flat=True`` OHNE Feldangabe auf ``self.model._meta.concrete_fields[0]``
zurueck (``django/db/models/query.py``), und ``Event.id`` (das erste
deklarierte Feld, ``core/models/event.py``) IST der Primary Key — der Mutant
liefert bitidentisch dieselbe Werteliste wie das Original. Der zugehoerige
Test bleibt trotzdem im Batch (Regressions-/Dokumentationswert fuer das
Undo-Inventar), zaehlt aber nicht als Pin fuer ``_23``.

Konventionen wie ``test_mutation_pins_099_deletion_a.py``/``_096_merge.py``:
eine Testklasse je Familie, jeder Test nennt seine Mutanten in der Docstring
samt der Abweichung, Zusicherungen vergleichen EXAKT (``==``/``is``), nie per
Teilstring; ``pytest.mark.django_db`` modulweit + ``_encryption_key``-Fixture
(Lehre 112); Fixtures aus ``src/tests/conftest.py`` bzw. lokale Helfer
(keine Importe aus fremden Pin-Dateien).
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

import core.services.client.merge as merge_module
from core.models import AuditLog, Client, Event
from core.models.workitem import DeletionRequest
from core.services.client.main import request_client_deletion
from core.services.client.merge import (
    approve_client_merge,
    create_or_get_client_merge_request,
    merge_clients,
    reject_client_merge,
    request_client_merge,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _identified(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


def _qualified(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


@pytest.fixture
def source(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeB-Quelle")


@pytest.fixture
def target(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeB-Ziel")


@pytest.fixture
def stranger(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeB-Dritte")


def _blind_first_call(real_lookup):
    """Nur der ERSTE Aufruf sieht nichts — simuliert die Race-Luecke zwischen
    Vorab-Check und echtem Insert (Muster ``test_deletion_request_race_
    idempotency.py``, hier lokal kopiert — bewusst ohne Importe aus fremden
    Testdateien).
    """
    state = {"first": True}

    def _lookup(*args, **kwargs):
        if state["first"]:
            state["first"] = False
            return None
        return real_lookup(*args, **kwargs)

    return _lookup


# ---------------------------------------------------------------------------
# approve_client_merge
# ---------------------------------------------------------------------------


class TestApproveClientMergeGuardMessages:
    """Vier-Augen-/Zustands-Meldungen: die Bedingung bleibt, nur der Text wird
    ``None`` bzw. eine Gross-/Klein-/``XX``-Variante desselben Wortlauts."""

    def test_reviewer_gleich_antragsteller_meldet_den_richtigen_text(self, facility, source, target, staff_user):
        """Pins ``x_approve_client_merge__mutmut_2``/``_3``: ``ValidationError(
        None)`` bzw. ``ValidationError(_(None))`` statt des deutschen
        Vier-Augen-Texts (Zeile 260). Toetet im selben Zug (exakter
        ``==``-Vergleich) ``_4`` (XX-Marker), ``_5``/``_6`` (Klein-/
        Grossschreibung) desselben Wortlauts — mitgetoetet, kein eigener Pin."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        with pytest.raises(ValidationError) as exc:
            approve_client_merge(dr, staff_user)

        assert exc.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_ohne_recht_meldet_den_richtigen_text(self, facility, source, target, staff_user, assistant_user):
        """Pins ``x_approve_client_merge__mutmut_8``/``_9`` (Zeile 262); toetet
        mit ``_10``/``_11``/``_12`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        assert assistant_user.can_confirm_deletion is False

        with pytest.raises(ValidationError) as exc:
            approve_client_merge(dr, assistant_user)

        assert exc.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_kein_zusammenfuehrungs_antrag_meldet_den_richtigen_text(self, facility, source, staff_user, admin_user):
        """Pins ``x_approve_client_merge__mutmut_19``/``_20`` (Zeile 268): ein
        reiner Loeschantrag (``merge_into`` ``None``) ist kein Merge-Antrag;
        toetet mit ``_21``/``_22``/``_23`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_deletion(source, staff_user, "Reiner Löschantrag")
        assert dr.merge_into_id is None

        with pytest.raises(ValidationError) as exc:
            approve_client_merge(dr, admin_user)

        assert exc.value.messages == ["Dies ist kein Zusammenführungs-Antrag."]

    def test_bereits_entschiedener_antrag_meldet_den_richtigen_text(
        self, facility, source, target, staff_user, admin_user
    ):
        """Pins ``x_approve_client_merge__mutmut_25``/``_26`` (Zeile 270): der
        Re-Check unter Lock greift bei einem bereits APPROVED-Antrag; toetet
        mit ``_27``/``_28``/``_29`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        approve_client_merge(dr, admin_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.APPROVED

        with pytest.raises(ValidationError) as exc:
            approve_client_merge(dr, admin_user)

        assert exc.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]


class TestApproveClientMergeReloadTiesToItsOwnFacility:
    """Der TOCTOU-Reload validiert die ``facility`` gegen die ANTRAGS-eigene,
    bereits geladene Instanz — eine gezielt veraltete Annahme, kein fremder
    Datensatz."""

    def test_approve_refuses_a_stale_facility_assumption(self, facility, second_facility, source, target, staff_user):
        """Pin: ``x_approve_client_merge__mutmut_17`` — ``select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility)`` -> ``get(
        pk=deletion_request.pk, )`` (Zeile 264).

        ``deletion_request.facility`` wird NACH dem Laden manuell auf eine
        ANDERE Einrichtung gesetzt (nicht gespeichert — das Feld ist im
        Service unveraenderlich). Original: der Reload filtert erneut nach
        dieser (jetzt falschen) Annahme und findet die Zeile nicht
        (``DoesNotExist``). Mutant: der Reload ignoriert die Annahme
        komplett und laedt die echte Zeile trotzdem — die Genehmigung liefe
        durch, obwohl der Aufrufer von einer anderen Einrichtung ausging.

        Die pruefende Person gehoert zur ANGENOMMENEN Einrichtung: die
        Einrichtungsgrenze vor der Antragssperre vergleicht mit der Annahme und
        laesst den Aufruf bis zum gesperrten Reload durch, der die Annahme
        verwirft (Mutant: Reload gelingt, die Grenze unter der Sperre meldet
        die fremde Einrichtung statt ``DoesNotExist``).
        """
        from core.models import User

        reviewer = User.objects.create_user(
            username="pin099_merge_leitung_angenommen",
            role=User.Role.FACILITY_ADMIN,
            facility=second_facility,
            can_confirm_deletion=True,
        )
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        dr.facility = second_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_client_merge(dr, reviewer)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        source.refresh_from_db()
        assert source.merged_into_id is None
        assert source.is_deleted is False


# ---------------------------------------------------------------------------
# reject_client_merge
# ---------------------------------------------------------------------------


class TestRejectClientMergeGuardMessages:
    def test_reviewer_gleich_antragsteller_meldet_den_richtigen_text(self, facility, source, target, staff_user):
        """Pins ``x_reject_client_merge__mutmut_2``/``_3`` (Zeile 295); toetet
        mit ``_4``/``_5``/``_6`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        with pytest.raises(ValidationError) as exc:
            reject_client_merge(dr, staff_user)

        assert exc.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_ohne_recht_meldet_den_richtigen_text(self, facility, source, target, staff_user, assistant_user):
        """Pins ``x_reject_client_merge__mutmut_8``/``_9`` und tötet
        ``_10``/``_11``/``_12``: Verlust und Verfälschungen der Meldung des
        Bestätigungsrecht-Guards."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

        with pytest.raises(ValidationError) as exc:
            reject_client_merge(dr, assistant_user)

        assert exc.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_kein_zusammenfuehrungs_antrag_meldet_den_richtigen_text(self, facility, source, staff_user, admin_user):
        """Pins ``x_reject_client_merge__mutmut_19``/``_20``; toetet mit
        ``_21``/``_22``/``_23`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_deletion(source, staff_user, "Reiner Löschantrag")
        assert dr.merge_into_id is None

        with pytest.raises(ValidationError) as exc:
            reject_client_merge(dr, admin_user)

        assert exc.value.messages == ["Dies ist kein Zusammenführungs-Antrag."]

    def test_bereits_entschiedener_antrag_meldet_den_richtigen_text(
        self, facility, source, target, staff_user, admin_user
    ):
        """Pins ``x_reject_client_merge__mutmut_25``/``_26``; toetet mit
        ``_27``/``_28``/``_29`` (XX/Klein/Gross) — mitgetoetet."""
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        reject_client_merge(dr, admin_user)
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.REJECTED

        with pytest.raises(ValidationError) as exc:
            reject_client_merge(dr, admin_user)

        assert exc.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]


class TestRejectClientMergeReloadTiesToItsOwnFacility:
    def test_reject_refuses_a_stale_facility_assumption(self, facility, second_facility, source, target, staff_user):
        """Pin: ``x_reject_client_merge__mutmut_17`` — ``select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility)`` -> ``get(
        pk=deletion_request.pk, )``. Begruendung siehe
        ``TestApproveClientMergeReloadTiesToItsOwnFacility`` (spiegelbildlich,
        auch die pruefende Person aus der angenommenen Einrichtung)."""
        from core.models import User

        reviewer = User.objects.create_user(
            username="pin099_merge_ablehnung_angenommen",
            role=User.Role.FACILITY_ADMIN,
            facility=second_facility,
            can_confirm_deletion=True,
        )
        dr = request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        dr.facility = second_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            reject_client_merge(dr, reviewer)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


# ---------------------------------------------------------------------------
# merge_clients
# ---------------------------------------------------------------------------


class TestMergeClientsGuardMessages:
    def test_ohne_leitungsrecht_meldet_den_richtigen_text(self, facility, source, target, staff_user):
        """Pins ``x_merge_clients__mutmut_2``/``_3`` (Zeile 330); toetet mit
        ``_4``/``_5``/``_6`` (XX/Klein/Gross) — mitgetoetet."""
        assert staff_user.is_lead_or_admin is False

        with pytest.raises(ValidationError) as exc:
            merge_clients(source, target, staff_user)

        assert exc.value.messages == ["Nur die Leitung darf Personen direkt zusammenführen."]

    def test_vier_augen_pflicht_meldet_den_richtigen_text(self, facility, staff_user, lead_user):
        """Pins ``x_merge_clients__mutmut_16``/``_17`` (Zeile 334): eine der
        beiden Personen ist ``QUALIFIED`` -> Direkt-Merge gesperrt; toetet mit
        ``_18``/``_19``/``_20`` (XX/Klein/Gross) — mitgetoetet."""
        qualified_source = _qualified(facility, staff_user, "Pin099-MergeB-Qualifiziert")
        identified_target = _identified(facility, staff_user, "Pin099-MergeB-VierAugenZiel")

        with pytest.raises(ValidationError) as exc:
            merge_clients(qualified_source, identified_target, lead_user)

        assert exc.value.messages == [
            "Für Personen mit Zusatzangaben ist ein Zusammenführungs-Antrag mit Bestätigung nötig."
        ]


# ---------------------------------------------------------------------------
# create_or_get_client_merge_request — IntegrityError-Backstop (Muster #2015)
# ---------------------------------------------------------------------------


class TestCreateOrGetClientMergeRequestRaceBackstop:
    """Der Re-Lookup im ``except IntegrityError``-Zweig muss GENAU den echten
    Gewinner finden und nur bei passendem Ziel idempotent zurueckgeben.

    Simuliert wird die Race deterministisch: der FRUEHE Vorab-Check
    (``get_pending_merge_request`` direkt nach ``_locked_clients``) wird fuer
    genau EINEN Aufruf geblendet (Muster ``test_deletion_request_race_
    idempotency.py``), waehrend der Gewinner-Antrag bereits ECHT in der DB
    liegt — der ``.create()``-Insert kollidiert dadurch mit dem echten
    partiellen Unique-Index, kein injizierter Fehler. ``_validate_merge``
    wird durch ein statisches ``get_pending_deletion_request``-Patch
    durchgelassen (sonst bricht schon die Vorbedingung, bevor der Backstop
    ueberhaupt erreicht wird) — dessen eigene Mutanten gehoeren nicht zu
    diesem Batch.
    """

    def test_race_backstop_gibt_den_passenden_gewinner_idempotent_zurueck(self, facility, source, target, staff_user):
        """Pins ``x_create_or_get_client_merge_request__mutmut_29`` (``existing
        = None`` statt des Re-Lookups, Zeile 225), ``_30`` (``get_pending_
        merge_request(None)`` statt ``(source)``, Zeile 225), ``_33`` (``!=``
        -> ``==``, Zeile 226) und ``_34`` (``return existing, False`` ->
        ``True``, Zeile 228).

        Original: der bereits existierende, ZUM SELBEN Ziel zeigende Antrag
        wird gefunden und idempotent zurueckgegeben (``created is False``,
        weiterhin genau EIN PENDING-Antrag). Mutant 29/30: der Backstop
        findet den Gewinner nicht mehr (``None`` fest bzw. ``AttributeError``
        durch ``None.pk``) und reicht den echten ``IntegrityError`` unbehandelt
        weiter — aus einer erwarteten Idempotenz-Antwort wird ein 500er.
        Mutant 33: die invertierte Bedingung wertet den TREFFER als Konflikt
        und wirft ebenfalls. Mutant 34: der ``created``-Flag luegt (``True``
        obwohl der Antrag bereits vorher existierte).
        """
        winner = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=source.pk,
            reason="Gewinner des Rennens",
            requested_by=staff_user,
            merge_into=target,
        )

        with (
            mock.patch(
                "core.services.client.merge.get_pending_merge_request",
                side_effect=_blind_first_call(merge_module.get_pending_merge_request),
            ),
            mock.patch("core.services.client.merge.get_pending_deletion_request", return_value=None),
        ):
            dr, created = create_or_get_client_merge_request(source, target, staff_user, "Zweiter, paralleler Antrag")

        assert dr.pk == winner.pk
        assert created is False
        assert DeletionRequest.objects.filter(target_id=source.pk, status=DeletionRequest.Status.PENDING).count() == 1

    def test_race_backstop_reicht_einen_echten_zielkonflikt_weiter(
        self, facility, source, target, stranger, staff_user
    ):
        """Zusaetzlicher Beleg fuer ``_33`` (spiegelbildlicher Fall): zeigt der
        bereits existierende Antrag auf ein ANDERES Ziel, ist es ein echter
        Konflikt — Original wirft den ``IntegrityError`` weiter statt ihn als
        Erfolg zu tarnen. Mutant 33 wuerde ihn schlucken und den FALSCHEN
        (fremden) Antrag als vermeintlichen Erfolg zurueckgeben."""
        winner = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=source.pk,
            reason="Gewinner mit anderem Ziel",
            requested_by=staff_user,
            merge_into=stranger,
        )

        with (
            mock.patch(
                "core.services.client.merge.get_pending_merge_request",
                side_effect=_blind_first_call(merge_module.get_pending_merge_request),
            ),
            mock.patch("core.services.client.merge.get_pending_deletion_request", return_value=None),
        ):
            with pytest.raises(IntegrityError):
                create_or_get_client_merge_request(source, target, staff_user, "Antrag mit anderem Ziel")

        assert DeletionRequest.objects.filter(target_id=source.pk).count() == 1
        winner.refresh_from_db()
        assert winner.merge_into_id == stranger.pk


# ---------------------------------------------------------------------------
# _execute_merge — Undo-Inventar
# ---------------------------------------------------------------------------


class TestExecuteMergeMovedEventInventory:
    def test_moved_event_ids_landen_unverfaelscht_im_audit_inventar(
        self, facility, source, target, lead_user, doc_type_contact
    ):
        """Regressionstest zum Undo-Inventar (KEIN Pin — ``x__execute_merge
        __mutmut_23`` ist ``equivalent``, siehe ``TestEquivalentMutantsDocumented``):
        ``Event.objects.filter(client=source).values_list("pk", flat=True)``
        -> ``values_list(flat=True)`` (Zeile 558, Feldangabe faellt weg) sieht
        wie eine Testluecke aus, ist aber bitidentisch — Djangos ``flat=True``
        OHNE Feldangabe faellt auf ``concrete_fields[0]`` zurueck, und das ist
        bei ``Event`` bereits die ``id``/PK-Spalte. Der Test bleibt als
        Dokumentation/Regression fuer das ``moved_events``-Inventar erhalten,
        das ``unmerge_client`` spaeter liest.
        """
        event = Event.objects.create(
            facility=facility,
            client=source,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=lead_user,
        )

        merge_clients(source, target, lead_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(source.pk),
        )
        assert row.detail.get("moved_events") == [str(event.pk)]
        event.refresh_from_db()
        assert event.client_id == target.pk


# ---------------------------------------------------------------------------
# Equivalente Mutanten (kein Pin — Begruendung mit Invarianten-Beleg)
# ---------------------------------------------------------------------------


class TestEquivalentMutantsDocumented:
    """Reine Dokumentationsklasse ohne Testcode — die Begruendungen stehen im
    Modul-Docstring UND im Triage-Bericht; hier nur die Nachweisstellen fuer
    einen schnellen Codepfad-Rueckgriff.

    * ``x__execute_merge__mutmut_23`` (``Event.objects.filter(client=source).
      values_list("pk", flat=True)`` -> ``values_list(flat=True)``, Zeile
      558): Djangos ``QuerySet.values_list(*fields, flat=True)`` faellt OHNE
      Feldangabe auf ``fields = [self.model._meta.concrete_fields[0].attname]``
      zurueck (``django/db/models/query.py``, Methode ``values_list``).
      ``Event.id`` ist das ERSTE deklarierte Feld der Klasse
      (``core/models/event.py``) UND ihr Primary Key — ``concrete_fields[0]``
      ist also exakt ``id``/``pk``. Der Mutant liefert bitidentisch dieselbe
      ``moved_event_ids``-Liste wie das Original (Kill-Nachweis: unter der
      Mutante bleibt die GESAMTE Pin-Datei gruen).
    * ``x__execute_merge__mutmut_45`` (``_assert_event_case_consistency(
      source, target)`` -> ``(target)``, Zeile 563): ZWEI Zeilen zuvor laeuft
      ``Case.objects.filter(client=source).update(client=target)`` UNBEDINGT
      und OHNE Zwischenschritt, der neue ``source``-Faelle anlegen koennte
      (die naechste Zeile ist nur der Event-Move). Danach ist ``Case.objects.
      filter(client=source)`` innerhalb DERSELBEN Transaktion beweisbar leer
      (read-your-own-writes) — und damit auch ``Event.objects.filter(
      case__client=source)``. Die Pruefung auf ``source`` ist an dieser
      Stelle nachweisbar tote Kontrolle; JEDE Inkonsistenz, die sie faende,
      faende die verbleibende ``target``-Pruefung ebenso (jeder betroffene
      Fall traegt nach dem Move ``client=target``).
    * ``x__execute_merge__mutmut_143`` (``getattr(user, "pk", None)`` ->
      ``getattr(user, "pk", )``, Zeile 619, reines Logging): beide
      Aufrufstellen von ``_execute_merge`` (``merge_clients``, ``approve_
      client_merge``) validieren ``user``/``reviewer`` VOR dem Aufruf
      bereits ueber echte Attribute (``is_lead_or_admin``,
      ``can_confirm_deletion``) — ``user`` ist an dieser Stelle immer ein
      echtes ``User``-Objekt mit ``pk``. Der Default greift nie.
    * ``x__adopt_missing_data__mutmut_55``/``_57`` (``marker.get("entries",
      [])`` -> ``marker.get("entries", None)`` bzw. ganz ohne Default, Zeile
      525): ``marker`` ist ``cleaned[slug]`` aus ``validate_data_json``
      (``core/services/dynamic_data.py``), das einen FILE-Slug NUR dann in
      ``cleaned`` uebernimmt, wenn ``is_file_marker(v)`` wahr ist — und
      ``is_file_marker`` verlangt ``isinstance(value.get("entries"), list)``
      (dynamic_data.py:61/77). Jeder ``marker`` mit ``__files__ is True``, der
      diese Zeile erreicht, traegt also GARANTIERT eine ``entries``-Liste;
      der Default wird nie gebraucht.
    """
