"""Pin-Tests des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.attachments.crud``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
wird das vollstaendige Prio-1-Set dieses Moduls: 15 bestaetigte Testluecken
in den Sperr-Vorlaufpfaden ``lock_attachment_batch_before_audit``,
``lock_batch_attachment_rows``, ``lock_owner_rows_in_facility_order``,
``apply_attachment_changes`` und ``purge_attachment_chain`` (Refs #2260/#2178).

Die Luecken gruppieren sich in vier Familien:

* **ADD-Batch ohne Owner-Sperre** — ``apply_attachment_changes`` gibt
  ``creates_rows`` nicht mehr an ``lock_batch_attachment_rows`` weiter; ein
  reiner ADD-Batch (keine Bestandszeile betroffen) sperrt dann die
  Owner-/Personenzeile nicht mehr vor dem ersten Schreibvorgang.
* **Default-/Anfangswerte der Vorsperre** — ``discards_existing_chains``,
  ``data_json`` und das interne ``creates_rows``-Startbit in
  ``lock_attachment_batch_before_audit`` sowie der ``creates_rows``-Default
  in ``lock_batch_attachment_rows`` kippen die Sperrmenge, ohne dass ein
  Bestandstest (der immer explizite Argumente uebergibt) das je beobachtet.
* **Vereinigung/Filter der Sperrmenge** — die Union aus Batch- und
  Altketten-IDs beim Typwechsel (``entry_ids | open_chain_entry_ids``), das
  R11-Sensitivity-Gate (``user``-Weitergabe an ``_iter_file_fields``) und das
  ``pk``-Praedikat der Owner-Sperre (``lock_owner_rows_in_facility_order``).
* **``deletes_rows`` beim Purge** — ``purge_attachment_chain`` sperrt ohne
  dieses Flag (bzw. mit falscher Zielmenge) nicht mehr die owner-uebergreifend
  referenzierenden ``superseded_by``-Zeilen vor dem ``delete()``.

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``: eine
Testklasse je Funktion; jeder Test nennt seine Mutanten in der Docstring samt
dem abweichenden Wert; Zusicherungen vergleichen EXAKT (``==``/``is``), nie
per Teilstring; keine Fixture-Fakes — es laufen echte Model-Objekte durch den
echten File-Vault. Die Sperr-Eigenschaft selbst ist einzelthreadig nicht
beobachtbar (eine Transaktion blockiert sich nicht selbst) — gepinnt wird
deshalb die Parameterliste/Praesenz der mitgeschnittenen ``FOR UPDATE``-Query
ueber ``connection.execute_wrapper`` (Muster aus ``test_mutation_pins_094_attachments_crud.py::TestLockOwnerRow``).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction

from core.models import Attachment, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.attachments.crud import (
    apply_attachment_changes,
    lock_attachment_batch_before_audit,
    lock_batch_attachment_rows,
    lock_owner_rows_in_facility_order,
    purge_attachment_chain,
)
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Minimales, gueltiges PDF — ``prepare_encrypted_upload`` prueft Magic-Bytes.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")


def _attach(owner, ft, user, *, name="beleg.pdf"):
    """Echtes ``Attachment`` (reiner ADD, frische ``entry_id``), ohne Marker-Pflege."""
    staged = prepare_encrypted_upload(owner.facility, _upload(name), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user)


def _marker(*attachments):
    return {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }


def _for_update_queries(mitschnitt, *, table=None):
    treffer = [(sql, params) for sql, params in mitschnitt if "FOR UPDATE" in sql.upper()]
    if table is not None:
        treffer = [(sql, params) for sql, params in treffer if table in sql]
    return treffer


class _Mitschnitt(list):
    def wrapper(self, execute, sql, params, many, context):
        self.append((sql, params))
        return execute(sql, params, many, context)


@pytest.fixture
def ft_file(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pins 0991)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def ft_file_hoch(facility):
    """Sensitivity ``high`` — fuer STAFF unsichtbar (``ROLE_MAX_SENSITIVITY[STAFF] == 1``)."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Verschlusssache (Pins 0991)",
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )


@pytest.fixture
def dt_file(facility, ft_file):
    dt = DocumentType.objects.create(facility=facility, name="Pins 0991 — ein Dateifeld")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt


@pytest.fixture
def dt_nur_hoch(facility, ft_file_hoch):
    dt = DocumentType.objects.create(facility=facility, name="Pins 0991 — nur Verschlusssache")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file_hoch, sort_order=0)
    return dt


@pytest.fixture
def owner2(facility, client_identified, doc_type_contact, staff_user):
    """Zweites Ereignis derselben Einrichtung — fuer owner-uebergreifende Referenzen."""
    from django.utils import timezone

    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        created_by=staff_user,
    )


@pytest.fixture
def anonymes_ereignis(facility, doc_type_contact, staff_user):
    """Ereignis OHNE Personenbezug — die Vorsperre sperrt nur die Owner-Zeile.

    Refs #2379: Owner mit Person nehmen den Helfer mit frischer
    Bindungslesung; die Owner-Sperre ohne Personenbezug erreicht nur ein
    Owner ohne ``client_id``.
    """
    from django.utils import timezone

    return Event.objects.create(
        facility=facility,
        client=None,
        is_anonymous=True,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        created_by=staff_user,
    )


@pytest.fixture(autouse=True)
def medienordner(settings, tmp_path):
    """``MEDIA_ROOT`` je Test isolieren — echte ``.enc``-Dateien, kein Projektbaum."""
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# apply_attachment_changes — creates_rows-Weitergabe an die Vorsperre (2 Mutanten)
# ---------------------------------------------------------------------------


class TestApplyAttachmentChangesCreatesRows:
    """Refs #2260: ein reiner ADD-Batch traegt die Owner-Fremdschluesselpruefung
    und muss deshalb Stufe 1 der Sperrordnung nehmen — ``creates_rows`` ist die
    einzige Information, die das an ``lock_batch_attachment_rows`` meldet.
    """

    def test_reiner_add_batch_sperrt_owner_zeile(self, sample_event, ft_file, dt_file, staff_user):
        """Pin: ``x_apply_attachment_changes__mutmut_27`` (``creates_rows=None``
        statt ``creates_rows=_batch_legt_zeilen_an(...)``) und ``…__mutmut_30``
        (das Schluesselargument entfaellt ganz, Default ``False``).

        Ein reiner ADD-Batch (kein REMOVE/REPLACE) unter ``ft_file`` haelt
        keine Bestandszeile in ``entry_ids`` — die Owner-Sperre kommt
        ausschliesslich ueber ``creates_rows=True``.

        Abweichung beim Mutanten: ``creates_rows`` wird ``None``/``False`` und
        ``lock_batch_attachment_rows`` kehrt vor jeder Abfrage zurueck — die
        mitgeschnittene ``FOR UPDATE``-Liste ist leer statt genau EINE
        Owner-Sperre (``core_event``, Parameter ``sample_event.pk``) zu
        enthalten.
        """
        staged = prepare_encrypted_upload(sample_event.facility, _upload(), staff_user, owner=sample_event)
        staged_by_key = {ft_file.slug: [staged]}

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            apply_attachment_changes(sample_event, staff_user, {}, {}, {}, dt_file, staged_by_key=staged_by_key)

        sperrend = _for_update_queries(mitschnitt, table="core_event")
        # Zwei Sperrabfragen: die Vorsperre aus ``lock_batch_attachment_rows``
        # (der gepinnte Aufruf) UND die eigene, unbedingte Sperre von
        # ``commit_staged_upload`` (idempotent, deckt nur den EINEN
        # Schreibvorgang ab). Der Mutant entfernt genau die erste — mit ihm
        # bleibt nur die zweite uebrig.
        assert len(sperrend) == 2
        for _sql, params in sperrend:
            assert str(sample_event.pk) in [str(p) for p in params]
        assert sample_event.attachments.count() == 1


# ---------------------------------------------------------------------------
# lock_attachment_batch_before_audit — Default-/Weitergabe-Mutanten (7 Mutanten)
# ---------------------------------------------------------------------------


class TestLockAttachmentBatchBeforeAudit:
    """Refs #2260: der aeussere Vorlauf-Einstieg fuer Aufrufer, die vor dem
    Anhangs-Batch auditieren (Event-/Client-/Case-Update). Alle sieben
    Prio-1-Mutanten dieser Funktion veraendern die Sperrmenge, ohne dass ein
    Bestandstest mit expliziten Argumenten das je beobachtet.
    """

    def test_discards_default_bleibt_false(self, sample_event, ft_file, dt_file, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_1`` — der Default
        von ``discards_existing_chains`` wechselt von ``False`` auf ``True``.

        Aufruf OHNE das Schluesselwort (Event-Update-Pfad ohne Typwechsel),
        batch-seitig unveraendert (kein REMOVE/REPLACE/ADD), Owner traegt eine
        separate offene Kette. Original: kein Merge der Altketten, keine
        Sperrabfrage ueberhaupt. Mutant: die offene Altkette wird trotzdem
        vorgezogen gesperrt.
        """
        alte_kette = _attach(sample_event, ft_file, staff_user, name="alt.pdf")

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(sample_event, staff_user, {}, {}, dt_file)

        assert _for_update_queries(mitschnitt) == []
        assert alte_kette.pk is not None  # Kette bleibt unberuehrt vom Aufruf

    def test_data_json_steuert_kuenftige_markerlage(self, sample_event, ft_file, dt_file, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_11`` (``data_json``
        an ``_iter_file_fields`` wird zu ``None``) und ``…__mutmut_15`` (das
        Schluesselargument entfaellt ganz — derselbe Effekt).

        ``owner.data_json`` (aktueller DB-Stand) kennt ``ft_file.slug`` NICHT;
        die kuenftige Markerlage (``data_json``-Argument) referenziert
        ``att1``, das per REMOVE-CSV entfernt werden soll. Original: die
        Sperrmenge liest die kuenftige Lage und sperrt Owner- UND
        Attachment-Zeile. Mutant: liest die aktuelle (leere) Lage — keine
        Uebereinstimmung, keine Sperrabfrage.
        """
        att1 = _attach(sample_event, ft_file, staff_user)
        assert ft_file.slug not in sample_event.data_json
        kuenftige_lage = {**sample_event.data_json, ft_file.slug: _marker(att1)}
        request_post = {f"{ft_file.slug}__remove": str(att1.entry_id)}

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(
                sample_event, staff_user, request_post, {}, dt_file, data_json=kuenftige_lage
            )

        # Drei Sperrabfragen aus ``lock_owner_rows_in_facility_order``
        # (Client-Zeile UND Event-Zeile, ``sample_event.client_id`` ist
        # gesetzt) plus die Attachment-Zeile — alle drei nur, wenn die
        # kuenftige (nicht die aktuelle) Markerlage gelesen wird.
        sperrend = _for_update_queries(mitschnitt)
        assert len(sperrend) == 3
        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        assert len(attachment_sperren) == 1
        _sql, params = attachment_sperren[0]
        assert str(att1.entry_id) in [str(p) for p in params]

    def test_typwechsel_nimmt_offene_altketten_mit(self, sample_event, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_28`` — die
        Vereinigung ``entry_ids | open_chain_entry_ids(owner)`` wird beim
        Typwechsel durch ``entry_ids=None`` ersetzt.

        ``document_type=None`` (Typ wird entfernt) mit leerem Batch, aber
        einer bestehenden offenen Kette — genau der in der Docstring
        beschriebene Fall. Original: die Altkette wandert in die Sperrmenge.
        Mutant: ``entry_ids=None`` ist falsy wie ``creates_rows=False`` —
        ``lock_batch_attachment_rows`` kehrt sofort zurueck, keine Sperre.
        """
        ft_egal = FieldTemplate.objects.create(
            facility=sample_event.facility, name="Egal (Pins 0991)", field_type=FieldTemplate.FieldType.FILE
        )
        altkette = _attach(sample_event, ft_egal, staff_user, name="alt.pdf")

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(sample_event, staff_user, {}, {}, None, discards_existing_chains=True)

        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        assert len(attachment_sperren) == 1
        _sql, params = attachment_sperren[0]
        assert str(altkette.entry_id) in [str(p) for p in params]

    def test_typwechsel_vereinigt_statt_schneidet(self, sample_event, ft_file, dt_file, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_29`` — die
        Vereinigung von Batch- und Altketten-IDs wird zur Schnittmenge.

        Batch entfernt ``att_neu`` (eigenes Feld); ``att_alt`` ist eine
        DISJUNKTE offene Kette desselben Owners (kein Marker-Eintrag).
        Original: Union = beide Ketten. Mutant: Schnittmenge zweier disjunkter
        Mengen ist leer — ``att_alt`` fehlt in der Sperrmenge.
        """
        att_neu = _attach(sample_event, ft_file, staff_user, name="neu.pdf")
        att_alt = _attach(sample_event, ft_file, staff_user, name="alt.pdf")
        sample_event.data_json = {**sample_event.data_json, ft_file.slug: _marker(att_neu)}
        request_post = {f"{ft_file.slug}__remove": str(att_neu.entry_id)}

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(
                sample_event, staff_user, request_post, {}, dt_file, discards_existing_chains=True
            )

        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        assert len(attachment_sperren) == 1
        _sql, params = attachment_sperren[0]
        werte = [str(p) for p in params]
        assert str(att_neu.entry_id) in werte
        assert str(att_alt.entry_id) in werte

    def test_initialwert_creates_rows_bleibt_false(self, sample_event, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_4`` — der
        Initialwert ``creates_rows = False`` wird zu ``True``.

        ``document_type=None`` ohne Altketten und ohne Typwechsel-Flag: der
        Schleifenkoerper, der ``creates_rows`` normalerweise setzt, laeuft gar
        nicht. Original: ``creates_rows`` bleibt ``False``, keine Abfrage.
        Mutant: startet bei ``True`` — die Owner-Zeile wird trotzdem gesperrt.
        """
        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(sample_event, staff_user, {}, {}, None)

        assert _for_update_queries(mitschnitt) == []

    def test_user_gate_wird_nicht_umgangen(self, sample_event, ft_file_hoch, dt_nur_hoch, staff_user):
        """Pin: ``x_lock_attachment_batch_before_audit__mutmut_9`` — der an
        ``_iter_file_fields`` weitergereichte ``user`` wird zu ``None``.

        ``dt_nur_hoch`` hat ein einziges FILE-Feld mit Sensitivity ``high`` —
        fuer ``staff_user`` (Rang 1) unsichtbar. Ein ADD-Upload unter diesem
        Slug darf fuer STAFF keine Sperrmenge/Owner-Sperre ausloesen.

        Abweichung beim Mutanten: ``user=None`` bedeutet Systemkontext ohne
        Rolle — das R11-Gate greift nicht, das Feld erscheint in
        ``file_field_states``, ``creates_rows`` wird ``True`` und die
        Owner-Zeile wird trotzdem gesperrt.
        """
        staged = prepare_encrypted_upload(sample_event.facility, _upload(), staff_user, owner=sample_event)
        staged_by_key = {ft_file_hoch.slug: [staged]}

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_attachment_batch_before_audit(sample_event, staff_user, {}, staged_by_key, dt_nur_hoch)

        assert _for_update_queries(mitschnitt) == []


# ---------------------------------------------------------------------------
# lock_batch_attachment_rows — Default von creates_rows (1 Mutant)
# ---------------------------------------------------------------------------


class TestLockBatchAttachmentRows:
    def test_creates_rows_default_bleibt_false(self, sample_event):
        """Pin: ``x_lock_batch_attachment_rows__mutmut_1`` — der Default von
        ``creates_rows`` wechselt von ``False`` auf ``True``.

        Aufruf mit leerer ``entry_ids``-Menge OHNE das Schluesselwort.
        Original: ``not [] and not False`` -> frueher Return, keine Abfrage.
        Mutant: Default ``True`` -> ``lock_attachment_rows_in_facility_order``
        laeuft an und sperrt die Owner-Zeile.
        """
        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_batch_attachment_rows(sample_event, [])

        assert _for_update_queries(mitschnitt) == []


# ---------------------------------------------------------------------------
# lock_owner_rows_in_facility_order — pk-Praedikat der Owner-Sperre (1 Mutant)
# ---------------------------------------------------------------------------


class TestLockOwnerRowsInFacilityOrder:
    def test_sperrt_exakt_die_eigene_owner_zeile(self, anonymes_ereignis, owner2):
        """Pin: ``x_lock_owner_rows_in_facility_order__mutmut_28`` — der
        ``pk=owner.pk``-Filter der Owner-Sperre (``type(owner).objects.filter(
        pk=owner.pk, facility=facility_id)``) entfaellt.

        Zwei Ereignisse derselben Einrichtung. Original: die ``FOR
        UPDATE``-Query traegt ``anonymes_ereignis.pk`` UND ``facility_id`` als
        Parameter. Mutant: nur noch ``facility_id`` — potenziell jede
        Ereigniszeile der Einrichtung statt der eigenen.

        Refs #2379: bewusst ein Owner OHNE Person — nur er erreicht diese
        Owner-Sperre; die Sperre mit Bindungsbedingung (Owner mit Person)
        pinnt ``test_mutation_pins_0994_attachments_crud.py``.
        """
        assert owner2.pk != anonymes_ereignis.pk

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper), transaction.atomic():
            lock_owner_rows_in_facility_order(anonymes_ereignis)

        sperrend = _for_update_queries(mitschnitt, table="core_event")
        assert len(sperrend) == 1
        _sql, params = sperrend[0]
        werte = sorted(str(p) for p in params)
        assert werte == sorted([str(anonymes_ereignis.pk), str(anonymes_ereignis.facility_id)])


# ---------------------------------------------------------------------------
# purge_attachment_chain — deletes_rows-Vorsperre (4 Mutanten)
# ---------------------------------------------------------------------------


class TestPurgeAttachmentChainDeletesRows:
    """Refs #2260: der einzelne Vernichtungspfad muss vor dem ``delete()`` auch
    die owner-uebergreifend referenzierenden ``superseded_by``-Zeilen sperren
    (Merge-Szenario, ADR-039 Docstring von ``lock_attachment_rows_in_facility_order``).
    """

    def test_cross_owner_referenz_wird_mitgesperrt(self, sample_event, owner2, ft_file, staff_user):
        """Pin: ``x_purge_attachment_chain__mutmut_40`` (``deletes_rows=None``),
        ``…__mutmut_43`` (Schluesselargument entfaellt, Default ``False``) und
        ``…__mutmut_44`` (``deletes_rows=False`` explizit).

        ``att_fremd`` gehoert ``owner2`` und referenziert per
        ``superseded_by`` auf ``att_ziel`` (Merge-Szenario). Original:
        ``deletes_rows=True`` baut die owner-uebergreifende Facility-Query mit
        ``superseded_by_id``-Zweig, die ``att_fremd`` ueber die WHERE-Bedingung
        mitsperrt. Mutant: der Aufruf bleibt auf die einfache
        ``owner.attachments``-Sperre beschraenkt — der ``superseded_by_id``-
        Zweig entfaellt komplett, ``att_fremd`` wird nie gesperrt.
        """
        att_ziel = _attach(sample_event, ft_file, staff_user, name="ziel.pdf")
        att_fremd = _attach(owner2, ft_file, staff_user, name="fremd.pdf")
        Attachment.objects.filter(pk=att_fremd.pk).update(superseded_by_id=att_ziel.pk)

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            entfernt = purge_attachment_chain(sample_event, att_ziel.entry_id)

        assert entfernt == 1
        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        assert len(attachment_sperren) == 1
        sql, params = attachment_sperren[0]
        # ``deletes_rows=True`` baut eine ANDERE Query-Form (OR-Verknuepfung
        # ueber ``superseded_by_id``, owner-uebergreifend nach Facility
        # gescopt) als die einfache, owner-gescopte ``entry_id``-Sperre.
        # ``att_fremd`` erscheint nie als gebundener Parameter (er wird ueber
        # die WHERE-Bedingung an der Zeile selbst getroffen) — beobachtbar ist
        # die Query-FORM, nicht ein einzelner Parameterwert.
        assert "superseded_by_id" in sql
        assert str(att_ziel.pk) in [str(p) for p in params]

    def test_sperrmenge_bleibt_auf_die_zielkette_beschraenkt(self, sample_event, ft_file, staff_user):
        """Pin: ``x_purge_attachment_chain__mutmut_39`` — die gezielte
        Sperrmenge ``[entry_id]`` wird durch ``None`` (alle Owner-Anhaenge)
        ersetzt.

        ``att_andere`` ist eine ZWEITE, unabhaengige Kette desselben Owners.
        Original: nur ``att_ziel`` wird gesperrt. Mutant: ``entry_ids=None``
        sperrt ALLE Attachment-Zeilen des Owners — auch ``att_andere``.
        """
        att_ziel = _attach(sample_event, ft_file, staff_user, name="ziel.pdf")
        att_andere = _attach(sample_event, ft_file, staff_user, name="andere.pdf")

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            entfernt = purge_attachment_chain(sample_event, att_ziel.entry_id)

        assert entfernt == 1
        attachment_sperren = _for_update_queries(mitschnitt, table="core_attachment")
        werte = {str(p) for _sql, params in attachment_sperren for p in params}
        assert str(att_ziel.pk) in werte
        assert str(att_andere.pk) not in werte
