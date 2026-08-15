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
    azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[], is_pilot=True)
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

    def test_la_matriz_muestra_una_provincia_por_pestana(
        self, client: TestClient, session: Session
    ) -> None:
        """Cada pestaña dibuja una sola provincia. La otra sigue nombrada en la
        barra de pestañas con su muestra, para saber qué hay del otro lado sin
        abrirla."""
        self._cargar_dos_provincias(session)

        texto = client.get("/control").text

        # Un solo cruce por tarjeta: la provincia de la pestaña activa.
        assert texto.count('class="saimc-col saimc-slot-') == 2
        # Las dos pestañas existen. Activa la primera del catálogo (slot 0 es
        # La Habana), no la de más muestra: si la eligieran los datos, la
        # pestaña inicial cambiaría sola de un día para el otro.
        assert 'class="saimc-tab saimc-slot-0 is-active"' in texto
        assert 'class="saimc-tab saimc-slot-1"' in texto
        assert 'href="?days=7&amp;province=Santiago%20de%20Cuba"' in texto

    def test_la_pestana_se_elige_por_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        """El estado vive en la URL: la pestaña se puede compartir y sobrevive
        a recargar, sin JavaScript de por medio."""
        self._cargar_dos_provincias(session)

        texto = client.get("/control?province=Santiago de Cuba").text
        tarjetas = texto.split('<details class="saimc-prod"')[1:]
        paneles = [t.split("</summary>")[1] for t in tarjetas]

        # El panel dibuja Santiago; La Habana ya no aparece como columna.
        assert all("Santiago de Cuba" in p for p in paneles)
        assert not any('saimc-col-name">La Habana' in p for p in paneles)

    def test_una_provincia_desconocida_no_rompe_la_pagina(
        self, client: TestClient, session: Session
    ) -> None:
        """Una URL vieja o tipeada a mano cae en la primera provincia, no en un
        500."""
        self._cargar_dos_provincias(session)

        response = client.get("/control?province=Camagüey")

        assert response.status_code == 200
        assert "La Habana" in response.text

    def test_el_hueco_de_cobertura_sigue_visible_dentro_de_la_pestana(
        self, client: TestClient, session: Session
    ) -> None:
        """Pollo no tiene nada en Santiago. Su tarjeta no desaparece de esa
        pestaña: cae al final con su cero, porque esconderla sería volver a no
        poder ver qué falta cargar."""
        self._cargar_dos_provincias(session)

        texto = client.get("/control?province=Santiago de Cuba").text
        tarjetas = texto.split('<details class="saimc-prod"')[1:]

        assert "sin series cargadas" in tarjetas[-1]
        assert "Pollo" in tarjetas[-1].split("</summary>")[0]

    def _cargar_dos_provincias(self, session: Session) -> None:
        azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[], is_pilot=True)
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([azucar, pollo, habana, santiago])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/matriz.txt",
            content_hash="hash-matriz",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        for producto, provincia, precio in (
            (azucar, habana, "400"),
            (azucar, santiago, "420"),
            (pollo, habana, "700"),
        ):
            session.add(
                PriceHistory(
                    product_id=producto.id,
                    province_id=provincia.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    source_id=origen.id,
                )
            )
        session.commit()

    def test_solo_la_tarjeta_de_mayor_muestra_arranca_abierta(
        self, client: TestClient, session: Session
    ) -> None:
        """Con todas cerradas la vista cargaría en blanco, que es peor que la
        lista larga que el colapso vino a resolver; con todas abiertas el
        colapso no haría nada."""
        self._cargar_dos_productos(session)

        texto = client.get("/control").text

        assert texto.count('<details class="saimc-prod"') == 2
        assert texto.count('<details class="saimc-prod" open>') == 1
        # La abierta es la de más muestra: Azúcar (9) antes que Pollo (2).
        abierta = texto.split('<details class="saimc-prod" open>')[1]
        assert "Azúcar blanca" in abierta.split("</summary>")[0]

    def test_la_tarjeta_cerrada_sigue_diciendo_donde_esta_la_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        """El reparto por provincia va en la cabecera. Sin eso, plegar una
        tarjeta escondería justamente lo que la vista existe para mostrar."""
        self._cargar_dos_productos(session)

        texto = client.get("/control").text
        # Acotado a las tarjetas de la matriz: «Pollo» también aparece antes,
        # en la sección de cobertura.
        tarjetas = texto.split('<details class="saimc-prod"')[1:]
        cabecera_pollo = next(
            t.split("</summary>")[0] for t in tarjetas if "Pollo" in t.split("</summary>")[0]
        )

        assert "La Habana 2" in cabecera_pollo
        assert "Santiago de Cuba 0" in cabecera_pollo

    def test_el_colapso_no_trae_javascript(
        self, client: TestClient, session: Session
    ) -> None:
        """`<details>` nativo: nada que descargar con la conexión caída."""
        self._cargar_dos_productos(session)

        texto = client.get("/control").text

        assert "<script" not in texto

    def _cargar_dos_productos(self, session: Session) -> None:
        azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[], is_pilot=True)
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([azucar, pollo, habana, santiago])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/colapso.txt",
            content_hash="hash-colapso",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        filas = [(azucar, habana, "400")] * 5
        filas += [(azucar, santiago, "420")] * 4
        filas += [(pollo, habana, "700")] * 2
        for producto, provincia, precio in filas:
            session.add(
                PriceHistory(
                    product_id=producto.id,
                    province_id=provincia.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    source_id=origen.id,
                )
            )
        session.commit()

    def test_la_columna_de_provincia_no_usa_color_de_semaforo(
        self, client: TestClient, session: Session
    ) -> None:
        """spec.md §12.6: el color codifica identidad de provincia, no
        veredicto. Rojo o verde afirmarían un umbral que nadie fijó."""
        _cargar(session, precios=["420", "440"])

        texto = client.get("/control").text

        for semaforo in ("#dc3545", "#198754", "text-danger", "text-success"):
            assert semaforo not in texto.split("<!-- ---------- 8.")[1]


class TestRangoMinorista:
    """`GET /control` también resume el precio minorista por producto,
    normalizado por unidad (2026-08-12): "en qué rango se movió Arroz",
    independiente del envase.
    """

    def _cargar_retail(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
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
    """`/control` muestra solo los productos piloto (2026-08-12): el resto
    del catálogo (dispersión de 2026-08-11) queda fuera de esta vista por
    ahora, no de la base — es un filtro de pantalla, no un borrado de datos.

    Desde el 2026-08-15 el conjunto piloto sale de `products.is_pilot` y no de
    una tupla fija en código, así que estos tests lo declaran en la base.
    """

    def _declarar_piloto(self, session: Session, nombre: str) -> Product:
        """Piloto sin observaciones: es el estado «falta cargar esto»."""
        item = Product(name=nombre, category="x", unit="lb", aliases=[], is_pilot=True)
        session.add(item)
        session.commit()
        return item

    def _cargar(
        self,
        session: Session,
        *,
        producto: str,
        provincia: str = "Santiago de Cuba",
        is_pilot: bool = True,
    ) -> None:
        item = Product(name=producto, category="x", unit="lb", aliases=[], is_pilot=is_pilot)
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
        self._cargar(session, producto="Sazon Dubai", is_pilot=False)

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
        self._cargar(session, producto="Sazon Dubai", is_pilot=False)
        for faltante in ("Aceite", "Azúcar blanca", "Pollo"):
            self._declarar_piloto(session, faltante)

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
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
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
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
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
        # El conjunto piloto vive en la base desde el 2026-08-15: los que
        # `_cargar` no crea hay que declararlos para que la grilla los dibuje.
        for faltante in ("Aceite", "Arroz", "Pollo"):
            if session.execute(
                select(Product).where(Product.name == faltante)
            ).scalar_one_or_none() is None:
                session.add(
                    Product(name=faltante, category="x", unit="lb", aliases=[], is_pilot=True)
                )
        session.commit()

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
