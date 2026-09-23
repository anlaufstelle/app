"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — Batch ``client-2``
(``core.services.client.restriction``, ``.dsgvo_package``, ``.export``, ``.targets``).

Quelle: Vollmutation #2174, Batch ``client-2``
(``welle-l-0.99/reports/m-scratch/batch-client-2.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-client-2/triage-099-client-2.json``). Die
Batch hat 70 Survivor-Mutanten ueber vier Module; 53 sind Testlücken (``test``), 17 sind
``equivalent`` (Begründung je Mutante in der Triage-JSON; keine ``skip``-Mutanten — jede
Wortlaut-/Case-Variante wird von einem der Exakt-Wortlaut-Pins unten mitgetötet). Die 53
``test``-Mutanten sind hier mit 17 toetenden Tests gepinnt; zwei weitere Tests dienen
AUSSCHLIESSLICH als empirischer Aequivalenz-Nachweis (bleiben unter den betroffenen
``equivalent``-Mutanten GRUEN, siehe Killmap) — 19 Tests insgesamt:

**``restriction.py`` (32 Mutanten, sechs Tests):**

* **Rollen-Guard mit fehlendem Attribut (``_require_staff``, zwei Mutanten).**
  ``getattr(user, "is_staff_or_above", False)`` verliert entweder seinen Default
  (``x__require_staff__mutmut_7``, wird zum 2-Arg-``getattr`` — bei fehlendem Attribut
  ``AttributeError`` statt des dokumentierten Gates) oder bekommt den Default ``True``
  (``x__require_staff__mutmut_10`` — das Gate wird bei fehlendem Attribut fail-open statt
  fail-closed). Der Moduldocstring nennt ausdrücklich Nicht-Formular-Aufrufer
  (Offline-Replay, CSV-Import) als Zielgruppe dieses SERVICE-Guards — ``user: Any`` ist
  bewusst weit typisiert. Ein Aufrufer-Objekt ohne ``is_staff_or_above`` deckt beide aus.
  Der Default-Wechsel ``False`` → ``None`` (``mutmut_4``) ist dagegen ``equivalent``: beide
  sind falsy, und das Ergebnis wird nur über ``not getattr(...)`` konsumiert.

* **Meldungs-VERLUST an sechs Guard-Stellen (30 Mutanten, vier Tests unten in
  ``TestRestrictProcessingMeldungen``/``TestLiftProcessingRestrictionMeldungen``).**
  ``raise ValidationError(_(text))`` wird zu ``ValidationError(None)``/``ValidationError(_(None))``
  bzw. der Wortlaut traegt den mutmut-``XX``-Marker oder eine Gross-/Kleinschreibungs-Variante.
  Vier Tests rufen die betroffene Guard-Stelle auf und vergleichen ``exc_info.value.messages``
  exakt gegen den Original-Wortlaut — die Wortlaut-Varianten sterben dabei MIT (kein separater
  ``skip``). ``x__require_staff__mutmut_11`` (Meldung an der gemeinsamen Guard-Funktion selbst
  verloren) wird vom ersten dieser Tests mitgetötet, da jeder Aufrufer über dieselbe Funktion geht.

**``dsgvo_package.py`` (27 Mutanten, neun Tests):**

* ``_settings_hash``: der ``facility_full_name``-Fallback ``"XXXX"`` statt ``""``
  (``mutmut_17``) veraendert den Hash-Payload beobachtbar — ``equivalent`` sind der
  ``None``-Default (``mutmut_10``, von ``or ""`` maskiert) und die Codec-Grossschreibung
  ``"UTF-8"`` (``mutmut_29``, byteidentisch).
* ``_document_type_retention_overrides``: sowohl der Verlust des DB-Filters
  ``retention_days__isnull=False`` (``mutmut_5``) als auch ``continue`` → ``break``
  (``mutmut_9``) sind ``equivalent`` — der DB-Filter bleibt in ``mutmut_9`` UNVERAENDERT
  bestehen, sodass die Schleife nie eine Zeile mit ``retention_days is None`` sieht (die
  ``continue``/``break``-Verzweigung ist toter Code); in ``mutmut_5`` filtert die
  Python-Schleife selbst identisch nach.
* ``_format_document_type_overrides``: der Trenner ``"; "`` → ``"XX; XX"`` (``mutmut_5``)
  veraendert den DSFA-Tabelleninhalt direkt.
* ``_build_footer``: drei ``getattr(django_settings, "SOURCE_CODE_VERSION", ...)``-Default-
  Mutanten (``mutmut_5``/``8``/``11``) sind ``equivalent``, weil ``SOURCE_CODE_VERSION`` in
  ``settings/base.py`` IMMER gesetzt ist (der Default nie greift); der ``"n/a"``-Fallback-Text
  (``mutmut_12``/``13``), der Footer-Trenner (``mutmut_19``) und die Commit-Kürzung auf neun
  statt acht Zeichen (``mutmut_23``) sind dagegen ``test``.
* ``render_document``: drei Encoding-/``settings_obj``-Typ-Mutanten (``mutmut_9``/``11``/``13``)
  sind ``equivalent`` (Container-Locale ``C.UTF-8`` deckt sich mit ``"utf-8"``, Codec-Namen sind
  case-insensitiv, ``settings_obj`` wird nur über ``getattr``-Ketten gelesen); das Datumsformat
  (``mutmut_26``) sowie sechs Platzhalter-Leck-/``None``-Mutanten an den beiden
  Historien-Monatsfeldern (``mutmut_59``/``60``/``61``/``64``/``65``/``66``) sind ``test``.
* ``get_document_list``: vier Dict-Schluessel-Mutanten (``mutmut_3``/``4``/``7``/``8``) sind
  ``test`` (abweichender Schluessel eines zurueckgegebenen Mappings).

**``export.py`` (6 Mutanten, ein Test) und ``targets.py`` (5 Mutanten, ein Test):**

* ``_gather_events`` verliert ``"deleted_by"`` aus ``select_related`` (``mutmut_16``) — ein
  echter N+1-Query-Regression (empirisch per SQL-Vergleich verifiziert, ``_serialize_event``
  liest ``event.deleted_by``), gepinnt per Query-Zaehlung. Die uebrigen fuenf ``export.py``-
  Mutanten sind ``equivalent`` (redundanter Scope-Filter maskiert durch Dict-Lookup,
  identisches select_related-SQL, ``values_list(flat=True)`` faellt bei allen drei betroffenen
  Modellen auf das erste konkrete Feld ``id`` == ``pk`` zurueck — je empirisch verifiziert).
  ``TestGatherAuditTrailValuesListEquivalence`` ruft ``_gather_audit_trail`` ueber
  ``export_client_data`` mit je einer echten RectificationRequest-/DeletionRequest-/
  OutcomeGoal-Auditzeile auf und bleibt unter allen drei betroffenen Mutanten GRUEN.
* ``client_activity_targets_q`` verliert den ContentType-Scope in drei Zweigen
  (``mutmut_11``/``27``/``43``: Event/WorkItem/Case) — eine Activity mit derselben PK wie ein
  echtes Ziel, aber falschem ``target_type``, würde nach der Mutante faelschlich mitgezaehlt.
  ``client_legal_hold_targets_q`` verliert denselben Scope fuer den Activity-Zweig
  (``mutmut_25``); der ``values_list(flat=True)``-Mutant (``mutmut_10``) ist wie in ``export.py``
  ``equivalent`` (``Activity``s erstes konkretes Feld ist ebenfalls ``id``).

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``/work/anlaufstelle/mutants``) hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture wären die Pins dort aus dem
FALSCHEN Grund rot.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DocumentType, LegalHold, OutcomeGoal, Settings
from core.models import Case as CaseModel
from core.services.client.dsgvo_package import (
    DOCUMENTS,
    _backup_retention_days,
    _build_footer,
    _document_type_retention_overrides,
    _format_document_type_overrides,
    _retention_values,
    _settings_hash,
    get_document_list,
    render_document,
)
from core.services.client.export import _gather_events, export_client_data
from core.services.client.main import request_client_deletion
from core.services.client.rectification import request_rectification
from core.services.client.restriction import (
    _require_staff,
    lift_processing_restriction,
    restrict_processing,
)
from core.services.client.targets import client_activity_targets_q, client_legal_hold_targets_q
from tests._factories import make_event

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# restriction.py
# ---------------------------------------------------------------------------


class TestRequireStaffOhneRollenattribut:
    class _AufruferOhneRollenattribut:
        """Steht fuer einen Any-typisierten Aufrufer ausserhalb des User-Modells
        (Offline-Replay/CSV-Import, siehe Moduldocstring von ``restriction.py``) —
        traegt bewusst KEIN ``is_staff_or_above``-Attribut."""

    def test_require_staff_ohne_rollenattribut_faellt_fail_closed_auf_kein_zugriff(self):
        """Pins ``x__require_staff__mutmut_7`` (Default entfernt -> 2-Arg-``getattr``,
        wirft bei fehlendem Attribut ein unkontrolliertes ``AttributeError`` statt des
        Gates) und ``x__require_staff__mutmut_10`` (Default ``False`` -> ``True``, das
        Gate wird fail-open statt fail-closed). ``x__require_staff__mutmut_4`` (Default
        ``False`` -> ``None``) bleibt bewusst ungepinnt: beide Werte sind falsy und
        werden nur ueber ``not getattr(...)`` konsumiert, das Ergebnis ist identisch —
        dieser Test bleibt unter dieser Mutante GRUEN (Aequivalenz-Nachweis)."""
        with pytest.raises(ValidationError) as exc_info:
            _require_staff(self._AufruferOhneRollenattribut(), "Testmeldung Pin099-Client2")

        assert exc_info.value.messages == ["Testmeldung Pin099-Client2"]


class TestRestrictProcessingMeldungen:
    def test_restrict_processing_ohne_fachkraft_meldet_exakten_wortlaut(self, client_identified, assistant_user):
        """Pins ``x_restrict_processing__mutmut_2`` (``_require_staff(user, None)``),
        ``…__mutmut_5`` (``_require_staff(user, _(None))``) sowie die mutmut-Wortlaut-
        Varianten ``…__mutmut_6`` (``XX``-Marker), ``…__mutmut_7`` (Kleinschreibung) und
        ``…__mutmut_8`` (Grossschreibung) — alle fuenf veraendern den an ``_require_staff``
        uebergebenen Meldungstext. Toetet zugleich ``x__require_staff__mutmut_11``
        (Meldung an der gemeinsamen Guard-Funktion selbst verloren)."""
        with pytest.raises(ValidationError) as exc_info:
            restrict_processing(client_identified, assistant_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

        assert exc_info.value.messages == ["Nur Fachkräfte dürfen die Verarbeitung einschränken."]

    def test_restrict_processing_mit_ungueltigem_grund_meldet_exakten_wortlaut(self, client_identified, staff_user):
        """Pins ``x_restrict_processing__mutmut_10`` (``ValidationError(None)``) sowie die
        Wortlaut-Varianten ``…__mutmut_12`` (``XX``-Marker), ``…__mutmut_13``
        (Kleinschreibung) und ``…__mutmut_14`` (Grossschreibung) am Grund-Validierungsguard."""
        with pytest.raises(ValidationError) as exc_info:
            restrict_processing(client_identified, staff_user, reason="pin099-client2-kein-gueltiger-grund")

        assert exc_info.value.messages == ["Ungültiger Grund für die Einschränkung der Verarbeitung."]


class TestLiftProcessingRestrictionMeldungen:
    def test_lift_processing_restriction_ohne_fachkraft_meldet_exakten_wortlaut(
        self, client_identified, assistant_user
    ):
        """Pins ``x_lift_processing_restriction__mutmut_2``/``…_5`` (Meldungs-Verlust) sowie
        die Wortlaut-Varianten ``…_6``/``…_7``/``…_8``."""
        with pytest.raises(ValidationError) as exc_info:
            lift_processing_restriction(client_identified, assistant_user)

        assert exc_info.value.messages == ["Nur Fachkräfte dürfen die Einschränkung aufheben."]

    def test_lift_processing_restriction_ohne_gesetzten_vermerk_meldet_exakten_wortlaut(
        self, client_identified, staff_user
    ):
        """Pins ``x_lift_processing_restriction__mutmut_12`` (Meldungs-Verlust) sowie die
        Wortlaut-Varianten ``…_14``/``…_15``/``…_16`` am Zustandsguard (kein gesetzter
        Vermerk)."""
        with pytest.raises(ValidationError) as exc_info:
            lift_processing_restriction(client_identified, staff_user)

        assert exc_info.value.messages == ["Für diese Person ist die Verarbeitung nicht eingeschränkt."]

    def test_lift_processing_restriction_vier_augen_meldet_exakten_wortlaut(
        self, client_identified, staff_user, lead_user
    ):
        """Pins ``x_lift_processing_restriction__mutmut_20`` (Meldungs-Verlust der
        drei-zeiligen Vier-Augen-Meldung) sowie die neun Wortlaut-Varianten der drei
        Teilzeilen (``…_22``..``…_30``, je ``XX``-Marker/Klein-/Grossschreibung)."""
        restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

        with pytest.raises(ValidationError) as exc_info:
            lift_processing_restriction(client_identified, staff_user)

        assert exc_info.value.messages == [
            "Die Einschränkung kann nicht von derselben Person aufgehoben werden, "
            "die sie gesetzt hat (Vier-Augen-Prinzip) — bitte die Leitung oder eine "
            "zweite berechtigte Person hinzuziehen."
        ]


# ---------------------------------------------------------------------------
# dsgvo_package.py
# ---------------------------------------------------------------------------


class TestSettingsHashUndFooterInhalt:
    def test_settings_hash_unterscheidet_fehlenden_von_erfundenem_einrichtungsnamen(self):
        """Pins ``x__settings_hash__mutmut_17``: der ``or``-Fallback fuer
        ``facility_full_name`` wird von ``""`` auf das Literal ``"XXXX"`` verfaelscht —
        bei fehlendem Einrichtungsnamen enthielte der Hash-Payload einen erfundenen
        Platzhaltertext statt eines leeren Werts. Rechnet den erwarteten Hash aus denselben
        Bausteinen wie das Original nach, statt Implementierungsdetails zu duplizieren."""
        baseline = _settings_hash(None)

        expected_payload = {
            **_retention_values(None),
            "backup_retention_days": _backup_retention_days(),
            "facility_full_name": "",
            "document_type_overrides": [],
        }
        expected_hash = hashlib.sha256(json.dumps(expected_payload, sort_keys=True).encode("utf-8")).hexdigest()[:8]

        assert baseline == expected_hash

    def test_build_footer_fallback_ohne_source_code_version_zeigt_exakten_wortlaut(self, settings):
        """Pins ``x__build_footer__mutmut_12`` (Fallback-Text ``"n/a"`` -> ``"XXn/aXX"``)
        und ``…__mutmut_13`` (Grossschreibung ``"N/A"``, case-sensitiv abgedeckt vom
        exakten Vergleich unten). ``SOURCE_CODE_VERSION`` ist per Default in
        ``settings/base.py`` bereits ``""`` — der Fallback greift also im Standardfall,
        wird hier zur Klarheit trotzdem explizit gesetzt."""
        settings.SOURCE_CODE_VERSION = ""

        footer = _build_footer(None)

        assert "Software-Version: n/a" in footer

    def test_build_footer_hat_exakten_trenner_und_stempelformat(self):
        """Pins ``x__build_footer__mutmut_19``: der feste Footer-Trenner
        ``"\\n\\n---\\n\\n"`` (Bestandteil JEDES gerenderten DSGVO-Dokuments) traegt in
        der Mutante den mutmut-Marker ``XX`` am Anfang und Ende."""
        footer = _build_footer(None)

        assert footer.startswith("\n\n---\n\n")

    def test_build_footer_kuerzt_commit_auf_acht_zeichen(self, settings):
        """Pins ``x__build_footer__mutmut_23``: ``commit[:8]`` wird zu ``commit[:9]`` —
        bei gesetzter ``SOURCE_CODE_VERSION`` zeigte der Stempel ein Zeichen zu viel."""
        settings.SOURCE_CODE_VERSION = "abcdefghijklmnop"

        footer = _build_footer(None)

        assert "Software-Version: abcdefgh ·" in footer
        assert "abcdefghi" not in footer


class TestDocumentTypeRetentionOverridesReihenfolge:
    def test_document_type_retention_overrides_liefert_nur_typen_mit_eigener_frist(self, facility):
        """Aequivalenz-Nachweis fuer ZWEI Mutanten, die beide die
        ``if dt.retention_days is None:``-Verzweigung betreffen: der DB-Filter
        ``retention_days__isnull=False`` (unveraendert in BEIDEN Mutanten) sorgt bereits
        dafuer, dass in der Schleife NIE ein ``None``-Wert ankommt — die Verzweigung ist
        toter Code. ``x__document_type_retention_overrides__mutmut_5`` entfernt den
        redundanten DB-Filter (die Python-Schleife filtert danach selbst per ``continue``
        identisch); ``x__document_type_retention_overrides__mutmut_9`` ersetzt
        ``continue`` durch ``break`` (bleibt unbeobachtbar, weil dieser Zweig ohnehin nie
        erreicht wird — der DB-Filter garantiert bereits ``retention_days IS NOT NULL``
        fuer jede Zeile, die die Schleife sieht; empirisch bestaetigt: dieser Test bleibt
        unter BEIDEN Mutanten GRUEN, siehe Killmap)."""
        DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="Pin099-Client2-OhneFrist",
            sort_order=1,
        )
        mit_frist = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="Pin099-Client2-MitFrist",
            sort_order=2,
            retention_days=42,
        )

        result = _document_type_retention_overrides(facility)

        assert result == [(mit_frist.name, 42)]

    def test_format_document_type_overrides_trennt_mit_exaktem_separator(self):
        """Pins ``x__format_document_type_overrides__mutmut_5``: der Trenner ``"; "``
        wird zum mutmut-Marker ``"XX; XX"`` verfaelscht — veraendert direkt den
        DSFA-Tabelleninhalt bei mehreren Dokumentationstyp-Ausnahmen."""
        result = _format_document_type_overrides([("Pin099-A", 10), ("Pin099-B", 20)])

        assert result == "Pin099-A: 10 Tage; Pin099-B: 20 Tage"


class TestRenderDocumentPlatzhalter:
    def test_render_document_datum_hat_exaktes_format(self, facility):
        """Pins ``x_render_document__mutmut_26``: das Strftime-Format ``"%d.%m.%Y"`` wird
        zu ``"XX%d.%m.%YXX"`` verfaelscht — das gerenderte Datum traegt dann den
        mutmut-Marker um den eigentlichen Wert herum."""
        content, _ = render_document("toms", facility)
        today = timezone.localdate().strftime("%d.%m.%Y")

        assert today in content
        assert f"XX{today}XX" not in content

    def test_render_document_ersetzt_eventhistory_monate_platzhalter_korrekt(self, facility):
        """Pins sechs Mutanten an den beiden Historien-Monatsfeldern (``loeschkonzept.md``
        § Historien-Inhalte/-Chronologie): ``x_render_document__mutmut_59``/``…_64``
        (Ersetzungsschluessel traegt den ``XX``-Marker, der Platzhalter bleibt im
        Dokument UNERSETZT stehen), ``…_60``/``…_65`` (Schluessel grossgeschrieben,
        gleicher Leck-Defekt) und ``…_61``/``…_66`` (Wert wird IMMER durch das Literal
        ``"None"`` ersetzt, unabhaengig vom Settings-Wert). Zwei unterschiedliche Werte
        fuer die beiden Felder schliessen aus, dass ein vertauschter Platzhalter
        unbemerkt bleibt."""
        Settings.objects.create(facility=facility, eventhistory_detail_months=7, eventhistory_chronicle_months=11)

        content, _ = render_document("loeschkonzept", facility)

        assert "{{ eventhistory_detail_months }}" not in content
        assert "{{ eventhistory_chronicle_months }}" not in content
        assert "{{ EVENTHISTORY_DETAIL_MONTHS }}" not in content
        assert "{{ EVENTHISTORY_CHRONICLE_MONTHS }}" not in content
        assert "7 Monate" in content
        assert "11 Monate" in content
        assert "None Monate" not in content


class TestGetDocumentListSchluessel:
    def test_get_document_list_hat_stabile_schluesselmenge(self):
        """Pins vier Dict-Schluessel-Mutanten (``x_get_document_list__mutmut_3``/``…_7``:
        ``XX``-Marker auf ``"name"``/``"article"``; ``…_4``/``…_8``: Grossschreibung
        ``"NAME"``/``"ARTICLE"``) — ein abweichender Schluessel im zurueckgegebenen
        Mapping ist unabhaengig davon zu pinnen, was
        heutige Aufrufer lesen."""
        result = get_document_list()

        assert len(result) == len(DOCUMENTS)
        for item in result:
            assert set(item) == {"slug", "name", "article"}
        assert {item["slug"] for item in result} == set(DOCUMENTS)


# ---------------------------------------------------------------------------
# export.py
# ---------------------------------------------------------------------------


class TestGatherEventsQueryCount:
    def test_gather_events_liest_deleted_by_ohne_zusatzquery(
        self, facility, doc_type_contact, client_identified, staff_user
    ):
        """Pins ``x__gather_events__mutmut_16``: ``select_related(...)`` verliert
        ``"deleted_by"``. ``_serialize_event`` liest ``event.deleted_by`` — ohne den
        select_related-Eintrag loest das bei einem soft-deleteten Ereignis mit gesetztem
        ``deleted_by`` EINE zusaetzliche Query aus (empirisch per SQL-Vergleich der
        beiden select_related-Varianten bestaetigt, siehe Triage)."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from core.models import Event

        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        Event.objects.filter(pk=event.pk).update(is_deleted=True, deleted_at=timezone.now(), deleted_by=staff_user)

        with CaptureQueriesContext(connection) as ctx:
            events_data, event_ids = _gather_events(client_identified, staff_user, is_deleted=True)

        assert len(events_data) == 1
        # 2 Queries im Original: das Event (inkl. deleted_by ueber select_related
        # bereits gejoint) + der Anhang-Abruf in _serialize_event. Ohne den
        # select_related-Eintrag kommt eine dritte Query fuer deleted_by dazu.
        assert len(ctx.captured_queries) == 2


class TestGatherAuditTrailValuesListEquivalence:
    def test_gather_audit_trail_erfasst_rectification_deletion_und_goal_auditzeilen(
        self, facility, client_identified, staff_user
    ):
        """Aequivalenz-Nachweis fuer ``x__gather_audit_trail__mutmut_21``
        (RectificationRequest), ``…__mutmut_35`` (DeletionRequest) und ``…__mutmut_64``
        (OutcomeGoal): Django faellt bei ``values_list(flat=True)`` OHNE Feldnamen auf
        ``model._meta.concrete_fields[0].attname`` zurueck; fuer alle drei Modelle ist
        das erste konkrete Feld ``"id"`` == der Primaerschluessel (empirisch verifiziert),
        identisch zu ``values_list("pk", flat=True)``. Dieser Test ruft
        ``_gather_audit_trail`` ueber ``export_client_data`` mit je einer echten Zeile pro
        Modell auf und bleibt unter allen drei Mutanten GRUEN — bewusst in dieser
        Pin-Datei (statt bestehende Tests in ``test_client_export*.py`` als Vehikel zu
        nutzen): jene tragen kein ``_encryption_key``-``usefixtures`` und waeren im
        ``.env``-losen Mutantenbaum aus dem FALSCHEN Grund rot."""
        request_client_deletion(client_identified, staff_user, "Pin099-Client2-Loeschgrund")
        request_rectification(client_identified, staff_user, "notes", "Pin099-Client2-Neu", "Pin099-Client2-Grund")
        case = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Pin099-Client2-Fall", created_by=staff_user
        )
        goal = OutcomeGoal.objects.create(case=case, title="Pin099-Client2-Ziel", created_by=staff_user)
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.GOAL_UPDATE,
            target_type="OutcomeGoal",
            target_id=str(goal.pk),
        )

        data = export_client_data(client_identified, facility, staff_user)

        actions = {row["action"] for row in data["audit_trail"]}
        assert AuditLog.Action.DELETION_REQUESTED.label in actions
        assert AuditLog.Action.RECTIFICATION_REQUESTED.label in actions
        assert AuditLog.Action.GOAL_UPDATE.label in actions


# ---------------------------------------------------------------------------
# targets.py
# ---------------------------------------------------------------------------


class TestClientActivityTargetsQ:
    def test_client_activity_targets_q_grenzt_ueber_target_type_ab(
        self,
        facility,
        admin_user,
        doc_type_contact,
        client_identified,
        staff_user,
    ):
        """Pins ``x_client_activity_targets_q__mutmut_11`` (Event-Zweig),
        ``…__mutmut_27`` (WorkItem-Zweig) und ``…__mutmut_43`` (Case-Zweig): jeder Zweig
        verliert seine ``target_type=ContentType.objects.get_for_model(...)``-Bedingung
        und filtert danach nur noch ueber ``target_id``. Kreuzproben-Activities tragen
        dieselbe ``target_id`` wie ein ECHTES Ziel der Person, aber den ``ContentType``
        von ``Client`` — unter dem Original ausgeschlossen (falscher Typ UND falsche
        ``target_id`` fuer den Client-Zweig), unter jeder Mutante ueber den jeweiligen
        entwerteten Zweig fälschlich mitgezaehlt."""
        from core.models import WorkItem

        sample_event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        sample_case = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Pin099-Client2-Fall", created_by=staff_user
        )
        sample_workitem = WorkItem.objects.create(
            facility=facility, client=client_identified, title="Pin099-Client2-Aufgabe", created_by=staff_user
        )

        def activity_for(target):
            return Activity.objects.create(
                facility=facility,
                actor=staff_user,
                verb=Activity.Verb.UPDATED,
                target=target,
                context={"schema": 1, "action": Activity.Action.REDACTED.value},
            )

        own = {activity_for(t).pk for t in (client_identified, sample_event, sample_workitem, sample_case)}

        client_content_type = ContentType.objects.get_for_model(Client)
        for foreign_target_id in (sample_event.pk, sample_workitem.pk, sample_case.pk):
            Activity.objects.create(
                facility=facility,
                actor=staff_user,
                verb=Activity.Verb.UPDATED,
                target_type=client_content_type,
                target_id=foreign_target_id,
                context={"schema": 1, "action": Activity.Action.REDACTED.value},
            )

        matches = set(
            Activity.objects.filter(client_activity_targets_q(client_identified, [sample_event.pk])).values_list(
                "pk", flat=True
            )
        )

        assert matches == own


class TestClientLegalHoldTargetsQ:
    def test_client_legal_hold_targets_q_grenzt_activity_ueber_target_type_ab(
        self, facility, admin_user, staff_user, doc_type_contact, client_identified
    ):
        """Pins ``x_client_legal_hold_targets_q__mutmut_25``: die Activity-Bedingung
        ``Q(target_type="Activity", target_id__in=activity_ids)`` verliert
        ``target_type="Activity"`` und filtert danach nur noch ueber ``target_id``. Eine
        Kreuzproben-``LegalHold`` traegt dieselbe ``target_id`` wie eine ECHTE Activity
        der Person, aber ``target_type="Client"`` — unter dem Original ausgeschlossen
        (falscher Typ fuer den Activity-Zweig, falsche ``target_id`` fuer den
        Client-Zweig), unter der Mutante ueber den entwerteten Activity-Zweig faelschlich
        mitgezaehlt."""
        sample_event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        own_activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            context={"schema": 1, "action": Activity.Action.REDACTED.value},
        )

        real_hold = LegalHold.objects.create(
            facility=facility,
            target_type="Activity",
            target_id=own_activity.pk,
            reason="Pin099-Client2-EchterActivityHold",
            created_by=admin_user,
        )
        cross_probe = LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=own_activity.pk,
            reason="Pin099-Client2-FalscherZieltyp",
            created_by=admin_user,
        )

        matches = set(
            LegalHold.objects.filter(client_legal_hold_targets_q(client_identified, [sample_event.pk])).values_list(
                "pk", flat=True
            )
        )

        assert matches == {real_hold.pk}
        assert cross_probe.pk not in matches
