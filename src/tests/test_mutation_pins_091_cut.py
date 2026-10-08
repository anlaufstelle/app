"""Pin-Tests aus der Survivor-Triage des v0.91.0-Cut-Deltas (BASE v0.89.0).

Der ``mutation-delta``-Lauf zum Cut v0.91.0 war der ERSTE mit belastbaren
Zahlen (Per-Fork-Test-DBs, Refs #2097): 3098 Delta-Mutanten, 455 Survivors,
16 ohne deckenden Test. Die Triage klassifizierte 471 Eintraege vollstaendig;
jeder Aequivalenz-/Kosmetik-Befund wurde adversarial gegengeprueft. Diese
Datei pinnt das Prio-1-Set des Cuts — drei Bloecke:

* ``core.forms.clients`` — alle killbaren Survivors (Gate: forms >= 85 %);
* ``core.services.system._db_admin`` — RLS-Bypass-Helfer, inkl. des bislang
  fehlenden Negativpfads von ``has_rls_bypass_context`` (Refs #1016);
* ``core.services.compliance.db_roles`` — Pflichtfelder der Compliance-
  Karten, die App-Seite der Admin-Check-Zusicherungen und die erstmalige
  echte Ausfuehrung der ``_query_*``-pg_catalog-Schicht (Refs #902/ADR-020).

Die uebrigen killbaren Survivors (merge/export/main/activity/deletion) sind
bewusst NICHT hier: Follow-up-Ticket der Welle D, Muster wie Refs #2087.
Als aequivalent BESTAETIGTE Mutanten sind in den Triage-Unterlagen der Welle
dokumentiert (Ledger ``welle-d-0.91/pins/equivalents_*.md``), nicht gepinnt.

Konventionen wie ``test_mutation_pins_089/090/091.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten); Sprachkontext wird wo noetig per
``translation.override("de")`` festgenagelt; KEINE Imports aus
``test_rls_functional`` (its fixture ``rls_test_role`` skips itself under
``MUTANT_UNDER_TEST``; a pin requesting it never runs in a mutation run); jeder Test
nennt seine Mutanten in der
Docstring. Der Kill-Nachweis ist der Delta-RERUN des Cuts (Delta-Mutanten
werden immer neu getestet) plus Stichproben von Hand.
"""

from contextlib import contextmanager

import pytest
from django.db import connection
from django.utils import translation

from core.forms.clients import ClientForm, ClientInlineCreateForm
from core.models import Client, DocumentType
from core.services.compliance import ComplianceStatus, db_roles
from core.services.compliance.db_roles import RoleCheck
from core.services.system import _db_admin
from core.services.system._db_admin import current_connection_owns, has_rls_bypass_context
from tests.conftest import xdist_role_suffix

# ============================================================================
# core.forms.clients — Formular-Verteidigungen (18 Mutanten)
# ============================================================================


class TestClientFormDocumentTypeStaysOptionalAndClearable:
    """Der Dokumentationstyp einer Person bleibt abwaehlbar — und heisst so.

    ``ClientForm.__init__`` setzt am ``document_type``-Feld zwei Dinge:
    ``required = False`` (typlose Personen sind ausdruecklich erlaubt) und die
    Beschriftung der Leer-Option ``empty_label``.

    ``ModelChoiceIterator.__iter__`` liefert die Leer-Option NUR, solange
    ``empty_label is not None``. Faellt sie weg, laesst sich ein einmal
    zugewiesener Typ im Bearbeiten-Formular nicht mehr loesen, und die
    Neuanlage-Maske hat den ersten Typ vorausgewaehlt, obwohl ``required``
    typlose Personen zulaesst.

    Der Wortlaut ist keine Log-Zeile, sondern der sichtbare Select-Eintrag und
    zugleich der ``gettext``-Schluessel. Er wird deshalb EXAKT verglichen: ein
    Teilstring-Vergleich liesse die mutmut-Variante ``"XX— Kein Typ —XX"``
    unbemerkt durchgehen, und Klein-/Grossschreibung faellt auch im Handtest
    kaum auf, bricht aber den Katalogeintrag.
    """

    def test_document_type_is_not_required(self, facility):
        """Toetet ``core.forms.clients.xǁClientFormǁ__init____mutmut_13``."""
        form = ClientForm(facility=facility)

        # Identitaets-Check statt ``not ...``: Django wertet ``required`` nur
        # truthy aus, ``None`` verhaelt sich zur Laufzeit exakt wie ``False``
        # und waere ueber das Verhalten nicht zu bemerken. Hauskonvention aus
        # ``src/tests/test_mutation_followup_forms.py`` (EpisodeForm/WorkItemForm).
        assert form.fields["document_type"].required is False

    def test_empty_option_is_offered_with_its_exact_label(self, facility):
        """Toetet ``core.forms.clients.xǁClientFormǁ__init____mutmut_15``,
        ``core.forms.clients.xǁClientFormǁ__init____mutmut_17``,
        ``core.forms.clients.xǁClientFormǁ__init____mutmut_18`` und
        ``core.forms.clients.xǁClientFormǁ__init____mutmut_19``.
        """
        DocumentType.objects.create(
            facility=facility,
            name="Personentyp",
            entity_type=DocumentType.EntityType.CLIENT,
        )

        with translation.override("de"):
            form = ClientForm(facility=facility)
            choices = list(form.fields["document_type"].choices)

            # Ohne ``empty_label`` faellt die Leer-Option ersatzlos weg und
            # an Position 0 steht der erste Dokumentationstyp.
            assert choices, "Auswahl ist unerwartet leer"
            assert choices[0][0] == "", f"Leer-Option fehlt in {choices!r}"
            assert str(choices[0][1]) == "— Kein Typ —"


@pytest.mark.django_db
class TestClientFormKeepsItsConstructorKwargs:
    """Beide ``__init__``-Kwargs landen am Formularobjekt.

    ``facility`` ist bereits gepinnt (``test_facility_kwarg_is_stored_on_form``
    in ``src/tests/test_forms_clients.py``); das Gegenstueck fuer ``user``
    fehlte. Die Sensitivity-Filterung liest den lokalen Parameter, nicht
    ``self.user`` — das Attribut ist heute reine oeffentliche Form-API und nur
    ueber genau diesen Assert beobachtbar.
    """

    def test_user_kwarg_is_stored_on_form(self, facility, staff_user):
        """Toetet ``core.forms.clients.xǁClientFormǁ__init____mutmut_4``."""
        form = ClientForm(facility=facility, user=staff_user)

        assert form.user is staff_user


@pytest.mark.django_db
class TestDuplicatePseudonymMessageIsPinnedWordForWord:
    """Die Duplikat-Meldung wird im Wortlaut festgenagelt.

    Alle vier Bestandsstellen pruefen nur den Teilstring ``existiert
    bereits`` — der ueberlebt sowohl das mutmut-Praefix ``XX…XX`` als auch das
    komplette Kleinschreiben der Meldung, weil das Teilwort selbst ohnehin
    klein geschrieben ist. Die Nutzerin saehe eine offensichtlich kaputte
    Meldung, und der ``gettext``-Katalogeintrag greift nicht mehr.
    """

    def test_duplicate_pseudonym_message_matches_exactly(self, facility, client_identified):
        """Toetet ``core.forms.clients.xǁ_FacilityScopedPseudonymMixinǁclean_pseudonym__mutmut_14``
        und ``core.forms.clients.xǁ_FacilityScopedPseudonymMixinǁclean_pseudonym__mutmut_15``.
        """
        erwartet = "Eine Person mit diesem Pseudonym existiert bereits in dieser Einrichtung."

        with translation.override("de"):
            form = ClientForm(
                data={
                    "pseudonym": client_identified.pseudonym,
                    "contact_stage": Client.ContactStage.IDENTIFIED,
                    "age_cluster": Client.AgeCluster.UNKNOWN,
                    "notes": "",
                },
                facility=facility,
            )

            assert not form.is_valid()
            # EXAKT (==), nie per Teilstring: mutmut erzeugt zu jedem
            # String-Literal die Variante ``"XX<text>XX"``, in der der
            # Originaltext als Teilstring weiterhin enthalten ist.
            assert [str(message) for message in form.errors["pseudonym"]] == [erwartet]


@pytest.mark.django_db
class TestPrimaryKeyAloneDoesNotCountAsEditing:
    """Eine ungespeicherte Instanz mit fremder ``pk`` bleibt eine Anlage.

    ``Client.id`` ist ein ``UUIDField`` mit ``default=uuid.uuid4`` — eine
    frische, ungespeicherte Instanz traegt also bereits eine ``pk``. Genau
    deshalb steht in ``clean_pseudonym`` neben ``self.instance.pk`` das
    ``not self.instance._state.adding`` (die M0-Lehre aus #1056, im Quelltext
    als ACHTUNG-Kommentar vermerkt).

    Zwei Mutanten ersetzen das ``and`` durch ein ``or`` und machen
    ``_state.adding`` damit zu totem Code: die eigene ``pk`` wird IMMER aus dem
    Duplikat-Queryset ausgeschlossen. Trennbar ist das ueber ``instance=`` —
    einen dokumentierten, oeffentlichen ``ModelForm``-Parameter — mit einer
    ganz normal konstruierten ``Client(pk=<vorhandene pk>)``-Instanz. Im
    heutigen Aufrufgraphen entsteht dieser Zustand nicht; der Guard ist
    trotzdem zugesichertes Verhalten und wird hier belegt.
    """

    def test_unsaved_instance_with_existing_pk_still_hits_the_duplicate_check(
        self,
        facility,
        staff_user,
    ):
        """Toetet ``core.forms.clients.xǁ_FacilityScopedPseudonymMixinǁclean_pseudonym__mutmut_7``
        und ``core.forms.clients.xǁ_FacilityScopedPseudonymMixinǁclean_pseudonym__mutmut_8``.
        """
        vorhanden = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-Dup-01",
            created_by=staff_user,
        )
        zwilling = Client(pk=vorhanden.pk, facility=facility)
        # Voraussetzung des Falls: gleiche pk, aber weiterhin im Anlage-Zustand.
        assert zwilling._state.adding is True

        form = ClientForm(
            data={
                "pseudonym": "Pin-Dup-01",
                "contact_stage": Client.ContactStage.IDENTIFIED,
                "age_cluster": Client.AgeCluster.UNKNOWN,
                "notes": "",
            },
            instance=zwilling,
            facility=facility,
        )

        assert not form.is_valid()
        assert "pseudonym" in form.errors

        # Derselbe Mixin traegt die Inline-Maske — zweiter Einstieg, gleicher Guard.
        inline = ClientInlineCreateForm(
            data={
                "pseudonym": "Pin-Dup-01",
                "contact_stage": Client.ContactStage.IDENTIFIED,
            },
            facility=facility,
            min_stage=Client.ContactStage.IDENTIFIED,
            instance=Client(pk=vorhanden.pk, facility=facility),
        )

        assert not inline.is_valid()
        assert "pseudonym" in inline.errors


@pytest.mark.django_db
class TestInlineCreateFormForwardsDjangoFormKwargs:
    """Die Inline-Maske reicht die Django-Standard-Kwargs unveraendert durch.

    ``facility`` und ``min_stage`` sind eigene Keyword-Parameter; alles andere
    (``data``, ``files``, ``initial``, ``instance``, ``prefix``) gehoert an
    ``forms.ModelForm``. Heute uebergibt jede Aufrufstelle ``data``
    positional, deshalb ist ``**kwargs`` an jeder Stelle leer und ein
    verschlucktes ``**kwargs`` faellt nirgends auf — mit ``data=`` waere das
    Formular unbound und ``is_valid()`` dauerhaft False.

    Am selben Pfad haengt ein zweiter Vertrag: eine bereits vorbelegte,
    HOEHERE Kontaktstufe darf die Mindeststufe nicht ueberschrieben bekommen.
    Vier Mutanten ersetzen den Nachschlag des Initialwerts durch etwas, das
    immer ``None`` liefert (``stage_index(None)``, ``get(None)``, verfaelschter
    Key) — der Index ist dann konstant ``-1`` und die Stufe wird BEDINGUNGSLOS
    heruntergesetzt.
    """

    def test_data_and_initial_kwargs_survive_the_constructor(self, facility):
        """Toetet ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_2``,
        ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_38``,
        ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_39``,
        ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_40`` und
        ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_41``.
        """
        gebunden = ClientInlineCreateForm(
            data={
                "pseudonym": "Pin-Kwargs-01",
                "contact_stage": Client.ContactStage.QUALIFIED,
                "min_stage": Client.ContactStage.QUALIFIED,
            },
            facility=facility,
        )

        # Wird ``**kwargs`` verschluckt, kommt ``data`` nie an: unbound.
        assert gebunden.is_bound is True

        vorbelegt = ClientInlineCreateForm(
            facility=facility,
            min_stage=Client.ContactStage.IDENTIFIED,
            initial={"contact_stage": Client.ContactStage.QUALIFIED},
        )

        assert vorbelegt.initial["contact_stage"] == Client.ContactStage.QUALIFIED


@pytest.mark.django_db
class TestInlineCreateFormPrefillsTheLowestStage:
    """Auch die UNTERSTE Kontaktstufe ist ein gueltiger Mindestwert.

    ``stage_index`` liefert fuer die unterste Stufe den Index ``0``; nur ein
    unbekannter Wert ergibt ``-1``. Zwei Mutanten verschieben die Grenze auf
    ``> 0`` bzw. ``>= 1`` und ueberspringen damit den kompletten Block, sobald
    der Dokumentationstyp ``identified`` verlangt. Sichtbare Folge: die
    Anlegemaske hat KEINE vorausgewaehlte Kontaktstufe mehr — ``BaseModelForm``
    fuellt ``initial`` ohne ``instance`` nicht aus dem Modell-Default.

    Der Bestands-Pin ``test_identified_stays_selectable``
    (``src/tests/test_mutation_pins_090.py``) prueft nur die angebotenen
    Choices und ``initial["min_stage"]``; die Choices sind bei ``required_index
    == 0`` in beiden Fassungen gleich, deshalb greift er hier nicht.
    """

    def test_lowest_min_stage_still_prefills_contact_stage(self, facility):
        """Toetet ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_24``
        und ``core.forms.clients.xǁClientInlineCreateFormǁ__init____mutmut_25``.
        """
        form = ClientInlineCreateForm(facility=facility, min_stage=Client.ContactStage.IDENTIFIED)

        assert "contact_stage" in form.initial, "Die Anlegemaske haette keine vorausgewaehlte Stufe mehr"
        assert form.initial["contact_stage"] == Client.ContactStage.IDENTIFIED
        assert form.initial["min_stage"] == str(Client.ContactStage.IDENTIFIED)


@pytest.mark.django_db
class TestInlineCreateFormCleanKeepsTheModelValidationLayer:
    """``clean()`` ruft ``super()`` — und schaltet damit die Modellpruefung scharf.

    ``BaseModelForm.clean()`` tut zweierlei: es gibt ``self.cleaned_data``
    zurueck (dokumentierter Rueckgabevertrag) und setzt ``_validate_unique``
    sowie ``_validate_constraints`` auf ``True``. Faellt der ``super()``-Aufruf
    weg, bleiben beide Schalter auf ``False``: ``_post_clean`` ueberspringt
    ``validate_unique()`` und ``validate_constraints()`` vollstaendig.

    Dass diese Schicht heute leerlaeuft (``facility`` ist kein Formularfeld und
    faellt deshalb aus ``_get_validation_exclusions``), ist ein Erreichbarkeits-
    und kein Aequivalenzargument: sobald ``facility`` ein Formularfeld wird oder
    eine Constraint ueber reine Formularfelder dazukommt, ist derselbe Mutant
    ein fail-open auf ``unique_facility_pseudonym``. Der Pin haelt den Schalter
    fest, nicht seine heutige Wirkung.
    """

    def test_clean_returns_cleaned_data_and_arms_model_validation(self, facility):
        """Toetet ``core.forms.clients.xǁClientInlineCreateFormǁclean__mutmut_1``."""
        form = ClientInlineCreateForm(
            data={
                "pseudonym": "Pin-Clean-01",
                "contact_stage": Client.ContactStage.QUALIFIED,
                "min_stage": Client.ContactStage.QUALIFIED,
            },
            facility=facility,
        )

        assert form.is_valid(), dict(form.errors)

        # Die beiden Schalter setzt ausschliesslich ``BaseModelForm.clean()``.
        assert form._validate_unique is True
        assert form._validate_constraints is True

        rueckgabe = form.clean()
        assert rueckgabe is not None
        assert rueckgabe == form.cleaned_data


# ============================================================================
# core.services.system._db_admin — RLS-Bypass-Helfer (11 Mutanten)
# ============================================================================
#
# Pin-Tests fuer die RLS-Bypass-Helfer in ``core/services/system/_db_admin.py``.
#
# Beide Funktionen des Moduls sind sicherheitskritisch:
# :func:`has_rls_bypass_context` ist der Fail-Loud-Guard **aller** Wartungs- und
# Cron-Pfade (Refs #1016 A1.1), :func:`current_connection_owns` entscheidet, ob
# der Direktweg ueber ``ALTER TABLE ... DISABLE TRIGGER`` ueberhaupt gangbar ist
# (Refs #2085). Die Survivor-Triage zum v0.91.0-Delta hat dort elf echte
# Testluecken belegt — keine davon ein Live-Bug, aber jede ein unbewiesenes
# Verhalten:
#
# 1. **Kein Negativpfad.** Keiner der ~200 deckenden Tests ruft
#    ``has_rls_bypass_context()`` je unter einer Rolle **ohne**
#    SUPERUSER/BYPASSRLS auf; alle ~15 Aufrufer-Tests ersetzen die Funktion per
#    ``monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: ...)``. Der
#    Test-DB-User ist Superuser, also liefern Original *und* Mutante True — der
#    Guard liess sich komplett invertieren, ohne dass die Suite rot wurde.
# 2. **GUC-Zweig ungedeckt.** Die Quellzeilen 33/34 (``app.is_super_admin``)
#    werden nie erreicht, weil als Superuser schon Zeile 32 zurueckkehrt.
# 3. **Nicht-PostgreSQL-No-op ungedeckt.** Die Suite laeuft ausschliesslich gegen
#    PostgreSQL; der zugesicherte ``return True``-Vertrag beider Funktionen
#    (Quellzeilen 27 bzw. 222) ist nie geprueft worden.
#
# **Bewusst ohne die ``rls_test_role``-/``as_rls_role``-Helfer aus
# ``tests/test_rls_functional.py``:** the fixture skips itself under
# ``MUTANT_UNDER_TEST`` (Refs #1388), so a pin requesting it never runs in a
# mutation run. Until the skip moved into the fixture, the module skipped itself
# at import and took every importing file with it (documented in
# ``test_mutation_pins_091.py``). Das Rollen-Handling ist
# deshalb — wie in
# ``test_client_anonymize_now.py::TestAnonymizeClientNowUnterDerAppRolle`` — lokal
# kopiert. Aus demselben Grund gibt es hier **keinen** Modul-Level-Skip.
#
# **Kein Monkeypatch auf dem Django-``connection``-Proxy** (Refs #1647): gepatcht
# wird ausschliesslich die *Modulreferenz* ``_db_admin.connection``, nie
# ``django.db.connection`` selbst — ein Patch auf dem Proxy vergiftet den
# pytest-django-DatabaseBlocker und schlaegt spaeter als wandernder
# Session-Teardown-Fehler zurueck.
#
# Jeder Test nennt in seiner Docstring die Mutanten, die er toetet (voller
# mutmut-Name). Zusicherungen vergleichen exakt; Meldungstexte werden hier nicht
# gepinnt, weil beide Funktionen nur Booleans liefern.


class _FremdesBackend:
    """Minimaler Ersatz fuer ``django.db.connection`` mit Nicht-PostgreSQL-Vendor.

    ``cursor()`` ist absichtlich eine Falle: der No-op-Zweig muss **vor** jedem
    Datenbankzugriff zurueckkehren. Damit haelt der Stub zugleich die
    Vendor-Weiche fest — eine invertierte Weiche liefe in den Cursor.
    """

    vendor = "sqlite"

    def cursor(self):
        raise AssertionError("Der Nicht-PostgreSQL-Zweig darf keinen Cursor oeffnen")


class TestNichtPostgresBackendsBleibenNoop:
    """Der dokumentierte No-op-Vertrag beider Helfer auf Nicht-PostgreSQL.

    Gepinnte Mutanten:

    * ``core.services.system._db_admin.x_has_rls_bypass_context__mutmut_4``
      (Zeile 27: ``return True`` -> ``return False``) — mit ``False`` braeche auf
      einem Nicht-PostgreSQL-Backend **jeder** Wartungsbefehl
      (``enforce_retention``, ``verify_audit_chain``, ``backfill_audit_chain``,
      ``detect_breaches``, ``reencrypt_fields``, ``setup_facility``,
      ``load_package``, ...) sofort fail-loud ab.
    * ``core.services.system._db_admin.x_current_connection_owns__mutmut_4``
      (Zeile 222: ``return True`` -> ``return False``) — mit ``False`` naehme
      ``_redact_event_history`` (``core/services/client/main.py``) den
      ``deferred``-Pfad und liesse die ``EventHistory``-Klartexte bis zum
      naechsten Cron-Sweep stehen (DSGVO Art. 17).
    * als Zugabe ``x_has_rls_bypass_context__mutmut_1`` (Vendor-Weiche
      ``!=`` -> ``==``): die Mutante faellt auf dem Stub durch die Weiche
      hindurch und laeuft in die ``cursor()``-Falle.

    Ohne DB-Marker und ohne ``@pytest.mark.postgres``: der Pfad ist per
    Konstruktion backend-los, der Stub ersetzt die Verbindung vollstaendig.
    Vorbild fuer die Zusicherung:
    ``test_migration_rls_guard.py::test_guard_is_noop_on_non_postgresql``.
    """

    def test_has_rls_bypass_context_ist_auf_fremdem_backend_ein_noop(self, monkeypatch):
        """``True`` ohne jeden Katalog-/GUC-Zugriff — sonst waere der Guard auf
        SQLite eine Dauer-Blockade statt eines No-ops."""
        monkeypatch.setattr(_db_admin, "connection", _FremdesBackend())

        assert has_rls_bypass_context() is True

    def test_current_connection_owns_ist_auf_fremdem_backend_ein_noop(self, monkeypatch):
        """``True`` fuer eine beliebige Tabelle: auf Nicht-PostgreSQL ist auch
        ``bypass_replication_triggers`` ein No-op, der Direktweg also immer
        gangbar (Docstring-Vertrag der Funktion)."""
        monkeypatch.setattr(_db_admin, "connection", _FremdesBackend())

        assert current_connection_owns("core_eventhistory") is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestRlsBypassKontextUnterNichtBypassRolle:
    """Der fehlende Negativpfad: die **echte** Funktion unter einer Rolle ohne Bypass.

    Gepinnte Mutanten in ``has_rls_bypass_context`` (voller mutmut-Name mit
    Praefix ``core.services.system._db_admin.``):

    * ``x_has_rls_bypass_context__mutmut_1`` — Zeile 26,
      ``connection.vendor != "postgresql"`` -> ``==``: auf PostgreSQL, also im
      gesamten Produktivbetrieb, kehrt die Funktion sofort ``True`` zurueck und
      prueft weder Rolle noch GUC.
    * ``x_has_rls_bypass_context__mutmut_2`` — Zeile 26, ``"postgresql"`` ->
      ``"XXpostgresqlXX"``: wirkungsgleich, der Vergleich ist stets ungleich.
    * ``x_has_rls_bypass_context__mutmut_3`` — Zeile 26, ``"postgresql"`` ->
      ``"POSTGRESQL"``: ``connection.vendor`` ist immer klein, ebenfalls stets
      ungleich.
    * ``x_has_rls_bypass_context__mutmut_10`` — Zeile 31, ``if row and row[0]``
      -> ``if row or row[0]``: ``row`` ist immer ein 1-Tupel und damit wahr, die
      Funktion liefert auf PostgreSQL konstant ``True``.
    * ``x_has_rls_bypass_context__mutmut_13`` — Zeile 33, ``cur.execute(...)``
      -> ``cur.execute(None)``.
    * ``x_has_rls_bypass_context__mutmut_14`` — Zeile 33, Query-String ->
      ``"XX...XX"`` (Syntaxfehler).
    * ``x_has_rls_bypass_context__mutmut_16`` — Zeile 33, Query komplett gross
      **inklusive der String-Literale**: der GUC-Name ist case-egal, der
      Textvergleich ``= 'TRUE'`` aber case-SENSITIV. Der GUC-Zweig wird damit
      zur Konstanten ``False`` — ein legitim per ``app.is_super_admin``
      ausgewiesener Wartungslauf braeche fail-loud ab.
    * ``x_has_rls_bypass_context__mutmut_17`` — Zeile 34,
      ``bool(cur.fetchone()[0])`` -> ``bool(None)``: GUC-Zweig konstant
      ``False``. **Nur** der True-Pfad-Test toetet diese Mutante.
    * ``x_has_rls_bypass_context__mutmut_18`` — Zeile 34, ``[0]`` -> ``[1]``:
      ``IndexError`` beim ersten Betreten des GUC-Zweigs.

    ``transaction=True``: ``CREATE ROLE`` / ``SET ROLE`` und die auf
    Session-Ebene gesetzte GUC brauchen echte, nicht zurueckgerollte Sessions.
    ``@pytest.mark.postgres``: auf Nicht-PostgreSQL sauber uebersprungen
    (``tests/conftest.py::pytest_collection_modifyitems``).
    """

    # Refs #1479 (TST-03) / Refs #2097: Postgres-Rollen sind cluster-global —
    # xdist-Worker UND die parallelen mutmut-Forks teilen sich den Namensraum,
    # obwohl jeder seine eigene Test-DB hat. Ohne Suffix kollidieren zwei
    # Laeufe mit ``role already exists`` / ``cannot drop role``.
    @property
    def _role(self):
        return f"pin_dbadmin_norls{xdist_role_suffix()}"

    def _drop_role(self, role):
        """Idempotenter DROP — ``DROP OWNED BY`` vor ``DROP ROLE``, sonst blockt
        eine GRANT-Abhaengigkeit auf fremd-owned Tabellen. Das vorangestellte
        ``RESET ROLE`` ist das Sicherheitsnetz fuer den Fall, dass ein Test
        mitten unter der Rolle abgebrochen ist."""
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f"  EXECUTE format('DROP OWNED BY %I', '{role}'); "
                f"  EXECUTE format('DROP ROLE %I', '{role}'); "
                "END IF; END $$;"
            )

    def _create_role(self, role):
        """Prod-analoge App-Rolle: ``NOSUPERUSER NOBYPASSRLS`` (ADR-033,
        ``deploy/postgres-init/01-app-role.sh``).

        **Bewusst ohne jeden GRANT:** ``has_rls_bypass_context`` liest nur
        ``pg_roles`` (fuer PUBLIC lesbar) und ``current_setting`` — die Rolle
        braucht kein einziges zusaetzliches Recht, und was nicht vergeben wird,
        kann auch nicht liegenbleiben.
        """
        with connection.cursor() as cur:
            cur.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")

    @pytest.fixture(autouse=True)
    def _rolle_und_guc(self, db):
        """Rolle vor **und** nach dem Test wegraeumen; GUC exakt restaurieren.

        Eine stehengebliebene cluster-globale Rolle risse den naechsten Lauf —
        auch den eines anderen Testmoduls — mit ``role already exists``. Die
        Session-GUC wird auf ihren Vorwert zurueckgesetzt (Idiom aus
        ``_db_admin.rls_bypass_for_read``), weil die Connection von Folgetests
        im selben Prozess weiterbenutzt wird.
        """
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_super_admin', true)")
            self._guc_basis = cur.fetchone()[0] or ""
        self._drop_role(self._role)
        self._create_role(self._role)
        try:
            yield
        finally:
            self._drop_role(self._role)
            with connection.cursor() as cur:
                cur.execute("SELECT set_config('app.is_super_admin', %s, false)", [self._guc_basis])

    @contextmanager
    def _als_rolle_mit_guc(self, guc_wert):
        """Prod-Runtime nachgestellt: Nicht-Bypass-Rolle + definierte Session-GUC.

        Die GUC wird **vor** dem ``SET ROLE`` gesetzt und ``is_local=false``
        (Session-Ebene) — unter ``transaction=True`` laeuft jede Anweisung in
        ihrer eigenen Transaktion, ein ``SET LOCAL`` waere sofort wieder weg.
        Der ``finally``-Zweig raeumt Rolle und GUC weg.
        """
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.is_super_admin', %s, false)", [guc_wert])
            cur.execute(f"SET ROLE {self._role}")
        try:
            yield
        finally:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")
                cur.execute("SELECT set_config('app.is_super_admin', %s, false)", [self._guc_basis])

    def _rollen_posture(self):
        """``(rolsuper, rolbypassrls, current_user)`` der aktuell verbundenen Rolle.

        Der mitgelesene ``current_user`` belegt, dass ein ``SET ROLE`` wirklich
        gegriffen hat — ohne ihn waere ein stillschweigend wirkungsloses
        ``SET ROLE`` als gruener Test getarnt.
        """
        with connection.cursor() as cur:
            cur.execute("SELECT rolsuper, rolbypassrls, current_user FROM pg_roles WHERE rolname = current_user")
            return cur.fetchone()

    def test_ohne_bypass_rolle_und_ohne_guc_ist_der_kontext_falsch(self):
        """Toetet ``__mutmut_1``, ``__mutmut_2``, ``__mutmut_3``, ``__mutmut_10``,
        ``__mutmut_13``, ``__mutmut_14`` und ``__mutmut_18``.

        Die Kernzusage von Refs #1016 A1.1: laeuft ein Wartungs-/Cron-Lauf unter
        einer Rolle ohne SUPERUSER/BYPASSRLS und ohne Super-Admin-GUC, dann
        filtert RLS ihn auf 0 Zeilen — der Guard muss das **merken**. Alle vier
        Konstant-``True``-Mutanten (Vendor-Weiche und ``row or row[0]``) machen
        den Guard im Produktivbetrieb wirkungslos: der Lauf meldete Erfolg,
        ohne eine einzige Zeile gesehen zu haben.

        Die beiden Vorbedingungs-Zusicherungen halten den Test davon ab,
        scheingruen zu werden: ohne wirksames ``SET ROLE`` (Dev/Test laufen im
        Legacy-Einzelrollen-Modell als Superuser) waere ``False`` nie das
        richtige Ergebnis.
        """
        with self._als_rolle_mit_guc(""):
            rolsuper, rolbypassrls, aktueller_user = self._rollen_posture()
            assert aktueller_user == self._role, "Vorbedingung verletzt: SET ROLE hat nicht gegriffen"
            assert (rolsuper, rolbypassrls) == (False, False), (
                "Vorbedingung verletzt: die Testrolle muss NOSUPERUSER NOBYPASSRLS sein"
            )

            assert has_rls_bypass_context() is False

    def test_die_super_admin_guc_ersetzt_die_bypass_rolle(self):
        """Toetet ``__mutmut_16`` und ``__mutmut_17`` (und erneut ``__mutmut_13``,
        ``__mutmut_14``, ``__mutmut_18``).

        Der zweite, bislang vollstaendig ungedeckte Zweig: eine Rolle ohne
        Bypass-Attribut weist sich per Session-GUC ``app.is_super_admin`` aus
        (Migration 0047/0085) und gilt damit als RLS-berechtigt. Die
        Gross-Schreib-Mutante auf Zeile 33 macht genau diesen Zweig zur
        Konstanten ``False`` — der Vergleich ``= 'TRUE'`` ist case-sensitiv,
        gesetzt wird im ganzen Code aber ``'true'`` (``core/signals/audit``,
        ``rls_bypass_for_read``, ``run_system_detections``). Ein reiner
        False-Pfad-Test wuerde ``__mutmut_17`` (``bool(None)``) **nicht**
        toeten; deshalb dieser True-Pfad.
        """
        with self._als_rolle_mit_guc("true"):
            rolsuper, rolbypassrls, aktueller_user = self._rollen_posture()
            assert aktueller_user == self._role, "Vorbedingung verletzt: SET ROLE hat nicht gegriffen"
            assert (rolsuper, rolbypassrls) == (False, False), (
                "Vorbedingung verletzt: sonst entschiede die Rolle und nicht die GUC"
            )

            assert has_rls_bypass_context() is True

    def test_kontrolle_die_verbindungsrolle_selbst_hat_den_bypass(self):
        """Kontrolle: dieselbe Funktion, dieselbe Connection, ohne ``SET ROLE``.

        Belegt, dass das ``False`` oben an der **Rolle** haengt und nicht daran,
        dass die Funktion in dieser Umgebung generell ``False`` liefert. Die
        Zusicherung auf ``pg_roles`` davor macht eine abweichende Testumgebung
        (Verbindungsrolle ohne SUPERUSER/BYPASSRLS) als solche kenntlich, statt
        sie als Regression der Funktion erscheinen zu lassen.
        """
        rolsuper, rolbypassrls, _ = self._rollen_posture()
        assert rolsuper or rolbypassrls, (
            "Vorbedingung verletzt: die Test-Verbindungsrolle muss SUPERUSER oder BYPASSRLS sein"
        )

        assert has_rls_bypass_context() is True


# ============================================================================
# core.services.compliance.db_roles — Drei-Rollen-Modell (57+4 Mutanten)
# ============================================================================
#
# Pin-Tests aus der Survivor-Triage zu ``core.services.compliance.db_roles``.
#
# Welle D (0.91), Quelle der Befunde: ``reports/triage-dbroles1.json`` +
# ``triage-dbroles2.json``, gegengelesen in ``verify-dbroles1.json`` +
# ``verify-dbroles2.json``.
#
# ``db_roles`` ist die Auswertung des Drei-Rollen-Modells (ADR-020): sie traegt
# das Deploy-Gate ``manage.py check_db_roles`` (Exit 1 blockiert den Rollout)
# und die Datenbank-Karten des Compliance-Dashboards. Die Triage hat drei
# Luecken benannt, die diese Datei schliesst:
#
# 1. **Pflichtfelder der Compliance-Karten.** ``label``/``category``/
#    ``message``/``action_hint`` sind in ``_types.py`` als Pflicht deklariert und
#    werden in ``templates/core/system/compliance.html`` unbedingt gerendert —
#    ``None`` erscheint dort als literales "None". Die Bestandstests pruefen in
#    den betroffenen Zweigen nur ``status`` bzw. ``key``. Muster und Begruendung
#    wie ``test_mutation_pins_089.py`` (TestRetentionCheckCarriesMessageAndActionHint).
# 2. **Das gleiche Loch wie beim Admin-Check, nur auf der App-Seite.**
#    ``_db_role_admin_check`` hat fuer den UNKNOWN-Zweig laengst eine
#    Meldungs-Zusicherung; ``_db_role_attribute_check`` (App-Rolle) hat in
#    KEINEM der drei Zweige eine — dort ueberlebt ``message=None``.
#    Spiegelbildlich fuer ``check_db_ownership``: der Tabellen-Zweig killt eine
#    ``==``/``!=``-Vertauschung nur zufaellig, der DB-Owner-Zweig gar nicht.
# 3. **Die pg_catalog-Zugriffsschicht war komplett unausgefuehrt.**
#    ``_query_role``, ``_query_db_owner`` und ``_query_table_owner`` werden von
#    jedem Bestandstest wegge-monkeypatcht; ``mutmut-stats.json`` fuehrt die
#    letzten beiden gar nicht erst unter den gedeckten Funktionen. Spaltenwahl,
#    Parameterbindung und Row-Unpacking des Deploy-Gates waren damit
#    unverifiziert. Diese Datei faehrt sie gegen die echte Test-DB — lesend, per
#    ``@pytest.mark.postgres``, gegen die VORHANDENE Verbindungsrolle und die
#    vorhandenen Katalogeintraege. Es wird **keine** Rolle angelegt, kein Trigger
#    umgeschaltet, kein Zustand hinterlassen.
#
# **Konvention Meldungstexte:** Zusicherungen vergleichen EXAKT (``==``), nie per
# Teilstring. mutmut erzeugt zu jedem String-Literal u.a. die Variante
# ``"XX<text>XX"`` — ein ``in``-Vergleich faende den Originaltext darin weiter und
# liesse den Mutanten leben. Gettext ist hier nicht im Spiel: ``db_roles`` baut
# seine Texte als reine f-Strings, ein ``translation.override("de")`` waere
# wirkungslose Dekoration.
#
# **Bewusst NICHT gepinnt** (statt stillschweigend wegzulassen):
#
# * Der Wortlaut der beiden App-Karten-Labels ("App-DB-Rolle NOSUPERUSER" /
#   "App-DB-Rolle kein BYPASSRLS", ``_db_role_checks__mutmut_20/21/22`` und
#   ``36/37/38``) ist im Verify-Report als aequivalent BESTAETIGT — Feld bleibt
#   besetzt, kein Konsument vergleicht ihn. Gepinnt wird dort deshalb nur der
#   **Feld-Verlust** (``label=None``), nicht die Schreibweise.
# * Der Wortlaut der beiden Tabellen-Owner-Meldungen in ``check_db_ownership``
#   (``mutmut_48/49/50`` und ``52/53/54``) ist ebenfalls als aequivalent
#   bestaetigt — der Test in ``TestOwnershipGateChecksEveryAppendOnlyTable``
#   assertiert deshalb die **Anzahl** der Funde, nicht ihren Text.
# * No RLS fixture from ``tests/test_rls_functional.py``: ``rls_test_role`` skips
#   itself under ``MUTANT_UNDER_TEST``, a pin requesting it never runs in the
#   mutation run. Finding 1 of Refs #2087 concerned the former module-level skip,
#   which propagated through every import and dropped this whole file.
#   Auch modulweite Skips gibt es hier aus demselben Grund nicht.

_ROLE_SETUP_HINT = "DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh."
_APP_ROLE = "anlaufstelle_app"
_ADMIN_ROLE = "anlaufstelle_admin"


def _app_role_check(role, *, actual_super, actual_bypassrls):
    """``RoleCheck`` mit App-Erwartung (NOSUPERUSER + NOBYPASSRLS)."""
    return RoleCheck(
        role=role,
        label="App",
        expected_super=False,
        expected_bypassrls=False,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


def _admin_role_check(role, *, actual_super, actual_bypassrls):
    """``RoleCheck`` mit Admin-Erwartung (NOSUPERUSER + BYPASSRLS)."""
    return RoleCheck(
        role=role,
        label="Admin",
        expected_super=False,
        expected_bypassrls=True,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


# ---------------------------------------------------------------------------
# P-1 — _db_role_attribute_check: Pflichtfelder der App-Karten
# ---------------------------------------------------------------------------


class TestAttributeCheckKeepsItsDisplayFields:
    """Eine Karte ohne Ueberschrift, Gruppe und Massnahme ist kein Betriebssignal.

    ``test_compliance_service.py::TestDbRoleChecks`` prueft in allen drei
    Zweigen nur ``status``, ``test_mutation_followup_db_roles.py`` nur ``key``.
    ``label``/``category``/``action_hint`` sind damit unbelegt — obwohl
    ``compliance.html`` Zeile 61 ``{{ check.label }}`` unbedingt rendert (Django
    schreibt ``None`` als literales "None" ins Dashboard) und der ganze
    "Empfohlen:"-Block (Zeilen 72-76) an ``{% if check.action_hint %}`` haengt.

    ``category`` ist zusaetzlich maschinenrelevant: ``views/system/compliance.py``
    sortiert die nicht in ``_CATEGORY_ORDER`` gelisteten Restkategorien per
    ``sorted(grouped)`` — ein ``None`` darunter kippt den Request mit TypeError.
    """

    def test_unknown_branch_keeps_label_category_and_action_hint(self):
        """Toetet ``x__db_role_attribute_check__mutmut_17`` (``label=None``),
        ``__mutmut_18`` (``category=None``), ``__mutmut_21``
        (``action_hint=None``) und ``__mutmut_27`` (``action_hint``-kwarg ganz
        gestrichen, Dataclass-Default greift).

        UNKNOWN heisst hier: die als App-Rolle konfigurierte Rolle steht gar
        nicht in ``pg_roles``. Genau dann ist der Verweis auf
        ``01-app-role.sh`` die einzige Handlungsanweisung der Karte.
        """
        rc = _app_role_check("ghost_app", actual_super=None, actual_bypassrls=None)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.status == ComplianceStatus.UNKNOWN
        assert result.label == "App-DB-Rolle NOSUPERUSER"
        assert result.category == "Datenbank"
        assert result.action_hint == _ROLE_SETUP_HINT

    def test_ok_branch_keeps_label_and_category(self):
        """Toetet ``x__db_role_attribute_check__mutmut_30`` (``label=None``) und
        ``__mutmut_31`` (``category=None``).

        Auch die gruene Karte muss ihre Ueberschrift und ihre Gruppe behalten —
        sie ist der Nachweis, DASS geprueft wurde. Dass der gruene Zweig bewusst
        keine Massnahme traegt, wird hier mitgepinnt.
        """
        rc = _app_role_check(_APP_ROLE, actual_super=False, actual_bypassrls=False)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.status == ComplianceStatus.OK
        assert result.label == "App-DB-Rolle NOSUPERUSER"
        assert result.category == "Datenbank"
        assert result.action_hint is None

    def test_critical_branch_keeps_label_category_and_action_hint(self):
        """Toetet ``x__db_role_attribute_check__mutmut_42`` (``label=None``),
        ``__mutmut_47`` (``action_hint=None``) und ``__mutmut_54``
        (``action_hint``-kwarg gestrichen).

        Der teuerste Verlust der Serie: die Handtest-Matrix ENT-COMP-01
        (``docs/testing/manual-test-matrix-b.md``, Negativtest Schritt 2)
        verlangt woertlich, dass die rote App-NOSUPERUSER-Karte den Action-Hint
        "manage.py check_db_roles" zeigt. Die Form "kwarg gestrichen" ist im
        Repo schon einmal als kill-wuerdig entschieden worden
        (``test_mutation_pins_089.py``, Retention-``mutmut_64``).
        """
        rc = _app_role_check(_APP_ROLE, actual_super=True, actual_bypassrls=False)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.status == ComplianceStatus.CRITICAL
        assert result.label == "App-DB-Rolle NOSUPERUSER"
        assert result.category == "Datenbank"
        assert result.action_hint == "manage.py check_db_roles + Rollen-Init-Script pruefen (Refs #902)."


class TestAttributeCheckStatesWhatIsWrong:
    """Dasselbe Loch wie beim Admin-Check — nur auf der App-Seite offen.

    Fuer ``_db_role_admin_check`` gibt es im UNKNOWN-Zweig laengst eine
    Meldungs-Zusicherung (``test_mutation_followup_compliance.py``); fuer
    ``_db_role_attribute_check`` in keinem einzigen Zweig. Deshalb ueberlebt
    dort ``message=None`` — eine Karte, die Rot/Gelb/Grau anzeigt, ohne zu
    sagen, worum es geht. ``message`` ist Pflichtfeld (``_types.py``:42) und
    wird in ``compliance.html``:69 unbedingt gerendert.
    """

    def test_unknown_branch_names_the_missing_role(self):
        """Toetet ``x__db_role_attribute_check__mutmut_20`` (``message=None``).

        Der Rollenname ist der einzige Hinweis, WELCHE Rolle fehlt — ohne ihn
        ist die Karte im Drei-Rollen-Modell nicht handhabbar.
        """
        rc = _app_role_check("ghost_app", actual_super=None, actual_bypassrls=None)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.message == "Rolle 'ghost_app' nicht in pg_roles gefunden."
        assert result.detail is None

    def test_ok_branch_states_the_observed_attribute(self):
        """Toetet ``x__db_role_attribute_check__mutmut_33`` (``message=None``),
        ``__mutmut_34`` (``detail=None``) und ``__mutmut_40``
        (``detail``-kwarg gestrichen).

        ``detail`` ist im gruenen Zweig der gemessene Wert (``rolsuper=False``)
        — er unterscheidet "geprueft und in Ordnung" von "nicht geprueft".
        """
        rc = _app_role_check(_APP_ROLE, actual_super=False, actual_bypassrls=False)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.message == "Korrekt konfiguriert."
        assert result.detail == "anlaufstelle_app: rolsuper=False"

    def test_critical_branch_names_the_role_and_the_deviation(self):
        """Toetet ``x__db_role_attribute_check__mutmut_45`` (``message=None``).

        Der rote Fall ist der sicherheitsrelevante: eine App-Runtime-Rolle mit
        SUPERUSER haengt RLS komplett aus. Ohne Meldung sagt das Dashboard, dass
        etwas rot ist, aber nicht welche Rolle welches Attributprofil hat.
        """
        rc = _app_role_check(_APP_ROLE, actual_super=True, actual_bypassrls=False)

        result = db_roles._db_role_attribute_check(
            rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"
        )

        assert result.message == "Rolle 'anlaufstelle_app' hat falsches Attributprofil."
        assert result.detail == "rolsuper=True (erwartet: False)"


# ---------------------------------------------------------------------------
# P-2 — _db_role_admin_check: Pflichtfelder + Meldung der Admin-Karte
# ---------------------------------------------------------------------------


class TestAdminCheckKeepsItsDisplayFields:
    """Die Admin-Karte ist die einzige Anzeige der Wartungsrolle.

    ``test_mutation_followup_compliance.py`` prueft dort Status + Detail,
    ``test_mutation_followup_db_roles.py`` den ``key`` — ``label``,
    ``category`` und ``action_hint`` bleiben in allen drei Zweigen unbelegt.
    """

    def test_unknown_branch_keeps_label_category_and_action_hint(self):
        """Toetet ``x__db_role_admin_check__mutmut_3`` (``label=None``),
        ``__mutmut_4`` (``category=None``), ``__mutmut_7`` (``action_hint=None``)
        und ``__mutmut_13`` (``action_hint``-kwarg gestrichen).

        UNKNOWN heisst: ``POSTGRES_ADMIN_USER`` ist gesetzt, aber die Rolle
        existiert nicht — der Wartungspfad kann die Append-Only-Trigger dann
        gar nicht bedienen, und ``01-app-role.sh`` ist die einzige Anweisung.
        """
        rc = _admin_role_check("ghost_admin", actual_super=None, actual_bypassrls=None)

        result = db_roles._db_role_admin_check(rc)

        assert result.status == ComplianceStatus.UNKNOWN
        assert result.label == "Admin-DB-Rolle"
        assert result.category == "Datenbank"
        assert result.action_hint == _ROLE_SETUP_HINT

    def test_ok_branch_keeps_label_and_category(self):
        """Toetet ``x__db_role_admin_check__mutmut_17`` (``label=None``) und
        ``__mutmut_18`` (``category=None``)."""
        rc = _admin_role_check(_ADMIN_ROLE, actual_super=False, actual_bypassrls=True)

        result = db_roles._db_role_admin_check(rc)

        assert result.status == ComplianceStatus.OK
        assert result.label == "Admin-DB-Rolle"
        assert result.category == "Datenbank"
        assert result.action_hint is None

    def test_critical_branch_keeps_label_category_and_action_hint(self):
        """Toetet ``x__db_role_admin_check__mutmut_31`` (``label=None``),
        ``__mutmut_32`` (``category=None``), ``__mutmut_36``
        (``action_hint=None``) und ``__mutmut_43`` (``action_hint``-kwarg
        gestrichen).

        Rote Karte ohne Massnahme: der Verweis auf
        ``deploy/postgres-init/01-app-role.sh`` (Refs #902) ist der einzige
        Zeiger auf das Init-Script, das die Attribute setzt.
        """
        rc = _admin_role_check("bad_admin", actual_super=True, actual_bypassrls=False)

        result = db_roles._db_role_admin_check(rc)

        assert result.status == ComplianceStatus.CRITICAL
        assert result.label == "Admin-DB-Rolle"
        assert result.category == "Datenbank"
        assert result.action_hint == "deploy/postgres-init/01-app-role.sh pruefen (Refs #902)."


class TestAdminCheckStatesWhatIsWrong:
    """Meldung und Detail der Admin-Karte in den beiden belegten Zweigen."""

    def test_ok_branch_states_the_expected_profile(self):
        """Toetet ``x__db_role_admin_check__mutmut_20`` (``message=None`` im
        gruenen Zweig).

        Auch gruen ist ``message`` Pflichtfeld und wird unbedingt gerendert —
        die Karte zeigte sonst "None" unter ihrer Ueberschrift.
        """
        rc = _admin_role_check(_ADMIN_ROLE, actual_super=False, actual_bypassrls=True)

        result = db_roles._db_role_admin_check(rc)

        assert result.message == "Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS)."
        assert result.detail == "anlaufstelle_admin: rolsuper=False, rolbypassrls=True"

    def test_critical_branch_lists_every_deviation_separated(self):
        """Toetet ``x__db_role_admin_check__mutmut_34`` (``message=None``) und
        ``__mutmut_47`` (Trennzeichen ``"; "`` -> ``"XX; XX"``).

        Der Fall mit ZWEI Abweichungen (Superuser **und** fehlendes BYPASSRLS)
        ist der einzige, in dem das Trennzeichen ueberhaupt wirkt — mit nur
        einem Problem waere der Mutant unbeobachtbar. Genau dieser Fall ist der
        gefaehrliche: eine Admin-Rolle mit SUPERUSER haengt RLS aus und bedient
        die Append-Only-Trigger trotzdem nicht wie vorgesehen.
        """
        rc = _admin_role_check("bad_admin", actual_super=True, actual_bypassrls=False)

        result = db_roles._db_role_admin_check(rc)

        assert result.message == "Admin-Rolle hat falsches Attributprofil."
        assert result.detail == (
            "Rolle 'bad_admin': rolsuper=True (erwartet: False).; "
            "Rolle 'bad_admin': rolbypassrls=False (erwartet: True)."
        )


# ---------------------------------------------------------------------------
# P-3 — _db_role_checks: Karten-Aufbau (App-Labels + Warning-Karte)
# ---------------------------------------------------------------------------


class TestDbRoleChecksBuildsCompleteCards:
    """``_db_role_checks`` ist die Fabrik der drei Datenbank-Karten.

    Die Bestandstests greifen ausschliesslich ueber ``key`` und ``status`` zu;
    die an der Aufrufstelle uebergebenen Anzeigetexte sind unbelegt.
    """

    def test_both_app_cards_keep_a_label(self, monkeypatch):
        """Toetet ``x__db_role_checks__mutmut_11`` und ``__mutmut_27``
        (``label``-Argument der beiden App-Karten -> ``None``).

        Gepinnt wird bewusst nur der **Feld-Verlust**, nicht der Wortlaut: die
        Schreibvarianten derselben Literale (``mutmut_20/21/22`` bzw.
        ``36/37/38``) sind im Verify-Report als aequivalent BESTAETIGT — das
        Feld bleibt dort besetzt, kein Konsument vergleicht es, und ein
        ``==``-Wortlaut-Pin wuerde nur die Schreibweise zementieren.
        ``label=None`` dagegen rendert im Dashboard als literales "None".
        """
        app = _app_role_check(_APP_ROLE, actual_super=False, actual_bypassrls=False)
        monkeypatch.setattr(db_roles, "check_db_roles", lambda: ([app], []))

        checks = db_roles._db_role_checks()

        by_key = {c.key: c for c in checks}
        assert set(by_key) == {"db_role_app_nosuperuser", "db_role_app_nobypassrls"}
        for key, check in by_key.items():
            assert isinstance(check.label, str), f"{key}: label ist Pflichtfeld (str), nicht None"
            assert check.label.strip(), f"{key}: label darf nicht leer sein"

    @pytest.mark.django_db  # web runtime: marker lookup, empty log (Refs #2415)
    def test_admin_missing_card_carries_message_detail_and_hint(self, monkeypatch):
        """Pinnt Pflichtfelder + Wortlaut der "Admin-DB-Rolle"-Karte gegen
        Feld-Verlust (``label``/``message``/``action_hint`` -> ``None``,
        gestrichenes ``action_hint``-kwarg) und gegen ein zerschossenes
        Trennzeichen (``"; "`` -> ``"XX; XX"``).

        Die Karte ist der einzige Hinweis darauf, dass gar keine Wartungsrolle
        konfiguriert ist; ohne ``action_hint`` verschwindet der komplette
        "Empfohlen:"-Block und mit ihm der Zeiger auf den Wartungsdienst.

        Zwei Config-Errors statt einem: nur dann ist das Trennzeichen
        ueberhaupt beobachtbar. Der Wortlaut der echten Config-Error-Texte
        gehoert zu ``check_db_roles`` und wird hier bewusst nicht mitgepinnt —
        deshalb stehen hier neutrale Platzhalter.

        Refs #2310: Die Karte wird seit 0.99.2 von
        ``_db_role_admin_missing_check`` gebaut und ist in der Web-Runtime
        NEUTRAL (``unknown``) statt gelb — im Web-Container fehlen die
        Admin-Zugangsdaten per Deployment-Design. Der Wartungs-Kontext wird
        hier bewusst ausgeschlossen (``ANLAUFSTELLE_PROCESS_ROLE`` geloescht),
        damit der Pin nicht von der Umgebung des Testlaufs abhaengt; die
        gelbe Gegenprobe steht in ``test_compliance_dashboard_dbrole_status``.
        """
        monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)
        app = _app_role_check(_APP_ROLE, actual_super=False, actual_bypassrls=False)
        monkeypatch.setattr(
            db_roles,
            "check_db_roles",
            lambda: ([app], ["erster Konfigurationshinweis", "zweiter Konfigurationshinweis"]),
        )

        checks = db_roles._db_role_checks()

        card = next(c for c in checks if c.key == "db_role_admin_missing")
        assert card.status == ComplianceStatus.UNKNOWN
        assert card.label == "Admin-DB-Rolle"
        assert card.category == "Datenbank"
        assert card.message == "Im Web-Container nicht prüfbar — Prüfung über den Wartungsdienst."
        assert card.detail == "erster Konfigurationshinweis; zweiter Konfigurationshinweis"
        assert card.action_hint == (
            "Im web-Container nicht pruefbar — Prüfung über docker compose -f "
            "docker-compose.prod.yml run --rm maintenance check_db_roles."
        )


# ---------------------------------------------------------------------------
# P-4 — pg_catalog-Zugriffsschicht: echte Ausfuehrung gegen die Test-DB
# ---------------------------------------------------------------------------
#
# Kontrollabfragen bewusst NICHT in der Form der Quelle: je Spalte ein eigenes
# Statement bzw. ein Join ueber pg_class/pg_namespace statt der pg_tables-Sicht.
# Ein vertauschter Spaltenindex oder eine geschluckte Parameterbindung im
# Produktionscode faellt damit auf, statt sich gegen eine identische Kopie zu
# spiegeln. Alle Abfragen sind rein lesend — kein Rollen-/Trigger-Zustand.


def _current_role():
    """Effektive Rolle der laufenden Verbindung (``current_user``)."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_user")
        return cur.fetchone()[0]


def _catalog_role_attributes(role):
    """``(rolsuper, rolbypassrls)`` aus ``pg_roles`` — je Spalte eine Abfrage."""
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", [role])
        row = cur.fetchone()
        if row is None:
            return None, None
        is_super = bool(row[0])
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", [role])
        bypasses_rls = bool(cur.fetchone()[0])
    return is_super, bypasses_rls


def _catalog_db_owner():
    """Owner der aktuellen Datenbank ueber den Join ``pg_database`` x ``pg_roles``."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT r.rolname FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba "
            "WHERE d.datname = current_database()"
        )
        return cur.fetchone()[0]


def _catalog_table_owner(table):
    """Owner einer Tabelle ueber ``pg_class``/``pg_namespace`` statt ``pg_tables``."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT r.rolname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_roles r ON r.oid = c.relowner "
            "WHERE c.relname = %s AND n.nspname = 'public'",
            [table],
        )
        row = cur.fetchone()
    return row[0] if row else None


@pytest.mark.postgres
@pytest.mark.django_db
class TestQueryRoleReadsThePgRolesCatalog:
    """``_query_role`` ist die EINZIGE Stelle, die die realen Rollenattribute liest.

    Kein Bestandstest faehrt ihren Koerper: alle 14 Fundstellen in
    ``test_check_db_roles.py`` ersetzen sie per ``monkeypatch.setattr`` durch
    ein Lambda, das das Tupel fertig liefert. Damit war offen, ob ueberhaupt
    die richtigen Spalten in der richtigen Reihenfolge gelesen werden — ein
    konstant ``False`` gemeldetes ``rolsuper`` liesse eine App-Rolle mit
    SUPERUSER als "korrekt konfiguriert" durchs Deploy-Gate.

    Geprueft wird gegen die vorhandene Verbindungsrolle des Test-Clusters; es
    wird keine Rolle angelegt.
    """

    def test_connection_role_matches_the_catalog(self):
        """Toetet ``x__query_role__mutmut_8`` (``row = None``),
        ``__mutmut_9`` (Guard ``if not row`` invertiert), ``__mutmut_10``
        (``bool(row[0])`` -> ``bool(None)``, rolsuper konstant False) und
        ``__mutmut_12`` (``bool(row[1])`` -> ``bool(None)``, rolbypassrls
        konstant False).

        ``mutmut_8``/``__mutmut_9`` liefern ``(None, None)`` statt eines
        Bool-Paars — die Rolle erschiene als "existiert nicht". ``mutmut_10``
        und ``__mutmut_12`` sind die fail-open-faehigen: die Verbindungsrolle
        des Test-Clusters ist der Bootstrap-Superuser (``rolsuper`` und
        ``rolbypassrls`` beide wahr), ein konstant falsches Attribut faellt
        damit auf.
        """
        role = _current_role()
        expected = _catalog_role_attributes(role)
        assert expected[0] is not None, "Die Verbindungsrolle muss in pg_roles stehen."

        observed = db_roles._query_role(role)

        assert observed == expected
        assert all(isinstance(value, bool) for value in observed), "Die Attribute sind bool, nicht der Rohwert."

    def test_unknown_role_yields_two_nones(self):
        """Zweite Haelfte des Nachweises fuer ``x__query_role__mutmut_9``.

        Mit invertiertem Guard faellt der nicht existierende Fall in das
        ``row[0]``-Unpacking und zerreisst mit ``TypeError``. Der ``(None,
        None)``-Rueckgabewert ist zugleich der Vertrag, auf dem
        ``RoleCheck.exists`` und die UNKNOWN-Karten aufsetzen.
        """
        assert db_roles._query_role("pins_dbroles_rolle_gibt_es_nicht") == (None, None)


@pytest.mark.postgres
@pytest.mark.django_db
class TestQueryDbOwnerReadsPgDatabase:
    """``_query_db_owner`` traegt die halbe A1.4-Ownership-Assertion.

    ``mutmut-stats.json`` fuehrt die Funktion gar nicht erst unter den
    gedeckten Funktionen: jeder Aufrufer-Test patcht sie weg. Damit waren
    Statement, Spaltenwahl und Row-Unpacking des Gates unverifiziert.
    """

    def test_returns_the_owner_of_the_current_database(self):
        """Toetet ``x__query_db_owner__mutmut_1`` (``cur.execute(None)``),
        ``__mutmut_2`` (SQL-Literal XX-umhuellt -> ProgrammingError),
        ``__mutmut_5`` (``row = None``) und ``__mutmut_6`` (``row[0]`` ->
        ``row[1]``, IndexError bei einspaltigem Ergebnis).

        Ein still ``None`` liefernder Owner-Helfer macht das Gate zur No-op:
        ``check_db_ownership`` wertet ``None`` als "Objekt existiert noch
        nicht" und meldet nichts — ein unvollstaendiger § 9.6-Umbau bliebe
        gruen.
        """
        expected = _catalog_db_owner()
        assert expected, "Die Test-DB muss einen Owner haben."

        assert db_roles._query_db_owner() == expected


@pytest.mark.postgres
@pytest.mark.django_db
class TestQueryTableOwnerReadsPgTables:
    """``_query_table_owner`` entscheidet, ob die Append-Only-Trigger geschuetzt sind.

    Auch diese Funktion wurde von keinem Test ausgefuehrt — inklusive der
    Parameterbindung, an der die ganze Tabellenunterscheidung haengt.
    """

    def test_returns_the_owner_of_an_append_only_table(self):
        """Toetet ``x__query_table_owner__mutmut_1`` (``cur.execute(None, …)``),
        ``__mutmut_2`` (Parameterliste -> ``None``), ``__mutmut_3`` (erstes
        Argument entfernt, die Parameterliste rutscht auf die SQL-Position),
        ``__mutmut_4`` (Parameter-Argument ganz entfernt), ``__mutmut_5``
        (SQL-Literal XX-umhuellt), ``__mutmut_7`` (SQL komplett gross —
        ``%S`` ist kein gueltiger psycopg-Platzhalter), ``__mutmut_8``
        (``row = None``) und ``__mutmut_9`` (``row[0]`` -> ``row[1]``).

        ``core_auditlog`` ist eine der beiden Append-Only-Tabellen aus
        ``_APPEND_ONLY_TABLES`` — sie existiert in jeder migrierten Test-DB.
        """
        expected = _catalog_table_owner("core_auditlog")
        assert expected, "core_auditlog muss in der Test-DB existieren."

        assert db_roles._query_table_owner("core_auditlog") == expected

    def test_missing_table_yields_none(self):
        """Pinnt den ``None``-Pfad (``return row[0] if row else None``).

        Kein eigener Mutant, aber der Vertrag, auf dem der Erstinbetriebnahme-
        Fall beruht: ``docker-migrate.sh`` faehrt das Gate VOR dem ersten
        ``migrate``; ein Fund auf einer noch fehlenden Tabelle wuerde jede
        Neuinstallation fail-closed blockieren (Refs #2086).
        """
        assert db_roles._query_table_owner("core_auditlog_gibt_es_nicht") is None


@pytest.mark.postgres
@pytest.mark.django_db
class TestCurrentConnectionBypassesRlsRunsAgainstTheRealConnection:
    """Die L10-Deploy-Assertion (Refs #1375) war im Mutationslauf unsichtbar.

    ``test_connection_bypassrls.py`` covers it in ``make ci`` with fixtures from
    ``tests/test_rls_functional.py``. Until the skip moved into ``rls_test_role``,
    that module skipped itself under ``MUTANT_UNDER_TEST`` at import and took the
    importing file along, so the mutation run reported the function as "no-tests".
    This test needs no RLS fixture.
    """

    def test_reports_the_rls_state_of_the_running_connection(self):
        """Toetet ``x_current_connection_bypasses_rls__mutmut_1``
        (``connection_bypasses_rls(None)`` statt der echten Verbindung —
        ``None.cursor()`` bricht mit ``AttributeError`` ab).

        "Umgeht RLS" = Superuser ODER ``rolbypassrls``; die Erwartung wird aus
        denselben zwei Katalogspalten gebildet, aber unabhaengig abgefragt.
        """
        is_super, bypasses_rls = _catalog_role_attributes(_current_role())

        result = db_roles.current_connection_bypasses_rls()

        assert isinstance(result, bool)
        assert result == (is_super or bypasses_rls)


# ---------------------------------------------------------------------------
# P-5 — check_db_ownership: beide Append-Only-Tabellen, beide Owner-Zweige
# ---------------------------------------------------------------------------


def _three_role_env(monkeypatch, settings):
    """Drei-Rollen-Modell wie in ``TestOwnershipGateRequiresAdminOwner``."""
    monkeypatch.setitem(settings.DATABASES["default"], "USER", _APP_ROLE)
    monkeypatch.setenv("POSTGRES_APP_USER", _APP_ROLE)
    monkeypatch.setenv("POSTGRES_ADMIN_USER", _ADMIN_ROLE)


@pytest.mark.postgres
class TestOwnershipGateChecksEveryAppendOnlyTable:
    """Der Bestands-Fake macht beide Append-Only-Tabellen ununterscheidbar.

    ``_patch_owners`` in ``test_check_db_roles.py`` patcht
    ``_query_table_owner`` als ``lambda table: table_owner`` — das Lambda
    IGNORIERT ``table`` und liefert fuer ``core_auditlog`` und
    ``core_eventhistory`` denselben Owner. Genau diese eine Entscheidung haelt
    zwei Fail-open-Mutanten am Leben. Dieser Test baut den Bestandstest NICHT
    um; er stellt daneben einen tabellen-abhaengigen Fake, den es bisher
    nirgends gab.
    """

    def test_second_table_is_still_checked_when_the_first_one_is_clean(self, monkeypatch, settings):
        """Toetet ``x_check_db_ownership__mutmut_41`` (``_query_table_owner(None)``
        statt ``(table)`` — das Gate wird zur permanenten No-op) und
        ``__mutmut_45`` (``continue`` -> ``break`` — ``core_eventhistory`` wird
        nie geprueft, sobald ``core_auditlog`` sauber ist).

        Szenario: erste Tabelle admin-owned (sauber), zweite noch app-owned —
        der real haeufige Zwischenstand eines halb gefahrenen § 9.6-Umbaus.
        Erwartet werden genau zwei Eintraege: der Fund zu ``core_eventhistory``
        plus der Runbook-Trailer. Beide Mutanten liefern eine leere Liste.

        Bewusst OHNE Wortlaut-Zusicherung auf dem Fund: die Textvarianten
        dieser Meldung (``mutmut_48/49/50``) sind im Verify-Report als
        aequivalent bestaetigt und sollen nicht per Seiteneffekt zementiert
        werden.
        """
        _three_role_env(monkeypatch, settings)
        owners = {"core_auditlog": _ADMIN_ROLE, "core_eventhistory": _APP_ROLE}
        monkeypatch.setattr(db_roles, "_query_db_owner", lambda: _ADMIN_ROLE)
        monkeypatch.setattr(db_roles, "_query_table_owner", lambda table: owners.get(table))

        problems = db_roles.check_db_ownership()

        assert len(problems) == 2


@pytest.mark.postgres
class TestOwnershipGateNamesTheAppRoleAsDatabaseOwner:
    """Der DB-Owner-Zweig unterscheidet zwei Faelle — bisher ungeprueft.

    ``test_app_owning_database_is_flagged`` assertiert per Teilstring
    (``"Datenbank" in p and "anlaufstelle_app" in p``); beide Teilstrings
    stehen auch in der Fremd-Owner-Variante. Deshalb ueberlebt die Vertauschung
    von ``==``/``!=`` in Zeile 167 — dasselbe Loch, das der Tabellen-Zweig nur
    zufaellig killt (dort assertiert ein Bestandstest auf einen Rollennamen,
    der in der vertauschten Meldung fehlt).
    """

    def test_app_owned_database_uses_the_app_specific_wording(self, monkeypatch, settings):
        """Toetet ``x_check_db_ownership__mutmut_34`` (``if db_owner ==
        app_role`` -> ``!=``, die beiden Meldungsvarianten vertauscht) und —
        als Seiteneffekt der exakten Zusicherung — ``__mutmut_36/37/38``
        (Schreibvarianten des Satzschlusses "Owner sein.").

        Der Unterschied ist fachlich: "die App-Runtime-Rolle ist DB-Owner" ist
        der A1.4-Bestandsfall (Trigger per Owner-DDL abschaltbar), ein
        beliebiger Fremd-Owner der Fall-C-Zwischenstand aus Runbook § 9.6.
        Die Meldung ist das einzige Merkmal, an dem die Betreiberin die beiden
        unterscheidet.
        """
        _three_role_env(monkeypatch, settings)
        monkeypatch.setattr(db_roles, "_query_db_owner", lambda: _APP_ROLE)
        monkeypatch.setattr(db_roles, "_query_table_owner", lambda table: _ADMIN_ROLE)

        problems = db_roles.check_db_ownership()

        assert problems[0] == (
            "Datenbank-Owner ist die App-Runtime-Rolle 'anlaufstelle_app' — im "
            "Drei-Rollen-Modell (A1.4) muss die Admin-/Wartungs-Rolle 'anlaufstelle_admin' "
            "Owner sein."
        )


class TestCheckDbRolesLabelsItsRoleChecks:
    """``check_db_roles()`` beschriftet seine RoleChecks exakt mit "App"/"Admin".

    Toetet ``core.services.compliance.db_roles.x_check_db_roles__mutmut_60``,
    ``__mutmut_71``, ``__mutmut_72`` und ``__mutmut_73`` (Admin-Label ->
    ``None`` / ``"XXAdminXX"`` / ``"admin"`` / ``"ADMIN"``).

    Das Label ist kein Anzeigetext, sondern der ROUTING-SCHLUESSEL der
    Modulgrenze: ``_db_role_checks()`` waehlt die Dashboard-Karten ueber
    ``rc.label == "App"`` / ``rc.label == "Admin"``. Kippt das Admin-Label,
    verschwindet die Admin-Karte STILL aus dem Compliance-Dashboard — die
    "nicht konfiguriert"-Warnung erscheint dabei NICHT, denn sie verlangt
    zusaetzlich einen config_error, den eine GESETZTE Rolle gerade nicht
    liefert. Exakter Listenvergleich, damit auch Case-/XX-Varianten sterben.
    """

    def test_app_und_admin_rolecheck_tragen_ihre_routing_labels(self, monkeypatch):
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(db_roles, "_query_role", lambda role: (False, True))

        checks, config_errors = db_roles.check_db_roles()

        assert config_errors == []
        assert [rc.label for rc in checks] == ["App", "Admin"]
