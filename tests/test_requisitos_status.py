"""Estado de los 16 módulos (`docs/requisitos.md`) — casos sintéticos y
contraste con el documento real.

Mismo criterio que `TestReadH0Runs` en `test_h0_measurement.py`: los casos
sintéticos fijan la respuesta correcta a mano, antes e independientemente de
correr el parser, y viven en el propio test —no se derivan de una corrida—.
El único test que sí mira el documento real fija el número que importa para
la vista «Hoy» de `/control`: cuántos módulos son accionables hoy.
"""

from pathlib import Path

from saimc.requisitos_status import read_module_status


class TestReadModuleStatusArchivoReal:
    def test_exactamente_dos_modulos_accionables_hoy(self) -> None:
        """Contra `docs/requisitos.md` tal como está en el repo: los únicos
        módulos ni terminados ni bloqueados son 05 (Validación) y 07
        (Antimanipulación C1–C4). Si la tabla del documento cambia, este
        número cambia con ella — no está fijado en el código."""
        modules = read_module_status()

        assert len(modules) == 16
        accionables = [(m.number, m.name) for m in modules if m.is_actionable]
        assert accionables == [
            ("05", "Validación"),
            ("07", "Antimanipulación C1–C4"),
        ]


def _doc(*rows: str) -> str:
    """Un documento sintético con la tabla bajo el título que el parser busca."""
    tabla = "\n".join(rows)
    return (
        "## Estado de los 16 módulos\n"
        "\n"
        "| # | Módulo | Estado | Bloqueado por |\n"
        "| --- | --- | --- | --- |\n"
        f"{tabla}\n"
    )


class TestReadModuleStatusSintetico:
    def test_fila_con_bloqueo_no_es_accionable(self, tmp_path: Path) -> None:
        path = tmp_path / "requisitos.md"
        path.write_text(
            _doc(
                "| 04 | Normalización y comparabilidad | Parcial |"
                " `price_basis` histórico |"
            ),
            encoding="utf-8",
        )

        modules = read_module_status(path)

        assert len(modules) == 1
        assert modules[0].blocked_by == "`price_basis` histórico"
        assert modules[0].is_actionable is False

    def test_fila_construido_sin_bloqueo_no_es_accionable(self, tmp_path: Path) -> None:
        path = tmp_path / "requisitos.md"
        path.write_text(
            _doc("| 01 | Captura e ingesta | Construido | — |"),
            encoding="utf-8",
        )

        modules = read_module_status(path)

        assert modules[0].status == "Construido"
        assert modules[0].is_actionable is False

    def test_fila_ausente_con_bloqueo_no_es_accionable(self, tmp_path: Path) -> None:
        path = tmp_path / "requisitos.md"
        path.write_text(
            _doc(
                "| 06 | Reputación de fuentes | **Ausente** |"
                " Falta el modelo (H-23) |"
            ),
            encoding="utf-8",
        )

        modules = read_module_status(path)

        # El ** de negrita markdown no llega al valor: se limpia acá, no en
        # la plantilla.
        assert modules[0].status == "Ausente"
        assert modules[0].is_actionable is False

    def test_fila_ausente_sin_bloqueo_es_accionable(self, tmp_path: Path) -> None:
        path = tmp_path / "requisitos.md"
        path.write_text(
            _doc("| 05 | Validación | **Ausente** | — |"),
            encoding="utf-8",
        )

        modules = read_module_status(path)

        assert modules[0].status == "Ausente"
        assert modules[0].blocked_by == "—"
        assert modules[0].is_actionable is True

    def test_archivo_ausente_devuelve_lista_vacia(self, tmp_path: Path) -> None:
        assert read_module_status(tmp_path / "no-existe.md") == []

    def test_seccion_ausente_devuelve_lista_vacia(self, tmp_path: Path) -> None:
        path = tmp_path / "requisitos.md"
        path.write_text(
            "## Otra sección\n"
            "\n"
            "| # | Módulo | Estado | Bloqueado por |\n"
            "| --- | --- | --- | --- |\n"
            "| 01 | Captura e ingesta | Construido | — |\n",
            encoding="utf-8",
        )

        assert read_module_status(path) == []
