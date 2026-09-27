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
| 3c | Glosario CERRADO (2026-09-27, verificado y en prod); recorte del texto sigue Pendiente | EN PROGRESO |
| 4 | Interfaz de revisión: Aceptar, atajo, orden de la cola | CERRADO (2026-09-27) |
| 5 | Vigilancia del rubber-stamping en `/control` | CERRADO (2026-09-27, verificado por el operador) |

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
     corrigieron con backup previo. Las 2 filas de arroz «por mayor» a 50 kg que sí
     eran error del modelo: **RESUELTO (2026-09-27).** Ejemplo agregado a la regla
     de `sale_format` (`PROMPT_VERSION` `2026-09-27.1`), re-medido en
     `data/llm-backtest-report-v5.md`: 97,1% (68/70) contra 90,0% (63/70). Detalle
     en `docs/decisions.md`.
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
5. **3c — glosario: HECHO y verificado por el operador. CERRADO (2026-09-27).**
   Ver detalle en la sección del slice más abajo. El recorte del texto (la
   otra mitad de 3c) sigue pendiente, para otra sesión.
6. **Slice 5 — HECHO y verificado por el operador. CERRADO (2026-09-27).**

### Pendientes sueltos (no son de un slice; se atienden cuando toquen)

- **Prompt caching del prefijo fijo: VERIFICADO (2026-09-26)** con la API real
  (`prefill --execute --limit 3`): `cache_read_tokens` = 2733 en las tres llamadas.
- **Respuesta truncada en `max_tokens`:** en esa misma prueba un anuncio salió
  `DEGRADADO (other)` con 4096 tokens de salida (`62cd0e01`, sin filas). Es el
  primer dato para 3c: mirar su `raw_response` y ver en qué se fueron los tokens.

- **Las reglas de la base del precio no llegan a la cola: RESUELTO (2026-09-27).**
  `propose_all` aplica ahora `_declared_price_basis` y `_packaging_price_basis`
  línea por línea (antes solo `propose()` lo hacía). Detalle en
  `docs/decisions.md`.
- **El pre-cálculo no filtra por foco: RESUELTO (2026-09-27).** `select_candidates`
  descarta los pendientes de texto fuera de foco (mismo criterio que
  `_pending_in_focus`); una imagen sin transcribir sigue entrando. Detalle en
  `docs/decisions.md`.
- **Costo real por anuncio sin medir en la cola: RESUELTO (2026-09-27).**
  Recalculado con `llm_proposals`: mediana $0,0128/anuncio con Sonnet 5 (contra
  $0,0073 del backtest). `docs/costos.md` actualizado.
- **Redacción de teléfonos: RESUELTO (2026-09-27).** No cubría formatos como
  «+53 5 234 5678»; corregido en `src/saimc/privacy.py`.
- **`classify_failure`: RESUELTO (2026-09-27).** Ya no etiqueta `auth` el 400
  de «clave sin workspace»; el status manda sobre el texto.
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

### Slice 3c — Glosario y recorte del texto — glosario CERRADO (2026-09-27), recorte Pendiente

- **Investigación previa («en qué se van los tokens de salida»): RESUELTA
  (2026-09-27), y desvió el diagnóstico.** No era el texto de entrada: `propose_ad`
  no desactivaba `thinking`, y Sonnet 5 corre *adaptive thinking* por defecto —
  facturado como `output_tokens`, invisible en `raw_response`. Medido contra 3
  anuncios reales: con el default, 2 de 3 agotaban `max_tokens` (4096) antes de
  terminar el JSON y degradaban (mismo caso que `62cd0e01`); con `thinking:
  disabled`, 7-8x menos tokens de salida y JSON completo en los tres. Ya
  corregido en `propose_ad` (`docs/decisions.md`). El glosario y el recorte de
  abajo siguen pendientes — apuntan a la entrada, que no era el problema
  medido — y se retoman con la vara de costo ya más baja por este fix.
- **Nota del operador y glosario: HECHO y verificado por el operador. CERRADO
  (2026-09-27).** Tabla `glossary_entries` (migración `0023`),
  CRUD en `/catalog#glosario` (alta, edición, activar/desactivar, borrar), y
  las entradas activas se inyectan en el bloque cacheado de `propose_ad`. La
  versión del prompt se deriva sola por hash del contenido activo
  (`saimc.glossary.effective_prompt_version`) en vez de exigir que el operador
  suba `PROMPT_VERSION` a mano. Detalle y lo descartado (no se migró el
  ejemplo ya backteado de «Pollo Muslo 4x10») en `docs/decisions.md` y
  `docs/spec-slice-3c.md`.
- **Recorte del texto: sigue pendiente**, sin bloqueo — se retoma aparte.
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

### Slice 5 — Vigilancia del rubber-stamping en `/control` — CERRADO (2026-09-27)

Implementado en el commit `64907a1` (2026-09-27); quedó marcado «Pendiente»
por descuido cuando este tablero se puso al día, no porque faltara código.

- Tasa de aceptación sin edición, por campo (de `llm_reviews`).
- Tiempo por decisión.
- Aviso si la aceptación sin edición sube y el tiempo por decisión baja a la vez.

Detalle completo en `docs/decisions.md`, entrada «Slice 5: vigilancia del
rubber-stamping en `/control`» (2026-09-27). **Verificación en pantalla:
HECHA** — el operador confirmó la sección «Aceptación sin revisar» (tasa 80%,
132 decisiones medidas, mediana 81s con `n=5`, tabla por campo), sin banner de
alerta visible — esperable, no es un bug: la alerta solo dispara si sube la
tasa sin editar Y baja el tiempo por decisión a la vez contra la semana
anterior, y `n=5` es chico para esa comparación todavía.

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
