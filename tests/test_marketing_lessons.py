"""Sección educativa: las lecciones, su borrador y el turno diario «Educativo».

Los casos esperados están escritos a mano antes que el código: los topes son
los de `options.FREE_TYPES` (título 80, texto 240, chip 60), la rotación es por
`date.toordinal()`, y el vocabulario vedado sale de `docs/narrativa-guani.md` y
de las reglas del slice (nada de «mipyme», nada de decir que una persona revisa
cada precio, nada de cifras).
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

from saimc.marketing.caption import CAPTION_MAX, HASHTAGS_MAX
from saimc.marketing.context import BY_SLUG
from saimc.marketing.daily import EDU_SLOT, run_daily, slot_label
from saimc.marketing.fonts import avatar_data_uri
from saimc.marketing.html import card_html
from saimc.marketing.lessons import LESSONS, Lesson, lesson_for_day
from saimc.marketing.options import FREE_TYPES, CardOptions, FreeBlock, options_from_dict
from saimc.marketing.posts import MAX_SLIDES, approve, create_lesson_draft, slide_html
from saimc.models import MarketingPost
from saimc.models.marketing_post import PostOrigin, PostStatus
from saimc.web.app import app
from saimc.web.routers.marketing import get_today
from tests.test_marketing_post_router import _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client  # noqa: F401

LUNES = date(2026, 10, 19)
LECCION = BY_SLUG["leccion"]

#: Lo que la voz de Guani no dice (narrativa) más las reglas duras del slice.
VEDADAS = (
    r"mipyme", r"una persona (revisa|supervisa)", r"cada precio (lo )?revis",
    r"poner orden", r"\bcaro\b", r"\bbarat[oa]s?\b", r"\bfiable\b", r"algoritmo",
    r"garantizado", r"\bconviene\b", r"\bdeber[ií]as\b",
    r"\d",  # sin cifras ni porcentajes: texto narrativo
    r"\bvos\b", r"\bmirá\b", r"\bfijate\b", r"\bpodés\b", r"\btenés\b",  # sin voseo
)


def _textos(leccion: Lesson) -> list[str]:
    textos = [leccion.title, leccion.summary]
    textos += [b.text for diap in leccion.slides for b in diap]
    return textos


class TestLecciones:
    def test_hay_cuatro_lecciones_de_cuatro_a_cinco_diapositivas(self) -> None:
        assert [x.slug for x in LESSONS] == [
            "de-los-grupos-a-la-cifra", "rango-y-mediana", "muestra-en-formacion",
            "cada-precio-con-sus-anuncios",
        ]
        assert all(4 <= len(x.slides) <= 5 <= MAX_SLIDES for x in LESSONS)

    def test_los_bloques_respetan_los_topes_de_la_tarjeta_libre(self) -> None:
        for leccion in LESSONS:
            for diap in leccion.slides:
                assert diap, leccion.slug
                for bloque in diap:
                    assert bloque.type in FREE_TYPES, (leccion.slug, bloque.type)
                    _, tope, _ = FREE_TYPES[bloque.type]
                    assert len(bloque.text) <= tope, (leccion.slug, bloque.text)

    def test_la_primera_es_portada_y_la_ultima_cierra_con_una_llamada(self) -> None:
        for leccion in LESSONS:
            assert leccion.slides[0][0].type == "titulo"
            assert leccion.slides[-1][-1].type == "chip", leccion.slug

    @pytest.mark.parametrize("patron", VEDADAS)
    def test_ninguna_leccion_usa_palabras_vedadas(self, patron: str) -> None:
        for leccion in LESSONS:
            for texto in _textos(leccion):
                texto = re.sub(r"^\d\. ", "", texto)  # la numeración de los pasos no es cifra
                assert not re.search(patron, texto, flags=re.I), (leccion.slug, texto, patron)

    def test_la_rotacion_es_determinista_por_dia_y_las_recorre_todas(self) -> None:
        dias = [LUNES + timedelta(days=i) for i in range(len(LESSONS))]

        assert [lesson_for_day(d) for d in dias] == [lesson_for_day(d) for d in dias]
        assert lesson_for_day(LUNES) is LESSONS[LUNES.toordinal() % len(LESSONS)]
        assert {lesson_for_day(d).slug for d in dias} == {x.slug for x in LESSONS}
        # Pasados len(LESSONS) días vuelve a la misma.
        assert lesson_for_day(LUNES + timedelta(days=len(LESSONS))) is lesson_for_day(LUNES)


class TestBorrador:
    def test_arma_un_carrusel_con_los_bloques_precargados(self, session: Session) -> None:
        leccion = lesson_for_day(LUNES)

        post = create_lesson_draft(session, lesson=leccion, planned_for=LUNES)
        session.commit()

        assert post.format == "carousel"
        assert post.status == PostStatus.DRAFT
        assert post.origin == PostOrigin.OPERATOR and post.batch_slot is None
        assert [s.position for s in post.slides] == list(range(1, len(leccion.slides) + 1))
        assert {s.card_slug for s in post.slides} == {"leccion"}
        # Todas del mismo tamaño: un carrusel no mezcla medidas.
        assert (LECCION.width, LECCION.height) == (1080, 1350)
        for slide, bloques in zip(post.slides, leccion.slides, strict=True):
            assert options_from_dict(LECCION, slide.options).free == bloques
            assert slide.params["product"] is None and slide.params["province"] is None

    def test_el_texto_del_post_cabe_y_termina_en_los_hashtags(self, session: Session) -> None:
        post = create_lesson_draft(session, lesson=lesson_for_day(LUNES), planned_for=LUNES)

        assert post.caption is not None
        assert len(post.caption) <= CAPTION_MAX
        assert post.caption.startswith(lesson_for_day(LUNES).summary)
        etiquetas = [w for w in post.caption.split() if w.startswith("#")]
        assert "#guani" in etiquetas and len(etiquetas) <= HASHTAGS_MAX

    def test_aprobar_funciona_sin_producto_ni_datos(
        self, session: Session, tmp_path: Path
    ) -> None:
        post = create_lesson_draft(session, lesson=lesson_for_day(LUNES), planned_for=LUNES)
        session.commit()

        approve(session, post, images_dir=tmp_path / "posts", render=_jpeg_falso)

        assert post.status == PostStatus.APPROVED
        assert all(s.image_path for s in post.slides)
        assert post.caption is not None and "#guani" in post.caption
        # Lo congelado conserva los bloques tal cual.
        snapshot = post.slides[0].snapshot
        assert snapshot is not None
        assert options_from_dict(LECCION, snapshot["options"]).free == (
            lesson_for_day(LUNES).slides[0]
        )

    def test_cada_diapositiva_se_pinta_con_su_texto_el_kicker_y_el_contador(
        self, session: Session
    ) -> None:
        leccion = lesson_for_day(LUNES)
        post = create_lesson_draft(session, lesson=leccion, planned_for=LUNES)
        session.commit()

        for slide, bloques in zip(post.slides, leccion.slides, strict=True):
            html = slide_html(session, slide)
            assert html is not None
            assert "Aprende con Guani" in html
            assert f"{slide.position} / {len(post.slides)}" in html
            for b in bloques:
                assert b.type == "separador" or "&" in b.text or b.text in html, b.text


class TestRenderYAvatar:
    def test_la_diapositiva_escapa_el_texto_libre(self) -> None:
        opciones = CardOptions(show={}, free=(FreeBlock("titulo", "<b>hola</b>"),))

        html = card_html(LECCION, {}, opciones)

        assert "<b>hola</b>" not in html and "&lt;b&gt;hola&lt;/b&gt;" in html

    @pytest.mark.parametrize(
        "slug",
        ["libre", "leccion", "carrusel-portada", "carrusel-consejo", "carrusel-cierre",
         "como-funciona", "og-compartir", "story-precalentamiento"],
    )
    def test_las_tarjetas_llevan_el_avatar_a_color_embebido_y_no_el_svg(self, slug: str) -> None:
        contexto = {"products": ["Arroz"]} if slug == "story-precalentamiento" else {}

        html = card_html(BY_SLUG[slug], contexto)

        assert avatar_data_uri() in html
        assert 'class="mc-avatar"' in html
        assert "<svg" not in html  # el SVG gris ya no está en las tarjetas

    def test_el_avatar_es_un_webp_embebido_como_data_uri(self) -> None:
        uri = avatar_data_uri()

        assert uri.startswith("data:image/webp;base64,")
        assert avatar_data_uri() is uri  # cacheado


class TestTurnoDiario:
    def test_ejecutado_crea_el_turno_educativo_sin_producto_ni_provincia(
        self, session: Session, tmp_path: Path
    ) -> None:
        reporte = run_daily(session, LUNES, tmp_path, execute=True)

        edu = next(c for c in reporte.cards if c.slug == EDU_SLOT)
        assert (edu.estado, edu.product, edu.province) == ("creada", None, None)
        post = session.scalars(
            select(MarketingPost).where(MarketingPost.batch_slot == EDU_SLOT)
        ).one()
        assert post.origin == PostOrigin.BATCH and post.planned_for == LUNES
        assert len(post.slides) == len(lesson_for_day(LUNES).slides)
        assert slot_label(EDU_SLOT) == "Educativo"

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


class TestPantallaHoy:
    def test_hoy_avisa_que_falta_el_educativo_y_crearlo_arma_la_leccion_del_dia(
        self, client: TestClient, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: LUNES

        assert "Falta «Educativo»" in client.get("/marketing").text
        r = client.post("/marketing/post", data={"kind": "educativo"}, follow_redirects=False)

        assert r.status_code == 303
        post = session.get(MarketingPost, uuid.UUID(r.headers["location"].rsplit("/", 1)[1]))
        assert post is not None
        assert len(post.slides) == len(lesson_for_day(LUNES).slides)
        hoy = client.get("/marketing").text
        assert "Falta «Educativo»" not in hoy  # la hecha a mano cubre el turno
