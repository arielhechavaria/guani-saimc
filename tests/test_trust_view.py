"""Slice B4 (`docs/spec-slice-B4.md`): por celda, de qué está hecho el n.

Los esperados están escritos a mano, antes de `trust_of`: cuántas filas de cada
procedencia y de cada estado hay en la celda sintética, y la frase que sale.

HOY = domingo 30/08/2026; ventana de 30 días (31/07–30/08).

Arroz, La Habana, en la ventana (12 filas): 9 `human`, 2 `auto`, 1 `audited`;
estados: 8 `approved`, 3 `anomaly`, 1 nulo.
Arroz, Santiago de Cuba (5 filas): todas `human`, todas `approved`.
Pollo, La Habana (1 fila): `auto`, `approved`.
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.explore_view import build_explore_products
from saimc.web.price_charts import build_month_vs_month
from saimc.web.trust_view import trust_of
from saimc.web.watchlist_view import build_watchlist

HOY = date(2026, 8, 30)
DESDE = date(2026, 7, 31)
PROVINCIAS = ["La Habana", "Santiago de Cuba"]


def _obs(
    product: str,
    province: str,
    dia: int,
    provenance: str | None,
    status: str | None,
    precio: str = "300",
) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=date(2026, 8, dia),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=True,
        provenance=provenance,
        validation_status=status,
    )


def _habana_arroz() -> list[RetailObservation]:
    procedencias = ["human"] * 9 + ["auto"] * 2 + ["audited"]
    estados = ["approved"] * 8 + ["anomaly"] * 3 + [None]
    return [
        _obs("Arroz", "La Habana", 10 + i, p, s)
        for i, (p, s) in enumerate(zip(procedencias, estados, strict=True))
    ]


OBS = [
    *_habana_arroz(),
    *[_obs("Arroz", "Santiago de Cuba", 20 + i, "human", "approved") for i in range(5)],
    _obs("Pollo", "La Habana", 29, "auto", "approved", "700"),
]


# --- trust_of ------------------------------------------------------------------


def test_cuenta_procedencia_y_estado_de_una_celda() -> None:
    trust = trust_of(_habana_arroz())

    assert trust is not None
    assert (trust.n, trust.human, trust.auto, trust.audited) == (12, 9, 2, 1)
    assert (trust.approved, trust.flagged, trust.unevaluated) == (8, 3, 1)
    assert trust.by_system == 3
    assert trust.line == "12 anuncios · 9 a mano · 3 por el sistema"


def test_todos_a_mano_no_dice_cero_por_el_sistema() -> None:
    trust = trust_of([_obs("Arroz", "La Habana", 10 + i, "human", "approved") for i in range(5)])

    assert trust is not None
    assert trust.line == "5 anuncios · todos a mano"
    assert "0 por el sistema" not in trust.line


def test_un_solo_anuncio_del_sistema_va_en_singular_y_sin_porcentaje() -> None:
    trust = trust_of([_obs("Pollo", "La Habana", 29, "auto", "approved")])

    assert trust is not None
    assert trust.line == "1 anuncio · 1 por el sistema"
    assert "%" not in trust.line


def test_todos_del_sistema() -> None:
    trust = trust_of([_obs("Arroz", "La Habana", 10 + i, "auto", None) for i in range(2)])

    assert trust is not None
    assert trust.line == "2 anuncios · 2 por el sistema"


def test_las_sumas_siempre_cierran_en_n() -> None:
    trust = trust_of(_habana_arroz())

    assert trust is not None
    assert trust.human + trust.auto + trust.audited == trust.n
    assert trust.approved + trust.flagged + trust.unevaluated == trust.n


def test_sin_filas_no_hay_bloque() -> None:
    assert trust_of([]) is None


def test_procedencia_desconocida_no_se_inventa() -> None:
    """Una fila sin procedencia (solo existe en datos sintéticos: la columna es
    NOT NULL) no se cuenta como a mano: no hay bloque."""
    assert trust_of([_obs("Arroz", "La Habana", 10, None, None)]) is None


# --- las dos vistas llaman a la misma función ----------------------------------


def test_explorar_trae_el_desglose_en_el_nodo_de_la_provincia() -> None:
    [arroz] = build_explore_products(
        ["Arroz"], OBS, window_days=30, hoy=HOY, provinces=PROVINCIAS
    )
    habana = next(n for n in arroz.nodes if n.name == "La Habana")
    santiago = next(n for n in arroz.nodes if n.name == "Santiago de Cuba")

    assert habana.trust is not None and habana.trust.line == "12 anuncios · 9 a mano · 3 por el sistema"
    assert habana.trust.n == habana.count
    assert santiago.trust is not None and santiago.trust.line == "5 anuncios · todos a mano"
    # Los formatos hijos no llevan el dato (spec, pregunta 3).
    assert all(h.trust is None for n in arroz.nodes for h in n.children)


def test_el_tablero_trae_el_desglose_en_la_celda_y_coincide_con_su_n() -> None:
    slopes = build_month_vs_month(OBS, [], window_days=30, hoy=HOY)
    lista = build_watchlist(
        ["Arroz", "Pollo"],
        [],
        slopes,
        provinces=PROVINCIAS,
        selected_province="",
        desde=DESDE,
        hasta=HOY,
        weekly_obs=OBS,
    )
    arroz = next(r for r in lista.rows if r.product_name == "Arroz")
    habana, santiago = arroz.cells

    assert habana is not None and habana.trust is not None
    assert habana.trust.line == "12 anuncios · 9 a mano · 3 por el sistema"
    assert habana.trust.n == habana.count
    assert santiago is not None and santiago.trust is not None
    assert santiago.trust.n == santiago.count == 5
