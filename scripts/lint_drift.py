"""Job diario de lint: arregla lo mecánico, nunca lo que requiere criterio.

Pensado para correr sin supervisión (cron, systemd timer, o un job
programado) — la razón de por qué existe y qué no hace está en
`docs/decisions.md`. Dos pasos, y solo dos:

1. `ruff check --fix` (el seguro, sin `--unsafe-fixes`): reordena imports,
   moderniza sintaxis, saca noqa muertos. Comportamiento idéntico, así que
   se puede aplicar y commitear sin que nadie lo revise antes.
2. Compara los hallazgos que SIGUEN ahí (los que ruff no puede arreglar solo,
   más todo mypy) contra `docs/lint-baseline.json` y reporta qué es nuevo.

Nunca "arregla" un `union-attr`, un `arg-type` ni nada que dependa de decidir
qué debería pasar con el dato — eso sigue pidiendo criterio humano
(docs/checklist-pr.md). Nunca hace `git push`: created commit queda local,
igual que cualquier otro slice de este repo.

Uso:
    python scripts/lint_drift.py                  # autofix + reporte de drift
    python scripts/lint_drift.py --no-autofix      # solo reporte, no toca nada
    python scripts/lint_drift.py --update-baseline # el operador ya revisó
                                                    # los hallazgos nuevos:
                                                    # los vuelve el piso nuevo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "docs" / "lint-baseline.json"

Signature = tuple[str, str, str]  # (archivo relativo, código, mensaje)


def _run(*args: str) -> str:
    """Corre un comando y devuelve su stdout. No revienta si el exit code no es 0:
    ruff y mypy devuelven 1 cuando encuentran hallazgos, que es el caso normal
    acá, no un fallo del comando."""
    completed = subprocess.run(  # noqa: S603 — sin shell; siempre se llama con listas literales de este mismo archivo
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def _relative(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path


def _ruff_autofix() -> None:
    subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--fix"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _ruff_findings() -> Counter[Signature]:
    salida = _run(sys.executable, "-m", "ruff", "check", ".", "--output-format=json")
    hallazgos = json.loads(salida) if salida.strip() else []
    return Counter(
        (_relative(h["filename"]), h["code"], h["message"])
        for h in hallazgos
        if h["code"] is not None
    )


def _mypy_findings() -> Counter[Signature]:
    salida = _run(sys.executable, "-m", "mypy", "--output=json", "--no-error-summary")
    contador: Counter[Signature] = Counter()
    for linea in salida.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        entrada = json.loads(linea)
        if entrada.get("severity") != "error":
            continue
        codigo = entrada.get("code") or "sin-codigo"
        contador[(_relative(entrada["file"]), codigo, entrada["message"])] += 1
    return contador


def _git_dirty() -> bool:
    salida = _run("git", "status", "--porcelain")
    return bool(salida.strip())


def _git_commit_autofix() -> bool:
    """Commitea SOLO si el fix mecánico dejó algo para commitear. Nunca hace
    push — eso es siempre del operador (CLAUDE.md)."""
    if not _git_dirty():
        return False
    subprocess.run(["git", "add", "-u"], cwd=REPO_ROOT, check=True)  # noqa: S607 — "git" del PATH
    mensaje = (
        "chore: ruff --fix automático (job diario de lint)\n\n"
        "Solo arreglos seguros (imports, sintaxis moderna, noqa muertos): "
        "sin cambio de comportamiento. scripts/lint_drift.py, sin supervisión."
    )
    subprocess.run(["git", "commit", "-m", mensaje], cwd=REPO_ROOT, check=True)  # noqa: S603, S607 — "git" del PATH, sin shell
    return True


def _cargar_baseline() -> dict[str, Counter[Signature]]:
    if not BASELINE_PATH.is_file():
        return {"ruff": Counter(), "mypy": Counter()}
    crudo = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return {
        herramienta: Counter({tuple(item["signature"]): item["count"] for item in items})
        for herramienta, items in crudo.items()
        if herramienta in ("ruff", "mypy")
    }


def _guardar_baseline(hallazgos: dict[str, Counter[Signature]]) -> None:
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        **{
            herramienta: [
                {"signature": list(firma), "count": cuenta}
                for firma, cuenta in sorted(contador.items())
            ]
            for herramienta, contador in hallazgos.items()
        },
    }
    BASELINE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reportar_drift(nombre: str, actual: Counter[Signature], base: Counter[Signature]) -> bool:
    nuevos = actual - base
    resueltos = base - actual
    print(f"\n=== {nombre} ===")
    print(f"Total actual: {sum(actual.values())} (piso: {sum(base.values())})")
    if nuevos:
        print(f"NUEVOS ({sum(nuevos.values())}) — necesitan revisión, no se tocaron solos:")
        for (archivo, codigo, mensaje), cuenta in sorted(nuevos.items()):
            print(f"  [{codigo}] {archivo} x{cuenta} — {mensaje}")
    if resueltos:
        print(f"Resueltos desde el piso ({sum(resueltos.values())}):")
        for (archivo, codigo, mensaje), cuenta in sorted(resueltos.items()):
            print(f"  [{codigo}] {archivo} x{cuenta} — {mensaje}")
    if not nuevos and not resueltos:
        print("Sin cambios contra el piso.")
    return bool(nuevos)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-autofix",
        action="store_true",
        help="Solo reporta drift, no corre ruff --fix ni commitea nada.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Escribe los hallazgos actuales como el nuevo piso y termina.",
    )
    args = parser.parse_args()

    if not args.no_autofix and not args.update_baseline:
        if _git_dirty():
            print(
                "Árbol de trabajo con cambios sin commitear: no corro --fix ni "
                "commiteo nada (CLAUDE.md — no se mezcla con trabajo en curso). "
                "Sigo solo con el reporte de drift, sobre lo que hay ahora."
            )
        else:
            _ruff_autofix()
            if _git_commit_autofix():
                print("Arreglos mecánicos commiteados (sin push).")
            else:
                print("Nada que --fix pudiera arreglar solo.")

    hallazgos = {"ruff": _ruff_findings(), "mypy": _mypy_findings()}

    if args.update_baseline:
        _guardar_baseline(hallazgos)
        print(f"Piso actualizado en {BASELINE_PATH.relative_to(REPO_ROOT)}.")
        return 0

    piso = _cargar_baseline()
    hay_nuevos = False
    for herramienta in ("ruff", "mypy"):
        hay_nuevos |= _reportar_drift(herramienta, hallazgos[herramienta], piso[herramienta])

    return 1 if hay_nuevos else 0


if __name__ == "__main__":
    raise SystemExit(main())
