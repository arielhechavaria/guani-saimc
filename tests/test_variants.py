"""Resolución de variantes de producto — `get_or_create_variant` y el plegado
de identidad que lo hace seguro contra texto libre repetido con distinta
ortografía (design.md D3).

Los valores esperados de `variant_key`/`variant_display` se escriben a mano,
antes e independientemente de correr `get_or_create_variant`: es la capa de
extracción/validación bajo `CLAUDE.md`, y el caso sintético es legítimo
porque la respuesta correcta no depende de ningún extractor ya escrito.
"""

import pytest
from sqlalchemy.orm import Session

from saimc.models import Product, ProductVariant
from saimc.variants import get_or_create_variant, variant_display, variant_key


def _crear_producto(session: Session, *, name: str = "Aceite") -> Product:
    producto = Product(name=name, category="aceites", unit="l")
    session.add(producto)
    session.flush()
    return producto


class TestVariantKey:
    """Plegado idéntico al de `engine._strip_accents`/`llm_assist._normalize`
    (design.md D3), con `casefold()` en vez de `.lower()`: es la primitiva de
    comparación sin distinción de mayúsculas de Python, no una transformación
    de display."""

    @pytest.mark.parametrize(
        "raw", ["Vitalín", "VITALIN", "  vitalin ", "vitalín", "ViTaLin"]
    )
    def test_variantes_de_escritura_pliegan_al_mismo_valor(self, raw: str) -> None:
        assert variant_key(raw) == "vitalin"

    def test_blanco_o_none_pliega_a_string_vacio(self) -> None:
        assert variant_key("") == ""
        assert variant_key("   ") == ""
        assert variant_key(None) == ""


class TestVariantDisplay:
    """Conserva tildes y mayúsculas tal como las escribió el operador; solo
    recorta bordes y colapsa espacios internos (design.md D3)."""

    def test_recorta_bordes_y_colapsa_espacios_internos(self) -> None:
        assert variant_display("  Vitalín   importado  ") == "Vitalín importado"

    def test_blanco_o_none_pliega_a_string_vacio(self) -> None:
        assert variant_display("   ") == ""
        assert variant_display(None) == ""


class TestGetOrCreateVariant:
    """Punto único de resolución, mirroring `get_or_create_format` (design.md
    D7 del cambio hermano): declarar y elegir son el mismo acto, idempotente
    por `uq_product_variants_identity`."""

    def test_crea_en_la_primera_llamada_y_reutiliza_en_la_segunda(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)

        primera = get_or_create_variant(
            session,
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
        )
        session.commit()
        assert primera is not None

        segunda = get_or_create_variant(
            session,
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
        )
        session.commit()

        assert segunda is not None
        assert segunda.id == primera.id
        assert session.query(ProductVariant).count() == 1

    def test_la_misma_variante_de_un_solo_eje_declarada_dos_veces_da_una_fila(
        self, session: Session
    ) -> None:
        """La regresión D1: declarar el mismo `kind` dos veces con brand/origin
        vacíos las dos veces no debe crear una segunda fila — con columnas
        nullable, `NULL <> NULL` habría dejado pasar el duplicado."""
        producto = _crear_producto(session)

        primera = get_or_create_variant(
            session, product_id=producto.id, kind="girasol", brand="", origin=""
        )
        session.commit()

        segunda = get_or_create_variant(
            session, product_id=producto.id, kind="girasol", brand="", origin=""
        )
        session.commit()

        assert primera is not None and segunda is not None
        assert segunda.id == primera.id
        assert session.query(ProductVariant).count() == 1

    def test_un_eje_distinto_crea_una_segunda_variante_sin_vocabulario(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)

        get_or_create_variant(
            session,
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
        )
        session.commit()

        conchita = get_or_create_variant(
            session,
            product_id=producto.id,
            kind="girasol",
            brand="Conchita",
            origin="importado",
        )
        session.commit()

        assert conchita is not None
        assert session.query(ProductVariant).count() == 2

    def test_los_tres_ejes_vacios_devuelve_none(self, session: Session) -> None:
        producto = _crear_producto(session)

        resultado = get_or_create_variant(
            session, product_id=producto.id, kind="", brand="", origin=""
        )

        assert resultado is None
        assert session.query(ProductVariant).count() == 0
