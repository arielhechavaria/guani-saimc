"""El estado del TUI sale del mismo flujo que `/control/flujo`, etapa por etapa."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.models import SourceType
from saimc.tui import status
from saimc.tui.registry import COMMANDS, GROUPS
from saimc.web import control_flow
from saimc.web.control_flow import FlowInputs

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _stages(**kwargs) -> tuple[status.Stage, ...]:
    return status.stages_from_view(control_flow.build_view(FlowInputs(now=NOW, **kwargs)))


def test_there_is_one_stage_per_flow_node_in_flow_order() -> None:
    assert [s.group for s in _stages()] == [
        "Ingesta",
        "Pre-cálculo",
        "Carril 0",
        "Carril 1",
        "Cola",
        "Tablero",
        "Marketing",
    ]


def test_every_stage_group_is_a_registry_group_that_has_commands() -> None:
    commands_by_group = {c.group for c in COMMANDS}
    for group in status.STAGE_GROUPS.values():
        assert group in GROUPS
        assert group in commands_by_group, group


def test_the_ingest_stage_counts_the_last_24_hours() -> None:
    intake = [(NOW - timedelta(hours=h), SourceType.WHATSAPP_CHANNEL) for h in (1, 2, 3)]
    ingest = _stages(intake=intake, last_ingest_at=NOW - timedelta(hours=1))[0]
    assert ingest.summary == "3 · 24 h"
    assert ingest.foot == "hace 1 h"
    assert "Por WhatsApp: 3" in ingest.details[0]


def test_a_stage_without_a_rule_in_the_strip_is_off_not_ok() -> None:
    ingest = _stages()[0]
    assert ingest.health == "off"
    assert ingest.health_label == "Apagado"


def test_marketing_lists_its_steps() -> None:
    marketing = _stages(marketing={"draft": 2, "approved": 1, "published": 5})[-1]
    assert marketing.details == ("2 borradores · 1 aprobadas · 5 publicadas",)


def test_snapshot_finds_a_stage_by_group() -> None:
    snapshot = status.Snapshot(stages=_stages())
    stage = snapshot.stage("Cola")
    assert stage is not None
    assert stage.group == "Cola"
    assert snapshot.stage("Backup") is None


def test_read_builds_the_snapshot_from_the_database(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # El único chequeo de red de la franja es WAHA: sin base_url no sale a ninguna parte.
    monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "")
    snapshot = status.read(session, Settings())
    assert snapshot.error == ""
    assert len(snapshot.stages) == 7
    assert snapshot.stage("Tablero") is not None


def test_load_returns_the_reason_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(status, "get_session_factory", broken)
    snapshot = status.load()
    assert snapshot.stages == ()
    assert "base caída" in snapshot.error
