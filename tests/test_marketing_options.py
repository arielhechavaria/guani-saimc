"""Bloques y textos sobrescribibles de las tarjetas (`saimc.marketing.options`).

La URL puede apagar bloques con interruptor y reescribir dos textos; nunca
puede tocar una cifra ni apagar un bloque que hace que la pieza diga algo.
"""

import pytest

from saimc.marketing.context import BY_SLUG
from saimc.marketing.options import CTA_MAX, SUBTITLE_MAX, parse_options

CON_BLOQUES = ("dato-del-dia", "como-se-movio", "de-donde-sale")


@pytest.mark.parametrize("slug", CON_BLOQUES)
def test_titulo_y_cifra_principal_no_tienen_interruptor(slug: str) -> None:
    fijos = {b.slug for b in BY_SLUG[slug].blocks if not b.toggleable}

    assert "titulo" in fijos
    assert fijos & {"rango", "cambio"}


@pytest.mark.parametrize("slug", CON_BLOQUES)
def test_cada_tarjeta_con_datos_tiene_bloques_con_interruptor(slug: str) -> None:
    assert [b for b in BY_SLUG[slug].blocks if b.toggleable]


def test_las_tarjetas_de_branding_no_declaran_bloques() -> None:
    assert BY_SLUG["carrusel-cierre"].blocks == ()


def test_sin_parametros_todo_queda_como_por_defecto() -> None:
    kind = BY_SLUG["dato-del-dia"]

    opciones = parse_options(kind, {})

    assert opciones.show == {b.slug: b.default for b in kind.blocks}
    assert opciones.subtitle == "" and opciones.cta == ""


def test_b_slug_0_apaga_un_bloque_con_interruptor() -> None:
    opciones = parse_options(BY_SLUG["dato-del-dia"], {"b_barra": "0"})

    assert opciones.show["barra"] is False
    assert opciones.show["mediana"] is True


def test_un_bloque_sin_interruptor_no_se_apaga_aunque_lo_pidan() -> None:
    opciones = parse_options(BY_SLUG["dato-del-dia"], {"b_titulo": "0", "b_rango": "0"})

    assert opciones.show["titulo"] is True
    assert opciones.show["rango"] is True


def test_un_bloque_que_la_tarjeta_no_tiene_se_ignora() -> None:
    opciones = parse_options(BY_SLUG["dato-del-dia"], {"b_anuncios": "0", "b_inventado": "0"})

    assert "anuncios" not in opciones.show and "inventado" not in opciones.show


def test_un_valor_raro_no_apaga_nada() -> None:
    opciones = parse_options(BY_SLUG["dato-del-dia"], {"b_barra": "no", "b_mediana": ""})

    assert opciones.show["barra"] is True and opciones.show["mediana"] is True


def test_los_textos_se_limpian_y_respetan_el_tope() -> None:
    opciones = parse_options(
        BY_SLUG["dato-del-dia"],
        {"subtitle": "  hola \n  mundo  ", "cta": "x" * (CTA_MAX + 50)},
    )

    assert opciones.subtitle == "hola mundo"
    assert len(opciones.cta) == CTA_MAX


def test_el_subtitulo_tiene_su_propio_tope() -> None:
    opciones = parse_options(BY_SLUG["dato-del-dia"], {"subtitle": "y" * 500})

    assert len(opciones.subtitle) == SUBTITLE_MAX


def test_los_textos_se_ignoran_en_una_tarjeta_sin_bloques() -> None:
    opciones = parse_options(BY_SLUG["carrusel-cierre"], {"subtitle": "hola", "cta": "chau"})

    assert opciones.subtitle == "" and opciones.cta == ""


def test_la_consulta_vuelve_a_dar_las_mismas_opciones() -> None:
    kind = BY_SLUG["de-donde-sale"]
    original = parse_options(kind, {"b_nota": "0", "subtitle": "Una semana", "cta": "Entrá"})

    vuelta = parse_options(kind, dict(original.query_items()))

    assert vuelta == original
