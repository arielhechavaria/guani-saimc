"""El texto de las tarjetas sigue `docs/narrativa-guani.md`.

Las frases esperadas salen del documento (capa por capa de su
«Recomendación»: idea madre, vara, cuentas claras, cierre «tú decides»), no del
código. Y el vocabulario que el documento manda evitar no puede aparecer en
ninguna plantilla de tarjeta: «poner orden» suena a control, «caro/barato»
opina, «se paga» promete lo que un anuncio no dice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import saimc.web
from saimc.marketing.caption import build_caption
from saimc.marketing.context import BY_SLUG
from saimc.marketing.html import card_html

TEMPLATES = Path(saimc.web.__file__).parent / "templates"
PRODUCTOS = {"products": ["Arroz", "Pollo"]}

#: «Vocabulario de la marca», columna «Evitar».
PROHIBIDAS = (
    r"poner orden", r"ordenar el mercado", r"ordenamiento", r"mercado negro", r"bolsa negra",
    r"\bcaro\b", r"\bbarat[oa]s?\b", r"se paga", r"en tiempo real", r"\bfiable\b",
    r"algoritmo", r"garantizado", r"precio justo", r"scraping",
)


def _texto(slug: str, context: dict | None = None) -> str:
    return card_html(BY_SLUG[slug], context if context is not None else PRODUCTOS)


@pytest.mark.parametrize("plantilla", sorted(p.name for p in TEMPLATES.glob("marketing_*.html")))
def test_ninguna_plantilla_de_tarjeta_usa_vocabulario_que_la_marca_evita(plantilla: str) -> None:
    if plantilla in ("marketing_post.html", "marketing_semana.html"):
        pytest.skip("pantallas del operador, no tarjetas")
    texto = re.sub(r"\{#.*?#\}", "", (TEMPLATES / plantilla).read_text(), flags=re.S)
    for patron in PROHIBIDAS:
        assert not re.search(patron, texto, flags=re.I), f"{plantilla}: «{patron}»"


class TestFrasesDelDocumento:
    def test_portada_del_carrusel_abre_con_la_idea_madre(self) -> None:
        html = _texto("carrusel-portada", {})

        assert "Parece un relajo, pero tiene su orden." in html
        assert "provincia por provincia" in html
        assert "Mayoristas y minoristas" not in html  # Guani le habla a todo el pueblo

    def test_el_consejo_dice_el_precio_del_medio(self) -> None:
        assert "el precio del medio" in _texto("carrusel-consejo", {})

    def test_el_cierre_es_la_prueba_y_el_tu_decides(self) -> None:
        html = _texto("carrusel-cierre", {})

        assert "Cada precio con su cuenta." in html
        assert "Guani enseña el precio. Tú decides qué hacer con él." in html

    def test_la_portada_de_facebook_es_la_vara(self) -> None:
        html = _texto("portada-facebook")

        assert "Medimos el mercado con la misma vara." in html
        assert "Cada precio con su cuenta." in html

    def test_la_vista_previa_al_compartir_es_la_idea_madre_y_no_promete_verificacion(self) -> None:
        html = _texto("og-compartir", {})

        assert "Parece un relajo, pero tiene su orden." in html
        assert "verificados" not in html

    def test_la_story_dice_lo_que_se_anuncia_y_no_lo_que_se_vende(self) -> None:
        html = _texto("story-precalentamiento")

        assert "Lo que se anuncia hoy en Cuba" in html
        assert "se vende" not in html

    def test_como_funciona_cuenta_que_una_maquina_lee_y_una_persona_revisa(self) -> None:
        html = _texto("como-funciona", {})

        assert "Los leemos y los revisamos" in html
        assert "una persona revisa cada uno" in html
        assert "estadística" not in html
        assert "Los procesamos" not in html

    def test_la_tarjeta_libre_cierra_con_lo_que_se_anuncia(self) -> None:
        assert "Lo que se anuncia en Cuba, provincia por provincia." in _texto("libre", {})


class TestSinOpinion:
    def test_dato_del_dia_marca_la_mediana_mas_baja_no_la_mas_barata(self) -> None:
        fuente = (TEMPLATES / "marketing_dato_del_dia.html").read_text()

        assert "mediana más baja" in fuente
        assert "más barata" not in fuente


class TestTextoDelPost:
    def test_el_texto_generico_sigue_la_idea_madre_y_el_tu_decides(self) -> None:
        texto = build_caption(BY_SLUG["carrusel-portada"], {})

        assert texto.startswith("Parece un relajo, pero tiene su orden.")
        assert "Guani enseña el precio. Tú decides qué hacer con él." in texto
