"""Rutas de la fábrica con estado: «Hoy» (`/marketing`), el editor
(`/marketing/post/<id>`) y sus transiciones (F2,
`docs/spec-slice-fabrica-publicaciones.md` §1.5 y §1.7, caso 6).

Reusa los fixtures de `test_marketing_router.py` (arroz en La Habana y
Santiago, pollo fuera del foco). El día, la carpeta de imágenes y el render
JPEG se inyectan con `dependency_overrides`: ni reloj de La Habana, ni disco
del repo, ni Chromium.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.marketing.posts import approve, create_draft
from saimc.marketing.render import RendererUnavailable
from saimc.models import MarketingPost
from saimc.models.marketing_post import PostOrigin, PostStatus
from saimc.web.app import app
from saimc.web.routers.marketing import get_jpeg_renderer, get_marketing_dir, get_today
from tests.test_marketing_router import client, datos  # noqa: F401

HOY = date.today()


def _jpeg_falso(html: str, *, width: int, height: int) -> bytes:
    return b"JPEG falso"


@pytest.fixture
def fabrica(client: TestClient, tmp_path: Path) -> Iterator[Path]:  # noqa: F811
    app.dependency_overrides[get_today] = lambda: HOY
    app.dependency_overrides[get_marketing_dir] = lambda: tmp_path
    app.dependency_overrides[get_jpeg_renderer] = lambda: _jpeg_falso
    yield tmp_path


def _borrador(session: Session, **extra: object) -> MarketingPost:
    valores: dict[str, object] = {
        "card_slug": "dato-del-dia", "planned_for": HOY, "product": "Arroz",
        "province": None, "data_through": HOY,
    }
    valores.update(extra)
    post = create_draft(session, **valores)  # type: ignore[arg-type]
    session.commit()
    return post


def _aprobada(session: Session, carpeta: Path) -> MarketingPost:
    post = _borrador(session)
    approve(session, post, images_dir=carpeta / "posts", render=_jpeg_falso)
    session.commit()
    return post


class TestHoy:
    def test_lista_lo_de_hoy_y_no_lo_de_otro_dia(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        hoy = _borrador(session)
        ayer = _borrador(session, card_slug="carrusel-cierre", planned_for=HOY - timedelta(days=1))

        html = client.get("/marketing").text

        assert f"/marketing/post/{hoy.id}" in html
        assert f"/marketing/post/{ayer.id}" not in html
        assert "Borrador" in html

    def test_la_narrativa_de_la_marca_queda_a_mano(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        """Cada pieza sale con la voz de Guani: «Hoy» enlaza al documento de
        narrativa y repite su idea madre, sin tener que ir a buscarlo a `/docs`."""
        html = client.get("/marketing").text

        assert 'href="/docs/narrativa-guani"' in html
        assert "el orden que ya existe" in html

    def test_avisa_lo_que_falta_del_calendario(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        html = client.get("/marketing").text

        assert "Falta «Dato del día»" in html
        assert 'name="kind" value="dato-del-dia"' in html

    def test_un_tipo_que_ya_esta_no_se_avisa(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        _borrador(session)

        html = client.get("/marketing").text

        assert "Falta «Dato del día»" not in html
        assert 'name="kind" value="dato-del-dia"' not in html

    def test_una_aprobada_muestra_su_imagen_congelada(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)

        html = client.get("/marketing").text

        assert f"/marketing/post/{post.id}/slide/1.jpg" in html
        assert "Aprobada" in html

    def test_las_piezas_fijas_siguen_plegadas_abajo(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        html = client.get("/marketing").text

        assert "<details" in html
        assert "/marketing/card/portada-facebook.jpg" in html
        assert "/marketing/card/como-funciona.jpg" in html

    def test_ya_no_tiene_la_seccion_de_instagram(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        html = client.get("/marketing").text

        assert 'id="instagram"' not in html

    def test_el_editor_sin_estado_ya_no_existe(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        assert client.get("/marketing/vista").status_code == 404


class TestCrear:
    def test_crear_un_borrador_redirige_al_editor(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        respuesta = client.post(
            "/marketing/post", data={"kind": "dato-del-dia"}, follow_redirects=False
        )

        assert respuesta.status_code == 303
        post = session.query(MarketingPost).one()
        assert respuesta.headers["location"] == f"/marketing/post/{post.id}"
        assert post.origin == PostOrigin.OPERATOR and post.planned_for == HOY
        assert post.slides[0].params["product"] == "Arroz"

    def test_una_tarjeta_desconocida_o_fija_no_se_crea(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        for slug in ("inventada", "og-compartir", "portada-facebook"):
            assert client.post("/marketing/post", data={"kind": slug}).status_code == 404


class TestEditor:
    def test_un_borrador_trae_interruptores_texto_y_htmx(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        html = client.get(f"/marketing/post/{post.id}").text

        assert 'name="b_barra"' in html and 'name="b_comparacion"' in html
        assert 'name="b_titulo"' not in html and 'name="b_rango"' not in html
        assert 'name="subtitle"' in html and 'name="cta"' in html
        assert f'hx-post="/marketing/post/{post.id}"' in html
        assert "/static/htmx.min.js" in html
        assert "<textarea" in html and "#guani" in html
        assert f"/marketing/post/{post.id}/preview/1" in html
        assert "Aprobar" in html
        # Un borrador no se comparte: lo que se publica es lo congelado.
        assert "data-mk-share" not in html

    def test_guardar_persiste_opciones_y_texto(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        respuesta = client.post(
            f"/marketing/post/{post.id}",
            data={
                "product": "Arroz", "data_through": HOY.isoformat(),
                "b_barra": ["0"], "b_comparacion": ["0", "1"], "cta": "Entrá a Guani",
                "caption": "Texto a mano", "b_titulo": "0",
            },
            headers={"HX-Request": "true"},
        )

        assert respuesta.status_code == 200
        assert "<html" not in respuesta.text
        session.refresh(post)
        slide = post.slides[0]
        assert slide.options["show"]["barra"] is False
        assert slide.options["show"]["comparacion"] is True
        assert slide.options["show"]["titulo"] is True
        assert slide.options["cta"] == "Entrá a Guani"
        assert post.caption == "Texto a mano"
        vista = client.get(f"/marketing/post/{post.id}/preview/1").text
        assert 'data-block="barra"' not in vista and "Entrá a Guani" in vista

    def test_un_producto_fuera_del_foco_no_se_guarda(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        respuesta = client.post(
            f"/marketing/post/{post.id}",
            data={"product": "Pollo", "data_through": HOY.isoformat()},
        )

        assert respuesta.status_code == 422
        session.refresh(post)
        assert post.slides[0].params["product"] == "Arroz"

    def test_una_aprobada_es_de_solo_lectura_y_se_comparte(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)

        html = client.get(f"/marketing/post/{post.id}").text

        assert 'name="b_barra"' not in html
        assert "readonly" in html
        assert f'data-mk-files=\'[{{"name": "guani-dato-del-dia-{HOY.isoformat()}.jpg", ' \
            f'"url": "/marketing/post/{post.id}/slide/1.jpg"}}]\'' in html
        # Una suelta no ofrece «Guardar todas»: eso es del carrusel.
        assert "data-mk-save-all" not in html
        assert "Marcar publicada" in html and "Descartar" in html and "Duplicar" in html
        assert (
            client.post(f"/marketing/post/{post.id}", data={"caption": "x"}).status_code == 409
        )

    def test_una_publicacion_inexistente_da_404(
        self, client: TestClient, datos: None, fabrica: Path  # noqa: F811
    ) -> None:
        falso = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/marketing/post/{falso}").status_code == 404
        assert client.get("/marketing/post/no-es-un-uuid").status_code == 404


class TestImagen:
    def test_el_jpeg_de_un_borrador_no_existe(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        assert client.get(f"/marketing/post/{post.id}/slide/1.jpg").status_code == 404

    def test_el_jpeg_de_una_aprobada_es_el_congelado(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)

        respuesta = client.get(f"/marketing/post/{post.id}/slide/1.jpg")

        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "image/jpeg"
        assert respuesta.content == b"JPEG falso"


class TestTransiciones:
    def test_aprobar_desde_la_web(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        respuesta = client.post(f"/marketing/post/{post.id}/aprobar", follow_redirects=False)

        assert respuesta.status_code == 303
        session.refresh(post)
        assert post.status == PostStatus.APPROVED
        assert (fabrica / "posts" / post.slides[0].image_path).read_bytes() == b"JPEG falso"

    def test_aprobar_sin_datos_da_422(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session, data_through=HOY - timedelta(days=60))

        respuesta = client.post(f"/marketing/post/{post.id}/aprobar")

        assert respuesta.status_code == 422
        session.refresh(post)
        assert post.status == PostStatus.DRAFT

    def test_aprobar_sin_chromium_da_503_y_no_cambia_nada(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        def _sin_chromium(html: str, *, width: int, height: int) -> bytes:
            raise RendererUnavailable("Chromium no arrancó. Instalá el paquete")

        app.dependency_overrides[get_jpeg_renderer] = lambda: _sin_chromium
        post = _borrador(session)

        respuesta = client.post(f"/marketing/post/{post.id}/aprobar")

        assert respuesta.status_code == 503
        assert "Instalá" in respuesta.text
        session.refresh(post)
        assert post.status == PostStatus.DRAFT

    def test_aprobar_no_rasteriza_dentro_del_event_loop(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        """Playwright sync falla si hay un event loop corriendo en el hilo: el
        render tiene que correr en un hilo aparte (regresión del 500 en producción)."""
        import asyncio

        def _como_playwright(html: str, *, width: int, height: int) -> bytes:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return b"JPEG falso"
            raise RuntimeError("Playwright Sync API dentro del asyncio loop")

        app.dependency_overrides[get_jpeg_renderer] = lambda: _como_playwright
        post = _borrador(session)

        respuesta = client.post(f"/marketing/post/{post.id}/aprobar", follow_redirects=False)

        assert respuesta.status_code == 303
        session.refresh(post)
        assert post.status == PostStatus.APPROVED

    def test_publicar_un_borrador_da_409(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _borrador(session)

        respuesta = client.post(
            f"/marketing/post/{post.id}/publicada",
            data={"url": "https://www.instagram.com/p/abc/"},
        )

        assert respuesta.status_code == 409

    def test_marcar_publicada_con_enlace(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)

        malo = client.post(f"/marketing/post/{post.id}/publicada", data={"url": "hola"})
        bueno = client.post(
            f"/marketing/post/{post.id}/publicada",
            data={"url": "https://www.instagram.com/p/abc/"}, follow_redirects=False,
        )

        assert malo.status_code == 422
        assert bueno.status_code == 303
        session.refresh(post)
        assert post.status == PostStatus.PUBLISHED
        assert "https://www.instagram.com/p/abc/" in client.get(f"/marketing/post/{post.id}").text

    def test_descartar_una_aprobada_borra_su_jpeg(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)
        archivo = fabrica / "posts" / post.slides[0].image_path

        respuesta = client.post(f"/marketing/post/{post.id}/descartar", follow_redirects=False)

        assert respuesta.status_code == 303
        assert respuesta.headers["location"] == "/marketing"
        assert not archivo.exists()
        session.refresh(post)
        assert post.status == PostStatus.DISCARDED

    def test_duplicar_abre_el_borrador_nuevo(
        self, client: TestClient, datos: None, fabrica: Path, session: Session  # noqa: F811
    ) -> None:
        post = _aprobada(session, fabrica)

        respuesta = client.post(f"/marketing/post/{post.id}/duplicar", follow_redirects=False)

        assert respuesta.status_code == 303
        copia = session.query(MarketingPost).filter(MarketingPost.id != post.id).one()
        assert respuesta.headers["location"] == f"/marketing/post/{copia.id}"
        assert copia.status == PostStatus.DRAFT
