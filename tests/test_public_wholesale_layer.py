"""Capa mayorista opcional en `/producto/<slug>` (2026-10-16).

El precio de referencia es el minorista (`cells.REFERENCE_SALE_FORMAT`). El
mayorista se superpone a pedido, con `?capa=mayorista`, como banda secundaria
junto a la minorista del mismo formato y provincia. Sin el parámetro la página
no cambia: los datos mayoristas no entran en ninguna cifra.

Los valores esperados están calculados a mano acá, antes de escribir el código.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

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
from saimc.web.deps import get_db
from saimc.web.price_charts import Headline
from saimc.web.wholesale_layer import (
    LAYER_LABEL,
    LAYER_PARAM,
    build_overlays,
    unpaired_wholesale,
    wants_wholesale,
)


def _cifra(
    *, median: str, minimum: str, maximum: str, count: int = 3, formato: str | None = None,
    province: str = "La Habana",
) -> Headline:
    return Headline(
        product_name="Arroz",
        province_name=province,
        currency="CUP",
        unit="lb",
        count=count,
        outliers=0,
        median=Decimal(median),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
        previous_median=None,
        previous_count=0,
        last_day=date(2026, 10, 10),
        format_label=formato,
    )


class TestParametro:
    def test_solo_mayorista_prende_la_capa(self) -> None:
        assert LAYER_PARAM == "mayorista"
        assert wants_wholesale("mayorista") is True
        for otro in (None, "", "precio", "densidad", "MAYORISTA "):
            assert wants_wholesale(otro) is False

    def test_el_rotulo_dice_que_es_escala_de_contenedor(self) -> None:
        assert LAYER_LABEL == "Al por mayor (escala de contenedor)"


class TestSuperposicion:
    def test_bandas_en_un_eje_comun_y_ratio(self) -> None:
        """Minorista 280–300–320, mayorista 200–250–260. Eje común 200–320
        (120 de ancho). Minorista: izquierda (280−200)/120 = 66,67 %, ancho
        40/120 = 33,33 %, mediana 100/120 = 83,33 %. Mayorista: izquierda 0,
        ancho 60/120 = 50 %, mediana 50/120 = 41,67 %. Ratio 300/250 = 1,2."""
        menor = _cifra(median="300", minimum="280", maximum="320", formato="saco de 25 kg")
        mayor = _cifra(median="250", minimum="200", maximum="260", count=4, formato="saco de 25 kg")

        (capa,) = build_overlays([menor], [mayor])

        assert capa.has_data is True
        assert capa.wholesale is mayor
        assert round(capa.retail_band.left_pct, 2) == 66.67
        assert round(capa.retail_band.width_pct, 2) == 33.33
        assert round(capa.retail_band.median_pct, 2) == 83.33
        assert round(capa.wholesale_band.left_pct, 2) == 0.0
        assert round(capa.wholesale_band.width_pct, 2) == 50.0
        assert round(capa.wholesale_band.median_pct, 2) == 41.67
        assert capa.ratio == Decimal("1.2")
        assert capa.ratio_text == "×1,20"

    def test_sin_mayorista_del_mismo_formato_no_hay_banda(self) -> None:
        menor = _cifra(median="300", minimum="280", maximum="320", formato="paquete de 1 kg")
        mayor = _cifra(median="250", minimum="200", maximum="260", formato="saco de 25 kg")

        (capa,) = build_overlays([menor], [mayor])

        assert capa.has_data is False
        assert capa.wholesale_band is None
        assert capa.ratio is None

    def test_otra_provincia_no_empareja(self) -> None:
        menor = _cifra(median="300", minimum="280", maximum="320")
        mayor = _cifra(median="250", minimum="200", maximum="260", province="Santiago de Cuba")

        (capa,) = build_overlays([menor], [mayor])

        assert capa.has_data is False

    def test_una_sola_observacion_por_lado_no_divide_por_cero(self) -> None:
        """Las dos cifras valen 100: eje 90–110 (±10 %), las dos en el medio."""
        menor = _cifra(median="100", minimum="100", maximum="100", count=1)
        mayor = _cifra(median="100", minimum="100", maximum="100", count=1)

        (capa,) = build_overlays([menor], [mayor])

        assert round(capa.retail_band.median_pct, 2) == 50.0
        assert capa.retail_band.width_pct == 0.0
        assert capa.ratio_text == "×1,00"

    def test_el_mayorista_sin_par_queda_aparte(self) -> None:
        menor = _cifra(median="300", minimum="280", maximum="320", formato="paquete de 1 kg")
        sin_par = _cifra(median="250", minimum="200", maximum="260", formato="saco de 25 kg")

        assert unpaired_wholesale([menor], [sin_par]) == [sin_par]
        assert unpaired_wholesale([menor], []) == []


# --- La ruta -------------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _hace(dias: int) -> date:
    return date.today() - timedelta(days=dias)


def _seed(session: Session, *, mayorista: bool = True) -> None:
    """Arroz en La Habana: minorista 300 y 320 por libra; mayorista 200 y 250
    por libra (sacos de 100 lb a 20.000 y 25.000 en total). Medianas 310 y
    225: el minorista sale 310 / 225 = 1,377… → «×1,38»."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    session.add_all([arroz, habana])
    session.commit()
    fuente = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/capa.dat",
        content_hash="hash-capa-publica",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(fuente)
    session.flush()
    filas = [("300", "1", None, SaleFormat.RETAIL), ("320", "1", None, SaleFormat.RETAIL)]
    if mayorista:
        filas += [
            ("20000", "100", "total", SaleFormat.WHOLESALE),
            ("25000", "100", "total", SaleFormat.WHOLESALE),
        ]
    for precio, cantidad, base, formato in filas:
        session.add(
            PriceHistory(
                provenance="human",
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal(precio),
                currency=Currency.CUP.value,
                source_id=fuente.id,
                unit="lb",
                quantity=Decimal(cantidad),
                price_basis=base,
                sale_format=formato.value,
                published_date=_hace(3),
            )
        )
    session.commit()


class TestRutaProducto:
    def test_sin_el_parametro_el_mayorista_no_cambia_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """La página por defecto con filas mayoristas en la base es byte a
        byte la misma que sin ellas: ningún dato mayorista se filtra."""
        _seed(session)
        con_mayorista = client.get("/producto/arroz").text
        session.execute(
            delete(PriceHistory).where(PriceHistory.sale_format == SaleFormat.WHOLESALE.value)
        )
        session.commit()
        sin_mayorista = client.get("/producto/arroz").text

        assert con_mayorista == sin_mayorista
        assert LAYER_LABEL not in con_mayorista

    def test_el_interruptor_es_un_enlace_que_conserva_el_periodo(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)
        desde, hasta = date(2026, 9, 1), date.today()

        texto = client.get(f"/producto/arroz?desde={desde}&hasta={hasta}").text

        assert (
            f'href="/producto/arroz?desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}'
            f'&amp;capa=mayorista"'
        ) in texto

    def test_con_la_capa_aparece_la_banda_con_su_n_y_el_ratio(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)

        texto = client.get("/producto/arroz?capa=mayorista").text

        assert LAYER_LABEL in texto
        assert "<b>225</b> CUP/lb" in texto  # mediana mayorista por libra
        assert "2 anuncios al por mayor" in texto
        assert "×1,38" in texto
        assert 'class="ph-lite-banda is-mayorista"' in texto
        # El formulario de fechas no apaga la capa al enviar.
        assert '<input type="hidden" name="capa" value="mayorista">' in texto

    def test_la_cifra_principal_sigue_siendo_la_minorista(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)

        texto = client.get("/producto/arroz?capa=mayorista").text

        # Mediana minorista de 300 y 320.
        assert "310 <small>CUP/lb</small>" in texto

    def test_sin_datos_mayoristas_lo_dice(self, client: TestClient, session: Session) -> None:
        _seed(session, mayorista=False)

        texto = client.get("/producto/arroz?capa=mayorista").text

        assert "Sin datos al por mayor" in texto
        assert 'class="ph-lite-banda is-mayorista"' not in texto

    def test_un_valor_viejo_de_capa_deja_la_pagina_como_siempre(
        self, client: TestClient, session: Session
    ) -> None:
        """`?capa=densidad` venía de la capa del mapa de `/precios`: acá se
        sigue ignorando. Lo único distinto es `og:url`, que repite la URL
        pedida tal cual (comportamiento de siempre del shell)."""
        _seed(session)

        def sin_og_url(texto: str) -> list[str]:
            return [linea for linea in texto.splitlines() if 'property="og:url"' not in linea]

        viejo = client.get("/producto/arroz?capa=densidad").text
        assert sin_og_url(viejo) == sin_og_url(client.get("/producto/arroz").text)

    def test_el_mayorista_sin_formato_minorista_igual_va_aparte_y_sin_ratio(
        self, client: TestClient, session: Session
    ) -> None:
        """Una caja de 40 lb solo al por mayor: no hay minorista de ese
        formato, así que se lista en su propia sección y sin ratio."""
        _seed(session, mayorista=False)
        arroz = session.query(Product).filter_by(name="Arroz").one()
        habana = session.query(Province).filter_by(name="La Habana").one()
        fuente = session.query(RawIngestion).one()
        caja = ProductFormat(product_id=arroz.id, packaging="caja", quantity=Decimal("40"), unit="lb")
        session.add(caja)
        session.flush()
        session.add(
            PriceHistory(
                provenance="human", product_id=arroz.id, province_id=habana.id,
                price=Decimal("8000"), currency=Currency.CUP.value, source_id=fuente.id,
                unit="lb", quantity=Decimal("40"), price_basis="total",
                sale_format=SaleFormat.WHOLESALE.value, format_id=caja.id,
                published_date=_hace(2),
            )
        )
        session.commit()

        texto = client.get("/producto/arroz?capa=mayorista").text
        aparte = texto.split("Al por mayor, en formatos sin precio al por menor")[1]

        assert "Caja de 40 lb" in aparte
        assert "200 <small>CUP/lb</small>" in aparte  # 8000 / 40
        assert "×" not in aparte
        assert "Sin datos al por mayor" in texto  # la minorista sin formato no tiene par

    def test_el_mayorista_sin_convertir_no_se_publica(
        self, client: TestClient, session: Session
    ) -> None:
        """Misma regla de publicación que la minorista: sin tasa no entra."""
        _seed(session, mayorista=False)
        arroz = session.query(Product).filter_by(name="Arroz").one()
        habana = session.query(Province).filter_by(name="La Habana").one()
        fuente = session.query(RawIngestion).one()
        session.add(
            PriceHistory(
                provenance="human", product_id=arroz.id, province_id=habana.id,
                price=Decimal("2"), currency=Currency.USD.value, price_cup=None,
                source_id=fuente.id, unit="lb", quantity=Decimal("1"),
                sale_format=SaleFormat.WHOLESALE.value, published_date=_hace(2),
            )
        )
        session.commit()

        texto = client.get("/producto/arroz?capa=mayorista").text

        assert "Sin datos al por mayor" in texto
        assert "USD" not in texto
