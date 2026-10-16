# Slice definir-validada — Qué es una fila validada y cómo llega a `approved`

Parte de `docs/plan-bloque-mvp.md` (T0). Toca la entrada del motor de validación
(`validation/context.py`), así que esta especificación va antes del código. Se
apoya en `docs/spec-motor-validacion.md`, `docs/spec-slice-B1.md` y en
`spec.md` §6.2 («Resultado de validación»: el único estado literal es
`approved`).

## Problema

El SELECT del 2026-10-16 sobre `prices_history` dio **0 de 1107 filas en
`approved`** (461 `human`, 344 `auto`, 302 `audited`). Dos causas, las dos de
plomería, ninguna del criterio:

1. `persist.validate_rows` llamaba a `validate_stored_row` sin
   `channel_min_history` ni `channel_max_edit_rate`. Los valores (3 y 0,5) ya
   los había fijado el operador en G1b, pero solo vivían en `lane1.CONFIG`.
2. `context.build_stored_rows_context` arma el canal con `decisions = 0`,
   `proposed_rows = 0`, `changed_rows = 0`. B1 lo dejó así a propósito («no se
   calcula mientras la regla 4 no tenga parámetros»); con los parámetros ya
   fijados, esa excepción caducó.

Sin las dos, la regla 4 da `unknown` en toda fila guardada y `_status` nunca
llega a `approved`. En 248 de las 327 filas humanas `unverified`, la regla 4 es
la única que no pasa.

## Definición

**Fila validada = `validation_status = 'approved'`**: las seis reglas en `pass`
y la fila verificada por una persona. No hay una segunda definición: el tablero,
`/datos.csv` y cualquier reporte que diga «validado» cuenta `approved`.

- `verified` sigue siendo `provenance = human`. **No cambia.** Las filas `auto`
  y `audited` no llegan a `approved` (§6.1: «el sistema propone; el operador
  confirma»; §10.7: un humano por registro). Si una `audited` sin cambios debe
  contar es una decisión aparte, con su propia especificación.
- Las reglas, el orden de los estados y el puntaje no cambian. `ENGINE_VERSION`
  no cambia.

## Qué se construye

1. **Parámetros de la regla 4 al guardar.** `persist.CHANNEL_MIN_HISTORY = 3` y
   `persist.CHANNEL_MAX_EDIT_RATE = 0.5`; `persist.validate_rows` los pasa y
   `lane1.CONFIG` los lee de ahí (una sola fuente). `validation_detail.config`
   guarda los tres parámetros con que se validó.
2. **Historial del canal para una fila guardada.** `build_stored_rows_context`
   usa `channel_history(session, channel, exclude_ingestion_id=ingestion.id)`:
   **todo lo decidido hasta hoy en decisiones individuales, sin contar el propio
   anuncio** (elegido por el operador el 2026-10-16). Misma función y misma
   definición de «decisión individual» que el carril 1; no se escribe una
   segunda.
3. **Efecto en lo público.** `trust_view.py` no cambia: ya cuenta `approved`. La
   frase «N pasaron las seis reglas» de `_confianza.html` empieza a aparecer.
   Ningún porcentaje ni umbral se publica (§12.6).
4. **`revalidate --execute`** reclasifica las filas existentes (paso del
   operador en el VPS).

## Consecuencias que hay que saber

- El resultado depende de la base, no del orden: para la misma base y la misma
  versión del motor, el estado no cambia con el orden de recorrido. Sí cambia
  cuando entran decisiones nuevas del canal; `revalidate` lo vuelve a calcular.
- Una fila ya `approved` puede dejar de serlo si el canal se ensucia después.
  Es lo deseado: la regla 4 describe al canal de hoy.
- Costo: una consulta por anuncio al guardar o revalidar (0,11 s en el canal más
  grande del 2026-10-16; ~1 min el `revalidate` completo).

## Casos esperados (se escriben antes que el código)

Guardado, con `min_cell_n = 5`, `channel_min_history = 3`,
`channel_max_edit_rate = 0,5`, celda de 5 referencias y precio dentro de la banda:

1. Fila humana de un canal con 3 decisiones previas sin tocar: las seis reglas
   en `pass`, estado `approved`.
2. Canal con 3 decisiones previas, 2 con el precio tocado (2 > 0,5 × 3): regla 4
   en `fail`, estado `untrusted_source`.
3. Canal con 2 decisiones previas: regla 4 en `unknown`, estado `unverified`.
4. El propio anuncio no cuenta: canal con 2 decisiones previas y la del anuncio
   bajo prueba ya escrita: sigue `unknown` (2 < 3).
5. Fila `auto` de un canal limpio: nunca `approved`; `unverified` con el motivo
   «la fila no está verificada».
6. Anuncio sin canal: regla 4 en `unknown`, `unverified` (no cambia).
7. `validation_detail.config` trae `min_cell_n`, `channel_min_history` y
   `channel_max_edit_rate`.
8. `revalidate --execute` sobre un canal limpio pasa sus filas humanas a
   `approved` y no toca la procedencia; una segunda corrida no cambia nada, y el
   orden de recorrido no cambia ningún estado.
9. `lane1.CONFIG` y `persist` usan los mismos valores.

## Qué no entra

- Contar `audited` como verificada (queda propuesto, sin aprobar).
- Cambiar reglas, estados, puntaje o `ENGINE_VERSION`.
- Cambiar `trust_view.py` ni las plantillas.
- Caché del historial del canal.
