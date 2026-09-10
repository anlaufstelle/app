"""Charakterisierungstests fuer Retention-Strategien (Refs #776).

Vier Strategien × pos/neg/boundary plus eine Cross-Strategy-Intersection
fuer das Cutoff-Verhalten in :mod:`core.retention.enforcement`.

Sicherheitsnetz fuer Refactorings: wenn das Submodul umgebaut
wird, muessen die Cutoffs in Tagen weiterhin **strikt unter** dem
``occurred_at`` greifen — nicht ``<=``, sonst loeschen wir Events am
Stichtag.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from core.models import AuditLog, Case, Event, LegalHold, Settings, ShiftHandoverNote
from core.retention.enforcement import (
    collect_doomed_events,
    enforce_anonymous,
    enforce_document_type_retention,
    enforce_handover_notes,
    enforce_identified,
    enforce_qualified,
)
from core.retention.proposals import create_proposals_for_facility
from core.services.file_vault import encrypt_field


def _at(days_ago: int):
    return timezone.now() - timedelta(days=days_ago)


@pytest.fixture
def settings_obj(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )


@pytest.mark.django_db
class TestEnforceAnonymous:
    def test_pos_old_event_is_doomed(self, facility, staff_user, doc_type_contact, settings_obj):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True) == {"count": 1}

    def test_neg_recent_event_is_kept(self, facility, staff_user, doc_type_contact, settings_obj):
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(1),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True) == {"count": 0}

    def test_boundary_exact_cutoff_is_kept(self, facility, staff_user, doc_type_contact, settings_obj):
        """Cutoff ist strict less-than (``<``), nicht ``<=`` — Stichtag bleibt."""
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(90),  # exakt am Cutoff
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        # Etwas nach dem Cutoff schauen, damit kein clock-skew-Race entsteht.
        now = timezone.now() + timedelta(microseconds=1)
        assert enforce_anonymous(facility, settings_obj, now, dry_run=True)["count"] == 1


@pytest.mark.django_db
class TestEnforceIdentified:
    def test_pos_old_identified_event(self, facility, staff_user, doc_type_contact, client_identified, settings_obj):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_recent_identified_event(self, facility, staff_user, doc_type_contact, client_identified, settings_obj):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(10),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_boundary_qualified_client_excluded(
        self, facility, staff_user, doc_type_contact, client_qualified, settings_obj
    ):
        """Qualified-Client-Events fallen NICHT unter ``enforce_identified`` —
        die Kategorisierung haengt am ContactStage des Klienten."""
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0


@pytest.mark.django_db
class TestEnforceQualified:
    def test_pos_event_in_long_closed_case(
        self, facility, staff_user, doc_type_contact, client_qualified, settings_obj
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Lang geschlossen",
            status=Case.Status.CLOSED,
            closed_at=_at(4000),
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_open_case_keeps_event(self, facility, staff_user, doc_type_contact, client_qualified, settings_obj):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Offen",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_neg_recently_closed_case(self, facility, staff_user, doc_type_contact, client_qualified, settings_obj):
        case = Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Frisch geschlossen",
            status=Case.Status.CLOSED,
            closed_at=_at(10),
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_qualified,
            case=case,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={},
            created_by=staff_user,
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0


@pytest.mark.django_db
class TestEnforceDocumentTypeRetention:
    def test_pos_doctype_specific_retention(self, facility, staff_user, doc_type_contact):
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(60),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 1

    def test_neg_no_retention_days_set(self, facility, staff_user, doc_type_contact):
        """``retention_days IS NULL`` -> DocumentType wird ignoriert."""
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(99999),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 0

    def test_boundary_recent_event_kept(self, facility, staff_user, doc_type_contact):
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(1),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 0

    def test_neg_client_entity_doctype_is_an_explicit_noop(
        self,
        facility,
        staff_user,
        admin_user,
        client_identified,
        doc_type_client_profile,
        doc_type_contact,
        settings_obj,
    ):
        """``retention_days`` bleibt event-only (Produktentscheid M24, Refs #1738).

        Fuer Personen- und Fall-Typen gilt der **Owner-Lebenszyklus**: ihre
        Daten (und damit ihre Anhaenge) fallen mit der Klienten-Anonymisierung,
        nicht ueber eine eigene Zeitfrist. Ein an einem Personen-Typ gesetztes
        ``retention_days`` darf deshalb nichts ausloesen.

        Der Test prueft beides:

        * **Wirkung** — die Person, ihr personen-eigener Anhang und die
          Vorschlagsliste bleiben unangetastet.
        * **Mechanik** — der Personen-Typ wird gar nicht erst abgefragt. Ohne
          den ``entity_type``-Filter lief pro Personen-/Fall-Typ eine
          Event-Count-Query, die nur deshalb leer blieb, weil ein Ereignis
          niemals einen Personen-Typ traegt. Diese stille Kopplung waere die
          Sollbruchstelle, sobald der Typ jemals an einer anderen Entitaet
          haengt; ohne die Query-Probe koennte der Filter unbemerkt wieder
          herausfallen (der Wirkungs-Teil allein bliebe gruen).
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from core.models import Attachment, FieldTemplate, RetentionProposal

        doc_type_client_profile.retention_days = 1
        doc_type_client_profile.save(update_fields=["retention_days"])
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type"])
        attachment = Attachment.objects.create(
            facility=facility,
            client=client_identified,
            field_template=FieldTemplate.objects.create(
                facility=facility,
                name="Personenanlage",
                field_type=FieldTemplate.FieldType.FILE,
            ),
            storage_filename="person.enc",
            original_filename_encrypted={"ct": "x", "iv": "y"},
            file_size=42,
            mime_type="text/plain",
            created_by=admin_user,
        )

        with CaptureQueriesContext(connection) as ctx:
            result = enforce_document_type_retention(facility, timezone.now(), dry_run=False)

        assert result == {"count": 0}
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        # psycopg rendert UUID-Parameter ohne Bindestriche — beide Schreibweisen
        # pruefen, sonst laeuft die Probe still ins Leere.
        needles = (str(doc_type_client_profile.pk), doc_type_client_profile.pk.hex)
        touching = [q["sql"] for q in ctx.captured_queries if any(n in q["sql"] for n in needles)]
        assert not touching, f"Personen-Typ wurde trotz Event-only-Frist abgefragt:\n{touching}"

        # Refs #1738 (G4): Die fruehere Zeile ``assert not RetentionProposal…exists()``
        # bewies nichts — ``enforce_document_type_retention`` legt gar keine
        # Vorschlaege an und es waren auch keine geseedet. Der Vorschlags-Pfad
        # haengt an ``iter_strategies``; dort wird er hier tatsaechlich
        # angestossen, mit Positiv-Kontrolle im selben Lauf.
        now = timezone.now()
        assert create_proposals_for_facility(facility, settings_obj, now) == {"count": 0}
        assert not RetentionProposal.objects.filter(facility=facility).exists()

        doc_type_contact.retention_days = doc_type_client_profile.retention_days
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(10),
            data_json={},
            created_by=staff_user,
        )
        assert create_proposals_for_facility(facility, settings_obj, now) == {"count": 1}, (
            "Positiv-Kontrolle: dieselbe Frist an einem EREIGNIS-Typ muss sehr wohl "
            "einen Loeschvorschlag erzeugen — sonst pinnt der Negativ-Teil nur eine "
            "kaputte Vorschlags-Pipeline."
        )

    def test_neg_client_entity_doctype_yields_no_strategy(
        self, facility, staff_user, client_identified, doc_type_client_profile, doc_type_contact, settings_obj
    ):
        """Der Event-only-Filter gehoert in ``iter_strategies`` (Refs #1738, G4).

        ``iter_strategies`` ist die gemeinsame Quelle von Dry-Run/Vorschau
        (``collect_doomed_events``) und Loeschvorschlaegen
        (``create_proposals_for_facility``). Seit AP3 filtert
        ``enforce_document_type_retention`` auf ``entity_type=EVENT``, die
        Strategie-Quelle aber nicht — die Frist eines Personen-/Fall-Typs lief
        dort weiter mit und blieb nur deshalb folgenlos, weil ein Ereignis nie
        einen Personen-Typ traegt. Genau diese stille Kopplung ist die
        Sollbruchstelle (Muster
        ``test_neg_client_entity_doctype_is_an_explicit_noop``): Der Filter macht
        die Regel explizit, an der einzigen Quelle statt nur am Durchsetzungslauf.

        Positiv-Kontrolle mit identischer Frist an einem EREIGNIS-Typ: Strategie,
        doomed Event und Vorschlag entstehen dort sehr wohl.
        """
        from core.retention.enforcement import collect_doomed_events
        from core.retention.strategies import iter_strategies

        now = timezone.now()
        doc_type_client_profile.retention_days = 1
        doc_type_client_profile.save(update_fields=["retention_days"])

        assert "document_type" not in [s.category for s in iter_strategies(facility, settings_obj, now)]
        assert collect_doomed_events(facility, settings_obj, now).count() == 0
        assert create_proposals_for_facility(facility, settings_obj, now) == {"count": 0}

        doc_type_contact.retention_days = 1
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(10),
            data_json={},
            created_by=staff_user,
        )
        categories = [s.category for s in iter_strategies(facility, settings_obj, now)]
        assert categories.count("document_type") == 1
        assert collect_doomed_events(facility, settings_obj, now).count() == 1
        assert create_proposals_for_facility(facility, settings_obj, now) == {"count": 1}


@pytest.mark.django_db
class TestNonPositiveDaysSkipped:
    """Security N8 (Refs #1445): 0/negative Fristen erzeugen einen Cutoff
    ``>= jetzt`` (``now - timedelta(days=-5) == now + 5d``) und wuerden den
    Gesamtbestand zum Loeschen vorschlagen. Validator + DB-Constraint
    verhindern das beim Speichern; ``iter_strategies`` ueberspringt solche
    Kategorien zusaetzlich defensiv (Alt-Daten / Direkt-Writes).

    Die Settings-Faelle klemmen das Attribut nur IM SPEICHER (kein ``save``) —
    der DB-Constraint wuerde eine negative Frist gar nicht erst persistieren.
    """

    def test_negative_anonymous_days_yields_no_anonymous_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_anonymous_days = -5
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "anonymous" not in categories
        # Die uebrigen (gueltigen) Kategorien bleiben erhalten.
        assert "identified" in categories
        assert "qualified" in categories

    def test_zero_identified_days_yields_no_identified_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_identified_days = 0
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "identified" not in categories
        assert "anonymous" in categories

    def test_negative_qualified_days_yields_no_qualified_strategy(self, facility, settings_obj):
        from core.retention.strategies import iter_strategies

        settings_obj.retention_qualified_days = -1
        categories = [s.category for s in iter_strategies(facility, settings_obj, timezone.now())]
        assert "qualified" not in categories

    def test_negative_anonymous_days_collects_no_doomed_event(
        self, facility, staff_user, doc_type_contact, settings_obj
    ):
        """End-to-end ueber ``collect_doomed_events`` (Proposal-Pfad): ein altes
        anonymes Event ist bei gueltiger Frist doomed, bei negativer Frist nicht."""
        from core.retention.enforcement import collect_doomed_events

        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        # Positivkontrolle: gueltige Frist -> Event ist doomed.
        assert collect_doomed_events(facility, settings_obj, timezone.now()).count() == 1
        # Negative Frist -> Strategie uebersprungen -> nichts doomed.
        settings_obj.retention_anonymous_days = -5
        assert collect_doomed_events(facility, settings_obj, timezone.now()).count() == 0

    def test_zero_days_does_not_delete_in_enforce_path(self, facility, staff_user, doc_type_contact, settings_obj):
        """Security N8: der DIREKTE Loeschpfad (``enforce_anonymous``, nicht nur
        der Proposal-Pfad) darf bei 0/negativer Frist NICHTS soft-loeschen —
        Defense-in-Depth neben dem DB-Constraint."""
        from core.retention.enforcement import enforce_anonymous

        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(100),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        settings_obj.retention_anonymous_days = 0
        result = enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=False)
        assert result == {"count": 0}
        event.refresh_from_db()
        assert event.is_deleted is False


@pytest.mark.django_db
class TestStrategyIntersection:
    """Cross-Strategy: ein Event darf nicht von zwei Strategien doppelt
    gezaehlt werden — die Strategien sind disjunkt nach ``is_anonymous`` und
    ``contact_stage`` des Klienten.
    """

    def test_anonymous_event_not_counted_by_identified(
        self, facility, staff_user, doc_type_contact, client_identified, settings_obj
    ):
        # Anonymes Event hat keinen Client → ``enforce_identified`` sieht es
        # gar nicht erst (client__in-Filter), waehrend ``enforce_anonymous``
        # es zaehlt.
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        ano = enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True)["count"]
        ident = enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"]
        assert (ano, ident) == (1, 0)

    def test_event_in_two_categories_collected_once(
        self, facility, staff_user, doc_type_contact, client_identified, settings_obj
    ):
        """Refs #778: ein Event matcht 'identified' (alter Klient-Event) UND
        'document_type' (DocType hat retention_days=30) — ``collect_doomed_events``
        muss es **einmal** liefern (``.distinct()``), nicht zweimal."""
        from core.retention.enforcement import collect_doomed_events

        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(400),
            data_json={},
            created_by=staff_user,
        )

        doomed = collect_doomed_events(facility, settings_obj, timezone.now())
        assert doomed.count() == 1, (
            "Cross-Strategy-Intersection: ein Event in 2 Kategorien darf nur "
            "einmal in collect_doomed_events landen (sonst werden EventHistory "
            "und AuditLog doppelt geschrieben)."
        )


@pytest.mark.django_db
class TestFallHoldsImVorfilter:
    """Refs #2125: ein gehaltener FALL scheidet schon im Vorfilter aus.

    Der verbindliche Halt ist seit Refs #2122 der Re-Check unter der Sperre
    (``_lock_event_and_recheck_hold``) — funktional fehlte also nichts. Die
    Sammelstellen filterten aber weiterhin nur Ereignis- und Personen-Holds,
    weshalb jedes Ereignis eines gehaltenen Falls in JEDEM Lauf erneut
    eingesammelt, gesperrt und als ``deferred`` protokolliert wurde. Das ist
    betrieblich sichtbares Dauerrauschen: Solange der Hold gilt, ist ``deferred``
    kein Signal mehr, sondern Grundzustand.
    """

    def _gehaltener_fall_mit_ereignis(self, facility, staff_user, lead_user, doc_type, person, **case_kwargs):
        case = Case.objects.create(
            facility=facility,
            client=person,
            title="Fall unter Anordnung",
            created_by=staff_user,
            **case_kwargs,
        )
        event = Event.objects.create(
            facility=facility,
            client=person,
            case=case,
            document_type=doc_type,
            occurred_at=_at(5000),
            data_json={"freitext": "Klartext"},
            created_by=staff_user,
        )
        LegalHold.objects.create(
            facility=facility,
            target_type="Case",
            target_id=case.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        return case, event

    def test_collect_doomed_events_laesst_das_ereignis_des_gehaltenen_falls_aus(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified, settings_obj
    ):
        _, event = self._gehaltener_fall_mit_ereignis(
            facility, staff_user, lead_user, doc_type_contact, client_identified
        )
        gesammelt = set(collect_doomed_events(facility, settings_obj, timezone.now()).values_list("pk", flat=True))
        assert event.pk not in gesammelt, "Das Ereignis eines gehaltenen Falls steckt weiter im Vorfilter (Refs #2125)"

    def test_enforce_identified_zaehlt_das_ereignis_des_gehaltenen_falls_nicht(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified, settings_obj
    ):
        self._gehaltener_fall_mit_ereignis(facility, staff_user, lead_user, doc_type_contact, client_identified)
        assert enforce_identified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_enforce_anonymous_zaehlt_das_ereignis_des_gehaltenen_falls_nicht(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified, settings_obj
    ):
        _, event = self._gehaltener_fall_mit_ereignis(
            facility, staff_user, lead_user, doc_type_contact, client_identified
        )
        Event.objects.filter(pk=event.pk).update(is_anonymous=True)
        assert enforce_anonymous(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_enforce_qualified_zaehlt_das_ereignis_des_gehaltenen_falls_nicht(
        self, facility, staff_user, lead_user, doc_type_contact, client_qualified, settings_obj
    ):
        self._gehaltener_fall_mit_ereignis(
            facility,
            staff_user,
            lead_user,
            doc_type_contact,
            client_qualified,
            status=Case.Status.CLOSED,
            closed_at=_at(4000),
        )
        assert enforce_qualified(facility, settings_obj, timezone.now(), dry_run=True)["count"] == 0

    def test_enforce_document_type_retention_zaehlt_das_ereignis_des_gehaltenen_falls_nicht(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified, settings_obj
    ):
        doc_type_contact.retention_days = 30
        doc_type_contact.save(update_fields=["retention_days"])
        self._gehaltener_fall_mit_ereignis(facility, staff_user, lead_user, doc_type_contact, client_identified)
        assert enforce_document_type_retention(facility, timezone.now(), dry_run=True)["count"] == 0

    def test_der_lauf_meldet_kein_deferred_mehr_fuer_den_gehaltenen_fall(
        self, facility, staff_user, lead_user, doc_type_contact, client_identified, settings_obj
    ):
        """Der betrieblich sichtbare Kern: kein Dauer-``deferred`` im Lauf-Protokoll."""
        self._gehaltener_fall_mit_ereignis(facility, staff_user, lead_user, doc_type_contact, client_identified)
        # Positiv-Kontrolle: ein faelliges Ereignis OHNE Fall-Hold laeuft durch.
        frei = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=_at(5000),
            data_json={"freitext": "Klartext"},
            created_by=staff_user,
        )

        result = enforce_identified(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["count"] == 1
        frei.refresh_from_db()
        assert frei.is_deleted is True, "Positiv-Kontrolle: der Lauf agiert real"
        audit = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.DELETE,
            detail__category="identified",
        ).latest("timestamp")
        assert "deferred" not in audit.detail, (
            f"Der Lauf meldet weiterhin ein zurueckgestelltes Ereignis fuer einen gehaltenen Fall: {audit.detail}"
        )


@pytest.mark.django_db
class TestEnforceHandoverNotes:
    """Refs #2042: eigene, kurze Frist fuer die manuelle Schichtnotiz.

    Muster ``enforce_activities``: physische Loeschung, je Zeile Sperre +
    Re-Read, eine ``audit_retention_decision``-Zeile je Lauf mit Treffern.
    Bewusst OHNE Hold-Ziel-Typ — eine Uebergabenotiz ist keine Personenakte
    (bewusste Grenze, ``docs/security-notes.md`` § Hold-Kontrakt).
    """

    @staticmethod
    def _notiz(facility, staff_user, *, alter_tage: int, shift_date=None):
        from datetime import date

        tag = shift_date or date(2026, 1, 1)
        beginn = timezone.make_aware(datetime.combine(tag, time.min))
        note = ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=tag,
            time_filter=None,
            note=encrypt_field("Freitext"),
            author=staff_user,
            updated_by=staff_user,
            # Refs #2042 (E-01): die Fenstergrenzen sind seit Fix-Runde 1
            # Pflichtfelder. Fuer die Retention selbst spielen sie keine Rolle
            # — massgeblich ist ``updated_at`` —, sie muessen aber plausibel
            # gesetzt sein.
            window_start=beginn,
            window_end=timezone.make_aware(datetime.combine(tag, time.max)),
            editable_until=timezone.make_aware(datetime.combine(tag + timedelta(days=1), time.max)),
        )
        ShiftHandoverNote.objects.filter(pk=note.pk).update(updated_at=_at(alter_tage))
        return note

    def test_pos_alte_notiz_wird_geloescht(self, facility, staff_user, settings_obj):
        note = self._notiz(facility, staff_user, alter_tage=120)
        assert enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False) == {"count": 1}
        assert not ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_neg_junge_notiz_bleibt(self, facility, staff_user, settings_obj):
        note = self._notiz(facility, staff_user, alter_tage=30)
        assert enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False) == {"count": 0}
        assert ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_boundary_genau_auf_der_frist_bleibt(self, facility, staff_user, settings_obj):
        """Cutoff ist strikt ``<`` — am Stichtag wird nicht geloescht.

        ``now`` wird EINMAL festgehalten und sowohl fuer ``updated_at`` als auch
        fuer den Lauf verwendet: mit zwei ``timezone.now()``-Aufrufen laege die
        Zeile um die Laufzeit dazwischen vor dem Stichtag und der Test pruefte
        nicht mehr den Grenzfall, sondern die Uhr.
        """
        jetzt = timezone.now()
        note = self._notiz(facility, staff_user, alter_tage=0)
        ShiftHandoverNote.objects.filter(pk=note.pk).update(updated_at=jetzt - timedelta(days=90))
        result = enforce_handover_notes(facility, settings_obj, jetzt, dry_run=True)
        assert result == {"count": 0}
        assert ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_boundary_eine_sekunde_darueber_wird_geloescht(self, facility, staff_user, settings_obj):
        """Gegenprobe zum Grenzfall: eine Sekunde aelter faellt."""
        jetzt = timezone.now()
        note = self._notiz(facility, staff_user, alter_tage=0)
        ShiftHandoverNote.objects.filter(pk=note.pk).update(updated_at=jetzt - timedelta(days=90, seconds=1))
        assert enforce_handover_notes(facility, settings_obj, jetzt, dry_run=False) == {"count": 1}
        assert not ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_dry_run_zaehlt_ohne_zu_loeschen(self, facility, staff_user, settings_obj):
        note = self._notiz(facility, staff_user, alter_tage=120)
        assert enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=True) == {"count": 1}
        assert ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_frist_unter_eins_bricht_defensiv_ab(self, facility, staff_user, settings_obj):
        settings_obj.retention_handover_notes_days = 0
        settings_obj.save(update_fields=["retention_handover_notes_days"])
        note = self._notiz(facility, staff_user, alter_tage=5000)
        assert enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False) == {"count": 0}
        assert ShiftHandoverNote.objects.filter(pk=note.pk).exists()

    def test_fremde_einrichtung_bleibt_unberuehrt(self, facility, second_facility, staff_user, settings_obj):
        from datetime import date

        fremd = ShiftHandoverNote.objects.create(
            facility=second_facility,
            shift_date=date(2026, 1, 1),
            time_filter=None,
            note=encrypt_field("Freitext"),
            window_start=timezone.make_aware(datetime.combine(date(2026, 1, 1), time.min)),
            window_end=timezone.make_aware(datetime.combine(date(2026, 1, 1), time.max)),
            editable_until=timezone.make_aware(datetime.combine(date(2026, 1, 2), time.max)),
        )
        ShiftHandoverNote.objects.filter(pk=fremd.pk).update(updated_at=_at(500))
        assert enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False) == {"count": 0}
        assert ShiftHandoverNote.objects.filter(pk=fremd.pk).exists()

    def test_audit_zeile_traegt_kategorie_und_frist_ohne_text(self, facility, staff_user, settings_obj):
        self._notiz(facility, staff_user, alter_tage=120)
        enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False)
        audit = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.DELETE,
            detail__category="handover_notes",
        ).latest("timestamp")
        assert audit.target_type == "ShiftHandoverNote"
        assert audit.detail["count"] == 1
        assert audit.detail["retention_days"] == 90
        assert audit.detail["command"] == "enforce_retention"
        assert "Freitext" not in str(audit.detail)

    def test_ohne_treffer_keine_audit_zeile(self, facility, staff_user, settings_obj):
        self._notiz(facility, staff_user, alter_tage=5)
        enforce_handover_notes(facility, settings_obj, timezone.now(), dry_run=False)
        assert not AuditLog.objects.filter(detail__category="handover_notes").exists()
