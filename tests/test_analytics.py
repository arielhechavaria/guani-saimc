"""Instrumentación mínima de la capa pública (spec.md §9, Fase 1: «100
usuarios activos diarios»). Ver el docstring de `saimc.web.analytics`.

Lo que estos casos protegen: que la línea de log solo salga para una visita
real a una vista pública (no assets, no rutas de operador, no fragmentos
htmx de la misma página, no un 404), y que nunca lleve la IP en texto plano.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import ContentType, Product, RawIngestion, SourceType
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.analytics import es_vista_publica_rastreable, hash_visitante
from saimc.web.app import app
from saimc.web.deps import get_db

_LOGGER = "saimc.web.analytics"


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _crear_ingestion(session: Session) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/evidencia.dat",
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.TEXT,
        original_text="Arroz 25kg a 8.500 CUP",
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
    )
    session.add(ingestion)
    session.commit()
    return ingestion


class TestFuncionesPuras:
    def test_rastrea_home_producto_y_anuncio(self) -> None:
        assert es_vista_publica_rastreable("/")
        assert es_vista_publica_rastreable("/producto/arroz")
        assert es_vista_publica_rastreable("/anuncio/" + str(uuid.uuid4()))

    def test_no_rastrea_assets_ni_rutas_de_operador(self) -> None:
        assert not es_vista_publica_rastreable("/static/saimc-public.css")
        assert not es_vista_publica_rastreable("/control")
        assert not es_vista_publica_rastreable("/curation")
        assert not es_vista_publica_rastreable("/catalog")

    def test_el_hash_nunca_es_la_ip_en_claro(self) -> None:
        resultado = hash_visitante("203.0.113.7", "2026-09-14")

        assert "203.0.113.7" not in resultado

    def test_el_hash_es_estable_para_la_misma_ip_y_dia(self) -> None:
        a = hash_visitante("203.0.113.7", "2026-09-14")
        b = hash_visitante("203.0.113.7", "2026-09-14")

        assert a == b

    def test_el_hash_cambia_de_un_dia_a_otro(self) -> None:
        """El hash no es criptografía fuerte a propósito (ver el docstring del
        módulo) — lo que sí tiene que sostener es que dos días distintos no
        colisionen sistemáticamente, o la cuenta de "activos por día" se
        rompe."""
        a = hash_visitante("203.0.113.7", "2026-09-14")
        b = hash_visitante("203.0.113.7", "2026-09-15")

        assert a != b


class TestMiddlewareEnLasVistasPublicas:
    def test_la_home_deja_rastro(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/precios")

        registro = next(r for r in caplog.records if "public.pageview" in r.message)
        assert "path=/" in registro.message
        assert "visitor=" in registro.message

    def test_producto_enfocado_deja_rastro(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        session.add(
            Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        )
        session.commit()

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/producto/arroz")

        assert any(
            "public.pageview path=/producto/arroz" in r.message for r in caplog.records
        )

    def test_el_anuncio_publico_deja_rastro(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        ingestion = _crear_ingestion(session)

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get(f"/anuncio/{ingestion.id}")

        assert any(
            f"public.pageview path=/anuncio/{ingestion.id}" in r.message
            for r in caplog.records
        )

    def test_el_anuncio_inexistente_no_deja_rastro(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """404: no hubo vista real que registrar."""
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            respuesta = client.get(f"/anuncio/{uuid.uuid4()}")

        assert respuesta.status_code == 404
        assert not any("public.pageview" in r.message for r in caplog.records)

    def test_un_fragmento_htmx_de_la_misma_pagina_no_deja_rastro(
        self, client: TestClient, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Un chip togglea `?productos=` con `hx-get`: es la misma visita
        reabriendo `/`, no una visita nueva — contarla infla el número sin
        que haya llegado nadie más a la página."""
        session.add(
            Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        )
        session.commit()

        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/precios?productos=arroz", headers={"HX-Request": "true"})

        assert not any("public.pageview" in r.message for r in caplog.records)

    def test_una_ruta_de_operador_no_deja_rastro(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/control")

        assert not any("public.pageview" in r.message for r in caplog.records)

    def test_un_asset_estatico_no_deja_rastro(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/static/saimc-public.css")

        assert not any("public.pageview" in r.message for r in caplog.records)

    def test_la_ip_del_visitante_nunca_llega_al_log_en_claro(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            client.get("/precios")

        registro = next(r for r in caplog.records if "public.pageview" in r.message)
        # El TestClient pega desde "testclient" (Starlette) — lo que importa
        # es que la línea trae `visitor=<hash>` y no un campo `ip=`.
        assert "ip=" not in registro.message
        assert "testclient" not in registro.message
