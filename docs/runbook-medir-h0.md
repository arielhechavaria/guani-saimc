# Runbook — Medir H0

Procedimiento para correr la medición de H0 y dejar registro de la corrida.
Seguí estos pasos cada vez; no improvises variantes.

Qué mide y contra qué está en `spec.md` §3 (la hipótesis), §12.5 (las cinco
métricas) y §9 (el criterio de salida de la Fase 0). El motivo por el que H0 no
es un veredicto está en `CLAUDE.md`, sección «H0 — métrica de eficiencia».

---

## Antes de correr

1. **Corré en el servidor**, no en local. La medición re-corre el OCR sobre cada
   imagen curada del corpus real; en local la base no lo tiene.
2. **Árbol de trabajo limpio.** `git status` sin cambios sin commitear. Si está
   sucio la corrida se registra igual, pero marcada `git_dirty: true` y el
   número deja de ser atribuible a un commit — sirve para mirar, no para el
   histórico.
3. **Tesseract disponible.** `tesseract --version` responde. Si falta, la corrida
   igual sale pero `tesseract_version` queda `null` y las métricas de imagen se
   hunden sin que nada más lo delate.

---

## Mirar el número sin registrarlo

```
python -m saimc.extraction.measure --no-record
```

Imprime el reporte y **no toca** `docs/h0-runs.jsonl`. Usalo para chequear
dónde está la medición sin ensuciar el histórico — por ejemplo mientras iterás
sobre una regla del extractor.

En la salida, mirá en este orden:

- **Anuncios evaluados** — el tamaño del conjunto de referencia. Por debajo de
  300 la corrida **no es la medición oficial de H0** (`spec.md` §3). El propio
  reporte lo avisa al final.
- **Tasa de descarte** — objetivo 30% (`spec.md` §12.5). Hoy está muy por
  encima; es señal de ruido en la cola, no de un problema del extractor.
- **Extracción completa correcta** — el número de H0. Cualquiera que sea, es un
  resultado válido: gobierna cuánto trabajo manual cuesta cada anuncio, no si el
  proyecto avanza.
- **Sin texto del OCR** — si este número no es 0, hay imágenes cuyo OCR devolvió
  vacío y están contando como extracción fallida. Revisá que Tesseract esté bien.

---

## Registrar una corrida

```
python -m saimc.extraction.measure
```

Sin la flag, además de imprimir el reporte, appendea **una línea JSON** a
`docs/h0-runs.jsonl` con:

| Campo | Para qué |
| --- | --- |
| `timestamp` | Cuándo se corrió (UTC). |
| `git_commit`, `git_branch` | Qué versión del extractor produjo el número. |
| `git_dirty` | `true` si el árbol estaba sucio → número no atribuible. |
| `tesseract_version` | Qué OCR se usó; es el techo real de H0. |
| `is_official` | `true` solo con 300+ anuncios evaluados. |
| Las 15 métricas del reporte | Aplanadas, tal cual se imprimieron. |

**Tarda varios minutos.** Re-corre el OCR sobre todo el corpus curado de
imagen. Es normal, no lo interrumpas.

Al terminar imprime la ruta del archivo y el commit registrado. Si el árbol
estaba sucio, imprime además el aviso correspondiente.

---

## Después de registrar

El archivo `docs/h0-runs.jsonl` está en git a propósito: cada corrida queda al
lado del código que la produjo.

```
git add docs/h0-runs.jsonl
git commit -m "chore(h0): medición del <fecha>"
```

Commitealo cuando la corrida valga la pena guardar (una medición real, no un
tanteo). Para comparar el número entre versiones del extractor:

```
git log -p docs/h0-runs.jsonl
```

Cada diff muestra una corrida nueva y, al lado, en el mismo rango de commits,
qué cambió en el extractor entre una y otra.

---

## Qué NO hacer

- No midas H0 contra casos sintéticos ni contra el historial de correcciones del
  extractor. La medición oficial corre **solo** contra los 300+ anuncios reales
  etiquetados a mano, vía la cola de curaduría (`CLAUDE.md`, sección «Código»).
- No edites `docs/h0-runs.jsonl` a mano. Es un archivo de solo-anexado; lo
  escribe el comando.
- No trates el 85 / 70 como umbral de viabilidad. Son referencia de capacidad de
  escala (`CLAUDE.md`, decisión del 2026-08-12).
