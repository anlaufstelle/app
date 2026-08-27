"""Direkte Testabdeckung der RunPython-Datenmigration 0165 (Refs #2013).

``0165_client_anonymized_at`` stempelt den Bestand bereits getilgter Personen
nach. Ohne diesen Backfill liefe jeder Alt-Tombstone nach dem Deploy noch
genau ein letztes Mal durch ``anonymize_eligible_soft_deleted_clients`` und
erzeugte eine ueberfluessige ``CLIENT_ANONYMIZED``-Zeile im append-only
AuditLog — also genau das, was das Ticket abstellt.

Muster: ``test_0145_backfill_report_key``.
"""

from datetime import UTC, datetime

from django_test_migrations.contrib.unittest_case import MigratorTestCase

_DELETED_AT = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


class TestMigrate0165BackfillClientAnonymizedAt(MigratorTestCase):
    migrate_from = ("core", "0164_user_access_audit_actions")
    migrate_to = ("core", "0165_client_anonymized_at")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        Client = apps.get_model("core", "Client")

        org = Organization.objects.create(name="Mig-0165-Org")
        fac = Facility.objects.create(organization=org, name="Mig-0165-Stelle")

        def redacted(**kwargs):
            """Eine Zeile genau so, wie ``_redact_client_identity`` sie
            hinterlaesst: ``is_active=False`` und das aus der eigenen UUID
            gebaute Pseudonym."""
            row = Client.objects.create(facility=fac, pseudonym="platzhalter", is_active=False, **kwargs)
            row.pseudonym = f"Gelöscht-{str(row.pk)[:8]}"
            row.save(update_fields=["pseudonym"])
            return row.pk

        # (1) Getilgter Tombstone im Papierkorb — der Regelfall.
        self.tombstone_pk = redacted(is_deleted=True, deleted_at=_DELETED_AT)
        # (2) Von der K-Anon-Bridge getilgt: OHNE Papierkorb, also ohne
        #     ``deleted_at`` — der Stempel faellt auf ``updated_at`` zurueck.
        self.bridge_pk = redacted(is_deleted=False)
        # (3a) AKTIVE Person, die legitim mit dem Marker-Praefix heisst.
        #      ``clean_pseudonym`` prueft nur Eindeutigkeit, der Name ist also
        #      erlaubt. Sie darf NIEMALS gestempelt werden — sonst waere sie
        #      nach ihrer spaeteren regulaeren Loeschung dauerhaft vom
        #      Nachtlauf ausgeschlossen (Art. 17).
        self.lebendig_mit_praefix_pk = Client.objects.create(
            facility=fac,
            pseudonym="Gelöscht-Test",
            is_active=True,
        ).pk
        # (3b) Deaktivierte Person mit Marker-Praefix, aber FREMDER UUID —
        #      kein Ergebnis von ``_redact_client_identity``. Fehlerrichtung:
        #      im Zweifel nicht stempeln.
        self.inaktiv_fremdes_praefix_pk = Client.objects.create(
            facility=fac,
            pseudonym="Gelöscht-fremd999",
            is_active=False,
        ).pk
        # (3c) K-anonymisierte Person: ``is_active=False``, aber ``anon-``.
        self.k_anon_pk = Client.objects.create(
            facility=fac,
            pseudonym="anon-0123456789ab",
            is_active=False,
            k_anonymized=True,
        ).pk
        # (3) Lebende Person — darf keinen Stempel bekommen.
        self.live_pk = Client.objects.create(facility=fac, pseudonym="P-Lebendig").pk
        # (4) Person im Papierkorb, aber noch NICHT getilgt: der naechtliche
        #     Lauf soll sie weiterhin finden.
        self.trashed_pk = Client.objects.create(
            facility=fac,
            pseudonym="P-Papierkorb",
            is_deleted=True,
            deleted_at=_DELETED_AT,
        ).pk

    def _clients(self):
        return self.new_state.apps.get_model("core", "Client")

    def test_tombstone_im_papierkorb_erbt_deleted_at(self):
        assert self._clients().objects.get(pk=self.tombstone_pk).anonymized_at == _DELETED_AT

    def test_bridge_tombstone_ohne_papierkorb_bekommt_ersatzstempel(self):
        client = self._clients().objects.get(pk=self.bridge_pk)
        assert client.anonymized_at is not None
        assert client.anonymized_at == client.updated_at

    def test_lebende_person_bleibt_ungestempelt(self):
        assert self._clients().objects.get(pk=self.live_pk).anonymized_at is None

    def test_aktive_person_mit_marker_praefix_bleibt_ungestempelt(self):
        """Der Kernfall des Cross-Review-Befunds: ``Gelöscht-`` ist ein
        erlaubtes Pseudonym. Ein Stempel hier naehme eine reale Person
        dauerhaft aus der Tilgung."""
        assert self._clients().objects.get(pk=self.lebendig_mit_praefix_pk).anonymized_at is None

    def test_inaktive_person_mit_fremder_uuid_im_pseudonym_bleibt_ungestempelt(self):
        assert self._clients().objects.get(pk=self.inaktiv_fremdes_praefix_pk).anonymized_at is None

    def test_k_anonymisierte_person_bleibt_ungestempelt(self):
        """``k_anonymize_client`` ist nicht ``_redact_client_identity`` — sie
        vergibt ``anon-`` und setzt keinen Art.-17-Tilgungsstempel."""
        assert self._clients().objects.get(pk=self.k_anon_pk).anonymized_at is None

    def test_papierkorb_ohne_tilgung_bleibt_ungestempelt(self):
        """Sonst faende der naechtliche Lauf sie nie — der Papierkorb ist
        noch nicht die Tilgung."""
        assert self._clients().objects.get(pk=self.trashed_pk).anonymized_at is None
