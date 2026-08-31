# Inventario de requisitos funcionales

Todo requisito funcional que `spec.md` manda, que `decisions.md` agregó o cambió, y
los que faltan — agrupado en 16 módulos y ordenado por fase.

**Qué es y qué no es.** Es un índice de consulta, no una fuente. La fuente sigue
siendo `docs/spec.md` para lo especificado y `docs/decisions.md` para lo decidido:
si este archivo y alguno de esos difieren, ganan ellos. Existe para poder responder
«¿qué falta?» y «¿qué bloquea qué?» sin releer 7.000 líneas.

**Cómo se cita.** Siempre por sección (`§6.1`), nunca por línea, igual que manda
`CLAUDE.md`. Las entradas de `decisions.md` se citan por su fecha.

**Vigencia.** Extraído el 2026-08-30 de `spec.md` (1649 líneas) y `decisions.md`
(5292 líneas), contrastado contra el código en `f97e3b2`. La columna «hoy» de cada
módulo caduca con el código; el resto no.

---

## Por qué módulos y no áreas

El board del Project #1 agrupa por **área** (Dato, Extracción y captura, Precio y
unidades, Privacidad y alcance, Visualización, Infraestructura). Eso sirve para
repartir trabajo.

Este documento agrupa por **módulo de pipeline**, que es otro corte del mismo
sistema y sirve para otra cosa: ver qué módulo depende de cuál, y por lo tanto en
qué orden se pueden integrar. Los dos cortes conviven; ninguno reemplaza al otro.

---

## Estado de los 16 módulos

| # | Módulo | Estado | Bloqueado por |
| --- | --- | --- | --- |
| 01 | Captura e ingesta | Construido | — |
| 02 | Curaduría | Construido | — |
| 03 | Extracción | Techo medido (43,5%) | El OCR, no las reglas |
| 04 | Normalización y comparabilidad | Parcial | `price_basis` histórico |
| 05 | Validación | **Ausente** | — |
| 06 | Reputación de fuentes | **Ausente** | Falta el modelo (H-23) |
| 07 | Antimanipulación C1–C4 | Solo C2 | — |
| 08 | Conversión monetaria | Construido | — |
| 09 | Agregación | Construido | — |
| 10 | Inteligencia predictiva | **Ausente** | H0 / volumen |
| 11 | Publicación y dashboard | Fase 0 completa | Umbrales de §12.6 |
| 12 | Transparencia y linaje | Parcial | Evidencia de imagen |
| 13 | Contribución comunitaria | **Ausente** | Módulos 06 y 07/C3 |
| 14 | Privacidad | Construido, con límite declarado | — |
| 15 | Métricas y observabilidad | Construido | — |
| 16 | Catálogo y administración | Construido | — |

### La cadena de bloqueo

No es preferencia: sale de la especificación.

```
Contribución comunitaria  ←  Reputación (06)  +  C3 del módulo 07
Inteligencia predictiva   ←  volumen  ←  H0
```

§10.7 y §9 (Fase 2, «Hito de seguridad») coinciden: el índice de control paralelo
tiene que estar **operativo antes** de habilitar el botón «subir dato», no después.
Ninguno de los dos existe.

### Fuerza normativa

Cada requisito trae cómo de atado está, según la tabla de `docs/spec-map.md`:

- **Vinculante** — la especificación ya decidió; no hay nada que proponer.
- **DDL** — viene con esquema literal.
- **Cerrada** — decisión de stack, en `CLAUDE.md` o en el ADR 001.
- **Prosa** — diseño abierto.
- **Apartado** — se decidió no cumplirlo, con su entrada en `decisions.md`.
- **Propuesto** — no está en ningún documento; lo propone este inventario.

---

# Grupo 1 — Núcleo del dato

## 01 · Captura e ingesta

**Hoy:** cuatro productores en producción — bandeja Syncthing, pegado, webhook de
WAHA y backfill manual. 550 anuncios procesados. Falta Telegram, que §6.1 manda
agotar primero, y el contrato de entrada explícito.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| CAP-01 | Capturar desde múltiples fuentes sin perder el origen | §6.1 | todas | Prosa |
| CAP-02 | «Primero almacenar, después interpretar»: ningún dato recibido se modifica | §6.1 | todas | Prosa |
| CAP-03 | Conservar cada entrada inicial como evidencia original | §6.1 | todas | Prosa |
| CAP-04 | Interfaz de entrada única: un evento crudo que no cambia según quién lo produzca | §6.1 | todas | **Vinculante** |
| CAP-05 | Pasar de curaduría a APIs sin reescribir el pipeline: solo agregar un productor | §6.1 | todas | **Vinculante** |
| CAP-06 | Separar en el tiempo captura y curaduría; no unificarlas en un formulario | §6.1 | 0 | **Vinculante** |
| CAP-07 | Guardar el anuncio en el formato más rápido: captura, texto o foto | §6.1 | 0 | **Vinculante** |
| CAP-08 | Único requisito: que el material llegue al directorio de entrada | §6.1 | 0 | **Vinculante** |
| CAP-09 | Observar la bandeja y registrar cada archivo como `raw_ingestion` | §6.1 | 0 | **Vinculante** |
| CAP-10 | Registrar archivo, hash, tipo, fecha de captura y estado | §6.1 | 0 | **Vinculante** |
| CAP-11 | Nunca modificar ni eliminar el archivo original | §6.1 | todas | **Vinculante** |
| CAP-19 | Fuentes `inbox_image`, `inbox_text`, `telegram_api`, `community` | §7.1 | todas | **DDL** |
| CAP-20 | Deduplicar por hash de contenido con índice único | §7.1 | 0 | **DDL** |
| CAP-21 | Capa cruda de solo lectura, auditoría completa, nunca se elimina | §7.1 | todas | **DDL** |
| CAP-13 | Las fuentes automáticas escriben en la misma bandeja sin cambiar la curaduría | §6.1 | 3 | **Vinculante** |
| CAP-14 | Telegram por Bot API sobre canales autorizados, agotado primero | §6.1 | 3 | **Vinculante** |
| CAP-16 | Evaluar términos y datos personales de cualquier otra fuente | §6.1, §10.6 | 3 | **Vinculante** |
| CAP-17 | Habilitar fuentes automáticas solo al superar el umbral de precisión | §6.1 | 3 | **Vinculante** |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-10 | Celular → bandeja por Syncthing, carpeta `receiveonly` del lado del VPS |
| 2026-08-10 | La ingesta corre en `GET /curation`, no en un timer ni cola de trabajos |
| 2026-08-15 | `POST /curation/paste`: un registro crudo por bloque separado con guiones |
| 2026-08-15 | Webhook de WhatsApp con HMAC-SHA512. **Apartamiento** de la decisión 2 de `CLAUDE.md` y del orden de fuentes de §6.1 |
| 2026-08-17, 2026-08-20 | Botón «Importar histórico» por canal, manual, 250 mensajes por consulta |
| 2026-08-18 | Los grupos entran por su propio endpoint, ruteado por sufijo del JID |
| 2026-08-21 | El webhook descarga la foto sin caption; sin foto no hay evidencia (§6.1) |
| 2026-08-21 | Registrar por qué se descarta cada mensaje, solo metadatos, nunca el cuerpo |

**Propuestos:**

- **N-01 · Contrato de entrada explícito.** CAP-04 y CAP-05 son vinculantes, pero
  los cuatro productores llegan a `raw_ingestions` por caminos distintos. Si el plan
  es integrar módulos, este contrato es el primero que hay que declarar.
- **N-02 · Alerta de ingesta detenida.** Ya ocurrió: la sesión de WAHA cayó a
  `FAILED` y toda la ingesta automática paró en silencio. Hoy solo hay un indicador
  pasivo que exige que alguien mire.
- **N-03 · Reproceso de descartados.** `discarded` es terminal: 373 anuncios no
  vuelven a mirarse aunque cambie el foco o mejore el extractor. Re-encolar por
  criterio, sin tocar la fila cruda.

## 02 · Curaduría

**Hoy:** cola con un elemento por vez, orden por aporte al foco, contexto de ráfaga,
sugerencia de descarte por regla, historial y edición en el lugar. Cerrada como
línea de desarrollo el 2026-08-22; sigue como rutina de dato.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| CUR-01 | Mostrar un elemento pendiente por vez | §6.1 | 0 | **Vinculante** |
| CUR-02 | Mostrar la imagen o el texto original | §6.1 | 0 | **Vinculante** |
| CUR-03 | Campos: producto, precio, moneda, cantidad, unidad, provincia, canal, fecha | §6.1 | 0 | **Vinculante** |
| CUR-04 | Permitir descartar lo que no tiene información económica útil | §6.1 | 0 | **Vinculante** |
| CUR-05 | Permitir marcar el elemento como parte del conjunto de referencia | §6.1 | 0 | **Vinculante** |
| CUR-06 | Sostener ≈1 h diaria; la captura no cuenta como tiempo de trabajo | §6.1, §9 | 0 | **Vinculante** |
| CUR-07 | La cola es a la vez herramienta y medición; sin sistema de evaluación aparte | §6.1 | 0 | **Vinculante** |
| CUR-08 | Cada confirmación o corrección genera un evento que alimenta H0 | §6.1 | 0 | **Vinculante** |
| CUR-09 | Etiquetar a mano un conjunto de referencia de al menos 300 anuncios reales | §3, §9 | 0 | **Vinculante** |
| CUR-10 | Interfaz de curaduría para carga manual con evidencia original | §9 | 0 | **Vinculante** |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-08 | Multi-producto: una fila por línea, tabla editable, contrato todo o nada |
| 2026-08-12 | `/curation/history` + edición en el lugar; el registro crudo nunca se toca |
| 2026-08-14 | `published_date` obligatoria y bloqueante, con cota en el día de captura |
| 2026-08-14 | `min_purchase_quantity` opcional; cuarto filtro del rango público |
| 2026-08-15 | El foco es `products.is_pilot`; el alta de productos sale a `/catalog` |
| 2026-08-17 | La provincia del canal reemplaza siempre el matching por texto |
| 2026-08-18, 2026-08-20 | La cola ordena por aporte al foco y esconde el texto fuera de foco; esconder no es descartar |
| 2026-08-21 | Mensajes vecinos del canal (±10 min) como contexto de solo lectura |
| 2026-08-22 | Sugerir descarte por regla explícita, nunca auto-descartar: §6.1 exige que el operador confirme |

**Propuesto:**

- **N-04 · Identidad del operador por acción.** `/curation` es una contraseña
  compartida y ninguna fila registra quién la curó. Si entra un segundo operador
  —lo que la medición de H0 vuelve necesario— no hay auditoría ni medición por
  persona.

## 03 · Extracción

**Hoy:** OCR y motor de reglas como asistentes de la cola, nunca autónomos.
Extracción completa correcta: **43,5%** sobre 278 filas de 175 anuncios.

El promedio esconde dos poblaciones muy distintas (medición del 2026-08-31,
por fila):

| | anuncios | filas | producto | precio | provincia | cant/unid | **completa** |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Texto | 91 | 180 | 92,8% | 72,8% | 98,9% | 73,9% | **64,4%** |
| Imagen | 84 | 98 | 72,4% | 24,5% | 53,1% | 19,4% | **5,1%** |
| Total | 175 | 278 | 85,6% | 57,2% | 82,7% | 54,7% | **43,5%** |

Consecuencia directa: **sumar anuncios en la misma proporción no mueve el
43,5%** — es el promedio ponderado de dos tasas fijas. El umbral de 300 de §3
vuelve la medición oficial, no mejor.

Dentro de las 98 filas de imagen, la culpa del fallo de precio se reparte: 35
(35,7%) fallan con el número correcto ya transcripto por el OCR —recuperables
con mejores reglas— y 39 (39,8%) fallan porque el OCR nunca produjo ese
número. No es «el techo es el OCR» a secas: es mitad y mitad.

Para que la extracción **completa** se mueva en la mitad de imagen, el cuello
no es el precio sino cantidad/unidad (19,4%) y provincia (53,1%): una fila
necesita los cuatro campos bien.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| EXT-01 | OCR y reglas como asistentes de la cola, no etapas autónomas | §6.1 | 0 | **Vinculante** |
| EXT-02 | El sistema propone; el operador confirma o corrige | §6.1 | 0 | **Vinculante** |
| EXT-03 | Evitar que el error de OCR se acumule sobre el de extracción sin supervisión | §6.1 | 0 | **Vinculante** |
| EXT-04 | Aceptar texto pegado y proponer producto, precio, moneda, cantidad, provincia | §6.1 | 0 | **Vinculante** |
| EXT-05 | Inferir la moneda cuando el anuncio no la declara, marcándola como inferida | §6.1 | 0 | **Vinculante** |
| EXT-06 | Reglas y diccionarios como primera línea | §6.1 | 0 | **Vinculante** |
| EXT-07 | LLM solo para lo que las reglas no resuelven, con confirmación humana | §6.1, §10.4 | 0 | **Vinculante** |
| EXT-09 | Extraer producto, precio, moneda, cantidad, ubicación, fecha y proveedor | §6.3 | todas | Prosa |
| EXT-10 | MVP con regex; a escala spaCy y modelos con lenguaje cubano | §6.3, §13.1 | 2 | Prosa |
| EXT-08 | Ejecutar el OCR sin confirmación humana solo donde la precisión lo justifique | §6.1 | 3 | **Vinculante** |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-07 | Casos sintéticos por regla; H0 corre exclusivamente contra los anuncios reales |
| 2026-08-07 | Alias de provincia como dato estático en el motor. **Roza §9.0** |
| 2026-08-08, 2026-08-20 | Modificadores que descalifican el match genérico (picadillo, muslo, alitas…) |
| 2026-08-17 | Reconocer «43.5 o 30885» y tomar el mayor: es el lado CUP |
| 2026-08-20 | Leer el anuncio como bloque cuando hay escalón de volumen y un solo producto |
| 2026-08-21 | Tesseract con `--psm 6`: la evidencia es una captura de chat, no una página |
| 2026-08-25 | Proponer `price_basis` cuando el anuncio lo declara. **Apartamiento** de D5/D4 |
| 2026-08-31 | **Descartado** excluir el número de "Sale X" de `_extract_price`: medido contra producción, regresa la precisión (55,8%→54,3%) en vez de mejorarla — la ambigüedad entre el precio del lote y el de "Sale" es real y depende del anuncio, no hay preferencia fija que acierte siempre |
| 2026-08-31 | Marca de moneda como sufijo ("850$") y número pelado como último recurso: precio 55,8%→57,2%. **Decisión del operador**: prefiere revisar una propuesta dudosa a que el motor se abstenga |

**Propuestos:**

- **N-05 · Versionado del extractor y H0 longitudinal.** `measure.py` da un número
  puntual y no queda registro por versión, así que no se puede afirmar que un cambio
  ayudó. Persistir cada corrida con su commit.
- **N-06 · Presupuesto de tiempo por anuncio.** Si la tesis es que hace falta un
  operador de ingesta, la métrica que gobierna es anuncios/hora y el reparto entre
  descartar y corregir. Hoy nada mide tiempo.

## 04 · Normalización y comparabilidad

**Hoy:** §5 llama a esta capa **el núcleo técnico y el activo transferible** del
proyecto. Existen `unit`, `sale_format`, `packaging`, `price_basis` y
`convert_price_per_unit()`. Pero `price_basis` quedó NULL en 202 de 225 filas
históricas y ese retro-completado sigue en Todo.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| EXT-11 | Normalizar unidades y declarar la comparabilidad: «aceite 1L» y «aceite 900ml» a una unidad común, o a dos declaradas como distintas | §5 | todas | **Vinculante** |
| EXT-12 | Tratar esta capa como el núcleo técnico y activo transferible del proyecto | §5 | todas | **Vinculante** |
| MET-09 | Si la normalización es el cuello de botella, acotar los productos bandera; no bajar el estándar | §12.5 | 0 | **Vinculante** |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-11 | Regla minorista por la prueba de la jabita: ¿existe una transacción a ese precio por esa unidad? |
| 2026-08-12 | `unit`, `sale_format` y `packaging` por fila; la jabita etiqueta, ya no descarta |
| 2026-08-12 | Mayorista es escala de contenedor; vender un saco completo sigue siendo minorista |
| 2026-08-12 | `packaging` con vocabulario cerrado; no participa en ningún cálculo |
| 2026-08-21 | `price_basis` obligatorio solo cuando `quantity != 1`; nunca inferido ni prellenado |
| 2026-08-22 | `detect_basis_suspects()` sin umbral: contrasta contra la mediana de las filas no ambiguas de la propia celda |
| 2026-08-22 | Una libra son 0,45359237 kg exactos: no es umbral de §12.6. Se aplica al precio por unidad, después de dividir |

---

# Grupo 2 — Calidad y confianza

## 05 · Validación — **ausente**

**Hoy:** el motor de §6.2 no existe. La columna `confidence_score` está en el
esquema con su CHECK 0-100 y poblada en cero filas. Las seis reglas están
especificadas y ninguna corre. Es ítem Todo del board desde el 2026-08-10.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| VAL-01 | Someter cada entrada a un pipeline de validación | §6.2 | todas | Prosa |
| VAL-02 | Orden: estructura → producto → precio → ubicación → usuario → duplicados → score | §6.2 | todas | Prosa |
| VAL-03 | Regla 1 — Estructura mínima: producto, valor numérico, unidad monetaria, contexto | §6.2 | 0 | Prosa |
| VAL-04 | Regla 2 — Coherencia de precio: 70% promedio provincial + 30% tendencia nacional | §6.2 | 0 | Prosa |
| VAL-05 | Regla 3 — Geográfica: provincias, municipios y regiones; aceptar zona sin provincia | §6.2 | todas | Prosa |
| VAL-08 | Regla 5 — Deduplicación por hash, similitud semántica, imagen, fecha y fuente | §6.2 | 0 | Prosa |
| VAL-09 | Regla 6 — Coherencia cantidad/precio contra historial, peso y unidad | §6.2 | todas | Prosa |
| VAL-10 | Emitir `validation_status`, `confidence_score` y el desglose por regla | §6.2 | todas | Prosa |
| VAL-11 | Enrutar según veredicto: Rechazado, Anomaly Review o Clean Market Dataset | §6 | todas | Prosa |
| VAL-12 | En Fase 0 activar las Reglas 1, 2, 4 y 5 | §9 | 0 | **Vinculante** |
| VAL-13 | En Fase 0 correr el motor en modo entrenamiento: propone, no decide | §9 | 0 | **Vinculante** |
| VAL-14 | Pasar el Validation Engine a modo activo | §9 | 2 | Prosa |
| VAL-15 | Alcanzar 90% de validación automática | §9 | 2 | Prosa |

Decisión relacionada: **2026-08-07** — reglas monetarias como CHECK de base;
`confidence_score` 0..100 declarado como lectura razonable del ejemplo de §6.2, no
como cita literal.

## 06 · Reputación de fuentes — **ausente**

**Hoy:** no hay tabla de usuarios, ni `trust_score`, ni eventos de reputación. §7
tampoco define dónde vivirían (hueco H-23). Es la precondición del módulo 13: sin
reputación no se puede abrir el botón «subir dato».

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| VAL-06 | Regla 4 — `trust_score` y `level`: 0-30 bloqueado, 31-60 normal, 61-80 confiable, 81-100 verificada | §6.2 | 0 | Prosa |
| VAL-07 | Evolución: confirmado +10, consistente +5, precio falso −20, spam −15, duplicado −5 | §6.2 | 0 | Prosa |
| PRI-02 | Guardar el identificador de usuario origen para reputación, nunca publicarlo | §10.6 | todas | **Vinculante** |
| MET-14 | Determinar los criterios de fuente de alta confianza por antigüedad y consistencia medidas | §12.6 | 2 | **Vinculante** |

Decisión relacionada: **2026-08-18** — guardar `participant` (el emisor dentro de un
grupo de WhatsApp) queda explícitamente fuera de alcance, aunque es la identidad que
§6.1 previó para reputación.

## 07 · Antimanipulación (C1–C4) — solo C2

**Hoy:** de las cuatro contramedidas, solo C2 corre — la mediana es la medida de
tendencia central de todos los agregados. C1, C3 y C4 no existen. §10.7 dice que las
cuatro se diseñan desde ahora aunque no todas bloqueen, y que **C3 debe estar
operativo antes** de habilitar el botón «subir dato».

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| ADV-05 | C2 — Mediana sobre media, recorte de extremos, MAD para atípicos | §10.7 | 0 | **Vinculante** |
| ADV-01 | Tratar la manipulación coordinada como riesgo de primer orden, distinto de §10.1 | §10.7 | todas | **Vinculante** |
| ADV-02 | Reconocer el ataque a la tendencia central: quien controla 50%+1 de una celda fija el valor | §10.7 | todas | **Vinculante** |
| ADV-03 | Cada celda producto × provincia es blanco independiente; la densidad baja abarata el ataque | §10.7 | todas | **Vinculante** |
| ADV-09 | Diseñar las cuatro contramedidas desde ahora, aunque no todas bloqueen desde el mes 1 | §10.7 | todas | **Vinculante** |
| ADV-10 | Declarar el alcance: elevar el costo del ataque y detectarlo, no alcanzar inmunidad | §10.7 | todas | **Vinculante** |
| ADV-04 | C1 — Ninguna celda bajo N observaciones produce valor publicado | §10.7 | 2 | **Vinculante** |
| ADV-11 | Las contramedidas son condición de publicación, no añadido posterior | §10.7 | 2 | **Vinculante** |
| ADV-06 | C3 — Índice de control paralelo; la divergencia sostenida dispara revisión | §10.7 | 2 | **Vinculante** |
| ADV-12 | C3 operativo antes de habilitar el botón, no después | §9 | 2 | **Vinculante** |
| ADV-07 | C4 — Detección por sincronía, similitud textual, concentración, cuentas nuevas | §10.7 | 2 | **Vinculante** |
| ADV-08 | Reservar de publicación los parámetros de C4 | §10.7, §6.4.1 | todas | **Vinculante** |

---

# Grupo 3 — Derivación

## 08 · Conversión monetaria

**Hoy:** elTOQUE TRMI en vivo con `exchange_rate_cache` como respaldo, los cuatro
campos persistidos por fila, y estado visible en `/control` sin salir a la red.
MON-05 sigue incumplido y está registrado como apartamiento deliberado.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| MON-01 | CUP como moneda base de las series publicadas | §6.3 | todas | **Vinculante** |
| MON-02 | Aceptar CUP, USD, EUR y MLC, incluso mezclados en el mismo mensaje | §6.3, §5 | todas | **Vinculante** |
| MON-03 | Tratar el tipo de cambio como dependencia externa declarada | §6.3, §5 | todas | **Vinculante** |
| MON-04 | Fuente primaria: TRMI de elTOQUE por su API pública | §6.3 | todas | **Vinculante** |
| MON-06 | Toda conversión guarda la tasa aplicada y su fecha, no solo el resultado | §6.3 | 0 | **Vinculante** |
| MON-07 | Ante fuente caída, usar la última tasa conocida con ventana de validez máxima | §6.3 | todas | **Vinculante** |
| MON-08 | Marcar la serie calculada con tasa fuera de ventana | §6.3 | todas | **Vinculante** |
| MON-09 | Atribuir visiblemente toda serie derivada de la TRMI | §6.3, §5 | todas | **Vinculante** |
| MON-10 | Guardar moneda y tasa desde el primer modelo, aunque en Fase 0 sea caso de borde | §6.3 | 0 | **Vinculante** |
| MON-11 | Persistir `price_cup`, `exchange_rate`, `exchange_rate_date`, `exchange_rate_source` | §7.2 | 0 | **Vinculante** |
| MON-12 | SAIMC consume la referencia de cambio; no la produce | §5 | todas | **Vinculante** |
| MON-05 | Configurar al menos una fuente de respaldo — **apartado el 2026-08-13**: no hay segunda fuente informal confiable; el caché es el único respaldo | §6.3 | todas | Apartado |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-13 | `/control` separa tasa Disponible de tasa Aplicada; cargar la vista no consulta la API |
| 2026-08-13 | Las celdas se funden por `price_cup`; lo que no tiene tasa queda aparte, marcado |

## 09 · Agregación

**Hoy:** `compute_cells`, `compute_retail_ranges`, `compute_availability` y las
series por día. Todo el módulo se construyó bajo una regla repetida: **mide, nunca
dictamina** — porque §12.6 prohíbe inventar los umbrales que un veredicto
necesitaría.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| INT-08 | Producir cientos de series: matriz producto × provincia, granularidad provincial | §5 | todas | **Vinculante** |
| INT-03 | Índice de Disponibilidad 0-100 sobre frecuencia, desaparición, volumen y menciones | §6.3, §7.3 | todas | Prosa |
| INT-11 | Mantener `availability_index` con `score`, `trend`, `calculated_at` | §7.3 | todas | Prosa |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-11 | `/control` resume en celdas con n, mediana, mín, máx y amplitud. Descarta marcar «muestra insuficiente» |
| 2026-08-12 | La celda se parte por producto × provincia × moneda × unidad × formato. El nulo no se funde con nada |
| 2026-08-12 | `compute_retail_ranges()` con tres filtros, ancla en `published_date`, ventana configurable |
| 2026-08-13 | Dividir cada fila por su cantidad antes de agregar; una fila sin cantidad no asume 1 |
| 2026-08-15 | **Cambio de criterio rector:** lo que gobierna Fase 0 es la densidad por celda, no el volumen |
| 2026-08-23 | Índice de disponibilidad como conteo, no score. **Apartamiento** de §7.3: la spec nunca dice cómo se calcula y §12.6 prohíbe inventar la escala |
| 2026-08-25 | `compute_channel_intake`: curados/descartados/pendientes por canal |

## 10 · Inteligencia predictiva — **ausente**

**Hoy:** nada construido, y es correcto que así sea: es lo que la medición de H0
bloquea. Un extractor al 43,5% no sostiene el volumen que un forecast necesita. El
módulo entra cuando el dataset histórico exista, no antes.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| INT-01 | Pasar de «¿cuánto cuesta?» a «¿qué probablemente ocurrirá?» | §6.3 | 3 | Prosa |
| INT-04 | Modelo de Riesgo Comercial: producto → dólar informal → importación → disponibilidad → riesgo | §6.3 | todas | Prosa |
| INT-05 | Grafo dinámico del mercado que conecte producto, provincia, precio, fuente, reputación | §6.6 | 3 | Prosa |
| INT-06 | Responder preguntas de segundo orden: «¿dónde conviene comprar esta semana?» | §6.6 | 3 | Prosa |
| INT-07 | Acumular relaciones históricas, comportamiento regional y estacionalidad | §6.6 | 3 | Prosa |
| INT-02 | Forecasting a 7 días por producto × provincia, con precio esperado y confianza | §6.3, §9 | 3 | Prosa |
| INT-10 | Mantener `forecasts` con `prediction_date`, `expected_price`, `confidence`, `model_version` | §7.3 | 3 | Prosa |
| INT-12 | Etiquetar la madurez del forecast: exploratoria, en desarrollo, consolidada | §11 | 3 | Prosa |
| MET-19 | 300+ productos con forecasting, 10.000 alertas/mes, precisión >85% | §12.4 | 3 | Prosa |

---

# Grupo 4 — Superficie pública

## 11 · Publicación y dashboard

**Hoy:** `GET /` con mapa SVG de las 16 provincias por estado de publicación,
tarjetas de rango, series por día, tres filtros combinables, CSV y URLs limpias. Los
cinco ítems de vista pública del board están cerrados. Faltan semáforo, alertas y
mapa coloreado por precio — los tres exigen umbrales que §12.6 prohíbe inventar.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| PUB-28 | Prototipo de mapa de calor y tarjetas de producto dentro del alcance de Fase 0 | §9 | 0 | **Vinculante** |
| PUB-13 | Ninguna celda muestra un precio como titular sin respaldo de muestra | §9.0 | 0 | **Vinculante** |
| PUB-12 | Por debajo del umbral, la provincia queda en ingesta sin publicación | §9.0 | todas | **Vinculante** |
| PUB-15 | Mostrar qué provincias están publicadas y cuáles en ingesta, con su progreso | §9.0 | todas | **Vinculante** |
| PUB-16 | Comunicar la ausencia de datos como decisión metodológica, no como falla | §9.0 | todas | **Vinculante** |
| PUB-17 | Cubrir 2-3 provincias elegidas por densidad esperada, no por importancia económica | §9.0 | 0 | **Vinculante** |
| PUB-19 | Que el costo marginal por provincia adicional tienda a cero | §9.0 | todas | **Vinculante** |
| PUB-20 | Medir la dispersión por categoría antes de decidir qué se destaca | §9.0 | todas | **Vinculante** |
| PUB-21 | H0 no condiciona el lanzamiento del dashboard público | §3 | 1 | **Vinculante** |
| PUB-25 | Cada celda muestra su tamaño de muestra; al cierre de la fase se fija N | §9, §9.0 | 1 | **Vinculante** |
| PUB-18 | Los números de provincia de fases siguientes son techos de ingesta, no metas | §9.0 | 1 | **Vinculante** |
| PUB-01 | Que cualquier cubano entienda en 5 segundos la situación de su producto | §6.7 | 1 | Prosa |
| PUB-02 | Mapa de calor: verde bajo el promedio, amarillo en el promedio, rojo encima, gris sin datos | §6.7 | 1 | Prosa |
| PUB-03 | Hacer clic en una provincia para ver el detalle | §6.7 | 1 | Prosa |
| PUB-04 | Tarjeta: precio actual, promedio semanal, tendencia, recomendación, disponibilidad | §6.7 | 1 | Prosa |
| PUB-05 | Semáforo de tendencia sin números: bajando / estable / subiendo | §6.7 | 1 | Prosa |
| PUB-06 | Gráfico a 30 días con banda mín-máx y predicción punteada a 7 días | §6.7 | 1 | Prosa |
| PUB-07 | Alertas: variación >10% en 24 h en naranja; disponibilidad bajo 30/100 en rojo | §6.7 | 1 | Prosa |
| PUB-08 | Responder en 3 s: ¿está caro?, ¿sube o baja?, ¿compro o espero? | §6.7 | 1 | Prosa |
| PUB-22 | Dashboard público con mapa de calor y tarjetas con semáforos | §9 | 1 | Prosa |
| PUB-23 | SAIMC Market Report como primer producto pagador, a $20 | §9 | 1 | Prosa |
| PUB-24 | Expandir la ingesta a 10 productos y 5 provincias; luego 50 y 10 | §9 | 1 | Prosa |
| PUB-10 | Publicar una provincia solo cuando supere y sostenga el umbral de densidad | §9.0 | 2 | **Vinculante** |
| PUB-11 | Condición en tres partes: volumen sobre N, sostenido, sobre un conjunto de categorías | §9.0 | 2 | **Vinculante** |
| PUB-14 | C1 y C4 pasan a bloqueantes al abrir la ingesta comunitaria | §9.0, §10.7 | 2 | **Vinculante** |
| PUB-09 | API Layer que alimente dashboard, API premium y datasets | §6 | 3 | Prosa |
| PUB-27 | API pública con plan Developer $15/mes y Professional $50/mes | §9 | 3 | Prosa |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-22 | **Apartamiento de §13.1:** SVG inline de las 16 provincias, no MapLibre GL — un motor de mapas mete dependencia de red en runtime |
| 2026-08-22 | El mapa colorea estado de publicación, no cobertura; el conjunto sale de la base, no de una constante |
| 2026-08-22 | El titular es el rango mín-máx, nunca un valor puntual; toda tarjeta va rotulada «muestra en formación» |
| 2026-08-23 | **Apartamiento de §6.7:** sin línea entre los puntos — con dato en 11 de 30 días, una línea continua dibuja precios que nadie observó |
| 2026-08-26 | **Apartamiento de §9:** `GET /datos.csv`, ninguna columna personal |
| 2026-08-26 | `?producto=`, `?provincia=`, `?dias=` combinables; `/producto/<slug>` como URL limpia |

## 12 · Transparencia y linaje — parcial

**Hoy:** `GET /anuncio/{id}` publica la evidencia **solo de texto**: el 40% que es
imagen sigue sin ruta pública, porque los píxeles traen el teléfono. Y de las doce
publicaciones que §6.4.1 exige —fórmulas, umbrales, reglas, ventanas, métricas de
calidad— **no hay ninguna página que las publique**. Es el requisito vinculante más
grande sin arrancar en toda la superficie pública.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| TRA-21 | Que cada precio publicado sea rastreable hasta el anuncio que lo originó | §5 | todas | **Vinculante** |
| TRA-22 | Exponer el anuncio original visible detrás de cada precio | §5 | todas | **Vinculante** |
| TRA-23 | Modelo de apertura: código, esquema y datos agregados abiertos | §5 | todas | **Vinculante** |
| TRA-03 | Kerckhoffs: resistir manipulación aunque toda la metodología sea pública | §6.4.1 | todas | **Vinculante** |
| TRA-04 | Publicar las fórmulas de agregación y el cálculo del índice, con su justificación | §6.4.1 | todas | **Vinculante** |
| TRA-05 | Publicar los umbrales de validación y densidad, con los valores vigentes | §6.4.1 | todas | **Vinculante** |
| TRA-06 | Publicar las reglas del motor de validación | §6.4.1 | todas | **Vinculante** |
| TRA-07 | Publicar los criterios de normalización y agrupamiento de productos | §6.4.1 | todas | **Vinculante** |
| TRA-08 | Publicar las ventanas temporales de cálculo y la tasa de cambio aplicada | §6.4.1 | todas | **Vinculante** |
| TRA-09 | Publicar los canales y tipos de fuente monitoreados, a nivel de categoría | §6.4.1 | todas | **Vinculante** |
| TRA-10 | Publicar métricas de calidad: precisión, volumen válido, cobertura, tasa de rechazo | §6.4.1 | todas | **Vinculante** |
| TRA-11 | Publicar el código fuente del pipeline | §6.4.1 | todas | **Vinculante** |
| TRA-12 | Publicar los datos agregados con su historial de versiones | §6.4.1 | todas | **Vinculante** |
| TRA-13 | Reservar únicamente los parámetros de C4 | §6.4.1 | todas | **Vinculante** |
| TRA-14 | Declarar públicamente que esa reserva existe, qué hace y por qué | §6.4.1 | todas | **Vinculante** |
| TRA-15 | Documentar los valores reservados y abrirlos a auditoría bajo acuerdo | §6.4.1 | todas | **Vinculante** |
| TRA-16 | Revisar la política si la reserva resulta insuficiente o excesiva | §6.4.1 | todas | **Vinculante** |
| TRA-01 | Cadena visible por dato: fuente → extraído → validaciones → resultado | §6.4 | todas | Prosa |
| TRA-02 | Pirámide de cinco niveles: fuente, estructurados, métricas, predicciones, premium | §6.4 | todas | Prosa |
| TRA-18 | Todo dato responde: de dónde salió, cuándo, qué procesos atravesó, por qué se confía | §6.5 | todas | Prosa |
| TRA-20 | Objeto de trazabilidad por registro con fuente, original, extraído y validación | §6.5 | todas | Prosa |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-25 | `GET /anuncio/{id}` sin auth, solo TEXT y solo CURATED; cualquier otro caso da el mismo 404 |
| 2026-08-25 | Descartado publicar imagen redactada: tapar píxeles exige OCR/ML, fuera de alcance en Fase 0 |

**Propuesto:**

- **N-07 · Página de metodología.** §6.4.1 es vinculante y enumera doce
  publicaciones obligatorias. Hoy no existe ninguna.

## 13 · Contribución comunitaria — **ausente**

**Hoy:** analizado el 2026-08-15 y no construido a propósito. Se descartó el
marketplace por colisionar con §5, §10.6 y §10.7, y se propuso en su lugar un canal
donde un tercero reporta un precio que vio, con evidencia, entrando a la misma cola.
Bloqueado río arriba por el módulo 06 y por C3 del módulo 07.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| CAP-15 | Botón «subir dato», sujeto al Validation Engine y a la reputación | §6.1, §9 | 2 | **Vinculante** |
| PUB-26 | Activar el botón «Subir dato» para usuarios | §9 | 2 | Prosa |
| MET-21 | 5.000 datos/mes, 500 usuarios registrados, 90% validación automática | §9 | 2 | Prosa |

Decisiones relacionadas:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-15 | Canal de contribución con evidencia, no marketplace: el contribuyente es testigo, no parte interesada. El operador sigue siendo el filtro |
| 2026-08-15 | Con qué barrera se abre una ruta de escritura pública queda declarado como cuestión previa a implementar nada |

---

# Grupo 5 — Transversales

## 14 · Privacidad

**Hoy:** los teléfonos se redactan en ingesta y curaduría, sobre texto e imagen por
igual. El límite es preciso y está escrito: **se redacta la columna, no el archivo.**
El `content_hash` se calcula sobre el archivo original, así que la deduplicación
sigue funcionando — y los píxeles siguen mostrando el número.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| PRI-03 | No capturar ni almacenar teléfonos, direcciones exactas ni datos de contacto | §10.6 | todas | **Vinculante** |
| PRI-02 | Seudonimización en la capa pública | §10.6 | todas | **Vinculante** |
| PRI-01 | Orden de fuentes por riesgo creciente | §10.6 | todas | **Vinculante** |
| PRI-05 | La curaduría primero permite construir producto antes de asumir exposición legal | §10.6 | 0 | **Vinculante** |
| PRI-04 | Consulta legal formal antes de la Etapa 3, no antes | §10.6 | 3 | **Vinculante** |
| PRI-10 | Cubrir error, ruido y spam con validación en capas + reputación + anomalías | §10.1 | todas | Prosa |
| PRI-09 | Costos de IA con estrategia híbrida: LLM solo para casos complejos | §10.4 | todas | Prosa |
| PRI-08 | Mitigar dependencia de plataformas externas con arquitectura multi-fuente | §10.5 | todas | Prosa |
| PRI-06 | Mitigar ataques dirigidos con WAF, rate limiting y geo-bloqueo | §10.2 | todas | Prosa |
| PRI-07 | Mitigar el riesgo regulatorio con modelo offshore | §10.3 | todas | Prosa |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-11 | La redacción se aplica en `ingest_file` y en la curaduría: se redacta la columna, nunca `data/raw/` |
| 2026-08-13 | **Apartamiento de la decisión 5:** HTTP Basic sobre `/curation` y `/evidence`, porque exponían capturas crudas. Cierra en falso sin contraseña |
| 2026-08-19 | `Channel.address` es uso interno: §10.6 habla del anunciante individual, no del local que ya se publicita |

## 15 · Métricas y observabilidad

**Hoy:** H0 se mide re-corriendo el extractor sobre evidencia inmutable, incluidas
las imágenes. `/control` tiene progreso contra la meta de §9, ritmo, huecos,
cobertura y salud de la ingesta. La tasa de descarte objetivo es 30% y hoy está en
**67,8%** — declarada en código (`progress.DISCARD_RATE_OBJECTIVE`), incumplida por
más del doble.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| MET-23 | Criterio de salida de Fase 0: «H0 quedó respondida con un número» | §9 | 0 | **Vinculante** |
| MET-01 | Medir las reglas contra el conjunto de referencia de 300+ anuncios reales | §3, §12.5 | 0 | **Vinculante** |
| MET-02 | Producir la primera medición de precisión como entregable de Fase 0 | §9 | 0 | **Vinculante** |
| MET-22 | 500 registros validados de 3-5 productos en 2 provincias | §9 | 0 | **Vinculante** |
| MET-03 | Precisión de producto: mínimo 85%, objetivo >92% | §12.5 | 0 | **Vinculante** |
| MET-04 | Precisión de precio: mínimo 90%, objetivo >95% | §12.5 | 0 | **Vinculante** |
| MET-05 | Precisión de provincia: mínimo 80%, objetivo >90% | §12.5 | 0 | **Vinculante** |
| MET-06 | Precisión de cantidad/unidad: mínimo 70%, objetivo >85% | §12.5 | 0 | **Vinculante** |
| MET-07 | Extracción completa correcta: mínimo 70%, objetivo >85% | §12.5 | 0 | **Vinculante** |
| MET-08 | Tasa de descarte: objetivo <30% | §12.5 | 0 | **Vinculante** |
| MET-10 | No inventar el valor de ningún umbral de la v3.5; medirlo y documentarlo | §12.6 | todas | **Vinculante** |
| MET-11 | Determinar N por estabilidad de la mediana frente al tamaño de muestra | §12.6 | 1 | **Vinculante** |
| MET-12 | Determinar el período mínimo de sostenimiento por la variabilidad del volumen | §12.6 | 1 | **Vinculante** |
| MET-13 | Determinar las categorías mínimas por provincia por la cobertura observada | §12.6 | 1 | **Vinculante** |
| MET-15 | Determinar la ventana de validez de la tasa por la frecuencia real de la fuente | §12.6 | 1 | **Vinculante** |
| MET-17 | Calidad: >90% aprobados, >95% precisión validada, >80% duplicados, score >85 | §12.2 | 2 | Prosa |
| MET-16 | Año 1: 1.000.000+ registros, 500+ productos, 16 provincias, 100+ fuentes | §12.1 | 3 | Prosa |
| MET-18 | Comerciales: 50.000 registrados, 10.000 activos, 100 premium | §12.3 | 3 | Prosa |
| MET-25 | Tabla de veredicto de H0 — **apartada el 2026-08-12**; los umbrales quedan como referencia de capacidad de escala | §3 | 0 | Apartado |
| MET-24 | «Si la precisión cae por debajo del 70%, la Fase 1 no arranca» — **apartada el 2026-08-12** | §9 | 0 | Apartado |
| MET-26 | Que las métricas de extracción sean las únicas relevantes en Fase 0 — **apartada el 2026-08-12** | §12.5 | 0 | Apartado |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-11 | H0 mide también imágenes, re-corriendo el OCR sobre la evidencia original, nunca sobre el texto ya corregido |
| 2026-08-13 | `/control` compara solo contra objetivos del documento; la extrapolación se presenta como «a este ritmo», no como fecha |
| 2026-08-21 | El conjunto de referencia se cuenta en **anuncios**, no en filas: las filas de un anuncio comparten evidencia y fallan juntas |

**Propuesto:**

- **N-08 · Restaurar el backup, no solo hacerlo.** Hay `pg_dump` por timer desde el
  2026-08-10; un backup que nunca se restauró no es un backup.

## 16 · Catálogo y administración

**Hoy:** `/catalog` administra productos piloto, unidad, canales, provincia por
canal, dirección, alta/baja y buscador. Regla sostenida: **nada se borra si ya
capturó algo** — se apaga, porque los registros crudos siguen refiriéndose a él.

| Id | Requisito | Fuente | Fase | Fuerza |
| --- | --- | --- | --- | --- |
| DAT-01 | `raw_ingestions` con el DDL dado, más índice único por hash | §7.1 | 0 | **DDL** |
| DAT-02 | `products` como catálogo maestro con nombre, categoría, unidad y alias | §7.2 | 0 | **DDL** |
| DAT-03 | `prices_history` como serie temporal principal con linaje y conversión | §7.2 | 0 | **DDL** |
| DAT-04 | `provinces` dentro del modelo de datos mínimo | §9 | 0 | **Vinculante** |
| DAT-05 | La incorporación de una provincia debe ser configuración, no desarrollo | §9.0 | todas | **Vinculante** |
| DAT-06 | Mantener el estado de publicación por provincia como dato consultable | §9.0 | todas | **Vinculante** |
| FAS-03 | 3-5 productos bandera | §9 | 0 | **Vinculante** |
| FAS-04 | 2 provincias: La Habana y Santiago de Cuba | §9 | 0 | **Vinculante** |
| STK-01 | Fase 0-1: Python de extremo a extremo, un runtime, un despliegue | §13.1 | 0 | Cerrada |
| STK-03 | PostgreSQL, con TimescaleDB cuando la serie lo justifique | §13.1 | 0 | Cerrada |
| STK-04 | Curaduría en Jinja2 + HTMX; no justifica una SPA | §13.1 | 0 | Cerrada |
| STK-06 | Sin cola de trabajos en Fase 0; Redis + Celery en Fase 1 | §13.1 | 1 | Cerrada |
| STK-07 | Angular diferido a Fase 2, contra una API ya estabilizada | §13.1 | 2 | Cerrada |
| STK-09 | Escala media: RabbitMQ/Kafka, Angular, worker de forecasting separado | §13.2 | 3 | Prosa |

Decisiones que lo modifican:

| Fecha | Qué introduce |
| --- | --- |
| 2026-08-15 | `/catalog` con `is_pilot` y `channels`; sacar del foco no borra nada |
| 2026-08-19 | `raw_ingestions.channel_id` es la identidad real de origen; el nombre resuelve solo si matchea una fila |
| 2026-08-20 | Un canal se borra solo si nunca capturó nada; con historial, se apaga |

---

# Huecos

Cosas que la especificación nombra como necesarias y nunca define. No son omisiones
de este inventario: §12.6 prohíbe inventarles un valor, así que quedan abiertas
hasta que se midan.

| Id | Hueco | Dónde queda abierto |
| --- | --- | --- |
| H-01 | Cómo se recorta o redacta una captura para publicación | §6.1 vs §10.6 — la contradicción abierta más vieja |
| H-06 | El valor de ningún umbral introducido en la v3.5 | §12.6 — prohíbe inventarlos |
| H-07 | Qué fuente alternativa de tipo de cambio respalda a la TRMI | §6.3 exige una y no nombra ninguna |
| H-08 | Cómo se calcula el score 0-100 del Índice de Disponibilidad | §6.3, §6.7, §7.3 |
| H-09 | Cómo se calcula el Modelo de Riesgo Comercial | §6.3 |
| H-10 | Ninguna fase asignada al Índice de Disponibilidad ni al Riesgo Comercial | §6.3 vs §9 — ambiguo |
| H-11 | Qué es «tendencia nacional» y sobre qué ventana se calcula | §6.2 Regla 2 |
| H-12 | Qué margen convierte un precio en anomalía | §6.2 Regla 2 |
| H-13 | Cómo se calcula `confidence_score` | §6.2, §6.5, §7.2 |
| H-14 | Umbral de similitud para deduplicar, y qué hace el sistema con un posible duplicado | §6.2 Regla 5 |
| H-15 | Cómo se detecta la inconsistencia de cantidad/precio | §6.2 Regla 6 |
| H-16 | Qué es «unidad monetaria suficiente» y «contexto suficiente» | §6.2 Regla 1 |
| H-17 | De dónde salen los usuarios de la reputación en Fase 0, si hay un solo operador | §6.2 vs §9 — ambiguo |
| H-18 | Cómo se define y se pobla el catálogo de categorías de producto | §9.0, §7.2 |
| H-19 | Cómo se mide la dispersión por categoría, y qué se hace con el resultado | §9.0 |
| H-20 | Qué es la «estimación derivada de modelo» de C1 y con qué modelo | §10.7 |
| H-21 | Qué es «divergencia sostenida» en C3, y qué hace la revisión que dispara | §10.7 |
| H-22 | Cómo se persiste y versiona el índice de control paralelo | §10.7 vs §7 |
| H-23 | Cómo se persiste la reputación: no hay tabla de usuarios ni de eventos | §6.2 vs §7 |
| H-24 | Dónde viven `Rejected Data` y `Anomaly Review` | §6 vs §7 |
| H-25 | Cómo se implementa el historial de versiones de los datos agregados | §6.4.1 |
| H-26 | Cómo se persiste el grafo de inteligencia de mercado | §6.6 vs §7, §13.1 |
| H-27 | Cómo se marca «parte del conjunto de referencia» en el modelo | §6.1 vs §7.1 — sigue en Todo |
| H-29 | Dónde se persiste `provider`, que §6.3 lista como entidad extraída | §6.3 vs §7.2 — sigue en Todo |
| H-30 | Cómo se mapea la Etapa 2 de §6.1 con la Fase 0 de §9 | Ambiguo |
| H-31 | Qué umbral exactamente habilita la Etapa 3 | §6.1, §3, §12.5 — agravado por el apartamiento del 2026-08-12 |
| H-32 | Ventana de cálculo de las series publicadas | §6.4.1, §10.7 |
| H-33 | Qué es el «promedio nacional» del mapa, y qué margen separa los tres colores | §6.7 |
| H-34 | Qué significa «datos suficientes» para el gris del mapa | §6.7 vs §12.6 |
| H-35 | Cómo se genera la recomendación de compra de la tarjeta | §6.7 |
| H-36 | Sobre qué ventana se calcula la variación >10% en 24 h | §6.7 |
| H-37 | Cómo se autentica al contribuyente y cómo nace su `trust_score` | §6.1, §6.2 |
| H-38 | Qué contiene el SAIMC Market Report | §9 — solo se nombra |
| H-39 | Qué distinguen los planes Developer y Professional, y qué expone la API | §9 — solo se nombran |
| H-40 | Si la Isla de la Juventud es una provincia más o un caso aparte | §12.1 vs §9.0 — ambiguo |
| H-41 | Cuándo se activa TimescaleDB | §13.1 — «cuando lo justifique», sin criterio |
| H-42 | Cómo conviven las métricas de §12.2 con las de §12.5 | Ambiguo — miden cosas distintas con nombres parecidos |
| H-45 | Publicar la evidencia de imagen: el 40% de lo curado sigue sin ruta pública | 2026-08-25 |
| H-46 | El extractor toma el número de «Sale X» en vez del precio de la oferta | 2026-08-25 — slice propio |
| H-47 | Por qué no entran las fotos sin caption que el código ya debería capturar | 2026-08-21 |
| H-48 | Una fila IMAGE con `original_text` deja el formulario vacío | 2026-08-21 — latente |
| H-49 | Los máximos de las series siguen envenenados por `price_basis` mal curado | 2026-08-22 |

---

# Deuda encontrada al hacer este inventario

**Marcadores de conflicto commiteados en `docs/decisions.md`.** Dos bloques tienen
`<<<<<<< HEAD`, `=======` y `>>>>>>> umbral-en-anuncios` sin resolver. La entrada del
2026-08-23 los menciona y decide no tocarlos. Revisadas las dos ramas del conflicto:
ninguna entrada contradice a otra — el conflicto es de posición en el archivo, no de
contenido, y se resuelve conservando ambos lados en orden cronológico.

**`docs/spec-map.md` quedó corto en sus apartamientos.** Su tabla lista cuatro.
Faltan al menos tres posteriores, todos registrados en `decisions.md`: el gráfico de
evolución en SVG servidor sin línea (§13.1 y §6.7, 2026-08-23), el índice de
disponibilidad como conteo y no score (§7.3, 2026-08-23) y la descarga CSV (§9,
2026-08-26).

**`N-09`** de la lista de propuestos es resolver el primero de los dos.
