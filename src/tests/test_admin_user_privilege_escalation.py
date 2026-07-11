"""A2.1 — Admin-Privilege-Escalation facility_admin -> super_admin abdichten.

Refs #1020 (Tracker #1016, Workstream A2.1).

Bedrohung: ``/admin-mgmt/`` ist fuer super_admin UND facility_admin offen. Der
``UserAdmin`` gab das ``role``-Feld bisher als freies Choice-Feld inkl.
``super_admin`` aus und das ``facility``-FK ungescoped — ein facility_admin
konnte sich (oder einen beliebigen User der eigenen Facility) auf super_admin
eskalieren bzw. einen User einer fremden Facility zuweisen.

Verteidigung in mehreren Schichten:
- ``AnlaufstelleAdminSite.assignable_roles`` als Single Source of Truth.
- ``UserAdmin.formfield_for_choice_field('role')`` -> UI-Restriktion + serverseitige
  Choice-Validierung.
- ``UserAdmin.formfield_for_foreignkey('facility')`` -> nur eigene Facility.
- ``UserAdmin.save_model`` -> Defense-in-Depth-Guard gegen umgangene Form-Validierung.
- ``UserAdmin.has_change/delete_permission`` -> facility_admin darf super_admin-Objekte
  nicht verwalten (schliesst De-Eskalations-Randfall).
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory

from core.admin import UserAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Facility, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def other_facility(organization):
    return Facility.objects.create(organization=organization, name="Andere Stelle")


def _request(rf, user, method="get"):
    """Admin-Request analog zur FacilityScopeMiddleware (setzt current_facility)."""
    request = getattr(rf, method)("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return UserAdmin(User, anlaufstelle_admin_site)


def _role_choice_values(admin_cls, request, obj):
    form_cls = admin_cls.get_form(request, obj=obj, change=True)
    return [value for value, _label in form_cls.base_fields["role"].choices]


# ---------------------------------------------------------------------------
# assignable_roles (Single Source of Truth)
# ---------------------------------------------------------------------------
class TestAssignableRoles:
    def test_super_admin_may_assign_all_roles(self, rf, super_admin_user):
        request = _request(rf, super_admin_user)
        values = [v for v, _ in anlaufstelle_admin_site.assignable_roles(request)]
        assert values == [v for v, _ in User.Role.choices]
        assert User.Role.SUPER_ADMIN in values

    def test_facility_admin_may_not_assign_super_admin(self, rf, admin_user):
        request = _request(rf, admin_user)
        values = [v for v, _ in anlaufstelle_admin_site.assignable_roles(request)]
        assert User.Role.SUPER_ADMIN not in values
        assert set(values) == {
            User.Role.FACILITY_ADMIN,
            User.Role.LEAD,
            User.Role.STAFF,
            User.Role.ASSISTANT,
        }


# ---------------------------------------------------------------------------
# role-Choices im echten Admin-Form (formfield_for_choice_field)
# ---------------------------------------------------------------------------
class TestRoleFieldChoices:
    def test_facility_admin_role_choices_exclude_super_admin(self, rf, admin_user, staff_user):
        values = _role_choice_values(_admin(), _request(rf, admin_user), staff_user)
        assert User.Role.SUPER_ADMIN not in values

    def test_super_admin_role_choices_include_super_admin(self, rf, super_admin_user, staff_user):
        values = _role_choice_values(_admin(), _request(rf, super_admin_user), staff_user)
        assert User.Role.SUPER_ADMIN in values

    def test_facility_admin_role_field_rejects_super_admin_value(self, rf, admin_user, staff_user):
        """Manipulierter POST role=super_admin scheitert an der Form-Validierung."""
        form_cls = _admin().get_form(_request(rf, admin_user), obj=staff_user, change=True)
        with pytest.raises(ValidationError):
            form_cls.base_fields["role"].clean(User.Role.SUPER_ADMIN)

    def test_super_admin_role_field_accepts_super_admin_value(self, rf, super_admin_user, staff_user):
        form_cls = _admin().get_form(_request(rf, super_admin_user), obj=staff_user, change=True)
        assert form_cls.base_fields["role"].clean(User.Role.SUPER_ADMIN) == User.Role.SUPER_ADMIN


# ---------------------------------------------------------------------------
# facility-FK-Scope (formfield_for_foreignkey)
# ---------------------------------------------------------------------------
class TestFacilityFieldScope:
    def test_facility_admin_facility_choices_limited_to_own(self, rf, admin_user, facility, other_facility):
        facility_field = User._meta.get_field("facility")
        formfield = _admin().formfield_for_foreignkey(facility_field, _request(rf, admin_user))
        facilities = list(formfield.queryset)
        assert facility in facilities
        assert other_facility not in facilities

    def test_super_admin_facility_choices_unrestricted(self, rf, super_admin_user, facility, other_facility):
        facility_field = User._meta.get_field("facility")
        formfield = _admin().formfield_for_foreignkey(facility_field, _request(rf, super_admin_user))
        facilities = list(formfield.queryset)
        assert facility in facilities
        assert other_facility in facilities


# ---------------------------------------------------------------------------
# save_model Defense-in-Depth-Guard
# ---------------------------------------------------------------------------
class TestSaveModelGuard:
    def test_facility_admin_cannot_create_super_admin(self, rf, admin_user):
        obj = User(
            username="escalated",
            email="escalated@example.de",
            role=User.Role.SUPER_ADMIN,
            facility=admin_user.facility,
        )
        with pytest.raises(PermissionDenied):
            _admin().save_model(_request(rf, admin_user, method="post"), obj, form=None, change=False)
        assert not User.objects.filter(username="escalated").exists()

    def test_facility_admin_cannot_elevate_existing_user(self, rf, admin_user, staff_user):
        staff_user.role = User.Role.SUPER_ADMIN
        with pytest.raises(PermissionDenied):
            _admin().save_model(_request(rf, admin_user, method="post"), staff_user, form=None, change=True)
        staff_user.refresh_from_db()
        assert staff_user.role == User.Role.STAFF

    def test_super_admin_may_assign_super_admin_role(self, rf, super_admin_user, staff_user):
        staff_user.role = User.Role.SUPER_ADMIN
        _admin().save_model(_request(rf, super_admin_user, method="post"), staff_user, form=None, change=True)
        staff_user.refresh_from_db()
        assert staff_user.role == User.Role.SUPER_ADMIN


# ---------------------------------------------------------------------------
# Objekt-Level-Permissions (super_admin-Konten unantastbar fuer facility_admin)
# ---------------------------------------------------------------------------
class TestObjectLevelPermissions:
    def test_facility_admin_has_no_change_permission_on_super_admin(self, rf, admin_user, super_admin_user):
        request = _request(rf, admin_user)
        admin_cls = _admin()
        assert admin_cls.has_change_permission(request, super_admin_user) is False
        assert admin_cls.has_delete_permission(request, super_admin_user) is False

    def test_facility_admin_keeps_change_permission_on_staff(self, rf, admin_user, staff_user):
        assert _admin().has_change_permission(_request(rf, admin_user), staff_user) is True

    def test_super_admin_keeps_change_permission_on_super_admin(self, rf, super_admin_user):
        request = _request(rf, super_admin_user)
        assert _admin().has_change_permission(request, super_admin_user) is True


# ---------------------------------------------------------------------------
# Integration: get_queryset-Invariante (super_admin nicht im facility_admin-Scope)
# ---------------------------------------------------------------------------
class TestChangePageAccessInvariant:
    def test_facility_admin_cannot_open_super_admin_change_page(self, client, admin_user, super_admin_user):
        client.force_login(admin_user)
        url = f"/admin-mgmt/core/user/{super_admin_user.pk}/change/"
        response = client.get(url, follow=False)
        # 404 (get_queryset filtert) oder 302 (Djangos does-not-exist-Redirect) -> Zugriff verweigert.
        assert response.status_code in (302, 404)


# ---------------------------------------------------------------------------
# Django-native Permission-Felder (is_staff/is_superuser/groups/user_permissions)
# ---------------------------------------------------------------------------
# Refs #1371 — UserAdmin erbte Djangos "Berechtigungen"-Fieldset ungefiltert;
# ein facility_admin (und selbst super_admin) konnte is_superuser=True setzen.
# App-seitig folgenlos (Autorisierung laeuft ausschliesslich ueber `role`, der
# Admin-Zugang am Rollen-Gate der Custom-AdminSite statt an is_staff), aber
# latente Eskalation und ein Least-Privilege-Verstoss — und fuer is_superuser ein
# Widerspruch zur zur Laufzeit erzwungenen #1271/#1297-Invariante (kein App-User
# traegt is_superuser=True). Fix: `UserAdmin.get_fieldsets` entfernt
# is_staff/is_superuser/groups/user_permissions fuer ALLE Editoren (auch
# super_admin) vollstaendig aus Fieldset UND Form (nicht nur readonly) — der
# legitime Verwaltungsbedarf ist null. `get_form` berechnet seine Felder daraus,
# wodurch ein POST-Smuggling-Versuch auch fuer super_admin ins Leere laeuft.
def _date_joined_post_data(form_cls, user):
    """POST-Daten fuer das SplitDateTimeField `date_joined` ueber die Widget-
    eigene decompress/format_value-Rundreise erzeugen -- robust gegen
    Locale-/Input-Format-Details, die hier nicht Testgegenstand sind."""
    widget = form_cls.base_fields["date_joined"].widget
    date_part, time_part = widget.decompress(user.date_joined)
    return {
        "date_joined_0": widget.widgets[0].format_value(date_part),
        "date_joined_1": widget.widgets[1].format_value(time_part),
    }


class TestPermissionFieldsHidden:
    _RESTRICTED = ("is_staff", "is_superuser", "groups", "user_permissions")

    def test_facility_admin_fieldsets_exclude_permission_fields(self, rf, admin_user, staff_user):
        fieldsets = _admin().get_fieldsets(_request(rf, admin_user), obj=staff_user)
        visible = {f for _name, options in fieldsets for f in options.get("fields", ())}
        assert visible.isdisjoint(self._RESTRICTED)
        # is_active bleibt: Accounts sperren gehoert zur Facility-Verwaltung.
        assert "is_active" in visible

    def test_super_admin_fieldsets_also_exclude_permission_fields(self, rf, super_admin_user, staff_user):
        """Auch super_admin sieht die vier Django-Permission-Felder nicht (Refs #1371).

        Die App nutzt Django-Perms nicht und die #1271/#1297-Invariante verbietet
        is_superuser=True fuer App-User — der legitime Verwaltungsbedarf ist null.
        """
        fieldsets = _admin().get_fieldsets(_request(rf, super_admin_user), obj=staff_user)
        visible = {f for _name, options in fieldsets for f in options.get("fields", ())}
        assert visible.isdisjoint(self._RESTRICTED)
        # is_active bleibt auch fuer super_admin: Accounts sperren gehoert zur Verwaltung.
        assert "is_active" in visible

    def test_facility_admin_form_has_no_permission_fields(self, rf, admin_user, staff_user):
        form_cls = _admin().get_form(_request(rf, admin_user), obj=staff_user, change=True)
        assert set(form_cls.base_fields).isdisjoint(self._RESTRICTED)

    def test_super_admin_form_also_has_no_permission_fields(self, rf, super_admin_user, staff_user):
        """Auch das super_admin-Editorformular traegt die vier Felder nicht (Refs #1371)."""
        form_cls = _admin().get_form(_request(rf, super_admin_user), obj=staff_user, change=True)
        assert set(form_cls.base_fields).isdisjoint(self._RESTRICTED)

    def test_facility_admin_post_smuggled_is_superuser_has_no_effect(self, rf, admin_user, staff_user):
        """POST-Smuggling: is_superuser/is_staff im Body ohne zugehoeriges
        Formfeld aendern trotz gueltigem, sonst vollstaendigem POST nichts --
        das Form-Feld existiert serverseitig gar nicht erst (Refs #1371)."""
        staff_user.is_staff = False
        staff_user.is_superuser = False
        staff_user.save(update_fields=["is_staff", "is_superuser"])

        request = _request(rf, admin_user, method="post")
        form_cls = _admin().get_form(request, obj=staff_user, change=True)
        data = {
            "username": staff_user.username,
            "role": staff_user.role,
            "facility": str(staff_user.facility_id or ""),
            "preferred_language": staff_user.preferred_language,
            "is_active": "on",
            **_date_joined_post_data(form_cls, staff_user),
            # Smuggling: Felder, fuer die dieses Form-Objekt kein Feld besitzt.
            "is_superuser": "on",
            "is_staff": "on",
        }
        form = form_cls(data=data, instance=staff_user)
        assert form.is_valid(), form.errors
        saved = form.save(commit=False)
        assert saved.is_superuser is False
        assert saved.is_staff is False

    def test_super_admin_post_smuggled_is_superuser_has_no_effect(self, rf, super_admin_user, staff_user):
        """Auch super_admin kann is_superuser/is_staff nicht per POST-Smuggling setzen.

        Das ins Gegenteil gedrehte Gegenprobe (Refs #1371, adversariales Review
        2026-07-11): Die vier Django-Permission-Felder existieren serverseitig auch
        im super_admin-Formular nicht — ein POST ``is_superuser=on``/``is_staff=on``
        ohne zugehoeriges Formfeld bleibt wirkungslos. Der legitime Verwaltungsbedarf
        ist null (App nutzt Django-Perms nicht; is_superuser=True per #1271/#1297
        ganz verboten)."""
        staff_user.is_staff = False
        staff_user.is_superuser = False
        staff_user.save(update_fields=["is_staff", "is_superuser"])

        request = _request(rf, super_admin_user, method="post")
        form_cls = _admin().get_form(request, obj=staff_user, change=True)
        data = {
            "username": staff_user.username,
            "role": staff_user.role,
            "facility": str(staff_user.facility_id or ""),
            "preferred_language": staff_user.preferred_language,
            "is_active": "on",
            **_date_joined_post_data(form_cls, staff_user),
            # Smuggling: Felder, fuer die auch dieses super_admin-Form kein Feld besitzt.
            "is_superuser": "on",
            "is_staff": "on",
        }
        form = form_cls(data=data, instance=staff_user)
        assert form.is_valid(), form.errors
        saved = form.save(commit=False)
        assert saved.is_superuser is False
        assert saved.is_staff is False

    def test_super_admin_save_preserves_no_django_superuser_invariant(self, rf, super_admin_user, staff_user):
        """Nach einem echten Admin-Save eines super_admin-Editors bleibt die
        #1271/#1297-Invariante erhalten: KEIN App-User traegt is_superuser=True.

        Voller Speicher-Roundtrip (commit=True) mit einem POST, der is_superuser/
        is_staff einzuschmuggeln versucht — danach muss
        ``User.objects.filter(is_superuser=True).exists()`` False bleiben (Refs #1371)."""
        staff_user.is_staff = False
        staff_user.is_superuser = False
        staff_user.save(update_fields=["is_staff", "is_superuser"])
        assert User.objects.filter(is_superuser=True).exists() is False

        request = _request(rf, super_admin_user, method="post")
        admin_cls = _admin()
        form_cls = admin_cls.get_form(request, obj=staff_user, change=True)
        data = {
            "username": staff_user.username,
            "role": staff_user.role,
            "facility": str(staff_user.facility_id or ""),
            "preferred_language": staff_user.preferred_language,
            "is_active": "on",
            **_date_joined_post_data(form_cls, staff_user),
            "is_superuser": "on",
            "is_staff": "on",
        }
        form = form_cls(data=data, instance=staff_user)
        assert form.is_valid(), form.errors
        admin_cls.save_model(request, form.save(commit=False), form, change=True)

        staff_user.refresh_from_db()
        assert staff_user.is_superuser is False
        assert staff_user.is_staff is False
        assert User.objects.filter(is_superuser=True).exists() is False
