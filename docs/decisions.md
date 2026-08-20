# Decisiones

Registro de anexado. No se reescriben ni se borran entradas viejas. Si una
decisión cambia, se agrega una entrada nueva que diga a cuál reemplaza.

Existe como evidencia de las condiciones bajo las que se tomó cada medición de H0,
y como fuente para validar decisiones tomadas antes. No es un registro general:
solo se anota lo que, mirando el código, alguien concluiría razonablemente que
está mal.

---

## 2026-08-06 — raw_ingestions se aparta del DDL del whitepaper en dos puntos

Contexto: el whitepaper §7.1 (línea 1061) trae el `CREATE TABLE` de
`raw_ingestions` sin ninguna restricción `NOT NULL` y con `TIMESTAMP` sin zona
horaria. Implementado tal cual, `content_hash` sería nulable bajo un índice único,
y en PostgreSQL varios `NULL` no colisionan entre sí: la deduplicación de capturas
repetidas —que es para lo que ese índice existe— no funcionaría.

Decisión: se agregó `NOT NULL` a `id`, `source_type`, `file_path`, `content_hash`,
`content_type`, `processing_status`, `captured_at` y `created_at`. Quedan nulables
`source_reference` y `original_text`, que sí se completan más tarde durante la
curaduría. Las dos marcas de tiempo pasaron a `TIMESTAMPTZ`.

Descartado: copiar el DDL literal. Habría dado un índice único inútil y fechas
ambiguas en un entorno WSL donde la zona del sistema y la del servidor no tienen
por qué coincidir.

Consecuencia: el esquema real y el whitepaper ya no son idénticos. La diferencia
está acotada a restricciones y zona horaria; nombres de tabla, de columnas, tipos
base y el nombre del índice (`idx_raw_ingestions_hash`) se mantienen para que la
comparación siga siendo directa. Anotado en `docs/whitepaper-map.md`.

---

## 2026-08-06 — Diferenciación frente a elTOQUE reescrita sobre premisas verificables

Contexto: la v3.4 presentaba a elTOQUE como «caja negra» y su transparencia como
«opaca». La metodología de la TRMI es pública —recolección por bots, NLP, mediana
sobre ventana de 24 h con filtrado de extremos, EMA para monedas de baja densidad—.
Una diferenciación apoyada en esa premisa se cae en la primera pregunta de un
inversor que abra el sitio del competidor.

Decisión: el posicionamiento (§5) se reconstruye sobre cuatro ejes verificables:
objeto medido (bienes vs. divisas), granularidad (provincial vs. nacional),
naturaleza del problema técnico (extracción **más** resolución de entidades), y
trazabilidad por dato. Se separa además «elTOQUE — TRMI» de «elTOQUE Markets» en el
mapa de competidores: son productos distintos y solo el segundo compite con SAIMC.

Descartado: sostener «ellos son opacos». También se descartó la formulación de la
investigación, que abandonaba el eje de trazabilidad. Que la metodología sea pública
no implica que cada precio publicado sea rastreable hasta el anuncio que lo originó;
ese eje es el más fuerte que SAIMC tiene y se conserva.

Consecuencia: la Transparency Layer (§6.4) y el Data Lineage Framework (§6.5) dejan
de ser una funcionalidad más y pasan a ser el sustento del posicionamiento. Si se
recortan, se cae la diferenciación completa.

---

## 2026-08-06 — El tipo de cambio informal es una dependencia externa declarada

Contexto: los anuncios llegan en CUP, USD, EUR y MLC. El whitepaper extraía `currency`
y `prices_history` tenía la columna, pero no existía en ninguna parte una política de
conversión. Una serie temporal comparable exige base monetaria única, y esa base sale
de una referencia que SAIMC no produce.

Decisión: se adopta CUP como moneda base de las series publicadas y se declara la TRMI
de elTOQUE como fuente primaria de conversión, con fuente de respaldo obligatoria,
política de degradación (última tasa conocida, ventana de validez máxima, marcado
explícito de serie con tasa vencida) y atribución visible. `prices_history` suma
`price_cup`, `exchange_rate`, `exchange_rate_date` y `exchange_rate_source`
(§6.3 componente E y §7.2).

Descartado: guardar solo el precio convertido. Sin la tasa histórica almacenada, un
cambio retroactivo en la fuente reescribe la serie entera en silencio y rompe la
reproducibilidad que sostiene §6.5.

Consecuencia: SAIMC hereda las propiedades estadísticas y los riesgos de
disponibilidad de su fuente de tipo de cambio. Los cuatro campos entran en el diseño
de `prices_history` desde el primer modelo: agregarlos después obliga a reprocesar
registros crudos.

---

## 2026-08-06 — La manipulación adversarial entra como riesgo de primer orden

Contexto: el riesgo 10.1 cubría error, ruido y spam —fuentes independientes entre sí—.
No había nada sobre manipulación deliberada y coordinada. El modelo de ataque está
públicamente articulado contra el sistema metodológicamente más cercano: en diciembre
de 2025 se difundió un análisis que señalaba que cuatro grupos de Telegram aportaban
más de la mitad de la muestra de la TRMI. Quien controla la mitad más uno de las
observaciones de una celda determina su mediana.

Decisión: nuevo riesgo §10.7 con cuatro contramedidas —umbral mínimo de muestra (C1),
estimadores robustos (C2), índice de control paralelo (C3), detección de coordinación
(C4)— y alcance declarado: elevar el costo del ataque y detectarlo, no alcanzar
inmunidad.

Descartado: numerarlo como 10.2 y correr el resto, que es donde la investigación lo
ubicaba por importancia. El §10.6 está referenciado desde `CLAUDE.md`, `.gitignore`,
`README.md` y `src/saimc/ingestion/inbox.py`; renumerar rompía todas esas referencias
por una ganancia de orden de lectura. Se compensa con un puntero desde 10.1.

Consecuencia: SAIMC es estructuralmente más vulnerable que un sistema de tipo de
cambio —matriz en vez de valor único, densidad por celda mucho menor—. C3 debe estar
operativo **antes** de habilitar el botón «subir dato» de la Fase 2, no después.

---

## 2026-08-06 — Umbral de densidad como condición de publicación por provincia

Contexto: la sobreextensión geográfica es una causa documentada de fracaso en sistemas
de información de mercado de primera generación. La arquitectura permite ingerir todas
las provincias desde el día 1, y eso es precisamente la trampa: cobertura nacional con
densidad insuficiente produce series ruidosas y manipulables, y hunde la credibilidad
del sistema entero por el desempeño de sus celdas más débiles.

Decisión: §9.0. Una provincia se publica solo tras superar y sostener un umbral N de
observaciones válidas, verificado sobre múltiples categorías. Por debajo, ingesta sin
publicación, con el estado de cada provincia visible en la interfaz. Los números de
provincias de las Fases 1 y 2 pasan a ser techos de ingesta, no metas de publicación.

Descartado: aplicar la restricción literal desde el mes 1. Con 500 registros sobre
3–5 productos y 2 provincias, ninguna celda alcanza densidad seria, y el dashboard de
Fase 1 quedaría vacío —contradiciendo §3, que dice que H0 no bloquea el lanzamiento
público—. La conciliación separa las dos razones del umbral: la estadística rige desde
el día 1 (tamaño de muestra visible, sin titular de precio); la adversarial rige desde
la Fase 2, cuando se abre la ingesta comunitaria y desaparece la barrera del operador
humano que verifica cada registro contra su evidencia.

Consecuencia: las Fases 0 y 1 son el período de calibración de N, no el período en que
N se aplica. Y queda una restricción vinculante sobre el próximo slice: incorporar una
provincia debe ser configuración, no desarrollo.

---

## 2026-08-06 — Se publica toda la metodología, con una reserva acotada y declarada

Contexto: el whitepaper comprometía «reglas públicas y lógica de negocio abierta» sin
calificar. La contramedida C4 —detección de coordinación— es antifraude: sus umbrales
de sincronía, similitud textual y puntuación de fuentes pierden eficacia al publicarse,
igual que las reglas de detección de fraude en pagos o los filtros antispam.

Decisión: §6.4.1. Se publica todo —fórmulas, umbrales de validación y densidad, reglas
del validador, criterios de normalización, ventanas de cálculo, métricas de calidad,
código y datos agregados— aplicando el principio de Kerckhoffs: si publicar los
umbrales comprometiera la integridad, el problema sería el diseño. Se reservan
únicamente los parámetros de C4, y la existencia de esa reserva se declara
públicamente junto con su motivo.

Descartado: presentar esto como «resolución de un ítem abierto», que es como lo
planteaba la investigación. No había ítem abierto: el compromiso previo era sin
reservas. Esto **acota** el principio 1 de Transparencia Radical. Registrarlo como
aclaración habría escondido una reducción de alcance.

Consecuencia: cualquier ampliación futura de lo reservado necesita entrada nueva acá y
anuncio público. El principio 1 lleva ahora un puntero explícito a §6.4.1.

---

## 2026-08-07 — Processing Layer: reglas de negocio del whitepaper aplicadas como CHECK de base

Contexto: el whitepaper especifica en prosa (§6.3, líneas 896-899) que un precio en
divisa sin tasa de cambio se guarda sin convertir en vez de bloquearse, y usa «94»
como ejemplo de `confidence_score` (§6.2, línea 797) sin fijar un rango explícito. No
trae DDL para `provinces` ni `products`: la única restricción vinculante ahí es
«configuración, no desarrollo» (§9.0, línea 1603).

Decisión: `0002_processing_layer` traduce la regla monetaria en dos `CHECK` con
nombre explícito sobre `prices_history` (CUP sin tasa; tasa siempre con fecha y
fuente) y agrega un tercer `CHECK 0..100` sobre `confidence_score` como lectura
razonable del ejemplo del whitepaper, no como cita literal. Mismo criterio que
`raw_ingestions` (entrada del 2026-08-06): una regla dicha en prosa se aplica también
a nivel de base, no solo en código de aplicación.

Descartado: dejar las reglas monetarias solo como validación de aplicación. Un
`INSERT` fuera de ese camino (migración futura, corrección manual) rompería la regla
sin que la base lo impida.

Consecuencia: cualquier fila de `prices_history` que viole esas tres reglas falla en
el `INSERT`, no en una revisión posterior. Aclaración explícita para no confundir esto
con un apartamiento del whitepaper: `provinces.code`, `provinces.is_published` y
`prices_history.province_id` nulable no son deviaciones — llenan un área que el
documento dejó sin DDL o que permite explícitamente (línea 733, «Provincia: No
determinada»).

---

## 2026-08-07 — TDD de extracción: casos sintéticos por regla, corpus real solo para medir H0

Contexto: la regla original de CLAUDE.md («el caso esperado se escribe antes que el
código, contra el conjunto de anuncios etiquetados a mano») se leía como si bloqueara
escribir cualquier extractor hasta acumular los 300+ anuncios reales curados — lo que
a su vez exigía curaduría 100% manual, sin ninguna propuesta automática. Eso
contradice el flujo que describe el whitepaper (§6.1, líneas 583-611): «el sistema
propone, el operador confirma o corrige», con ejemplo concreto de producto/precio/
moneda/cantidad/provincia pre-llenados.

Decisión: separar dos niveles. Las reglas de extracción determinísticas (precio,
moneda, cantidad/unidad por regex; producto y provincia por matching contra catálogo
cerrado — 3-5 productos con alias, 16 provincias ya sembradas) se testean con TDD
usando casos sintéticos escritos a mano, siempre que la respuesta correcta se defina
antes e independientemente de correr el extractor. La medición de H0 (el número de
precisión final, §3/§12.5) sigue corriendo exclusivamente contra los 300+ anuncios
reales curados.

Descartado: curaduría 100% manual sin ninguna asistencia hasta acumular 300+ anuncios
reales. Bloqueaba construir el motor de reglas y contradecía la UX de «propone y
confirma» que el propio whitepaper define como el instrumento de medición.

Consecuencia: el motor de reglas (regex + catálogo) se construye ya, con TDD
sintético. Se conecta a la cola de curaduría desde el primer anuncio real; cada
confirmación o corrección alimenta el corpus real que más adelante mide H0.

---

## 2026-08-07 — Motor de reglas de extracción: alias de provincia en código, no en esquema

Contexto: el motor de extracción (whitepaper §6.1 Etapa 2) necesita resolver
abreviaturas informales de provincia en el texto («stgo», «habana») para proponer
valores en la cola de curaduría. `products.aliases` ya vive en la tabla como columna
JSON; `provinces` no tiene columna equivalente.

Decisión: el diccionario de abreviaturas de provincia vive como dato estático dentro
de `src/saimc/extraction/engine.py`, no como columna en `provinces`. Cubre las 16
provincias fijas de Cuba.

Descartado: agregar `provinces.aliases` (columna JSON, simétrica a `products`) para
mantener el mismo patrón. Habría exigido tocar el esquema de nuevo — migración y
flujo de especificación completo, por la regla de CLAUDE.md — para un dato que es
puramente de la capa de extracción: nunca se consulta ni se muestra fuera de ahí, a
diferencia de `products.aliases`, que sí es dato de catálogo usado en varios lugares.

Consecuencia: agregar o corregir una abreviatura de provincia exige tocar código y
redeploy, no un `INSERT`. Aceptable porque la lista de provincias es fija (§9.0) y la
jerga regional es acotada. Si esto se vuelve un problema real —abreviaturas que
cambian seguido, necesidad de que el operador las edite sin deploy— se revisita
moviendo el diccionario a una tabla.

---

## 2026-08-07 — Medición de H0: unidad comparada contra el catálogo, no contra una fila propia

Contexto: §12.5 exige medir «precisión de cantidad/unidad» como una de las cuatro
métricas decisivas de H0, pero `prices_history` no persiste `unit` por fila (decisión
de la cola de curaduría: se asume la unidad canónica del producto).

Decisión: el script de medición (`src/saimc/extraction/measure.py`) compara la unidad
que propuso el motor de reglas contra `Product.unit` del producto que el operador
confirmó, no contra un valor por fila. El propio whitepaper valida este enfoque
(§12.5: «la respuesta correcta es acotar los productos bandera a aquellos con unidad
de venta estable, no bajar el estándar»).

Descartado: agregar `prices_history.unit` para tener verdad de terreno por fila.
Habría exigido flujo completo de especificación por tocar esquema, para un dato que
el propio whitepaper dice que debería ser estable por producto en Fase 0.

Consecuencia: si un anuncio real usa una unidad distinta a la canónica del producto
(ej. «docena» para huevos cuando el catálogo dice «unidad»), la métrica lo cuenta
como extracción incorrecta aunque el motor haya leído bien el texto. Si esto se
vuelve el cuello de botella real, la respuesta correcta —según el propio
whitepaper— es ajustar el catálogo de productos bandera, no agregar la columna.

---

## 2026-08-07 — OCR conectado a la cola de curaduría, en la curaduría y no en la ingesta

Contexto: 16 de los 17 elementos pendientes eran imágenes, sin `original_text` y por
ende sin propuesta del motor de reglas — el formulario salía en blanco (ver
`docs/backlog.md`, ítem removido con este slice). El whitepaper (§6.1, «Rol del OCR»)
ya ubica el OCR como asistente de la cola de curaduría, explícitamente fuera de la
ingesta.

Decisión: `saimc.extraction.ocr.transcribe()` (Tesseract vía `pytesseract`, idioma
`spa`) corre recién en `GET /curation`, sobre la imagen del elemento `pending` que se
está mostrando — no en `python -m saimc.ingestion`. El texto reconocido alimenta
`propose()` igual que ya hacía el texto pegado, y se muestra en un campo editable. Al
confirmar la curaduría, ese texto (tal cual o corregido por el operador) se escribe en
`raw_ingestion.original_text`, que hasta ese momento es `None` a propósito. Mismo
criterio ya usado con `source_reference`: completar un campo nullable durante la
curaduría no viola la inmutabilidad de `raw_ingestions` — lo que nunca se toca es la
evidencia capturada (`file_path`, `content_hash`, `captured_at`, `content_type`).
Fallos esperables del OCR (binario ausente, archivo inexistente, imagen corrupta)
degradan a texto vacío en vez de romper la cola; cualquier otro error se propaga.

Descartado: correr OCR en la ingesta y guardar el resultado ahí mismo. Contradice el
whitepaper explícitamente, y mezclaría en una fila inmutable un dato que todavía nadie
confirmó.

Consecuencia: cada `GET /curation` sobre una imagen pendiente ejecuta Tesseract en el
momento — sin caché, sin cola de trabajos (decisión 3 de CLAUDE.md, todavía sin
justificación de volumen real para romperla). Aceptable para un solo operador con
curaduría de a un elemento por vez; si el volumen lo justifica, se revisita.

---

## 2026-08-07 — Alta de producto desde la cola de curaduría, no solo catálogo cerrado

Contexto: al armar la Processing Layer se dejó `products` sembrado solo con los 5
productos bandera de Fase 0 (`0004_seed_products.py`), con la cola de curaduría
consumiendo ese catálogo como un `<select>` cerrado. Al curar el primer anuncio real,
el usuario encontró productos que no están en esa lista — el `<select>` bloqueaba
avanzar.

Decisión: el campo "Producto" del formulario pasa a ser texto libre con
autocompletado (`<datalist>`, sugiere los existentes, sin JS). Si el nombre tipeado
coincide con un producto existente (comparación case-insensitive), se usa ese
producto. Si no coincide con ninguno, se exige categoría y unidad y se crea el
producto en el momento de confirmar la curaduría.

Descartado: mantener el catálogo cerrado y forzar descartar cualquier anuncio con un
producto fuera de los 5 bandera. Habría descartado la mayoría de los anuncios reales
del usuario, contradiciendo el propósito mismo de la curaduría en Fase 0 (juntar el
conjunto de referencia).

Consecuencia: el catálogo de `products` deja de ser un dato fijo sembrado una sola
vez — crece con el uso. La deduplicación depende de que el operador escriba el mismo
nombre (la comparación es case-insensitive, pero no tolera variantes de escritura,
p. ej. "Aceite" vs "Aceite de cocina" como productos distintos). Si eso genera
catálogo sucio en la práctica, se revisita con una vista de administración de
productos — no antes, sin evidencia de que haga falta.

---

## 2026-08-08 — Multi-producto por anuncio: una fila por línea, sin cambio de esquema

Contexto: anuncios reales del mercado informal cubano suelen ofrecer varios productos
con precios distintos en el mismo texto, uno por línea (ej. venta mayorista con 5-6
cortes de pollo, cada uno con su propio precio). `propose()` y la cola de curaduría
solo manejaban un producto por anuncio — la mayoría del precio real en esos anuncios
se perdía.

Decisión: `propose_all()` (motor) devuelve una propuesta por línea con producto o
precio reconocible; provincia y moneda caen al texto completo cuando la línea puntual
no trae marca propia. La cola de curaduría suma `action=save_and_continue` (guarda una
fila, el `raw_ingestion` sigue `pending`, vuelve a mostrarse con el siguiente
candidato) y `POST /curation/{id}/finish` (cierra el anuncio, exige al menos una fila
guardada). `prices_history.source_id` ya no tiene restricción de unicidad implícita —
varias filas por anuncio no exigieron ningún cambio de esquema.

Descartado: repetir campos indexados en un solo formulario (`product_name_0`,
`product_name_1`, ...) para cargar todo de una. Exige decidir de antemano cuántas
filas renderizar y complica la validación; guardar-y-continuar reutiliza el mismo
formulario de siempre, sin JS.

Consecuencia: `saimc/extraction/measure.py` pasó de evaluar por ingesta a evaluar por
fila (`evaluated_rows_count`, distinto de `text_curated_count`) — antes pisaba en
silencio todas las filas de un anuncio menos la última al indexar por `source_id`. El
emparejamiento fila-candidato es por `product_id` primero, con fallback posicional
para lo que no matchea (`_match_rows_to_candidates`), así una fila sin producto
reconocido igual se compara contra la línea real que la originó en las otras tres
métricas.

---

## 2026-08-08 — Multi-producto: tabla editable de una vez, reemplaza "guardar y agregar otro"

Contexto: la entrada anterior (2026-08-08, "Multi-producto por anuncio") resolvió la
extracción (`propose_all`) pero dejó la carga como una serie de POSTs de a una fila
(`action=save_and_continue` + `POST .../finish`). Probado contra un anuncio real de 6
productos (hígado/picadillo/muslo/molleja/spam de pollo, un mayorista de Santiago de
Cuba), ese flujo resultó tedioso: 6 recargas de página para un solo anuncio.

Decisión: `GET /curation` ahora arma una tabla con una fila por candidato de
`propose_all` más 3 filas en blanco para altas manuales, todo en un único formulario.
`POST /curation/{id}` pasa a `async def` y lee el form crudo (`await request.form()`)
con campos indexados (`product_name_0`, `price_0`, ...) en vez de parámetros `Form()`
tipados, porque la cantidad de filas es dinámica. Todo o nada: si cualquier fila con
datos tiene un error, no se guarda ninguna — evita estados a medias confusos. Filas
vacías se ignoran en silencio, sin exigir que el operador las borre.

Descartado: el flujo de "guardar y agregar otro" — se elimina por completo (`action`,
`POST .../finish`, y los helpers que quedaron huérfanos). Reemplazarlo en vez de
mantener los dos caminos evita que compitan dos formas distintas de resolver lo mismo.

Consecuencia: ya no hay guardado incremental — si el operador cierra la pestaña a
mitad de una tabla larga, no se guarda nada de esa vuelta (antes, cada "guardar y
agregar otro" persistía al toque). Aceptado como trade-off consciente por ahora; se
revisita si en la práctica se pierden cargas largas seguido. Dos filas con el mismo
nombre de producto nuevo en el mismo submit crean un solo `Product`, no dos.

---

## 2026-08-08 — Tabla multi-producto: excluir fila sin vaciarla, categoría/unidad opcionales

Contexto: usando la tabla editable contra anuncios reales, dos fricciones. (1) Una
fila prellenada con un falso positivo del motor obligaba a borrar varios campos a
mano para que se ignorara. (2) Exigir categoría y unidad para dar de alta un producto
nuevo bloqueaba el submit entero por un dato secundario que casi nunca importa en
Fase 0 (3-5 productos bandera).

Decisión: cada fila suma un checkbox "Incluir" (marcado por default); si el operador
lo desmarca, la fila se descarta completa sin leer sus otros campos, sea cual sea su
contenido. Categoría y unidad de producto nuevo dejan de ser obligatorias: si faltan,
se completan con defaults (`"sin categoría"`, `"unidad"`) resueltos en
`curation.py`, sin tocar `Product.category`/`Product.unit` (`NOT NULL` sin default en
el modelo — hacerlas nullable sería cambio de esquema, flujo completo de
especificación, fuera de alcance de este ajuste).

Descartado: hacer los defaults configurables o inferirlos de algo (ej. copiar
categoría de un producto similar). Sobreingeniería para Fase 0 — `"sin categoría"` es
un valor legible y buscable si hace falta limpiarlo después a mano.

Consecuencia: productos nuevos dados de alta sin categoría/unidad explícitas quedan
con esos strings genéricos hasta que alguien los corrija. Si eso ensucia el catálogo
en la práctica, se revisita (ver ítem ya anotado en `docs/backlog.md` sobre
deduplicación de nombres) — no antes, sin evidencia de que haga falta.

---

## 2026-08-08 — Producto vuelve a ser `<select>`, alta nueva detrás de un "+"

Contexto: el input de texto con `<datalist>` (autocompletado nativo) no se percibía
como una lista de selección real — pedido explícito del usuario: un `<select>` clásico
de productos existentes, con un "+" al lado para dar de alta uno nuevo sin recargar
la página ni perder el resto de la tarjeta.

Decisión: cada tarjeta tiene un `<select name="product_id_i">` con los productos
existentes, más un `<details>`/`<summary>` nativo de HTML ("+ Agregar producto
nuevo") colapsado por default, sin JavaScript, con los campos de alta
(`new_product_name_i`, `product_category_i`, `product_unit_i`). Si el operador
completa el alta nueva, tiene prioridad sobre lo que haya elegido en el `<select>`
— decidió agregar en vez de usar el existente.

Descartado: mantener el input de texto libre. Resolvía lo mismo técnicamente (dejaba
elegir o escribir) pero no se percibía como tal — la forma en que se lee la interfaz
importa tanto como lo que técnicamente permite hacer.

Consecuencia: `product_id` reemplaza a `product_name` en el contrato de campos del
form (`_row_from_form`, `_values_from_proposal`). Una fila con precio pero sin
producto (ni seleccionado ni alta nueva) ahora es un error explícito ("seleccioná o
agregá un producto") en vez de ignorarse en silencio — antes una fila sin nombre de
producto se trataba igual que una fila vacía; ahora, con precio cargado, se asume que
había intención de completarla.

---

## 2026-08-08 — Eliminar tarjeta del lado del cliente, primer JavaScript del proyecto

Contexto: el checkbox "Incluir" (entrada anterior, mismo día) exigía vaciar cada
campo a mano para ignorar una tarjeta prellenada con datos no deseados, y no achicaba
la lista — el operador pidió poder sacarla de la vista directamente, con las tarjetas
siguientes renumerándose.

Decisión: cada tarjeta suma un botón "Eliminar" que la saca del DOM con un puñado de
líneas de JavaScript vanilla (sin librería, sin CDN, un único `<script>` en la
página) y renumera las etiquetas "Producto N" restantes. Es el primer JavaScript del
proyecto — hasta acá todo fue HTML nativo (`datalist`, `details`). No contradice la
decisión de stack cerrada (`CLAUDE.md`, decisión 1: FastAPI + Jinja2 + HTMX): HTMX ya
implica cargar JS, esto es más liviano todavía y no es un framework.

Descartado: mantener el checkbox "Incluir" además del botón nuevo. Sacar la tarjeta
del DOM ya logra el mismo efecto en el backend (sus campos nunca viajan en el POST,
caen en la rama de fila vacía que ya existía) — tener las dos formas de lograr lo
mismo era redundante, así que el checkbox se eliminó en vez de sumarse.

Consecuencia: sin JavaScript habilitado en el navegador, no hay forma de sacar una
tarjeta de la vista — hay que vaciar sus campos a mano, igual que antes de este
cambio. Aceptado como límite conocido; es una herramienta interna de un operador, no
un caso de uso que priorice accesibilidad sin JS.

---

## 2026-08-08 — Modificadores que descalifican el match genérico de producto

Contexto: `_match_product` hacía substring puro — "hígado de pollo" matcheaba contra
"Pollo" solo porque la palabra está contenida ahí adentro. Un hígado de pollo y un
pollo entero son productos distintos con precios por libra completamente distintos;
guardarlos bajo el mismo producto genérico corrompe la inteligencia de precios.
Detectar en general que un corte es "otro producto" es un problema de NLP fuera de
alcance para reglas y diccionarios (whitepaper §6.1, línea 611: LLM solo para lo que
las reglas no resuelven, y esto lo es — pero no se justifica todavía sin evidencia de
volumen real).

Decisión: lista curada de modificadores (`_PRODUCT_DISQUALIFYING_MODIFIERS`: hígado,
picadillo, muslo, pechuga, molleja, ala, corazón, molida, falda, lomo, costilla,
contramuslo, rabadilla, menudencia/s). Si alguno aparece en el texto Y el nombre del
producto que matchearía NO lo incluye (es un match genérico ajeno al corte), no se
confía en ese match — la fila queda sin producto. `ExtractionProposal` suma
`product_name_guess`: el texto antes de la primera pista de cantidad o precio en la
línea, para prellenar el alta de producto nuevo en vez de dejar la tarjeta en blanco.
Si el modificador SÍ forma parte del nombre que matchea (ej. un producto ya cargado
como "Picadillo"), el match es válido — el modificador no descalifica su propio
producto.

Descartado: resolver esto con LLM ahora. Es exactamente el caso que el whitepaper
reserva para eso, pero sin volumen real todavía no se justifica el costo (llamadas a
API, latencia) — la lista curada resuelve el caso concreto encontrado hoy.

Consecuencia: un corte que no esté en la lista todavía puede matchear mal contra el
genérico — la lista es curada a mano, no exhaustiva. Misma red de seguridad de
siempre: el operador revisa la propuesta antes de confirmar. Si aparecen más casos
así, se agregan a la lista; no hace falta releer esta decisión para eso.

---

## 2026-08-08 — Canal origen se propone desde el patrón de fecha de redes

Contexto: `source_reference` era 100% tipeo manual (`docs/backlog.md`). En
anuncios reales de redes (Facebook/Marketplace-style) el nombre de quien
publica suele encabezar el texto con un patrón detectable: "Nombre - Ayer a
las H:MM" / "Nombre - D de mes a las H:MM".

Decisión: `guess_source_reference()` en `engine.py` busca ese patrón solo en
la primera línea del texto (imagen transcripta por OCR o texto pegado) y, si
matchea, prellena el campo "Canal origen" del formulario de curaduría —igual
que `product_name_guess`, una sugerencia editable, nunca lo que termina
guardado sin pasar por el operador.

Descartado: buscar el patrón en cualquier parte del texto. El nombre real
siempre encabeza el anuncio; buscar en todo el cuerpo arriesgaba matchear un
" - " suelto de la mitad del texto que no tiene nada que ver con quien
publicó.

Consecuencia: un anuncio cuyo texto no siga ese encabezado (texto pegado a
mano sin metadata de red, por ejemplo) deja el campo vacío como antes — no
hay regresión, solo menos tipeo cuando el patrón está presente.

---

## 2026-08-08 — Diccionario de provincias revisado contra uso real, sin cambios

Contexto: `docs/backlog.md` pedía validar `_PROVINCE_ALIASES` (`engine.py`)
contra anuncios reales — hasta ahora era una estimación de jerga sin
contraste con datos capturados de verdad.

Decisión: se corrió `_match_province` contra los 8 `raw_ingestions` con texto
real que hay hoy en la base (de 17 ingestas totales, 24 filas ya curadas en
`prices_history`). Las dos provincias que aparecen en ese texto —"Habana" y
"Santiago de Cuba", en formas como "Santiago de cuba" minúscula y "CIMEX,
Sucursal Santiago de Cuba"— matchean correctamente en los 8 casos. No se
tocó el diccionario: no había nada que corregir con esta muestra.

Descartado: agregar alias especulativos para las 14 provincias que todavía
no aparecen en ningún anuncio real. Sin un caso real que lo motive, sería
adivinar jerga en vez de validarla contra uso — exactamente lo que esta
tarea buscaba evitar.

Consecuencia: la tarea del backlog queda revisada pero abierta (ver
`docs/backlog.md`) — el volumen actual solo ejercita 2 de las 16 provincias.
Vuelve a valer la pena revisarla cuando haya anuncios reales de otras zonas.

---

## 2026-08-08 — propose_all descarta líneas con precio suelto sin producto ni nombre

Contexto: el usuario reportó que curar un anuncio de varios productos suele
dejar un par de tarjetas en blanco que hay que eliminar a mano (motivo por el
que se pidió el botón "Eliminar" de `62e992a`). Corriendo `propose_all`
contra los 8 `raw_ingestions` reales que hay hoy, aparecieron 4 tarjetas así
en 2 de los 8 anuncios: líneas donde el precio matchea pegado al principio
("a 8/4/2026 at 11:48" — una fecha que `_PRICE_AFTER_A_RE` lee como precio;
"$23,500.Sale 427.27Lb" — el nombre del producto vive en una línea anterior
del mismo anuncio). En ambos casos `_guess_product_phrase` no tiene texto
antes del precio para adivinar un nombre: la tarjeta queda con precio
prellenado y producto completamente vacío, sin pista de qué es — peor que
una fila en blanco común, porque aparenta que el motor entendió algo.

Decisión: `propose_all` ahora descarta una línea cuando no hay producto
matcheado NI `product_name_guess` (antes solo descartaba cuando no había ni
producto ni precio). El texto crudo sigue visible en la tarjeta "Evidencia"
de la cola de curaduría, así que no se pierde información: el operador puede
seguir agregando esa oferta a mano en una de las filas en blanco de reserva
si la quiere cargar.

Descartado: intentar reconocer el nombre de producto de una línea anterior
del mismo anuncio (el caso de "Sacos de azúcar blanca" en una línea y el
precio en la siguiente). Es exactamente el tipo de estructura multi-línea
que el whitepaper reserva para LLM (§6.1, línea 611) — no se justifica con
el volumen de hoy.

Consecuencia: un anuncio con ese patrón (nombre y precio en líneas separadas)
sigue sin proponer un candidato completo — antes generaba ruido a eliminar,
ahora genera silencio; el operador lo carga a mano igual que antes de que
existiera `propose_all`.

---

## 2026-08-08 — TabPFN evaluado para priorización de cola y forecasting

Contexto: hace falta elegir, a futuro, qué modelo usar para priorizar la cola
de curaduría y para la capa de forecasting de precios (whitepaper, capa
predictiva/B2B, posterior a H0).
Decisión: se evaluó TabPFN — encaja bien con datasets pequeños y corre
offline, sin depender de la conexión intermitente del entorno. Se pospone su
adopción hasta tener datos reales de dos provincias.
Descartado: adoptarlo ahora, sin datos reales que lo justifiquen — Fase 0
todavía valida H0 con curaduría manual, no hay capa predictiva que alimentar.
Consecuencia: TabPFN queda como candidato para cuando exista volumen real en
al menos 2 provincias; no bloquea nada de lo que se está trabajando hoy.

---

## 2026-08-08 — Canal origen: buscar en varias líneas, no solo la primera

Contexto: prueba manual del operador sobre el anuncio pendiente real mostró
"Canal origen" siempre vacío. La decisión previa (misma fecha, "Canal origen
se propone desde el patrón de fecha de redes") asumía que el patrón
"Nombre - fecha a las H:MM" encabeza la línea 0 del texto. Contra los 8
`raw_ingestions` reales, esto era falso en el 100% de los casos: la línea 0
es el nombre de la página/grupo o ruido de OCR, y el patrón real aparece en
la línea 1.

Decisión: `guess_source_reference()` ahora revisa las primeras 5 líneas
(antes solo la línea 0) y devuelve el primer match. También tolera "a la"
además de "a las" (variante que deja el OCR). Reemplaza el alcance de la
decisión anterior sin cambiar su diseño de fondo (sigue siendo una sugerencia
editable, nunca lo que se guarda sin pasar por el operador).

Descartado: unir líneas cuando nombre y fecha quedan en renglones separados
sin " - " entre medio (ej. "CIMEX... - Seguir\nAyer a las 12:53 p.m.", un
botón "Seguir" de Facebook interrumpe el patrón) — la heurística de línea
única no lo cubre y unir líneas arbitrariamente arriesga falsos positivos
más de lo que vale la pena para este caso.

Consecuencia: contra el corpus real, pasó de 0/8 a 4/8 anuncios con canal
origen prellenado (los otros 4 no tienen el patrón en absoluto, o lo tienen
partido en dos líneas sin guion). El resultado a veces incluye ruido de OCR
al principio del nombre (ej. "iS Yongert Clavel Danger") — igual de tolerado
que el ruido en `product_name_guess`: el operador lo corrige al confirmar.

---

## 2026-08-08 — Match de producto ignora tildes

Contexto: revisando duplicados reales en producción apareció "Azucar blanca"
(sin tilde) y "Azúcar blanca" (con tilde) como dos productos separados.
`_match_product` comparaba `name.lower()` contra `text.lower()` sin
normalizar acentos —a diferencia de `_match_province`, que ya usa
`_strip_accents`—, así que un texto sin tilde nunca matcheaba un producto
cargado con tilde (o viceversa): cada variante terminaba dando de alta un
producto nuevo.

Decisión: `_match_product` ahora compara con `_strip_accents` de los dos
lados, igual que `_match_province`. La lógica de modificadores
descalificantes se mantiene igual, solo corre sobre el texto ya normalizado.

Descartado: normalizar tildes al guardar el nombre del producto (forzar
"Azucar blanca" sin tilde en la base). El nombre visible para el operador
debe llevar tilde correcta; normalizar es un problema de comparación, no de
almacenamiento.

Consecuencia: de acá en más, un anuncio real con o sin tilde matchea el
mismo producto — no se van a seguir generando duplicados por esta causa. Los
duplicados que ya existen en la base se limpian aparte (ver próxima entrada).

---

## 2026-08-08 — product_name_guess recorta viñetas de lista del OCR

Contexto: mismo hallazgo que la entrada anterior — revisando duplicados
reales apareció "4) Azúcar blanca" y "4 Azúcar parda" como nombres de
producto. El "4)" o "4 " es una viñeta de lista que el OCR deja pegada al
principio del renglón ("4) Azúcar blanca a 22500", patrón repetido en varios
anuncios reales); `_guess_product_phrase` solo recortaba espacios, guiones,
viñetas "•" y tabs al principio (`.strip(" -•\t")"`), no dígitos de lista.
El operador confirmó el alta sin editar el nombre y el "4)" quedó pegado.

Decisión: `_guess_product_phrase` aplica un segundo recorte con
`_LIST_MARKER_RE` (`^\d{1,2}[).:]?\s+`) después del `.strip()` existente,
antes de devolver la frase candidata.

Descartado: intentar detectar y quitar viñetas en cualquier posición del
texto (no solo al principio). El patrón real observado siempre es "número +
puntuación opcional + espacio" al inicio del renglón — cubrir más que eso es
generalizar sin evidencia.

Consecuencia: los nombres adivinados de acá en más llegan limpios de
viñetas; no previene otro tipo de ruido de OCR en el nombre (ver la
decisión de "canal origen busca en varias líneas" sobre el mismo tipo de
ruido tolerado — el operador sigue revisando antes de confirmar).

---

## 2026-08-08 — Limpieza de duplicados reales en la base de producción

Contexto: el operador reportó productos repetidos en `products` mientras
probaba los slices del día. La causa raíz eran las dos fallas de arriba
(tildes sin normalizar en `_match_product`, viñetas de lista sin recortar en
`product_name_guess`), ya corregidas — esta entrada es la limpieza de los
datos que esas fallas ya habían dejado escritos.

Decisión: script puntual (no migración Alembic, es DML no DDL) que:
reasignó las filas de `prices_history` de "Azucar blanca" (`b787d5b4`) y "4)
Azúcar blanca" (`17412d6a`) hacia el producto canónico "Azúcar blanca"
(`859a13f0`), borró esos 2 duplicados, y renombró "4 Azúcar parda"
(`8ae6a2f0`) a "Azúcar parda" (no era duplicado, solo tenía la viñeta "4 "
pegada al nombre real). Se confirmó el plan exacto (qué producto es
canónico, cuántas filas se reasignan, qué se borra) con el operador antes de
tocar la base real, dado que el dashboard corre en producción.

Descartado: soft-delete o columna de estado para los duplicados en vez de
borrar. Son 2 filas sin ningún dato propio más allá del nombre —no hay nada
que preservar—, y el esquema de `Product` no tiene esa columna; agregarla
para este caso puntual sería tocar el esquema para un problema que no lo
necesita.

Consecuencia: `products` pasó de 15 a 13 filas. "Azúcar blanca" concentra
ahora las 3 referencias de `prices_history` que antes estaban repartidas
entre 3 productos. Sin script de reconciliación automática: si aparecen
duplicados nuevos por otra causa, se revisan y limpian igual que esta vez.

---

## 2026-08-08 — Líneas de puro URL/símbolo: ya cubiertas, sin código nuevo

Contexto: un anuncio real (venta de arroz, Santiago de Cuba) trae mucho ruido
de OCR — línea de invitación a grupo de WhatsApp con URL, líneas con un solo
carácter suelto, saltos de línea irregulares. Antes de agregar un filtro
explícito para descartar URLs y líneas de puro símbolo/emoji, se verificó
contra el motor real si hacía falta.

Decisión: no hace falta código nuevo. `_extract_price` y `_extract_quantity`
ya devuelven `None` para esas líneas (ninguna regex de precio/cantidad
matchea una URL o una línea sin letras ni dígitos), así que la regla que ya
existe en `propose_all` ("sin producto y sin precio, se descarta") las filtra
sola. Se agregaron tests con el caso real (URL de WhatsApp, línea de
separadores, línea de solo emoji) como cobertura de regresión de este
comportamiento, sin tocar `engine.py`.

Descartado: agregar un filtro explícito de "línea basura" antes del loop
principal. Hubiera sido código muerto — ninguna de las líneas que cubriría
llega a producir una propuesta hoy.

Consecuencia: el ruido de OCR "puro" (URLs, símbolos, líneas de un
carácter) no es la causa de las tarjetas parciales que se ven en anuncios
como este — la causa real es la de la entrada anterior sobre "nombre
arriba, precio abajo sin repetirlo" (`Arroz Santiago de cuba` matchea sin
precio, `Pacas de 30... a $730` matchea precio sin producto). Esa queda
pendiente, deliberadamente, hasta tener más volumen real que muestre qué
tan común es el patrón (ver `docs/backlog.md`).

---

## 2026-08-10 — Cobertura de provincias en Fase 0: son 2, no 16

Contexto: el backlog traía el ítem "revisar diccionario de abreviaturas de
provincia" como abierto, "bloqueado por: volumen real que cubra el resto de
las provincias" — como si Fase 0 necesitara eventualmente las 16. El
whitepaper (§9 Fase 0, línea 1633) fija el alcance en exactamente 2
provincias (La Habana y Santiago de Cuba); las 16 son el techo de ingesta de
fases posteriores (§9.0, ya registrado 2026-08-06), no una meta de Fase 0.

Decisión: se cierra el ítem del backlog. Las 2 provincias vigentes ya están
validadas contra los 8 anuncios reales existentes (`decisions.md`,
2026-08-08); no hace falta acumular más anuncios para dar por completo el
diccionario dentro del alcance actual, porque esos anuncios seguirían siendo
de las mismas 2 provincias.

Descartado: dejar el ítem abierto esperando "más volumen". Cargar cientos de
anuncios adicionales de Habana/Santiago no agrega cobertura de provincia
nueva — el volumen que hace falta es de otro tipo (para H0), no de
provincias.

Consecuencia: el backlog deja de sugerir una meta de cobertura que Fase 0 no
pide. Cuando Fase 1/2 sumen una provincia nueva (configuración, según §9.0),
su alias se agrega en ese momento — no antes. También se sacó del backlog el
ítem de "canal origen", ya implementado desde el 2026-08-08 y nunca removido.

---

## 2026-08-10 — Columna `published_date` en `prices_history`

Contexto: el backlog traía abierto el campo "fecha de publicación" (§6.1c del
whitepaper), bloqueado por "cambio de esquema → flujo completo de
especificación". Se corrió ese flujo (spec/design/tasks) para la migración
0005, la columna en el modelo y la heurística de sugerencia en `engine.py`.

Decisión: `published_date DATE NULL`, sin default y sin CHECK de fecha futura
(la validación de fecha futura vive en la capa web y degrada a `NULL`, no en
un CHECK de base). Un solo input a nivel de anuncio en la cola de curaduría,
propagado igual a todas las filas de `prices_history` que genera ese anuncio.
`guess_published_date` reutiliza el mismo grupo de regex que ya matchea
`_SOURCE_REFERENCE_RE` para `guess_source_reference`, con un helper
compartido (`_match_source_line`).

Descartado: un `CHECK (published_date <= created_at)` en base — convertiría
un dato secundario en un fallo de transacción. También se descartó un input
de fecha por fila: el anuncio tiene una sola fecha de publicación real.

Consecuencia: sin backfill — todo lo curado antes de esta migración queda con
`published_date = NULL`. Ningún consumidor (dashboard, métricas de H0) lee
esta columna todavía; queda disponible para cuando se decida usarla como eje
temporal.

---

## 2026-08-10 — Transporte celular → bandeja de entrada: Syncthing, no cloud+script

Contexto: el operador quiere que las capturas de pantalla y fotos que toma en
el celular lleguen solas a `inbox/` en el VPS. El whitepaper (§6.1 Etapa 1,
línea 558) no fija el mecanismo — "el único requisito es que el material
llegue al directorio de entrada". La opción evaluada inicialmente era carpeta
de un servicio en la nube (Google Drive/Dropbox) más un script con `rclone`
corrido por cron.

Decisión: Syncthing. Paquete del repositorio de Ubuntu, servicio systemd
`syncthing@ariel` corriendo como el usuario `ariel` (sin Docker, sin root),
sincroniza `inbox/` directo entre el celular y el VPS, sin intermediario en
la nube. Del lado del VPS la carpeta queda en modo `receiveonly`: el celular
empuja archivos, el VPS nunca escribe ni borra hacia el celular — protege el
original en el celular incluso si algo corre mal en el VPS (ej. el script de
ingesta moviendo duplicados dentro de `inbox/`). GUI de administración atada
a `127.0.0.1:8384` (no expuesta a internet); puerto de sincronización
`22000/tcp` y `22000/udp` abiertos en `ufw`.

Descartado: cloud provider + `rclone` + cron. Funciona, pero suma una
dependencia externa (cuenta de Drive/Dropbox) y un script propio a mantener.
Syncthing sincroniza directo, sin intermediario, y reacciona a archivos
nuevos por sí solo (fs watcher) en vez de sondear en un intervalo fijo.

Consecuencia: los archivos van a aparecer solos en `inbox/`, pero **nadie los
registra todavía** — `python -m saimc.ingestion` sigue siendo un paso manual,
no hay temporizador que lo corra. Si eso se vuelve fricción real, se evalúa
un `systemd timer` aparte (es solo scheduling de un script ya idempotente,
no una cola de trabajos — no rompe la decisión 3 de `CLAUDE.md`).

---

## 2026-08-10 — La ingesta de la bandeja se engancha en `GET /curation`, no en un timer

Contexto: con Syncthing (entrada anterior) los archivos llegan a `inbox/` de
forma continua, pero el registro en `raw_ingestions` seguía dependiendo de
correr `python -m saimc.ingestion` a mano por SSH. La alternativa evaluada
era un `systemd timer` que corriera ese script cada N minutos.

Decisión: `curation_queue()` (`GET /curation`) llama a `ingest_inbox()` como
primer paso, antes de leer el `pending` más antiguo — la rutina real del
operador ya es abrir esa página una vez al día para curar en bloque
(whitepaper §6.1 Etapa 1), así que la ingesta viaja gratis con esa visita, sin
sumar ningún servicio nuevo.

Descartado: el `systemd timer`. Es una pieza de infraestructura más para
mantener, resolviendo un problema que la propia rutina del operador ya
resuelve gratis. Se revisita si alguna vez hace falta que las filas existan
*antes* de que alguien abra la cola (hoy no hay ese caso).

Consecuencia: `GET /curation` deja de ser una lectura pura — también mueve
archivos de `inbox/` a `data/raw/` y escribe filas nuevas. Sin aislar
`SAIMC_INBOX_DIR`/`SAIMC_RAW_STORE_DIR` en los tests, correr la suite hubiera
escaneado y movido archivos reales del `inbox/` del repo (los que llegan por
Syncthing) — el fixture `client` de `tests/test_curation.py` ahora los apunta
siempre a un `tmp_path` aislado. El script `python -m saimc.ingestion` queda
vivo para correrlo a mano fuera del navegador (debugging).

---

## 2026-08-10 — Deploy incompleto: faltó `alembic upgrade head` tras el restart

Contexto: después de pushear `published-at-field` y reiniciar `saimc.service`,
curar un anuncio real dio 500 (`UndefinedColumn: published_date`). El
`systemctl restart` trajo el código nuevo, pero nadie corrió la migración
`0005` contra el PostgreSQL real — quedó en `0004`. Los tests corren contra
SQLite en memoria con `Base.metadata.create_all()` (ver `tests/conftest.py`),
así que nunca hubieran detectado esto: no ejercitan Alembic contra el motor
real.

Decisión: se corrió `alembic upgrade head` a mano contra producción (`0004 →
0005`). Ningún dato se perdió — la fila que falló nunca llegó a insertarse
(transacción completa o nada), el `raw_ingestion` que la originó sigue
`pending` para volver a curarla.

Descartado: nada — no hay CI/CD todavía (decisión 4 de `CLAUDE.md`), así que
no hay un paso automático que hubiera evitado esto sin agregar la
infraestructura que esa decisión pospone a propósito.

Consecuencia: deploy manual de un cambio con migración es **dos pasos, no
uno** — `systemctl restart saimc` Y `alembic upgrade head`, en ese orden no
importa cuál primero mientras se hagan los dos. Sin CI/CD que lo automatice,
queda como recordatorio explícito acá hasta que el volumen real lo justifique.

---

## 2026-08-10 — Backup diario de PostgreSQL: `pg_dump` + systemd timer, local

Contexto: con Syncthing y la ingesta enganchada en `/curation` (entradas
anteriores), la carga real empezó a entrar rápido — la medición de H0 depende
enteramente de estos datos y no existía ningún backup de la base.

Decisión: `scripts/backup_db.sh` corre `pg_dump` contra `SAIMC_DATABASE_URL`
(leída de `.env`, sin duplicar la credencial en el script), comprime, y
retiene los últimos 7 días. `systemd/saimc-backup.{service,timer}` (instalados
a mano en `/etc/systemd/system/`, mismo patrón que `saimc.service` y
`syncthing@ariel`) lo corren todos los días a las 3am UTC, con
`Persistent=true` para que se ponga al día si el VPS estuvo apagado a esa
hora. Los dumps quedan en `backups/` (gitignored, mismo motivo que
`inbox/`/`data/`: contienen los datos completos de la base).

Descartado: backup fuera del VPS (S3, otro servidor). Es el límite conocido
más importante de esta decisión — si el disco del VPS se pierde entero, se
pierde el backup también. No se agrega ahora por simplicidad y porque no hay
evidencia todavía de que el riesgo lo justifique frente al costo de sumar una
dependencia externa (conexión intermitente, decisión de contexto de
`CLAUDE.md`); se revisita si el volumen real lo amerita.

Consecuencia: primer artefacto de infraestructura versionado en el repo
(`systemd/`) — antes `saimc.service` se armó a mano y nunca quedó en git. No
se agregó retroactivamente por estar fuera del alcance de este slice.

---

## 2026-08-10 — Rediseño visual de la cola de curaduría: Bootstrap, no Tailwind

Contexto: pedido explícito del usuario (ex-frontend) de mejorar el look de
`/curation`, sin especificar herramienta ("Bootstrap o Tailwind, no sé").

Decisión: se queda con Bootstrap 5 (ya vendorizado local en
`static/bootstrap.min.css`) y se suma `static/curation.css` encima con
paleta, tipografía y tarjetas propias. Cambio solo visual: ningún `name`,
`id`, ni la relación de atributos de la que dependen los tests
(`tests/test_curation.py` — ej. `value="X" selected`, `class="mt-1" open`) se
tocó.

Descartado: Tailwind. En este proyecto exigiría un CDN externo (dependencia
de red que CLAUDE.md pide evitar) o un pipeline de build con Node — ninguno
de los dos existe hoy y sumarlo es más que un ajuste de interfaz.

Consecuencia: botones "Curar anuncio"/"Descartar" pasan a vivir fuera de sus
`<form>` y se conectan por atributo `form="id"` (HTML5) para poder agruparlos
visualmente sin anidar un `<form>` dentro de otro. Sin acceso a navegador
desde esta sesión (VPS headless): el cambio corre contra la suite completa
(153/153 verdes) y se sirvió en caliente, pero falta la revisión visual real
del usuario.

---

## 2026-08-11 — H0 mide también las imágenes, re-corriendo el OCR sobre el original

Contexto: el operador cuestionó el valor de H0 ("aunque haya errores de
extracción yo en el formulario lo arreglo"). Corriendo la medición contra la
base real apareció la causa: de 29 anuncios curados, 27 son imágenes, y
`_curated_text_ingestions` filtraba `content_type == TEXT`. El 93% del corpus
real no entraba en la medición — su docstring todavía decía "sin OCR en este
slice", frase que quedó vieja el 2026-08-07, cuando el OCR se conectó a la cola
de curaduría. La métrica se calculaba sobre 2 anuncios.

Decisión: el universo de las cinco métricas pasa a ser todo `raw_ingestion`
curado, sea texto o imagen. Para una imagen, el texto evaluado sale de
**re-correr el OCR sobre la evidencia original preservada**, no de
`original_text`. `compute_h0_report` recibe el transcriptor como parámetro
(`transcribe: Transcriber = ocr.transcribe`) para que la suite no dependa del
binario de Tesseract. Se agrega `image_ocr_empty_count` al reporte: un OCR que
devuelve "" es indistinguible de una extracción fallida dentro del porcentaje,
y sin ese contador un Tesseract ausente hundiría las métricas sin que nada lo
delate.

Descartado: usar `original_text` de la imagen, que es lo que ya está guardado y
no habría costado ninguna llamada a Tesseract. En una imagen ese campo guarda la
transcripción **ya corregida por el operador** (`curation.py:465`): medir sobre
ella evaluaría las reglas contra un texto que un humano limpió, inflando el
número justo en la dimensión que H0 pregunta. El propio módulo advertía contra
medir "un eco de sí misma" (§3 línea 286 incluye explícitamente "información en
imágenes"; §6.1 línea 587 pone al OCR dentro de lo supervisado). El archivo
original es inmutable, así que la transcripción es reproducible.

Consecuencia: el número cae fuerte al dejar de estar sesgado — extracción
completa correcta pasa de 54.5% (33 filas, 2 anuncios) a 31.7% (60 filas, 29
anuncios). No es una regresión: es la primera medición que mira el corpus real.
Queda por debajo del umbral de "detener y replantear" (<70%, §3), pero con 60
de las 300+ filas que H0 exige **no es todavía la medición oficial** y el script
lo avisa. La medición ahora ejecuta Tesseract una vez por imagen curada: a 300+
anuncios va a tardar, y si eso molesta se cachea — no antes. La sub-métrica que
más arrastra es cantidad/unidad (40.0%); §12.5 ya anticipó ese cuello de botella
y fija la respuesta correcta: acotar los productos bandera a los de unidad de
venta estable, no bajar el estándar.

---

## 2026-08-11 — Composición del corpus revisada: sin contaminación formal, sin cambios

Contexto: el operador planteó que el mercado informal lo mueven vendedores
individuales de WhatsApp y Facebook sin TCP ni MIPYME, y sospechó que el corpus
se estuviera llenando de grandes negocios. La sospecha tenía base: en los
anuncios reales ya había aparecido "CIMEX, Sucursal Santiago de Cuba"
(`decisions.md`, 2026-08-08), que es una cadena estatal, y el whitepaper §5
—vinculante— deja el comercio formal explícitamente del lado de un competidor
("A Ver Que Sale"), no de SAIMC.

Decisión: no se cambia nada. Clasificados los 29 anuncios curados por marcadores
de texto, 18 son capturas de WhatsApp de vendedor individual, 2 traen marcadores
de mayorista o página comercial, y 9 no dan señal clara. El corpus ya está
dominado por exactamente la fuente que el whitepaper declara como objeto. Lo de
CIMEX era un anuncio suelto, no un patrón.

Descartado: agregar una columna de tipo de vendedor a `raw_ingestions` para
clasificar formal/informal. Es un cambio de esquema —flujo completo de
especificación— para separar un problema que la medición muestra que hoy no
existe. Si la proporción se invierte al subir el volumen, se revisita con datos.

Consecuencia: queda medido y fechado que al 2026-08-11 el corpus no está
contaminado, así que una sospecha futura se contrasta contra este número en vez
de volver a discutirse de memoria. Hallazgo lateral registrado en el camino:
`guess_source_reference` prellena **0/29** contra este corpus — la heurística se
calibró el 2026-08-08 contra anuncios de Facebook con el patrón "Nombre - Ayer a
las H:MM", que las capturas de WhatsApp no tienen. Por eso 26/29 anuncios
tienen "Canal origen" vacío. Va al backlog, no se toca acá.

---

## 2026-08-11 — Teléfonos: se redactan al curar, con backfill de lo ya guardado

Contexto: revisando la composición del corpus (entrada anterior) apareció que
**20 de 29 anuncios curados tenían teléfonos almacenados** en
`raw_ingestions.original_text` — encabezados de chat de WhatsApp que el OCR
transcribe ("+53 5 5550101 Hoy, 9:22 a. m.") y hashtags de contacto
("#llame_escriba_55550303"). `CLAUDE.md` lo prohíbe sin matices: «no capturamos
ni almacenamos teléfonos, direcciones exactas ni datos de contacto de
anunciantes» (riesgo §10.6). Estaban en la base de producción y, desde el
2026-08-10, en los backups diarios.

Decisión: `saimc/privacy.py` con `redact_personal_data()`, aplicado en el único
punto donde la curaduría persiste texto capturado (`curation.py`, sobre
`original_text` y `source_reference`). Más `scripts/redact_existing.py` (DML, no
migración — mismo criterio que la limpieza de duplicados del 2026-08-08) para lo
ya guardado, con dry-run por default. Aplicado contra producción tras verificar
el backup de las 03:00 UTC: 19 ingestas de imagen redactadas, 1 de texto
deliberadamente intacta. El redactor cubre **solo teléfonos**: las direcciones
exactas son reconocimiento de entidades, no regex, y hoy no hay volumen que lo
justifique.

Descartado: (a) redactar solo en la capa de publicación, que es la lectura de la
contradicción 1 de `whitepaper-map.md` («es un problema de publicación, no de
almacenamiento»). No cumple la regla: `CLAUDE.md` dice *almacenar*, no
*publicar*, y los números seguirían en los backups. Cuando los dos documentos
chocan, gana `CLAUDE.md`. (b) Redactar también las ingestas de TEXTO. Ahí
`original_text` es la evidencia escrita en la ingesta, no un campo nulable
completado después: reescribirla sí rompe la inmutabilidad de `raw_ingestions`.
Para imágenes no hay conflicto, porque el precedente del 2026-08-07 ya
estableció que completar `original_text` en la curaduría no viola nada —lo
intocable es `file_path`, `content_hash`, `captured_at`, `content_type`.

Consecuencia: **la redacción es de texto, no de píxeles**. La captura en
`data/raw/` sigue mostrando el número en la imagen, y ese archivo es la
evidencia inmutable que la Transparency Layer publicaría — la contradicción 1
del mapa sigue abierta y ahora con un caso concreto. Queda además 1 ingesta de
texto con teléfono (`53de1590-…`) esperando una decisión sobre inmutabilidad
vs. privacidad. La medición de H0 no se movió (31.7% antes y después): para
imágenes se re-corre el OCR sobre los píxeles, así que redactar la columna no
toca el número — propiedad heredada de la decisión anterior de este mismo día.

---

## 2026-08-11 — Redacción de teléfonos en toda la base; el archivo preservado no se toca

Reemplaza el alcance de la entrada anterior de hoy («Teléfonos: se redactan al
curar, con backfill de lo ya guardado»), que dejaba fuera las ingestas de texto
y no cubría la ingesta. El diseño del redactor no cambia.

Contexto: la entrada anterior tapaba solo la curaduría, y la curaduría solo
escribe `original_text` para imágenes. Quedaban dos huecos. (1)
`ingestion/inbox.py` lee el `.txt` y guarda su contenido crudo: cualquier
archivo de texto nuevo entraba con el teléfono intacto, y había 5 esperando en
`inbox/`. (2) Una ingesta de texto ya cargada seguía con el número en base.
Pedido explícito del operador: sanar todo e impedir que entren.

Decisión: `redact_personal_data` se aplica también en `ingest_file`, sobre el
texto que va a la base. El backfill pasa a cubrir imagen y texto por igual. La
colisión con «`raw_ingestions` es inmutable» se resuelve a favor de la
privacidad, con un límite preciso: **se redacta la columna, no el archivo**.
`data/raw/` queda intacto y `content_hash` lo sigue describiendo — el hash se
calcula sobre el archivo, nunca sobre el texto redactado, así que la
deduplicación de recapturas sigue funcionando (hay test que lo fija).
Verificado después de aplicar: 0 de 64 ingestas con teléfono en base.

Descartado: redactar también los archivos de `data/raw/`. Rompería la línea 568
del whitepaper («el archivo original nunca se modifica»), dejaría `content_hash`
describiendo algo que ya no está en disco, y —lo decisivo— **no se puede hacer
de forma consistente**: 62 de los 64 archivos son imágenes con el teléfono en
los píxeles, que solo se borra destruyendo la evidencia. Sanear 1 `.txt` de 63
archivos comprometidos compra poco y cuesta la garantía de integridad. El lugar
correcto para ese problema es la capa de publicación, que todavía no existe.

Consecuencia: la base y los backups quedan limpios, que es la superficie de
exposición real hoy (la aplicación lee de ahí; `pg_dump` corre todas las
noches). Queda declarado y acotado el residuo: 1 `.txt` y 62 imágenes en
`data/raw/` conservan el número. Eso convierte la contradicción 1 de
`whitepaper-map.md` en un bloqueante concreto de la Transparency Layer: no se
puede publicar la evidencia literal sin resolver antes el recorte o la
redacción de la captura.

---

## 2026-08-11 — Productos piloto, provincias piloto y regla minorista

Contexto: la medición de la matriz mostró 60 filas repartidas en 48 productos —
85% con una sola observación— y una sola provincia. Una celda con una
observación no produce mediana, así que el sistema no podía publicar un solo
precio pese a tener datos. El whitepaper ya fijaba el alcance en «3-5 productos
bandera» (§9 Fase 0, línea 1632) y 2 provincias (línea 1633); la dispersión era
un apartamiento no declarado, no una decisión.

Decisión: los pilotos son **arroz, pollo entero, azúcar y aceite**, en **La
Habana y Santiago de Cuba**. Se eligieron contra la oferta real del flujo de
captura (64 anuncios transcritos), no por intuición: azúcar 9% de cobertura,
arroz 6%, pollo 5%, aceite 2%. Se suma la **regla minorista**: esos cuatro se
cargan solo cuando el anuncio da precio por la unidad canónica (libra para
arroz/pollo/azúcar, litro para aceite); los anuncios mayoristas se saltean para
esas celdas.

Descartado: (a) agregar `unit` por fila a `prices_history`, que resolvería
estructuralmente la mezcla minorista/mayorista. Es cambio de esquema y exige
flujo completo de especificación. Se difiere con un argumento que no es de
simplicidad: con la regla minorista **todas** las filas quedan en unidad
canónica, así que el backfill futuro es `unit = product.unit`. Cargar mezclado
sin registrar unidad es lo que volvería caro ese cambio, no postergarlo. (b)
Elegir los pilotos por la lista de ejemplo del whitepaper sin contrastarla:
huevos, que ahí figura, no aparece en ningún anuncio real capturado.

Consecuencia: se pierde el dato mayorista de las celdas piloto — de las 6 filas
de azúcar cargadas, sobreviven 2. Se acepta a conciencia: §8.1 Nivel 0 define
el objetivo como «resolver la necesidad diaria del hogar cubano», y un hogar no
compra sacos de 25 kg. Aceite queda como el piloto más débil (2% de cobertura,
~6 observaciones proyectadas cada 300 anuncios) y no debe esperarse densidad
temprana de esa celda. El límite real medido: solo 11 de 64 anuncios (17%)
mencionan algún piloto, y ninguno es de La Habana — eso no lo arregla el
código, lo arregla la rutina de captura.

---

## 2026-08-11 — Vista de control del operador: mediciones, nunca veredictos

Contexto: el circuito estaba abierto. Se capturaba, se curaba, y no había
ninguna vista de salida — la app tenía exactamente dos rutas (`/curation` y
`/evidence`). Los dos errores de clasificación encontrados hoy (derivados de
pollo cayendo en el producto genérico, y la mezcla minorista/mayorista en
azúcar) salieron de consultas SQL hechas a mano, no del sistema: el operador no
tenía forma de verlos. Curar en volumen sin cerrar ese circuito es fabricar
deuda a velocidad de curaduría.

Decisión: `GET /control` (`saimc/cells.py` + `web/routers/control.py`) resume
`prices_history` en celdas producto × provincia × moneda y muestra `n`,
mediana, mínimo, máximo y amplitud (máximo/mínimo), ordenadas por tamaño de
muestra. La moneda entra en la clave porque `price_cup` sigue vacío en las 60
filas: promediar CUP con USD sería inventar una conversión que el sistema no
tiene. La mediana se calcula en Python y no con `percentile_cont` porque los
tests corren sobre SQLite.

Descartado: marcar celdas como «muestra insuficiente» o «dispersión alta».
§12.6 (línea 1961) es explícito: «un umbral asignado por intuición y presentado
como metodología es peor que no tener umbral». N no está fijado y se calibra
por «análisis de estabilidad de la mediana frente al tamaño de muestra» — esta
vista es justamente el instrumento que lo va a permitir. La vista informa;
juzga el operador. También se descartó construir el dashboard público de Fase 1
(mapa, semáforos): con 91% de celdas de una observación sería una vitrina sin
mercadería.

Consecuencia: la amplitud delató de inmediato lo que estaba oculto — azúcar en
Santiago da mediana 11.490 CUP con amplitud 56×, porque mezcla 420 CUP la libra
con 23.500 CUP el saco. Ese número no significaba nada y nadie podía verlo.
Cada sesión de curaduría pasa a ser verificable de un vistazo, y la vista es
además el primer paso hacia la matriz que el whitepaper define como núcleo
(§5, «objeto medido»).

---

## 2026-08-11 — Limpieza de filas fuera de la regla minorista, y "para bebé" como modificador

Contexto: la regla minorista (entrada anterior de hoy) rige de acá en adelante,
pero `prices_history` ya traía filas cargadas bajo el criterio viejo. La vista
de control las exponía: azúcar en Santiago daba mediana 11.490 CUP con amplitud
56×. Sin limpiarlas, la amplitud iba a seguir alta aunque el operador curara
perfecto, y eso se leería como que la regla no funciona.

Decisión: se borran 7 filas de `prices_history` (DML puntual, no migración —
mismo criterio que la limpieza de duplicados del 2026-08-08), clasificadas una
por una contra el texto del anuncio que las originó, no por su magnitud:

- Azúcar: 3 sacos mayoristas (21.500/55, 21.600/25 kg, 23.500/25 kg) y 1 fila
  minorista **por kilo** («Azúcar blanca 1 kg 1380.00»). Esta última no es
  mayorista, pero mezclar kg con lb es el mismo error en menor escala.
- Arroz: 2 sacos de 25 kg del mismo anuncio mayorista (17.500 y 31.000).
- Aceite: 1 fila que no era aceite. El texto decía «**Aceite para bebé** 100
  ML 1120.00 cup» y el matcher genérico la cargó como el producto «Aceite»,
  que es aceite de cocina y producto piloto.

Ese último caso se corrigió además en el motor: `"bebe"` entra en
`_PRODUCT_DISQUALIFYING_MODIFIERS`. No es un corte ni un derivado del mismo
bien —las dos categorías que la lista ya cubría— sino otro artículo que
comparte la palabra, así que la lista gana una tercera categoría.

Descartado: borrar por umbral de precio o de cantidad, que habría sido más
rápido. Habría acertado con los sacos y fallado con el aceite de bebé (1.120
CUP es un precio perfectamente plausible para un litro de aceite de cocina).
La clasificación tenía que salir del texto original, que para eso se preserva.
También se descartó pinear el `product_name_guess` exacto en el test del
artículo de bebé: hoy arrastra el «100 ML» porque ML no se reconoce como unidad
de cantidad, y fijar ese recorte convertiría un detalle de presentación en un
contrato.

Consecuencia: `prices_history` pasa de 60 a 53 filas. Las celdas piloto quedan
en amplitud 1.0× —una sola observación cada una— que es el punto de partida
honesto para medir si la regla minorista funciona: si mañana la amplitud sube,
algo entró mezclado. Queda anotado un hueco menor: «100 ML» no se reconoce como
cantidad, así que los envases en mililitros llegan sin cantidad extraída.

---

## 2026-08-11 — Precisión de la regla minorista: el precio derivado de un saco no cuenta

Precisa la entrada «Productos piloto, provincias piloto y regla minorista» de
hoy, que decía «precio por la unidad canónica» sin resolver un caso ambiguo.
Corrige además una instrucción equivocada que se le había dado al operador:
que si un anuncio mayorista traía el precio por libra calculado, ese sí se
podía cargar.

Contexto: el operador detectó el hueco. Los anuncios mayoristas suelen cerrar
con «$17.500 el saco. Sale a $318.20 Lb». Ese número parece cumplir la regla
—es un precio por libra— pero no lo es: verificado contra los anuncios reales,
17.500 / (25 kg × 2,2046) = 317,52 y 23.500 / (25 kg × 2,2046) = 426,38, contra
los 318,20 y 427,27 que declaran. **Es exactamente la división del precio del
saco por las libras que trae.** Nadie vende a ese precio: la compra mínima
sigue siendo un saco.

Decisión: el criterio no es «el anuncio expresa un precio por unidad canónica»
sino **«¿existe una transacción a ese precio por esa unidad?»**. La formulación
del operador es la operativa: si no puedo ir con una jabita y comprar una libra
a ese precio, no se carga. Un anuncio que solo vende sacos se descarta entero,
traiga o no el precio por libra calculado. Un anuncio que ofrece las dos
modalidades carga solo la línea minorista.

Descartado: aceptar el precio derivado para no perder observaciones. Habría
sido tentador porque duplicaría la muestra de las celdas piloto y porque los
números están cerca —en los dos casos observados el minorista real quedó a
+0,6% y −1,7% del derivado—. Pero esa cercanía es una observación con n=2, no
una equivalencia: son transacciones distintas y el derivado es sistemáticamente
el de quien compra 25 kg. Como en el corpus hay más anuncios mayoristas que
minoristas, admitirlo habría arrastrado la mediana hacia un precio que ningún
hogar paga, y la serie habría medido la aritmética del mayorista en vez del
mercado.

Consecuencia: la regla es más restrictiva de lo que parecía y las celdas piloto
van a llenarse más despacio. Se acepta: §8.1 Nivel 0 define el objetivo como
«resolver la necesidad diaria del hogar cubano», y el precio que le sirve a un
hogar es aquel al que puede comprar. Queda como hipótesis a vigilar —no como
supuesto— que el minorista de Santiago cotiza cerca del derivado mayorista; si
se sostiene con muestra real, es un hallazgo de mercado que merece su propia
entrada.

---

## 2026-08-12 — `sale_format` por fila: la prueba de la jabita se guarda, no descarta

Reemplaza dos entradas anteriores. (1) 2026-08-07 «Medición de H0: unidad
comparada contra el catálogo, no contra una fila propia», que descartaba
agregar `prices_history.unit` por no justificar el flujo completo de
especificación. (2) 2026-08-11 «Precisión de la regla minorista: el precio
derivado de un saco no cuenta», que aplicaba la prueba de la jabita para
descartar el anuncio mayorista entero en vez de cargarlo.

Contexto: medir cuánto costaba en la práctica la regla del 2026-08-11 mostró
que descartaba alrededor del 60% de los datos aprovechables: sobre 64 anuncios
reales, solo 2 observaciones minoristas genuinas de precio por libra
sobrevivían, con pollo y aceite en cero. La regla protegía la mediana de la
aritmética del mayorista, pero al precio de vaciar las celdas piloto que
debían alimentar H0.

Decisión: `prices_history` suma tres columnas nulables — `unit`, `sale_format`
(`retail`/`wholesale`, con CHECK) y `packaging` — y la cola de curaduría pide
`sale_format` como pregunta explícita del operador, independiente de `unit` y
`quantity`, nunca inferida de aritmética. La prueba de la jabita sobrevive
intacta como criterio para responderla, pero cambia de rol: ya no decide si el
anuncio se carga, decide cómo se etiqueta la fila que sí se carga. Esto
también corre la medición de H0 fuera de §12.5: la unidad ya no se compara
solo contra `Product.unit` del catálogo, sino que queda disponible por fila.

Descartado: mantener el descarte del 2026-08-11 y compensar con más volumen.
El déficit no era de cantidad de anuncios sino de qué fracción de cada anuncio
la regla dejaba pasar; más anuncios mayoristas no agregan observaciones
minoristas. También se descartó inferir `sale_format` de `unit` o de dividir
`price`/`quantity`: es exactamente el cálculo que la prueba de la jabita existe
para detectar, así que automatizarlo reintroduce el problema original.

Consecuencia: las filas mayoristas que antes se perdían ahora se guardan y
distinguen de las minoristas por `sale_format`, engordando las celdas piloto
en vez de vaciarlas. `unit`/`sale_format`/`packaging` quedan inertes en este
slice — ningún agregado (`cells.py`) ni métrica de H0 los lee todavía; conectar
`sale_format` al filtrado de celdas y a §12.5 es trabajo de un slice
posterior.

## 2026-08-12 — H0 pasa a métrica de eficiencia, no a criterio de salida de Fase 0

Contexto: el operador corrige cada error de extracción en el formulario de
curaduría antes de que la fila se persista, así que la precisión del extractor
no gobierna la calidad del dato guardado: ese dato es correcto por
construcción. Lo que la precisión gobierna es cuánto trabajo manual cuesta cada
anuncio. Con eso, un H0 bajo no significa que el mercado sea ilegible; significa
que el sistema queda limitado al volumen que una persona puede procesar a mano.
El whitepaper ya lo dice en §3 línea 300 —«lo que H0 condiciona es la capacidad
de escalar [...] el producto puede existir sin H0; la tesis del whitepaper
no»— pero la tabla de decisión de §3 línea 296 sigue diciendo «< 70% detener y
replantear», y §12.5 línea 1909 declara las métricas de extracción «las únicas
métricas relevantes durante la Fase 0». Las dos lecturas no conviven.

Decisión: H0 se mide igual, con la misma definición y contra el mismo conjunto
de 300+ anuncios reales etiquetados a mano, pero deja de ser el criterio de
avance o detención de la Fase 0. Pasa a ser el indicador de eficiencia que
gobierna cuándo el sistema puede escalar más allá de la curaduría manual, que
es exactamente el rol que §3 línea 300 ya le asignaba. Los umbrales 85/70 se
conservan como referencia de esa capacidad de escala, no como veredicto sobre
la viabilidad del proyecto.

Descartado: mantener «< 70% detener y replantear» como veredicto operativo.
Bajo curaduría manual ese umbral no describe ningún estado de fallo real: el
dashboard se alimenta de dato corregido a mano y entrega valor con H0 en
cualquier valor. También se descartó reemplazar H0 por una métrica de densidad
en el mismo movimiento: inventar un umbral de densidad hoy, sin datos que lo
respalden, es precisamente lo que §12.6 prohíbe.

Consecuencia: la Fase 0 conserva su criterio de salida, que §9 línea 1648
define como «H0 quedó respondida con un número» — eso no cambia. Lo que se cae
es la mitad condicional de esa misma línea, «si la precisión cae por debajo del
70%, la Fase 1 no arranca», junto con la tabla de veredictos de §3 (líneas
290-296) y la exclusividad que §12.5 línea 1909 le da a las métricas de
extracción. La Fase 1 arranca cuando H0 tiene un número, sea cual sea ese
número, más los entregables ya cuantificados en §9 línea 1645: 500 registros
validados de 3-5 productos en 2 provincias. Ese sigue siendo el objetivo de
densidad y no hizo falta inventarlo. Lo que un H0 bajo bloquea es la capa
predictiva y B2B, no el lanzamiento público. `docs/whitepaper-map.md` marca
como apartados §3 líneas 290-296, §12.5 línea 1909 y la segunda oración de §9
línea 1648.

---

## 2026-08-12 — El whitepaper se parte: `spec.md` vive, el pitch se congela

Contexto: el documento rector era `docs/whitepaper.md`, subtitulado literalmente
«Investor & Technical Whitepaper», con estado «Propuesta para Inversión». Tenía
dos audiencias y un solo registro: nada distinguía una decisión vinculante de un
argumento de venta. La consecuencia era medible. La §14.1 pedía financiar
«Dashboard Angular SSR + TimescaleDB» contra la §13.1, que cerró el stack en
Python para Fase 0-1. La §14.5 fijaba «Hito 1: 16 provincias» cuando el 2026-08-10
se registró que la Fase 0 son 2. Ninguna de las dos se detectó en la v3.4 ni en la
v3.5: nadie relee la mitad que no usa. Además, `docs/whitepaper-map.md`, con sus
secciones «Contradicciones conocidas», «Apartamientos deliberados» y «Lo que el
whitepaper no define», ya era un parche construido para compensar exactamente esto.

Decisión: se parte en dos archivos con fuente de verdad asimétrica.
`docs/spec.md` nace con §3, §5, §6, §7, §9, §10, §11, §12 y §13 —extracción
mecánica, byte-idéntica, verificada con `diff`— y es el documento rector vivo.
`docs/whitepaper.md` se renombra a `docs/pitch-v3.5.md` y se congela entero, con
un encabezado que declara que no rige y lista sus dos contradicciones sin
corregirlas. `docs/whitepaper-map.md` pasa a `docs/spec-map.md`, regenerado contra
los números de línea nuevos. Los números de sección se conservan intactos: faltan
§1, §2, §4, §8, §14 y §15 y ese hueco es deliberado, porque renumerar habría
invalidado de golpe las ~30 citas por sección del código y las migraciones.

Descartado: reescribir ya el pitch corto y citante. No hay conversación de
inversión activa, así que no tiene fecha de entrega, y es prosa de negocio que
escribe el autor, no una extracción mecánica. Se hace cuando haga falta, contra un
spec ya limpio. También se descartó un solo archivo con marcas de audiencia por
sección: no separa las capas, solo las etiqueta, y la deriva vuelve igual.

Apartamiento del plan aprobado: se acordó mover de §5 solo el bloque «Ejes de
diferenciación» (387-416 del original). Se movió §5 entera. Partirla habría dejado
una definición vinculante —el objeto medido que cita `src/saimc/cells.py`— dentro
de un documento declarado como no vigente, y habría puesto una misma sección en
dos archivos, que es justo el patrón de duplicación que este corte elimina. El
costo es que ~130 líneas de prosa de posicionamiento viajan dentro del spec.

Consecuencia: `spec.md` (1649 líneas) es lo que se consulta vía `spec-map.md`.
`pitch-v3.5.md` (2130) no rige y conserva copias de las secciones técnicas por
haberse congelado entero; si difieren, gana `spec.md`. Queda pendiente un segundo
slice: reescribir las citas del código de número de línea a ancla de sección
—varias ya estaban podridas, `alembic/versions/0001` cita «§7.1 líneas 1058-1077»
cuando §7.1 vivía en 1282-1307— y actualizar `CLAUDE.md`, `README.md` y
`openspec/config.yaml`, que todavía apuntan a `docs/whitepaper.md`.

---

## 2026-08-12 — Las citas al documento rector pasan a ancla de sección

Contexto: segunda mitad del corte del whitepaper (entrada anterior de esta misma
fecha). 31 archivos citaban `docs/whitepaper.md`, que ya no existe. Peor que el
renombre: catorce citas apuntaban a un número de línea, y varias ya estaban
podridas antes del corte. `alembic/versions/0001` citaba «§7.1 (líneas 1058-1077)»
cuando §7.1 vivía en 1282-1307; `models/raw_ingestion.py` citaba «línea 1074» para
un nombre de columna, «línea 448» y «líneas 421 y 427» para §6.1. El documento
creció de v3.3 a v3.5 agregando secciones intermedias: los números de sección
sobrevivieron, los de línea se corrieron ~224 posiciones sin que nada avisara.

Decisión: toda cita al documento rector va por **ancla de sección**, y cuando hace
falta precisión, por la frase del encabezado —`spec.md §6.1, «Ventaja adicional del
patrón»`— nunca por número de línea. La regla queda escrita en `CLAUDE.md`. Se
verificó que las ocho anclas nuevas existen textualmente en `spec.md`, una vez cada
una. La única excepción legítima es `docs/spec-map.md`, que existe justamente para
traducir sección → líneas y que declara arriba cómo verificar su propia vigencia
(`wc -l docs/spec.md`).

Descartado: mantener los números de línea actualizándolos al corte nuevo. Habría
reproducido el mismo mecanismo de podredumbre en la próxima edición de `spec.md`,
que es un documento vivo. También se descartó reescribir las citas de
`docs/decisions.md`, `docs/decisions/SETUP.md` y `openspec/changes/archive/**`: son
archivos de anexado o registros históricos, y reescribir el pasado para que
coincida con el presente es exactamente lo que `CLAUDE.md` prohíbe. Ahí «whitepaper»
sigue nombrando lo que se llamaba así cuando se escribió.

Consecuencia: 31 archivos actualizados —`src/`, `tests/`, `alembic/`, `CLAUDE.md`,
`README.md`, `docs/backlog.md`, `openspec/config.yaml` y la spec viva de
`processing-layer-schema`—. 224 tests pasan. `openspec/config.yaml` reemplaza las
claves `whitepaper`/`whitepaper_map` por `spec`/`spec_map`/`pitch_frozen`. Queda
abierto un detalle sin resolver: `CLAUDE.md` afirma que la carpeta `docs/decisions/`
no existe, y existe (`SETUP.md`). No se tocó en este slice.

---

## 2026-08-12 — CLAUDE.md dejaba de existir la carpeta docs/decisions/

Contexto: `CLAUDE.md` afirmaba «No existe una carpeta `docs/decisions/`. No la
busques ni la referencies». La carpeta existe desde el setup inicial del
proyecto (`cdb1c93`, «whitepaper v3.4 + contexto de proyecto») y contiene
`SETUP.md`, el handoff histórico de puesta en marcha con Claude Code.

Decisión: se corrige la afirmación. `docs/decisions/` existe pero no es el log
de decisiones — solo contiene el handoff histórico, sin relación con
decisiones de diseño. El log de decisiones sigue siendo exclusivamente el
archivo `docs/decisions.md`.

Descartado: mover o borrar `SETUP.md`. Es un registro histórico legítimo de
cómo arrancó el proyecto, no un error a corregir.

Consecuencia: `CLAUDE.md` ya no contradice el estado real del filesystem.

---

## 2026-08-12 — CLAUDE.md ya no dice que decisions.md no existe

Contexto: `CLAUDE.md` traía «Ese archivo todavía no existe: creálo en la
primera entrada» sobre `docs/decisions.md`, que a esta fecha tiene más de
1300 líneas y decenas de entradas desde 2026-08-06. Instrucción obsoleta del
día 0 del proyecto que nunca se actualizó.

Decisión: se elimina la frase. La regla que importa —anexar al final, no
reescribir entradas viejas— ya está cubierta por el resto de la sección
«Decisiones».

Consecuencia: ninguna. Es una corrección de una instrucción que ya no aplica.

---

## 2026-08-12 — La celda de control se parte por unidad y formato de venta

Contexto: revisando `/control` durante la curaduría apareció que la matriz
analiza por producto y no por producto comparable. `cells.py` agrupaba por
producto × provincia × moneda e ignoraba `unit` y `sale_format`, que existen
desde la entrada de hoy «`sale_format` por fila». Esa misma entrada dejó
anotado que las tres columnas quedaban inertes y que conectarlas era «trabajo
de un slice posterior»: este. El caso real: `Arroz` en Santiago mezclaba 730
por libra, 750 por kilo —las dos filas describiendo el mismo paquete de 1 kg— y
dos sacos de 110 lb a 31.000 y 33.000, todo en una sola mediana.

Decisión: la clave de celda pasa a producto × provincia × moneda × `unit` ×
`sale_format`. El nulo **no se funde con nada**: «sin responder» es su propia
celda, mismo criterio por el que `canonical_unit()` devuelve `None` en vez de
adivinar. Los desempates del orden se hacen explícitos porque partir la celda
multiplica los empates de `count` y sin ellos el orden lo decidiría el hash del
dict. La vista suma columnas Unidad y Formato.

Descartado: (a) agregar `saco` y `paquete` al vocabulario de `UNITS`, que fue
la propuesta inicial. Un saco no tiene magnitud —los datos reales traen sacos
de 25 kg, 50 kg y 110 lb— así que `unit=saco` no vuelve dos precios
comparables: mueve la mezcla un nivel arriba y la hace invisible. Es
exactamente el problema de resolución de entidades que `spec.md` §5 define como
el núcleo técnico del proyecto. Queda anotado que `caja`, ya presente en
`UNITS`, arrastra el mismo defecto. (b) Meter `packaging` en la clave: es texto
libre sin vocabulario cerrado («Saco de arroz De 110 lb»), así que produciría
una celda por variante de redacción. Si `unit` + `sale_format` no alcanzan,
darle vocabulario cerrado se decide con los datos de esta vista, no antes
(§12.6).

Consecuencia: 55 celdas donde antes había menos, y la separación deja ver algo
que estaba tapado: `sale_format` está respondido en **1 de 59 filas**, así que
los dos sacos de arroz siguen agrupados con la fila minorista de 730 —celda de
n=3, amplitud 45.2×, con formato «sin responder»—. El agregador ya no es el
límite; el límite es responder la prueba de la jabita al curar. Anotado además
un dato claramente erróneo a corregir: `Azúcar blanca` con `price=2.00` CUP y
`packaging="Paquete de azúcar blanca 2lb"`.

---

## 2026-08-12 — Corrección acotada de anuncios curados: historial + edición

Contexto: mientras curaba, el operador encontró `Azúcar blanca` cargada con
`price=2.00` CUP para 2 lb — un error de tipeo. No había forma de arreglarlo
sin SQL directo contra producción: `_require_pending` (curation.py) solo
acepta anuncios `pending`, y `raw_ingestions` es inmutable por regla del
proyecto, pero eso nunca alcanzó a `prices_history`. Un operador solo no puede
depender de que alguien le corra una query cada vez que se equivoca.

Decisión: `GET /curation/history` lista los anuncios curados (más recientes
primero, sin paginar — al volumen de Fase 0 entra entero en una página) y
enlaza a `GET /curation/{id}/edit`, que reabre exactamente las filas de
`prices_history` que ese anuncio ya produjo, contra la misma evidencia.
`POST /curation/{id}/edit` actualiza esas filas **en el lugar**: no crea ni
borra ninguna. Cada fila viaja con su propio `price_id_i` — a diferencia de la
curaduría original, que no lo necesita — para que el matching no dependa de
la posición ni pueda tocar una fila de otro anuncio por accidente. Mismo
contrato todo-o-nada que `submit_curation`: una fila con error bloquea el
guardado entero.

La tarjeta de producto/precio (~120 líneas de campos indexados) se extrajo de
`curation.html` a una macro Jinja (`_price_row_card.html`), compartida por la
curaduría normal y la corrección. Se intentó primero con `{% include %}`, que
rompió los 26 tests existentes: `loop` no viaja a una plantilla incluida en
Jinja2, solo el contexto de variables — el índice se pasa explícito como
parámetro de la macro.

Descartado: (a) un editor libre que permita agregar o quitar filas, que
reabriría el mismo problema de cardinalidad que la curaduría original ya
resuelve con cuidado. (b) match de fila por posición en vez de `price_id`,
que confundiría un reordenamiento del formulario con una corrección real y no
protegería contra editar la fila de otro anuncio. (c) permitir vaciar
`source_reference` desde la edición: mismo criterio que `submit_curation`,
que solo pisa el campo si llegó algo no vacío.

Consecuencia: la corrección de datos ya no depende de acceso a la base. Un
detalle menor documentado en el código: `published_date` es una sola fecha
por anuncio (todas sus filas se cargan con la misma), así que el formulario
de edición la prellena desde la primera fila, no fila por fila.

---

## 2026-08-12 — Rango minorista por producto, normalizado por unidad

Contexto: separar `/control` por `unit` + `sale_format` (entrada anterior de
hoy) resolvió la mezcla de mayorista con minorista, pero abrió una pregunta
distinta: el operador quiere saber «¿en qué rango se movió Arroz esta
semana?», y ahí el envase no importa — sigue siendo un solo producto aunque
se venda a granel, por saco o por paquete. Fundir los precios crudos para
responder eso reintroduce el problema original. Medido con los datos reales:
normalizando por cantidad (precio ÷ `quantity`), un saco de 110 lb sale
~281-300 CUP/lb contra ~730-750 CUP/lb minorista — 2.5x de diferencia real
entre comprar mayorista y minorista, no ruido de carga. Promediarlos después
de normalizar por unidad sería tan engañoso como promediarlos antes.

Decisión: `compute_retail_ranges()` (`cells.py`) agrega por producto ×
provincia × moneda × unidad canónica, con tres filtros: `sale_format ==
"retail"` (el precio que le importa al hogar, spec.md §8.1 Nivel 0); `unit`
de la fila debe coincidir con `Product.unit` pasado por `canonical_unit()`
(el seed lo guarda sin canonicalizar — "litro", no "l" — comparar contra el
crudo excluiría todo el aceite por accidente); y `quantity` no nulo, porque
sin cantidad no hay precio por unidad que calcular. La fecha de referencia es
`published_date`, con repliegue a `created_at` cuando falta. `/control` suma
esta tabla arriba de la matriz completa, con ventana configurable
(`?days=N`, default 7).

Descartado: convertir entre unidades (kg↔lb) para ampliar la cobertura. No
hay una tabla de conversión definida en ningún lado del proyecto, y
adivinar un factor es exactamente lo que §12.6 prohíbe. Una fila en otra
unidad queda fuera de esta vista pero sigue visible en `compute_cells`.

Consecuencia: hoy la vista está **vacía en producción** — la única fila con
`sale_format=retail` (Arroz, 750 CUP) quedó cargada en `unit=kg`, mientras el
catálogo define `Arroz` en `lb`, así que no coincide con la unidad canónica.
Aceptado a conciencia (decisión del usuario, 2026-08-12): construir la vista
igual aunque cubra casi nada, para que crezca sola a medida que la curaduría
carga `unit`+`quantity`+`sale_format` de forma consistente — mismo criterio
que ya rige el resto de `/control`.

---

## 2026-08-12 — Los dos sacos de Arroz se marcan wholesale (corrección pendiente aplicada)

Contexto: al diagnosticar la celda `Arroz/Santiago/lb/sin responder` (n=3,
amplitud 45.2×) se dijo que los dos sacos de 110 lb («Saco de arroz De 110
lb», 31.000 y 33.000 CUP) se iban a marcar `wholesale` de inmediato —
inequívoco por prueba de la jabita, mismo criterio que el azúcar del
2026-08-11. La corrección quedó anotada pero no se ejecutó: la conversación
saltó a diseñar `compute_retail_ranges` antes de aplicarla, y el operador la
notó porque `/control` seguía mostrando la celda sin corregir después del
deploy.

Decisión: `UPDATE prices_history SET sale_format = 'wholesale' WHERE
packaging = 'Saco de arroz De 110 lb' AND sale_format IS NULL` — 2 filas.
Mismo patrón de DML puntual que las correcciones del 2026-08-08 y
2026-08-11, sin migración porque no cambia esquema.

Consecuencia: la celda `Arroz/lb/sin responder` baja a n=1 (la fila de 730);
aparece una celda nueva `Arroz/lb/wholesale` con n=2, mediana 32.000. La vista
de rango minorista sigue vacía: 730 sigue sin `sale_format` respondido, y 750
sigue en `unit=kg` contra el canónico `lb` del catálogo — ninguna de las dos
entra todavía. Pendiente, sin resolver a propósito: si 730 es el mismo
paquete de 1kg con la unidad mal tildeada (debería ser `kg`, no `lb`) o una
observación distinta — queda para revisión del operador vía
`/curation/history`, no se adivina.

---

## 2026-08-12 — /control filtra a los 4 pilotos: pantalla, no datos

Contexto: el operador reportó confusión real frente a `/control` — «no sé lo
que estás haciendo, está la página llena de productos». La causa: la
curaduría se enfoca en 4 pilotos desde el 2026-08-11 (arroz, pollo entero,
azúcar, aceite), pero `/control` seguía mostrando los 51 productos del
catálogo con el mismo peso visual, la mayoría altas al vuelo de una sola
observación (dispersión ya diagnosticada ese mismo día). 56 celdas por 4
productos que importan es ruido, no información.

Decisión: `_PILOT_PRODUCTS` en `web/routers/control.py` — constante con los
4 nombres exactos del catálogo (`Arroz`, `Pollo`, `Azúcar blanca`, `Aceite`)
— filtra tanto la matriz completa como el rango minorista antes de llegar a
la plantilla. Es un filtro de **pantalla**, no de datos: no se borra ni se
oculta ninguna fila de `prices_history`, solo lo que `/control` renderiza.
Los contadores del encabezado (Productos, Provincias, sin-muestra) se
recalculan sobre el conjunto ya filtrado para que no contradigan la tabla de
abajo.

Descartado: una tabla de "producto piloto" en el esquema, o un flag en
`Product`. Ninguna decisión pidió modelar la noción de piloto como dato —
hoy es una decisión operativa de dónde enfocar la curaduría, no un atributo
del producto. Si eso cambia, se revisita con datos, no antes (mismo
criterio que ya rige `unit`/`sale_format`).

Consecuencia: en producción, 56 celdas bajan a 7 (verificado contra la base
real). Cuando cambien los pilotos, `_PILOT_PRODUCTS` se edita a mano — es una
lista de cuatro strings, no vale la pena una tabla para esto todavía.

---

## 2026-08-12 — Eliminar anuncio curado, y la columna n de /control enlaza al historial

Contexto: dos preguntas del operador. (1) ¿Se puede eliminar un anuncio
curado sin contradecir la inmutabilidad de `raw_ingestions`? (2) ¿Puede la
columna `n` de `/control` abrir los anuncios detrás de esa celda, para editar
o eliminar?

Decisión — eliminar: `POST /curation/{id}/delete` borra las filas de
`prices_history` del anuncio y lo pasa a `discarded` (elegido sobre volver a
`pending`: el caso de uso es «no debí cargar esto», no «lo curé mal, quiero
rehacerlo» — para eso ya existe `/edit`). El registro crudo nunca se toca:
no es solo una promesa del endpoint, `PriceHistory.source_id` tiene
`ondelete="RESTRICT"` contra `raw_ingestions` (spec.md §6.1, línea 321), así
que el esquema mismo impide borrar el archivo capturado mientras tenga
precios asociados. Verificado con un caso real: el registro sobrevive con su
`original_text` intacto después del delete.

Decisión — columna `n`: `GET /curation/history` suma cinco filtros opcionales
(`product`, `province`, `currency`, `unit`, `sale_format`) que son
exactamente la clave de una celda de `compute_cells`. Un parámetro ausente no
filtra; un parámetro presente pero vacío (`unit=`) filtra por «sin
responder» — mismo contrato que ya usa `_SALE_FORMAT_VALUES` en el submit,
para que la cadena vacía nunca se confunda con "no filtrar". La columna `n`
de ambas tablas de `/control` (matriz y rango minorista) enlaza con esos
cinco parámetros codificados vía `urlencode`.

Descartado: un modal en `/control` con los anuncios embebidos. Habría
duplicado la lista que `/curation/history` ya construye, con edición y ahora
eliminación — más JS y una segunda fuente de la misma información, sin
necesidad real (el operador no pidió quedarse en `/control` sin navegar).

Consecuencia: 71 tests nuevos en `test_curation.py` (filtro + delete) y 2 en
`test_control.py` (formato exacto del link, con `urlencode` — espacios como
`%20`, no `+`). 263 tests pasan.

---

## 2026-08-12 — Mayorista es escala de contenedor, no vender por saco

Contexto: la regla del 2026-08-11 («prueba de la jabita») decía «un anuncio
que solo vende sacos se descarta entero [hoy: se marca mayorista]». Aplicada
tal cual, los dos sacos de 110 lb de Arroz se marcaron `wholesale` hoy mismo.
El operador corrigió: en el mercado informal cubano actual, un minorista
vende arroz por saco, por paquete o por libra desde su casa — las tres son
transacciones minoristas reales, accesibles a un hogar. Mayorista es una
categoría distinta: venta a escala de contenedor completo, que un hogar no
compra.

Decisión: se corrige la definición operativa. `sale_format=wholesale` exige
escala de contenedor; vender un saco o paquete completo —aunque sea la
compra mínima del anuncio— sigue siendo `retail`. Los dos sacos de Arroz
(31.000 y 33.000 CUP, 110 lb) vuelven a `retail` (estaban en `wholesale`
desde hoy temprano). El texto de ayuda del formulario se reescribe:
«Mayorista es escala de contenedor, no vender un saco o paquete individual
— eso lo ofrece cualquier minorista desde su casa».

Descartado: mantener la regla original y tratar la corrección como un caso
de borde. No lo es — el corpus real de Fase 0 no tiene ningún anuncio de
escala de contenedor todavía, así que bajo la regla vieja **ningún** anuncio
mayorista genuino existía en la base: todo lo marcado `wholesale` hasta hoy
era en realidad venta minorista por saco. La corrección no es un ajuste
fino, es la corrección del error.

Consecuencia: el criterio nuevo probablemente vacía `sale_format=wholesale`
en el corpus actual hasta que aparezca un anuncio real de escala de
contenedor. Eso es correcto: no había ninguno. Pendiente de revisar si
alguna otra fila fuera de Arroz quedó marcada `wholesale` bajo el criterio
viejo.

---

## 2026-08-12 — Envase pasa a vocabulario cerrado: Paquete / Saco / A granel / Otro

Contexto: propuesta inicial del operador fue agregar «saco» y «paquete» al
selector de **unidad**. Verificado contra datos reales: un saco trae 25 kg,
50 kg o 110 lb según el anuncio; un paquete va de 1 kg (arroz) a 2 lb
(azúcar). Ninguno tiene magnitud fija, así que no sirven como `unit` — eso
reabriría exactamente el problema que separar por `unit`+`sale_format`
resolvió el mismo día: la mezcla de precios incomparables en una sola celda.

Decisión: `packaging` (el envase, hasta hoy texto libre) pasa a un `<select>`
con vocabulario cerrado — Paquete / Saco / A granel — más «Otro» con un
campo de detalle libre como válvula de escape. `unit`+`quantity` siguen
siendo la única fuente para normalizar precio por unidad; `packaging` nombra
la presentación sin participar en ningún cálculo. `_packaging_from_form()`
combina los dos campos del form en el string que guarda la columna;
`_split_packaging()` hace el camino inverso al editar una fila vieja —
texto libre anterior al día de hoy que no calza con ninguna etiqueta cerrada
cae en «Otro» con el texto original completo, sin perderlo.

Descartado: agregar saco/paquete a `saimc.units.UNITS`. Habría movido la
mezcla un nivel arriba en vez de resolverla — mismo argumento que ya se usó
para no meter el envase en la clave de celda de `compute_cells` el mismo día.

Consecuencia: el formulario de curaduría cambia de un `<input>` libre a un
`<select>` + detalle condicional. Filas cargadas antes de hoy no se tocan;
al abrirlas en `/curation/{id}/edit`, su texto libre se prellena en «Otro».
269 tests pasan (6 nuevos de vocabulario, más ajustes a los que ya cubrían
`packaging` con el campo viejo).

---

## 2026-08-13 — Conversión de moneda con elTOQUE como única fuente en vivo

Contexto: `spec.md` §6.3 E pide «al menos una alternativa configurada; una
sola fuente crítica es punto único de fallo» para la tasa de cambio. No
existe hoy una segunda fuente informal confiable: el sitio de CiberCuba
requiere scraping frágil de HTML sin API, y los agregadores comunitarios que
se evaluaron están sin mantenimiento desde 2024.

Decisión: se envía elTOQUE TRMI como única fuente en vivo, con
`exchange_rate_cache` (Postgres) como único respaldo — la última tasa
conocida, nunca una segunda fuente en vivo. Queda registrado como
apartamiento deliberado de §6.3 E, no como omisión. El token de la API
vence 2027-04-13.

Descartado: la tasa oficial del BCC como alternativa configurada. Es una
serie estructuralmente distinta a la TRMI informal, con una brecha
persistente de aproximadamente 45 CUP contra el mercado informal; usarla
como respaldo inyectaría un salto discontinuo en la serie justo cuando
elTOQUE falla, rompiendo la comparabilidad que la normalización existe para
garantizar.

Consecuencia: si elTOQUE cae, la resolución degrada a la última tasa
cacheada o a «sin convertir» — nunca a la tasa oficial. Se revisa esta
decisión si aparece una segunda fuente informal confiable.

---

## 2026-08-13 — /control funde celdas por price_cup, no solo las anota

Contexto: con `price_cup` ya resuelto (§6.3 E), `/control` seguía separando
Arroz-en-USD de Arroz-en-CUP porque `cells.py` agrupaba por moneda original,
un criterio que era correcto antes de que existiera conversión pero ya no.

Decisión: `compute_cells` y `compute_retail_ranges` funden por `price_cup`
cuando la fila no es CUP y tiene tasa resuelta — la celda resultante queda
con `currency = "CUP"` sin importar la moneda de origen de cada fila. Lo que
no tiene tasa resuelta (`currency != CUP` y `price_cup IS NULL`) no se funde
a ciegas: queda en su propia celda, marcada `unconverted=True`, con la
moneda original en la clave — mismo criterio que ya usa `sale_format` nulo.
El link a `/curation/history` omite `&currency=` para la celda fundida (para
no esconder filas que originalmente eran de otra moneda del filtro) y lo
manda solo para la celda sin convertir.

Descartado: solo anotar el equivalente en CUP al lado del valor original sin
fundir la celda — el operador la vio y prefirió fundir, porque la matriz por
producto/provincia/unidad/formato es más legible con una celda por serie
real en vez de una por combinación de moneda.

Consecuencia: si en el futuro se agrega una moneda nueva o elTOQUE deja de
resolver por un tiempo largo, esas filas se acumulan en celdas
`unconverted` — visibles y separadas, no perdidas ni fundidas por error.

---

## 2026-08-13 — HTTP Basic en `/curation`, apartamiento de "sin auth en Fase 0"

Contexto: la decisión de no tener autenticación en Fase 0 (`CLAUDE.md`,
decisión #5) asume «una herramienta de un solo operador en local». El
despliegue real corre `uvicorn` pegado directo al puerto público, sin proxy
ni firewall delante. Se verificó en vivo: `GET /curation` respondía 200
desde fuera de la red del operador, y `curation.html` embebe
`<img src="/evidence/{id}">` sin ningún control — cualquiera con la IP veía
las capturas crudas de WhatsApp/Telegram, que pueden traer nombre o teléfono
del anunciante (rompe la mitigación de `spec.md` §10.6).

Decisión: HTTP Basic (`saimc.web.auth.require_operator`) sobre todo el
router de `/curation`, incluida `/evidence/{id}`. Sin
`SAIMC_CURATION_PASSWORD` configurado, cierra en falso (503, niega todo) en
vez de abrir. `/control`, el dashboard público, no lleva esta dependencia y
sigue exactamente como antes — la decisión #5 sigue vigente para la
audiencia que sí la necesitaba sin login.

Descartado: nginx u otro proxy con ACL por ruta — es infraestructura nueva,
contra la decisión #4 de no sumarla hasta validar H0, y HTTP Basic resuelve
el mismo problema con un cambio de código mínimo.

Consecuencia: hace falta `SAIMC_CURATION_PASSWORD` en `.env` y reiniciar
`saimc.service` para que el fix tome efecto. Hasta entonces, `/curation`
sigue expuesta.

---

## 2026-08-13 — /control gana panel de progreso contra la meta de §9

Contexto: `/control` respondía bien «¿lo que estoy cargando sirve?» (matriz,
amplitud, rango minorista) pero no respondía «¿cuánto falta y qué falta?».
Con 83 registros de los 500 que pide `spec.md` §9, una sola de las dos
provincias objetivo y el catálogo disperso en 50+ productos por altas al
vuelo, el operador no tenía forma de ver dónde poner la hora diaria de
curaduría sin consultar la base a mano.

Decisión: se agrega `saimc.progress` y cinco secciones nuevas a `/control` —
progreso contra los tres ejes de la meta (registros, productos bandera,
provincias), ritmo de carga con extrapolación aritmética, huecos por
responder enlazados al historial filtrado, grilla de cobertura producto ×
provincia con los ceros visibles, y salud de la ingesta con la tasa de
descarte. **Los dos únicos objetivos que se comparan salen del documento**:
500 registros de 3–5 productos en 2 provincias (§9) y tasa de descarte <30%
(§12.5). Ningún otro umbral se fija: el mínimo de observaciones por celda
sigue sin marcar, que es lo que §12.6 manda calibrar con datos reales.

La extrapolación de ritmo se presenta como «a este ritmo» y no como fecha de
llegada: es una división, no un pronóstico, y la capa predictiva es Fase 2.

Descartado: marcar celdas como «suficientes» o «insuficientes» según su
tamaño de muestra. Es justamente el parámetro N que §12.6 prohíbe inventar, y
esta vista es el instrumento con el que se va a calibrar más adelante.

Consecuencia: los contadores «Productos: N» y «Provincias: N» de la cabecera
se eliminaron por redundantes — las tarjetas nuevas dicen lo mismo y además
nombran cuáles faltan. Lo primero que expuso el panel contra datos reales: 50
de 83 filas sin `unit` y 51 sin `sale_format`, es decir más de la mitad de lo
cargado no puede entrar al rango minorista todavía.

---

## 2026-08-13 — /control muestra el estado de la tasa sin consultar a elTOQUE

Contexto: con la conversión monetaria andando, el operador no tenía forma de
saber qué tasa estaba aplicando el sistema ni de cuándo era. La única manera
era consultar `exchange_rate_cache` a mano.

Decisión: se agrega `saimc.exchange_rate.status` y una sección a `/control`
con dos lecturas distintas. **Disponible**: última tasa cacheada por moneda,
su fecha, antigüedad en días, y si hay tasa del día de hoy (con tasa del día
curar no sale a la red; sin ella, el próximo anuncio dispara el fetch).
**Aplicada**: las tasas ya escritas en `prices_history`, agrupadas con su
volumen de filas — lo cargado no cambia porque el cache se actualice, y
distinguir las dos cosas evita leer una tasa vieja en cache como si
invalidara lo ya convertido.

Ambas salen de la base. **Cargar `/control` no consulta la API de elTOQUE**:
un fetch en un GET agregaría latencia de red a una vista de consulta y además
tendría efecto de escritura, porque el paso 2 de `RateResolver` hace
write-through al cache. Quien sale a la red es la curaduría, que es cuando la
tasa se necesita.

Descartado: marcar una tasa como vencida según su antigüedad. La ventana de
validez de la tasa de cambio es uno de los parámetros que `spec.md` §12.6
lista sin valor asignado y manda calibrar contra la frecuencia real de
actualización de la fuente. La vista informa antigüedad en días; el veredicto
queda para cuando exista la medición. Hay un test que lo protege
(`test_no_inventa_una_ventana_de_validez`).

Consecuencia: las monedas sin ninguna tasa cacheada (hoy EUR y MLC) se
muestran igual, en blanco. Ese hueco significa que un anuncio en esa moneda
se guardaría sin convertir si elTOQUE no responde, y no se veía en ningún
otro lado.

---

## 2026-08-13 — La matriz de pilotos se agrupa por producto y dibuja el rango

Contexto: la matriz repetía el nombre del producto una vez por serie. Con
Arroz partido en tres (kg minorista, lb minorista, kg mayorista) se leía como
tres productos distintos en vez de un producto con tres series. Y la columna
de amplitud —que existe justamente para delatar una celda que mezcla
minorista con mayorista— quedaba como un número al final de la fila: en los
datos reales había un 44.5× y un 103.1× que nadie estaba mirando.

Decisión: se agrega `saimc.web.matrix` y la tabla pasa a bloques agrupados por
producto × provincia, cada serie con una barra de rango mínimo–mediana–máximo.
El largo de la barra codifica la amplitud en **escala logarítmica**,
normalizada contra la serie más amplia de toda la tabla; el marcador ubica la
mediana dentro del propio rango de la serie, también en logarítmica.

Dos elecciones que no son obvias. **Logarítmica y no lineal**: la amplitud es
una razón, no una diferencia; en lineal un 44× y un 103× se ven casi iguales
contra un máximo de 103, y un 2× queda invisible. **Normalizada contra toda la
tabla y no contra el grupo**: si cada producto se escalara solo, la amplitud
de Azúcar se vería igual de ancha que la de Arroz y la comparación entre
productos mentiría.

Descartado: pintar de rojo las celdas de amplitud alta. Es fijar un umbral de
dispersión, que `spec.md` §12.6 prohíbe explícitamente y que ya protege el
test `test_no_emite_veredictos_sobre_la_muestra`. La barra es codificación
proporcional del dato que la celda ya tiene: el ojo del operador juzga, el
sistema no declara nada. La geometría se calcula en Python y no en Jinja para
que sea testeable (`tests/test_matrix.py`).

Consecuencia: con los datos del día la lectura queda evidente — Arroz lb
minorista dibuja una barra al 100% con la mediana al 2.5% del borde
izquierdo, contra Azúcar lb minorista al 15% con la mediana al 14%. Eso son
sacos cargados como minorista dentro de las celdas de Arroz, pendientes de
corregir en la cola.

---

## 2026-08-13 — La celda de /control agrega precio por unidad, no precio total

Contexto: el operador reportó que la matriz mezclaba «un paquete de 1 kg con
un saco de 50 kg» y propuso agrupar por envase para separarlos. Los datos
mostraron que el diagnóstico del síntoma era correcto pero la causa era otra:
`compute_cells` agregaba el precio **total** y nunca dividía por `quantity`.
El saco de 50 kg a 32500 y el paquete de 1 kg a 750 no tienen precios
incomparables —son 650 y 750 por kg, la diferencia normal entre mayorista y
minorista—; lo que rompía la celda era comparar 32500 contra 750. Su función
hermana `compute_retail_ranges` ya dividía por cantidad desde el 2026-08-12;
la matriz se había quedado atrás.

Decisión: `compute_cells` divide cada fila por su `quantity` antes de agregar.
La moneda se resuelve primero y la cantidad después: al revés daría un precio
unitario en la moneda de origen, no comparable con el resto de la celda. Una
fila sin cantidad (o con cantidad cero) no se puede normalizar y **no** se
funde asumiendo cantidad 1 —eso sería inventar el dato—: queda en su propia
celda con `Cell.unquantified = True`, agregando totales y marcada como tal en
la vista. Mismo criterio que ya rige para `unit`, `sale_format` y la celda sin
convertir.

Descartado: agrupar por envase, que era la propuesta original. Dos motivos.
Primero, no resolvía el problema: dentro de cada grupo se seguirían comparando
totales, y un «Saco» de 50 kg contra un «Saco» de 110 lb seguirían sin ser
comparables. Segundo, normalizado por cantidad el envase **dejó de ser un eje
que separe series**: un saco y un paquete del mismo producto por la misma
unidad son comparables una vez que el precio es por kg, y lo que los distingue
—comprar al por mayor sale más barato— ya lo captura `sale_format`. Se
actualizó el docstring de `cells.py`, que justificaba dejar el envase fuera de
la clave por ser texto libre; desde el 2026-08-12 tiene vocabulario cerrado,
así que ese motivo había caducado aunque la conclusión siga siendo la misma
por una razón más fuerte.

Consecuencia: cinco de las seis celdas piloto pasaron de amplitudes de 44.5×,
2.0× y 1.9× a 1.3×, 1.1× y 1.1×. La sexta, Arroz lb minorista, **subió** de
103.1× a 360× y eso es lo correcto: ya no es ruido de agregación sino una
única fila mal cargada (`quantity=340`, `price=340`, es decir 1 CUP por libra,
del anuncio `57d230c7` capturado el 2026-08-12) quedando sola contra un rango
sano de 281–360. La amplitud pasó a señalar errores reales en vez de
artefactos. Nota de lectura: la mediana pegada al borde derecho de la barra
indica un valor extremo bajo, igual que pegada al izquierdo indica uno alto.

---

## 2026-08-14 — La fecha de publicación pasa a obligatoria y bloqueante

Contexto: el operador preguntó si tiene sentido curar anuncios de una a tres
semanas atrás para llegar a la meta de 500 registros de la Fase 0 (`spec.md`
§9). Tiene sentido: `compute_phase0_progress` cuenta filas sin ventana
temporal, y el ritmo diario del panel se mide por `created_at`, no por la
fecha del anuncio. Lo que apareció al verificarlo es otra cosa. Desde el
2026-08-10 `published_date` era opcional y degradaba a `NULL`, con el
argumento de que «ningún consumidor lee esta columna todavía». Esa premisa
caducó: hoy la leen dos, y los dos se repliegan a la fecha de carga cuando
falta. `cells.compute_cells` la usa como eje de la ventana de N días, así que
un anuncio de hace tres semanas cargado sin fecha entra al rango de precio
vigente como si fuera de hoy; y `anchor_date` ancla en ella la tasa de cambio,
así que a ese mismo anuncio se le aplica la tasa de hoy y no la que regía
cuando se publicó. Con la tasa moviéndose, eso no es un redondeo: es un precio
en CUP mal traducido, publicado.

Decisión: `published_date` es obligatoria y bloqueante en los dos formularios
—cola de curaduría y corrección de curado—, validada en
`_validated_published_date` (capa web). Vacía, no parseable o posterior al día
de la captura devuelve 422 y no guarda nada, dentro del mismo todo-o-nada que
ya rige para las filas. La cota superior es el día de la captura en calendario
de La Habana, vía el mismo `anchor_date` que usa la conversión monetaria, y ya
no `date.today()`. En HTML el input lleva `required` y `max`.

Descartado: dos alternativas. Una, dejarla opcional y rellenar el `NULL` con
la fecha de captura al guardar — es exactamente el error que se quiere evitar,
solo que explícito en la base. Dos, un CHECK en base, ya descartado el
2026-08-10 por el mismo motivo de entonces: la validación de un dato de
formulario no debe ser un fallo de transacción.

Consecuencia: reemplaza el contrato de «campo nunca bloqueante» de la entrada
del 2026-08-10, que queda sin vigencia en esa parte (la columna sigue siendo
`DATE NULL`, sin CHECK, y sigue habiendo una sola fecha por anuncio). Las
filas anteriores a esta regla conservan `published_date = NULL` y los
repliegues de lectura se mantienen para poder seguir leyéndolas; lo que ya no
se puede es crear filas nuevas sin fecha. Corregir una fila vieja desde
`/curation/{id}/edit` ahora exige completarle la fecha: es el backfill que
nunca se hizo, cobrado de a una fila y solo cuando se toca. El test
`test_published_date_vacio_o_invalido_no_bloquea_el_submit` se reemplazó por
cinco que fijan el contrato nuevo, incluido el caso que motivó todo: un
anuncio de tres semanas atrás se acepta y entra a la serie con su propia
fecha.

---

## 2026-08-14 — Compra mínima: columna nueva y cuarto filtro del rango público

Contexto: el operador reportó que algunos anuncios exigen comprar un lote y que
eso no tenía dónde ir en el formulario. Medido contra el corpus real, **13 de
61 anuncios con texto** traen señal de lote o mínimo, concentrada en los
pilotos. Los precios cargados no estaban mal —el operador venía anotando el
precio unitario de la paca, que es el correcto—, pero faltaba el dato que
convierte ese precio en accesible o no. Peor: el mínimo **ya era el criterio
con el que se decide `sale_format`**, y era lo único que la fila no guardaba.
La fila de `2195e2ea` dice `0.90 USD, quantity=1 kg, wholesale` y nada en ella
explica por qué es mayorista; el motivo —«mínimo de venta 30,000 bolsas»— vivía
solo en el texto crudo.

De paso, otra premisa vencida: la entrada del 2026-08-12 afirmaba que el corpus
no tenía ningún anuncio de escala de contenedor. Ya tiene al menos dos
(`2195e2ea`, `44980bae`).

Decisión: tres partes.
1. `prices_history.min_purchase_quantity` (`Numeric(12,3)` NULL, migración
   0008, CHECK `ck_prices_history_min_purchase_quantity` de positividad),
   expresada **en la unidad de la propia fila** para que se compare con
   `quantity` sin tabla de conversión. Un mínimo dicho en pacas no sirve: una
   paca trae 10 o 30 paquetes según el anuncio.
2. Campo por fila en la curaduría, **opcional**. Vacío, no parseable o no
   positivo degrada a `NULL`.
3. Cuarto filtro en `compute_retail_ranges`: la fila cuyo mínimo supera su
   cantidad no alimenta el rango público, y el conteo de excluidas se muestra
   en `/control`. `compute_cells` no se filtra: es el detector de mezcla del
   operador y tiene que seguir mostrando todo.

Descartado, tres cosas. **Hacerlo obligatorio como `published_date`**: la
prueba no es «¿el campo importa?» sino «¿un vacío puede fabricar un valor
equivocado?». Con la fecha podía —el repliegue estampaba el día de carga sobre
un anuncio de tres semanas—; acá no, porque «sin mínimo declarado» y «el mínimo
es la cantidad de esta fila» dan el mismo resultado en el filtro. Obligarlo
compraría una confirmación, no un valor correcto, a cambio de un número más en
cada fila de cada anuncio. **Filtrar por plata** («excluir lo que un hogar no
puede pagar») exige un corte en pesos, que es exactamente lo que §12.6 prohíbe
inventar; por eso el filtro contesta la pregunta más chica que el dato resuelve
solo: ¿se puede comprar la cantidad que esta fila cotiza? **Derivar
`sale_format` del mínimo**: la decisión del 2026-08-12 prohíbe inferirlo por
aritmética y el motivo sigue en pie.

Consecuencia: el filtro es una comparación estructural entre dos columnas de la
misma fila, nunca un umbral, y por eso el saco de 50 kg con mínimo de un saco
sigue adentro **aunque cueste más plata** (30.990) que la paca excluida
(21.900): mide comprabilidad de lo cotizado, no poder adquisitivo. El remedio
para la paca es cargarla como el lote que es (`quantity=30`, `price=21900`), y
vuelve a entrar al mismo 730 por kg. Las 97 filas anteriores quedan en `NULL` y
siguen alimentando el rango. `compute_retail_ranges` pasa a devolver
`RetailRangeReport` (rangos + excluidas) en vez de una lista. Fuera de alcance
y anotado en la propuesta: extraer el mínimo del texto, el precio escalonado
por volumen («más de 5 pacas a 725»), y agregar «Paca» al vocabulario de
envase. Artefactos en `openspec/changes/min-purchase-quantity/`.

---

## 2026-08-14 — La matriz de pilotos pasa a grilla producto × provincia

Contexto: la matriz de `/control` agrupaba por producto × provincia y apilaba
los grupos ordenados por muestra, así que «Arroz · Santiago» y «Arroz · La
Habana» podían quedar separados por otros productos. Comparar provincias —el
objeto medido de `spec.md` §5— había que hacerlo de memoria, y una combinación
sin datos simplemente no aparecía: el hueco de cobertura era invisible.

Decisión: pivote a grilla en `saimc.web.matrix.build_matrix`. Una fila por
producto, una columna por provincia, con la columna vacía dibujada
explícitamente cuando el cruce no tiene series. Cada provincia lleva un color
propio en el borde de su columna, en el encabezado y en la barra de rango. Dos
reglas sostienen el color: **(a)** la paleta es categórica —índigo, violeta,
cian, magenta— y no incluye rojo ni verde, porque un semáforo afirmaría un
umbral de dispersión que `spec.md` §12.6 prohíbe fijar; **(b)** la ranura de
color y el orden de las columnas salen del catálogo `PHASE0_PROVINCES`, no de
los datos, así que una provincia conserva su color y su posición aunque un día
no se cargue nada en ella.

Descartado: asignar color y orden por tamaño de muestra. Se veía más «vivo»
pero hace que una provincia cambie de color y de lugar de un día para el otro,
y entonces el color deja de ser identidad y hay que releer los encabezados en
cada carga.

Consecuencia: `group_cells` sigue siendo la capa que calcula las barras y su
normalización contra toda la tabla; `build_matrix` solo pivota, no toca la
escala. El CSS define cuatro ranuras de color más el gris de «sin provincia»:
con más de cuatro provincias reales los colores se repiten, que es preferible
a inventar matices en tiempo de render. Si algún día se agregan provincias al
piloto, el orden se cambia en `PHASE0_PROVINCES` y la vista lo sigue.

---

## 2026-08-14 — La matriz se pliega por producto, con sombreado por provincia

Contexto: sobre la grilla producto × provincia, la pregunta era entre dos
formas de hacerla más legible: tabs por provincia, o sombrear las columnas y
plegar las tarjetas. Medida contra la base real, la matriz tiene hoy 3 filas,
2 columnas y 9 series — o sea que ninguna de las dos resuelve un problema de
escala que exista todavía.

Decisión: sombreado más colapso, elegido por el operador con el tamaño real a
la vista. Cada columna gana un fondo tenue de su provincia (`--prov-wash`, un
tercer tono además del fuerte y el del relleno de la barra) para que las
columnas se lean como bloques y no como una lista con una rayita al costado.
Cada producto pasa a ser un `<details>`/`<summary>` nativo: **la tarjeta de
mayor muestra arranca abierta y el resto cerradas**, y la cabecera de una
tarjeta cerrada lleva el reparto de observaciones por provincia con el punto
de color, así que plegarla no esconde dónde está la muestra.

Descartado: **tabs por provincia**, porque muestran una provincia y esconden
la otra. Comparar Arroz en Santiago contra Arroz en La Habana pasaría a ser
mirar, memorizar, cambiar de tab y comparar de memoria — exactamente el
problema que la grilla vino a resolver el mismo día, y la comparación entre
provincias es el objeto medido de `spec.md` §5. Cuando en Fase 1 las columnas
no entren, la salida tampoco son tabs sino un filtro de provincia: el filtro
elige qué comparás, el tab prohíbe comparar.

Consecuencia: el colapso es HTML nativo, sin JavaScript ni descarga (hay un
test que fija que la página no sirve ningún `<script>`). El encabezado del
producto queda gris a propósito: la tarjeta cruza las dos provincias, así que
pintarla de un color de provincia diría algo falso — el color de identidad
vive dentro, en cada columna. La pista de la barra pasa de gris a blanca
porque sobre el fondo tenue de la columna un gris claro se confundía con el
relleno. Si la lista de productos crece, el criterio de «solo la primera
abierta» sigue sirviendo; si algún día hay que recordar qué tarjetas dejó
abiertas el operador, eso ya pide estado y no entra por `<details>` solo.

---

## 2026-08-14 — Pestaña por provincia, conservando el colapso

**Reemplaza la entrada de hoy «La matriz se pliega por producto, con sombreado
por provincia» en su parte de descarte**: ahí los tabs quedaron afuera con el
argumento de que rompen la comparación lado a lado. El argumento sigue siendo
cierto; el operador lo escuchó, lo pesó y eligió igual las pestañas. La grilla
de dos columnas ya no existe. El colapso y el sombreado se conservan enteros.

Contexto: con la grilla, cada provincia se llevaba media pantalla y las series
quedaban apretadas. El operador pidió pestaña por provincia manteniendo el
plegado.

Decisión: una pestaña por provincia, con el estado en la URL
(`?province=<nombre>`, el mismo lenguaje que ya usan los enlaces al historial y
consistente con el `?days=` de esta misma vista). Son enlaces, no un control
con JavaScript: la pestaña se comparte, se marca y sobrevive a recargar. Dos
funciones nuevas en `saimc.web.matrix`: `resolve_column`, que traduce el
parámetro a columna y **cae en la primera provincia ante cualquier valor
desconocido en vez de romper la página**, y `select_province`, que recorta la
matriz a esa provincia.

Tres cosas amortiguan lo que la pestaña cuesta, y ninguna es decorativa:
1. **La cabecera de cada tarjeta conserva el reparto de TODAS las provincias**,
   con la que no estás mirando apagada. Por eso `ProductRow` gana el campo
   `tally`, separado de `columns`: `columns` es lo que se dibuja, `tally` es lo
   que se cuenta. Sin esa separación, la pestaña escondería dónde está la
   muestra y habría que ir pestaña por pestaña para saberlo.
2. **Cada pestaña rotula su muestra** (`ProvinceColumn.total`), así que se sabe
   qué hay del otro lado antes de abrirla.
3. **Las filas se reordenan por la muestra de la provincia visible.** Conservar
   el orden por el total de las dos pondría arriba de la pestaña de La Habana
   un producto que en La Habana casi no tiene nada.

Descartado: pestañas con CSS puro (radio + `:checked`). Habrían evitado la
recarga, pero exigen reglas de CSS indexadas por pestaña y markup retorcido, y
el estado no queda en la URL. Con la vista corriendo en local, la recarga no se
siente.

Consecuencia: un producto sin series en la provincia activa **no se esconde**;
cae al final de la pestaña con su cero, porque esconderlo sería volver a no
poder ver qué falta cargar. Las barras no se reescalan al recortar, así que la
misma serie mide igual en todas las pestañas. Si en Fase 1 aparecen muchas
provincias, la barra de pestañas crece en horizontal y va a haber que revisarla;
ahí el sucesor natural es un filtro que admita más de una provincia a la vez,
que es lo único que devolvería la comparación.
## 2026-08-15 — Canal de contribución con evidencia, no marketplace

Contexto: el techo de la curaduría manual —aproximadamente una hora diaria de un
solo operador— es el cuello de botella real del proyecto, y sin volumen no hay
capa predictiva. Apareció la propuesta de invertir el orden de fases: construir
primero un marketplace donde el usuario busca un producto en su provincia y ve
la oferta más cercana y más barata, de modo que el flujo de usuarios genere los
datos, y dejar la predicción para después.

Decisión: se conserva el orden de fases y se ataca el cuello de botella por otra
vía. Se abre un canal de contribución en el que un tercero reporta un precio que
observó, acompañado de su evidencia, y ese elemento entra a la misma cola de
curaduría con estado `pending`. El operador sigue siendo el filtro que valida
cada registro contra su evidencia original. El modelo de datos ya lo contempla:
`SourceType.COMMUNITY` está declarado desde el inicio y `spec.md` §6.1 define la
bandeja de entrada como interfaz genérica sobre la que escriben fuentes
distintas. No es un pipeline nuevo, es un remitente nuevo sobre el que ya existe.

Descartado: el marketplace como primera etapa. Colisiona con tres decisiones
vinculantes. Exige almacenar y exponer contacto del vendedor, contra §10.6 y la
regla de datos personales del proyecto. Elimina la barrera anti-inyección que
§10.7 declara vigente en Fase 0-1 —el operador humano que verifica cada registro
contra su evidencia— y encima invierte el incentivo: rankear por precio más bajo
le paga al vendedor por publicar un precio falso y negociar al alza en el
contacto. Y crea una dependencia circular, porque abrir ingesta de vendedores
exige las contramedidas C1/C3/C4 operativas, mientras §9.0 define las Fases 0-1
como el período en que se calibra ese umbral y §12.6 prohíbe inventarlo. Aparte,
un marketplace mide el inventario propio y no el mercado, lo que cambia el
objeto medido que §5 marca como no reversible. Si el marketplace se retoma, es
un proyecto distinto y requiere su propia entrada.

Consecuencia: la curaduría no se reduce, aumenta. El canal multiplica la entrada
sin multiplicar la salida, así que el cuello de botella se desplaza de la captura
a la curaduría. De ahí se sigue que la cola va a necesitar priorización por
producto y provincia faltante en lugar de atención por orden de llegada, y que
esa priorización deja de ser opcional en cuanto el canal tenga tracción. Queda
abierta una cuestión previa a implementar nada: la decisión cerrada 5 del
`CLAUDE.md` establece «sin autenticación en Fase 0», y valía mientras nada
escribiera desde afuera. Un canal de contribución es una ruta de escritura
pública, y hay que definir con qué barrera se abre antes de escribir código.

## 2026-08-15 — La restricción que manda es densidad por celda, no volumen

Contexto: la curaduría manual cansa y aparecieron tres candidatos para acelerar
la entrada de datos: automatizar Telegram, leer canales de WhatsApp, o cambiar
el formato capturado. Medido contra la base: 120 precios repartidos en 63 celdas
producto × provincia, de las cuales 53 tienen una sola observación. La única
serie con densidad real es Arroz, con 40 observaciones sobre las dos provincias.
El 98% del material capturado son imágenes.

Una primera medición sugirió que el texto era la palanca, porque un registro de
texto rinde 10,67 precios contra 1,17 de una imagen. Sometida a prueba, esa
conclusión se cayó: el registro más productivo de la base, una lista de precios
de 25 filas, tocó 25 productos distintos de los cuales 24 quedaron con una sola
observación y ninguno alcanzó cinco. La tasa de productos huérfanos es del 90%
en registros de texto contra 63% en imágenes. Las listas rendían volumen
impublicable.

Decisión: la métrica que gobierna la Fase 0 deja de ser el volumen de precios y
pasa a ser la densidad por celda. Toda acción de captura o curaduría se juzga
por una sola pregunta: ¿agrega observaciones a celdas que van a alcanzar
densidad publicable? De ahí se sigue el orden de trabajo: primero restringir la
extracción a los productos foco en el formulario de curaduría, después capturar
listas de precios de los mismos canales comerciales de forma repetida extrayendo
solo las filas foco, después el dashboard público cuando cuatro a seis celdas
superen la densidad, y recién al final la automatización de la ingesta, que para
entonces ya tiene su filtro definido.

Descartado: automatizar la captura primero, por Telegram o por canales de
WhatsApp. Se descartó por evidencia, no por prudencia: la automatización agrega
volumen y es indiferente a la densidad, así que aplicada sobre el catálogo
actual habría multiplicado las celdas de una sola observación en vez de
resolverlas. También se descartó seguir curando listas de precios completas, que
es la práctica que produjo la cola larga de productos huérfanos. La lista se
sigue ingiriendo entera, porque el registro crudo es inmutable, pero solo se
extrae precio de las filas que caen en el conjunto foco.

Consecuencia: el catálogo de productos deja de crecer por arrastre de lo que
aparezca en un anuncio y pasa a ser una decisión explícita. El formulario de
curaduría necesita conocer el conjunto foco y negarse a extraer fuera de él, lo
que convierte la disciplina de foco en una propiedad de la herramienta en vez de
una intención del operador. La prueba retrospectiva que sostiene todo esto: las
mismas 120 observaciones ya cargadas, concentradas en cinco productos sobre dos
provincias, habrían dado unas doce observaciones por celda en diez celdas, y el
dashboard público sería publicable hoy. Mismo esfuerzo, resultado distinto.
Ninguno de estos pasos rompe la decisión cerrada 2, ni toca el esquema, ni
necesita credenciales externas.

## 2026-08-15 — El foco es una columna, no una intención

Contexto: la decisión de esta misma fecha estableció que la Fase 0 se gobierna
por densidad por celda. Faltaba el mecanismo. La medición mostró de dónde salía
la dispersión: el catálogo tenía 60 productos, de los cuales 5 venían sembrados
y 55 los había creado el propio formulario de curaduría con su alta al vuelo,
todos con categoría «sin categoría». Entre ellos había duplicados que parten en
dos la densidad de una serie real, como «Arroz criollo» contra «Arroz» y
«Azúcar blanco» contra «Azúcar blanca». Además el conjunto piloto vivía
duplicado: una tupla fija en `saimc.progress` que usaba la vista de control, y
el catálogo entero que usaba la curaduría.

Decisión: el conjunto foco pasa a ser la columna `products.is_pilot`, única
fuente de verdad, administrada en una vista nueva, `/catalog`. La curaduría
extrae precio solo de productos piloto: el filtro se aplica en un único punto,
`_pilot_products()`, que alimenta a la vez las propuestas del motor, el
`<select>` de cada fila y la resolución por nombre. Las propuestas que no caen
en un piloto ya no producen fila en el formulario, así que una lista de precios
de veinticinco renglones muestra solo los pocos que importan. El alta de
productos sale de la curaduría y queda solo en `/catalog`, donde nace como
piloto porque agregarlo ahí ya es la decisión de medirlo. La misma vista
administra los canales y grupos seguidos, en la tabla nueva `channels`, que es
la ronda diaria del operador y no una fuente automática.

Descartado: dejar el alta al vuelo creando productos fuera del foco. Habría
sido menos invasivo y conserva la nota mental de «vi algo que no está en el
catálogo», pero mantiene dos lugares para dar de alta un producto y con eso el
camino que genera los duplicados. También se descartó encender como pilotos los
cinco productos bandera que nombra `spec.md` §9: «Leche» y «Huevos» están
sembrados y no tienen una sola observación, mientras que «Azúcar blanca», que
el documento no nombra, ya acumulaba once. La migración enciende los cuatro que
el operador había elegido con datos a la vista, dentro del rango de 3 a 5 que
sí fija el documento.

Consecuencia: sacar un producto del foco no borra nada — conserva sus precios
históricos y sigue en el catálogo, lo único que pierde es la capacidad de
recibir precios nuevos, y volver a incluirlo es un clic. Un canal se apaga y
tampoco se borra, porque los registros crudos ya ingeridos siguen
refiriéndose a él. `/catalog` muestra el conteo de observaciones por producto
para que promover uno sea una decisión con evidencia y no una corazonada: así
apareció «Azúcar blanca». La vista `/control` y el progreso de Fase 0 leen el
mismo `is_pilot`, de modo que promover un producto se refleja en las dos sin
tocar código. Queda pendiente aplicar las migraciones 0009 y 0010 contra la
base de producción antes de usar la aplicación, porque el filtro consulta una
columna que hasta ahora no existía.

## 2026-08-15 — Entrada rápida por pegado

Contexto: la medición del mismo día mostró que el 98% del material capturado
eran imágenes y que un registro de texto rinde varias veces más precios que uno
de imagen. Faltaba explicar por qué el operador capturaba imágenes si el texto
rinde más, y la explicación no era preferencia sino fricción: desde el teléfono
una captura de pantalla se sincroniza sola a la bandeja, mientras que meter
texto exigía abrir un editor, crear un archivo `.txt` y guardarlo en la carpeta
sincronizada. No había dónde pegar.

Decisión: se agrega `POST /curation/paste` y una caja de pegado al principio de
la cola de curaduría. Se pega el texto tal cual y, para meter varios anuncios de
una sola vez, se separan con una línea de guiones. Cada bloque produce un
registro crudo propio, porque el registro crudo es la unidad de curaduría y
pegar tres anuncios como uno solo perdería la trazabilidad de cada precio hasta
su anuncio.

Descartado: implementar un camino de ingesta paralelo. El endpoint escribe cada
bloque como archivo en la bandeja y llama al mismo `ingest_inbox` de siempre, de
modo que hereda sin escribir lógica nueva el hash de contenido, la
deduplicación, la redacción de datos personales y el archivo de evidencia en el
almacén. Una vía rápida con menos garantías que la bandeja habría sido
exactamente el agujero que prohíbe la sección 10.6 de la especificación.

Consecuencia: el separador es deliberadamente tolerante —cualquier línea que
sea solo guiones, con espacios alrededor— porque se pega desde el teléfono y
exigir una forma exacta convertiría la vía rápida en una fuente de errores
silenciosos. Los archivos temporales se borran siempre, incluso cuando el bloque
resulta duplicado y no llega al almacén, para que no vuelvan a entrar en el
siguiente `GET /curation`. Esto no automatiza la captura ni toca la decisión
cerrada 2: el operador sigue eligiendo y pegando cada anuncio a mano.

## 2026-08-15 — La provincia se matchea con guiones bajos normalizados

Contexto: un anuncio real de un canal comercial de Santiago de Cuba abría con
el hashtag «#Santiago_de_Cuba». El matcher de provincia no lo reconocía y
dejaba el campo en blanco pese a que el texto la nombra sin ambigüedad.

Decisión: `_match_province` normaliza los guiones bajos a espacio antes de
buscar. La causa es que `\b`, el límite de palabra de regex, trata el guion
bajo como carácter de palabra (`\w` lo incluye), así que
«santiago_de_cuba» es una sola palabra larga para el motor y nunca calza
contra el alias «santiago de cuba», que lleva espacio. Es el mismo tipo de
normalización que ya hacía `_strip_accents` para tildes, aplicado a un
separador distinto que también usa la jerga real de los canales.

Descartado: agregar variantes con guion bajo al diccionario de alias. Habría
multiplicado cada entrada de `_PROVINCE_ALIASES` sin cubrir el caso general —un
hashtag con guion bajo en cualquier alias existente o futuro—, cuando
normalizar el separador antes de buscar resuelve todos los casos de una vez.

Consecuencia: ninguna, aditivo. `_match_product` no necesitó el mismo cambio
porque ya normaliza sobre `_strip_accents` y los nombres de producto no suelen
aparecer en hashtags compuestos.

## 2026-08-15 — Canales de WhatsApp automatizados vía WAHA

Contexto: el operador pidió automatizar la extracción de los canales
registrados hasta la curaduría, y acotó el alcance a WhatsApp — es donde están
los importadores grandes que le interesan, no Telegram. Ningún canal de la
tabla `channels` tenía todavía un mecanismo de automatización.

Decisión: se agrega `POST /webhooks/whatsapp`, receptor de eventos de una
instancia de WAHA que el operador corre en Docker sobre su propio VPS, con un
número de teléfono descartable vinculado por QR. Un canal se automatiza
cargándole `external_id` —el identificador que WAHA manda en el campo `from`
de cada mensaje, algo como "1234567890@newsletter"— en /catalog; sin ese
campo, sigue siendo solo la lista de rondas manual que ya existía. Cada mensaje
que llega autenticado y con texto entra a `raw_ingestions` como `pending`, por
el mismo camino que ya usan la bandeja y el pegado (`ingest_inbox`), así que
hereda el hash de contenido, la deduplicación y la redacción de datos
personales sin código nuevo para eso. El operador sigue confirmando cada fila
en /curation: no cambia la barrera de la sección 10.7 ni la medición de H0.

La autenticación es la firma HMAC-SHA512 que WAHA calcula sobre el body crudo
del webhook (`config.hmac.key` de WAHA, `SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY`
acá), verificada con comparación de tiempo constante. Sin esa clave
configurada, el endpoint rechaza todo — mismo criterio que ya rige
`require_operator` para /curation: cerrar en falso, no abrir.

Descartado: automatizar Telegram, ya evaluado como el camino técnicamente más
limpio, porque el operador no lo quiere. Descartado también resolver
imágenes sin texto desde el webhook — un mensaje de solo foto, sin caption, se
ignora en este slice. Meterle OCR a un webhook exige descargar el archivo de
media desde la API de WAHA y correr el reconocimiento fuera del flujo
supervisado que hoy corre en la curaduría, y la medición del propio operador
mostró que el texto rinde varias veces más que la imagen: no vale la pena
resolver el caso de peor rendimiento primero. Se prefirió Apify en un momento
de la conversación por costo y por no depender de un número dedicado, pero el
operador señaló que ya tiene un VPS y puede correr WAHA ahí sin pagar por
consulta; con esa base propia, la objeción de fiabilidad ("conexión
intermitente") deja de aplicar, porque el servicio corre en el VPS, no en la
máquina de desarrollo.

Apartamiento de dos decisiones cerradas del CLAUDE.md, ambas con OK explícito
del operador en la conversación, no asumido:

- **Decisión cerrada 2** ("nada de scraping, bots de Telegram ni Playwright en
  Fase 0"): automatizar la CAPTURA no automatiza la EXTRACCIÓN. El operador
  sigue siendo quien valida cada precio contra su evidencia; el motivo
  original de la decisión —no construir sobre una hipótesis de extracción sin
  verificar— queda intacto.
- **Decisión cerrada 4** ("sin Docker hasta después de validar H0"): WAHA solo
  se distribuye de forma soportada como imagen Docker. Corre en el VPS del
  operador, no en el despliegue de SAIMC, así que no es infraestructura nueva
  para *este* proyecto en el sentido que motivaba la decisión —un solo
  desarrollador operando un solo despliegue—, pero sigue siendo Docker
  entrando al ecosistema del operador antes de validar H0, y se registra como
  tal.

Consecuencia: la forma exacta del payload de WAHA se calibró contra la
documentación pública (waha.devlike.pro/docs/how-to/events/, verificada
2026-08-15), no contra una instancia real, que no existe todavía. El parser
(`saimc.ingestion.whatsapp_webhook.parse_message`) acepta tanto el objeto de
mensaje directo que muestra la documentación como la forma envuelta en
`{"event", "session", "payload"}` que WAHA usa en otros webhooks, precisamente
por esa incertidumbre. Es esperable que el primer webhook real contra la
instancia del operador exija un ajuste acá — eso no es un error de diseño, es
el costo de diseñar contra documentación en vez de contra un sistema en vivo.
Falta, y es trabajo del operador, no de este slice: levantar WAHA en el VPS,
vincular el número dedicado por QR, configurar `config.hmac.key` con el mismo
valor que `SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY`, apuntar el webhook de la sesión a
`/webhooks/whatsapp`, y cargar el `external_id` de cada canal en /catalog.

## 2026-08-15 — docker-compose de WAHA versionado en deploy/waha

Contexto: la guía de arranque de WAHA se armó como comandos sueltos para el
operador. Pidió compose en vez de comandos manuales, y que se terminara todo
lo que se pudiera desde este lado.

Decisión: se agrega `deploy/waha/docker-compose.yml` y
`deploy/waha/.env.example` al repositorio. Es la contraparte declarativa y
versionada del `docker run` manual: un solo `docker compose up -d` deja WAHA
corriendo con el webhook ya apuntado a SAIMC, sin tocar el dashboard a mano.
`WHATSAPP_HOOK_URL`, `WHATSAPP_HOOK_EVENTS` y `WHATSAPP_HOOK_HMAC_KEY` salen
confirmados de la documentación oficial de WAHA, no inventados.

Se versiona pese a ser infraestructura de un servicio externo a SAIMC, por el
mismo motivo que ya vale para `systemd/`: sin versionarlo se pierde en cuanto
se reinstala el VPS. No es infraestructura de SAIMC —el propio despliegue de
SAIMC sigue sin Docker— es la contraparte documentada del webhook que ya vive
en `src/saimc/web/routers/whatsapp_webhook.py`.

Descartado: commitear un `.env` real con las claves generadas. Se generaron
valores aleatorios (API key, clave HMAC, contraseña del dashboard) para
ahorrarle al operador el paso de inventarlas, pero se entregaron solo en la
conversación, nunca escritas a un archivo del repo. `deploy/waha/.env.example`
sigue el mismo patrón que `env.example` en la raíz: placeholders, gitignored
el real.

Consecuencia: `WHATSAPP_HOOK_HMAC_KEY` en `deploy/waha/.env` tiene que ser
idéntica a `SAIMC_WHATSAPP_WEBHOOK_HMAC_KEY` en el `.env` de SAIMC — son dos
archivos en dos servicios distintos, nada los sincroniza solo. Queda a cargo
del operador copiar el mismo valor a los dos lados.

---

## 2026-08-16 — `127.0.0.1` en `WHATSAPP_HOOK_URL` no llega: falta `host.docker.internal`

Contexto: con WAHA levantado en el VPS y el webhook apuntado según la entrada
anterior, no entraba ningún mensaje real — `/curation` seguía vacío. Revisando
`journalctl -u saimc` sí se veían pedidos `POST /webhooks/whatsapp` recibidos,
pero `raw_ingestions` no sumaba filas nuevas. SAIMC corre en el VPS como
proceso `uvicorn` directo sobre el host (decisión 4 de `CLAUDE.md`, sin
Docker); WAHA corre en un contenedor Docker en ese mismo VPS.

Decisión: `deploy/waha/.env.example` cambia `WHATSAPP_HOOK_URL` de
`https://tu-dominio-de-saimc/webhooks/whatsapp` a
`http://host.docker.internal:8000/webhooks/whatsapp`, con comentario
explicando por qué. `deploy/waha/docker-compose.yml` suma
`extra_hosts: host.docker.internal:host-gateway` al servicio de WAHA.

Causa raíz: `127.0.0.1` dentro de un contenedor es el loopback del propio
contenedor, no el del host que lo aloja — apuntar el webhook ahí nunca podía
llegar al `uvicorn` de SAIMC, sin importar que compartieran VPS. `docker-compose.yml`
anterior no tenía forma de resolver el host desde adentro del contenedor;
`host.docker.internal` es el nombre estándar de Docker para eso, pero exige
declarar `extra_hosts` explícitamente en Linux (en Docker Desktop viene
resuelto por default, en el motor de Linux no).

Descartado: correr WAHA con `network_mode: host` para que comparta la pila de
red del VPS directamente. Resuelve el mismo problema pero cede el
aislamiento de red completo del contenedor por un solo webhook — más
superficie de la que hace falta para el problema puntual.

Consecuencia: sin verificar todavía contra un mensaje real después del fix —
falta confirmar con un canal de prueba que el webhook llega. Si el operador
alguna vez mueve SAIMC a un contenedor Docker propio, `host.docker.internal`
deja de ser necesario y esta entrada queda obsoleta.

---

## 2026-08-17 — `_extract_price` reconoce el precio en dos monedas de los canales de La Habana

Contexto: el webhook de WAHA (entrada anterior) ya está entregando mensajes reales
— 3 `raw_ingestions` de "Groupej Morales Surl" en la cola. Ese canal publica su
catálogo diario completo en un formato que `_extract_price` no reconocía:
`"43.5 o 30885"` (43.5 USD, o —al tipo de cambio propio del vendedor— 30885
CUP). Verificado directo contra `_extract_price`: matchea el producto pero
`price=None`. Medido contra los 96 mensajes reales del canal (vía
`GET /channels/{id}/messages/preview` de la API de WAHA, ver hallazgo de
Engram del mismo día): sin este fix, el backfill de histórico que motivó la
medición rendía **0 filas de precio piloto** sobre 37 textos únicos con
Arroz y Pollo mencionados explícitamente.

Decisión: nuevo regex `_DUAL_PRICE_TWO_CURRENCIES_RE` que captura los dos
números separados por "o"/"O" (tolera el punto suelto que a veces queda
pegado al número de la izquierda: `"0,40. O 280"`). `_extract_price` toma
**el mayor de los dos**, no una posición fija. Se intentó primero "el de la
derecha" (falso: los tres casos reales que motivaron el fix son
consistentemente USD-izquierda/CUP-derecha) pero medir contra más mensajes
del mismo canal encontró el mismo producto, incluso el mismo día, publicado
también como `"30885 o 43.5"` — CUP primero. El vendedor no es consistente en
el orden; la brecha de escala entre USD y CUP en este mercado (~700:1,
medida contra el propio canal: 30885/43.5, 7810/11, 688.7/0.97 dan el mismo
~710) es estructural y no depende de cómo lo tipeó cada vez. Se toma el lado
CUP —no el USD, aunque el motor podría "convertir" con la tasa de elToque—
porque exige cero tasa externa: es el número que el comprador paga tal cual,
y no depende de que la tasa informal del vendedor coincida con la fuente de
conversión de SAIMC ese día (mismo principio que ya fijó CUP como moneda base
en la entrada del 2026-08-06 sobre tipo de cambio).

La regla nueva solo se aplica cuando la línea no trae ya una marca de moneda
explícita (`_extract_currency_explicit(text) is None`): con marca explícita
("25 Usd o 17175"), gana el camino viejo (`_PRICE_BEFORE_CURRENCY_RE`), que
da precio y moneda como pareja consistente (25, USD). Forzar el lado CUP ahí
sin también forzar la moneda habría dejado precio=17175 con moneda=USD,
contradictorio entre sí.

Descartado: resolver también el caso de precio "pelado" sin ningún marcador
—ni `$`, ni `a`, ni moneda, ni el patrón de dos monedas de esta entrada—,
que aparece con mucha frecuencia en el mismo canal fuera de la sección de
abarrotes (`"Vinagre claro 1L➔ 260"`, `"Mayonesa 960g. 2200"`). Se descarta
por ahora: sin ningún marcador que lo distinga, la única señal disponible es
"último número de la línea", que produce falsos positivos contra cualquier
cantidad sin unidad reconocida (`_UNIT_WORDS` no incluye "g", "ml" ni "l" —
gap aparte, tampoco resuelto acá). Generalizar sin esa distinción es
exactamente el tipo de sobreingeniería que este proyecto evita por regla.
También se descartó ampliar `_UNIT_WORDS` en el mismo slice: no lo pedía el
hallazgo original (Arroz/Pollo con marcador "o") y mezclarlo hubiera hecho
más difícil aislar cuál cambio explica cuál resultado.

Consecuencia: remedido después del fix contra los mismos 96 mensajes reales
de "Groupej Morales Surl": 25 filas de precio piloto (antes 0), todas
verificadas en rango CUP coherente (Arroz: 688.7–30885; Pollo: 7810 — sin
ningún valor por debajo de 100 colado del lado USD). Los otros dos
productos piloto de la sección de abarrotes de ese canal (Aceite, Azúcar
blanca) no aparecieron en esta muestra — no hay evidencia todavía de si el
canal los vende. Casos de prueba en `tests/test_extraction.py::TestPrecio`
y `TestMoneda`, con las líneas reales (orden normal e invertido) más el
límite de moneda-explícita como regresión.

---

## 2026-08-17 — Backfill manual de histórico de WhatsApp desde /catalog

Contexto: el webhook de WAHA (entrada del 2026-08-15) solo captura mensajes
que lleguen desde que un canal queda registrado — todo lo publicado antes de
esa fecha se pierde. Medido contra la instancia real del operador
(2026-08-17, `GET .../messages/preview`): WAHA sí tiene ese histórico
disponible, con un techo real de ~96 mensajes por canal y **sin paginación
hacia atrás** — `startCursor`, `endCursor` y `offset` (documentados por WAHA)
se probaron los cuatro contra la instancia real y los cuatro devuelven
exactamente los mismos 96 mensajes, ignorados en silencio. La profundidad
que da ese techo depende del volumen del canal, no es un número fijo de
días: Groupej Morales Surl rinde 7.7 días, The Boss Agency 28.9.

Decisión: botón "Importar histórico" en `/catalog`, por canal, solo visible
cuando el canal tiene `external_id` cargado. `POST
/catalog/channels/{id}/backfill` llama a
`saimc.ingestion.whatsapp_backfill.fetch_channel_history` (nuevo módulo,
mismo patrón de `saimc.exchange_rate.eltoque`: transporte separado de
parseo puro, `httpx.MockTransport` en los tests) y reusa `ingest_inbox`
exactamente como el webhook — mismo hash, misma deduplicación, misma
redacción de datos personales. Es un disparador **manual, no automático**:
el operador decide cuándo correrlo por canal, no corre solo.

El límite de mensajes por consulta queda **fijo en 100, no configurable**
desde la UI ni por settings: es un valor medido contra la API real, no una
preferencia. `limit=500` se probó en el mismo sondeo del 2026-08-17 y tardó
172 segundos y tumbó la sesión de WAHA en producción (pasó a `STARTING`,
afectando también a los otros canales mientras se recuperaba sola). Exponer
ese número como perilla de configuración sería invitar a que alguien lo
suba pensando que trae más historia —no la trae, WAHA la ignora igual— con
el único efecto real de arriesgar la sesión en vivo.

Nueva configuración (`SAIMC_WAHA_BASE_URL`, `SAIMC_WAHA_API_KEY`,
`SAIMC_WAHA_SESSION`, `SAIMC_WAHA_TIMEOUT`) — dirección opuesta al webhook:
ahí WAHA llama a SAIMC, acá SAIMC llama a la API de WAHA. Sin
`waha_base_url`/`waha_api_key` configurados, el botón degrada a un mensaje
de error server-side, mismo criterio de "cierra en falso" que ya rige
`whatsapp_webhook_hmac_key` y `curation_password` — nunca una excepción sin
manejar.

Primer flash message del proyecto: `_back()` ahora acepta `message`/`error`
opcionales y los pasa por query string del redirect (sin sesión, sin
cookie, se lee una vez del lado del `GET /catalog`). Deliberadamente chico
—sin categorías, sin auto-cierre— porque es exactamente lo que hace falta
para que un botón que puede fallar (WAHA caída, sesión no lista, canal sin
configurar) no falle en silencio.

Descartado: exponer el `limit` como parámetro del formulario (motivo
arriba). También se descartó agrupar todos los mensajes de un canal en un
solo llamado a `ingest_inbox`: los overrides de esa función (`captured_at`
entre ellos) se aplican a **todos** los `paths` de una llamada, y cada
mensaje del histórico trae su propia fecha real — agruparlos les habría
puesto a todos la fecha de la corrida del backfill en vez de la fecha real
de cada uno. Se llama una vez por mensaje, igual de simple, sin ese efecto
secundario.

Consecuencia: verificado con tests (`tests/test_whatsapp_backfill.py`,
`tests/test_catalog.py::TestCatalogoBackfillHistorico`) contra la forma real
capturada de la API — incluida la del error de sesión no lista (`{"error":
...}` con 200) y la de imagen sin caption (66/96 mensajes reales del corpus
medido) — pero **no probado todavía contra la instancia real en producción**:
falta agregar `SAIMC_WAHA_BASE_URL`/`SAIMC_WAHA_API_KEY` al `.env` real y
reiniciar `saimc.service`. Ninguna de las dos cosas se hizo en este slice a
propósito — es un reinicio de un servicio con usuarios reales mirando el
dashboard, y corresponde que lo dispare el operador, no que se decida solo.

---

## 2026-08-17 — /catalog: feedback visual del backfill, canales primero, y un bug real detrás de una falla "conocida"

Contexto: dos pedidos de UI sobre el slice anterior. (1) El botón "Importar
histórico" no daba ninguna señal de que el clic había registrado — un POST
normal recarga la página sin feedback intermedio, y con WAHA de por medio
(latencia variable) el operador no tenía forma de distinguir "está
corriendo" de "no pasó nada". (2) Canales antes que Productos en la página,
con un menú de anclas arriba para saltar entre las dos secciones — el
operador entra a `/catalog` a administrar canales con más frecuencia que a
dar de alta productos nuevos.

Decisión: `catalog.html` suma su primer bloque de JavaScript propio para
esto (vanilla, un solo `<script>`, sin librería — mismo criterio que el
botón "Eliminar" de curaduría, 2026-08-08): al enviar el formulario de
backfill, el botón se deshabilita y pasa a decir "Importando…". "Terminó"
ya lo cubre el mensaje flash que aparece tras el redirect (slice anterior);
no hacía falta agregar nada para eso. Las dos tarjetas (Canales, Productos)
se reordenan y ganan `id` ancla; el encabezado suma enlaces `#canales` /
`#productos` junto a los que ya llevaban a `/curation` y `/control`.

Se investigó primero, no se asumió: el operador recordaba una decisión
previa sobre no dejar entrar a `/curation` mensajes sin producto ni precio
(ej. un saludo del canal con números de contacto). Buscado en
`docs/decisions.md` y Engram: no existe tal decisión. Comportamiento real
verificado en `curation.py`: `_oldest_pending` no filtra por si el mensaje
tiene candidatos, así que ese tipo de mensaje sí llega y el operador tiene
que abrirlo y descartarlo a mano — confirma el problema que el operador
describía, aunque la causa no fuera la que recordaba. Queda pendiente de
diseño (no de este slice): auto-descartar sin que nadie lo vea contradice
"el operador corrige cada error" (la premisa misma de la curaduría manual,
decisión cerrada 2 del `CLAUDE.md`); la alternativa —mantenerlo en la cola
pero con un descarte de un clic cuando no hay candidatos— no rompe esa
premisa. Se le preguntó al operador cuál prefiere.

Se encontró y corrigió, de paso, la causa real de una falla que este mismo
archivo venía reportando como "ajena, del entorno" en las dos entradas
anteriores (`tests/test_whatsapp_webhook.py::test_sin_clave_configurada_rechaza_todo`,
401 en vez de 503): no es ruido del entorno, es un bug de test.
`monkeypatch.delenv` borra la variable de `os.environ`, pero
`pydantic_settings.BaseSettings` con `env_file=".env"` lee el archivo del
disco como fuente propia — si ese archivo real ya tiene la clave cargada
(como pasó a mitad de este slice: apareció `SAIMC_WAHA_BASE_URL`/
`SAIMC_WAHA_API_KEY` ya configuradas en el `.env` de producción, sin que
nadie de este lado las escribiera — asumido como que el operador las cargó
él mismo tras el reporte del slice anterior), `delenv` no alcanza para
simular "sin configurar": hay que pisar la variable con `setenv("", ...)`.
Corregido ahí y en el test nuevo de `SAIMC_WAHA_*` que iba a heredar el
mismo bug.

Descartado: escribir un sistema de tabs con mostrar/ocultar por JS (como
tiene `/control` con las provincias). El menú de anclas resuelve "moverse
rápido entre las dos secciones" con HTML puro; tabs reales son la respuesta
correcta si algún día `/catalog` crece a más de dos secciones, no antes.

Consecuencia: suite completa en verde por primera vez en varias sesiones
(511/511) — las dos entradas previas de este archivo quedan con una falla
ya resuelta, no hace falta anotarlas de nuevo. `.env` real: confirmar con el
operador si ya cargó las variables de WAHA con intención de que el backfill
corra en producción, o si fue sin darse cuenta de que hacía falta reiniciar
`saimc.service` además.

---

## 2026-08-17 — Atajo de un clic sin candidatos, y provincia fija por canal

Contexto: dos pedidos del operador, ambos resueltos con `AskUserQuestion`
antes de tocar código porque los dos son decisiones de diseño reales, no
ajustes de interfaz — el primero porque contradice o no la premisa de la
curaduría manual (decisión cerrada 2 del `CLAUDE.md`), el segundo porque
toca el esquema (`channels`).

**(1) Atajo de un clic cuando `propose_all` no encuentra nada.** Un mensaje
como el saludo de bienvenida de un canal ("¡Buenos tardes, familia!... 📱
[teléfono removido]") llega a `/curation` igual que cualquier otro, y hoy
había que abrir el formulario completo —evidencia, tabla con 3 filas en
blanco— para llegar al botón "Descartar" al final. Opciones planteadas:
descarte automático y silencioso (nunca llega a la cola) vs. mantenerlo en
la cola con un atajo de un clic. El operador eligió la segunda: la premisa
de "el operador corrige cada error" se sostiene —nada se descarta sin que
alguien lo vea— y el costo baja de "abrir, escanear, scrollear, click" a
"un click".

Decisión: `curation_queue()` calcula `no_candidates` sobre la salida cruda
de `propose_all` (antes del filtro por foco) — vacía significa que no hubo
NI producto NI precio en ningún renglón del anuncio completo. Cuando es
`True`, `curation.html` muestra una tarjeta entre la Evidencia y el
formulario con un solo botón, "Descartar (sin candidatos)", que postea al
mismo `/curation/{id}/discard` que ya existía — no fue necesario un
endpoint nuevo. El formulario completo sigue disponible debajo: es un
atajo, no un reemplazo, para cuando el operador mira la evidencia y de
todos modos quiere cargar algo a mano.

Distinción importante: `no_candidates` NO es "sin producto piloto". Un
anuncio de un producto real fuera del foco con precio real (ej. "cemento a
500 el saco") sí produce candidatos —`propose_all` los encuentra, aunque
después se filtren por no ser piloto— y sigue mostrando el formulario
completo sin el atajo: ese anuncio vale la pena mirarlo, no es ruido.

**(2) `channels.province_id` — "el canal siempre manda".** El operador
conoce de antemano la provincia de sus canales fijos (Groupej Morales Surl
vende en Cotorro, La Habana) y no quiere depender de que cada mensaje la
mencione, ni de que `_match_province` la adivine bien. Pregunta real antes
de tocar el esquema: ¿la provincia del canal reemplaza el matching por
texto, o es un prefill que el texto puede seguir pisando? El operador
eligió que el canal reemplace siempre, sin excepción.

Decisión: migración `0012_channels_province.py` (nullable,
`ondelete="RESTRICT"`, mismo criterio que `PriceHistory.province_id`).
`/catalog` gana un `<select>` de provincia en el alta de canal y un
`<select>` inline por fila en la tabla (auto-envía con `onchange`, sin
botón aparte) para fijarla en canales que **ya existen** — necesario
porque el caso real que motivó esto, Groupej Morales Surl, ya estaba en la
tabla antes de que la columna existiera. En `curation.py`,
`_channel_province_id(db, ingestion)` resuelve el canal por
`source_reference == channel.name` y, si tiene provincia fija, la escribe
sobre TODAS las filas de la propuesta — incluidas las filas en blanco de
más, no solo las que `propose_all` generó del texto.

El reemplazo se restringe a `SourceType.WHATSAPP_CHANNEL`: ahí
`source_reference` es el nombre exacto del canal, escrito por código (el
webhook y el backfill), nunca tipeado por el operador. Para el resto de
las fuentes (bandeja, pegado) el campo es texto libre — un anuncio pegado
a mano cuyo `source_reference` coincida por casualidad con el nombre de un
canal automatizado NO debe resolverse contra ese canal, y así lo prueba el
test (`test_fuente_no_automatizada_no_resuelve_contra_ningun_canal`).

Descartado: la alternativa de diseño para (1) —descarte automático y
silencioso al ingerir— por la razón ya dicha. Para (2), un prefill/fallback
(el canal propone, el texto puede pisar) — el operador prefirió la certeza
del dato que él mismo cargó sobre una heurística de texto que ya demostró
fallar (`_match_province` no tiene forma de distinguir "el vendedor
mencionó otra provincia por error de tipeo" de "el vendedor realmente
vende ahí esta vez").

Consecuencia: un canal automatizado sin provincia fija sigue funcionando
exactamente como antes (cae a `_match_province` sobre el texto, sin
cambios) — el override solo entra en juego cuando el operador decide
cargarlo. Suite completa: 525/525.

---

## 2026-08-17 — Canal origen usa el dato ya conocido, y el atajo sin candidatos colapsa el formulario

Contexto: primer uso real en producción del slice anterior (34 anuncios de
Groupej Morales Surl importados por backfill). Dos fallas que solo se ven
con dato real, no con los sintéticos de TDD.

**(1) "Canal origen" vacío pese a que el dato ya estaba.** `curation_queue()`
prellenaba ese campo solo con `guess_source_reference(texto)` — la
heurística del 2026-08-08 que busca el patrón "Nombre - Ayer a las H:MM" de
capturas estilo Facebook. Un mensaje de WhatsApp normal nunca trae ese
patrón, así que el campo quedaba vacío pese a que `ingestion.source_reference`
YA tenía el nombre exacto del canal — el webhook y el backfill lo escriben
ahí mismo al ingerir (`docs/decisions.md`, 2026-08-15 y 2026-08-17). El
código nunca miraba ese campo para prellenar el formulario, solo lo
guardaba en la fila.

Decisión: `source_reference_guess = ingestion.source_reference or
guess_source_reference(texto) or ""` — el dato cierto gana sobre la
adivinanza, igual criterio que "el canal siempre manda" con la provincia
(misma entrada de arriba, mismo día). La heurística de texto sigue viva
para lo que no tiene ese campo ya escrito: bandeja, pegado.

**(2) El atajo sin candidatos no ocultaba "Datos del anuncio".** El diseño
original de esta misma sesión (entrada de arriba) dejaba el formulario
completo visible al lado del botón de descarte, a propósito ("es un
agregado, no un reemplazo"). Contra un caso real, el operador pidió lo
contrario: que compita menos por la atención, no que conviva al lado.

Decisión: se reemplaza `<div class="card">` por `<details class="card"
{% if not no_candidates %}open{% endif %}>` — mismo patrón ya usado en
"Pegar anuncios" de esta misma plantilla. Colapsado por default cuando no
hay candidatos, abierto por default en el caso normal. No es un
"ocultamiento" total: sigue a un click de distancia si el operador mira la
evidencia y quiere cargar algo a mano — la diferencia con el diseño
anterior es que ya no aparece expandido compitiendo con el botón de
descarte.

Descartado: sacar el formulario del todo cuando no hay candidatos. Se
prefirió colapsarlo (recuperable) antes que eliminarlo (exigiría volver a
cargar la página o un camino aparte para el caso raro de que el operador
quiera agregar algo a mano sobre un anuncio que el motor leyó como vacío).

Consecuencia: ambos fixes tenían test antes de tocar el template/router
(`test_canal_origen_ya_conocido_gana_sobre_la_adivinanza_de_texto`,
`test_el_formulario_completo_sigue_disponible_pero_colapsado` +
regresión `test_con_candidatos_el_formulario_sigue_abierto_por_default`).
Suite completa: 527/527.

---

## 2026-08-17 — Fecha de publicación de canal automatizado usa `captured_at`, no adivinanza de texto

Contexto: mismo hallazgo que "Canal origen" (entrada de arriba, mismo día),
señalado por el operador después de ver el caso real: "Fecha de
publicación" también quedaba vacía para los anuncios de WAHA — obligatoria
y bloqueante desde el 2026-08-14, así que el operador tenía que tipearla a
mano en cada uno de los 34 anuncios importados. Causa idéntica:
`guess_published_date` busca el mismo patrón "Nombre - fecha" de capturas
Facebook que `guess_source_reference`, y un mensaje de WhatsApp no lo trae.

A diferencia del canal origen, acá no había ningún campo ya escrito
esperando ser usado — pero sí había un dato mejor que la adivinanza:
`ingestion.captured_at`. Para un canal automatizado, ese valor no es el
mtime aproximado de un archivo capturado después (que es la ambigüedad por
la que `published_date` existe como campo separado desde el 2026-08-10):
es el `timestamp` que WAHA reporta del envío real del mensaje — la fecha
de publicación exacta, no una aproximación.

Decisión: `curation_queue()` usa `ingestion.captured_at.date()` como
`published_date_guess` cuando `ingestion.source_type ==
SourceType.WHATSAPP_CHANNEL`, con la adivinanza de texto
(`guess_published_date`) como respaldo para el resto de las fuentes, donde
`captured_at` sigue sin ser confiable para esto. Coherente con la cota
superior que ya impone `_validated_published_date` (el día de captura en
calendario de La Habana): usar exactamente ese valor nunca puede violar la
cota, es el propio límite.

Descartado: nada nuevo — mismo criterio que la entrada de "Canal origen" de
arriba, aplicado al mismo tipo de dato conocido-versus-adivinado.

Consecuencia: el prellenado se calcula al abrir `/curation`, no al
ingerir — así que los 34 anuncios de Groupej Morales Surl que ya estaban
pendientes en la cola también se benefician con este mismo deploy, sin
necesidad de reimportarlos. Test:
`test_fecha_de_canal_automatizado_usa_captured_at_no_el_texto`. Suite
completa: 528/528.

---

## 2026-08-17 — Escala, envase y compra mínima se prellenan desde la última confirmación del mismo canal

Contexto: idea del operador, no un bug reportado. Escala de venta
(minorista/mayorista), envase y compra mínima son los tres únicos campos
del formulario que **nunca** se infieren del texto del anuncio (design.md,
D4/D7: son juicio explícito del operador) — así que en cada uno de los 34
anuncios de Groupej Morales Surl, el operador los tipeaba de cero. La
observación: una mipyme repite el mismo formato de venta anuncio tras
anuncio (mismo saco, mismo mínimo), así que la última vez que el operador
confirmó esos tres campos para ese canal y ese producto es un prefill
razonable — no una regla del sistema, una sugerencia basada en lo que el
propio operador ya confirmó antes.

Decisión: `_historical_defaults(db, source_reference, product_id)` en
`curation.py` busca la fila más reciente de `prices_history` para el mismo
`(source_reference del canal, product_id)`, vía `JOIN raw_ingestions ON
source_id`, y devuelve `sale_format`/`packaging_format`/
`packaging_detail`/`min_purchase`. `curation_queue()` la aplica fila por
fila (cada fila de un anuncio multi-producto puede tener su propio
historial) después de armar `rows`, junto al override de provincia de la
entrada anterior. Reusa `_split_packaging`, el mismo helper que ya
prellena la corrección de un anuncio ya curado.

A diferencia de "el canal siempre manda" (provincia) y "canal
origen"/"fecha de publicación", acá **no hay restricción por
`SourceType.WHATSAPP_CHANNEL`**: esta búsqueda no confía en ningún campo
de la ingesta actual como certeza, es una consulta histórica por
`source_reference` tal cual esté escrito. Si para una fuente manual el
operador tipeó el mismo texto de canal de forma consistente, también se
beneficia; si no fue consistente, la búsqueda no encuentra nada — el mismo
resultado que hoy, sin caso peor.

Descartado: restringir la búsqueda a un patrón de texto similar (mismo
"casi el mismo mensaje"), que hubiera exigido una métrica de similitud
completamente nueva. La clave (canal, producto) exacta es más simple, más
barata, y es literalmente lo que el operador describió: no "este mensaje
se parece a otro", sino "este canal vende este producto siempre igual".

Consecuencia: un canal que cambia de formato de venta (saco de 25kg en vez
de 50kg, por ejemplo) va a mostrar el valor viejo prellenado hasta que el
operador confirme uno nuevo — es una sugerencia editable, no una regla, así
que el operador la corrige y a partir de ahí el prefill sigue al día. Bug
real encontrado en el camino: `_historical_defaults` recibía `product_id`
como string y lo pasaba directo a un filtro contra una columna `Uuid` —
`_parse_uuid()` (ya existente) lo resuelve. Suite completa: 533/533, con
tests de no-mezcla entre canales y entre productos, y de "usa la
confirmación más reciente" cuando hay más de un antecedente.

---

## 2026-08-17 — Puerto de WAHA atado a loopback, no a 0.0.0.0

Contexto: revisión de cierre de jornada. Verificado con `ss -ltn` contra el
VPS real: el puerto 3000 de WAHA seguía escuchando en `0.0.0.0`, alcanzable
desde internet, pese a que `SAIMC_WAHA_BASE_URL` ya apunta a `127.0.0.1` del
lado de SAIMC (entrada del backfill, arriba) — la mitigación quedó a medias:
se corrigió cómo SAIMC *llama* a WAHA, pero no cómo WAHA *se expone*. La API
key de WAHA (`X-Api-Key`) quedó además pegada en texto plano en esta misma
sesión de trabajo, en un comando `curl` que el operador compartió para
investigar el histórico de canales.

Decisión: `deploy/waha/docker-compose.yml` pasa de `"3000:3000"` a
`"127.0.0.1:3000:3000"` en `ports`. Sin el prefijo de IP, la sintaxis corta
de Docker Compose publica en todas las interfaces por default — ese fue el
error, no una regla de firewall faltante. Se corrigió también la copia real
desplegada (`/home/ariel/waha/docker-compose.yml`, fuera del repo por
diseño — no es infraestructura de SAIMC, ver entrada del 2026-08-15).

Descartado: resolver esto solo con una regla de `ufw deny 3000`. Docker
inserta sus propias reglas en la cadena `DOCKER` de `iptables` para cada
puerto publicado, y en la configuración por default de Ubuntu esas reglas
se evalúan antes que la cadena `INPUT` donde vive `ufw` — un `ufw deny`
sobre un puerto que Docker publica en `0.0.0.0` típicamente no lo bloquea.
Es un problema documentado de la interacción Docker+ufw, no una elección de
diseño de este proyecto. La forma correcta es que Docker mismo no publique
el puerto fuera de loopback, que es lo que hace este cambio.

Consecuencia: el cambio en el archivo no tiene efecto hasta que alguien
corra `docker compose up -d` para recrear el contenedor — commiteado sin
aplicar a propósito, mismo criterio que las migraciones de esquema: el
comando que reinicia un servicio en producción lo dispara el operador, no
se decide solo. Queda pendiente, y es tarea del operador: recrear el
contenedor, rotar `WAHA_API_KEY` (la que está en uso quedó expuesta en esta
sesión) actualizando los dos `.env` reales (WAHA y SAIMC, tienen que
coincidir), y reiniciar `saimc.service` después. Ninguno de esos tres pasos
se automatizó — tocan servicios en producción y secretos reales.

## 2026-08-18 — La cola de curaduría ordena por aporte al foco, no por antigüedad

Contexto: el operador planteó que, al abrir la ingesta a grupos de WhatsApp
(además de canales), el sistema debería intervenir con la lista piloto para
no gastar su hora diaria en anuncios que no alimentan ninguna celda. La
primera formulación fue filtrar en la ingesta —que un mensaje sin producto
piloto no entre a `raw_ingestions`—, y eso contradice dos cosas ya cerradas:
la entrada del 2026-08-17 («Atajo de un clic sin candidatos»), donde el
propio operador eligió mantener todo en la cola porque «nada se descarta sin
que alguien lo vea», y la inmutabilidad de la capa cruda (`spec.md` §6.1).
Hay además un motivo que ninguna de las dos cubre: `is_pilot` cambia con un
clic en /catalog, así que un filtro en ingesta perdería para siempre el
histórico de un producto que entra al foco después. El problema real no
estaba en qué entra, sino en el orden: `_oldest_pending` era FIFO puro por
`captured_at`, y un anuncio de cemento del lunes se miraba antes que uno de
arroz del martes.

Decisión: `_oldest_pending` pasa a ser `_next_pending(db, products)`, que
ordena los `PENDING` en tres niveles — `_FOCUS_MATCH` (el texto menciona un
producto del foco), `_FOCUS_UNKNOWN` (imagen sin OCR, relevancia no
averiguable sin transcribir) y `_FOCUS_NONE` (texto sin ningún producto del
foco)— y dentro de cada nivel sigue mandando `captured_at`. El desempate por
antigüedad sale de que `min()` devuelve el primer elemento de valor mínimo
sobre una lista que el SQL ya trajo ordenada, sin ordenar por tupla. La
señal es `mentions_any_product(text, products)`, nueva en
`extraction/engine.py`: recorre línea por línea igual que `propose_all`
—porque el `has_modifier` de `_match_product` calculado sobre el texto entero
haría que un «hígado de pollo» en un renglón descarte el arroz limpio del
siguiente— y no construye la propuesta completa, que para ordenar no hace
falta. Ordena, no filtra: nada deja de entrar ni se descarta.

Descartado: filtrar en la ingesta por lista piloto (motivos arriba). También
se descartó congelar la relevancia en una columna de `raw_ingestions` al
ingerir: ataría el orden a la lista piloto vigente el día de la captura, y
justamente lo que se quiere es que un producto que entra al foco hoy suba
solo los anuncios viejos que ya lo mencionaban, sin reprocesar nada. Y se
descartó empujar el filtro a SQL con `ILIKE`: `_match_product` normaliza
tildes de los dos lados y el piloto real «Azúcar blanca» las tiene, así que
un `ILIKE` no matchearía «azucar blanca» sin depender de `unaccent`, una
extensión de Postgres que los tests en SQLite no tienen.

Consecuencia: `_next_pending` carga todos los `PENDING` en memoria en vez de
un `LIMIT 1`, y corre la señal sobre cada uno en cada carga de `/curation`.
Con la cola real de hoy (0 pendientes; 98 curados y 80 descartados
históricos) y el volumen de Fase 0 es irrelevante —son comparaciones de
substring sobre unos pocos miles de textos en el peor caso—, pero es el
primer lugar del proyecto donde el costo crece con el tamaño de la cola y no
con el elemento que se muestra. Si algún día molesta, el arreglo es acotar
la ventana, no volver al filtro en ingesta. Queda fuera de alcance y sin
hacer: mostrarle al operador cuántos pendientes del foco le quedan, que hoy
no se distingue del total en `_status_counts`. Suite completa: 544/544.

## 2026-08-18 — Los grupos de WhatsApp entran por su propio endpoint

Contexto: la ingesta automatizada solo contemplaba canales de WhatsApp
(`…@newsletter`). El operador quiso expandirla a grupos (`…@g.us`), donde vive
buena parte del mercado informal. El registro no era el problema: el webhook
(`parse_message`) matchea `Channel.external_id` contra el `from` del mensaje
sin mirar el sufijo del JID, y WAHA manda el evento `message` para grupos
igual que para canales, así que un grupo dado de alta con su JID ya ingresaba
en vivo sin tocar código. El backfill sí estaba atado: pegaba siempre a
`/api/{s}/channels/{id}/messages/preview`, que no sirve para un JID de grupo.

Medición contra la instancia real (2026-08-18), porque la forma de un endpoint
de WAHA no se supone, se mide — mismo criterio que con canales el 2026-08-17:

- Grupos responden en `/api/{s}/chats/{id}/messages`, y cada entrada es el
  objeto de mensaje **plano**, sin el envoltorio `{"message", "reactions"}` del
  endpoint de canales. `parse_message` los lee sin desenvolver nada.
- No tienen el techo de 100 de los canales: `limit=1000` trajo 344 mensajes en
  17s sin tumbar la sesión, contra los 172s y la sesión caída que costó
  `limit=500` en un canal.
- Traen un campo que un canal no tiene: `participant`, el emisor individual,
  como LID seudónimo (`…@lid`), no como teléfono.
- La señal varía muchísimo entre grupos: de 173 mensajes con texto sobre 200 en
  uno, a 3 sobre 200 en otro que publica solo fotos.

Decisión: el sufijo del JID rutea endpoint y parser en `fetch_channel_history`.
Se agrega `parse_group_history` (forma plana) al lado de `parse_history` (forma
envuelta), y `Platform.WHATSAPP_GROUP` para que el operador pueda etiquetar la
fila — descripción, no ruteo: el sufijo lo escribe WhatsApp, la plataforma la
tipea una persona, y cuando pueden contradecirse gana el que no se equivoca.
Sin migración: `platform` es `String(30)` y el enum vive en Python.

Se apaga el prellenado de escala/envase/compra-mínima cuando el origen es un
grupo. Ese prellenado (2026-08-17) descansa entero en que `source_reference`
identifique a UN vendedor, cierto en un canal —el nombre del canal es la
mipyme— y falso en un grupo, donde es el nombre del mercado y adentro conviven
muchos vendedores: se midieron 27 emisores distintos en uno solo. Heredar el
saco de uno al anuncio de otro no rompe nada visible, escribe dato plausible en
el formulario, y por eso mismo es confirmable sin notarlo.

Descartado: rutear por `Platform` en vez de por el sufijo del JID (deja que un
error de tipeo del operador mande la consulta al endpoint equivocado).
Descartado también un `SourceType.WHATSAPP_GROUP` nuevo: `WHATSAPP_CHANNEL`
significa «llegó por WAHA», y partirlo obligaba a ensanchar
`_channel_province_id`, que hoy funciona para grupos gratis — y es más útil ahí
que en un canal, porque un grupo de mercado suele ser provincial. Y se descartó
subir el límite de 100 para grupos aprovechando que la API lo permite: el cuello
de botella no es WAHA, es la hora diaria de curaduría, y un solo grupo activo
midió 200 mensajes en un día.

Consecuencia: un grupo se da de alta igual que un canal, con su JID `@g.us`, y
queda con webhook en vivo y botón «Importar histórico» sin nada más. Queda
pendiente y explícitamente fuera de este slice guardar `participant` en
`raw_ingestions` — es la identidad del vendedor que `spec.md` §6.1 ya previó
para reputación, y es lo único que permite reabrir el prellenado dentro de un
grupo; toca esquema, así que va por el flujo completo. Sigue sin resolverse el
volumen: la cola ordena por aporte al foco pero no filtra, y varios grupos
activos generan más anuncios por día de los que una hora de curaduría absorbe.
Verificado end-to-end contra la instancia real: 86 mensajes con texto de un
grupo activo, 93 del canal ya en producción por su endpoint viejo. Suite
completa: 555/555.

---

## 2026-08-19 — Dirección del local por canal, uso interno

Contexto: el operador quiere geolocalizar más adelante, y en muchos
grupos/canales de WhatsApp la dirección del local ya está publicada en la
propia descripción del grupo — el negocio se auto-publicita para que lo
encuentren. A primera lectura choca con `CLAUDE.md` ("no capturamos ni
almacenamos... direcciones exactas... de anunciantes") y con `spec.md` §10.6.
Verificado antes de decidir: §10.6 habla de datos personales de un
*anunciante individual*, no del local de un negocio que ya se publicita solo;
y `redact_personal_data()` ya declara su alcance como "solo teléfonos" —
las direcciones que aparecen en texto capturado hoy YA pasan sin filtrar por
`original_text`, sin estructurar. No es una exposición nueva, es estructurar
algo que ya circula.

Decisión: `Channel.address` (texto libre, nullable), mismo patrón que
`province_id` — es dato del canal, no de cada anuncio, porque la dirección
sale de la descripción del grupo, no del texto de cada mensaje individual.
Editable en `/catalog` al crear el canal y también después (`POST
/catalog/channels/{id}/address`, mismo motivo que el endpoint de provincia:
canales ya existentes como Groupej Morales Surl no tienen este dato y no hay
forma de inferirlo con retroactividad). Sin geocodificar todavía — solo texto
tal cual lo carga el operador. **No se expone en ninguna vista pública**
(`/control`, futura API): mismo criterio de seudonimización que ya rige
`source_reference` (§10.6, mitigación 2) hasta que haya una decisión
explícita de publicarlo.

Descartado: reusar el campo `notes` existente (cero cambios de esquema, pero
mezclado con notas de cualquier otro tipo — más trabajo parsear direcciones
de ahí cuando llegue la geocodificación real). El operador eligió el campo
dedicado.

Consecuencia: migración `0013_channels_address.py`. Si en algún momento se
decide exponer esto públicamente o automatizar la extracción desde la
descripción del grupo (en vez de carga manual), esa es una decisión nueva,
no una extensión implícita de esta.

---

## 2026-08-19 — `channels.name` deja de ser único

Contexto: `channels.name` compartido legítimamente por un canal y un grupo —
"Ventas Mayoristas", el mismo negocio publicando en su canal `@newsletter` y
en su grupo `@g.us` con el mismo nombre. `idx_channels_name` era UNIQUE desde
el origen de la tabla, así que el alta del segundo rechazaba con un choque de
base, y la cola de curaduría resolvía el canal de origen buscando por
`source_reference == name` con `scalar_one_or_none()` — con dos filas de
mismo nombre eso pasa a lanzar `MultipleResultsFound` y revienta `/curation`.

Decisión: índice `idx_channels_name` NO único (migración `0014`) +
`raw_ingestions.channel_id`, FK nullable a `channels.id` (migración `0015`),
como identidad real de origen para toda ingesta que el código escribe
(webhook, backfill). La curaduría resuelve el canal de una ingesta con
`_resolve_origin`: prefiere `channel_id`; a falta de él, cae al nombre solo si
matchea exactamente una fila — dos o más nunca se adivina, resuelve a
"origen desconocido" en vez de lanzar o elegir al azar.

Descartado: retropoblar `channel_id` en filas viejas de `raw_ingestions` —
la tabla es inmutable (`CLAUDE.md`), no se escribe ni se corrige una vez
creada. También se descartó seguir exigiendo unicidad de nombre y forzar al
operador a renombrar uno de los dos ("Ventas Mayoristas 🛒" vs "Ventas
Mayoristas 🛒 (grupo)"): el nombre lo pone WhatsApp, no el operador, y no hay
forma de imponer una convención sobre eso.

Consecuencia: 42 filas preexistentes con `source_reference='Ventas
Mayoristas 🛒'` (20 pendientes) pierden el override de provincia heredado y
el prellenado de escala/envase al registrarse el grupo homónimo, porque el
nombre pasa a ser ambiguo. `channel_id` nunca se retropuebla en esas filas:
el fallback por nombre queda permanente para todo lo creado antes de esta
migración.

---

## 2026-08-20 — `_historical_defaults` acota por `channel_id`, no solo por texto

Contexto: verificación de la entrada anterior encontró una fuga residual.
`_resolve_origin` resuelve bien el canal de origen, pero la consulta
histórica seguía filtrando `PriceHistory` solo por `RawIngestion.source_reference
== source_reference` — texto plano. Canal y grupo homónimos escriben el
mismo `source_reference` (es `channel.name` en los dos), así que una
ingesta con `channel_id` apuntando al CANAL igual traía el historial
confirmado del GRUPO si el texto coincidía. Mismo error cruzado que la
guarda `is_whatsapp_group` ya cerraba, colándose por la puerta de al lado.
Reproducido con un test antes de tocar el código (`hash-canal-vs-grupo-*`,
`tests/test_curation.py`).

Decisión: la consulta histórica ahora filtra por `RawIngestion.channel_id ==
origen.id` cuando `origen` se resolvió sin ambigüedad, con `OR channel_id IS
NULL AND source_reference == source_reference` para no perder las filas
anteriores a la migración 0015, que no tienen `channel_id` y siguen
dependiendo del texto como único rastro.

Descartado: dejarlo documentado como riesgo conocido sin arreglar — la fuga
es exactamente la que esta migración completa existe para cerrar; dejarla
abierta en un camino y cerrada en el otro no es una limitación aceptable,
es la mitad del trabajo.

Consecuencia: ninguna migración nueva — cambio acotado a
`src/saimc/web/routers/curation.py`. Suite completa 577/577.

## 2026-08-20 — /catalog: editar y eliminar canal/grupo

Contexto: pedido del operador ("en catalog debería existir la opción de
editar/eliminar un grupo"). Hoy solo se pueden corregir provincia y
dirección de un canal ya dado de alta; nombre, plataforma, enlace, notas e
Id. WAHA quedan fijos desde el alta. Tampoco hay forma de sacar del
catálogo un canal cargado por error (nombre mal tipeado, alta duplicada).

El modelo documenta una regla explícita: "un canal se desactiva, nunca se
borra" (`raw_ingestion.py`, FK `channel_id` con `ondelete="RESTRICT"`) —
existe para que la referencia de un canal con historial capturado nunca
quede colgando. Esa regla no se toca: el borrado nuevo solo se ejecuta si
`raw_ingestions.channel_id` no tiene ninguna fila apuntando al canal
(pre-chequeo con SELECT, mismo criterio que el de `external_id` repetido en
`create_channel`). Con historial, `POST /catalog/channels/{id}/delete`
devuelve error y el operador tiene que apagarlo (`toggle_channel`), como
hasta ahora.

Decisión: `POST /catalog/channels/{id}/edit` corrige nombre, plataforma,
enlace, notas e Id. WAHA (mismas reglas de `create_channel`, incluida la
protección de `external_id` duplicado, excluyendo al propio canal).
`POST /catalog/channels/{id}/delete` borra el canal solo si nunca capturó
nada. En la plantilla, "Editar" es un `<details>` nativo (sin JS) que abre
un formulario inline; "Eliminar" pide confirmación con `onsubmit="return
confirm(...)"`, mismo patrón que `curation_history.html`.

Descartado: exponer un DELETE incondicional que dependiera de capturar el
`IntegrityError` de Postgres — repetiría el problema que el pre-chequeo de
`external_id` ya evita (sesión envenenada, necesita rollback explícito
antes de poder redirigir con el error).

Consecuencia: ninguna migración — cambio acotado a
`src/saimc/web/routers/catalog.py` y `catalog.html`. Suite completa
583/583.

## 2026-08-20 — La cola esconde el texto sin producto del foco

Contexto: con volumen real de WhatsApp la cola se llenó de mensajes propios
de los negocios (saludos, alertas de clima, links, ofertas de cemento,
diésel y mayonesa) y el operador terminó descartando a mano más de lo que
curaba. Medido sobre producción: de 104 ingestas de WhatsApp con texto, 82
fueron descartadas — y 77 de esas 82 (94%) no mencionaban ningún producto
del foco. La repetición, en cambio, resultó marginal: solo el 13% son
casi-duplicados, y el dedupe por `content_hash` ya atrapa los idénticos.

La decisión del 2026-08-18 ordenaba por foco pero no filtraba, y la del
2026-08-17 sostenía que un precio real de producto ajeno al foco «vale la
pena que el operador lo vea». Con volumen de negocio esa premisa se dio
vuelta: es la fuente principal del trabajo manual que el foco existe para
eliminar.

Decisión: `_pending_in_focus` deja de mostrar la ingesta de TEXTO cuyo
contenido no menciona ningún producto del foco. Es el mismo criterio que el
2026-08-15 ya aplicaba a las filas del formulario, subido al nivel del
anuncio entero. Tres límites lo hacen seguro:

- **Esconder no es descartar.** La fila sigue `PENDING` y su relevancia se
  recalcula en cada carga: un producto que entra al foco desde /catalog hace
  reaparecer sus anuncios sin reprocesar nada. Nada se marca `DISCARDED` ni
  deja de ingerirse, así que el denominador de H0 no se toca.
- **Sin foco definido no se filtra.** Con `is_pilot` vacío no hay relevancia
  que medir y la cola se mostraría muerta; ahí pasa todo, como antes.
- **La imagen sin OCR sigue entrando.** Su relevancia es desconocida, no
  negativa, y descubrirla exigiría transcribir la cola entera en cada carga.

El contador «Fuera de foco» declara cuántos quedaron escondidos: sin él,
«Por curar: 0» no se distingue de «no entró nada».

Descartado: aprender los textos de ruido repetidos, que es lo que sugería la
intuición inicial. Ataca el 13% del problema y exigiría almacenar patrones y
decidir un umbral de similitud, cuando el catálogo del foco ya separa el 94%
sin estado nuevo. Descartado también filtrar en el webhook: congelaría la
decisión contra el foco del día de captura y rompería la reversibilidad, con
`raw_ingestions` inmutable.

Consecuencia: el filtro es tan bueno como los alias del catálogo, y ahí vive
el aprendizaje que el operador pidió — cada alias que agrega en /catalog hace
entrar todos los anuncios futuros que usan ese término, sin ML ni migración.
Medido antes de activarlo, el matcher escondía 5 de 22 anuncios ya curados
(«MUSLO DE POLLO PILGRIM'S», «Muslo con contramuslo», «Saco de azúcar 25kg»);
con los alias `muslo de pollo`, `muslo con contramuslo`, `contramuslo` en
Pollo y `saco de azucar`, `azucar magdalena`, `azucar refino` en Azúcar
blanca, la pérdida baja a 0 y el ruido eliminado queda en 75/82 (91%). Los
alias son datos de producción: el filtro no debe activarse sin ellos.

## 2026-08-20 — El backfill mira más atrás: 100 → 250 mensajes
Contexto: el importar histórico traía 100 mensajes por canal/grupo, número
puesto el 2026-08-17 con dos medidas viejas y flojas — «96 mensajes es el
techo de la API» y «`limit=500` tardó 172s y tumbó la sesión». Con volumen
real y el filtro de foco ya activo, ese 100 dejaba fuera anuncios en foco que
WAHA sí tenía sincronizados.

Decisión: `_HISTORY_LIMIT` pasa a 250, mismo número para canales y grupos.
Medido contra la instancia real (WAHA 2026.8.1, WEBJS) subiendo escalonado y
mirando el estado de la sesión después de cada consulta: 250 y 300 responden
en menos de 3s y la dejan WORKING; 350 y 500 cuelgan más de 300s; 400 devuelve
HTTP 500 a los 266s y la manda a FAILED. No hay techo de 96: un canal devolvió
288 mensajes en 2.3s. El rendimiento en la cola, contra los tres canales
registrados, sube de 39 a 93 anuncios visibles después del filtro de foco.

Descartado: 300, que también dio bien. El corte está entre 300 y 350 y del
lado malo no hay degradación suave — la consulta no vuelve, `waha_timeout`
(30s) corta del lado de SAIMC dando «no se pudo consultar», pero WAHA sigue
moliendo y reinicia la sesión. Ese reinicio dura 10s-20s y durante esa ventana
se pierden los webhooks entrantes. 300 es el último valor bueno conocido, sin
margen. Descartado también un límite distinto por tipo de fuente: en grupos el
endpoint sirve desde el store local de WEBJS, responde en menos de 0.2s y
devuelve lo mismo con 100, 1000 o 5000 — dos constantes no comprarían nada.

Consecuencia: el número queda atado a este motor y a esta versión de WAHA, y
se verifica de la misma forma —escalonado, mirando la sesión— si alguno de los
dos cambia. Un grupo trae hoy unidades de mensajes porque el re-pareo vacía el
store local de WEBJS: ahí el límite no es la restricción y el histórico solo
crece hacia adelante.

## 2026-08-20 — El anuncio con precio por volumen se lee como bloque, no por línea
Contexto: un anuncio real del operador («ALITAS DE POLLO / CAJA DE 40 lb / La
caja 28,5 usd o 25650 cup. / Más de 10 cajas 28 / Más de 100 cajas 27,5»)
salía inservible de la cola. Medido contra el motor: proponía dos filas, una
con producto y sin precio, y otra que ofrecía dar de alta un producto llamado
«La caja». Los dos escalones mayoristas se perdían enteros, porque su precio
va pelado al final de la línea y `_extract_price` —con razón— no toma números
sueltos. El problema no era un regex faltante: `propose_all` asume «una línea
= una oferta autónoma», y este anuncio es un bloque donde el encabezado
declara el producto, otra línea declara el lote, y las de precio heredan las
dos. Ninguna línea sola está completa.

Decisión: `_tiered_proposals` lee el anuncio como bloque cuando hay al menos
un escalón («más de N», «a partir de N», «desde N») y UN solo producto en
todo el texto. Propone dos filas —el precio base y el mejor escalón
mayorista—, ambas con el producto y el lote del encabezado. El mínimo se
guarda en la unidad del lote, no en bultos: «100 cajas» de 40 lb son 4000 lb,
porque `min_purchase_quantity` se compara entre anuncios sin tabla de
conversión que lo arregle después. Cierra la mitad de `design.md` D7 que
quedaba pendiente, sin tocar el esquema: `min_purchase_quantity`,
`sale_format` y `packaging` ya existían.

Descartado: una fila por escalón (tres acá). El escalón intermedio describe la
misma oferta sin agregar un extremo, y tres filas del mismo producto y día
cargan la serie con precios que no son comparables entre sí. Descartado
también inferir el mínimo cuando el anuncio no declara el tamaño del lote
(«Más de 10 cajas» sin decir qué trae una caja): ahí no se propone la fila
mayorista, porque una segunda fila con otro precio y ningún mínimo que lo
explique es peor que no proponerla. Y se mantiene deliberadamente sin
prellenar `sale_format` y `packaging`: siguen siendo juicio explícito del
operador (D4), y este slice no los toca.

Consecuencia: la lectura de bloque gana sobre la de línea cuando dispara, así
que su condición de disparo es la garantía de que un anuncio multiproducto no
cambie — dos productos distintos la desactivan. Queda anotado aparte que
«alitas» no está en `_PRODUCT_DISQUALIFYING_MODIFIERS`, así que hoy matchea
Pollo genérico igual que lo haría «pollo entero»; si el operador quiere que
las alitas sean un producto propio, como ya lo son muslo y pechuga, es una
línea en esa lista y una decisión suya, no del motor.

## 2026-08-20 — Las alitas no son Pollo; el muslo sí, y ya lo era
Contexto: al revisar la lectura de bloque quedó a la vista que «ALITAS DE
POLLO» matcheaba Pollo genérico. El operador definió el criterio: muslo y
contramuslo cuentan como Pollo, las alitas no.

Decisión: `alas`, `alita` y `alitas` entran a
`_PRODUCT_DISQUALIFYING_MODIFIERS`. «ala» ya estaba pero el regex es `\b...\b`
y no tocaba ni el plural ni el diminutivo, que es como se anuncia el corte de
verdad. Muslo y contramuslo NO se tocan: verificado contra la base de
producción, ya matchean Pollo por los alias que el operador cargó en /catalog
el 2026-08-20, que es el mecanismo previsto — `_match_product` levanta la
descalificación cuando el alias que matchea contiene el propio modificador.
Cambiarlo en código habría sido redundante y habría desprotegido el match
genérico de cualquier otro producto.

Descartado: sacar «muslo» y «contramuslo» de la lista. La lista protege a
TODOS los productos; el alias es la excepción por producto, editable desde la
interfaz y sin despliegue.

Consecuencia: un anuncio de alitas deja de atribuirse a Pollo y, mientras
alitas no exista como producto, cae fuera de foco y la cola lo esconde —
correcto, porque no se está midiendo alitas; darlo de alta en /catalog lo hace
reaparecer sin reprocesar nada. Para que el bloque siga sirviendo en ese
estado, `_tiered_proposals` ya no exige producto del catálogo: sin match
propone el encabezado como `product_name_guess` (`_block_header`), porque
`_guess_product_phrase` adivina a partir del texto que precede a un precio y
un encabezado no tiene ninguno. Queda anotado que «pechuga» e «hígado» siguen
descalificando, sin alias que los levante: si el operador los quiere como
Pollo, es un alias en /catalog, no un cambio de código.
