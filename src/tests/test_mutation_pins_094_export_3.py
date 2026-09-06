"""Pin-Tests zum Meilenstein release-0.94 — Survivor-Batch ``export_3``.

Quelle: ``src/core/services/client/export.py`` (Auskunfts-Export nach DSGVO
Art. 15/20). Modul-Batch ``export_3`` der Survivor-Triage; Vorgabe ist
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (11 Mutanten, Herkunft
``welle-d``, Mutantenbaum ``mutants/src/core/services/client/export.py``).

Der Batch besteht aus drei Familien — allesamt Mutanten, die den
**Rueckgabewert nicht veraendern** oder ihn nur in Konstellationen
veraendern, die kein Bestandstest herstellt:

* **Die vollstaendige Sortierkette der Anhaenge**
  (:func:`~core.services.client.export._ordered_attachments`). Alle vier
  Kriterien (``field_template__name`` -> ``sort_order`` -> ``created_at`` ->
  ``pk``) waren ungepinnt: die Bestandstests legen je Testfall genau EIN
  Datei-Feld an oder suchen den Anhang per ``next(...)``/``by_name`` statt
  ueber die Listenposition. Jedes Kriterium bekommt hier einen eigenen Test
  mit einem Datensatz, in dem **nur** dieses Kriterium die Reihenfolge
  entscheidet — die uebrigen drei sind gleichgesetzt bzw. laufen der
  erwarteten Reihenfolge entgegen.

* **Die N+1-Klammer ``select_related("created_by")``** derselben Funktion.
  Ergebnis bitgleich, Kosten linear — pinnbar ausschliesslich ueber die
  tatsaechlich abgesetzten Queries. Hausform wie in
  ``test_mutation_pins_094_export_2.py``: eine **Skalierungs**-Zusicherung
  (wenige vs. viele Anhaenge kosten gleich viele Queries), keine feste
  Query-Zahl — die waere ein Implementierungs-Pin.

* **Die Durchreichung des Anhang-Index in die beiden Papierkorb-Zweige** von
  :func:`~core.services.client.export.export_client_data` (Refs #1738, G4).
  ``_gather_events``/``_gather_cases`` tragen fuer ``attachment_index`` den
  Default ``None`` und fallen dann je Datensatz auf ``owner.attachments``
  zurueck: dieselbe Ausgabe, aber eine Anhang-Query pro geloeschtem Ereignis
  bzw. pro geloeschtem Fall. Gepinnt wird deshalb die Zahl der Queries, die
  ueberhaupt auf ``core_attachment`` gehen — sie muss unabhaengig von der
  Zahl der Papierkorb-Datensaetze sein.

* **Der ``export_meta``-Filter in**
  :func:`~core.services.client.export.render_client_export_html`. Das
  Template referenziert ``export_meta`` nirgends, der gerenderte HTML-String
  ist beim Mutanten also byte-gleich; der Unterschied liegt im **Kontext**,
  der die Template-Schicht erreicht. Gepinnt per ``template_rendered``-Signal
  (dieselbe Instrumentierung, aus der ``response.context`` gespeist wird):
  der Schluesselsatz des Kontexts wird gegen den aus ``export_client_data``
  **abgeleiteten** Sollsatz verglichen. Der Sollsatz wird zur Laufzeit
  berechnet, waechst also mit neuen Auskunfts-Kategorien mit.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Fixtures
kommen aus ``src/tests/conftest.py`` bzw. sind echte Model-Objekte (keine
Fakes); KEIN Import aus ``test_rls_functional``. Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
Uebersetzte Texte kommen in diesem Batch nicht vor (verglichen werden
Dateinamen, Query-Zahlen und Kontext-Schluessel), ein
``translation.override`` waere hier also Dekoration ohne Wirkung.

**Zur Determiniertheit des ``pk``-Tiebreaks.** Ein entfernter ``"pk"``-
Tiebreak laesst die Reihenfolge bei Gleichstand aller drei fachlichen
Kriterien offen. Der Test zwingt sie trotzdem auseinander (Muster wie
``test_mutation_pins_094_export_1/2.py``): die Zeilen bekommen **explizite**
UUID-PKs, die der Einfuegereihenfolge entgegenlaufen, und der gemeinsame
``created_at``-Wert wird in EINER ``UPDATE``-Anweisung gesetzt, damit die
physische Reihenfolge im Heap die Einfuegereihenfolge bleibt. Postgres
sortiert vier Tupel per (stabilem) Insertion Sort auf dem Seq-Scan-Ergebnis
und liefert bei Gleichstand damit die Einfuegereihenfolge. Original:
PK-aufsteigend. Mutant: Einfuegereihenfolge (= PK-absteigend).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime

import pytest
from django.db import connection
from django.test.signals import template_rendered
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Attachment, Event, FieldTemplate
from core.models import Case as CaseModel
from core.services.client.export import (
    _gather_attachments,
    export_client_data,
    render_client_export_html,
)


def _pk(n: int) -> uuid.UUID:
    """Deterministische UUID mit **bekannter** Sortierlage.

    ``Attachment`` traegt ``UUIDField(primary_key=True, default=uuid.uuid4)``.
    Zufaellige PKs machen jede Aussage ueber „nach ``pk`` sortiert" unpruefbar;
    ``id`` ist aber schreibbar. ``_pk(1) < _pk(2) < …`` gilt sowohl fuer den
    ``uuid``-Typ von Postgres (bytewise) als auch fuer den String-Vergleich
    sonstiger Backends.
    """
    return uuid.UUID(int=n)


def _file_template(facility, name: str) -> FieldTemplate:
    """Datei-Feldvorlage; ``name`` ist zugleich das primaere Sortierkriterium."""
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
    )


def _attach(facility, owner, field_template, user, *, pk: int, filename: str, sort_order: int = 0) -> Attachment:
    """Echte Anhang-Zeile an ``owner`` (kein Fake, keine Datei auf Platte).

    ``original_filename_encrypted`` bleibt Klartext: ``safe_decrypt`` reicht
    alles ohne ``__encrypted__``-Marker unveraendert durch, der Dateiname ist
    damit die Sonde fuer die Reihenfolge.
    """
    owner_field = owner._meta.model_name
    return Attachment.objects.create(
        id=_pk(pk),
        facility=facility,
        field_template=field_template,
        storage_filename=f"vault-{pk}.bin",
        original_filename_encrypted=filename,
        file_size=1000 + pk,
        mime_type="application/pdf",
        sort_order=sort_order,
        created_by=user,
        **{owner_field: owner},
    )


def _set_created_at(attachments, moment) -> None:
    """``created_at`` ist ``auto_now_add`` — nachtraeglich gesetzt, in EINER Anweisung.

    Eine einzige ``UPDATE``-Anweisung ueber die Gruppe schreibt die neuen
    Tupel-Versionen in Scan-Reihenfolge zurueck; die physische Reihenfolge im
    Heap bleibt damit die Einfuegereihenfolge (Grundlage des Tiebreak-Pins).
    """
    Attachment.objects.filter(pk__in=[attachment.pk for attachment in attachments]).update(created_at=moment)


def _count_queries(call) -> int:
    """Query-Zahl eines Aufrufs — Skalierungs-Pins gegen N+1."""
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len(ctx.captured_queries)


def _count_attachment_queries(call) -> int:
    """Nur die Queries, die auf ``core_attachment`` gehen.

    Der Auskunfts-Export beruehrt zwei Dutzend Tabellen; eine Gesamtzahl waere
    ein Implementierungs-Pin auf das ganze Modul. Gezaehlt wird deshalb
    ausschliesslich die Anhang-Schicht — genau die, die der Index buendelt.
    """
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len([query for query in ctx.captured_queries if "core_attachment" in query["sql"]])


@contextmanager
def _captured_template_contexts():
    """Kontexte, die die Template-Schicht tatsaechlich erreichen.

    ``django.test.signals.template_rendered`` ist dieselbe Instrumentierung,
    aus der ``response.context`` gespeist wird (aktiv ueber
    ``setup_test_environment``). Damit ist der Kontext eines
    ``render_to_string``-Aufrufs pruefbar, ohne irgendetwas zu patchen.
    """
    captured = []

    def _receiver(sender, template, context, **kwargs):
        captured.append(context)

    template_rendered.connect(_receiver)
    try:
        yield captured
    finally:
        template_rendered.disconnect(_receiver)


# ============================================================================
# _ordered_attachments — die vollstaendige Sortierkette (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestOrderedAttachmentsOrdering:
    """Vier Kriterien, vier Tests — jeder isoliert genau eines.

    Der Docstring von ``_ordered_attachments`` macht die Reihenfolge zur
    Zusicherung: Einzel-Abfrage und Sammel-Index muessen dieselbe Folge
    liefern, „sonst haengt das Auskunftsdokument vom Eintrittspunkt ab".
    Geprueft wird ueber ``_gather_attachments`` (indexloser Weg), weil dort
    die Reihenfolge in der Auskunft ankommt.
    """

    def test_field_template_name_is_the_primary_criterion(self, facility, client_identified, staff_user):
        """Pin: ``x__ordered_attachments__mutmut_5`` — ``"field_template__name"`` aus
        ``order_by`` entfernt.

        Datensatz: der Anhang des ALPHABETISCH SPAETEREN Feldes („Z-Nachweis")
        wird zuerst hochgeladen, beide mit ``sort_order=0``.
        Original: nach Feldname gruppiert -> ``["a-feld.pdf", "z-feld.pdf"]``.
        Mutant: erstes Kriterium ist ``sort_order`` (gleich), es entscheidet
        ``created_at`` -> ``["z-feld.pdf", "a-feld.pdf"]``.
        """
        ft_late = _file_template(facility, "Z-Nachweis")
        ft_early = _file_template(facility, "A-Nachweis")
        first = _attach(facility, client_identified, ft_late, staff_user, pk=1, filename="z-feld.pdf")
        second = _attach(facility, client_identified, ft_early, staff_user, pk=2, filename="a-feld.pdf")
        _set_created_at([first], datetime.fromisoformat("2026-01-01T08:00:00+00:00"))
        _set_created_at([second], datetime.fromisoformat("2026-01-02T08:00:00+00:00"))

        filenames = [entry["filename"] for entry in _gather_attachments(client_identified)]

        assert filenames == ["a-feld.pdf", "z-feld.pdf"]

    def test_sort_order_is_the_second_criterion(self, facility, client_identified, staff_user):
        """Pin: ``x__ordered_attachments__mutmut_6`` — ``"sort_order"`` aus ``order_by``
        entfernt.

        Datensatz: EIN Datei-Feld, zwei Anhaenge; der mit ``sort_order=1``
        wird zuerst hochgeladen.
        Original: manuelle Reihenfolge zaehlt -> ``["platz-0.pdf", "platz-1.pdf"]``.
        Mutant: ``sort_order`` faellt weg, es entscheidet ``created_at`` ->
        ``["platz-1.pdf", "platz-0.pdf"]``.
        """
        field_template = _file_template(facility, "Mehrfach-Nachweis")
        later_slot = _attach(
            facility, client_identified, field_template, staff_user, pk=1, filename="platz-1.pdf", sort_order=1
        )
        first_slot = _attach(
            facility, client_identified, field_template, staff_user, pk=2, filename="platz-0.pdf", sort_order=0
        )
        _set_created_at([later_slot], datetime.fromisoformat("2026-01-01T08:00:00+00:00"))
        _set_created_at([first_slot], datetime.fromisoformat("2026-01-02T08:00:00+00:00"))

        filenames = [entry["filename"] for entry in _gather_attachments(client_identified)]

        assert filenames == ["platz-0.pdf", "platz-1.pdf"]

    def test_created_at_orders_the_version_chain(self, facility, client_identified, staff_user):
        """Pin: ``x__ordered_attachments__mutmut_7`` — ``"created_at"`` aus ``order_by``
        entfernt.

        Datensatz: EIN Datei-Feld, gleicher ``sort_order`` — die Versionskette
        (V1 ersetzt durch V2). Die PKs laufen der Chronologie **entgegen**
        (V1 = ``_pk(9)``, V2 = ``_pk(1)``).
        Original: chronologisch -> ``["v1.pdf", "v2.pdf"]``.
        Mutant: nach ``created_at`` folgt direkt ``pk`` -> ``["v2.pdf", "v1.pdf"]``.
        """
        field_template = _file_template(facility, "Versionierter Nachweis")
        older = _attach(facility, client_identified, field_template, staff_user, pk=9, filename="v1.pdf")
        newer = _attach(facility, client_identified, field_template, staff_user, pk=1, filename="v2.pdf")
        _set_created_at([older], datetime.fromisoformat("2026-01-01T08:00:00+00:00"))
        _set_created_at([newer], datetime.fromisoformat("2026-01-02T08:00:00+00:00"))

        filenames = [entry["filename"] for entry in _gather_attachments(client_identified)]

        assert filenames == ["v1.pdf", "v2.pdf"]

    def test_pk_is_the_stable_tiebreak(self, facility, client_identified, staff_user):
        """Pin: ``x__ordered_attachments__mutmut_8`` — ``"pk"`` aus ``order_by`` entfernt
        (Kette endet nach ``created_at``).

        Datensatz: vier Anhaenge an EINEM Datei-Feld, identischem
        ``sort_order`` UND identischem ``created_at`` (in einer einzigen
        ``UPDATE``-Anweisung gesetzt). Eingefuegt werden sie in absteigender
        PK-Folge.
        Original: ``["p1.pdf", "p2.pdf", "p3.pdf", "p4.pdf"]`` (PK-aufsteigend).
        Mutant: keine Sortierklausel mehr wirksam -> Heap-/Einfuegereihenfolge
        ``["p4.pdf", "p3.pdf", "p2.pdf", "p1.pdf"]``.
        """
        field_template = _file_template(facility, "Gleichstand-Nachweis")
        rows = [
            _attach(facility, client_identified, field_template, staff_user, pk=index, filename=f"p{index}.pdf")
            for index in (4, 3, 2, 1)
        ]
        _set_created_at(rows, datetime.fromisoformat("2026-01-01T08:00:00+00:00"))

        filenames = [entry["filename"] for entry in _gather_attachments(client_identified)]

        assert filenames == ["p1.pdf", "p2.pdf", "p3.pdf", "p4.pdf"]


# ============================================================================
# _ordered_attachments — der ``created_by``-Join (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestOrderedAttachmentsCreatedByJoin:
    """Der Uploader wird mitgeladen, nicht je Anhang nachgeladen.

    ``_gather_attachments`` liest aus jeder Zeile ``_username(created_by)``.
    Ohne ``select_related("created_by")`` ist die Ausgabe bitgleich, kostet
    aber eine Query je Anhang — und zwar auch dann, wenn alle Anhaenge
    denselben Uploader haben: Django cached FK-Instanzen nicht ueber
    Modell-Instanzen hinweg (G4-Befund, Refs #1738).
    """

    def test_uploader_is_joined_not_reloaded_per_attachment(self, facility, client_identified, staff_user):
        """Pin: ``x__ordered_attachments__mutmut_12`` — ``select_related("field_template",
        "created_by")`` -> ``select_related("field_template")``.

        Original: die Query-Zahl von ``_gather_attachments`` ist unabhaengig
        von der Zahl der Anhaenge (ein SELECT mit Joins) -> beide Messungen
        gleich.
        Mutant: ``attachment.created_by`` laedt je Zeile nach — bei einem
        Anhang eine Query mehr, bei sechs Anhaengen sechs (gemessen: 2 vs. 7).
        Der Uploader ist in allen Zeilen derselbe Nutzer; die Zusicherung
        haengt also nicht an unterschiedlichen Nutzern, sondern am Join.
        """
        field_template = _file_template(facility, "Nachweis")
        _attach(facility, client_identified, field_template, staff_user, pk=1, filename="anhang-1.pdf")
        # Aufwaermen: der erste Aufruf traegt einmalige Nebenkosten.
        _gather_attachments(client_identified)
        with_one = _count_queries(lambda: _gather_attachments(client_identified))

        for index in range(2, 7):
            _attach(
                facility,
                client_identified,
                field_template,
                staff_user,
                pk=index,
                filename=f"anhang-{index}.pdf",
            )
        with_six = _count_queries(lambda: _gather_attachments(client_identified))

        entries = _gather_attachments(client_identified)
        assert [entry["uploaded_by"] for entry in entries] == [staff_user.username] * 6
        assert with_six == with_one


# ============================================================================
# export_client_data — der Anhang-Index in den Papierkorb-Zweigen (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestExportClientDataPassesAttachmentIndexToTrashBranches:
    """Der gebuendelte Anhang-Index gilt auch fuer Papierkorb-Ereignisse und -Faelle.

    Refs #1738 (G4): ``_build_attachment_index`` holt ALLE Anhaenge der Person
    in drei Queries. Faellt einer der vier Serialisierungs-Pfade auf den
    indexlosen Weg zurueck, ist die Ausgabe unveraendert — die Auskunft
    skaliert aber wieder linear mit der Zahl der Datensaetze. Gezaehlt wird
    deshalb die Zahl der Queries auf ``core_attachment``; sie darf von der
    Zahl der Papierkorb-Datensaetze nicht abhaengen.
    """

    @staticmethod
    def _trashed_event(facility, client, doc_type, user) -> Event:
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=user,
            is_deleted=True,
        )

    @staticmethod
    def _trashed_case(facility, client, user, *, index: int) -> CaseModel:
        return CaseModel.objects.create(
            facility=facility,
            client=client,
            title=f"Papierkorb-Fall {index}",
            status=CaseModel.Status.OPEN,
            created_by=user,
            is_deleted=True,
            deleted_at=timezone.now(),
        )

    def test_deleted_events_use_the_bundled_index(
        self, facility, client_identified, doc_type_contact, staff_user, admin_user
    ):
        """Pin: ``x_export_client_data__mutmut_16`` (``attachment_index`` -> ``None``) und
        ``x_export_client_data__mutmut_20`` (drittes Positionsargument ganz entfernt,
        Default ``None``) — beide im Aufruf
        ``_gather_events(client, user, attachment_index, is_deleted=True)``.

        Original: die Zahl der ``core_attachment``-Queries bleibt bei einem und
        bei fuenf Papierkorb-Ereignissen gleich (die drei Index-Queries).
        Mutant: je geloeschtem Ereignis kommt eine eigene
        ``event.attachments``-Query dazu — gemessen 4 vs. 8. Die AUSGABE ist
        bei beiden identisch, deshalb wird sie hier zusaetzlich mitgeprueft:
        der Pin darf nicht auf einem Ausgabeunterschied beruhen, den es nicht
        gibt.
        """
        field_template = _file_template(facility, "Ereignis-Nachweis")
        first_event = self._trashed_event(facility, client_identified, doc_type_contact, staff_user)
        _attach(facility, first_event, field_template, staff_user, pk=1, filename="ereignis-1.pdf")
        export_client_data(client_identified, facility, admin_user)
        with_one = _count_attachment_queries(lambda: export_client_data(client_identified, facility, admin_user))

        for index in range(2, 6):
            event = self._trashed_event(facility, client_identified, doc_type_contact, staff_user)
            _attach(facility, event, field_template, staff_user, pk=index, filename=f"ereignis-{index}.pdf")
        with_five = _count_attachment_queries(lambda: export_client_data(client_identified, facility, admin_user))

        data = export_client_data(client_identified, facility, admin_user)
        assert len(data["deleted_events"]) == 5
        assert [entry["attachments"][0]["filename"] for entry in data["deleted_events"]] == [
            "ereignis-5.pdf",
            "ereignis-4.pdf",
            "ereignis-3.pdf",
            "ereignis-2.pdf",
            "ereignis-1.pdf",
        ]
        assert with_five == with_one

    def test_deleted_cases_use_the_bundled_index(self, facility, client_identified, staff_user, admin_user):
        """Pin: ``x_export_client_data__mutmut_46`` (``attachment_index`` -> ``None``) und
        ``x_export_client_data__mutmut_49`` (zweites Positionsargument ganz entfernt,
        Default ``None``) — beide im Aufruf
        ``_gather_cases(client, attachment_index, is_deleted=True)``.

        Original: die Zahl der ``core_attachment``-Queries bleibt bei einem und
        bei fuenf Papierkorb-Faellen gleich (die drei Index-Queries).
        Mutant: je geloeschtem Fall kommt eine eigene ``case.attachments``-
        Query dazu — gemessen 4 vs. 8. Die Ausgabe ist identisch und wird
        zusaetzlich mitgeprueft.
        """
        field_template = _file_template(facility, "Fall-Nachweis")
        first_case = self._trashed_case(facility, client_identified, staff_user, index=1)
        _attach(facility, first_case, field_template, staff_user, pk=1, filename="fall-1.pdf")
        export_client_data(client_identified, facility, admin_user)
        with_one = _count_attachment_queries(lambda: export_client_data(client_identified, facility, admin_user))

        for index in range(2, 6):
            trashed_case = self._trashed_case(facility, client_identified, staff_user, index=index)
            _attach(facility, trashed_case, field_template, staff_user, pk=index, filename=f"fall-{index}.pdf")
        with_five = _count_attachment_queries(lambda: export_client_data(client_identified, facility, admin_user))

        data = export_client_data(client_identified, facility, admin_user)
        assert len(data["deleted_cases"]) == 5
        assert sorted(entry["attachments"][0]["filename"] for entry in data["deleted_cases"]) == [
            "fall-1.pdf",
            "fall-2.pdf",
            "fall-3.pdf",
            "fall-4.pdf",
            "fall-5.pdf",
        ]
        assert with_five == with_one


# ============================================================================
# render_client_export_html — der ``export_meta``-Filter (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestRenderClientExportHtmlContextKeys:
    """Was die Template-Schicht bekommt — und was bewusst nicht.

    ``render_client_export_html`` leitet den Kontext aus
    ``export_client_data`` ab und nimmt genau EINEN Schluessel heraus:
    ``export_meta``. Dessen Inhalt kommt aufbereitet zurueck
    (``facility_name`` + ``generated_at``), das Roh-Dict bleibt draussen. Weil
    das Template ``export_meta`` nirgends referenziert, ist der gerenderte
    HTML-String beim Mutanten unveraendert — der Unterschied ist
    ausschliesslich im uebergebenen Kontext sichtbar.
    """

    def test_export_meta_is_the_only_key_removed_from_the_context(
        self, facility, client_identified, sample_event, admin_user
    ):
        """Pin: ``x_render_client_export_html__mutmut_10`` (``"export_meta"`` ->
        ``"XXexport_metaXX"``) und ``x_render_client_export_html__mutmut_11``
        (-> ``"EXPORT_META"``).

        Beide Mutanten machen die Bedingung ``key != …`` fuer JEDEN echten
        Schluessel wahr; der Filter greift nicht mehr.
        Original: der Kontext-Schluesselsatz ist
        ``set(export_client_data(...)) - {"export_meta"} | {"facility_name",
        "generated_at"}``.
        Mutant: derselbe Satz PLUS ``"export_meta"`` — der Vergleich der
        Schluesselmengen schlaegt fehl (ein Schluessel zu viel).
        """
        with _captured_template_contexts() as captured:
            html = render_client_export_html(client_identified, facility, admin_user)

        assert html != ""
        assert captured != []
        context = captured[0]
        context_keys = {key for layer in context.dicts for key in layer} - {"True", "False", "None"}
        expected_keys = (set(export_client_data(client_identified, facility, admin_user)) - {"export_meta"}) | {
            "facility_name",
            "generated_at",
        }

        assert context_keys == expected_keys
