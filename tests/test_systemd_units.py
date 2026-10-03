"""Las unidades de systemd del repo (`systemd/`): lo que no puede cambiar sin avisar.

No hay CI ni se arranca systemd acá: el test fija por texto lo que, si alguien lo
toca sin querer, gasta plata (tope por corrida, `--execute`) o se rompe en el VPS
(usuario, directorio, `Persistent`). Comparte usuario y directorio con el backup
porque es el mismo patrón de instalación.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"


def _leer(nombre: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(SYSTEMD / nombre, encoding="utf-8")
    return parser


@pytest.fixture
def servicio() -> configparser.ConfigParser:
    return _leer("saimc-prefill.service")


@pytest.fixture
def temporizador() -> configparser.ConfigParser:
    return _leer("saimc-prefill.timer")


def test_el_servicio_es_oneshot_y_corre_el_prefill_real_con_tope(servicio) -> None:
    unidad = servicio["Service"]
    comando = unidad["ExecStart"]

    assert unidad["Type"] == "oneshot"
    assert "-m saimc.extraction.prefill" in comando
    assert "--execute" in comando
    assert "--max-cost ${SAIMC_PREFILL_MAX_COST}" in comando
    assert unidad["Environment"] == "SAIMC_PREFILL_MAX_COST=0.15"


def test_el_servicio_usa_el_mismo_usuario_y_directorio_que_el_backup(servicio) -> None:
    backup = _leer("saimc-backup.service")["Service"]

    assert servicio["Service"]["User"] == backup["User"]
    assert servicio["Service"]["WorkingDirectory"] == backup["WorkingDirectory"]


def test_una_corrida_colgada_no_bloquea_el_temporizador_para_siempre(servicio) -> None:
    assert servicio["Service"]["TimeoutStartSec"] == "2h"


def test_el_temporizador_corre_cada_3_horas_y_se_pone_al_dia(temporizador) -> None:
    unidad = temporizador["Timer"]

    assert unidad["OnCalendar"] == "*-*-* 00/3:00:00"
    assert unidad["Persistent"] == "true"
    assert temporizador["Install"]["WantedBy"] == "timers.target"
