"""Vista del historial de ediciones (slice K1): el `detail` de un evento
`edited` convertido en filas legibles. Lógica pura, sin base de datos.

Los casos se escriben a mano con la forma documentada en
`docs/spec-slice-A2.md` («detail»), no derivados de lo que escribe
`submit_edit`.
"""

from decimal import Decimal

from saimc.curation_events import (
    CatalogNames,
    describe_edit,
    referenced_catalog_ids,
)

PRODUCTO = "11111111-1111-1111-1111-111111111111"
PROVINCIA = "22222222-2222-2222-2222-222222222222"
NOMBRES: CatalogNames = {
    "product_id": {PRODUCTO: "Pollo"},
    "province_id": {PROVINCIA: "La Habana"},
}


def test_fila_actualizada_muestra_antes_y_despues_con_etiqueta() -> None:
    detail = {
        "rows": [
            {
                "price_history_id": "x",
                "op": "updated",
                "before": {"price": "100.00"},
                "after": {"price": "900.00"},
            }
        ]
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    (fila,) = resumen.rows
    assert fila.deleted is False
    (cambio,) = fila.changes
    assert (cambio.label, cambio.before, cambio.after) == ("Precio", "100.00", "900.00")
    assert resumen.source_reference is None


def test_fila_eliminada_lleva_todos_sus_valores_y_omite_los_nulos() -> None:
    detail = {
        "rows": [
            {
                "price_history_id": "x",
                "op": "deleted",
                "before": {
                    "product_id": PRODUCTO,
                    "province_id": None,
                    "price": "100.00",
                    "currency": "CUP",
                },
            }
        ]
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    (fila,) = resumen.rows
    assert fila.deleted is True
    valores = {c.label: c.after for c in fila.changes}
    assert valores == {"Producto": "Pollo", "Precio": "100.00", "Moneda": "CUP"}


def test_los_ids_se_muestran_como_nombre_del_catalogo() -> None:
    detail = {
        "rows": [
            {
                "op": "updated",
                "before": {"province_id": None, "product_id": PRODUCTO},
                "after": {"province_id": PROVINCIA, "product_id": "ya-no-existe"},
            }
        ]
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    cambios = {c.label: (c.before, c.after) for c in resumen.rows[0].changes}
    assert cambios["Provincia"] == ("—", "La Habana")
    assert cambios["Producto"] == ("Pollo", "(eliminado)")


def test_un_nulo_se_muestra_como_raya() -> None:
    detail = {
        "rows": [
            {"op": "updated", "before": {"unit": "lb"}, "after": {"unit": None}}
        ]
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    assert resumen.rows[0].changes[0].after == "—"


def test_cambio_de_source_reference() -> None:
    detail = {
        "rows": [],
        "source_reference": {"before": "grupo viejo", "after": "grupo nuevo"},
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    assert resumen.rows == []
    cambio = resumen.source_reference
    assert cambio is not None
    assert (cambio.label, cambio.before, cambio.after) == (
        "Canal origen", "grupo viejo", "grupo nuevo",
    )


def test_una_edicion_sin_cambios_no_es_un_cambio() -> None:
    assert describe_edit({"rows": []}, NOMBRES) is None


def test_un_detail_mal_formado_no_rompe() -> None:
    assert describe_edit({}, NOMBRES) is None
    assert describe_edit({"rows": [{"op": "raro"}]}, NOMBRES) is None


def test_referenced_catalog_ids_junta_los_ids_de_antes_y_despues() -> None:
    detail = {
        "rows": [
            {"op": "updated", "before": {"product_id": "a"}, "after": {"product_id": "b"}},
            {"op": "deleted", "before": {"format_id": "f", "variant_id": None, "price": "1"}},
        ]
    }

    ids = referenced_catalog_ids([detail])

    assert ids == {"product_id": {"a", "b"}, "format_id": {"f"}}


def test_decimal_no_se_usa_en_detail_pero_se_tolera() -> None:
    detail = {
        "rows": [
            {"op": "updated", "before": {"price": Decimal("1.50")}, "after": {"price": "2"}}
        ]
    }

    resumen = describe_edit(detail, NOMBRES)

    assert resumen is not None
    assert resumen.rows[0].changes[0].before == "1.50"
