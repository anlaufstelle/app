"""Pin-Tests zum Meilenstein release-0.94 — Modul-Batch ``mfa``.

Quelle: Survivor-Triage des Moduls ``src/core/services/security/mfa.py``
(Mutantenbaum ``mutants/src/core/services/security/mfa.py``). Vorgabe ist die
Survivor-Triage release-0.94 (Ledger des Meilensteins) (15 Mutanten, Herkunft
durchgehend ``nachlauf``; vier davon waren zunaechst als aequivalent notiert
und wurden in der Gegenpruefung widerlegt — sie sind hier mitgepinnt).

Drei Bloecke, drei Wahrheiten des 2FA-Recovery-Pfads:

* **Der Legacy-Reparaturzweig** in :func:`~core.services.security.mfa._get_or_create_device`
  (Zeilen 67-69) war von keinem einzigen Test erreicht: kein Bestandstest legt
  ein ``StaticDevice(name="backup", confirmed=False)`` an und schickt es durch
  ``generate_backup_codes``. Bleibt ein solches Altgeraet unbestaetigt, filtert
  :func:`~core.services.security.mfa.verify_backup_code` es weg (``confirmed=True``,
  Zeile 107) — frisch ausgegebene Backup-Codes waeren fuer diesen Nutzer wertlos
  (2FA-Recovery-Lockout). Ebenfalls unbeobachtet: dass ein NEU angelegtes Geraet
  sofort bestaetigt INSERTet wird (kein Zwischenzustand, kein Nach-UPDATE) und
  dass die Reparatur nur die Spalte ``confirmed`` schreibt.
* **Die Namens-/Bestaetigungs-Klammer** des Geraets. Der Modul-Docstring
  (Zeilen 3-7) verspricht *genau ein* ``StaticDevice`` namens ``"backup"`` pro
  Nutzer. Kein Bestandstest legt einem Nutzer je ein ZWEITES ``StaticDevice``
  mit abweichendem Namen an — deshalb sind der Namensfilter in
  ``remaining_backup_codes``/``verify_backup_code`` und der ``confirmed``-Filter
  in ``verify_backup_code`` ungepinnt. Die Pins stellen jeweils eine echte
  Koeder-Zeile daneben.
* **Die Eingangs-Normalisierung** von :func:`~core.services.security.mfa.verify_totp_or_backup`
  (Zeile 139). Der Leer-Guard schuetzt das Backup-Geraet vor Throttle-Inkrementen
  bei leerem Formularfeld (``views/sudo_mode.py`` schickt genau ``""``), das
  ``replace(" ", "")`` traegt den aus der Authenticator-App kopierten,
  gruppierten Token (``"123 456"``). Beides war unbeobachtet: die F-1-Pins in
  ``test_mutation_pins_091.py`` fahren nur gueltige bzw. fremde Tokens ohne
  Whitespace.

Die Save-Form-Pins (Zahl der ``StaticDevice``-Saves, ``created``-Flag,
``update_fields``) haengen an einem echten ``post_save``-Receiver auf
``StaticDevice`` statt an SQL-Textvergleichen — ``_record_device_saves``. Das
ist die Stelle, an der Django die Schreibabsicht offenlegt, und sie bricht
nicht an einer geaenderten Query-Formatierung.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); KEINE Imports
aus ``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht. Texte
werden hier keine verglichen, daher kein ``translation.override``.
"""

from contextlib import contextmanager

import pytest
from django.db.models.signals import post_save
from django_otp.oath import totp as oath_totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from core.models import EncryptedTOTPDevice as TOTPDevice
from core.models import User
from core.services.security.mfa import (
    BACKUP_CODES_COUNT,
    BACKUP_DEVICE_NAME,
    generate_backup_codes,
    remaining_backup_codes,
    verify_backup_code,
    verify_totp_or_backup,
)

#: Ein Alt-Code im Pre-#790-Cleartext-Format (``xxxx-xxxx``). Er wird hier nur
#: als *Koeder* gebraucht: die Pins pruefen, dass er NICHT verifiziert, weil das
#: tragende Geraet den falschen Namen bzw. keine Bestaetigung hat.
LEGACY_CODE = "abcd-1234"


@contextmanager
def _record_device_saves():
    """Zeichnet jeden ``StaticDevice``-Save als ``(created, confirmed, update_fields)`` auf.

    Django reicht ``update_fields`` als ``frozenset`` (bzw. ``None``) an
    ``post_save`` durch — damit ist sowohl die ANZAHL der Saves als auch die
    geschriebene Spaltenliste exakt vergleichbar, ohne SQL-Text zu parsen.
    ``bulk_create`` (die Backup-Codes selbst) feuert kein ``post_save``, die
    Aufzeichnung enthaelt also ausschliesslich Geraete-Saves.
    """
    records: list[tuple[bool, object, object]] = []

    def _receiver(sender, instance, created, update_fields, **kwargs):
        records.append((created, instance.confirmed, update_fields))

    post_save.connect(_receiver, sender=StaticDevice, dispatch_uid="pins094-mfa-device-saves")
    try:
        yield records
    finally:
        post_save.disconnect(sender=StaticDevice, dispatch_uid="pins094-mfa-device-saves")


def _valid_token_for(device):
    """Aktuell gueltiger TOTP-Token eines Geraets (Muster: ``test_mutation_pins_091.py``)."""
    token = oath_totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)
    return f"{token:0{device.digits}d}"


@pytest.fixture
def mfa_pin_user(facility):
    """Eigener Nutzer ohne jedes OTP-Geraet — die Pins bauen ihre Lage selbst."""
    return User.objects.create_user(
        username="pins094-mfa",
        password="pins094mfa!Pass",
        role=User.Role.STAFF,
        facility=facility,
    )


# ---------------------------------------------------------------------------
# _get_or_create_device — Anlage und Legacy-Reparatur (7 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetOrCreateDeviceRepairsLegacyRows:
    """Ein Backup-Geraet ist bestaetigt — beim Anlegen wie beim Vorfinden.

    ``verify_backup_code`` filtert hart auf ``confirmed=True`` (mfa.py:107).
    Ein unbestaetigtes Geraet ist damit unsichtbar: ``generate_backup_codes``
    wuerde Codes ausgeben, die niemand einloesen kann — 2FA-Recovery-Lockout
    fuer genau die Nutzer, deren Zeile aus der Zeit vor dem Reparaturzweig
    stammt. Vor diesen Pins fuhr kein Test je ein unbestaetigtes Backup-Geraet
    durch die Funktion (``test_mfa_backup_codes.py:71`` und ``:242`` legen es
    immer mit ``confirmed=True`` an), der Zweig mfa.py:67-69 war komplett
    unbeobachtet.
    """

    def test_legacy_unconfirmed_device_is_repaired_and_codes_work(self, mfa_pin_user):
        """Pin: ``x__get_or_create_device__mutmut_11`` — ``if not device.confirmed:`` -> ``if device.confirmed:``
        Pin: ``x__get_or_create_device__mutmut_12`` — ``device.confirmed = True`` -> ``= None``
        Pin: ``x__get_or_create_device__mutmut_13`` — ``device.confirmed = True`` -> ``= False``
        Pin: ``x__get_or_create_device__mutmut_15`` — ``update_fields=["confirmed"]`` -> ``["XXconfirmedXX"]``
        Pin: ``x__get_or_create_device__mutmut_16`` — ``update_fields=["confirmed"]`` -> ``["CONFIRMED"]``

        Original: ``device.confirmed`` steht nach dem Lauf auf ``True``, der
        erste Code verifiziert (``True``), und es bleibt bei genau einem Geraet.
        Mutant 11 laesst den Reparaturzweig aus, Mutant 13 schreibt ``False``
        zurueck — in beiden Faellen bleibt ``device.confirmed`` ``False`` und
        ``verify_backup_code`` liefert ``False`` statt ``True``. Mutant 12
        schreibt NULL in ein NOT-NULL-Feld (``IntegrityError`` aus dem
        ``save()``), Mutant 15/16 reichen einen Feldnamen durch, den es nicht
        gibt (``ValueError`` aus Djangos ``update_fields``-Validierung) — der
        Aufruf ``generate_backup_codes`` bricht ab, statt Codes zu liefern.
        """
        device = StaticDevice.objects.create(user=mfa_pin_user, name=BACKUP_DEVICE_NAME, confirmed=False)

        codes = generate_backup_codes(mfa_pin_user)

        device.refresh_from_db()
        assert device.confirmed is True
        assert StaticDevice.objects.filter(user=mfa_pin_user, name=BACKUP_DEVICE_NAME).count() == 1
        assert len(codes) == BACKUP_CODES_COUNT
        assert verify_backup_code(mfa_pin_user, codes[0]) is True

    def test_repair_save_touches_only_the_confirmed_column(self, mfa_pin_user):
        """Pin: ``x__get_or_create_device__mutmut_14`` — ``save(update_fields=["confirmed"])``
        -> ``save(update_fields=None)``

        Der Reparatur-Save ist ein Teil-Save: Django schickt genau
        ``frozenset({"confirmed"})`` an ``post_save``, weil nur diese eine
        Spalte gemeint ist. ``update_fields=None`` (der Mutant) schreibt
        stattdessen ALLE Spalten der In-Memory-Instanz zurueck — inklusive
        ``throttling_failure_count``, ``throttling_failure_timestamp`` und
        ``last_used_at``; ein parallel hochgezaehlter Throttle-Wert waere
        damit ueberschrieben (Lost Update auf dem Drossel-Zaehler des
        Recovery-Pfads).

        Gemessen: Original ``[(False, True, frozenset({"confirmed"}))]``,
        Mutant ``[(False, True, None)]``.
        """
        StaticDevice.objects.create(user=mfa_pin_user, name=BACKUP_DEVICE_NAME, confirmed=False)

        with _record_device_saves() as saves:
            generate_backup_codes(mfa_pin_user)

        assert saves == [(False, True, frozenset({"confirmed"}))]

    def test_new_device_is_inserted_already_confirmed(self, mfa_pin_user):
        """Pin: ``x__get_or_create_device__mutmut_10`` — ``defaults={"confirmed": True}`` -> ``{"confirmed": False}``

        Ein frisch angelegtes Backup-Geraet ist von der ersten Zeile an gueltig:
        EIN Save, ``created=True``, ``confirmed=True``, ohne
        ``update_fields``-Einschraenkung. Der Mutant INSERTet die Zeile
        unbestaetigt und laesst sie erst vom Reparaturzweig nachziehen — der
        Endzustand stimmt zwar, das Schreibverhalten nicht: es gibt einen
        (fuer die eigene Transaktion sichtbaren) Zwischenzustand
        ``confirmed=False`` und ein zusaetzliches UPDATE.

        Gemessen: Original ``[(True, True, None)]``, Mutant
        ``[(True, False, None), (False, True, frozenset({"confirmed"}))]``.
        """
        assert StaticDevice.objects.filter(user=mfa_pin_user).count() == 0

        with _record_device_saves() as saves:
            generate_backup_codes(mfa_pin_user)

        assert saves == [(True, True, None)]


# ---------------------------------------------------------------------------
# remaining_backup_codes / verify_backup_code — Geraete-Klammer (3 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBackupDeviceIsBoundToNameAndConfirmation:
    """Nur das bestaetigte Geraet namens ``"backup"`` zaehlt und verifiziert.

    ``StaticDevice`` ist ein django-otp-Standardmodell; andere Plugin-Flows
    (oder das Admin) koennen einem Nutzer weitere statische Geraete anlegen.
    Ohne Namensfilter zaehlte die Restcode-Anzeige fremde Tokens mit und die
    Verifikation konsumierte sie. Kein Bestandstest legt einem Nutzer je ein
    zweites ``StaticDevice`` mit abweichendem Namen an.
    """

    def test_foreign_static_device_is_not_counted(self, mfa_pin_user):
        """Pin: ``x_remaining_backup_codes__mutmut_5``
        — ``filter(user=user, name=BACKUP_DEVICE_NAME)`` -> ``filter(user=user)``

        Der Nutzer hat KEIN Backup-Geraet, aber ein anderes statisches Geraet
        mit drei Tokens — und zwar als einzige Zeile der Tabelle, damit die
        ``.first()``-Reihenfolge nichts entscheidet. Original: ``0``.
        Mutant: ``3`` (die Restcode-Anzeige zaehlte fremde Tokens als
        einloesbare Backup-Codes).
        """
        other = StaticDevice.objects.create(user=mfa_pin_user, name="other", confirmed=True)
        StaticToken.objects.bulk_create([StaticToken(device=other, token=f"tok-{i}") for i in range(3)])
        assert StaticDevice.objects.filter(user=mfa_pin_user).count() == 1

        assert remaining_backup_codes(mfa_pin_user) == 0

    def test_foreign_static_device_tokens_are_not_consumable(self, mfa_pin_user):
        """Pin: ``x_verify_backup_code__mutmut_8``
        — ``filter(user=user, name=BACKUP_DEVICE_NAME, confirmed=True)``
        -> ``filter(user=user, confirmed=True)``

        Gleiche Lage wie oben, jetzt auf dem Verifikationspfad: das einzige
        Geraet des Nutzers heisst ``"other"`` und traegt einen Token. Original:
        ``False``, und der Token liegt danach unangetastet da. Mutant: ``True``
        — die Sudo-Re-Auth oeffnet mit einem Token, der gar kein Backup-Code
        ist, und LOESCHT ihn dabei (``token_set.count()`` faellt auf ``0``).
        """
        other = StaticDevice.objects.create(user=mfa_pin_user, name="other", confirmed=True)
        StaticToken.objects.create(device=other, token=LEGACY_CODE)
        assert StaticDevice.objects.filter(user=mfa_pin_user).count() == 1

        assert verify_backup_code(mfa_pin_user, LEGACY_CODE) is False
        assert other.token_set.count() == 1

    def test_unconfirmed_backup_device_does_not_verify(self, mfa_pin_user):
        """Pin: ``x_verify_backup_code__mutmut_9``
        — ``filter(..., confirmed=True)`` -> ``filter(...)`` ohne ``confirmed``

        Das Geraet heisst richtig, ist aber nicht bestaetigt — ein halb
        eingerichteter oder bewusst entwerteter Recovery-Kanal. Der
        ``confirmed``-Filter ist die einzige Stelle, die ihn ausschliesst.
        Original: ``False``, Token bleibt liegen. Mutant: ``True`` und der
        Token ist verbraucht (``token_set.count() == 0``). Fuer
        ``verify_totp_or_backup`` ist dieser Negativfall bereits gepinnt
        (``test_mutation_pins_091.py``), fuer ``verify_backup_code`` fehlte er.
        """
        device = StaticDevice.objects.create(user=mfa_pin_user, name=BACKUP_DEVICE_NAME, confirmed=False)
        StaticToken.objects.create(device=device, token=LEGACY_CODE)

        assert verify_backup_code(mfa_pin_user, LEGACY_CODE) is False
        assert device.token_set.count() == 1


# ---------------------------------------------------------------------------
# verify_backup_code — Ein-Save-Consume (2 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSuccessfulConsumeWritesTheDeviceExactlyOnce:
    """Throttle-Reset und ``last_used_at`` gehen in EINEM Save in die Zeile.

    ``verify_backup_code`` sammelt beide Aenderungen bewusst im Speicher
    (``commit=False``) und schreibt sie mit einem einzigen ``device.save()``
    (mfa.py:125-127). Wer ``commit=True`` setzt, erzeugt zusaetzliche
    vollstaendige UPDATEs auf ``otp_static_staticdevice`` mitten im Consume —
    mehr Schreiblast auf dem Pfad, der unter Brute-Force-Last am haeufigsten
    laeuft, und ein zusaetzliches Zeitfenster, in dem der zurueckgesetzte
    Zaehler schon persistiert ist, der Verbrauch aber noch nicht abgeschlossen.
    """

    def test_consume_triggers_a_single_device_save(self, mfa_pin_user):
        """Pin: ``x_verify_backup_code__mutmut_24`` — ``throttle_reset(commit=False)`` -> ``commit=True``
        Pin: ``x_verify_backup_code__mutmut_26`` — ``set_last_used_timestamp(commit=False)`` -> ``commit=True``

        Aufgezeichnet wird nur der Consume (die Geraete-Anlage laeuft vor dem
        Recorder). Original: genau ein Save mit den drei geaenderten Spalten
        (seit Refs #2355 spaltengenau statt ``None``). Beide
        Mutanten fuegen davor einen weiteren vollstaendigen Save ein
        (``ThrottlingMixin.throttle_reset`` bzw.
        ``TimestampMixin.set_last_used_timestamp`` rufen bei ``commit=True``
        ``self.save()``), die Aufzeichnung hat dann ZWEI Eintraege.
        """
        codes = generate_backup_codes(mfa_pin_user)

        with _record_device_saves() as saves:
            assert verify_backup_code(mfa_pin_user, codes[0]) is True

        assert saves == [
            (False, True, frozenset({"throttling_failure_timestamp", "throttling_failure_count", "last_used_at"}))
        ]


# ---------------------------------------------------------------------------
# verify_totp_or_backup — Eingangs-Normalisierung (3 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerifyTotpOrBackupNormalisesTheInput:
    """Zeile 139 ist der Eingang der Sudo-Mode-Re-Auth — und war unbeobachtet.

    Die F-1-Pins in ``test_mutation_pins_091.py`` fahren ausschliesslich
    gueltige bzw. fremde Tokens ohne Whitespace; weder die Leer-Eingabe noch
    ein gruppierter Token (``"123 456"``) ist dort je durchgelaufen.
    """

    def test_empty_input_never_touches_the_backup_throttle(self, mfa_pin_user):
        """Pin: ``x_verify_totp_or_backup__mutmut_7`` — ``token = (token or "")`` -> ``(token or "XXXX")``

        Leeres Feld heisst: sofort ``False``, ohne jede Nebenwirkung. Der
        Sudo-Mode-View schickt bei leerem Eingabefeld genau ``""``
        (``views/sudo_mode.py``); der Platzhalter des Mutanten (``"XXXX"``)
        laeuft am Leer-Guard (Zeile 140) vorbei bis in ``verify_backup_code``
        und loest dort ein ``throttle_increment`` auf dem Backup-Geraet aus.
        Der Rueckgabewert bleibt in beiden Faellen ``False`` — beobachtbar ist
        die Drossel: Original ``throttling_failure_count == 0`` und
        ``throttling_failure_timestamp is None``, Mutant ``count >= 1``. Damit
        sperrt eine Handvoll versehentlich leer abgeschickter Formulare den
        Nutzer aus seinem eigenen Recovery-Kanal aus.
        """
        generate_backup_codes(mfa_pin_user)

        assert verify_totp_or_backup(mfa_pin_user, "") is False
        assert verify_totp_or_backup(mfa_pin_user, None) is False

        device = StaticDevice.objects.get(user=mfa_pin_user, name=BACKUP_DEVICE_NAME)
        assert device.throttling_failure_count == 0
        assert device.throttling_failure_timestamp is None

    def test_grouped_totp_token_with_spaces_still_verifies(self, mfa_pin_user):
        """Pin: ``x_verify_totp_or_backup__mutmut_8`` — ``.replace(" ", "")`` -> ``.replace("XX XX", "")``
        Pin: ``x_verify_totp_or_backup__mutmut_9`` — ``.replace(" ", "")`` -> ``.replace(" ", "XXXX")``

        Authenticator-Apps zeigen den Token gruppiert an (``"123 456"``); genau
        so landet er beim Copy-Paste im Feld, oft mit Rand-Leerzeichen. Original:
        die Normalisierung macht daraus den nackten Token, das bestaetigte
        Geraet verifiziert ihn -> ``True``. Mutant 8 sucht den Literalstring
        ``"XX XX"`` und entfernt gar nichts, Mutant 9 ersetzt jedes Leerzeichen
        durch ``"XXXX"`` — in beiden Faellen geht ein verstuemmelter Token in
        ``verify_token``, es gibt kein Backup-Geraet als Auffangnetz, und das
        Ergebnis ist ``False`` statt ``True``.
        """
        device = TOTPDevice.objects.create(user=mfa_pin_user, name="totp", confirmed=True, key="a" * 40)
        token = _valid_token_for(device)
        grouped = f"  {token[:3]} {token[3:]}  "

        assert verify_totp_or_backup(mfa_pin_user, grouped) is True
