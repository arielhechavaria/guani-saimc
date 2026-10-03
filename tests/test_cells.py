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

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.cells import (
    Cell,
    compute_availability,
    compute_cells,
    compute_retail_observations,
    compute_retail_ranges,
)
from saimc.models import (
    Channel,
    Currency,
    Platform,
    PriceHistory,
    Product,
    ProductFormat,
    ProductVariant,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType


def _ingestion(
    session: Session,
    hash_: str,
    *,
    content_type: str = ContentType.TEXT,
    channel: Channel | None = None,
) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT if content_type == ContentType.TEXT else SourceType.INBOX_IMAGE,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=content_type,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        channel_id=channel.id if channel else None,
    )
    session.add(row)
    session.flush()
    return row


def _canal(session: Session, name: str) -> Channel:
    canal = Channel(name=name, platform=Platform.WHATSAPP_CHANNEL)
    session.add(canal)
    session.flush()
    return canal


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
    price_basis: str | None = None,
    format_id: object | None = None,
    variant_id: object | None = None,
) -> None:
    session.add(
        PriceHistory(
            provenance="human",
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
            price_basis=price_basis,
            format_id=format_id,
            variant_id=variant_id,
        )
    )


def _variante(
    session: Session,
    *,
    product: Product,
    kind: str = "",
    brand: str = "",
    origin: str = "",
) -> ProductVariant:
    """Valores ya canónicos: el plegado de acento/mayúscula es
    responsabilidad de `variant_key` (`test_variants.py`), no de esta capa."""
    variante = ProductVariant(
        product_id=product.id,
        kind=kind,
        brand=brand,
        origin=origin,
        kind_key=kind,
        brand_key=brand,
        origin_key=origin,
    )
    session.add(variante)
    session.flush()
    return variante


def _formato(
    session: Session,
    *,
    product: Product,
    packaging: str,
    quantity: str,
    unit: str,
) -> ProductFormat:
    fmt = ProductFormat(
        product_id=product.id,
        packaging=packaging,
        quantity=Decimal(quantity),
        unit=unit,
    )
    session.add(fmt)
    session.flush()
    return fmt


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


class TestComposicionDeMuestra:
    """`count` cuenta mensajes; solo, se lee como si contara observaciones
    independientes. Medido contra producción el 2026-09-18: la celda de Arroz
    en Santiago tenía 27 mensajes de 2 emisores en 15 días, con el 85% de un
    solo canal, y la de Pollo en La Habana 20 mensajes con el 90% de uno. No
    son duplicados —`content_hash` es único y el 2026-08-23 ya lo verificó
    contra este mismo grupo—: son reposteos reales del mismo vendedor. Pero
    `n = 27` afirma una densidad que la muestra no tiene.

    `spec.md` §10.7 C1 pide para la Fase 0 exactamente esto —«informativa:
    tamaño de muestra visible»— y C4 nombra la señal: «concentración inusual
    del aporte por fuente o canal en una celda».

    Los tres números se calculan a mano acá, antes de correr el agregador.
    """

    def _arroz_scu(self, session: Session) -> tuple[Product, Province]:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        return arroz, scu

    def test_un_emisor_reposteando_no_multiplica_los_emisores(
        self, session: Session
    ) -> None:
        """El caso real de «Tienda: ⚜️ The Boss»: 5 mensajes, 1 emisor, 5 días."""
        arroz, scu = self._arroz_scu(session)
        canal = _canal(session, "Tienda: The Boss")
        for dia in range(1, 6):
            origen = _ingestion(session, f"hash-boss-{dia}", channel=canal)
            _precio(
                session,
                product=arroz,
                province=scu,
                price="300",
                source=origen,
                published_date=date(2026, 8, dia),
            )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.count == 5
        assert celda.sources == 1
        assert celda.unidentified_sources == 0
        assert celda.days == 5

    def test_cuenta_emisores_y_dias_distintos(self, session: Session) -> None:
        """3 mensajes: canal A el día 1 y el 2, canal B el día 1.
        -> 3 mensajes, 2 emisores, 2 días."""
        arroz, scu = self._arroz_scu(session)
        a, b = _canal(session, "Canal A"), _canal(session, "Canal B")
        for hash_, canal, dia in (
            ("hash-a1", a, 1),
            ("hash-a2", a, 2),
            ("hash-b1", b, 1),
        ):
            origen = _ingestion(session, hash_, channel=canal)
            _precio(
                session,
                product=arroz,
                province=scu,
                price="300",
                source=origen,
                published_date=date(2026, 8, dia),
            )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.count == 3
        assert celda.sources == 2
        assert celda.days == 2

    def test_sin_canal_no_se_funde_en_un_emisor_ni_se_reparte_en_varios(
        self, session: Session
    ) -> None:
        """Una ingesta sin `channel_id` no dice de quién viene. Contarlas
        todas como un emisor afirmaría que son el mismo vendedor; contar cada
        una como emisor propio afirmaría que son distintos. Ninguna de las dos
        cosas está declarada, así que quedan aparte y visibles — mismo criterio
        que `unconverted` y `unquantified`, que no se funden con lo resuelto.

        No es un caso de borde: al 2026-09-18 son 106 de 336 filas en
        producción (65 de imagen del inbox, 37 de WhatsApp sin canal, 4 de
        texto).
        """
        arroz, scu = self._arroz_scu(session)
        for hash_ in ("hash-suelta-1", "hash-suelta-2"):
            origen = _ingestion(session, hash_)
            _precio(
                session,
                product=arroz,
                province=scu,
                price="300",
                source=origen,
                published_date=date(2026, 8, 1),
            )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.count == 2
        assert celda.sources == 0
        assert celda.unidentified_sources == 2
        assert celda.days == 1

    def test_emisor_identificado_y_sin_identificar_conviven_en_la_celda(
        self, session: Session
    ) -> None:
        """La celda mixta es el caso mayoritario en producción (18 celdas,
        219 filas): 2 mensajes de un canal + 1 sin canal."""
        arroz, scu = self._arroz_scu(session)
        canal = _canal(session, "Canal A")
        for hash_ in ("hash-mix-1", "hash-mix-2"):
            origen = _ingestion(session, hash_, channel=canal)
            _precio(
                session,
                product=arroz,
                province=scu,
                price="300",
                source=origen,
                published_date=date(2026, 8, 1),
            )
        suelta = _ingestion(session, "hash-mix-3")
        _precio(
            session,
            product=arroz,
            province=scu,
            price="300",
            source=suelta,
            published_date=date(2026, 8, 1),
        )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.count == 3
        assert celda.sources == 1
        assert celda.unidentified_sources == 1

    def test_el_dia_cae_a_la_fecha_de_carga_sin_published_date(
        self, session: Session
    ) -> None:
        """Mismo repliegue que ya usan `compute_retail_observations` y
        `compute_availability`: `published_date or created_at.date()`. No se
        inventa un tercer criterio con `captured_at`."""
        arroz, scu = self._arroz_scu(session)
        canal = _canal(session, "Canal A")
        # Una con fecha declarada de otro día, otra sin declarar (cae en hoy).
        con_fecha = _ingestion(session, "hash-fecha-1", channel=canal)
        _precio(
            session,
            product=arroz,
            province=scu,
            price="300",
            source=con_fecha,
            published_date=date(2026, 8, 1),
        )
        sin_fecha = _ingestion(session, "hash-fecha-2", channel=canal)
        _precio(session, product=arroz, province=scu, price="300", source=sin_fecha)
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.count == 2
        assert celda.days == 2

    def test_las_celdas_separadas_cuentan_su_propia_composicion(
        self, session: Session
    ) -> None:
        """La composición es por celda, no por producto: separar minorista de
        mayorista tiene que repartir también los emisores y los días."""
        arroz, scu = self._arroz_scu(session)
        a, b = _canal(session, "Canal A"), _canal(session, "Canal B")
        for hash_, canal, dia in (
            ("hash-r1", a, 1),
            ("hash-r2", b, 2),
            ("hash-r3", a, 3),
        ):
            origen = _ingestion(session, hash_, channel=canal)
            _precio(
                session,
                product=arroz,
                province=scu,
                price="300",
                source=origen,
                sale_format=SaleFormat.RETAIL.value,
                published_date=date(2026, 8, dia),
            )
        mayorista = _ingestion(session, "hash-w1", channel=a)
        _precio(
            session,
            product=arroz,
            province=scu,
            price="280",
            source=mayorista,
            sale_format=SaleFormat.WHOLESALE.value,
            published_date=date(2026, 8, 1),
        )
        session.commit()

        celdas = compute_cells(session)
        minorista = next(c for c in celdas if c.sale_format == SaleFormat.RETAIL.value)
        mayor = next(c for c in celdas if c.sale_format == SaleFormat.WHOLESALE.value)

        assert (minorista.count, minorista.sources, minorista.days) == (3, 2, 3)
        assert (mayor.count, mayor.sources, mayor.days) == (1, 1, 1)


class TestSampleLabel:
    """La etiqueta que lee la composición. Vive acá y no en Jinja por el mismo
    motivo que `variant_label` y `format_label`: una cadena armada en la
    plantilla no se puede testear."""

    def _celda(self, **kwargs: int) -> Cell:
        base = {
            "product_name": "Arroz",
            "province_name": "Santiago de Cuba",
            "currency": "CUP",
            "unit": "lb",
            "sale_format": None,
            "count": 1,
            "median": Decimal("300"),
            "minimum": Decimal("300"),
            "maximum": Decimal("300"),
        }
        return Cell(**{**base, **kwargs})  # type: ignore[arg-type]

    def test_varios_emisores_y_varios_dias(self) -> None:
        celda = self._celda(count=27, sources=2, days=15)
        assert celda.sample_label == "2 emisores · 15 días"

    def test_un_emisor_y_un_dia_van_en_singular(self) -> None:
        celda = self._celda(count=1, sources=1, days=1)
        assert celda.sample_label == "1 emisor · 1 día"

    def test_sin_emisor_identificado_no_dice_cero_emisores(self) -> None:
        """«0 emisores» sería falso: no es que no haya vendedor, es que la
        ingesta no declara cuál."""
        celda = self._celda(count=2, sources=0, unidentified_sources=2, days=1)
        assert celda.sample_label == "2 sin emisor identificado · 1 día"

    def test_identificados_y_sin_identificar_conviven(self) -> None:
        celda = self._celda(count=3, sources=1, unidentified_sources=1, days=2)
        assert celda.sample_label == "1 emisor +1 sin identificar · 2 días"

    def test_celda_sin_composicion_calculada_no_inventa_etiqueta(self) -> None:
        """Los campos tienen default 0 para no romper construcciones
        posicionales viejas; con todo en cero no hay nada que afirmar."""
        assert self._celda().sample_label is None


class TestCeldaSeparaUnidadYFormato:
    """El formato de venta sigue partiendo la celda; la unidad ya no.

    Hasta el 2026-09-02 esta vista separaba kg de lb a propósito («vista de
    control de carga»). El operador pidió lo contrario: para leer cómo se
    movió el pollo en La Habana, la diferencia kg/lb es ruido. Ahora cada fila
    se lleva a la unidad canónica del producto y se funde; solo lo que no
    convierte ni en principio (`caja`, magnitudes distintas) queda aparte,
    marcado `unconverted_unit`. Reemplaza la parte de unidad de la decisión de
    2026-08-12; la parte de formato de venta sigue vigente.
    """

    def _arroz_y_santiago(self, session: Session) -> tuple[Product, Province]:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        return arroz, scu

    def test_funde_kg_en_la_unidad_canonica_del_producto(self, session: Session) -> None:
        """Dos por libra (300, 320) y una por kilo (700).

        700/kg son 700 × 0,45359237 = 317,514659/lb, calculado a mano. Una
        sola celda de tres observaciones en lb; mediana de
        [300, 317,514659, 320] = 317,514659.
        """
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-unidad")
        for p in ("300", "320"):
            _precio(
                session, product=arroz, province=scu, price=p, source=origen,
                unit="lb", quantity="1", sale_format=SaleFormat.RETAIL.value,
            )
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="kg", quantity="1", sale_format=SaleFormat.RETAIL.value,
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 1
        celda = celdas[0]
        assert celda.unit == "lb"
        assert celda.count == 3
        assert celda.median == Decimal("317.514659")
        assert celda.unconverted_unit is False

    def test_caja_no_convierte_y_queda_en_su_propia_serie(self, session: Session) -> None:
        """`caja` no declara cuánto contiene: no se funde ni en principio."""
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        hab = Province(code="hab", name="La Habana")
        session.add_all([pollo, hab])
        session.commit()
        origen = _ingestion(session, "hash-caja")
        _precio(
            session, product=pollo, province=hab, price="700", source=origen,
            unit="lb", quantity="1", sale_format=SaleFormat.RETAIL.value,
        )
        _precio(
            session, product=pollo, province=hab, price="23000", source=origen,
            unit="caja", quantity="1", sale_format=SaleFormat.RETAIL.value,
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        por_unidad = {c.unit: c for c in celdas}
        assert por_unidad["lb"].unconverted_unit is False
        assert por_unidad["caja"].unconverted_unit is True

    def test_unidad_de_otra_magnitud_no_funde_y_queda_marcada(
        self, session: Session
    ) -> None:
        """Catálogo en `unidad`, filas en `lb`: masa a conteo no convierte.

        La fila conserva su unidad cruda y se marca — mismo desenlace que la
        exclusión de `compute_retail_ranges`, pero acá visible como celda.
        """
        picadillo = Product(
            name="Picadillo", category="carnes", unit="unidad", aliases=[]
        )
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([picadillo, scu])
        session.commit()
        origen = _ingestion(session, "hash-picadillo")
        _precio(
            session, product=picadillo, province=scu, price="515", source=origen,
            unit="lb", quantity="1", sale_format=SaleFormat.RETAIL.value,
        )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.unit == "lb"
        assert celda.unconverted_unit is True

    def test_sin_unidad_no_es_lo_mismo_que_no_haber_podido_convertir(
        self, session: Session
    ) -> None:
        """Una fila `unit=None` es «sin unidad»: su celda no lleva la marca."""
        arroz, scu = self._arroz_y_santiago(session)
        origen = _ingestion(session, "hash-sin-unidad")
        _precio(
            session, product=arroz, province=scu, price="320", source=origen,
            quantity="1", sale_format=SaleFormat.RETAIL.value,
        )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.unit is None
        assert celda.unconverted_unit is False

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


class TestComputeRetailObservationsEvidencia:
    """Transparency Layer pública (docs/decisions.md, 2026-08-25): cada
    observación lleva el id de la ingesta cruda que la sostiene, y si esa
    evidencia es pública (solo texto, §10.6)."""

    def _arroz(self, session: Session) -> Product:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.commit()
        return arroz

    def test_evidencia_de_texto_es_publica(self, session: Session) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-evidencia-texto", content_type=ContentType.TEXT)
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (observacion,) = compute_retail_observations(session).observations

        assert observacion.evidence_id == origen.id
        assert observacion.evidence_is_public is True

    def test_evidencia_de_imagen_no_es_publica(self, session: Session) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-evidencia-imagen", content_type=ContentType.IMAGE)
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (observacion,) = compute_retail_observations(session).observations

        assert observacion.evidence_id == origen.id
        assert observacion.evidence_is_public is False


class TestComputeRetailRanges:
    """Desde el 2026-09-04 el rango minorista se separa por formato: «el
    paquete de arroz» y «el saco de arroz» son series distintas, porque la
    pregunta del hogar es por el formato, no por el producto. Supersede la
    decisión del 2026-08-12 que los fundía. El precio por unidad canónica
    sobrevive **solo** como comparador entre formatos.
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

    def test_dos_formatos_del_mismo_producto_son_dos_rangos(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-rango-formatos")
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="kg")
        saco = _formato(session, product=arroz, packaging="saco", quantity="25", unit="kg")
        _precio(
            session, product=arroz, province=scu, price="688.70", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
            price_basis="total", format_id=paquete.id,
        )
        _precio(
            session, product=arroz, province=scu, price="16685", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="25",
            price_basis="total", format_id=saco.id,
        )
        session.commit()

        rangos = compute_retail_ranges(session).ranges

        assert len(rangos) == 2
        assert {r.format_label for r in rangos} == {"paquete de 1 kg", "saco de 25 kg"}

    def test_el_titular_del_rango_es_el_precio_del_formato(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-rango-precio-formato")
        saco = _formato(session, product=arroz, packaging="saco", quantity="25", unit="kg")
        _precio(
            session, product=arroz, province=scu, price="16685", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="25",
            price_basis="total", format_id=saco.id,
        )
        session.commit()

        (rango,) = compute_retail_ranges(session).ranges

        # 16685 / 25 kg = 667,4 CUP/kg = 302,73 CUP/lb es el comparador.
        assert rango.median.quantize(Decimal("0.01")) == Decimal("302.73")
        # El saco de 25 kg vuelve a costar 16.685.
        assert rango.format_median.quantize(Decimal("1")) == Decimal("16685")
        assert rango.format_label == "saco de 25 kg"

    def test_sin_formato_el_rango_no_tiene_precio_de_formato(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add(scu)
        session.commit()
        origen = _ingestion(session, "hash-rango-sin-formato")
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1", format_id=None,
        )
        session.commit()

        (rango,) = compute_retail_ranges(session).ranges

        assert rango.format_median is None
        assert rango.format_label is None

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

    def test_unidad_de_la_misma_magnitud_se_convierte_a_la_canonica(
        self, session: Session
    ) -> None:
        """Reemplaza a `test_unidad_distinta_de_la_canonica_se_excluye_sin_convertir`
        (2026-08-22). La equivalencia libra/kilo es una definición, no un
        umbral de los que §12.6 manda calibrar, así que la fila entra
        convertida en vez de descartarse.

        Un kilo a 1000 es una libra a 453,59237: la libra es menos producto,
        así que cuesta menos.
        """
        arroz = self._arroz(session, unit="lb")
        origen = _ingestion(session, "hash-unidad-convertible")
        _precio(
            session, product=arroz, province=None, price="1000", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (rango,) = compute_retail_ranges(session).ranges
        assert rango.unit == "lb"
        assert rango.count == 1
        assert rango.median == Decimal("453.59237")

    def test_kilo_y_libra_del_mismo_producto_se_funden_en_una_celda(
        self, session: Session
    ) -> None:
        """Antes eran dos series que no se hablaban y una de las dos se tiraba.
        Convertidas son la misma serie, que es el punto de la unidad canónica.
        """
        arroz = self._arroz(session, unit="lb")
        origen = _ingestion(session, "hash-funde-kg-lb")
        # 1000/kg -> 453,59237/lb, y una fila ya en libras a 500.
        _precio(
            session, product=arroz, province=None, price="1000", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )
        _precio(
            session, product=arroz, province=None, price="500", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        (rango,) = compute_retail_ranges(session).ranges
        assert rango.unit == "lb"
        assert rango.count == 2
        assert rango.minimum == Decimal("453.59237")
        assert rango.maximum == Decimal("500")

    def test_el_caso_real_del_arroz_en_kilos_por_saco(self, session: Session) -> None:
        """Caso medido en producción el 2026-08-22: cuatro filas de Arroz en La
        Habana que parecían no tener relación —688,70 · 16.330 · 16.685 ·
        30.885— son cuatro lecturas del mismo precio una vez normalizadas.

        Los esperados salen de la definición, no de correr el agregador:
        16.330 CUP por 25 kg son 653,20 CUP/kg, y 653,20 x 0,45359237 =
        296,2865... CUP/lb. El rango completo queda entre ~296 y ~312, no entre
        688 y 30.885.
        """
        arroz = self._arroz(session, unit="lb")
        hab = Province(code="hab", name="La Habana")
        session.add(hab)
        session.commit()
        origen = _ingestion(session, "hash-arroz-real")
        for price, qty, unit in (
            ("688.70", "1", "kg"),
            ("16330", "25", "kg"),
            ("16685", "25", "kg"),
            ("30885", "100", "lb"),
        ):
            _precio(
                session, product=arroz, province=hab, price=price, source=origen,
                unit=unit, sale_format=SaleFormat.RETAIL.value, quantity=qty,
                price_basis="total",
            )

        (rango,) = compute_retail_ranges(session).ranges
        assert rango.unit == "lb"
        assert rango.count == 4
        # Todas caen dentro de un rango estrecho y coherente de mercado.
        assert Decimal("296") < rango.minimum < Decimal("297")
        assert Decimal("312") < rango.maximum < Decimal("313")

    def test_unidad_sin_magnitud_sigue_excluida_y_se_cuenta(
        self, session: Session
    ) -> None:
        """`caja` no dice cuánto contiene: no es convertible ni en principio.
        Se sigue excluyendo, pero ahora el descarte es visible en vez de
        silencioso — que era la mitad del problema.
        """
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        session.add(pollo)
        session.commit()
        origen = _ingestion(session, "hash-caja")
        _precio(
            session, product=pollo, province=None, price="9000", source=origen,
            unit="caja", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        report = compute_retail_ranges(session)
        assert report.ranges == []
        assert report.excluded_by_unit == 1

    def test_magnitud_distinta_se_excluye_y_se_cuenta(self, session: Session) -> None:
        """Producto con unidad de catálogo `unidad` y filas en `lb`: el caso
        real de Picadillo. Convertir masa a conteo exigiría saber cuánto pesa
        una unidad, así que la fila no entra — pero el contador deja ver que la
        unidad del catálogo es la que está mal elegida.
        """
        picadillo = Product(name="Picadillo", category="carnes", unit="unidad", aliases=[])
        session.add(picadillo)
        session.commit()
        origen = _ingestion(session, "hash-picadillo")
        _precio(
            session, product=picadillo, province=None, price="1200", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        report = compute_retail_ranges(session)
        assert report.ranges == []
        assert report.excluded_by_unit == 1

    def test_la_fila_convertida_no_incrementa_el_contador_de_excluidas(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session, unit="lb")
        origen = _ingestion(session, "hash-convertida-no-cuenta")
        _precio(
            session, product=arroz, province=None, price="1000", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
        )

        assert compute_retail_ranges(session).excluded_by_unit == 0

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

    def test_hasta_excluye_lo_posterior_al_rango_elegido(self, session: Session) -> None:
        """Date picker de rango de calendario (2026-09-14): `hasta` acota el
        otro lado de la ventana — sin tocar `cutoff`/`days`, sigue siendo
        `[cutoff, hasta]` en vez de `[cutoff, ∞)`."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-hasta")
        hoy = date(2026, 8, 12)
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=hoy - timedelta(days=5),
        )
        _precio(
            session, product=arroz, province=None, price="9999", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=hoy,
        )

        rangos = compute_retail_ranges(
            session, days=30, hoy=hoy, hasta=hoy - timedelta(days=1)
        ).ranges

        (rango,) = rangos
        assert rango.count == 1
        assert rango.median == Decimal("700")

    def test_sin_hasta_no_hay_tope_superior(self, session: Session) -> None:
        """`hasta=None` (el default) es el comportamiento de siempre: nada
        de lo que ya funcionaba (`/control`, `compute_availability`) se ve
        afectado por sumar el parámetro."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-sin-hasta")
        hoy = date(2026, 8, 12)
        _precio(
            session, product=arroz, province=None, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=hoy,
        )

        rangos = compute_retail_ranges(session, days=7, hoy=hoy).ranges

        (rango,) = rangos
        assert rango.count == 1

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
            min_purchase_quantity="30", price_basis="total",
        )

        reporte = compute_retail_ranges(session)

        (rango,) = reporte.ranges
        assert rango.median == Decimal("730")
        assert reporte.excluded_by_minimum == 0
        assert reporte.excluded_by_basis == 0

    def test_saco_entero_con_minimo_de_un_saco_sigue_adentro(self, session: Session) -> None:
        """No es un umbral de plata: el saco cuesta MÁS que la paca excluida
        (30990 contra 21900) y entra igual, porque se puede comprar en la
        cantidad que cotiza — comprar un saco es minorista (2026-08-12)."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-min-saco")
        _precio(
            session, product=arroz, province=None, price="30990", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="50",
            min_purchase_quantity="50", price_basis="total",
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
            price_basis="total",
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
        assert cells[0].unresolved_basis is False

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
            price_basis="total",
        )
        session.commit()

        cells = compute_cells(session)

        assert cells[0].median == Decimal("589.6")
        assert cells[0].currency == "CUP"


class TestPriceBasis:
    """`price_basis` declara qué representa `price`, y la división deja de
    ser incondicional (era el criterio desde 2026-08-13). Caso real
    (`proposal.md`): «Arroz guyanés a 340lb» (`price=340, quantity=340`) sin
    basis se leía implícitamente como `'total'` y agregaba 1 CUP/lb en vez de
    ~340 — exactamente el bug que este cambio corrige.
    """

    def test_basis_unit_no_divide(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-unit")
        _precio(
            session, product=arroz, province=None, price="340", source=origen,
            unit="lb", quantity="340", price_basis="unit",
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.median == Decimal("340")
        assert celda.unresolved_basis is False

    def test_basis_total_divide(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-total")
        _precio(
            session, product=arroz, province=None, price="21900", source=origen,
            unit="kg", quantity="30", price_basis="total",
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.median == Decimal("730")
        assert celda.unresolved_basis is False

    def test_quantity_uno_no_depende_de_basis(self, session: Session) -> None:
        """Con `quantity = 1`, unitario y total son el mismo número: la fila
        se agrega igual que antes de este cambio, sin marcarse sin resolver."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-qty-uno")
        _precio(
            session, product=arroz, province=None, price="750", source=origen,
            unit="kg", quantity="1", price_basis=None,
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.median == Decimal("750")
        assert celda.unresolved_basis is False

    def test_basis_sin_resolver_va_a_celda_propia_y_no_se_funde(
        self, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = _ingestion(session, "hash-basis-sin-resolver")
        _precio(
            session, product=arroz, province=scu, price="340", source=origen,
            unit="lb", quantity="340", price_basis=None,
        )
        _precio(
            session, product=arroz, province=scu, price="700", source=origen,
            unit="lb", quantity="1", price_basis="unit",
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        sin_resolver = [c for c in celdas if c.unresolved_basis][0]
        assert sin_resolver.count == 1
        # Sin resolver: agrega el total tal cual, no lo divide (asumir
        # 'total' sería fabricar la respuesta que el operador nunca dio).
        assert sin_resolver.median == Decimal("340")
        resuelta = [c for c in celdas if not c.unresolved_basis][0]
        assert resuelta.count == 1
        assert resuelta.median == Decimal("700")

    def test_fila_sin_resolver_se_excluye_del_rango_minorista(
        self, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-excluded-basis")
        _precio(
            session, product=arroz, province=None, price="340", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="340",
            price_basis=None,
        )

        reporte = compute_retail_ranges(session)

        assert reporte.ranges == []
        assert reporte.excluded_by_basis == 1

    def test_exclusion_por_minimo_y_por_basis_no_se_absorben(
        self, session: Session
    ) -> None:
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-y-minimo")
        _precio(
            session, product=arroz, province=None, price="730", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="1",
            min_purchase_quantity="30",
        )
        _precio(
            session, product=arroz, province=None, price="340", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="340",
            price_basis=None,
        )

        reporte = compute_retail_ranges(session)

        assert reporte.excluded_by_minimum == 1
        assert reporte.excluded_by_basis == 1

    def test_una_sola_fila_excluida_por_los_dos_motivos_a_la_vez_no_se_cuenta_doble(
        self, session: Session
    ) -> None:
        """Guarda de regresión de un hallazgo de `sdd-verify` (2026-08-21): el
        caso anterior prueba independencia con DOS filas, una por motivo. Acá
        una sola fila dispara los dos filtros a la vez (`quantity != 1` y
        `price_basis` sin resolver, más `min_purchase_quantity` por encima de
        `quantity`) — el filtro de mínimo corre primero y hace `continue`
        (design.md, D7: «cada contador reporta filas que todo OTRO filtro
        habría admitido»), así que la fila cuenta una sola vez, en el de
        mínimo, y `excluded_by_basis` se queda en 0 para ella. Invertir el
        orden de las dos guardas en `compute_retail_ranges` cambiaría cuál de
        los dos cuenta sin romper ningún otro test — por eso hace falta fijar
        el comportamiento acá."""
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-y-minimo-misma-fila")
        _precio(
            session, product=arroz, province=None, price="340", source=origen,
            unit="kg", sale_format=SaleFormat.RETAIL.value, quantity="340",
            min_purchase_quantity="1000", price_basis=None,
        )

        reporte = compute_retail_ranges(session)

        assert reporte.ranges == []
        assert reporte.excluded_by_minimum == 1
        assert reporte.excluded_by_basis == 0

    def test_moneda_se_resuelve_antes_que_basis(self, session: Session) -> None:
        """El orden importa, igual que con `quantity`: la decisión de basis
        se evalúa sobre la cifra YA convertida a CUP, no sobre la original.

        `basis='unit'` con la conversión ya resuelta: el precio en CUP NO se
        divide. Contra el criterio previo a este cambio (dividir siempre por
        `quantity`) esto da 730, no 21900 — la prueba de que la división dejó
        de ser incondicional.
        """
        arroz = Product(name="Arroz", category="granos", unit="kg", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-basis-moneda")
        _precio(
            session, product=arroz, province=None, price="60", source=origen,
            unit="kg", quantity="30", currency=Currency.USD.value,
            price_cup="21900", price_basis="unit",
        )
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.currency == "CUP"
        assert celda.median == Decimal("21900")

    def test_basis_unit_en_rango_minorista_no_divide(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.commit()
        origen = _ingestion(session, "hash-rango-basis-unit")
        _precio(
            session, product=arroz, province=None, price="340", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="340",
            price_basis="unit",
        )

        (rango,) = compute_retail_ranges(session).ranges

        assert rango.median == Decimal("340")


class TestComputeAvailability:
    """Índice de disponibilidad (spec.md §7.3), sin score ni umbral.

    §7.3 define `availability_index` con un `score` 0-100 y un `trend`, pero
    nunca dice cómo calcular el score, y el ejemplo de §6.7 ("32/100 ->
    Escasez") es un umbral sobre una escala que no existe — inventarla
    violaría §12.6. Lo que sí se puede afirmar sin inventar nada es cuántos
    anuncios minoristas hubo esta ventana contra la anterior, mismo ancho.
    """

    def _arroz(self, session: Session) -> Product:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        session.add(arroz)
        session.commit()
        return arroz

    def _fila(
        self,
        session: Session,
        *,
        product: Product,
        province: Province | None,
        origen: RawIngestion,
        published_date: date,
    ) -> None:
        _precio(
            session, product=product, province=province, price="700", source=origen,
            unit="lb", sale_format=SaleFormat.RETAIL.value, quantity="1",
            published_date=published_date,
        )

    def test_cuenta_ventana_actual_y_anterior_por_separado(
        self, session: Session
    ) -> None:
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-disponibilidad-basico")
        hoy = date(2026, 8, 22)

        # Ventana actual: [hoy-7, hoy) -> 3 filas dentro.
        for dias_atras in (0, 2, 6):
            self._fila(
                session, product=arroz, province=None, origen=origen,
                published_date=hoy - timedelta(days=dias_atras),
            )
        # Ventana anterior: [hoy-14, hoy-7) -> 1 fila dentro.
        self._fila(
            session, product=arroz, province=None, origen=origen,
            published_date=hoy - timedelta(days=10),
        )
        session.commit()

        (fila,) = compute_availability(session, days=7, hoy=hoy)

        assert fila.product_name == "Arroz"
        assert fila.current_count == 3
        assert fila.previous_count == 1
        assert fila.delta == 2

    def test_producto_sin_anuncios_esta_semana_da_delta_negativo(
        self, session: Session
    ) -> None:
        """El producto no desaparece de la lista solo porque se quedó en
        silencio: `current_count=0` con `previous_count>0` es justamente la
        señal de escasez que esta vista existe para mostrar, sin clasificarla.
        """
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-disponibilidad-silencio")
        hoy = date(2026, 8, 22)

        for dias_atras in (8, 9, 12):
            self._fila(
                session, product=arroz, province=None, origen=origen,
                published_date=hoy - timedelta(days=dias_atras),
            )
        session.commit()

        (fila,) = compute_availability(session, days=7, hoy=hoy)

        assert fila.current_count == 0
        assert fila.previous_count == 3
        assert fila.delta == -3

    def test_sin_anuncios_en_ninguna_ventana_no_aparece(
        self, session: Session
    ) -> None:
        """Nada que reportar no es lo mismo que 0 contra 0: fabricar una fila
        en cero para todo el catálogo inventaría un dato que nadie observó."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-disponibilidad-viejo")
        hoy = date(2026, 8, 22)

        self._fila(
            session, product=arroz, province=None, origen=origen,
            published_date=hoy - timedelta(days=30),
        )
        session.commit()

        assert compute_availability(session, days=7, hoy=hoy) == []

    def test_separa_por_provincia(self, session: Session) -> None:
        arroz = self._arroz(session)
        scu = Province(code="scu", name="Santiago de Cuba")
        hab = Province(code="hab", name="La Habana")
        session.add_all([scu, hab])
        session.commit()
        origen = _ingestion(session, "hash-disponibilidad-provincia")
        hoy = date(2026, 8, 22)

        for _ in range(2):
            self._fila(
                session, product=arroz, province=scu, origen=origen,
                published_date=hoy - timedelta(days=1),
            )
        self._fila(
            session, product=arroz, province=hab, origen=origen,
            published_date=hoy - timedelta(days=1),
        )
        session.commit()

        filas = compute_availability(session, days=7, hoy=hoy)

        por_provincia = {f.province_name: f.current_count for f in filas}
        assert por_provincia == {"Santiago de Cuba": 2, "La Habana": 1}

    def test_el_dia_frontera_cae_en_la_ventana_actual(self, session: Session) -> None:
        """`published_date == hoy - days` es el primer día de la ventana
        actual, no el último de la anterior — mismo límite que usa
        `compute_retail_observations` (`effective_date < cutoff` excluye,
        no `<=`)."""
        arroz = self._arroz(session)
        origen = _ingestion(session, "hash-disponibilidad-frontera")
        hoy = date(2026, 8, 22)

        self._fila(
            session, product=arroz, province=None, origen=origen,
            published_date=hoy - timedelta(days=7),
        )
        session.commit()

        (fila,) = compute_availability(session, days=7, hoy=hoy)

        assert fila.current_count == 1
        assert fila.previous_count == 0

    def test_ordena_por_conteo_actual_descendente(self, session: Session) -> None:
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        arroz = self._arroz(session)
        session.add(pollo)
        session.commit()
        origen = _ingestion(session, "hash-disponibilidad-orden")
        hoy = date(2026, 8, 22)

        self._fila(
            session, product=arroz, province=None, origen=origen,
            published_date=hoy - timedelta(days=1),
        )
        for _ in range(3):
            self._fila(
                session, product=pollo, province=None, origen=origen,
                published_date=hoy - timedelta(days=1),
            )
        session.commit()

        filas = compute_availability(session, days=7, hoy=hoy)

        assert [f.product_name for f in filas] == ["Pollo", "Arroz"]


class TestCeldaSeparaPorFormato:
    """El formato de venta es la identidad de la serie (spec.md §5, «o a
    unidades distintas declaradas como tales»). Revierte la exclusión que
    `cells.py` documentaba: ninguna de las 39 filas de Pollo del corpus tiene
    `quantity = 1`, así que el precio por unidad que esa regla producía no
    corresponde a ninguna transacción observada.
    """

    def test_dos_formatos_del_mismo_producto_y_provincia_son_dos_celdas(
        self, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="cárnicos", unit="lb")
        session.add(pollo)
        habana = Province(code="hab", name="La Habana")
        session.add(habana)
        session.flush()
        fuente = _ingestion(session, "hash-formatos")
        caja = _formato(session, product=pollo, packaging="caja", quantity="40", unit="lb")
        paquete = _formato(
            session, product=pollo, packaging="paquete", quantity="10", unit="lb"
        )
        _precio(
            session, product=pollo, province=habana, price="12400", source=fuente,
            unit="lb", quantity="40", price_basis="total", format_id=caja.id,
        )
        _precio(
            session, product=pollo, province=habana, price="3500", source=fuente,
            unit="lb", quantity="10", price_basis="total", format_id=paquete.id,
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        assert {c.format_id for c in celdas} == {caja.id, paquete.id}

    def test_una_fila_sin_formato_no_se_funde_con_una_que_si_lo_tiene(
        self, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="cárnicos", unit="lb")
        session.add(pollo)
        session.flush()
        fuente = _ingestion(session, "hash-sin-formato")
        caja = _formato(session, product=pollo, packaging="caja", quantity="40", unit="lb")
        _precio(
            session, product=pollo, province=None, price="12400", source=fuente,
            unit="lb", quantity="40", price_basis="total", format_id=caja.id,
        )
        _precio(
            session, product=pollo, province=None, price="12400", source=fuente,
            unit="lb", quantity="40", price_basis="total", format_id=None,
        )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        sin_formato = [c for c in celdas if c.format_id is None]
        assert len(sin_formato) == 1
        assert sin_formato[0].format_label is None


class TestPrecioDelFormato:
    """El titular es lo que se compra, no una división. `resolve_row()` sigue
    dando el precio por unidad base —el comparador entre formatos— y el precio
    del formato se deriva multiplicándolo por su cantidad (design.md D8)."""

    def test_el_precio_del_formato_es_la_mediana_por_su_cantidad(
        self, session: Session
    ) -> None:
        pollo = Product(name="Pollo", category="cárnicos", unit="lb")
        session.add(pollo)
        session.flush()
        fuente = _ingestion(session, "hash-precio-formato")
        caja = _formato(session, product=pollo, packaging="caja", quantity="40", unit="lb")
        _precio(
            session, product=pollo, province=None, price="12400", source=fuente,
            unit="lb", quantity="40", price_basis="total", format_id=caja.id,
        )
        session.commit()

        celda = compute_cells(session)[0]

        # 12.400 / 40 lb = 310 CUP/lb es el comparador; la caja vale 12.400.
        assert celda.median == Decimal("310")
        assert celda.format_median == Decimal("12400")
        assert celda.format_label == "caja de 40 lb"

    def test_el_formato_en_otra_unidad_se_convierte_a_la_del_producto(
        self, session: Session
    ) -> None:
        """Producto canónico en lb, formato declarado en kg: la caja de 10 kg
        son 22,046 lb, y su precio sale de multiplicar por esa cantidad."""
        pollo = Product(name="Pollo", category="cárnicos", unit="lb")
        session.add(pollo)
        session.flush()
        fuente = _ingestion(session, "hash-formato-kg")
        caja = _formato(session, product=pollo, packaging="caja", quantity="10", unit="kg")
        _precio(
            session, product=pollo, province=None, price="7810", source=fuente,
            unit="kg", quantity="10", price_basis="total", format_id=caja.id,
        )
        session.commit()

        celda = compute_cells(session)[0]

        # El anuncio real dice «354 CUP/LB»: 7810 / 10 kg = 781 CUP/kg,
        # y 781 x 0,45359237 = 354,2554 -> 354.
        assert celda.median.quantize(Decimal("1")) == Decimal("354")
        # Y la caja vuelve a costar lo que costaba.
        assert celda.format_median.quantize(Decimal("1")) == Decimal("7810")
        assert celda.format_label == "caja de 10 kg"

    def test_sin_formato_no_hay_precio_de_formato(self, session: Session) -> None:
        pollo = Product(name="Pollo", category="cárnicos", unit="lb")
        session.add(pollo)
        session.flush()
        fuente = _ingestion(session, "hash-sin-fmt-precio")
        _precio(
            session, product=pollo, province=None, price="310", source=fuente,
            unit="lb", quantity="1", format_id=None,
        )
        session.commit()

        celda = compute_cells(session)[0]

        assert celda.format_median is None
        assert celda.format_label is None


class TestCeldaSeparaPorVariante:
    """La variante declarada es un segundo eje de identidad, libre y
    cruzable con el formato (product-variants; design.md D4)."""

    def test_sin_variante_declarada_la_celda_no_cambia(self, session: Session) -> None:
        """spec: public-price-range — antes de declarar cualquier variante,
        las celdas existentes no cambian."""
        pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[])
        session.add(pollo)
        session.commit()
        origen = _ingestion(session, "hash-sin-variante")
        for p in ("100", "200", "300"):
            _precio(session, product=pollo, province=None, price=p, source=origen)
        session.commit()

        (celda,) = compute_cells(session)

        assert celda.count == 3
        assert celda.median == Decimal("200")
        assert celda.variant_id is None
        assert celda.variant_label is None

    def test_dos_variantes_son_dos_celdas_y_lo_sin_declarar_no_se_funde(
        self, session: Session
    ) -> None:
        """spec: public-price-range — dos variantes del mismo producto son
        dos celdas distintas, cada una con su propio conteo y mediana."""
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.flush()
        fuente = _ingestion(session, "hash-variantes")
        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        magdalena = _variante(
            session, product=aceite, kind="girasol", brand="Magdalena", origin="importado"
        )
        _precio(session, product=aceite, province=None, price="100", source=fuente, variant_id=vitalin.id)
        _precio(session, product=aceite, province=None, price="120", source=fuente, variant_id=vitalin.id)
        _precio(session, product=aceite, province=None, price="90", source=fuente, variant_id=magdalena.id)
        _precio(session, product=aceite, province=None, price="80", source=fuente, variant_id=None)
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 3
        por_variante = {c.variant_id: c for c in celdas}
        assert por_variante[vitalin.id].count == 2
        assert por_variante[vitalin.id].median == Decimal("110")
        assert por_variante[magdalena.id].count == 1
        assert por_variante[None].count == 1

    def test_una_variante_cruzada_con_dos_formatos_da_cuatro_celdas(
        self, session: Session
    ) -> None:
        """spec: product-variants — tres ejes independientes; design.md D4 —
        `_FormatPriced` no se toca."""
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.flush()
        fuente = _ingestion(session, "hash-variante-cruce-formato")
        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        botella = _formato(session, product=aceite, packaging="botella", quantity="1", unit="l")
        caja = _formato(session, product=aceite, packaging="caja", quantity="12", unit="l")
        for variant_id in (vitalin.id, None):
            for formato in (botella, caja):
                _precio(
                    session, product=aceite, province=None, price="500", source=fuente,
                    unit="l", quantity=str(formato.quantity), price_basis="total",
                    format_id=formato.id, variant_id=variant_id,
                )
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 4
        assert {(c.variant_id, c.format_id) for c in celdas} == {
            (vitalin.id, botella.id),
            (vitalin.id, caja.id),
            (None, botella.id),
            (None, caja.id),
        }

    def test_el_precio_del_formato_no_cambia_por_declarar_variante(
        self, session: Session
    ) -> None:
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.flush()
        fuente = _ingestion(session, "hash-variante-formato-precio")
        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        caja = _formato(session, product=aceite, packaging="caja", quantity="12", unit="l")
        _precio(
            session, product=aceite, province=None, price="6000", source=fuente,
            unit="l", quantity="12", price_basis="total", format_id=caja.id, variant_id=vitalin.id,
        )
        session.commit()

        celda = compute_cells(session)[0]

        # 6000 / 12 l = 500 CUP/l es el comparador; la caja vale 6000 — misma
        # aritmética que sin variante declarada (`_FormatPriced` intacto).
        assert celda.median == Decimal("500")
        assert celda.format_median == Decimal("6000")
        assert celda.variant_label == "girasol Vitalín importado"


class TestFiltrarCeldasPorUnEjeDeVariante:
    """Con kind/brand/origin viajando en la celda, agrupar por un solo eje es
    un filtro en Python sobre lo ya calculado (public-price-range: "grouping
    by one axis crosses the other two")."""

    def test_filtrar_por_kind_cruza_la_marca(self, session: Session) -> None:
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.flush()
        fuente = _ingestion(session, "hash-filtro-eje")
        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        magdalena = _variante(
            session, product=aceite, kind="girasol", brand="Magdalena", origin="importado"
        )
        _precio(session, product=aceite, province=None, price="100", source=fuente, variant_id=vitalin.id)
        _precio(session, product=aceite, province=None, price="90", source=fuente, variant_id=magdalena.id)
        session.commit()

        celdas = compute_cells(session)
        girasol = [c for c in celdas if c.variant_kind == "girasol"]

        assert len(girasol) == 2
        assert {c.variant_brand for c in girasol} == {"Vitalín", "Magdalena"}


class TestDeclararVarianteNoBorraLaSerieGenerica:
    def test_la_serie_generica_mantiene_su_conteo(self, session: Session) -> None:
        """spec: public-price-range — declarar una variante nueva no borra ni
        funde la serie de lo no declarado."""
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.flush()
        fuente = _ingestion(session, "hash-generica-intacta")
        for _ in range(12):
            _precio(session, product=aceite, province=None, price="100", source=fuente, variant_id=None)
        session.commit()

        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        _precio(session, product=aceite, province=None, price="150", source=fuente, variant_id=vitalin.id)
        session.commit()

        celdas = compute_cells(session)

        assert len(celdas) == 2
        generica = next(c for c in celdas if c.variant_id is None)
        declarada = next(c for c in celdas if c.variant_id == vitalin.id)
        assert generica.count == 12
        assert declarada.count == 1


class TestVariantesEnRangoMinorista:
    """`compute_retail_ranges`/`compute_retail_observations` cargan la misma
    variante que `compute_cells` (public-price-range: "the variant enters the
    aggregation key ... alongside the existing format axis")."""

    def test_dos_variantes_dan_dos_rangos_y_dos_observaciones(
        self, session: Session
    ) -> None:
        aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[])
        session.add(aceite)
        session.commit()
        fuente = _ingestion(session, "hash-rango-variante")
        vitalin = _variante(
            session, product=aceite, kind="girasol", brand="Vitalín", origin="importado"
        )
        magdalena = _variante(
            session, product=aceite, kind="girasol", brand="Magdalena", origin="importado"
        )
        _precio(
            session, product=aceite, province=None, price="500", source=fuente,
            unit="l", sale_format=SaleFormat.RETAIL.value, quantity="1", variant_id=vitalin.id,
        )
        _precio(
            session, product=aceite, province=None, price="450", source=fuente,
            unit="l", sale_format=SaleFormat.RETAIL.value, quantity="1", variant_id=magdalena.id,
        )
        session.commit()

        rangos = compute_retail_ranges(session).ranges
        observaciones = compute_retail_observations(session).observations

        assert len(rangos) == 2
        assert {r.variant_id for r in rangos} == {vitalin.id, magdalena.id}
        assert {r.variant_label for r in rangos} == {
            "girasol Vitalín importado",
            "girasol Magdalena importado",
        }
        assert {o.variant_id for o in observaciones} == {vitalin.id, magdalena.id}
        assert {(o.variant_kind, o.variant_brand, o.variant_origin) for o in observaciones} == {
            ("girasol", "Vitalín", "importado"),
            ("girasol", "Magdalena", "importado"),
        }
