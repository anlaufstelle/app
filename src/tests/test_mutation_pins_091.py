"""Pin-Tests aus der Survivor-Triage zum v0.89.0-Delta (Refs #2087).

Der ``mutation-delta``-Lauf zum v0.89.0-Cut hinterliess acht Follow-ups
(F-1…F-8), die **echte Testluecken** benennen — kein einziger davon ist ein
Live-Bug: der ausgelieferte Code ist in allen Faellen korrekt, es fehlte der
Nachweis, dass die Suite eine Verletzung *merken wuerde*. Diese Datei liefert
genau diesen Nachweis nach, fuer den Stand, den der Cut v0.91.0 ausliefert.

Zur Einordnung der Herkunft: die Laeufe, aus denen diese Survivor stammen,
waren nach oben verzerrt (parallele mutmut-Forks teilten sich eine Test-DB,
Fremdfehler wurden als ``killed`` verbucht — Refs #2097). Die Pins sind davon
unberuehrt: sie pinnen Verhalten, das heute unbewiesen ist, unabhaengig davon,
wie die Prozentzahl zustande kam.

Warum eine eigene Datei statt Streuung in die Bestandsdateien: die Zuordnung
Mutant -> Test ist der Nachweis, den die Cut-Policy verlangt
(``docs/dev/release-checklist.md`` § 0.5(3)). Muster und Begruendung wie
``src/tests/test_mutation_pins_089.py`` / ``test_mutation_pins_090.py``.

**Konvention Meldungstexte:** Zusicherungen vergleichen EXAKT (``==``), nie
per Teilstring. mutmut erzeugt zu jedem String-Literal u.a. die Variante
``"XX<text>XX"`` — ein Teilstring-Vergleich (``in``) findet den Originaltext
darin weiterhin und liesse den Mutanten ueberleben. Dasselbe gilt fuer
``pytest.raises(..., match=...)``: ``match`` ist ein Regex-*Suchen*, also
faktisch ein Teilstring — wo eine Meldung gepinnt wird, wird sie zusaetzlich
per ``==`` gegen ``str(exc.value)`` geprueft.

Jeder Test nennt in seiner Docstring den Mutanten, den er toetet. **Jeder
dieser Mutanten wurde von Hand in der Quelle nachgestellt, der Test
nachweislich rot gesehen und die Quelle danach zurueckgesetzt** — ein echter
``mutmut``-Lauf ist nicht Teil dieser Runde. Als aequivalent erkannte
Mutanten sind ebenfalls vermerkt, statt sie stillschweigend wegzulassen.

Der Sprachkontext wird dort explizit auf Deutsch festgenagelt
(``translation.override("de")``), wo ``gettext_lazy``-Texte verglichen werden.
"""

import json
import struct

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, override_settings
from django.utils import timezone
from django_otp.oath import totp as oath_totp

from core.models import AuditLog, Client, Event, User
from core.models import EncryptedTOTPDevice as TOTPDevice
from core.services.audit import log_audit_event
from core.services.compliance.breach_detection import detect_distributed_login_attack
from core.services.compliance.k_anonymization import count_clients_in_bucket, is_k_anonymous
from core.services.dashboard.external_report import ORDER_DOCUMENT_TYPE, _Breakdown, _escalate
from core.services.events.history_diff import (
    REDACTED_VALUE_TEXT,
    _diff_update,
    build_field_redaction_marker,
)
from core.services.file_vault import encryption as enc
from core.services.file_vault import generate_key, is_encrypted_value, safe_decrypt
from core.services.file_vault.encryption import (
    FILE_FORMAT_VERSION,
    MAX_CHUNK_TOKEN_BYTES,
    EncryptionError,
    decrypt_file_stream,
)
from core.services.file_vault.rotation import _reencrypt_events, _reencrypt_totp_devices
from core.services.security.mfa import verify_totp_or_backup
from core.services.security.totp import decrypt_totp_key

# Bewusst KEIN modulweites ``django_db``: mutmut faehrt die hier versammelten
# Tests fuer JEDEN Mutanten erneut (``pytest_add_cli_args_test_selection``),
# eine DB-Transaktion pro Test kostet dort real Laufzeit. Die DB-freien Bloecke
# (F-3, F-4b) bleiben deshalb DB-frei; nur die Klassen, die wirklich Zeilen
# brauchen, tragen die Marke.


def _valid_token_for(device):
    """Aktuell gueltiger TOTP-Token eines Geraets (Muster: test_mfa_login.py)."""
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


# ---------------------------------------------------------------------------
# F-1 — core.services.security.mfa.verify_totp_or_backup
# ---------------------------------------------------------------------------


@pytest.fixture
def mfa_user_a(facility):
    return User.objects.create_user(
        username="pin091-mfa-a", password="pin091pass!A", role=User.Role.STAFF, facility=facility
    )


@pytest.fixture
def mfa_user_b(facility):
    return User.objects.create_user(
        username="pin091-mfa-b", password="pin091pass!B", role=User.Role.STAFF, facility=facility
    )


@pytest.mark.django_db
class TestVerifyTotpOrBackupIsOwnerBound:
    """Der TOTP-Verify muss an den **Eigentuemer** des Geraets gebunden bleiben.

    Gepinnte Mutanten in ``verify_totp_or_backup`` (``mfa.py``), Zeile
    ``TOTPDevice.objects.filter(user=user, confirmed=True).first()``:

    * ``filter(user=user, confirmed=True)`` -> ``filter(confirmed=True)``
      (Eigentuemer-Filter faellt weg) — der schwerwiegende Fall: ein fremdes
      2FA-Geraet wuerde den Sudo-Mode-Re-Auth eines beliebigen Kontos
      freischalten (``views/sudo_mode.py`` ist der einzige Aufrufer).
    * ``confirmed=True`` -> ``confirmed=False`` (bzw. Wegfall) — ein noch
      nicht bestaetigtes, halb eingerichtetes Geraet duerfte nie verifizieren.

    Vor dieser Datei gab es **keinen** Test, der ``verify_totp_or_backup``
    ueberhaupt aufruft (``grep -rn "verify_totp_or_backup" src/tests/`` = 0);
    abgedeckt war nur der View darueber.
    """

    def test_foreign_device_does_not_verify_when_owner_has_none(self, mfa_user_a, mfa_user_b):
        """Deterministischer Killer des Eigentuemer-Filters: **genau eine** Zeile.

        ``mfa_user_a`` hat weder TOTP-Geraet noch Backup-Codes, ``mfa_user_b``
        ein bestaetigtes. Original: ``filter(user=a, …)`` -> ``None`` ->
        Fallback ``verify_backup_code`` -> ``False``. Ohne Eigentuemer-Filter
        findet ``.first()`` zwangslaeufig B's Geraet (die einzige Zeile der
        Tabelle) und verifiziert dessen Token -> ``True``. Kein
        ``.first()``-Reihenfolgerisiko, weil es nichts zu ordnen gibt.
        """
        device_b = TOTPDevice.objects.create(user=mfa_user_b, name="totp", confirmed=True, key="b" * 40)
        assert TOTPDevice.objects.count() == 1

        assert verify_totp_or_backup(mfa_user_a, _valid_token_for(device_b)) is False

    def test_foreign_token_does_not_verify_when_both_have_devices(self, mfa_user_a, mfa_user_b):
        """Die vom Ticket woertlich verlangte Zwei-Geraete-Variante.

        Beide Nutzer haben ein bestaetigtes Geraet, B's Token wird gegen A
        gefahren. Die Geraete tragen **verschiedene** Secrets — damit ist das
        Ergebnis unabhaengig davon, welche Zeile ein ungefiltertes
        ``.first()`` (ohne ``order_by``) liefert: unter A's Geraet verifiziert
        B's Token nie.
        """
        TOTPDevice.objects.create(user=mfa_user_a, name="totp", confirmed=True, key="a" * 40)
        device_b = TOTPDevice.objects.create(user=mfa_user_b, name="totp", confirmed=True, key="b" * 40)

        assert verify_totp_or_backup(mfa_user_a, _valid_token_for(device_b)) is False

    def test_own_confirmed_device_still_verifies(self, mfa_user_a):
        """Kontrolle: der Positivfall bleibt positiv.

        Ohne ihn koennte ein Pin auch dadurch gruen sein, dass
        ``verify_totp_or_backup`` grundsaetzlich ``False`` liefert.
        """
        device_a = TOTPDevice.objects.create(user=mfa_user_a, name="totp", confirmed=True, key="a" * 40)

        assert verify_totp_or_backup(mfa_user_a, _valid_token_for(device_a)) is True

    def test_unconfirmed_own_device_does_not_verify(self, mfa_user_a):
        """Toetet ``confirmed=True`` -> ``confirmed=False``/Wegfall.

        Das Geraet gehoert dem richtigen Nutzer und der Token ist gueltig —
        nur die Bestaetigung fehlt. Ein halb eingerichtetes Geraet darf den
        Sudo-Mode nicht oeffnen; ohne diesen Test ist die
        ``confirmed``-Bedingung der Zeile unbeobachtet.
        """
        device_a = TOTPDevice.objects.create(user=mfa_user_a, name="totp", confirmed=False, key="a" * 40)

        assert verify_totp_or_backup(mfa_user_a, _valid_token_for(device_a)) is False


# ---------------------------------------------------------------------------
# F-3 — core.services.events.history_diff._diff_update (Redaktions-Weiche)
# ---------------------------------------------------------------------------

#: Ein einziges, unverschluesseltes Feld ohne Sensitivity — die Weiche selbst
#: haengt an keiner Maskierung, und ein schlanker ``slug_info``-Stub haelt den
#: Pin DB-frei (er wird bei jedem kuenftigen Mutationslauf erneut gefahren).
_DIFF_SLUG_INFO = {"notiz": {"name": "Notiz", "is_encrypted": False, "sensitivity": ""}}


def _diff(before, after):
    return _diff_update(before, after, _DIFF_SLUG_INFO, None, "")


class TestDiffUpdateRedactionSwitchIsOneSided:
    """Die halbseitige Redaktion muss in den Redaktions-Pfad fallen.

    Gepinnter Mutant in ``_diff_update`` (``history_diff.py``):
    ``is_redaction_marker(data_before) or is_redaction_marker(data_after)``
    -> ``... and ...``. Unter dem Mutanten laufen genau die **halbseitigen**
    Faelle (eine Spalte redigiert, die andere mit echten Werten) in den
    Normalpfad; dort werden die Marker-Schluessel ``_redacted``/``fields`` wie
    Feld-Slugs behandelt und erscheinen als Diff-Zeilen ``_Redacted``/
    ``Fields`` auf der Detailseite — genau das, was ADR-050 D6 verhindern
    soll. Halbseitig ist der Regelfall, nicht die Ausnahme: der Alters-Sweep
    redigiert die eine Spalte einer noch lebenden Fassung.

    Markerform durchgehend ``build_field_redaction_marker`` (die
    ``fields``-Form). Sie ist die einzige, die Slugs mitfuehrt und deshalb
    ueberhaupt eine pruefbare Diff-Zeile erzeugt; die Art.-17-Variante
    (``build_anonymization_marker``, ``anonymized`` statt ``fields``) faellt
    ueber ``_marker_slugs`` auf die leere Slug-Liste zurueck und traegt zur
    Weiche nichts bei. Dass ``is_redaction_marker`` **beide** Formen erkennt,
    ist bereits gepinnt (``test_eventhistory_retention.py``
    ``TestRedactionMarkerInventory::test_the_two_marker_forms_are_pinned``)
    und wird hier nicht wiederholt.

    Der Redaktions-Anzeigewert wird gegen die importierte Konstante
    ``REDACTED_VALUE_TEXT`` verglichen, nicht gegen ein abgeschriebenes
    Literal: sonst pinnte der Test dieselbe Zeichenkette zweimal statt die
    Weiche.
    """

    def test_only_before_is_redacted(self):
        """Halbseitig links — der Killer-Fall Nr. 1."""
        result = _diff(build_field_redaction_marker(["notiz"]), {"notiz": "neuer Text"})

        assert result == [
            {
                "label": "Notiz",
                "old_value": REDACTED_VALUE_TEXT,
                "new_value": "neuer Text",
                "changed": True,
            }
        ]

    def test_only_after_is_redacted(self):
        """Halbseitig rechts — der Killer-Fall Nr. 2."""
        result = _diff({"notiz": "alter Text"}, build_field_redaction_marker(["notiz"]))

        assert result == [
            {
                "label": "Notiz",
                "old_value": "alter Text",
                "new_value": REDACTED_VALUE_TEXT,
                "changed": True,
            }
        ]

    def test_both_sides_redacted_stay_in_the_redacted_path(self):
        """Kontrolle: beidseitig redigiert faellt unter ``or`` **und** ``and``
        in den Redaktions-Pfad — der Test ist also nicht einfach immer rot.

        Fachlich zugleich die Zusicherung aus ``_diff_update_redacted``: zwei
        redigierte Werte werden **nicht** als „unveraendert" weggefiltert
        (``changed`` bleibt True), weil hier nichts verglichen werden kann.
        """
        result = _diff(build_field_redaction_marker(["notiz"]), build_field_redaction_marker(["notiz"]))

        assert result == [
            {
                "label": "Notiz",
                "old_value": REDACTED_VALUE_TEXT,
                "new_value": REDACTED_VALUE_TEXT,
                "changed": True,
            }
        ]

    def test_neither_side_redacted_stays_in_the_normal_path(self):
        """Kontrolle: ohne Marker bleibt es beim Normalpfad — unter ``or``
        wie unter ``and`` identisch. Belegt, dass die beiden Killer-Faelle
        oben wirklich an der Weiche haengen und nicht am Vergleichscode."""
        result = _diff({"notiz": "alter Text"}, {"notiz": "neuer Text"})

        assert result == [
            {
                "label": "Notiz",
                "old_value": "alter Text",
                "new_value": "neuer Text",
                "changed": True,
            }
        ]


# ---------------------------------------------------------------------------
# F-6 — core.services.audit.helpers.log_audit_event (Pre-Auth-GUC-Sync)
# ---------------------------------------------------------------------------


def _preauth_request():
    """Anonymer Request wie im Login-Lockout-/Password-Reset-Pfad."""
    request = RequestFactory().post("/login/", REMOTE_ADDR="203.0.113.10")
    request.user = AnonymousUser()
    return request


def _guc(name):
    """Aktueller Wert einer Session-GUC (``None``, wenn nie gesetzt)."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting(%s, true)", [name])
        return cur.fetchone()[0]


def _set_guc(name, value):
    with connection.cursor() as cur:
        cur.execute("SELECT set_config(%s, %s, false)", [name, value])


@pytest.mark.postgres
@pytest.mark.django_db
class TestLogAuditEventSuperAdminGuc:
    """Der Pre-Auth-Zweig darf die Super-Admin-GUC nicht zu weit oeffnen.

    Gepinnte Mutanten in ``log_audit_event`` (``audit/helpers.py``), Zeile
    ``_set_session_vars(facility, is_super_admin=bool(getattr(user, "is_super_admin", False)))``:

    * ``getattr(user, "is_super_admin", False)`` -> ``..., True)`` — der
      gefaehrliche: im Lockout-Pfad ist ``user`` regelmaessig ``None``
      (unbekannter Username), der Default greift also **im Normalbetrieb**.
      Mit ``True`` stuende ``app.is_super_admin='true'`` in der Session und
      der WITH-CHECK-Zweig aus Migration 0085 ginge fuer eine **beliebige**
      Facility auf.
    * ``"is_super_admin"`` -> ``"XXis_super_adminXX"`` (und die
      ``bool(...)``-Negation) — dann faende ``getattr`` das Attribut nie und
      ein echter Super-Admin bekaeme die GUC **nicht** gesetzt.

    Beobachtet wird die Wirkung an der Session-GUC, die
    ``core.signals.audit._set_session_vars`` schreibt (``"true"`` bzw. ``""``).
    Der Setter selbst liegt in ``core/signals/`` und damit ausserhalb des
    Mutations-Scope; gepinnt wird hier die **Aufrufstelle** im Service.

    **Bewusst ohne die ``rls_test_role``-Fixturen** aus
    ``tests/test_rls_functional.py`` (die § 4.6 des Briefings vorschlug):
    jenes Modul ruft bei gesetztem ``MUTANT_UNDER_TEST`` ein
    ``pytest.skip(allow_module_level=True)`` (Refs #1388) — ein Import von
    dort wuerde **diese ganze Datei** in jeder mutmut-Phase ueberspringen und
    damit saemtliche Pins wirkungslos machen (empirisch belegt: mit
    ``MUTANT_UNDER_TEST=`` sammelt ``test_preauth_audit_rls.py`` 0 statt 3
    Tests). Fuer die hier gepinnte Zusicherung ist die Rolle auch nicht
    noetig: gemessen wird der GUC-Wert, nicht das Verhalten der Policy.
    """

    def test_unknown_user_does_not_claim_super_admin(self, facility):
        """Toetet den ``False`` -> ``True``-Default.

        Lockout gegen einen unbekannten Username: ``user=None``, aber
        ``facility`` gesetzt. ``getattr(None, "is_super_admin", False)`` muss
        ``False`` bleiben — die GUC also leer.
        """
        _set_guc("app.is_super_admin", "sentinel")

        log_audit_event(
            _preauth_request(),
            AuditLog.Action.LOGIN_FAILED,
            user=None,
            facility=facility,
            detail={"message": "Fehlgeschlagener Login-Versuch", "username": "unbekannt"},
        )

        assert _guc("app.is_super_admin") == ""
        assert _guc("app.current_facility_id") == str(facility.pk)

    def test_super_admin_does_claim_super_admin(self, super_admin_user):
        """Toetet die Attributnamen-Mutation und die ``bool(...)``-Negation.

        Derselbe Pre-Auth-Pfad mit einem echten Super-Admin (ohne
        Facility-Bindung, Refs #867): die GUC muss ``"true"`` tragen, sonst
        scheitert der AuditLog-INSERT unter der NOBYPASSRLS-App-Rolle an der
        WITH-CHECK-Policy und die Lockout-Meldung ginge still verloren.
        """
        _set_guc("app.is_super_admin", "sentinel")

        log_audit_event(
            _preauth_request(),
            AuditLog.Action.LOGIN_FAILED,
            user=super_admin_user,
            facility=super_admin_user.facility,
            detail={"message": "Login blockiert durch Account-Lockout", "reason": "locked"},
        )

        assert _guc("app.is_super_admin") == "true"

    def test_regular_user_does_not_claim_super_admin(self, staff_user):
        """Kontrolle: derselbe Pfad mit einem gewoehnlichen Konto laesst die
        GUC leer. Belegt, dass die beiden Faelle oben am **Wert** haengen und
        nicht daran, dass der Zweig ueberhaupt feuert."""
        _set_guc("app.is_super_admin", "sentinel")

        log_audit_event(
            _preauth_request(),
            AuditLog.Action.LOGIN_FAILED,
            user=staff_user,
            facility=staff_user.facility,
            detail={"message": "Login blockiert durch Account-Lockout", "reason": "locked"},
        )

        assert _guc("app.is_super_admin") == ""


# ---------------------------------------------------------------------------
# F-4a — core.services.file_vault.rotation: Rotation ueber MEHR als ein Objekt
# ---------------------------------------------------------------------------


def _raw_totp_key(pk) -> str:
    """``otp_totp_totpdevice.key`` direkt aus der DB-Zeile (ohne ORM-Cache)."""
    with connection.cursor() as cur:
        cur.execute("SELECT key FROM otp_totp_totpdevice WHERE id = %s", [pk])
        return cur.fetchone()[0]


def _raw_event_data_json(event):
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_event WHERE id = %s", [str(event.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.mark.django_db
class TestRotationCountsEveryObjectNotJustTheFirst:
    """Die Rotationspfade muessen **jedes** Objekt anfassen und zaehlen.

    Alle Bestandsproben in ``test_reencrypt_fields.py`` fahren genau **ein**
    Objekt ueber ``call_command("reencrypt_fields")``. Damit ist eine ganze
    Mutantenklasse unsichtbar, weil 1 und „konstant 1" nicht unterscheidbar
    sind:

    * ``_reencrypt_totp_devices``: ``updated += 1`` -> ``updated = 1`` (beide
      Vorkommen, Dry-Run- und Rewrap-Zweig) sowie ``continue`` -> ``break`` im
      Dry-Run-Zweig — Letzteres liesse ab dem zweiten Geraet **alle**
      restlichen Secrets unrotiert unter dem Alt-Schluessel liegen, waehrend
      der Lauf Erfolg meldet. Genau darauf stuetzt sich die Rotations-Prozedur
      des Admin-Guide, wenn sie den Alt-Schluessel entfernt.
    * ``_reencrypt_events``: ``updated += 1`` -> ``updated = 1`` und
      ``changed or healed != event.data_json`` -> ``... and ...``.

    Die privaten Funktionen werden **direkt** aufgerufen statt ueber
    ``call_command`` — mutmut faehrt diese Tests fuer jeden Mutanten erneut,
    ein voller Sieben-Pfad-Lauf je Mutant waere unbezahlbar.
    """

    def test_totp_rewrap_counts_both_devices(self, facility, mfa_user_a, mfa_user_b):
        """Zwei Geraete -> Zaehler 2, und **beide** Secrets liegen danach unter
        dem neuen Primaerschluessel."""
        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            device_a = TOTPDevice.objects.create(user=mfa_user_a, name="totp", confirmed=True, key="a" * 40)
            device_b = TOTPDevice.objects.create(user=mfa_user_b, name="totp", confirmed=True, key="b" * 40)
        before = {device_a.pk: _raw_totp_key(device_a.pk), device_b.pk: _raw_totp_key(device_b.pk)}

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            updated, contended = _reencrypt_totp_devices(dry_run=False)

            assert updated == 2
            assert contended == []
            # Nur der neue Schluessel: beide Tokens muessen frisch gewickelt sein.
            with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
                assert decrypt_totp_key(_raw_totp_key(device_a.pk)) == "a" * 40
                assert decrypt_totp_key(_raw_totp_key(device_b.pk)) == "b" * 40
        assert _raw_totp_key(device_a.pk) != before[device_a.pk]
        assert _raw_totp_key(device_b.pk) != before[device_b.pk]

    def test_totp_dry_run_counts_both_devices(self, facility, mfa_user_a, mfa_user_b):
        """Der Dry-Run-Zweig hat ein eigenes ``continue`` und einen eigenen
        Zaehler — beide brauchen ein zweites Geraet, um beobachtbar zu sein."""
        key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=key):
            device_a = TOTPDevice.objects.create(user=mfa_user_a, name="totp", confirmed=True, key="a" * 40)
            device_b = TOTPDevice.objects.create(user=mfa_user_b, name="totp", confirmed=True, key="b" * 40)
            before = {device_a.pk: _raw_totp_key(device_a.pk), device_b.pk: _raw_totp_key(device_b.pk)}

            updated, contended = _reencrypt_totp_devices(dry_run=True)

        assert updated == 2
        assert contended == []
        assert _raw_totp_key(device_a.pk) == before[device_a.pk]
        assert _raw_totp_key(device_b.pk) == before[device_b.pk]

    def test_events_count_both_the_rotated_and_the_healed_row(
        self, facility, client_identified, doc_type_crisis, staff_user
    ):
        """Zwei Events, **zwei verschiedene** Gruende gezaehlt zu werden.

        Event 1 traegt einen echten Ciphertext (``changed=True``), Event 2
        einen Klartext im verschluesselten Slug (``changed=False``, aber
        ``healed != data_json`` — der ``document_type``-Heal-Pfad aus A4.4).
        Der Zaehler muss 2 sein: unter ``updated = 1`` wie unter
        ``changed and healed != …`` waere er 1.
        """
        slug = doc_type_crisis.fields.first().field_template.slug
        old_key = generate_key()
        new_key = generate_key()
        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=old_key):
            rotated = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={slug: "Ciphertext-Zeile"},
                created_by=staff_user,
            )
            healed = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_crisis,
                occurred_at=timezone.now(),
                data_json={slug: "wird gleich zu Klartext"},
                created_by=staff_user,
            )
        assert is_encrypted_value(_raw_event_data_json(rotated)[slug])
        # Klartext-Altbestand nachstellen: am ORM vorbei, damit nichts verschluesselt.
        Event.objects.filter(pk=healed.pk).update(data_json={slug: "Klartext-Altbestand"})
        assert not is_encrypted_value(_raw_event_data_json(healed)[slug])

        with override_settings(ENCRYPTION_KEYS=f"{new_key},{old_key}", ENCRYPTION_KEY=""):
            updated = _reencrypt_events(dry_run=False)

            assert updated == 2
            assert is_encrypted_value(_raw_event_data_json(healed)[slug])
            with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=new_key):
                assert safe_decrypt(_raw_event_data_json(rotated)[slug]) == "Ciphertext-Zeile"
                assert safe_decrypt(_raw_event_data_json(healed)[slug]) == "Klartext-Altbestand"


# ---------------------------------------------------------------------------
# F-4b — decrypt_file_stream: der Deckel greift bei ``>``, nicht bei ``>=``
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_encryption_key")
class TestDecryptFileStreamChunkTokenCeiling:
    """``token_len == MAX_CHUNK_TOKEN_BYTES`` muss **durchgehen**.

    Gepinnter Mutant in ``decrypt_file_stream`` (``file_vault/encryption.py``):
    ``if token_len > MAX_CHUNK_TOKEN_BYTES`` -> ``>=``. Der Bestandstest
    ``test_file_encryption.py::TestVaultHeaderBounds::
    test_oversized_token_length_is_rejected_before_reading`` faehrt
    ``MAX + 1`` und ueberlebt den Mutanten unbeschadet — ``MAX + 1`` wird
    unter ``>`` wie unter ``>=`` abgewiesen. Beobachtbar wird der Unterschied
    erst **exakt auf** der Grenze.

    Fachlich ist die Grenze der groesste legitim geschriebene Token (voller
    v2-Chunk); ein ``>=`` wuerde genau diesen Chunk fuer unlesbar erklaeren
    und damit Bestandsanhaenge (Art.-9-Daten) sperren.

    Der Nachweis haengt bewusst **nicht** allein an der Fehlermeldung: der
    Test belegt ueber die tatsaechlich an ``read`` gestellten Groessen, dass
    der Token gelesen **wurde** — die Ablehnung kommt danach von Fernet, nicht
    vom Deckel. Braucht keine DB.
    """

    def test_token_length_exactly_at_the_ceiling_is_read_and_not_refused(self, tmp_path, monkeypatch):
        out = tmp_path / "atmax.enc"
        with open(out, "wb") as handle:
            handle.write(struct.pack(">B", FILE_FORMAT_VERSION))
            handle.write(struct.pack(">I", 1))
            handle.write(struct.pack(">I", MAX_CHUNK_TOKEN_BYTES))
            # Body vollstaendig vorhanden: die Ablehnung darf nicht als
            # Truncation erklaerbar sein.
            handle.write(b"\x00" * MAX_CHUNK_TOKEN_BYTES)

        requested: list[int] = []
        real_open = open

        def _spy_open(*args, **kwargs):
            handle = real_open(*args, **kwargs)
            real_read = handle.read

            def _read(size=-1):
                requested.append(size)
                return real_read(size)

            handle.read = _read  # type: ignore[method-assign]
            return handle

        monkeypatch.setattr(enc, "open", _spy_open, raising=False)

        with pytest.raises(EncryptionError) as excinfo:
            list(decrypt_file_stream(out))

        # Der Token wurde gelesen — der Deckel hat NICHT vorher zugeschlagen.
        assert MAX_CHUNK_TOKEN_BYTES in requested
        # Und die Meldung ist die des Fernet-Decrypts, nicht die des Deckels.
        # Exakter Vergleich (kein ``match=``-Teilstring): mutmuts
        # ``"XX…XX"``-Variante der Deckel-Meldung wuerde einen Teilstringtest
        # ueberleben.
        assert str(excinfo.value) == "Chunk decryption failed: "
        assert str(excinfo.value) != (
            f"Implausible chunk token length in header: {MAX_CHUNK_TOKEN_BYTES} exceeds {MAX_CHUNK_TOKEN_BYTES}"
        )


# ---------------------------------------------------------------------------
# F-7a — compliance.k_anonymization.is_k_anonymous
# ---------------------------------------------------------------------------


def _make_clients(facility, staff_user, *, count, age_cluster, contact_stage, prefix):
    return [
        Client.objects.create(
            facility=facility,
            pseudonym=f"{prefix}-{i}",
            contact_stage=contact_stage,
            age_cluster=age_cluster,
            created_by=staff_user,
        )
        for i in range(count)
    ]


@pytest.mark.django_db
class TestIsKAnonymousCutsTheBucketByContactStage:
    """Die Aequivalenzklasse ist ``(age_cluster, contact_stage)`` — **beide**.

    Gepinnte Mutanten in ``is_k_anonymous`` (``k_anonymization.py``):
    ``count_clients_in_bucket(..., contact_stage=client.contact_stage)`` ->
    ``contact_stage=None`` bzw. Wegfall des kwargs. ``count_clients_in_bucket``
    behandelt ``contact_stage=None`` ausdruecklich als „nur ``age_cluster``
    matchen" — der Mutant vergroessert die gemessene Anonymitaetsmenge also
    still und erklaert eine Person fuer k-anonym, die es nicht ist. Fail-safe-
    Richtung waere das Gegenteil.

    Der Bestandstest ``test_k_anonymization.py::TestIsKAnonymous::
    test_count_clients_in_bucket_matches_equivalence_class`` prueft den
    ``contact_stage``-Schnitt zwar, aber an ``count_clients_in_bucket``
    **direkt** — nicht *durch* ``is_k_anonymous`` hindurch, wo der Mutant
    sitzt. Die Schwelle selbst (``>= k`` -> ``> k``) ist dort bereits
    beidseitig gepinnt (``:70`` darunter, ``:84`` exakt darauf) und wird hier
    bewusst nicht wiederholt.
    """

    def test_bucket_below_k_although_the_age_cluster_alone_would_reach_it(self, facility, staff_user):
        """Vier Personen in ``18_26/identified``, drei in ``18_26/qualified``.

        Mit dem ``contact_stage``-Schnitt zaehlt die Funktion 4 (< 5 -> False);
        ohne ihn 7 (>= 5 -> True). Genau diese Konstellation koennen die
        Bestandstests nicht herstellen — dort liegen alle Zeilen in derselben
        Kontaktstufe.
        """
        identified = _make_clients(
            facility,
            staff_user,
            count=4,
            age_cluster=Client.AgeCluster.AGE_18_26,
            contact_stage=Client.ContactStage.IDENTIFIED,
            prefix="pin091-k-id",
        )
        _make_clients(
            facility,
            staff_user,
            count=3,
            age_cluster=Client.AgeCluster.AGE_18_26,
            contact_stage=Client.ContactStage.QUALIFIED,
            prefix="pin091-k-qu",
        )

        assert count_clients_in_bucket(facility, Client.AgeCluster.AGE_18_26) == 7
        assert is_k_anonymous(identified[0], k=5) is False

    def test_same_stage_peers_do_reach_k(self, facility, staff_user):
        """Kontrolle: fuenf Personen in **derselben** Stufe erreichen k=5.

        Belegt, dass der Fall oben am ``contact_stage``-Schnitt haengt und
        nicht daran, dass ``is_k_anonymous`` hier grundsaetzlich ``False``
        liefert.
        """
        peers = _make_clients(
            facility,
            staff_user,
            count=5,
            age_cluster=Client.AgeCluster.AGE_18_26,
            contact_stage=Client.ContactStage.IDENTIFIED,
            prefix="pin091-k-same",
        )

        assert is_k_anonymous(peers[0], k=5) is True

    def test_default_k_is_five(self, facility, staff_user):
        """Toetet das Default-Literal ``k: int = 5`` -> ``6``.

        Kein Aufrufer im Baum ruft ``is_k_anonymous`` ohne ``k=`` auf; ohne
        diesen Pin ist der Default vollstaendig unbeobachtet. Fachlich ist er
        die Rueckfallschwelle, wenn ein kuenftiger Aufrufer sie nicht setzt.
        """
        five = _make_clients(
            facility,
            staff_user,
            count=5,
            age_cluster=Client.AgeCluster.AGE_27_PLUS,
            contact_stage=Client.ContactStage.IDENTIFIED,
            prefix="pin091-k-def5",
        )
        four = _make_clients(
            facility,
            staff_user,
            count=4,
            age_cluster=Client.AgeCluster.U18,
            contact_stage=Client.ContactStage.IDENTIFIED,
            prefix="pin091-k-def4",
        )

        assert is_k_anonymous(five[0]) is True
        assert is_k_anonymous(four[0]) is False


# ---------------------------------------------------------------------------
# F-7b — compliance.breach_detection.detect_distributed_login_attack
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.django_db
class TestDistributedLoginDefaultThreshold:
    """Die Default-Schwelle ``10`` greift nur, wenn die Einstellung **fehlt**.

    Gepinnte Mutanten:

    * ``detect_distributed_login_attack``: ``_get_threshold(
      "BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD", 10)`` -> ``…, 11)`` — der im
      Triage-Report gemeinte „10 vs. 11"-Fall.
    * ``_get_threshold``: ``getattr(settings, name, default)`` ->
      ``getattr(settings, name, None)`` bzw. Wegfall des Defaults.

    **Alle** sieben Bestandsproben (``test_breach_detection.py``,
    ``test_mutation_1642_breach_detection.py``) setzen die Einstellung
    explizit; der Schwellenvergleich ``distinct_ips__gte`` ist dort beidseitig
    gepinnt (3 IPs bei Schwelle 3 -> Alarm, 2 -> keiner), das Default-Literal
    dagegen nie erreicht. Dieser Test entfernt die Einstellung, statt sie zu
    setzen — damit wird der Default zum wirksamen Wert.
    """

    def test_default_of_ten_applies_when_the_setting_is_absent(self, facility, admin_user, settings):
        del settings.BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD
        settings.BREACH_DETECTION_WINDOW_MINUTES = 60
        assert not hasattr(django_settings, "BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD")

        for i in range(9):
            AuditLog.objects.create(
                facility=facility,
                user=admin_user,
                action=AuditLog.Action.LOGIN_FAILED,
                ip_address=f"203.0.113.{i}",
            )
        assert detect_distributed_login_attack(facility) == []

        AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN_FAILED,
            ip_address="203.0.113.9",
        )
        findings = detect_distributed_login_attack(facility)

        assert len(findings) == 1
        assert findings[0]["count"] == 10
        assert findings[0]["threshold"] == 10


# ---------------------------------------------------------------------------
# F-2 — Offenlegungskontrolle: was die Messung wirklich ergeben hat
# ---------------------------------------------------------------------------
#
# F-2 war der einzige der acht Punkte, dessen Lueckenbehauptung sich nicht
# durch Lesen bestaetigen liess. Deshalb wurde jeder Bedingungs-Mutant der
# beiden benannten Funktionen von Hand hergestellt und gegen die BESTEHENDE
# Suite gefahren (``test_disclosure_inference.py``,
# ``test_external_report_service.py``,
# ``test_reporting_disclosure_regression.py``, 120 Tests). Ergebnis:
# 14 von 16 sterben bereits dort — ``disclosure._hidden_bounds`` ist
# vollstaendig abgedeckt (7 von 7), ``external_report._escalate`` bis auf
# zwei. Nur fuer die zwei Ueberlebenden steht hier ein Pin; die uebrigen
# werden NICHT nachgebaut, weil ein zweiter Test fuer einen bereits toten
# Mutanten nichts nachweist.


class TestEscalateReportsOnlyRealProgress:
    """``_escalate`` darf „etwas passiert" nur melden, wenn etwas passiert ist.

    Die zwei Ueberlebenden der F-2-Messung, beide in derselben Zeile bzw. am
    Funktionsende von ``_escalate`` (``dashboard/external_report.py``):

    * ``if margin_key and report.get(margin_key) is not None:`` -> ``or``.
      Dann greift der Zweig auch, wenn die Randsumme im Artefakt **gar nicht
      steht**: ``report[margin_key] = None`` legt den Schluessel neu an — das
      Artefakt bekaeme eine Randsummen-Zeile, die es nie hatte — und die
      Funktion meldet Fortschritt, den es nicht gab. Die Guard-Schleife in
      ``apply_residual_guard`` dreht daraufhin bis ``MAX_GUARD_ITERATIONS``
      (256) leer und laesst die Aufschluesselung anschliessend fail-safe ganz
      fallen, statt sie nach einem sauberen ``break`` zu behalten.
    * ``return False`` am Funktionsende -> ``return True``: derselbe
      Leerlauf, nur ohne Bedingung.

    Beide sind in Fail-safe-Richtung (es wird eher zu viel unterdrueckt), aber
    sie entwerten die Abbruchbedingung der Leiter — und genau die ist das
    Terminierungsargument der Funktion.

    Aufruf direkt auf ``_escalate`` mit einfachen Dicts: der Mutant sitzt in
    der Abbruchbedingung, nicht im Zusammenspiel mit echten Berichtsdaten,
    und der Pin bleibt damit DB-frei und schnell (er laeuft je Mutant erneut).
    """

    def test_absent_margin_is_no_progress_and_is_not_invented(self):
        """Randsumme steht nicht im Artefakt: kein Fortschritt, kein neuer Schluessel."""
        spec = _Breakdown("by_document_type", "total_contacts", ORDER_DOCUMENT_TYPE)
        report: dict = {}

        assert _escalate(report, {spec.key: spec}, [(spec.key, "zelle-1")]) is False
        assert report == {}

    def test_absent_person_margin_is_no_progress_either(self):
        """Derselbe Zweig ueber den Personen-Fallback: eine Aufschluesselung
        ohne eigene Spec faellt auf ``PERSON_MARGIN_KEY`` zurueck — auch der
        darf nicht erfunden werden."""
        report: dict = {}

        assert _escalate(report, {}, [("unbekannte_partition", "zelle-1")]) is False
        assert report == {}

    def test_present_margin_is_dropped_and_counts_as_progress(self):
        """Kontrolle: liegt die Randsumme vor, faellt sie — und **das** ist
        Fortschritt. Belegt, dass die beiden Faelle oben an der Anwesenheit
        des Schluessels haengen und nicht daran, dass ``_escalate`` hier
        grundsaetzlich ``False`` liefert."""
        spec = _Breakdown("by_document_type", "total_contacts", ORDER_DOCUMENT_TYPE)
        report: dict = {"total_contacts": 12}

        assert _escalate(report, {spec.key: spec}, [(spec.key, "zelle-1")]) is True
        assert report == {"total_contacts": None}
