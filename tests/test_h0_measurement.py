"""Script de medición de H0 (spec.md §12.5) — casos sintéticos.

Igual que `test_extraction.py`: los casos están armados a mano, con la
respuesta correcta (los porcentajes esperados) calculada de antemano e
independiente del código de medición.

Las cinco métricas de §12.5, contra TODO anuncio curado —texto e imagen—. Para
las imágenes se re-corre el OCR sobre la evidencia original preservada, no se
lee `original_text`: ese campo guarda la transcripción ya corregida por el
operador (`src/saimc/web/routers/curation.py`), y medir sobre él evaluaría las
reglas contra un texto que un humano ya limpió. El OCR se inyecta como
parámetro para que la suite no dependa del binario de Tesseract:

- Precisión de producto
- Precisión de precio (valor Y moneda correctos)
- Precisión de provincia (None == None cuenta como correcto: "no determinada"
  confirmada por el operador coincide con "no determinada" propuesta)
- Precisión de cantidad/unidad (unidad comparada contra `prices_history.unit`,
  la que el operador confirmó para ESA fila, canonicalizada por
  `saimc.units.canonical_unit` en los dos lados)
- Extracción completa correcta (las cuatro anteriores a la vez)

Más la tasa de descarte, sobre el total de anuncios procesados (curados +
descartados), sin distinguir texto de imagen.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from saimc.extraction.measure import compute_h0_report
from saimc.models import (
    Channel,
    ContentType,
    Currency,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)


# Transcripciones simuladas, por nombre de archivo de la evidencia. Un archivo
# ausente devuelve "" — el mismo contrato que `ocr.transcribe` real, que degrada
# a texto vacío ante fallos esperables.
_OCR_FALSO: dict[str, str] = {
    "caso-6.png": "pollo congelado 40lb a 500 habana",
}


def _transcribe_falso(path: Path) -> str:
    return _OCR_FALSO.get(path.name, "")


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
    unit: str | None,
) -> None:
    """Crea el par `raw_ingestion` (curated) + `prices_history` de un caso de texto.

    `unit` es obligatorio y explícito a propósito: es la verdad de referencia
    contra la que se mide la unidad extraída (`prices_history.unit`), así que
    dejarlo caer a un default escondería justo el dato que el caso está
    fijando.
    """
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
            unit=unit,
            source_id=ingestion.id,
        )
    )
    session.commit()


def _curar_imagen(
    session: Session,
    *,
    hash_: str,
    product_id,
    price: str,
    province_id,
    quantity: str | None,
    unit: str | None,
    original_text: str | None = None,
) -> None:
    """Un curado de imagen. La propuesta a evaluar sale de re-correr el OCR
    sobre `file_path`, no de `original_text` — por eso este helper permite
    guardar un `original_text` distinto de la transcripción simulada.
    """
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_IMAGE,
        file_path=f"raw/{hash_}.png",
        content_hash=hash_,
        content_type=ContentType.IMAGE,
        original_text=original_text,
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
            currency=Currency.CUP.value,
            quantity=Decimal(quantity) if quantity is not None else None,
            unit=unit,
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
        unit="lb",
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
        unit="lb",
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
        unit=None,
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
        unit=None,
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
        unit="lb",
    )
    # Caso 6 — imagen curada: el OCR simulado devuelve el mismo texto que el
    # caso 1, así que las cuatro métricas son correctas. Entra en el
    # denominador igual que un anuncio de texto.
    _curar_imagen(
        session,
        hash_="caso-6",
        product_id=pollo.id,
        price="500",
        province_id=habana.id,
        quantity="40",
        unit="lb",
    )
    # Caso 7 — descartado.
    _descartar(session, hash_="caso-7")


class TestH0Report:
    def test_texto_e_imagen_entran_ambos_en_el_denominador(
        self, session: Session, dataset: None
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.text_curated_count == 5
        assert report.image_curated_count == 1
        # Las 5 de texto + la imagen: ninguna queda fuera de la medición.
        assert report.evaluated_rows_count == 6
        assert report.image_ocr_empty_count == 0

    def test_totales_de_procesamiento(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.total_curated == 6
        assert report.total_discarded == 1
        assert report.total_processed == 7
        assert report.discard_rate == pytest.approx(1 / 7)

    def test_precision_de_producto(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Correcto en 1,3,4,5,6 (5/6); incorrecto en 2 (el texto no nombra el producto).
        assert report.product_accuracy == pytest.approx(5 / 6)

    def test_precision_de_precio(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Correcto en 1,2,4,5,6 (5/6); incorrecto en 3 (moneda propuesta USD vs CUP confirmado).
        assert report.price_accuracy == pytest.approx(5 / 6)

    def test_precision_de_provincia(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Los seis casos coinciden (incluye None == None en 4 y 5).
        assert report.province_accuracy == pytest.approx(6 / 6)

    def test_precision_de_cantidad_unidad(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Correcto en 1,2,3,4,6 (5/6); incorrecto en 5 (cantidad propuesta None vs 5 confirmado).
        assert report.quantity_unit_accuracy == pytest.approx(5 / 6)

    def test_extraccion_completa_correcta(self, session: Session, dataset: None) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Los cuatro campos a la vez: solo 1, 4 y 6 los tienen todos correctos (3/6).
        assert report.complete_accuracy == pytest.approx(3 / 6)

    def test_sin_datos_no_rompe(self, session: Session) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.total_processed == 0
        assert report.text_curated_count == 0
        assert report.evaluated_rows_count == 0
        assert report.image_ocr_empty_count == 0
        assert report.discard_rate is None
        assert report.product_accuracy is None
        assert report.complete_accuracy is None

    def test_evaluated_rows_count_suma_texto_mas_imagen_sin_multiproducto(
        self, session: Session, dataset: None
    ) -> None:
        """En este dataset cada ingesta tiene una sola fila: las filas evaluadas
        son la suma de anuncios de texto e imagen, no solo los de texto.
        """
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.evaluated_rows_count == (
            report.text_curated_count + report.image_curated_count
        )


class TestH0UmbralEnAnuncios:
    """El conjunto de referencia de H0 se cuenta en ANUNCIOS, no en filas.

    `spec.md` §3 lo dice literal: «el operador etiqueta manualmente un conjunto
    de referencia de al menos 300 anuncios reales. Las reglas de extracción se
    miden contra ese conjunto». §9 lo repite («Conjunto de referencia
    etiquetado de 300+ anuncios») y usa la otra unidad para la meta del
    producto de datos, que sí son filas: «500 registros validados». Son dos
    unidades a propósito, y `progress.py` ya respeta la segunda
    (`PHASE0_TARGET_ROWS = 500`).

    `measure.py` comparaba el umbral contra las FILAS mientras su propio
    comentario decía «300 anuncios etiquetados a mano»: código y comentario se
    contradecían. Medido contra producción el 2026-08-21, la diferencia no era
    cosmética — 211 filas contra 132 anuncios, y un solo anuncio aportaba 25 de
    esas filas, el 11.8% de la muestra.

    El motivo de fondo es de independencia estadística: las filas de un mismo
    anuncio comparten evidencia, OCR, canal y provincia, así que fallan juntas.
    Contarlas por separado infla el tamaño de muestra sin agregar información.
    """

    def test_el_aviso_cuenta_anuncios_y_no_filas(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """Un anuncio con tres filas es UN anuncio del conjunto de referencia."""
        texto = (
            "pollo congelado 40lb a 500 habana\n"
            "pollo congelado 20lb a 300 habana\n"
            "pollo congelado 10lb a 150 habana\n"
        )
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/multi3.txt",
            content_hash="hash-multi3",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()
        for precio, cantidad in (("500", "40"), ("300", "20"), ("150", "10")):
            session.add(
                PriceHistory(
                    product_id=pollo.id,
                    province_id=habana.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    quantity=Decimal(cantidad),
                    unit="lb",
                    source_id=ingestion.id,
                )
            )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 3
        assert report.evaluated_ads_count == 1

    def test_un_curado_sin_filas_no_entra_en_el_conjunto(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """El conjunto de referencia son los anuncios contra los que las reglas
        se midieron de verdad. Un curado sin fila confirmada no aporta ninguna
        comparación, así que no lo engrosa."""
        _curar_texto(
            session,
            hash_="con-fila",
            texto="pollo congelado 40lb a 500 habana",
            product_id=pollo.id,
            price="500",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="40",
            unit="lb",
        )
        session.add(
            RawIngestion(
                source_type=SourceType.INBOX_TEXT,
                file_path="raw/sin-fila.txt",
                content_hash="hash-sin-fila",
                content_type=ContentType.TEXT,
                original_text="pollo a 100 habana",
                processing_status=ProcessingStatus.CURATED,
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.total_curated == 2
        assert report.evaluated_ads_count == 1

    def test_sin_datos_el_conjunto_es_cero(self, session: Session) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.evaluated_ads_count == 0


class TestH0ReportImagenes:
    """Las imágenes son la mayoría del corpus real (27 de 29 anuncios curados al
    2026-08-11) y hasta este fix quedaban enteras fuera de la medición.
    """

    def test_no_mide_sobre_el_texto_corregido_por_el_operador(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """`original_text` de una imagen es la transcripción YA corregida a mano.
        Medir sobre ella evaluaría las reglas contra un texto que un humano
        limpió — H0 pregunta si el pipeline funciona SIN esa intervención.
        """
        _curar_imagen(
            session,
            hash_="ocr-sucio",
            product_id=pollo.id,
            price="500",
            province_id=habana.id,
            quantity="40",
            unit="lb",
            # Lo que el operador dejó guardado: limpio, daría las 4 correctas.
            original_text="pollo congelado 40lb a 500 habana",
        )
        # Pero el OCR real sobre la imagen no devuelve nada aprovechable
        # (no está en `_OCR_FALSO`): la medición honesta es 0.
        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        assert report.product_accuracy == pytest.approx(0.0)
        assert report.complete_accuracy == pytest.approx(0.0)

    def test_ocr_vacio_se_reporta_aparte(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """Un OCR que devuelve "" es indistinguible de una extracción fallida en
        el porcentaje. Se cuenta aparte para que un Tesseract ausente no se lea
        como "las reglas no funcionan".
        """
        _curar_imagen(
            session,
            hash_="sin-ocr",
            product_id=pollo.id,
            price="500",
            province_id=habana.id,
            quantity="40",
            unit="lb",
        )
        report = compute_h0_report(session, transcribe=lambda path: "")

        assert report.image_curated_count == 1
        assert report.image_ocr_empty_count == 1
        assert report.evaluated_rows_count == 1


class TestH0ReportMultiProducto:
    """Un anuncio puede tener varias filas de `prices_history` con el mismo
    `source_id` (spec.md: varios productos en el mismo anuncio, ver
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
                unit="lb",
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
                unit="lb",
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
                unit="lb",
                source_id=ingestion.id,
            )
        )
        session.commit()
        return ingestion, picadillo

    def test_cuenta_ingestas_y_filas_por_separado(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.text_curated_count == 1
        assert report.evaluated_rows_count == 3

    def test_precision_de_producto_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Filas 1 y 2 matchean por producto (2/3); fila 3 no matchea ninguna línea.
        assert report.product_accuracy == pytest.approx(2 / 3)

    def test_precision_de_precio_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        # Solo la fila 1 tiene precio correcto (1/3); fila 2 tiene producto
        # correcto pero precio distinto, fila 3 no matchea nada.
        assert report.price_accuracy == pytest.approx(1 / 3)

    def test_extraccion_completa_correcta_por_fila(
        self, session: Session, dataset_multiproducto: tuple[RawIngestion, Product]
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.complete_accuracy == pytest.approx(1 / 3)


class TestH0ProvinciaDelCanal:
    """La medición tiene que evaluar el mismo extractor que corre en producción.

    La cola de curaduría aplica la provincia fija del canal desde el
    2026-08-17 ("el canal siempre manda", docs/decisions.md), pero
    `compute_h0_report` re-corría `propose_all` sin ese dato. El resultado era
    una precisión de provincia sistemáticamente por debajo de la real: el
    operador veía la provincia ya puesta en el formulario y la confirmaba, y
    la medición contaba esa misma fila como fallo porque el texto del mensaje
    no la nombraba.
    """

    def test_la_provincia_del_canal_cuenta_como_extraida(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=habana.id,
            is_active=True,
        )
        session.add(canal)
        session.commit()

        ingestion = RawIngestion(
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales",
            file_path="raw/canal-1.txt",
            content_hash="canal-1",
            content_type=ContentType.TEXT,
            # El mensaje NO nombra la provincia: es lo normal en un canal, el
            # vendedor no repite dónde está en cada anuncio.
            original_text="pollo congelado 40lb a 500",
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            channel_id=canal.id,
        )
        session.add(ingestion)
        session.flush()
        session.add(
            PriceHistory(
                product_id=pollo.id,
                province_id=habana.id,
                price=Decimal("500"),
                currency=Currency.CUP.value,
                quantity=Decimal("40"),
                unit="lb",
                source_id=ingestion.id,
            )
        )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        assert report.province_accuracy == 1.0
        assert report.complete_accuracy == 1.0

    def test_sin_canal_la_provincia_ausente_sigue_contando_como_fallo(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """El contrapeso: sin canal que la aporte, una provincia que el texto
        no dice y el operador sí cargó sigue siendo una extracción fallida."""
        _curar_texto(
            session,
            hash_="sin-canal-1",
            texto="pollo congelado 40lb a 500",
            product_id=pollo.id,
            price="500",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="40",
            unit="lb",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        assert report.province_accuracy == 0.0


class TestH0UnidadConfirmadaEnLaFila:
    """La unidad se mide contra `prices_history.unit` —la que el operador
    confirmó para ESA fila— y no contra `Product.unit`, la del catálogo.

    Motivo, medido contra producción el 2026-08-21: 95 de 211 filas tienen
    `unit='kg'` mientras el producto del catálogo dice `'lb'`. Un saco de arroz
    se anuncia y se confirma en kg aunque el arroz se venda por libra al
    menudeo. La comparación vieja contaba cada una de esas filas como fallo de
    extracción, o sea penalizaba al extractor por acertar.

    Estos casos son sintéticos y su respuesta correcta está fijada de antemano,
    igual que el resto del archivo. El número de H0 sigue saliendo del corpus
    real vía la cola de curaduría (CLAUDE.md, «Código»).
    """

    @pytest.fixture
    def arroz(self, session: Session) -> Product:
        # Unidad de catálogo `lb`: el arroz se vende por libra al menudeo.
        producto = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(producto)
        session.commit()
        return producto

    def test_unidad_del_anuncio_distinta_a_la_del_catalogo_es_correcta(
        self, session: Session, arroz: Product, habana: Province
    ) -> None:
        """El caso de las 95 filas: el anuncio vende un saco por kg, el
        operador confirma kg, y el catálogo dice lb. El extractor propone kg
        y ACIERTA."""
        _curar_texto(
            session,
            hash_="saco-kg",
            texto="arroz 25kg a 17500 cup habana",
            product_id=arroz.id,
            price="17500",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="25",
            unit="kg",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        assert report.quantity_unit_accuracy == 1.0

    def test_unidad_distinta_a_la_confirmada_sigue_siendo_fallo(
        self, session: Session, arroz: Product, habana: Province
    ) -> None:
        """El contrapeso: el cambio no es «aceptar cualquier unidad». El texto
        dice kg, el operador confirmó lb, y eso sigue contando como fallo."""
        _curar_texto(
            session,
            hash_="kg-vs-lb",
            texto="arroz 25kg a 17500 cup habana",
            product_id=arroz.id,
            price="17500",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="25",
            unit="lb",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.quantity_unit_accuracy == 0.0

    def test_la_forma_flexionada_del_extractor_equivale_a_la_canonica(
        self, session: Session, habana: Province
    ) -> None:
        """`engine.py` reconoce `litro` y `curation.py` guarda el canónico `l`
        (`saimc.units`). Los dos lados se canonicalizan antes de comparar, así
        que esa diferencia de vocabulario no cuenta como fallo."""
        aceite = Product(name="Aceite", category="abarrotes", unit="l", aliases=[])
        session.add(aceite)
        session.commit()
        _curar_texto(
            session,
            hash_="aceite-litro",
            texto="aceite 1 litro a 900 cup habana",
            product_id=aceite.id,
            price="900",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="1",
            unit="l",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.quantity_unit_accuracy == 1.0

    def test_unidad_propuesta_sobre_fila_sin_unidad_es_fallo(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """El operador borró la unidad que el formulario traía prellenada: la
        propuesta sobra y la fila cuenta como fallo, no como acierto por
        ausencia."""
        _curar_texto(
            session,
            hash_="unidad-borrada",
            texto="pollo congelado 40lb a 500 habana",
            product_id=pollo.id,
            price="500",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="40",
            unit=None,
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.quantity_unit_accuracy == 0.0


class TestH0EmparejadoAmbiguo:
    """Una fila cuyo producto no aparece en ninguna línea no puede llevarse el
    precio de una línea elegida por posición.

    `_match_rows_to_candidates` empareja primero por `product_id` y lo que
    sobra lo reparte por orden. Eso es correcto mientras haya una sola línea
    candidata: no hubo elección, y precio o provincia pudieron extraerse bien
    aunque el producto no se reconociera. Deja de serlo cuando sobran varias,
    porque el orden de las filas confirmadas no tiene por qué seguir el de las
    líneas del anuncio.

    Medido contra producción el 2026-08-21: de 33 filas que caen al reparto,
    15 tenían varias líneas candidatas y 9 de ellas se llevaban el precio por
    coincidencia — unos 4.3 puntos de precisión de precio que eran ruido.

    La provincia es el caso aparte: todos los candidatos de un mismo anuncio
    la comparten (la fija el canal, docs/decisions.md 2026-08-17), así que
    acreditarla no depende de qué línea se elija y se conserva.
    """

    @pytest.fixture
    def anuncio_multilinea(
        self, session: Session, pollo: Product, habana: Province
    ) -> Product:
        """Tres líneas reconocibles y una fila confirmada de un producto que no
        aparece en ninguna: quedan DOS candidatos sobrantes, así que el reparto
        posicional es una adivinanza."""
        picadillo = Product(name="Picadillo", category="carnes", unit="lb", aliases=[])
        aceite = Product(name="Aceite", category="abarrotes", unit="l", aliases=[])
        # Confirmado pero ausente del texto: no lo nombra ninguna línea.
        frijol = Product(name="Frijol", category="granos", unit="lb", aliases=[])
        session.add_all([picadillo, aceite, frijol])
        session.commit()

        texto = (
            "pollo congelado 40lb a 500 habana\n"
            "picadillo 20lb a 777 habana\n"
            "aceite 1 litro a 900 habana\n"
        )
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/ambiguo.txt",
            content_hash="hash-ambiguo",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()
        # Fila 1: matchea por producto con la línea 1. Correcta en todo.
        session.add(
            PriceHistory(
                product_id=pollo.id,
                province_id=habana.id,
                price=Decimal("500"),
                currency=Currency.CUP.value,
                quantity=Decimal("40"),
                unit="lb",
                source_id=ingestion.id,
            )
        )
        # Fila 2: Frijol no está en el texto. Su precio confirmado coincide con
        # el de la línea de picadillo (777) a propósito: si el reparto
        # posicional le regala ese candidato, el precio contaría como acertado
        # sin que el extractor haya extraído nada para esta fila.
        session.add(
            PriceHistory(
                product_id=frijol.id,
                province_id=habana.id,
                price=Decimal("777"),
                currency=Currency.CUP.value,
                quantity=None,
                unit=None,
                source_id=ingestion.id,
            )
        )
        session.commit()
        return frijol

    def test_el_precio_de_una_linea_ajena_no_cuenta_como_acertado(
        self, session: Session, anuncio_multilinea: Product
    ) -> None:
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.evaluated_rows_count == 2
        # Solo la fila de pollo tiene el precio realmente extraído.
        assert report.price_accuracy == pytest.approx(1 / 2)

    def test_la_provincia_se_conserva_aunque_el_emparejado_sea_ambiguo(
        self, session: Session, anuncio_multilinea: Product
    ) -> None:
        """No depende de qué línea se elija: la comparten todos los candidatos
        del anuncio."""
        report = compute_h0_report(session, transcribe=_transcribe_falso)
        assert report.province_accuracy == pytest.approx(2 / 2)

    def test_con_una_sola_linea_sobrante_el_emparejado_se_conserva(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """El contrapeso: sin elección que hacer, la fila se sigue comparando
        contra la única línea del anuncio. El precio pudo extraerse bien aunque
        el producto no se reconociera."""
        texto = "cositas ricas 40lb a 500 habana\n"
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/una-linea.txt",
            content_hash="hash-una-linea",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()
        session.add(
            PriceHistory(
                product_id=pollo.id,
                province_id=habana.id,
                price=Decimal("500"),
                currency=Currency.CUP.value,
                quantity=Decimal("40"),
                unit="lb",
                source_id=ingestion.id,
            )
        )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        # El texto no nombra "pollo", así que el producto falla...
        assert report.product_accuracy == 0.0
        # ...pero precio y cantidad salieron de la única línea que hay.
        assert report.price_accuracy == 1.0
        assert report.quantity_unit_accuracy == 1.0
