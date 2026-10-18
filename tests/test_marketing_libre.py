"""Tarjeta «Libre»: una plantilla de marca vacía sobre la que el operador arma
bloques (título, texto, llamada, separador) en el orden que quiera.

Los casos se escriben a mano antes del código. Reglas que cuidan: máximo 8
bloques, topes de caracteres por tipo, un tipo desconocido no entra, y el
texto libre se escapa (nunca HTML). Como el resto de las opciones, lo libre
se congela al aprobar.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from saimc.marketing.context import BY_SLUG
from saimc.marketing.html import card_html
from saimc.marketing.options import (
    FREE_MAX_BLOCKS,
    FreeBlock,
    options_from_dict,
    options_to_dict,
    parse_options,
)
from saimc.marketing.posts import POSTABLE, approve, create_draft
from tests.test_marketing_post_router import _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client, datos  # noqa: F401

LIBRE = BY_SLUG["libre"]
HOY = date.today()


def _form(*pares: tuple[str, str]) -> FormData:
    return FormData(list(pares))


def _bloques(opts) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return [(b.type, b.text) for b in opts.free]


class TestCatalogo:
    def test_es_una_tarjeta_publicable_de_1080x1350_sin_datos(self) -> None:
        assert (LIBRE.width, LIBRE.height) == (1080, 1350)
        assert not LIBRE.needs_product and not LIBRE.needs_province
        assert LIBRE in POSTABLE


class TestParse:
    def test_lee_los_bloques_en_orden(self) -> None:
        opts = parse_options(
            LIBRE,
            _form(
                ("free_type", "titulo"), ("free_text", "Hola"),
                ("free_type", "texto"), ("free_text", "Un párrafo"),
                ("free_type", "separador"), ("free_text", "ignorado"),
            ),
        )

        assert _bloques(opts) == [("titulo", "Hola"), ("texto", "Un párrafo"), ("separador", "")]

    def test_un_tipo_desconocido_no_entra(self) -> None:
        opts = parse_options(
            LIBRE,
            _form(
                ("free_type", "script"), ("free_text", "x"),
                ("free_type", "texto"), ("free_text", "ok"),
            ),
        )

        assert _bloques(opts) == [("texto", "ok")]

    @pytest.mark.parametrize(
        ("tipo", "tope"), [("titulo", 80), ("texto", 240), ("chip", 60)]
    )
    def test_el_texto_se_recorta_al_tope_de_su_tipo(self, tipo: str, tope: int) -> None:
        opts = parse_options(LIBRE, _form(("free_type", tipo), ("free_text", "a" * 500)))

        assert len(opts.free[0].text) == tope

    def test_deja_una_sola_linea_en_titulo_y_llamada_pero_conserva_los_saltos_del_texto(self) -> None:
        opts = parse_options(
            LIBRE,
            _form(
                ("free_type", "titulo"), ("free_text", "uno\ndos"),
                ("free_type", "texto"), ("free_text", "uno\ndos"),
            ),
        )

        assert _bloques(opts) == [("titulo", "uno dos"), ("texto", "uno\ndos")]

    def test_ocho_bloques_como_maximo(self) -> None:
        pares = [p for _ in range(12) for p in (("free_type", "texto"), ("free_text", "x"))]

        assert len(parse_options(LIBRE, _form(*pares)).free) == FREE_MAX_BLOCKS == 8

    def test_otras_tarjetas_ignoran_los_bloques_libres(self) -> None:
        dato = BY_SLUG["dato-del-dia"]

        assert parse_options(dato, _form(("free_type", "texto"), ("free_text", "x"))).free == ()


class TestOperaciones:
    base = (
        ("free_type", "titulo"), ("free_text", "A"),
        ("free_type", "texto"), ("free_text", "B"),
        ("free_type", "chip"), ("free_text", "C"),
    )

    def _op(self, op: str) -> list[tuple[str, str]]:
        return _bloques(parse_options(LIBRE, _form(*self.base, ("free_op", op))))

    def test_agregar_suma_al_final_un_bloque_vacio_del_tipo(self) -> None:
        assert self._op("add:separador")[-1] == ("separador", "")
        assert len(self._op("add:texto")) == 4

    def test_agregar_un_tipo_desconocido_no_hace_nada(self) -> None:
        assert len(self._op("add:iframe")) == 3

    def test_agregar_con_el_tope_lleno_no_hace_nada(self) -> None:
        pares = [p for _ in range(8) for p in (("free_type", "texto"), ("free_text", "x"))]

        opts = parse_options(LIBRE, _form(*pares, ("free_op", "add:texto")))

        assert len(opts.free) == 8

    def test_subir_y_bajar_intercambian_con_el_vecino(self) -> None:
        assert [t for _, t in self._op("up:1")] == ["B", "A", "C"]
        assert [t for _, t in self._op("down:1")] == ["A", "C", "B"]

    def test_subir_el_primero_y_bajar_el_ultimo_no_hacen_nada(self) -> None:
        assert [t for _, t in self._op("up:0")] == ["A", "B", "C"]
        assert [t for _, t in self._op("down:2")] == ["A", "B", "C"]

    def test_quitar(self) -> None:
        assert [t for _, t in self._op("del:1")] == ["A", "C"]

    @pytest.mark.parametrize("op", ["del:9", "up:-1", "up:x", "boom", "del:", ""])
    def test_una_operacion_invalida_no_cambia_nada(self, op: str) -> None:
        assert [t for _, t in self._op(op)] == ["A", "B", "C"]


class TestGuardado:
    def test_ida_y_vuelta_por_el_json(self) -> None:
        opts = parse_options(LIBRE, _form(("free_type", "titulo"), ("free_text", "Hola")))

        guardado = options_to_dict(opts)
        vuelta = options_from_dict(LIBRE, guardado)

        assert guardado["free"] == [{"type": "titulo", "text": "Hola"}]
        assert vuelta.free == (FreeBlock("titulo", "Hola"),)

    def test_lo_guardado_vuelve_a_pasar_por_las_reglas(self) -> None:
        vuelta = options_from_dict(
            LIBRE, {"free": [{"type": "script", "text": "x"}, {"type": "chip", "text": "y" * 99}]}
        )

        assert [(b.type, len(b.text)) for b in vuelta.free] == [("chip", 60)]


class TestRender:
    def _html(self, *pares: tuple[str, str]) -> str:
        return card_html(LIBRE, {}, parse_options(LIBRE, _form(*pares)))

    def test_pinta_los_bloques_en_orden_con_la_marca(self) -> None:
        html = self._html(
            ("free_type", "titulo"), ("free_text", "PRIMERO"),
            ("free_type", "texto"), ("free_text", "SEGUNDO"),
            ("free_type", "chip"), ("free_text", "TERCERO"),
        )

        assert html.index("PRIMERO") < html.index("SEGUNDO") < html.index("TERCERO")
        assert "Guani" in html
        assert "width: 1080px" in html and "height: 1350px" in html

    def test_escapa_el_html(self) -> None:
        html = self._html(("free_type", "texto"), ("free_text", "<script>alert(1)</script>"))

        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;" in html

    def test_un_bloque_sin_texto_no_deja_hueco_ni_marca(self) -> None:
        html = self._html(
            ("free_type", "titulo"), ("free_text", ""),
            ("free_type", "texto"), ("free_text", "solo"),
        )

        assert 'data-free="titulo"' not in html
        assert 'data-free="texto"' in html

    def test_la_tarjeta_vacia_se_pinta_igual(self) -> None:
        assert "Guani" in self._html()


class TestCiclo:
    def test_aprobarla_congela_los_bloques(self, session: Session, tmp_path: Path) -> None:
        post = create_draft(
            session, card_slug="libre", planned_for=HOY, product=None, province=None,
            data_through=HOY,
            options=parse_options(LIBRE, _form(("free_type", "titulo"), ("free_text", "Congelado"))),
        )
        session.commit()

        approve(session, post, images_dir=tmp_path / "posts", render=_jpeg_falso)
        session.commit()

        slide = post.slides[0]
        assert slide.snapshot["options"]["free"] == [{"type": "titulo", "text": "Congelado"}]  # type: ignore[index]
        assert slide.image_path is not None


class TestRuta:
    def _borrador(self, session: Session) -> str:
        post = create_draft(
            session, card_slug="libre", planned_for=HOY, product=None, province=None,
            data_through=HOY,
        )
        session.commit()
        return str(post.id)

    def test_se_puede_crear_desde_hoy(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        assert 'value="libre"' in client.get("/marketing").text

    def test_agregar_y_guardar_bloques_desde_el_editor(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        pid = self._borrador(session)

        client.post(
            f"/marketing/post/{pid}",
            data={"position": "1", "free_op": "add:titulo"},
            follow_redirects=False,
        )
        client.post(
            f"/marketing/post/{pid}",
            data={"position": "1", "free_type": ["titulo", "texto"], "free_text": ["Mi título", "Cuerpo"]},
            follow_redirects=False,
        )

        vista = client.get(f"/marketing/post/{pid}/preview/1").text
        editor = client.get(f"/marketing/post/{pid}").text
        assert "Mi título" in vista
        assert 'name="free_text"' in editor
        assert 'value="down:0"' in editor and 'value="up:1"' in editor
        assert 'value="up:0"' not in editor and 'value="down:1"' not in editor
        assert 'value="del:1"' in editor

    def test_un_borrador_sin_bloques_ofrece_agregar(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        editor = client.get(f"/marketing/post/{self._borrador(session)}").text

        assert "Agregar bloque" in editor
        assert 'value="add:titulo"' in editor
