"""`get_or_create_format` — punto único de resolución del triple
`(envase, cantidad, unidad)` a un `ProductFormat`.

Lo usan el formulario de curaduría y `/catalog` (design.md D7). Idempotente
por `uq_product_formats_identity`: declarar un formato y elegirlo son el mismo
acto, así que la creación al vuelo no puede duplicar aunque el operador tipee
la misma caja en dos anuncios.

Casos sintéticos escritos antes que `src/saimc/formats.py` (CLAUDE.md: en la
capa de validación el caso esperado va antes que el código).

`format_label` vive en el mismo módulo y se prueba acá abajo: es punto único
de cómo se *nombra* el triple, igual que `get_or_create_format` lo es de cómo
se resuelve.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.formats import format_label, format_quantity, get_or_create_format
from saimc.models import Product, ProductFormat


def _producto(session: Session, name: str = "Pollo") -> Product:
    producto = Product(name=name, category="cárnicos", unit="lb")
    session.add(producto)
    session.flush()
    return producto


def _contar_formatos(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(ProductFormat)) or 0


class TestGetOrCreateFormatCreacion:
    def test_primera_llamada_crea_el_formato(self, session: Session) -> None:
        producto = _producto(session)
        formato = get_or_create_format(
            session,
            product_id=producto.id,
            packaging="caja",
            quantity=Decimal("40"),
            unit="lb",
        )
        assert formato.id is not None
        assert formato.product_id == producto.id
        assert formato.packaging == "caja"
        assert formato.quantity == Decimal("40")
        assert formato.unit == "lb"
        assert _contar_formatos(session) == 1


class TestGetOrCreateFormatIdempotencia:
    def test_el_mismo_triple_devuelve_la_misma_fila(self, session: Session) -> None:
        producto = _producto(session)
        primero = get_or_create_format(
            session,
            product_id=producto.id,
            packaging="caja",
            quantity=Decimal("40"),
            unit="lb",
        )
        segundo = get_or_create_format(
            session,
            product_id=producto.id,
            packaging="caja",
            quantity=Decimal("40"),
            unit="lb",
        )
        assert segundo.id == primero.id
        assert _contar_formatos(session) == 1

    def test_distinta_cantidad_crea_un_segundo_formato(self, session: Session) -> None:
        producto = _producto(session)
        get_or_create_format(
            session,
            product_id=producto.id,
            packaging="caja",
            quantity=Decimal("40"),
            unit="lb",
        )
        get_or_create_format(
            session,
            product_id=producto.id,
            packaging="caja",
            quantity=Decimal("33"),
            unit="lb",
        )
        # El tamaño es identidad (design.md D1): la caja de 33 lb es otra serie.
        assert _contar_formatos(session) == 2

    def test_el_mismo_triple_bajo_otro_producto_es_otro_formato(
        self, session: Session
    ) -> None:
        pollo = _producto(session, "Pollo")
        arroz = _producto(session, "Arroz")
        get_or_create_format(
            session, product_id=pollo.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        get_or_create_format(
            session, product_id=arroz.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        assert _contar_formatos(session) == 2


class TestGetOrCreateFormatValidacion:
    """El punto único de resolución es estricto: el vocabulario se
    canonicaliza en el llamador (`curation.py`, `/catalog`), acá se exige ya
    canónico y todo lo demás levanta antes de tocar la sesión."""

    def test_envase_fuera_del_vocabulario_levanta_sin_tocar_la_sesion(
        self, session: Session
    ) -> None:
        producto = _producto(session)
        with pytest.raises(ValueError):
            get_or_create_format(
                session,
                product_id=producto.id,
                packaging="contenedor",
                quantity=Decimal("40"),
                unit="lb",
            )
        assert _contar_formatos(session) == 0
        assert not session.new

    def test_unidad_fuera_del_vocabulario_levanta(self, session: Session) -> None:
        producto = _producto(session)
        with pytest.raises(ValueError):
            get_or_create_format(
                session,
                product_id=producto.id,
                packaging="caja",
                quantity=Decimal("40"),
                unit="quintal",
            )
        assert _contar_formatos(session) == 0


class TestFormatLabel:
    """Cómo se nombra el triple en pantalla. Punto único: lo leen `/catalog`,
    la Matriz y el `<select>` de la cola, y tres copias harían que el mismo
    formato se lea distinto en cada pantalla."""

    def test_la_etiqueta_nombra_el_triple_en_espanol(self) -> None:
        assert format_label("caja", Decimal("40"), "lb") == "caja de 40 lb"
        assert format_label("botella", Decimal("900"), "ml") == "botella de 900 ml"

    def test_la_escala_de_numeric_no_llega_a_la_pantalla(self) -> None:
        """`Numeric(12, 3)` devuelve 40.000 y 0.900: el operador nunca tipeó
        esos ceros, así que no los ve."""
        assert format_label("caja", Decimal("40.000"), "lb") == "caja de 40 lb"
        assert format_label("botella", Decimal("0.900"), "l") == "botella de 0.9 l"

    def test_la_cantidad_sale_como_se_escribe(self) -> None:
        assert format_quantity(Decimal("40.000")) == "40"
        assert format_quantity(Decimal("0.900")) == "0.9"
        assert format_quantity(Decimal("1")) == "1"
