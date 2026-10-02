# Slice A5 — Motor de validación §6.2 y carriles

Parte del plan `docs/plan-factor-humano.md` (A5a especifica; A5b lo programa en
código puro; A5c lo corre en sombra; B1 lo persiste; B2 lo enciende).
Toca el motor de validación: esta especificación se aprueba antes de programar.

**Estado: aprobada por el operador el 2026-10-02**, con las seis respuestas por
defecto de «Preguntas al operador».

## Problema

1. No existe motor de validación. `prices_history.confidence_score` está NULL en
   las 368 filas y no hay `validation_status` ni procedencia.
2. Para que el operador deje de tocar cada anuncio hace falta una regla escrita,
   única y medible que diga qué anuncio se descarta solo (carril 0), cuál se
   acepta solo (carril 1) y cuál va a revisión (carril 2).
3. La vía rápida (2026-10-02) prevé encender el carril 1 antes de llegar al n
   del modo sombra. El motor, la auditoría y el freno tienen que servir con n
   chico desde la primera semana.

## Hechos de partida

Del mapa del código y de la base local del 2026-10-02.

**Lo que `spec.md` §6.2 define y lo que no.**
- Seis reglas: estructura, coherencia del precio, geográfica, reputación,
  deduplicación, cantidad/precio.
- La regla 2 da una fórmula («70% promedio provincial + 30% tendencia nacional»)
  pero ningún umbral entre VALIDADO y ANOMALÍA. Las reglas 5 y 6 no traen
  fórmula ni umbral.
- El «Resultado de validación» trae cinco claves (`structure`, `price`,
  `location`, `trust`, `duplicate`): no hay clave para la regla 6.
- El único estado literal es `"approved"`. No hay lista de estados ni pesos para
  `confidence_score`.
- §12.6: ningún umbral se inventa; se mide y se registra.
- §6.1: OCR y extracción son «asistentes de la cola», «no etapas autónomas».

**Código que se reusa.**
- `llm_checks.verify_evidence` y `compare_with_rules` son puras.
  `compare_with_rules` no compara la provincia.
- `price_charts.robust_fence` es pura (log-IQR×3, mínimo ×2), declarada «de
  dibujo, no de publicación».
- `cells.detect_basis_suspects` consulta la base y no devuelve el id de la fila.
- El duplicado al ingerir es por hash exacto del archivo (`inbox.compute_hash`).

**Evidencia que ya existe, y cuánta es de verdad.**
- `llm_reviews` tiene 418 revisiones, pero 406 son los descartes del lote de
  imágenes (`batch_model`), aceptados con las miniaturas marcadas por defecto.
  No son evidencia independiente. Quedan 12 revisiones individuales (11 curadas,
  1 descartada), todas con `discard = false` del modelo.
- El oro del backtest v6 (100 anuncios, nunca vistos por el asistente) es la
  única evidencia independiente del descarte: el modelo dijo «descartar» en 49 y
  se equivocó en 1 (una imagen). Cota superior al 95%: 9,3%. En el otro sentido,
  de 51 que el modelo quiso conservar, 5 eran descartes (no afecta al carril 0).
- **Consecuencia:** G1a no se puede decidir «con las 418 revisiones». Se decide
  con el oro, que hoy da n = 49 y una cota de 9,3% antes de aplicar la segunda
  condición del carril 0. El plan lo decía más optimista de lo que es.

## Qué se construye

### El motor: código puro, sin base y sin red

`saimc/validation/engine.py`. Una función recibe todo lo que necesita ya leído
y devuelve el resultado. No consulta la base, no llama a ningún proveedor y no
escribe nada (A5b). Quien lo llama arma el contexto: el informe de sombra (A5c),
el guardado (B1) y el pre-cálculo (B2).

**Entrada (`AdContext`).**
- el anuncio: tipo de contenido, texto (o transcripción del OCR), canal, fecha;
- la propuesta del modelo con su metadato por fila (`evidence_found`,
  `price_in_evidence`, `currency_explicit`), o ninguna;
- la propuesta de las reglas;
- por cada fila, los precios de referencia de su celda **anteriores a la fecha
  del anuncio**, y las filas del mismo canal en las 24 h previas;
- el historial del canal;
- las señales externas, si hay.

**Salida.**
- por fila: el resultado de cada regla, `validation_status` y
  `confidence_score`;
- por anuncio: el carril (0, 1 o 2) y la lista de motivos legibles que lo
  explican.

Cada regla devuelve tres valores, no dos: `pass`, `fail` o `unknown` (no hay con
qué evaluarla). **`unknown` nunca aprueba.**

### Las seis reglas

| # | Regla | `pass` | `fail` | `unknown` |
| --- | --- | --- | --- | --- |
| 1 | Estructura | producto piloto del catálogo, precio > 0, moneda y provincia presentes | falta alguno | — |
| 2 | Coherencia del precio | el precio normalizado cae dentro de la banda robusta de su celda | cae fuera | la celda tiene menos referencias que `min_cell_n` |
| 3 | Geográfica | la provincia es del catálogo y coincide con la del canal | no es del catálogo | el canal no tiene provincia, o difiere de la de la fila |
| 4 | Reputación (canal) | el canal tiene historial y está limpio | historial sucio | canal sin historial suficiente |
| 5 | Duplicado | no hay fila del mismo canal, producto, provincia y precio en las 24 h previas | la hay | — |
| 6 | Cantidad y precio | la lectura de `price_basis` es la más cercana a la referencia de la celda | la otra lectura queda más cerca | sin referencia en la celda |

Precisiones:

- **Regla 2 — banda robusta, no la fórmula 70/30.** Se usa `robust_fence` sobre
  los precios de la celda (producto × provincia × unidad base, con repliegue a
  todas las provincias), con la misma normalización que `cells.compute_cells`.
  Solo entran referencias anteriores al anuncio. Es un apartamiento de §6.2
  regla 2 y del comentario «de dibujo» de `robust_fence`: ver «Contraste».
- **Regla 3.** `compare_with_rules` suma la provincia a lo que compara. Una
  provincia válida pero distinta de la del canal no es un error: es `unknown`,
  y va a revisión.
- **Regla 4, a nivel canal.** «Historial» son las decisiones individuales del
  operador sobre anuncios de ese canal. «Limpio» se mide con la fracción de
  filas propuestas que el operador editó o borró. El mínimo de historial y el
  tope de esa fracción quedan sin fijar. Por vendedor, cuando exista B5.
- **Regla 5.** Se suma al hash exacto de la ingesta, que sigue igual. No hay
  similitud semántica ni de imagen: la spec las nombra sin umbral.
- **Regla 6.** `detect_basis_suspects` se refactoriza: el cálculo pasa a una
  función pura que evalúa una fila contra la referencia de su celda, y la
  función actual la llama. El comportamiento de `/control` no cambia.

### Estados y puntaje

`validation_status`, por fila. La spec solo trae `approved`; el resto se define
acá.

| Estado | Cuándo |
| --- | --- |
| `approved` | las seis reglas en `pass` |
| `incomplete` | falla la regla 1 |
| `anomaly` | falla la regla 2 |
| `inconsistent` | falla la regla 6 |
| `possible_duplicate` | falla la regla 5 |
| `untrusted_source` | falla la regla 3 o la 4 |
| `unverified` | ninguna falla, pero alguna quedó en `unknown` |

Si falla más de una, gana la primera en el orden de la tabla; el detalle guarda
todas.

`confidence_score` (0–100): reglas en `pass` sobre seis, por cien. Es
**descriptivo**. Ningún carril lo usa y no se compara contra ningún umbral: la
spec no da pesos y §12.6 prohíbe inventarlos. Los carriles se deciden por
predicados.

El detalle sigue el JSON de §6.2 con una clave más, `quantity_price`, y con
`pass|fail|unknown` en lugar de `true|false`.

### Los carriles

El motor es el único que decide el carril. Un anuncio sin propuesta del modelo
(sin Claude, o propuesta degradada) va siempre al carril 2.

**Carril 0 — descarte automático.** Todas:
1. la propuesta está `ok` y dice `discard = true` con motivo `off_focus` o
   `no_data`;
2. las reglas no encuentran ningún producto piloto en el texto.

**Carril 1 — aceptación automática.** El anuncio es de texto, la propuesta está
`ok`, no descarta, no tiene filas caídas (`dropped`) y **todas** sus filas
cumplen:
1. cita verificada: `evidence_found` y `price_in_evidence`;
2. reglas y modelo coinciden en producto, precio, moneda y provincia;
3. ninguna abstención en cantidad, unidad ni `price_basis`;
4. las seis reglas en `pass`.

Una sola fila que no cumple manda el anuncio entero al carril 2.

**Carril 2 — revisión humana.** Todo lo demás.

### Señales externas

Aditivas y desconectables. La calibración base se hace sin ellas.

- La señal responde solo preguntas cerradas cuyas opciones salen de los
  catálogos del sistema (producto piloto, moneda, provincia, formato de venta).
- El motor la lee como acuerdo o desacuerdo con el valor extraído, más la
  probabilidad. Un desacuerdo con probabilidad alta saca el anuncio del carril 1;
  una señal nunca mete un anuncio en un carril automático.
- No hay puntaje global de confianza ni preguntas sobre el precio.
- Una respuesta ausente, tardía o de tipo inesperado equivale a «sin señal».
- No se supone ningún proveedor. Jev, si pasa A6, entra por A6b con este
  contrato.

### Parámetros, todos «sin fijar»

| Parámetro | Qué gobierna | Dónde se fija |
| --- | --- | --- |
| `min_cell_n` | referencias mínimas para que la regla 2 y la 6 opinen | A5c |
| `channel_min_history` | decisiones mínimas para que la regla 4 opine | A5c |
| `channel_max_edit_rate` | tope de filas editadas para «canal limpio» | A5c |
| meta de falsos descartes | carril 0 | G1a, la elige el operador |
| meta de error | carril 1 | G1b, la elige el operador |
| probabilidad «alta» de una señal | cuándo un desacuerdo saca del carril 1 | A6b |

Viven en una configuración única. A5b los recibe como argumentos: el motor no
trae ningún valor por defecto, y sin configuración todo anuncio va al carril 2.

### Protocolo del modo sombra

- **Calibración** sobre el oro del backtest; **evaluación** sobre las revisiones
  nuevas. No se mezclan.
- **Qué cuenta como evidencia:** solo decisiones individuales del operador
  (`curation_events.mode = individual`) y lo rescatado de un lote. Un descarte
  aceptado en lote no cuenta.
- **Métricas**, cada una con su n y su cota superior de Clopper-Pearson al 95%:
  - tasa de automatización (carriles 0 y 1 sobre el total);
  - error del carril 1, por anuncio y por campo;
  - falsos descartes del carril 0, separados por texto e imagen.
- **Qué es un error del carril 1:** el operador editó o borró algún campo de
  alguna fila, o agregó una fila.
- **Mínimo para informar:** no hay n mínimo; un porcentaje nunca se informa sin
  su n y su cota. Referencia: 0 errores en 60 da una cota de 4,9%; 0 en 30, 9,5%;
  3 en 150, 5,1%.
- Los precios de referencia son los de la fecha del anuncio.

### Auditoría y freno con el carril 1 adelantado

Para B2b y B2c, si el operador enciende el carril 1 antes del n.

- **Auditoría total al arranque.** Mientras lo auditado no llegue a `n0`, el
  operador audita todas las filas automáticas, en lote, después de publicadas.
  Desde `n0`, una muestra aleatoria semanal estratificada por canal.
- **Freno con n chico.** La cota superior no sirve de freno al principio (0
  errores en 10 ya da 26%). El carril 1 se apaga solo cuando la cota **inferior**
  al 95% del error auditado supera la meta, es decir, cuando hay evidencia de
  que el error es mayor que la meta. Desde `n0` rige además la cota superior,
  como pide el plan.
- `n0` y la meta los elige el operador en G1b.

## Contraste con `spec.md`

| Qué | De qué se aparta | Motivo |
| --- | --- | --- |
| Banda robusta en la regla 2 | §6.2 regla 2 (fórmula 70/30) | la fórmula no trae umbral y pide una «tendencia nacional» que no existe; la banda ya existe y es robusta a valores extremos (§10.7 C2) |
| `robust_fence` para validar | su comentario «de dibujo, no de publicación» | se reusa el cálculo; el comentario se actualiza en A5b |
| Clave `quantity_price` y valores `pass/fail/unknown` | JSON de §6.2 | la spec no da clave para la regla 6 ni cómo decir «no se pudo evaluar» |
| Estados propios | la spec solo trae `approved` | hacen falta para que el operador sepa por qué algo fue a revisión |
| Reputación por canal | §6.2 regla 4 (por usuario, con `trust_score`) | el emisor no se guarda (§10.6) hasta B5 |

Encender los carriles se aparta de §6.1 y, el carril 1, de §10.7. Eso no se
decide acá: es de G1a y G1b.

Sin apartamiento: §12.6 (ningún umbral se fija en este documento), §10.6 (el
motor no ve emisores), §7.1 (`raw_ingestions` no cambia).

## Qué no entra

- Código (A5b), el informe (A5c), columnas nuevas (B1), encender algo (B2).
- La regla 4 por vendedor (B5) y los controles C3 y C4 de §10.7 (slice C3).
- Similitud semántica o de imagen para la regla 5.
- Imágenes en el carril 1: entran con C2.

## Descartado

- **Un puntaje con pesos y un umbral único.** La spec no da pesos; inventarlos
  viola §12.6 y esconde por qué se aceptó una fila.
- **Fórmula 70/30.** Ver «Contraste».
- **Un orquestador aparte para las señales externas.** El motor decide el carril.
- **Contar los descartes de lote como acuerdo humano.** Infla el acierto del
  carril 0 con decisiones que nadie miró una por una.
- **Valores por defecto en el motor.** Sin configuración explícita no automatiza.

## Casos esperados (se escriben antes que el código)

Catálogo del caso: pilotos arroz, pollo, aceite; canal «Mercado Habana»
(provincia La Habana, historial limpio); celda arroz × La Habana × lb con
referencias de 300 a 340 CUP y más de `min_cell_n`; celda pollo × La Habana × lb
con referencias de 400 a 460 CUP.

| # | Anuncio | Carril | Estado de la fila | Por qué |
| --- | --- | --- | --- | --- |
| 1 | «Arroz 320 CUP la libra, Habana»; modelo y reglas coinciden | 1 | `approved` | todo en `pass` |
| 2 | «Vendo iPhone 13, 450 USD»; modelo `off_focus`; reglas sin piloto | 0 | — | las dos condiciones |
| 3 | «Buenos días grupo»; modelo `no_data`; reglas sin piloto | 0 | — | las dos condiciones |
| 4 | «Arroz importado, escribir al privado»; modelo `no_data`; reglas encuentran arroz | 2 | — | las reglas ven un piloto |
| 5 | «Arroz 3200 CUP la libra» | 2 | `anomaly` | fuera de la banda |
| 6 | «Arroz 320 la libra» sin moneda; el modelo pone CUP con `currency_explicit = false` y las reglas no dan moneda | 2 | `unverified` | no coinciden en moneda |
| 7 | «Arroz 320 CUP», sin cantidad ni unidad | 2 | `unverified` | abstención en cantidad y unidad |
| 8 | «Arroz 320 CUP lb» y «Pollo 430 CUP lb» en un anuncio; el pollo sin cita verificada | 2 | arroz `approved`, pollo `unverified` | una fila no cumple |
| 9 | «Arroz 320 CUP lb» en un canal sin historial | 2 | `unverified` | regla 4 en `unknown` |
| 10 | «Arroz 320 CUP lb» en un canal con historial sucio | 2 | `untrusted_source` | regla 4 falla |
| 11 | «Arroz 320 CUP lb, Santiago» en el canal de La Habana | 2 | `unverified` | provincia distinta de la del canal |
| 12 | «Arroz 320 CUP lb» repetido a las 3 h en el mismo canal | 2 | `possible_duplicate` | regla 5 |
| 13 | El mismo anuncio a las 30 h | 1 | `approved` | fuera de la ventana |
| 14 | «Saco de arroz 100 lb, 32000 CUP», base `unit` | 2 | `inconsistent` | leído como total queda en la referencia |
| 15 | «Saco de arroz 100 lb, 32000 CUP», base `total` | 1 | `approved` | 320 por libra |
| 16 | «Aceite 900 CUP el litro»; celda con menos de `min_cell_n` | 2 | `unverified` | reglas 2 y 6 en `unknown` |
| 17 | Imagen con «Arroz 320 CUP lb», todo coincide | 2 | `approved` | las imágenes no entran al carril 1 |
| 18 | Imagen; modelo `off_focus`; reglas sin piloto | 0 | — | el carril 0 no distingue tipo |
| 19 | Anuncio sin propuesta del modelo | 2 | — | sin Claude, todo a revisión |
| 20 | Propuesta degradada | 2 | — | idem |
| 21 | «Arroz 320 CUP lb», todo en `pass`, y una señal externa en desacuerdo con probabilidad alta sobre el producto | 2 | `approved` | la señal saca del carril 1 |
| 22 | El caso 6 con una señal externa de acuerdo | 2 | `unverified` | una señal nunca mete en un carril |
| 23 | El caso 1 sin configuración de parámetros | 2 | `unverified` | sin configuración no se automatiza |

## Preguntas al operador — resueltas el 2026-10-02

El operador aceptó las seis respuestas por defecto. A5b las implementa tal cual.

1. **¿Regla 2 con banda robusta o con la fórmula 70/30?** Defecto: banda
   robusta, registrado como apartamiento.
2. **¿El carril 1 arranca solo con texto?** Defecto: sí. El extractor acierta
   bastante menos en imágenes; entran con C2.
3. **¿El carril 0 incluye imágenes?** Defecto: sí, medido aparte de los textos,
   y G1a decide por tipo. El único falso descarte del oro fue una imagen.
4. **¿Un canal sin historial puede entrar al carril 1?** Defecto: no. Los
   canales nuevos del paso 7 pasan por revisión hasta juntar historial.
5. **¿Los descartes de lote cuentan como evidencia?** Defecto: no. Con eso la
   evidencia de G1a queda en el oro (n = 49 hoy).
6. **¿Auditoría total al arranque si el carril 1 se adelanta?** Defecto: sí,
   hasta un `n0` que elegís en G1b.

## Registro

Entrada en `docs/decisions.md` (2026-10-02, «A5a»): los cinco apartamientos de
«Contraste», el carril por predicados y no por puntaje, y la evidencia de G1a
acotada al oro.
