"""Agregación por celda producto × provincia × moneda — casos sintéticos.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el agregador.

Esta vista es el instrumento con el que el operador verifica lo que carga. Por
eso reporta **mediciones, no veredictos**: `n`, mediana, mínimo, máximo y
amplitud. Ningún umbral — el whitepaper §12.6 (línea 1961) es explícito en que
«un umbral asignado por intuición y presentado como metodología es peor que no
tener umbral». N se calibra más adelante con datos reales, y esta misma vista
es la que va a permitir hacerlo.
"""

from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.cells import compute_cells
from saimc.models import Currency, PriceHistory, Product, Province, RawIngestion
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
) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            province_id=province.id if province else None,
            price=Decimal(price),
            currency=Currency.CUP.value,
            source_id=source.id,
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
