"""Turno educativo: secciones de la presentación «Guani para instituciones».

Los casos esperados están escritos a mano antes que el código: las tres
secciones y su orden salen de la decisión del operador (A = cover…quees,
B = flujo…cuentas, C = noopina…miralo; `hoy` y `cierre` nunca), la rotación es
por `date.toordinal() % 3` (el 2026-10-19 da 0, el 20 da 1, el 21 da 2) y el
vocabulario vedado sale de `docs/narrativa-guani.md` y de las reglas del slice.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import saimc.marketing.context as context_module
from saimc.marketing.caption import CAPTION_MAX, HASHTAGS_MAX, with_hashtags
from saimc.marketing.context import BY_SLUG, build_context
from saimc.marketing.daily import EDU_SLOT, run_daily, slot_label
from saimc.marketing.deck import (
    DECK_CARD,
    PUBLISHABLE,
    SECTIONS,
    DeckSection,
    render_slide,
    section_for_day,
)
from saimc.marketing.html import card_html
from saimc.marketing.options import CardOptions, FreeBlock
from saimc.marketing.posts import (
    POSTABLE,
    approve,
    create_draft,
    duplicate,
    effective_caption,
    save_draft,
    slide_html,
)
from saimc.models import MarketingPost
from saimc.models.marketing_post import PostOrigin, PostStatus
from saimc.web.app import app
from saimc.web.routers.marketing import get_today
from tests.test_marketing_post_router import _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client  # noqa: F401

LUNES = date(2026, 10, 19)  # toordinal() % 3 == 0 → sección A
KIND = BY_SLUG[DECK_CARD]

SECCION_A = ["cover", "orden", "pregunta", "quienvende", "quees"]
SECCION_B = ["flujo", "embudo", "vara", "respuesta", "cuentas"]
SECCION_C = ["noopina", "publico", "viene", "miralo"]

#: Lo que la voz de Guani no dice (narrativa) más las reglas duras del slice.
VEDADAS = (
    r"mipyme", r"una persona (revisa|supervisa)", r"cada precio (lo )?revis",
    r"poner orden", r"\bcaro\b", r"\bbarat[oa]s?\b", r"\bfiable\b", r"algoritmo",
    r"garantizado", r"\bconviene\b", r"\bdeber[ií]as\b",
    r"\d",  # sin cifras ni porcentajes
    r"\bvos\b", r"\bmirá\b", r"\bfijate\b", r"\bpodés\b", r"\btenés\b",  # sin voseo
)


class TestSecciones:
    def test_son_tres_con_las_laminas_que_decidio_el_operador(self) -> None:
        assert [list(s.slides) for s in SECTIONS] == [SECCION_A, SECCION_B, SECCION_C]

    def test_hoy_y_cierre_nunca_se_publican(self) -> None:
        assert len(PUBLISHABLE) == 14
        assert "hoy" not in PUBLISHABLE and "cierre" not in PUBLISHABLE
        assert render_slide("hoy") is None and render_slide("cierre") is None
        assert render_slide("no-existe") is None

    def test_la_rotacion_es_determinista_y_recorre_las_tres(self) -> None:
        assert [list(section_for_day(LUNES + timedelta(days=i)).slides) for i in range(4)] == [
            SECCION_A, SECCION_B, SECCION_C, SECCION_A,
        ]
        assert section_for_day(LUNES) is section_for_day(LUNES)

    @pytest.mark.parametrize("seccion", SECTIONS, ids=lambda s: s.slug)
    def test_el_texto_del_post_cabe_y_no_dice_lo_vedado(self, seccion: DeckSection) -> None:
        texto = with_hashtags(seccion.summary)

        assert len(texto) <= CAPTION_MAX
        etiquetas = [w for w in texto.split() if w.startswith("#")]
        assert "#guani" in etiquetas and len(etiquetas) <= HASHTAGS_MAX
        assert "guani.app" in texto
        assert "Guarda esto" in texto or "Mándaselo a quien hace las compras" in texto
        cuerpo = seccion.summary
        for patron in VEDADAS:
            assert not re.search(patron, cuerpo, flags=re.I), (seccion.slug, patron)


class TestRenderizado:
    @pytest.mark.parametrize("lamina", PUBLISHABLE)
    def test_sin_contador_sin_blobs_y_sin_etiquetas_del_runtime(self, lamina: str) -> None:
        html = render_slide(lamina)

        assert html is not None and html.startswith("<section")
        assert not re.search(r"\b\d+ / \d+\b", html)  # el «n / 16» escrito a mano
        assert "/_blob/" not in html
        assert "<x-" not in html

    def test_quitar_el_contador_deja_el_resto_del_texto(self) -> None:
        html = render_slide("vara")

        assert html is not None
        assert "Las mismas reglas para todos los precios." in html
        assert "Guani, por Leira Studio" in html  # el pie se queda; solo se va el contador
        assert "8 / 16" not in html

    def test_las_imagenes_locales_viajan_como_data_uri(self) -> None:
        for lamina, cantidad in (("cover", 1), ("pregunta", 1), ("respuesta", 1)):
            html = render_slide(lamina)
            assert html is not None
            assert html.count('src="data:image/png;base64,') == cantidad, lamina

    def test_los_iconos_y_conectores_pasan_a_svg(self) -> None:
        iconos = render_slide("quienvende")
        flujo = render_slide("flujo")

        assert iconos is not None and iconos.count("<svg") == 3
        assert flujo is not None and flujo.count("<svg") == 6  # entre los siete pasos

    def test_la_diapositiva_pinta_la_lamina_en_un_lienzo_de_1920_por_1080(self) -> None:
        html = card_html(KIND, build_context(KIND, None, product=None, province=None,  # type: ignore[arg-type]
                                             deck_slide="orden"))

        assert (KIND.width, KIND.height) == (1920, 1080)
        assert "El mercado cubano parece caótico, pero tiene orden." in html
        assert "font-face" in html  # Inter viaja dentro del HTML
        assert 'id="orden"' in html

    def test_una_lamina_fuera_de_las_publicables_no_arma_contexto(self) -> None:
        for lamina in ("hoy", "cierre", "x", None):
            assert build_context(
                KIND, None, product=None, province=None, deck_slide=lamina  # type: ignore[arg-type]
            ) is None


class TestTurnoDiario:
    def test_ejecutado_crea_el_carrusel_de_la_seccion_del_dia_en_orden(
        self, session: Session, tmp_path: Path
    ) -> None:
        reporte = run_daily(session, LUNES, tmp_path, execute=True)

        edu = next(c for c in reporte.cards if c.slug == EDU_SLOT)
        assert (edu.estado, edu.province) == ("creada", None)
        post = session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).one()
        assert post.origin == PostOrigin.BATCH and post.planned_for == LUNES
        assert post.format == "carousel"
        assert [s.position for s in post.slides] == [1, 2, 3, 4, 5]
        assert {s.card_slug for s in post.slides} == {DECK_CARD}
        assert [s.params["deck_slide"] for s in post.slides] == SECCION_A
        assert slot_label(EDU_SLOT) == "Educativo"

    def test_otro_dia_otra_seccion(self, session: Session, tmp_path: Path) -> None:
        run_daily(session, LUNES + timedelta(days=1), tmp_path, execute=True)

        post = session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).one()
        assert [s.params["deck_slide"] for s in post.slides] == SECCION_B

    def test_en_seco_lo_preve_sin_escribir(self, session: Session, tmp_path: Path) -> None:
        reporte = run_daily(session, LUNES, tmp_path, execute=False)

        assert ("educativo", "prevista") in [(c.slug, c.estado) for c in reporte.cards]
        assert session.scalars(select(MarketingPost)).all() == []

    def test_es_idempotente(self, session: Session, tmp_path: Path) -> None:
        run_daily(session, LUNES, tmp_path, execute=True)
        reporte = run_daily(session, LUNES, tmp_path, execute=True)

        assert ("educativo", "ya existía") in [(c.slug, c.estado) for c in reporte.cards]
        edu = session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).all()
        assert len(edu) == 1

    def test_el_texto_del_post_sale_de_la_seccion_sin_guardarse(
        self, session: Session, tmp_path: Path
    ) -> None:
        run_daily(session, LUNES, tmp_path, execute=True)
        post = session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).one()

        texto = effective_caption(session, post)

        assert post.caption is None
        assert texto.startswith(SECTIONS[0].summary)
        assert texto.endswith("#mercadocubano")


class TestAprobar:
    def _borrador(self, session: Session, tmp_path: Path) -> MarketingPost:
        run_daily(session, LUNES, tmp_path, execute=True)
        return session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).one()

    def test_rasteriza_cada_lamina_a_1920_por_1080(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = self._borrador(session, tmp_path)
        pedidos: list[tuple[int, int]] = []

        def renderer(html: str, *, width: int, height: int) -> bytes:
            pedidos.append((width, height))
            return b"JPEG falso"

        approve(session, post, images_dir=tmp_path / "posts", render=renderer)

        assert pedidos == [(1920, 1080)] * 5
        assert post.status == PostStatus.APPROVED
        assert all(s.image_path for s in post.slides)
        assert post.caption is not None and post.caption.startswith(SECTIONS[0].summary)

    def test_lo_aprobado_queda_congelado_aunque_cambie_la_presentacion(
        self, session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        post = self._borrador(session, tmp_path)
        approve(session, post, images_dir=tmp_path / "posts", render=_jpeg_falso)
        session.commit()
        monkeypatch.setattr(
            context_module, "render_slide", lambda _id: "<section>CAMBIADA EN DOCS</section>"
        )

        aprobada = slide_html(session, post.slides[1])
        nueva = create_draft(
            session, card_slug=DECK_CARD, planned_for=LUNES, product=None, province=None,
            data_through=LUNES,
        )
        nueva.slides[0].params = {**nueva.slides[0].params, "deck_slide": "orden"}

        assert aprobada is not None
        assert "CAMBIADA EN DOCS" not in aprobada
        assert "El mercado cubano parece caótico" in aprobada
        assert "CAMBIADA EN DOCS" in (slide_html(session, nueva.slides[0]) or "")

    def test_editar_o_duplicar_no_pierde_la_lamina_ni_el_texto_automatico(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = self._borrador(session, tmp_path)
        segunda = post.slides[1]

        save_draft(
            session, post, product=None, province=None, data_through=LUNES,
            options=CardOptions(show={}), caption=None, position=2,
        )
        approve(session, post, images_dir=tmp_path / "posts", render=_jpeg_falso)
        copia = duplicate(session, post, today=LUNES + timedelta(days=1))

        assert segunda.params["deck_slide"] == "orden"
        assert [s.params["deck_slide"] for s in copia.slides] == SECCION_A
        assert copia.caption is None  # el texto era el automático: se regenera


class TestPantallaHoy:
    def test_crear_arma_la_seccion_del_dia_y_cubre_el_turno(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: LUNES

        assert "Falta «Educativo»" in client.get("/marketing").text
        r = client.post("/marketing/post", data={"kind": "educativo"}, follow_redirects=False)

        assert r.status_code == 303
        post = session.get(MarketingPost, uuid.UUID(r.headers["location"].rsplit("/", 1)[1]))
        assert post is not None
        assert [s.params["deck_slide"] for s in post.slides] == SECCION_A
        assert "Falta «Educativo»" not in client.get("/marketing").text

    def test_el_menu_de_nueva_ofrece_el_educativo_de_la_presentacion_y_no_la_leccion(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: LUNES
        html = client.get("/marketing").text
        menu = html.split('aria-label="Nueva publicación"')[1]

        assert f'<option value="{EDU_SLOT}">Educativo' in menu
        assert 'value="leccion"' not in menu

    def test_crear_una_leccion_de_texto_ya_no_es_posible(
        self, client: TestClient, fabrica: Path  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: LUNES

        r = client.post("/marketing/post", data={"kind": "leccion"}, follow_redirects=False)

        assert r.status_code == 404

    def test_el_editor_escala_el_lienzo_apaisado_y_no_ofrece_agregar_diapositivas(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: LUNES
        r = client.post("/marketing/post", data={"kind": "educativo"}, follow_redirects=False)
        editor = r.headers["location"]

        pagina = client.get(editor).text
        vista = client.get(f"{editor}/preview/2")

        assert 'data-mk-w="1920"' in pagina
        assert "aspect-ratio: 1920/1080" in pagina
        assert "Agregar diapositiva" not in pagina  # no hay otra plantilla de 1920×1080
        assert vista.status_code == 200 and "El mercado cubano parece caótico" in vista.text

    def test_la_presentacion_no_se_ofrece_como_tarjeta_suelta(self) -> None:
        assert DECK_CARD not in [t.slug for t in POSTABLE]


class TestCompatibilidadConLasLecciones:
    """Las lecciones de texto ya no se crean, pero hay publicaciones con ellas."""

    def test_la_tarjeta_leccion_sigue_en_el_catalogo_y_se_pinta(self) -> None:
        leccion = BY_SLUG["leccion"]
        opciones = CardOptions(show={}, free=(FreeBlock("titulo", "Rango y mediana"),))

        html = card_html(leccion, {}, opciones)

        assert (leccion.width, leccion.height) == (1080, 1350)
        assert "Aprende con Guani" in html and "Rango y mediana" in html

    def test_una_leccion_vieja_se_lee_y_se_aprueba(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = create_draft(
            session, card_slug="leccion", planned_for=LUNES, product=None, province=None,
            data_through=LUNES,
            options=CardOptions(show={}, free=(FreeBlock("titulo", "Rango y mediana"),)),
        )
        session.commit()

        approve(session, post, images_dir=tmp_path / "posts", render=_jpeg_falso)

        assert post.status == PostStatus.APPROVED
        assert post.slides[0].snapshot is not None
