"""Public admin paths must leave an audit trail before exposing content."""

from unittest.mock import patch

import pytest
from django.urls import reverse

from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, DocumentTypeField, EventHistory, Settings

# Unfold's compatibility warning must not preempt the audit behavior checks.
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.filterwarnings(
        "ignore:Overriding get_action_choices.*:django.utils.deprecation.RemovedInDjango70Warning"
    ),
]

ADMIN_MODELS = tuple(anlaufstelle_admin_site._registry)
WRITE_MODELS = ("fieldtemplate", "quicktemplate", "timefilter", "organization", "facility", "settings", "documenttype")


@pytest.fixture
def admin_client(client, super_admin_user):
    client.force_login(super_admin_user)
    return client


def admin_url(model, action="changelist", pk=None):
    args = [pk] if pk is not None else []
    return reverse(f"admin:core_{model}_{action}", args=args)


@pytest.mark.parametrize("model", ADMIN_MODELS, ids=lambda model: model.__name__)
def test_read_audit_failure_denies_every_admin_list(admin_client, model):
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(admin_url(model._meta.model_name))
    assert response.status_code == 403
    assert "cl" not in (response.context or {})


@pytest.mark.parametrize("action", ["change", "delete", "history"])
def test_read_audit_failure_denies_object_views(admin_client, deletion_request, action):
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(admin_url("deletionrequest", action, deletion_request.pk))
    assert response.status_code == 403
    assert deletion_request.reason.encode() not in response.content


def test_read_audit_failure_denies_add_form(admin_client):
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(admin_url("fieldtemplate", "add"))
    assert response.status_code == 403


def test_read_audit_failure_denies_system_view(admin_client):
    with patch("core.views.system.mixins.audit_system_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(reverse("core:system_audit_list"))
    assert response.status_code == 403
    assert "audit_logs" not in (response.context or {})


@pytest.mark.architecture
@pytest.mark.parametrize("model", ADMIN_MODELS, ids=lambda model: model.__name__)
def test_every_registered_admin_has_audit_mixins(model):
    from core.admin import mixins

    registered = anlaufstelle_admin_site._registry[model]
    assert isinstance(registered, mixins.AdminReadAuditMixin)
    assert isinstance(registered, mixins.AdminWriteAuditMixin)


def write_data(model, facility, doc_type_contact):
    common = {"facility": str(facility.pk)}
    return {
        "fieldtemplate": {
            **common,
            "name": "Audit template",
            "slug": "audit-template",
            "field_type": "text",
            "sensitivity": "normal",
        },
        "quicktemplate": {**common, "name": "Audit quick", "document_type": str(doc_type_contact.pk), "sort_order": 0},
        "timefilter": {**common, "label": "Audit shift", "start_time": "08:00", "end_time": "16:00", "sort_order": 0},
        "organization": {"name": "Audit organization"},
        "facility": {"name": "Audit facility", "organization": str(facility.organization_id), "package": "default"},
        "documenttype": {
            **common,
            "name": "Audit type",
            "sensitivity": "normal",
            "entity_type": "event",
            "sort_order": 0,
            "fields-TOTAL_FORMS": 0,
            "fields-INITIAL_FORMS": 0,
        },
    }[model]


def form_data(response):
    form = response.context["adminform"].form
    data = {}
    for key in form.fields:
        field = form[key]
        value = field.value()
        widget = field.field.widget
        if hasattr(widget, "decompress"):
            for suffix, part in zip(widget.widgets_names, widget.decompress(value), strict=True):
                data[key + suffix] = "" if part is None else part
        else:
            data[key] = "" if value is None else value
        if field.field.show_hidden_initial:
            initial_keys = [key + suffix for suffix in widget.widgets_names] if hasattr(widget, "decompress") else [key]
            for initial_key in initial_keys:
                data["initial-" + initial_key] = data[initial_key]
    return data


def post_create(admin_client, model, facility, doc_type_contact):
    if model == "settings":
        Settings.objects.filter(facility=facility).delete()
    data = form_data(admin_client.get(admin_url(model, "add")))
    data.update(
        {"facility": str(facility.pk)} if model == "settings" else write_data(model, facility, doc_type_contact)
    )
    response = admin_client.post(admin_url(model, "add"), data)
    assert response.status_code == 302, (
        response.context["adminform"].form.errors if response.context else response.content
    )
    return response


@pytest.mark.parametrize("model", WRITE_MODELS)
def test_admin_creation_has_single_attributed_audit(admin_client, super_admin_user, facility, doc_type_contact, model):
    post_create(admin_client, model, facility, doc_type_contact)
    entries = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_type__iexact=model)
    entry = entries.get()
    assert entry.actor_id == super_admin_user.pk
    assert entry.user_id == super_admin_user.pk
    assert "changed_fields" in entry.detail


@pytest.mark.parametrize("model", WRITE_MODELS)
def test_admin_update_has_single_audit(admin_client, super_admin_user, facility, doc_type_contact, model):
    if model == "settings":
        obj, _ = Settings.objects.get_or_create(facility=facility)
    else:
        response = post_create(admin_client, model, facility, doc_type_contact)
        assert response.status_code == 302
        obj = (
            next(m for m in ADMIN_MODELS if m._meta.model_name == model).objects.get(
                name=write_data(model, facility, doc_type_contact)["name"]
            )
            if model != "timefilter"
            else next(m for m in ADMIN_MODELS if m._meta.model_name == model).objects.get(label="Audit shift")
        )
    page = admin_client.get(admin_url(model, "change", obj.pk))
    data = form_data(page)
    changed = "facility_full_name" if model == "settings" else "label" if model == "timefilter" else "name"
    data[changed] = "Updated audit marker"
    if model == "documenttype":
        data.update({"fields-TOTAL_FORMS": 0, "fields-INITIAL_FORMS": 0})
    before = set(AuditLog.objects.values_list("pk", flat=True))
    response = admin_client.post(admin_url(model, "change", obj.pk), data)
    assert response.status_code == 302
    entries = (
        AuditLog.objects.exclude(pk__in=before)
        .filter(target_id=str(obj.pk))
        .exclude(action=AuditLog.Action.SYSTEM_VIEW)
    )
    entry = entries.get()
    assert entry.actor_id == super_admin_user.pk
    assert changed in entry.detail["changed_fields"]


@pytest.mark.parametrize("model", [m for m in WRITE_MODELS if m != "settings"])
@pytest.mark.parametrize("bulk", [False, True])
def test_admin_deletion_has_single_audit(admin_client, super_admin_user, facility, doc_type_contact, model, bulk):
    from tests._factories import make_document_type

    cls = next(m for m in ADMIN_MODELS if m._meta.model_name == model)
    if model == "documenttype":
        obj = make_document_type(facility=facility, name="Delete audit type")
    elif model == "facility":
        obj = cls.objects.create(organization=facility.organization, name="Delete audit facility")
    elif model == "organization":
        obj = cls.objects.create(name="Delete audit organization")
    else:
        data = write_data(model, facility, doc_type_contact)
        data["facility"] = facility
        if model == "quicktemplate":
            data["document_type"] = doc_type_contact
        obj = cls.objects.create(**data)
    pk = str(obj.pk)
    if bulk:
        response = admin_client.post(
            admin_url(model), {"action": "delete_selected", "_selected_action": pk, "post": "yes"}
        )
    else:
        response = admin_client.post(admin_url(model, "delete", pk), {"post": "yes"})
    assert response.status_code == 302
    assert not cls.objects.filter(pk=pk).exists()
    entry = AuditLog.objects.get(action=AuditLog.Action.CONFIG_DELETE, target_type=cls.__name__, target_id=pk)
    assert entry.actor_id == super_admin_user.pk


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_inline_mutation_has_single_audit(admin_client, super_admin_user, doc_type_contact, field_template, operation):
    from django.contrib.auth.models import Permission

    # Inlines use Django model permissions in addition to the site's role gate.
    super_admin_user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="core", content_type__model="documenttypefield")
    )
    obj = None
    if operation != "create":
        obj = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=field_template, sort_order=1
        )
    page = admin_client.get(admin_url("documenttype", "change", doc_type_contact.pk))
    data = form_data(page)
    data.update(
        {
            "fields-TOTAL_FORMS": 1,
            "fields-INITIAL_FORMS": 0 if obj is None else 1,
            "fields-0-document_type": str(doc_type_contact.pk),
            "fields-0-field_template": str(field_template.pk),
            "fields-0-sort_order": 2,
        }
    )
    if obj:
        data["fields-0-id"] = str(obj.pk)
    if operation == "delete":
        data["fields-0-DELETE"] = "on"
    response = admin_client.post(admin_url("documenttype", "change", doc_type_contact.pk), data)
    assert response.status_code == 302
    if operation == "delete":
        assert not DocumentTypeField.objects.filter(pk=obj.pk).exists()
    else:
        assert (
            DocumentTypeField.objects.get(document_type=doc_type_contact, field_template=field_template).sort_order == 2
        )
    entry = AuditLog.objects.get(target_type="DocumentTypeField", action=f"config_{operation}")
    assert entry.actor_id == super_admin_user.pk
    assert "changed_fields" in entry.detail


@pytest.mark.parametrize("model", ["event", "eventhistory"])
def test_event_admin_renders_only_metadata(admin_client, sample_event, staff_user, model):
    marker = "PRIVATE_ADMIN_DATA_2527"
    sample_event.data_json = {"private": marker}
    sample_event.search_text = marker
    sample_event.save(update_fields=["data_json", "search_text"])
    history = EventHistory.objects.create(
        event=sample_event,
        changed_by=staff_user,
        action="update",
        data_before={"private": marker},
        data_after={"private": marker},
        field_metadata={"private": {"label": marker}},
    )
    obj = sample_event if model == "event" else history
    response = admin_client.get(admin_url(model, "change", obj.pk))
    assert response.status_code == 200
    assert marker.encode() not in response.content
    form = response.context["adminform"]
    fields = {field for fieldset in form.fieldsets for field in fieldset[1]["fields"]}
    assert not fields.intersection({"data_json", "data_before", "data_after", "field_metadata", "search_text"})


def test_autocomplete_audit_failure_denies_results(admin_client):
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(
            reverse("admin:autocomplete"),
            {"app_label": "core", "model_name": "quicktemplate", "field_name": "document_type", "term": ""},
        )
    assert response.status_code == 403


@pytest.mark.parametrize("model", ADMIN_MODELS, ids=lambda model: model.__name__)
def test_each_admin_list_has_single_read_audit(admin_client, model):
    response = admin_client.get(admin_url(model._meta.model_name))
    assert response.status_code == 200
    entry = AuditLog.objects.get(action=AuditLog.Action.SYSTEM_VIEW, target_type=model.__name__, target_id="")
    assert entry.detail == {"admin_view": "changelist"}


def test_generic_save_failure_rolls_back_mutation(admin_client, facility, doc_type_contact):
    data = form_data(admin_client.get(admin_url("fieldtemplate", "add")))
    data.update(write_data("fieldtemplate", facility, doc_type_contact))
    with patch("core.services.audit.audit_event", side_effect=RuntimeError("Audit unavailable")):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            admin_client.post(admin_url("fieldtemplate", "add"), data)
    from core.models import FieldTemplate

    assert not FieldTemplate.objects.filter(slug="audit-template").exists()


@pytest.mark.parametrize("bulk", [False, True])
def test_generic_delete_failure_rolls_back_mutation(admin_client, facility, bulk):
    from core.models import FieldTemplate

    obj = FieldTemplate.objects.create(facility=facility, name="Keep on failure", field_type="text")
    with patch("core.services.audit.audit_event", side_effect=RuntimeError("Audit unavailable")):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            if bulk:
                admin_client.post(
                    admin_url("fieldtemplate"),
                    {"action": "delete_selected", "_selected_action": str(obj.pk), "post": "yes"},
                )
            else:
                admin_client.post(admin_url("fieldtemplate", "delete", obj.pk), {"post": "yes"})
    assert FieldTemplate.objects.filter(pk=obj.pk).exists()


def test_user_role_update_keeps_single_specialized_audit(admin_client, staff_user, super_admin_user):
    staff_user.date_joined = staff_user.date_joined.replace(microsecond=0)
    staff_user.save(update_fields=["date_joined"])
    page = admin_client.get(admin_url("user", "change", staff_user.pk))
    data = form_data(page)
    data["role"] = "lead"
    before = set(AuditLog.objects.values_list("pk", flat=True))
    response = admin_client.post(admin_url("user", "change", staff_user.pk), data)
    assert response.status_code == 302, (
        response.context["adminform"].form.errors if response.context else response.content
    )
    entries = (
        AuditLog.objects.exclude(pk__in=before)
        .filter(target_id=str(staff_user.pk))
        .exclude(action=AuditLog.Action.SYSTEM_VIEW)
    )
    assert entries.count() == 1, list(entries.values_list("action", "detail"))
    entry = entries.get()
    assert entry.action == AuditLog.Action.USER_ROLE_CHANGED
    assert entry.actor_id == super_admin_user.pk


def test_user_contact_update_has_generic_audit(admin_client, staff_user, super_admin_user):
    staff_user.date_joined = staff_user.date_joined.replace(microsecond=0)
    staff_user.save(update_fields=["date_joined"])
    data = form_data(admin_client.get(admin_url("user", "change", staff_user.pk)))
    data["email"] = "updated@example.test"
    response = admin_client.post(admin_url("user", "change", staff_user.pk), data)
    assert response.status_code == 302, (
        response.context["adminform"].form.errors if response.context else response.content
    )
    entry = AuditLog.objects.get(target_type="User", target_id=str(staff_user.pk), action=AuditLog.Action.CONFIG_UPDATE)
    assert entry.actor_id == super_admin_user.pk
    assert entry.detail == {"changed_fields": ["email"]}


def test_password_form_audit_failure_denies_output(admin_client, staff_user):
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(f"/admin-mgmt/core/user/{staff_user.pk}/password/")
    assert response.status_code == 403


def test_existing_field_activation_has_single_attributed_audit(admin_client, field_template, super_admin_user):
    response = admin_client.post(
        admin_url("fieldtemplate"), {"action": "deactivate_selected", "_selected_action": str(field_template.pk)}
    )
    assert response.status_code == 302
    entry = AuditLog.objects.get(
        target_type="FieldTemplate", target_id=str(field_template.pk), action=AuditLog.Action.CONFIG_UPDATE
    )
    assert entry.actor_id == super_admin_user.pk
    assert entry.detail["changed_fields"] == ["is_active"]


def test_event_admin_search_ignores_payload(admin_client, sample_event):
    marker = "PRIVATE_ADMIN_SEARCH_2527"
    sample_event.data_json = {"private": marker}
    sample_event.search_text = marker
    sample_event.save(update_fields=["data_json", "search_text"])
    response = admin_client.get(admin_url("event"), {"q": marker})
    assert response.status_code == 200
    assert sample_event not in response.context["cl"].queryset


SITE_ROUTE_CLASSIFICATION = {
    "index": True,
    "app_list": False,
    "login": False,
    "logout": False,
    "password_change": True,
    "password_change_done": False,
    "autocomplete": True,
    "jsi18n": False,
    "view_on_site": True,
    "search": True,
    None: False,
}


@pytest.mark.architecture
def test_every_site_endpoint_has_a_reviewed_data_classification():
    from django.urls import URLPattern, URLResolver

    all_routes = anlaufstelle_admin_site.get_urls()
    model_routes = {str(route.pattern) for route in all_routes if isinstance(route, URLResolver)}
    assert model_routes == {
        f"{model._meta.app_label}/{model._meta.model_name}/" for model in anlaufstelle_admin_site._registry
    }
    routes = [route for route in all_routes if isinstance(route, URLPattern)]
    assert len(routes) == len(SITE_ROUTE_CLASSIFICATION)
    assert {route.name for route in routes} == SITE_ROUTE_CLASSIFICATION.keys()


@pytest.mark.parametrize("name", [name for name, has_data in SITE_ROUTE_CLASSIFICATION.items() if has_data])
def test_site_data_routes_fail_closed(admin_client, super_admin_user, name):
    from django.contrib.admin.models import ADDITION, LogEntry
    from django.contrib.contenttypes.models import ContentType

    marker = "PRIVATE_SITE_LOG_2527"
    content_type = ContentType.objects.get_for_model(super_admin_user)
    LogEntry.objects.create(
        user=super_admin_user,
        content_type=content_type,
        object_id=str(super_admin_user.pk),
        object_repr=marker,
        action_flag=ADDITION,
    )
    args = [content_type.pk, super_admin_user.pk] if name == "view_on_site" else []
    query = {"s": super_admin_user.username} if name == "search" else {}
    if name == "autocomplete":
        query = {"app_label": "core", "model_name": "quicktemplate", "field_name": "document_type", "term": ""}
    with patch("core.services.audit.audit_admin_view", side_effect=RuntimeError("Audit unavailable")):
        response = admin_client.get(reverse(f"admin:{name}", args=args), query)
    assert response.status_code == 403
    assert marker.encode() not in response.content
    assert "app_list" not in (response.context or {})


def test_site_index_records_read_before_showing_recent_actions(admin_client, super_admin_user):
    response = admin_client.get(reverse("admin:index"))
    assert response.status_code == 200
    entry = AuditLog.objects.get(action=AuditLog.Action.SYSTEM_VIEW, target_type="AdminSite")
    assert entry.user_id == super_admin_user.pk
    assert entry.detail["admin_view"] == "index"


@pytest.mark.parametrize(
    ("name", "expected_status"),
    [
        ("index", 200),
        ("app_list", 200),
        ("login", 200),
        ("logout", 405),
        ("password_change_done", 200),
        ("autocomplete", 200),
        ("search", 200),
        ("view_on_site", 404),
        ("jsi18n", 200),
        (None, 404),
    ],
)
def test_site_routes_render_expected_get_response(admin_client, super_admin_user, name, expected_status):
    from django.contrib.contenttypes.models import ContentType

    if name == "login":
        admin_client.logout()
    args = []
    kwargs = {}
    query = {}
    if name == "app_list":
        kwargs = {"app_label": "core"}
    elif name == "view_on_site":
        args = [ContentType.objects.get_for_model(super_admin_user).pk, super_admin_user.pk]
    elif name == "autocomplete":
        query = {"app_label": "core", "model_name": "quicktemplate", "field_name": "document_type", "term": ""}
    elif name == "search":
        query = {"s": super_admin_user.username}
    url = reverse(f"admin:{name}", args=args, kwargs=kwargs) if name is not None else "/admin-mgmt/missing-route/"
    response = admin_client.get(url, query)
    assert response.status_code == expected_status
    if expected_status != 405:
        assert response.content
    if name == "login":
        assert b'name="username"' in response.content
        assert b'name="password"' in response.content


def test_site_own_password_change_get_renders_form(admin_client):
    response = admin_client.get(reverse("admin:password_change"))
    assert response.status_code == 200
    assert not response.context["form"].is_bound
    for field in ("old_password", "new_password1", "new_password2"):
        assert f'name="{field}"'.encode() in response.content


def test_site_own_password_change_invalid_post_keeps_password_without_write_audit(admin_client, super_admin_user):
    old_hash = super_admin_user.password
    before = set(AuditLog.objects.values_list("pk", flat=True))
    response = admin_client.post(
        reverse("admin:password_change"),
        {
            "old_password": "incorrect-old-password",
            "new_password1": "Cyclone-9182-new",
            "new_password2": "Cyclone-9182-new",
        },
    )
    assert response.status_code == 200
    form = response.context["form"]
    assert form.is_bound
    assert set(form.errors) == {"old_password"}
    assert form.errors.as_data()["old_password"][0].code == "password_incorrect"
    for field in ("old_password", "new_password1", "new_password2"):
        assert f'name="{field}"'.encode() in response.content
    assert str(form.errors["old_password"][0]).encode() in response.content
    super_admin_user.refresh_from_db()
    assert super_admin_user.password == old_hash
    assert super_admin_user.check_password("testpass123")
    assert not super_admin_user.check_password("Cyclone-9182-new")
    entries = AuditLog.objects.exclude(pk__in=before)
    assert entries.exists()
    assert not entries.exclude(action=AuditLog.Action.SYSTEM_VIEW).exists()


def test_site_own_password_change_is_audited(admin_client, super_admin_user):
    new_password = "Cyclone-9182-new"
    response = admin_client.post(
        reverse("admin:password_change"),
        {
            "old_password": "testpass123",
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )
    assert response.status_code == 302
    super_admin_user.refresh_from_db()
    assert super_admin_user.check_password(new_password)
    entry = AuditLog.objects.get(action=AuditLog.Action.CREDENTIALS_REVOKED, user=super_admin_user)
    assert entry.actor_id == super_admin_user.pk
    assert entry.detail == {"reason": "password_change"}
    assert response.url == reverse("admin:password_change_done")
    done = admin_client.get(response.url)
    assert done.status_code == 200
    assert str(done.context["title"]).encode() in done.content
    assert b'name="old_password"' not in done.content


def test_site_own_password_change_rolls_back_on_audit_failure(admin_client, super_admin_user):
    old_hash = super_admin_user.password
    with patch("core.services.security.credentials.log_audit_event", side_effect=RuntimeError("Audit unavailable")):
        with pytest.raises(RuntimeError, match="Audit unavailable"):
            admin_client.post(
                reverse("admin:password_change"),
                {
                    "old_password": "testpass123",
                    "new_password1": "Cyclone-9182-new",
                    "new_password2": "Cyclone-9182-new",
                },
            )
    super_admin_user.refresh_from_db()
    assert super_admin_user.password == old_hash
    assert not AuditLog.objects.filter(action=AuditLog.Action.CREDENTIALS_REVOKED).exists()
