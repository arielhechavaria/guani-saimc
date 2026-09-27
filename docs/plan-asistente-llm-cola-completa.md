# Plan — Curaduría de toda la cola con el asistente LLM

Fecha del plan: 2026-09-25. Última actualización: 2026-09-27.

## Tablero (empezá por acá)

| Slice | Qué | Estado |
| --- | --- | --- |
| 1 | Backtest del modelo contra lo curado a mano | CERRADO |
| 2 | «Completar con IA» propone el anuncio entero, con cita, «difiere» y descarte sugerido | CERRADO |
| 3a | Guardar la propuesta y capturar la revisión (tablas `llm_proposals`, `llm_reviews`) | CERRADO |
| 3b | Pre-cálculo por lotes (`python -m saimc.extraction.prefill`) | CERRADO |
| 3d | El modelo propone también formato de venta, base del precio y envase | CERRADO (2026-09-27) |
| 3c | Glosario y recorte del texto | Pendiente |
| 4 | Interfaz de revisión: Aceptar, atajo, orden de la cola | CERRADO (2026-09-27) |
| 5 | Vigilancia del rubber-stamping en `/control` | Pendiente |

Regla: un slice se cierra (tests, `decisions.md`, checklist, commit y verificación
del operador) antes de abrir el siguiente.

### Próximos pasos, en orden

1. **3d — backtest pagado del prompt v4: HECHO (2026-09-26).** Los 100 anuncios,
   $0,91 en total. Sobre 70 filas del oro (reglas entre paréntesis): producto 98,6%
   (94,3), precio y moneda 84,3% (55,7), cantidad y unidad 85,7% (60,0), provincia
   95,7% (94,3), **extracción completa 71,4% (40,0)**, contra 67,1% de la v3. Campos
   nuevos: formato de venta 90,0% (63/70), base del precio 94,4% (51/54), envase
   66,2% (45/68). Descarte sin falsos descartes. Detalle en
   `data/llm-backtest-report-v4.md`.
   - **Envase:** el 66% es sobre todo «sin propuesta»: en el parcial de 80, de 20
     errores, 18 eran abstenciones (`null`) y solo 2 un valor equivocado.
   - **Formato de venta:** las 7 filas de la lista diaria de Santiago («Sacos Arroz
     25Kg… Venta mínima: 1 Saco») estaban mal etiquetadas en el oro (un saco
     individual es minorista, aclarado por el operador el 2026-09-26) y se
     corrigieron con backup previo. Quedan 2 filas de arroz «por mayor» a 50 kg que
     sí son error del modelo: dejó que la palabra «mayor» decidiera. Opcional: una
     línea en el prompt («que el anuncio diga “por mayor” no basta») con nuevo
     `PROMPT_VERSION` y re-medición.
   - **Base del precio:** 3 fallos de 54.
   - Ojo con la muestra: esa misma lista aparece 33 veces en la base con distintas
     fechas; pesa mucho en cualquier medición por fila.
2. **3d — etiquetas a 390 px: HECHO** (commit `55eb0ab`). Las preguntas de
   «formato de venta» y «base del precio» pasan a una línea propia encima del
   `<select>` (`m3-field-stacked`). Sin captura: el Chromium de esta máquina no
   arranca (falta `libnspr4`); lo verifica el operador.
3. **3d — verificación del operador en pantalla: HECHA. Slice CERRADO
   (2026-09-27).**
4. **Slice 4 — Aceptar, atajo y orden de la cola: HECHO y verificado.
   CERRADO (2026-09-27).**
5. **3c.** Antes del recorte: mirar en `llm_proposals.raw_response` en qué se van
   los tokens de salida (un anuncio de 4 filas gastó 2629; y el degradado por
   truncamiento de la verificación del caching, más abajo).
6. **Slice 5.**

### Pendientes sueltos (no son de un slice; se atienden cuando toquen)

- **Prompt caching del prefijo fijo: VERIFICADO (2026-09-26)** con la API real
  (`prefill --execute --limit 3`): `cache_read_tokens` = 2733 en las tres llamadas.
- **Respuesta truncada en `max_tokens`:** en esa misma prueba un anuncio salió
  `DEGRADADO (other)` con 4096 tokens de salida (`62cd0e01`, sin filas). Es el
  primer dato para 3c: mirar su `raw_response` y ver en qué se fueron los tokens.

- **Las reglas de la base del precio no llegan a la cola.** `propose_all` (que usan
  `/curation` y el backtest) no aplica `_declared_price_basis` ni
  `_packaging_price_basis`; solo `propose()` lo hace. Por eso la marca
  «Reglas: …» de ese campo casi nunca aparece. Slice propio.
- **El pre-cálculo no filtra por foco:** paga también los pendientes fuera de foco
  que después se descartan en lote.
- **Costo real por anuncio sin medir en la cola:** el backtest dio ~$0,0073; las
  primeras propuestas reales, ~$0,02. Recalcular con lo que ya hay en
  `llm_proposals` y actualizar `docs/costos.md`.
- **Redacción de teléfonos** no cubre formatos como «+53 5 234 5678». Importa más
  ahora que todo el texto sale a un tercero.
- **`classify_failure`** etiqueta como `auth` el 400 de «clave sin workspace».
- **Qué significa H0 ahora** (ver la sección al final).

---

## Qué cambia

Antes el operador curaba cada anuncio a mano y el asistente solo completaba huecos,
a pedido, uno por uno (`docs/decisions.md`, 2026-09-01). El camino nuevo:

- **El asistente hace la curaduría de toda la cola**: propone el formulario
  completo y, cuando corresponde, el descarte con su motivo.
- **El operador solo verifica** el formulario contra el anuncio y da **Aceptar**
  (o corrige y acepta, o descarta).
- La confirmación humana se mantiene: nada llega a `prices_history` sin que el
  operador la dé.

## Decisiones del operador (2026-09-25)

1. **Todo pasa por el asistente.** No se reserva una muestra sin asistencia.
2. **La medición de H0 queda por redefinir** (ver «Pendiente»).
3. **Aprender de las correcciones queda diferido**, salvo la nota del operador y
   el glosario (3c), que son contexto curado a mano, no entrenamiento.

## Apartamientos registrados en `docs/decisions.md`

| Qué se aparta | De dónde | Dónde quedó registrado |
| --- | --- | --- |
| «LLM solo para lo que las reglas no resuelven» | `EXT-07`, `spec.md` §6.1 | slice 2 |
| «Solo a pedido, nunca en lote» | decisión 2026-09-01 | slices 2 y 3b |
| «H0 se mide sobre lo no asistido» | decisión 2026-09-01, `spec.md` §3 | slices 2, 3a y 3b (redefinición pendiente) |
| «La curaduría es manual» | decisión cerrada 2 | slice 2 |
| «Formato de venta y envase nunca se prellenan» | design 2026-08-12 (D4/D5) | slice 3d |

**Decisión cerrada 3 (sin cola de trabajos):** se respeta. El pre-cálculo es un
comando que lanza el operador, no un worker ni un cron.

## Lo que NO cambia

- La imagen nunca sale del VPS: el modelo recibe solo el texto (OCR) ya pasado por
  `redact_personal_data`. Un modelo de visión sigue descartado (privacidad).
- Catálogos cerrados, revalidados de nuestro lado: nada se da de alta desde la
  salida del modelo.
- `raw_ingestions` inmutable; toda extracción conserva referencia al registro crudo.
- Sin clave, el asistente degrada a «sin propuesta» y la cola funciona a mano.

---

## Slices

### Slice 1 — Backtest — CERRADO

Corre el modelo sobre los anuncios ya curados a mano y compara con lo que confirmó
el operador, con las mismas funciones que H0 (`llm_backtest.py`). Reporta por texto
e imagen: veredicto de descarte, acierto por campo modelo frente a reglas,
abstención, filas de más, consumo y calidad de las señales. Sin umbrales: la vara
la fija el operador (`spec.md` §12.6).

- Resultado con el prompt v3 (100 anuncios): precio y moneda 84,3%; costo real
  $0,73 por 100 anuncios. Detalle en `docs/decisions.md` («Backtest del asistente
  LLM») y `docs/costos.md`.
- Hallazgo: la API rechaza `enum` con tipo en lista; la nulabilidad va con `anyOf`
  (`_enum_o_null`). Los tests con cliente falso no lo detectan.

### Slice 2 — Propuesta completa y descarte — CERRADO

Una llamada por anuncio (`llm_propose_ad.py`). El modelo propone todas las filas,
cita la evidencia de cada una (verificada por código, `llm_checks.py`) y sugiere
descartar con motivo. Las reglas son segunda opinión: el campo que difiere lleva
halo ámbar y «Reglas: x». Lo medido cambió el diseño: la «moneda asumida» y la
evidencia hallada no predicen el error y no se muestran como marca; el acuerdo con
las reglas sí. Se descartó la confianza 0–100 autodeclarada.

### Slice 3a — Guardar la propuesta y capturar la revisión — CERRADO

Especificación en `docs/spec-slice-3a.md`; migración `0021`. `llm_proposals`
guarda cada llamada (filas, respuesta cruda, tokens, estado). `llm_reviews` guarda,
al curar o descartar con propuesta a la vista, qué se propuso, qué confirmó el
operador, qué campos editó y su nota. Recargar no vuelve a pagar la llamada.

### Slice 3b — Pre-cálculo por lotes — CERRADO

`python -m saimc.extraction.prefill`: en seco por defecto; `--execute` procesa los
pendientes de a uno, reanudable, con `--limit` y `--max-cost`. Sus líneas en el
registro llevan `"source": "prefill"`: suman consumo en `/control` pero no sacan el
anuncio de H0 (lo saca `llm_reviews`, al curarlo con la propuesta a la vista). Uso
en `docs/runbook-asistente-llm.md`.

### Slice 3d — Formato de venta, base del precio y envase — ABIERTO

Prompt `2026-09-25.4`. El modelo propone también minorista o mayorista
(`sale_format`), precio por unidad o total del lote (`price_basis`) y envase
(`packaging`, vocabulario cerrado), con las definiciones del propio formulario
(mayorista es escala de contenedor; un saco suelto no lo es). Ejemplo: «Pollo Muslo
4x10: 27300» → caja, 40 lb, total del lote, minorista.

- Sin cambio de esquema. El formulario prellena los tres campos; la base del precio
  lleva «difiere» frente a las reglas. La captura de la revisión registra si se
  editaron. El backtest mide los tres, con las filas cuyo oro es NULL aparte.
- La variante (tipo, marca, origen) queda fuera: es texto libre y crea registros
  en el catálogo.
- Falta: backtest pagado, arreglo de etiquetas a 390 px y verificación del
  operador (ver «Próximos pasos»).

### Slice 3c — Glosario y recorte del texto — Pendiente

- **Nota del operador y glosario.** La nota ya se guarda (3a). Falta poder
  **promoverla al glosario**: reglas cortas, legibles y editables que se inyectan
  en el prompt de todas las llamadas (ejemplo: «4x10» = 4 paquetes de 10 lb, la
  caja pesa 40 lb). Condiciones:
  - cada cambio del glosario cambia la versión del prompt, para que el backtest
    compare antes y después;
  - glosario corto: cada regla cuesta tokens en todas las llamadas, y reglas
    contradictorias empeoran al modelo;
  - un patrón muy regular (como `NxM`) se evalúa primero como regla determinista
    del extractor, que es gratis y no falla.
- **Recorte del texto antes de enviarlo.** En listas largas se envía solo lo
  pertinente:
  - las líneas que el motor de reglas reconoce como producto foco
    (`engine._match_product`), más la línea siguiente (precios por volumen);
  - el encabezado que declara moneda o unidad («Precios en CUP»);
  - si no se reconoce ninguna línea, el texto entero.
  Riesgos y medición:
  - un producto foco que los sinónimos no reconocen nunca llega al modelo: el
    backtest se corre con y sin recorte sobre el mismo oro, y el recorte entra solo
    si no pierde filas;
  - la evidencia y el resaltado se verifican contra el texto recortado;
  - sale menos texto a un tercero (privacidad, `spec.md` §10.6);
  - **ojo con el ahorro:** en el anuncio medido (4 filas, $0,033) la entrada costó
    $0,007 y la salida $0,026. Primero mirar la respuesta cruda.

### Slice 4 — Interfaz de revisión — CERRADO (2026-09-27)

- **Aceptar: HECHO.** Con una propuesta del modelo a la vista el botón
  principal dice «Aceptar» (antes «Curar anuncio», mismo endpoint) y responde
  al atajo `Ctrl+Enter` / `Cmd+Enter` en cualquier campo del formulario, que
  sigue editable. Sin propuesta el botón vuelve a decir «Curar anuncio» y el
  atajo no se activa. El descarte sugerido en un clic ya estaba del slice 2.
- **Orden por acuerdo: HECHO, solo en `/curation` (vista de un anuncio).**
  Dentro de cada nivel de foco (`_focus_rank`), se reordena por acuerdo con
  las reglas: coincide → sin propuesta/sin regla → difiere
  (`_order_by_agreement`, `curation.py`). Las imágenes (`_FOCUS_UNKNOWN`) se
  ordenan solo por el ESTADO de su propuesta guardada —con filas, sugiere
  descartar, ninguna de las dos—, nunca por acuerdo con las reglas: eso
  exigiría OCR ahí mismo, que es justo lo que la cola evita a propósito
  (decisión del operador, 2026-09-26). **`/curation/cola` (el listado
  completo) NO se tocó** — sigue por `captured_at` dentro de cada foco; no
  estaba pedido y reordenarlo ahí es un cambio más grande.
- **Verificación en pantalla: HECHA.** El operador revisó el botón, el atajo
  y el orden (390 px y escritorio) y confirmó que anda según lo planificado
  (2026-09-27). No hay prototipo aprobado de este slice (no lo hay en
  `docs/decisions.md`); la vara fue el plan, no un diseño visual previo.

### Slice 5 — Vigilancia del rubber-stamping en `/control` — Pendiente

- Tasa de aceptación sin edición, por campo (de `llm_reviews`).
- Tiempo por decisión.
- Aviso si la aceptación sin edición sube y el tiempo por decisión baja a la vez.

---

## Costo

- Backtest v3: $0,73 por 100 anuncios con Sonnet 5 (~$0,0073 por anuncio).
- Primeras propuestas reales en la cola: ~$0,02 por anuncio (listas largas; muestra
  chica). Pendiente recalcular con `llm_proposals` (ver «Pendientes sueltos»).
- A $0,0073: ~1200 anuncios al mes ≈ $9. A $0,02: ≈ $24.
- La API se factura aparte del plan Pro, con auto-reload apagado.

## Del análisis externo (2026-09-25): qué se tomó y qué no

Se tomó: evidencia por fila verificada por código, contexto del mercado cubano y
versión del modelo y del prompt en cada propuesta. No se tomó: modelos multimodales
(privacidad), tool use (ya hay salida estructurada), ejemplos few-shot inventados,
la confianza autodeclarada, ni medir H0 con el historial de correcciones
(contradice `CLAUDE.md`).

## Fuera de alcance

- Aceptar sin revisión del operador (auto-aceptar por confianza).
- Modelo de visión sobre la imagen cruda.
- Worker, cron o cola de trabajos.
- Red neuronal propia, fine-tuning o agente: con ~195 curados a mano, sin GPU y en
  2 vCPU rinden peor que las alternativas de abajo.

## Aprendizaje: decisión diferida (2026-09-25)

No entra en este plan, salvo el glosario (3c). Motivos: sin números no se sabe
dónde falla el modelo; un sistema que cambia con el uso mueve la vara de medir; y
aprender de lo *aceptado* refuerza errores si se acepta con prisa (solo las
*correcciones* son señal limpia). La captura de la revisión (3a) deja la opción
abierta.

**Cuándo reabrirlo**, con algunas semanas de curaduría asistida:

- errores concentrados por canal o formato → ejemplos curados recuperados en el
  prompt, comparados contra el conjunto de referencia fijo;
- correcciones recurrentes del mismo patrón → proponer una regla determinista que
  el operador aprueba;
- errores dispersos → nada.

**Regla fija:** los ~195 anuncios curados a mano antes del asistente quedan como
**conjunto de referencia congelado**, fuera de cualquier aprendizaje.

## Pendiente — qué significa H0 ahora

Con todo asistido, la etiqueta ya no es independiente del extractor: el operador
confirma lo que el modelo propuso. **No se resuelve en este plan.** Puntos de
partida:

- El backtest contra el conjunto congelado es la última medición independiente.
- «Aceptación sin edición» mide el trabajo que ahorra el asistente, no su precisión.
- El umbral de 300 anuncios etiquetados (`spec.md` §3) y la meta de 500 registros
  validados (§9) hay que releerlos a la luz de esto.
- Toca `spec.md` §3, §9 y §12.5; el apartamiento se registra en `docs/decisions.md`.
