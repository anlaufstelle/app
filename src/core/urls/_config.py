"""Formular-Konfigurations-Routen (``/config/…``, facility_admin only).

Ausgelagert aus ``core/urls.py`` (Refs #1835) — siehe Modul-Docstring von
``core/urls/_system.py`` zur ``include()``-Namensraum-Freiheit.
"""

from django.urls import path

from core.views.config.conditions import (
    ConditionBoardView,
    ConditionCreateView,
    ConditionDeleteView,
    ConditionOperatorInputView,
    ConditionUpdateView,
    ConditionValueInputView,
)
from core.views.config.document_types import (
    ConfigDocTypeFieldAddView,
    ConfigDocTypeFieldRemoveView,
    ConfigDocumentTypeCreateView,
    ConfigDocumentTypeDeleteView,
    ConfigDocumentTypeDetailView,
    ConfigDocumentTypeListView,
    ConfigDocumentTypeUpdateView,
)
from core.views.config.field_groups import (
    ConfigFieldGroupCreateView,
    ConfigFieldGroupDeleteView,
    ConfigFieldGroupUpdateView,
)
from core.views.config.field_templates import (
    ConfigFieldTemplateCreateView,
    ConfigFieldTemplateDeleteView,
    ConfigFieldTemplateListView,
    ConfigFieldTemplateToggleActiveView,
    ConfigFieldTemplateUpdateView,
)
from core.views.config.reorder import (
    ConfigMoveFieldView,
    ConfigReorderBlocksView,
    ConfigReorderFieldsView,
)

urlpatterns = [
    # Config — Formular-Konfiguration (M0, Refs #1056): facility_admin only.
    path("config/document-types/", ConfigDocumentTypeListView.as_view(), name="config_doctype_list"),
    path("config/document-types/new/", ConfigDocumentTypeCreateView.as_view(), name="config_doctype_create"),
    path("config/document-types/<uuid:pk>/", ConfigDocumentTypeDetailView.as_view(), name="config_doctype_detail"),
    path("config/document-types/<uuid:pk>/edit/", ConfigDocumentTypeUpdateView.as_view(), name="config_doctype_update"),
    path(
        "config/document-types/<uuid:pk>/delete/",
        ConfigDocumentTypeDeleteView.as_view(),
        name="config_doctype_delete",
    ),
    # P6: Feld-Zuordnung + FieldGroup-Verwaltung im DocumentType-Detail (HTMX-Board).
    path(
        "config/document-types/<uuid:pk>/fields/add/",
        ConfigDocTypeFieldAddView.as_view(),
        name="config_doctype_field_add",
    ),
    path(
        "config/document-types/<uuid:pk>/fields/<uuid:dtf_pk>/remove/",
        ConfigDocTypeFieldRemoveView.as_view(),
        name="config_doctype_field_remove",
    ),
    path(
        "config/document-types/<uuid:pk>/groups/new/",
        ConfigFieldGroupCreateView.as_view(),
        name="config_fieldgroup_create",
    ),
    path(
        "config/document-types/<uuid:pk>/groups/<uuid:group_pk>/edit/",
        ConfigFieldGroupUpdateView.as_view(),
        name="config_fieldgroup_update",
    ),
    path(
        "config/document-types/<uuid:pk>/groups/<uuid:group_pk>/delete/",
        ConfigFieldGroupDeleteView.as_view(),
        name="config_fieldgroup_delete",
    ),
    # P7 / #1713: Drag-and-Drop-Batch-Reorder (globale Block-Sequenz, Felder,
    # Feld verschieben).
    path(
        "config/document-types/<uuid:pk>/reorder/blocks/",
        ConfigReorderBlocksView.as_view(),
        name="config_reorder_blocks",
    ),
    path(
        "config/document-types/<uuid:pk>/reorder/fields/",
        ConfigReorderFieldsView.as_view(),
        name="config_reorder_fields",
    ),
    path(
        "config/document-types/<uuid:pk>/reorder/move-field/",
        ConfigMoveFieldView.as_view(),
        name="config_move_field",
    ),
    # M1 P6 (Refs #1057): Regel-Editor für bedingte Feldsichtbarkeit im
    # DocumentType-Detail (HTMX-Board). facility_admin only.
    path(
        "config/document-types/<uuid:pk>/conditions/new/",
        ConditionCreateView.as_view(),
        name="config_condition_create",
    ),
    path(
        "config/document-types/<uuid:pk>/conditions/<uuid:condition_pk>/edit/",
        ConditionUpdateView.as_view(),
        name="config_condition_update",
    ),
    path(
        "config/document-types/<uuid:pk>/conditions/<uuid:condition_pk>/delete/",
        ConditionDeleteView.as_view(),
        name="config_condition_delete",
    ),
    path(
        "config/document-types/<uuid:pk>/conditions/value-input/",
        ConditionValueInputView.as_view(),
        name="config_condition_value_input",
    ),
    path(
        "config/document-types/<uuid:pk>/conditions/board/",
        ConditionBoardView.as_view(),
        name="config_condition_board",
    ),
    path(
        "config/document-types/<uuid:pk>/conditions/operator-input/",
        ConditionOperatorInputView.as_view(),
        name="config_condition_operator_input",
    ),
    path("config/field-templates/", ConfigFieldTemplateListView.as_view(), name="config_fieldtemplate_list"),
    path("config/field-templates/new/", ConfigFieldTemplateCreateView.as_view(), name="config_fieldtemplate_create"),
    path(
        "config/field-templates/<uuid:pk>/edit/",
        ConfigFieldTemplateUpdateView.as_view(),
        name="config_fieldtemplate_update",
    ),
    path(
        "config/field-templates/<uuid:pk>/delete/",
        ConfigFieldTemplateDeleteView.as_view(),
        name="config_fieldtemplate_delete",
    ),
    path(
        "config/field-templates/<uuid:pk>/toggle-active/",
        ConfigFieldTemplateToggleActiveView.as_view(),
        name="config_fieldtemplate_toggle",
    ),
]
