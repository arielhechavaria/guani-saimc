"""Rutas del carrusel en el editor (F3 de `docs/plan-fabrica-marketing.md`):
agregar, subir, bajar y quitar diapositivas con botones, elegir cuál se
edita (`?d=<posición>`) y compartir todas las imágenes de una vez.

Reusa los fixtures de `test_marketing_post_router.py` (día, carpeta y render
inyectados) y los datos de `test_marketing_router.py`.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.marketing.posts import MAX_SLIDES, approve, create_carousel_draft
from saimc.models import MarketingPost
from saimc.web.app import app
from saimc.web.routers.marketing import get_today
from tests.test_marketing_post_router import HOY, _borrador, _jpeg_falso, fabrica  # noqa: F401
from tests.test_marketing_router import client, datos  # noqa: F401

CUATRO = ("carrusel-portada", "carrusel-consejo", "dato-del-dia", "carrusel-cierre")
#: Un miércoles cualquiera: el día del carrusel en la semana tipo.
MIERCOLES = date(2026, 10, 21)


def _carrusel(session: Session, planned_for: date = HOY) -> MarketingPost:
    post = create_carousel_draft(
        session,
        card_slugs=CUATRO,
        planned_for=planned_for,
        product="Arroz",
        province=None,
        data_through=planned_for,
    )
    session.commit()
    return post


def _slugs(session: Session, post: MarketingPost) -> list[str]:
    session.expire_all()
    releida = session.get(MarketingPost, post.id)
    assert releida is not None
    return [s.card_slug for s in releida.slides]


class TestDiapositivas:
    def test_agregar_eligiendo_plantilla_lleva_a_la_nueva(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        r = client.post(
            f"/marketing/post/{post.id}/diapositivas",
            data={"kind": "como-se-movio"},
            follow_redirects=False,
        )

        assert r.status_code == 303
        assert r.headers["location"] == f"/marketing/post/{post.id}?d=5"
        assert _slugs(session, post) == [*CUATRO, "como-se-movio"]

    def test_pasado_el_tope_no_agrega_y_lo_dice(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = create_carousel_draft(
            session,
            card_slugs=("carrusel-cierre",) * MAX_SLIDES,
            planned_for=HOY,
            product="Arroz",
            province=None,
            data_through=HOY,
        )
        session.commit()

        r = client.post(f"/marketing/post/{post.id}/diapositivas", data={"kind": "carrusel-cierre"})

        assert r.status_code == 422
        assert "10" in r.text
        assert len(_slugs(session, post)) == MAX_SLIDES

    def test_con_10_no_ofrece_agregar(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = create_carousel_draft(
            session,
            card_slugs=("carrusel-cierre",) * MAX_SLIDES,
            planned_for=HOY,
            product="Arroz",
            province=None,
            data_through=HOY,
        )
        session.commit()

        html = client.get(f"/marketing/post/{post.id}").text

        assert f"/marketing/post/{post.id}/diapositivas" not in html
        assert "10 de 10" in html

    def test_subir_bajar_y_quitar(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        r = client.post(f"/marketing/post/{post.id}/diapositiva/3/subir", follow_redirects=False)
        assert r.headers["location"] == f"/marketing/post/{post.id}?d=2"
        assert _slugs(session, post) == [
            "carrusel-portada",
            "dato-del-dia",
            "carrusel-consejo",
            "carrusel-cierre",
        ]

        r = client.post(f"/marketing/post/{post.id}/diapositiva/1/bajar", follow_redirects=False)
        assert r.headers["location"] == f"/marketing/post/{post.id}?d=2"
        assert _slugs(session, post)[:2] == ["dato-del-dia", "carrusel-portada"]

        r = client.post(f"/marketing/post/{post.id}/diapositiva/4/quitar", follow_redirects=False)
        assert r.headers["location"] == f"/marketing/post/{post.id}?d=3"
        assert _slugs(session, post) == ["dato-del-dia", "carrusel-portada", "carrusel-consejo"]

    def test_una_aprobada_no_se_reordena(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        approve(session, post, images_dir=fabrica / "posts", render=_jpeg_falso)
        session.commit()

        for accion in ("subir", "bajar", "quitar"):
            r = client.post(f"/marketing/post/{post.id}/diapositiva/2/{accion}")
            assert r.status_code == 409
        r = client.post(f"/marketing/post/{post.id}/diapositivas", data={"kind": "carrusel-cierre"})
        assert r.status_code == 409
        assert _slugs(session, post) == list(CUATRO)


class TestEditorDeCarrusel:
    def test_lista_las_diapositivas_con_flechas_y_previsualiza_la_elegida(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        html = client.get(f"/marketing/post/{post.id}?d=3").text

        for n in range(1, 5):
            assert f"/marketing/post/{post.id}/diapositiva/{n}/quitar" in html
        # La primera no sube y la última no baja.
        assert "/diapositiva/1/subir" not in html
        assert "/diapositiva/4/bajar" not in html
        assert f"/marketing/post/{post.id}/preview/3" in html
        # El formulario edita la elegida: la de datos tiene producto.
        assert 'name="position" value="3"' in html
        assert 'name="product"' in html

    def test_guardar_con_htmx_edita_la_diapositiva_elegida(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        r = client.post(
            f"/marketing/post/{post.id}",
            data={"position": "3", "product": "Arroz", "data_through": HOY.isoformat(), "b_comparacion": "0"},
            headers={"HX-Request": "true"},
        )

        assert r.status_code == 200
        assert f"/marketing/post/{post.id}/preview/3" in r.text
        session.expire_all()
        releida = session.get(MarketingPost, post.id)
        assert releida is not None
        assert releida.slides[2].options["show"]["comparacion"] is False

    def test_una_posicion_inexistente_cae_en_la_primera(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        html = client.get(f"/marketing/post/{post.id}?d=99").text

        assert f"/marketing/post/{post.id}/preview/1" in html

    def test_aprobada_comparte_todas_en_orden_y_ofrece_guardarlas(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        approve(session, post, images_dir=fabrica / "posts", render=_jpeg_falso)
        session.commit()

        html = client.get(f"/marketing/post/{post.id}").text

        bruto = re.search(r"data-mk-files='([^']+)'", html)
        assert bruto is not None
        archivos = json.loads(bruto.group(1).replace("&#34;", '"').replace("&quot;", '"'))
        assert [a["url"] for a in archivos] == [
            f"/marketing/post/{post.id}/slide/{n}.jpg" for n in (1, 2, 3, 4)
        ]
        assert [a["name"][-6:] for a in archivos] == ["-1.jpg", "-2.jpg", "-3.jpg", "-4.jpg"]
        assert "Descargar esta diapositiva" in html
        assert f'href="/marketing/post/{post.id}/imagenes.zip"' in html
        assert "Guardar todas" not in html
        for n in (1, 2, 3, 4):
            assert client.get(f"/marketing/post/{post.id}/slide/{n}.jpg").status_code == 200

    def test_el_zip_trae_las_cuatro_en_orden_y_con_el_contenido_del_disco(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        approve(session, post, images_dir=fabrica / "posts", render=_jpeg_falso)
        session.commit()

        respuesta = client.get(f"/marketing/post/{post.id}/imagenes.zip")

        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"] == "application/zip"
        assert respuesta.headers["content-disposition"].startswith('attachment; filename="guani-')
        with zipfile.ZipFile(io.BytesIO(respuesta.content)) as z:
            assert z.namelist() == [
                f"0{n}-guani-carrusel-{HOY.isoformat()}-{n}.jpg" for n in (1, 2, 3, 4)
            ]
            for slide in post.slides:
                en_disco = (fabrica / "posts" / str(slide.image_path)).read_bytes()
                assert z.read(z.namelist()[slide.position - 1]) == en_disco

    def test_el_zip_de_un_borrador_da_404(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,  # noqa: F811
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)

        assert client.get(f"/marketing/post/{post.id}/imagenes.zip").status_code == 404

    def test_el_zip_con_un_archivo_faltante_da_404(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        post = _carrusel(session)
        approve(session, post, images_dir=fabrica / "posts", render=_jpeg_falso)
        session.commit()
        (fabrica / "posts" / str(post.slides[2].image_path)).unlink()

        assert client.get(f"/marketing/post/{post.id}/imagenes.zip").status_code == 404


class TestHoyConCarrusel:
    def test_el_miercoles_avisa_que_falta_el_carrusel(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: MIERCOLES

        html = client.get("/marketing").text

        assert "Falta «Carrusel»" in html

    def test_con_el_carrusel_creado_ya_no_falta_y_dice_cuantas_tiene(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: MIERCOLES
        post = _carrusel(session, MIERCOLES)

        html = client.get("/marketing").text

        assert "Falta «Carrusel»" not in html
        assert f"/marketing/post/{post.id}" in html
        assert "4 diapositivas" in html

    def test_crear_el_carrusel_desde_hoy_arma_las_cuatro(
        self,
        client: TestClient,
        datos: None,
        fabrica: Path,
        session: Session,  # noqa: F811
    ) -> None:
        app.dependency_overrides[get_today] = lambda: MIERCOLES

        r = client.post("/marketing/post", data={"kind": "carrusel"}, follow_redirects=False)

        assert r.status_code == 303
        post_id = r.headers["location"].rsplit("/", 1)[1]
        post = session.get(MarketingPost, uuid.UUID(post_id))
        assert post is not None
        assert [s.card_slug for s in post.slides] == list(CUATRO)
        assert post.format == "carousel"
