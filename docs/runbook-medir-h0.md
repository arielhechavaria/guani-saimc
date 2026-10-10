# Runbook — Medir H0

Procedimiento para correr la medición de H0 y dejar registro de la corrida.
Seguí estos pasos cada vez; no improvises variantes.

H0 mide una sola cosa: **qué tan bien lee el extractor por reglas**, contra un
conjunto de referencia congelado. Las etiquetas del conjunto no cambian entre
una corrida y otra, así que el número se mueve solo cuando se mueve el
extractor (o lo que lo rodea: el catálogo del foco, la provincia fija de cada
canal, el OCR).

Qué mide y contra qué está en `spec.md` §3 (la hipótesis), §12.5 (las cinco
métricas) y §9 (el criterio de salida de la Fase 0), con los apartamientos de
`docs/spec-map.md`. El diseño del conjunto congelado está en
`docs/spec-slice-B3.md`. El motivo por el que H0 no es un veredicto está en
`CLAUDE.md`, sección «H0 — métrica de eficiencia».

---

## Congelar el conjunto

Se hace **una vez**. Sin un conjunto congelado la medición no corre.

1. En seco, para ver qué entraría:

   ```
   python -m saimc.extraction.reference_set --exclude ab9bb650 --exclude 0944e4dd --exclude 8442a0dd --exclude e08f6cc2
   ```

   Los cuatro excluidos son los anuncios con las 7 filas insertadas el
   2026-10-05: su etiqueta ya no es independiente del modelo.
2. Leé los conteos: cuántos anuncios y filas entran, por tipo, y cuántos
   curados quedan afuera por cada motivo (asistidos, sin filas, excluidos a
   mano). Si algo no cierra, pará acá: en seco no se escribió nada.
3. El mismo comando con `--execute`. Imprime la versión y el hash del conjunto.
4. Anotá en `docs/decisions.md` la versión, el n por tipo, el hash y los
   excluidos con su motivo.

Entrar es una regla, no una elección anuncio por anuncio: curado, con al menos
una fila, sin asistencia del modelo y fuera de la lista de excluidos. El
detalle de cada modo está en `docs/comandos.md`.

---

## Antes de correr

1. **Tiene que existir un conjunto congelado.** Se ve con
   `python -m saimc.extraction.reference_set status`. Si no hay ninguno, la
   medición termina con error, nombra el comando de congelado y no escribe
   nada.
2. **Corré en el servidor**, no en local. El conjunto vive en la base de
   producción y la medición re-corre el OCR sobre cada imagen que tiene
   adentro; en local la base no lo tiene.
3. **Árbol de trabajo limpio.** `git status` sin cambios sin commitear. Si está
   sucio la corrida se registra igual, pero marcada `git_dirty: true` y el
   número deja de ser atribuible a un commit — sirve para mirar, no para el
   histórico.
4. **RapidOCR instalado (`pip install -e ".[ocr]"`), Tesseract como respaldo.**
   Desde el 2026-08-31 el motor primario de imagen es RapidOCR
   (`saimc.extraction.ocr`); Tesseract solo entra si RapidOCR no está instalado
   o no encuentra texto en una imagen puntual. `tesseract --version` sigue
   siendo útil para ese caso, pero su ausencia ya no hunde las métricas de
   imagen como antes — cae a RapidOCR, no al revés.

---

## Mirar el número sin registrarlo

```
python -m saimc.extraction.measure --no-record
```

Imprime el reporte y **no toca** `docs/h0-runs.jsonl`. Usalo para chequear
dónde está la medición sin ensuciar el histórico — por ejemplo mientras iterás
sobre una regla del extractor.

En la salida, mirá en este orden:

> **Pendiente (B3-3).** La especificación manda mirar primero la métrica de la
> Fase 0 —cuánto se resuelve y cuánto se publica sin el operador, cada número
> con su error auditado— y recién después H0. Esa sección todavía no sale en el
> reporte: llega con el slice B3-3. Hasta entonces el reporte es solo H0.

- **Versión y hash del conjunto** — contra qué se midió. Dos corridas solo se
  comparan si coinciden.
- **Miembros con deriva** — anuncios del conjunto cuyas filas publicadas ya no
  son las que se congelaron: alguien editó un precio, borró una fila o eliminó
  el anuncio después del congelado. **No cambia el número**: H0 sigue midiendo
  contra las filas congeladas, y el miembro sigue adentro. Es un aviso de que
  lo publicado y la referencia se separaron en esos anuncios. Cuáles son lo
  dice `python -m saimc.extraction.reference_set status`. **No se arregla
  congelando de nuevo**: eso mueve la referencia y rompe la comparación con
  todo lo anterior (ver «Versión nueva del conjunto»).
- **Corpus** — curados, descartados y la **tasa de descarte** (objetivo 30%,
  `spec.md` §12.5). Estos números salen de la base de hoy, no del conjunto:
  describen lo que entró. Una tasa alta es señal de ruido en la cola, no de un
  problema del extractor.
- **Anuncios evaluados** — el tamaño del conjunto de referencia. Por debajo de
  300 la corrida **no es la medición oficial de H0** (`spec.md` §3) y el propio
  reporte lo avisa al final. El conjunto congelado no llega a 300 y ya no
  crece: todo lo que se cura hoy se cura con la propuesta del modelo a la
  vista. El número se informa con su n; el aviso va a salir en todas las
  corridas, y es correcto que salga.
- **Sin texto del OCR** — si este número no es 0, hay imágenes del conjunto
  cuyo OCR devolvió vacío (ni RapidOCR ni el respaldo de Tesseract encontraron
  nada) y están contando como extracción fallida.
- **Extracción completa correcta** — el número de H0. Cualquiera que sea, es un
  resultado válido: gobierna cuánto trabajo manual cuesta cada anuncio, no si el
  proyecto avanza.

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
| `tesseract_version` | Versión del respaldo disponible en la máquina, no necesariamente el motor que transcribió — desde el 2026-08-31 el primario es RapidOCR y este campo no lo distingue. |
| `reference_set_version` | Versión del conjunto contra el que se midió. |
| `reference_set_hash` | Huella del conjunto: la misma que imprime `reference_set status`. |
| `reference_set_drifted` | Miembros con deriva el día de la corrida. |
| `assisted_ads_count`, `assisted_rows_count` | Siempre 0 desde que se mide contra el conjunto congelado: quién queda afuera por asistencia ya lo decidió el congelado. Se siguen escribiendo para no romper a quien lee el archivo. |
| `is_official` | `true` solo con 300+ anuncios evaluados. |
| Las métricas del reporte | Aplanadas, tal cual se imprimieron. |

**Cómo se lee una línea vieja.** Una línea sin `reference_set_version` es una
corrida anterior al conjunto congelado: midió contra el oro vivo de su día,
que se rearmaba en cada corrida. Las diez primeras líneas del archivo son así
y no se tocan. **No son comparables con las nuevas**: la diferencia entre la
última vieja y la primera nueva no es atribuible al extractor. `/control`
muestra el conjunto de cada corrida debajo de la fecha («oro vivo» o
«conjunto vN») y solo pone la diferencia en puntos entre dos corridas del
mismo conjunto.

**Tarda varios minutos.** Re-corre el OCR sobre todas las imágenes del
conjunto. Es normal, no lo interrumpas.

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

## Versión nueva del conjunto

Congelar de nuevo escribe la versión siguiente con los valores vivos de ese
día. La versión anterior queda intacta en la tabla, pero la medición usa
siempre la mayor.

Corresponde en dos casos, y solo en esos:

- **Un error de etiquetado confirmado.** Una etiqueta congelada estaba mal, se
  corrigió desde la edición, y la corrección tiene que entrar a la referencia.
  Hasta la versión nueva, H0 sigue contando la etiqueta vieja como verdad.
- **Anuncios nuevos etiquetados sin asistencia.** Hoy no los hay: no existe un
  modo de la cola que cure sin mostrar la propuesta del modelo.

Lo que cuesta: **rompe la comparación con todas las corridas anteriores.** Una
corrida contra la versión 2 no se compara con una contra la versión 1; el
historial de `/control` deja de mostrar la diferencia entre ellas. Por eso es
un acto explícito, con su entrada en `docs/decisions.md`: qué anuncios
cambiaron y por qué, la versión, el n por tipo, el hash y los excluidos.

El procedimiento es el de «Congelar el conjunto».

---

## Qué NO hacer

- No midas H0 contra casos sintéticos ni contra el historial de correcciones del
  extractor. La medición corre **solo** contra el conjunto de referencia
  congelado: anuncios reales etiquetados a mano, sin asistencia.
- **No congeles una versión nueva para mover un número.** Ni para «limpiar» los
  miembros con deriva, ni porque el número bajó. Una versión nueva se justifica
  por las etiquetas, nunca por el resultado.
- No edites `docs/h0-runs.jsonl` a mano. Es un archivo de solo-anexado; lo
  escribe el comando.
- No toques la tabla `reference_set_members` a mano. Es de solo altas: no se
  modifica ni se borra una fila.
- No trates el 85 / 70 como umbral de viabilidad. Son referencia de capacidad de
  escala (`CLAUDE.md`, decisión del 2026-08-12).
