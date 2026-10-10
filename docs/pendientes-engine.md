# Pendientes de `extraction/engine.py`

Trabajo por hacer que salió de la revisión profunda de `src/saimc/extraction/engine.py`
del 2026-09-28. Ya se arreglaron los bugs 1, 7 y 9 (commit `d0d0ce9`) y los 2 y 3
(commit `1e3894e`); el motivo de cada arreglo está en `docs/decisions.md`, entradas
del 2026-09-28.

Este archivo es una excepción a `docs/backlog.md` (congelado, el backlog general vive
en el GitHub Project): el operador eligió tener estos pendientes en el repo. Cuando
se arregla uno, se borra de acá y el porqué va a `decisions.md`.

Las funciones se citan por nombre y no por línea, porque las líneas se corren.

**Regla para todos:** esta es la capa de extracción. Cada arreglo arranca con el
test y la respuesta correcta definida a mano antes de tocar el código (CLAUDE.md).
Los casos de «Reproducción» sirven de base.

## Criticidad

- **Alta:** corrompe o pierde dato sin avisar al operador.
- **Media:** tumba una vista, deja un dato inconsistente que el operador puede no
  ver, o le suma trabajo manual en un campo sensible.
- **Baja:** rendimiento, legibilidad o formatos poco frecuentes.

## Resumen

| Prioridad | Pendiente | Criticidad |
|---|---|---|
| 2 | Mínimo de lote sin unidad | Media-alta |
| 2 | Línea «Más de N» leída como producto en un catálogo | Media |
| 3 | `u` no es unidad reconocida en el lote | Baja |
| 2 | División por cero con cantidad 0 | Media |
| 3 | Alias vacíos o nulos del lado del LLM | Media |
| 3 | Unidad sin canonizar en `_declared_price_basis` | Media |
| 4 | Recompilación de regex por línea | Baja |
| 5 | Menores sin reproducir | Baja |
| 5 | Validar alias al guardar en `/catalog` | Baja |
| — | ¿Proponer líneas con número suelto y sin producto? | Decisión |

---

## P2 · Mínimo de lote sin unidad (bug 5) — Media-alta

**Dónde:** `_tier_minimum`.

**Qué pasa:** si el anuncio no declara la unidad del lote (`_lot_size` devuelve
`(None, None)`), la función devuelve el número pelado. La fila sale con
`min_purchase_quantity` lleno pero `unit` y `quantity` en `None`. Eso rompe la
invariante de `ExtractionProposal.min_purchase_quantity`: el mínimo va siempre en la
misma unidad que `unit`.

**Reproducción:**

```
Pollo
A 550
Más de 500 lb 480
```

Da una fila con `min_purchase_quantity=500` y `unit=None`.

**Riesgo:** si el operador no lo ve, se guarda un mínimo de compra sin unidad.

## P2 · Línea «Más de N» leída como producto en un catálogo — Media

**Dónde:** lectura línea por línea de `propose_all` (hallazgo al cerrar P1).

**Qué pasa:** cuando un catálogo de tienda trae una línea de escalón y el anuncio no
es un bloque, esa línea sale como una fila más con nombre «Mas de» y el precio
corrido. El operador la ve y la descarta, pero es una fila basura por anuncio.

**Reproducción:** `"Tienda Los Primos\nDetergente Ariel 1kg 350\nJabon Marsella 2kg 500\nMas de 10 cajas 28"`
da, además de las dos filas correctas, una con `product_name_guess="Mas de"`,
precio 28 y cantidad 10 cajas.

## P3 · `u` no se reconoce como unidad del lote — Baja

**Dónde:** `_lot_size` / `_QUANTITY_RE`.

**Qué pasa:** «Jabon Marsella 12u 500» y «CAJA DE 12 u» no dan cantidad ni unidad;
la línea del jabón no se propone y el bloque pierde el mínimo del escalón. Con «lb»
o «kg» anda. Puede ser intencional (unidad no canónica): hay que decidirlo.

## P2 · División por cero con cantidad 0 (bug 8) — Media

**Dónde:** `_declared_price_basis` (y la misma guarda en `_packaging_price_basis`).

**Qué pasa:** divide el precio por la cantidad sin chequear que no sea cero. La guarda
actual solo excluye `None` y `1`.

**Reproducción:** `"Cafe 0 kg a 3500 CUP. Sale 3500 kg"` lanza `decimal.DivisionByZero`
y tumba la vista de curaduría de ese anuncio.

**Por qué prioridad 2 aunque sea improbable:** el arreglo es excluir cantidades `<= 0`
en las dos guardas, una línea cada una.

## P3 · Alias vacíos o nulos del lado del LLM — Media

**Dónde:** `extraction/llm_propose_ad.py` (`parse_response`, el matcheo con
`lambda p: (p.name, *p.aliases)`) y `extraction/llm_assist.py` (mismo patrón).

**Qué pasa:** es el mismo riesgo del bug 9, que en el motor de reglas ya se arregló
(`_match_product` saltea alias vacíos, en blanco o nulos). Estos dos módulos recorren
`p.aliases` para matchear la respuesta del modelo contra el catálogo sin ese filtro.
No se revisaron: hay que ver primero qué hace su matcheo con un alias vacío antes de
decidir el arreglo.

## P3 · Unidad sin canonizar en `_declared_price_basis` (bug 6) — Media

**Dónde:** `_declared_price_basis`.

**Qué pasa:** compara `canonical_unit(declarada)` contra la unidad cruda de la línea,
sin canonizar. Una unidad en plural o con alias («cajas», «litros») nunca coincide.

**Reproducción:** `"5 cajas a 15000 Sale 3000 cup caja"`: `unit="cajas"` y
`canonical_unit("caja")="caja"` no coinciden, y la función devuelve `None`. La misma
marca «Sale» hace que `_packaging_price_basis` también devuelva `None`, así que
`price_basis` queda vacío aunque el anuncio lo declara y la aritmética lo confirma.

**Por qué no es Alta:** no corrompe el dato, deja de proponerlo y el operador lo
completa a mano. Pero `price_basis` ya tuvo un bug real de precio unitario contra
total (comentario en `models/price_history.py`), así que cada propuesta perdida acá
es un campo sensible que se completa a mano.

## P4 · Recompilación de regex por línea (hallazgo 10) — Baja

**Dónde:** `_name_pattern`, `_match_product`, `_match_province`.

**Qué pasa:** se compila una regex por cada combinación de línea, producto y alias, y
`propose_all` llama a `_match_product` varias veces sobre las mismas líneas (lectura
de bloque, `mencionados`, bucle principal). `_match_province` rearma los patrones de
alias en cada llamada. Todo eso se repite en cada carga de la cola.

**Arreglo sugerido:** `functools.lru_cache` en `_name_pattern`, precompilar los alias
de provincia a nivel de módulo y reutilizar los matches de producto por línea.

**Por qué Baja:** no es un bug. Pesa más a medida que crece el catálogo.

## P5 · Menores sin reproducir — Baja

La revisión los reportó pero no los reprodujo:

- `_QUANTITY_RE` no lee «5 unidades»: `_UNIT_WORDS` no tiene la forma plural.
- `_TIER_RE` no reconoce un escalón que termina en moneda: «Más de 10 cajas 28 usd».
- Las funciones `fila` y `coincide` tienen nombre en español, contra la regla de
  CLAUDE.md de nombrar en inglés.
- La guarda de producto único está duplicada en `_tiered_proposals` y en
  `propose_all`, y hay dos guardas de cantidad casi iguales en `_declared_price_basis`
  y `_packaging_price_basis`.

## P5 · Validar alias al guardar en `/catalog` — Baja

**Dónde:** `web/routers/catalog.py`, alta y edición de producto.

**Qué pasa:** el motor ya ignora los alias vacíos o nulos, pero la tabla los acepta.
Filtrarlos al guardar es una segunda defensa. No reemplaza al filtro del motor,
porque el catálogo también se edita con DML a mano en producción.

## Decisión pendiente · ¿Proponer líneas con número suelto y sin producto?

**Contexto:** una línea sin producto de catálogo cuyo único precio es un número
suelto («Papas prefritas 2500») hoy se descarta en `propose_all`, porque no tiene
nombre candidato. Eso es lo que frena que la dirección «Carretera Central Km 34» o
el número de una URL se lean como ofertas (`test_numero_suelto_sin_producto_sigue_descartado`).

**Qué haría falta para decidir:** medir contra la cola real cuántas ofertas genuinas
se pierden por esto y cuántas filas basura entrarían si se aceptaran. Sin esa
medición, se mantiene el comportamiento actual.
