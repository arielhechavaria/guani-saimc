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

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.extraction import llm_assist
from saimc.extraction.measure import (
    H0Report,
    append_h0_run,
    compute_h0_report,
    h0_run_record,
    read_h0_runs,
)
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
        name="Pollo",
        category="carnes",
        unit="lb",
        aliases=["pollo congelado"],
        # El foco es lo que la cola le ofrece al motor, y desde el 2026-09-09 la
        # medición usa el mismo catálogo: un producto sin `is_pilot` no se
        # extrae ni en producción ni acá.
        is_pilot=True,
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
            name="Picadillo", category="carnes", unit="lb", aliases=[], is_pilot=True
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
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
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

    def test_una_fila_sin_candidato_conserva_la_provincia_del_canal(
        self, session: Session, pollo: Product, habana: Province
    ) -> None:
        """Una fila que se queda sin línea libre igual tiene provincia.

        `curation.py` aplica la provincia del canal a TODAS las filas del
        formulario —incluida una que el operador agrega en blanco— sin mirar si
        el motor propuso algo para ella (`row["province_id"] = channel_province_id`,
        2026-08-17). La medición le daba `ExtractionProposal()` pelada y contaba
        la provincia como fallo: otra vez, un extractor más ciego que el de
        producción. Al 2026-09-09 esto afectaba al 12.9% de las filas de texto.

        El producto y el precio SIGUEN siendo fallo: el canal aporta la
        provincia y nada más.
        """
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=habana.id,
            is_active=True,
        )
        session.add(canal)
        session.commit()

        # Una sola línea usable -> un solo candidato, pero el operador confirmó
        # DOS filas (menudeo y mayoreo del mismo producto).
        ingestion = RawIngestion(
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales",
            file_path="raw/sin-candidato-1.txt",
            content_hash="sin-candidato-1",
            content_type=ContentType.TEXT,
            original_text="pollo congelado 40lb a 500",
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            channel_id=canal.id,
        )
        session.add(ingestion)
        session.flush()
        for price, quantity in (("500", "40"), ("14", "1")):
            session.add(
                PriceHistory(
                    product_id=pollo.id,
                    province_id=habana.id,
                    price=Decimal(price),
                    currency=Currency.CUP.value,
                    quantity=Decimal(quantity),
                    unit="lb",
                    source_id=ingestion.id,
                )
            )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 2
        # Las dos filas tienen la provincia del canal, igual que en el formulario.
        assert report.province_accuracy == 1.0
        # La segunda fila no tuvo candidato: su producto y su precio son fallo.
        assert report.product_accuracy == 0.5
        assert report.price_accuracy == 0.5

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


class TestH0CatalogoDelFoco:
    """La medición tiene que ofrecerle al motor el MISMO catálogo que la cola.

    Mismo principio que `TestH0ProvinciaDelCanal`, sobre el otro insumo de
    `propose_all`. La cola de curaduría le pasa solo los productos del foco
    (`_pilot_products` en `web/routers/curation.py`: «único punto por donde el
    catálogo entra a la curaduría»), pero `compute_h0_report` le pasaba el
    catálogo entero — 60 productos contra 5 en producción al 2026-09-09.

    Los 55 de más no son inertes: `_match_product` devuelve **el primero de la
    lista** que matchea, así que un producto fuera del foco con nombre parecido
    —o directamente un artefacto de parseo como `Precio :`, que matchea texto
    presente en casi todos los anuncios— le roba el match al piloto y la fila
    cuenta como fallo. Medir así no era más exigente: era medir otro extractor,
    uno que en producción no existe.
    """

    def test_un_producto_fuera_del_foco_no_le_roba_el_match_al_piloto(
        self, session: Session, habana: Province
    ) -> None:
        # El señuelo se inserta PRIMERO a propósito: sin `ORDER BY` el catálogo
        # sale en orden de inserción y `_match_product` corta en el primero que
        # matchea, así que este es el orden en que el defecto se manifiesta.
        criollo = Product(
            name="Arroz criollo",
            category="sin categoría",
            unit="unidad",
            aliases=[],
            is_pilot=False,
        )
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        session.add_all([criollo, arroz])
        session.commit()

        _curar_texto(
            session,
            hash_="foco-1",
            texto="arroz criollo a 300 cup la libra en la habana",
            product_id=arroz.id,
            price="300",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="1",
            unit="lb",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        # La cola real solo ofrece "Arroz": es lo que el operador vio y
        # confirmó, y es lo que la medición tiene que acreditar.
        assert report.evaluated_rows_count == 1
        assert report.product_accuracy == 1.0

    def test_una_fila_contra_un_producto_fuera_del_foco_cuenta_como_fallo(
        self, session: Session, habana: Province
    ) -> None:
        """Contrapeso: filtrar el catálogo no borra filas del denominador.

        Las filas históricas confirmadas contra un producto que hoy está fuera
        del foco —el operador lo dio de alta desde la cola antes de que el foco
        se aplicara— siguen contando, y como fallo: la cola de hoy tampoco
        podría proponerlo. Filtrar no puede ser una forma de inflar el número.
        """
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        blanco = Product(
            name="Azúcar blanco",
            category="sin categoría",
            unit="unidad",
            aliases=[],
            is_pilot=False,
        )
        session.add_all([arroz, blanco])
        session.commit()

        _curar_texto(
            session,
            hash_="foco-2",
            texto="azucar blanco a 250 cup la libra en la habana",
            product_id=blanco.id,
            price="250",
            currency=Currency.CUP.value,
            province_id=habana.id,
            quantity="1",
            unit="lb",
        )

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 1
        assert report.product_accuracy == 0.0


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
        producto = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
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
        aceite = Product(name="Aceite", category="abarrotes", unit="l", aliases=[], is_pilot=True)
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
        picadillo = Product(name="Picadillo", category="carnes", unit="lb", aliases=[], is_pilot=True)
        aceite = Product(name="Aceite", category="abarrotes", unit="l", aliases=[], is_pilot=True)
        # Confirmado pero ausente del texto: no lo nombra ninguna línea.
        frijol = Product(name="Frijol", category="granos", unit="lb", aliases=[], is_pilot=True)
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


class TestH0EmparejadoMismoProductoVariasFilas:
    """Un anuncio con el MISMO producto en dos filas —menudeo por kg y saco por
    lb— y los dos candidatos correctos. Emparejar por orden los cruza y cuenta
    las dos como fallo de precio Y de cantidad/unidad; emparejar por mejor
    ajuste los alinea.

    Caso real medido el 2026-09-03: los price-lists de canal (`COTORRO`,
    `SAN JOSÉ`, `OFERTAS DEL DÍA`) publican «Arroz grano dorado 100 lb … o
    30885» y «Arroz grano dorado 1 kg … o 688,7» en el mismo mensaje. El
    extractor sacaba las dos filas bien; `_match_rows_to_candidates` las
    pareaba por posición y las dos contaban mal. Eran 39 de 295 filas.
    """

    @pytest.fixture
    def arroz(self, session: Session) -> Product:
        producto = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        session.add(producto)
        session.commit()
        return producto

    def test_dos_filas_del_mismo_producto_se_alinean_por_mejor_ajuste(
        self, session: Session, arroz: Product, habana: Province
    ) -> None:
        texto = "Arroz 100 lb a 30000 habana\nArroz 1 kg a 700 habana\n"
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/arroz-dos-formatos.txt",
            content_hash="hash-arroz-dos-formatos",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()
        # Orden cruzado a propósito: la PRIMERA fila confirmada corresponde a
        # la SEGUNDA línea del anuncio. Con emparejado posicional, la fila de
        # 700/kg se compara contra el candidato de 30000/lb y viceversa.
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("700"),
                currency=Currency.CUP.value,
                quantity=Decimal("1"),
                unit="kg",
                source_id=ingestion.id,
            )
        )
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("30000"),
                currency=Currency.CUP.value,
                quantity=Decimal("100"),
                unit="lb",
                source_id=ingestion.id,
            )
        )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 2
        assert report.price_accuracy == 1.0
        assert report.quantity_unit_accuracy == 1.0
        assert report.complete_accuracy == 1.0

    def test_una_fila_bien_extraida_y_otra_no_no_se_prestan_el_acierto(
        self, session: Session, arroz: Product, habana: Province
    ) -> None:
        """El mejor ajuste no infla: si el extractor solo sacó bien una de las
        dos líneas del producto, la otra fila se queda con el candidato que
        sobra —del mismo producto— y sigue fallando lo que el extractor no
        extrajo. No hay candidato ajeno del que tomar prestado un acierto."""
        # El anuncio solo tiene UNA línea de Arroz legible; la de kg está rota.
        texto = "Arroz 100 lb a 30000 habana\nArroz saco a granel habana\n"
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/arroz-una-legible.txt",
            content_hash="hash-arroz-una-legible",
            content_type=ContentType.TEXT,
            original_text=texto,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(ingestion)
        session.flush()
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("30000"),
                currency=Currency.CUP.value,
                quantity=Decimal("100"),
                unit="lb",
                source_id=ingestion.id,
            )
        )
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("700"),
                currency=Currency.CUP.value,
                quantity=Decimal("1"),
                unit="kg",
                source_id=ingestion.id,
            )
        )
        session.commit()

        report = compute_h0_report(session, transcribe=_transcribe_falso)

        assert report.evaluated_rows_count == 2
        # Una de las dos filas: la que el extractor sí sacó.
        assert report.price_accuracy == pytest.approx(1 / 2)
        assert report.complete_accuracy == pytest.approx(1 / 2)


# --- N-05: registro longitudinal de cada corrida ----------------------------
#
# Los casos fijan la FORMA del registro (qué claves, qué tipos, cómo se
# serializa) de antemano e independiente del código, igual que el resto de esta
# suite. No miden extracción: `h0_run_record` solo aplana un `H0Report` ya
# calculado y `append_h0_run` solo escribe una línea.


class TestH0RunRecord:
    def _report(self, **overrides: object) -> H0Report:
        base: dict[str, object] = dict(
            total_curated=10,
            total_discarded=5,
            total_processed=15,
            discarded_off_focus=0,
            discard_rate=1 / 3,
            text_curated_count=6,
            image_curated_count=4,
            image_ocr_empty_count=0,
            evaluated_rows_count=12,
            evaluated_ads_count=10,
            assisted_ads_count=0,
            assisted_rows_count=0,
            product_accuracy=0.9,
            price_accuracy=0.5,
            province_accuracy=0.8,
            quantity_unit_accuracy=0.7,
            complete_accuracy=0.435,
        )
        base.update(overrides)
        return H0Report(**base)  # type: ignore[arg-type]

    def test_aplana_el_reporte_con_metadatos_de_version(self) -> None:
        record = h0_run_record(
            self._report(),
            now=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
            commit="abc1234",
            dirty=False,
            branch="master",
            tesseract_version="5.5.0",
        )

        assert record["timestamp"] == "2026-08-31T12:00:00+00:00"
        assert record["git_commit"] == "abc1234"
        assert record["git_dirty"] is False
        assert record["git_branch"] == "master"
        assert record["tesseract_version"] == "5.5.0"
        # Las 15 métricas del H0Report viajan aplanadas dentro del registro.
        assert record["complete_accuracy"] == 0.435
        assert record["evaluated_ads_count"] == 10
        assert record["price_accuracy"] == 0.5
        assert record["discard_rate"] == pytest.approx(1 / 3)

    def test_is_official_cruza_el_umbral_en_300_anuncios(self) -> None:
        kw = dict(
            now=datetime(2026, 8, 31, tzinfo=UTC),
            commit="c",
            dirty=False,
            branch="master",
            tesseract_version=None,
        )
        bajo = h0_run_record(self._report(evaluated_ads_count=299), **kw)  # type: ignore[arg-type]
        justo = h0_run_record(self._report(evaluated_ads_count=300), **kw)  # type: ignore[arg-type]

        assert bajo["is_official"] is False
        assert justo["is_official"] is True

    def test_append_escribe_una_linea_json_por_corrida(self, tmp_path: Path) -> None:
        path = tmp_path / "sub" / "h0-runs.jsonl"
        kw = dict(dirty=False, branch="master", tesseract_version=None)

        append_h0_run(
            h0_run_record(
                self._report(),
                now=datetime(2026, 8, 31, tzinfo=UTC),
                commit="c1",
                **kw,  # type: ignore[arg-type]
            ),
            path,
        )
        append_h0_run(
            h0_run_record(
                self._report(complete_accuracy=0.5),
                now=datetime(2026, 9, 1, tzinfo=UTC),
                commit="c2",
                **kw,  # type: ignore[arg-type]
            ),
            path,
        )

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["git_commit"] == "c1"
        assert json.loads(lines[1])["git_commit"] == "c2"
        assert json.loads(lines[1])["complete_accuracy"] == 0.5

    def test_append_no_reescribe_lo_ya_guardado(self, tmp_path: Path) -> None:
        path = tmp_path / "h0-runs.jsonl"
        path.write_text('{"git_commit": "viejo"}\n', encoding="utf-8")

        append_h0_run(
            h0_run_record(
                self._report(),
                now=datetime(2026, 8, 31, tzinfo=UTC),
                commit="nuevo",
                dirty=False,
                branch="master",
                tesseract_version=None,
            ),
            path,
        )

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["git_commit"] == "viejo"
        assert json.loads(lines[1])["git_commit"] == "nuevo"

    def test_sin_datos_serializa_nulls_sin_romper(self, tmp_path: Path) -> None:
        vacio = self._report(
            discard_rate=None,
            evaluated_rows_count=0,
            evaluated_ads_count=0,
            product_accuracy=None,
            price_accuracy=None,
            province_accuracy=None,
            quantity_unit_accuracy=None,
            complete_accuracy=None,
        )
        path = tmp_path / "h0-runs.jsonl"

        append_h0_run(
            h0_run_record(
                vacio,
                now=datetime(2026, 8, 31, tzinfo=UTC),
                commit="c",
                dirty=False,
                branch="master",
                tesseract_version=None,
            ),
            path,
        )

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["complete_accuracy"] is None
        assert loaded["discard_rate"] is None
        assert loaded["is_official"] is False

    def test_arbol_sucio_queda_marcado_en_el_registro(self, tmp_path: Path) -> None:
        path = tmp_path / "h0-runs.jsonl"

        append_h0_run(
            h0_run_record(
                self._report(),
                now=datetime(2026, 8, 31, tzinfo=UTC),
                commit="abc1234",
                dirty=True,
                branch="n05-h0-longitudinal",
                tesseract_version="5.5.0",
            ),
            path,
        )

        assert json.loads(path.read_text(encoding="utf-8"))["git_dirty"] is True


# --- N-05: lectura del histórico para /control -----------------------------
#
# `read_h0_runs` es la contracara de `append_h0_run`: parsea el archivo de
# solo-anexado y lo entrega como resúmenes para la vista. Los casos fijan qué
# tolera (archivo ausente, líneas rotas) y cómo calcula el delta.


class TestReadH0Runs:
    def _line(self, **overrides: object) -> str:
        record: dict[str, object] = dict(
            timestamp="2026-08-31T12:00:00+00:00",
            git_commit="abc1234",
            git_dirty=False,
            git_branch="master",
            tesseract_version="5.5.0",
            is_official=False,
            evaluated_ads_count=175,
            evaluated_rows_count=278,
            complete_accuracy=0.435,
            product_accuracy=0.9,
            price_accuracy=0.5,
            province_accuracy=0.8,
            quantity_unit_accuracy=0.7,
            discard_rate=0.678,
        )
        record.update(overrides)
        return json.dumps(record)

    def test_archivo_ausente_devuelve_lista_vacia(self, tmp_path: Path) -> None:
        assert read_h0_runs(tmp_path / "no-existe.jsonl") == []

    def test_parsea_cada_linea_y_deriva_la_fecha(self, tmp_path: Path) -> None:
        path = tmp_path / "h0-runs.jsonl"
        path.write_text(self._line() + "\n", encoding="utf-8")

        runs = read_h0_runs(path)

        assert len(runs) == 1
        assert runs[0].date == "2026-08-31"
        assert runs[0].git_commit == "abc1234"
        assert runs[0].complete_accuracy == 0.435
        assert runs[0].discard_rate == 0.678
        assert runs[0].complete_delta is None

    def test_orden_cronologico_y_delta_contra_la_anterior(self, tmp_path: Path) -> None:
        path = tmp_path / "h0-runs.jsonl"
        path.write_text(
            self._line(timestamp="2026-08-31T00:00:00+00:00", complete_accuracy=0.40)
            + "\n"
            + self._line(timestamp="2026-09-05T00:00:00+00:00", complete_accuracy=0.45)
            + "\n",
            encoding="utf-8",
        )

        runs = read_h0_runs(path)

        assert [r.date for r in runs] == ["2026-08-31", "2026-09-05"]
        assert runs[0].complete_delta is None
        assert runs[1].complete_delta == pytest.approx(0.05)

    def test_una_linea_rota_se_saltea_sin_tumbar_el_resto(self, tmp_path: Path) -> None:
        path = tmp_path / "h0-runs.jsonl"
        path.write_text(
            self._line(git_commit="c1")
            + "\n"
            + "{ esto no es json valido\n"
            + "\n"  # línea en blanco
            + "[1, 2, 3]\n"  # json válido pero no un objeto
            + self._line(git_commit="c2")
            + "\n",
            encoding="utf-8",
        )

        runs = read_h0_runs(path)

        assert [r.git_commit for r in runs] == ["c1", "c2"]

    def test_una_corrida_sin_numero_no_corta_el_delta_entre_las_que_si(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "h0-runs.jsonl"
        path.write_text(
            self._line(timestamp="2026-08-31T00:00:00+00:00", complete_accuracy=0.40)
            + "\n"
            + self._line(timestamp="2026-09-01T00:00:00+00:00", complete_accuracy=None)
            + "\n"
            + self._line(timestamp="2026-09-05T00:00:00+00:00", complete_accuracy=0.44)
            + "\n",
            encoding="utf-8",
        )

        runs = read_h0_runs(path)

        assert runs[1].complete_accuracy is None
        assert runs[1].complete_delta is None
        # La tercera compara contra la primera con número (0.40), no contra el
        # hueco del medio.
        assert runs[2].complete_delta == pytest.approx(0.04)


class TestH0CorteAsistido:
    """H0 sigue midiendo las REGLAS contra una verdad no contaminada.

    Una fila confirmada con ayuda del modelo corre el riesgo de que la verdad
    absorba un valor que escribió el modelo — y entonces H0 mediría las reglas
    contra algo que el modelo produjo. Es la misma trampa que `CLAUDE.md`
    prohíbe para los tests. El corte lo evita (`design.md`, D6).
    """

    def _id_de(self, session: Session, hash_: str) -> str:
        ingestion = session.execute(
            select(RawIngestion).where(RawIngestion.content_hash == hash_)
        ).scalar_one()
        return str(ingestion.id)

    def _log(self, tmp_path, session: Session, *hashes: str):
        ruta = tmp_path / "assist.jsonl"
        for hash_ in hashes:
            llm_assist.append_assist_run(
                {
                    "timestamp": "2026-09-01T00:00:00+00:00",
                    "ingestion_id": self._id_de(session, hash_),
                    "model": "m",
                    "requested": ["quantity"],
                    "answered": ["quantity"],
                    "dropped": [],
                    "degraded": False,
                },
                ruta,
            )
        return ruta

    def test_sin_registro_los_numeros_son_los_de_siempre(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        """La introducción del corte no mueve nada por sí sola: sin
        asistencia, el conjunto no asistido ES el conjunto entero."""
        sin_corte = compute_h0_report(session, transcribe=_transcribe_falso)
        con_log_vacio = compute_h0_report(
            session,
            transcribe=_transcribe_falso,
            assist_log_path=tmp_path / "no-existe.jsonl",
        )

        assert con_log_vacio.evaluated_rows_count == sin_corte.evaluated_rows_count
        assert con_log_vacio.complete_accuracy == sin_corte.complete_accuracy
        assert con_log_vacio.assisted_ads_count == 0
        assert con_log_vacio.assisted_rows_count == 0

    def test_un_anuncio_asistido_sale_del_conjunto_medido(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        ruta = self._log(tmp_path, session, "caso-1")

        report = compute_h0_report(
            session, transcribe=_transcribe_falso, assist_log_path=ruta
        )

        assert report.evaluated_ads_count == 5
        assert report.evaluated_rows_count == 5
        assert report.assisted_ads_count == 1
        assert report.assisted_rows_count == 1

    def test_lo_asistido_no_altera_los_totales_de_curaduria(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        """Sale de la MEDICIÓN, no de la cola: sigue siendo un anuncio curado."""
        ruta = self._log(tmp_path, session, "caso-1")

        report = compute_h0_report(
            session, transcribe=_transcribe_falso, assist_log_path=ruta
        )

        assert report.total_curated == 6
        assert report.discard_rate == pytest.approx(1 / 7)

    def test_la_precision_se_calcula_sin_lo_asistido(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        """`caso-1` es uno de los correctos: sacarlo BAJA la precisión de
        producto. Que el número se mueva es exactamente el punto — mide otro
        conjunto, y decirlo es más honesto que promediar los dos."""
        entero = compute_h0_report(session, transcribe=_transcribe_falso)
        ruta = self._log(tmp_path, session, "caso-1")
        recortado = compute_h0_report(
            session, transcribe=_transcribe_falso, assist_log_path=ruta
        )

        assert entero.product_accuracy == pytest.approx(5 / 6)
        assert recortado.product_accuracy == pytest.approx(4 / 5)

    def test_todo_asistido_deja_la_medicion_sin_datos(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        """El borde: si se asistió todo, no queda nada limpio que medir y las
        precisiones son `None`, no cero. Cero afirmaría que las reglas
        fallaron; `None` dice que no hay evidencia."""
        ruta = self._log(
            tmp_path,
            session,
            "caso-1",
            "caso-2",
            "caso-3",
            "caso-4",
            "caso-5",
            "caso-6",
        )

        report = compute_h0_report(
            session, transcribe=_transcribe_falso, assist_log_path=ruta
        )

        assert report.evaluated_rows_count == 0
        assert report.complete_accuracy is None
        assert report.assisted_ads_count == 6

    def test_una_ingesta_asistida_dos_veces_cuenta_una(
        self, session: Session, dataset: None, tmp_path
    ) -> None:
        ruta = self._log(tmp_path, session, "caso-1", "caso-1")

        report = compute_h0_report(
            session, transcribe=_transcribe_falso, assist_log_path=ruta
        )

        assert report.assisted_ads_count == 1
        assert report.evaluated_ads_count == 5
