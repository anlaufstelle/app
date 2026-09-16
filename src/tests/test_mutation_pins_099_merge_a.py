"""Vollmutation Welle L (0.99), #2174/#2193 — Batch ``merge-a``.

Quelle: ``reports/m-scratch/batch-merge-a.json`` (67 ``survived``-Mutanten,
ausschliesslich in :func:`core.services.client.merge.unmerge_client`).

**Meldungs-Verlust-Gruppen (bindend Klasse ``test``, Prio 3, exakte
Assertion auf ``exc.value.messages``):** fuenf Guard-Clauses der Funktion
tragen je einen deutschen ``ValidationError``-Text; mutmut variiert jeden
Text als ``None``/``_(None)``/XX-Marker/Kleinschreibung/Grossschreibung.
Zwei dieser fuenf Clauses (die zweite „Diese Person ist nicht
zusammengeführt."-Pruefung nach dem Zeilen-Lock und die LIFO-Pruefung der
Ziel-Person) werden von KEINEM Bestandstest ueberhaupt erreicht — die
Tests unten stellen zuerst das ausloesende Szenario her (TOCTOU-Neuzeigung
des Quell-``merged_into``, bzw. eine LIFO-Kette A→B→C) und pruefen dann den
exakten Wortlaut. Die dritte Gruppe („Die Quelle wurde bereits endgültig
anonymisiert …") wird zwar von ``test_client_merge_workflow.py`` erreicht,
aber nur mit einem klein geschriebenen Teilstring-Vergleich (``"anonymisiert"
in message.lower()``) geprueft — das laesst XX-Marker- und
Gross-/Kleinschreibungs-Varianten ueberleben, weil das gesuchte Teilwort in
allen Varianten weiter vorkommt.

**Undo-Inventar-Scoping (Klasse ``test``, Prio 1 — Datenintegritaet):**
``unmerge_client`` schreibt Faelle/Ereignisse/Aufgaben NUR anhand des im
``CLIENT_MERGED``-Audit hinterlegten Inventars zurueck. Drei Luecken bleiben
nach dem bereits gepinnten Aufgaben-/Anhangs-Kwarg (096/098-Pins):
``WorkItem``-Filter ohne ``client=target`` (reisst eine zwischenzeitlich an
eine DRITTE Person umgehaengte Aufgabe zurueck), der ``Event``-Filter ohne
``case__isnull=True`` (reisst ein Ereignis mit, das nach dem Merge einem am
Ziel VERBLEIBENDEN Fall zugeordnet wurde — verletzt die Invariante
``Event.client == Event.case.client``) und der Abschluss-Guard
``_assert_event_case_consistency(source, target)`` selbst, dessen beide
Argumente unabhaengig entfernbar sind: je EINE Seite (Quelle/Ziel) einer
ausserhalb der Dienstschicht entstandenen Inkonsistenz bleibt dann
unentdeckt, obwohl der Guard genau dafuer da ist („Abschluss-Check der
Transaktion").

**Rechenschaftsfelder (Klasse ``test``, Prio 1 fuer Akteur/Audit, Prio 3 fuer
die reine Logzeile):** der ``Activity``-Akteur des Aufhebens, der
``old_stage`` der Kontaktstufen-Herabstufung und die strukturierten Felder
der ``client_unmerged``-Logzeile (Pendant zu
``test_client_merged_log_has_exact_message_and_structured_fields`` in
``test_client_merge_workflow.py``, dort nur fuer die Merge-Richtung
gepinnt).

**Belegte Aequivalenzen (18 Mutanten, mit Diff + Testlauf unter der Mutante
verifiziert — alle bleiben unter JEDEM Test dieser Datei gruen):**

* ``x_unmerge_client__mutmut_57/59/64/66/71/73/78/80/85/87/92/94/97`` —
  ``inventory.get("moved_cases"/"moved_events"/"moved_workitems"/
  "moved_attachments"/"adopted_slugs"/"document_type_adopted", <default>)``:
  der einzige Schreiber der ``CLIENT_MERGED``-Zeile ist ``_execute_merge``,
  und der schreibt ALLE sechs Schluessel immer explizit (auch leere Listen/
  ``False``) — ``validate_audit_detail`` filtert keine Schluessel heraus.
  Der ``.get()``-Default ist damit fuer jedes ueber ``_merge_inventory``
  gelesene ``detail`` unerreichbarer Code; alle sechs Werte werden zudem nur
  hinter ``if X:``-Wahrheitspruefungen verwendet, sodass selbst ein
  erreichbarer ``None``-Default sich nicht von ``[]``/``False`` unterscheiden
  wuerde.
* ``x_unmerge_client__mutmut_110/111`` — ``event_filter |= Q(pk__in=
  moved_events, case__isnull=True)`` zu ``=``/``&=`` verkuerzt: empirisch
  verifiziert (``Q() | Q(x) == Q() & Q(x) == Q(x)``, Djangos ``Q._combine``
  gibt bei leerem ``self`` unabhaengig vom Verknuepfer eine Kopie von
  ``other`` zurueck) — ``event_filter`` ist an dieser Stelle IMMER frisch
  ``Q()`` (zwei Zeilen zuvor initialisiert), der Operator der ERSTEN
  Zuweisung ist deshalb folgenlos.
* ``x_unmerge_client__mutmut_147/158`` — zwei ``target.save(update_fields=
  [...])`` verlieren die Feldliste (``update_fields=None`` = voller Save).
  Drei Belege, warum das hier zustandsgleich bleibt: (1) ``target`` ist zum
  Save-Zeitpunkt frisch geladen und per ``_locked_clients``
  (``select_for_update()``) gesperrt — kein anderer Prozess kann dazwischen
  eine andere Spalte geaendert haben, die ein voller Save ueberschreiben
  koennte. (2) ``updated_at`` steht in BEIDEN Feldlisten bereits explizit
  (``["document_type", "data_json", "updated_at"]`` bzw. ``["contact_stage",
  "updated_at"]``) — das ``auto_now``-Feld wird also so oder so auf
  denselben Zeitstempel gesetzt, ein voller Save bumpt hier nichts
  zusaetzlich.
  (3) kein anderes Feld von ``target`` weicht zum Save-Zeitpunkt vom
  DB-Stand ab (``Client.save()`` verschluesselt unbedingt und ruft danach
  ``super().save(*args, **kwargs)`` ohne weitere, von ``update_fields``
  abhaengige Verzweigung; kein ``post_save``-Empfaenger auf ``Client``) — ein
  voller Save schreibt an beiden Stellen identische Werte wie der gezielte.
  ``x_unmerge_client__mutmut_172`` (``source.save(update_fields=
  ["merged_into"])`` verliert die Feldliste) ist dagegen NICHT aequivalent:
  ``updated_at`` fehlt in dieser Feldliste, ein voller Save wendet
  ``auto_now`` trotzdem auf ALLE Felder an und bumpt ``source.updated_at``
  zusaetzlich — beobachtbar, siehe ``TestUndoInventoryScoping::
  test_source_merged_into_reset_leaves_updated_at_untouched`` unten.
* ``x_unmerge_client__mutmut_216`` — ``getattr(user, "pk", None)`` verliert
  den Default. ``user`` ist an dieser Stelle immer eine echte, bereits durch
  ``user.is_lead_or_admin`` gelesene ``User``-Instanz — ``.pk`` existiert
  IMMER, der Default-Pfad ist unerreichbar.

Konventionen wie ``test_mutation_pins_098_merge.py``: exakte Vergleiche
(``==``), Fixtures aus ``src/tests/conftest.py`` bzw. echte Model-Objekte,
kein Nachbau von Produktionslogik.
"""

import logging
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Activity, AuditLog, Case, Client, Event, FieldTemplate
from core.models.workitem import WorkItem
from core.services.client.merge import approve_client_merge, merge_clients, request_client_merge, unmerge_client

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

LOGGER_NAME = "core.services.client.merge"


def _identified(facility, user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=user,
    )


@pytest.fixture
def source(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeA-Quelle")


@pytest.fixture
def target(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeA-Ziel")


@pytest.fixture
def stranger(facility, staff_user):
    return _identified(facility, staff_user, "Pin099-MergeA-Dritte")


class TestGuardMessagesExactWording:
    """Fuenf ``ValidationError``-Texte, exakt statt als Teilstring geprueft."""

    def test_refuses_a_non_lead_or_admin_user(self, source, staff_user):
        """Pin: ``x_unmerge_client__mutmut_2`` (Meldung -> ``None``), ``_3``
        (``_(None)``), ``_4``/``_5``/``_6`` (XX-Marker/klein/GROSS).

        Original: ``teststaff`` (Rolle ``STAFF``) bekommt exakt „Nur die
        Leitung darf eine Zusammenführung aufheben." — unabhaengig vom
        Merge-Zustand, die Pruefung steht als ERSTE Zeile der Funktion.
        """
        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, staff_user)

        assert exc.value.messages == ["Nur die Leitung darf eine Zusammenführung aufheben."]

    def test_refuses_a_client_that_was_never_merged(self, source, lead_user):
        """Pin: ``x_unmerge_client__mutmut_8`` (Meldung -> ``None``), ``_10``/
        ``_11``/``_12`` (XX-Marker/klein/GROSS) — die ERSTE der beiden
        ``merged_into``-Pruefungen (vor dem Zeilen-Lock).

        Original: eine niemals zusammengefuehrte Person liefert exakt
        „Diese Person ist nicht zusammengeführt."
        """
        assert source.merged_into_id is None

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert exc.value.messages == ["Diese Person ist nicht zusammengeführt."]

    def test_refuses_when_the_source_merge_pointer_was_reassigned_concurrently(
        self, facility, source, target, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_24`` (Meldung -> ``None``), ``_25``
        (``_(None)``), ``_26``/``_27``/``_28`` (XX-Marker/klein/GROSS) — die
        ZWEITE ``merged_into``-Pruefung, NACH ``_locked_clients``.

        Kein Bestandstest erreicht diese Zeile ueberhaupt: sie schuetzt vor
        einer Race — zwischen dem initialen Lesen von
        ``source.merged_into_id`` (durch die Aufruferin, HIER simuliert
        durch ein bewusst „stale" Python-Objekt) und dem Zeilen-Lock hat ein
        anderer Vorgang den Zeiger umgehaengt. Original: exakt „Diese Person
        ist nicht zusammengeführt." (dieselbe Meldung wie oben, andere
        Zeile/anderer Ausloeser).
        """
        other_target = _identified(facility, staff_user, "Pin099-MergeA-Ziel2")
        merge_clients(source, target, lead_user)
        stale_source = Client.objects.get(pk=source.pk)
        assert stale_source.merged_into_id == target.pk

        # Ausserhalb der Dienstschicht (Reparatur/zweiter Vorgang): die
        # Quelle zeigt jetzt auf eine ANDERE Person.
        Client.objects.filter(pk=source.pk).update(merged_into=other_target)

        with pytest.raises(ValidationError) as exc:
            unmerge_client(stale_source, lead_user)

        assert exc.value.messages == ["Diese Person ist nicht zusammengeführt."]

    def test_refuses_an_anonymized_source_with_exact_wording(self, source, target, lead_user):
        """Pin: ``x_unmerge_client__mutmut_41``/``_42``/``_43`` (XX-Marker/
        klein/GROSS) — ``test_client_merge_workflow.py::
        test_unmerge_refuses_source_with_anonymized_at_even_if_active``
        prueft nur ``"anonymisiert" in message.lower()``; das Teilwort
        kommt in allen drei Varianten weiter vor, die Mutanten ueberleben
        dort. Original: exakt „Die Quelle wurde bereits endgültig
        anonymisiert — die Zusammenführung ist nicht mehr umkehrbar."
        """
        merge_clients(source, target, lead_user)
        Client.objects.filter(pk=source.pk).update(anonymized_at=timezone.now(), is_active=True)
        source.refresh_from_db()

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert exc.value.messages == [
            "Die Quelle wurde bereits endgültig anonymisiert — die Zusammenführung ist nicht mehr umkehrbar."
        ]

    def test_refuses_when_the_target_was_itself_merged_onward(self, facility, source, target, staff_user, lead_user):
        """Pin: ``x_unmerge_client__mutmut_45`` (ganze Meldung -> ``None``),
        ``_46`` (``_(None)`` im ersten Halbsatz), ``_47``/``_48``/``_49``
        (XX-Marker/klein/GROSS erster Halbsatz), ``_50``/``_51``/``_52``
        (XX-Marker/klein/GROSS zweiter Halbsatz).

        Kettenmerge A→B→C (LIFO, ADR-045 Entscheid 2): B traegt seinerseits
        einen weiteren Merge-Tombstone. Der Ketten-Guard in
        ``_validate_merge`` verhindert genau diesen Zustand nur fuer NEUE
        Merges UEBER DEN SERVICE-WEG (B darf nicht Quelle werden, solange
        es den aktiven Tombstone A traegt) — hier als Reparatur-/Altzustand
        (z. B. Altdaten-Migration) direkt hergestellt, denn die Pruefung in
        ``unmerge_client`` ist die Tiefen-Abwehr genau fuer diesen Fall.
        Original: exakt „Die Ziel-Person wurde ihrerseits zusammengeführt —
        zuerst deren Zusammenführung aufheben (jüngste zuerst)."
        """
        third = _identified(facility, staff_user, "Pin099-MergeA-C")
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        assert source.merged_into_id == target.pk

        Client.objects.filter(pk=target.pk).update(
            merged_into=third, is_deleted=True, deleted_at=timezone.now(), deleted_by=lead_user
        )

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert exc.value.messages == [
            "Die Ziel-Person wurde ihrerseits zusammengeführt — zuerst deren Zusammenführung aufheben (jüngste zuerst)."
        ]


class TestEventCaseInvariantGuards:
    """``Event.client == Event.case.client`` — der Abschluss-Check der Transaktion."""

    def test_an_event_reassigned_to_a_case_that_stays_at_the_target_is_left_alone(
        self, facility, source, target, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_115`` — ``event_filter |= Q(pk__in=
        moved_events, case__isnull=True)`` verliert ``case__isnull=True``.

        Das Ereignis war zum Merge-Zeitpunkt fallfrei (im Inventar als
        ``moved_events`` gelistet) und wandert regulaer zum Ziel. NACH dem
        Merge wird es dort einem NEUEN Fall zugeordnet, der beim Ziel bleibt.
        Original: der Fall-Filter des Event-Rueckzugs greift nicht (Fall
        nicht in ``moved_cases``), das Ereignis bleibt beim Ziel — Invariante
        haelt. Mutant: das veraltete ``moved_events``-Inventar reisst es
        ueber die reine PK-Liste zurueck, waehrend sein Fall am Ziel bleibt
        — ``_assert_event_case_consistency`` bricht die Transaktion mit
        einem ``ValidationError`` ab, wo das Original klaglos durchlaeuft.
        """
        event = Event.objects.create(
            facility=facility,
            client=source,
            case=None,
            document_type=doc_type_client_profile,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        merge_clients(source, target, lead_user)
        new_case_at_target = Case.objects.create(
            facility=facility, client=target, title="Fall Ziel (nach Merge)", created_by=staff_user
        )
        Event.objects.filter(pk=event.pk).update(case=new_case_at_target)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        event.refresh_from_db()
        new_case_at_target.refresh_from_db()
        assert event.client_id == target.pk
        assert event.case_id == new_case_at_target.pk
        assert new_case_at_target.client_id == target.pk

    def test_catches_a_leftover_inconsistency_on_the_source_side(
        self, facility, source, target, stranger, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_125`` — ``_assert_event_case_
        consistency(source, target)`` -> ``_assert_event_case_consistency
        (target)`` (verliert ``source``).

        Fall und Ereignis wandern regulaer mit dem Merge zum Ziel. Ausserhalb
        der Dienstschicht wird das Ereignis danach einer DRITTEN Person
        zugeordnet — der Fall bleibt (voruebergehend) am Ziel haengen. Beim
        Aufheben wandert der Fall zur Quelle zurueck, das veraltete Ereignis
        bleibt bei der Dritten: eine Inkonsistenz, die NUR auf der
        Quell-Seite sichtbar wird. Original: ``_assert_event_case_
        consistency`` prueft beide Seiten und bricht ab. Mutant: prueft nur
        das Ziel (dort inzwischen sauber) und committet die Inkonsistenz
        klaglos.
        """
        moved_case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)
        moved_event = Event.objects.create(
            facility=facility,
            client=source,
            case=moved_case,
            document_type=doc_type_client_profile,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        merge_clients(source, target, lead_user)
        Event.objects.filter(pk=moved_event.pk).update(client=stranger.pk)
        source.refresh_from_db()

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert exc.value.messages == [
            "Interner Fehler: Ereignis- und Fall-Zuordnung sind inkonsistent — Zusammenführung abgebrochen."
        ]

    def test_catches_a_leftover_inconsistency_on_the_target_side(
        self, facility, source, target, stranger, doc_type_client_profile, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_126`` — ``_assert_event_case_
        consistency(source, target)`` -> ``_assert_event_case_consistency
        (source, )`` (verliert ``target``).

        Fall und Ereignis gehoeren von Anfang an dem ZIEL, sind nicht Teil
        des Quell-Inventars und bleiben vom Merge/Unmerge unberuehrt.
        Ausserhalb der Dienstschicht wird das Ereignis einer DRITTEN Person
        zugeordnet — eine Inkonsistenz, die NUR auf der Ziel-Seite sichtbar
        ist. Original: bricht ab. Mutant: prueft nur die Quelle (dort nichts
        falsch) und committet klaglos.
        """
        target_case = Case.objects.create(
            facility=facility, client=target, title="Fall Ziel (unabhaengig)", created_by=staff_user
        )
        target_event = Event.objects.create(
            facility=facility,
            client=target,
            case=target_case,
            document_type=doc_type_client_profile,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        merge_clients(source, target, lead_user)
        Event.objects.filter(pk=target_event.pk).update(client=stranger.pk)
        source.refresh_from_db()

        with pytest.raises(ValidationError) as exc:
            unmerge_client(source, lead_user)

        assert exc.value.messages == [
            "Interner Fehler: Ereignis- und Fall-Zuordnung sind inkonsistent — Zusammenführung abgebrochen."
        ]


class TestUndoInventoryScoping:
    """Nur was wanderte, wandert zurueck — Aufgaben- und Datenfeld-Grenze."""

    def test_a_workitem_moved_away_after_the_merge_is_left_alone(
        self, facility, source, target, stranger, staff_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_131`` — ``WorkItem.objects.filter(
        pk__in=moved_workitems, client=target)`` verliert ``client=target``.

        Spiegel zu ``test_an_attachment_moved_away_after_the_merge_is_left_
        alone`` (098-Pins), hier fuer ``WorkItem``: die Aufgabe wandert mit
        dem Merge zum Ziel, wird danach an eine DRITTE Person umgehaengt.
        Original: der ``client=target``-Filter trifft 0 Zeilen, sie bleibt
        bei der Dritten. Mutant: das veraltete Inventar reisst sie zur
        wiederhergestellten Quelle.
        """
        moved_item = WorkItem.objects.create(
            facility=facility, client=source, title="Aufgabe Quelle", created_by=staff_user
        )
        merge_clients(source, target, lead_user)
        WorkItem.objects.filter(pk=moved_item.pk).update(client=stranger.pk)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        moved_item.refresh_from_db()
        assert moved_item.client_id == stranger.pk

    def test_tolerates_a_target_edit_that_removed_an_adopted_slug(
        self, facility, source, target, doc_type_client_profile, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_143`` — ``data.pop(slug, None)``
        verliert den Default (entspricht ``data.pop(slug)``).

        Dokumentierte Grenze des Minimal-Undo: „Zwischenzeitliche Ziel-Edits
        GENAU DIESER Felder gehen dabei verloren" — das Ziel-``data_json``
        kann den uebernommenen Slug zum Aufheben-Zeitpunkt bereits verloren
        haben. Original: ``pop(slug, None)`` ignoriert das klaglos. Mutant:
        ``pop(slug)`` ohne Default wirft ``KeyError`` und reisst die ganze
        Transaktion um.
        """
        source.document_type = doc_type_client_profile
        source.data_json = {"wohnsituation": "Notunterkunft"}
        source.save()
        target.document_type = doc_type_client_profile
        target.data_json = {}
        target.save()

        merge_clients(source, target, lead_user)
        target.refresh_from_db()
        assert "wohnsituation" in target.data_json

        # Zwischenzeitlicher Ziel-Edit entfernt genau das uebernommene Feld.
        target.data_json = {k: v for k, v in target.data_json.items() if k != "wohnsituation"}
        target.save()
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.merged_into_id is None

    def test_source_merged_into_reset_leaves_updated_at_untouched(self, source, target, lead_user):
        """Pin: ``x_unmerge_client__mutmut_172`` — ``source.save(update_fields=
        ["merged_into"])`` verliert die Feldliste (-> ``update_fields=None``).

        ``Client.updated_at`` ist ``auto_now=True`` und NICHT in dieser
        Feldliste — das Original laesst den Zeitstempel deshalb unberuehrt
        (``auto_now`` wird nur fuer Felder angewandt, die explizit in
        ``update_fields`` stehen). Mutant: der volle Save
        (``update_fields=None``) wendet ``auto_now`` auf ALLE Felder an und
        bumpt ``source.updated_at`` zusaetzlich — kuenstlich gealtert, um den
        Unterschied beobachtbar zu machen (``source.restore()`` direkt danach
        sperrt sich selbst wieder auf ``["is_deleted", "deleted_at",
        "deleted_by"]`` und beruehrt ``updated_at`` ebenfalls nicht).
        """
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        aged = source.updated_at - timedelta(days=1)
        Client.objects.filter(pk=source.pk).update(updated_at=aged)
        source.refresh_from_db()
        assert source.updated_at == aged

        unmerge_client(source, lead_user)

        source.refresh_from_db()
        assert source.updated_at == aged


class TestUnmergeAccountability:
    """Akteur, Kontaktstufen-Herabstufung und die strukturierte Logzeile."""

    def test_activity_carries_the_acting_user_as_actor(self, source, target, lead_user):
        """Pin: ``x_unmerge_client__mutmut_189`` — ``actor=user`` -> ``actor=
        None`` im ``log_activity``-Aufruf.

        Original: die ``CLIENT_UNMERGED``-Zeitstrom-Zeile traegt die
        aufhebende Person als Akteur.
        """
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        row = Activity.objects.get(target_id=target.pk, context__action=Activity.Action.CLIENT_UNMERGED.value)
        assert row.actor_id == lead_user.pk

    def test_stage_rollback_audit_carries_the_correct_previous_stage(
        self, facility, source, target, staff_user, admin_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_164`` — ``update_client_stage(
        target, Client.ContactStage.QUALIFIED, Client.ContactStage.
        IDENTIFIED, user)`` verliert das dritte-letzte Argument (``old_stage``
        -> ``None``).

        Quelle traegt ``QUALIFIED`` (Vier-Augen-Weg), Ziel startet
        ``IDENTIFIED``: der Merge hebt das Ziel auf ``QUALIFIED``
        („Kontaktstufe: hoechste gewinnt"). Original: das Aufheben schreibt
        ``STAGE_CHANGE`` mit ``old_stage="qualified"``/``new_stage=
        "identified"``. Mutant: ``old_stage`` wird ``None`` — das
        Rechenschaftsfeld der Herabstufung ist falsch.
        """
        source.contact_stage = Client.ContactStage.QUALIFIED
        source.save()

        dr = request_client_merge(source, target, staff_user, reason="Pin099-MergeA Vier-Augen")
        approve_client_merge(dr, admin_user)
        target.refresh_from_db()
        assert target.contact_stage == Client.ContactStage.QUALIFIED
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        row = (
            AuditLog.objects.filter(
                action=AuditLog.Action.STAGE_CHANGE,
                target_type="Client",
                target_id=str(target.pk),
            )
            .order_by("-timestamp")
            .first()
        )
        assert row.detail == {"old_stage": "qualified", "new_stage": "identified"}

    def test_contact_stage_rollback_save_does_not_persist_a_pending_encryption_transform(
        self, facility, source, target, doc_type_client_profile, staff_user, admin_user, lead_user
    ):
        """Pin: ``x_unmerge_client__mutmut_158`` — ``target.save(update_fields=
        ["contact_stage", "updated_at"])`` verliert die Feldliste (->
        ``update_fields=None``).

        ``Client.save()`` (``core/models/client.py``) ruft VOR jedem Save
        unbedingt ``_encrypt_sensitive_fields()`` (``core/models/mixins.py``)
        auf — der Hook mutiert ``self.data_json`` IN-MEMORY, sobald ein
        ``FieldTemplate`` nachtraeglich auf ``is_encrypted=True`` gestellt
        wurde, waehrend die DB noch den Klartext traegt (z. B. eine
        Einrichtung aktiviert die Verschluesselung fuer ein bestehendes
        Feld). OB diese Transformation tatsaechlich persistiert, entscheidet
        NICHT der Hook, sondern die ``update_fields``-Liste DIESES
        Save-Aufrufs: Original (``["contact_stage", "updated_at"]``)
        schreibt ``data_json`` gar nicht in die UPDATE-Anweisung — der
        DB-Wert bleibt Klartext. Mutant (``update_fields=None``, voller
        Save) schreibt ALLE Spalten inkl. der in-memory verschluesselten
        ``data_json`` — der Klartext in der DB wird ueberschrieben.

        Rohwerte bewusst per ``values_list(..., flat=True)`` gelesen (kein
        Modell-Roundtrip, der selbst wieder ``_encrypt_sensitive_fields``
        anstossen koennte).
        """
        target.document_type = doc_type_client_profile
        target.data_json = {"wohnsituation": "Klartext vor Verschlüsselung"}
        target.save()
        raw_before = Client.objects.filter(pk=target.pk).values_list("data_json", flat=True).first()
        assert raw_before["wohnsituation"] == "Klartext vor Verschlüsselung"

        # Verschluesselung wird NACHTRAEGLICH aktiviert — die DB traegt noch
        # den Klartext, der Save-Hook wuerde ihn ab jetzt verschluesseln.
        FieldTemplate.objects.filter(facility=facility, name="Wohnsituation").update(is_encrypted=True)

        source.contact_stage = Client.ContactStage.QUALIFIED
        source.save()
        dr = request_client_merge(source, target, staff_user, reason="Pin099-MergeA Vier-Augen (Verschlüsselung)")
        approve_client_merge(dr, admin_user)
        target.refresh_from_db()
        assert target.contact_stage == Client.ContactStage.QUALIFIED
        source.refresh_from_db()

        unmerge_client(source, lead_user)

        raw_after = Client.objects.filter(pk=target.pk).values_list("data_json", flat=True).first()
        assert raw_after["wohnsituation"] == "Klartext vor Verschlüsselung"

    def test_client_unmerged_log_has_exact_message_and_structured_fields(self, source, target, lead_user, caplog):
        """Pin: ``x_unmerge_client__mutmut_198`` (Meldung -> ``None``),
        ``_199``/``_201`` (``extra`` entfaellt), ``_202``/``_203`` (XX-Marker/
        GROSS), ``_204``..``_212``/``_217``/``_218`` (Schluessel-/Wert-
        Varianten von ``source_id``/``target_id``/``user_id``).

        Pendant zu ``test_client_merged_log_has_exact_message_and_
        structured_fields`` (``test_client_merge_workflow.py``, dort nur
        fuer die Merge-Richtung gepinnt). Original: exakt EIN
        ``INFO``-Satz ``"client_unmerged"`` mit den drei strukturierten
        Feldern.
        """
        merge_clients(source, target, lead_user)
        source.refresh_from_db()

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            unmerge_client(source, lead_user)

        records = [record for record in caplog.records if record.name == LOGGER_NAME and record.levelno == logging.INFO]
        assert [
            (
                record.getMessage(),
                getattr(record, "source_id", None),
                getattr(record, "target_id", None),
                getattr(record, "user_id", None),
            )
            for record in records
            if record.getMessage() == "client_unmerged"
        ] == [("client_unmerged", str(source.pk), str(target.pk), lead_user.pk)]
