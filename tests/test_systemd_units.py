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


# --- saimc-marketing: las tarjetas del día (docs/decisions.md, 2026-10-18)


@pytest.fixture
def servicio_marketing() -> configparser.ConfigParser:
    return _leer("saimc-marketing.service")


@pytest.fixture
def temporizador_marketing() -> configparser.ConfigParser:
    return _leer("saimc-marketing.timer")


def test_marketing_es_oneshot_y_escribe_de_verdad(servicio_marketing) -> None:
    unidad = servicio_marketing["Service"]
    comando = unidad["ExecStart"]

    assert unidad["Type"] == "oneshot"
    assert "-m saimc.marketing.daily" in comando
    assert "--execute" in comando
    assert unidad["TimeoutStartSec"] == "10min"


def test_marketing_usa_el_mismo_usuario_y_directorio_que_el_backup(servicio_marketing) -> None:
    backup = _leer("saimc-backup.service")["Service"]

    assert servicio_marketing["Service"]["User"] == backup["User"]
    assert servicio_marketing["Service"]["WorkingDirectory"] == backup["WorkingDirectory"]


def test_marketing_corre_a_las_5_de_la_manana_de_la_habana(temporizador_marketing) -> None:
    unidad = temporizador_marketing["Timer"]

    # El huso va escrito en la unidad: a las 05:00 de Cuba, no del VPS (UTC).
    # Una hora antes del envío de los datos al canal (docs/decisions.md 2026-10-20).
    assert unidad["OnCalendar"] == "*-*-* 05:00:00 America/Havana"
    assert unidad["Persistent"] == "true"
    assert temporizador_marketing["Install"]["WantedBy"] == "timers.target"


# --- saimc-instagram: el lote de la cuenta de Instagram (docs/spec-slice-instagram.md)


@pytest.fixture
def servicio_instagram() -> configparser.ConfigParser:
    return _leer("saimc-instagram.service")


@pytest.fixture
def temporizador_instagram() -> configparser.ConfigParser:
    return _leer("saimc-instagram.timer")


def test_instagram_es_oneshot_y_escribe_de_verdad(servicio_instagram) -> None:
    unidad = servicio_instagram["Service"]
    comando = unidad["ExecStart"]

    assert unidad["Type"] == "oneshot"
    assert "-m saimc.instagram.sync" in comando
    assert "--execute" in comando
    assert unidad["TimeoutStartSec"] == "10min"


def test_instagram_usa_el_mismo_usuario_y_directorio_que_el_backup(servicio_instagram) -> None:
    backup = _leer("saimc-backup.service")["Service"]

    assert servicio_instagram["Service"]["User"] == backup["User"]
    assert servicio_instagram["Service"]["WorkingDirectory"] == backup["WorkingDirectory"]


def test_instagram_corre_cada_3_horas_y_se_pone_al_dia(temporizador_instagram) -> None:
    unidad = temporizador_instagram["Timer"]

    assert unidad["OnCalendar"] == "*-*-* 00/3:30:00"
    assert unidad["Persistent"] == "true"
    assert temporizador_instagram["Install"]["WantedBy"] == "timers.target"


# --- saimc-canal: publicar lo aprobado en el canal de WhatsApp (docs/decisions.md, 2026-10-20)


def test_canal_es_una_plantilla_oneshot_que_manda_de_verdad_el_turno() -> None:
    servicio = _leer("saimc-canal@.service")
    unidad = servicio["Service"]
    comando = unidad["ExecStart"]

    assert unidad["Type"] == "oneshot"
    assert "-m saimc.marketing.channel" in comando
    # %i es el turno: lo pone el temporizador al instanciar la plantilla.
    assert "--slot %i" in comando
    assert "--execute" in comando
    # Si nadie aprobó el borrador, lo aprueba y lo manda (docs/decisions.md, 2026-10-26).
    assert "--auto-approve" in comando
    assert unidad["TimeoutStartSec"] == "10min"


def test_canal_usa_el_mismo_usuario_y_directorio_que_el_backup() -> None:
    servicio = _leer("saimc-canal@.service")["Service"]
    backup = _leer("saimc-backup.service")["Service"]

    assert servicio["User"] == backup["User"]
    assert servicio["WorkingDirectory"] == backup["WorkingDirectory"]


@pytest.mark.parametrize(
    ("turno", "hora"),
    [("datos", "06:00"), ("educativo", "06:30")],
)
def test_cada_turno_del_canal_sale_a_su_hora_de_la_habana(turno: str, hora: str) -> None:
    temporizador = _leer(f"saimc-canal-{turno}.timer")
    unidad = temporizador["Timer"]

    assert unidad["OnCalendar"] == f"*-*-* {hora}:00 America/Havana"
    assert unidad["Unit"] == f"saimc-canal@{turno}.service"
    assert unidad["Persistent"] == "true"
    assert temporizador["Install"]["WantedBy"] == "timers.target"


def test_los_datos_salen_despues_del_lote_que_crea_los_borradores() -> None:
    lote = _leer("saimc-marketing.timer")["Timer"]["OnCalendar"].split()[1]
    datos = _leer("saimc-canal-datos.timer")["Timer"]["OnCalendar"].split()[1]

    assert lote < datos
