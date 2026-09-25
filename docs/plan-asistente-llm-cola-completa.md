# Plan — Curaduría de toda la cola con el asistente LLM

**Estado: en ejecución.** Slices 1 y 2 cerrados; slice 3 abierto (2026-09-25).

Fecha del plan: 2026-09-25.

---

## Qué cambia

Hoy el operador cura cada anuncio a mano y el asistente solo completa huecos, a
pedido, uno por uno (`docs/decisions.md`, 2026-09-01). El camino nuevo:

- **El asistente hace la curaduría de toda la cola**: propone el formulario
  completo y, cuando corresponde, el descarte con su motivo.
- **El operador solo verifica** el formulario contra el anuncio y da **Aceptar**
  (o corrige y acepta, o descarta).
- La confirmación humana se mantiene: nada llega a `prices_history` sin que el
  operador la dé.

## Decisiones del operador (2026-09-25)

1. **Todo pasa por el asistente.** No se reserva una muestra sin asistencia.
2. **La medición de H0 queda por redefinir** (ver «Pendiente»). Este es el camino
   de ahora en adelante.

## Apartamientos que hay que registrar en `docs/decisions.md`

Se anexan al ejecutar el slice 2, junto con el código (regla del proyecto):

| Qué se aparta | De dónde | Motivo |
| --- | --- | --- |
| «LLM solo para lo que las reglas no resuelven» | `EXT-07`, `spec.md` §6.1 | El cuello es el triage (68% de descarte), no los huecos. Se mantiene «con confirmación humana». |
| «Solo a pedido, nunca en lote» | decisión 2026-09-01 | El pre-cálculo por lotes es lo que permite que el operador solo revise. |
| «H0 se mide sobre lo no asistido» | decisión 2026-09-01, `spec.md` §3 | Si todo es asistido, ese conjunto queda vacío. Redefinición pendiente. |
| «La curaduría es manual» | decisión cerrada 2 | Pasa a ser curaduría asistida con revisión humana anuncio por anuncio. |

**Decisión cerrada 3 (sin cola de trabajos):** se respeta. El pre-cálculo es un
comando que lanza el operador, no un worker ni un cron. Si algún día se quiere
programarlo, es una decisión aparte.

## Lo que NO cambia

- La imagen nunca sale del VPS: el modelo recibe solo el texto (OCR) ya pasado por
  `redact_personal_data`. Un modelo de visión sigue descartado (privacidad).
- Catálogos cerrados, revalidados de nuestro lado: nada se da de alta desde la
  salida del modelo.
- `raw_ingestions` inmutable; toda extracción conserva referencia al registro crudo.
- Sin clave o sin paquete, el asistente degrada a «sin propuesta» y la cola
  funciona a mano como siempre.
- Registro de solo-anexado por invocación (`data/llm-assist-log.jsonl`).

---

## Slices (uno por sesión; el siguiente no se abre hasta que el operador confirme)

### Slice 1 — Backtest (sin cambiar el producto)

Correr el modelo sobre los anuncios **ya curados a mano** y comparar su propuesta
con el valor final del operador.

- Entrega: precisión por campo (producto, precio, moneda, cantidad, unidad,
  provincia, formato de venta) y acierto del veredicto de descarte.
- Es la línea base contra la que se comparan todos los números futuros, y la única
  medición del modelo contra etiqueta humana independiente que existirá una vez que
  todo sea asistido.
- Va contra el corpus real curado a mano, nunca contra casos sintéticos (regla de
  medición de `CLAUDE.md`).
- **Umbral para seguir:** lo fija el operador al ver los números. No se inventa
  (`spec.md` §12.6).
- Costo: una llamada por anuncio; se informa el gasto antes de correr.
- **Estado: CERRADO (2026-09-25).** Construido en `llm_propose_ad.py` y
  `llm_backtest.py`; corrido sobre 100 anuncios con dos versiones de prompt.
  Resultados y lecciones en `docs/decisions.md` (entrada del 2026-09-25, «Backtest
  del asistente LLM»); costo real ~$0,006 por anuncio, en `docs/costos.md`. Línea
  base v2 (70 filas): completa 60,0% frente a 40,0% de las reglas; descarte sin
  falsos descartes en 50. Los ajustes de prompt que quedaron (ejemplo de moneda
  marcada) pasan al slice 2.
- **Cómo se mide:** cada anuncio se manda (ya redactado) al modelo en una llamada y
  también al motor de reglas; ambas propuestas se comparan con lo que el operador
  confirmó a mano, con las mismas funciones que H0. Se reportan por texto e imagen:
  veredicto de descarte (matriz de confusión), acierto por campo y completo del
  modelo frente a las reglas, abstención, quién acierta cuando difieren y consumo.
  Sin umbrales: la vara la fija el operador.
- **Hallazgo de la implementación:** la API rechaza un `enum` combinado con un tipo
  en lista (`["string","null"]`); la nulabilidad va con `anyOf`. Los tests con
  cliente falso no lo detectan: por eso el smoke real existe.

### Slice 2 — Propuesta completa y veredicto de descarte

**Estado: CERRADO (2026-09-25).** Verificado por el operador en pantalla.
Datos y lógica en `llm_propose_ad.py` y `llm_checks.py` (commit `1613f7e`); conexión a
`/curation` en `fe5a7dd`. Decisiones y apartamientos en `docs/decisions.md` (entrada
«Completar con IA propone el anuncio entero»). Lo medido cambió el diseño: la
«moneda asumida» y la evidencia hallada no predicen el error y no se muestran como
marca; el acuerdo con las reglas sí. Del slice 4 ya quedó hecho el descarte sugerido
con confirmación en un clic; quedan el botón **Aceptar**, el atajo de teclado y el
orden de la cola.

- El modelo propone **todas** las filas (no solo huecos) y `descartar` con motivo
  corto.
- Las reglas pasan a ser segunda opinión: cada campo queda marcado como
  **coincide** (modelo y reglas iguales) o **difiere** (se resalta). Es la defensa
  principal contra el rubber-stamping.
- Anuncios con varias filas (listas de precios): una fila por producto del foco,
  igual que hoy.
- **Evidencia por campo, verificada por código.** El modelo cita el fragmento
  literal del anuncio que sustenta cada valor. Nosotros comprobamos (texto
  normalizado) que ese fragmento existe; si no aparece, la fila se marca como
  sospechosa. La interfaz (slice 4) resalta el fragmento en el anuncio, lo que
  acelera la verificación y ataca el rubber-stamping. Cuesta más tokens de salida:
  se mide en este slice antes de fijarlo.
- **Moneda inferida.** El modelo indica si la moneda venía explícita en el anuncio
  o la asumió por defecto (CUP). La interfaz resalta la moneda asumida: es el campo
  más frágil, porque un error de moneda mueve el precio unas 700 veces.
- **Contexto del mercado cubano en el prompt** (abreviaturas de provincia y de
  unidad, quintal, separadores de miles). Se arma con los alias que ya tiene el
  repo y con anuncios reales curados, nunca con una lista inventada, y solo si el
  backtest muestra que los errores están ahí.
- **Señal de calidad:** el acuerdo con las reglas (coincide/difiere). Se descarta la
  «confianza 0–100» autodeclarada del modelo, que suele estar mal calibrada.
- Se anexan las entradas de `decisions.md` de la tabla de arriba.

### Slice 3 — Pre-cálculo por lotes (toca esquema → flujo completo de especificación)

**Partido en sub-slices (2026-09-25, aprobado por el operador).** Cada uno se
cierra antes de abrir el siguiente:

- **3a — Guardar la propuesta y capturar la revisión.** **CERRADO (2026-09-25)**,
  verificado por el operador; especificación en `docs/spec-slice-3a.md`.
- **3b — Pre-cálculo por lotes** (`prefill`, `/curation` lee lo guardado, H0 por
  `llm_reviews`). **Construido (2026-09-25), pendiente de verificación del
  operador.** Sin cambio de esquema. Comando manual, uno a la vez, en seco por
  defecto; sus líneas en el registro llevan `"source": "prefill"` (suman
  consumo, no excluyen de H0). Uso en `docs/runbook-asistente-llm.md`, motivo
  en `docs/decisions.md` (2026-09-25, slice 3b).
- **3d — El modelo propone también formato de venta, base del precio y envase**
  (pedido del operador, 2026-09-25; va antes que 3c). Hoy el modelo propone seis
  campos (producto, precio, moneda, cantidad, unidad, provincia). Minorista o
  mayorista (`sale_format`), precio por unidad o total del lote (`price_basis`),
  formato del catálogo y envase los rellenan las reglas cuando pueden, y si no,
  quedan «sin responder». `price_basis` es el más caro: sin responder, la fila
  queda fuera del rango público. Ejemplo: «Pollo Muslo 4x10: 27300» → envase caja,
  40 lb, total del lote, minorista.
  - Sin cambio de esquema: las columnas ya existen en `prices_history`. Cambian el
    prompt (vocabularios cerrados), la validación y el formulario.
  - Se mide con backtest contra el oro, que ya tiene esos valores curados a mano,
    antes de mostrarlo.
  - La variante (tipo, marca, origen) queda fuera: es texto libre y crea registros
    nuevos en el catálogo.
- **3c — Glosario y recorte del texto** (los dos puntos de abajo). Antes del recorte
  se mira en qué se van los tokens de salida con la respuesta cruda que guarda 3a.

- Comando `python -m saimc.extraction.prefill`: recorre los pendientes, hace OCR y
  llamada al modelo, y guarda la propuesta. Una a la vez (VPS de 2 vCPU).
- Tabla nueva para las propuestas (ingestion, payload, modelo y su versión, estado,
  fecha).
  Migración con Alembic.
- **Requisito transversal: captura de la revisión.** Por cada fila revisada se
  guarda la propuesta del modelo, el valor final que confirmó el operador y si lo
  **editó** (por campo). Sin esto no hay señal para el slice 5, para redefinir H0 ni
  para decidir después si conviene algún tipo de aprendizaje (ver «Aprendizaje»). El
  registro de la propuesta es inmutable: no se sobrescribe al corregir.
- `/curation` lee la propuesta guardada en vez de llamar al abrir.
- Al cargar el backlog inicial (~267) se informa el costo estimado antes de correr.
- Idempotente: reanudable si se corta, sin repetir lo ya procesado.
- **Nota del operador y glosario (pedido del operador, 2026-09-25).** Al revisar,
  el operador puede dejar una nota libre sobre lo que el modelo no pudo saber
  (ejemplo real: «Pollo Muslo 4x10» = 4 paquetes de 10 lb, la caja pesa 40 lb).
  La nota se guarda con la captura de la revisión. Si vale para todos los
  anuncios, el operador la **promueve al glosario**: reglas cortas, legibles y
  editables que se inyectan en el prompt de todas las llamadas. No es
  entrenamiento: es contexto curado a mano. Condiciones:
  - Cada cambio del glosario cambia `PROMPT_VERSION`, para que el backtest compare
    antes y después.
  - Glosario corto: cada regla cuesta tokens en todas las llamadas, y reglas
    contradictorias empeoran al modelo.
  - Un patrón muy regular (como `NxM`) se evalúa primero como regla determinista
    del extractor, que es gratis y no falla.
  - Esto reabre en parte «Aprendizaje» (abajo), en su forma más controlada: el
    operador decide cada regla.
- **Recorte del texto antes de enviarlo (pedido del operador, 2026-09-25).** En
  listas largas (supermercados de WhatsApp con 40 productos) se envía al modelo solo
  lo pertinente:
  - las líneas que el emparejado por sinónimos del catálogo reconoce como producto
    foco, más la línea siguiente (precios por volumen como «Más de 50 cajas»);
  - el encabezado que declara moneda o unidad («Precios en CUP»);
  - si no se reconoce ninguna línea, el texto entero (el descarte lo sigue
    decidiendo el modelo).
  Riesgos y cómo se miden:
  - Un producto foco escrito de forma que los sinónimos no reconocen nunca llega
    al modelo. El backtest se corre con y sin recorte sobre el mismo oro, y el
    recorte entra solo si no pierde filas.
  - La evidencia y el resaltado se verifican contra el texto recortado.
  - Ganancia extra: sale menos texto a un tercero (privacidad, `spec.md` §10.6).
  - **Ojo con el ahorro.** En el anuncio medido (4 filas, $0,033) la entrada
    costó $0,007 y la salida $0,026. El recorte ataca la entrada; el grueso fue
    la salida (2629 tokens para 4 filas, sin razonamiento extendido). Antes de
    recortar hay que guardar la respuesta cruda de algunas llamadas largas y ver
    en qué se van esos tokens.

### Slice 4 — Interfaz de revisión

- **Aceptar** con un clic y atajo de teclado; el formulario sigue editable.
- Descarte propuesto aceptable con un clic, mostrando el motivo.
- Cola ordenada: primero lo coincidente, al final lo dudoso o en desacuerdo.
- Verificación contra el diseño: página real renderizada a 390 px y a escritorio
  (mobile first), no solo tests.

### Slice 5 — Vigilancia del rubber-stamping en `/control`

- Tasa de aceptación sin edición, por campo.
- Tiempo por decisión.
- Correcciones del operador sobre propuestas del modelo (lo que hoy da la señal de
  calidad), leídas de la captura de la revisión del slice 3.
- Aviso si la aceptación sin edición sube y el tiempo por decisión baja a la vez.

## Costo

**Corregido con el piloto (2026-09-25):** medido ~$0,0065 por anuncio con Sonnet 5
(~2100 tokens de entrada: catálogos más el texto, que en las listas de WhatsApp es
largo). Los ~$0,0031 de `docs/costos.md` eran del prompt viejo de solo huecos.
Entonces: ~1200 anuncios al mes ≈ $8; el backlog de ~371 pendientes ≈ $2,4. Haiku
4.5 costaría aproximadamente la mitad: el backtest dirá si alcanza. Adicional al
plan Pro; la API se factura aparte y con auto-reload apagado. Actualizar
`docs/costos.md` al cerrar el slice 1.

## Del análisis externo (2026-09-25): qué se toma y qué no

Se revisó un análisis de terceros sobre el mismo tema. Se toma: evidencia por campo
verificada por código, moneda inferida marcada y contexto del mercado cubano
(arriba, en el slice 2), y `model_version` (slice 3). No se toma: modelos
multimodales (privacidad, decisión del 2026-09-01), tool use (ya se usa salida
estructurada con esquema), ejemplos few-shot inventados (se usarán curados reales,
ver «Aprendizaje»), la confianza autodeclarada, ni medir H0 con el historial de
correcciones (contradice `CLAUDE.md`).

## Fuera de alcance

- Aceptar sin revisión del operador (auto-aceptar por confianza).
- Modelo de visión sobre la imagen cruda.
- Worker, cron o cola de trabajos.
- Red neuronal propia, fine-tuning o agente: con ~195 curados a mano, sin GPU y en
  2 vCPU rinden peor que las alternativas de abajo.

## Aprendizaje: decisión diferida (2026-09-25)

Se evaluó que el sistema «aprenda» de las correcciones del operador y **no entra en
este plan**. Motivos:

- Sin backtest no se sabe dónde falla el modelo; el aprendizaje optimizaría a ciegas.
- Un clasificador de descarte propio ahorraría centavos (todo el asistente cuesta
  ~$4/mes) y el LLM ya da veredicto de descarte: es sobreingeniería.
- Un sistema que cambia con el uso mueve la vara de medir, con H0 ya sin definir.
- Aprender de lo *aceptado* refuerza errores si se acepta con prisa; solo las
  *correcciones* son señal limpia.

Lo único que se hace ahora es la **captura de la revisión** (slice 3), que deja la
opción abierta sin construir nada.

**Cuándo reabrirlo:** después del slice 1 y de algunas semanas de curaduría asistida,
con los números en la mano:

- Si los errores se concentran por canal o formato → primer paso: **ejemplos
  recuperados en el prompt** (los curados más parecidos como contexto, sin
  entrenamiento). Se compara contra el conjunto de referencia fijo.
- Si hay correcciones recurrentes del mismo patrón → **proponer una regla
  determinista** que el operador aprueba (mejora el motor de reglas, que es la
  segunda opinión del slice 2).
- Si los errores son dispersos → no hace falta nada.
- El clasificador de descarte solo si el backtest muestra que el veredicto del LLM
  es malo.

**Regla fija:** los ~195 anuncios curados a mano antes del asistente quedan como
**conjunto de referencia congelado**, fuera de cualquier aprendizaje. Es lo que
permite saber si el sistema mejora de verdad.

---

## Pendiente — qué significa H0 ahora

Con todo asistido, «precisión de extracción contra etiqueta humana» cambia de
sentido: la etiqueta ya no es independiente del extractor, porque el operador
confirma lo que el modelo propuso. **No se resuelve en este plan.** Puntos de
partida para cuando se aborde:

- El backtest del slice 1 es la última medición independiente disponible.
- «Aceptación sin edición» mide el trabajo que ahorra el asistente, no su precisión
  (el operador puede aceptar mal).
- El umbral de 300 anuncios etiquetados (`spec.md` §3) y la meta de 500 registros
  validados (§9) hay que releerlos a la luz de esto.
- Toca `spec.md` §3, §9 y §12.5; el apartamiento se registra en `docs/decisions.md`.

## Antes de ejecutar

1. El operador enciende el asistente (`docs/runbook-asistente-llm.md`, pasos 1–5) y
   avisa.
2. Confirmar el estado del repo (sin cambios sin commitear) y abrir el slice 1.
