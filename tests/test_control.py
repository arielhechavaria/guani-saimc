"""Vista de control del operador — `GET /control`."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pathlib import Path

from saimc.config import get_settings
from saimc.extraction import llm_assist
from saimc.ingestion.whatsapp_health import SessionHealth
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.extraction.measure import H0RunSummary
from saimc.models import (
    Currency,
    PriceHistory,
    Product,
    ProductFormat,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db
from saimc.web.routers import control


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
        assert texto.count('class="m3-matrix-body m3-slot-') == 2
        # Las dos pestañas existen. Activa la primera del catálogo (slot 0 es
        # La Habana), no la de más muestra: si la eligieran los datos, la
        # pestaña inicial cambiaría sola de un día para el otro.
        assert 'class="m3-tab m3-slot-0 is-active"' in texto
        assert 'class="m3-tab m3-slot-1"' in texto
        assert 'href="?days=7&amp;province=Santiago%20de%20Cuba"' in texto

    def test_la_pestana_se_elige_por_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        """El estado vive en la URL: la pestaña se puede compartir y sobrevive
        a recargar, sin JavaScript de por medio."""
        self._cargar_dos_provincias(session)

        texto = client.get("/control?province=Santiago de Cuba").text
        tarjetas = texto.split('<details class="m3-card m3-matrix-card"')[1:]
        paneles = [t.split("</summary>")[1] for t in tarjetas]

        # El panel dibuja Santiago; La Habana ya no aparece como columna.
        assert all("Santiago de Cuba" in p for p in paneles)
        assert not any('m3-col-name">La Habana' in p for p in paneles)

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
        tarjetas = texto.split('<details class="m3-card m3-matrix-card"')[1:]

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

        assert texto.count('<details class="m3-card m3-matrix-card"') == 2
        assert texto.count('<details class="m3-card m3-matrix-card" open>') == 1
        # La abierta es la de más muestra: Azúcar (9) antes que Pollo (2).
        abierta = texto.split('<details class="m3-card m3-matrix-card" open>')[1]
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
        tarjetas = texto.split('<details class="m3-card m3-matrix-card"')[1:]
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



class TestLaBarraDeRangoHablaLaUnidadDelTitular:
    """Reporte del operador (2026-09-05): una celda de «paquete de 1 kg ·
    minorista» titulaba 688,70 CUP por paquete y la barra decía 273,52–385,55.
    Revisó los 22 anuncios y ninguno traía esos valores — porque no eran
    precios de paquete sino precios por libra, la unidad canónica del producto.
    El titular pasó al formato el 2026-09-04 (design.md D8) y los extremos de
    la barra se quedaron en la unidad base: dos escalas en la misma línea.
    """

    def _celda_con_formato(self, session: Session, *, precios: list[str]) -> None:
        """Azúcar canónica en lb, vendida en paquete de 1 kg. Los precios son
        por paquete, que es lo que dice el anuncio."""
        azucar = Product(
            name="Azúcar blanca", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([azucar, scu])
        session.commit()
        paquete = ProductFormat(
            product_id=azucar.id, packaging="paquete", quantity=Decimal("1"), unit="kg"
        )
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/barra.txt",
            content_hash="hash-barra",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add_all([paquete, origen])
        session.flush()
        for precio in precios:
            session.add(
                PriceHistory(
                    product_id=azucar.id,
                    province_id=scu.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    source_id=origen.id,
                    unit="kg",
                    quantity=Decimal("1"),
                    sale_format=SaleFormat.RETAIL.value,
                    format_id=paquete.id,
                )
            )
        session.commit()

    def test_los_extremos_son_precios_de_paquete_y_no_por_libra(
        self, client: TestClient, session: Session
    ) -> None:
        """Tres anuncios de 603, 700 y 850 CUP el paquete. La barra tiene que
        decir 603 y 850 — números que el operador puede reconocer en la cola —
        y no su equivalente por libra (273,52 y 385,55), que no dijo nadie."""
        self._celda_con_formato(session, precios=["603", "700", "850"])

        matriz = client.get("/control").text.split("<!-- ---------- 10.")[1]

        assert "603.00" in matriz
        assert "850.00" in matriz
        assert "273.52" not in matriz
        assert "385.55" not in matriz

    def test_el_tooltip_de_la_mediana_acompana_a_los_extremos(
        self, client: TestClient, session: Session
    ) -> None:
        """La marca de la mediana vive dentro de la barra, así que su tooltip
        tiene que hablar la misma escala que los dos extremos: 700 el paquete,
        no 317,51 la libra."""
        self._celda_con_formato(session, precios=["603", "700", "850"])

        matriz = client.get("/control").text.split("<!-- ---------- 10.")[1]

        assert 'title="Mediana 700.00"' in matriz

    def test_una_sola_observacion_se_reporta_en_precio_de_paquete(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin rango que dibujar la barra se reemplaza por una frase, y esa
        frase es el mismo número leído de otra forma: tiene que seguir siendo
        el precio del paquete. El 273,52 por libra sigue apareciendo aparte,
        como comparador entre formatos —ahí sí corresponde—."""
        self._celda_con_formato(session, precios=["603"])

        matriz = client.get("/control").text.split("<!-- ---------- 10.")[1]

        assert "Una sola observación: 603.00" in matriz
        assert "273.52/lb para comparar" in matriz

    def test_sin_formato_la_barra_sigue_en_la_unidad_base(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin formato declarado no hay precio de formato que derivar: el
        titular ya es el precio por unidad y la barra lo acompaña."""
        _cargar(session, precios=["420", "440"])

        matriz = client.get("/control").text.split("<!-- ---------- 10.")[1]

        assert "420.00" in matriz
        assert "440.00" in matriz

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


class TestDisponibilidad:
    """`GET /control` también muestra cuántos anuncios minoristas hubo esta
    ventana contra la anterior, sin score ni umbral (spec.md §7.3, §12.6).
    """

    def test_sin_anuncios_muestra_aviso(self, client: TestClient) -> None:
        response = client.get("/control")

        assert response.status_code == 200
        assert "ninguna celda tiene anuncios en esta ventana" in response.text.lower()

    def test_muestra_conteo_actual_anterior_y_diferencia(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/disponibilidad.txt",
            content_hash="hash-disponibilidad-control",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        for precio in ("700", "800"):  # ventana actual: sin published_date -> hoy
            session.add(
                PriceHistory(
                    product_id=arroz.id, province_id=scu.id, price=Decimal(precio),
                    currency=Currency.CUP.value, unit="lb",
                    sale_format=SaleFormat.RETAIL.value, quantity=Decimal("1"),
                    source_id=origen.id,
                )
            )
        session.commit()

        response = client.get("/control")

        assert response.status_code == 200
        assert "Disponibilidad" in response.text
        assert "Arroz" in response.text
        # 2 anuncios en la ventana actual, 0 en la anterior -> +2.
        assert "+2" in response.text

    def test_filtra_por_pilotos(self, client: TestClient, session: Session) -> None:
        no_piloto = Product(
            name="Producto no piloto", category="otros", unit="lb", aliases=[], is_pilot=False
        )
        session.add(no_piloto)
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/disponibilidad-no-piloto.txt",
            content_hash="hash-disponibilidad-no-piloto",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=no_piloto.id, price=Decimal("100"),
                currency=Currency.CUP.value, unit="lb",
                sale_format=SaleFormat.RETAIL.value, quantity=Decimal("1"),
                source_id=origen.id,
            )
        )
        session.commit()

        response = client.get("/control")

        assert response.status_code == 200
        assert "Producto no piloto" not in response.text


class TestExclusionPorBasisSinResolver:
    """`price_basis` sin resolver excluye una fila del rango minorista, con un
    conteo separado del de compra mínima — el operador necesita distinguir
    el motivo para priorizar qué cola de curaduría atacar primero (2026-08-21,
    public-price-range spec).
    """

    def test_muestra_el_conteo_separado_del_de_minimo(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[], is_pilot=True)
        session.add(arroz)
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/basis-control.txt",
            content_hash="hash-basis-control",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add_all(
            [
                # Fuera por basis sin resolver (quantity != 1, price_basis NULL).
                PriceHistory(
                    product_id=arroz.id,
                    price=Decimal("340"),
                    currency=Currency.CUP.value,
                    unit="kg",
                    sale_format=SaleFormat.RETAIL.value,
                    quantity=Decimal("340"),
                    source_id=origen.id,
                ),
                # Fuera por compra mínima, motivo distinto.
                PriceHistory(
                    product_id=arroz.id,
                    price=Decimal("730"),
                    currency=Currency.CUP.value,
                    unit="kg",
                    sale_format=SaleFormat.RETAIL.value,
                    quantity=Decimal("1"),
                    min_purchase_quantity=Decimal("30"),
                    source_id=origen.id,
                ),
            ]
        )
        session.commit()

        response = client.get("/control")

        assert response.status_code == 200
        # Conteo de basis sin resolver, en su propio banner — no fusionado
        # con el texto de compra mínima ya existente.
        assert "quedó fuera de este rango por no declarar" in response.text
        assert "quedó fuera de este rango por compra mínima" in response.text


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

        assert '<span class="m3-chip m3-chip-ok m3-chip-sm">Arroz</span>' in response.text
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


class TestDrillDownAislaLaCeldaExacta:
    """El link `n =` tiene que llevar a *las filas de esa celda*, ni una más.

    Desde que la matriz funde kg con lb (2026-09-02) la celda ya no se
    identifica por la columna cruda `prices_history.unit`: `/curation/history`
    recomputa `resolve_row` fila por fila y filtra por la unidad efectiva más
    los tres flags (`unquantified`, `unresolved_basis`, `unconverted_unit`).
    Antes de esto, el link de una celda de una sola observación caía sobre
    todo el bloque producto/provincia/formato.
    """

    @pytest.fixture(autouse=True)
    def _sin_auth(self) -> Iterator[None]:
        # `/curation/history` exige operador; su auth tiene cobertura propia
        # en test_curation_auth.py.
        app.dependency_overrides[require_operator] = lambda: None
        yield
        app.dependency_overrides.pop(require_operator, None)

    def _curado(
        self,
        session: Session,
        *,
        product: Product,
        province: Province,
        ref: str,
        price: str,
        unit: str,
        sale_format: str,
        quantity: Decimal | None = Decimal("1"),
        price_basis: str | None = None,
    ) -> RawIngestion:
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/{ref}.txt",
            content_hash=f"hash-{ref}",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            source_reference=ref,
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=product.id,
                province_id=province.id,
                price=Decimal(price),
                currency=Currency.CUP.value,
                unit=unit,
                quantity=quantity,
                price_basis=price_basis,
                sale_format=sale_format,
                source_id=origen.id,
            )
        )
        session.commit()
        return origen

    @staticmethod
    def _href(response_text: str, *, contains: str) -> str:
        matches = re.findall(
            r'href="(/curation/history\?product=Pollo[^"]*)"', response_text
        )
        for href in matches:
            if contains in href:
                return href.replace("&amp;", "&")
        raise AssertionError(f"no hay link de celda que contenga {contains!r}")

    @staticmethod
    def _cuenta_anuncios(response_text: str) -> int:
        return len(re.findall(r'href="/curation/[0-9a-f-]+/edit"', response_text))

    def test_una_celda_resuelta_no_arrastra_la_celda_sin_basis_vecina(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([pollo, habana])
        session.commit()
        self._curado(
            session, product=pollo, province=habana, ref="resuelta",
            price="585.56", unit="lb", sale_format=SaleFormat.WHOLESALE.value,
        )
        self._curado(
            session, product=pollo, province=habana, ref="sin-basis",
            price="7810.00", unit="kg", quantity=Decimal("5"), price_basis=None,
            sale_format=SaleFormat.WHOLESALE.value,
        )

        control_html = client.get("/control").text
        href = self._href(control_html, contains="unresolved_basis=0")

        historial = client.get(href).text
        assert "585.56" in historial
        assert "7810.00" not in historial
        assert self._cuenta_anuncios(historial) == 1

    def test_la_celda_sin_basis_tampoco_arrastra_la_resuelta(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([pollo, habana])
        session.commit()
        self._curado(
            session, product=pollo, province=habana, ref="resuelta",
            price="585.56", unit="lb", sale_format=SaleFormat.WHOLESALE.value,
        )
        self._curado(
            session, product=pollo, province=habana, ref="sin-basis",
            price="7810.00", unit="kg", quantity=Decimal("5"), price_basis=None,
            sale_format=SaleFormat.WHOLESALE.value,
        )

        control_html = client.get("/control").text
        href = self._href(control_html, contains="unresolved_basis=1")

        historial = client.get(href).text
        assert "7810.00" in historial
        assert "585.56" not in historial
        assert self._cuenta_anuncios(historial) == 1

    def test_una_celda_sin_cantidad_se_separa_de_la_resuelta_de_su_misma_unidad(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([pollo, habana])
        session.commit()
        self._curado(
            session, product=pollo, province=habana, ref="con-cantidad",
            price="703.50", unit="lb", sale_format=SaleFormat.RETAIL.value,
        )
        self._curado(
            session, product=pollo, province=habana, ref="sin-cantidad",
            price="8500.00", unit="lb", quantity=None,
            sale_format=SaleFormat.RETAIL.value,
        )

        control_html = client.get("/control").text

        solo_sin_cantidad = client.get(self._href(control_html, contains="unquantified=1")).text
        assert "8500.00" in solo_sin_cantidad
        assert "703.50" not in solo_sin_cantidad
        assert self._cuenta_anuncios(solo_sin_cantidad) == 1

        solo_resuelta = client.get(self._href(control_html, contains="unquantified=0")).text
        assert "703.50" in solo_resuelta
        assert "8500.00" not in solo_resuelta
        assert self._cuenta_anuncios(solo_resuelta) == 1

    def test_la_celda_fundida_sigue_incluyendo_las_filas_convertidas_de_otra_unidad(
        self, client: TestClient, session: Session
    ) -> None:
        """La razón por la que el link antes omitía `unit=`: no esconder las
        filas que entraron en kg y se convirtieron a lb. El filtro por unidad
        efectiva las conserva.
        """
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([pollo, habana])
        session.commit()
        self._curado(
            session, product=pollo, province=habana, ref="en-libras",
            price="700.00", unit="lb", sale_format=SaleFormat.RETAIL.value,
        )
        self._curado(
            session, product=pollo, province=habana, ref="en-kilos",
            price="1543.21", unit="kg", sale_format=SaleFormat.RETAIL.value,
        )

        control_html = client.get("/control").text
        # Una sola celda fundida: n = 2, unidad efectiva lb.
        # `format_id=` (vacío = sin formato) va entre `sale_format` y los flags
        # desde que el formato entra a la identidad de la celda.
        href = self._href(
            control_html, contains="unit=lb&sale_format=retail&format_id=&unquantified=0"
        )

        historial = client.get(href).text
        assert "700.00" in historial
        assert "1543.21" in historial
        assert self._cuenta_anuncios(historial) == 2


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
        assert '<span class="m3-chip m3-chip-ok m3-chip-sm">Santiago de Cuba</span>' in response.text

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
        # El chip resume; el banner explica. El aviso son las dos cosas.
        assert "sobre el 30%" in response.text
        assert "Se descarta más de lo que §12.5 pone como objetivo" in response.text

    def test_sin_nada_decidido_no_inventa_una_tasa_de_descarte(
        self, client: TestClient
    ) -> None:
        """Cero decididos no es «0% de descarte»: es que no hay qué medir."""
        response = client.get("/control")

        assert "Nada decidido todavía" in response.text

    def test_muestra_el_descarte_por_canal(
        self, client: TestClient, session: Session
    ) -> None:
        """`/control` recomendaba «mirar de qué canales viene lo que se
        descarta» sin dar ese desglose. Ahora la tabla lo trae."""
        from saimc.models import Channel
        from saimc.models.channel import Platform

        _cargar(session, precios=["420"])
        canal = Channel(name="Ofertas Habana", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add(canal)
        session.flush()
        for i in range(2):
            session.add(
                RawIngestion(
                    source_type=SourceType.INBOX_TEXT,
                    file_path=f"raw/canal-{i}.txt",
                    content_hash=f"hash-canal-{i}",
                    content_type=ContentType.TEXT,
                    processing_status=ProcessingStatus.DISCARDED,
                    channel_id=canal.id,
                    captured_at=datetime(2026, 8, 1, tzinfo=UTC),
                )
            )
        session.commit()

        response = client.get("/control")

        assert "Descarte por canal" in response.text
        assert "Ofertas Habana" in response.text
        assert "100%" in response.text

    def test_agrupa_la_captura_manual_sin_canal(
        self, client: TestClient, session: Session
    ) -> None:
        """La mitad del trabajo del operador es sin canal (`channel_id` nulo);
        el desglose no puede dejarla fuera."""
        session.add(
            RawIngestion(
                source_type=SourceType.INBOX_TEXT,
                file_path="raw/manual-1.txt",
                content_hash="hash-manual-1",
                content_type=ContentType.TEXT,
                processing_status=ProcessingStatus.DISCARDED,
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        session.commit()

        response = client.get("/control")

        assert "Captura manual" in response.text

    def test_sin_ninguna_ingesta_no_muestra_la_tabla_de_canales(
        self, client: TestClient
    ) -> None:
        """Nada cargado todavía: la tabla no tiene sentido de mostrar vacía."""
        response = client.get("/control")

        assert "Descarte por canal" not in response.text


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
        assert "tasa de hoy" in response.text

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


class TestHistorialDeH0:
    def _run(self, **overrides: object) -> H0RunSummary:
        base: dict[str, object] = dict(
            date="2026-08-31",
            timestamp="2026-08-31T12:00:00+00:00",
            git_commit="abc1234",
            git_dirty=False,
            git_branch="master",
            tesseract_version="5.5.0",
            is_official=False,
            evaluated_ads_count=175,
            evaluated_rows_count=278,
            assisted_ads_count=0,
            assisted_rows_count=0,
            complete_accuracy=0.435,
            product_accuracy=0.9,
            price_accuracy=0.5,
            province_accuracy=0.8,
            quantity_unit_accuracy=0.7,
            discard_rate=0.678,
            complete_delta=None,
        )
        base.update(overrides)
        return H0RunSummary(**base)  # type: ignore[arg-type]

    def test_sin_corridas_muestra_el_aviso_y_enlaza_al_runbook(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(control, "read_h0_runs", lambda: [])

        texto = client.get("/control").text

        assert "Historial de H0" in texto
        assert "Sin corridas registradas" in texto
        assert 'href="/docs/runbook-h0"' in texto

    def test_lista_cada_corrida_con_su_numero_y_su_commit(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runs = [
            self._run(git_commit="c1", complete_accuracy=0.40, complete_delta=None),
            self._run(
                git_commit="c2",
                complete_accuracy=0.45,
                complete_delta=0.05,
                date="2026-09-05",
            ),
        ]
        monkeypatch.setattr(control, "read_h0_runs", lambda: runs)

        texto = client.get("/control").text

        assert "45.0%" in texto and "40.0%" in texto
        assert "c1" in texto and "c2" in texto
        # Las columnas por métrica salen del macro `h0pct`: producto 0.9 → 90.0%.
        assert "90.0%" in texto
        # El delta de la corrida más nueva contra la anterior.
        assert "+5.0 pts" in texto

    def test_muestra_cuantos_anuncios_quedaron_fuera_por_asistencia(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El número de al lado mide SOLO lo no asistido. Si no se ve cuántos
        quedaron afuera, la divergencia entre asistido y no asistido queda
        enterrada en un archivo (design.md del asistente, D6)."""
        monkeypatch.setattr(
            control, "read_h0_runs", lambda: [self._run(assisted_ads_count=7)]
        )

        texto = client.get("/control").text

        assert "7 asistidos fuera" in texto

    def test_sin_asistencia_no_muestra_la_marca(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(control, "read_h0_runs", lambda: [self._run()])

        assert "asistidos fuera" not in client.get("/control").text

    def test_marca_la_corrida_no_oficial_y_el_arbol_sucio(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            control,
            "read_h0_runs",
            lambda: [self._run(is_official=False, git_dirty=True)],
        )

        texto = client.get("/control").text

        assert "/ 300" in texto
        assert "árbol sucio" in texto


class TestConsumoDelAsistente:
    """`/control` responde las dos preguntas del operador: cuánto llevo
    gastado, y si se agotó el saldo."""

    def _log(self, tmp_path, *lineas: dict) -> Path:
        ruta = tmp_path / "assist.jsonl"
        for linea in lineas:
            base = {
                "timestamp": "2026-09-01T10:00:00+00:00",
                "ingestion_id": "ing-1",
                "model": "claude-sonnet-5",
                "requested": ["quantity"],
                "answered": ["quantity"],
                "dropped": [],
                "degraded": False,
                "input_tokens": 1000,
                "output_tokens": 100,
                "failure": "",
            }
            base.update(linea)
            llm_assist.append_assist_run(base, ruta)
        return ruta

    def test_sin_invocaciones_no_muestra_la_seccion(
        self, client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sin asistente conectado la sección sería ruido en una vista que ya
        tiene diez."""
        monkeypatch.setenv("SAIMC_LLM_ASSIST_LOG_PATH", str(tmp_path / "vacio.jsonl"))
        get_settings.cache_clear()

        assert "Consumo del asistente" not in client.get("/control").text

    def test_muestra_llamadas_y_gasto_estimado(
        self, client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ruta = self._log(tmp_path, {}, {}, {})
        monkeypatch.setenv("SAIMC_LLM_ASSIST_LOG_PATH", str(ruta))
        get_settings.cache_clear()

        texto = client.get("/control").text

        assert "Consumo del asistente" in texto
        assert "3" in texto

    def test_avisa_cuando_se_agoto_el_saldo(
        self, client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ruta = self._log(tmp_path, {}, {"degraded": True, "failure": "credit"})
        monkeypatch.setenv("SAIMC_LLM_ASSIST_LOG_PATH", str(ruta))
        get_settings.cache_clear()

        texto = client.get("/control").text

        assert "falló por <strong>saldo insuficiente" in texto

    def test_sin_saldo_agotado_no_avisa(
        self, client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ruta = self._log(tmp_path, {}, {})
        monkeypatch.setenv("SAIMC_LLM_ASSIST_LOG_PATH", str(ruta))
        get_settings.cache_clear()

        # Específico a propósito: un substring suelto matchea contra
        # comentarios del HTML y del CSS, y ya pasó tres veces en este
        # proyecto. Se busca el aviso real, no la palabra.
        assert "falló por <strong>saldo insuficiente" not in client.get("/control").text


class TestFranjaUsaComponentesCompartidos:
    """La tira de salud vive en la hoja, no en el `<style>` de la plantilla.

    Tenía ocho colores fijos —dos verdes, dos ámbares, dos rojos y dos
    grises— ninguno de la paleta del proyecto. Se repatrió a `.m3-health-*`
    y los colores se volvieron tokens (docs/decisions.md, 2026-09-04).

    Lo que se fija acá es la regla del diseño original que más fácil se pierde
    al refactorizar: **`off` no es `down`**. Una capacidad sin configurar va en
    gris y no cuenta para el aviso rojo, porque pintar de rojo lo que el
    operador no usa entrena a ignorar el rojo.
    """

    def _hoja(self) -> str:
        return (
            Path(__file__).resolve().parents[1]
            / "src"
            / "saimc"
            / "web"
            / "static"
            / "saimc-material.css"
        ).read_text(encoding="utf-8")

    def test_la_plantilla_ya_no_trae_css_propio(self) -> None:
        plantilla = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "saimc"
            / "web"
            / "templates"
            / "control.html"
        ).read_text(encoding="utf-8")

        assert "<style>" not in plantilla, (
            "volvió CSS al `<style>` de control.html — el componente es "
            "`.m3-health-*` y vive en saimc-material.css"
        )
        assert "hs-" not in plantilla

    def test_los_cuatro_niveles_estan_definidos(self) -> None:
        hoja = self._hoja()

        for nivel in ("ok", "warn", "down", "off"):
            assert f".m3-health-card.is-{nivel}" in hoja

    def test_apagado_no_usa_el_color_de_roto(self) -> None:
        """`off` en gris, `down` en rojo. Si alguien los unifica, la franja
        empieza a gritar por capacidades que el operador no configuró."""
        hoja = self._hoja()
        apagado = hoja[hoja.index(".m3-health-card.is-off") :][:200]

        assert "--m3-disabled" in apagado
        assert "--m3-error" not in apagado

    def test_los_colores_de_nivel_salen_de_tokens(self) -> None:
        """Ni un `#hex` en el componente: el rojo de la franja tiene que ser el
        mismo rojo que el del resto de la app."""
        hoja = self._hoja()
        bloque = hoja[hoja.index("── Tira de salud") : hoja.index("── Campos")]
        sin_comentarios = re.sub(r"/\*.*?\*/", "", bloque, flags=re.DOTALL)

        assert not re.findall(r"#[0-9a-fA-F]{3,6}", sin_comentarios)


class TestFranjaDeEstado:
    """La franja que abre `/control`: lo que exige acción hoy, arriba de todo."""

    def test_la_franja_se_renderiza(self, client: TestClient) -> None:
        texto = client.get("/control").text

        assert 'aria-label="Estado del sistema"' in texto
        assert "m3-health-grid" in texto

    def test_muestra_el_estado_de_whatsapp(self, client: TestClient) -> None:
        """El indicador que motivó todo esto (hueco N-02): sin él, WAHA se
        cae y la ingesta para en silencio."""
        texto = client.get("/control").text

        assert "WhatsApp" in texto

    def test_sin_waha_configurado_no_pinta_nada_roto(
        self, client: TestClient
    ) -> None:
        """Sin `waha_base_url` la capacidad está apagada, no rota. El panel no
        puede gritar por algo que el operador no usa."""
        texto = client.get("/control").text

        assert "m3-health-banner is-down" not in texto

    def test_waha_caido_pone_el_banner_rojo(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            control,
            "session_health",
            lambda **kw: SessionHealth(configured=True, status="failed"),
        )

        texto = client.get("/control").text

        assert "m3-health-banner is-down" in texto
        assert "Reiniciala en WAHA" in texto

    def test_la_franja_enlaza_al_detalle(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un semáforo sin salida obliga a buscar la sección a mano."""
        monkeypatch.setattr(
            control,
            "session_health",
            lambda **kw: SessionHealth(configured=True, status="working"),
        )

        texto = client.get("/control").text

        assert 'href="#ingesta"' in texto
        assert 'id="ingesta"' in texto

    def test_cargar_control_no_rompe_si_waha_no_contesta(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WAHA apagado es el caso normal, no un error: la vista tiene que
        cargar igual y decirlo."""
        monkeypatch.setattr(
            control,
            "session_health",
            lambda **kw: SessionHealth(configured=True, status="unreachable"),
        )

        response = client.get("/control")

        assert response.status_code == 200
        assert "no responde" in response.text


class TestLaTablaDeSospechososDeclaraDeDondeSaleLaReferencia:
    """La referencia replegada a todas las provincias (2026-09-05) tiene que
    verse en la fila. Si no, el operador no puede descartar que lo que mira sea
    una diferencia regional real, y esta tabla existe justamente para que él
    juzgue con los números a la vista."""

    def _pollo(
        self, session: Session, *, con_referencia_propia: bool
    ) -> None:
        """La caja de 40 lb a 24.800 marcada «por libra», sola en Santiago.
        La Habana aporta la referencia (600, 620, 640 -> mediana 620)."""
        pollo = Product(
            name="Pollo", category="cárnicos", unit="lb", aliases=[], is_pilot=True
        )
        santiago = Province(code="scu", name="Santiago de Cuba")
        habana = Province(code="hab", name="La Habana")
        session.add_all([pollo, santiago, habana])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/sospechosa.txt",
            content_hash="hash-sospechosa",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()

        def fila(provincia: Province, precio: str, cantidad: str, basis: str | None) -> None:
            session.add(
                PriceHistory(
                    product_id=pollo.id,
                    province_id=provincia.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    source_id=origen.id,
                    unit="lb",
                    quantity=Decimal(cantidad),
                    sale_format=SaleFormat.RETAIL.value,
                    price_basis=basis,
                )
            )

        for precio in ("600", "620", "640"):
            fila(habana, precio, "1", None)
        if con_referencia_propia:
            for precio in ("400", "400"):
                fila(santiago, precio, "1", None)
        fila(santiago, "24800", "40", "unit")
        session.commit()

    def test_marca_la_referencia_que_vino_de_otras_provincias(
        self, client: TestClient, session: Session
    ) -> None:
        self._pollo(session, con_referencia_propia=False)

        texto = client.get("/control").text

        assert "todas las provincias" in texto

    def test_no_la_marca_cuando_la_provincia_puso_su_propia_referencia(
        self, client: TestClient, session: Session
    ) -> None:
        """Santiago tiene sus dos filas de una libra, así que la referencia es
        la suya y no hay nada que aclarar."""
        self._pollo(session, con_referencia_propia=True)

        texto = client.get("/control").text

        assert "todas las provincias" not in texto
