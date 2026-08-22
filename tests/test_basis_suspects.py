"""Detección de `price_basis='unit'` que la propia celda desmiente.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el detector.

Lo que se prueba no es «esta fila está mal» —eso lo decide el operador— sino la
regla: entre las dos lecturas posibles del mismo número, se señala la fila
cuando leerla como **total** la acerca al resto de su celda más que leerla como
**unitaria**. Sin cortes de precio y sin factor mínimo de sospecha: §12.6.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.cells import detect_basis_suspects
from saimc.models import Currency, Product, Province, SaleFormat
from tests.test_cells import _ingestion, _precio

RETAIL = SaleFormat.RETAIL.value


def _arroz(session: Session) -> tuple[Product, Province]:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
    habana = Province(code="hab", name="La Habana")
    session.add_all([arroz, habana])
    session.commit()
    return arroz, habana


class TestDetectBasisSuspects:
    def test_señala_la_fila_cuyo_total_encaja_con_su_celda(
        self, session: Session
    ) -> None:
        """Caso real reducido: el saco de arroz a 30.885 por 100 lb.

        Referencia de la celda: tres filas de 1 lb a 300, 310 y 320 -> mediana
        310. Leída como unitaria la fila declara 30.885/lb, que se aparta ~99
        veces. Leída como total da 308,85/lb, que cae casi encima de la
        mediana. Gana la lectura de total, así que la fila se señala.
        """
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-saco")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="30885", source=origen,
            unit="lb", quantity="100", sale_format=RETAIL, price_basis="unit",
        )
        session.commit()

        sospechosas = detect_basis_suspects(session)

        assert len(sospechosas) == 1
        caso = sospechosas[0]
        assert caso.product_name == "Arroz"
        assert caso.as_unit == Decimal("30885")
        assert caso.as_total == Decimal("308.85")
        assert caso.reference == Decimal("310")

    def test_no_señala_la_fila_cuyo_valor_declarado_encaja(
        self, session: Session
    ) -> None:
        """El caso Picadillo con referencia: `quantity != 1` NO es de por sí un
        error. Si el precio por libra declarado concuerda con la celda, la fila
        está bien marcada y no se toca."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-lote")
        for precio in ("500", "510", "520"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        # 525 por libra, disponible en lotes de 40 lb. Como total daría 13,1/lb,
        # que se aparta 39 veces de la celda: gana la lectura declarada.
        _precio(
            session, product=arroz, province=habana, price="525", source=origen,
            unit="lb", quantity="40", sale_format=RETAIL, price_basis="unit",
        )
        session.commit()

        assert detect_basis_suspects(session) == []

    def test_sin_filas_no_ambiguas_no_hay_veredicto(self, session: Session) -> None:
        """El caso Picadillo real: todas las filas de la celda son `'unit'` con
        `quantity != 1`, así que no hay contra qué contrastarlas. Inventarles un
        juicio sería peor que no darlo."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-sin-ref")
        for precio, cantidad in (("525", "40"), ("515", "50"), ("460", "40")):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity=cantidad, sale_format=RETAIL, price_basis="unit",
            )
        session.commit()

        assert detect_basis_suspects(session) == []

    def test_una_fila_total_tambien_sirve_de_referencia(self, session: Session) -> None:
        """No solo `quantity == 1`: una fila que el operador ya resolvió como
        `'total'` tampoco puede estar mal marcada de esta forma."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-total")
        # 3.000 por 10 lb declarado total -> 300/lb, la referencia.
        _precio(
            session, product=arroz, province=habana, price="3000", source=origen,
            unit="lb", quantity="10", sale_format=RETAIL, price_basis="total",
        )
        _precio(
            session, product=arroz, province=habana, price="30000", source=origen,
            unit="lb", quantity="100", sale_format=RETAIL, price_basis="unit",
        )
        session.commit()

        sospechosas = detect_basis_suspects(session)

        assert len(sospechosas) == 1
        assert sospechosas[0].reference == Decimal("300")

    def test_basis_nulo_es_indeterminado_no_sospechoso(self, session: Session) -> None:
        """`compute_retail_ranges` ya lo excluye y lo cuenta en
        `excluded_by_basis`. Señalarlo acá lo contaría dos veces y además
        afirmaría algo que la fila nunca dijo."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-nulo")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="30885", source=origen,
            unit="lb", quantity="100", sale_format=RETAIL, price_basis=None,
        )
        session.commit()

        assert detect_basis_suspects(session) == []

    def test_una_fila_sin_conversion_resuelta_no_entra(self, session: Session) -> None:
        """Sin tasa no es comparable con las de su celda, y mezclarla inventaría
        la equivalencia que `unconverted` existe para evitar."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-sin-tasa")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="900", source=origen,
            unit="lb", quantity="100", sale_format=RETAIL, price_basis="unit",
            currency=Currency.USD.value, price_cup=None,
        )
        session.commit()

        assert detect_basis_suspects(session) == []

    def test_compara_en_la_unidad_canonica_del_producto(self, session: Session) -> None:
        """El caso Arroz/Santiago: la fila viene en kg y el catálogo dice lb.

        17.500 por 25 kg. Como unitaria son 17.500/kg = 7.937,87/lb; como total
        son 700/kg = 317,51/lb. La celda ronda las 320/lb, así que gana total.
        Sin la conversión, los dos números serían incomparables con la celda.
        """
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-kg")
        for precio in ("310", "320", "330"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        _precio(
            session, product=arroz, province=habana, price="17500", source=origen,
            unit="kg", quantity="25", sale_format=RETAIL, price_basis="unit",
        )
        session.commit()

        sospechosas = detect_basis_suspects(session)

        assert len(sospechosas) == 1
        assert sospechosas[0].unit == "lb"
        assert round(sospechosas[0].as_total, 2) == Decimal("317.51")

    def test_ordena_por_magnitud_del_error(self, session: Session) -> None:
        """El operador ataca primero la que más envenena su celda."""
        arroz, habana = _arroz(session)
        origen = _ingestion(session, "hash-orden")
        for precio in ("300", "310", "320"):
            _precio(
                session, product=arroz, province=habana, price=precio, source=origen,
                unit="lb", quantity="1", sale_format=RETAIL,
            )
        # 3.100 por 10 lb -> se aparta 10 veces. 31.000 por 100 lb -> 100 veces.
        _precio(
            session, product=arroz, province=habana, price="3100", source=origen,
            unit="lb", quantity="10", sale_format=RETAIL, price_basis="unit",
        )
        _precio(
            session, product=arroz, province=habana, price="31000", source=origen,
            unit="lb", quantity="100", sale_format=RETAIL, price_basis="unit",
        )
        session.commit()

        sospechosas = detect_basis_suspects(session)

        assert [s.as_unit for s in sospechosas] == [Decimal("31000"), Decimal("3100")]
