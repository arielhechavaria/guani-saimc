"""Vista de control del operador — `GET /control`."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, Product, Province, RawIngestion, SaleFormat
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.web.app import app
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _cargar(session: Session, *, precios: list[str]) -> None:
    azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[])
    scu = Province(code="scu", name="Santiago de Cuba")
    session.add_all([azucar, scu])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/control.txt",
        content_hash="hash-control",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    for precio in precios:
        session.add(
            PriceHistory(
                product_id=azucar.id,
                province_id=scu.id,
                price=Decimal(precio),
                currency=Currency.CUP.value,
                source_id=origen.id,
            )
        )
    session.commit()


class TestControlPanel:
    def test_sin_datos_no_rompe(self, client: TestClient) -> None:
        response = client.get("/control")

        assert response.status_code == 200
        assert "no hay filas de precio" in response.text.lower()

    def test_muestra_la_celda_con_su_muestra_y_mediana(
        self, client: TestClient, session: Session
    ) -> None:
        _cargar(session, precios=["420", "440", "23500"])

        response = client.get("/control")

        assert response.status_code == 200
        assert "Azúcar blanca" in response.text
        assert "Santiago de Cuba" in response.text
        assert "440.00 CUP" in response.text  # mediana de 420, 440, 23500

    def test_la_amplitud_expone_la_celda_mezclada(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso real: minorista por libra (420) y saco mayorista (23500) en la
        misma celda. La amplitud lo hace visible sin emitir ningún juicio.
        """
        _cargar(session, precios=["420", "440", "23500"])

        response = client.get("/control")

        assert "56.0×" in response.text  # 23500 / 420

    def test_no_emite_veredictos_sobre_la_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        """Whitepaper §12.6: ningún umbral se inventa. La vista informa, no
        califica — nada de "insuficiente", "alta" o "baja".
        """
        _cargar(session, precios=["420"])

        texto = client.get("/control").text.lower()

        for veredicto in ("insuficiente", "dispersión alta", "muestra baja"):
            assert veredicto not in texto

    def test_cuenta_las_celdas_de_una_sola_observacion(
        self, client: TestClient, session: Session
    ) -> None:
        _cargar(session, precios=["420"])

        response = client.get("/control")

        assert "1 de 1 celdas tienen una sola" in response.text


class TestRangoMinorista:
    """`GET /control` también resume el precio minorista por producto,
    normalizado por unidad (2026-08-12): "en qué rango se movió Arroz",
    independiente del envase.
    """

    def _cargar_retail(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/rango.txt",
            content_hash="hash-rango-control",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        for precio in ("700", "800"):
            session.add(
                PriceHistory(
                    product_id=arroz.id,
                    province_id=scu.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    unit="lb",
                    sale_format=SaleFormat.RETAIL.value,
                    quantity=Decimal("1"),
                    source_id=origen.id,
                )
            )
        session.commit()

    def test_sin_filas_minoristas_normalizables_muestra_aviso(
        self, client: TestClient
    ) -> None:
        response = client.get("/control")

        assert response.status_code == 200
        assert "ninguna fila entra todavía en este rango" in response.text.lower()

    def test_muestra_la_mediana_normalizada_por_producto(
        self, client: TestClient, session: Session
    ) -> None:
        self._cargar_retail(session)

        response = client.get("/control")

        assert response.status_code == 200
        assert "750.00 CUP/lb" in response.text  # mediana de 700 y 800

    def test_dias_es_configurable_por_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        """La ventana real (qué fecha queda adentro o afuera) ya la cubre
        `test_ventana_de_dias_excluye_lo_viejo` en test_cells.py con una fecha
        fija — acá solo se verifica que `?days=` llega hasta la plantilla.
        """
        self._cargar_retail(session)

        response = client.get("/control?days=1")

        assert "últimos 1 días" in response.text
        assert "750.00 CUP/lb" in response.text


class TestFiltroDePilotos:
    """`/control` muestra solo los 4 productos piloto (2026-08-12): el resto
    del catálogo (dispersión de 2026-08-11) queda fuera de esta vista por
    ahora, no de la base — es un filtro de pantalla, no un borrado de datos.
    """

    def _cargar(
        self, session: Session, *, producto: str, provincia: str = "Santiago de Cuba"
    ) -> None:
        item = Product(name=producto, category="x", unit="lb", aliases=[])
        scu = session.execute(
            select(Province).where(Province.name == provincia)
        ).scalar_one_or_none()
        if scu is None:
            scu = Province(code="scu", name=provincia)
            session.add(scu)
        session.add(item)
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/{producto}.txt",
            content_hash=f"hash-{producto}",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=item.id,
                province_id=scu.id,
                price=Decimal("100"),
                currency=Currency.CUP.value,
                source_id=origen.id,
            )
        )
        session.commit()

    def test_oculta_productos_fuera_de_los_cuatro_pilotos(
        self, client: TestClient, session: Session
    ) -> None:
        self._cargar(session, producto="Arroz")
        self._cargar(session, producto="Sazon Dubai")

        response = client.get("/control")

        assert "Arroz" in response.text
        assert "Sazon Dubai" not in response.text

    def test_los_cuatro_pilotos_pasan_el_filtro(
        self, client: TestClient, session: Session
    ) -> None:
        for producto in ("Arroz", "Pollo", "Azúcar blanca", "Aceite"):
            self._cargar(session, producto=producto)

        texto = client.get("/control").text

        for producto in ("Arroz", "Pollo", "Azúcar blanca", "Aceite"):
            assert producto in texto

    def test_el_conteo_de_pilotos_ignora_los_productos_fuera_del_filtro(
        self, client: TestClient, session: Session
    ) -> None:
        """Esto se leía antes en el contador «Productos: N» de la cabecera. Lo
        reemplazó la tarjeta «Productos bandera», que además nombra cuáles
        faltan; el invariante que se prueba es el mismo, y algo más fuerte: una
        alta al vuelo fuera de los pilotos no infla el progreso de §9.
        """
        self._cargar(session, producto="Arroz")
        self._cargar(session, producto="Sazon Dubai")

        response = client.get("/control")

        assert '<span class="saimc-chip saimc-chip-done">Arroz</span>' in response.text
        assert "Sazon Dubai" not in response.text
        for faltante in ("Aceite", "Azúcar blanca", "Pollo"):
            assert f"{faltante} · sin dato" in response.text


class TestColumnaNEnlazaAlHistorial:
    """La columna `n` enlaza a `/curation/history` filtrado por la identidad
    exacta de la celda (2026-08-12) — «ver los anuncios detrás de este
    número» sin consultar la base a mano.
    """

    def test_el_link_de_una_celda_fundida_no_manda_moneda(
        self, client: TestClient, session: Session
    ) -> None:
        """Desde que `compute_cells` funde por `price_cup` (2026-08-13), una
        celda con `currency == "CUP"` puede mezclar filas que originalmente
        eran de otra moneda — mandar `&currency=CUP` al historial escondería
        esas filas del filtro. El link omite el parámetro para esa celda.
        """
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/link.txt",
            content_hash="hash-link",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=scu.id,
                price=Decimal("31000"),
                currency=Currency.CUP.value,
                unit="lb",
                sale_format=SaleFormat.WHOLESALE.value,
                source_id=origen.id,
            )
        )
        session.commit()

        response = client.get("/control")

        assert (
            "/curation/history?product=Arroz&province=Santiago%20de%20Cuba"
            "&unit=lb&sale_format=wholesale" in response.text
        )
        assert "currency=CUP" not in response.text

    def test_el_link_de_una_celda_sin_convertir_manda_su_moneda_original(
        self, client: TestClient, session: Session
    ) -> None:
        """Una celda `unconverted` no tiene ambigüedad: todas sus filas son
        de la misma moneda original, así que el filtro sí la manda.
        """
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/link-sin-convertir.txt",
            content_hash="hash-link-sin-convertir",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=scu.id,
                price=Decimal("10"),
                currency=Currency.USD.value,
                price_cup=None,
                unit="lb",
                sale_format=SaleFormat.WHOLESALE.value,
                source_id=origen.id,
            )
        )
        session.commit()

        response = client.get("/control")

        assert (
            "/curation/history?product=Arroz&province=Santiago%20de%20Cuba"
            "&currency=USD&unit=lb&sale_format=wholesale" in response.text
        )
        assert "sin convertir" in response.text


class TestPanelDeProgreso:
    """Las secciones que responden «¿cuánto falta y qué falta?» (spec.md §9).

    Todo lo que se compara contra un objetivo acá sale del documento rector:
    500 registros de 3–5 productos en 2 provincias (§9) y tasa de descarte
    <30% (§12.5). Ver `saimc.progress`.
    """

    def test_muestra_la_meta_de_500_registros(
        self, client: TestClient, session: Session
    ) -> None:
        _cargar(session, precios=["420", "440"])

        response = client.get("/control")

        assert "/ 500" in response.text
        assert "Faltan 498" in response.text

    def test_nombra_la_provincia_de_la_meta_que_falta(
        self, client: TestClient, session: Session
    ) -> None:
        """§9 nombra dos provincias. Con datos solo en Santiago, La Habana
        tiene que aparecer marcada como pendiente, no simplemente ausente."""
        _cargar(session, precios=["420"])

        response = client.get("/control")

        assert "La Habana · sin dato" in response.text
        assert '<span class="saimc-chip saimc-chip-done">Santiago de Cuba</span>' in response.text

    def test_los_huecos_enlazan_al_historial_filtrado(
        self, client: TestClient, session: Session
    ) -> None:
        """Un hueco sirve si se puede ir a arreglarlo. `_cargar` no completa
        `unit` ni `sale_format`, así que ambos huecos tienen que estar."""
        _cargar(session, precios=["420", "440"])

        response = client.get("/control")

        assert "/curation/history?unit=" in response.text
        assert "/curation/history?sale_format=" in response.text

    def test_la_cobertura_muestra_el_cero_de_lo_que_falta(
        self, client: TestClient, session: Session
    ) -> None:
        """La grilla producto × provincia se dibuja completa: el cero es
        justamente lo que dice qué salir a capturar."""
        _cargar(session, precios=["420"])

        response = client.get("/control")

        assert "Cobertura de la meta" in response.text
        # Los cuatro pilotos aparecen como fila aunque no tengan una sola fila
        # cargada; Pollo y Aceite no fueron cargados por `_cargar`.
        for piloto in ("Aceite", "Arroz", "Azúcar blanca", "Pollo"):
            assert piloto in response.text

    def test_avisa_cuando_el_descarte_supera_el_objetivo_del_documento(
        self, client: TestClient, session: Session
    ) -> None:
        """§12.5 fija la tasa de descarte objetivo en <30%. Con 1 curado y 2
        descartados la tasa es 67%."""
        _cargar(session, precios=["420"])
        for i in range(2):
            session.add(
                RawIngestion(
                    source_type=SourceType.INBOX_TEXT,
                    file_path=f"raw/descartado-{i}.txt",
                    content_hash=f"hash-descartado-{i}",
                    content_type=ContentType.TEXT,
                    processing_status=ProcessingStatus.DISCARDED,
                    captured_at=datetime(2026, 8, 1, tzinfo=UTC),
                )
            )
        session.commit()

        response = client.get("/control")

        assert "67%" in response.text
        assert "Por encima del objetivo" in response.text

    def test_sin_nada_decidido_no_inventa_una_tasa_de_descarte(
        self, client: TestClient
    ) -> None:
        """Cero decididos no es «0% de descarte»: es que no hay qué medir."""
        response = client.get("/control")

        assert "Nada decidido todavía" in response.text


class TestPanelDeTasaDeCambio:
    """Visibilidad de la tasa: qué hay disponible y qué quedó aplicado."""

    def test_muestra_la_tasa_cacheada_con_su_antiguedad(
        self, client: TestClient, session: Session
    ) -> None:
        from datetime import date

        from saimc.models import ExchangeRateCache

        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date.today(),
                rate=Decimal("670"),
                source="elTOQUE",
                fetched_at=datetime.now(UTC),
            )
        )
        session.commit()

        response = client.get("/control")

        assert "670.00" in response.text
        assert "Hay tasa de hoy" in response.text

    def test_una_moneda_sin_cache_se_muestra_igual(self, client: TestClient) -> None:
        """El hueco es el dato: sin tasa cacheada, un anuncio en esa moneda se
        guarda sin convertir si elTOQUE no responde."""
        response = client.get("/control")

        assert "EUR → CUP" in response.text
        assert "Nunca se cacheó una tasa" in response.text

    def test_no_inventa_una_ventana_de_validez(
        self, client: TestClient, session: Session
    ) -> None:
        """spec.md §12.6 lista la ventana de validez como parámetro pendiente
        de calibración. La vista informa antigüedad, nunca vigencia."""
        from datetime import date, timedelta

        from saimc.models import ExchangeRateCache

        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date.today() - timedelta(days=40),
                rate="670",
                source="elTOQUE",
                fetched_at=datetime.now(UTC),
            )
        )
        session.commit()

        texto = client.get("/control").text.lower()

        assert "hace 40 días" in texto
        for veredicto in ("vencida", "expirada", "caducada", "obsoleta"):
            assert veredicto not in texto
