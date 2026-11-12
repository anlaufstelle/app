"""Regression tests for active statistics aggregates and range parsing."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from core.models import Client, Event
from core.services.dashboard import (
    event_queryset_for_period,
    parse_statistics_view,
)
from core.services.dashboard.statistics import (
    _aggregate_by_age_cluster,
    _aggregate_contact_stage,
    _aggregate_top_clients,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _make_client(facility, user, **kwargs):
    defaults = {
        "facility": facility,
        "pseudonym": "Pin-Client",
        "contact_stage": Client.ContactStage.IDENTIFIED,
        "age_cluster": Client.AgeCluster.UNKNOWN,
        "created_by": user,
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


def _make_event(facility, doc_type, **kwargs):
    defaults = {
        "facility": facility,
        "document_type": doc_type,
        "occurred_at": timezone.now(),
        "data_json": {},
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _base_qs(facility):
    """Heute-1 … heute deckt alle in dieser Datei erzeugten Ereignisse ab."""
    today = date.today()
    return event_queryset_for_period(facility, today - timedelta(days=1), today)


# ---------------------------------------------------------------------------
# parse_statistics_view
# ---------------------------------------------------------------------------


class TestParseStatisticsViewUnknownModeReturnsEmptyString:
    """Killt ``x_parse_statistics_view__mutmut_16`` (``VIEW_ALIASES.get(raw, None)``)
    und ``__mutmut_18`` (Default-Argument ganz weggelassen, dieselbe Wirkung):
    beide liefern ``None`` statt der laut Docstring garantierten leeren
    Zeichenkette fuer eine fehlende oder unbekannte ``ansicht``/``view``-Angabe
    — Templates verwenden den Rueckgabewert direkt als Wahrheitswert UND als
    Attributwert, ``None`` wuerde dort ``"None"`` rendern."""

    def test_missing_ansicht_returns_empty_string_not_none(self):
        result = parse_statistics_view({})
        assert result == ""
        assert result is not None

    def test_unknown_ansicht_value_returns_empty_string_not_none(self):
        result = parse_statistics_view({"ansicht": "unbekannter-wert"})
        assert result == ""
        assert result is not None


# ---------------------------------------------------------------------------
# _aggregate_contact_stage — Prio 1 (K0-b Personenzaehlung, Refs #1770)
# ---------------------------------------------------------------------------


class TestAggregateContactStagePersonCountsUseCorrectFilterAndDistinct:
    """Killt ``x__aggregate_contact_stage__mutmut_35`` (``anonym_persons``
    zaehlt ohne ``_STAGE_ANONYM_Q``-Filter DISTINCT-Klient:innen der GESAMTEN
    Grundgesamtheit statt nur der anonym-Q-Treffer) und ``__mutmut_38``
    (derselbe Filter komplett weggelassen, dieselbe Wirkung) sowie
    ``__mutmut_62``/``__mutmut_65``/``__mutmut_68`` (``qualifiziert_persons``
    zaehlt ohne ``DISTINCT`` — ``distinct=None``, weggelassen bzw.
    ``distinct=False`` — jede Ereigniszeile statt einmal je Person).

    Beides ist Prio 1: ``persons_by_contact_stage`` ist die
    Suppressions-*Eingabe* des Export-/PDF-Pfads (K0-b) — eine falsch hohe
    Personenzahl kann eine tatsaechlich kleine, schutzbeduerftige Gruppe
    faelschlich ueber der k-Schwelle erscheinen lassen.
    """

    def test_anonym_persons_counts_only_anonym_stage_clients(self, facility, staff_user, doc_type_contact):
        anon_client = _make_client(facility, staff_user, pseudonym="Anon-01")
        ident_client = _make_client(facility, staff_user, pseudonym="Ident-01")
        _make_event(facility, doc_type_contact, client=anon_client, is_anonymous=True)
        _make_event(facility, doc_type_contact, client=ident_client, is_anonymous=False)

        _, persons = _aggregate_contact_stage(_base_qs(facility))

        assert persons["anonym"] == 1

    def test_qualifiziert_persons_counts_distinct_clients_not_event_rows(self, facility, staff_user, doc_type_contact):
        qualified_client = _make_client(
            facility, staff_user, pseudonym="Qual-01", contact_stage=Client.ContactStage.QUALIFIED
        )
        for _ in range(3):
            _make_event(facility, doc_type_contact, client=qualified_client)

        _, persons = _aggregate_contact_stage(_base_qs(facility))

        assert persons["qualifiziert"] == 1


# ---------------------------------------------------------------------------
# _aggregate_by_age_cluster
# ---------------------------------------------------------------------------


class TestAggregateByAgeClusterLabelMapping:
    """Killt ueber EINE exakte Textassertion je Alterscluster eine ganze
    Gruppe von Survivors in ``_aggregate_by_age_cluster``:

    - Dict-KEY-Mutationen in ``age_labels`` (``__mutmut_2``/``_3`` fuer
      ``"u18"``, ``__mutmut_8``/``_10`` fuer ``"18_26"``, ``__mutmut_11``/
      ``_12`` fuer ``"27_plus"``, ``__mutmut_15``/``_16`` fuer ``"unknown"``):
      der geaenderte Key matcht den DB-Wert aus ``client__age_cluster`` nicht
      mehr, ``.get()`` faellt auf den unuebersetzten Rohcode zurueck.
    - Text-VERLUST ``__mutmut_4`` (``_("Unter 18")`` → ``_(None)``): Label
      wird ``None`` statt des uebersetzten Textes.
    - Wortlaut-Varianten derselben gettext-Strings (``__mutmut_5``/``_6``/
      ``_7``/``_14``/``_18``/``_19``/``_20``, XX-Marker bzw. Groß-/
      Kleinschreibung) — durch dieselbe exakte Assertion mitgetoetet, daher
      KEIN ``skip``.
    - Rueckgabedict-KEY-Umbenennung ``__mutmut_48``/``_49`` (``"label"`` →
      ``"XXlabelXX"``/``"LABEL"``): ``row["label"]`` wirft ``KeyError``.
    - ``__mutmut_50`` (``age_labels.get(None, row[...])`` — Lookup-Key immer
      ``None``, Default IMMER genutzt): Label ist fuer JEDEN Cluster der
      Rohcode statt der Uebersetzung, faellt bei jedem Parametrisierungsfall
      dieses Tests auf.
    """

    @pytest.mark.parametrize(
        "age_cluster,expected_label",
        [
            (Client.AgeCluster.U18, "Unter 18"),
            (Client.AgeCluster.AGE_18_26, "18–26"),
            (Client.AgeCluster.AGE_27_PLUS, "27+"),
            (Client.AgeCluster.UNKNOWN, "Unbekannt"),
        ],
    )
    def test_label_is_exact_translated_text_for_every_known_cluster(
        self, facility, staff_user, doc_type_contact, age_cluster, expected_label
    ):
        client = _make_client(facility, staff_user, pseudonym=f"Age-{age_cluster}", age_cluster=age_cluster)
        _make_event(facility, doc_type_contact, client=client)

        rows = _aggregate_by_age_cluster(_base_qs(facility))

        row = next(r for r in rows if r["cluster"] == age_cluster)
        assert row["label"] == expected_label


class TestAggregateByAgeClusterUnknownCodeFallback:
    """Killt ``x__aggregate_by_age_cluster__mutmut_51``/``_52``/``_53``:
    das Default-Argument des ``.get()``-Fallbacks (``None`` statt des
    Rohcodes) fuer Alterscluster-Codes AUSSERHALB der bekannten vier Keys —
    z. B. Altdaten, die ``full_clean()`` nie durchlaufen haben. Der
    dokumentierte Vertrag: unbekannte Codes zeigen sich selbst als Label,
    nicht ``None``.
    """

    def test_out_of_choices_code_falls_back_to_the_raw_code_as_label(self, facility, staff_user, doc_type_contact):
        client = _make_client(facility, staff_user, pseudonym="Legacy-01")
        # QuerySet.update() ruft keine Feld-Validatoren auf — simuliert einen
        # Altbestand mit einem Alterscluster-Code außerhalb der ``choices``
        # (max_length=20, daher kurzer Code).
        Client.objects.filter(pk=client.pk).update(age_cluster="alt_code_x")
        _make_event(facility, doc_type_contact, client=client)

        rows = _aggregate_by_age_cluster(_base_qs(facility))

        row = next(r for r in rows if r["cluster"] == "alt_code_x")
        assert row["label"] == "alt_code_x"


# ---------------------------------------------------------------------------
# _aggregate_top_clients
# ---------------------------------------------------------------------------


class TestAggregateTopClientsLimitIsExactlyFive:
    """Killt ``x__aggregate_top_clients__mutmut_14`` (``[:5]`` → ``[:6]``):
    der Docstring verspricht „Top-5", sechs Klient:innen mit je einem
    Ereignis muessen auf genau fuenf Zeilen begrenzt werden."""

    def test_returns_at_most_five_rows_with_six_distinct_clients(self, facility, staff_user, doc_type_contact):
        for i in range(6):
            client = _make_client(facility, staff_user, pseudonym=f"Top-{i}")
            _make_event(facility, doc_type_contact, client=client)

        rows = _aggregate_top_clients(_base_qs(facility))

        assert len(rows) == 5


# ---------------------------------------------------------------------------
# _query_statistics_mv
