"""Script de medición de H0 (whitepaper §12.5) — casos sintéticos.

Igual que `test_extraction.py`: los casos están armados a mano, con la
respuesta correcta (los porcentajes esperados) calculada de antemano e
independiente del código de medición.

Las cinco métricas de §12.5, todas contra el conjunto de anuncios curados con
texto (las imágenes quedan excluidas: sin OCR en este slice no hubo propuesta
que evaluar contra la corrección del operador):

- Precisión de producto
- Precisión de precio (valor Y moneda correctos)
- Precisión de provincia (None == None cuenta como correcto: "no determinada"
  confirmada por el operador coincide con "no determinada" propuesta)
- Precisión de cantidad/unidad (unidad comparada contra `Product.unit` del
  producto confirmado — no se persiste unidad por fila, ver
  `src/saimc/web/routers/curation.py`)
- Extracción completa correcta (las cuatro anteriores a la vez)

Más la tasa de descarte, sobre el total de anuncios procesados (curados +
descartados), sin distinguir texto de imagen.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from saimc.extraction.measure import compute_h0_report
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)


@pytest.fixture
def pollo(session: Session) -> Product:
    producto = Product(
        name="Pollo", category="carnes", unit="lb", aliases=["pollo congelado"]
    )
    session.add(producto)
    session.commit()
    return producto


@pytest.fixture
def habana(session: Session) -> Province:
    provincia = Province(code="LH", name="La Habana")
    session.add(provincia)
    session.commit()
    return provincia


def _curar_texto(
    session: Session,
    *,
    hash_: str,
    texto: str,
    product_id,
    price: str,
    currency: str,
    province_id,
    quantity: str | None,
) -> None:
    """Crea el par `raw_ingestion` (curated) + `prices_history` de un caso de texto."""
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        original_text=texto,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(ingestion)
    session.flush()
    session.add(
        PriceHistory(
            product_id=product_id,
            province_id=province_id,
            price=Decimal(price),
            currency=currency,
            quantity=Decimal(quantity) if quantity is not None else None,
            source_id=ingestion.id,
        )
    )
    session.commit()


def _curar_imagen(session: Session, *, hash_: str, product_id) -> None:
    """Un curado de imagen: sin texto, por ende sin propuesta que evaluar."""
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_IMAGE,
        file_path=f"raw/{hash_}.png",
        content_hash=hash_,
        content_type=ContentType.IMAGE,
        original_text=None,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(ingestion)
    session.flush()
    session.add(
        PriceHistory(
            product_id=product_id,
            province_id=None,
            price=Decimal("100"),
            currency=Currency.CUP.value,
            quantity=None,
            source_id=ingestion.id,
        )
    )
    session.commit()


def _descartar(session: Session, *, hash_: str) -> None:
    session.add(
        RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/{hash_}.txt",
            content_hash=hash_,
            content_type=ContentType.TEXT,
            original_text="publicidad sin info economica",
            processing_status=ProcessingStatus.DISCARDED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    session.commit()


@pytest.fixture
def dataset(session: Session, pollo: Product, habana: Province) -> None:
    # Caso 1 — las cuatro métricas correctas.
    _curar_texto(
        session,
        hash_="caso-1",
        texto="pollo congelado 40lb a 500 habana",
        product_id=pollo.id,
        price="500",
        currency=Currency.CUP.value,
        province_id=habana.id,
        quantity="40",
    )
    # Caso 2 — producto incorrecto (el texto no lo nombra), resto correcto.
    _curar_texto(
        session,
        hash_="caso-2",
        texto="comida rica 20lb a 300 habana",
        product_id=pollo.id,
        price="300",
        currency=Currency.CUP.value,
        province_id=habana.id,
        quantity="20",
    )
    # Caso 3 — precio incorrecto: moneda propuesta (USD) no coincide con la confirmada (CUP).
    _curar_texto(
        session,
        hash_="caso-3",
        texto="pollo a 500 usd habana",
        product_id=pollo.id,
        price="500",
        currency=Currency.CUP.value,
        province_id=habana.id,
        quantity=None,
    )
    # Caso 4 — sin provincia en el texto ni en lo confirmado: None == None, correcto.
    _curar_texto(
        session,
        hash_="caso-4",
        texto="pollo a 200",
        product_id=pollo.id,
        price="200",
        currency=Currency.CUP.value,
        province_id=None,
        quantity=None,
    )
    # Caso 5 — cantidad incorrecta: el texto no trae cantidad, el operador cargó 5.
    _curar_texto(
        session,
        hash_="caso-5",
        texto="pollo a 100",
        product_id=pollo.id,
        price="100",
        currency=Currency.CUP.value,
        province_id=None,
        quantity="5",
    )
    # Caso 6 — imagen curada: excluida de las métricas de extracción.
    _curar_imagen(session, hash_="caso-6", product_id=pollo.id)
    # Caso 7 — descartado.
    _descartar(session, hash_="caso-7")


class TestH0Report:
    def test_denominador_de_texto_excluye_imagenes(
        self, session: Session, dataset: None
    ) -> None:
        report = compute_h0_report(session)
        assert report.text_curated_count == 5
        assert report.image_curated_count == 1

    def test_totales_de_procesamiento(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        assert report.total_curated == 6
        assert report.total_discarded == 1
        assert report.total_processed == 7
        assert report.discard_rate == pytest.approx(1 / 7)

    def test_precision_de_producto(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        # Correcto en 1,3,4,5 (4/5); incorrecto en 2 (el texto no nombra el producto).
        assert report.product_accuracy == pytest.approx(4 / 5)

    def test_precision_de_precio(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        # Correcto en 1,2,4,5 (4/5); incorrecto en 3 (moneda propuesta USD vs CUP confirmado).
        assert report.price_accuracy == pytest.approx(4 / 5)

    def test_precision_de_provincia(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        # Los cinco casos coinciden (incluye None == None en 4 y 5).
        assert report.province_accuracy == pytest.approx(5 / 5)

    def test_precision_de_cantidad_unidad(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        # Correcto en 1,2,3,4 (4/5); incorrecto en 5 (cantidad propuesta None vs 5 confirmado).
        assert report.quantity_unit_accuracy == pytest.approx(4 / 5)

    def test_extraccion_completa_correcta(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session)
        # Los cuatro campos a la vez: solo 1 y 4 los tienen todos correctos (2/5).
        assert report.complete_accuracy == pytest.approx(2 / 5)

    def test_sin_datos_no_rompe(self, session: Session) -> None:
        report = compute_h0_report(session)
        assert report.total_processed == 0
        assert report.text_curated_count == 0
        assert report.evaluated_rows_count == 0
        assert report.discard_rate is None
        assert report.product_accuracy is None
        assert report.complete_accuracy is None

    def test_evaluated_rows_count_coincide_con_text_curated_count_sin_multiproducto(
        self, session: Session, dataset: None
    ) -> None:
        """En este dataset cada ingesta de texto tiene una sola fila: los dos
        conteos deben coincidir (1:1), aunque midan cosas distintas.
        """
        report = compute_h0_report(session)
        assert report.evaluated_rows_count == report.text_curated_count == 5


class TestH0ReportMultiProducto:
    """Un anuncio puede tener varias filas de `prices_history` con el mismo
    `source_id` (whitepaper: varios productos en el mismo anuncio, ver
    `propose_all` y `POST /curation/{id}` con `action=save_and_continue`).
    Las métricas se evalúan por FILA, no por ingesta — antes de este fix,
    `measure.py` pisaba en silencio todas las filas menos la última.
    """

    @pytest.fixture
    def dataset_multiproducto(
        self, session: Session, pollo: Product, habana: Province
    ) -> tuple[RawIngestion, Product]:
        picadillo = Product(
            name="Picadillo", category="carnes", unit="lb", aliases=[]
        )
        session.add(picadillo)
        session.commit()

        texto = "pollo congelado 40lb a 30 habana\npicadillo 20lb a 50 habana\n"
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/multi.txt",
            content_hash="hash-multi",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()

        # Fila 1: coincide exactamente con lo que propone la línea 1. Correcta
        # en las cuatro métricas.
        session.add(
            PriceHistory(
                product_id=pollo.id,
                province_id=habana.id,
                price=Decimal("30"),
                currency=Currency.CUP.value,
                quantity=Decimal("40"),
                source_id=ingestion.id,
            )
        )
        # Fila 2: el producto confirmado (Picadillo) SÍ matchea la línea 2,
        # pero el precio confirmado (999) no coincide con el propuesto (50).
        # Producto correcto, precio incorrecto.
        session.add(
            PriceHistory(
                product_id=picadillo.id,
                province_id=habana.id,
                price=Decimal("999"),
                currency=Currency.CUP.value,
                quantity=Decimal("20"),
                source_id=ingestion.id,
            )
        )
        # Fila 3: producto confirmado que ninguna línea del texto propuso
        # (Arroz no aparece en el texto). Ningún candidato matchea por
        # producto: las cuatro métricas cuentan como incorrectas para esta fila.
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.flush()
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("10"),
                currency=Currency.CUP.value,
                quantity=Decimal("1"),
                source_id=ingestion.id,
            )
        )
        session.commit()
        return ingestion, picadillo

    def test_cuenta_ingestas_y_filas_por_separado(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session)
        assert report.text_curated_count == 1
        assert report.evaluated_rows_count == 3

    def test_precision_de_producto_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session)
        # Filas 1 y 2 matchean por producto (2/3); fila 3 no matchea ninguna línea.
        assert report.product_accuracy == pytest.approx(2 / 3)

    def test_precision_de_precio_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session)
        # Solo la fila 1 tiene precio correcto (1/3); fila 2 tiene producto
        # correcto pero precio distinto, fila 3 no matchea nada.
        assert report.price_accuracy == pytest.approx(1 / 3)

    def test_extraccion_completa_correcta_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session)
        assert report.complete_accuracy == pytest.approx(1 / 3)
