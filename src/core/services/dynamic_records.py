"""Ordered carriers shared by dynamic data maintenance and protection paths."""

from core.models import Case, Client, Episode, Event, WorkItem

DynamicDataModel = type[Client] | type[Case] | type[Event] | type[Episode] | type[WorkItem]

# Keep parent records before their children when taking row locks.
DYNAMIC_DATA_MODELS: tuple[DynamicDataModel, ...] = (Client, Case, Event, Episode, WorkItem)


def dynamic_facility_lookup(model: DynamicDataModel) -> str:
    """Return the ORM lookup for a carrier's owning facility."""
    return "case__facility_id" if model is Episode else "facility_id"


def dynamic_client_lookup(model: DynamicDataModel) -> str | None:
    """Return the parent person lookup when the carrier has one."""
    if model is Client:
        return None
    return "case__client_id" if model is Episode else "client_id"


def dynamic_case_lookup(model: DynamicDataModel) -> str | None:
    """Return the parent case lookup when the carrier has one."""
    return "case_id" if model in (Event, Episode) else None
