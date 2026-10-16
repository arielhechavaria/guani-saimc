# Mapa de la especificación

Índice de consulta de `spec.md`. Existe para no releer 1649 líneas cada vez que hay
que contrastar una propuesta contra el documento rector.

**Cómo se usa:** buscá la fila que corresponde a lo que estás por proponer, leé
**solo** ese rango de líneas, y recién entonces proponé. Si el documento ya decidió
la cuestión, no hay nada que proponer: hay que implementar lo decidido.

No es una camisa de fuerza. Apartarse de la especificación es legítimo, pero hay que
decirlo explícitamente y dejar el motivo en `decisions.md`. Lo que no es legítimo es
apartarse sin saber que se estaba apartando.

---

## Qué documento es cuál

- **`docs/spec.md`** — la especificación técnica. **Rige.** Es lo que mapea este archivo.
- **`docs/decisions/001-stack-python-unico.md`** — el ADR del stack y de la capa de
  visualización, incluido el camino Fase 0 → 1 → 2. Es el que §13.1 pide por nombre.
  No reemplaza a `docs/decisions.md`: registra el camino, no los pasos.
- **`docs/pitch-v3.5.md`** — el documento de inversión, congelado el 2026-08-12.
  **No rige.** Antes se llamaba `whitepaper.md`. Contiene copias de las secciones
  técnicas por haberse congelado entero; esas copias no son fuente de nada.

Si los dos difieren, gana `spec.md`. Las contradicciones conocidas del pitch están
listadas en su propio encabezado.

---

## Vigencia del mapa

- Mapea: **`spec.md`**, 1649 líneas, extraído del whitepaper v3.5 el 2026-08-12.
- Antes de confiar en los números de línea: `wc -l docs/spec.md`. Si no da 1649, los
  números están corridos y hay que regenerar el mapa.
- Anclas de respaldo, siempre vigentes: `rg -n '^#{1,4} ' docs/spec.md`. Los números
  de sección (§6.1, §10.6) sobreviven a las ediciones; los de línea no.
- **Los números de sección se conservaron del whitepaper.** Faltan §1, §2, §4, §8,
  §14 y §15 porque son las secciones de inversión y quedaron en el pitch. El hueco
  es deliberado: renumerar habría invalidado las citas del código de un plumazo.

---

## Antes de proponer → dónde verificar

| Si vas a proponer algo sobre… | Sección | Líneas | Qué tan atado está |
| --- | --- | --- | --- |
| Captura, bandeja de entrada, cola de curaduría | §6.1 Etapa 1 | 303–334 | **Vinculante** |
| Inmutabilidad del archivo original | §6.1 | **321** | **Vinculante** |
| OCR, extracción asistida, quién decide | §6.1 | **336–350** | **Vinculante** |
| Esquema de `raw_ingestions` | §7.1 | 1035–1060 | **DDL completo** |
| `products`, `prices_history` | §7.2 | 1062–1115 | Campos + conversión monetaria |
| Conversión de moneda, tasa de cambio | §6.3 E | **625–654** | **Vinculante** |
| Intelligence Layer, agregados | §7.3 | 1117–1159 | Prosa — diseño abierto |
| Reglas de validación | §6.2 | 448–542 | Prosa detallada |
| Score, estados de validación | §6.2 | 491–561 | Prosa |
| H0, umbrales de decisión | §3 | 48–68 | **Apartado 2026-08-12 y B3 (2026-10-10)** — la tabla de veredictos (56–62) ya no rige; el párrafo 66 sí. El conjunto de referencia (54) está congelado con menos de 300 anuncios: ver «Apartamientos deliberados» |
| Métricas de extracción | §12.5 | 1554–1571 | **Apartado 2026-08-12 y B3 (2026-10-10)** — H0 dejó de ser la única métrica de Fase 0; la de la Fase 0 es la automatización con su error auditado (ver «Apartamientos deliberados») |
| Umbrales nuevos sin valor asignado | §12.6 | **1594–1612** | **Vinculante: no inventar** |
| Alcance de Fase 0, entregables | §9 Fase 0 | 1271–1297 | **Vinculante** |
| Criterio de salida de Fase 0 | §9 | **1295** | **Vinculante en su 1ª oración**; el corte por debajo de 70% se apartó el 2026-08-12 |
| Qué provincia se publica y cuándo | §9.0 | **1169–1269** | **Vinculante** |
| Manipulación, ataque coordinado, C1–C4 | §10.7 | **1407–1518** | **Vinculante** |
| Qué se publica de la metodología | §6.4.1 | **697–761** | **Vinculante** |
| Stack tecnológico | §13.1 | 1616–1638 | **Cerrada.** Leé antes `docs/decisions/001-stack-python-unico.md`: es el ADR y trae dos apartamientos |
| Datos personales, privacidad | §10.6 | 1390–1405 | **Vinculante** |
| Transparency Layer, qué se publica | §6.4 | 656–695 | Prosa |
| Linaje y trazabilidad del dato | §6.5 | 763–853 | Prosa |
| Dashboard, visualización | §6.7 | 928–1009 | Prosa |
| Posicionamiento frente a elTOQUE, objeto medido | §5 | 89–219 | **Vinculante: no revertir** |
| Roadmap, fases posteriores | §9 | 1161–1343 | Prosa |
| Niveles de producto, monetización | §8.1 | — | **En el pitch congelado, no rige.** Nada que consultar acá |

---

## Especificidad del modelo de datos

Distinguir esto evita los dos errores simétricos: proponer lo ya decidido, y
tratar como decidido lo que nunca se especificó.

| Tabla | Qué hay en la especificación | Estado |
| --- | --- | --- |
| `raw_ingestions` | `CREATE TABLE` con tipos, comentarios e índice único | Implementar tal cual |
| `products` | Lista de campos sin tipos ni restricciones | Diseño abierto |
| `prices_history` | Campos, incluidos los cuatro de conversión monetaria (§6.3 E) | Campos atados, tipos abiertos |
| `provinces` | Nombrada en el alcance de Fase 0 (1286) y restringida por §9.0 | Sin definir, pero con una restricción vinculante |

`raw_ingestions` es la **única** tabla con DDL real en todo el documento.

**Restricción vinculante sobre `provinces` (§9.0, línea 1250):** «la incorporación de
una provincia debe ser configuración, no desarrollo». Cualquier diseño que exija tocar
código para sumar una provincia contradice el documento.

**Estado de publicación por provincia.** §9.0 exige que la interfaz distinga entre
provincia publicada e provincia en ingesta sin publicación. Eso es estado que vive en
algún lado: tenerlo en cuenta al diseñar `provinces`.

---

## Contradicciones conocidas

Registradas para no volver a descubrirlas y para no proponer algo que camine
directo hacia ellas.

**1. Qué muestra la Transparency Layer (abierta).**
La línea 321 dice que se publica «literalmente el archivo capturado». La §10.6
mitigación 2 dice que se muestra el anuncio «no la identidad del anunciante», y la
mitigación 3 dice que no se almacenan teléfonos ni datos de contacto. Con texto
pegado las tres se sostienen. Con una captura de WhatsApp o Telegram no: los
píxeles traen nombre del remitente y a menudo teléfono.

Alcance: es un problema de **publicación**, no de almacenamiento. Guardar local en
Fase 0 —un operador, sin auth, sin exponer— es defendible. Publicar la captura tal
cual rompe la mitigación 2.

No bloquea `raw_ingestions`: la salida derivada y redactada es aditiva (tabla nueva
o columna nullable) y no toca la fila inmutable.

**2. Umbral de densidad vs. lanzamiento del dashboard (resuelta en v3.5).**
§9.0 exigía no publicar una provincia sin densidad mínima; §3 dice que H0 no bloquea
el lanzamiento del dashboard. Aplicadas juntas y en sentido literal, el dashboard de
Fase 1 quedaba vacío. Resuelto en §9.0 «Cómo se concilia con las Fases 0 y 1»: la
condición estadística rige desde el día 1 (tamaño de muestra visible, sin titular de
precio); la condición adversarial rige desde Fase 2, cuando se abre la ingesta
comunitaria. Registrado en `decisions.md`.

**3. Las de la §14 ya no son contradicciones de la especificación.**
El pitch pide financiar Angular SSR + TimescaleDB (§14.1) y fija «16 provincias»
como Hito 1 (§14.5), contra §13.1 y §9. Al congelarse el pitch, dejaron de ser
contradicciones internas del documento rector y pasaron a ser deuda del pitch,
listada en su encabezado. Se resuelven cuando el pitch se reescriba.

---

## Apartamientos deliberados

Lo implementado que no coincide con el documento, con su entrada en `decisions.md`.

| Tema | Especificación | Implementado | Entrada |
| --- | --- | --- | --- |
| Restricciones de `raw_ingestions` | DDL sin `NOT NULL` | `NOT NULL` en 8 columnas | 2026-08-06 |
| Marcas de tiempo | `TIMESTAMP` | `TIMESTAMPTZ` | 2026-08-06 |
| H0 como veredicto de fase | Tabla de decisión avanzar/detener (§3, 56–62) | Métrica de eficiencia; los umbrales 85/70 son referencia de escala | 2026-08-12 |
| Motor del mapa de calor | §13.1: MapLibre GL | SVG inline de las 16 provincias, con la geometría vendorizada. **Decidido, sin construir** | 2026-08-22 |
| Pie de atribución del dato | §6.5: la procedencia acompaña a lo publicado | No se dibuja en `?vista=tablero`; ahí las cuatro preguntas las contesta el panel «Sobre los datos», que además se quedó con el crédito a elTOQUE. Intacto en el mapa y en el resto de la capa pública | 2026-09-21 |
| Rótulo «muestra en formación» | Entrada del 2026-08-22, no el texto de §9.0 | Fuera del tablero. Lo vinculante de §9.0 —qué provincias están publicadas y cuáles en ingesta, y la ausencia como decisión— lo dice la viñeta «N de 16 provincias tienen datos» | 2026-09-21 |
| Mapa de calor por precio | §6.7: verde / amarillo / rojo contra el promedio nacional | Capa «precio»: un solo matiz, relativa a las provincias con dato del producto abierto, sin umbral ni verde/rojo; el cambio va sin flecha ni color | 2026-09-21 |
| Respuesta pública a «¿por qué el sistema confía en él?» | §6.5: `trust.user_score` y `trust.confidence_score` en el registro | Conteos por celda de procedencia (`human`/`auto`/`audited`) y de estado de validación; ningún puntaje se publica (§12.6) | 2026-10-10 (B4) |
| Columnas de `prices_history` | §7.2: la lista de campos no trae estado de validación, detalle ni procedencia | `validation_status`, `validation_detail` y `provenance` (`human`, `auto`, `audited`), agregadas por la migración 0027; `confidence_score` pasa a llenarse | 2026-10-03 (B1) |
| Resultado de validación | §6.2, «Resultado de validación»: un solo JSON con `validation_status`, `confidence_score` y `rules` | El estado y el puntaje son columnas; el JSON (`validation_detail`) guarda las reglas, los motivos, la versión del motor, los parámetros y la fecha y, si hay, la señal `jev` (lo que contestó Jev en sombra: no entra a ninguna regla) | 2026-10-03 (B1); `jev`: 2026-10-04 (A6b) |
| Tabla `jev_signals` | §7: el esquema no la trae | Salida derivada y aditiva, de solo altas, igual que `ocr_transcriptions`: la respuesta de Jev por anuncio y por huella de preguntas (migración 0030). No toca `raw_ingestions` | 2026-10-04 (A6b) |
| Referencias mínimas por celda | §12.6: ningún umbral se inventa, se mide | `min_cell_n = 5` para persistir la validación: decisión del operador, sin medición que lo elija y sin efecto sobre ningún carril | 2026-10-03 (B1) |
| Descarte automático (carril 0) | §6.1: OCR y extracción como asistentes de la cola, «no como etapas autónomas del pipeline» | El motor descarta solo, sin pasar por la cola, lo que entra al carril 0 (texto e imagen), con auditoría semanal por muestra. **Decidido en G1a, se construye en B2a**. Desde jev-decisor, con `SAIMC_LANE0_JEV_ENABLED`, Jev es segundo juez del texto (P(sí) < 0,2) y descarta solo cuando el asistente no propuso, con cota propia | 2026-10-03; Jev: 2026-10-16 (jev-decisor) |
| Aceptación automática (carril 1) | §6.1, «Rol del OCR y la extracción asistida»: «el sistema propone; el operador confirma o corrige». §10.7: un humano por registro en la Fase 0–1 | El motor acepta solo, sin pasar por la cola, los textos de una fila que entran al carril 1, y sus filas se publican de inmediato con `provenance = auto`. Se auditan después: todo hasta 60 auditados, y una muestra semanal por canal desde ahí. El carril se frena solo si el error auditado supera 10 %. Nace apagado (`SAIMC_LANE1_ENABLED`). **Decidido en G1b, adelantada; se construye en B2b** | 2026-10-09 |
| Anuncios aceptados solos y H0 | §12.5: la precisión se mide contra anuncios etiquetados a mano | Un anuncio aceptado por el carril 1 no tiene etiqueta humana previa: queda fuera del conjunto de referencia, **también después de auditado**. El conjunto es cerrado, y la auditoría confirma una lectura del modelo: no es una etiqueta independiente. Lo que la auditoría alimenta es el error auditado del carril | 2026-10-09 (B2b); corregida el 2026-10-10 (B3-2) |
| Métrica de la Fase 0 | §12.5: las métricas de extracción son «las únicas relevantes durante la Fase 0» | La métrica de la Fase 0 es la automatización —resuelto sin el operador y publicado sin el operador, dos números por cohorte de ingreso— y cada uno se publica con su error auditado y su n (`saimc/automation.py`; `measure.py`, `/control` y `/metodologia`). H0 queda como medición del extractor por reglas sobre el conjunto congelado. Ninguna meta nueva ni número compuesto | 2026-10-10 (B3) |
| Marca de conjunto de referencia | §6.1: la cola «permite marcarlo como parte del conjunto de referencia» | El conjunto se congela por regla y por lote, con un comando (`python -m saimc.extraction.reference_set`), y se versiona en `reference_set_members`. No hay marca por anuncio: elegir a mano qué entra sesga la referencia | 2026-10-10 (B3-1) |
| Tamaño del conjunto de referencia | §3: al menos 300 anuncios | El conjunto se congela con los anuncios etiquetados sin asistencia que existen (unos 216; el n real se anota en `docs/decisions.md` al congelar) y ya no crece: todo lo que se cura hoy nace asistido. H0 se mide contra esa copia y se informa con su n. `is_official` sigue en falso: el 300 no se baja. El criterio de salida de §9 («H0 respondida con un número») se cumple con el número sobre el conjunto congelado, diciendo que no llega a 300 | 2026-10-10 (B3-2) |
| Estadístico robusto contra atípicos | §10.7 C2: mediana y MAD | Mediana en los agregados y, para el atípico, cerco de IQR sobre el logaritmo (`validation/fence.py`): el de la regla 2 y el de la cifra pública. MAD no se usa | 2026-10-03 (C3) |
| Índice de control y señales de coordinación | §10.7 C3: fuentes de alta confianza elegidas por emisor (historial, consistencia, antigüedad). C3 y C4 «no aplicables» en la Fase 0–1 | La fuente de alta confianza es la fila que verificó una persona (`provenance` `human` o `audited`). El plan adelanta C3 y C4 a antes del carril 1: C3 con el cerco existente, C4 como medición sin umbrales | 2026-10-03 (C3) |

El último está razonado en `docs/decisions/001-stack-python-unico.md`, que es
además el ADR que §13.1 (línea 1636) pide por nombre.

**HTMX no es un apartamiento.** §13.1 lo nombra, el ADR lo confirma para el
dashboard, y desde el 2026-08-22 está **vendorizado** en
`src/saimc/web/static/htmx.min.js` (2.0.10, con su sha256 fijado en los tests).
Todavía no lo carga ninguna plantilla: la vista que lo necesite opta desde su
`{% block scripts %}`, y la curaduría se queda deliberadamente sin él.
`GZipMiddleware` también entró ese día — el CSS son 26.390 B contra 6.079 B
gzipeados.

---

## Lo que la especificación no define

- Tipos y restricciones de `products`, `prices_history`, `provinces`.
- Qué motor de OCR usar (define el rol del OCR, no la herramienta).
- Cómo se recorta o redacta una captura para publicación.
- Estructura de directorios del proyecto, layout de paquetes Python.
- Estrategia de migraciones más allá de «Alembic desde el primer modelo».
- El valor de ningún umbral introducido en la v3.5. Ver §12.6: **no inventarlos.**
- Qué fuente alternativa de tipo de cambio se usa como respaldo de la TRMI.
