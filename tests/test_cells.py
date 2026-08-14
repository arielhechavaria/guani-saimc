"""Agregación por celda producto × provincia × moneda — casos sintéticos.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el agregador.

Esta vista es el instrumento con el que el operador verifica lo que carga. Por
eso reporta **mediciones, no veredictos**: `n`, mediana, mínimo, máximo y
amplitud. Ningún umbral — `spec.md` §12.6 es explícito en que
«un umbral asignado por intuición y presentado como metodología es peor que no
tener umbral». N se calibra más adelante con datos reales, y esta misma vista
es la que va a permitir hacerlo.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from datetime import date, timedelta

from saimc.cells import compute_cells, compute_retail_ranges
from saimc.models import (
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType

from datetime import UTC, datetime


def _ingestion(session: Session, hash_: str) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    return row


def _precio(
    session: Session,
    *,
    product: Product,
    province: Province | None,
    price: str,
    source: RawIngestion,
    unit: str | None = None,
    sale_format: str | None = None,
    quantity: str | None = None,
    published_date: date | None = None,
    currency: str = Currency.CUP.value,
    price_cup: str | None = None,
    min_purchase_quantity: str | None = None,
) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            province_id=province.id if province else None,
            price=Decimal(price),
            currency=currency,
            source_id=source.id,
            unit=unit,
            sale_format=sale_format,
            quantity=Decimal(quantity) if quantity is not None else None,
            published_date=published_date,
            price_cup=Decimal(price_cup) if price_cup is not None else None,
            min_purchase_quantity=(
                Decimal(min_purchase_quantity) if min_purchase_quantity is not None else None
            ),
        )
    )


class TestComputeCells:
    def test_agrega_por_celda_y_ordena_por_muestra(self, session: Session) -> None:
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        azucar = Product(name="Azúcar", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([pollo, arroz, azucar, scu])
        session.commit()

        origen = _ingestion(session, "hash-celdas")
        # Pollo/Santiago: 100, 200, 300 -> n=3, mediana=200, min=100, max=300
        for p in ("100", "200", "300"):
            _precio(session, product=pollo, province=scu, price=p, source=origen)
        # Arroz/Santiago: 100, 200 -> n=2, mediana=150 (promedio de los dos centrales)
        for p in ("100", "200"):
            _precio(session, product=arroz, province=scu, price=p, source=origen)
        # Azúcar sin provincia: una sola observación
        _precio(session, product=azucar, province=None, price="400", source=origen)
        session.commit()

        celdas = compute_cells(session)

        assert [c.product_name for c in celdas] == ["Pollo", "Arroz", "Azúcar"]

        pollo_cell = celdas[0]
        assert pollo_cell.province_name == "Santiago de Cuba"
        assert pollo_cell.count == 3
        assert pollo_cell.median == Decimal("200")
        assert pollo_cell.minimum == Decimal("100")
        assert pollo_cell.maximum == Decimal("300")

        arroz_cell = celdas[1]
        assert arroz_cell.count == 2
        assert arroz_cell.median == Decimal("150")

        azucar_cell = celdas[2]
        assert azucar_cell.province_name is None
        assert azucar_cell.count == 1
        assert azucar_cell.median == Decimal("400")
        assert azucar_cell.minimum == azucar_cell.maximum == Decimal("400")

    def test_amplitud_expone_la_mezcla_de_unidades(self, session: Session) -> None:
        """Caso real del 2026-08-11: la celda de azúcar mezclaba precio
        minorista por libra (420) con saco mayorista (23500). La amplitud lo
        hace evidente sin que el sistema emita ningún juicio.
        """
        azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([azucar, scu])
        session.commit()
        origen = _ingestion(session, "hash-amplitud")
        for p in ("420", "440", "23500"):
            _precio(session, product=azucar, province=scu, price=p, source=origen)
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.count == 3
        assert celda.median == Decimal("440")
        assert celda.spread == Decimal("23500") / Decimal("420")

    def test_separa_provincias_de_un_mismo_producto(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        hab = Province(code="hab", name="La Habana")
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, hab, scu])
        session.commit()
        origen = _ingestion(session, "hash-provincias")
        _precio(session, product=arroz, province=hab, price="700", source=origen)
        _precio(session, product=arroz, province=hab, price="800", source=origen)
        _precio(session, product=arroz, province=scu, price="500", source=origen)
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        assert celdas[0].province_name == "La Habana"
        assert celdas[0].count == 2
        assert celdas[1].province_name == "Santiago de Cuba"
        assert celdas[1].count == 1

    def test_sin_datos_devuelve_lista_vacia(self, session: Session) -> None:
        assert compute_cells(session) == []


class TestCeldaSeparaUnidadYFormato:
    """Caso real del 2026-08-12: `Arroz` en Santiago traía 730 con `unit=lb` y
    750 con `unit=kg` describiendo el mismo paquete de 1 kg, más dos sacos de
    110 lb a 31.000 y 33.000. Agrupados solo por producto × provincia × moneda
    daban una sola mediana que no significaba nada.
    """

    def _arroz_y_santiago(self, session: Session) -> tuple[Product, Province]:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        return arroz, scu

    def test_separa_por_unidad(self, session: Session) -> None:
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-unidad")
        # Dos por libra (700, 800 -> mediana 750) y una por kilo (750).
        _precio(session, product=arroz, province=scu, price="700", source=origen, unit="lb")
        _precio(session, product=arroz, province=scu, price="800", source=origen, unit="lb")
        _precio(session, product=arroz, province=scu, price="750", source=origen, unit="kg")
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        por_unidad = {c.unit: c for c in celdas}
        assert por_unidad["lb"].count == 2
        assert por_unidad["lb"].median == Decimal("750")
        assert por_unidad["kg"].count == 1
        assert por_unidad["kg"].median == Decimal("750")

    def test_separa_minorista_de_mayorista(self, session: Session) -> None:
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-formato")
        # Minorista por libra (700, 800) contra dos sacos de 110 lb.
        for p in ("700", "800"):
            _precio(
                session, product=arroz, province=scu, price=p, source=origen,
                unit="lb", sale_format=SaleFormat.RETAIL.value,
            )
        for p in ("31000", "33000"):
            _precio(
                session, product=arroz, province=scu, price=p, source=origen,
                unit="lb", sale_format=SaleFormat.WHOLESALE.value,
            )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        por_formato = {c.sale_format: c for c in celdas}
        assert por_formato["retail"].median == Decimal("750")
        assert por_formato["retail"].spread == Decimal("800") / Decimal("700")
        assert por_formato["wholesale"].median == Decimal("32000")

    def test_sin_responder_no_se_funde_con_lo_respondido(self, session: Session) -> None:
        """`unit` y `sale_format` nulos son «sin responder», no un valor más.

        Fundirlos con las filas que sí traen dato inventaría una equivalencia
        que nadie afirmó — el mismo criterio con el que `canonical_unit()`
        devuelve `None` en vez de adivinar.
        """
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-nulos")
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
        )
        _precio(session, product=arroz, province=scu, price="320", source=origen)
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        sin_responder = [c for c in celdas if c.unit is None][0]
        assert sin_responder.sale_format is None
        assert sin_responder.count == 1
        assert sin_responder.median == Decimal("320")


class TestCeldaFundePorPriceCup:
    """§6.3 E: `price_cup` ya se llena desde el 2026-08-13, así que la celda
    deja de partirse por moneda — se funde por `price_cup` y solo lo que
    todavía no tiene tasa resuelta queda aparte (2026-08-13, ver docstring
    del módulo). Reemplaza el criterio viejo, documentado hasta ayer, de que
    "la moneda entra en la clave a propósito" porque no había conversión.
    """

    def _arroz_y_santiago(self, session: Session) -> tuple[Product, Province]:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        return arroz, scu

    def test_funde_cup_y_usd_convertido_en_una_celda(self, session: Session) -> None:
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-fusion-cup")
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
            currency=Currency.CUP.value,
        )
        _precio(
            session, product=arroz, province=scu, price="10", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
            currency=Currency.USD.value, price_cup="800",
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.currency == "CUP"
        assert celda.count == 2
        assert celda.median == Decimal("750")
        assert celda.minimum == Decimal("700")
        assert celda.maximum == Decimal("800")
        assert celda.unconverted is False

    def test_usd_sin_convertir_no_se_funde_y_queda_marcada(
        self, session: Session
    ) -> None:
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-sin-convertir")
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
            currency=Currency.CUP.value,
        )
        _precio(
            session, product=arroz, province=scu, price="10", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
            currency=Currency.USD.value, price_cup=None,
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        sin_convertir = [c for c in celdas if c.unconverted][0]
        assert sin_convertir.currency == "USD"
        assert sin_convertir.count == 1
        assert sin_convertir.median == Decimal("10")

        fundida = [c for c in celdas if not c.unconverted][0]
        assert fundida.currency == "CUP"
        assert fundida.count == 1

    def test_celda_toda_en_cup_no_queda_marcada(self, session: Session) -> None:
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-toda-cup")
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.unconverted is False


class TestComputeRetailRanges:
    """El producto sigue siendo uno solo aunque el formato varíe (2026-08-12):
    Arroz a granel, por saco y por paquete no son series distintas. Pero fundir
    sus precios crudos reintroduce el problema que separar por `sale_format`
    resolvió — un saco de 110 lb a 31.000 no es comparable con un paquete a 750
    aunque los dos sean "Arroz". Lo comparable es el precio **minorista** por
    unidad canónica del producto: son ~2.5x más baratos por libra al por mayor,
    que es economía real, no ruido de carga (ver docs/decisions.md).
    """

    def _arroz(self, session: Session, *, unit: str = "lb") -> Product:
        arroz = Product(name="Arroz", category="granos", unit=unit, aliases=[])
        session.add(arroz)
        session.commit()
        return arroz

    def test_normaliza_por_cantidad_y_agrupa_por_producto(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-rango-basico")
        # 700/lb y 800/lb minoristas -> mediana 750, sin importar el envase.
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )
        _precio(
            session, product=arroz, province=scu, price="800", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (rango,) = compute_retail_ranges(session).ranges

        assert rango.product_name == "Arroz"
        assert rango.unit == "lb"
        assert rango.count == 2
        assert rango.median == Decimal("750")

    def test_saco_normalizado_no_se_funde_con_minorista(
        self, session: Session
    ) -> None:
        """Mismo caso real del 2026-08-12: aunque se divida el saco por su
        cantidad, wholesale sigue siendo otro nivel de precio — se excluye,
        no se promedia.
        """
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-mayorista-excluido")
        _precio(
            session, product=arroz, province=scu, price="750", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )
        _precio(
            session, product=arroz, province=scu, price="31000", source=origen,
            unit="lb", sale_format=SaleFormat.WHOLESALE.value, quantity="110",
        )

        (rango,) = compute_retail_ranges(session).ranges

        assert rango.count == 1
        assert rango.median == Decimal("750")

    def test_sin_sale_format_respondido_se_excluye(self, session: Session) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-sin-formato")
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", quantity="1",
        )

        assert compute_retail_ranges(session).ranges == []

    def test_sin_quantity_se_excluye(self, session: Session) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-sin-cantidad")
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value,
        )

        assert compute_retail_ranges(session).ranges == []

    def test_unidad_distinta_de_la_canonica_se_excluye_sin_convertir(
        self, session: Session
    ) -> None:
        """Sin tabla de conversión lb<->kg: convertir sin que nadie la haya
        definido es exactamente lo que §12.6 prohíbe. La fila con unidad
        distinta a la canónica del producto queda fuera de esta vista — sigue
        visible en `compute_cells`, que no exige coincidencia de unidad.
        """
        arroz = self._arroz(session, unit="lb")
        origen = _ingestion(session, "hash-unidad-no-canonica")
        _precio(
            session, product=arroz, province=None, price="750", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        assert compute_retail_ranges(session).ranges == []

    def test_producto_seedeado_con_litro_canonicaliza_a_l(
        self, session: Session
    ) -> None:
        """`Product.unit` puede venir sin canonicalizar (el seed de aceite usa
        "litro", `canonical_unit()` lo normaliza a "l") — comparar contra el
        valor crudo excluiría todo el aceite minorista por accidente.
        """
        aceite = Product(name="Aceite", category="aceites", unit="litro", aliases=[])
        session.add(aceite)
        session.commit()
        origen = _ingestion(session, "hash-litro")
        _precio(
            session, product=aceite, province=None, price="500", source=origen,
            unit="l", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (rango,) = compute_retail_ranges(session).ranges
        assert rango.product_name == "Aceite"
        assert rango.unit == "l"

    def test_ventana_de_dias_excluye_lo_viejo(self, session: Session) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-ventana")
        hoy = date(2026, 8, 12)
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=hoy - timedelta(days=2),
        )
        _precio(
            session, product=arroz, province=None, price="9999", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=hoy - timedelta(days=20),
        )

        rangos = compute_retail_ranges(session, days=7, hoy=hoy).ranges

        (rango,) = rangos
        assert rango.count == 1
        assert rango.median == Decimal("700")

    def test_sin_published_date_usa_fecha_de_carga(self, session: Session) -> None:
        """Sin pasar `hoy`: `created_at` lo pone `utc_now()` al insertar (recién,
        durante el test), así que cae dentro de cualquier ventana razonable sin
        necesidad de fijar una fecha de referencia.
        """
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-sin-fecha-publicacion")
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        assert len(compute_retail_ranges(session, days=7).ranges) == 1

    def test_sin_datos_devuelve_lista_vacia(self, session: Session) -> None:
        assert compute_retail_ranges(session).ranges == []

    def test_funde_cup_y_usd_convertido_por_price_cup(self, session: Session) -> None:
        """Mismo criterio de fusión que `compute_cells` (2026-08-13): acá se
        normaliza por `quantity` antes de fundir, así que el valor esperado
        es la mediana de los dos precios por unidad ya en CUP.
        """
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-rango-fusion")
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            currency=Currency.CUP.value,
        )
        _precio(
            session, product=arroz, province=None, price="10", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            currency=Currency.USD.value, price_cup="800",
        )

        (rango,) = compute_retail_ranges(session).ranges

        assert rango.currency == "CUP"
        assert rango.count == 2
        assert rango.median == Decimal("750")
        assert rango.unconverted is False

    def test_usd_sin_convertir_queda_aparte_en_el_rango(self, session: Session) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-rango-sin-convertir")
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            currency=Currency.CUP.value,
        )
        _precio(
            session, product=arroz, province=None, price="10", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            currency=Currency.USD.value, price_cup=None,
        )

        rangos = compute_retail_ranges(session).ranges

        assert len(rangos) == 2
        sin_convertir = [r for r in rangos if r.unconverted][0]
        assert sin_convertir.currency == "USD"
        assert sin_convertir.count == 1
        assert sin_convertir.median == Decimal("10")

        fundido = [r for r in rangos if not r.unconverted][0]
        assert fundido.currency == "CUP"
        assert fundido.count == 1


class TestFiltroDeCompraMinima:
    """Cuarto filtro del rango público (2026-08-14): una fila cuya compra
    mínima supera su cantidad cotiza un precio por un volumen que no se puede
    comprar. Casos tomados del corpus real: la paca de `07202f09` («Pacas de
    30 paquetes, $730 por unidad») contra el saco de `dd2dd6a4` («por saco de
    50KG $30990»).
    """

    def _arroz(self, session: Session) -> Product:
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        return arroz

    def test_paca_cotizada_por_unidad_no_entra_al_rango(self, session: Session) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-paca")
        _precio(
            session, product=arroz, province=None, price="730", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
            min_purchase_quantity="30",
        )

        reporte = compute_retail_ranges(session)

        assert reporte.ranges == []
        assert reporte.excluded_by_minimum == 1

    def test_la_misma_paca_cargada_como_lote_si_entra(self, session: Session) -> None:
        """El filtro corrige cómo se anota la observación, no la borra: el
        precio por kg que aporta es el mismo 730."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-lote")
        _precio(
            session, product=arroz, province=None, price="21900", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="30",
            min_purchase_quantity="30",
        )

        reporte = compute_retail_ranges(session)

        (rango,) = reporte.ranges
        assert rango.median == Decimal("730")
        assert reporte.excluded_by_minimum == 0

    def test_saco_entero_con_minimo_de_un_saco_sigue_adentro(self, session: Session) -> None:
        """No es un umbral de plata: el saco cuesta MÁS que la paca excluida
        (30990 contra 21900) y entra igual, porque se puede comprar en la
        cantidad que cotiza — comprar un saco es minorista (2026-08-12)."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-saco")
        _precio(
            session, product=arroz, province=None, price="30990", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="50",
            min_purchase_quantity="50",
        )

        reporte = compute_retail_ranges(session)

        (rango,) = reporte.ranges
        assert rango.count == 1
        assert reporte.excluded_by_minimum == 0

    def test_sin_minimo_declarado_la_fila_participa(self, session: Session) -> None:
        """`NULL` es «el anuncio no exige lote», no «se desconoce»: es lo que
        tienen todas las filas anteriores a este cambio."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-null")
        _precio(
            session, product=arroz, province=None, price="750", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        reporte = compute_retail_ranges(session)

        (rango,) = reporte.ranges
        assert rango.median == Decimal("750")
        assert reporte.excluded_by_minimum == 0

    def test_la_fila_excluida_del_rango_sigue_visible_en_la_matriz(
        self, session: Session
    ) -> None:
        """`compute_cells` es el detector de mezcla del operador: su trabajo es
        mostrar lo que hay, incluido lo que el rango público descarta."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-matriz")
        _precio(
            session, product=arroz, province=None, price="730", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
            min_purchase_quantity="30",
        )

        assert compute_retail_ranges(session).ranges == []

        (celda,) = compute_cells(session)
        assert celda.product_name == "Arroz"
        assert celda.count == 1
        assert celda.median == Decimal("730")

    def test_solo_cuenta_las_filas_que_habrian_entrado(self, session: Session) -> None:
        """El conteo corre después de los otros filtros: una fila mayorista con
        mínimo grande ya estaba afuera por `sale_format` y no se cuenta dos
        veces."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-orden")
        _precio(
            session, product=arroz, province=None, price="730", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
            min_purchase_quantity="30",
        )
        _precio(
            session, product=arroz, province=None, price="0.90", source=origen,
            unit="kg", sale_format=SaleFormat.WHOLESALE.value, quantity="1",
            min_purchase_quantity="30000",
        )

        assert compute_retail_ranges(session).excluded_by_minimum == 1


class TestNormalizacionPorCantidad:
    """La celda compara precio POR UNIDAD, no precio total (2026-08-13).

    El caso real que lo motivó: un saco de 50 kg a 32500 y un paquete de 1 kg
    a 750 caían en la misma celda `kg` minorista, y la amplitud daba 43× — que
    no describía nada del mercado, solo que se estaban comparando un total
    contra otro total. Por kg son 650 contra 750: la diferencia real entre
    comprar mayorista y minorista, que es economía, no ruido de carga.

    Es el mismo criterio que `compute_retail_ranges` ya usaba; la matriz se
    había quedado atrás.
    """

    def test_divide_el_precio_por_la_cantidad(self, session: Session) -> None:
        """Saco de 50 kg a 32500 = 650/kg; paquete de 1 kg a 750 = 750/kg.
        Misma celda, amplitud 750/650 = 1.15."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = _ingestion(session, "hash-normaliza")
        _precio(
            session, product=arroz, province=scu, price="32500",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="50",
        )
        _precio(
            session, product=arroz, province=scu, price="750",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )
        session.commit()

        cells = compute_cells(session)

        assert len(cells) == 1
        assert cells[0].minimum == Decimal("650")
        assert cells[0].maximum == Decimal("750")
        assert cells[0].unquantified is False

    def test_la_fila_sin_cantidad_no_se_funde_con_las_normalizadas(
        self, session: Session
    ) -> None:
        """Sin cantidad no hay precio por unidad que calcular. Fundirla a
        ciegas asumiría cantidad 1, que es inventar el dato — mismo criterio
        que ya rige para `unit` y `sale_format` nulos."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = _ingestion(session, "hash-sin-cantidad")
        _precio(
            session, product=arroz, province=scu, price="750",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )
        _precio(
            session, product=arroz, province=scu, price="32500",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value, quantity=None,
        )
        session.commit()

        cells = compute_cells(session)

        assert len(cells) == 2
        normalizada = next(c for c in cells if not c.unquantified)
        sin_cantidad = next(c for c in cells if c.unquantified)
        assert normalizada.median == Decimal("750")
        # La celda sin cantidad conserva el precio tal cual: es un total, y la
        # vista tiene que decirlo en vez de disfrazarlo de precio unitario.
        assert sin_cantidad.median == Decimal("32500")

    def test_cantidad_cero_se_trata_como_sin_cantidad(self, session: Session) -> None:
        """Nunca debe dividir por cero, y tampoco desaparecer de la vista:
        una fila escondida es una fila que nadie va a corregir."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = _ingestion(session, "hash-cantidad-cero")
        _precio(
            session, product=arroz, province=scu, price="900",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="0",
        )
        session.commit()

        cells = compute_cells(session)

        assert len(cells) == 1
        assert cells[0].unquantified is True
        assert cells[0].median == Decimal("900")

    def test_normaliza_tambien_lo_convertido_a_cup(self, session: Session) -> None:
        """El orden importa: primero se resuelve la moneda, después se divide
        por la cantidad. 44 USD por un saco de 50 kg, a 670, son 29480 CUP =
        589.60 por kg."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = _ingestion(session, "hash-normaliza-usd")
        _precio(
            session, product=arroz, province=scu, price="44",
            source=origen, unit="kg", sale_format=SaleFormat.RETAIL.value,
            quantity="50", currency=Currency.USD.value, price_cup="29480",
        )
        session.commit()

        cells = compute_cells(session)

        assert cells[0].median == Decimal("589.6")
        assert cells[0].currency == "CUP"
