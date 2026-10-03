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

## 2026-08-20 — Rediseño Material 3 de las vistas web

Contexto: el bundle `redesing/design_handoff_material_saimc/` traía una hoja
`saimc-material.css` y siete plantillas Jinja listas para el repo. Un sondeo
previo midió 26 tests en rojo de 602, todos de presentación: renombre de
clases `saimc-*` a `m3-*`, contadores que pasan de `<span>Curados: <strong>N`
a etiqueta y cifra en nodos hermanos, dos textos reformulados, y aserciones
que comparaban `id="x" value="y"` como cadena literal en una línea.

Decisión: se integra el rediseño completo. Bootstrap (232 KB) y `curation.css`
salen del repo; entran Roboto, Roboto Mono y Material Symbols vendorizadas en
`static/fonts/` (82 KB las tres). Los iconos van subseteados a las 35
ligaduras que usan las plantillas: 5,7 KB contra los 370 KB de la fuente
completa. El subsetting lo hace el servidor de Google vía `icon_names`, así
que no hace falta `fontTools` en el venv. Roboto y Roboto Mono son fuentes
variables y se declaran con rango de pesos (`font-weight: 100 900`), no con
un peso fijo: con peso fijo el navegador sintetiza la negrita en vez de usar
el trazo real.

De los 26 tests, 14 no se tocaron: fallaban porque la plantilla partía
`id=` y `value=` en dos líneas, así que se juntaron los atributos en los
seis `<input>` afectados. El resto se actualizó al marcado nuevo sin aflojar
lo que verifica — los contadores pasan por un helper `_contador()` que exige
que la cifra sea la del contador pedido y no un número suelto de la página,
y el aviso de descarte alto ahora comprueba el chip y el banner, no una
frase sola.

Descartado: relajar las 14 aserciones a expresiones regulares tolerantes al
espaciado. Habría acoplado los tests al formato del código fuente de la
plantilla en vez de al HTML que ve el navegador, y tocaba 14 casos para
resolver un problema que se arregla en 6 líneas de markup.

Consecuencia: `/control` pierde ~10 párrafos de justificación de spec,
condensados en dos grillas de cuatro notas; las referencias a §12.5 y §12.6
salen de los chips pero siguen en los banners largos. Se agrega
`tests/test_static_assets.py`, que falla si una fuente desaparece, si los
iconos dejan de venir subseteados o si alguna vista vuelve a pedirle un asset
a `fonts.googleapis.com` en runtime. El bundle `redesing/` se borra una vez
integrado: ya cumplió su función de handoff y este archivo deja constancia de
que su `PLANTILLAS.md` documentaba una URL de fuentes que devolvía 404, para
quien busque el motivo en el historial.

---

## 2026-08-21 — El prellenado por historial del canal se retira

Contexto: pedido explícito del operador tras usar la cola con volumen real de
WhatsApp. Escala de venta, envase y compra mínima se venían prellenando desde
la última confirmación del propio operador para ese mismo (canal, producto)
—decisión del 2026-08-17, refinada el 2026-08-18 con la guarda de grupo y el
2026-08-20 con el acote por `channel_id`—. El diagnóstico del operador:
«esto está metiendo ruido». La sugerencia acertaba lo suficiente como para
confirmarse sin mirarla y erraba lo suficiente como para ensuciar el dato, que
es el peor de los dos mundos: un campo vacío se nota, uno mal prellenado no.
El costo que la decisión original quería evitar —tipear tres campos por
anuncio— resultó más barato que revisar tres campos ya escritos.

Decisión: se borra `_historical_defaults` de `curation.py` y su aplicación
fila por fila en `curation_queue`. Lo que el motor de extracción no puede leer
del texto del anuncio llega en blanco y lo escribe el operador. Sigue en pie
todo lo que se deriva del texto, incluido el mínimo de compra de un anuncio
con precio escalonado (2026-08-20), porque eso lo declara el propio anuncio.
También sigue en pie el override de provincia por canal (2026-08-17, «el canal
siempre manda»): ese dato no es juicio del operador sobre un anuncio, es una
propiedad del canal que él mismo cargó en /catalog.

Descartado: dejar el prellenado detrás de una preferencia o mostrarlo como
sugerencia visual sin escribir el campo. Las dos alternativas conservan la
complejidad —la resolución de origen, la guarda de grupo, el acote por
`channel_id`— para un beneficio que el operador ya midió como negativo.
Reemplaza en este punto a las entradas del 2026-08-17 («Escala, envase y
compra mínima se prellenan desde la última confirmación del mismo canal»),
2026-08-18 (guarda de grupo) y 2026-08-20 («`_historical_defaults` acota por
`channel_id`, no solo por texto»), que quedan como historia del camino
recorrido.

Consecuencia: `_resolve_origin` deja de devolver la tupla `(canal, ambiguo)` y
devuelve `Channel | None` — el flag existía solo para que el prellenado
cortara en seco ante un nombre ambiguo, y su único consumidor restante
(`_channel_province_id`) siempre lo ignoró. `Channel.is_whatsapp_group` se
queda en el modelo sin consumidor en código: distinguir canal de grupo va a
volver a hacer falta el día que se guarde la identidad del emisor
(`participant`), y borrarla es otro slice. Suite completa: 603/603.

---

## 2026-08-21 — `price_basis`: qué representa el precio guardado
Contexto: `cells.py` divide `price / quantity` sin condición desde 2026-08-13,
así que una fila sin declarar si `price` es por unidad o por el lote se lee
implícitamente como total. De 105 filas con `quantity > 1`, 3 están mal
divididas contra la mediana sin ambigüedad de su propia celda: «Arroz
guyanés a 340lb» se guarda como `price=340, quantity=340` y agrega 1 CUP/lb
en vez de ~340. Solo 14 celdas tienen suficiente referencia `quantity=1`
para detectar el error, así que las ~90 filas restantes son *no verificadas*,
no verificadas-correctas.
Decisión: columna `price_basis` (`'unit'` | `'total'` | `NULL`), migración
`0016` (aditiva, sin backfill — el head real de alembic es `0015`, no el
`0009` que nombraba la propuesta). Obligatoria en el formulario solo cuando
`quantity != 1`: con cantidad uno, unitario y total son el mismo número y
exigir la respuesta ahí solo sería trabajo manual sin evitar ningún error.
Nunca se infiere ni se prellena desde el historial (D5): un basis heredado
mal multiplica el precio final por `quantity`, a diferencia de `sale_format`
o `packaging`, donde heredar mal es cosmético. `compute_cells` deja de
dividir incondicionalmente; una fila con `quantity != 1` y `price_basis` sin
resolver va a su propia celda (`unresolved_basis`) y se excluye de
`compute_retail_ranges` con un conteo separado (`excluded_by_basis`) del de
compra mínima.
Descartado: asumir `'total'` para las filas legadas sin resolver (es
exactamente el bug que este cambio corrige); un CHECK cruzado
`quantity = 1 OR price_basis IS NOT NULL` (rompería toda fila legada al
crearse, la obligatoriedad es regla de captura, no invariante de esquema);
partir el cambio en dos PRs por capa (schema+agregación vs. captura): el
primero solo retiraría filas del rango público sin darle al operador ninguna
forma de resolverlas, así que si se divide para revisión tienen que
desplegarse juntos.
Consecuencia: las ~90 filas legadas con `quantity > 1` y sin `price_basis`
salen del rango público minorista hasta que el operador las recure desde
`/curation/history`; `/control` muestra el conteo por separado del de compra
mínima para que decida cuál cola atacar primero. `engine.py` no se toca:
inferir el basis del texto del anuncio queda fuera de alcance, con su propio
contrato de TDD, igual que `guess_min_purchase` en el cambio anterior.

## 2026-08-21 — El anuncio es una ráfaga: contexto de mensajes vecinos en la cola
Contexto: al investigar por qué las fotos de grupos y canales no se capturan
(`whatsapp_webhook.parse_message` descarta el media sin caption, 183 de 200 en
un grupo medido el 2026-08-18) apareció una pérdida mayor y ya activa. El
vendedor parte el anuncio en varios mensajes seguidos: producto o foto en uno,
precio en el siguiente. Medido contra producción — «Para mañana entrará aceite
de 900ml» (01:40:46) y «A 4700» (01:45:19), mismo canal, 4m33s; y «...picadillo
de pollo americano de 40lb a 535» seguido de «La caja sale a 21400» a 22
segundos. El segundo mensaje no nombra ningún producto, así que
`mentions_any_product` no lo reconoce y el filtro de foco del 2026-08-20 lo
esconde para siempre: la fila queda `pending` y no aparece en ninguna cuenta de
descarte. Sobre 104 textos con canal: 37 con precio y sin producto piloto, 35
anuncios completos, y 17 huérfanos con un vecino del foco a menos de 10 minutos.
Decisión: la cola de curaduría muestra los mensajes del mismo canal dentro de
±`burst_window_minutes` (10 por defecto, configurable) como contexto de solo
lectura en la tarjeta de evidencia, en cualquier estado de procesamiento. Una
consulta, solo para la cabeza de la cola. Sin cambio de esquema: `channel_id` y
`captured_at` ya existen.
Descartado: ascender el huérfano a la cola con un rango `_FOCUS_BURST` propio.
Se implementó y se midió contra producción antes de descartarlo: rescataba 18
pendientes de los cuales 1 era el caso buscado — en un canal activo cualquier
anuncio de gasolina, keratina o alitas de pollo cae dentro de la ventana, y el
filtro del 2026-08-20 quedaba anulado a cambio de casi nada. También se descartó
usar el catálogo completo como discriminador de «anuncio que se explica solo»:
`products` tiene 60 filas y varias son basura de curaduría («Precio :»,
«Precio: *»), así que matchearía contra cualquier cosa. Y la ventana es fija
alrededor del ancla, no cadena transitiva: encadenar saltos de nueve minutos
fundiría un canal activo en una sola ráfaga de todo el día.
Consecuencia: el huérfano sigue fuera de foco y no le cuesta un clic al
operador; se lee desde el anuncio que sí entra. La pérdida se evita hacia
adelante, no hacia atrás: el par medido ya tiene el ancla en `discarded` —el
operador la descartó por no tener precio, que es exactamente el error que este
contexto previene— y esos huérfanos del backlog siguen escondidos. La captura
de fotos sin caption queda pendiente en su propio slice, y rinde más con este
agrupamiento ya en su lugar: la foto del saco de arroz aterriza al lado de
«arroz a 340 por libra» en vez de sola.

## 2026-08-21 — El webhook captura las fotos sin caption
Contexto: `parse_message` descartaba todo mensaje sin `body`, así que una foto
sin caption no llegaba nunca a `raw_ingestions`. Medido el 2026-08-18 en
`parse_group_history`: 183 de 200 mensajes de un grupo que publica solo fotos.
El operador confirmó el 2026-08-21 que el precio a veces va dentro de la
imagen, a veces en el caption, y a veces en el mensaje siguiente. Sin la foto
no hay evidencia preservada para esos anuncios, que es lo que `spec.md` §6.1
(Etapa 1b) exige como base de la Transparency Layer.
Decisión: cuando el mensaje no trae texto pero WAHA publicó `media.url` con un
mimetype de imagen fija, el webhook baja el archivo con la `X-Api-Key` y lo
mete por el mismo `ingest_inbox` que todo lo demás — hereda hash, deduplicación
y almacén sin duplicar lógica. Módulo nuevo `ingestion/whatsapp_media.py`
porque `whatsapp_webhook.py` declara sus funciones puras y acá se abre un
socket. La descarga es sincrónica: WAHA corre en el mismo VPS, es una lectura
de disco disfrazada de HTTP, y meter una cola rompería la decisión cerrada 3
para resolver una latencia que no existe. Toda falla degrada a 200 con
`ingested: 0`; un 500 haría que WAHA reintente en loop.
Descartado: bajar también la foto cuando SÍ hay caption. Preservar las dos
cosas en una fila exige que `curation_queue` sepa proponer desde el
`original_text` de una fila `image` —hoy no lo hace, cae fuera de sus dos
ramas y no propone nada—, así que va en su propio slice. Descartado `image/webp`:
WhatsApp reencoda las fotos a JPEG y el webp es casi siempre un sticker;
dejarlo entrar inundaría la cola de caritas. Descartado tocar el backfill de
histórico: pide `downloadMedia=false` y el techo de `_HISTORY_LIMIT = 250` se
midió con esa bandera apagada, así que encenderla cambia el costo por completo
y del lado malo reinicia la sesión de WAHA perdiendo webhooks. `_with_text`
mantiene ese camino como estaba.
Consecuencia: la foto entra como fila `image` con `original_text` nulo y el OCR
sigue corriendo recién en la cola (spec.md §6.1), así que H0 se mide por capas
separadas igual que antes. La comparación de la URL contra `waha_base_url`
trata `localhost` y `127.0.0.1` como el mismo host: WAHA arma `media.url` con
`localhost` y el `.env` del operador declara `127.0.0.1`, y compararlas como
cadenas habría descartado todas las fotos en silencio. `deploy/waha/.env.example`
declara `WHATSAPP_DOWNLOAD_MEDIA=true` y `WHATSAPP_FILES_MIMETYPES` para que el
modo de falla «hasMedia sin media.url» no sea invisible. Falta medir cuánto
volumen real entra: la cola las ordena como `_FOCUS_UNKNOWN`, que nunca se
esconde, así que si inundan hay que revisar ese ranking con el número en la mano.

---

## 2026-08-21 — Buscador y filtro en /catalog, sin paginación

Contexto: el operador pidió mejorar la experiencia de /catalog con buscador,
filtro canal/grupo y paginación. Medido contra producción antes de tocar
código: 10 canales, 60 productos.

Decisión: se agregó búsqueda de texto (`q_canal`, `q_producto`) y filtro de
plataforma (`platform`) para canales, todo por querystring server-side —
mismo criterio ya usado por «Solo el foco» (`focus=1`), documentado en el
propio template. Sin JavaScript nuevo.

Descartado: paginación. Con los volúmenes medidos no hay problema que
resuelva — sería complejidad sin necesidad real.

Consecuencia: si canales o productos crecen mucho más, paginación vuelve a
ser una opción legítima; se revisita con volumen real en la mano, no antes.

---

## 2026-08-21 — El backfill de histórico también reconoce grupos con JID `@lid`

Contexto: importar histórico del grupo real «🛒Habana ventas» fallaba con
«No se pudo consultar WAHA». Su `external_id` es `157724134363163@lid`, no
`...@g.us`. `@lid` es el identificador de privacidad que WhatsApp usa cada
vez más en vez del JID tradicional, sin exponer el número real. El código
solo reconocía `@g.us` como sufijo de grupo, así que este canal caía al
branch de "canal" por descarte. Confirmado contra la instancia real de WAHA:
`/channels/{id}/messages/preview` responde 500 para este `@lid`;
`/chats/{id}/messages` responde 200 con mensajes reales del grupo.

Decisión: `_GROUP_JID_SUFFIXES` (`whatsapp_backfill.py`) pasa de un string
único a `("@g.us", "@lid")`. `Channel.is_whatsapp_group` se actualiza igual,
por consistencia (mismo criterio documentado ahí: "gana el sufijo que no se
puede equivocar").

Descartado: guardar el `platform` del canal para decidir el ruteo en vez del
sufijo. El propio diseño ya declara que el sufijo gana porque lo escribe
WhatsApp, no el operador — cambiar ese criterio para este caso puntual
reabriría la puerta a que un operador tipee mal la plataforma y rompa el
ruteo de un canal que sí venía funcionando.

Consecuencia: cualquier grupo cuyo `external_id` llegue en el futuro con otro
sufijo nuevo de WhatsApp (no hay evidencia de que exista otro hoy) volvería
a fallar igual, en silencio — se revisita si aparece otro caso real.

---

## 2026-08-21 — Revertido: el soporte de `@lid` como grupo estaba mal diagnosticado

Contexto: reemplaza la entrada del mismo día «El backfill de histórico
también reconoce grupos con JID `@lid`». El operador corrigió a mano el
`external_id` de «🛒Habana ventas» de `157724134363163@lid` a
`5355107677-1612646457@g.us` —un número completamente distinto— y eso
resolvió el problema. La verificación que sostenía la decisión anterior
(WAHA respondía 200 al pedir `/chats/157724134363163@lid/messages`) era un
falso positivo: el único mensaje que traía esa respuesta era una notificación
de sistema (`e2e_notification`/`encrypt`), no historial real del grupo.
`@lid` es el formato de privacidad que WhatsApp usa para números de teléfono
individuales — no existe un grupo legítimo con JID `@lid`; el valor guardado
originalmente nunca fue el JID real del grupo.

Decisión: se revierte el cambio de código (`_GROUP_JID_SUFFIXES` vuelve a
`_GROUP_JID_SUFFIX = "@g.us"` en `whatsapp_backfill.py`, `Channel.is_whatsapp_group`
vuelve a comparar solo contra `@g.us`) y se saca el test de regresión que
dependía de esa premisa. La causa real del fallo original era un
`external_id` mal capturado en el alta del canal, no una limitación del
ruteo por sufijo.

Descartado: dejar el soporte de `@lid` "por las dudas". Trataría cualquier
`external_id` mal cargado que termine en `@lid` como si fuera un grupo válido
y lo consultaría contra el endpoint de chats, arriesgando traer el historial
de un chat ajeno (individual, no el grupo que el operador cree estar
importando) en vez de fallar visiblemente con el error que ya existe.

Consecuencia: si un canal falla al importar histórico, la causa más probable
sigue siendo un `external_id` mal capturado — se corrige el dato, no el
código, salvo evidencia nueva de que WAHA realmente usa `@lid` para algún
grupo real.

---

## 2026-08-21 — Mensaje del backfill separa capturado de visible en la cola

Contexto: medido contra producción, 174 pendientes reales y solo 17 visibles
en `/curation` — el filtro de foco (2026-08-20) esconde lo que no menciona
ningún producto piloto. El mensaje del backfill decía «N anuncios nuevos EN
LA COLA DE CURADURÍA», dando a entender que iban a verse ahí; con canales
mayoristas genéricos, la mayoría queda escondida y el operador lo lee como
que la importación no hizo nada.

Decisión: el mensaje pasa a «N anuncios nuevos capturados, M van a verse en
la cola de curaduría». `M` se calcula sobre `parsed.text` de cada mensaje
recién ingerido (no duplicado), con `mentions_any_product` — mismo criterio
que `_pending_in_focus`, incluida su excepción: sin productos piloto
definidos, todo cuenta como visible.

Descartado: nada — cambio acotado al texto del flash y al cálculo que ya
tenía todo lo necesario a mano (`parsed.text`, catálogo de pilotos).

Consecuencia: el número de "visibles" puede quedar desactualizado si el
operador cambia el foco (agrega/saca un piloto) después de correr el
backfill — es una foto del momento de la importación, no una cuenta viva.
Aceptable: el mensaje es informativo y desaparece al recargar.

## 2026-08-21 — La medición de H0 recibe la provincia del canal

Contexto: al analizar los datos contra H0 (211 filas curadas de las 300 del
umbral) la precisión de provincia daba 46.9%, y el 93% de esos fallos eran
propuesta VACÍA, no propuesta errónea. La causa no era el extractor: la cola
de curaduría YA aplica la provincia fija del canal desde el 2026-08-17 ("el
canal siempre manda"), pero `compute_h0_report` re-corría `propose_all` sin
ese dato. Se estaba midiendo un extractor más ciego que el que corre en
producción — contra la base real, la provincia del canal coincidió con la
confirmada por el operador en 49 de 49 filas.

Decisión: `propose`, `propose_all` y `_tiered_proposals` aceptan
`channel_province` (keyword, default `None`) con semántica de override, no de
fallback: le gana a la línea y al texto completo. `measure.py` se lo pasa
resolviéndolo con la misma regla que la cola. La resolución del canal de
origen y su provincia se mudaron de `web/routers/curation.py` a
`saimc/ingestion/origin.py`, sin cambiar comportamiento: la capa de
extracción no puede importar de un router web sin invertir la dependencia.

Descartado: (a) semántica de fallback con el texto ganando — contradice la
decisión del 2026-08-17, y el dato no la sostiene: el canal acertó 49/49
mientras el texto, cuando propone algo, acierta 99/107; (b) duplicar la regla
en `measure.py` en vez de mudarla — dejaba dos definiciones de "el canal
siempre manda" divergiendo en silencio; (c) reemplazar el override post-hoc
de `curation.py` por el del motor — ese además rellena las filas en blanco de
más, que no salen de ninguna propuesta.

Consecuencia: la medición sube de 10.9% a 18.5% de extracción completa
(provincia 46.9% → 76.8%) sin curar una fila más, porque `measure.py`
re-corre el extractor sobre evidencia inmutable y la mejora es retroactiva.
El número anterior no era "más exigente", era de otro extractor. El cuello de
botella pasa a ser cantidad/unidad (23.7%): si se arreglara, la extracción
completa daría 52.1% sobre este mismo corpus.

## 2026-08-21 — La unidad de H0 se mide contra la fila, no contra el catálogo

Contexto: cantidad/unidad quedó como el peor componente de H0 (23.7%) y se
asumió que era un cuello de extracción — reglas pobres en `_extract_quantity`.
Al medirlo resultó ser lo mismo que la provincia: verdad de referencia
obsoleta. `measure.py:_quantity_unit_correct` comparaba la unidad propuesta
contra `Product.unit`, la unidad canónica del CATÁLOGO, y su docstring
justificaba eso diciendo que la unidad «no se persiste por fila». Dejó de ser
cierto: `prices_history.unit` existe y `curation.py` la completa con
`canonical_unit()`. Está poblada en 161 de 211 filas, y 95 de ellas dicen
`kg` mientras su producto en el catálogo dice `lb` — un saco de arroz se
anuncia y se confirma en kg aunque el arroz se venda por libra al menudeo.
La métrica contaba cada una de esas filas como fallo: penalizaba al extractor
por acertar. El anuncio real «Sacos Arroz Argentino 25Kg. $17,500 Cup. Sale
$318.20 Lb» lo confirma con su propia aritmética (17500 / 55.1 lb = 317.6).

Decisión: comparar contra `prices_history.unit`, la unidad que el operador
confirmó para esa fila, pasando los dos lados por `canonical_unit()`. La
normalización es necesaria porque los vocabularios difieren a propósito
(`saimc.units`): `engine.py` reconoce formas flexionadas del anuncio (`litro`,
`cajas`) y la base guarda las canónicas (`l`, `caja`). Una unidad propuesta
sobre una fila sin unidad pasa a contar como fallo, no como acierto por
ausencia.

Descartado: (a) ampliar el vocabulario de `_UNIT_WORDS` con plurales y formas
escritas (`lbs`, `libras`, `litros`, `unidades`) — medido contra el corpus,
BAJA la extracción completa de 40.8% a 33.6%, porque rompe las filas donde el
operador confirmó sin cantidad: cambia silencio por respuesta errónea; (b) un
default «1 unidad» cuando el texto no dice cantidad — sube cantidad/unidad a
56.4% pero deja la extracción completa clavada en 40.8%; (c) dejar la
comparación contra el catálogo por ser independiente de la propuesta — es
independiente pero hace la pregunta equivocada, y la alternativa no es más
honesta sino sistemáticamente errónea.

Consecuencia: cantidad/unidad 23.7% → 53.6% y extracción completa 18.5% →
40.8%, sobre las mismas 211 filas y sin curar ninguna. Se asume un costo
metodológico medido: `curation.py` prellena el formulario con la propuesta del
extractor, así que la columna puede ser eco. La tasa real de intervención del
operador sobre la unidad es 31.8% (56 filas donde escribió una unidad que el
motor no propuso, 7 cambios, 4 borrados). El eco ya afecta igual a producto y
precio, y CLAUDE.md §H0 ya asume que el operador corrige antes de guardar.
El techo de cantidad/unidad queda en 46.9% de extracción completa, así que
perfeccionarla entera vale +6.1 puntos: el cuello pasa a ser PRECIO (57.3%).

## 2026-08-21 — El precio está limitado por el OCR, no por sus reglas

Contexto: con la unidad ya arreglada, precio quedó como el peor componente de
H0 (57.3%) y se abrió como próximo cuello. El diagnóstico contra el corpus lo
desmintió como problema de reglas. Cortado por tipo de evidencia: sobre TEXTO
el precio se extrae al 78.9% (n=123, propuesta vacía 8.9%); sobre IMAGEN al
27.3% (n=88, propuesta vacía 64.8%). De los 90 fallos de precio, 64 están en
imágenes y 26 en texto. Y de esos 26, solo 19 tienen el número confirmado
presente en el texto: los otros 7 el operador los dedujo y ninguna regla los
recupera. A diferencia de provincia y unidad, acá la verdad de referencia NO
estaba obsoleta: `price_basis` no explica ninguno de los 19 números que
difieren (0 de 19 cumplen la razón precio/cantidad).

Decisión: no se toca la selección de precio. Se cierra un bug puntual de
moneda encontrado en el camino (ver abajo) y se deja el resto como está.

Descartado: mejorar el OCR por configuración. Medido sobre las mismas 77
imágenes con cuatro variantes de Tesseract — precio 27.3% con los defaults de
hoy, 27.3% con `--psm 6`, y 25.0% con upscaling 2x + escala de grises, con o
sin autocontraste. El preprocesado no mueve el precio y el upscaling lo
empeora; solo producto gana algo (73.9% → 78.4%). Un motor distinto o un
modelo de visión quedan fuera de Fase 0 por la restricción de entorno
(conexión lenta, sin descargas grandes) y por sobreingeniería.

Consecuencia: el precio sobre imagen es el techo real de H0 y no se levanta
con reglas. Las imágenes son 88 de 211 filas. La palanca disponible sigue
siendo la curaduría manual, que es justamente lo que H0 mide como costo: un
anuncio en imagen cuesta más trabajo por anuncio, no es dato inaccesible.

## 2026-08-21 — La marca de moneda pegada al número

Contexto: `_CURRENCY_RE` exigía `\b` antes del marcador, pero
`_PRICE_BEFORE_CURRENCY_RE` admite cero espacios entre número y marca. En
"4.45USD" no hay frontera de palabra entre "5" y "U": el precio salía de ese
"USD" y la moneda no lo veía, cayendo al default CUP. Los dos regex
discrepaban sobre el mismo texto, con un error resultante de ~700x. 29 de los
132 anuncios curados escriben la marca pegada.

Decisión: el borde izquierdo de `_CURRENCY_RE` pasa de `\b` a un lookbehind
negativo de letra. Sigue excluyendo el marcador incrustado en una palabra
("column" termina en "mn"), que es lo que `\b` protegía.

Descartado: quitar el borde izquierdo del todo — haría que cualquier palabra
terminada en "mn" o "cup" declare moneda.

Consecuencia: cero movimiento en la métrica sobre este corpus, y se registra
así a propósito. De las 29 ingestas afectadas, 22 traen solo "cup"/"mn"
pegado, donde el default CUP ya acertaba por casualidad; las 7 restantes
tienen marcador no-CUP pero fallan igual por el número. El arreglo cierra una
clase de error latente —a medida que entren anuncios en USD con la marca
pegada— sin mejorar el número de hoy.

## 2026-08-21 — El emparejado ambiguo no acredita precio

Contexto: `_match_rows_to_candidates` (solo en `measure.py`, no toca
producción) empareja cada fila confirmada con un candidato de `propose_all`.
Primero por `product_id`; lo que no matchea así se llevaba el primer candidato
que sobrara, viniera de la línea que viniera. Su docstring lo justificaba
diciendo que la fila se compara «contra la línea real que la originó», pero
eso solo vale si el orden de las filas confirmadas sigue el de las líneas del
anuncio, cosa que nada garantiza. Medido contra producción: 33 de 211 filas
caían al reparto; 13 ya quedaban vacías, 5 tenían una sola línea libre
(emparejado forzoso) y 15 tenían varias. De esas 15, 9 se llevaban el precio
por coincidencia con una línea de otro producto.

Decisión: el reparto distingue no tener elección de tener que adivinar. Con
una sola línea libre se empareja con ella, igual que antes. Con varias se
devuelve una propuesta que conserva solo la provincia —y solo si todas las
líneas libres coinciden en ella—, porque la provincia no depende de qué línea
se elija: la fija el canal para todo el anuncio (2026-08-17) y la cola se la
aplica a todas las filas por igual. Ninguna línea se consume en el caso
ambiguo: no se usó.

Descartado: (a) emparejar siempre contra una propuesta vacía cuando no hay
match de producto — mide de menos, tira la provincia que sí estaba bien
extraída y hunde esa métrica de 76.8% a 69.2%; (b) dejarlo como estaba — son
9 filas de precio acreditadas por azar; (c) emparejar por precio en vez de por
posición — cerraría el círculo, usando como pista lo mismo que se quiere
medir.

Consecuencia: precisión de precio 57.3% → 53.1%. El número BAJA y ese es el
resultado correcto: los 4.2 puntos que se pierden eran coincidencias, no
extracción. Provincia se sostiene en 76.8% gracias a conservarla, y
cantidad/unidad y extracción completa (40.8%) no se mueven, porque esas filas
ya fallaban por otro lado. Tercera vez que un número de H0 cambia por la
medición y no por el extractor.

## 2026-08-21 — El OCR lee la captura como un bloque, no como una página

Contexto: las imágenes son 88 de las 211 filas curadas y arrastran todas las
métricas. `ocr.transcribe` llamaba a Tesseract con los defaults, y el modo de
segmentación por defecto (psm 3) busca estructura de página —columnas,
bloques, orientación—. La evidencia real no es una página escaneada: es una
captura de pantalla de un chat de WhatsApp o Facebook.

Decisión: pasar `--psm 6`, que trata la imagen como un bloque uniforme de
texto. Medido sobre las mismas 77 imágenes: producto 84.4% → 85.3%, provincia
76.8% → 79.1%, precio 53.1% → 53.6%, cantidad/unidad 53.6% → 53.1%.

Descartado: (a) upscaling 2x + escala de grises, con o sin autocontraste —
sube producto pero BAJA el precio (27.3% → 25.0%) sobre las filas de imagen;
(b) un motor OCR distinto o un modelo de visión — fuera de Fase 0 por la
restricción de entorno (conexión lenta, sin descargas grandes) y por
sobreingeniería.

Consecuencia: la extracción completa NO se mueve (40.8%) — lo que mejora cae
en filas que ya fallaban por otro lado. Se adopta igual porque cada campo
bien prellenado es trabajo que el operador no hace, y eso es exactamente lo
que H0 mide como costo (CLAUDE.md, «H0 — métrica de eficiencia»). Cuesta ~45%
más de tiempo por imagen (~0.9s), imperceptible abriendo un anuncio por vez.
Queda registrado que el precio sobre imagen (26.1%) sigue siendo el techo
real y que no se levanta con configuración de Tesseract.

## 2026-08-21 — El webhook registra por qué descarta cada mensaje

Contexto: el canal «Tienda: ⚜️ The Boss» publicó 5 imágenes y solo una entró a
la cola. Al investigar, ese canal tenía 28 filas de texto y 1 sola imagen en
`raw_ingestions` mientras WAHA seguía teniendo las fotos. No hubo forma de
saber por cuál de los caminos de descarte se fueron: el webhook devolvía 200
con `{"ingested": 0, "reason": ...}` en todos ellos y no registraba nada, así
que el journal solo mostraba `POST /webhooks/whatsapp 200 OK`. El propio
`whatsapp_media` ya había anticipado este modo de falla: «no rompe nada y no
captura nada». Hasta acá el proyecto no tenía logging en ningún módulo.

Decisión: el webhook registra cada evento que NO termina en fila, con motivo
explícito y con el JID y la marca de tiempo necesarios para cruzar contra
WAHA. `_write_media_to_inbox` pasa a devolver `(ruta, motivo)` en vez de
`None` pelado, porque sus cuatro caminos de falla son indistinguibles desde
afuera y llevan a arreglos distintos: `media_sin_url_descargable`,
`media_url_no_confiable`, `sin_api_key_de_waha`, `descarga_fallida`. Los
motivos del endpoint son `json_invalido`, `sin_mensaje_utilizable`,
`canal_no_registrado`, `duplicado` e `ingesta_salteada`.

Nivel según lo que significa cada caso: WARNING cuando se pierde algo que
debía entrar, INFO cuando el no-ingreso es lo esperado. `duplicado` es INFO
porque WAHA manda DOS eventos por cada mensaje —los POST llegan siempre de a
pares en el journal— y a nivel WARNING sería la mitad del log.

**Nunca se registra el cuerpo del mensaje**, solo metadatos. El log no es la
capa pública, pero el texto de un anuncio trae teléfonos y el proyecto no
almacena datos de contacto (spec.md §10.6). Hay un test que lo fija y se
verificó también contra el proceso real, no solo con `caplog`.

`web/app.py` llama a `logging.basicConfig`: el despliegue lanza uvicorn sin
`--log-config` y uvicorn configura solo sus propios loggers, dejando el raíz
sin handler. Sin eso, un INFO no llega a journald y un WARNING sale por
`logging.lastResort` pelado, sin nivel ni nombre de logger. Verificado
levantando el servidor de verdad y mandándole eventos firmados.

Descartado: (a) devolver un código de error a WAHA en los descartes — haría
que reintente en loop, y el motivo del descarte casi nunca se arregla
reintentando; (b) registrar el payload completo para depurar — filtra
teléfonos al journal; (c) logging estructurado en JSON — no hay nada que lo
consuma, y `journalctl -u saimc -g webhook.descartado` alcanza.

Consecuencia: la próxima pérdida se puede atribuir. No arregla ninguna de las
tres causas conocidas por las que se pierden fotos —el backfill filtra a solo
texto y pide `downloadMedia=false`, el caption le gana a la foto en el
webhook, y `image/webp` está excluido—, que siguen abiertas.

## 2026-08-21 — Las fotos históricas de un canal no se pueden recuperar de WAHA

Contexto: tras el logging de descartes se abrió el slice de traer las fotos
del histórico, que `whatsapp_backfill` venía difiriendo («va en su propio
slice»). La hipótesis era rutear los canales `@newsletter` al endpoint de
grupos, porque `/chats/{id}/messages` con `downloadMedia=true` devolvía 5 de 5
URLs de media en 0.1s contra 2 de 7 en 13.0s del preview.

La medición de profundidad la refutó. Contra el canal «Tienda: ⚜️ The Boss»:

    /chats/{id}/messages          22 mensajes, 08-19 a 08-20  (1 día)
    /channels/{id}/messages/preview   239 mensajes, 08-15 a 08-21  (6 días)

El store local de WEBJS es diez veces más chato y ni siquiera llega al día de
hoy. Cambiar el ruteo canjearía seis días de historia por uno con fotos, y
además perdería los mensajes más recientes, que es lo contrario de lo que un
backfill tiene que hacer. El `limit` no cambia nada: 22 es todo lo que tiene,
con 50, 250, 1000 o 5000.

Los otros dos caminos también están cerrados, medidos:

- **`downloadMedia=true` en el endpoint de canales no se amortiza.** Tres
  corridas seguidas con `limit=10`: 13.0s, 12.9s, 13.0s. Es ~1.3s por mensaje,
  siempre, y a `limit=250` serían ~325s — muy por encima de `waha_timeout`
  (30s) y en el rango que el comentario de `_HISTORY_LIMIT` documenta como
  "la sesión se cuelga y se reinicia perdiendo webhooks". Y aun pagándolo,
  solo 2 de 10 mensajes obtienen URL.
- **No hay endpoint por mensaje.** `/chats/{chat}/messages/{id}` existe (404
  estructurado, no "Cannot GET") pero no encuentra el mensaje: no está en el
  store local, el mismo límite de los 22.

Decisión: no se toca el backfill. Sigue trayendo solo texto con
`downloadMedia=false`, que es barato y profundo.

Consecuencia: la recuperación de las fotos ya perdidas queda descartada
mientras la instancia use el motor WEBJS. La captura de fotos solo puede
mejorar hacia adelante, por el webhook en vivo.

## 2026-08-21 — El caption no es el cuello de la captura de fotos

Contexto: se creía que la regla «el caption le gana a la foto»
(`whatsapp_webhook.py`) era la causa principal de que no entren fotos.
Clasificados los últimos 239 mensajes del canal «Tienda: ⚜️ The Boss»:

    foto SIN caption   162  67.8%   <- el código actual YA debería capturarlas
    solo texto          47  19.7%
    ni texto ni media   22   9.2%
    foto CON caption     8   3.3%   <- el bug del caption

El caption explica el 3.3%. El grueso son 162 fotos sin caption que el camino
actual ya contempla, y sin embargo SAIMC tiene UNA sola imagen de ese canal.
O sea: lo que falla es lo que ya debería funcionar, y todavía no se sabe por
qué — el logging de descartes se sumó recién y desde el reinicio no entró
tráfico que atribuir.

Decisión: no se toca la regla del caption todavía. Arreglar un 3.3% antes de
entender el 67.8% es optimizar la rama equivocada, y además el cambio no es
barato: exige que la cola sepa proponer desde el `original_text` de una fila
`image`, y hoy `curation.py` no lo hace — una fila IMAGE con `original_text`
no cae en ninguna de las dos ramas y deja el formulario vacío. Ese hueco está
latente, no vivo: las 77 filas de imagen con `original_text` ya están curadas
y no hay imágenes pendientes.

Descartado: cambiar la regla del caption «de paso» — redefiniría qué significa
`original_text` en una fila de imagen, que hoy es la transcripción corregida
por el operador y de la que `measure.py` se aparta a propósito. Production
propondría desde el caption mientras H0 mide OCR de la foto: exactamente la
divergencia medición/producción que se corrigió dos veces este mismo día.

Consecuencia: el próximo paso es leer `journalctl -u saimc -g
webhook.descartado` cuando el canal publique fotos, y recién ahí decidir.

## 2026-08-21 — El conjunto de referencia de H0 se cuenta en anuncios

Contexto: revisando qué significan los contadores de `/control` apareció que
`measure.py` comparaba el umbral de 300 contra las FILAS evaluadas, mientras
su propio comentario decía «300 anuncios etiquetados a mano». Código y
comentario se contradecían, y `docs/decisions.md` también consigo mismo: la
entrada del avance de OCR habla de «las 300+ filas que H0 exige», mientras las
otras cuatro menciones dicen anuncios.

La especificación no es ambigua y lo dice en las dos secciones. §3: «el
operador etiqueta manualmente un conjunto de referencia de al menos 300
anuncios reales. Las reglas de extracción se miden contra ese conjunto». §9:
«Conjunto de referencia etiquetado de 300+ anuncios». Y usa la otra unidad, a
propósito, para la meta del producto de datos: «500 registros validados», que
es la que `progress.PHASE0_TARGET_ROWS` ya respeta. Son dos números en dos
unidades para dos cosas distintas.

Decisión: `H0Report` gana `evaluated_ads_count` —cuántos anuncios distintos
aportaron al menos una fila evaluada— y el aviso de "todavía no es oficial" se
compara contra ese número. Las cinco precisiones se siguen calculando por
FILA: «¿cuántas extracciones salieron bien?» se responde por extracción,
«¿alcanza la muestra?» se responde por evidencia. La constante pasa de
`_H0_MINIMUM_SAMPLE` a `_H0_MINIMUM_ADS` para que el nombre diga la unidad.

El motivo de fondo es de independencia estadística, no de literalidad: las
filas de un mismo anuncio comparten evidencia, OCR, canal y provincia, así que
fallan juntas. Medido contra producción, un solo anuncio aportaba 25 de las
211 filas — el 11.8% de la muestra colgando de una sola pieza de evidencia.
Contar filas infla el tamaño de muestra sin agregar información.

Un curado sin fila confirmada no engrosa el conjunto: no aportó ninguna
comparación. Por eso el número es 130 y no los 132 curados.

Descartado: (a) dejarlo en filas y corregir el comentario — es la lectura
cómoda, contradice la spec en sus dos secciones y sostiene una muestra
inflada; (b) apartarse de la spec con motivo registrado — no hay motivo, la
lectura por anuncios es además la estadísticamente correcta.

Consecuencia: el conjunto de referencia pasa de "211 de 300" a **130 de 300**,
y lo que falta de 89 filas a **170 anuncios**. A ~21 filas/día eso es unas
tres semanas, no cuatro días. El número de H0 no cambia (40.8%); cambia
cuándo se lo puede llamar oficial. La medición anterior se habría declarado
oficial con menos de la mitad de la evidencia que la spec pide.

## 2026-08-22 — Sugerencia asistida de descarte, por regla explícita y no por similitud de texto

Contexto: el operador reportó que llega mucho mensaje sin información
económica útil y que curarlo le come tiempo. Propuso auto-descartar un
mensaje nuevo cuando matchea por similitud (~80%) contra la lista de
descartados previos. Medido contra los 159 descartes reales de producción
(31.7% de 502 filas) antes de diseñar nada: 0 grupos de texto casi-idéntico
entre esos descartes — el ruido no se repite palabra por palabra, es
estructural. El 66.7% de los descartes con texto no menciona ningún producto
del catálogo piloto; el resto se explica por "AGOTADO"/⛔ o venta mayorista
("contenedor", "cajones"). Además, 4 canales concentran casi todo el
descarte con canal conocido.

Decisión: `probable_discard_reasons()` (`extraction/engine.py`) combina esas
tres reglas con OR y las muestra en `/curation` como una marca — "Probable
descarte: <razones>" — con un botón de un clic que el operador confirma o
ignora. Nunca auto-descarta: spec.md §6.1 exige que el operador siempre
confirme o corrija, y la cola es a la vez la herramienta y el instrumento de
medición de H0 — auto-descartar antes de que la vea rompería esa garantía en
silencio. La tasa histórica de descarte del canal (`_channel_discard_stats`)
se muestra aparte, como dato informativo sin umbral de corte (`spec-map.md`
exige no inventar umbrales nuevos de spec.md §12.6): no dispara nada por sí
sola.

La regla "no menciona producto del foco" reutiliza `mentions_any_product()`
y es redundante para anuncios de TEXTO — el filtro de foco (2026-08-20) ya
los esconde de la cola antes de este punto — pero no para IMÁGENES: esas
entran igual y se transcriben recién al mostrarse, así que ahí la señal sí
aporta. El banner de esta sugerencia no convive con el de "sin candidatos"
(2026-08-17): cuando ese ya se muestra, es la señal más fuerte y un segundo
banner encima sería ruido.

Descartado: fuzzy-match de texto contra el historial de descartados (la
propuesta original) — la medición lo refuta directamente, no hay grupos que
agrupar con un umbral de similitud; auto-descarte sin supervisión — rompe la
garantía de spec.md §6.1 y arriesga perder anuncios reales en silencio si la
regla se equivoca.

Consecuencia: la cola gana una señal barata (sin llamada a modelo ni
comparación de texto contra texto) que cubre la mayoría del ruido medido,
sin tocar el esquema ni el flujo de guardado. 745 tests verdes (+7 sobre los
738 previos).

## 2026-08-22 — La curaduría cierra como línea de desarrollo; sigue como rutina de dato

Contexto: el operador preguntó si ya se puede dejar de implementar sobre la
curaduría y empezar a construir la fase siguiente, sin dejar de curar. La
pregunta traía una premisa equivocada compartida por los dos: que el mapa de
calor y las tarjetas de producto son Fase 1. No lo son. `spec.md` §9, en
«Alcance del MVP inicial» de la **Fase 0**, lista textualmente
«Visualización: Prototipo de mapa de calor simple y tarjetas de producto».
Construirlo no es adelantarse de fase: es el único ítem del alcance de Fase 0
que no existe. Verificado contra el código: los routers son `catalog`,
`control`, `curation` y `whatsapp_webhook` — no hay una sola ruta pública.

El resto del checklist de actividades de Fase 0 está cumplido: backend
FastAPI + PostgreSQL, modelo de datos mínimo, interfaz de curaduría (cola,
pegado, edición, historial, descarte, evidencia) y motor de reglas en modo
entrenamiento. Lo que falta además de la visualización es **dato, no código**:
142 de 300+ anuncios del conjunto de referencia, y 166 de 500 filas en los 5
productos bandera (`is_pilot`: Aceite, Arroz, Azúcar blanca, Picadillo,
Pollo), que concentran 166 de 225 filas (74%) — la cola larga son 59 filas en
53 productos, así que la curaduría no está dispersando esfuerzo.

Decisión: la curaduría deja de ser línea de desarrollo y pasa a ser rutina
diaria de acumulación de dato. El desarrollo sigue por la visualización de
Fase 0, y su primer slice **no es el mapa sino el precio comparable por
unidad canónica**, porque todas las vistas lo consumen: sin él se construyen
seis pantallas sobre un promedio que miente (medido: la serie diaria de
Arroz/La Habana da 29, 3.463, 15.787 y 30.885 CUP en días consecutivos). Ese
slice toca el motor de agregación, así que va por flujo completo de
especificación (`CLAUDE.md`).

Descartado: (a) esperar a cerrar H0 antes de construir la visualización — la
decisión del 2026-08-12 ya estableció que H0 no bloquea el lanzamiento del
dashboard, y el conjunto de referencia se completa curando, no programando;
(b) empezar por el mapa con precios crudos para «tener algo que mirar» —
un mapa de calor coloreado con promedios que mezclan moneda, base y unidad
enseña una conclusión falsa, y es más caro desmentirla después que no
publicarla; (c) seguir agregando herramientas a la cola de curaduría, que era
la inercia de las últimas nueve sesiones.

Consecuencia: la curaduría queda congelada como superficie de desarrollo —
tocarla otra vez exige justificar por qué, no basta con que sea lo conocido.
El backlog gana la entrada del prototipo de visualización con las features
que la spec ya define. Y queda pendiente una decisión de privacidad antes de
la Transparency Layer: mostrar el anuncio que sustenta cada precio (§6.5) es
casi gratis porque `/evidence/{ingestion_id}` ya existe, pero publicar la
captura cruda rompe la mitigación §10.6 — se publica el texto redactado, no
el píxel (contradicción #1 de `spec-map.md`, todavía abierta).

## 2026-08-22 — La equivalencia entre unidades no es un umbral de §12.6

Contexto: `compute_retail_ranges` exigía que `prices_history.unit` coincidiera
con `Product.unit` y descartaba en silencio todo lo demás. Su docstring lo
justificaba así: «Sin tabla de conversión lb↔kg: no hay una definida, y
adivinar un factor es exactamente lo que §12.6 prohíbe».

La cita no aplica. §12.6 enumera cinco parámetros —N mínimo por celda, período
mínimo de sostenimiento, categorías mínimas por provincia, criterios de fuente
de alta confianza, ventana de validez de la tasa— y los cinco son umbrales de
**juicio que deciden qué se publica**, que la especificación manda calibrar
contra datos reales precisamente porque admiten más de un valor defendible. Una
libra son 0,45359237 kg exactos por el acuerdo internacional de 1959: no se
calibra, no gobierna qué se publica y no admite otro valor. Tratarla como
umbral no fue prudencia, fue una categoría equivocada.

Lo que costaba, medido contra producción antes del cambio: Arroz declara `lb`
en el catálogo pero 71 de sus 99 filas vienen en kg; Azúcar blanca 17 de 25;
Pollo 10 de 29 en kg más 2 en `caja`. De 166 filas de productos bandera,
**unas 108 quedaban afuera solo por la unidad**. La vista minorista devolvía
para Arroz/La Habana una mediana de **30.885 CUP por libra con muestra 1** —
absurda, y sostenida por una única fila mal curada.

Decisión: `units.py` gana `convert_price_per_unit()`, que expresa cada unidad
en la base de su magnitud (gramo para masa, mililitro para volumen) en vez de
una tabla de pares, y devuelve `None` cuando la conversión no está definida —
misma convención de «nunca inventa» que `canonical_unit()`. `cells.py` la
aplica al **precio por unidad**, después de dividir por la cantidad: al revés
convertiría el total del lote. La clave de celda pasa a usar la unidad canónica
del producto, así que las filas en kg y en lb del mismo producto dejan de ser
dos series que no se hablan. Lo que sigue afuera se cuenta en
`excluded_by_unit` en lugar de descartarse callado.

`compute_cells` **no** se toca: separar kg de lb ahí es deliberado, porque esa
vista existe para que el operador vea qué está mezclando al cargar, y ocultarle
la unidad cruda le sacaría justamente la señal que necesita.

Descartado: (a) una tabla de pares origen→destino — N² entradas para el mismo
resultado, y sumar una unidad obligaría a tocar N líneas; (b) convertir
`docena`↔`unidad`, que es una equivalencia igual de cierta pero que ninguna
fila del corpus usa: sería vocabulario sin dato que lo respalde; (c) convertir
masa a volumen para el aceite, que exigiría la densidad del producto y sí
sería inventar un número; (d) normalizar también `compute_cells`, por el motivo
de arriba.

Consecuencia: contra producción, Arroz/La Habana a 30 días pasa de **n=1,
mediana 30.885** a **n=17, mediana 312,39 CUP/lb**, que es precio de mercado
real. Arroz/Santiago aparece con n=12 y Azúcar blanca/Santiago con n=5, celdas
que antes no existían. Las excluidas por unidad caen a 7 en 30 días, y son
exactamente las que no son convertibles ni en principio: 2 de Pollo en `caja` y
las 5 de Picadillo, cuyo catálogo declara `unidad` mientras sus filas vienen en
`lb` — el contador dejó a la vista que ahí el dato malo es la unidad del
catálogo, no el anuncio.

El cambio también **destapa** lo que el filtro tapaba: los máximos siguen
envenenados por filas con `price_basis` mal curado (Arroz/La Habana llega a
30.885 por una fila marcada `'unit'` que es el total del saco). La mediana ya
es robusta a eso, el máximo no. Queda en backlog como validación aparte.

768 tests verdes (+23 sobre los 745 previos).

**Nota de entorno, aparte de la decisión:** correr `pytest` desde un worktree
usa el `saimc` instalado en modo editable del checkout **principal**, no el
código del worktree — los tests dan verde contra código que no es el que se
está editando. Hay que forzar `PYTHONPATH=<worktree>/src`. Es el mismo tipo de
trampa silenciosa que ya tenía `measure.py` con `data/raw`.

## 2026-08-22 — El ADR del stack se escribe; el mapa de calor es SVG inline

Contexto: al abrir la línea de visualización se reabrió como pregunta un stack
que §13.1 cerró hace meses, y se propuso como acordado «Angular y
OpenStreetMap». Verificado: Angular está en §13.1 pero **diferido a Fase 2**, y
OpenStreetMap **no aparece en `spec.md`** (cero coincidencias para
`openstreetmap|osm|leaflet|mapbox|geojson|tiles`; la única pieza cartográfica
nombrada es MapLibre GL). La causa es documental: §13.1 línea 1636 pide un ADR
en `docs/decisions/001-stack-python-unico.md` y nunca se escribió, así que el
camino había que reconstruirlo cada vez desde §13.1 más `CLAUDE.md` más nueve
entradas de este log.

Decisión: se escribe el ADR y se registran dos apartamientos de §13.1. (a) El
mapa de calor es **SVG inline de las 16 provincias**, no MapLibre GL: son
polígonos fijos coloreados por una categoría que el servidor ya calcula, y un
motor de mapas introduce una dependencia de red en runtime donde hoy no hay
ninguna — el frontend entero pesa 120K (28K CSS + 92K fuentes) y no llama a
ningún tercero. OpenStreetMap entra como **fuente de la geometría**, derivada
una vez y vendorizada, mismo criterio que ya se aplicó a Roboto. (b) **HTMX no
se usa** y el apartamiento ya era un hecho: no existe la dependencia, ni un
atributo `hx-*`, ni el `<script>`; la única mención es un docstring en
`src/saimc/web/__init__.py:1` que describe algo que no está. Se adopta el
estado real —server-rendered con JS propio como mejora progresiva— en vez de
sumar una dependencia para coincidir con una lista.

Descartado: (a) adelantar Angular a Fase 0 — es lo que pedía el pitch §14.1 y
§13.1 ya rechazó (entrada del 2026-08-12), y son dos stacks en paralelo con un
solo desarrollador; (b) MapLibre con tiles autohospedados — un servicio más que
operar, contra la decisión 4 de `CLAUDE.md`; (c) Leaflet + tiles raster de OSM —
una petición de red por tile, por pan y por zoom, para un basemap que la
pregunta del usuario no necesita.

Consecuencia: la visualización de Fase 0 arranca sin decisiones de stack
pendientes. El ADR fija cuatro invariantes que hacen barata la Fase 2 (la
agregación fuera de las plantillas, cálculo en funciones puras testeables, cero
assets de terceros, el servidor decide y el JS mejora) — los cuatro ya se
cumplen hoy sin estar declarados. Queda por corregir el docstring de
`web/__init__.py` y por decidir, al construir esa vista, si el gráfico de
evolución usa Chart.js o SVG generado en servidor.

## 2026-08-22 — HTMX sí entra en el dashboard (corrige la entrada anterior de hoy)

Contexto: la entrada anterior de hoy descartó HTMX y lo registró como
apartamiento de §13.1. El operador preguntó por qué no usarlo. Al medir en vez
de argumentar de memoria, el descarte no se sostuvo.

Decisión: **se revierte el descarte de HTMX**. El dashboard lo usa, vendorizado
en `/static` como el CSS y las fuentes, nunca desde CDN. Queda alineado con
§13.1, así que deja de ser apartamiento — el apartamiento que sobrevive es solo
el del mapa (SVG inline en vez de MapLibre GL). La curaduría **no** se
retrofitea: funciona, está testeada y su JS propio ya está escrito como mejora
progresiva. Toda interacción del dashboard tiene que degradar a enlace o
formulario común con JS deshabilitado.

Tres errores en el razonamiento descartado, anotados porque son instructivos:
(a) se le aplicó a HTMX el argumento de MapLibre, cuando el problema de MapLibre
es una dependencia de **red en runtime** (tiles por pan y por zoom) y HTMX es un
archivo estático que cumple el invariante «cero peticiones a terceros» tal como
está escrito; (b) se generalizó desde la curaduría, que ya está construida, al
dashboard, que no lo está y tiene otro patrón de interacción; (c) se comparó
contra «nada», cuando la alternativa real son swaps parciales con `fetch` y DOM
a mano — y con un solo desarrollador, 16,5 KB de biblioteca probada le gana a
código a medida sin tests.

Medición del 2026-08-22, que es lo que cierra la discusión: htmx 2.x pesa 51.238
B minificado y **16.539 B gzipeado**. `saimc-material.css` pesa 28.672 B y
**6.079 B gzipeado**. Y `src/saimc/web/app.py` **no tiene `GZipMiddleware`**:
hoy ese CSS viaja sin comprimir. Activarlo ahorra ~22 KB, HTMX cuesta ~16,5 KB,
así que con las dos cosas la primera carga pesa **menos que hoy sin ninguna**.
El argumento de la conexión lenta apuntaba al revés de como se lo usó.

Descartado: (a) sostener el descarte de HTMX por peso — refutado por la medición;
(b) retrofitear la curaduría a HTMX — churn sin beneficio sobre código que ya
funciona y está testeado; (c) escribir los swaps con `fetch` y DOM propio.

Consecuencia: se agrega `GZipMiddleware` a `app.py`, independiente de HTMX y
antes que él, porque es la optimización más barata que quedaba. `spec-map.md`
pierde el apartamiento de HTMX y gana la nota de que es deuda: nombrado en
§13.1 y en el docstring de `web/__init__.py:1`, pero sin implementar. El ADR 001
queda revisado el mismo día que se escribió, con el razonamiento corregido
adentro en vez de borrado.

## 2026-08-22 — `GZipMiddleware` con `compresslevel=6`

Contexto: la entrada anterior destapó que `src/saimc/web/app.py` no registraba
ninguna compresión. `saimc-material.css` viajaba como 28.672 B cuando gzipeado
son 6.079 B — ~22 KB en cada primera carga contra la restricción que gobierna
toda la capa web (conexión lenta e intermitente).

Decisión: `app.add_middleware(GZipMiddleware, minimum_size=500,
compresslevel=6)`. El nivel **6 y no el 9** que trae Starlette por defecto:
medido sobre el CSS real, el 9 da 6.079 B y el 6 da 6.113 B. Son 34 bytes —medio
por mil— a cambio de bastante más CPU en cada respuesta. El 1 sí se nota
(7.548 B), así que 6 es donde la curva se aplana. `minimum_size` queda en el
default: por debajo de 500 B el encabezado y el overhead del gzip se comen la
ganancia, y los fragmentos del dashboard van a caer ahí seguido.

Las fuentes no se comprimen dos veces sin que haya que configurar nada:
`font/woff2` está en `DEFAULT_EXCLUDED_CONTENT_TYPES` de Starlette 1.6, y
`mimetypes` resuelve `.woff2` a ese tipo exacto. `image/svg+xml` **no** está
excluido, que es lo que queremos para el mapa de calor del ADR 001: es texto y
comprime muy bien.

Descartado: (a) dejar el `compresslevel=9` del default — 34 bytes no pagan el
CPU; (b) excluir tipos a mano — el default de Starlette ya cubre exactamente lo
que había que cubrir, y duplicarlo en código propio es una lista que se
desactualiza sola.

Consecuencia: 774 tests verdes (+6). La cobertura vive en
`tests/test_static_assets.py::TestCompresion` y se verificó que los dos casos
positivos van a **rojo** sin el middleware, no que simplemente estén verdes.
Esa verificación destapó de paso que `test_ninguna_vista_pide_assets_a_la_red`
pasa en vacío para 3 de sus 4 rutas: van detrás de `require_operator`, devuelven
401, y una página de error tampoco contiene `fonts.googleapis.com`. Queda en
backlog.

## 2026-08-22 — htmx 2.0.10 vendorizado, y corrección de los bytes que informé mal

Contexto: el ADR 001 aprobó htmx para el dashboard; faltaba traerlo al repo. Al
copiarlo apareció además que los tamaños que informé en las tres entradas
anteriores de hoy estaban mal.

Decisión: `src/saimc/web/static/htmx.min.js`, htmx **2.0.10**, junto al CSS y las
fuentes. **Procedencia verificada, no descargada y ya**: se bajó el tarball
oficial de npm, se comprobó su `integrity` sha512 publicado contra el archivo
recibido, se extrajo `package/dist/htmx.min.js` de ahí, y resultó byte a byte
idéntico al que sirve unpkg. Dos fuentes independientes, mismo stream. El sha256
(`71ea6718…`, 51.238 B) queda fijado en `tests/test_static_assets.py`: cambiar el
bundle sin repetir la verificación pone la suite en rojo.

**Ninguna plantilla lo carga todavía, a propósito.** El ADR deja a la curaduría
sin htmx, así que meterlo en `_shell.html` haría que las tres vistas del operador
se bajen 51 KB que no usan. La vista que lo necesite opta desde su
`{% block scripts %}`, y `_shell.html` documenta cómo. El cache-busting va por
`HTMX_VERSION` en `app.py` —expuesto a Jinja como `htmx_version()`— y no por
mtime como el CSS: el bundle es inmutable entre bumps y un `git clone` le cambia
el mtime sin cambiar un byte.

**Corrección de medición**, que reemplaza los números de las entradas de hoy
sobre HTMX y `GZipMiddleware`: informé 28.672 B de CSS y «92 KB» de fuentes.
Salían de `du`, que cuenta bloques de disco redondeados, no bytes. Con `stat`:
CSS **26.390 B**, fuentes **81.688 B** (5.756 + 32.796 + 43.136), frontend
completo **108.078 B**, no 120K. El ahorro de gzip sobre el CSS es 20.311 B, no
~22 KB. Ninguna decisión cambia —la primera carga con htmx y compresión son
104.306 B contra 108.078 B sin ninguna de las dos, así que sigue siendo
favorable— pero el margen es más chico de lo que dije y los números publicados
tienen que ser los reales.

Descartado: (a) cargar htmx desde CDN — contradice el invariante 3 del ADR y es
la forma más fácil de romperlo, así que `unpkg.com` entró a la lista negra del
test de assets; (b) meterlo en `_shell.html` «total ya está» — le cobra 51 KB a
la curaduría, que el ADR dejó deliberadamente afuera; (c) vendorizar sin fijar el
hash — un blob de 51 KB que nadie puede volver a verificar es deuda, no un asset.

Consecuencia: 778 tests verdes (+4). El dashboard puede usar htmx sin decisiones
pendientes ni descargas. Queda para cuando exista la primera vista que lo cargue:
ajustar el docstring de `src/saimc/web/__init__.py:1`, que sigue anunciando HTMX
como si ya estuviera en uso.

## 2026-08-22 — Primera ruta pública: el mapa de cobertura de las 16 provincias

Contexto: la visualización de Fase 0 era el único ítem del alcance de §9 sin
construir, y no había una sola ruta pública en el proyecto. El primer slice
acordado eran las dos tareas que no dependen de ningún hueco de dato: la
geometría y la ruta.

Decisión, en tres partes.

**1. La geometría sale de Natural Earth 10m 5.1.1, que es dominio público.** Se
evaluó geoBoundaries, que trae exactamente las 16 unidades administrativas de
Cuba, pero deriva de OpenStreetMap y es **ODbL**: atribución obligatoria y
share-alike sobre la base de datos. Para algo que se va a publicar, dominio
público es una atadura menos. `tools/build_cuba_map.py` lee el shapefile con un
parser mínimo de stdlib —sin GDAL, pyshp ni geopandas: una dependencia de decenas
de MB para leer 16 polígonos una sola vez no se paga con esta conexión—,
descarta los cayos por debajo de 0,001 grados² conservando siempre el anillo
mayor de cada provincia, simplifica con Douglas-Peucker a 0,008° y proyecta en
equirectangular con paralelo estándar en el centro del país. El épsilon se eligió
midiendo: a 1000 px de ancho un píxel son ~0,0105°, así que 0,008 queda por
debajo del píxel y baja de 5.377 puntos a **1.629**. La herramienta queda en el
repo por la misma razón por la que htmx lleva su sha256: un asset derivado que
nadie puede rehacer ni auditar es deuda.

Natural Earth todavía usa «Ciudad de la Habana», el nombre anterior a la
reorganización de 2011; el mapeo al nombre vigente está en la herramienta y hay
un test que compara las 16 claves de la geometría contra los nombres sembrados
por `alembic/versions/0003_seed_provinces.py`. Si esa corrección se pierde al
regenerar, La Habana queda muda y nada más avisaría.

**2. `GET /` es la primera ruta sin `require_operator`.** Shell propio
(`_public_shell.html`), que **no** extiende el del operador a propósito: ese trae
el rail de navegación a Catálogo / Curaduría / Control, y una página pública no
puede ofrecer enlaces a la herramienta interna. Hay un test que lo verifica. El
SVG va inline —cero peticiones extra— y el reparto de trabajo respeta el
invariante 1 del ADR 001: `web/map_view.py` arma la estructura con una función
pura y la plantilla solo la recorre.

**3. El mapa colorea cobertura, no precio, y eso es deliberado.** §6.7 describe
el gris como «sin datos **suficientes**» pero nunca dice cuántas observaciones
son suficientes, y §12.6 es explícita: los umbrales que la v3.5 introdujo sin
valor asignado no se inventan. Así que la página distingue lo único verificable
—una provincia tiene observaciones o no las tiene—, muestra el n al lado (que es
lo que §9.0 exige desde el día 1 en lugar de un titular de precio) y dice
textualmente que todavía no publica precio. Hay un test que falla si alguna cifra
en CUP aparece en esa página antes de que el umbral se mida.

Descartado: (a) geoBoundaries por la licencia; (b) inventar un umbral de
«datos suficientes» para tener los tres colores de §6.7 — es exactamente lo que
§12.6 prohíbe, y un mapa que dice «rojo» sobre 6 observaciones miente con más
autoridad que uno que no dice nada; (c) extender `_shell.html` en la vista
pública.

Consecuencia: 792 tests verdes (+14). Medido contra producción, la página
responde 200 y dice «2 de 16 provincias con observaciones, 218 en total», con La
Habana en 113 y Santiago en 105. Son 24.935 B de HTML que el `GZipMiddleware`
entrega en 9.255 B. Queda anotado en backlog que la vista pública arrastra los
~6 KB de estilos de curaduría que no usa.

## 2026-08-22 — Detectar `price_basis='unit'` que la propia celda desmiente

Contexto: al medir qué publicaría la tarjeta de producto apareció que el rango
minorista no es publicable. Aceite/La Habana daba **2.100 – 4.000 – 1.264.260
CUP/l** y Arroz/La Habana **277 – 312 – 30.885 CUP/lb**. Mi recomendación previa
de que la tarjeta «no depende de ningún hueco de dato» quedó refutada por la
medición y se corrige acá.

La causa del aceite resultó peor que mala curaduría: la fila es
`1,90 USD · 900 ml · price_basis='unit'`. Son 1,90 por la botella —un total— mal
marcado como unitario. Leído como precio por mililitro y convertido a litro, el
error se multiplica por **mil**. O sea: un `price_basis` mal curado no produce un
error proporcional, produce uno del orden del factor de conversión de unidad.

Decisión: `cells.py:detect_basis_suspects()`, un detector **sin ningún umbral**.
Para cada fila `'unit'` con `quantity != 1` calcula las dos lecturas posibles del
mismo número —`as_unit` y `as_total = price/quantity`, ambas convertidas a la
unidad canónica— y señala la fila cuando la lectura de **total** queda más cerca,
en proporción, de la mediana de las filas **no ambiguas** de su propia celda. No
hay corte de precio, ni factor mínimo de sospecha, ni «ningún arroz vale más de
X»: solo se compara cuál de las dos interpretaciones concuerda mejor con el
vecindario de la fila. Es lo que `docs/backlog.md` pedía y lo que §12.6 exige.

Fila **no ambigua** es la que no puede estar mal marcada de esta forma:
`quantity == 1` o `price_basis == 'total'`. Si la celda no tiene ninguna, **no
hay veredicto** y la fila no se reporta — es el caso de Picadillo, cuyas filas
`'unit'` con `quantity != 1` están bien y no tienen contra qué contrastarse.

La distancia se mide en proporción y no en diferencia absoluta porque el error es
multiplicativo. Se implementó como `max(a/b, b/a)` en vez de `abs(log(a/b))`:
misma monotonía, sin pasar por `float` y perder la exactitud de `Decimal` justo
en la comparación que decide.

Medido contra producción, 7 filas señaladas y **ninguna** es un falso positivo:
Aceite ×369,4; Arroz/Habana ×98,9; tres de Arroz/Santiago ×23,3 (17.500 por 25
kg); y las dos de Pollo/Habana, que leídas como total dan **las dos 850,00/lb**
contra una celda de 806,06 — dos filas independientes coincidiendo, que era
justamente la evidencia que el backlog había anticipado. Picadillo no aparece.

Descartado: (a) excluir las sospechosas del agregado automáticamente — el
detector señala, no corrige, y quien juzga sigue siendo el operador; (b) un
corte absoluto de precio, que §12.6 prohíbe y que además marcaría mal a
Picadillo; (c) exigir un factor mínimo de sospecha, que sería el umbral
inventado que todo este diseño evita.

Consecuencia: 800 tests verdes (+8). La tabla vive en `/control` —es trabajo de
curaduría, no una estadística pública— con las dos lecturas y la referencia al
lado. Los agregados **no cambian**: el rango minorista sigue mostrando el máximo
envenenado hasta que el operador corrija las filas, y esa corrección ahora es
dirigida en vez de a ciegas sobre 202 filas. La tarjeta de producto sigue
bloqueada hasta entonces.

## 2026-08-22 — Tarjeta de producto: se publica el rango, nunca un valor puntual

Contexto: §9.0 parecía prohibir mostrar precio en Fase 0, y sobre esa lectura el
mapa se había construido sin ninguna cifra. Al leer la sección completa
(líneas 1218-1222) resultó ser más precisa: «Ninguna celda muestra un precio
**como titular sin respaldo de muestra**. Por debajo del umbral, el dashboard
muestra el estado "muestra en formación" con el número de observaciones a la
vista, **nunca un valor puntual presentado como medición consolidada**».

No prohíbe el precio: prohíbe el valor puntual sin respaldo. Y como la misma
§9.0 dice que «las Fases 0 y 1 son el período de calibración de N», hoy todas
las celdas están por debajo del umbral porque el umbral todavía no existe.

Decisión: `web/card_view.py` con tres reglas que salen directo de ese párrafo.
(1) El titular de cada tarjeta es el **rango mín–máx**, no la mediana sola; la
mediana aparece adentro, en prosa («mitad de los anuncios, cerca de X»), nunca
como cifra única. (2) Toda tarjeta va rotulada **«muestra en formación»**, sin
estado alternativo: uno condicional exigiría el umbral que §12.6 prohíbe
inventar. (3) El **tamaño de muestra va en la tarjeta**, junto al precio, no en
una nota al pie.

Con `n=1` no se muestra rango sino el número solo y «una sola observación, sin
rango todavía»: mínimo, mediana y máximo son el mismo valor, y presentarlos como
rango sugeriría una variación que no se observó. No es un umbral de publicación,
es aritmética.

Se descartan de la vista pública, y no por ser malas: las filas **sin provincia**
(la tarjeta responde «cuánto cuesta acá») y las **`unconverted`** (moneda sin
tasa resuelta; mostrarlas junto a las de CUP afirmaría una equivalencia que nadie
calculó). Ambas siguen contando en la matriz del operador. Ventana de 30 días y
no los 7 de `/control`: esa vista responde «cómo se movió esta semana» para el
operador y esta «cuánto cuesta» para quien compra — medido, a 7 días entran 5
celdas y a 30 entran 6, con Arroz/La Habana pasando de n=2 a n=17.

`format_price()` redondea a entero por encima de 100 y a dos decimales por
debajo. **No es un umbral de los de §12.6**: no decide qué se publica ni qué es
válido, solo evita mostrar «806,0606060606060606» sin colapsar a cero un precio
chico.

Descartado: (a) seguir sin publicar precio — era una lectura de más de §9.0, que
sí autoriza el rango con respaldo visible; (b) publicar la mediana como titular,
que es literalmente «un valor puntual presentado como medición consolidada»;
(c) excluir de las tarjetas las filas que marca `detect_basis_suspects` — el
detector señala y no corrige, y meterlo en el agregado escondería dato en vez de
arreglarlo.

Consecuencia: 810 tests verdes (+10). **Advertencia operativa**: con las 7 filas
señaladas todavía sin corregir, 4 de las 6 tarjetas publican máximos falsos —
Aceite/La Habana muestra «2.100 – 1.264.260 CUP/l» y Pollo/La Habana tiene
además la mediana corrida a 3.400. La página es correcta; el dato todavía no.
**No conviene exponer `/` hasta que esas filas estén curadas.**

## 2026-08-22 — El mapa muestra estado de publicación, no cobertura

Contexto: §9.0 es vinculante y exige que «la interfaz muestra explícitamente qué
provincias están publicadas y cuáles en ingesta sin publicación, con su progreso
hacia el umbral. La ausencia de datos se comunica como decisión metodológica, no
como falla». No se cumplía. `Province.is_published` existía en el modelo desde el
seed y **no se usaba en ninguna parte** —ni en `web/` ni en los routers—, y el
mapa de `/` mostraba «con observaciones / sin observaciones», que es cobertura.

Cobertura y publicación no son lo mismo, y confundirlas era el bug: una es una
medición, la otra una decisión.

Decisión: `MapProvince` gana `is_published` y su `state` pasa de dos valores a
tres, cruzando los dos ejes — `publicada`, `en-ingesta` (recolectando, sin
exponer la serie) y `sin-datos`. La publicación **manda sobre la presencia de
dato**: una provincia publicada se muestra como tal aunque su conteo esté en
cero, porque publicar es una decisión ya tomada y esconderla sería el opuesto de
lo que la sección pide. Las etiquetas visibles viven en `STATE_LABELS`, en el
módulo y no en la plantilla, para que los tests las afirmen sin parsear HTML.

El conjunto de publicadas sale de la base (`select Province.name where
is_published`), no de una constante en código: §9.0 fija como restricción
vinculante que «la incorporación de una provincia debe ser configuración, no
desarrollo», y un `frozenset` en el módulo obligaría a un deploy para publicar
una provincia.

**El «progreso hacia el umbral» se muestra como el conteo crudo, no como
porcentaje.** Un porcentaje necesita el denominador N, que §9.0 manda calibrar
con datos reales y §12.6 prohíbe inventar: «114 observaciones» es honesto,
«57% del umbral» fabricaría el umbral. Hay un test que falla si aparece
«% del umbral» en la página.

Descartado: (a) dejar el mapa coloreando cobertura y agregar el estado solo como
texto — el color es lo que se lee primero y estaba diciendo la cosa equivocada;
(b) tratar «sin datos» como un tercer valor de `is_published`, contra el
comentario del modelo que ya había decidido dos estados citando §9.0.

Consecuencia: 816 tests verdes (+6). Medido contra producción: 0 publicadas, 2
en ingesta (La Habana 114, Santiago de Cuba 109) y 14 sin datos. El párrafo bajo
el mapa declara que ninguna cumple todavía el requisito de publicación, que es
exactamente la «decisión metodológica» que §9.0 manda comunicar en vez de dejar
implícita.

## 2026-08-23 — Gráfico de evolución: SVG en servidor y sin línea entre los puntos

Contexto: con las 7 filas ya curadas, el gráfico de evolución de §6.7 quedó
desbloqueado. Traía dos decisiones: la que el ADR 001 dejó abierta a propósito
—Chart.js contra SVG generado en servidor, «para medirla cuando el mapa ya
exista»— y una que apareció al medir la densidad real de la serie.

**Medición del 2026-08-23**, últimos 30 días: Arroz/La Habana tiene 17
observaciones graficables en **11 días distintos**; Arroz/Santiago 14 en 7;
Aceite/La Habana 5 en **2**; Azúcar/Santiago 5 en 3; Azúcar/La Habana 2 en 2.
Pollo/La Habana tiene 3 observaciones **todas el mismo día**.

Decisión 1: **SVG generado en servidor, no Chart.js.** Tercer apartamiento de
§13.1, decidido midiendo como se había prometido. Con 17 puntos como máximo y sin
ninguna interacción, los ~60 KB de Chart.js compran zoom, tooltips y animación
que el gráfico no usa, y serían la única dependencia JS de una página que no
tiene ninguna. Chart.js seguía siendo defendible —es vendorizable y cumple el
invariante 3, a diferencia de MapLibre—; lo que lo descarta es que no había nada
que comprar con esos bytes.

Decisión 2, la de fondo: **no hay línea que una los puntos.** Con dato en 11 de
30 días para la serie más densa y en 2 de 30 para Aceite, una línea continua
entre el 14/08 y el 17/08 dibuja tres precios que nadie observó, y en Aceite
uniría dos puntos afirmando un mes de precios inventados. §12.6 prohíbe inventar
umbrales; el mismo criterio vale para inventar observaciones. El gráfico muestra
cada anuncio como un punto en su fecha, con los huecos visibles y contados
(«19 días sin anuncios»), que es exactamente lo que la muestra sostiene. La banda
mín–máx y la mediana sí son horizontales de ancho completo: describen la muestra
del período, no una interpolación temporal.

**Una celda con un solo día no se grafica** (`MIN_DAYS = 2`). No es un umbral de
los que §12.6 prohíbe —no decide si el dato es válido ni si se publica el precio,
que la tarjeta ya muestra— es la condición aritmética para que un eje temporal
signifique algo. Por eso Pollo/La Habana tiene tarjeta y no tiene gráfico.

**El eje X abarca la ventana completa, no el rango de los datos.** Si las
observaciones se apelotonan en unos pocos días, tiene que verse apelotonado
dentro del mes; estirarlas hasta ocupar todo el ancho fingiría una cobertura
pareja que no existe.

Aparte, **refactor sin cambio de comportamiento**: la normalización de filas
minoristas —moneda, `price_basis`, conversión de unidad y los tres filtros— se
extrajo a `compute_retail_observations()`, y `compute_retail_ranges` pasó a ser
un agrupamiento sobre esa salida. El gráfico era su tercer consumidor y copiarla
otra vez garantizaba que las tres copias se separaran.

Descartado: (a) Chart.js, por lo dicho; (b) unir los puntos con una línea, que
es la representación que §6.7 describe («gráfico de líneas simple») pero que con
esta densidad afirma datos inexistentes — se deja anotado como apartamiento
consciente, a revisar cuando la serie sea diaria de verdad; (c) escalar el eje X
al rango de los datos.

Consecuencia: 825 tests verdes (+9). Contra producción se dibujan 5 series de
las 6 tarjetas, y la que falta es Pollo por tener un solo día. La página pasó de
31.442 a 43.892 bytes de HTML, todos comprimidos por el middleware.

## 2026-08-23 — El seguimiento de tareas se muda a GitHub Projects

Contexto: el operador reportó que le costaba hacer seguimiento del estado del
proyecto solo con texto — `backlog.md` había crecido a 370 líneas de anexado en
una semana y ya no daba una foto rápida de qué está hecho, qué falta y por qué
está bloqueado cada ítem.

Decisión: se crea el Project #1 en GitHub
(`https://github.com/users/arielhechavaria/projects/1`), con tres campos propios
además de `Status` (Todo/In Progress/Done, default de GitHub): `Área` (Dato,
Precio y unidades, Visualización, Privacidad y alcance, Extracción y captura,
Infraestructura/deuda) y `Bloqueo` (Ninguno, Decisión de producto, Decisión de
privacidad, Evidencia/volumen insuficiente, Reservado para Fase 2, Umbral sin
calibrar §12.6). Se volcaron las 29 tareas que tenía `backlog.md` — 10 hechas,
19 pendientes—, cada una con su estado real verificado contra el código y la
producción, no copiado a ciegas del texto.

Los items son *draft issues* del Project, no issues del repositorio: viven solo
ahí, sin aparecer en la pestaña Issues. Para un solo desarrollador es la fricción
mínima — crear un issue real por tarea hubiera duplicado trabajo sin sumar nada,
porque nadie más los va a buscar por fuera del tablero.

`backlog.md` queda congelado con una nota que apunta al Project; no recibe
entradas nuevas. `decisions.md` no cambia de rol: sigue siendo el log de por qué
se decidió algo, mirando atrás — el Project es el qué falta, mirando adelante.

Descartado: (a) issues reales del repositorio en vez de draft issues — más
ceremonia sin beneficio para un operador único; (b) mantener los dos archivos
—Project y `backlog.md`— sincronizados a mano, que es exactamente la carga
cognitiva que motivó el cambio.

Consecuencia: toda esta sesión —10 slices desde el ADR 001 hasta el gráfico de
evolución— queda visible de un vistazo en el tablero, agrupable por Área y
filtrable por Bloqueo. La entrada de `test_ninguna_vista_pide_assets_a_la_red`
que pasaba en vacío, las dos filas de Picadillo sin cubrir por el detector y el
posible duplicado de Arroz/Santiago —hallazgos que solo vivían en la
conversación— quedaron con su propia tarjeta en el Project para no perderse.

## 2026-08-23 — Rediseño Material 3 de la home pública, opción 1A

Contexto: `redesing/saimc_desing_root_page/` traía tres opciones de layout
(1A/1B/1C) para portar `public_home.html` al tratamiento Material 3 del resto
de la app, con paleta nueva y tipografía Alice. El README dejaba la opción sin
elegir (`[COMPLETAR: 1a/1b/1c]`).

Decisión: se porta **1A "Sala de control"**. Es la única de las tres que
incluye la sección "Cómo se movió" (series de evolución de precio); 1B y 1C la
omiten, y esa sección es la pieza más reciente del proyecto (commit
`de3fbc1`) — perderla al redisenar habría sido una regresión silenciosa. La
paleta nueva (`#05668d`/`#028090`/`#c1f2e8`/`#9aaaa6`/`#aec092`) entra como
custom properties `--ph-*` scoped dentro del `<style>` propio de
`public_home.html`, no como reemplazo de los tokens `--m3-*` de
`saimc-material.css`: esos los usa toda la herramienta del operador
(catálogo/curaduría/control) y este slice es solo de la home pública. Se
vendoriza Alice (peso 400, único que publica Google Fonts, subset latin,
25.788 B) como cuarta fuente en `static/fonts/`, mismo patrón que Roboto/Roboto
Mono/Material Symbols (entrada del 2026-08-20). El mapa sigue coloreando por
los tres estados discretos que ya da `map_view.py` (`publicada`/`en-ingesta`/
`sin-datos`), solo reestilizados con la paleta nueva — no se replica el
degradé continuo por intensidad del prototipo (`Math.pow` en JS): eso
necesitaría un umbral de intensidad que §12.6 prohíbe inventar, y ya lo cubre
`test_el_mapa_no_colorea_por_precio`.

Descartado: 1B ("Plano técnico") y 1C ("Terminal de datos"), por la razón de
arriba. También reproducir el hover con panel flotante en JS del prototipo
(React): el proyecto no tiene framework de UI y no lo amerita un tooltip —
se mantiene `<title>` dentro de cada `<path>`, accesible y sin JS, más el
panel estático "Concentración de la muestra" (armado en Jinja puro desde
`map.provinces`, sin campos nuevos en el contexto de `public.py`).

Consecuencia: `tests/test_static_assets.py` suma `alice.woff2` a `FUENTES` y
`"/"` a `VISTAS` de `test_ninguna_vista_pide_assets_a_la_red` — la home
pública quedaba sin ese chequeo justo al agregarle una fuente que sale de
Google Fonts en tiempo de build. 828 tests verdes (+3 sobre 825). Se borra
`redesing/saimc_desing_root_page/`: ya cumplió su función de handoff, mismo
criterio que la entrada del 2026-08-20.

**Nota aparte, fuera del alcance de este slice**: `docs/decisions.md` tiene
marcadores de conflicto de merge sin resolver, commiteados en `f479718`
("merge umbral-en-anuncios") — dos bloques, líneas ~3887-3960 y ~4004-4623 en
la versión de archivo previa a esta entrada. No se tocan acá porque no son
parte de este cambio y no se puede adivinar cuál lado del conflicto es el
correcto sin más contexto; queda para una sesión que resuelva ese merge.

---

## 2026-08-23 — `guess_source_reference` no se puede recalibrar contra WhatsApp: no hay dato que extraer

Contexto: la entrada del 2026-08-11 dejó anotado que `guess_source_reference`
prellena 0/29 contra el corpus real y que 18 de esos 29 son capturas de
WhatsApp de vendedor individual, "va al backlog, no se toca acá". El Project
#1 lo trae como tarjeta sin bloqueo ("Recalibrar `guess_source_reference`
contra capturas de WhatsApp"), asumiendo que es una recalibración de patrón
como cualquier otra.

Medido contra `inbox/duplicates/` (351 capturas de WhatsApp no-backfill) antes
de tocar código:

1. **Vendedor individual (la mayoría del corpus, 18/29 en la muestra
   clasificada)**: un chat de WhatsApp con un contacto no guardado no expone
   ningún nombre en el texto — ni en la app ni, por lo tanto, en lo que el
   operador copia o el OCR transcribe. El único identificador que aparece en
   el encabezado es el teléfono ("+53 5 5550101 Hoy, 9:22 a. m.", entrada del
   2026-08-11), que `redact_personal_data()` ya redacta antes de persistir
   (`curation.py:1070-1071`) por mandato de §10.6. No es un hueco de la
   heurística: es que el dato que haría falta para llenar `source_reference`
   no existe en el texto, o existe y está prohibido guardarlo.

2. **Página/canal comercial (la minoría)**: WhatsApp permite negrita
   (`*texto*`) al principio del mensaje, y 22 de las 351 capturas empiezan
   así. Pero se usa para lo que sea que el vendedor quiera resaltar — nombre
   de producto ("*Harina NT Multipropósito*"), estado ("*Picadillo (
   Agotado )*"), frase de venta ("*Continuamos con la venta*") — no
   específicamente para el nombre de quien vende. De las 22, solo 4 (un único
   canal, repetido) siguen el patrón "Nombre :" que sí identificaría al
   remitente. Un regex de "negrita al inicio" prellenaría `source_reference`
   con basura la mayoría de las veces — peor que dejarlo vacío, porque el
   operador tendría que revisar y borrar en vez de solo completar. Y el único
   caso limpio es la convención de un solo vendedor, no un patrón
   generalizable: exactamente el error que `CLAUDE.md` prohíbe en la capa de
   extracción («nunca derives un test del comportamiento de un extractor ya
   escrito», y por extensión, de un solo caso observado).

Decisión: no se toca `guess_source_reference`. La tarjeta se cierra con esta
medición en vez de con código: el hallazgo de 2026-08-11 no era un patrón sin
calibrar todavía, era el techo real de lo que el texto de WhatsApp puede dar
sin violar §10.6 o sin degradar la calidad del prellenado.

Descartado: extraer el teléfono igual y dejar que `redact_personal_data()`
lo tape en `source_reference`. El resultado ("[teléfono removido] Hoy, 9:22
a. m.") no identifica a nadie — no es una degradación seria del prellenado,
es simplemente inútil, y construir el camino para llegar a un valor inútil no
se justifica.

Consecuencia: para capturas manuales (bandeja/pegado) de un vendedor
individual, `source_reference` sigue dependiendo 100% de que el operador lo
tipee — mismo comportamiento que hoy, ahora con el motivo medido y escrito en
vez de asumido. La ruta automática (`WHATSAPP_CHANNEL` vía webhook de WAHA) no
tiene este problema: `curation_queue()` ya usa `ingestion.source_reference`
del canal conocido antes que la adivinanza de texto (entrada del 2026-08-17).
No se corrió la suite: no hay cambio de código.

---

## 2026-08-23 — Arroz/Santiago 17.500×25kg no es duplicado: el volumen creció y lo desmiente

Contexto: el Project #1 traía la tarjeta "Verificar posible duplicado: 3
filas idénticas de Arroz/Santiago" — 3 filas iguales (17.500 CUP, 25 kg)
provenientes de 3 ingestas distintas, con la sospecha de que fuera el mismo
anuncio contado varias veces.

Medido contra producción el 2026-08-23: hoy hay **11 filas**, no 3, con ese
mismo precio y cantidad. `captured_at` distinto en cada una (2026-08-13 a
2026-08-22, 10 días), la mayoría del mismo canal de WhatsApp
(`channel_id=f18f1f67…`). Un par comparte `captured_at` a nivel de segundo
(2026-08-20 12:53:30), lo que sí ameritaba revisar de cerca — pero
`md5(original_text)` de las 10 ingestas con `source_type='whatsapp_channel'`
da 10 hashes distintos: son 10 mensajes de texto diferentes, no el mismo
mensaje recibido dos veces por el webhook. El canal repite el mismo precio en
mensajes separados a lo largo de más de una semana.

Decisión: no se borra ninguna fila. No es un duplicado — es exactamente el
patrón de precio estable y denso en el tiempo que el proyecto busca (mismo
criterio que "Arroz/Habana n=17 en 11 días distintos", `docs/decisions.md`
2026-08-22 sobre el gráfico de evolución). La sospecha original tenía sentido
con 3 filas y poco volumen; con 11 filas y captured_at repartido en 10 días
distintos, la hipótesis de duplicado queda refutada por el propio dato.

Consecuencia: tarjeta cerrada en el Project sin cambio de código ni de dato.
Si en el futuro aparece un par con `captured_at` Y `md5(original_text)`
iguales, ahí sí habría un duplicado real de ingesta — no fue el caso acá.

---

## 2026-08-23 — Índice de disponibilidad (§7.3): conteo de anuncios, no score

Contexto: el Project #1 trae "Índice de disponibilidad (§7.3)" como la
tarjeta "In Progress" recomendada. `spec.md` §7.3 define `availability_index`
con columnas `score`/`trend`, y el ejemplo de §6.7 es "Pollo, Santiago, Score:
32/100, Estado: Escasez" — pero la especificación nunca dice cómo se calcula
ese score, y §12.6 prohíbe explícitamente inventar un umbral o una escala sin
medición previa. Construir el 0-100 tal cual habría sido exactamente eso.

Decisión: `cells.py` gana `compute_availability()`, que cuenta anuncios
minoristas por producto × provincia en la ventana actual (`[hoy-days, hoy)`)
contra la anterior, mismo ancho (`[hoy-2·days, hoy-days)`), reutilizando la
normalización de `compute_retail_observations` (retail, cantidad resuelta,
unidad convertible, compra mínima respetada). `Availability.delta` es la
resta directa de los dos conteos — sin clasificar la magnitud en ninguna
categoría. Va en `/control` (sección 8, nueva — corre la "Matriz de los
pilotos" a 9), no en la home pública: es introspección del operador, mismo
criterio que `basis_suspects`, y no un veredicto para el lector del
dashboard. Filtrado por `products.is_pilot`, igual que `retail_ranges`.

Un producto en cero anuncios esta ventana **no desaparece** de la tabla si
tenía anuncios en la anterior — señal de escasez válida sin clasificarla. Un
producto sin anuncios en ninguna de las dos ventanas sí se omite: fabricar un
"0 contra 0" para todo el catálogo inventaría una observación que nadie hizo.

Descartado: colorear la columna "Diferencia" en verde/rojo según el signo.
Esta misma vista ya declara el criterio contrario para la matriz de pilotos
("el color dice qué provincia, no qué tan bien va... sin umbral de dispersión
fijado, la paleta es de identidad y no incluye rojo ni verde"): un semáforo
sobre más/menos anuncios afirmaría el mismo tipo de juicio que ese texto
rechaza, aunque no dependa de un umbral numérico.

Consecuencia: TDD completo — `TestComputeAvailability` en `test_cells.py` (6
casos, valores calculados a mano) antes de escribir `compute_availability`, y
`TestDisponibilidad` en `test_control.py` (3 casos) para el wiring del
router/plantilla. 837 tests verdes (+9 sobre 828). El servicio local
(`systemctl restart saimc`) no se reinició en esta sesión —sin sudo
interactivo disponible—, así que el router nuevo no está activo en
`http://localhost:8000` todavía; el HTML se sirve actualizado porque Jinja2
recarga plantillas por `mtime`, pero el contexto (`availability`) lo arma
`control.py`, que sigue cargado en memoria con la versión anterior hasta el
próximo restart.

## 2026-08-24 — Indicador pasivo de última captura en `/curation`

Contexto: el operador reportó "no hay elementos en la cola" el 2026-08-24.
Medido contra producción: 173 `pending`, pero 0 mencionaban alguno de los 5
productos piloto — la cola estaba 100% fuera de foco, no vacía. Investigando
por qué, se encontró la causa real por separado: WAHA (el contenedor que
conecta con WhatsApp) sufrió un logout remoto el 2026-08-23 a las 02:11:58
UTC, ~7 segundos después de reautenticar tras un restart del contenedor —
desde entonces ninguna captura nueva entraba, y nada en la pantalla lo decía.

Decisión: `/curation` muestra ahora "Última captura: hace N horas/días",
calculado sobre `MAX(raw_ingestions.captured_at)` en cualquier estado (no
solo `pending`) — `_last_capture_notice` en `curation.py`. Es una medición,
nunca un veredicto de "atrasado": mismo criterio que `age_days` en
`exchange_rate/status.py` para la tasa de cambio (§12.6, sin ventana de
vigencia asignada, no se inventa un umbral que la spec no fija).

Descartado: una notificación activa (Telegram/email) que avise sin que el
operador abra la app. Exige un proceso separado corriendo para chequear y
disparar — la cola de trabajos que la decisión #3 de `CLAUDE.md` frena hasta
validar H0. Queda anotado para revisar después de esa validación si el
indicador pasivo no alcanza.

Consecuencia: `_last_capture_notice(db, *, now=None)` en `curation.py`, mismo
patrón de parámetro inyectable que `compute_rate_status(session, hoy=None)`
para poder testear sin depender del reloj real. 8 tests nuevos en
`TestUltimaCaptura` (`test_curation.py`), 845 tests verdes (+8 sobre 837).
Sin cambio de esquema. El bache de WAHA en sí (la cuenta de WhatsApp
deslogueada) sigue sin resolver — es operativo, requiere escanear un QR
nuevo, no se toca desde acá.

## 2026-08-25 — `saimc-public.css` separa la home pública del CSS del operador, y el hero pasa a claro

Contexto: ítem del Project #1 («Vista pública carga saimc-material.css entero»).
`_public_shell.html` enlazaba la hoja completa del operador (~28 KB en disco,
~6 KB gzipeados) para usar solo los tokens, el reset base, tres fuentes y
`.m3-subtitle`; arrastraba de paso topnav, tablas, botones, chips y snackbar
de catálogo/curaduría/control que la home pública nunca ejecuta. Ya estaba
anotado como deuda en el comentario de la plantilla desde el redesign
(entrada del mapa de cobertura, 2026-08-22).

De paso, el operador pidió que el panel de cobertura (`.ph-hero`) dejara de
ser oscuro y pasara a claro/Material, para que toda la home comparta un
lenguaje visual.

Decisión: nace `src/saimc/web/static/saimc-public.css`, subconjunto explícito
de `saimc-material.css` — mismos tokens `--m3-*` que usa el panel claro,
reset base, `@font-face` de Roboto/Roboto Mono/Alice (Material Symbols queda
afuera: ninguna vista pública usa iconos) y `.m3-subtitle`. `app.py` gana
`_public_css_version()`, paralelo a `_css_version()`, con el mismo
versionado por mtime para que el cache-busting no dependa del archivo
equivocado. `_public_shell.html` pasa a enlazar `saimc-public.css`.

El hero (`public_home.html`) cambia sus variables `--ph-dark-bg`/`--ph-dark-line`
por `--ph-hero-bg`/`--ph-hero-line`, resueltas contra los tokens `--m3-*`
claros, y todo lo que era `rgba(242,242,243,...)` (texto claro sobre fondo
oscuro) pasa a `var(--m3-on-surface-variant)` o equivalente. El mapa
(`is-publicada`/`is-en-ingesta`/`is-sin-datos`) sigue siendo categórico, no
semáforo: se recalibra a tonos que funcionan sobre fondo claro sin tocar la
decisión de 2026-08-22 que prohíbe verde/amarillo/rojo por precio (§12.6).

Descartado: mover el `<style>` del hero (page-specific) a `saimc-public.css`
— esas reglas son de una sola página, viajan gzipeadas con el HTML y no
justifican una petición aparte; el criterio ya estaba escrito en el propio
comentario de la plantilla.

Consecuencia: 851 tests verdes (+6 sobre 845). `tests/test_static_assets.py`
gana `test_la_hoja_publica_se_sirve`, `test_la_home_publica_no_carga_la_hoja_del_operador`,
`test_las_vistas_del_operador_siguen_con_la_hoja_completa` y
`test_el_css_publico_viaja_comprimido`. Medido: `/` sirve `saimc-public.css`
y no referencia `saimc-material.css` en el `<link>`; `/catalog`, `/curation`
y `/control` siguen con la hoja completa.

## 2026-08-25 — `/catalog` gana edición de unidad de producto; los otros 3 ítems del Todo no eran código

Contexto: de los 4 ítems del Project #1 sin bloqueo (`Bloqueo: Ninguno`), el
operador pidió encararlos los cuatro en secuencia. Antes de tocar código se
verificó cada uno contra la base de producción (este host es el propio
servidor, `saimc.service` sobre `localhost:5432`) y contra el código:

- **`sale_format` vacío (65 filas hoy, no 66 — el número del ítem es una
  foto vieja) y las 2 filas de Picadillo con problemas**: no son código.
  `sale_format` ya es un campo editable en la cola de curaduría (prueba de
  la jabita); rellenarlo bien exige leer la evidencia de cada anuncio y es
  exactamente la curaduría manual que la decisión 2 de `CLAUDE.md` reserva
  al operador. Completarlas por SQL/lectura automática de evidencia sería
  automatizar curaduría sobre la base que ya alimenta el dashboard público
  — lo que esa decisión descarta explícitamente. Quedan para que el
  operador las cure con la evidencia real.
- **`provinces.aliases` como columna**: contradice una decisión ya escrita
  en el código. `extraction/engine.py` (comentario junto a
  `_PROVINCE_ALIASES`) explica que el diccionario vive en la capa de
  extracción y no en `Province` porque es jerga de texto libre, no parte
  del esquema (§9.0). El propio ítem se declara «no urgente, solo si el
  diccionario se vuelve difícil de mantener» — son 38 líneas ordenadas, no
  lo es. No se toca.

**Solo «unidad de catálogo mal declarada en Picadillo» era accionable**, y
tampoco tal como estaba escrito: el ítem decía «se corrige desde /catalog»
pero esa edición no existía — `unit` solo se fijaba al dar de alta un
producto, la tabla de catálogo lo mostraba de solo lectura.

Decisión: `POST /catalog/products/{id}/unit`, mismo patrón que
`set_channel_address` — auto-envío al perder el foco, sin botón aparte.
Diferencia con `address`: `Product.unit` no admite NULL en el modelo, así
que un valor en blanco cae al mismo default que usa `create_product`
(`"unidad"`), no a nulo.

Descartado: un `<details>` de edición multi-campo como `edit_channel` —
el único campo roto es `unit`; agregar `category` a la misma pasada sería
una feature que nadie pidió.

Consecuencia: 853 tests verdes (+2 sobre 851). `_crear_producto` (fixture de
`test_catalog.py`) gana el parámetro `unit`, default `"lb"` para no tocar los
tests existentes. **Pendiente**: la corrección real del valor de Picadillo en
producción (hoy 8 filas en lb con `Product.unit="unidad"`) todavía no se
aplicó — `saimc.service` corre sin `--reload`, así que el endpoint nuevo no
está activo hasta reiniciar el servicio con el código nuevo desplegado.

## 2026-08-25 — Transparency Layer pública, solo para evidencia de texto

Contexto: ítem del Project #1, bloqueado por «Decisión de privacidad».
`docs/spec-map.md` ya tenía registrada la contradicción: la línea 321 de
`spec.md` pide publicar «literalmente el archivo capturado», pero §10.6
mitigación 2 exige mostrar «el anuncio, no la identidad del anunciante», y
una captura de WhatsApp/Telegram trae nombre del remitente y a veces
teléfono en los propios píxeles. Con texto pegado las tres mitigaciones de
§10.6 se sostienen; con imagen no.

Medido contra producción antes de decidir: de los 238 registros curados, 143
son de texto (`inbox_text` + `whatsapp_channel` texto) y 95 de imagen. Sobre
las 143 de texto, **0 contienen un patrón de 8 dígitos consecutivos**
(formato de teléfono cubano) — se contó el patrón, no se leyó ni se mostró
el contenido para esta medición.

Decisión: `GET /anuncio/{ingestion_id}` en `routers/public.py`, sin auth
(capa pública). Sirve `RawIngestion.original_text` solo cuando
`content_type == TEXT` y `processing_status == CURATED`; cualquier otro caso
—imagen, pendiente, descartado, id inexistente— da el mismo 404, para no
filtrar por el código de estado si una evidencia de imagen existe o no.
Nunca lee `channel_id` ni `source_reference`: el identificador de origen no
se expone en la capa pública (`CLAUDE.md`).

Descartado: publicar también imagen con alguna forma de redacción — exige
OCR/ML para localizar y tapar nombre/teléfono en los píxeles, fuera de
alcance en Fase 0 (CLAUDE.md, decisión 2). Queda documentada como la razón
por la que el 40% de la evidencia (95 filas) sigue sin ruta pública.

**Fuera de alcance de este slice, explícito**: la home pública todavía no
enlaza a `/anuncio/{id}` desde ningún lado —ni las tarjetas de precio (un
rango agrega n≥1 anuncios, no hay «el» anuncio) ni los puntos del gráfico de
evolución (cada punto sí es exactamente un anuncio, pero `RetailObservation`
—`cells.py`— no lleva el id de origen todavía; agregarlo toca la
normalización que comparten tarjetas, gráfico y matriz de pilotos). El
endpoint es real y navegable por URL directa; cablearlo a la UI es un
próximo slice.

Consecuencia: 859 tests verdes (+6 sobre 853). `tests/test_transparency_layer.py`
nuevo: texto curado se muestra, imagen/pendiente/descartado/id inexistente
dan 404, y el nombre del canal de origen no aparece en el HTML aunque la
ingesta lo tenga cargado.

## 2026-08-25 — El campo `price_basis` se oculta cuando la cantidad es 1

Contexto: el operador venía notando que el campo "¿el precio es por unidad,
o es el total?" se muestra en cada fila, aunque su propio texto de ayuda
diga «obligatorio solo si la cantidad no es 1». Con `quantity == 1`, `unit` y
`total` dan el mismo número (`cells.py`: dividir por 1 es la identidad), así
que preguntar ahí es ruido — y es exactamente el caso más común de la cola.

Decisión: JS cosmético en `_price_row_scripts.html`, mismo criterio D8 que
ya regía el relabel de "por unidad" — el HTML server-rendered es el
mecanismo que la especificación afirma; el script solo mejora la
experiencia. Sin JS el campo queda visible siempre, que es el comportamiento
de hoy: nunca se oculta del lado del servidor. `price_basis_wrap_{{ index0 }}`
en `_price_row_card.html` es el gancho que el script togglea al escribir en
`quantity_{{ index0 }}` (evento `input`, no `change`: es un `<input
type="text">`, no un `<select>`).

Descartado: ocultarlo server-side según `row.quantity` — el operador tipea
la cantidad después de que la fila ya se renderizó (viene de la propuesta
del extractor o en blanco), así que solo JS puede reaccionar en vivo.

Consecuencia: 860 tests verdes (+1 sobre 859). El toggle en sí no tiene
cobertura de Python —igual que el relabel existente—; el test nuevo
(`test_el_campo_price_basis_trae_el_gancho_para_ocultarse`) solo afirma que
el servidor renderiza el `id` del que el script depende.

## 2026-08-25 — La Transparency Layer se cablea a la home pública

Contexto: `GET /anuncio/{id}` (entrada anterior de esta misma fecha) quedó
construido, probado y desplegado, pero sin ningún link real en la home
pública — quedó anotado como "próximo slice" y el ítem del Project #1 se
dejó en In Progress en vez de Done. El operador preguntó por qué no
cerrarlo en la misma sesión si ya estaba todo lo necesario, y tenía razón:
`RetailObservation` (el único punto que faltaba) es consumido solo por
`series_view.build_series` —confirmado con grep antes de tocar nada—, así
que el cambio no se ramifica hacia `card_view`/`matrix` como se temió antes.

Decisión: `RetailObservation` (`cells.py`) gana `evidence_id` (`source_id`)
y `evidence_is_public` (`content_type == TEXT`), resueltos una sola vez en
`compute_retail_observations` con un join a `raw_ingestions` — el mismo
criterio de "solo texto" que ya regía el endpoint, ahora expuesto en el
grano en vez de repetido en cada vista. `SeriesPoint` (`series_view.py`)
hereda los dos campos sin lógica propia. En `public_home.html`, un punto de
la serie con `evidence_is_public` se envuelve en `<a href="/anuncio/{{
evidence_id }}">`; el resto queda como `<circle>` suelto, igual que hoy.

El join a `raw_ingestions` es `INNER`, no `LEFT`: `prices_history.source_id`
es `NOT NULL` con FK a la tabla, así que no hay fila que perder — verificado
contra el esquema antes de escribirlo, y confirmado después porque ningún
test existente cambió de resultado.

Descartado: envolver también el punto de imagen en un link a un endpoint
que sabe que va a dar 404 — sería peor UX que no envolver nada, y el
`title` del punto (visible al pasar el mouse) ya distingue los dos casos
por su propio texto.

Consecuencia: 865 tests verdes (+5 sobre 860). Medido contra producción: de
las observaciones minoristas de productos piloto, 94 tienen evidencia de
texto (enlazan) contra 57 de imagen (no enlazan) — la mayoría del gráfico
va a mostrar el link. `tests/test_series_view.py` gana un caso que verifica
que el id y la visibilidad viajan del grano al punto;
`tests/test_cells.py` gana `TestComputeRetailObservationsEvidencia` (texto
público, imagen no); `tests/test_public_map.py` gana un caso end-to-end por
cada sentido, seedeando una ingesta de texto y una de imagen contra `GET /`
real.

## 2026-08-25 — El extractor propone `price_basis` cuando el anuncio lo declara

Contexto: el operador propuso inferir `price_basis` de la cantidad («si es
mayor que 1, es por lote»). Medido contra producción, esa regla es **falsa**, y
el contraejemplo es textual: la ingesta `e1d99e90` dice «Picadillo de pollo
americano 40lb a 525» —cantidad 40, y 525 es el precio **por libra**— mientras
que «Cajas de Picadillo 50Lb $25,750» tiene cantidad 50 y el precio **sí** es
del lote. Mismo producto, misma unidad, cantidades parecidas, lecturas
opuestas. La cantidad no lo decide.

Pero la pregunta destapó un bug en vivo: **7 filas mal marcadas**, y la vista
pública publicaba «Picadillo 10,30–580 CUP/lb». Corregidas en producción, en
direcciones opuestas —5 de Picadillo de `total` a `unit`, 2 de Pollo de `unit`
a `total`—, que es la mejor demostración de por qué la cantidad no alcanza.
`detect_basis_suspects` pasó de 3 a 0, y el falso positivo que señalaba a la
fila 525×40 (que estaba bien) desapareció solo: era la celda envenenada por
las otras 5, no la fila.

Lo que sí se puede leer es lo que el anuncio **declara**. Medido: 27 de 150
anuncios de texto curados traen una pista explícita, y 18 usan la forma
«Sale $X Lb».

Decisión: `_declared_price_basis` (`extraction/engine.py`) lee «Sale X
&lt;unidad&gt;» y contrasta el número declarado contra las dos lecturas posibles
del precio de la fila: si `precio / cantidad` coincide, el precio cubre el lote
(`total`); si coincide `precio` a secas, ya viene por unidad (`unit`). Si no
coincide ninguna, devuelve `None` **a propósito** — el anuncio dice algo que la
regla no entiende y proponer ahí sería adivinar. Tolerancia relativa del 1%,
que no es un umbral de los que §12.6 prohíbe: no decide validez ni publicación,
solo absorbe el redondeo del anunciante («281.82» donde la división da
281,8181…).

**Apartamiento declarado de D5/D4** (`openspec/changes/archive/
2026-08-21-price-basis/design.md`), que fijaba `"price_basis": ""` *always* en
`_values_from_proposal`, por ser juicio del operador. El motivo de apartarse:
esa decisión asumía derivarlo de texto suelto, y acá no se deriva — el vendedor
lo escribe y la aritmética lo verifica. Además, las 7 filas de hoy prueban que
«es juicio del operador» no alcanza como salvaguarda. La propuesta sigue siendo
propuesta: llega preseleccionada y el operador confirma o corrige (§6.1).

Descartado: (a) convertir la unidad cuando la pista viene en otra que la fila
(«25Kg … Sale $318.20 Lb») — mezclaría la conversión de unidades con la lectura
del basis y un error de cualquiera enmascararía al otro; son 4 casos que quedan
sin propuesta, que es el costo aceptado; (b) leer también «X la libra» (9
anuncios) — es otra forma, sin número verificable aparte, y va en su propio
slice; (c) incorporar spaCy, evaluado a pedido del operador: hace análisis
sintáctico, no decide si un número es total o unitario, y el razonamiento real
—dividir y contrastar contra el rango— ya existe en `detect_basis_suspects`.

Consecuencia: 872 tests verdes (+7 sobre 865), con los casos escritos antes del
código (`TestPriceBasisDeclaradoEnElAnuncio`). Verificado sobre las 24 líneas
reales de producción que traen la pista: 15 reciben propuesta (9 `total` + 6
`unit`), **todas aritméticamente correctas**, y 9 se abstienen. Cero propuestas
equivocadas. Las 6 de `unit` tienen exactamente la forma de las 5 filas que
estaban rotas hoy.

Queda anotado un hallazgo aparte: en esos anuncios el extractor toma como
`price` el número de la cláusula «Sale X» en vez del precio de la oferta
(515 en vez de 25.750). El precio por unidad final sale bien igual, pero la
fila guarda un precio que no es el del anuncio. Es un slice propio.

## 2026-08-25 — Descarte por canal en /control

Contexto: producción mide 61% de tasa de descarte global (objetivo §12.5:
<30%). La propia vista ya recomendaba «mirar de qué canales viene lo que se
descarta antes de tocar nada», pero ese desglose no existía —
`compute_intake_health` solo agregaba en global. El pedido original derivó
en un MCP server para escanear el repo (`project-map-mcp`); se descartó
porque un escáner de archivos no puede saber qué debería existir (`missing`
exige un manifiesto a mano, que ya es el GitHub Project #1) y no ataca el
cuello de botella real: la suite corre en 26s con 872 tests, el desarrollo no
es lo lento.

Decisión: `compute_channel_intake` (progress.py) agrega curados/descartados/
pendientes por `channel_id`, agrupando `NULL` como "Captura manual". Se
ordena por descartes absolutos, no por tasa — 5 descartes sobre 10 cuestan más
hora de curaduría que 2 sobre 2 aunque la tasa de éstos sea peor. Reusa el
único umbral con veredicto del módulo (§12.5, <30%); no inventa uno propio
por canal. Tabla nueva en `/control`, sección "Descarte por canal", visible
solo si hay al menos una ingesta.

Descartado: `project-map-mcp` (Node + Astro + diagrams-mcp en el VPS) — sin
acceso SSH al VPS desde esta sesión, sin `diagrams-mcp` instalado, y
contradice la decisión 1 de `CLAUDE.md` (Python únicamente hasta Fase 2).

Consecuencia: el operador puede decidir con datos si vale la pena seguir
suscrito a un canal ruidoso, o si la tasa alta describe qué se está
capturando (texto sin precio) y no un problema de curaduría. No hay
automatismo que dé de baja canales solo — sigue siendo decisión del operador.

## 2026-08-26 — Filtro por producto en la vista pública

Contexto: `/` mostraba las 5 tarjetas piloto y sus series sin forma de
enfocarse en un producto. El operador pidió que la página, aun austera,
dejara «trabajar sobre el producto y sus precios» — leído como consultar y
acotar, no editar (editar es `/curation` y `/catalog`, y §5 de `CLAUDE.md`
prohíbe auth en Fase 0).

Decisión: `public_home` acepta `?producto=<slug>` y acota tarjetas y series a
ese producto. `src/saimc/web/product_nav.py` arma la barra («Todos» + un ítem
por piloto) y resuelve el slug; `product_slug` quita tildes con `unicodedata`
(«Azúcar blanca» → «azucar-blanca»). Un slug que no matchea no da 404: la
vista pública muestra todo. Es refinación de «tarjetas de producto» del
alcance §9, no expone nada nuevo.

Descartado: htmx para el intercambio parcial. `_public_shell` no lo carga a
propósito; recargar esta página gzipeada no justifica sumar una dependencia
JS a una audiencia con conexión lenta. Enlaces con query string, render
entero de servidor. Si las recargas molestan, htmx es un añadido chico.

Consecuencia: 897 tests verdes (+14 sobre 883), casos escritos antes del
código (`tests/test_public_filter.py`). Queda el patrón para los dos slices
siguientes: selector de ventana de días (`?dias=`) y tabla comparativa por
provincia para el producto enfocado, ambos como query string sobre la misma
ruta.

## 2026-08-26 — Selector de ventana de días en la vista pública

Contexto: segundo slice del pedido de «trabajar sobre el producto y sus
precios». La ventana de tarjetas y series era fija en 30 días.

Decisión: `?dias=` acepta 7, 30 o 90 (`product_nav.WINDOW_CHOICES`); cualquier
otro valor cae a 30. Aplica a tarjetas y series juntas —el gráfico tiene que
describir la misma muestra que la tarjeta resume—. Segunda barra de chips bajo
la de producto; cada enlace preserva el otro eje. `dias=30` se omite de la URL
por ser el default, para que el enlace compartible no lleve un parámetro
redundante. El default y su motivo (medición 2026-08-22) se mudaron de
`public._CARD_WINDOW_DAYS` a `product_nav.DEFAULT_WINDOW_DAYS`; la constante
vieja quedó muerta y se borró.

Descartado: ventana libre por número. Tres cortes nombrados —semana, mes,
trimestre— cubren la pregunta sin pedirle al visitante que elija un N, y
mantienen la URL enumerable.

Consecuencia: 905 tests verdes (+8 sobre 897), casos antes del código
(`TestResolveDays`, `TestBuildWindowNav`, `TestVentanaEnLaRuta`). Falta el
tercer slice: tabla comparativa por provincia para el producto enfocado.

## 2026-08-26 — Tabla comparativa por provincia en la vista pública

Contexto: tercer y último slice del pedido. Con un producto enfocado
(`?producto=`), la home repetía N tarjetas casi idénticas, una por provincia.

Decisión: con producto enfocado, `#precios` muestra una tabla (Provincia ·
Mínimo · Mediana · Máximo · Anuncios) en vez del grid de tarjetas. Mismos
datos y mismo orden que las tarjetas (`build_cards`, por cantidad de anuncios
descendente); sin cómputo nuevo. Con n=1 el mínimo y el máximo van como «—»,
igual criterio que la tarjeta que en ese caso muestra solo la mediana: hay un
dato, no un rango. Sin producto enfocado se mantiene el grid de tarjetas. El
rótulo §9.0 «muestra en formación» y el n por fila siguen visibles.

Descartado: mostrar tarjetas y tabla a la vez. Duplicar el mismo dato en dos
formas en la misma sección no agrega nada y alarga la página.

Consecuencia: 909 tests verdes (+4 netos sobre 905), casos antes del código
(`TestTablaPorProvincia`). Cierra el pedido de «trabajar sobre el producto y
sus precios» en la vista pública: filtro por producto, ventana de días y
comparación por provincia, todo por query string y render de servidor.

## 2026-08-26 — Filtro por provincia en la vista pública

Contexto: el filtro por producto (a992d4f) dejó la vista pública asimétrica —
se podía enfocar un producto pero no una provincia—. El board del Project #1
no tenía ninguna tarea de vista pública; las de visualización estaban todas
bloqueadas (umbral §12.6, privacidad, Fase 2). Se anotaron 4 ítems de vista
pública en el board antes de empezar.

Decisión: `?provincia=<slug>` acota tarjetas y series a esa provincia, simétrico
al de producto. `product_slug` ya servía para nombres con espacios
(«Santiago de Cuba» → «santiago-de-cuba»); `resolve_province` reusa la lógica
de `resolve_product` vía `_resolve`. `?provincia=` se resuelve contra las 16
provincias (conjunto estable): una elegida sin dato en la ventana igual
resuelve y muestra su aviso. La barra lista las provincias con dato piloto en
la ventana, más la elegida si quedó afuera. Los tres ejes (producto, provincia,
días) se combinan y cada enlace preserva los otros dos (`_query` con orden fijo
producto/provincia/días). `ProductChoice` → `NavChoice`, compartido por las
barras de producto y provincia.

Descartado: listar las 16 provincias siempre en la barra. La mayoría no tiene
dato piloto y el chip llevaría a una página vacía; se listan solo las que
tienen algo que mostrar.

Consecuencia: 918 tests verdes (+9 sobre 909), casos antes del código
(`TestResolveProvince`, `TestBuildProvinceNav`, `TestFiltroPorProvinciaEnLaRuta`).
Cierra la simetría producto/provincia de la vista pública.

## 2026-08-26 — Descarga CSV de la evidencia (GET /datos.csv)

Contexto: 5º slice de interactividad de la vista pública. La procedencia del
dato es el diferencial del proyecto (§6.5) pero solo se veía anuncio por
anuncio, sin forma de bajar el conjunto.

Decisión: `GET /datos.csv?producto=&provincia=&dias=` devuelve las
observaciones minoristas de la vista (piloto, convertidas a CUP, dentro de la
ventana y los filtros) como CSV adjunto. Columnas: producto, provincia, dia,
precio_por_unidad, moneda, unidad, anuncio (id de raw_ingestions, que ya viaja
en el HTML). **Ninguna columna personal**: sin channel_id, source_reference ni
identificador de origen (CLAUDE.md). `_visible_observations` es ahora la única
definición de "qué filas muestra la vista", compartida por página y CSV para
que no diverjan. La página enlaza al CSV con los filtros activos (`csv_href`,
compuesto por `download_href` en product_nav, misma regla que las barras).

Apartamiento anotado de §9: la sección acota el alcance de visualización a
"mapa de calor simple y tarjetas de producto", y un endpoint de descarga no es
eso. Es una expansión, no una contradicción —§9 lista lo que entra, no prohíbe
lo adyacente— y no expone nada que la página no muestre ya. Registrado acá y en
el docstring de `public_data_csv`.

Descartado: incluir `evidence_id` solo cuando la evidencia es pública (texto).
El id de imagen tampoco es dato personal y `/anuncio/{id}` ya da 404 para
imagen; condicionar la columna solo agregaba una rama sin cerrar ninguna fuga.

Consecuencia: 926 tests verdes (+8 sobre 918), casos antes del código
(`TestDescargaCsv`). Quedan 2 ítems de vista pública en el board: "última
actualización" visible y URLs limpias /producto/<slug>.

## 2026-08-26 — «Datos verificados hasta el …» en la vista pública

Contexto: 6º slice de la vista pública. La home no decía hasta cuándo llega el
dato; un visitante no podía saber si lo que ve es de esta semana o de hace un
mes.

Decisión: línea en el hero con el día del anuncio piloto más reciente
(`_latest_price_day`, sobre `compute_retail_observations` sin ventana). Va sin
ventana a propósito: no depende del filtro `?dias=`, porque si el dato más
nuevo es viejo hay que verlo aunque la ventana elegida lo deje afuera.
Antigüedad, no veredicto (§12.6): informa, no marca «atrasado».

Descartado: reusar `_last_capture_notice` de `/curation`. Esa mide la ingesta
más reciente en cualquier estado (incluye pendientes y descartados) y es señal
para el operador. El visitante necesita la frescura del dato **publicado**: el
precio piloto curado más nuevo, que es otra cosa.

Consecuencia: 929 tests verdes (+3 sobre 926), casos antes del código
(`TestFrescuraDelDato`). Queda 1 ítem de vista pública en el board: URLs
limpias `/producto/<slug>`.

## 2026-08-26 — URLs limpias /producto/<slug> en la vista pública

Contexto: último de los ítems de vista pública del board. `/?producto=arroz`
funcionaba pero no es una URL para compartir ni para leer.

Decisión: `GET /producto/<slug>` renderiza la misma vista que `/?producto=`,
que queda como alias válido (sin redirect: las dos formas viven). `?provincia=`
y `?dias=` se aceptan también en la ruta limpia. Un slug desconocido en
`/producto/<slug>` da **404** —en una ruta con nombre de recurso, «no existe»
es correcto—, a diferencia de `?producto=` raro que sigue mostrando todo. El
cuerpo de `public_home` se extrajo a `_render_home`, compartido por las dos
rutas. Los chips de producto ahora apuntan a `/producto/<slug>` cuando no hay
otro filtro activo (`_product_link`), y vuelven al query string en cuanto hay
provincia o ventana no-default. El `<title>` y el encabezado reflejan el
producto enfocado: al compartir el enlace, la vista previa muestra el producto,
no un genérico.

Descartado: redirect 301 de `?producto=` a `/producto/`. Rompería los enlaces
ya compartidos con query string y obligaría a componer siempre la forma limpia
en las barras, que no pueden expresar provincia ni ventana.

Consecuencia: 937 tests verdes (+8 sobre 929), casos antes del código
(`TestRutaProductoLimpia`, casos nuevos en `TestBuildProductNav`). Cierra los 5
ítems de vista pública del board.

## 2026-08-30 — Inventario de requisitos en 16 módulos de pipeline

Contexto: con H0 medido en 43,5% quedó claro que la ingesta sigue necesitando un
operador humano, y que el orden de construcción de los módulos que faltan importa
más que su lista. No había ningún lugar donde leer qué requisitos existen, cuáles
están construidos y cuál bloquea a cuál: eso vivía repartido entre `spec.md` (1649
líneas), `decisions.md` (5292) y el board.

Decisión: `docs/requisitos.md`, inventario de todo requisito funcional agrupado en
**16 módulos de pipeline** en 5 grupos, con su cita de sección, su fase, su fuerza
normativa y el estado real contra el código. Incluye los 42 huecos que la
especificación deja abiertos y 9 requisitos propuestos (`N-01`…`N-09`) que no salen
de ningún documento. Es índice de consulta, no fuente: si difiere de `spec.md` o
`decisions.md`, ganan ellos.

Lo que el agrupamiento hace visible y las áreas del board no: la **cadena de
bloqueo**. Contribución comunitaria depende de Reputación y de C3, y §10.7 con §9
coinciden en que C3 debe estar operativo *antes* de habilitar el botón «subir dato».
Inteligencia predictiva depende del volumen, que depende de H0. Ese es el orden de
integración, y no es una preferencia: sale de la especificación.

Descartado: agrupar por las áreas del board (Dato, Extracción y captura, Precio y
unidades, Privacidad y alcance, Visualización, Infraestructura). Sirven para
repartir trabajo, no para ver dependencias entre módulos. Los dos cortes conviven;
el board sigue siendo el seguimiento activo de tareas.

Consecuencia: queda registrado que de 16 módulos hay 4 ausentes (Validación,
Reputación, Predictivo, Contribución comunitaria) y que de las doce publicaciones
que §6.4.1 exige como vinculantes no existe ninguna — es el requisito vinculante
más grande sin arrancar. Se anotan dos deudas de documentación: los marcadores de
conflicto de merge commiteados en este mismo archivo (revisados: no hay
contradicción de contenido, solo de posición) y los tres apartamientos posteriores
que faltan en la tabla de `spec-map.md`.

## 2026-08-30 — Gestor de documentos en /docs, privado y con catálogo cerrado

Contexto: el repositorio es privado, así que los documentos del proyecto solo se
leían clonando. El operador pidió una página dedicada para leerlos online, «con
su js con markdown, pdf, todo lo que se necesite», como gestor de documentos de
Guani.

Decisión: `GET /docs` (índice), `/docs/<slug>` (lectura) y `/docs/<slug>/raw`
(archivo crudo), **todo detrás de `require_operator`**, la misma HTTP Basic de la
curaduría. Es privado porque el catálogo incluye `pitch-v3.5.md` —el documento de
inversión— y `decisions.md`, que ya filtró teléfonos reales una vez (2026-08-11).

Tres decisiones de diseño dentro del slice:

1. **Catálogo cerrado escrito a mano** (`web/docs_catalog.py`). El slug de la URL
   nunca se concatena a una ruta: es clave de diccionario, y si no está, 404. No
   hay `open()` que reciba texto del pedido, así que el traversal no tiene por
   dónde entrar. Sumar un documento es agregar una línea, a propósito: qué se
   puede leer desde la web es una decisión, no una consecuencia de dónde quedó un
   archivo. `.env` vive en la misma raíz y hay un test que lo fija.
2. **El markdown se renderiza en el navegador**, con `marked` 18.0.11 (MIT)
   vendorizado en `/static/marked.umd.js`, sha512 del tarball de npm verificado
   antes de extraerlo y sha256 fijado en los tests, igual que htmx. Evita sumar
   una dependencia de Python: `pip install markdown` se propuso y el operador lo
   descartó.
3. **El PDF sale por `window.print()`** contra una hoja `@media print`. El
   navegador ya sabe paginar y exportar; una librería de 350 KB haría lo mismo
   peor.

Descartado: renderizar del lado del servidor con un parser mínimo propio (~150
líneas) — se evaluó como alternativa sin dependencia y el operador eligió el
camino con JS. Descartado también un renderer JS por CDN: conexión lenta e
intermitente, todo se vendoriza.

Costo declarado: la vista **no degrada sin JavaScript** como pide la regla del
2026-08-22; queda un `<noscript>` con enlace al `.md` crudo, que es el mismo
contenido sin formato. Es el precio de no meter el renderer en el servidor.

Consecuencia: 966 tests verdes (+31). Quedan en rojo los 2 de
`test_public_filter.py` que envejecieron por fechas fijas en su seed, ajenos a
este slice.

## 2026-08-30 — Se apaga Swagger: publicaba el mapa de endpoints sin auth

Contexto: al montar el gestor en `/docs` la ruta chocó con el Swagger UI que
FastAPI monta solo. Al mirarlo se vio el problema real: el servicio escucha en
0.0.0.0:8000 sin proxy delante, así que `/docs` y `/openapi.json` se servían **sin
autenticación** y publicaban el mapa completo de endpoints a cualquiera que
preguntara — `/curation/*`, `/catalog/*` y `/webhooks/whatsapp` incluidos.
Verificado con curl contra producción antes de tocar nada.

Decisión: `docs_url=None`, `redoc_url=None` y `openapi_url=None` en la
construcción de la app. Nada del proyecto los consume: un solo operador, sin
clientes de API, y Angular está diferido a la Fase 2 (§13.1). Tres tests fijan
que las tres rutas den 404.

Descartado: dejar Swagger y mover el gestor a otra ruta. Habría conservado la
fuga por una preferencia de nombre. Descartado también ponerlo detrás de
`require_operator` en vez de apagarlo: agrega superficie que nadie usa.

Consecuencia: la firma HMAC sigue siendo la defensa real del webhook —esto no la
reemplaza—, pero deja de haber un índice público que lo señale. Si alguna vez
hace falta la consola de API, vuelve detrás de auth y en otra ruta.

## 2026-08-30 — La fuente de iconos se quedó corta; ahora la regenera una herramienta

Contexto: al agregar el icono `menu_book` al menú (item «Documentos») el
navegador dibujaba el texto crudo `menu_book` en vez del glifo. Al medir la
fuente vendorizada apareció que el problema era más viejo: `material-symbols-rounded.woff2`
se subseteó en `99c72eb` con los 28 iconos de entonces y nadie la regeneró
después. Ya estaban rotos `flag` y `history` (`curation.html`) y `rule`
(`control.html`); `menu_book` sumó el cuarto.

Decisión: se regenera el subconjunto con los 32 iconos que hoy usan las
plantillas (10.484 B contra los 5.756 anteriores) y se agrega
`tools/build_icon_font.py`, que lee los nombres de icono de las plantillas, le
pide a Google el subconjunto vía `icon_names` —el subsetting lo sigue haciendo
el servidor de Google, sin `fontTools` en el venv— y escribe el `.woff2` más un
manifiesto `.icons.txt` al lado. `tests/test_static_assets.py` compara ese
manifiesto contra los iconos de las plantillas: sumar un icono sin correr la
herramienta ahora pone la suite en rojo con el nombre del icono, en vez de
romperse en silencio en pantalla.

Descartado: subsetear a mano con `fontTools` (da un archivo la mitad de grande,
4.912 B, pero mete una dependencia de build que el proyecto evita desde el
2026-08-20). Descartado también fijar el sha256 del `.woff2` como con htmx: el
archivo depende de qué devuelve Google y cambia sin que cambie nada del
proyecto; el manifiesto de iconos es la unidad estable que sí tiene sentido
versionar.

Consecuencia: 968 tests verdes. Quedan los 2 de `test_public_filter.py` que
envejecieron por fechas fijas en su seed, ajenos a esto. El comentario del
`@font-face` en `saimc-material.css` deja de decir «35 ligaduras / 5,7 KB» y
apunta a la herramienta.

## 2026-08-31 — Descarte en lote de pendientes sin foco

Contexto: la tasa de descarte en producción es 67,8% contra un objetivo de 30%
y el 84,5% de ese descarte es ruido de WhatsApp posterior a fijar los productos
piloto. El operador lo tira de a uno desde la cola, gastando en triaje la hora
diaria que §6.1 reserva para curar.

Decisión: `GET /curation/sin-foco` lista todos los pendientes donde ningún
renglón del texto menciona un producto del foco (`_FOCUS_NONE`) y
`POST /curation/sin-foco/discard` los marca `discarded` en bloque. Acotado a esa
única señal: las otras dos de `probable_discard_reasons` (agotado, mayorista)
corren sobre el texto entero mientras el precio se extrae línea por línea, así
que un anuncio puede decir «agotado» en un renglón y tener un precio válido en
otro — agruparlas arriesgaría tirar una fila real y siguen descartándose de a
una. La lista se muestra completa antes de confirmar (§6.1: el operador siempre
confirma) y los ids viajan como campos ocultos, no se recalcula la lista en el
POST; cada id se revalida igual contra `PENDING` + `_FOCUS_NONE` por si el
catálogo piloto cambió entre el GET y el submit. `_FOCUS_UNKNOWN` (imagen sin
OCR) queda afuera: su relevancia es desconocida, no negativa.

Descartado: un botón «descartar todo» que recalcula en el servidor —rompe la
regla de §6.1 de mostrar qué se va a tocar— y auto-descartar por regla sin
intervención, que §6.1 prohíbe explícitamente.

Consecuencia: 976 tests verdes (+10). Siguen en rojo los 2 de
`test_public_filter.py` por fechas fijas en su seed, ajenos a este slice. El
enlace «Descartar en lote →» aparece en la cabecera de `/curation` solo cuando
el contador de pendientes fuera de foco es mayor que cero.

## 2026-08-31 — H0 longitudinal: cada corrida se registra en un archivo, no en una tabla (N-05)

Contexto: `measure.py` daba un número puntual y no quedaba registro por
versión, así que no se podía afirmar que un cambio del extractor ayudó
(`docs/requisitos.md`, N-05). Es la instrumentación previa a tocar el OCR
—el techo real de H0—: sin ella cualquier cambio se afina a ciegas.

Decisión: cada corrida real de `python -m saimc.extraction.measure` appendea
una línea JSON a `docs/h0-runs.jsonl` con los 15 campos del `H0Report`
aplanados más el contexto que los hace comparables: `timestamp`, `git_commit`
(corto), `git_dirty`, `git_branch`, `tesseract_version` e `is_official`
(mismo umbral de 300 anuncios que el aviso de la CLI). Se registra por
defecto; `--no-record` lo salta. `compute_h0_report()` queda intacto y puro
—las funciones nuevas (`h0_run_record`, `append_h0_run`) solo aplanan y
escriben—. Un árbol sucio no bloquea el registro: queda con `git_dirty=true`
y un aviso, porque el consumidor decide si confía en ese número.

Descartado: una tabla `h0_measurement_runs` + Alembic. Es telemetría de
experimento, no dato de dominio: su clave natural es el commit, no se
relaciona con ninguna entidad del modelo, y `git log -p docs/h0-runs.jsonl`
—el número al lado del código que lo movió— responde mejor la pregunta «¿este
commit ayudó?» que una consulta SQL. Además una tabla dispararía el flujo de
especificación completo que `CLAUDE.md` reserva para cambios de esquema, y la
regla de alcance del proyecto es preferir la solución más simple que funcione.
Si algún día hace falta el histórico en `/control`, importar el JSONL entonces
es trivial y ya con el consumidor real definido.

Consecuencia: 982 tests verdes (+6). El archivo `docs/h0-runs.jsonl` no existe
todavía: lo crea la primera corrida real del operador. Sigue pendiente, como
mejora separada, el desglose de precisión texto/imagen dentro del `H0Report`
—hoy solo registra la composición del corpus, no la tasa por tipo de
evidencia—, que es lo que haría medible el trabajo de OCR.

## 2026-08-31 — Historial de H0 en /control, leído del JSONL

Contexto: la medición de H0 quedó como comando (`python -m saimc.extraction.measure`)
y su registro como archivo. El operador pidió adelantar la vista del histórico
—lo liviano de la parte «medir H0 desde la interfaz», que en su forma completa
está bloqueada porque re-correr el OCR sobre todo el corpus tarda minutos y no
hay cola de trabajos en Fase 0 (decisión 3 de `CLAUDE.md`)—.

Decisión: sección nueva en `/control` («Historial de H0», la 2ª) que llama a
`read_h0_runs()` en cada carga y muestra una fila por corrida, más reciente
arriba: fecha, commit (+ «árbol sucio» y rama si no es master), versión de
Tesseract, las cinco precisiones, la tasa de descarte, los anuncios evaluados
con la marca «/ 300» cuando la corrida no es oficial, y el delta de extracción
completa contra la última corrida anterior con número. `read_h0_runs` vive al
lado de `append_h0_run` en `measure.py`, tolera el archivo ausente y las líneas
rotas (una línea ilegible se saltea, no tumba `/control`), y entrega un
`H0RunSummary` —subconjunto de las 21 claves del registro—. El delta se muestra
sin color: misma regla de «mide, no dictamina» que la columna «Diferencia» de
Disponibilidad. De paso, `_h0_runs_path()` dejó de invocar `git rev-parse` y
resuelve la raíz desde `__file__`, como `web/docs_catalog.py`.

Descartado: pasar el histórico por la base. Ya se decidió arriba (misma fecha):
es telemetría de experimento, el archivo es la fuente. Descartado también
colorear el delta verde/rojo: convertiría una medición en un semáforo.

Consecuencia: 991 tests verdes (+8). La sección se ve vacía (con enlace al
runbook) hasta que exista `docs/h0-runs.jsonl`. Los comentarios de sección de
`control.html` se renumeraron: la vista pasó de 9 secciones a 10.

## 2026-08-31 — Botón "Agregar fila" en el formulario de curaduría

Contexto: `propose_all` a veces propone menos filas de las que tiene el anuncio
(ítem del board, «producto mencionado una vez, precio repetido en varias
líneas»). Había 3 filas en blanco fijas de cortesía y un botón para eliminar
tarjetas, pero ninguna forma de agregar una cuarta.

Decisión: botón "Agregar fila" que clona del lado del cliente un `<template>`
renderizado con la misma macro `price_row_card` que las filas de arriba —una
sola fuente de verdad para los ~130 renglones de campos—. El índice de cada
fila nueva es un contador monótono sembrado en `row_count`: nunca se reusa, así
que borrar una del medio no obliga a rastrear huecos (los índices intermedios
llegan vacíos y caen en la rama de fila vacía que `submit_curation` ya tiene).
El JS reescribe solo el sufijo `_N` al final de los atributos `name`/`id`/`for`
del clon. El molde arranca con la provincia del canal, igual que las filas en
blanco. `submit_curation` no se tocó: sigue iterando `range(row_count)`.

Descartado: renderizar la fila nueva con htmx desde un endpoint. El ADR 001
deja la curaduría deliberadamente sin htmx (51 KB que las tres vistas del
operador no usan); el clon del `<template>` es JS plano, mismo estilo que el
botón "Eliminar" y `_price_row_scripts.html`. Descartado también recortar las 3
filas en blanco fijas ahora que hay botón: es otra decisión de UX, fuera del
alcance de este pedido.

Consecuencia: 994 tests verdes (+3). Sin JS el botón no hace nada y las filas
visibles (candidatas + 3 blancas) siguen siendo el piso, igual que hoy. El
`<template>` suma un `product_id_<row_count>` inerte al HTML — dos tests de GET
que contaban ocurrencias ahora acotan el conteo al form visible.

## 2026-08-31 — Descartado: forzar la exclusión del número de "Sale X" en `_extract_price`

Contexto: H-46 (`docs/requisitos.md` §03) señala que sobre líneas con cláusula
"Sale X <unidad>" el extractor a veces guarda ese número derivado como precio
en vez del precio real de la oferta — visto en `docs/decisions.md`, entrada
sobre `price_basis` del 2026-08-25 ("515 en vez de 25.750"). Un audit sobre el
dump de producción (`saimc_20260830T030008Z.sql.gz`, sin base de datos, solo
parseando el `COPY` de `raw_ingestions`) contó 35 de 147 líneas con esta
cláusula donde el extractor tomaba el número derivado en vez del número con
"$" de la misma línea, y de ahí se estimó una mejora de ~2,6 puntos de
precisión de precio si el extractor siempre prefiriera el número con "$".

Se implementó esa regla (excluir el tramo de "Sale X" al buscar precio, con
degradación al número excluido si no queda otro candidato) y se corrió
`measure.py` contra la base real (860 filas, mismos conteos que el dump).
**Resultado: precisión de precio 55,8% → 54,3%, extracción completa 43,5% →
42,1% — una regresión, no una mejora.**

Causa raíz del error de estimación: el audit inicial solo verificaba que el
número con "$" y el número de "Sale" DIFERÍAN entre sí, nunca los contrastó
contra `prices_history.price` (el valor real que el operador confirmó). Un
canal mayorista de Santiago ("Radicamos Reparto Sueño") republica el mismo
catálogo con frecuencia, y para la línea de Picadillo el operador confirmó el
número de "Sale" (515, la tarifa por libra) en 6 de 9 reposteos y el número
con "$" (el total del lote, 25.750 o 20.600) en los otros 3. La ambigüedad es
real y depende de una decisión del operador caso por caso —cuál de las dos
ofertas describe la fila que está curando—, no de cuál número aparece
primero en el texto. Forzar una preferencia fija en cualquier dirección
acierta en unos anuncios y falla en otros; no hay una regla universal que
resuelva esto sin inventar información que el texto no da por sí solo.

Descartado: la exclusión del tramo de "Sale X" tal como se implementó
(revertida antes de comitear, `engine.py` y `tests/test_extraction.py`
quedaron sin cambios).

Consecuencia: H-46 sigue abierto. Lo que sí queda demostrado, con número, es
que "hay ambigüedad real entre dos ofertas en la misma línea" no es lo mismo
que "hay que elegir la que no es la de Sale" — cualquier fix futuro para
H-46 necesita mostrarle la ambigüedad al operador en vez de resolverla en
silencio (dos candidatos de precio, no uno), o dejarla sin proponer del todo,
igual que ya hace `_declared_price_basis` cuando ninguna lectura cierra. La
medición contra `prices_history` real —y no contra una suposición sobre qué
"debería" ser correcto— es la que detectó esto antes de comitear nada.

## 2026-08-31 — Precio con marca de moneda al final, y número pelado como último recurso

Contexto: el desglose de H0 por tipo de evidencia mostró dos poblaciones muy
distintas — texto 64,4% de extracción completa contra imagen 5,1% — y que el
promedio de 43,5% no se mueve sumando anuncios en la misma proporción. Dentro
de las 98 filas de imagen, 35 (35,7%) fallaban el precio **con el número
correcto ya transcripto por el OCR**: el motor no las alcanzaba porque todas
sus pistas exigen algo *delante* del número ("a 500", "$500", "500 cup"), y
estas capturas escriben "al detalle 850$" (moneda al final) o directamente
"Papas prefritas 1kg 2500" (sin marca ninguna).

Decisión: dos tramos nuevos al final de `_extract_price`, en ese orden.
`_PRICE_DOLLAR_SUFFIX_RE` reconoce el "$" escrito después del número — sigue
siendo una pista explícita, así que va antes. `_bare_price_candidate` toma el
mayor número suelto de la línea, con tres guardas medidas sobre texto de OCR
real: descarta más de 6 dígitos (teléfono del anunciante), lo pegado a ":"
o "+" (hora de la captura, prefijo telefónico) y lo seguido de una unidad
(es cantidad, y ya lo lee `_QUANTITY_RE`). Cualquier pista real le sigue
ganando: el número pelado es el último recurso, no un competidor.

Proponer el número pelado **es decisión explícita del operador**: hasta hoy el
motor se abstenía a propósito porque un número suelto es ambiguo, y el
operador prefiere revisar una propuesta dudosa a completar el campo a mano.
Es coherente con §6.1 —el sistema propone, el humano confirma o corrige— y
con el criterio de CLAUDE.md de que la precisión gobierna cuánto trabajo
manual cuesta cada anuncio, no si el dato es válido.

Descartado: (a) tomar el ÚLTIMO número de la línea en vez del mayor — se
midió contra el corpus real y da exactamente el mismo resultado, porque en
estos anuncios el precio es a la vez el mayor y el último; se eligió el mayor
por no depender de dónde corte el renglón el OCR. (b) Filtrar por magnitud
mínima: el corpus tiene precios confirmados de 30 y 50 CUP, así que cualquier
piso razonable los perdería.

Consecuencia: 1004 tests verdes (+10), con los casos escritos antes del código
(`TestPrecioSinMarcaDelantera`). Medido contra la cola real antes de
comitear: **precisión de precio 55,8% → 57,2%** (en la mitad de imagen 24,5%
→ 28,6%), sin regresión en producto, provincia ni cantidad/unidad. La
extracción **completa** NO se mueve (43,5%) y era esperable: en la mitad de
imagen el cuello no es el precio sino cantidad/unidad (19,4%) y provincia
(53,1%), y una fila necesita los cuatro campos bien para contar. El costo es
+63 filas propuestas sobre 1123 (+5,6%) que el operador revisa — aceptado por
él explícitamente al tomar la decisión.

## 2026-08-31 — RapidOCR como motor primario de imagen, Tesseract de respaldo

Contexto: el desglose de H0 por tipo de evidencia (entrada anterior) mostró
imagen al 5,1% de extracción completa contra 64,4% de texto, y que la mitad
del fallo de precio en imagen ocurre con el número YA transcripto por
Tesseract — la otra mitad, con el número perdido del todo. El operador
propuso evaluar un motor local (`docs/decisions.md`, misma fecha, entrada
sobre PaddleOCR/rapidocr).

`paddlepaddle` (el runtime de PaddleOCR) publica wheels hasta cp313; el
proyecto corre Python 3.14, así que PaddleOCR propiamente dicho no se puede
instalar. `rapidocr` sí: es un wheel puro de Python que ejecuta los mismos
modelos PP-OCR convertidos a ONNX, sobre `onnxruntime` (que tiene cp314).

Medido contra las 98 filas de imagen curadas, ¿el precio confirmado aparece
en la transcripción?: Tesseract 58,2%, RapidOCR 78,6% (+23, −3). En H0
completo el salto es mucho menor —43,5% → 44,2%— porque el cuello se corre a
las reglas de extracción: el número pasa a estar en el texto, pero el motor
todavía no siempre lo agarra. RapidOCR **habilita** ese trabajo, no lo
reemplaza.

Decisión: `saimc.extraction.ocr.transcribe` es ahora una cascada. RapidOCR
primero (`_transcribe_rapidocr`); si no está instalado, revienta con esa
imagen puntual, o no encuentra ni un renglón, cae a Tesseract
(`_transcribe_tesseract`, el código de siempre, sin cambios). El motor de
RapidOCR se construye una sola vez por proceso (`_rapidocr_engine`, cacheado)
y tarde —en la primera transcripción, no al importar el módulo— para no
pagar el costo si nadie cura una imagen en esa corrida del server.

`rapidocr` + `onnxruntime` quedan como dependencia opcional
(`pyproject.toml`, extra `ocr`, 417 MB instalados) y no en `dependencies`: un
entorno sin ellos sigue transcribiendo con Tesseract solo, sin romper nada —
es exactamente la condición que el respaldo existe para cubrir.

Descartado: (a) el modelo latino de reconocimiento (`latin_PP-OCRv5_rec_mobile`)
en vez del chino por defecto — medido, da exactamente los mismos 44,2% (el
modelo chino de PP-OCR ya cubre el alfabeto latino) y es más lento por
imagen. (b) Reemplazar Tesseract en vez de poner a RapidOCR delante: perder
el respaldo cambia "instalación opcional pesada" por "dependencia dura", y el
20% de casos donde RapidOCR no encuentra nada seguirían sin transcribir.

Consecuencia: 1011 tests verdes (+7 sobre 1004), con los casos escritos antes
del código (`TestCascadaDeMotores`, que fuerza cada camino de la cascada con
motores falsos, y `sin_rapidocr` para que el resto de `test_ocr.py` siga
siendo determinista con o sin el paquete instalado). Medido de punta a punta
contra la cola real, a través de `ocr.transcribe` real y no de una
simulación: reproduce exacto lo medido antes de escribir código — precio
57,2%→57,9%, producto 85,6%→88,8%, provincia 82,7%→84,5%, completa
43,5%→44,2%. Costo: ~3,5s por imagen contra ~0,9s de Tesseract solo (~4×);
una corrida de `measure.py` pasa de ~1,5 a ~5,5 minutos.

Queda un hallazgo aparte, ya anotado en el requisito: ~21 puntos de hueco
entre "el precio está en el texto" (78,6%) y "el motor lo extrae bien"
(57,9%) en imagen. Es el próximo lever, y ahora sí se puede atacar sobre
texto legible en vez de sobre ruido de Tesseract.

## 2026-08-31 — Provincia y cantidad/unidad en imagen: sin lever de código, dos rescates descartados

Contexto: con RapidOCR ya en producción, se investigó por qué cantidad/unidad
(19,4%) y provincia (58,2%) siguen siendo el cuello de la extracción completa
en imagen, mientras el precio se benefició del cambio de OCR.

**Provincia — techo estructural, no bug.** Descompuesto contra las 98 filas
de imagen: 58,2% OK, 38,8% el texto simplemente no menciona ninguna
provincia, 2% menciona otra, 1% es ruido de pareo entre filas ambiguas. Se
verificó si había margen por canales sin provincia cargada —la salida más
barata posible, sin tocar el motor—: **77 de 84 imágenes curadas no vienen de
ningún canal** (bandeja o pegado directo, sin `channel_province` posible por
diseño), y las 9 que sí tienen canal ya tienen provincia cargada al 100%. No
queda ni una fila por recuperar de esa vía.

**Cantidad/unidad — dos intentos de rescatar la "línea huérfana", los dos
descartados.** El patrón real: RapidOCR separa en líneas distintas el nombre
del producto y la línea de precio+cantidad ("Papas prefritas" en una línea,
"1kg 2500" en la siguiente) — `_extract_quantity("1kg 2500")` lee bien
`(1, "kg")` en aislamiento, pero `propose_all` descarta la línea entera
porque no tiene producto ni nombre candidato propio (el marcador de cantidad
ocupa toda la línea).

Se probaron dos variantes, simuladas contra el corpus real antes de tocar
`engine.py`:

1. Atribuir la línea huérfana al único producto que matchea en TODO el
   anuncio (mismo criterio de `_tiered_proposals`). **Regresión clara**:
   precio en imagen 30,6%→16,3%, cantidad 19,4%→9,2%, completa 7,1%→4,1%.
   Causa: estas capturas de catálogo mayorista casi siempre tienen VARIOS
   productos; que solo uno haya matcheado no significa que sea el único real
   —el resto no matcheó por ruido de OCR, no porque no exista— y atribuirle
   todas las huérfanas mezcla ofertas de productos distintos.
2. Variante angosta: solo cuando hay EXACTAMENTE una línea huérfana en todo
   el anuncio (evita el caso multi-producto de arriba), tomando el nombre de
   la línea inmediata anterior con letras. **No mejora nada** (completa se
   queda en 7,1%, precio incluso baja levemente 30,6%→28,6%): agregar la fila
   rescatada introduce una candidata más que compite en
   `_match_rows_to_candidates`, y en la práctica le quita el pareo a otra
   fila del mismo anuncio tan seguido como lo que gana.

Verificación adicional que descarta el atajo obvio: de las 98 filas de
imagen, solo 17 son «cantidad 1 sin unidad» (el caso ambiguo de precio
unitario) y **47 tienen cantidad confirmada mayor a 1** — la mayoría del
fallo exige extraer un número real del texto, no completar un default.

Descartado: las dos variantes de rescate de línea huérfana (código nunca
llegó a comitearse, quedó en el scratchpad de la sesión). Cargar provincia
por canal: no hay canales pendientes de cargar.

Consecuencia: ningún código cambia en este slice. El hallazgo es negativo
pero real — dos intentos razonables, medidos y rechazados, evitan que el
próximo intento repita el mismo camino. Lo que queda es exactamente lo que
`spec.md` §6.1 y `EXT-07` (`docs/requisitos.md`) ya anticipan: reglas y
diccionarios agotaron su margen barato en cantidad/unidad y provincia de
imagen; el siguiente lever es un LLM sobre lo que las reglas no resuelven,
con confirmación humana — no otra regex.

## 2026-09-01 — Asistente de extracción por LLM: solo huecos, solo a pedido, solo texto redactado

Contexto: tres intentos de mejorar las reglas se midieron y se descartaron el
2026-08-31 (arbitraje de precio, y dos rescates de «línea huérfana»). Cambiar
el OCR a RapidOCR subió la presencia del precio en el texto de 58,2% a 78,6%
pero la extracción completa solo de 43,5% a 44,2%: quedaron ~21 puntos entre
«el número está en el texto» y «el motor lo agarra». Es el punto donde §6.1 y
`EXT-07` dejan de ser prematuros — «LLM únicamente para los casos que las
reglas no resuelven, con confirmación humana».

Decisión: `saimc/extraction/llm_assist.py` + `POST /curation/{id}/assist`,
implementado en cinco commits (`openspec/changes/llm-extraction-assist/`).

- **Solo huecos.** Se le piden los campos que `propose_all` dejó en `None`, y
  no puede pisar uno resuelto. `currency` queda afuera: §6.1 manda inferir CUP
  y nunca está vacía.
- **Solo texto redactado.** `redact_personal_data()` corre en el borde de la
  llamada, no solo antes de guardar. La imagen nunca sale del VPS.
- **Solo a pedido.** Un botón, nunca automático al abrir un pendiente. El costo
  lo decide el operador anuncio por anuncio.
- **Catálogos cerrados, validados de nuevo del lado nuestro.** Un producto,
  provincia o unidad que no resuelva se descarta como si no hubiera contestado.
  Nada se da de alta desde la salida del modelo.
- **Registro de solo-anexado por invocación**, en `data/` (no en `docs/` como
  `h0-runs.jsonl`: son ~600 líneas al mes atadas a ids de producción).
- **H0 se mide sobre el conjunto NO asistido.** Es lo único comparable con el
  histórico. Consecuencia asumida: un anuncio asistido no suma al umbral de 300.

Descartado: (a) un modelo de visión sobre la imagen cruda — resolvería mejor el
problema de layout, pero mandaría a un tercero los píxeles con el teléfono del
anunciante, una categoría que `spec-map.md` «Contradicción 1» nunca evaluó
porque la acota a publicación; (b) un modelo local — el VPS tiene 2 vCPU, sin
GPU y ~431 MB libres; (c) disparo automático o en lote — costo sin control del
operador, y la decisión cerrada 3 prohíbe la cola que haría falta.

Consecuencia: 1093 tests verdes. Dependencia y clave **ambas opcionales**: sin
ellas el botón no se renderiza y la cola funciona igual que antes. Costo ~$1,86
al mes con Sonnet 5, **adicional al plan Pro** (`docs/costos.md`). El riesgo
vivo es el rubber-stamping: el registro lo hace detectable después, la marca
visual lo hace menos probable en el momento, y ninguno de los dos lo hace
imposible.

## 2026-09-02 — Franja de estado en /control, y el estado de WAHA (N-02)

Contexto: el operador pidió dos cosas que resultaron ser la misma. Primero, que
WAHA se ve caído y `/control` no lo dice — que es exactamente el hueco **N-02**
de `docs/requisitos.md`: «Ya ocurrió: la sesión cayó a `FAILED` y toda la
ingesta automática paró en silencio». No había ningún chequeo en el código.
Segundo, que `/control` no se lee de un vistazo. Medido: **once secciones en
916 líneas**, todas con el mismo peso visual, y dos de ellas agregadas por mí
el día anterior. No es un panel, es un informe largo.

Las dos son el mismo problema: la vista no tiene jerarquía, así que para saber
si algo se rompió hay que leerla entera.

Decisión: `saimc/ingestion/whatsapp_health.py` consulta el estado de la sesión
de WAHA, y `saimc/web/health_strip.py` arma una franja de indicadores que abre
la vista — WhatsApp, última captura, cola, tasa de cambio y asistente.

- **Ordenada por gravedad, no por catálogo.** Lo roto sube solo al primer
  lugar; en un día sano la franja no exige nada.
- **Cada tarjeta dice qué hacer**, no solo que algo pasa. Un semáforo sin
  instrucción obliga a adivinar, que es el problema que vino a resolver, y
  enlaza a la sección con el detalle para no ser un callejón sin salida.
- **`off` no es `down`.** Una capacidad sin configurar se pinta gris y NO
  cuenta para el aviso rojo: pintar de rojo algo que el operador no usa
  entrena a ignorar el rojo.
- El color no es la única señal: cada tarjeta trae el estado escrito.

Descartado: (a) Bootstrap, Angular o React, que es la estética que el operador
nombró — la decisión cerrada 1 los deja para Fase 2 y no hacen falta: el
sistema Material 3 del proyecto ya tiene las piezas; (b) un umbral de "cola
demasiado grande" — sería un número inventado de los que §12.6 prohíbe, así
que la cola se muestra y no se juzga; (c) reordenar las once secciones, que era
la otra mitad del pedido: con la franja arriba, el orden de abajo dejó de ser
el problema, y moverlas costaba más riesgo que valor.

Los dos umbrales que sí existen —2 días sin captura, 2 días de tasa vieja— no
deciden si un dato es válido ni si se publica: solo eligen cuándo llamar la
atención. Queda anotado porque §12.6 prohíbe los otros.

Consecuencia: 1166 tests verdes (+46). El chequeo de WAHA degrada como
`eltoque.fetch_rate` —timeout corto, nunca levanta— porque vive dentro de la
carga de `/control` y no puede tumbarla. Verificado en vivo contra la instancia
real del operador: devolvió `working` y la franja lo muestra como «conectado».

## 2026-09-02 — Los alias de provincia se quedan en código, no pasan a `provinces`
Contexto: el board tenía la tarea «`provinces.aliases` como columna (en vez de
diccionario en código)». `_PROVINCE_ALIASES` en `saimc/extraction/engine.py`
mapea jerga y abreviaturas informales («stgo», «habana», «scu») al nombre
canónico de la provincia, y `_match_province` lo recorre de más largo a más
corto. La tarea propone moverlo a una columna nueva en `provinces`.
Decisión: no se hace. El diccionario sigue viviendo en la capa de extracción.
El propio comentario de `engine.py` ya fija el motivo: las 16 provincias son
esquema fijo, pero la jerga con que un anuncio las nombra no es parte del
esquema y no le pertenece a `Province`.
Descartado: la columna + seed en migración sin UI de edición — es movimiento
lateral. Sin una pantalla en `/catalog` para gestionar alias, cambiar uno
sigue exigiendo un deploy, igual que hoy, pero además suma una migración y el
riesgo de reprocesar datos crudos. El precedente de `Product.aliases` (columna
`JSON`, `list[str]`) confirma el patrón: también se creó sin UI y nadie la
edita (`catalog.py` la inicializa vacía y no la vuelve a tocar). La versión que
sí valdría la pena —columna + seed + `_match_province` leyendo de BD + UI en
`/catalog`— toca esquema y motor de validación, así que entra por el flujo de
especificación completo y es trabajo de varias sesiones, no un slice.
Consecuencia: la tarea sale del GitHub Project. Si en algún momento el operador
necesita tocar alias de provincia con frecuencia y sin deploy, se reabre como
cambio especificado con UI incluida; la columna suelta no se implementa.

## 2026-09-02 — La vista móvil se parchea, no se revierte el experimento de la barra superior
Contexto: el operador reportó que la vista móvil «se rompió», sobre todo en
`/curation`. Dos causas, las dos del commit `e25aaec` que cambió el rail
lateral por una barra superior. Primero: el rail viejo era `display:none` en
móvil, así que en el teléfono había una sola barra fija; la barra nueva
(`.m3-topnav`, 56px sticky) más `.m3-topbar` (sticky en `top:56px`, con título
+ subtítulo + stats) apilaban ~230px de cromo fijo sobre un viewport de ~640px.
Segundo: `.m3-actionbar` es `display:flex` sin `flex-wrap`, así que «Descartar»
+ «Curar anuncio» (grande) se salían del ancho del teléfono.
Decisión: parche CSS, no revertir `e25aaec`. `flex-wrap: wrap` en la regla base
de `.m3-actionbar`; y en `@media (max-width: 720px)`: `.m3-topbar` vuelve a
`position: static`, el título baja a 22px, y los dos botones de acción se
reparten una fila propia (`flex: 1 1 auto`). La barra superior de 56px —ya solo
iconos en móvil— se queda fija.
Descartado: revertir el experimento de la barra superior. `e25aaec` dejó dicho
que revertir era un commit si no convencía, pero el problema es solo el
tratamiento móvil, no la barra en sí; en escritorio funciona y el operador no
pidió volver al rail. Revertir cambiaría escritorio para arreglar teléfono.
Consecuencia: 1169 tests verdes (+3, todos sobre el CSS con el mismo patrón que
el test del atributo `hidden`). Los 2 fallos de `test_public_filter.py` siguen
preexistentes. La barra superior queda como estructura vigente, ya no como
experimento sin cerrar.

## 2026-09-02 — Retro-completado de price_basis por inferencia contra el vecindario
Contexto: la «Matriz de los pilotos» de `/control` mostraba el mismo producto
como varias series. Medido: de las duplicaciones dentro de un mismo
producto/provincia/unidad/formato, la causa no era la unidad (kg vs lb, que
`compute_cells` separa a propósito) sino `price_basis` nulo. Ese campo entró el
2026-08-21; 132 filas cargadas entre el 10 y el 21 de agosto quedaron sin él y
`cells.py` las aísla en su propia celda (`unresolved_basis`). Arroz/La Habana
`kg` minorista aparecía como dos series, n=23 mediana 688,70 y n=20 mediana
18.460. La regla basada en evidencia (`engine.py:_declared_price_basis`, que lee
«Sale $X Lb» del anuncio) resolvió 0 de 110: esos avisos viejos escriben «sacos
de 110 lb a 33 000» y nunca el precio unitario.
Decisión: `src/saimc/basis_backfill.py`. Para cada fila sin basis se calculan
las dos lecturas del mismo número —`as_unit` y `as_total = price/quantity`—,
ambas convertidas a la unidad canónica del producto, y gana la que queda
proporcionalmente más cerca de la mediana del vecindario (producto × provincia
× formato de venta × unidad canónica). Sin umbral de precio, de margen ni de
muestra mínima: §12.6. Sin vecindario no hay veredicto y la fila se queda nula.
El vecindario solo lo forman filas `quantity == 1` o `price_basis == 'total'`
—misma definición de «no ambigua» que `detect_basis_suspects`—: una fila
`price_basis == 'unit'` con `quantity != 1` es justo la forma que se puede
haber curado mal y no vota (un `24800 / 40 lb` de Pollo marcado `unit` movía la
mediana de su celda de 710 a 12.755 CUP/lb). El valor inferido NO es un valor
curado: cada escritura deja su rastro en `data/basis-backfill.jsonl`, log de
solo-anexado, mismo criterio que `data/llm-assist-log.jsonl` (design.md D6) —
sin columna nueva, sin migración. Aplicado una vez: 91 de 110 filas resueltas
(87 `total`, 4 `unit`), 19 sin vecindario quedan nulas. La matriz pasó de 97 a
86 celdas; Arroz/La Habana `kg` minorista es ahora una sola serie n=43.
Descartado: (a) escribir el basis inferido en la columna sin marca de
procedencia — lo vuelve indistinguible de lo curado y contamina la base de
medición de H0; (b) curar las 110 a mano por la cola — garantía idéntica al
resto pero una sesión entera de trabajo para deuda vieja; (c) excluir las 132
de las vistas agregadas — deja fuera dato recuperable.
Consecuencia: 1185 tests verdes (+16). Los 2 fallos de `test_public_filter.py`
siguen preexistentes (2026-08-30), ajenos a este cambio. `measure.py` todavía no
compara `price_basis`, así que el log no filtra ninguna métrica hoy; existe para
cuando lo haga. Fuera de alcance: unas pocas filas con precio mal tecleado
(ceros de más, ej. Pollo/La Habana con lectura de 465.500 CUP/lb) pasaron de
celdas n=1 aisladas a la serie normalizada, donde inflan el `max` pero no la
mediana; son un problema de calidad de dato distinto, que cubren
`detect_basis_suspects` y la revisión del operador.

## 2026-09-02 — La matriz de /control funde kg con lb; reemplaza la parte de unidad de la decisión de 2026-08-12
Contexto: la «Matriz de los pilotos» seguía mostrando el mismo producto como
varias series por unidad —Arroz/La Habana en `kg` y en `lb` por separado—.
`compute_cells` separaba `unit` a propósito desde el 2026-08-12 («vista de
control de carga»). El operador pidió lo contrario: para leer cómo se movió el
pollo en La Habana, la diferencia kg/lb es ruido de formato de anuncio, no una
serie distinta. La unidad base ya existía (`units.py:convert_price_per_unit`,
por producto) y ya la usaban `compute_retail_ranges` y
`compute_retail_observations`; faltaba aplicarla acá.
Decisión: `compute_cells` lleva cada fila a la unidad canónica del producto
(`Product.unit` vía `canonical_unit()`) con `convert_price_per_unit()`, después
de dividir por `quantity`, y agrupa por esa unidad. `sale_format` **sigue**
partiendo la celda (mayorista vs. minorista es economía real). La conversión
solo se intenta en la fila resuelta: la que no tiene cantidad (`unquantified`)
o basis (`unresolved_basis`) se queda con su unidad cruda, porque sin un precio
por unidad confiable no hay nada que convertir. Lo que no convierte ni en
principio —`caja`, que no declara cuánto contiene, o una magnitud distinta a la
del catálogo— conserva su unidad y se marca `Cell.unconverted_unit`: misma idea
que la exclusión de `compute_retail_ranges`, pero visible como celda. La fila
sin `unit` sigue en su celda `unit=None` y **no** se marca —es «sin unidad», no
«no se pudo convertir»—. En `control.html` el enlace «n = …» a
`/curation/history` deja de mandar `unit=` en las celdas fundidas: filtrar por
la unidad canónica escondería las filas convertidas.
Descartado: mantener la separación por unidad y agregar una vista aparte —
duplica lo que `compute_retail_ranges` ya hace y deja al operador reconciliando
dos pantallas. Convertir también las filas sin cantidad/basis — daría un número
que parece por unidad sin serlo.
Consecuencia: reemplaza la parte de **unidad** de la entrada del 2026-08-12
(«La celda de /control se parte por unidad y formato»); la parte de **formato de
venta** de esa entrada sigue vigente. Medido contra producción: matriz de 86 a
77 celdas; Pollo/La Habana de 7 series a 4, Arroz/La Habana de 7 a 2,
Arroz/Santiago de 7 a 3. 1188 tests verdes (+3). Los 2 fallos de
`test_public_filter.py` siguen preexistentes (2026-08-30). Queda como cola
aparte lo que no fundió por `price_basis` nulo en su celda (ej. Pollo/La Habana
`kg` mayorista, 6 filas sin basis y sin referencia en esa celda para inferirlo).

## 2026-09-02 — El drill-down «n = …» filtra por identidad efectiva; corrige la omisión de `unit=` de la entrada anterior
Contexto: la entrada de arriba dejó el enlace «n = …» de una celda fundida sin
mandar `unit=` a `/curation/history`, para no esconder las filas convertidas.
Pero el endpoint filtra por las columnas **crudas** de `prices_history`, así que
sin `unit=` no filtraba por unidad en absoluto: clickear una celda de una sola
observación (ej. Pollo/La Habana `lb` mayorista resuelta, n=1) abría también las
filas de `kg` mayorista sin basis de la misma celda vecina. Las celdas de flag
(`unquantified`, `unresolved_basis`) nunca fueron aislables por el endpoint
—esos flags no son columnas—, la fusión solo lo hizo más visible.
Decisión: la resolución de una fila cruda a su identidad efectiva —precio a CUP
y por unidad canónica, más los cuatro flags— se extrae a `cells.resolve_row()`,
que ahora usan `compute_cells` y `/curation/history`. El endpoint recomputa
`resolve_row` fila por fila y filtra por unidad **efectiva** más tres params
nuevos (`unquantified`, `unresolved_basis`, `unconverted_unit`, cada uno `0`/`1`
o ausente). `control.html` manda siempre `unit=` (la unidad efectiva de la
celda) y los tres flags. `currency` sigue crudo y solo lo manda la celda
`unconverted`.
Descartado: una clave opaca de celda con `/curation/history?cell=<clave>` que
re-corriera `compute_cells` — obliga a que `compute_cells` arrastre `source_id`
por celda, dato que hoy descarta. Filtrar solo por unidad y dejar los flags sin
resolver — no arreglaba las celdas «sin cantidad» / «sin basis», que son
justamente las de una sola observación que el operador reportó.
Consecuencia: `/curation/history` depende de `saimc.cells` (import nuevo, sin
ciclo). Queda sin cerrar la ambigüedad moneda↔celda: una celda fundida resuelta
(que no manda `currency`) todavía puede mostrar filas de una celda `unconverted`
vecina del mismo producto/provincia/unidad/formato; no es lo que se reportó y se
deja anotado. 1191 tests verdes (+4). Los 2 fallos de `test_public_filter.py`
siguen preexistentes (2026-08-30).

## 2026-09-02 — La matriz empareja minorista con mayorista y muestra el ratio (opción A)
Contexto: tras fundir las unidades, las series que quedaban dentro de una
tarjeta producto × provincia eran casi todas el corte por formato de venta,
apiladas sin orden y sin comparar. La brecha minorista/mayorista es estructura
de mercado —el código ya la trata como «economía real»—, pero quedaba como dos
filas sueltas entre las series con flag. Se analizaron tres formas (emparejar +
ratio; formato como eje de columna; dos matrices separadas). El dato de
producción: solo 6 de ~9 celdas piloto tienen los dos formatos, y 5 de esas 6
con mayorista n ≤ 9. La brecha real medida es 1,05–1,20× en las celdas sólidas
(no ~2,5× como asumía la entrada del 2026-08-12).
Decisión: opción A. En `matrix.py:_pair_formats` —solo presentación, no toca la
normalización—: (1) por cada unidad con una minorista limpia, la mayorista
limpia de esa misma unidad recibe `CellWithBar.retail_ratio = mediana_minorista
/ mediana_mayorista`; (2) las series se reordenan para que la unidad de más
muestra quede arriba y, dentro de cada unidad, la minorista antes que la
mayorista, así el par queda pegado. «Limpia» = sin `unconverted`,
`unquantified`, `unresolved_basis` ni `unconverted_unit`: una mediana con
cualquiera de esos no es un precio por unidad en CUP y compararla mediría dos
cosas distintas. Sin corte de muestra mínima (§12.6): el `n` de las dos series
queda a la vista al lado del ratio. En `control.html` la mayorista muestra «el
minorista sale ×N sobre esta serie».
Descartado: formato como eje de columna completo (opción B) — duplica el ancho
de la matriz para poblar 6 celdas, 5 con n ≤ 9; se reevalúa cuando el mayorista
pase de ~20 obs por celda. Dos matrices separadas (opción C) — `compute_retail_
ranges` ya es la vista minorista-only; una mayorista aparte hoy tendría ~6
celdas. Fundir formatos — sigue vigente que no son comparables (2026-08-12).
Consecuencia: 1194 tests verdes (+6). Los 2 fallos de `test_public_filter.py`
siguen preexistentes (2026-08-30). El ratio se apoya en la mediana mayorista,
que a este volumen suele ser n ≤ 9 y en dos celdas n = 1 — por eso se muestra
como razón cruda junto al `n`, nunca como veredicto. La entrada del 2026-08-12
que estimaba ~2,5× de brecha queda para revisar cuando el mayorista tenga
muestra: si la brecha real es ~1,15×, el argumento de «incomparables» se
sostiene por ser universos distintos, no por la magnitud.

## 2026-09-03 — H0: el emparejado fila↔candidato alinea por mejor ajuste cuando un producto se repite
Contexto: la medición de H0 (`measure._match_rows_to_candidates`) emparejaba cada
fila confirmada con el primer candidato del mismo `product_id`, por orden. En los
price-lists de canal —«COTORRO / SAN JOSÉ / OFERTAS DEL DÍA»— el mismo producto
aparece dos veces en un mensaje: «Arroz grano dorado 100 lb … o 30885» (saco
mayorista) y «Arroz grano dorado 1 kg … o 688,7» (menudeo). El extractor sacaba
las dos filas **bien**, pero el orden de las filas curadas no seguía el de las
líneas del anuncio, así que el emparejado cruzaba menudeo↔mayorista y contaba las
dos como fallo de precio Y de cantidad/unidad. Medido el 2026-09-03: 39 de 295
filas. Era el grueso de las «37 confusiones kg↔lb» de la tarea 2 del plan de
extracción — que resultó ser un bug de medición, no de reglas, igual que la
tarea 1.
Decisión: entre candidatos del **mismo producto**, `_assign_within_product`
elige por mejor ajuste —costo con peso precio 1,0 / cantidad-unidad 0,5 /
provincia 0,25, óptimo por `permutations` para conjuntos chicos, greedy si son
grandes—. Solo reordena dentro de un `product_id` ya reconocido: no toma nada
prestado de otro producto, así que alinear no puede inflar la métrica (la guarda
de 2026-08-21 contra «regalar el precio de una línea ajena» sigue intacta para
las filas cuyo producto no se reconoció). El reparto de las filas sin candidato
del mismo producto no cambia.
Descartado: dejar el emparejado posicional —pierde aciertos reales del extractor
por el orden de curación—. Un matcher que cruce productos —riesgo de acreditar
coincidencias, que es justo lo que la guarda de 2026-08-21 evita—.
Consecuencia: **la métrica se volvió más fiel, el extractor no cambió**. En el
corpus actual (183 anuncios / 295 filas) extracción completa **33,2% → 46,4%**;
precio 45,8% → 61,0%; cantidad/unidad 42,4% → 59,0%; producto y provincia sin
cambio. +39 filas a completa, 0 regresiones (medido). **`docs/h0-runs.jsonl`
tiene una discontinuidad acá**: las corridas anteriores a este commit y las
posteriores no son comparables sobre el mismo corpus. 1200 tests verdes (+2).
Los 2 fallos de `test_public_filter.py` siguen preexistentes (2026-08-30).

## 2026-09-03 — Extracción: "por libra" sin cantidad explícita se propone como (1, lb)
Contexto: pasada tight de la tarea 3 del plan de extracción. Después del fix de
emparejado (1d1e602), las fallas de precio y cantidad que quedaban estaban
fragmentadas en micro-patrones. Se simularon los dos más limpios contra el
corpus real, monkeypatcheando `engine.*` antes de escribir código (metodología
obs #272d / #276): (1) miles con espacio `31 000` → `31000` — **+0, descartado**,
esos casos caen por line-drop o por elección de moneda, no por el separador;
(2) "x libra" / "por libra" / "la libra" (y kilo) sin número.
Decisión: solo (2). `_extract_quantity`, cuando `_QUANTITY_RE` no encuentra
`N unidad`, busca `(x|por|la|/) (libra|lb|kilo|kg)s?` y devuelve `(1, lb|kg)` —
la unidad implícita que el operador confirma en esas filas—. Guarda: no aplica
si la línea nombra un envase de bulto (`_BULK_WORD_RE`: caja/saco/paca/
paquete/cajón/contenedor), porque ahí "la libra" es el precio unitario derivado
del saco, no la unidad de venta al menudeo.
Descartado: proponer `quantity=None` en vez de `1` — `_quantity_unit_correct`
compara la cantidad y `None != 1`, así que no cerraría la fila; y el operador
efectivamente cura 1. Miles con espacio (patrón 1) — medido +0.
Consecuencia: extracción completa **46,4% → 47,8%** en el corpus actual (183
anuncios / 295 filas), +4 filas, 0 regresiones (medido con OCR cacheado, los 4
casos son "X x libra (precio)"). 1210 tests verdes. Los 2 fallos de
`test_public_filter.py` siguen preexistentes (2026-08-30). La cola de fallas de
precio/cantidad restante —tokens pegados `Boquitas70.00cup`, doble moneda con
lado USD curado, `$ 23.500 CUP` en línea sin producto— queda para el asistente
LLM (tarea 4), que la resuelve mejor que más regex.

## 2026-09-03 — «Descarte» se parte en «sin dato útil» y «fuera de foco»
Contexto: `spec.md` §12.5 define la tasa de descarte como «anuncios sin
información económica extraíble», objetivo <30%. Pero el operador también
descarta anuncios que traen producto y precio, solo que fuera del foco de la
Fase 0 —no son de los pilotos, o son mayorista puro (ej. «Sazón Golden … $35,50
CUP … Compra mínima: 5 cajones x 480 u»)—. `discard_curation` guardaba todo
como `DISCARDED` sin motivo, así que los dos casos contaban igual y el 67% a la
vista decía «el sistema trae basura» cuando en gran parte decía «esto es útil
pero hoy no lo seguimos». De 378 descartes, el 66% son imágenes sin
`original_text`: el motivo no se puede derivar después, hay que capturarlo en
el momento.
Decisión: `raw_ingestions.discard_reason` nullable (`DiscardReason`:
`off_focus` / `no_data`), migración `0017`, sin backfill —los 378 previos
quedan NULL = «motivo no registrado» y se cuentan como `no_data`, lo
conservador—. `discard_curation` exige el motivo (`Form(...)`): dos botones en
la cola, «Sin dato útil» y «Fuera de foco». `discard_out_of_focus` (el lote
`/curation/sin-foco`) marca `off_focus` por construcción. `IntakeHealth.
discard_rate` y `measure.H0Report.discard_rate` se redefinen a la fracción de
§12.5: `(descartes - off_focus) / (decidido - off_focus)`, `None` si no queda
nada que medir. `off_focus_rate` se reporta aparte como contexto —un número
alto ahí dice que los canales suscritos venden mayormente cosas que la Fase 0
no sigue, que es señal de selección de canales, no de calidad de extracción—.
Descartado: no tocar nada y documentar que el número está inflado (parche que
hay que recordar cada vez). Un estado terminal nuevo `out_of_scope` o uno
aparcado `deferred` (onda expansiva sobre `processing_status`, que se usa en
muchos lados; y un anuncio fuera de foco hoy puede volver a serlo en Fase 1,
así que un motivo re-consultable es mejor que un estado nuevo). Filtrar en la
ingesta (contradice curaduría manual primero + `raw_ingestions` inmutable).
Consecuencia: `discard_reason` es metadato de procesamiento, como
`processing_status` —que ya se modifica—, no toca la evidencia inmutable. La
serie de `h0-runs.jsonl` sigue continua: las corridas anteriores tienen
`discarded_off_focus = 0`, así que su `discard_rate` con la fórmula nueva da lo
mismo que con la vieja; solo diverge a medida que se acumulen descartes
`off_focus`. El número a la vista no se mueve todavía (los 378 históricos son
NULL); se corrige a medida que el operador etiqueta los nuevos. 1217 tests
verdes. Los 2 fallos de `test_public_filter.py` siguen preexistentes
(2026-08-30).

## 2026-09-03 — Los productos bandera no se acotan: la premisa de §12.5 no aplica
Contexto: la nota de unidades de `spec.md` §12.5 dice que si la normalización de
cantidad resulta ser el cuello de botella, «la respuesta correcta es acotar los
productos bandera a aquellos con unidad de venta estable, no bajar el estándar».
Se abrió una tarea para decidirlo cuando cantidad/unidad medía 42,4%.
Decisión: **no se acota nada.** La nota pone dos condiciones y ninguna se
cumple. (1) Ya no es el cuello: tras el fix de emparejado (`1d1e602`) y la regla
«por libra» (`14eecd6`), cantidad/unidad está en **60,7%**, empatada con precio
(61,0%) como la métrica más débil y a 9 puntos del mínimo de 70%, no a 28. (2)
Los cinco pilotos **ya tienen unidad de venta estable**: filas de piloto con
unidad no convertible en toda la historia, **cero**. Aceite `l`+`ml` (100%
convertible), Arroz `lb`+`kg` (100%), Azúcar blanca `lb`+`kg` (100%), Pollo
`lb`+`kg` (100%), Picadillo `lb` + 1 fila sin unidad (95%). Que el Arroz venga
en kg y en lb no es inestabilidad: la libra son 0,45359237 kg por definición y
`cells.py` funde las dos desde `468e740`. Las `caja` de Pollo que había las
re-curó el operador.
Descartado: recortar la lista igual «por prudencia» — no sacaría ni una fila
problemática, sacaría productos sanos y achicaría la meta de Fase 0 sin ganar
nada. Bajar el umbral de 70% — lo que §12.5 prohíbe explícitamente.
Consecuencia: la meta de Fase 0 mantiene sus 5 pilotos. El 60,7% no viene de
unidades inestables sino de que el extractor no propone **nada** en anuncios de
imagen (el OCR parte el nombre del producto y la línea de precio en renglones
distintos, obs. medida el 2026-08-31): eso es trabajo del asistente LLM, no de
alcance. La tarea se reabre solo si entra un piloto nuevo que se venda en caja
o paquete sin contenido declarado.

## 2026-09-03 — Sección «Hoy» en /control lee `requisitos.md`, no el GitHub Project
Contexto: se pidió una vista en `/control` que le diga al operador/desarrollador
en qué enfocar el día — una hora de curaduría, el resto en diseño o
implementación. La fuente obvia para «qué falta» es el GitHub Project #1, pero
`/control` tiene la convención de no depender de un servicio externo al cargar
(mismo motivo por el que no consulta elTOQUE en vivo, ver `status.py`), y la
app no tiene ninguna integración con la API de GitHub.
Decisión: el bloque de diseño/implementación parsea `docs/requisitos.md`
(tabla «Estado de los 16 módulos»), documento local ya versionado, en vez de
llamar al Project. Módulo nuevo `requisitos_status.py`, mismo criterio
fail-soft que `read_h0_runs`. Marca «accionable hoy» al módulo que no está
terminado (`Estado` no empieza con «Construido» ni es «Fase 0 completa») y no
tiene bloqueo (`Bloqueado por` = «—») — con la tabla actual, dos: Validación
(05) y Antimanipulación C1–C4 (07).
Descartado: enlazar al GitHub Project en vez de parsear nada — es más simple
pero no «asiste», solo redirige. Consultar la API de GitHub en cada carga de
`/control` — dependencia externa que la vista ya evita a propósito en todo lo
demás.
Consecuencia: `docs/requisitos.md` y el GitHub Project quedan como dos cortes
del mismo sistema (uno por módulo de pipeline, el otro por área) que pueden
desincronizarse — confirmado al escribir esto: el módulo 03 del documento cita
datos del 31/8 y no las tareas 2 y 3 de extracción de esta semana. Mantener
`requisitos.md` al día queda como slice aparte, todavía sin diseñar.

## 2026-09-04 — El formato de venta es la identidad de la serie de precios
Contexto: la Matriz de `/control` publicaba una mediana por unidad canónica
(«310 CUP/lb»). El operador la rechazó explicándosela a otra persona, y la
evidencia lo respalda: **ninguna de las 39 filas de Pollo tiene `quantity = 1`**
—las cantidades reales son 4, 10, 33, 40 lb y 10 kg—, así que ese número es una
división que nadie pagó. El hogar no compra un kilo normalizado: compra el
paquete, la caja, o la libra suelta.
Decisión: la serie pasa a ser **producto × formato × provincia**, donde un
formato es la terna `(envase, cantidad, unidad)` de `product_formats`. El
tamaño es identidad, no variación: la caja de 40 lb y la de 33 lb son series
distintas, y una de 80 entra sola el día que aparezca. El titular publicado es
el precio del formato; el precio por unidad base **no se borra**, se degrada de
eje a **comparador** entre formatos. `sale_format` sobrevive como eje
perpendicular —el escalón de volumen sobre el mismo formato— y por eso el par
minorista/mayorista de `matrix.py` ahora se arma por formato y ya no por
unidad. Reemplaza la regla de `cells.py` que excluía el envase de la clave
(«normalizado por cantidad, el envase dejó de ser un eje que separe series») y
la parte del 2026-08-12 que dejó `packaging` como texto libre.
Descartado: tratar el tamaño como variación de un default declarado por
producto — no tiene forma de representar una caja de 80 lb sin ensuciar la
serie de 40 o reescribir la histórica en silencio. Mantener `packaging` como
texto libre: 266 filas ya habían producido 14 valores con `Caja`/`caja`,
`Contenedor`/`contenedor` y un pedazo de anuncio guardado como envase.
Consecuencia: `contenedor` y `paca` quedan fuera del vocabulario —vasijas de
contenido variable que transportan otro formato son logística, no presentación—
y sus 11 filas se remapean al formato que llevan debajo, sin perder ninguna
observación. Partir por formato divide la densidad: 109 de 266 filas quedan sin
formato tras la migración `0019`, y las que sí lo tienen se reparten en 43
series. No se introduce ningún umbral por eso (§12.6): «muestra en formación»
sigue siendo incondicional en la vista pública y `/control` sigue sin veredicto.
Queda pendiente separar por formato también `compute_retail_ranges`, que hoy
colapsa el envase por la decisión del 2026-08-12.

## 2026-09-04 — El rango minorista público se separa por formato
Contexto: la entrada del 2026-09-04 sobre formatos separó `compute_cells`
(Matriz interna) pero dejó `compute_retail_ranges` —que alimenta las tarjetas
públicas— colapsando el envase, por la decisión del 2026-08-12. Ahí vive la
pregunta original del operador: no «¿cómo se movió Arroz?» sino «¿cómo se movió
el paquete de arroz?».
Decisión: `RetailObservation` y `RetailRange` llevan la terna del formato;
`compute_retail_ranges` mete `format_id` en su clave de agrupación. El titular
de la tarjeta pública pasa a ser el precio del formato («16.685 CUP el saco de
25 kg»), con el precio por unidad base al lado como comparador. El cálculo del
precio del formato se factoriza en `_FormatPriced`, un mixin que comparten
`Cell` y `RetailRange`. Supersede la parte de la decisión del 2026-08-12 que
decía «el producto es uno solo aunque el envase varíe» para esta vista.
Descartado: mantener la vista pública colapsando el formato mientras la interna
lo separa — dejaría al dashboard respondiendo una pregunta distinta de la que
el operador pidió, y las dos mitades del módulo divergiendo otra vez.
Consecuencia: las tarjetas se multiplican por formato (4 rangos → 44 sobre el
corpus actual), muchas con dos o tres observaciones. El rótulo «muestra en
formación» ya cubre eso sin condición y no cambia. `compute_retail_observations`
—que también alimenta el gráfico de evolución— lleva ahora la terna del formato
pero el gráfico sigue dibujando la serie por unidad; separarlo por formato
queda pendiente si el operador lo pide.

## 2026-09-04 — Borrar un formato con observaciones las deja sin formato, en vez de bloquear
Contexto: `/catalog` no tenía forma de corregir un formato de venta ya
declarado. Editar faltaba directamente, y borrar (design.md D7) bloqueaba
cualquier formato con al menos una observación — pensado contra el retiro por
desuso («la caja de 40 lb dejó de traerse este mes»), pero también bloqueaba
el caso real: un formato al que la curaduría le asignó observaciones que no
le correspondían, y que el operador necesita poder deshacer.
Decisión: se separan los dos casos por si hay historial o no.
- **Editar** (`POST /catalog/formats/{id}/edit`, nuevo) solo si el formato
  tiene cero observaciones: `quantity`/`unit` de cada fila de precio se
  denormalizan DESDE el formato al momento de curar (design.md D8), así que
  reescribir el triple con historial ya cargado desalinearía esas copias en
  silencio. Sin observaciones no hay nada que desalinear.
- **Borrar** deja de bloquearse por tener observaciones. Sus filas de
  `prices_history` pasan a `format_id = NULL` —«sin formato», un estado que
  toda la agregación ya soporta (`cells.py` agrupa por `format_id` y trata
  `NULL` como su propia celda, igual que una fila sin envase capturado— y
  después se borra el formato. El operador recura cada fila huérfana desde
  `/curation/history`, reasignándole el formato correcto con el mismo flujo
  que ya usa `/curation/{id}/edit`.
Descartado: mantener editar y borrar con la misma regla que antes (bloquear
con cualquier observación). Habría dejado sin arreglo el caso que lo motivó
—un formato contaminado por una mala asignación— porque no hay forma de
reasignar observaciones ya atadas sin antes soltarlas de ese formato.
También se descartó que borrar reasigne solo o intente adivinar el formato
correcto: D4 ya estableció que el sistema nunca adivina un dato que el
operador no confirmó.
Consecuencia: reemplaza la parte de design.md D7 que decía que un formato con
historial no se retira nunca — ahora no se retira *el historial* (las
observaciones sobreviven, solo pierden el vínculo), pero el formato sí puede
borrarse. La ventana entre borrar y recurar dinamiza filas ya publicadas: caen
a su propia celda de «sin formato» hasta que el operador las corrija.

## 2026-09-04 — Bug: editar a CUP con formato nuevo perdía la corrección en silencio
Contexto: dos corridas de H0 seguidas dieron un número idéntico, campo por
campo, pese a que el operador había corregido ~81 anuncios en `/curation`
entre una y otra medición (10:35-14:14). Los logs del servidor
(`journalctl -u saimc.service`) mostraron la causa: de esos 81, **3 anuncios
reventaron con 500 en cada intento** (2-3 reintentos cada uno, siempre el
mismo error) y **1 nunca superó el 422**. Las correcciones de esos 4 nunca
llegaron a guardarse.
El 500 era `psycopg.errors.CheckViolation` contra
`ck_prices_history_exchange_rate_cup` (`currency <> 'CUP' OR exchange_rate IS
NULL`). Causa exacta en `curation.py::submit_edit`: `existing.currency` se
asignaba a `"CUP"` y varias líneas después, todavía con el `exchange_rate`
viejo sin limpiar, se llamaba a `_resolve_format_id` → `get_or_create_format`
— que hace un `SELECT` y, si el formato no existe, un `session.flush()`
explícito. Ambos flushean TODA la sesión, `existing` incluido, en ese estado a
medio actualizar: `currency='CUP'` + `exchange_rate` no nulo a la vez.
Decisión: dos cambios, uno necesario y uno defensivo.
- `curation.py`: la resolución de tasa (`apply_resolution`, el bloque
  `if currency == CUP / elif changed`) se mueve ANTES de
  `_resolve_format_id`, para que `existing` nunca llegue a una llamada que
  toca la base en un estado inconsistente. Es el fix real.
- `formats.py::get_or_create_format`: el `SELECT` de existencia se envuelve
  en `session.no_autoflush` — no alcanza solo (el `session.flush()` explícito
  de más abajo igual flushea todo), pero cierra la clase de bug más chica
  del autoflush implícito para cualquier llamador futuro, no solo este.
Descartado: arreglarlo solo con `no_autoflush` en `formats.py`. Se probó
primero y el mismo test seguía reventando, ahora en el `flush()` explícito de
crear el formato — confirmó que el orden de asignación en `curation.py` era
la causa real, no un detalle del autoflush.
Consecuencia: **3 anuncios de producción** (`6879f132-05af-4f17-b03e-6b72df83c883`,
`df7170ea-a0f7-450b-bb2d-491e8de7bddf`, `8a64f944-48ef-498a-b7cd-6c3ef72b7b4d`)
y el de 422 (`2f43c911-3826-4487-83de-50b4e3a2149a`) siguen con su dato viejo:
el operador tiene que recorregirlos una vez que el fix esté desplegado. TDD:
RED con el `IntegrityError` reproducido en SQLite antes del fix, GREEN después
(`tests/test_curation.py::TestConversionAlEditar::test_cambiar_a_cup_con_formato_nuevo_no_revienta_por_autoflush`).

## 2026-09-04 — Los tokens Material 3 tienen fuente única y una herramienta que los proyecta
Contexto: el operador pidió alinear todo Guani bajo Material Design desde la
Fase 0. Al auditar el estado apareció que Material 3 ya ES el sistema del
proyecto —47 tokens, ~60 componentes, fuentes e iconos vendorizados, cero
Bootstrap— pero derivado: `saimc-public.css` tenía 13 tokens **copiados a mano**
desde `saimc-material.css`, y las plantillas citaban 20 veces
`var(--m3-algo, #literal)` con fallbacks que ya no eran los colores del
proyecto. Cuatro de esos literales estaban mal: `#006a60` contra el
`--m3-primary` real `#00696e`, `#79747e` contra `--m3-outline` `#6f7979`, y
`#cac4d0` (×5) contra `--m3-outline-variant` `#c4cfcf` —ese `#cac4d0` es del
esquema violáceo BASE de Material, ni siquiera de este proyecto—. Peor: dos
tokens se citaban **sin existir en ninguna hoja**, así que el color que se veía
era siempre el fallback: `--m3-tertiary` (marca de campo asistido en
`/curation`) y `--m3-surface-variant` (`code`/`pre`/`th` de `/docs`).
Decisión: `src/saimc/web/design_tokens.css` es la fuente única —no se sirve,
vive fuera de `static/`— y `tools/build_css_tokens.py` copia su bloque `:root`
dentro de las dos hojas, entre centinelas, pelando los comentarios. Se definen
los dos tokens que faltaban (`--m3-tertiary: #4b607c`, un azul apagado fuera
del eje rojo/verde por la misma disciplina que los colores de provincia;
`--m3-surface-variant: #dbe5e5`, neutral de la familia teal) y se eliminan los
20 fallbacks literales de las plantillas. La hoja pública recibe el bloque
COMPLETO: **esto enmienda la entrada del split de `saimc-public.css`**, cuyo
criterio de recorte sigue vigente para los COMPONENTES —de ahí salían los
~22 KB— pero deja de aplicarse a los tokens.
Descartado: (a) un `tokens.css` aparte enlazado por los dos shells, que es la
solución obvia y suma un round-trip a cada vista contra la conexión lenta e
intermitente que fija CLAUDE.md; (b) solo un test que compare las dos listas a
mano, que hace verificable la duplicación pero no la elimina, y la lista va a
crecer cuando la home pública deje su sistema paralelo `--ph-*`; (c) corregir
los fallbacks en vez de sacarlos: el shell garantiza la hoja, así que el
literal nunca se usa y lo único que hace es envejecer sin que nadie lo note.
Consecuencia: 1308 tests verdes (+4). Siguen los 2 de `test_public_filter.py`
que envejecieron por fechas fijas en su seed, verificados idénticos contra el
código sin modificar. Los cuatro casos nuevos son tripwires probados en rojo
uno por uno, con el patrón del manifiesto de iconos (2026-08-30): nombran el
token exacto y el comando a correr. Cambio visible en pantalla: solo los dos
tokens que antes no existían —la marca de campo asistido pasa de violácea a
azul, y los bloques de código de `/docs` de gris azulado a gris teal—; los
otros 18 fallbacks nunca se usaban. Costo medido: la hoja pública pasa de
1.129 a 1.790 B gzipeados, +661 B por visita nueva. Queda abierto el slice A2
(escala de spacing 4dp y unificar los cuatro breakpoints), y detrás la
repatriación de los componentes hechos a mano (`.hs-*` de `/control`, `.ph-*`
de la home pública, `.docs-*`/`.doc-*` de `/docs`), que son ~337 líneas de CSS
viviendo dentro de plantillas.

## 2026-09-04 — Escala de espaciado 4dp, y por qué los breakpoints NO se mueven a los de Material
Contexto: segunda mitad del trabajo de alinear Guani bajo Material 3 (slice A2,
después de la fuente única de tokens de `d2fd439`). Dos cosas sin sistema: el
espaciado, escrito como literales px por todas partes, y cuatro cortes
responsive distintos —640 en la home pública, 720 y 900 en la hoja del
operador, 900 otra vez en `/docs`— sin escala ni nada que impidiera un quinto.
Decisión, en tres partes:
(a) Se define la escala de 4dp en `design_tokens.css` (`--m3-space-N`, donde N
es el multiplicador: `--m3-space-6` son 24px). Cubre 4-32 más los dos saltos
grandes que la hoja usaba, 56 y 96. Se sustituyeron **96 literales** en las dos
hojas, restringido a `padding`/`margin`/`gap`: `top`/`left`/`right`/`bottom`
quedan afuera porque son posicionamiento —`top: 56px` es el alto de la topnav,
no un espacio, y tokenizarlo mentiría—. Cambio visual: **ninguno, probado**;
revertir los tokens a px devuelve las hojas de `d2fd439` byte a byte.
(b) **Los 41 literales fuera de grilla que quedan (6, 10, 13, 14, 18, 22, 26px)
NO se snapearon.** Unos son ajustes ópticos de 1-3px y otros son la densidad de
`/curation`, que el operador usa una hora por día: moverlos cambia cuánta
información entra en pantalla sin que nadie lo haya pedido. La grilla es el
default para lo nuevo, no un decreto retroactivo.
(c) **Los breakpoints se quedan en 640/720/900 y NO pasan a las window size
classes de Material (600/840)**, que era lo esperable habiendo decidido alinear
todo bajo Material. Se documentan los tres en `design_tokens.css` con el layout
al que responde cada uno, y un test impide que aparezca un cuarto.
Descartado: renumerar a 600/840. Medido, rompe dos cosas ya resueltas. **720 →
600** devuelve el bug del 2026-09-02 en el teléfono EN HORIZONTAL: acostado
mide ~640-844 de ancho por ~360-390 de alto, con 720 cae en compacto y con 600
no, así que vuelve a comerse los ~230px de cromo fijo sobre 360px de alto.
**640 → 600** deja `.ph-cards` en `repeat(3, 1fr)` dentro de 600px: ~178px por
tarjeta de precio, con su barra de rango adentro. Los tres números están atados
a layouts que todavía no son fluidos; converger a Material exige antes que las
grillas dejen de tener columnas fijas y que la topnav sobreviva a 600px con las
etiquetas puestas. Eso es trabajo de la repatriación de componentes, no de
renumerar cortes. Descartado también tokenizar los breakpoints: no se puede,
`@media (max-width: var(--x))` no existe —las media queries se evalúan antes de
que las custom properties tengan valor—, así que la escala se hace cumplir
desde un test y no desde el CSS.
Consecuencia: 1311 tests verdes (+3). Siguen los 2 de `test_public_filter.py`,
preexistentes. Los tres casos nuevos son tripwires probados en rojo: un corte
de fantasía, un espaciado en grilla escrito como literal, y la escala del test
separándose de la de la fuente. El CSS que vive dentro de las plantillas
(~337 líneas) queda afuera de las dos reglas a propósito: sale de ahí cuando se
repatrien los componentes. Con esto A queda cerrado; sigue la repatriación
(`.hs-*` de `/control`, `.ph-*` de la home pública, `.docs-*`/`.doc-*`), que es
la que además destraba la convergencia a 600/840.

## 2026-09-04 — Repatriación, 1 de 4: el índice de /docs viste con la hoja compartida
Contexto: primer paso de la repatriación de componentes, el trabajo que queda
después de cerrar el slice A (tokens y escalas). La auditoría del estado visual
contó ~337 líneas de CSS viviendo dentro de plantillas, casi todas
reimplementando componentes que la hoja ya tiene. `docs_index.html` era el caso
más limpio: 35 líneas de `<style>`, 17 reglas, y seis colores fijos para los
tres badges de vigencia (`#dcede5`/`#1b6b4f` rige, `#eceff3`/`#5a6472`
congelado, `#f6ebcf`/`#8a6212` interno) que no salían de ninguna paleta del
proyecto, así que el mismo estado se veía de un color en `/docs` y de otro en
el resto de la app.
Decisión: se repatrían el marco y el badge, no la composición.
- A `saimc-material.css`: `.m3-card-outlined` (variante de borde, que Material
  3 define junto a la elevada), `.m3-card-link` (para envolver una tarjeta
  entera en un `<a>`, que hasta ahora no se hacía) y `.m3-badge-mono` +
  `.m3-badge-ok/-warn/-neutral`, con **los mismos tokens contenedor que
  `.m3-chip-ok/-warn/-neutral`**: un estado se ve igual sea chip o badge.
- El `<style>` de la plantilla baja de 35 a 21 líneas, de 17 reglas a 10 y de
  seis colores fijos a cero. Lo que queda es composición interna de la tarjeta
  —el flex del título con el badge, la fila de metadatos—, que no es un
  componente y no tiene por qué subir a la hoja.
- El buscador pasa a `.m3-input`, con un override de una línea: `.m3-input` es
  transparente porque nació dentro de tarjetas, y acá vive sobre el fondo de
  página.
- El JS del filtro dejaba de encontrar las tarjetas al cambiar la clase, así
  que ahora selecciona por `[data-buscar]`: el atributo es el contrato real del
  filtro, la clase era presentación.
Descartado: (a) mover el `<style>` entero a la hoja compartida, que sacaría el
CSS de la plantilla sin resolver la duplicación —el objetivo es que haya menos
CSS, no que esté en otro archivo—; (b) preservar los colores viejos como
variantes nuevas, que es exactamente la segunda paleta que el slice A vino a
eliminar; (c) reusar `.m3-chip` para el badge: el chip tiene borde, más padding
y fuente de texto, y acá hace falta una etiqueta compacta y monoespaciada.
Consecuencia: 1314 tests verdes (+3). Siguen los 2 de `test_public_filter.py`,
preexistentes. Cambio visible en `/docs` —a diferencia de A1 y A2, que fueron
invisibles a propósito—: los tres badges pasan a los colores canónicos de su
estado (misma familia, distinto tono) y la tarjeta gana el ritmo de
`.m3-card-pad` (20/24px contra los 16/18px que tenía a mano). Es el precio de
unificar y era el punto del pedido. Se actualizó también el fundamento de
`TestBuscadorFunciona`, que razonaba sobre `.docs-card` declarando
`display: block`: ahora eso lo hace `.m3-card-link`, y el argumento del
`[hidden] { display: none !important }` sigue en pie con el nombre correcto.
Quedan 3 de 4: `.doc-*` de `docs_view.html` (74 líneas, con TOC y tipografía
de markdown que son genuinamente propias), `.hs-*` de `/control` (46 líneas,
la tira de salud) y `.ph-*` de la home pública (181 líneas, un sistema
paralelo completo y el único que toca una vista con usuarios reales).

## 2026-09-04 — Repatriación, 2 de 4: el lector de /docs, y qué NO era duplicado
Contexto: segundo paso de la repatriación. `docs_view.html` era el bloque más
grande después de la home pública —74 líneas de `<style>`, 37 reglas— y la
auditoría lo había marcado casi entero como duplicado. Al mirarlo de cerca, no
lo era: la mayor parte es tipografía del markdown renderizado, que llega sin
clases desde `marked` y por eso se estiliza con selectores de descendencia.
Decisión: se repatrían tres cosas y se dejan explícitamente las demás.
- `.doc-actions a/button` → `.m3-btn m3-btn-outlined`. Eran un pill con borde
  reimplementado a mano.
- `.doc-error` → `.m3-banner m3-banner-error m3-banner-block`, con dos colores
  fijos menos (`#96263a`, `#f6dce0`, ninguno de la paleta). La variante
  `.m3-banner-block` es nueva y necesaria: `.m3-banner` es `display: flex`
  porque casi siempre es icono + rótulo, y con una frase que lleva un enlace
  adentro el flex parte la oración en items sueltos separados por el `gap`.
- `.doc-table-scroll` → `.m3-table-scroll`, que era idéntico salvo el margen.
**Lo que no se repatrió, y por qué:** (a) el índice lateral sticky y la hoja
`@media print`, que no existen en ninguna otra vista; (b) la tipografía del
markdown, por lo mismo; (c) **la tabla del markdown NO pasa a `.m3-table`**,
aunque la auditoría decía que la duplicaba. `.m3-table` es tabla de datos
—encabezado en versalitas, `nowrap`, sin bordes de celda, hover de fila— y las
de `requisitos.md` son grillas de referencia densas que necesitan borde en cada
celda y texto que envuelva. Comparten el nombre, no el problema; forzarlas
habría empeorado el documento que más se lee.
Descartado: inventar un `.m3-btn-sm` para que los botones del lector queden del
tamaño que tenían (7px/14px contra 10px/24px). El punto del trabajo es que un
botón se vea igual en toda la app; agregar una variante para conservar la
diferencia es conservar el problema con otro nombre. Si el tamaño estándar
molesta en el uso, se decide como cambio de diseño, no como excepción de una
vista.
Consecuencia: 1318 tests verdes (+4). El `<style>` baja de 66 a 56 líneas de
CSS real y de 3 colores fijos a 0 — mucho menos que en `docs_index` (35→21),
justamente porque acá casi todo era propio. El total de líneas del bloque no
baja: subió de 74 a 75 porque se documentó qué quedó afuera y por qué, que es
más valioso que el renglón ahorrado.
El test nuevo que importa es `TestClasesDelJavascriptExisten`: verifica que toda
clase asignada con `className = "..."` en las plantillas de `/docs` esté
definida en la hoja o en el `<style>` local. Es la trampa específica de
repatriar —se renombra el CSS y el HTML, y queda vieja la del script, que no
falla: solo deja de verse bien, sin error en consola— y estuvo a punto de pasar
dos veces seguidas, con el filtro del índice y con el envoltorio de tablas.
Quedan 2 de 4: `.hs-*` de `/control` (46 líneas, la tira de salud) y `.ph-*` de
la home pública (181 líneas, sistema paralelo completo y la única vista con
usuarios reales).

## 2026-09-04 — Repatriación, 3 de 4: la tira de salud de /control, y cuatro tokens que faltaban
Contexto: tercer paso de la repatriación, y el más delicado hasta acá: la tira
de salud es lo que el operador mira todos los días para saber si algo se rompió
(docs/decisions.md, 2026-09-02). Vivía entera en el `<style>` de
`control.html`: 49 líneas y **ocho colores fijos** —`#1b6b4f`, `#8a6212`,
`#a4232b`, `#9aa0a6` para punto y borde; `#fdf8ec`, `#fdeced` para el fondo;
`#7a1a20`, `#6b4c0e` para el texto del banner—. Ninguno salía de la paleta, así
que el rojo de «algo está roto» no era el rojo del resto de la aplicación.
Decisión: el componente pasa a `.m3-health-*` en `saimc-material.css`, con
modificadores `is-ok/-warn/-down/-off` (la convención de la hoja, como
`.is-active` o `.is-hero`), y los colores pasan a tokens.
**Cuatro tokens nuevos, cada uno tapando un hueco real de la paleta:**
- `--m3-success: #1b6b4f`. Faltaba por asimetría: `--m3-error` y `--m3-warn`
  existían como base y success solo tenía `-container`. Por eso la tira se
  pintaba el verde a mano.
- `--m3-warn-strong: #8a6212`. `--m3-warn` (#e0a700) es ámbar brillante y
  funciona como acento grueso —el borde superior de `.m3-gap`, el izquierdo de
  `.m3-row-card.is-review`— pero en un punto de 9px sobre blanco no se lee. Son
  dos roles distintos, no un duplicado; queda anotado por si conviene unificar
  más adelante.
- `--m3-warn-wash: #fdf8ec` y `--m3-error-wash: #fdeced`. Tinte pálido para
  pintar una tarjeta entera; `-container` está calibrado para chips y banners
  chicos y como fondo de tarjeta grita. Mismo escalón `-wash` que ya usaban los
  colores de provincia.
Se conservan las tres reglas del diseño original, anotadas ahora en la hoja
para que sobrevivan al próximo refactor: el color no es la única señal, `off`
NO es `down`, y cada tarjeta dice qué hacer y enlaza al detalle.
Descartado: (a) snapear los colores a los `-container` existentes, que habría
hecho la tarjeta de «roto» bastante más estridente sin que nadie lo pidiera;
(b) usar `--m3-mono` en `.m3-health-value` para alinearlo con
`.m3-metric-value`: acá el valor es una frase («conectado», «hace 2 días»), no
una cifra que haya que alinear en columna; (c) dejar el componente en la
plantilla por ser de una sola vista — `.m3-curation` y `.m3-matrix-*` también
lo son y viven en la hoja: la convención del proyecto es esa.
Consecuencia: 1322 tests verdes (+4). El `<style>` de `control.html`
desaparece: la plantilla queda sin CSS propio. Verificado declaración por
declaración contra la versión vieja con tokens expandidos: **todo el espaciado
quedó idéntico** y los únicos cambios son cuatro colores, todos dentro de su
familia — `down` de `#a4232b` a `#ba1a1a`, `off` de `#9aa0a6` a `#8a9494`, y el
texto de los dos banners a `--m3-on-error-container` / `--m3-on-warn-container`,
más oscuros que antes. Los verdes y ámbares del punto y el borde no se movieron.
`TestClasesDelJavascriptExisten` se movió de `test_docs.py` a
`test_static_assets.py` y ahora corre sobre **todas** las plantillas, no solo
las de `/docs`: el riesgo de dejar una clase huérfana en un `className` es de
cualquier vista con JS. Hoy el proyecto pasa limpio.
Queda 1 de 4: `.ph-*` de la home pública (181 líneas, sistema paralelo completo
con Alice serif). Es la única que toca una vista con usuarios reales y la que
destraba la convergencia de breakpoints a 600/840.

## 2026-09-04 — Repatriación, 4 de 4: la home pública, y el azul se queda
Contexto: último paso. La auditoría del estado visual había marcado
`public_home.html` como «un sistema paralelo completo» de 181 líneas. Al
leerlo entero, no lo era: **usaba 30 veces `var(--m3-*)` contra 36 veces
`var(--ph-*)`**, y dos de sus tokens —`--ph-hero-bg`, `--ph-hero-line`— eran
alias literales de `var(--m3-surface)` y `var(--m3-outline-variant)`. La
divergencia real se reducía a una fuente (Alice), cuatro colores, una sombra y
tres radios; de esos, `--ph-neutral` y `--ph-radius-md` tenían **cero usos**.
Decisión del operador, preguntada explícitamente: **el azul `#05668d` de la
capa pública se queda** como color de marca, distinto del teal `--m3-primary`
del operador. El visitante ve azul y el operador teal, a propósito. Material 3
no exige un solo acento: exige que los que haya estén en el sistema. Así que no
se unifica el color, se lo promueve a token.
Se hicieron tres pasos, uno por commit:
1. **Grillas fluidas** (`79a7466`). `.ph-cards` y `.ph-series` eran
   `repeat(3, 1fr)` con un corte duro en 640px, y eso dejaba una banda mala que
   nadie había medido: **entre 700 y 900px de ancho entraban tres tarjetas de
   211 a 278px**, con la cifra de precio adentro. Con `auto-fit` +
   `minmax(18rem, 1fr)`: 1200px da 3 columnas de 372px (idéntico), 700-900px da
   2 de 325-425px (mejor), 360-600px da 1 (idéntico). Con las tres grillas
   fluidas el `@media (max-width: 640px)` se quedó sin trabajo y **la escala de
   breakpoints bajó de tres cortes a dos**. Es el camino para los otros dos: un
   corte responsive es la confesión de que el layout no sabe acomodarse.
2. **Tokens fuera del HTML** (`d7c0c33`). El `:root` con los `--ph-*` vivía
   dentro del `<style>`; pasa a `design_tokens.css`. Ganan nombre propio
   `--ph-on-accent`, `--ph-accent-wash` y `--ph-accent-2-line`, que eran un
   `#fff`, un `rgba()` y un `#015a63`. Cuatro hairlines `rgba(0,0,0,.06-.18)`
   pasan a `--m3-divider` / `--m3-outline-variant`, que es lo que el resto de
   la app usa para lo mismo. Los dos alias del hero se eliminan.
3. **Superficies consolidadas**. `.ph-card`, `.ph-serie` y `.ph-table-card`
   declaraban los mismos tres valores (radio + fondo + sombra); `.ph-stat` y
   `.ph-panel`, otros tres. Quedan `.ph-surface` (elevada) y `.ph-inset`
   (hundida), y cada clase conserva solo su padding. Y `.m3-coverage` pasa a
   `.ph-coverage`: llevaba prefijo de clase compartida y se definía dentro del
   HTML, así que quien la buscara en la hoja no encontraba nada.
Descartado, y esto corrige mi propia estimación: en el plan dije que caerían
«entre 20 y 25 reglas». **Cayeron 2.** No se forzaron `.ph-filter a` →
`.m3-chip-btn` (el público es píldora de 999px y el del operador un rectángulo
de 8px), `.ph-stat` → `.m3-stat` (el valor es Alice de 28px, no Roboto Mono de
20px), ni `.ph-conc-track` → `.m3-progress-thin` (habría hecho falta anular su
`margin` y su `max-width`, dos líneas para ahorrar dos). Igual que en
`docs_view`, la mayor parte de lo que la auditoría llamó duplicación es
composición propia: el mapa, el gráfico SVG, la grilla del hero, el interior de
la tarjeta.
Consecuencia: 1323 tests verdes (+1). El `<style>` baja de 147 a 129 líneas de
CSS real, de **16 colores fijos a 0** y de 1 media query a 0. Verificado
declaración por declaración con tokens expandidos: los cinco elementos que
cambiaron de clase resuelven **exactamente lo mismo** que antes; la única
diferencia es que `#fff` ahora se escribe `#ffffff`.
Con esto la repatriación está completa, y el resultado se fija en un tripwire
nuevo: `TestNingunColorFijoEnLasPlantillas` recorre **todas** las plantillas y
falla si alguna vuelve a declarar un color a mano. La auditoría había contado
**60**; hoy hay **cero** en todo el proyecto.

## 2026-09-04 — Los seeds de la vista pública se anclan a hoy; la suite queda entera en verde
Contexto: `test_public_filter.py` arrastraba **dos fallos desde al menos el
2026-08-30**, anotados en varias entradas como «envejecieron por fechas fijas
en su seed» y nunca atacados. Al mirarlos: los seeds sembraban anuncios con
fechas absolutas de agosto de 2026 (`date(2026, 8, 18)` y vecinas), y la vista
filtra por `?dias=N` contra `date.today()`. Con el reloj en el 4 de septiembre,
una ventana de 7 días ya no alcanzaba a agosto, la página renderizaba vacía y
los dos casos que piden `dias=7` fallaban — uno buscando el enlace al CSV que
no se dibujaba, el otro reventando con `IndexError` al partir por una tabla que
no estaba.
Decisión: se agrega el helper `_hace(dias)` —`date.today() - timedelta(...)`—
y **las 27 fechas absolutas de los cinco seeds pasan a offsets relativos**,
conservando orden y espaciado exactos: 18/08 → hoy−6, 19/08 → hoy−5, 20/08 →
hoy−4, 21/08 → hoy−3, 22/08 → hoy−2. Todos los offsets quedan entre 2 y 6, o
sea dentro de la ventana más chica que ofrece la vista (7) y de la default
(30). Es prueba por construcción: los dos lados —seed y filtro— cuelgan de
`date.today()`, así que no queda dependencia del calendario.
No se arreglaron solo los dos casos rojos: los 27 eran el mismo bug latente y
el resto iba a caer igual al correrse el reloj un poco más.
Descartado: (a) fijar la fecha del sistema en los tests con una librería tipo
`freezegun`, que mete una dependencia para un problema que se resuelve con una
resta; (b) ampliar la ventana o relajar los asserts, que haría pasar los tests
sin que prueben lo que dicen probar; (c) tocar las cuatro fechas absolutas de
`TestFrescuraDelDato`, que son **deliberadas**: ahí el assert es el string
formateado en pantalla (`«Datos verificados hasta el 15/08/2026»`) y el punto
de la prueba es justamente que esa línea NO depende de la ventana. Queda
anotado en el docstring de `_hace()` para que nadie las «arregle» después.
Consecuencia: **1325 tests verdes, cero fallos** — la suite queda entera en
verde por primera vez desde que aparecieron estos dos. `test_respeta_la_ventana`
sigue probando la exclusión de verdad, porque siembra su propia fila a −20 días
con `date.today()` en vez de depender del seed. Se corrigió además un comentario
que había caducado con el mismo problema: decía «hoy es 2026-08-26».

## 2026-09-04 — README al día: asistente LLM, formatos de venta, Swagger apagado
Contexto: revisión de fin de semana del README contra el código real. Tres
huecos, cada uno de un momento distinto:
- **`SAIMC_ANTHROPIC_API_KEY` no existía en `env.example` ni en el README**,
  desde que el asistente de extracción por LLM se sumó a `/curation` el
  2026-09-01. La variable ya gobernaba si el botón aparece (`assist_available
  = bool(settings.anthropic_api_key)` en `curation.py`); solo faltaba
  documentarla.
- **La sección de `/curation` no mencionaba el asistente en absoluto.**
- **El README seguía diciendo que `/docs` era Swagger** («La API completa
  (Swagger) queda en `http://127.0.0.1:8000/docs`»), afirmación falsa desde el
  2026-08-30: `docs_url=None` en `app.py`, y `/docs` es hoy el gestor de
  documentos del repositorio. Quien siguiera el README a partir de esa fecha
  llegaba a una vista que no tenía nada que ver con lo prometido.
- **`/catalog` no mencionaba los formatos de venta**, la cadena de 8 commits
  que cambió qué identifica una serie de precios (2026-09-04, «El formato de
  venta es la identidad de la serie»).
- **`/control` no mencionaba la franja de estado** que abre la vista desde el
  2026-09-02, ni que la matriz ahora es producto × formato × provincia.
Decisión: se agregan las cinco piezas, con el mismo estilo que ya tenía el
README —una o dos frases, motivo y cita a `docs/decisions.md`—, sin reescribir
lo que ya estaba bien. `env.example` gana el bloque de
`SAIMC_ANTHROPIC_API_KEY` con el mismo formato que las demás: motivo, qué pasa
sin ella, dónde está el costo documentado.
No hay convención de «actualizar el README cada semana» en `CLAUDE.md` ni acá:
es reactivo, se corrige cuando una revisión encuentra una afirmación vencida o
un flujo real sin documentar. Esta entrada lo deja registrado por si conviene
formalizarlo más adelante.
Consecuencia: 1325 tests verdes, sin cambios — ningún test depende del texto
del README ni de `env.example`. Verificado contra el código, no supuesto:
`assist_available`, `docs_url=None` y `product_formats` en la plantilla de
catálogo, los tres leídos antes de escribir la frase que los describe.

## 2026-09-05 — La barra de rango de la matriz habla la escala del titular
Contexto: el operador revisó una celda de «paquete de 1 kg · minorista» con
n=22 que titulaba 688,70 CUP por paquete y cuya barra decía 273,52–385,55.
Verificó los 22 anuncios a mano y ninguno traía esos números — la mediana sí
era correcta. No eran precios inventados: eran los extremos reales (603 y 850
CUP el paquete) expresados por libra, la unidad canónica del producto
(273,52 = 603 × 0,45359237). Cuando el titular pasó al precio de formato el
2026-09-04 (design.md D8), los extremos de la barra se quedaron en la unidad
base y la línea terminó mezclando dos escalas.
Decisión: en `control.html`, mínimo, máximo y el tooltip de la mediana usan
`format_minimum`/`format_maximum`/`format_median` cuando la celda tiene
formato, y los campos por unidad base cuando no. El comparador
«312,39/lb para comparar» del titular no se toca: ahí la unidad base es
justamente lo que se quiere leer (D8), y es el único lugar donde corresponde.
Descartado: mostrar las dos escalas en la barra —cuatro números por celda
para una vista que ya es densa— y mover la conversión a `build_range_bar`,
que no hace falta: el factor de formato es un multiplicador constante, así
que la amplitud (una razón) y la posición logarítmica de la mediana no
cambian. Solo cambian las etiquetas.
Consecuencia: 1329 tests verdes. La regla —lo que rodea al titular se expresa
en la escala del titular— queda cubierta por
`TestLaBarraDeRangoHablaLaUnidadDelTitular` en `tests/test_control.py`,
incluido el caso sin formato, que sigue en unidad base. La home pública ya
era consistente (`format_minimum_text or minimum_text`) y no se tocó.

## 2026-09-05 — La opción «total» de `price_basis` nombra el lote que cubre
Contexto: un anuncio de «caja de pollo de 40 lb a 24800» se curó con
`price_basis='unit'`. Con esa base el precio no se divide, así que 24800 se
leyó como precio por libra y la caja terminó valiendo 992.000 CUP en la
matriz. Verificado en el código, no supuesto: el error no vino de un default.
`_declared_price_basis` solo propone base cuando el anuncio la declara
explícitamente y la aritmética coincide, y el `<select>` arranca en «— sin
responder —». Lo que falló es la copia: con `unit='lb'` las dos opciones se
leían «Precio por libra» y «Precio total del anuncio», y ninguna nombra lo que
el anuncio cotiza —una caja de 40 lb—. Quien piensa en cajas lee «una caja es
una unidad» y elige la primera. Le pasa a toda fila con envase de caja o saco.
Decisión: la opción `total` nombra el lote concreto de la fila — «Precio de
las 40 libras juntas» — siempre que haya cantidad distinta de 1 y unidad. El
plural va como diccionario propio junto a `unit_words`, porque «unidad» →
«unidades» hace que cualquier regla sea más larga que la tabla. Los valores
enviados (`unit` / `total`) no cambian: es copia, no contrato.
El relabel en vivo de `_price_row_scripts.html` se extiende a esta opción y
pasa a depender también de la cantidad. Deja de ser puramente cosmético, y por
eso se documenta: en la cola la fila llega vacía y el operador escribe cantidad
y unidad justo antes de responder la base, así que sin el relabel la opción
diría «Precio total del anuncio» en el único momento en que se la lee. Sin JS
el server sigue rindiendo la copia correcta para toda fila que ya tenga sus
valores, y ninguna regla del servidor depende del texto (D8).
Descartado: nombrar el envase («Precio de la caja completa»). Obligaría a
conocer el género de cada uno —«la paquete» sale mal— y «a granel» no admite
artículo; es el mismo motivo por el que `control.html` escribe «por caja» y no
«la caja». Cantidad y unidad desambiguan igual y además cubren la fila sin
envase, que es la mayoría porque el campo es opcional y vive colapsado.
Consecuencia: 1335 tests verdes. Dos tests existentes que afirmaban el
`<option>` de unidad completo se actualizaron por el nuevo
`data-basis-label-plural`. Esto ataca la clase, no el caso: la fila de 992.000
CUP sigue mal en la base y se corrige a mano desde `/curation/{id}/edit`.
Queda pendiente el otro hallazgo del mismo reporte —`detect_basis_suspects` no
puede levantar una fila cuya celda tiene una sola observación, porque no hay
fila no ambigua contra la cual contrastar (`cells.py`)—, sin decidir.

## 2026-09-05 — La referencia de `detect_basis_suspects` se repliega a todas las provincias
Contexto: la caja de pollo de 40 lb a 24.800 marcada `price_basis='unit'` —la
que puso la caja en 992.000 CUP y que el operador encontró a ojo en la
matriz— nunca fue reportada por el detector. Verificado con un test que
reproduce el escenario antes de tocar el código: era la única fila de pollo en
Santiago, y sin ninguna fila no ambigua en esa provincia no había mediana
contra la cual contrastarla. El vecindario era producto × provincia × unidad,
así que el detector quedaba ciego justo en el producto de una sola carga, que
es el más frágil y el que nadie más va a revisar.
Decisión: cuando la provincia de la fila no aporta ninguna fila no ambigua, la
referencia se repliega a la mediana de todas las provincias del mismo producto
y unidad. Es un repliegue, no un reemplazo: donde la provincia ya tenía
vecindario, sigue mandando el suyo. La fila señalada lo declara en
`BasisSuspect.reference_from_all_provinces`, y `/control` lo escribe en la
propia fila («todas las provincias») además de en la caption — sin ese dato el
operador no puede descartar que lo que ve sea una diferencia regional real, y
quien juzga sigue siendo él.
No hay umbral nuevo y §12.6 sigue respetada: la regla es la misma —cuál de las
dos lecturas queda más cerca en proporción de la referencia— y lo único que
cambia es de dónde sale la mediana. Se sostiene porque las dos escalas no se
parecen: el error que se busca es del orden del factor de cantidad o de
conversión (×40, ×99) mientras que la diferencia real de precio entre
provincias es de ×1,5 como mucho.
Descartado: usar siempre todas las provincias, que tiraría la señal local
donde sí existe; y detectar la fila por lo absurdo del precio de formato, que
exige exactamente el corte de precio que §12.6 prohíbe. Queda anotada, sin
decidir, una tercera idea que tampoco necesita umbral: comparar el precio
publicado contra los números que aparecen en el texto crudo del anuncio —para
esta fila, 24.800 está en el anuncio y 992.000 no—.
Consecuencia: 1342 tests verdes. El caso Picadillo del 2026-08-22 queda
intacto y con test propio: un producto sin filas no ambiguas en NINGUNA
provincia sigue sin veredicto. Esto cierra el segundo hallazgo del reporte del
2026-09-05; el primero es la entrada anterior sobre la copia de `price_basis`.

## 2026-09-05 — `price_basis` se propone también por marcador de envase
Contexto: cerrados los dos primeros hallazgos del reporte de la caja de 40 lb,
quedaba la idea de usar el texto crudo del anuncio como fuente de verdad. La
forma en que estaba planteada —comparar el precio publicado contra los números
del anuncio— **no habría atrapado esa fila**, y conviene dejarlo escrito para
no volver a proponerla: el anuncio dice «24800 la caja» con cantidad 40, y ni
620 (24800/40) ni 992.000 (24800×40) aparecen en el texto, así que el conjunto
de números es idéntico se declare `unit` o `total`. La aritmética contra el
texto no puede distinguir las dos lecturas. Donde sí serviría —anuncios que
declaran las dos cifras, «Precio por saco: 32500 (sale a 295 la libra)»— ya lo
cubre `_declared_price_basis`.
Lo que sí distingue las dos lecturas en esa fila es léxico: el anuncio dice
«la caja». Revisados los anuncios reales citados en los tests, el marcador
aparece seguido —«cajas de 40lb a 12000», «La caja 28,5 usd», «el saco a 500»,
«30100mn el saco»— y no había ninguna regla que lo leyera.
Decisión: `_packaging_price_basis` propone `'total'` cuando el anuncio nombra
un envase del vocabulario cerrado junto al precio, en cualquiera de las dos
formas reales («caja de …» o «la caja»). El marcador de unidad —«la libra»,
«la lb», «cada unidad»— siempre gana, y también gana el patrón de
`_declared_price_basis` aunque su aritmética no haya cerrado: si el anuncio
habla de la libra, dice algo del precio unitario que esta regla no puede
contradecir. «A granel» queda fuera del vocabulario de la regla: nombra la
ausencia de envase y su precio es justamente el unitario. Con `quantity == 1`
o sin precio no se propone nada, igual que la regla aritmética.
Es un **apartamiento de D4 mayor** que el del 2026-08-21: allá el número
declarado verifica la lectura antes de proponer, acá la evidencia es léxica y
no hay verificación aritmética posible. Se acepta porque la propuesta nunca se
persiste sola —el operador confirma cada fila en la cola— y porque el silencio
ya demostró costar más: la caja de 992.000 CUP la encontró un humano mirando
la matriz, no el sistema.
Descartado: la comparación numérica contra el texto (no distingue las
lecturas, ver arriba); y verificar que `price` aparezca entre los números del
anuncio, que es señal genuina y sin umbral pero para otro problema —tipeos al
cargar y errores de OCR—, y que no habría levantado esta fila. Queda anotada
sin decidir.
Consecuencia: 1352 tests verdes. Un test existente cambió de texto y no de
intención: `test_sin_pista_en_el_texto_no_se_propone_nada` usaba «Arroz saco de
50 lb a 25000», que bajo la regla nueva SÍ tiene pista; se le sacó el saco para
que siga afirmando lo que su nombre dice. La regla se pasó por los veinte
anuncios reales citados en los tests sin producir ningún falso positivo.

---

## 2026-09-06 — Variantes de producto: sub-unidad, no producto; nombres existentes no se descomponen

Contexto: la marca y el tipo de un producto («Vitalín», «girasol», «importado»)
llegan en el corpus y hasta ahora se descartaban al no tener dónde guardarse
— `_PRODUCT_DISQUALIFYING_MODIFIERS` y el alias de "azucar magdalena" hacia
"Azúcar blanca" son la evidencia en el propio código. Declarar `kind`/`brand`/
`origin` como un eje nuevo (`product_variants`) dejaba dos preguntas abiertas
antes de tocar el esquema: ¿una variante declarada cuenta como una producto
más contra el techo de `spec.md` §9 («3-5 productos bandera», «500 registros
validados de 3-5 productos»)? Y con la marca ahora representable, ¿hay que
descomponer los nombres de producto que ya cargan un tipo adentro —"Azúcar
blanca", "Arroz criollo", "Picadillo"— para no tener dos formas de decir lo
mismo?

Decisión: una variante es una sub-unidad de su producto, nunca una producto
independiente. No consume un cupo de "3-5 productos bandera" ni cuenta como
su propia producto contra la meta de "500 registros validados"; `is_pilot`
sigue viviendo únicamente en `Product`, y una variante hereda el foco de su
padre sin tener un `is_pilot` propio. Y los nombres de producto existentes
**no** se descomponen: "Azúcar blanca", "Arroz criollo" y "Picadillo"
conservan su nombre tal cual lo tienen hoy; la migración `0020` es puramente
aditiva, sin backfill y sin tabla de mapeo — no hay ninguna fila vieja que
reescribir.

Descartado: contar cada variante declarada como una producto nueva a los
efectos de §9 — infla el foco cada vez que se declara una marca, cuando lo
que en realidad crece es la densidad de una serie ya contada, no el número de
productos que el operador vigila; `product_formats` sentó el mismo precedente
(la caja de 40 lb no hizo de Pollo un sexto piloto). También se descartó
decomponer los nombres existentes en producto + tipo: reescribe la identidad
de la serie (`cells.py` agrupa por `product_name`), la ruta pública
`/producto/{slug}` (`progress.py::pilot_product_names`) y redefiniría en
silencio la línea base de `h0-runs.jsonl`, sin ninguna evidencia todavía que
lo justifique.

Consecuencia: `is_pilot` y el conteo de §9 se leen exclusivamente de
`Product`; cualquier lectura futura de "cuántos productos hay" tiene que
seguir ignorando `product_variants`. Dos formas de nombrar lo mismo pueden
coexistir un tiempo — el nombre de producto "Azúcar blanca" y una futura
variante `kind="blanca"` de un producto genérico "Azúcar" — hasta una
consolidación posterior que decida con datos reales, no con esta migración.

## 2026-09-06 — El formato del catálogo se elige en la cola, y rellena en vez de postear

Contexto: `/catalog` deja declarar formatos de venta por producto («botella de
900 ml»), pero el formulario de curaduría no los ofrecía: el operador rearmaba
el triple desde envase + cantidad + unidad, y `get_or_create_format` crea una
fila nueva si el triple no coincide exacto. Tipear «0.9» + «L» donde el
catálogo dice «900» + «ml» fabricaba un segundo `ProductFormat` para el mismo
objeto de mercado — la fragmentación que el eje de formatos vino a cerrar.

Decisión: un `<select>` con los formatos ya declarados para ese producto que,
al elegirlo, **rellena** `packaging_N`/`quantity_N`/`unit_N` con JS plano. No
tiene `name`, así que el POST llega idéntico al de hoy y `_resolve_format_id`
no se toca. Las opciones las arma `_format_options`, espejo de
`_variant_options`. La etiqueta sale de `saimc.formats.format_label`, nueva y
única: `/catalog` la armaba a mano con otro separador y un `| int` que mostraba
«0 L» para un formato de 0.9 L, y `cells.py` tenía su propia copia.

Descartado: postear un `format_id` directo y saltear la resolución por triple.
Habría que sostener dos caminos de escritura —el select y la carga a mano— y
el que menos se ejercita es el que se pudre; además obliga a decidir qué gana
cuando el `format_id` posteado contradice los tres campos. También se descartó
preseleccionar el formato de la fila: el control rellena, no refleja estado, y
mostrarlo apuntando al formato actual afirmaría que el triple salió de ahí.

Consecuencia: el select hereda la limitación ya declarada de la variante —
cambiar el producto de la fila no recarga las sugerencias, se corrige
recargando la página—, y está dicho en su texto de ayuda. Un producto sin
formatos declarados no lo renderiza y se carga a mano como antes. Cualquier
pantalla nueva que nombre un formato usa `format_label`, no una copia.

## 2026-09-06 — El `| int` del catálogo también corrompía al guardar, no solo al mostrar
Contexto: la entrada anterior arregló el `| int` de la etiqueta de `/catalog`
tratándolo como un problema de visualización. Estaba en dos lugares: el input
de edición del formato lo seguía usando. Abrir «botella de 0.9 l» —editable
porque no tiene observaciones— mostraba `0` en el campo, y guardar sin tocar
nada reescribía el formato con cantidad 0.
Decisión: el input toma la cantidad ya formateada por `saimc.formats.
format_quantity`, que viaja en la tupla de `formats_by_product` junto a la
etiqueta. Test de regresión en `test_catalog.py` que afirma el `0.9` y niega
el `0`.
Descartado: exponer `format_quantity` como filtro Jinja global. Un cuarto
elemento en una tupla que ya llevaba tres es más chico y no agrega
configuración de plantillas para un solo uso.
Consecuencia: `formats_by_product` pasa a ser una tupla de cuatro. Cualquier
pantalla que edite una cantidad de `Numeric(12,3)` tiene que usar
`format_quantity`, nunca `| int`: el truncado no avisa.

## 2026-09-07 — La tupla de unidades cumplía dos roles y confundirlos leía el envase como precio
Contexto: el operador reportó que «🚨ACEITE de🌻 900ml🚨 / 3400cup x12u x caja /
TOTAL 🟰 40800cup» salía mal. Investigando apareció una causa mucho más ancha que
ese anuncio: `_UNIT_WORDS` no tenía unidades métricas chicas, y esa única tupla
alimentaba dos cosas distintas — reconocer cantidad (`_QUANTITY_RE`) y proteger un
número de leerse como precio (`_BARE_NUMBER_UNIT_TAIL_RE`). Como
`_bare_price_candidate` se queda con el número MAYOR de la línea, en «Vinagre de
claro 500 ml ➔ 220» el volumen le ganaba al precio y el motor proponía 500.
Decisión: separar los dos roles. `_UNIT_WORDS` suma sólo lo que
`units.canonical_unit()` sabe resolver (`ml`, `l`, `litros`) — es el «cambio
aparte» que el docstring de `units.py` ya anticipaba. Las magnitudes que el
mercado escribe pero `UNITS` no almacena (`g`, `gr`, `oz`, `cc`, `lt`, `u`,
`uds`, `unidades`) van en `_NON_CANONICAL_MAGNITUDE_WORDS`, que sólo entra en la
guarda de precio. Medido contra la cola (914 anuncios con texto, 3368 líneas
únicas): 39 precios corregidos, 114 precios falsos borrados, 161 líneas ganan
cantidad+unidad, 0 pierden la que tenían, y las filas suben de 2563 a 2800
porque el precio falso caía dentro del nombre y dejaba a `_guess_product_phrase`
en `None`, lo que hacía que `propose_all` descartara la línea entera.
Descartado: meter todas las magnitudes en `_UNIT_WORDS`. `canonical_unit('g')`
devuelve `None`, así que reconocer «500 g» como cantidad dejaría una magnitud
huérfana —cantidad con `unit=None`— después de que `curation.py` canonice. Un
campo vacío es honesto; una cantidad sin unidad, no. También se descartó
resolver esto con el asistente LLM: `llm_assist.missing_fields()` sólo pregunta
por campos en `None` y `_run_assist` sólo escribe sobre `None` (design.md D1),
así que un precio mal extraído le es invisible por construcción — no es un hueco,
es una respuesta segura y equivocada.
Consecuencia: agregar una unidad al reconocimiento obliga a decidir en cuál de
las dos listas va, y el criterio es único: ¿la resuelve `canonical_unit()`? Si
sí, `_UNIT_WORDS`; si no, sólo la guarda. Las tres causas restantes del anuncio
del operador —la línea con precio y sin producto que se descarta, la falta de
herencia de encabezado fuera de escalones, y que `propose_all` nunca propone
`price_basis`— quedan abiertas y no las toca esta entrada.

## 2026-09-07 — Mapa de la cola: la vista de curaduría no permitía mirar
Contexto: dos veces seguidas entregué pasos de verificación que pedían «abrí
/curation y buscá tal anuncio». Es imposible por diseño: `/curation` muestra un
solo pendiente —`en_foco[0]`, el más antiguo que mencione un piloto— y no había
ruta para abrir uno puntual. El foco además esconde lo que no nombra un piloto,
así que ni siquiera se sabía qué había. Medido: 284 pendientes, de los cuales
201 son imágenes sin transcribir y 53 están fuera de foco.
Decisión: `GET /curation/cola`, el mapa completo. Lista todos los pendientes con
filtros (`foco`, `tipo`, `producto`, `q`), marcas emoji, contadores clicables por
foco, y un enlace por fila a `GET /curation?id=<uuid>` que abre ese anuncio para
curarlo. El salto acepta cualquier pendiente, también fuera de foco —es
justamente lo que la vista existe para dejar ver— y un id inválido, inexistente o
ya no pendiente degrada al primero de la cola en vez de dar 404.
Descartado: correr OCR en el listado para que las imágenes muestren su texto.
Son 201 de 284: pagaría la transcripción de la cola entera en cada carga de
página, el mismo motivo por el que `_focus_rank` deja `_FOCUS_UNKNOWN` sin
resolver. Se listan con su marca y se leen al abrirlas. También se descartó
numerar la posición sobre la lista filtrada: «el #3» tiene que significar lo
mismo en toda vista, así que se numera sobre la cola completa.
Consecuencia: los emoji son vocabulario, no adorno — cada marca la decide
`_queue_entry` en el router y la leyenda de la plantilla las nombra todas.
Sumar una marca es tocar esa función, no la plantilla. Los iconos de Material
que use una plantilla nueva tienen que estar en `material-symbols-rounded.icons.txt`,
que se regenera con `tools/build_icon_font.py` — el tripwire de
`test_static_assets.py` se pone rojo si alguien suma uno y no la corre.

## 2026-09-07 — La conexión de desarrollo no es una restricción; la del usuario final sí
Contexto: `CLAUDE.md` afirmaba «conexión a internet intermitente y lenta,
preferí soluciones que no dependan de descargas grandes o servicios externos
durante el desarrollo». El operador lo desmintió. Esa premisa venía sesgando
decisiones: horas antes, en el mapa de la cola, se eligieron iconos ya
subseteados en vez de correr `tools/build_icon_font.py` sólo para no bajar de
Google Fonts.
Decisión: se saca de `CLAUDE.md` la restricción sobre la conexión de desarrollo
—también del motivo de la decisión cerrada 4, que la citaba— y se la reemplaza
por lo contrario explícito: elegir por lo correcto, no por evitar la red. Se
reevaluaron los tres iconos por mérito: `filter_alt` (filtrar no es buscar) y
`edit` (el enlace abre a editar) quedan porque eran los correctos igual;
`inbox` pasa a `filter_alt_off`, que dice exactamente «no hay resultados bajo
estos filtros», y se regeneró la fuente (33 iconos, 10.716 B).
Descartado: sacar también la premisa de la conexión del **usuario final**
cubano. Es otra afirmación y sigue siendo cierta — sostiene la decisión cerrada
1 (render en servidor, nada de SPA), los assets servidos desde el repo en vez
de un CDN, y unas quince entradas de este mismo archivo. Confundirlas habría
volteado la arquitectura por una corrección sobre el entorno de desarrollo.
Consecuencia: `CLAUDE.md` ahora nombra las dos y advierte que no se confundan.
Una decisión futura que invoque «la conexión» tiene que decir de cuál habla.

## 2026-09-08 — Noto Color Emoji vendorizada: los emoji dejan de depender del sistema
Contexto: la vista de cola usa emoji como vocabulario (pedido del operador), pero
`saimc-material.css` autohospeda Roboto, Roboto Mono, Material Symbols y Alice y
NO declaraba ninguna familia de emoji. En el Linux del operador `fc-list` no
lista ninguna fuente de emoji: sólo renderizaban por el navegador de Windows, y
en cualquier otro sistema el resultado cambiaba de forma y tamaño. El operador lo
señaló como el defecto de fondo detrás de que la vista se viera «fea».
Decisión: vendorizar Noto Color Emoji igual que Material Symbols. Herramienta
hermana `tools/build_emoji_font.py` que importa `QUEUE_EMOJI` del router —la tupla
única con los seis glifos— y pide a Google el subconjunto vía `text=`. Pesa
6.728 B (la familia completa son ~10 MB). `@font-face` en `saimc-material.css` y
`"Noto Color Emoji"` en el stack de `--m3-font` después de Roboto: el navegador
la usa sólo para los code points que Roboto no cubre. Cuatro tests nuevos en
`test_static_assets.py`, gemelos de los de iconos: se sirve, va subseteada, el
manifiesto cubre `QUEUE_EMOJI`, ninguna plantilla usa un emoji fuera de esa
tupla, y la fuente está declarada y en el stack.
Descartado: (a) dejar los emoji al sistema, que es lo que estaba y es lo que el
operador rechazo; (b) escanear las plantillas para juntar los glifos, como hace
`build_icon_font.py` con los iconos: aca dos de los seis (agotado, mayorista)
viven en el router, no en el HTML, asi que la fuente unica es `QUEUE_EMOJI` y la
plantilla la recibe por contexto (`focus_marks`); (c) poner el `@font-face`
tambien en `saimc-public.css` porque la home no usa emoji, y el token la nombra
pero sin definirla ahi cae al sistema como antes, sin regresion.
Consecuencia: un emoji nuevo se agrega primero a `QUEUE_EMOJI` en `curation.py`,
despues se corre `python tools/build_emoji_font.py`; si no, el tripwire de
`test_static_assets.py` se pone rojo. Queda sin verificar en este entorno que el
navegador lo pinte a color (no hay navegador aca); lo confirma el operador.

## 2026-09-08 — El subconjunto de emoji se amplía al contenido de los anuncios
Contexto: la entrada anterior vendorizó Noto Color Emoji subseteada a las 6
marcas de la interfaz (~7 KB). El operador probó y avisó que «no está aplicado a
todas las páginas»: el texto de los anuncios trae 231 emoji distintos (🤑, 🌟,
🚨, 🍸…) en el 31% de la cola, y esos —que se ven en /curation, /curation/cola y
/curation/history— caían al emoji del sistema, inexistente en su Linux.
Decisión (elección del operador entre tres opciones con peso medido): vendorizar
el subconjunto que cubre TAMBIÉN el corpus. `tools/scan_ad_emoji.py` (nuevo, y el
único de `tools/` que toca la base de datos) lee `raw_ingestions`, extrae los
emoji y amplía el manifiesto `noto-color-emoji.chars.txt`; `build_emoji_font.py`
subsetea contra ese manifiesto versionado, sin necesitar la base. La fuente pasa
a **519.556 B**. El `@font-face` se agrega también a `saimc-public.css` —la home
casi no trae emoji, pero un nombre de producto puede (ya hay uno con ✅) y el
token la nombra igual—.
Descartado: (a) fuente del sistema (`apt install fonts-noto-color-emoji`): cubre
todo sin peso de página, pero el operador prefirió no depender de un paso de
instalación; (b) subset top-50 (~35 KB, 82% de las apariciones): el operador
quiso cobertura completa del corpus visto.
Consecuencia: la cola paga ~520 KB una vez (cacheado después). Un emoji nuevo en
un anuncio futuro se ve con el glifo del sistema hasta que se corra
`scan_ad_emoji.py` + `build_emoji_font.py` de nuevo — es la carrera que el
operador aceptó al elegir esta opción. El manifiesto sólo crece: un emoji que
salió de un anuncio ya borrado no se saca de la fuente salvo a mano.

## 2026-09-09 — La medición de H0 le pasaba al motor el catálogo entero, no el foco
Contexto: `compute_h0_report` construía el catálogo con `select(Product)` sin
filtro, mientras la cola de curaduría le pasa a `propose_all` solo los productos
con `is_pilot` (`_pilot_products`, «único punto por donde el catálogo entra a la
curaduría», 2026-08-15). Al 2026-09-09 eran **60 productos contra 5**. Los 55 de
más no son inertes: `_match_product` devuelve el PRIMER producto de la lista que
matchea y la consulta no tenía `ORDER BY`, así que un artefacto de parseo del
catálogo —`Precio :`, presente en casi todos los anuncios— o un casi-duplicado
—`Arroz criollo` contra `Arroz`— le robaba el match al piloto y la fila se
contaba como fallo. Es el mismo defecto que este archivo ya registró para la
provincia del canal (2026-08-17): medir con menos contexto no es más exigente,
es medir otro extractor.
Decisión: la consulta del foco pasa a `saimc.progress.pilot_products` —hermana
de `pilot_product_names`, con `where(is_pilot)` y `order_by(name)`— y la usan
`extraction/measure.py` y `web/routers/curation.py::_pilot_products`. El
`order_by` es parte del contrato, no cosmética: mismo filtro con distinto orden
sigue siendo otro extractor. Dos tests fijan la respuesta de antemano: un
señuelo fuera del foco no le roba el match al piloto, y una fila confirmada
contra un producto fuera del foco sigue contando como fallo —filtrar no puede
ser una forma de inflar el número—.
Descartado: parchear la consulta de `measure.py` en el lugar. Dejaba la
definición del foco escrita dos veces, que es exactamente lo que produjo el
defecto; el docstring de `pilot_product_names` ya había argumentado eso mismo
para los nombres en 2026-08-15.
Consecuencia: las 9 corridas de `docs/h0-runs.jsonl` anteriores a esta fecha no
son comparables con las siguientes — midieron un extractor con señuelos. La
próxima corrida es la primera del extractor real. Las ~10 filas históricas
confirmadas contra productos fuera del foco pasan a contar como fallo de
producto, que es honesto: la cola de hoy tampoco podría proponerlas.

## 2026-09-09 — CLAUDE.md decía «nada de bots» con WAHA hace 25 días en producción
Contexto: la decisión cerrada 2 de `CLAUDE.md` seguía afirmando «curaduría
manual primero, nada de scraping, bots de Telegram ni Playwright en Fase 0».
El apartamiento estaba hecho y registrado desde el 2026-08-15 —webhook de WAHA,
`SourceType.WHATSAPP_CHANNEL`, backfill manual desde `/catalog`— y al 2026-09-09
esa ruta aporta **1235 de 1373 registros ingeridos (90%)**. El documento que se
lee en cada sesión describía un proyecto que ya no existía. Costo concreto: en
esta misma sesión el asistente le discutió al operador desde esa premisa falsa
antes de verificarla contra el código.
Decisión: la decisión 2 pasa a decir lo que es cierto —la curaduría sigue siendo
manual, la captura ya no del todo— nombrando qué sigue prohibido (scraping,
Playwright, bots de Telegram), qué está automatizado y desde cuándo, y qué parte
del motivo original sigue en pie: el conjunto de referencia de H0 se etiqueta a
mano, así que automatizar más entrada no produce dato etiquetado, produce más
cola que drenar.
Descartado: borrar la decisión 2. El motivo que la sostenía no murió, se acotó:
sin conjunto etiquetado a mano no hay H0, y el cuello de hoy es el triage
—267 pendientes, 68% de descarte— no la captura.
Consecuencia: un apartamiento registrado acá no queda cerrado hasta que
`CLAUDE.md` lo refleje; el registro en este archivo no alcanza, porque
`CLAUDE.md` es el que se lee primero. Quedan por revisar con el mismo criterio
la decisión 4 (dice «sin Docker» y WAHA corre en Docker en el VPS) y el motivo
de la 3 (dice «con curaduría manual no hay trabajo asíncrono», y la ingesta por
webhook sí lo es).

## 2026-09-09 — Una fila sin candidato conserva la provincia del canal
Contexto: consecuencia directa del arreglo del catálogo del foco de hoy. Con 5
productos en vez de 60 el motor genera menos candidatos —762 contra 1673 sobre
las 134 ingestas de texto— y las filas confirmadas que se quedan sin línea libre
pasaron del 0.4% al **12.9%**. `_match_rows_to_candidates` le daba a esas filas
un `ExtractionProposal()` pelado, así que su provincia contaba como fallo. Pero
`curation.py` le pone la provincia del canal a TODAS las filas del formulario
—`row["province_id"] = channel_province_id` sobre `rows` entero, y también sobre
la fila en blanco que el operador agrega a mano (2026-08-17, «el canal siempre
manda»)— sin mirar si el motor propuso algo para ellas. Era otra vez el mismo
defecto de la corrida anterior: medir un extractor más ciego que el real.
Decisión: `_match_rows_to_candidates` recibe `channel_province` y la rama «sin
líneas libres» devuelve `ExtractionProposal(province=channel_province)` en vez
de una propuesta pelada. El producto, el precio y la cantidad de esas filas
siguen contando como fallo: el canal aporta la provincia y nada más.
Descartado: dejarlo como estaba y explicar la caída de provincia como «el número
honesto». No lo era en esa dimensión — producción sí completa esa provincia, así
que contarla como fallo medía un extractor inexistente, exactamente el error que
esta misma función ya había corregido para los candidatos que sí existen.
Consecuencia: la caída de provincia de la corrida intermedia (90.8% → 81.3%) era
en parte artefacto de este defecto, no señal. Las caídas de precio y
cantidad/unidad, en cambio, se quedan: esas sí eran acierto por casualidad que
los 55 productos señuelo regalaban al emparejar filas con líneas ajenas.

## 2026-09-09 — El espacio como separador de miles hacía que "7 000 CUP" valiera 0
Contexto: diagnóstico de la precisión de precio (42.0%) sobre las 232 filas de
texto del corpus real. Los cinco patrones de precio capturaban `(\d[\d.,]*)`,
sin espacio, y los canales del operador escriben "7 000 CUP", "27 400 CUP",
"24 000" constantemente. En `"Paquete de 10 lb: 7 000 CUP"` el patrón enganchaba
los `000` pegados a la moneda y `_normalize_number("000")` devolvía
**Decimal("0")**; en `"27 400 CUP"` devolvía 400. Un precio de cero es peor que
ninguno: el operador ve un número plausible en el formulario en vez de un campo
vacío que le pide atención.
Decisión: se extrae `_PRICE_NUMBER` y lo usan los cinco patrones anclados a una
marca de precio (moneda, "$", "a ", y los dos lados del patrón de doble moneda).
El grupo tiene que ser de exactamente tres dígitos: "30 8850" son dos números, no
treinta mil ochocientos cincuenta. `_normalize_number` saca el espacio, incluido
el duro (U+00A0), que WhatsApp y el OCR emiten indistinguible del normal.
Descartado: aplicarlo también a `_BARE_NUMBER_RE`. Sin ancla de precio el
espacio es ambiguo —"Km 34 500" es una dirección— y pegar los grupos inventaría
precios donde hoy no hay ninguno.
Consecuencia: **el arreglo no movió H0**. Medido sobre las 305 filas: precio
42.0% → 42.3%, cantidad/unidad 50.5% → 50.2%, extracción completa 33.4% → 33.4%.
Una fila para cada lado: ruido, no señal. Sobre las 232 filas de texto la
clasificación de fallos quedó idéntica (107 OK, 63 sin precio propuesto, 57 con
valor distinto). El arreglo es correcto y está cubierto por 10 casos nuevos,
pero no era el cuello. La traza de la fila que motivó el fix lo muestra: el motor
YA genera el candidato correcto (precio=7000 CUP, cant=10 lb) y
`_match_rows_to_candidates` lo descarta, porque empareja por `product_id`
primero y ese candidato quedó con `product=None` — la línea "Paquete de 10 lb"
no nombra el producto, que está en la línea de encabezado ("Cuartos de Pollo
MAR-JAC"). La fila se lleva el único candidato con producto reconocido, que trae
el precio equivocado. No es un defecto de la medición: el extractor de verdad no
supo atar el producto del encabezado a las líneas de formato, y en produccion esa
atadura la hace el operador a mano. El próximo paso para mover precio es que el
motor herede el producto entre líneas del mismo anuncio, no más ajustes de
parseo de números.

## 2026-09-09 — Las líneas de formato heredan el producto del encabezado
Contexto: el price-list mayorista cubano declara el producto en el encabezado y
el formato en las líneas siguientes ("Cuartos de Pollo MAR-JAC 4x10 lb" / "Caja
de 40 lb: 27 400 CUP" / "Paquete de 10 lb: 7 000 CUP"). `_tiered_proposals` ya
heredaba del encabezado, pero solo en la forma de ESCALONES por volumen ("Más de
10 cajas 28"); esta forma no los tiene y caía a la lectura línea por línea, con
las líneas de formato en `product=None`. La traza del 2026-09-09 mostró la
consecuencia: el motor generaba el candidato con el precio correcto y
`_match_rows_to_candidates` lo descartaba, porque empareja por `product_id`
primero y la fila se llevaba el único candidato con producto reconocido —el del
encabezado, con el precio equivocado ("4" salido de "4x10 lb")—.
Decisión: en `propose_all`, una línea sin producto propio hereda el único
producto del anuncio cuando trae precio y **nombra un envase**. Las dos
condiciones importan. La primera es la misma que ya usa `_tiered_proposals`: con
dos productos nombrados, atribuir un formato a uno sería inventar. La segunda
sale de `units.PACKAGING`, el vocabulario cerrado que ya usa el resto del
sistema, así que un envase nuevo entra una vez y la regla lo reconoce sola.
Descartado: pedir «precio y cantidad» en vez de envase. Fue la primera versión y
rompió un test existente: el ruido de OCR "$23,500.Sale 427.27Lb" trae precio
(23500) y cantidad (427.27 lb) y quedaba convertido en una oferta de pollo.
Nombrar un envase es lo que declara que la línea describe un formato; tener
cifras, no.
Consecuencia: primera mejora real de H0 de la jornada, medida sobre las 305
filas. Precio 42.3% -> **47.9%** (+5.6), extracción completa 33.4% -> **35.1%**
(+1.7), producto 80.3% -> 81.0%. Cantidad/unidad baja 50.2% -> 49.8%: al haber
más candidatos con producto, el emparejado deja de regalar cantidades por azar
—la misma deflación de casualidad que ya se vio al filtrar el catálogo—. Sobre
las 232 filas de texto el precio pasa de 107 a 120 filas correctas.

## 2026-09-10 — El catálogo de tienda deja de leerse como bloque escalonado
Contexto: se abrió un cambio para mostrarle al asistente LLM los valores que las
reglas extraen MAL —no solo los huecos—, porque D1 los hace invisibles por
diseño. La medición contra el corpus real lo descartó antes de escribir nada:
de las 50 filas con precio equivocado, 30 (60%) no salían de un valor ambiguo
sino de `_tiered_proposals`, que reclamaba anuncios que no le correspondían. El
testigo es un catálogo de tienda entero —decenas de productos en cinco
categorías— que termina con un «Más de 10 pacas» y por eso activaba la lectura
de bloque: colapsaba el anuncio a UNA fila con precio 34, el kilómetro de
«📍 Tienda San José Carretera Central Km 34», que era la primera línea con
cifras. Las dos líneas con la respuesta correcta («Arroz grano dorado 100lb.
43,5 o 30885» y «1 kg. 0,97 o 688,7») no se leían nunca, porque `propose_all`
corta apenas el bloque devuelve algo.
Decisión: cuando dos o más líneas CON PRECIO nombran el producto,
`_tiered_proposals` devuelve `None` y cede a la lectura línea por línea. La
premisa del bloque es que ninguna línea sola está completa; dos líneas con
precio que nombran el producto la falsifican, porque cada una es una oferta
completa —un formato distinto del mismo producto—.
Descartado: elegir el precio base de una línea que nombre el producto, dejando
el bloque activo. Se implementó y se midió primero: subía precio (+8 filas de
texto) pero BAJABA extracción completa (97 -> 93 filas), porque mover
`base_price` reordena el emparejado de `_match_rows_to_candidates` sin arreglar
que el catálogo se siga colapsando a dos filas. También se descartó, y esto es
lo que originó todo, mostrarle valores sospechosos al LLM: habría costado un
apartamiento argumentado de `spec.md` §6.1 para atacar menos fallos que este
arreglo determinista, que no toca D1 ni pregunta nada.
Consecuencia: la mejora más grande medida hasta ahora. Sobre las 232 filas de
texto, extracción completa 41.8% -> **53.0%** (+11.2), precio 51.7% -> **65.5%**,
producto 81.5% -> **90.5%**, cantidad/unidad 54.3% -> **66.8%**. Sobre la corrida
completa de 305 filas, completa 31.8% -> **40.3%** (los absolutos están
deprimidos porque el worktree no tiene `data/raw` y 60 de 61 anuncios de imagen
transcriben vacío; las dos corridas comparten el handicap, así que el delta
vale). Quedan sin atacar tres patrones ya cuantificados: precio dual USD/CUP en
una línea (30% de las 50), «Sale N Cup Lb» ganándole al precio real (20%) y
«31mil» sin expandir a 31000 (6%).
Nota de método: dos mediciones previas de esta misma sesión afirmaron que 40% y
luego 30% de los precios malos salían de líneas de dirección. Las dos eran
falsas —el número real es 0%— porque localizaban el precio buscándolo como
subcadena del anuncio en vez de rastrear qué línea lo produjo. `101` aparece
dentro de «Calle 20 entre 100 y 101» por casualidad. Las direcciones ya se
descartan en `propose_all`. Rastrear procedencia real, nunca coincidencia de
texto.

## 2026-09-10 — La suite corre con `.venv/bin/pytest` y deja de pudrirse con el calendario
Contexto: al mergear a master, `.venv/bin/pytest` cortaba en el colector con
`ModuleNotFoundError: No module named 'tests'` y 29 tests no llegaban a correr.
Aparte, `test_public_map.py::test_evidencia_de_texto_enlaza_a_anuncio` fallaba
sin que nadie lo tocara. Dos cosas distintas, las dos preexistentes.
Decisión: (1) `pythonpath = ["."]` en `[tool.pytest.ini_options]`. Los dos
archivos de `basis` hacen `from tests.test_cells import ...`, pero `tests/` no
es un paquete, así que el import solo resolvía con `python -m pytest`, que
agrega el CWD por su cuenta. (2) Las fechas de `TestEnlaceDeEvidenciaEnLaSerie`
pasan a ser relativas a hoy: fijaba 2026-08-10 y 2026-08-12, que el 2026-08-25
caían dentro de la ventana por defecto de 30 días y el 2026-09-10 dejaron de
caer, porque `build_series` recorta con `hoy - timedelta(days=30)`.
Descartado: agregar `tests/__init__.py`. Convierte la carpeta en paquete e
invita a más imports cruzados entre módulos de test, que es el acoplamiento que
originó el problema. Una línea de config lo arregla sin cambiar la semántica.
Consecuencia: `.venv/bin/pytest` y `python -m pytest` dan lo mismo — 1463
pasan, 1 salta, 0 fallan. El test de imagen del mismo par estaba verde por la
razón equivocada: aseguraba que no hubiera enlace cuando el enlace ya no se
dibujaba por la ventana, no por §10.6. Regla que queda: todo test que pegue a
la vista pública fecha relativo a hoy (`_hace()` en `test_public_filter.py`) o
le pasa un `hoy=` explícito a `build_series` (`test_series_view.py`), nunca una
fecha dura.

## 2026-09-10 — El test de la franja de estado declara su premisa en vez de heredarla del `.env`
Contexto: `test_control.py::TestFranjaDeEstado::test_sin_waha_configurado_no_pinta_nada_roto`
fallaba en el checkout del operador y pasaba en cualquier worktree. No aislaba
los settings del `.env` del cwd: su premisa es «sin WAHA configurado», pero el
`.env` real tiene `SAIMC_WAHA_BASE_URL`, así que WAHA quedaba
configurado-e-inalcanzable, `build_strip` lo ponía en `down` y la franja salía
roja. Estaba detectado desde el 2026-09-07 y anotado como preexistente fuera de
alcance; quedó invisible hasta hoy porque el colector abortaba antes de llegar
(ver la entrada de `pythonpath` de esta misma fecha). Arreglar la colección no
lo causó: lo destapó.
Decisión: el test declara su premisa con `monkeypatch.setattr(control,
"session_health", ...)` devolviendo `SessionHealth(configured=False,
status="unknown")` —la forma exacta que produce `whatsapp_health` cuando falta
la configuración—, igual que ya hace su hermano `test_waha_caido_pone_el_banner_rojo`.
Descartado: `monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "")` + `cache_clear()`.
Funciona, pero deja el aislamiento atado a cómo pydantic-settings interpreta la
cadena vacía y a limpiar una caché global que los tests vecinos ya ensucian sin
restaurarla. Declarar el estado de salud es más directo y no depende del
entorno en absoluto.
Consecuencia: la suite da igual con `.env` y sin él — 1463 pasan, 1 salta, 0
fallan, verificado corriéndola con `SAIMC_WAHA_BASE_URL` puesto y sin poner.
Regla que queda: un test que afirme «X no está configurado» declara el estado,
nunca lo hereda del entorno. Y verificar la suite con las variables del
operador puestas, no solo en un worktree limpio, que es lo que hizo que esto
tardara tres días en verse.

## 2026-09-10 — Riel de sub-secciones de Curaduría en el topbar
Contexto: el operador pidió que «todas las páginas» estuvieran en el menú del
header. El rail de secciones de `_shell.html` tiene solo cuatro entradas
(Catálogo / Curaduría / Control / Documentos), y las tres sub-vistas de
curaduría —cola (el mapa, 2026-09-07), historial y descarte en lote
(2026-08-31)— se alcanzaban únicamente por enlaces sueltos dentro de `/curation`.
Decisión: parcial nueva `_curation_tabs.html`, incluida desde el bloque `tabs`
de las cuatro plantillas de curaduría. Rinde un `.m3-tabs` en el topbar con
Curar / Cola / Historial / Sin foco; el activo sale de `request.url.path` por
igualdad exacta (`?id=` no cambia el path, así que curar un anuncio puntual
sigue marcando «Curar»). Se quitaron los breadcrumbs «Volver a curar/a la cola»
y los enlaces «Ver toda la cola →» / «Historial de curados →» de `curation.html`,
ya redundantes. El link contextual «Descartar en lote →», que solo aparece
cuando hay pendientes fuera de foco, se deja.
Descartado: subir las tres a entradas planas del rail de secciones (siete
ítems). Aprieta el rail —sobre todo apilado en móvil— y mezcla dos niveles de
navegación en la misma fila.
Consecuencia: `_curation_tabs.html` es la fuente única de la navegación entre
vistas de curaduría; agregar o renombrar una vista se toca ahí. Regla CSS nueva
`.m3-topbar .m3-tabs { margin-top }` porque el bloque queda pegado a
`.m3-topbar-row`. Las sub-vistas siguen marcando «Curaduría» en el rail de
secciones, que deriva de `path.startswith('/curation')`. `curation_edit.html` y
`/evidence/{id}` quedan sin el riel: son vistas de detalle, se llegan por
contexto.

## 2026-09-10 — Rediseño home pública, slice 1: el mapa es el lienzo a pantalla completa
Contexto: prototipo de Claude Design en `design_handoff_home_publica/` (README
con tokens y estados). La home hoy es un panel tipo BI; el rediseño hace del
mapa de Cuba el lienzo de la pantalla, con chips de producto, paneles flotantes
con histórico y hoja de detalle por provincia. Se implementa por slices; este
es el 1.
Decisión: en `public_home.html`, sección nueva `.ph-canvas` como primer bloque
del contenido — full-bleed (`margin-inline: calc(50% - 50vw)` dentro del
contenedor centrado del shell), `100dvh`, el SVG del mapa centrado y grande, con
overlay: `<h1>`, frescura del dato, leyenda de estados y una señal de scroll a
`#cobertura`. El encabezado del shell (`.m3-public-top`) se oculta por CSS en
este bloque y los `{% block heading/subtitle %}` quedan vacíos: su contenido se
reproduce en el overlay. El mapa se sacó de `.ph-hero` (había una sola copia y
sigue habiendo una); `.ph-hero` conserva stats, concentración de muestra, nota
metodológica y la tabla accesible «Observaciones por provincia».
El mapa sigue coloreado por estado de publicación, NO por precio: el coloreo por
precio (que `map_view.py` y `public.py` marcan como bloqueado por §9.0/§12.6)
quedó fuera de alcance por decisión del operador el 2026-09-10.
Descartado: mantener el encabezado del shell arriba del lienzo — un teléfono
acostado se comía el mapa bajo el cromo fijo, y «a pantalla completa» dejaba de
serlo. Sin `filter: drop-shadow` con color a mano: usa `var(--m3-elev-1)` para
no romper `test_static_assets.py::TestNingunColorFijoEnLasPlantillas`.
Consecuencia: `/` y `/producto/<slug>` comparten el lienzo (misma plantilla).
Las provincias todavía no son clicables — la hoja de detalle llega en el slice
5; los chips de producto, en el 2. Suite entera en verde con orden determinista
(`-p no:randomly`): 1463 pasan, 1 saltea. `test_public_filter.py` tiene
polución de orden preexistente ajena a este cambio (una corrida parcial puede
dejar `test_respeta_la_ventana` en rojo; con la suite completa pasa).

## 2026-09-10 — Rediseño home pública, slice 2: chips de producto multi-selección
Contexto: el filtro de producto de la home era una sola pestaña (`?producto=`,
un producto → tabla comparativa; sin producto → grilla). El rediseño lo quiere
como riel de chips flotante sobre el mapa, multi-selección, que en los slices
siguientes alimenta un panel flotante por producto activo.
Decisión: eje nuevo `?productos=aceite,arroz` (lista, orden = activación).
`product_nav.py` gana `resolve_products`, `ProductChip` + `build_product_chips`
(el `query` de cada chip es el estado tras togglear ese chip, preservando
provincia y ventana), y `_query` acepta `productos=` (gana sobre `producto=`
cuando trae algo). `_render_home` resuelve el conjunto activo; `?producto=` y la
ruta limpia `/producto/<slug>` siguen andando como alias de un solo producto.
`selected` (un producto, y con él la tabla comparativa de `#precios`) solo
existe con exactamente uno activo; con dos o más, la grilla de tarjetas acotada
al conjunto. `_visible_observations` pasa de `product: str|None` a
`products: frozenset|None`; el CSV lo respeta. El riel de chips va en el overlay
del lienzo, sin htmx: cada chip es un enlace de página completa con ancla
`#cobertura`. Se quitó la vieja `<ul class="ph-filter">` de producto; provincia
y ventana siguen ahí.
Descartado: threadear `?productos=` en `build_province_nav`/`build_window_nav`.
Con dos o más productos activos, cambiar provincia o ventana los resetea (cae a
«todo»). Es un slice intermedio; se corrige cuando los paneles del slice 4
tengan su propio estado. Descartado también `?foco=`: no tiene consumidor
visual hasta el slice 4 (coloreo del mapa / panel expandido), marcarlo sin
efecto confundía.
Consecuencia: `build_product_nav` queda sin uso en la plantilla pero se
conserva (con sus tests) — el slice 4 puede reusar su lógica de URL limpia para
`?foco=`. **Los tests se corren con
`PYTHONPATH=<worktree>/src`**: el `.pth` editable del `.venv` compartido apunta
al checkout principal, así que sin eso pytest prueba el código viejo y pasa en
falso (fue lo que escondió estas 5 roturas al principio). Suite: 1477 pasan, 1
saltea, con `PYTHONPATH` correcto y `-p no:randomly`.

## 2026-09-10 — Rediseño home pública, slice 4: paneles flotantes + htmx en la capa pública
Contexto: los chips del slice 2 prendían productos pero sin nada que mostrar
sobre el mapa. El rediseño quiere un panel flotante por producto activo, con su
rango nacional y su evolución.
Decisión: `panel_view.py` nuevo — `ProductPanel` + `build_product_panels()`
agrega por producto desde las tarjetas y las series que `_render_home` ya
calcula (mín de mínimos / máx de máximos / suma de conteos / provincias
distintas), en el orden de activación. Es lectura rápida, no medición nueva: la
tabla por provincia de `#precios` sigue siendo la autoridad. La marca del
gráfico se extrajo a la parcial `_price_plot.html`, compartida entre
`#como-se-movio` y los paneles. Cada panel es un `<details>` (acordeón nativo,
el primero abierto); el × quita ese producto de `?productos=`.
**htmx entra en la capa pública** (`public_home.html` `{% block scripts %}`,
`/static/htmx.min.js` ya vendorizado). `#ph-live` envuelve todo lo que depende
del conjunto activo; chips y × llevan `hx-get` a la misma URL que su `href`, y
htmx reemplaza solo `#ph-live` (`hx-select`/`outerHTML`/`hx-push-url`). Sin JS,
el `href` hace la navegación completa: degrada, no se rompe. La Transparency
Layer (`/anuncio/<id>`) sigue sin htmx.
Descartado: `?foco=` y coloreo del mapa por panel expandido — el coloreo por
precio quedó fuera de alcance (2026-09-10), así que el foco no tiene consumidor
visual; el acordeón nativo alcanza. Descartado también mantener la marca del
SVG duplicada en dos lugares.
Consecuencia: al togglear un chip, el acordeón vuelve al estado inicial (primer
panel abierto) — htmx re-renderiza `#ph-live` entero. Los paneles y las
secciones `#precios`/`#como-se-movio` muestran el mismo dato hoy; el slice de
cierre puede recortar la duplicación. Suite: 1488 pasan, 1 saltea
(`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Rediseño home pública, slice 5: hoja de detalle de provincia
Contexto: `?provincia=<slug>` ya filtraba `#precios`/`#como-se-movio`, pero el
mapa no era clicable y no había una vista «todo lo de esta provincia».
Decisión: cada `<path>` de provincia va envuelto en `<a href hx-get>` a
`?provincia=<slug>` (preservando chips y ventana); `MapProvince` gana la
propiedad `slug` (vía `product_slug`, el mismo que `resolve_province` usa para
el ida y vuelta). Con `?provincia=` puesta, `_render_home` arma
`province_sheet_rows` recorriendo TODOS los piloto de esa provincia —no los
chips activos— reusando `build_product_panels` sobre tarjetas y series acotadas
solo por provincia. La plantilla rinde una `<aside class="ph-sheet"
role="dialog">` dentro de `#ph-live` (htmx la trae y la quita en el swap):
drawer derecho en escritorio, bottom-sheet en angosto (corte 720). Un producto
sin fila en la provincia entra con «todavía no hay dato acá». Cerrar (× o fondo
oscurecido) va a `sheet_close_query` = la URL sin `provincia`, con `productos` y
`dias`. Scrim con `color-mix(var(--m3-on-surface) 32%, transparent)`, no rgba a
mano.
Descartado: trampa de foco de teclado en la hoja (haría falta JS de verdad; el
× y el fondo sí son alcanzables — queda como deuda anotada). Descartado también
animar la salida: htmx quita el nodo en el swap, así que la entrada anima y la
salida es instantánea.
Consecuencia: el mapa ahora navega. `map_view.py` importa `product_nav`
(sin ciclo: `product_nav` es solo stdlib). La hoja y el filtro `?provincia=` de
`#precios` conviven —muestran lo mismo con distinto formato—; se unifican en el
slice de cierre junto con la duplicación de paneles. Suite: 1494 pasan, 1
saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Rediseño home pública, slice de cierre (parcial): fin de la duplicación de gráficos
Contexto: tras los slices 1–5, la evolución de precio se dibujaba hasta tres
veces —panel flotante, hoja de provincia y sección `#como-se-movio`— y togglear
un chip colapsaba el acordeón de paneles.
Decisión: `#como-se-movio` pasa a ser SOLO la vista general: se renderiza
únicamente cuando no hay ningún producto activo ni provincia elegida
(`{% if series and not active_products and not selected_province %}`). Con foco,
su gráfico ya vive en el panel (producto) o en la hoja (provincia). El enlace
«Cómo se movió» de `.ph-nav` se condiciona igual. Los `<details>` de los
paneles abren siempre (`open` fijo, sin `{% if loop.first %}`): togglear un chip
ya no colapsa nada, y el `<summary>` sigue dejando plegar a mano. Se borró la
regla CSS muerta `.ph-map-wrap` (el mapa salió de ahí en el slice 1).
Descartado por ahora, queda como deuda: unificar la hoja de provincia con el
filtro `?provincia=` de `#precios` (siguen mostrando lo mismo con distinto
formato); recortar `#precios` vs. el resumen de los paneles; trampa de foco de
teclado y Escape-cierra en la hoja (necesitan JS de verdad). Se dejan para
cuando el operador revise el rediseño completo y decida cuánto recortar.
Consecuencia: en `/` sin filtros el comportamiento no cambia (la vista general
sigue). Apenas se prende un chip o se elige una provincia, `#como-se-movio`
desaparece. Suite: 1498 pasan, 1 saltea
(`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — La home pública es el mapa entero; lo demás son ventanas del menú
Contexto: el operador marcó que el brief era «toda la página es el mapa» (el
handoff también lo decía: «todo lo demás flota sobre el mapa»). Los slices 1–5
habían dejado cobertura, precios y evolución como secciones a un scroll de
distancia. Reemplaza la gating de `#como-se-movio` de la entrada anterior.
Decisión: `.ph-canvas` es `100dvh` sin scroll, en columna: barra superior
(`<h1>`, frescura y menú), riel de chips y un escenario que el mapa llena.
El menú del header abre ventanas flotantes —Cobertura, Precios, Cómo se
movió— y **varias pueden estar abiertas a la vez** (decisión del operador):
se apilan a la izquierda; los paneles de producto, a la derecha; en angosto
las dos pilas se juntan en una bandeja inferior que scrollea sola. Estado en
`?ver=cobertura,precios,evolucion` (`product_nav.VIEWS`, `resolve_views`,
`build_view_menu`); `_query` gana `ver` y todos los enlaces lo preservan
(chips, × de panel, provincias del mapa, hoja, filtros de provincia y días),
así nada cierra ventanas por accidente. Los filtros de provincia/días también
preservan ahora el conjunto de chips —se cierra la limitación del slice 2—.
Las ventanas se renderizan siempre y las cerradas llevan `hidden`: el HTML es
el mismo que antes traía la página por scroll, abrir/cerrar solo cambia el
atributo, y los tests de contenido siguen valiendo. El encabezado y el pie del
shell se ocultan acá; el crédito de Natural Earth va en la esquina del mapa y
la nota de procedencia al pie de la ventana Cobertura. Se borraron `.ph-hero`,
`.ph-nav`, `.ph-section*` y la señal de scroll. Se quitaron las anclas
`#cobertura`/`#precios` de los enlaces: ya no hay a dónde scrollear.
Descartado: una sola ventana a la vez (el operador pidió todas). Descartado
también renderizar solo las ventanas abiertas: aligera el swap, pero rompía
decenas de tests de contenido y la página nunca mandó menos HTML que esto;
queda como optimización si el peso se vuelve un problema medido.
Consecuencia: la home no tiene scroll en ningún ancho. `build_province_nav` y
`build_window_nav` aceptan `active_products` y `ver`. Deudas que siguen: foco
de teclado y Escape en la hoja y en las ventanas (JS). Suite: 1507 pasan, 1
saltea (`PYTHONPATH=<worktree>/src -p no:randomly`). Layout sin verificar en
navegador desde la sesión (no hay uno headless en el VPS).

## 2026-09-10 — Ventanas flotantes arrastrables, minimizables y expandibles
Contexto: el operador pidió que las ventanas flotantes (las del menú y los
paneles de producto) se puedan arrastrar, minimizar y expandir.
Decisión: primer JS propio de la capa pública, inline en el
`{% block scripts %}` de `public_home.html`, fuera de `#ph-live` y como mejora
progresiva: pone `.ph-js` en `<html>`, y sin eso los botones no se muestran y
las ventanas quedan apiladas como antes. Los paneles de producto dejan de ser
`<details>` y toman la misma forma que las ventanas del menú: `<section
data-win>` con barra `[data-win-handle]` y botones `[data-win-min]` /
`[data-win-max]` junto al ×. Arrastrar (umbral de 5px: un clic no despega la
ventana) la pasa a `position: fixed` acotada al viewport; expandir (botón o
doble clic en la barra) la estira al escenario del mapa; minimizar deja solo la
barra. El estado vive en `sessionStorage` por id de ventana y se reaplica en
`htmx:afterSettle`, porque htmx re-renderiza `#ph-live` entero en cada swap. En
angosto (≤720) no se arrastra —las ventanas viven en la bandeja inferior—, pero
minimizar y expandir sí. Iconos de los botones con pseudo-elementos CSS, sin
glifos que un teléfono viejo pueda no tener.
Descartado: archivo `.js` estático aparte. Sería cacheable, pero
`TestClasesDelJavascriptExisten` solo mira las plantillas: inline, el test ya
verifica que toda clase que asigna el script (`ph-js`, `is-floating`,
`is-dragging`, `is-min`, `is-max`) esté definida. Descartado también guardar
posiciones en la URL: son del visitante, no del estado compartible.
Consecuencia: ~5 KB de JS (sin comprimir) viajan con cada respuesta de la home,
también en los swaps de htmx. Arrastrar no es accesible por teclado (las
ventanas funcionan igual sin arrastrarlas; minimizar y expandir son botones).
Sintaxis del script verificada con `node --check` sobre el HTML renderizado;
comportamiento sin verificar en navegador desde la sesión. Suite: 1509 pasan,
1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Ventanas redimensionables desde sus bordes
Contexto: además de arrastrar, minimizar y expandir, el operador pidió poder
estirar y contraer cada ventana al tamaño que el visitante elija.
Decisión: tres agarres por ventana (`[data-win-grip]`): borde derecho (`e`),
inferior (`s`) y esquina (`se`, con marca visible). Estirar una ventana
apilada la despega (pasa a flotante). Mínimos de 240×120 px para que la barra
y los botones sigan entrando; máximos acotados al viewport. El alto elegido se
guarda con la posición en `sessionStorage` y arrastrar lo conserva. Ocultos sin
JS, con la ventana minimizada o expandida, y en angosto. Se corrigió además un
choque de cascada: `.ph-win, .ph-fpanel { position: relative }` —misma
especificidad y declarada después— pisaba el `position: fixed` de
`.is-floating`; quedó como `:not(.is-floating)`.
Descartado: `resize: both` nativo del navegador. Solo da la esquina, no deja
fijar mínimos útiles ni avisa cuándo terminó para guardar el tamaño.
Consecuencia: el borde izquierdo y el superior no redimensionan (habría que
mover la ventana a la vez); se arrastra y después se estira. Suite: 1510
pasan, 1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`). Comportamiento sin
verificar en navegador desde la sesión.

## 2026-09-10 — Chips de producto con ícono solo; el nombre al pasar por encima
Contexto: el operador pidió reemplazar el nombre de cada producto en los chips
por un ícono. Se le planteó ícono + nombre (hay productos sin ícono claro y en
pantalla táctil no hay «pasar por encima»); eligió ícono solo, con el nombre al
pasar el mouse.
Decisión: `product_nav.PRODUCT_ICONS` mapea slug → emoji para los cinco piloto
reales: arroz 🍚, pollo 🍗, picadillo 🥩, azúcar blanca 🍬, aceite 🫒.
`ProductChip.icon` lo trae; el chip muestra solo el emoji, con `aria-label` y un
tooltip CSS (`data-label` + `::after`) al pasar por encima o con el foco del
teclado. Prendido, aro en el acento en vez de relleno oscuro, para que el emoji
se siga viendo. El mismo ícono va delante del nombre en el panel del producto y
en la hoja de provincia: en táctil es donde se aprende qué ícono es qué. Un
piloto sin entrada en el mapa sigue mostrando su nombre. 🫒 y 🍬 no estaban en el
subconjunto vendorizado de Noto Color Emoji: `tools/build_emoji_font.py` ahora
suma `PRODUCT_ICONS` al vocabulario de interfaz y se regeneró la fuente (238 code
points, 521.572 B); un test nuevo en `test_static_assets.py` pone la suite en
rojo si un ícono queda fuera.
Descartado: una columna `icon` en `products` —cambio de esquema, pide el flujo
de especificación completo para un dato de presentación—. Descartado también
el `title` nativo: tarda en aparecer y duplicaría el tooltip propio.
Consecuencia: los íconos más flojos son 🍬 para azúcar (no hay emoji de azúcar)
y 🫒 para aceite (el aceite cubano es sobre todo de soya o girasol); se cambian
tocando una línea del mapa y corriendo la herramienta. Un producto nuevo que
pase a piloto aparece con su nombre hasta que alguien le asigne ícono. Suite:
1514 pasan, 1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Íconos de producto de Material Symbols, no emoji
Reemplaza a la entrada anterior («Chips de producto con ícono solo»).
Contexto: el operador aclaró que el proyecto está comprometido con Material, y
esperaba íconos de Material Symbols, no emoji.
Decisión: `PRODUCT_ICONS` pasa a nombres de ligadura de Material Symbols
Rounded. Verificado contra los 4.239 íconos del catálogo: solo el arroz tiene
uno propio (`rice_bowl`); el resto es la aproximación más cercana —pollo
`yakitori` (el único etiquetado «grilled chicken»), picadillo `skillet`,
azúcar `grain` (granulado), aceite `water_drop`—. El nombre sigue como tooltip y
etiqueta accesible. La hoja pública declara ahora Material Symbols y `.m3-icon`
(antes quedaba afuera: ninguna vista pública usaba íconos); es el mismo archivo
subseteado que el del operador. `tools/build_icon_font.py` suma
`PRODUCT_ICONS` a los íconos que junta de las plantillas; subconjunto regenerado
(38 íconos, 12.508 B) y `?v=` de la fuente subido a 3 en las dos hojas. Prendido,
el chip usa el relleno de acento de siempre: el ícono es monocromo. Se revirtió
lo del emoji: la fuente de Noto vuelve a la de `c9b6b68` y
`build_emoji_font.py` a su vocabulario original; el tripwire de íconos de
producto ahora mira el manifiesto de Material Symbols.
Descartado: mantener los emoji —Noto Color Emoji es la fuente de emoji de la
familia Material/Android y cubre mejor los alimentos, pero no es la
iconografía de Material—. Descartado dibujar íconos propios: rompe la
coherencia con el resto de la interfaz.
Consecuencia: cuatro de cinco íconos son aproximaciones; si alguno no se
entiende, se cambia en una línea del mapa y corriendo `build_icon_font.py`.
Suite: 1514 pasan, 1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — El chip muestra el nombre debajo del ícono
Contexto: con cuatro de cinco íconos aproximados, el operador pidió el nombre
visible debajo, chico y a 70% de opacidad, para que se entiendan de un vistazo.
Decisión: el chip de ícono pasa a dos líneas —ícono de 22px y `.ph-chip-name`
de 10,5px a opacidad .7 (.9 prendido)—, en rectángulo redondeado
(`--m3-radius-md`) porque la píldora se deforma con dos líneas. Se sacó el
tooltip (`data-label` + `::after`) y el `aria-label`: el nombre visible es ahora
el nombre accesible del enlace y el ícono sigue `aria-hidden`.
Descartado: mantener el tooltip además del nombre, que repetía lo mismo.
Consecuencia: los chips son más anchos (el más largo, «Azúcar blanca»); en
angosto el riel hace wrap. Suite: 1514 pasan, 1 saltea
(`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Gráficos del muestrario, slice 1: cifra con su muestra y rango por período en el panel
Contexto: el operador aceptó las recomendaciones del muestrario de gráficos
(12 formas comparadas con los 264 anuncios reales): panel de producto = cifra
con su muestra + rango por período; comparar provincias = lado a lado; «¿subió
o bajó?» = mes contra mes; y antes que nada, que un precio mal cargado no
deforme el eje.
Decisión: módulo nuevo `price_charts.py`, con el reparto de siempre (Python
agrupa y calcula coordenadas; la plantilla escupe SVG). Escala robusta:
`robust_fence` —cuartiles en escala logarítmica, 3 anchos de caja, nunca menos
de ×2— separa los precios fuera de escala, que se cuentan y se marcan con un
triángulo en el borde pero no estiran el eje ni mueven la cifra. Es de dibujo,
no de publicación: no decide qué se publica (§12.6). `build_range_series`: un
rango por período (semanas para 30 y 90 días, días para 7), contados hacia atrás
desde hoy, con los períodos sin anuncios vacíos y la cantidad de anuncios
debajo. `build_headlines`: mediana, rango y muestra de la ventana, y la mediana
del período anterior del mismo largo con su porcentaje de cambio —número, sin
flecha ni color de alarma—. El período anterior sale de pedir el doble de días y
quedarse con `día < hoy − ventana` (la ventana es `día ≥ hoy − ventana`: no se
pisan). El panel pasa a mostrar, por provincia, esa cifra y el rango
(`_range_chart.html`); `attach_blocks` empareja cifra y rango por producto,
provincia, moneda y unidad.
Descartado: una escala común a todas las provincias en el panel —aplasta las
celdas baratas— y la mediana nacional mezclando provincias.
Consecuencia: el panel ya no usa `_price_plot.html` (la hoja de provincia y la
ventana «Cómo se movió» sí, hasta los slices 2 y 3). Los 9 anuncios fuera de
escala del dato publicado se ven marcados; corregirlos es trabajo de curaduría.
Suite: 1531 pasan, 1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Gráficos del muestrario, slice 2: provincias lado a lado
Contexto: segunda recomendación aceptada del muestrario: responder «¿dónde está
más caro?» en la ventana Precios y en la hoja de provincia, sin colorear el mapa
por precio (eso sigue fuera de alcance).
Decisión: `price_charts.build_province_comparison` arma, por producto × moneda ×
unidad, una fila por provincia con rango, mitad central (cuartiles), mediana y
muestra, todas en un eje común; la más cara (por mediana) arriba. Cada provincia
lleva su propio cerco robusto —es su celda la que dice qué precio es imposible—,
pero comparten eje porque la pregunta es comparar. Unidades distintas no
comparten eje. Se calcula con TODAS las provincias aunque haya una elegida:
la ventana Precios la muestra al principio (para los productos prendidos, o
todos) con la provincia elegida resaltada; la hoja de provincia la muestra por
producto con la suya resaltada y las demás atenuadas, y deja de dibujar la
evolución por provincia (`_price_plot.html`), que queda para «Cómo se movió».
Partial `_province_compare.html`, una sola tinta: la identidad la da el nombre.
Descartado: un color por provincia —con el nombre en la fila no hace falta, y
un par de colores más obligaba a validar otra paleta—.
Consecuencia: `_render_home` hace una consulta más de observaciones (sin filtro
de producto ni provincia) para la comparación; con el volumen de la Fase 0 no
pesa, y se puede compartir con la del panel si hace falta. Suite: 1539 pasan,
1 saltea (`PYTHONPATH=<worktree>/src -p no:randomly`).

## 2026-09-10 — Gráficos del muestrario, slice 3: «Cómo se movió»
Contexto: la ventana «Cómo se movió» seguía con los puntos por anuncio del
gráfico viejo (`_price_plot.html`), que el operador descartó; el muestrario
aprobado le asignó mes contra mes, dos provincias en el tiempo y el calendario.
Decisión: `price_charts.build_evolution_blocks` arma un bloque por producto con
anuncios en la ventana: mes contra mes (mediana robusta del período anterior
del mismo largo contra la actual, con la muestra entre paréntesis y el cambio
en %, sin flecha ni color de alarma), la mediana por período de cada provincia
en escalones que se cortan donde no hubo anuncios (solo con dos períodos o más;
si no, lo dice), el calendario de días con anuncios y una tabla plegada con
cada anuncio y su enlace a `/anuncio/<id>`. Todas las provincias aunque haya
una elegida, como en Precios. Color por provincia fijo por orden alfabético
(`--m3-prov-0` / `--m3-prov-2`), siempre con leyenda y etiqueta directa.
Descartado: dejar la procedencia solo en el CSV. El gráfico viejo era el único
lugar de la home que enlazaba un precio a su anuncio; al reemplazarlo se perdía
(lo detectó `test_public_map`), y la tabla lo recupera y sirve además de vista
en tabla de los gráficos.
Consecuencia: `_price_plot.html` y sus clases `.ph-plot-*` / `.ph-serie*`
quedan sin uso; `series` sigue viajando a los paneles (`has_series`). Limpiarlos
es aparte. Con más de dos provincias los colores se repiten (`slot % 2`): hay
que sumar una tinta validada cuando entre la tercera. Suite: 1559 pasan,
1 saltea.

## 2026-09-11 — Minimizar ventanas: barra tipo Ubuntu en vez de título colapsado
Contexto: minimizar una ventana o panel dejaba solo su barra de título en el
lugar que ocupaba (apilada o flotante); el operador pidió el patrón de Ubuntu,
una barra de minimizados aparte.
Decisión: `.is-min` ahora oculta la ventana entera; un `<div data-taskbar>`
nuevo, armado por `actualizarTaskbar()` en el script del pie a partir de
`estado` y el DOM (sin estado propio), junta un botón por ventana minimizada
—ícono si lo tiene (paneles de producto) y nombre— y restaura al clic. Aparece
solo si hay algo minimizado. Franja fija al borde derecho del escenario,
igual en escritorio y en angosto (ajustado el mismo día: la primera versión
la ponía a la izquierda y la angosta la bajaba a una línea bajo los chips; el
operador pidió el mismo lado siempre). Con fondo y borde propios
(`--m3-surface` + `--m3-outline-variant`): es un panel de cosas abiertas, no
un chip más, y flotando sin límite no se leía como tal.
Descartado: reemplazar la bandeja inferior de angosto por la barra lateral
—compite por el ancho ya escaso de los chips; se optó por superponerla en vez
de tocar esa bandeja.
Consecuencia: `public_home.html` es el único archivo tocado (CSS y script
inline, sin JS nuevo por separado). `tests/test_static_assets.py` sigue en
verde (clases del taskbar definidas en el `<style>` local).

## 2026-09-11 — El chip de producto activo deja de cerrar su panel al clickearlo
Contexto: con las ventanas ya manejables por su cuenta (×, minimizar, la barra
de minimizados de hoy mismo), el operador notó que un chip activo seguía
actuando como apagador: un clic sobre un chip ya prendido lo togglea a
apagado y `product_panels` deja de incluirlo, así que la ventana desaparece
sin pasar por su propia ×. Lo señaló como error propio de la decisión original
(slice 2, 2026-09-10): dos controles —el chip y la ×— cerraban lo mismo, y el
chip lo hacía sin avisar.
Decisión: `build_product_chips` deja el conjunto activo sin cambios cuando el
chip clickeado ya estaba prendido (antes lo sacaba); prender uno apagado sigue
igual. Cerrar un panel es exclusivamente la × de `ProductPanel.close_query`
(`panel_view.py`), que no se tocó.
Descartado: deshabilitar el link del chip activo en el HTML (`pointer-events`
o quitar el `href`) — más superficie de cambio (plantilla + CSS) para el mismo
resultado; la solución en `product_nav.py` alcanza y no toca la plantilla.
Consecuencia: `test_togglear_un_prendido_lo_quita` (`test_public_filter.py`)
pasa a `test_un_chip_prendido_no_se_apaga_al_clickearlo`, con el aserto
invertido. El resto de los chips (íconos, orden, `aria-pressed`) no cambia.

## 2026-09-11 — Marcadores de conflicto sin resolver en `decisions.md`, limpiados
Contexto: al mergear el slice de la barra de minimizados apareció un conflicto
en este archivo; investigándolo aparecieron OTROS marcadores (`<<<<<<<` /
`=======` / `>>>>>>>`) ya commiteados en `master` desde antes, de un merge
viejo (`f479718`, "merge umbral-en-anuncios..."), alrededor de las entradas de
agosto de fotos de WAHA y de la sugerencia de descarte. Sin relación con el
slice de hoy.
Decisión: en los dos bloques, el lado `umbral-en-anuncios` estaba vacío —no
había contenido en conflicto real, solo los marcadores sin limpiar tras un
merge trivial—. Se quitaron los tres marcadores de cada bloque; el contenido
de las entradas de agosto queda intacto, tal como estaba del lado `HEAD`.
Descartado: nada — no había dos versiones que reconciliar, era puro
recorte de marcadores.
Consecuencia: se barrió el archivo entero con
`grep -n '^<<<<<<<\|^=======\|^>>>>>>>'` después del arreglo: cero coincidencias,
no quedó texto de conflicto sin resolver en ningún otro lado.

## 2026-09-11 — Test flaky de la descarga CSV: comparaba contra el UUID del anuncio
Contexto: `test_respeta_la_ventana` (`TestDescargaCsv`) buscaba `"999"` en el
texto entero del CSV descargado. La columna `anuncio` es `PriceHistory.id`,
un UUID al azar (`default=uuid.uuid4`, `price_history.py`): cada corrida trae
UUIDs nuevos para las cuatro filas de `_seed` más la propia, y de vez en
cuando alguno contiene la subcadena "999" por pura casualidad — falso
positivo latente, no relacionado con la ventana de días que el test dice
proteger.
Decisión: el test parsea el CSV con `csv.DictReader` y compara solo la
columna `precio_por_unidad`, nunca el texto crudo completo.
Descartado: fijar el `id` de la fila en el fixture (pasarle un UUID
constante a `_precio`) — tapa este caso puntual pero no la clase de bug: CUALQUIER
aserto de substring futuro contra el texto entero del CSV puede volver a
chocar con la columna `anuncio`.
Consecuencia: ninguna otra aserción de esta clase compara contra el texto
crudo con un valor numérico (las demás son nombres de producto/provincia,
que un UUID hexadecimal no puede formar), así que no hay otro caso latente
igual en este archivo.

## 2026-09-11 — Acordeón «qué es este gráfico» en los 5 gráficos de la home pública
Contexto: el operador entiende sus propios gráficos de un vistazo (trasfondo
técnico), pero notó que alguien sin ese trasfondo mirando el dashboard
público quizás no. Preguntado el patrón de UI (nada de hover: no existe en
táctil, mismo criterio que los chips), eligió un acordeón debajo de cada
gráfico antes que un popover o un modal aparte.
Decisión: `_chart_help.html` define la macro `chart_help(texto)` —un
`<details>` nativo, sin JS, mismo patrón que `.ph-evo-anuncios` (la tabla de
anuncios plegada)—, importada una vez en `public_home.html` y llamada justo
debajo de cada uno de los 5 gráficos activos: `_range_chart.html` (panel de
producto), `_province_compare.html` (ventana Precios y hoja de provincia,
dos sitios), `_slope_chart.html`, `_steps_chart.html` y `_calendar_chart.html`
(los tres de «Cómo se movió»). Cada llamada lleva su propio texto, describiendo
la codificación visual real de ESE gráfico (qué es la barra, la raya, el
punto, el color) en español llano. El ícono `info` ya estaba vendorizado
(lo usan `control.html`/`curation.html`): no hizo falta tocar
`tools/build_icon_font.py`.
Descartado: (a) popover flotante — más CSS/posicionamiento para el mismo
resultado y riesgo de cortarse en ventanas chicas; (b) modal aparte — más
pasos para leer un párrafo corto; (c) una sola macro genérica sin texto por
gráfico — cada gráfico tiene una codificación visual distinta, un texto
único no serviría a ninguno bien.
Consecuencia: cerrado por defecto, no cambia el layout ni los tests de
conteo existentes (`ph-fpanel`, `data-win-min`, etc., ninguno cuenta
`<details>` ni `<svg>` genéricamente). Producto en el catálogo sin gráfico
en pantalla (p. ej. `_price_plot.html`, ya sin uso) no lleva acordeón.

## 2026-09-11 — Barra de minimizados (taskbar) removida: el menú del header alcanza
Contexto: probada en producción, el operador decidió que la barra de
minimizados estilo Ubuntu (`70adb68`, ajustada en `539c32a`) no hace falta —
el menú del header (`.ph-menu`, abre/cierra cada ventana con su propio ítem)
ya cubre esa necesidad.
Decisión: revertidas manualmente las piezas de esas dos entradas —CSS
(`.ph-taskbar`, `.ph-tb-item`, `.ph-tb-icon`, `.ph-tb-name`), el
`<div data-taskbar>` del markup y la función `actualizarTaskbar()` con sus
tres puntos de enganche (`pintarTodas`, el click handler, el restore por
`data-tb-target`)—, sin tocar `git revert` porque las tres commits
siguientes (chip que no cierra el panel, test del CSV, acordeón de los
gráficos) tocan el mismo archivo y un revert automático las hubiera
peleado. Minimizar vuelve a su comportamiento previo: colapsa la ventana a
su barra de título, en el lugar donde estaba (apilada o flotante), sin
sidebar.
Descartado: dejar el botón de minimizar pero sin ningún lugar para
restaurar la ventana —la habría dejado inalcanzable—.
Consecuencia: `public_home.html` vuelve a su forma de antes del slice de
hoy en esas piezas puntuales; el chip que no cierra el panel, el fix del
test del CSV y el acordeón de los gráficos quedan intactos, no tienen
relación con la barra.

## 2026-09-11 — Las tres deudas del rediseño (slice de cierre, 2026-09-10): saldadas
Contexto: el operador pidió resolver de una las tres deudas que el slice de
cierre parcial del rediseño había dejado anotadas «para cuando el operador
revise el rediseño completo y decida cuánto recortar».
Decisión, en tres partes:
1. **Trampa de foco + Escape en la hoja de provincia.** El `<h2 id="ph-sheet-
   title">` gana `tabindex="-1"` para poder recibir foco por script.
   `manejarFocoDeHoja()` (enganchada a `htmx:afterSettle` y a la carga
   inicial) compara si el título está presente contra si lo estaba en el
   swap anterior (`provinciaAbierta`): recién abierta, el foco entra al
   título; recién cerrada, vuelve al enlace de esa provincia en el mapa
   (por `aria-label`, si sigue ahí). Un `keydown` global cierra con Escape
   (clickea `.ph-sheet-x`, que htmx ya intercepta) y atrapa Tab/Shift+Tab
   dentro de `.ph-sheet` (`agarreDeFoco`, sobre `a[href]`,
   `button:not([disabled])`, `input:not([disabled])`,
   `[tabindex]:not([tabindex="-1"])`).
2. **Unificada la hoja con el filtro `?provincia=` de Precios.** La hoja ya
   mostraba, por producto, la misma `_province_compare.html` con la
   provincia resaltada que Precios —mismo partial, mismo `resaltar`, dos
   veces en pantalla a la vez—. Con `selected_province` puesto, Precios deja
   de repetir ese bucle y muestra un aviso (`.ph-note`) que apunta a la hoja
   abierta.
3. **Recortado Precios contra el resumen de los paneles.** Un chip prendido
   ya abre su panel con el desglose por provincia (`build_product_panels`);
   la comparación de Precios para ESE MISMO producto era el mismo dato en
   otra forma. El bucle de comparación de Precios pasa a excluir los
   productos con panel activo (`comp.product_name not in active_products`);
   sin nada prendido, sigue mostrando todos, como antes.
Descartado: convertir la hoja en un `<dialog>` nativo —ganaría el atrapafoco
gratis, pero exige que el script llame `.showModal()` en cada apertura
(la hoja es HTML servido por htmx, no hay «mount» de componente) y su fondo
(`::backdrop`) no es el `.ph-sheet-scrim` ya construido; más cambio para el
mismo resultado. Descartado también mostrar la comparación de Precios para
un producto SIN chip mientras hay uno CON chip —dejaría huecos raros en el
medio del bucle sin ganar nada—.
Consecuencia: dos tests nuevos en `TestProvinciasLadoALadoEnLaRuta`
(`test_public_filter.py`) cubren 2 y 3; el string exacto del `<h2>` en
`test_provincia_en_la_url_abre_la_hoja` se actualizó por el `tabindex="-1"`
nuevo. Sin test de JS —el proyecto no tiene runner de JS, igual que el resto
del script de ventanas—. Suite: 1561 pasan, 1 saltea.

## 2026-09-11 — Zoom a la provincia elegida y capa «densidad de anuncios» en el mapa
Contexto: el operador (trasfondo GIS/ArcGIS) pidió dos cosas para el mapa:
(1) que elegir una provincia la encuadre visualmente, no solo abra la hoja
lateral; (2) un selector de capas, en el sentido GIS del término —varias
formas de sombrear la misma geometría—. Coloreo por precio y semáforo siguen
bloqueados por el umbral sin calibrar (§12.6); la única capa nueva que no
choca con eso es la densidad de anuncios, relativa al máximo actual.
Decisión, en dos partes:
1. **Zoom.** `actualizarZoomDelMapa()` (script del pie, sin animación) lee
   `data-provincia` de `.ph-stage` (server-side, `selected_province_slug`),
   calcula el `getBBox()` del `<path>` de esa provincia y reescribe el
   `viewBox` del SVG para encuadrarla (margen ×1.4, tope 6× para no
   pixelar una provincia chica). `.ph-map-province` gana
   `vector-effect: non-scaling-stroke` para que el borde no se engrose con
   el zoom. Sin JS, el mapa se queda en Cuba entera; la hoja sigue andando
   igual —mejora progresiva, no requisito.
2. **Capa «densidad».** `MapProvince.density_ratio` (`map_view.py`) es
   `rows / max(rows de las 16)`, 0.0 sin ninguna observación en ningún
   lado — relativo, nunca un umbral fijo, así que no choca con §12.6: no
   decide qué se publica, solo cómo se sombrea. `?capa=densidad` (nuevo eje,
   `product_nav.resolve_layer`/`build_layer_nav`/`LAYER_CHOICES`) se
   enhebra por `_query`/`products_query` igual que `ver`, así que
   sobrevive a cualquier otro clic de la página (chips, provincia, días,
   ventanas, cerrar un panel). El selector vive arriba de la leyenda del
   mapa, con la misma pinta que `.ph-menu-item`.
Descartado: (a) animar el zoom con `requestAnimationFrame` interpolando el
`viewBox` a mano, o con `transform` CSS sobre un `<g>` —la segunda tiene
ambigüedad real de unidades (px CSS vs. unidades de usuario del `viewBox`)
entre navegadores; un corte directo es la solución más simple que
funciona, y la hoja ya entra animada al lado; (b) bucketes discretos para
la densidad (como los 4 niveles del calendario) —con conteos por provincia
en un rango mucho más amplio que los del calendario, una opacidad continua
es más honesta que elegir cortes a mano; (c) etiquetar los extremos de la
rampa con nombres de provincia —el número solo (`map.provinces[0].max_rows`)
alcanza y no repite el nombre que ya está en el `<title>` de cada camino.
Consecuencia: `capa` se suma a `_query`/`products_query` y a
`build_product_panels` (el `close_query` de un panel también la
preserva) — todo sitio que ya enhebraba `ver` ahora enhebra `capa` igual.
12 tests nuevos (`TestDensidadDeAnuncios`, `TestCapaDelMapa` en
`test_public_map.py`; `TestResolveLayer`, `TestBuildLayerNav` en
`test_public_filter.py`). Sin test de JS para el zoom, mismo criterio que
el resto del script de ventanas. Suite: 1573 pasan, 1 saltea.

## 2026-09-11 — «Cómo se movió» respeta la provincia elegida (reemplaza el diseño de 2026-09-10)
Contexto: al cerrar el zoom del mapa, el operador señaló el hueco real:
elegir una provincia dice «solo me interesa ver los datos de esa provincia»,
y «Cómo se movió» seguía mostrando las 16 siempre —la única ventana que no
se enfocaba; Precios y los paneles de producto ya lo hacían—.
Decisión: reemplaza el diseño original de la ventana («Rediseño home
pública, Slice 3», 2026-09-10, y la nota de «La home pública es el mapa
entero», mismo día), que fijaba TODAS las provincias a propósito «porque la
pregunta es ¿dónde está más caro?». Con `?provincia=` puesta, `evolution_obs`
y `evolution_previous` se filtran a esa sola provincia antes de pasar a
`build_evolution_blocks` —filtrado en Python sobre lo que `all_provinces_obs`
ya trajo, sin otra consulta a la base—. `province_comparisons` (Precios y la
hoja de detalle) NO cambia: sigue con las 16 siempre, porque esa pregunta
—«¿dónde está más caro?», con la propia resaltada— sigue siendo válida ahí.
El rótulo de la ventana y del calendario pasan a decir el nombre de la
provincia en vez de «todas las provincias» cuando hay una elegida.
Descartado: filtrar también `province_comparisons` —rompería la hoja de
detalle, que reusa esos mismos datos para su propio gráfico «dónde está más
cara [la mía]» comparándola contra las otras 15—.
Consecuencia: con una sola provincia, la leyenda de colores por provincia
del bloque no aparece (`block.legend | length > 1`, ya condicional) y el
gráfico de escalones/pendiente queda con una sola línea — comportamiento
correcto, no un caso raro. Test viejo
`test_con_una_provincia_elegida_sigue_comparando_todas` (afirmaba lo
contrario) reemplazado por `test_con_una_provincia_elegida_solo_esa`, con el
aserto invertido. Suite: 1573 pasan, 1 saltea.

## 2026-09-11 — Hoja de provincia a pantalla completa y translúcida («modo provincia»)
Contexto: el operador preguntó por una vista 3D del mapa o algo al estilo
Street View de Google Maps —«entrar» a un lugar en vez de solo verlo desde
arriba—. 3D queda descartado: choca con la decisión cerrada 1 de CLAUDE.md
(mapa SVG servido por el servidor, nada de motor de mapas ni dependencia de
red en runtime, por la conexión del usuario final cubano). La alternativa
que sí encaja con el stack: la hoja de detalle, que ya reacciona al zoom del
mapa (2026-09-11, esta misma sesión), pasa a ocupar toda la pantalla y a ser
translúcida en vez de opaca, así el mapa ya encuadrado en esa provincia se
sigue viendo de fondo.
Decisión: `.ph-sheet` pasa de cajón lateral (`min(28rem, 100%)`) a
`width: 100%` en escritorio, y de fondo opaco a
`color-mix(in srgb, var(--m3-surface) 92%, transparent)` —mismo porcentaje
que ya usan `.ph-topbar` y `.ph-canvas-legend`, no un valor nuevo—. En
angosto pasa de `max-height: 85dvh` a `height: 100dvh` para la misma
consistencia. Sin `backdrop-filter`: desenfocar el fondo pesa en un teléfono
de gama baja, y un alfa simple ya deja ver el mapa detrás sin costo de GPU.
Descartado: (a) un mapa 3D real (three.js/WebGL) —contradice la decisión
cerrada 1 y no hay ninguna dimensión de dato que pida altura—; (b) duplicar
el SVG del mapa dentro de la hoja como hero recortado a esa provincia —el
mapa de fondo, ya zoomeado por `actualizarZoomDelMapa()`, alcanza sin
repetir geometría ni Python nuevo.
Consecuencia: cambio puramente visual (CSS), sin tocar Python ni el
contrato de ningún test — la suite corre igual. La segunda idea del
operador (una vista 2D con otro enfoque, tipo Street View) queda pendiente,
sin definir todavía.

## 2026-09-11 — Vista cercana de provincia: pinta por densidad, no por estado
Contexto: continuación de la entrada anterior. El operador retomó la
«segunda idea» (vista 2D con otro enfoque, tipo Street View) y, ante la
pregunta de qué mostrar de cerca, se descartó cualquier variante que
implicara ubicación real dentro de la provincia: el modelo no tiene
municipio ni lat/long, solo provincia (`src/saimc/models/`), así que
cualquier posición más fina que eso sería inventada —§12.6 lo prohíbe—.
Decisión: cuando hay una provincia elegida (`selected_province`), el SVG
fuerza la capa «densidad» (`MapProvince.density_ratio`, ya existente desde
la capa de densidad del mapa completo) en vez del color por estado de
publicación, sin importar qué capa esté elegida en el selector manual del
menú —que sigue mostrando «publicación» como default y no cambia—. Un solo
condicional en `public_home.html`: `{% if selected_layer == 'densidad' or
selected_province %}`.
Descartado: colocar chips/íconos de producto como capa espacial sobre la
silueta agrandada —el operador prefirió la opción más simple, sombrear la
forma misma—.
Consecuencia: cambio de plantilla, sin Python nuevo. Tests nuevos en
`TestCapaDelMapa` (`tests/test_public_map.py`): con provincia elegida pinta
`is-densidad` aunque `capa` no se haya tocado; sin provincia elegida, la
capa de publicación sigue de estado. Suite: 1575 pasan, 1 saltea.

## 2026-09-11 — «Guani» como avatar/nombre cercano, SAIMC como nombre técnico

Contexto: con una presentación la semana próxima, el operador pidió sumar
identidad de marca al sitio público: le gusta «Guani» —el codename interno
del proyecto, ya usado en CLAUDE.md y en `docs/decisions.md` antes de esta
entrada— como nombre cercano/avatar, con SAIMC quedando como nombre técnico
al lado, sin desaparecer. Pidió además un muñequito para Guani.

Decisión — texto: `<title>` pasa de «SAIMC — …» a «Guani — …»
(`_public_shell.html`); el kicker pasa de «SAIMC · Fase 0» a
«Guani · SAIMC · Fase 0» en las dos plantillas que lo muestran
(`_public_shell.html`, `public_home.html`). Alcance deliberado: la
herramienta interna del operador (`_shell.html`, curaduría/control) queda
sin tocar — no es lo que se muestra en una presentación, y no la pidió.

Decisión — avatar: un tocororo (ave nacional de Cuba) estilizado en trazo
flat, bocetado primero en un canvas de Claude Design (a pedido del
operador) y después bajado a `_guani_avatar.html`, un SVG inline reusable
por `{% include %}`. Sus siete colores son hex fijos, no `var(--m3-*)`: un
SVG no resuelve custom properties de una hoja externa sin duplicar el
bloque de estilos, así que se citó cada hex junto al nombre de su token de
origen en un comentario, y quedan atados a mano a `design_tokens.css` si
alguno cambia — mismo trato que ya reciben los colores de provincia. Vive
en el header de `public_home.html` (`.ph-brand-row`, nuevo, envolviendo el
avatar + el `.ph-brand` de siempre).

Descartado: poner el mismo avatar en `_public_shell.html` (la página
`/anuncio/<id>`). Al armarlo se encontró que `.m3-kicker`/`.m3-title`/
`.m3-public-top` —las clases que ese shell ya usaba— nunca se migraron a
`saimc-public.css` en el split del 2026-09-04: existen en
`saimc-material.css` (la hoja del operador) pero no en la pública, así que
esa página hoy renderiza su título y kicker sin ningún estilo. Meter un
avatar prolijo al lado de texto sin estilar habría quedado peor, no mejor.
Es un hallazgo aparte, no el trabajo de esta sesión — queda anotado acá
para no perderlo, no resuelto.

Consecuencia: `tests/test_public_filter.py` — título actualizado
(`test_el_titulo_refleja_el_producto_enfocado`) y test nuevo
(`test_el_kicker_muestra_guani_y_saimc_juntos`). Ocho asserts en
`tests/test_public_map.py` que hacían `texto.split("<svg")[1]` para aislar
el SVG del mapa dejaron de servir —el avatar es ahora el primer `<svg` de
la página— y pasaron a anclar en `'<svg class="ph-map"'`. Suite: 1576
pasan, 1 saltea.

## 2026-09-11 — El avatar de Guani dobla de indicador de carga

Contexto: siguiendo con la marca de la entrada anterior, el operador pidió
un indicador de carga hecho con el mismo avatar, para los `hx-get` de
chips/menú/provincia en la home pública (que hoy no tienen ninguno).

Decisión: `#ph-live` suma `hx-indicator="#ph-loading"`; `#ph-loading` —el
mismo `_guani_avatar.html`, con una animación de rebote— vive FUERA de
`#ph-live`, después de su cierre, no adentro: ese bloque entero se
reemplaza (`hx-swap="outerHTML"`) en cada respuesta, así que un indicador
adentro perdería la clase `htmx-request` a mitad de la animación en vez de
apagarse prolijo. Es la convención estándar de htmx (clase `htmx-indicator`
más opacidad condicional), sin librería nueva. `prefers-reduced-motion`
apaga el rebote, mismo trato que ya reciben las otras animaciones del
lienzo.

Consecuencia: test nuevo en `tests/test_public_filter.py`
(`test_el_indicador_de_carga_usa_el_avatar_y_vive_fuera_de_ph_live`), que
verifica la posición fuera de `#ph-live` anclando en el crédito del mapa
(«Natural Earth»), no en el comentario Jinja —ese no sobrevive al render—.
Suite: 1577 pasan, 1 saltea.

Aparte, se agregó una diapositiva de cierre (16:9, avatar grande sobre
fondo `--ph-accent`, «Gracias») como segundo artboard del mismo canvas de
diseño de la entrada anterior — no es código, vive solo en el canvas
publicado.

## 2026-09-11 — El avatar de Guani deja de ser un tocororo

Contexto: el operador corrigió el boceto del tocororo (dos entradas atrás):
«Guani» es un término aborigen cubano, no un ave — pidió explícitamente un
avatar taíno. Se le preguntó si lo quería como figura humana estilizada o
como símbolo geométrico/petroglifo (más fiel a lo que realmente dejaron los
taínos, que no eran figurativos); eligió figura humana, «lo más profesional
posible».

Decisión: `_guani_avatar.html` reemplaza el tocororo por un busto de
cacique/cacica en el mismo trazo flat — proporciones normales, expresión
serena, sin caricaturizar. Un solo elemento de adorno: una pluma sobre una
vincha, no un tocado completo, que es ornamento de las culturas de las
llanuras de Norteamérica, no del Caribe —confundirlas es el estereotipo más
común y el que se quiso evitar a propósito—. El boceto se armó primero en
el mismo canvas de Claude Design de las dos entradas anteriores (URL en la
entrada del avatar original) y quedó con una nota propia adentro marcando
que el ornamento sigue una referencia taína/caribeña más ajustada, pero que
vale revisión con más fuentes antes de darlo por definitivo — no se trató
como cerrado, se trató como primer boceto.

De los seis colores del SVG, cinco son nuevos —piel, cabello, vincha,
vestimenta, cuentas— y no existían en el sitio; el sexto (la pluma) reusa
`--m3-warn`. Documentados en el comentario del archivo, mismo trato que ya
recibía la paleta del tocororo.

Descartado: quedarse con la figura como símbolo abstracto/petroglifo — el
operador prefirió la figura humana explícitamente.

Consecuencia: el reemplazo es transparente para el código — mismo archivo,
mismo `class="ph-brand-avatar"`, mismo uso en kicker/indicador de
carga/diapositiva de cierre —, así que ningún test cambió. Suite: 1577
pasan, 1 saltea, sin roturas.

## 2026-09-11 — El indicador de carga se centra y se achica al 10%

Contexto: el operador pidió que el indicador de carga cubra toda petición
(ya lo hacía: `hx-indicator` se hereda desde `#ph-live`, y los doce
`hx-get` de la vista —chips, menú, provincias, ×, capa— viven todos
adentro), y que en vez de la esquina superior derecha vaya centrado en la
pantalla, con opacidad, al 10% del tamaño que tenía.

Decisión: `.ph-loading` pasa de `top/right: 1rem` a
`top: 50%; left: 50%; transform: translate(-50%, -50%)`, y de 2.5rem a
.25rem (10% exacto). Al rebote (`ph-loading-bob`, un `translateY`) lo
reemplaza un pulso de opacidad (`ph-loading-pulse`, 1 → .35 → 1): a 4px un
rebote se sale de su propia caja y se ve roto, y un pulso de opacidad es
además lo que el operador pidió con «con opacity» y el patrón usual de un
punto de carga chico.

Consecuencia: solo CSS, mismo marcado (`_guani_avatar.html`,
`hx-indicator="#ph-loading"`), ningún test tocado. Suite: 1577 pasan, 1
saltea.

## 2026-09-11 — Indicador de carga: +25% de tamaño, opacidad visible a .4

Contexto: ajuste fino del operador sobre la entrada anterior (centrado +
10% del tamaño original).

Decisión: `.ph-loading` pasa de `.25rem` a `.3125rem` (+25% sobre el valor
anterior, no sobre el original de 2.5rem). La opacidad visible
(`.htmx-indicator.htmx-request`) pasa de `1` a `.4`. El keyframe del pulso
(`ph-loading-pulse`) se deja en `1 ↔ .35` sin tocar a propósito: anima la
opacidad del SVG de *adentro*, que se multiplica con la de `.ph-loading`
—CSS anida opacidades—, así que el rango visible real ya cae solo en
`.4 → .14` sin reescalar el keyframe.

Consecuencia: solo CSS. Suite: 1577 pasan, 1 saltea.

## 2026-09-11 — Indicador de carga: +129% más, el operador no lo veía

Contexto: después del ajuste anterior (+25%, opacidad .4) el operador
reportó que seguía sin verlo. A .3125rem (5px) con techo de opacidad en .4
—y el pulso llegando a .14— es un punto casi imperceptible; probable causa,
no confirmada, de por qué no se veía.

Decisión: `.ph-loading` pasa de `.3125rem` a `.715625rem` (+129% sobre el
valor anterior, no sobre el original). Opacidad y posición quedan como en
la entrada anterior — no se tocaron, el pedido fue solo de tamaño.

Consecuencia: solo CSS. Suite: 1577 pasan, 1 saltea. Si a este tamaño
tampoco se ve, el sospechoso que sigue es la opacidad tope de .4 (con el
pulso llegando a .14), no el tamaño.

## 2026-09-11 — Indicador ×3 más grande; el avatar deja los colores figurativos por tokens M3

Contexto: el operador pidió dos cosas en el mismo pedido — el indicador de
carga «tres veces el tamaño actual», y sacarle al avatar «esos colores
vivos» para usar «la misma estética que manejamos en Material en todo el
sistema». Lo primero es continuación directa de la entrada anterior:
`.ph-loading` pasa de `.715625rem` a `2.146875rem` (×3), opacidad y
posición sin tocar.

Lo segundo es más de fondo. v2 (dos entradas atrás) había introducido cinco colores nuevos
—piel, cabello, vincha, vestimenta, cuentas— que no existían en ningún
otro lado del sitio: exactamente lo que la decisión del split de
`saimc-public.css` (2026-09-04) y `TestNingunColorFijoEnLasPlantillas`
vienen evitando. El tocororo original nunca había roto esa regla —sus
siete colores ya eran tokens—; se rompió recién al pasar a la figura
taína.

Decisión: `_guani_avatar.html` recolorea el mismo busto (mismas formas,
mismo trazo) con cuatro tokens existentes: cara `--ph-accent` (#05668d),
cabello y vincha `--m3-on-surface` (#191c1c), pluma `--m3-warn` (#e0a700,
sin cambios — ya era un token en v2), vestimenta `--ph-accent-2`
(#028090). Ojos, boca y cuentas del collar pasan a `--m3-surface`
(#ffffff) para tener contraste contra la cara ahora teal. El resultado es
un ícono de marca en la paleta del sitio, no una ilustración figurativa —
ningún color implica un tono de piel real, lo cual de paso separa el
avatar del terreno resbaladizo de elegir un tono de piel.

Consecuencia: mismo archivo, misma clase, mismo uso en las tres
ubicaciones (kicker, indicador de carga, diapositiva de cierre del
canvas — actualizada también). Ningún test cambió. Suite: 1577 pasan, 1
saltea.

## 2026-09-11 — Avatar con margen en el badge, versión de cuerpo entero

Contexto: el operador calificó el badge de feo, pidió una versión de
cuerpo entero y padding adentro del círculo. De paso pidió un «toggle para
meter tema oscuro».

Decisión — badge (sitio real): `_guani_avatar.html` — el dibujo llegaba
justo al borde del círculo recortado. Se le agregó
`transform="translate(40,40) scale(.8) translate(-40,-40)"` al `<g>` del
contenido: achica todo un 20% alrededor del centro sin tocar ninguna
coordenada del dibujo, dejando un margen visible de fondo blanco.

Decisión — cuerpo entero (solo canvas de diseño): nueva silueta de pie
—piernas, brazos, torso, más la misma cabeza que v3— en el mismo canvas de
Claude Design, reemplazando al busto en la tarjeta «presentación» y en la
diapositiva de cierre. Misma paleta de cuatro tokens que v3, sin colores
nuevos. No se agregó al código: hoy no hay ningún lugar del sitio que use
una figura de cuerpo entero, solo el badge circular (header/indicador).

Decisión — toggle de tema oscuro: se agregó SOLO al canvas de diseño, como
tweak (`data-props` booleano «Vista») que prueba el avatar y sus mockups
contra un fondo oscuro. **No es un tema oscuro del sitio** — el sitio no
tiene ninguno hoy, y esto no lo crea. Se interpretó así porque el pedido
llegó en el mismo mensaje que el feedback visual del avatar, sin mención de
alcance del sitio; si lo que se quiere es un tema oscuro real en producción
es un trabajo aparte (toca `design_tokens.css`, cada plantilla, y un
control persistente), a discutir como su propio slice.

Consecuencia: cambio de código real limitado al `transform` del badge —
ningún test lo cubre porque ninguno afirma coordenadas del dibujo, y la
suite sigue en 1577/1. El resto (cuerpo entero, toggle) vive solo en
https://claude.ai/code/artifact/a9374a81-35e8-479d-ab7b-00b747f3e913.

## 2026-09-11 — Indicador de carga ×5 y con fondo de superficie del sistema

Contexto: el operador pidió, de nuevo en un solo mensaje, agrandar el
indicador de carga ×5 y «usar los colores de fondo que hay en el sistema».

Decisión: `.ph-loading` pasa de `2.146875rem` a `10.734375rem` (×5). Para
el fondo, en vez de solo agrandar el SVG, se le agregó un fondo propio al
contenedor: `color-mix(in srgb, var(--m3-surface) 92%, transparent)` —el
mismo `color-mix` que ya usan `.ph-topbar` y `.ph-canvas-legend`
(`design_tokens.css`), no un color inventado— más `box-shadow:
var(--m3-elev-2)` y `border-radius: 999px`. Con `padding: .75rem` y
`box-sizing: border-box`, ese fondo asoma como un anillo alrededor del
badge blanco propio del SVG (que ya tenía su margen interno desde la
entrada anterior) — dos superficies concéntricas, ninguna con un color que
no estuviera ya en `design_tokens.css`.

Consecuencia: solo CSS, mismo marcado. Suite: 1577 pasan, 1 saltea.

## 2026-09-11 — Indicador ×5 otra vez (~859px) y su paleta a gris y blanco

Contexto: el operador pidió, en el mismo mensaje, multiplicar ×5 de nuevo
el indicador de carga y «los colores quiero que sean gris y blanco como se
usan en todo el sistema» — explícitamente sobre «el avatar que se usa en
el cargador», no el avatar en general.

Decisión — tamaño: `.ph-loading` pasa de `10.734375rem` a `53.671875rem`
(×5, ≈859px — el operador lo pidió así de grande, se avisa el número
exacto en vez de asumir que es un error).

Decisión — color, solo para esta instancia: `_guani_avatar.html` es
compartido con el header, que sigue con los colores de marca (teal/dorado)
— no era eso lo que se pidió. Se le agregaron tres clases sin estilo propio
(`ph-avatar-cara`, `ph-avatar-pluma`, `ph-avatar-vestimenta`) a esos
elementos, como ganchos. `public_home.html` las recolorea, acotado a
`.ph-loading svg`, a tokens grises reales: `--m3-outline` (cara),
`--m3-outline-variant` (pluma), `--m3-disabled` (vestimenta) — todos
`var(--m3-*)`, no hex, para no pisar
`TestNingunColorFijoEnLasPlantillas`. Cabello/vincha (#191c1c) y blancos
quedan igual, ya eran neutros.

Intento descartado: selectores de atributo `[fill="#05668d"]` etc.
—funcionan en CSS, pero meten hex literal en el `<style>` de la plantilla
y rompieron `test_ninguna_plantilla_declara_un_color_a_mano` al primer
intento; las clases lo resuelven sin ese costo.

Consecuencia: solo CSS + tres atributos `class` sin comportamiento propio
en el SVG compartido —el header no cambia—. Suite: 1577 pasan, 1 saltea.

## 2026-09-11 — El gris y blanco pasa a ser el único color del avatar

Contexto: el operador achicó el indicador (÷3) y, de paso, pidió que «el
otro avatar de Guani que está en el menú» —el del header, junto a
`.ph-menu`— usara también los colores gris/blanco de la entrada anterior.
Con eso, la distinción por instancia que introdujo esa entrada (gris solo
en el cargador, marca en el header) queda sin motivo: las dos usan la
misma paleta.

Decisión: `_guani_avatar.html` — v5. El default del archivo pasa
directamente a los tokens grises (`--m3-outline` cara, `--m3-on-surface`
cabello/vincha —sin cambio—, `--m3-outline-variant` pluma, `--m3-disabled`
vestimenta, `--m3-surface` blancos —sin cambio—). Se sacan las tres clases
`ph-avatar-*` (ya no hace falta un gancho: no hay dos paletas que
distinguir) y el override en `.ph-loading svg` de `public_home.html`,
ahora redundante. `.ph-loading` pasa de `53.671875rem` a `17.890625rem`
(÷3).

Consecuencia: `_guani_avatar.html` vuelve a ser un archivo sin
distinciones por instancia —más simple que la vuelta anterior—. Ningún
test afirma colores del SVG, así que ninguno cambió. Suite: 1577 pasan, 1
saltea.

## 2026-09-11 — El avatar del indicador gira (flip) y suma tres punticos

Contexto: última petición de la tanda — el operador pidió que el avatar
del indicador «haga flip, animado» y que abajo aparezcan los tres puntos
de carga clásicos (uno, dos, tres puntitos).

Decisión: `#ph-loading` pasa de contener solo el avatar a contener avatar
+ `.ph-loading-dots` (tres `<span>`). Eso cambia la forma del contenedor:
de círculo ajustado al avatar (`border-radius: 999px`, tamaño = tamaño del
avatar) a tarjeta redondeada en columna (`--m3-radius-lg`, `display: flex;
flex-direction: column`) — con dos elementos que acomodar, el círculo
dejó de tener sentido. El avatar reemplaza el pulso de opacidad de dos
entradas atrás por `rotateY(0→360deg)` infinito; `perspective: 40rem` en
`.ph-loading` hace que se vea como una moneda girando (con
foreshortening) y no como un achicamiento horizontal plano. Los tres
puntos son el patrón de siempre: mismo radio, mismo `--m3-outline`, demora
escalonada 0/.2s/.4s.

Consecuencia: `prefers-reduced-motion` suma `.ph-loading-dots span` a la
lista de animaciones que apaga (ya tenía al avatar). Solo CSS + un `<div>`
nuevo con tres `<span>` vacíos; ningún test cambia. Suite: 1577 pasan, 1
saltea.

## 2026-09-11 — El contenedor del indicador deja de verse

Contexto: el operador pidió sacarle al contenedor todo lo visual —sin
borde, sin sombra, sin fondo—: «lo único que quiero ver es los punticos y
el flip del avatar».

Decisión: `.ph-loading` pierde `background`, `box-shadow`, `border-radius`
y `padding`. Queda puro layout: `display: flex; flex-direction: column`,
centrado y `perspective` (necesario para que el `rotateY` del avatar se
vea 3D). El avatar y los tres puntos flotan directo sobre lo que haya
detrás, sin tarjeta. No se tocó el círculo blanco propio del SVG del
avatar (`_guani_avatar.html`) — eso es parte del dibujo, compartido con el
header, no «el contenedor» del que hablaba el pedido.

Consecuencia: solo CSS. Suite: 1577 pasan, 1 saltea.

## 2026-09-11 — Cierre del panel de producto: UI optimista, no JS puro

Contexto: el operador observó que la × de un panel de producto dispara una
petición al server y propuso manejarlo con JS. Verificado antes de
responder: `close_query` (`panel_view.py`) no solo cierra la tarjeta —saca
el producto de `?productos=`— y de eso cuelgan cinco cosas que el cliente
no puede resolver solo: el estado del chip (`public.py:226`), el filtro de
«Comparar» (`public_home.html:793`), el de «Cómo se movió»
(`public_home.html:928`), el enlace del CSV, y el `selected =
active_products[0]` de una sola selección (`public.py:185`), que cambia el
`<h1>` y el `<title>`. JS puro que solo escondiera el `<section>` dejaría
esas cinco desincronizadas.

Decisión: UI optimista, elegida por el operador entre esa y «sin petición,
solo JS». El `hx-get` queda igual —el server sigue siendo la fuente de
verdad—; lo que se agrega es el adelanto visual: al tocar la ×, el JS
esconde el panel (`.is-cerrado`) y apaga su chip en el acto, y el swap de
htmx pisa todo cuando llega. Si la petición falla (conexión intermitente,
el caso cubano de CLAUDE.md), `htmx:responseError`/`htmx:sendError`
deshacen el adelanto y el panel vuelve, en vez de quedar escondido
mintiendo que se cerró.

Chips y paneles suman `data-product="<slug>"` para emparejarse: es el
contrato que el JS necesita y lo cubre un test nuevo
(`test_el_panel_y_su_chip_comparten_data_product`), porque perder ese
atributo de un lado no rompe nada visible — el chip simplemente deja de
apagarse, en silencio, el mismo modo de falla que
`TestClasesDelJavascriptExisten` ya vigila para las clases.

Consecuencia: un atributo nuevo en dos plantillas, una clase CSS, ~35
líneas de JS y un test. Suite: 1578 pasan, 1 saltea.

## 2026-09-11 — El cierre optimista se extiende a las ventanas del menú

Contexto: quedaba anotado como fuera de alcance en la entrada anterior; el
operador pidió hacerlo.

Decisión: `cerrarPanelOptimista` pasa a `cerrarOptimista` y cubre
`.ph-fpanel .ph-x` y `.ph-win .ph-x`. Las dos ramas difieren en dos cosas
y nada más, así que se resuelven con dos condicionales y ninguna
abstracción: cómo encontrar el disparador (el panel con su chip por
`data-product`; la ventana con su ítem de menú por el `aria-controls` que
ya apuntaba a su `id` — marca que YA existía, no se agregó nada) y qué
clase apagarle (`is-active` en el chip, `is-open` en el ítem).

Esconder es `.is-cerrado` para los dos, no el `hidden` que las ventanas
cerradas ya traen del server: son dos estados distintos —«el server dice
que está cerrada» y «el cliente se adelantó»— y mezclarlos haría que
deshacer el adelanto le pisara el estado real.

Consecuencia: test nuevo
(`test_la_ventana_y_su_item_de_menu_se_emparejan_por_aria_controls`), que
fija el emparejamiento del que ahora depende el JS. Suite: 1579 pasan, 1
saltea.

## 2026-09-11 — Selector de provincia visible siempre, junto a los chips

Contexto: el operador dijo que «el tema de las provincias no lo
convence» — el filtro vivía solo adentro de la ventana «Precios» (había
que abrirla para verlo). Pidió un `<select>` con un chip que muestre la
provincia elegida; confirmado antes de tocar nada que Cuba entera como
default y el filtrado real (precios, «Comparar», «Cómo se movió», CSV,
`selected` de un solo producto) YA funcionaban — lo que faltaba era
el control, no la lógica. Preguntado dónde vivía el `<select>` nuevo:
eligió junto al riel de chips de producto, siempre visible, en vez de
mover el filtro existente de la ventana «Precios» a un `<select>` en el
mismo lugar.

Decisión: nuevo `<form class="ph-province-form">` arriba del mapa, junto
al riel de chips — reusa `province_nav` (`build_province_nav`, mismo dato
que ya alimentaba la lista `ph-filter-province` de la ventana «Precios»,
que se deja como está: dos accesos al mismo filtro, no dos filtros
distintos). Es un `<form>` real con `hx-get` en vez de un `<select>`
leído por JS a secas: sin JS, el `onchange` no engancha nada y el submit
nativo —el botón que aparece por `<noscript>`— hace la navegación
completa igual, mismo principio de degradación que ya sostienen todos
los enlaces del resto de la página. Los campos ocultos (`productos`,
`dias`, `ver`, `capa`) preservan los otros ejes; van siempre, no solo
cuando difieren del default, para no repetir en Jinja los valores por
defecto que ya son responsabilidad de `product_nav.py` — el costo
conocido es algún parámetro de más en la URL cuando todo está en su
default, no un comportamiento roto.

Con una provincia elegida aparece `.ph-province-chip`, con el nombre y
una × (mismo patrón que panel/ventana) que apunta al primer choice de
`province_nav` («Todas») para volver a Cuba entera de un clic. No se le
sumó el cierre optimista de las dos entradas anteriores: cambiar de
provincia reescribe extensamente la página (zoom, hoja, título, `<h1>`),
no hay una sola caja local que esconder.

Consecuencia: cuatro tests nuevos en `TestFiltroPorProvinciaEnLaRuta`
(existencia del `<select>` y sus opciones, la opción elegida marcada, el
chip solo con provincia elegida, los campos ocultos preservando los otros
ejes). Suite: 1583 pasan, 1 saltea.

## 2026-09-11 — Con provincia elegida, el mapa deja de dibujar las otras 15

Contexto: el operador dijo que no le alcanzaba con «esa ventana flotante»
—la hoja de detalle—: quería que «todo el sistema se adecúe solamente
para esa provincia, incluso el mapa. Lo único que debe pintarse en el
mapa es esa provincia». Verificado antes de tocar nada: precios,
tarjetas, «Comparar» y «Cómo se movió» YA se acotaban a la provincia
elegida (entradas de sesiones previas); lo que NO estaba filtrado era el
mapa mismo — las 16 provincias seguían en el DOM, el zoom (`ZOOM_MARGEN`
en `actualizarZoomDelMapa`) solo recortaba el `viewBox`, así que bordes de
provincias vecinas asomaban dentro del encuadre.

Decisión: el `{% for province in map.provinces %}` del SVG suma
`{% if not selected_province or province.name == selected_province %}` —
con una elegida, las otras 15 ni se dibujan, no solo quedan fuera de
cuadro. `actualizarZoomDelMapa()` no cambia: sigue buscando el `<path>` de
la provincia elegida por `data-province`, que sigue ahí. El
`aria-label` del `<svg>` pasa a decir «Mapa de {{ provincia }}» en vez del
conteo de cobertura de las 16, que ya no describe lo que hay dibujado.

De paso, un bug encontrado al verificar: la leyenda de abajo del mapa
tenía su propia condición (`selected_layer == 'densidad'`) que NO conocía
`selected_province` —a diferencia del `<path>`, que sí fuerza densidad con
provincia elegida desde una entrada anterior—, así que con capa
«publicación» y una provincia elegida, el mapa pintaba por densidad pero
la leyenda seguía mostrando «Publicada/En ingesta/Sin datos» y el conteo
de las 16. Se le agregó la misma condición, y con provincia elegida el
conteo pasa a ser el de esa provincia sola («N en {{ provincia }}»), no el
total de las 16.

Consecuencia: dos tests nuevos en `TestCapaDelMapa`
(`test_con_provincia_elegida_el_mapa_pinta_solo_esa`,
`test_la_leyenda_de_densidad_cuenta_solo_esa_provincia`). Suite: 1585
pasan, 1 saltea.

## 2026-09-12 — La hoja de detalle de provincia se saca del todo

Contexto: después de mergear la entrada anterior, el operador reportó que
seguía saliendo «ese flotante que no me deja ver la provincia» — la hoja
de detalle a pantalla completa (slice 5, 2026-09-10) se abre
automáticamente con toda `?provincia=`, sin forma de elegir provincia sin
que se abra. Preguntado entre desacoplarla (elegir provincia solo
filtra/zoomea; la hoja se abre aparte) o sacarla del todo, eligió sacarla.

Decisión: se elimina el bloque `.ph-sheet-scrim`/`.ph-sheet` completo de
`public_home.html` (marcado, CSS, keyframes, media query angosto), la
trampa de foco y el manejo de foco al abrir/cerrar en el JS
(`agarreDeFoco`, `manejarFocoDeHoja`, el listener de `keydown` para
Escape), y su cómputo en `routers/public.py`
(`province_sheet_rows`/`sheet_close_query`, que reusaba
`build_product_panels` — la función sigue viva para los paneles
flotantes reales, que no se tocaron).

Encontrado al sacarla: la ventana «Precios», con provincia elegida, no
mostraba el gráfico «¿dónde está más caro?» —mostraba una nota diciendo
que estaba «en la hoja de detalle que está abierta» (deuda saldada el
2026-09-11, ver esa entrada)—. Sin hoja, esa nota apuntaba a la nada y el
gráfico no se veía en ningún lado con provincia elegida. Se fusionaron las
dos ramas: «Precios» muestra ese gráfico siempre ahora, con o sin
provincia, resaltando la elegida cuando la hay (mismo `resaltar` que ya
usaba la rama sin provincia).

Descartado: desacoplar la hoja de la selección de provincia (dejarla
como una acción aparte). El operador prefirió sacarla directamente —el
dato que mostraba (productos piloto de la provincia, comparación entre
provincias) ya está disponible filtrado en las ventanas del menú.

Consecuencia: `TestHojaDetalleProvincia` pasa a `TestEnlacesDeProvinciaEnElMapa`,
con los dos tests que no dependían de la hoja (enlaces del mapa, chip sin
ícono); los cinco tests de la hoja se eliminan. `TestProvinciasLadoALadoEnLaRuta`
invierte su test de dedup (`test_con_provincia_elegida_la_ventana_precios_no_repite_la_comparacion`
→ `test_con_provincia_elegida_la_ventana_precios_la_resalta`, con el
aserto invertido). Test nuevo confirmando que no aparece ninguna hoja. Suite:
1580 pasan, 1 saltea (bajó de 1585: seis tests eliminados, dos reescritos, uno nuevo).

## 2026-09-12 — Fondo de relieve del mapa: Natural Earth, no OpenStreetMap

Contexto: el operador comparó el mapa SVG plano contra OpenStreetMap y pidió
mejorarlo visualmente. Se le marcó que un fondo estilo OSM en vivo (tiles)
choca con la decisión cerrada 1 (nada de motor de mapas ni dependencia de
red en runtime, por la conexión del usuario final cubano) — pero que
renderizar un extracto UNA VEZ en build time y servirlo como imagen estática
no rompe el motivo real de esa decisión. El operador pidió avanzar por esa
vía; elegido el nivel de detalle: físico/relieve (terreno, ríos, costa —
sin calles ni ciudades).

Verificado antes de construir: el estilo «físico/relieve» no es lo que
OpenStreetMap modela bien (su base es calles/POIs/límites administrativos).
Es exactamente lo que ofrece Natural Earth —la fuente que las 16 provincias
ya usan— en sus productos ráster de relieve sombreado. Se cambió de fuente
antes de bajar nada.

Decisión: `tools/build_cuba_relief.py`, nueva herramienta de build (mismo
patrón de reproducibilidad que `build_cuba_map.py`) que:
1. Recorta `NE2_HR_LC_SR_W_DR` (Natural Earth II alta resolución, relieve +
   agua + drenajes, serie 10m) al bounding box en grados de las 16
   provincias — el MISMO bounding box, no uno elegido a ojo.
2. Un solo `Image.resize` no uniforme del recorte en píxeles crudos al
   tamaño del `viewBox` reproduce el achatado por `stretch =
   cos(latitud_media)` de la proyección de las provincias sin calcularlo
   aparte.
3. Se probó primero con la serie 50m (`NE2_50M_SR_W`, ~89 MB): el recorte
   daba ~324×103 px, pixelado al escalar a los 1000 px del `viewBox`. La
   serie 10m (~326 MB de origen) da ~650×207 px — el doble de lineal,
   suficiente para un fondo estático.
Salida: `src/saimc/web/static/relieve_cuba.webp`, 1000×342px, ~21 KB.
Verificado dibujando las 16 rutas SVG encima: la costa de la imagen y los
bordes vectoriales coinciden al píxel.

Se integra como `<image>` dentro del mismo `<svg class="ph-map">`, antes del
`{% for province %}` — comparte `viewBox`, así que se alinea sin ningún
cálculo en JS y hace zoom junto con las provincias porque es del mismo
elemento. Los tres estados de provincia (`is-publicada`/`is-en-ingesta`/
`is-sin-datos`) suman `fill-opacity: .55` para dejar ver el relieve debajo
del color; el hover sube a `.85` para que la marca de interacción siga
leyéndose nítida. El pie del mapa («Mapa: Natural Earth, dominio público»)
no cambia — ya cubre las dos fuentes, ninguna atribución nueva.

Versionado: `relief_version()` en `app.py`, mismo patrón por mtime que
`css_version()`/`public_css_version()` — si se regenera el asset, el
navegador no se queda con la copia vieja.

Descartado: OpenStreetMap en cualquier forma (tiles en vivo, que choca con
la decisión cerrada 1; o un extracto propio, que hubiera sido peor
herramienta para relieve y encima ODbL en vez de dominio público).

Consecuencia: dos tests nuevos en `TestAssetsVendorizados`
(`tests/test_static_assets.py`) — que el asset se sirve como
`image/webp`, y que la home pública lo referencia versionado. Suite: 1582
pasan, 1 saltea.

## 2026-09-12 — Fondo de relieve revertido; tiles en vivo, pausado

Contexto: probado en el sitio real, el operador reportó el problema que el
ráster de la entrada anterior no podía resolver: al no ser interactivo, no
se ajusta a la pantalla, y al hacer zoom a una provincia (hasta 6×, `ZOOM_TOPE`
en el script del mapa) se ve pixelado — ~650×207 px reales estirados bien
más allá de su resolución nativa. El SVG vectorial no tiene ese problema
—escala infinita, siempre nítido—, que es justamente lo que el fondo
ráster sacrificaba a cambio de textura.

El operador preguntó si la salida era OSM. Aclarado antes de tocar nada: el
pixelado al zoom NO es un problema de fuente de datos (Natural Earth vs.
OSM) — es un problema de imagen estática vs. interactivo. Un extracto único
de OSM tendría el mismo techo de resolución. Lo único que de verdad resuelve
«se ajusta a la pantalla y no se rompe con el zoom» es tiles interactivos en
vivo (Leaflet u otro visor + un servidor de tiles) — pero eso es exactamente
la dependencia de red en runtime que la decisión cerrada 1 prohíbe, y esa
decisión protege la conexión del usuario final cubano, no la del entorno de
desarrollo.

El operador eligió esa vía (tiles en vivo) sabiendo el costo. Antes de
construir nada se le señaló un segundo costo, operacional: el servidor de
tiles gratuito de OpenStreetMap prohíbe en su política de uso que un sitio
en producción con usuarios reales lo consuma sin arreglo previo —y esto ya
está en producción (CLAUDE.md, Fase 0)—, así que las vías reales son un
proveedor comercial (MapTiler/Stadia/Thunderforest, de pago) o
autohospedar un servidor de tiles propio (que choca con la decisión
cerrada 4: nada de infraestructura pesada antes de validar H0). Con las dos
decisiones cerradas en juego (1 y 4) y ningún camino gratis y sin costo de
infraestructura, el operador prefirió pausar en vez de decidir apurado.

Decisión: se revierte el fondo de relieve de la entrada anterior —
`tools/build_cuba_relief.py`, `relieve_cuba.webp`, `relief_version()`, el
`<image>` del SVG, el `fill-opacity` de los tres estados y los dos tests de
`TestAssetsVendorizados`—. El mapa vuelve a ser 100% vectorial, como antes
de esa entrada. Esta entrada NO se borra —el archivo es de anexado—: queda
como registro de lo que se probó, por qué no alcanzó, y las dos decisiones
cerradas (1 y 4) que cualquier vía de tiles en vivo tiene que asumir a
sabiendas si se retoma.

Descartado, por ahora: fondo híbrido (relieve ráster solo en la vista de
Cuba entera, plano al zoomear a una provincia) — se ofreció como opción
intermedia sin costo de red ni de licencia; el operador prefirió ir
directo a evaluar tiles en vivo.

Consecuencia: código vuelto al estado de la entrada «Selector de provincia
visible siempre» (2026-09-11). Suite: 1580 pasan, 1 saltea — mismo número
que antes de tocar el relieve. Sin decisión tomada sobre tiles en vivo;
retomar exige elegir proveedor comercial o autohospedaje, cada uno con su
propio apartamiento a registrar aparte.

## 2026-09-12 — El mapa pasa de SVG a Leaflet interactivo (tiles de OSM)

Contexto: cerrando el hilo de las tres entradas anteriores (relieve
Natural Earth revertido por pixelado al zoom → tiles en vivo elegidos
sabiendo el costo → política de uso de OSM señalada), el operador confirmó
«vamos con OpenStreetMap cueste lo que cueste». **Apartamiento consciente
de la decisión cerrada 1** de CLAUDE.md (nada de motor de mapas ni
dependencia de red en runtime, por la conexión del usuario final cubano):
el operador la rompe a sabiendas, no por iniciativa de la sesión.

Decisión — fuente de tiles: el servidor público de OSM
(`tile.openstreetmap.org`) directo, sin API key, para no bloquear al
operador con un alta de cuenta. Es apto para el volumen bajo de Fase 0
(CLAUDE.md: ~1h de carga diaria) pero corre contra la política de uso de
OSM para producción con usuarios reales sin arreglo previo — riesgo
consciente, no escondido; si el tráfico crece, esto necesita moverse a un
proveedor comercial (MapTiler/Stadia) o autohospedaje.

Decisión — geometría: `map_cuba.py` (rutas SVG proyectadas) se borra.
`tools/build_cuba_geojson.py`, nueva herramienta (mismo shapefile de
`build_cuba_map.py`, reutiliza sus funciones de parseo), genera
`static/cuba_provincias.geojson` — lon/lat real, no proyectado, con
`slug` en cada feature para emparejar con el dato del server sin
reimplementar `product_slug` en JS. Cada provincia es un `MultiPolygon`
—no `Polygon`—: los cayos que sobreviven al filtro de área son tierra
separada, no agujeros.

Decisión — `map_view.py`: pierde `path`/`viewbox`. `build_map` ahora recibe
`province_names` de afuera (`_all_province_names(db)`, la misma consulta
que ya resolvía `?provincia=`) en vez de leerlos de la geometría —la
geometría ya no vive en Python, vive en el `.geojson` estático—. Esto
cambia un invariante real: antes las 16 provincias aparecían siempre
aunque la tabla `provinces` estuviera vacía (la geometría las traía
igual); ahora dependen de que la tabla esté sembrada, como en producción
siempre lo está. Los tests que asumían lo viejo necesitan sembrar la
tabla explícitamente (`_seed_provincias` en `test_public_map.py`).

Decisión — integración: `routers/public.py` arma `map_provinces_json`
(slug/estado/etiqueta/conteo/densidad/query por provincia) y lo deja en
`<script id="ph-map-data">`; el JS del pie (`inicializarMapa`,
`estiloDeProvincia`, `estiloAlPasarElMouse`, `navegarAProvincia`) lo lee
junto con el `.geojson` (cacheado en `window.__cubaGeoJSON` para no
pedirlo de nuevo en cada swap de htmx) y arma el mapa con Leaflet.
`mapaLeaflet.remove()` antes de cada `inicializarMapa()` porque
`htmx:afterSettle` reemplaza el `<div>` entero. Colores por tokens CSS
reales (`getComputedStyle`, no hex duplicado): un `<path>` de Leaflet es
un atributo de presentación SVG puesto por `setAttribute`, que no
resuelve `var(--token)`. Clic navega con `htmx.ajax(..., pushUrl: true)`,
mismo patrón que cualquier otro `hx-get` de la página.

Cambio de comportamiento notado a propósito: con el SVG, elegir una
provincia hacía que el server dejara de dibujar las otras 15 (pedido
del operador el 2026-09-11). Con Leaflet las 16 siempre viajan —la
geometría es un archivo estático que no cambia con el filtro, y esconder
polígonos dejaría huecos sin color en cuanto el usuario hiciera pan—: la
provincia elegida se resalta y las demás se atenúan (`opacidadRelleno *
0.35`), mismo vocabulario «resaltar/atenuada» que ya usan los gráficos de
comparación entre provincias.

Vendorizado: Leaflet 1.9.4 (BSD-2-Clause), verificado byte a byte contra
el `integrity` sha512 de npm antes de extraerlo — mismo trato que htmx y
marked. Los íconos de `dist/images/` (control de capas, marcador por
defecto) viajan aunque el mapa no los use hoy, para que `leaflet.css` no
pida al vacío un archivo que no está.

**Lo que se pierde de cobertura de tests**: con el SVG, pytest podía
afirmar qué color pintaba cada provincia (`is-publicada`, `is-densidad`,
etc.) porque el color era una clase CSS puesta por el server. Con Leaflet
ese color lo decide `estiloDeProvincia()` en el navegador — pytest no
ejecuta JS, así que ya no puede probarlo. Lo que sigue cubierto es el
contrato de datos que ese script consume (`#ph-map-data`, `data-provincia`,
`data-capa`, el `.geojson`); si ese contrato se rompe, el JS falla en
silencio y el test lo agarra antes que un usuario. Verificación visual
real queda pendiente del operador — no hay navegador en este entorno.

Consecuencia: `tests/test_public_map.py` reescrito case por caso
(`TestGeometria` contra el `.geojson`, no `map_cuba`; `TestCapaDelMapa`
contra el JSON de datos, no clases SVG). `tests/test_static_assets.py`
suma verificación de cadena de suministro de Leaflet y confirma —no
esconde— que `tile.openstreetmap.org` aparece en la página. Suite: 1585
pasan, 1 saltea.

## 2026-09-12 — El mapa es la app: el cromo flota, y los z-index arrancan en 1100
Contexto: pedido del operador después del pase a Leaflet — «El mapa debe ser
toda la app, es decir los menús de los productos y las provincias y todo lo
demás debe salir sobre el mapa de forma flotante. Los chips de productos y las
provincias deben salir en la misma fila, de ser posible en resolución de
escritorio, como cuando usamos flex y hacemos space between». Buscando dónde
meter eso apareció un bug introducido por el pase a Leaflet del mismo día:
**todo el cromo de la página vivía en z-index 1..4, y Leaflet numera sus
propias capas internas en 200..1000** (panes de tiles y overlays, controles,
esquinas). Mientras el mapa era un `<svg>` propio esos números alcanzaban; con
Leaflet, la barra superior, el riel de chips, las ventanas y el indicador de
carga quedaron **debajo** del mapa.

Decisión: tres cosas, todas en `templates/public_home.html`.

1. **El escenario sale del flujo.** `.ph-stage` pasa de ser un hijo más del
   flex column (que consumía lo que sobraba después de la barra y los chips) a
   `position: absolute; inset: 0; z-index: 0`, con `.ph-map` al 100 % de las
   dos dimensiones. El mapa cubre `.ph-canvas` entero, a sangre, y todo lo
   demás flota encima. El degradado de `.ph-canvas` queda de fondo a propósito:
   si los tiles no cargan —la conexión del usuario final, motivo de la decisión
   cerrada 1— se ve eso y no un vacío blanco.

2. **La escala de z-index arranca en 1100**, documentada en un comentario al
   lado de `.ph-stage` para que el próximo que agregue una capa no vuelva a
   elegir un número chico: `0` escenario · `1100` leyenda · `1200` muelle de
   ventanas y paneles · `1300` barra superior y fila de filtros · `1400`
   indicador de carga. Cualquier cosa que tenga que verse sobre el mapa va por
   encima de 1000.

3. **Una sola fila de filtros.** `.ph-filters` envuelve el riel de chips de
   producto y un `.ph-province-group` nuevo (el `<select>` de provincia más el
   chip de «volver a Cuba entera») con `display: flex; justify-content:
   space-between`. Antes eran dos bloques apilados. En angosto (≤720 px) se
   apilan solos por `flex-wrap` y el grupo de provincia pasa a `width: 100%`
   alineado a la izquierda, en vez de quedar pegado al borde derecho. La fila
   lleva `pointer-events: none` y sus dos grupos `auto`: el aire del medio es
   mapa, y ahí el arrastre tiene que llegarle al mapa y no morir contra un
   contenedor invisible.

El muelle de ventanas se mudó de adentro de `.ph-stage` a un `.ph-dock-layer`
propio (`flex: 1; min-height: 0`), el único hijo del flex column que crece: las
ventanas ocupan exactamente el hueco que queda debajo del cromo, sin que nadie
calcule alturas. Dentro del escenario habría tapado la barra superior, y una
ventana arrastrada hacia arriba se metía abajo de ella sin forma de agarrar su
título.

Descartado: dejar `.ph-stage` en el flujo y subirle el alto con `calc()`
restando barra y filtros. Anda hasta que la barra envuelve a dos líneas —que
envuelve, tiene `flex-wrap`— y entonces el alto queda mal por una cantidad que
depende del ancho de la ventana. El layout ya sabe medir eso; el `calc()` era
duplicarlo a mano.

También descartado: subir el cromo con `z-index: 5..9` en vez de 1100. Es el
mismo trabajo y queda igual de roto, porque el techo a superar son los 1000 de
Leaflet, no los 4 de antes.

Consecuencia: se eliminó `<p class="ph-attrib">` (CSS y markup). Caía en la
esquina inferior derecha, la misma que el control de atribución de Leaflet: dos
cajas de crédito pisándose. Los dos créditos —Natural Earth por los polígonos,
OpenStreetMap por los tiles, ODbL, obligatoria— viajan ahora en el
`attribution` del tile layer que arma `inicializarMapa()`. El pie del shell
(`_public_shell.html`) conserva su propia línea de Natural Earth: habla de la
procedencia del dato (§6.5), no es el crédito de la esquina del mapa.

`tests/test_public_map.py` suma `TestElMapaEsLaApp` (5 casos: los dos grupos
hermanos en una sola fila, el `space-between` declarado, el escenario a sangre,
los cuatro z-index del cromo por encima de 1000, el muelle afuera del
escenario, el crédito sin duplicar). El docstring de la clase dice qué NO
prueba: que la fila se vea como una fila lo decide el CSS, y sin navegador no
hay layout que medir — eso queda de la verificación del operador.
`tests/test_public_filter.py` re-ancló el caso del indicador de carga: usaba el
crédito «Natural Earth» como marca del final de `#ph-live`, y al mudarse ese
crédito al `attribution` de Leaflet quedó impreso en el JS del pie, después del
indicador. Ahora se ancla en el pie de la leyenda del mapa, que sí es el último
markup de `#ph-live`. Suite: 1590 pasan, 1 saltea.

Verificado aparte, con un parser de HTML sobre el render de `/`,
`/?provincia=la-habana` y `/?productos=aceite&ver=precios`: el árbol de `<div>`
cierra balanceado en los tres. El conteo sobre la plantilla cruda daba 49
abiertos contra 48 cerrados, que era artefacto de los `{% if %}` (abren y
cierran en ramas distintas), no un `<div>` sin cerrar.

## 2026-09-12 — Deudas estéticas de UI: el shell público sin regla, y el acento equivocado
Contexto: pedido del operador después del cierre del slice del mapa a pantalla
completa — «dale con las deudas esteticas de ui». Un cruce mecánico de clases
usadas contra reglas declaradas (markup público vs. `saimc-public.css` + el
`<style>` inline de `public_home.html`) encontró tres cosas reales, y descartó
dos falsos positivos.

Decisión: tres arreglos, todos en `src/saimc/web/static/saimc-public.css`.

1. **El shell público (`_public_shell.html`) no tenía NINGUNA regla propia.**
   `.m3-public`, `.m3-public-top`, `.m3-kicker`, `.m3-title`, `.m3-public-main`
   y `.m3-public-foot` — las seis clases del layout compartido — llevaban toda
   la vida sin una sola declaración en `saimc-public.css`. Nadie lo vio porque
   `public_home.html` las tapa: sobrescribe `heading`/`subtitle` en vacío,
   oculta `.m3-public-top` con `display: none` y trae ~600 líneas de
   `<style>` propio. `/anuncio/<id>` (`public_evidence.html`) es la ÚNICA
   vista que renderiza el shell tal cual, sin tapar nada, y salía a sangre
   contra el borde de la ventana, con el `<h1>` en el default del navegador
   (Times bold) en vez del serif editorial. Se agregaron las seis reglas,
   con la misma voz que ya usan las ventanas de la home: `--ph-font-heading`
   para el titular, versalita chica para el kicker, pie separado por un
   filete.

2. **El acento de los enlaces públicos era el teal del operador, no el azul
   de la marca pública.** `saimc-public.css` pintaba `a { color:
   var(--m3-primary) }` — `--m3-primary` es el teal de la herramienta
   interna; el 2026-09-04 se decidió que la capa pública tiene su propio
   acento, `--ph-accent` (azul), y son dos colores de marca deliberados. La
   home no lo delataba porque no tiene enlaces sueltos en el cuerpo del
   texto; se veía en el «← Volver a precios» de `/anuncio/<id>` y en el
   enlace de Natural Earth del pie. Cambiado a `--ph-accent` en `a`, `a:hover`
   y `:focus-visible`.

3. **La tarjeta de evidencia (`/anuncio/<id>`) usaba `.ph-table-card`**, una
   clase que solo el `<style>` de `public_home.html` define — acá renderizaba
   sin fondo ni sombra, con el único `style="padding:1.5rem"` inline
   sobreviviendo. Se le dio clase propia, `.ph-evidence-card` (misma
   superficie que `.ph-win`/`.ph-fpanel`: `--ph-radius-sm`, `--m3-surface`,
   `--ph-elev`) — no `.ph-table-card`, porque ese nombre ya tiene otro
   trabajo en la home (contenedor de scroll de una tabla, a veces anidado
   adentro de una ventana que ya es superficie); reusarlo acá habría puesto
   tarjeta sobre tarjeta ahí.

4. **`.ph-card-format`/`.ph-card-variant` (tarjetas de precio de `/`) no
   tenían regla**: el `<div>` del formato declarado («Bolsa 1kg») o la
   variante («girasol Vitalín importado») renderizaba de bloque a full
   width, pegado al titular de la tarjeta, sin el tratamiento secundario que
   ya usa `.ph-card-median` al lado. Mismo tratamiento que ese vecino:
   `margin-top: .3rem; font-size: .8125rem; opacity: .75`.

Descartado, tras revisar el markup y el CSS base de cada uno: `.ph-filter-
province` y `.ph-provinces` (la lista de provincias y la tabla de la
ventana «Precios»). Son modificadores sin ninguna regla propia, pero heredan
el estilo COMPLETO y funcional de su clase base (`.ph-filter`, `.ph-coverage`)
— no son un hueco de renderizado, son ganchos de selector sin necesidad
visual distinta hoy. Inventarles una regla habría sido decorar por decorar,
sin ninguna razón que lo pida. También descartado: el comentario que menciona
`ph-attrib` en `public_home.html` — es prosa en pasado explicando qué había
antes de sacar ese `<p>` (slice anterior), no una clase huérfana en markup.

Fuera de alcance, señalado y no tocado: la deuda de CSS muerto que encontró el
mismo cruce —14 selectores en el `<style>` de `public_home.html` (`.ph-
series`, `.ph-serie*`, `.ph-plot*`) que solo estilan `_price_plot.html`, un
partial que no referencia ningún template ni router— porque es housekeeping
de código muerto, no una deuda estética, y este slice era específicamente
sobre lo segundo.

Consecuencia: `tests/test_transparency_layer.py` suma
`TestDeudaEsteticaDelShellPublico` (3 casos: el `<h1>` usa la tipografía
editorial, la tarjeta de evidencia tiene su propia superficie, los enlaces
públicos citan `--ph-accent` y no `--m3-primary`). `tests/test_public_filter.py`
extiende `TestVarianteEnTarjetaPublica` con la regla de `.ph-card-variant` y
agrega el caso de `.ph-card-format`. Suite: 1594 pasan, 1 saltea.

## 2026-09-13 — Rediseño del topbar: «Brújula»
Contexto: pedido del operador — «necesito volver a disenar el toolbar el
menu principal […] alli arriba del mapa y no me convence mucho». Se armaron
cuatro direcciones «rectas» (Segmentado, Compacta, Editorial, Iconos
grandes) en un canvas de Claude Design para evaluar. El operador eligió
Segmentado, pero pidió una vuelta más: «necesito algo mas creativo […]
sentir que el topbar menu y mapa es una sola cosa». Se armaron tres
direcciones nuevas que fusionan cromo y mapa (Cristal flotante, Brújula de
esquina, Degradado sin costura); eligió Brújula y pidió refinarla antes de
implementar.

Decisión: la barra horizontal (`.ph-topbar`) desaparece. El avatar de Guani
pasa a ser el control mismo, un círculo anclado a la esquina superior
izquierda como el control de capas/zoom de un mapa, con un dock vertical de
botones debajo (Cobertura, Precios, Cómo se movió, Descargar CSV, zoom del
mapa) y el título flotando directo sobre el mapa sin caja dura —un halo
radial como fondo, no un rectángulo—. Un hilo fino conecta compás → título
→ dock para que las tres piezas se lean como un solo objeto, no tres cosas
sueltas. Los chips de producto y el selector de provincia se consolidan en
una sola píldora de vidrio (`backdrop-filter`) arriba a la derecha,
reemplazando la fila a lo ancho con `space-between` del slice anterior.

Todo el cromo pasa de `position: relative` (dentro del flujo del flex
column de `.ph-canvas`) a `position: absolute`, así que `.ph-dock-layer`
(que ya era `flex: 1`) pasa a ocupar el lienzo ENTERO en vez de lo que
sobraba debajo de una barra. Eso obligó a mover la responsabilidad de
apartar las ventanas del compás al `inset` de `.ph-dock`: los cuatro lados
se escriben a mano (izquierda 6.5rem para que la pila de ventanas del menú
no arranque debajo del dock vertical; arriba 6.5rem para que la pila de
paneles de producto no arranque debajo de la píldora de filtros).

El zoom de Leaflet (`zoomControl`, arriba a la izquierda por defecto)
chocaba exactamente con el compás — se desactivó (`zoomControl: false`) y
dos botones nuevos en el dock (`[data-zoom-in]`/`[data-zoom-out]`) llaman
`mapaLeaflet.zoomIn()/zoomOut()` por delegación de evento en `document`,
mismo patrón que ya usa `cerrarOptimista`: el header entero vive dentro de
`#ph-live` y se reemplaza en cada swap de htmx, así que un listener puesto
al crear el botón se perdería.

`ViewToggle` (`product_nav.py`) suma un campo `icon`, poblado desde
`VIEW_ICONS` (mismo patrón que `PRODUCT_ICONS`) — un botón redondo sin texto
necesita un ícono. Cinco íconos nuevos entraron al subset de Material
Symbols (`download`, `map`, `remove`, `sell`, `show_chart`; `add` ya estaba)
vía `tools/build_icon_font.py`, que ahora también escanea `VIEW_ICONS` y no
solo `PRODUCT_ICONS` — los de `view_menu` salen de Python, invisibles para
el regex que lee las plantillas.

Descartado: `all: unset` para resetear los botones de zoom. Además de
`border`/`background`/`padding`, también borra `outline`, y con eso se va el
anillo de `:focus-visible` que ya define `saimc-public.css` — un botón sin
indicador de foco para quien navega con teclado. Reseteo a mano en su
lugar, dejando `outline` sin tocar.

Encontrado y corregido al mismo tiempo (no al momento del primer pase): la
primera versión del bloque CSS nuevo se armó cortando el viejo desde
`.ph-topbar` hasta `.ph-province-chip .ph-x`, y el corte se llevó puesto —
sin querer— TODO el CSS del indicador de carga (`.ph-loading`,
`.ph-loading-dots`, las dos animaciones), que vivía en el medio de ese
tramo. El indicador se quedó sin estilo un momento; se recuperó el bloque
exacto del último commit y se reinsertó entero, sin tocarlo.

Consecuencia: `tests/test_public_map.py::TestElMapaEsLaApp` se reescribe
para el nuevo cromo — chips+provincia hermanos dentro de
`.ph-compass-filters` (no ya `.ph-filters` con `space-between`), los cinco
z-index del compás por encima de 1000, el hilo decorativo antes que el
compás en el DOM, y el contrato de `zoomControl: false` +
`data-zoom-in`/`data-zoom-out`. Verificado aparte, con un parser de HTML
sobre el estado más cargado (producto activo, provincia elegida, las tres
ventanas del menú abiertas a la vez): el árbol de `<div>` cierra
balanceado. Suite: 1596 pasan, 1 saltea.

## 2026-09-13 — Brújula, ajuste: dock oculto hasta tocar el avatar
Contexto: el operador probó el rediseño «Brújula» en vivo, ajustó
`.ph-compass-filters` a mano en DevTools (le sacó fondo, borde, sombra,
radio y el layout flex — le gustó cómo quedaba «vidrio sobre vidrio» de
menos, ya que los chips y el `<select>` traen su propia superficie) y pidió
además que el dock vertical (`.ph-menu`) quede oculto por defecto y
aparezca solo al pasar el mouse —o tocar, en táctil— sobre el avatar.

Decisión, en dos partes.

1. **`.ph-compass-filters` queda tal cual el operador la dejó**: sin
   `background`/`border`/`box-shadow`/`border-radius`/`display: flex`. Se
   restauró únicamente el `backdrop-filter` sin prefijo (el pegado solo
   traía `-webkit-backdrop-filter`): Chrome y Firefox no leen la variante
   con prefijo si no está también la estándar, así que sin ella el blur no
   se habría visto en la mayoría de los navegadores. El `clamp(1rem, 3vw,
   1rem)` del `right` se simplificó a `1rem` fijo —con el mismo piso y
   techo, el `clamp` no hacía nada.

2. **El dock aparece al interactuar con el avatar, no siempre**.
   `.ph-compass-nav` envuelve avatar + hilo + dock en una sola zona de
   hover: sin ese envoltorio, mover el mouse del avatar hacia el dock por
   el hilo (2px de ancho) perdería el `:hover` a mitad de camino, porque
   `:hover` no salta el espacio entre dos hermanos. Tres disparadores, no
   uno: `:hover` (mouse), `:focus-within` (teclado — tabular hasta el
   botón lo revela) y `.is-active` (JS, alternado al tocar/cliquear el
   avatar). El compás pasa de `<div>` a `<button type="button">` para ser
   real receptor de foco y click.

   Ocultar con `opacity`/`visibility` y no `display: none`: con
   `display: none` los ítems del dock saldrían del orden de tabulación
   para siempre, y nadie podría llegar a ellos con teclado. `visibility`
   con demora (`0s linear .18s` al cerrar, `0s` al abrir) es lo que además
   los saca de la tabulación mientras están cerrados, sin que la
   transición de opacidad se corte a la mitad.

   `pointer-events: auto` en `.ph-compass-nav`, no `none` como el resto
   del cromo transparente de la página: con `none`, `:hover`/
   `:focus-within` nunca dispararían —ese valor saca al elemento de la
   detección de mouse por completo—. El costo aceptado: un rectángulo
   inerte para arrastrar el mapa, de ~6.5rem × 27rem en la esquina
   superior izquierda; se puede arrastrar desde cualquier otro punto.

Descartado: seguir el patrón `pointer-events: none` en el envoltorio y
resolver el hover con JS (mousemove con tolerancia de distancia en vez de
`:hover` puro). Más código para el mismo resultado, y CSS solo ya alcanza.

Consecuencia: `tests/test_public_map.py` suma
`TestDockOcultoHastaTocarElAvatar` (3 casos: el compás es un `<button>` y
el dock vive adentro del envoltorio en el orden hilo→botón→dock; se oculta
por `opacity`/`visibility`, nunca por `display: none`; `alternarCompas`
existe y está registrado por delegación). Suite: 1599 pasan, 1 saltea.

## 2026-09-13 — Brújula: saca el rectángulo invisible que rompía el mapa en celular
Contexto: el operador probó el ajuste anterior y reportó dos cosas — sacar
el `backdrop-filter`/`-webkit-backdrop-filter` de `.ph-compass-filters`
(quedó pedido a medias la vez anterior), y que en escritorio el revelado
del dock andaba bien pero «en el móvil es un desastre».

El desastre tenía una causa concreta: `.ph-compass-nav` (el envoltorio que
sostiene el `:hover` entre avatar y dock) tenía tamaño fijo
(`width: 6.5rem; height: 27rem`) y `pointer-events: auto` para que el
`:hover` de CSS pudiera dispararse. Ese rectángulo —invisible pero real
para el mouse— tapaba el arrastre del mapa en una franja de ~104×432px en
la esquina superior izquierda, que en un viewport de celular es más de
medio alto de pantalla.

Decisión: sacar el rectángulo entero, no achicarlo. `.ph-compass-nav` pasa
a ser un envoltorio sin posición ni tamaño propios —solo agrupa en el
DOM—; sus hijos siguen posicionándose contra `.ph-canvas`, como si el
envoltorio no existiera para efectos de layout. El revelado por mouse ya
no es `:hover` de CSS: es JS (`abrirCompas`/`programarCierreCompas`,
delegados por `mouseover`/`mouseout` con chequeo de `relatedTarget`, que sí
burbujean —`mouseenter`/`mouseleave` no—) con una demora de 300ms, que
tolera cruzar el hueco de 2px del hilo entre avatar y dock sin que el dock
se cierre a mitad de camino. `:focus-within` (teclado) y `.is-active` (JS,
al tocar/cliquear) se mantienen igual, sin necesitar geometría.

Efecto secundario que hubo que resolver: con el hover ahora manejado por
JS y el toque también por JS (`alternarCompas`), un click sobre el avatar
ya hovereado en desktop toggleaba `.is-active` a la INVERSA de lo que el
hover acababa de poner, cerrando el dock al toque. Se resolvió con
`matchMedia("(hover: hover)")`: en un dispositivo con hover real, clickear
el avatar solo confirma que quede abierto (no alterna); en uno sin hover
—táctil—, el toque es el único disparador y sí alterna.

Descartado: agrandar el rectángulo para que también cubriera el título
(pensando que «el menú del título» pedía ocultar el título también) en vez
de sacarlo. Un rectángulo más grande agrava exactamente el problema que
se está resolviendo. Se interpretó el pedido como «el dock debe
comportarse igual en ancho y en móvil», no como pedir ocultar el título —
si la lectura es la otra, es un cambio de alcance nuevo, no una corrección
de este.

Consecuencia: `.ph-compass-filters` queda sin `background`/`border`/
`box-shadow`/`backdrop-filter`, solo `position`/`right`/`padding`. Dos
tests de `tests/test_public_map.py::TestElMapaEsLaApp`/
`TestDockOcultoHastaTocarElAvatar` se reescriben para el nuevo contrato
(sin vidrio en la píldora; sin `.ph-compass-nav {` con geometría propia;
el hover de mouse documentado por JS, no por `:hover` de CSS) y se agrega
un caso para `abrirCompas`/`programarCierreCompas`. Suite: 1600 pasan, 1
saltea.

## 2026-09-13 — Brújula: el título también se oculta hasta interactuar con el avatar
Contexto: el operador confirmó una lectura que había quedado abierta —«el
header que dice guani saimc y todo lo demas solo debe estar visible
cuando un hoiver o un lick sobre el avatar»—. El dock ya se ocultaba así;
el título (kicker + `<h1>` + frescura del dato) seguía siempre visible.

Decisión: `.ph-compass-title-group` y `.ph-compass-link` (el enlace corto
que lo conectaba al compás) se mudan adentro de `.ph-compass-nav` y se
suman al mismo mecanismo de revelado que ya tenía el dock —hover con
demora por JS, `:focus-within` por teclado, `.is-active` al tocar/cliquear
el avatar—.

Con una diferencia deliberada en CÓMO se ocultan. El dock se oculta con
`opacity` + `visibility` (sus enlaces son focales: sin `visibility`,
tabular los alcanzaría igual, invisibles). El título, el hilo y el enlace
decorativo se ocultan con `opacity` NADA MÁS. El motivo:
`<section class="ph-canvas">` usa `aria-labelledby="ph-canvas-title"`,
apuntando al `<h1>` de ese título — y `visibility: hidden` (como
`display: none`) saca a un elemento del cómputo de nombre accesible. Si el
título se hubiera ocultado igual que el dock, la sección se habría
quedado sin nombre accesible la mayor parte del tiempo, mientras nadie
pasa el mouse por el avatar. Con `opacity` sola el elemento sigue en el
árbol de accesibilidad —el `<h1>` sigue siendo un nombre válido para
`aria-labelledby`— aunque esté invisible y sin ítems focales que
tabular (no tiene ninguno). El botón del compás además trae su propio
`aria-label` con la marca, para quien un lector de pantalla lleve directo
ahí sin pasar por el título.

Consecuencia: `tests/test_public_map.py` extiende
`TestDockOcultoHastaTocarElAvatar` con
`test_el_titulo_se_oculta_sin_romper_el_nombre_accesible_de_la_seccion`, y
ajusta el test de z-index (`.ph-compass`/`.ph-menu`/`.ph-compass-filters`
tienen más de una regla con el mismo selector — la de posición/z-index, y
la que oculta por defecto o la del `@media` angosto, sin z-index — el
test ahora busca la que trae `z-index:`, no la primera aparición cruda
del selector). Suite: 1601 pasan, 1 saltea.

## 2026-09-13 — Brújula: en angosto, filtros al lado opuesto del compás
Contexto: el operador confirmó que el escritorio quedó bien, pero en
angosto seguía sin convencer — «los productos y las provincias ahora se
van a convertir en una barra situada en el lado contrario a la barra que
sale de el avatar de guani… hablar solamente para el movil».

El compás vive arriba a la izquierda —título y dock se despliegan desde
ahí, hacia abajo—. La píldora de chips+provincia, que hasta ahora bajaba a
una banda justo debajo del compás (`top: 3.75rem`), competía por la misma
región de la pantalla que el título y el dock recién revelados: exactamente
el tipo de choque que el operador venía señalando.

Decisión, solo dentro de `@media (max-width: 720px)` — la regla de
escritorio (sin `display: flex`, tal como el operador la dejó en DevTools
la vez anterior) no se toca: `.ph-compass-filters` se muda al lado opuesto,
abajo del todo (`top: auto; bottom: 0; left: 0; right: 0`), como una barra
propia a lo ancho (`display: flex; flex-wrap: wrap; justify-content:
center`). `.ph-dock` (la bandeja de ventanas, que en angosto ya se anclaba
abajo) le reserva el hueco: `inset: auto 0 4.5rem 0` en vez de `0`, y
`max-height` baja de 62% a 52% para no competir por el mismo espacio.

Consecuencia: `tests/test_public_map.py` suma
`TestFiltrosEnAngostoVanAlLadoContrarioDelCompas` (2 casos: la píldora de
filtros trae `top: auto`/`bottom: 0`/`display: flex` en la regla angosta —
no en la de escritorio—, y `.ph-dock` reserva el hueco de 4.5rem). Suite:
1603 pasan, 1 saltea.

## 2026-09-13 — Brújula: la leyenda también le deja el hueco a la barra de abajo
Contexto: el operador confirmó que la barra de filtros abajo del todo (ver
entrada anterior) «no esta mal», pero «no se ve bien por el ccartel de
publicacion / densdidad» — la leyenda del mapa (`.ph-canvas-legend`, el
selector Publicación/Densidad + las referencias de color) vive en la misma
esquina inferior izquierda donde acaba de aterrizar la barra nueva, y las
dos se pisan.

Decisión: mismo patrón que ya se usó para `.ph-dock` en la entrada
anterior — reservarle el hueco, no rediseñar nada. Solo dentro de
`@media (max-width: 720px)`: `.ph-canvas-legend { bottom: 4.5rem; }`, el
mismo valor que ya usa `.ph-dock` para no quedar debajo de la barra de
chips+provincia. La regla de escritorio no se toca.

Consecuencia: `tests/test_public_map.py` suma un caso a
`TestFiltrosEnAngostoVanAlLadoContrarioDelCompas`
(`test_la_leyenda_tambien_le_deja_hueco_a_la_barra_de_abajo`). Suite: 1604
pasan, 1 saltea.

## 2026-09-13 — Brújula: la leyenda colapsa a un botón en angosto
Contexto: el hueco de 4.5rem del slice anterior no alcanzó — el operador
avisó que la leyenda «esta muy cerca de los productos y molesta» todavía,
e invitó a resolverlo con creatividad: «o lo movemos de lugar o lo
ocultamos y mostramos de alguna forma… vista movil».

Decisión: mismo idioma visual que ya tiene el compás, aplicado a la
leyenda. Solo dentro de `@media (max-width: 720px)`: `.ph-canvas-legend`
colapsa a un botón redondo chico (`.ph-legend-toggle`, ícono `layers`) que
vive en el mismo rincón inferior izquierdo; tocarlo revela la tarjeta
completa (Publicación/Densidad + referencias de color), que aparece por
encima del botón (`bottom: 7.75rem`, arriba de los `4.5rem` del botón) en
vez de reemplazarlo — así el mismo botón sigue disponible para volver a
cerrarla. `opacity`+`visibility` para ocultarla (sus botones de capa,
`.ph-layer-btn`, son focales — igual que el dock, y a diferencia del
título del compás, que no tiene nada enfocable propio). El toggle es JS
(`alternarLeyenda`, mismo patrón que `alternarCompas` sin la rama de
hover: el botón no existe en escritorio, así que no hace falta distinguir
mouse de táctil) más `:focus-within` para teclado.

En escritorio no cambia nada: `.ph-legend-toggle` queda en `display: none`
y la leyenda sigue siempre abierta, como toda la vida — el operador acotó
el pedido a «vista movil».

Consecuencia: cinco íconos de Material Symbols nuevos entraron al subset
en total desde que empezó «Brújula» (`download`, `map`, `remove`, `sell`,
`show_chart`); este slice suma uno más, `layers`, vía
`tools/build_icon_font.py`. `tests/test_public_map.py` suma
`TestLaLeyendaColapsaEnAngosto` (4 casos) y reescribe el test del hueco
anterior, superado por este mecanismo. Suite: 1608 pasan, 1 saltea.

## 2026-09-13 — Brújula: el botón de la leyenda se muda adentro del dock
Contexto: el operador, tras ver el botón redondo colapsado de la leyenda
—«esta muy bien»—, pidió mudarlo: «quiero que ese boton este dentro de la
barra que esta dentro de guani avatar, la que aparece cuando se
interactua con el». El botón deja de ser una pieza suelta en su propia
esquina y pasa a ser un ítem más del dock del compás.

Decisión: el `<button class="ph-legend-toggle">` se muda adentro de
`<nav class="ph-menu">`, con la clase `.ph-menu-item` (mismo círculo con
etiqueta revelable que ya usan Cobertura/Precios/Evolución/Descargar CSV),
después del zoom. Sigue en `display: none` en escritorio —la leyenda
sigue siempre abierta ahí, sin nada que este botón deba abrir—.

Esto rompe el supuesto de la versión anterior: botón y contenido ya NO
comparten un envoltorio común en el DOM (el botón vive dentro del compás,
arriba a la izquierda; la leyenda sigue en su rincón de siempre, abajo a
la izquierda), así que `:focus-within` y el `contains()` de JS que
dependían de un padre compartido dejaron de servir. `alternarLeyenda`
pasa a alternar `.is-active` en `.ph-canvas-legend` DIRECTO (buscada por
`document.querySelector`, no por contención), sin importar dónde vive el
botón que la dispara; y de paso el botón se marca `is-open` (mismo relleno
de acento que ya tienen los ítems de vista) para que se vea cuál de los
dos estados —dock abierto, leyenda abierta— está activo.

Con el botón fuera de esa esquina, ya no hace falta reservarle un hueco
propio ahí: la leyenda vuelve a `bottom: 4.5rem`, el mismo valor que ya
usa el resto del cromo de abajo para librar la barra de chips+provincia,
en vez de los `7.75rem` que necesitaba para no taparlo cuando el botón
vivía justo debajo.

Consecuencia: `tests/test_public_map.py::TestLaLeyendaColapsaEnAngosto` se
reescribe para el nuevo contrato (el botón vive dentro de `<nav
class="ph-menu">`; `alternarLeyenda` apunta a `.ph-canvas-legend`
directo, marca `is-open` en el botón). Suite: 1609 pasan, 1 saltea.

## 2026-09-13 — Checklist de revisión de slice, contra el código que se ve bien y pudre
Contexto: con vistas a sumar desarrolladores —y ya trabajando con código
generado por IA en cada sesión— el olfato del operador para detectar un diff
podrido no escala ni es transferible. Es el mismo riesgo que ya pagan caro
empresas grandes: PRs largos, prolijos, con tests en verde, que explotan meses
después porque nada objetivo los frenó a tiempo.
Decisión: se agrega `docs/checklist-pr.md`, checklist mecánico de cuatro ejes
(trazabilidad, calidad del test con prueba de mutación mental, smells típicos
de código generado por IA, blast radius), fundado en prácticas con nombre
propio (problema del oráculo, code smells de Fowler) en vez de reglas
inventadas ad hoc. Se suma como cuarto requisito de cierre de slice en
`CLAUDE.md` § Alcance, junto a tests + decisión + commit. Se agrega al
catálogo cerrado de `/docs` (`docs_catalog.py`, slug `checklist-pr`) y se
referencia desde `README.md`.
Descartado: gate automático (hook pre-commit/pre-push) y linter (ruff/mypy)
por ahora — el primero tensiona con la decisión 4 (sin CI/CD hasta validar
H0) y necesita aval explícito antes de tocarla; el segundo queda como
candidato a próximo slice. No se resuelven los tres frentes a la vez.
Consecuencia: cerrar un slice exige pasar este checklist, no solo tests en
verde. `CLAUDE.md`, `README.md` y el catálogo de `/docs` apuntan al archivo.

## 2026-09-13 — Orden de lectura para un dev nuevo en `/docs`
Contexto: se evaluó adoptar una plataforma externa de documentación
(Docusaurus, MkDocs, GitBook) pensando en incorporar un desarrollador. Al
revisar el código, `/docs` ya cumple ese rol —catálogo cerrado, markdown
renderizado, agrupado por vigencia— así que faltaba contenido, no
herramienta: el índice agrupa por vigencia (criterio de mantenimiento), no
por secuencia de lectura para quien arranca de cero.
Decisión: se agrega `docs/empezar-aqui.md`, entrada nueva del catálogo
(`docs_catalog.py`, slug `empezar-aqui`, standing `rige`) con el orden de
lectura: README → spec-map → decisions → CLAUDE.md → checklist-pr.
Referenciado desde `README.md`.
Descartado: adoptar un generador de sitios externo. Duplicaría `/docs` en
otro stack, contra la decisión 1 (Python únicamente) y la decisión 4 (sin
infraestructura nueva hasta validar H0), sin agregar capacidad real.
Consecuencia: `/docs` abre mostrando primero la guía de lectura (es el
documento más reciente del grupo "rige"). Los tests de `tests/test_docs.py`
que iteran sobre `docs_catalog.CATALOG` cubren la entrada nueva sin
cambios; se suma un caso puntual para fijar su contenido.
## 2026-09-13 — ruff + mypy en pyproject.toml
Contexto: siguiendo la entrada anterior (checklist de revisión de slice), la
capa mecánica y barata que ese documento asume que ya corrió no existía. Sin
ella, el checklist gasta sus cuatro ejes en cosas que una herramienta
detecta sola.
Decisión: se agrega `ruff>=0.16` y `mypy>=2.3` a `dev` en `pyproject.toml`,
con configuración ajustada contra el código real —no la config por
defecto— para que la señal sea honesta:
- ruff: `select = [E,F,I,B,UP,SIM,C4,A,RUF,S]`, `line-length = 110`.
  `ignore` RUF001/002/003 (disparan con cualquier acento o guión largo de la
  prosa en español que pide CLAUDE.md — pensadas para homoglifos en
  identificadores, no prosa). `S101` exceptuado en `tests/*` (`assert` es la
  forma estándar de pytest). `Depends`/`Query`/etc. de FastAPI exceptuados de
  B008 vía `flake8-bugbear.extend-immutable-calls` (sacarlos del default
  rompería la inyección de dependencias).
- mypy: adopción gradual (`check_untyped_defs` sin exigir anotar todo el
  código heredado el día 1), `mypy_path = "src"` + `explicit_package_bases`
  para resolver `saimc.*` por ruta y no por el paquete editable —el `.pth`
  del venv compartido apunta a `master`, no a este worktree. `pytesseract` y
  `anthropic` con `ignore_missing_imports` (el primero no trae stubs; el
  segundo es la dependencia opcional del extra `llm`, docs/costos.md).
Se corrió una vez contra el repo real para no dejar la herramienta gritando
falsos positivos desde el día 1 (`--fix` seguro: 28 arreglos mecánicos,
reordenar imports, `datetime.UTC`, literales — sin cambio de comportamiento,
1609 tests siguen en verde). Dos falsos positivos puntuales que no entraban
en la config general se resolvieron en el código: `tests/test_processing_layer.py`
ganó un import bajo `TYPE_CHECKING` para que la anotación de retorno entre
comillas de `_crear_formato`/`_crear_variante` deje de leerse como nombre
indefinido (el import real sigue adentro de la función, sin cambio de
runtime); `src/saimc/extraction/measure.py` ganó `# noqa: S603,S607` en el
`subprocess.run(["git", ...])` de telemetría (lista literal, sin shell, ya
razonado en un comentario propio).
Descartado: arreglar los hallazgos reales que quedan (65 de ruff, 141 de
mypy) en este mismo slice — mezclaría "agregar la herramienta" con "tocar
la mitad del repo", que es justo lo que el checklist de la entrada anterior
pide declarar aparte. El hallazgo más señalado: `src/saimc/cells.py:341`,
división de un `Decimal` contra algo que mypy tipa como `None` — el tipo de
bug que hoy no truena porque ningún test pisa ese camino.
Consecuencia: `ruff check .` y `mypy` quedan disponibles y honestos, pero no
integrados a ningún gate (no hay CI/CD, decisión 4). Los 65 + 141 hallazgos
reales quedan como deuda visible, no como bloqueo — decidir cuándo se
atacan es un slice aparte.

## 2026-09-13 — scripts/lint_drift.py: ratchet de deuda, no reparador
Contexto: propuesta del operador — correr ruff/mypy a diario y "atacar" un
número de hallazgos sin supervisión. Se discutió la distinción entre arreglo
mecánico (sin criterio, seguro sin revisión) y arreglo semántico (qué hacer
con un `Decimal / None`, un `union-attr` — decisión de negocio, no de estilo).
Automatizar el segundo sin supervisión reproduce el riesgo mismo que motivó
`docs/checklist-pr.md`: un cambio que calla al type-checker sin que nadie
verifique que la resolución es correcta.
Decisión: `scripts/lint_drift.py` hace exactamente dos cosas y nada más.
(1) `ruff check --fix` (el seguro, nunca `--unsafe-fixes`) y commitea el
resultado si el árbol estaba limpio — sin push, igual que cualquier commit
de este repo. (2) Compara los hallazgos que quedan (ruff + mypy) contra
`docs/lint-baseline.json` y reporta qué es nuevo (exit 1) y qué se resolvió,
sin tocar ninguno. `--update-baseline` es la única forma de mover el piso, y
la usa el operador después de revisar. Si el árbol tiene cambios sin
commitear al arrancar, salta el autofix/commit entero (no se mezcla con
trabajo en curso) y solo reporta.
Piso inicial: 65 hallazgos de ruff + 141 de mypy (los mismos de la entrada
anterior), guardados en `docs/lint-baseline.json`.
Descartado: que el job arregle hallazgos semánticos, aunque sea "solo
algunos por día" — no hay forma de que una herramienta sepa cuál es la
resolución correcta de un `Decimal / None` en este dominio; esa decisión es
del operador o de una sesión con supervisión, vía `docs/checklist-pr.md`.
Pendiente, no resuelto acá: **cómo** se dispara "diario" (cron/systemd en el
VPS, o un job programado de Claude Code) — toca la decisión cerrada 4 si es
infraestructura a operar, así que queda para que el operador elija.

## 2026-09-13 — conftest.py fuerza `saimc` al checkout del worktree, no al de master
Contexto: al escribir el test de `POST /control/lint-drift` (entrada
siguiente), `monkeypatch.setattr(control.subprocess, ...)` reventó con
`AttributeError` — el módulo `control` cargado no tenía el código que
acababa de escribir. Causa: el venv compartido tiene `saimc` instalado en
editable (`pip install -e`) apuntando al checkout de `master`, fijo desde que
se instaló; no sabe de worktrees. Cada `pytest` corrido en este worktree
durante esta sesión (entradas de checklist-pr, ruff/mypy) importó `saimc` de
`master`, no de acá. Ya estaba anotado como hallazgo de sesiones anteriores
("PYTHONPATH=<worktree>/src obligatorio") pero exigía acordarse de
exportarlo a mano cada vez, y hoy no se hizo.
Impacto real de lo ya cerrado hoy: ninguno. Los cambios de las dos entradas
anteriores (checklist-pr.md, config de ruff/mypy, el fix de
`test_processing_layer.py`) son documentación, configuración de herramienta
o anotaciones de tipo bajo `TYPE_CHECKING` — verificados por `ruff`/`mypy`
directo sobre los archivos (que sí leen del worktree, no pasan por el
paquete instalado) o sin comportamiento en runtime que un test pudiera
romper. El riesgo era silencioso para cualquier cambio *futuro* que sí
tocara comportamiento de la app.
Decisión: `tests/conftest.py` inserta `<este_checkout>/src` al frente de
`sys.path` antes de cualquier `import saimc`, así se resuelve siempre contra
el checkout de donde corre pytest, gane o no la carrera contra el `.pth` del
sitio. Nota: esto cubre `pytest`. Correr `uvicorn` a mano en un worktree
sigue necesitando `PYTHONPATH=<worktree>/src` explícito — no hay conftest.py
para invocaciones fuera de pytest.
Descartado: seguir dependiendo de acordarse de exportar `PYTHONPATH` cada
sesión — ya falló dos veces (esta y una anterior, registrada en Engram).
Consecuencia: `pytest` en cualquier worktree de este repo prueba su propio
código, no el de `master`, sin variables de entorno. Suite: 1611 pasan (2 más
que antes: los tests nuevos de la entrada siguiente), 1 saltea.

## 2026-09-13 — Botón en /control dispara scripts/lint_drift.py
Contexto: siguiendo la entrada de `lint_drift.py`, el operador propuso
disparar el job desde `/control` con un click en vez de un cron/systemd —
evita la infraestructura de la decisión 4 (no hay servidor que operar) y
sigue siendo una acción deliberada, no un job sin supervisión. Se preguntó
explícitamente el alcance: ¿el botón solo reporta, o también aplica el
`--fix` seguro y commitea como el script completo? El operador eligió lo
segundo — el webapp gana la capacidad de invocar un subproceso y escribir al
repo vía HTTP, sin autenticación (decisión 5).
Decisión: `POST /control/lint-drift` (nuevo, en `control.py`) corre
`scripts/lint_drift.py` como subproceso con ruta absoluta fija —el pedido
HTTP no participa de qué se ejecuta— y devuelve su salida tal cual en
`control_lint_drift.html`, una página chica aparte (no la vista completa de
`/control`, para no duplicar el armado de contexto de `control_panel`).
Botón `m3-btn` + `<form method="post">`, mismo patrón que ya usa
`catalog.html` — no HTMX (vendorizado desde el 2026-08-22 pero sin uso real
todavía; introducirlo recién acá hubiera sido una decisión de frontend más
grande que "agregar un botón"). El script tarda ~2.6s: corre síncrono dentro
del request, sin cola (decisión 3 sigue sin tocarse).
Descartado: que el botón solo reporte sin escribir (alternativa más
conservadora que propuse y el operador no eligió) — queda registrado el
tradeoff por si se revisita: cualquiera que llegue a `/control` sin
autenticarse puede ahora hacer que el servidor cree un commit.
Consecuencia: piso de `docs/lint-baseline.json` actualizado (65 ruff + 142
mypy — sumó 1: el propio `run_lint_drift` devuelve `Any` de
`TemplateResponse`, mismo patrón ya aceptado 13 veces en el resto del
código). Tests con el subproceso mockeado en `TestLintDrift`
(`tests/test_control.py`); confirmado también en vivo con `curl` contra un
`uvicorn` real (detectó el árbol sucio de esta misma sesión y saltó el
autofix, como debía).

## 2026-09-13 — /control/lint-drift muestra elementos visuales, no texto plano
Contexto: al ver la primera versión en producción, el operador pidió un
elemento visual que explicara los hallazgos en vez de un `<pre>` con el
texto crudo del script.
Decisión: `scripts/lint_drift.py` gana `--json` (paralelo al modo texto, que
no cambió: se comparó línea por línea contra la salida anterior) — imprime
`{autofix, drift: [{tool, total, baseline, new, resolved}], has_new}` en vez
de la narración. `POST /control/lint-drift` ahora llama con `--json`, parsea
y arma la página con los componentes M3 que ya existían para exactamente
este propósito (`m3-banner-ok/-warn`, `m3-badge-mono-ok/-warn`, `m3-table`)
en vez de inventar estilos nuevos. Si el JSON no parsea (el script crasheó,
cambió de forma), degrada al texto crudo en un banner de error — no un 500.
Efecto lateral: sumé dos iconos nuevos (`new_releases`, `task_alt`) a las
plantillas, y `tests/test_static_assets.py::test_el_subconjunto_cubre...`
—que existe justo para esto— se puso roja hasta correr
`python tools/build_icon_font.py` (regenera el subconjunto vendorizado
contra Google Fonts, ver ese archivo). Sin sorpresas: es la herramienta que
el propio mensaje del test nombra.
Consecuencia: el modo texto de `lint_drift.py` sigue igual para uso por
terminal (`docs/checklist-pr.md` lo sigue apuntando ahí). El piso no se
movió (65 ruff + 142 mypy, sin drift). Tests con el JSON completo mockeado
en `TestLintDrift`, incluido el caso de degradación cuando no parsea.

## 2026-09-13 — /control/lint-drift muestra toda la deuda, no solo el drift
Contexto: el operador vio la versión con banners/tabla en producción y
señaló que faltaban hallazgos — la página solo mostraba lo nuevo contra el
piso (1 de 66 en ruff), no los otros 65 que ya estaban ahí desde antes. La
intención original ("elemento visual que explique los errores") era ver
la deuda completa, no solo el delta del día.
Decisión: `_calcular_drift` en `scripts/lint_drift.py` suma `known`
(`actual - nuevos`: lo que sigue presente y ya era parte del piso) al JSON
de cada herramienta. El modo texto no cambia —sigue mostrando solo
new/resolved, 65+142 líneas todos los días por terminal sería ruido, no
señal para el chequeo diario—; `known` es exclusivo de `--json`. La
plantilla lo agrega como un tercer desplegable ("N ya conocidos, parte del
piso"), mismo patrón que "resueltos" pero con badge neutral en vez de
verde/rojo.
Consecuencia: la página ahora expone los 65+142 hallazgos completos,
colapsados por default (no compiten visualmente con lo nuevo, que es lo
urgente). Test nuevo en `TestLintDrift` que cubre el caso. Sin drift de
piso por este cambio.

## 2026-09-13 — Ícono ilustrado por producto en el panel (no Material Symbols)
Contexto: al elegir una provincia y un producto en la home pública, el
panel flotante (`.ph-fpanel`) ya acotaba sus bloques a esa sola provincia
—mediana, rango y conteo salen filtrados por `selected_province` desde
antes de este cambio, vía `_visible_observations`—, pero el ícono del
header seguía siendo la aproximación genérica de Material Symbols
(`PRODUCT_ICONS`: aceite → `water_drop`, una gota cualquiera, no una
botella). El operador lo pidió explícito, con experiencia previa
representando información agregada sobre mapas (OpenLayers + cluster).
Decisión: ícono ilustrado propio por producto piloto, SVG dibujado a mano
e incluido inline (`templates/_product_illustration.html`, mismo patrón
que `_range_chart.html`), solo en el header del panel — los chips del
riel siguen con Material Symbols, no se tocaron. `product_nav.PRODUCT_ILLUSTRATIONS`
(frozenset de slugs) + `has_product_illustration()` deciden si el panel
usa la ilustración o cae al glifo de `product_icon()`. Sin color propio:
`currentColor`, que `.ph-fpanel-illustration` fija a `--ph-accent` — no
abre una paleta fija nueva (`TestNingunColorFijoEnLasPlantillas`).
Descartado: vendorizar un set de íconos ilustrativos de terceros — suma
licencia a trackear y una dependencia de red por 5 archivos chicos que se
pueden dibujar a mano; también extender `PRODUCT_ICONS`/`tools/build_icon_font.py`
buscando el glifo de Material Symbols más parecido — evaluado y
descartado porque el pedido explícito era una ilustración reconocible
(botella), no una aproximación mejor dentro del mismo catálogo genérico.
Consecuencia: un producto piloto nuevo sin entrada en `PRODUCT_ILLUSTRATIONS`
cae al ícono de Material Symbols del chip (o sin ícono, igual que hoy);
agregar su ilustración es sumar un `{% elif %}` a `_product_illustration.html`
y su slug al frozenset. Verificado con test de endpoint
(`test_provincia_y_producto_acota_el_panel_a_esa_provincia`) y de unidad
en `panel_view` — pendiente que el operador vea las 5 formas en el
navegador, son dibujadas a mano sin herramienta de diseño.

## 2026-09-13 — Insignia sobre el mapa + sin sombreado en la provincia elegida
Contexto: el operador probó el slice anterior (ícono en el panel flotante)
y aclaró dos cosas al ver el mapa: (1) esperaba el ícono pegado a la
provincia dentro del mapa, no solo en la ventana flotante aparte — se
preguntó explícitamente y confirmó vía AskUserQuestion; (2) la provincia
elegida se pinta con el relleno de la capa «densidad» (forzado desde
`estiloDeProvincia`, ver 2026-09-12) y pidió sacarlo: «con los bordes me
basta».
Decisión: (1) Insignia server-rendered (ícono ilustrado + mediana + conteo)
en un `<template id="ph-map-badge">` dentro de `.ph-stage`, que
`inicializarMapa()` clona con `L.divIcon` (`iconSize:[0,0]` +
`iconAnchor:[0,0]` + `translate(-50%,-50%)` en CSS, para centrarla sin
conocer su ancho de antemano) en `capaElegida.getBounds().getCenter()`
—el centro del recuadro, no el centroide exacto del polígono, alcanza
para una insignia—. Solo se arma con UNA provincia elegida y UN producto
activo con dato ahí (`product_panels | selectattr("has_data") | length
== 1`): con más de un producto activo no se dibuja, para no superponer
íconos en el mismo punto; el panel flotante sigue siendo la fuente
completa en ese caso. (2) `estiloDeProvincia()`: `if (esElegida)
opacidadRelleno = 0` después de la atenuación de las otras 15 — el borde
(`weight` ya venía más grueso para la elegida) y ahora también la
insignia siguen marcando cuál es, la capa densidad no se toca para el
resto del mapa ni para el toggle Publicación/Densidad general.
Descartado: mover el ícono del panel flotante al mapa en vez de sumarlo
—el panel sigue siendo necesario para el rango completo y el cambio %,
que no entran en una insignia chica—; centroide geométrico real (turf.js
o similar) en vez de `getBounds().getCenter()` — de más para una forma
que ya se ve encuadrada a pantalla completa, el bounding-box alcanza.
Consecuencia: `interactive:false` + `pointer-events:none` en la insignia
para que el clic/hover siga llegando al polígono de abajo. Sin test
automatizado para el sombreado (es JS puro sobre Leaflet, sin arnés de
test de UI en el repo, mismo criterio que el resto de `estiloDeProvincia`);
sí hay 4 tests de endpoint nuevos para cuándo aparece/no aparece la
insignia. Sigue pendiente que el operador vea el resultado real en
navegador — mismo motivo que el slice anterior, sin Postgres accesible
en el entorno de la sesión.

## 2026-09-13 — Insignia: apilar por producto en vez de ocultar con 2+
Contexto: el operador probó la insignia de la entrada anterior y reportó
"no veo los iconos... lo vi en algún momento pero no está presente todo
el tiempo". Preguntado si tenía uno o varios productos prendidos a la
vez, confirmó: varios. La regla original —ocultar la insignia entera con
2+ productos activos, para no superponer íconos en el mismo punto— se
disparaba sola porque los chips no se apagan al tocarlos de nuevo
(`ProductChip`, decisión previa 2026-09-11: cerrar es cosa de la × del
panel), así que acumular productos activos sin querer es el uso normal,
no un caso raro.
Decisión: en vez de ocultar, se apila una insignia por cada producto
activo con dato en la provincia elegida (`.ph-map-badge-stack`, columna
con `gap`), dentro del mismo `<template id="ph-map-badge">` y el mismo
`L.divIcon`/marcador — `inicializarMapa()` no cambió, sigue clonando un
solo elemento. `panel_con_dato` deja de filtrarse a `length == 1`.
Descartado: pedirle al operador que primero cierre los paneles sobrantes
—le traslada a él un problema de diseño—; una insignia combinada con
todos los productos en una sola línea —ilegible pasado de 2 productos,
y no se sabe de antemano cuántos van a estar activos—.
Consecuencia: con muchos productos activos a la vez la columna puede
crecer bastante alto sobre la provincia; no se puso un tope porque no
hay evidencia todavía de que eso pase en uso real (el operador prueba
con 1-2 productos por ahora). Tests actualizados: el que afirmaba "con 2
productos no hay insignia" se reemplaza por uno que cuenta 2
`.ph-map-badge` dentro del stack, más uno nuevo que confirma que un
producto activo SIN dato en esa provincia no deja un hueco vacío en la
columna.

## 2026-09-13 — Insignia también sin provincia elegida (una por cada una)
Contexto: el operador confirmó que la insignia SÍ se ve con una provincia
elegida, pero "si no tengo ninguna provincia seleccionada y selecciono
algún productos entonces no se ven". Causa real: las dos entradas
anteriores de hoy condicionaban todo el bloque a `selected_province`
truthy — mirando el país entero (`GET /` sin `?provincia=`) nunca se
armaba ningún `<template>`, sin importar cuántos productos estuvieran
activos ni cuánto dato tuvieran.
Decisión: se saca la dependencia de `selected_province` del todo. La
agrupación pasa de Jinja a Python: `panel_view.group_blocks_by_province`
recorre los bloques de TODOS los paneles activos y arma
`dict[slug_provincia, list[(ProductPanel, PanelBlock)]]`
(`routers/public.py`, contexto `map_badges_by_province`). La plantilla
ya no arma un solo `<template id="ph-map-badge">`: arma uno por cada
provincia con dato (`id="ph-map-badge-<slug>"`). El JS se mueve de "una
vez después de `fitBounds`" a adentro de `onEachFeature`, que ya corre
una vez por provincia — busca su propio `ph-map-badge-<slug>` y lo clona
si existe. Con una provincia elegida el resultado no cambia (`blocks` ya
viene filtrado a esa sola provincia desde `_render_home`, así que
`agrupado` tiene una sola clave); sin elegir ninguna, aparece una
insignia por cada provincia con dato del producto activo — el
comportamiento tipo "cluster" que el operador pedía desde el principio
(spec original: ícono al elegir producto, sin mencionar que hiciera
falta acotar a una provincia primero).
Descartado: mantener dos code paths (uno con provincia elegida, otro
sin) — la agrupación por provincia sirve para los dos casos sin
bifurcar, es menos código y menos superficie de bug.
Consecuencia: `product_panels` pasa a calcularse una sola vez en
`_render_home` (antes se armaba inline dentro del diccionario de
contexto) y se reusa para `"product_panels"` y `map_badges_by_province`
— mismo resultado, sin duplicar el cálculo. 8 tests cubren ahora el
agrupado (`test_panel_blocks.py::TestGroupBlocksByProvince`, 3 casos) y
el render por provincia (`test_public_filter.py`, ids
`ph-map-badge-<slug>` por provincia, incluido el caso sin
`selected_province`).

## 2026-09-13 — Insignia colapsa a cluster con 2+ productos (sin tope)
Contexto: el operador vio la columna de insignias apiladas y señaló la
preocupación real detrás de todo el hilo del día: hoy son 5 productos
piloto, pero mañana pueden ser 200 — una columna que crece 1:1 con
productos activos no escala. Trajo como referencia
https://googlemaps.github.io/js-markerclusterer/ preguntando qué idea de
ahí se podía robar. La pieza aplicable no es el clustering geográfico en
sí (sin geolocalización por anuncio no hay puntos que agrupar) sino el
patrón de interacción: colapsado por defecto a un círculo con un número,
que se abre recién al tocarlo.
Decisión: con 2+ productos con dato en una provincia, la insignia
colapsa a un cluster — `<details class="ph-map-badge-cluster">` +
`<summary class="ph-map-badge-count">{{ N }}</summary>`, nativos del
navegador, sin JS propio (mismo mecanismo que ya usa `_range_chart.html`
para la ayuda de los gráficos). El stack de `.ph-map-badge` por producto
queda adentro, oculto hasta que se abre. Con un solo producto no hay
nada que clusterizar: se pinta la insignia sola, sin círculo, igual que
antes. El ítem repetido (ícono+mediana+conteo) se extrajo a
`templates/_map_badge_item.html` para no duplicarlo entre el caso simple
y cada fila del cluster. `pointer-events` se reorganiza: el wrap sigue en
`none` (el resto de la provincia sigue siendo clickeable para navegar),
pero `.ph-map-badge-root` —la raíz de cada insignia, clusterizada o
no— pasa a `auto`, porque el círculo del cluster sí necesita recibir el
click para abrirse.
Descartado: JS propio con `addEventListener` para el toggle — `<details>`
ya lo resuelve gratis, sin estado que mantener sincronizado entre
re-renders de htmx. Tope numérico duro (ej. "mostrar máximo 5 y agregar
+195 aparte") — el cluster ya resuelve el problema de espacio sin
inventar un umbral nuevo que calibrar; si el cluster abierto llega a ser
demasiado alto con cientos de productos, ese es el próximo problema a
mirar cuando exista de verdad, no antes.
Consecuencia: el número del círculo es la cantidad de PRODUCTOS con dato
ahí, no una suma de anuncios ni de precios entre productos distintos
(mezclaría monedas/unidades sin sentido) — mismo criterio que ya regía
`spec.md` §9.0 contra un valor puntual sin respaldo. 2 tests nuevos en
`test_public_filter.py` (colapsa con 2+, no colapsa con 1). Sigue
pendiente la verificación visual real del operador — ni el clic para
abrir el cluster ni el `<details>` nativo se pueden probar con
`TestClient`, solo su presencia en el HTML.

## 2026-09-14 — Insignia rediseñada: cartelito de precio, no píldora SaaS
Contexto: el operador aprobó la mecánica del cluster pero calificó la
estética de la insignia como "un asco" y pidió una pasada de diseño real
en vez de solo funcional. La versión anterior era exactamente el default
genérico de IA: píldora blanca redondeada, sombra gris suave, texto sans
— el mismo look que cualquier tarjeta de notificación SaaS, sin relación
con el proyecto.
Decisión: se usó el skill `frontend-design` para una pasada de diseño
deliberada. Concepto elegido: "cartelito de precio de mercado", no
notificación — el objeto real que este proyecto capta. Cambios concretos
en `public_home.html`/`_map_badge_item.html`: (1) fondo sólido
`--ph-accent` en vez de blanco —la marca de la capa pública, no un color
nuevo—; (2) la cifra pasa a Alice (`--ph-font-heading`), la misma serif
que ya usan `.ph-card-range .num` y `.ph-stat-value` para "el número que
importa" en el resto de la app, en vez de sans genérico; (3) forma de
cartelito con una colita triangular que señala el punto exacto del mapa
(`::after` + `transform: translate(-50%, calc(-100% - 7px))` en la raíz),
como un pin, en vez de flotar centrada como un toast; (4) el cluster pasa
a ser una ficha/moneda —círculo sólido del mismo azul con un canto claro
(`border: 2px solid var(--ph-on-accent)`) y el número también en Alice—,
mismo material que el cartelito pero otra forma porque señala una
cantidad, no un precio. Se mantiene centrado sobre el punto (no con
colita): es el criterio de cualquier librería de clustering, el punto
ES el cluster.
Descartado: paleta o tipografía nuevas para este componente — meterle
una identidad propia a una insignia chica la aislaría del resto de la
página; la pasada de diseño reutiliza exactamente lo que la home pública
ya tiene (azul de marca, serif de cifras), no inventa una tercera cosa.
Un ícono de "pin" de mapa genérico en vez de la colita dibujada — ya
existen las 5 ilustraciones de producto (aceite, arroz, etc.), sumar
otro ícono redundaría.
Consecuencia: sin cambios de comportamiento ni de tests (mismas clases
`ph-map-badge`/`ph-map-badge-count`/`ph-map-badge-cluster`, los 8 tests
de insignia siguen pasando tal cual); es una pasada puramente visual.
Sigue pendiente la verificación real en navegador — es la tercera vez en
el día que este componente se ajusta a ciegas sin poder verlo el mismo
Claude, dado que el Postgres local del entorno de desarrollo no tiene
credenciales válidas.

## 2026-09-14 — Candidato a fase futura: vista simple por tipo de usuario
Contexto: el operador planteó la prueba de la tía sin formación técnica —
si le pasa el link de Guani, ¿responde la página lo que ella espera sin
esfuerzo? `spec.md` §6.7 fija exactamente esa vara: "cualquier cubano, en
5 segundos" tiene que saber si un producto está caro/barato, si sube o
baja, si conviene comprar ahora. Contrastado contra la home actual —
mediana vs. promedio explicada en un tooltip, capas "densidad/
publicación", ventanas flotantes arrastrables, triángulos de "fuera de
escala"—, la respuesta es que no la pasa: son elementos que asumen
cierta alfabetización estadística y de interfaz.
Decisión: no se resuelve ahora. Se anota como candidato a fase futura:
una vista/módulo separado por tipo de usuario (lego vs. quien quiere el
detalle metodológico), en vez de simplificar la vista única existente.
No es un ajuste de copy: el semáforo rojo/verde y el "precio promedio"
que pide §6.7 son EXACTAMENTE lo que el resto del proyecto bloquea a
propósito hoy — `§12.6` prohíbe inventar umbrales sin calibrar, y por
eso `routers/public.py` ya documenta "el coloreado del mapa por precio y
los semáforos siguen bloqueados por el umbral sin calibrar (§12.6)". Una
vista simple honesta recién es posible cuando exista un umbral real que
calibrar (atado a H0, §3/§9), no antes — construirla hoy obligaría a
inventar el mismo umbral que el proyecto ya se negó a inventar en el
mapa.
Descartado: simplificar la vista única actual quitándole las etiquetas
técnicas sin más — mediana/rango/muestra no son ruido cosmético, son la
disciplina anti-§9.0 (nunca un valor puntual sin respaldo); sacarlos
para "verse simple" escondería la incertidumbre, no la resolvería.
Consecuencia: ninguna todavía — es un candidato registrado, no una
tarea. Se retoma cuando H0 tenga un número real (criterio de salida de
Fase 0, `spec.md` §9) o cuando el operador decida priorizarlo antes.

## 2026-09-14 — Tarjeta simple: cambio vs. período anterior + ícono
Contexto: siguiendo la prueba de la tía (entrada anterior), se revisó
`.ph-card` (`#precios` sin producto único enfocado) contra las tres
preguntas de §6.7. Ya contestaba «¿caro o barato?» en prosa simple
(mediana traducida a «mitad de los anuncios, cerca de X», no la palabra
«mediana» suelta) y «¿con qué respaldo?» (muestra siempre visible). Le
faltaba «¿sube o baja?»: ese dato existe hace tiempo
(`Headline.change_text`, ya usado en el panel flotante) pero nunca llegó
a la tarjeta simple.
Decisión: `card_view.attach_trends(cards, headlines)` empareja por
producto+provincia+moneda+unidad (mismo patrón que
`panel_view.attach_blocks`) y pega `ProductCard.change_text`. La
plantilla lo muestra con la clase ya existente `.ph-cifra-cambio` —el
mismo número sin color que el panel, no un elemento nuevo—, como línea
propia entre la mediana y la muestra. De paso, la tarjeta suma el ícono
ilustrado del producto (`ProductCard.has_illustration`/`.icon`/`.slug`,
mismas properties que ya tenía `ProductPanel`) como ancla visual junto
al nombre, reusando `_product_illustration.html` con `{% set panel =
card %}`.
Descartado: un semáforo o flecha de color sobre el cambio — seguiría
siendo el umbral inventado que §12.6 prohíbe; el número solo (como ya
lo hace el panel) es la versión honesta. Calcular el cambio a nivel de
formato/variante en vez de aggregate por producto — no hay comparación
histórica por formato calculada todavía en ningún lado; una tarjeta de
formato hereda el cambio agregado del producto entero en esa provincia,
documentado como aproximación en el docstring de `attach_trends`.
Consecuencia: import circular latente entre `card_view.py` y
`price_charts.py` (`price_charts` ya importaba `format_price` de
`card_view`) resuelto con `Headline` importado solo bajo
`TYPE_CHECKING` en `card_view.py` —la anotación es perezosa por `from
__future__ import annotations`, así que mypy la resuelve sin ejecutar
el import en runtime—. 8 tests nuevos: `test_card_view.py`
(`TestAttachTrends`, `TestIconoDeTarjeta`) y `test_public_filter.py`
(`TestTendenciaEnLaTarjeta`, con y sin período anterior). Sigue
pendiente la verificación visual real del operador.

## 2026-09-14 — Fix: controles del panel maximizado tapados por el compás
Contexto: el operador reportó que con un panel de producto maximizado
era imposible trabajar porque sus controles (minimizar/cerrar) quedaban
detrás de otros íconos. Causa raíz, no visual sino de contexto de
apilamiento CSS: `.ph-dock-layer` (`position: relative; z-index: 1200`)
crea su propio contexto de apilamiento. `alFrente()` le pone a cada
ventana un `z-index` a mano (20-44, «por debajo de la hoja de
provincia») pero ESE número solo compite contra sus ventanas hermanas
DENTRO de `.ph-dock-layer` — nunca contra la barra superior (z-index
1300, un HERMANO de `.ph-dock-layer`, no un descendiente). Ni siquiera
`position: fixed` (que ya tiene `.is-floating`) escapa el contexto de un
ancestro que ya lo fijó: es el gotcha clásico de "fixed atrapado en un
ancestro con z-index". Una ventana expandida a pantalla completa
(`expandir()`, `.is-max`) queda entonces SIEMPRE detrás del compás,
sin importar qué tan alto suba `alFrente()` el número.
Decisión: en vez de subirle el z-index a la ventana (no serviría de
nada, es el mismo contexto), `pintar()` llama a `actualizarDockLayer()`,
que prende `.ph-dock-layer.has-max` (z-index 1500, arriba de la barra y
del indicador de carga) mientras exista alguna `[data-win].is-max` en la
página, y lo apaga si no. Documentado en el comentario de la escala de
z-index (arriba de `.ph-stage`) para que la próxima vez que alguien
toque esa escala vea la trampa antes de repetirla.
Descartado: escribir `expandir()` con `position: fixed` sacando la
ventana del DOM de `.ph-dock-layer` (moverla a `document.body`
temporalmente) — funcionaría, pero perdería el `pointer-events` y la
lógica de agarres/arrastre que dependen de la jerarquía actual; el fix
de una sola clase en el ancestro es más chico y no toca nada de eso.
Consecuencia: al escribir `actualizarDockLayer()` con el
`document.querySelector(...)` anidado directo como segundo argumento de
`classList.toggle(...)`, `TestClasesDelJavascriptExisten` lo marcó como
clase huérfana — el regex del test extrae CUALQUIER string entre
comillas dentro del primer paréntesis que cierra, y `"[data-win].is-max"`
(el selector) quedó adentro de esa captura. Se resolvió sacando el
`querySelector` a una variable aparte antes del `.toggle(...)`, más
prolijo igual. Sin test automatizado del comportamiento en sí —es JS
puro sobre `position`/contextos de apilamiento, sin arnés de test de UI
en el repo—; el operador tiene que confirmar en el navegador.

## 2026-09-14 — El panel flotante deja de fundir formatos distintos
Contexto: el operador preguntó por qué la tarjeta de pollo no mostraba
cómo varía el precio según el envase, y al verificar salió que el
mecanismo SÍ existe —`compute_retail_ranges` (`cells.py`) ya agrupa por
formato, y `/control` y la tabla enfocada de `#precios` ya lo muestran—.
El operador corrigió: en `/control` SÍ ve esa variación; en home, no.
Causa raíz: el PANEL FLOTANTE (`.ph-fpanel`) es la única vista que la
pierde. `price_charts._group` —de la que salen `Headline` (la cifra) y
`RangeSeries` (el gráfico de rango)— agrupaba por producto × provincia ×
moneda × unidad, sin el formato: una botella y una garrafa del mismo
aceite en la misma provincia se fundían en un solo rango ciego al
envase, aunque `/control` y la tabla ya las separaban.
Decisión: se sumó el formato al eje de agrupado de `_group`
(`price_charts.py`), y `Headline`/`RangeSeries` ganaron el campo
`format_label`. Con eso, `panel_view.attach_blocks` ya arma un
`PanelBlock` por provincia × formato en vez de uno por provincia a
secas —solo hizo falta emparejar `rango_de` por la misma clave ampliada—.
La plantilla (`.ph-cifra-prov`) muestra "La Habana · Botella de 1 L"
en vez de solo "La Habana" cuando el producto tiene formato declarado.
Otras cuatro funciones de `price_charts.py` también usan `_group`
(`build_province_comparison`, `_by_product`/`build_median_steps`,
`build_month_vs_month`) pero responden preguntas de PROVINCIA
("¿dónde está más caro?", "¿subió o bajó?"), no de formato —ahí se
vuelve a fundir el formato explícitamente después de agrupar, para no
cambiarles el comportamiento (ya probado, sigue pasando sin tocar).
`panel_view.group_blocks_by_province` (la insignia del mapa, entrada de
ayer) también se ajustó: con 2+ formatos del mismo producto en la misma
provincia se queda con uno solo —el de más muestra—, porque la insignia
es una lectura rápida y desglosar por formato ahí sería ruido, no señal.
Descartado: mostrar el precio propio del formato (como hace
`card_view.format_median`, "302,73 CUP/lb × 25 kg → 16.685 CUP el
saco") en el panel también — el pedido era separar el eje, no sumar una
cifra nueva; queda anotado como posible mejora si hace falta.
Consecuencia: `capitalize` de Jinja también pone en minúscula el resto
del string, así que "botella de 1 L" se leía "Botella de 1 l" con la
unidad rota — se cambió a subir solo la primera letra a mano
(`cifra.format_label[0] | upper`). 8 tests nuevos cubren el eje nuevo:
`test_price_charts.py::TestEjeDeFormato` (3), `test_panel_blocks.py`
(2, uno por `attach_blocks` y uno por `group_blocks_by_province`) y
`test_public_filter.py::TestFormatoEnElPanel` (2, endpoint). Suite
completa sin regresiones (1640 casos) y sin deuda de lint nueva.

## 2026-09-14 — Panel: solo el primer bloque arranca abierto
Contexto: apenas cerrada la separación por formato, el operador vio el
panel crecer con 2+ bloques por producto (una provincia por formato) y
pidió que todos menos el primero arranquen colapsados, con opción de
abrir y cerrar.
Decisión: cada `.ph-cifra` pasa de `<section>` a `<details>`, con
`<summary class="ph-cifra-prov">` mostrando provincia + formato + la
mediana de vistazo (oculta cuando el bloque está abierto, porque ya se
repite grande adentro). Solo el primer bloque —el de más muestra, que ya
viene ordenado así desde `build_headlines`— lleva el atributo `open`
(`{% if loop.first %} open{% endif %}`); el resto arranca cerrado.
Mismo mecanismo nativo que ya usa la insignia del mapa (`<details>`/
`<summary>`, sin JS propio), aplicado ahora también acá. De paso, para
resolver el mismo problema de `capitalize` que ya había aparecido en la
entrada anterior, `format_label_display` (property nueva en `Headline`)
reemplaza el `[0]|upper` inline de la plantilla por código Python
testeable.
Descartado: JS propio para el estado abierto/cerrado — `<details>` lo
resuelve gratis. Recordar cuál bloque estaba abierto entre re-renders de
htmx — no se pidió, y el criterio "el de más muestra" ya es determinista
y estable mientras la muestra no cambie de orden.
Consecuencia: `.ph-cifra` deja de ser `display: grid` directo —ahora
ese layout vive en `.ph-cifra-body`, adentro del `<details>`, porque
`<summary>` pasó a ser un hijo directo y compite por ese grid—. 3 tests
nuevos (`test_price_charts.py` +2 para `format_label_display`,
`test_public_filter.py::test_solo_el_primer_bloque_arranca_abierto`) y
uno existente ajustado a la nueva estructura del DOM
(`test_sin_formato_declarado_sigue_una_sola_cifra`, que buscaba
`</div>` donde ahora hay `</summary>`). Suite completa: 1643 casos, sin
deuda de lint nueva.

## 2026-09-14 — Chevron Material + chip de formato en el panel
Contexto: recién agregado el colapso de bloques, el operador pidió dos
cosas más, explícitamente "filosofía material": (1) una marca visual de
que el bloque se abre/cierra —el triángulo nativo del `<details>` no
alcanza, casi no se nota en algunos motores—; (2) que el formato, el eje
nuevo de la sesión, se lea como un concepto importante con chip/label/
color, no como texto de relleno.
Decisión: mismo criterio de la pasada de diseño anterior (reusar lo que
la home pública ya tiene, no inventar una identidad nueva). (1) Chevron: `expand_more` de Material Symbols —ya
vendorizado, lo usa `/control`, cero costo de red—, con
`transform: rotate(180deg)` atado a `.ph-cifra[open]`; se ocultó el
triángulo nativo (`list-style: none` + `::-webkit-details-marker`) para
no duplicar la señal. (2) El formato pasa de texto plano con middot a
`<span class="ph-chip">` — la MISMA clase que ya usa `.ph-card-sample`
para «muestra en formación» en las tarjetas simples, no un chip
inventado para la ocasión: mismo lenguaje visual en toda la home
pública para «esto es una etiqueta categórica».
Descartado: chip también para la provincia — con dos chips por fila
(provincia + formato) ninguno de los dos se lee como EL dato importante;
la provincia sigue como texto porque es el eje estructural (siempre
está), el formato es el que varía y el que hay que notar. Chip para
`change_text` (¿sube/baja?) — ya tiene peso visual propio
(`.ph-cifra-cambio`, junto al precio grande) y convertirlo en chip
hubiera competido con el del formato por atención.
Consecuencia: tests existentes ajustados — las dos aserciones que
buscaban `"La Habana · Botella de 1 L"` como string contiguo dejaron de
servir (el middot desapareció, provincia y formato son `<span>`
separados) y pasan a verificar presencia del chip
(`'<span class="ph-chip">Botella de 1 L</span>' in panel`); la que
verificaba ausencia de «·» pasa a verificar ausencia de la clase
`ph-chip` — assertion más directa sobre lo que realmente importa.

## 2026-09-14 — Recorrido hacia atrás: de cada cifra a sus anuncios
Contexto: el operador preguntó qué había pensado para que, desde la
tarjeta de precios, se pudiera llegar hasta los anuncios de origen —
"justo como hacemos en /control". Verificado antes de responder: el
mecanismo público YA existe (`GET /anuncio/{id}`, texto redactado, §6.5)
y YA se usa, pero solo dentro de «Cómo se movió» (`.ph-evo-anuncios`,
`AdRow`/`build_evolution_blocks`) — ahí lista los anuncios de todo el
PRODUCTO, mezclando provincias y formatos. Faltaba engancharlo a cada
`.ph-cifra` puntual, que es la celda que el visitante realmente está
mirando.
Decisión: `price_charts.build_cell_ads(observations)` agrupa con la
MISMA clave que `_group` (producto×provincia×moneda×unidad×formato) y
arma un `AdRow` por anuncio de esa celda exacta, más reciente primero.
`PanelBlock` gana el campo `ads: tuple[AdRow, ...]`; `attach_blocks`
recibe un `ads_by_cell` opcional (`Mapping` con la misma clave de 5) y
lo empareja igual que ya hace con `ranges`. La plantilla reusa el mismo
acordeón `.ph-evo-anuncios` que ya existía —ni una clase CSS nueva—,
solo sin la columna «Provincia» (redundante: ya está en el `<summary>`
del bloque).
Descartado: un endpoint nuevo tipo `/curation/history` para el público
—esa ruta es del operador, filtra el catálogo entero; acá alcanza con
lo que ya se computa para el panel—. Filtrar por outliers en la lista de
anuncios — mismo criterio que `AdRow` en «Cómo se movió»: el precio
real, tal como quedó, se muestra igual, no se esconde.
Consecuencia: dos ajustes de lint post-implementación — mypy exigió
repetir el chequeo `province_name is not None` dentro de
`build_cell_ads` (la garantía de `_group` no cruza la frontera de
función); ruff marcó un default mutable (`uuid.UUID(...)` como valor
por defecto de parámetro) en el helper de test, resuelto con una
constante de módulo. 13 tests nuevos: `test_price_charts.py::
TestBuildCellAds` (5), `test_panel_blocks.py` (+2) y
`test_public_filter.py::TestAnunciosEnElPanel` (2). Suite completa:
1652 casos, sin deuda de lint nueva.

## 2026-09-14 — Atribución de la TRMI en el pie de la capa pública
Contexto: revisión de mejoras no consideradas para la home; spec.md §5
("Posicionamiento frente a elTOQUE") marca como vinculante que "toda serie
derivada que use la TRMI como base de conversión debe atribuirla
visiblemente". La capa pública convierte a CUP usando la TRMI (todo lo que
no queda `unconverted`) sin mencionar la fuente en ningún lado — cero
menciones de elTOQUE fuera de `/control`, que es operador-only.
Decisión: se agrega una línea de atribución al pie de `_public_shell.html`,
junto al crédito ya existente a Natural Earth. Al vivir en el shell y no en
`public_home.html`, llega a toda la capa pública (`/`, `/anuncio/{id}`,
`/producto/{slug}`) sin repetirse por vista.
Descartado: marcar la atribución por tarjeta o por bloque de precio. El
requisito es "visible", no "junto a cada cifra"; repetirlo en cada tarjeta
sería ruido que compite con la jerarquía visual que se vino cuidando toda
la sesión (§6.7).
Consecuencia: cualquier vista pública nueva que use este shell hereda la
atribución sin tocar nada. Si algún día se agrega una fuente alternativa de
tipo de cambio (redundancia obligatoria, §5), este pie es el lugar natural
para sumarla.

## 2026-09-14 — Open Graph en la capa pública
Contexto: idea #2 del reporte de mejoras no consideradas para la home. El
`<title>` ya cambia por vista/producto pensando en compartir, pero un link
pegado en WhatsApp o Telegram —el canal por el que se mueve este mercado—
no mostraba ninguna vista previa: cero `og:title`/`og:description` en
`_public_shell.html`.
Decisión: se agregan `og:type`, `og:site_name`, `og:url`, `og:title` (via
`self.title()`, reusa el bloque `title` ya existente) y `og:description`
(bloque nuevo `og_description`, con default genérico en el shell y override
por vista en `public_home.html` — con y sin producto enfocado— y
`public_evidence.html`). `<meta name="description">` reusa el mismo bloque
para no mantener dos textos.
Descartado: `og:image`. No hay ningún asset de marca en el repo (ni logo ni
banner); generarlo o pedirlo es una decisión de diseño aparte, no de este
slice — se deja explícito en el HTML en vez de omitirlo en silencio.
Consecuencia: cualquier vista pública nueva que extienda el shell hereda
`og:title`/`og:description` genéricos automáticamente; si necesita algo más
específico, solo sobrescribe `{% block og_description %}`.

## 2026-09-14 — Tope de tamaño al riel de chips de producto
Contexto: idea #3 del reporte de mejoras no consideradas. `.ph-compass-filters`
(la isla de filtros arriba a la derecha del mapa) no tenía `max-width`, y
`.ph-chip-rail` no tenía `max-height`: con los 5 productos de hoy no se nota,
pero un catálogo de 200 estiraría la isla a lo ancho de todo el mapa y
después hacia abajo hasta taparlo — el riesgo real que el operador ya había
nombrado antes en esta sesión para la insignia del mapa ("hoy solo tenemos 5
productos mañana pueden ser 200"), sin resolver todavía para el riel de chips.
Decisión: `.ph-compass-filters` suma `max-width: min(22rem, 60vw)`;
`.ph-chip-rail` suma `max-height: 6.5rem; overflow-y: auto;
overscroll-behavior: contain` — mismo patrón ya usado en `.ph-evo-anuncios
.ph-table-card` y en el panel flotante. Con 5 chips el contenido entra
entero y no aparece scroll; con un catálogo grande, la isla se congela en
tamaño y el riel scrollea internamente en vez de invadir el mapa.
Descartado: buscador/agrupación por categoría dentro del riel. El reporte lo
nombraba como posible mejora, pero construirlo hoy es diseñar para 200
productos que no existen — sobreingeniería contra la regla de alcance del
proyecto. Queda anotado como candidato: si el catálogo crece de verdad, la
solución sigue siendo un buscador o agrupación, no otro tope de CSS.
Consecuencia: el contenimiento por CSS aguanta el crecimiento del catálogo
sin romper el layout; el día que haga falta un buscador real, la referencia
es esta entrada.

## 2026-09-14 — Instrumentación mínima de visitas a la capa pública
Contexto: idea #4 del reporte de mejoras no consideradas. spec.md §9 fija
la meta de Fase 1 en "100 usuarios activos diarios", pero no existe ninguna
forma de saber si se cumple: cero analítica en la home pública.
Decisión: middleware ASGI (`saimc/web/analytics.py`,
`registrar_visitas_publicas`) que deja una línea de log INFO por cada GET
exitoso (200) a una vista pública real (`/`, `/producto/{slug}`,
`/anuncio/{id}`), excluyendo los `hx-get` disparados por la propia página
(chips/selector de provincia — misma visita, no una nueva) y cualquier ruta
de operador o asset estático. El campo `visitor` es `sha256(IP+día)`
truncado, nunca la IP en texto plano. Sin tabla nueva: no toca el esquema,
así que no dispara el flujo de especificación completo que CLAUDE.md reserva
para esos cambios. El operador agrega a mano sobre el log
(`journalctl | grep public.pageview | grep -oP 'visitor=\K\S+' | sort -u | wc -l`
por día, receta en el docstring del módulo).
Descartado: una tabla de eventos en la base de datos con un dashboard
propio. Es lo que un producto de analítica real haría, pero es una decisión
de esquema (migración, política de retención, diseño de agregación) que
CLAUDE.md marca como territorio del flujo de especificación completo — no
algo para decidir en piloto automático mientras el operador está afuera.
Esta instrumentación es el paso mínimo reversible: pasa de "cero dato" a
"materia prima agregable a mano", sin comprometer a nada más grande.
Consecuencia: si el operador confirma que esto alcanza, se puede agregar un
script chico que corra el `grep` de arriba. Si hace falta un dashboard de
verdad, es la primera pieza de trabajo con esquema que corresponde pasar
por explorar → proponer → especificar → diseñar → implementar → verificar.

## 2026-09-14 — /producto/{slug} liviano: queda como candidato, no implementado
Contexto: idea #5 del reporte de mejoras no consideradas. `/producto/{slug}`
es el link pensado para compartir (el `<title>`, y desde hoy `og:title`/
`og:description`, ya reflejan el producto), pero carga la página completa:
mapa Leaflet interactivo + tiles de OSM + compás + dock de ventanas.
Se investigó el porqué antes de decidir si tocarlo: las tarjetas de precio
(`.ph-cards`, lo más liviano que tiene la vista) NO son una sección aparte
—viven adentro de `<section class="ph-win" data-win="...">`, la misma
ventana flotante/arrastrable/maximizable que se apoya sobre
`<section class="ph-canvas">` y su mapa Leaflet a sangre (`position:
absolute; inset:0`). Hoy no existe ninguna variante de esta página sin
mapa: sacar Leaflet deja el lienzo de fondo vacío y rompe el layout del
resto, porque todo lo demás se posiciona respecto a él.
Decisión: NO se construye una variante liviana en este slice. Es una
decisión de arquitectura de página nueva (qué sobrevive sin el mapa: ¿las
tarjetas solamente? ¿el gráfico de rango? ¿el enlace de vuelta al mapa
interactivo?), no un arreglo mecánico — y es exactamente la superficie que
el operador señaló como "la piedra angular" del proyecto (2026-09-14, ver
memoria de sesión). Tomar esa decisión en piloto automático, sin poder
verla en un navegador real en esta sesión (Postgres inaccesible, sin QA
visual posible), es el tipo de apuesta que CLAUDE.md pide frenar y avisar
en vez de resolver sola.
Descartado: forzar una versión a medias (ocultar el mapa por CSS sin
rediseñar el layout). Deja una página rota a la vista de cualquiera que
reciba el link — peor que no tocar nada.
Consecuencia: candidato a fase futura, ya diagnosticado y listo para
ejecutar cuando el operador defina el alcance. Camino recomendado: una
plantilla nueva y chica que reuse `cards`/`ProductCard` (`card_view.py`,
ya calculados en `routers/public.py::public_product`) sin `_public_shell`
del mapa — sin Leaflet, sin compás, sin dock —, con un enlace de vuelta a
`/?producto={slug}` para quien quiera el mapa interactivo completo.

## 2026-09-14 — Pista de primera visita en el compás
Contexto: idea #6 del reporte de mejoras no consideradas. Investigando antes
de tocar nada se confirmó que el gap es más profundo de lo que sonaba: TODO
el compás —título `<h1>`, "Qué cuesta y dónde lo sabemos", nota de frescura,
y el menú Cobertura/Precios/Evolución/Descarga/Zoom— arranca oculto
(`opacity: 0`) detrás de un botón circular sin ninguna etiqueta visible
(`.ph-compass`, solo `aria-label` para lector de pantalla). Un visitante
nuevo, sobre todo en celular (sin hover real), no tiene ninguna pista de que
ese círculo abre algo. El riel de chips de producto SÍ es visible y
accionable sin ayuda —no es el problema real, contra lo que sonaba el ítem
del reporte—; el compás sí lo es.
Decisión: pulso de una sola vez alrededor del botón del compás (`::after`,
CSS puro, `animation: ph-pista-pulso 1.8s ease-out 3`), gateado por
`localStorage` desde JS (`guani:compas-visto`) para que se muestre solo en
la primera visita de ese navegador y desaparezca apenas se toca, hace foco
o se hace clic en el compás. Respeta `prefers-reduced-motion: reduce`
(queda un anillo fijo, sin animación, mismo criterio que el resto de esta
vista). Auto-contenido en un IIFE aparte al pie del archivo: no modifica
`alternarCompas`/`abrirCompas` ni ninguna lógica ya en producción.
Descartado: mostrar las etiquetas del menú siempre en vez de solo al
interactuar. Es un cambio de layout dentro de `.ph-menu` que no se puede
verificar visualmente en esta sesión (Postgres inaccesible, sin navegador
real) — mismo motivo que frenó la idea #5. El pulso es aditivo puro: no
reordena ni redimensiona nada existente, así que no corre ese riesgo.
Descartado también: no tocar nada y dejarlo para cuando el operador pueda
ver la página. Esta pieza, a diferencia de la #5, es chica, reversible con
una línea (sacar la clase del botón) y no toca la arquitectura de la
página — dentro del margen que el "haz todas las tareas" ya cubre.
Consecuencia: si el operador prueba la home en una pestaña nueva/incógnito
debería ver el anillo pulsar tres veces sobre el compás; en una visita
repetida (mismo navegador) no vuelve a aparecer.

## 2026-09-14 — Auditoría de accesibilidad: un gap real, el resto ya estaba bien
Contexto: idea #7 del reporte de mejoras no consideradas — verificar teclado
y lector de pantalla sobre todo lo nuevo de la sesión (`<details>`/chevron/
drag), solo probado visualmente hasta ahora.
Auditoría realizada (sin navegador real disponible en esta sesión —
Postgres inaccesible—, así que fue lectura de código y de la cadena
HTML→CSS→accesibilidad, no una prueba con lector de pantalla real):
- `<details>`/`<summary>` (insignia del mapa, bloques `.ph-cifra`, acordeón
  de anuncios): semántica nativa correcta. El triángulo por defecto se
  oculta con `list-style`/`::-webkit-details-marker`, que es puramente
  visual y no toca el rol/estado que expone el navegador. El chevron
  (`expand_more`) es `aria-hidden="true"` — no duplica lo que ya anuncia
  `<summary>` por su cuenta.
- Insignia con solo un número visible (`.ph-map-badge-count`) ya traía
  `aria-label` descriptivo desde que se escribió, el mismo día. Los
  acordeones de anuncios tienen texto propio autodescriptivo ("3 anuncios
  detrás de esta cifra") — no necesitan `aria-label` aparte.
- `:focus-visible` global (`saimc-public.css`) sigue aplicando sobre todo lo
  nuevo: nada lo pisó con un reset que borre `outline`.
- Controles de ventana (cerrar/minimizar/maximizar) son `<button>`/`<a>`
  reales con `aria-label`/`aria-pressed`; arrastrar (`data-win-handle`) es
  mouse/touch únicamente, pero es conveniencia de reposicionamiento, no
  contenido — la ventana se usa entera sin necesidad de arrastrarla.
- El mapa Leaflet tiene alternativa accesible ya existente: el `<select>`
  de provincia (`ph-province-form`) degrada sin JS y cubre lo que el mapa
  no puede para quien no puede clickear un polígono.
Encontrado y corregido: `.ph-menu-item` (los círculos del compás —
Cobertura/Precios observados/Evolución/Descarga/Leyenda) solo revelaba su
etiqueta de texto con `:hover` o `.is-open`, nunca con `:focus-visible`.
Un lector de pantalla ya escuchaba el nombre correcto (cada ítem trae su
propio `aria-label`, sin relación con la etiqueta visual) pero alguien que
ve la pantalla y navega solo con teclado tabulaba círculo por círculo sin
ver ningún texto hasta activar uno a ciegas.
Decisión: se agrega `:focus-visible` a las dos reglas de `.ph-menu-item`
(revelado de `.ph-menu-label` y el resaltado de fondo/borde/color), mismo
tratamiento que ya recibe `:hover`. Cambio de una línea por regla, cero
riesgo de layout.
Consecuencia: con esto se cierran las 7 ideas del reporte de mejoras no
consideradas de esta sesión — 5 implementadas, 1 diagnosticada y registrada
como candidato (idea #5, `/producto/{slug}` liviano), y esta auditoría.

## 2026-09-14 — /producto/{slug} pasa a ser la vista liviana
Reemplaza a la entrada del mismo día «/producto/{slug} liviano: queda como
candidato, no implementado». El operador pidió estudiarlo mejor y volver
con un plan; ese estudio encontró un dato que cambiaba el peso de la
decisión (ver abajo) y, con el plan revisado, dio luz verde («metele»).
Contexto: `_product_link` (`product_nav.py`) ya hacía que cualquier chip de
producto, sin provincia ni ventana filtrada —el caso común—, apuntara a
`/producto/{slug}` y `hx-push-url="true"` empujara esa URL a la barra de
direcciones. No era solo "el link para compartir": es la URL en la que
termina casi cualquier sesión real tras un solo clic. Estudiado el costo:
Leaflet solo (JS+CSS) son 162.358 B sin comprimir (~46 KB gzip) más los
tiles de OpenStreetMap en vivo —varias a varias decenas de peticiones HTTP
a un host externo—, contra una conexión que CLAUDE.md marca como lenta e
intermitente. Todo lo que un panel de producto necesita (`ProductPanel` con
sus bloques por provincia y formato: cifra, rango, tendencia, anuncios) ya
se calculaba sin depender del mapa.
Decisión: `/producto/{slug}` renderiza `public_product_light.html` —sin
Leaflet, sin compás, sin dock—, con un enlace de vuelta a `/?producto=
{slug}` (`_build_focused_product_panel`, `routers/public.py`) para quien
quiera el mapa interactivo. `?provincia=`/`?dias=` se siguen aceptando para
acotar el panel; `?ver=`/`?capa=` (ventanas y capa del mapa) ya no
significan nada acá y se ignoran en silencio en vez de romper un enlace
viejo. `/?producto={slug}` sigue siendo la vista completa, sin cambios.
Descartado: una ruta nueva aparte (ej. `/producto/{slug}/simple`) dejando
la ruta limpia como estaba. Habría hecho falta inventar un mecanismo nuevo
para que alguien llegue a esa URL —hoy no hay botón de «compartir»—, y sin
eso la vista liviana quedaba sin ningún enlace real hacia ella. Elegido
explícitamente por el operador entre las dos opciones presentadas.
Descartado también: reusar el CSS de `.ph-cifra`/`.ph-chip`/`.ph-evo-
anuncios` de `public_home.html` tal cual. Esas clases están pensadas para
flotar sobre un mapa (vidrio, `backdrop-filter`) y viven en el `<style>` de
esa plantilla, no en `saimc-public.css`; traerlas sin cambios a una página
de fondo común se habría visto como una ventana flotante sin ventana. Se
escribió un CSS chico y propio (`ph-lite-*`), reusando los tokens
compartidos (`--ph-*`, `.m3-title`) que ya carga `_public_shell.html`.
Se dejó afuera de v1 el gráfico SVG de rango (`_range_chart.html`) y su
acordeón de ayuda: son autocontenidos pero añaden ~8 clases de CSS propias
sin las que el texto numérico (mediana, rango, conteo) ya contesta la
pregunta. Candidato a sumar después si el operador lo pide viendo la
página real.
Consecuencia: `test_el_query_string_sigue_siendo_alias` (2026-09-11) dejó
de ser cierto y se reescribió como `test_ya_no_es_alias_la_ruta_limpia_no_
trae_el_mapa`, afirmando explícitamente la diferencia. Suite completa:
1674 casos, sin deuda de lint nueva (un `no-any-return` nuevo en mypy —
tercera instancia del mismo `TemplateResponse` sin tipar que ya cargaban
`_render_home` y `public_evidence`— resuelto con `cast`, no con un bump de
piso).

## 2026-09-14 — El riel de chips engancha a la ruta limpia
Contexto: al preguntar "¿cómo llego a /producto/{slug}?" se destapó un error
de la entrada anterior («/producto/{slug} pasa a ser la vista liviana»):
esa entrada asumía que el riel de chips visible (`.ph-chip-rail`,
`build_product_chips`) ya apuntaba a la ruta limpia al prender el primer
producto. Falso — usaba `products_query` directo, que siempre arma
`?productos=`. La única función que sabía armar `/producto/<slug>`
(`_product_link`, vía `build_product_nav`) no estaba enganchada a ningún
lado: código huérfano con tests propios pero sin alcance real. Hasta esta
entrada, `/producto/{slug}` solo se llegaba escribiendo la URL a mano.
Decisión: `build_product_chips` arma la ruta limpia cuando encender un chip
deja UN solo producto activo y no hay ningún otro filtro puesto (provincia,
ventana de días, ventanas abiertas, capa de mapa no default) — mismo
criterio que ya tenía `_product_link`, sumándole `ver`/`capa` porque
`build_product_chips` sí los conoce. Dos o más productos, o cualquier
filtro puesto, siguen en `?productos=`.
El punto no obvio: la ruta limpia no puede llevar `hx-get`. El riel vive
dentro de `public_home.html` y sus chips normalmente hacen un swap parcial
contra `#ph-live` (`hx-target`/`hx-select` heredados); `public_product_
light.html` no tiene ese elemento —ni ningún otro DOM compatible—, así que
un swap ahí no encontraría nada que reemplazar y dejaría el riel roto.
`ProductChip` suma `is_full_navigation: bool`; la plantilla omite `hx-get`
en ese caso y el clic pasa a ser una navegación de página entera.
Descartado: reusar `_product_link`/`build_product_nav` tal cual. Esa función
no sabe de `ver`/`capa`, y extenderla exigía tocar su único caller actual
(código no wireado, fuera de alcance de este slice). Se escribió la
condición directo en `build_product_chips`, calculándola una sola vez y
reusándola para la URL y para `is_full_navigation`.
Consecuencia: `_product_link`/`build_product_nav` siguen sin uso real —
quedan para otra sesión decidir si se retiran o se les encuentra un lugar.
Suite completa: 1681 casos, sin deuda de lint nueva.

## 2026-09-14 — El chip se queda en el mapa; la vista simple se alcanza desde el panel
Reemplaza a la entrada del mismo día «El riel de chips engancha a la ruta
limpia». El operador probó ese enganche y no le gustó: «no me gusta que
vaya directo a esa vista y luego me das la opción de volver al mapa,
déjame en el mapa y permíteme ir a esa vista» — quería el flujo al revés.
Decisión: se revierte `build_product_chips` a su comportamiento de
siempre —todo chip va a `?productos=`, siempre con `hx-get`, nunca a la
ruta limpia—. En su lugar, `ProductPanel` suma `light_query`
(`panel_view.py`, vía el nuevo `light_product_query` en `product_nav.py`):
el panel flotante de cada producto activo trae ahora DOS enlaces —«Ver el
detalle por provincia» (de siempre) y «Vista simple para compartir»,
apuntando a `/producto/<slug>` sin `hx-get` por el mismo motivo técnico de
antes (esa vista no tiene `#ph-live`, un swap parcial no encontraría nada
que reemplazar). `light_query` preserva `provincia`/`dias` si están
puestos —la vista liviana los sigue aceptando—; no lleva `ver` ni `capa`,
que ahí no significan nada.
Descartado: mantener el enganche del chip y agregar SOLO el enlace del
panel (las dos entradas a la vez). El operador pidió explícitamente una
sola dirección: el mapa es el default, la vista simple es la salida
consciente, no algo en lo que se puede caer sin querer con el primer clic.
Consecuencia: `_product_link`/`build_product_nav` (código huérfano, ver la
entrada revertida) siguen sin uso real. Suite completa: 1679 casos, sin
deuda de lint nueva.

## 2026-09-14 — Date picker de rango de calendario en la home pública
Contexto: al corregir el enganche del riel de chips, el operador pidió
pensar el siguiente tema — "necesitamos un componente de fechas... que
va a regir el tiempo que se está midiendo para los anuncios procesados,
hoy estamos asumiendo los últimos 30 días". Se investigó el motor antes de
diseñar (fork dedicado): `compute_retail_observations`/`compute_retail_
ranges` (`cells.py`) filtran fecha en Python puro (`cutoff = hoy -
timedelta(days=days)`, sin tope superior), no en SQL — agregar un tope
opcional era un cambio chico y aditivo. Se le preguntó al operador si el
picker debía elegir un rango de calendario real (desde→hasta) o solo
presets de N días con mejor UI; eligió lo primero.
Decisión, en capas:
- **Motor** (`cells.py`): `compute_retail_observations`/`compute_retail_
  ranges` suman `hasta: date | None = None` — `None` (el default) es
  exactamente el comportamiento de siempre, así que ningún caller existente
  (`/control`, `compute_availability`) se entera del cambio.
- **Capa de URLs** (`product_nav.py`): nuevo `resolve_window(desde, hasta,
  dias, hoy=None)` — `desde`/`hasta` explícitos y válidos (ISO, desde ≤
  hasta) ganan; si no, cae a `?dias=` de siempre (compatibilidad con
  enlaces viejos). Todo constructor de URL (`_query`, `products_query`,
  `light_product_query`, `download_href`, `build_product_chips`,
  `build_province_nav`, `build_view_menu`, `build_layer_nav`,
  `build_window_nav`) pasa de `dias: int` a `desde: date, hasta: date`
  explícitos y obligatorios —sin default—: un caller que no los pase
  falla en el momento (`TypeError`/mypy), no en silencio.
- **`desde`/`hasta` viajan SIEMPRE explícitos en la URL**, a diferencia de
  `dias` (que se omitía en el default). Es la respuesta directa al pedido
  de "que siempre se pueda ver el rango en uso": una URL que dice
  exactamente qué ventana mira es más clara para compartir, no menos —y
  evita que un link compartido muestre datos distintos según cuándo lo
  abra el destinatario (antes, `?dias=30` sin fecha ancla recalculaba
  "los últimos 30 días" contra el reloj de quien lo abre).
- **Router** (`routers/public.py`): resuelve el rango efectivo una vez y
  lo pasa como `hoy=hasta`/`window_days=(hasta-desde).days` a las 5
  funciones de `price_charts.py` que binean por período —sin tocar sus
  firmas, que ya aceptaban `hoy`/`window_days`—. Aplica tanto a la home
  con mapa como a `/producto/{slug}`.
- **UI**: dos `<input type="date">` siempre visibles —junto al riel de
  chips y el selector de provincia en la home, junto a la nota de frescura
  en la vista liviana—, precargados con el rango en uso (eso ES el "que
  siempre se pueda ver"). `<form>` real sin `onchange` de auto-submit —dos
  campos, no uno— con botón "Ver" explícito; degrada sin JS. Los atajos de
  7/30/90 días (`window_nav`) se preservan tal cual junto al picker, ahora
  expresados como el rango de calendario que representan.
Descartado: omitir `desde`/`hasta` de la URL cuando coinciden con el
default (como hacía `dias`). Habría dejado el rango implícito otra vez,
justo lo que el pedido quería evitar.
Descartado: auto-submit al cambiar cualquiera de las dos fechas. Con dos
campos, cambiar el primero dispararía un envío con el segundo todavía
viejo — dos navegaciones en vez de una, y una de ellas con datos a medio
completar.
Consecuencia: superficie de test grande (motor + capa de URLs + router +
plantillas, ~9 archivos) pero acotada y ya cubierta; toda la suite verde.
Un efecto colateral cosmético del reporte de `lint_drift.py`: el nuevo
docstring de `compute_retail_observations` corrió unas líneas más abajo un
`no-redef` de mypy ya aceptado (`as_total`/`as_unit`, sin relación con este
cambio) — aparece como "nuevo" + "resuelto" en la misma corrida porque el
piso es por número de línea, no cambió la deuda real (142 antes, 142
ahora). Queda para que el operador corra `--update-baseline` si lo
confirma, no se tocó solo.

## 2026-09-14 — Isla de filtros en horizontal, con overflow de chips
Contexto: el operador marcó mal ubicados en escritorio los tres grupos que
viven en `.ph-compass-filters` (chips de producto, selector de provincia,
y el date picker recién agregado) — quedaban apilados uno debajo del otro
por una decisión del 2026-09-13 ("sin `display: flex`... caen en su
posición estática, uno debajo del otro"), que tenía sentido con dos grupos
pero no con tres, y menos con sitio de sobra al costado en escritorio.
Pidió explícitamente diseño horizontal, usando el skill `frontend-design`,
con un mecanismo de overflow para los chips: "los productos son 5 hoy, el
día que haya más se pone un ícono para abrir el modal con los otros
elementos".
Decisión:
- `.ph-compass-filters` pasa a `display: flex; flex-wrap: wrap; align-
  items: center` en la regla base (antes solo la versión angosta de
  `@media` lo tenía) — los tres grupos caen en una sola fila cuando hay
  sitio, envolviendo solo si hace falta.
- El riel de chips se autolimita a `VISIBLE_CHIP_COUNT = 5` (hoy son
  exactamente 5 productos piloto: el mecanismo no se nota todavía, y
  empieza a actuar el día que se sume un sexto). El resto se pliega detrás
  de un disparador `<details>` con ícono `more_horiz` y contador "+N" —
  mismo patrón sin JS que ya usa el cluster de la insignia del mapa—, que
  abre un panel flotante (mismo `--ph-elev`/superficie que el resto de la
  capa pública) con los chips restantes.
- Reemplaza la contención reactiva de hoy mismo (`.ph-chip-rail` con
  `max-height`/scroll, agregada horas antes en el reporte de mejoras no
  consideradas): con el tope deliberado de 5 + overflow, un catálogo de
  200 productos pesa en la barra lo mismo que uno de 5 — no hace falta
  además un tope de scroll defensivo.
- `.ph-compass-filters` deja el `max-width` reactivo de antes (pensado
  solo para contener el riel) por uno puramente defensivo contra el borde
  de la ventana (`calc(100vw - 2rem)`), ya que los tres grupos ahora se
  autolimitan por su cuenta.
Descartado: un modal real (overlay a pantalla completa con fondo oscuro).
El pedido usaba la palabra "modal" mencionando su función ("abrir con los
otros elementos"), no la pidió como pieza de UI específica; un panel
flotante anclado (mismo patrón `<details>` que el cluster del mapa) cumple
la misma función sin JS nuevo ni la fricción de bloquear el resto de la
página para ver dos chips más.
Consecuencia: sin poder verificar visualmente en un navegador real esta
sesión (Postgres inaccesible) — igual que el resto del trabajo visual de
hoy, queda para que el operador confirme cómo se ve. Suite completa: 1692
casos, sin deuda de lint nueva.

## 2026-09-14 — Compás y filtros con margen; rango de fechas compactado a ícono
Contexto: segunda vuelta sobre el rediseño horizontal de horas antes. El
operador pidió: (1) un margen garantizado entre el avatar del compás y la
isla de filtros, y (2) compactar el rango de fechas al mismo patrón que
los chips — los atajos 7/30/90 quedan visibles, y un ícono más abre el
panel con los campos `desde`/`hasta` para un rango a medida.
Decisión:
- `.ph-compass-filters` reserva `38rem` a la izquierda (`max-width: min(46rem,
  calc(100vw - 38rem))`) — cubre el máximo real de `.ph-compass-title-group`
  (`left: 7.25rem` + hasta `28rem` de ancho cuando el compás está activo)
  con aire de sobra, así las dos islas nunca llegan a tocarse en una
  ventana angosta de escritorio.
- El rango de fechas pasa de dos `<input type="date">` sueltos en la barra
  a un ícono `calendar_month` (mismo patrón `<details>` que el overflow de
  chips) que abre el `<form>` de siempre. Se enciende (`is-selected`) solo
  cuando el rango activo no coincide con ninguno de los tres atajos —
  calculado una vez (`es_personalizado`) y reusado.
- El panel flotante del overflow de chips y el del rango personalizado
  comparten CSS (`.ph-filter-popover`, antes duplicado en dos clases):
  misma tarjeta anclada debajo del disparador que abre.
Bug encontrado al camino, no buscado: el regex que escanea qué íconos usan
las plantillas (`test_static_assets.py` Y `tools/build_icon_font.py`,
copias idénticas) exigía que `>` viniera pegado a la comilla de `class`,
así que cualquier ícono con OTRO atributo en el medio (`aria-hidden`,
`style`) quedaba fuera del escaneo sin que el test lo notara. Ya afectaba
en producción a `description` en `/curation` y `/curation/editar` —
dibujaba el nombre crudo en vez del ícono, desde antes de esta sesión.
Corregido en los dos lugares (`[^>]*` antes del `>` final) y la fuente
regenerada con los 49 íconos reales (antes 46, silenciosamente 3 cortos).
Consecuencia: sin poder verificar visualmente en navegador esta sesión
(Postgres inaccesible) — el operador confirma el margen y el ícono nuevo a
mano. Suite completa: 1692 casos, sin deuda de lint nueva.

## 2026-09-16 — /docs sin breakpoint de 900px: sidebar fluido en vez de grid+media query
Contexto: el operador pidió converger el sistema de breakpoints hacia algo
que no dependa de números "de fantasía" — la duda concreta era por qué
`design_tokens.css` no usa las window size classes de Material (600/840)
en vez de 720/900. Investigando, el propio comentario ya lo explicaba: no
es una cuestión de alinearse a un estándar, es que esos dos cortes todavía
sostienen layouts no fluidos (medido el 2026-09-04, renumerar rompe el bug
de teléfono en horizontal). El camino real es repetir lo que ya sacó el
corte de 640px de la home pública: hacer el layout fluido y que el
breakpoint deje de hacer falta.
Decisión: `.doc-layout` (`/docs`, TOC + documento) pasa de
`grid-template-columns: 240px 1fr` + `@media (max-width: 900px)` a flexbox
con el patrón "Sidebar" de Every Layout: `.doc-toc { flex: 1 1 240px }`,
`.doc-body { flex: 999 1 0; min-width: 50% }`. Colapsa a una columna cuando
el contenedor no alcanza para el TOC + el 50% del cuerpo, sin ningún número
de breakpoint involucrado. `CORTES` en `test_static_assets.py` sigue en
`{720, 900}` — 900 sigue vivo porque `.m3-curation` (`/curation`) todavía lo
usa, con una complicación extra: el panel de evidencia pasa de
`position: sticky` a `static` en el mismo corte, así que eliminarlo ahí no
es un simple cambio de `grid` a `flex` y queda para un slice aparte.
Descartado: `@container` en vez de `@media` — mismo problema (un número
literal en px, solo que escala al contenedor en vez del viewport) y además
se escapa por completo del test `TestEscalaResponsive`, que solo regexea
`@media`. No resuelve nada, esconde el número.
Consecuencia: `design_tokens.css` §Breakpoints y el docstring de
`TestEscalaResponsive` (que decía "tres cortes" desde antes de esta sesión,
stale desde que se sacó el de 640px el 2026-09-04) quedan actualizados.
Suite completa: 1691 casos, sin deuda nueva.

## 2026-09-16 — /curation sin breakpoint de 900px: flex con wrap + container query derivada
Contexto: después de sacar el 900px de `/docs`, quedaba `/curation`
(evidencia + formulario). No alcanzaba con pasar de grid a flex: en el mismo
corte la evidencia pasa de `sticky` (con alto acotado y scroll propio) a
estática, y ese estado tiene que seguir a cuándo se apilan las columnas —
apilada y sticky, la evidencia se pega arriba y tapa el formulario. CSS no
tiene un selector de «este ítem flex bajó de línea».
Decisión: tres piezas, cada una atada a su causa real.
(1) `.m3-curation` pasa a flex con wrap; evidencia `flex: 5 1 0` con
`min-width: min(300px, 100%)`, formulario `flex: 7 1 0` con
`min(360px, 100%)`. Reproduce el 5:7 del grid (`flex-basis: 0`), y el
`min(…, 100%)` evita que el formulario desborde un teléfono más angosto que
su mínimo. El layout decide solo cuándo apilar.
(2) La evidencia sticky vive dentro de `@container curation (width >= 680px)`
sobre el propio `.m3-curation`. 680 = 300 + 20 (gap) + 360: es exactamente la
condición con que flexbox arma las líneas, medida sobre el mismo ancho de
contenido, así que no es un corte sino un espejo del layout.
`TestCuraduriaFluida` lo recalcula desde las reglas y el token del gap, y
exige un test así para cualquier container query nueva. Tres mutantes
verificados en rojo: mínimo cambiado sin tocar la query, sticky fuera de la
query, query borrada.
(3) Dentro de `@media (max-width: 720px)` la evidencia vuelve a estática:
su `top: 236px` asume la topbar sticky, y ahí la topbar deja de serlo. Es la
dependencia real de ese número, no de la cantidad de columnas.
`CORTES` queda en `{720}`.
Descartado: (a) JS con ResizeObserver comparando el `offsetTop` de los dos
hijos — sin ningún número, pero suma un script a dos plantillas para algo que
CSS resuelve, y sin JS la evidencia queda en el estado que se elija por
defecto. (b) `@container` con un umbral elegido a mano, que es el mismo
breakpoint escondido donde `TestEscalaResponsive` no mira. (c) El
`auto-fit` de la home pública: reparte en columnas iguales y perdería el 5:7.
Verificado con Chromium headless sobre una página de prueba con la
estructura de `_shell.html` y la hoja vieja contra la nueva, de 400 a 1280px
de ancho y con alto de 800 y 375 (sin la app ni la base).
Consecuencia: el cambio de columnas ya no está en 900px de viewport sino en
680px de contenido (~745px de viewport con el padding de escritorio). Entre
745 y 900px `/curation` muestra ahora dos columnas con la evidencia sticky
donde antes apilaba; entre 712 y 720px, dos columnas con la evidencia
estática. Un teléfono acostado de más de ~745px de ancho (844×390) pasa a
ver la evidencia en una caja sticky de ~120px de alto al lado del formulario:
es el mismo bug de alto del cromo fijo (2026-09-02) que el corte de 720 no
cubre por encima de ese ancho, y lo ataca el slice siguiente, no este. La
topnav desborda en horizontal en la página de prueba (a 860 y a 400px),
igual con la hoja vieja; en parte es porque ahí no carga la fuente de
íconos, y lo que sea real también es del slice siguiente. Suite completa:
1694 casos (+3), ruff en el piso.

## 2026-09-16 — Re-evaluado /docs (markdown en el cliente, sin dependencia Python): sigue igual
Contexto: el operador pidió reevaluar la decisión del 2026-08-30 —renderizar
markdown con `marked` en el navegador en vez de `pip install markdown` en el
servidor— para ver si el estado del proyecto la había vuelto obsoleta.
Comparado punto por punto: (1) dependencias Python, 11 en `pyproject.toml`
tanto el 2026-08-30 como hoy, ni una nueva — el argumento de no sumar una no
se debilitó. (2) Bytes por la red: medido con `markdown` (Python) sobre
`decisions.md`, `pitch-v3.5.md` y `spec.md` — el HTML comprimido con gzip pesa
prácticamente lo mismo que el markdown crudo comprimido (+6,6 KB en el peor
caso sobre 231 KB); server-side no ahorra transferencia, nunca fue un
argumento real. (3) La regla de degradación sin JS del 2026-08-22 sigue sin
cumplirse ahí, igual que en agosto. Lo único que cambió: `decisions.md` casi
se duplicó (5.326→10.217 líneas, 315→638 KB) en dos semanas y media, sin poda
posible por ser de anexado. Medido en Chromium real con `Emulation.setCPUThrottlingRate`:
parsear y renderizar `decisions.md` con `marked` tarda 289ms en CPU nativa y
1,49s simulando un celular gama media (6×) — nada catastrófico, pero ya en la
zona donde un dispositivo débil lo nota, sobre el documento que "más se
consulta hacia atrás" según su propio catálogo.
Decisión: no se migra. El costo medido no justifica el cambio todavía, y los
dos argumentos originales (dependencia, bytes) siguen intactos o nunca
aplicaron. Queda una razón a favor de migrar que la decisión original no
consideró —anclas por sección estables y citables, hoy reconstruidas por JS
en cada carga— pero no alcanza sola.
Descartado: migrar ahora a render server-side. Medir el tamaño de archivo en
que el parseo se vuelve un problema real, antes de decidir: el operador
prefirió cerrar con la comparación ya hecha.
Consecuencia: sin cambios de código. Vuelve a levantarse si `decisions.md`
seguís creciendo al ritmo actual (duplicaría otra vez en ~semanas) o si el
tiempo de parseo se vuelve perceptible en uso real, no simulado.

## 2026-09-16 — openspec/config.yaml actualizado: arrastraba el estado del 2026-08-12
Contexto: el operador pidió revisar `openspec/config.yaml` por contexto viejo.
Confirmado por fecha de último commit (5a67f94, 2026-08-12): quedó atrás de
cuatro cambios reales, todos posteriores.
Decisión: cuatro correcciones.
1. `constraints.closed_decisions` no mencionaba los dos apartamientos de WAHA
   (2026-08-15, CLAUDE.md decisiones 2 y 4): ingesta de WhatsApp automatizada
   y WAHA en Docker. Se agregaron como excepción a cada decisión, no como
   decisión nueva.
2. `quality.linter`/`type_checker` decían `none`; ruff y mypy entraron el
   2026-09-13 (`24b629c`) con `scripts/lint_drift.py` como ratchet contra
   `docs/lint-baseline.json`. Corregido a los comandos reales.
3. `constraints.process` no incluía `docs/checklist-pr.md` (agregado
   2026-09-13) entre los requisitos para cerrar un slice, y `CLAUDE.md` § Alcance
   ya lo exige como cuarto punto.
4. `references` no listaba `docs/checklist-pr.md` ni `docs/lint-baseline.json`.
Verificado que NO estaba desactualizado: `testing.command: ".venv/bin/pytest"`.
El comentario de `pyproject.toml` sobre que ese comando "revienta" databa de
antes de `886f33c` (2026-09-10), que agregó `pythonpath = ["."]` para que
funcionen los dos. Confirmado corriendo `.venv/bin/pytest` directo: pasa.
Casi lo "corrijo" por el comentario sin probarlo primero.
Consecuencia: sin cambios de código, solo `openspec/config.yaml`. YAML
validado con `yaml.safe_load`. Suite completa: 1694 casos, ruff en el piso;
los 4 hallazgos nuevos de mypy (`cells.py`, `rapidocr`) son preexistentes y
ajenos a este archivo.

## 2026-09-16 — IntakeHealth/ChannelIntake: se descarta herencia, se extrae una función
Contexto: el operador preguntó por qué `IntakeHealth` y `ChannelIntake`
(`src/saimc/progress.py`) no usan herencia, señalando que comparten
`pending`/`curated`/`discarded`, `decided` y `above_objective` letra por
letra.
Decisión: no aplicar herencia. `discard_rate` significa cosas distintas en
cada clase —`IntakeHealth` neta `discarded_off_focus` del numerador y
denominador, `ChannelIntake` no, porque `compute_channel_intake` nunca
desglosa el descarte por canal según motivo—, así que no es la misma
métrica con dos nombres: es una relación "tiene-un-umbral-en-común", no
"es-un". Forzar una clase base habría heredado un campo sin sentido
(`discarded_off_focus` por canal) o pisado el método, violando sustitución.
Contado además: ninguna de las ~30 dataclasses del dominio hereda de otra
—la única herencia del repo es la que exige el framework (`Base` de
SQLAlchemy, `Settings` de Pydantic)—, así que meter una acá habría sido la
excepción, no la continuación de un patrón.
Se extrajo `_above_objective(rate)`, función libre, único fragmento
idéntico de verdad entre las dos clases; `pending`/`curated`/`discarded`/
`decided` quedan repetidos a propósito (`CLAUDE.md`: tres líneas similares
es mejor que una abstracción prematura).
Descartado: Template Method con clase base abstracta — correcto en teoría
(el patrón existe justo para "mismo algoritmo, paso final distinto"), pero
sobreingeniería para 2 clases y 3 miembros duplicados, y la única jerarquía
de todo el dominio.
Consecuencia: sin cambio de comportamiento. Suite completa: 1694 casos,
ruff en el piso. Si aparece una tercera clase con esta misma forma, ahí sí
vale la pena revisar de nuevo.

## 2026-09-16 — Modo oscuro en todo el sistema: toggle persistente, sin nueva dependencia
Contexto: pedido explícito del operador, "todo el sistema" — las tres
superficies (shell del operador, shell público, home pública con su propio
compás flotante).
Decisión: el sistema de tokens ya obligaba a que todo color viviera en
`design_tokens.css` y se citara vía `var(--m3-x)`/`var(--ph-x)` (test
`TestTokensMaterial` desde 2026-09-04), incluidos los SVG inline
(`stroke: var(--prov)` en `public_home.html`) — así que el modo oscuro no
tocó una plantilla por su color: agregó un segundo `:root[data-theme="dark"]`
en `design_tokens.css` con el mismo nombre de cada token de color y un valor
distinto, y `tools/build_css_tokens.py` (antes un solo bloque, ahora
`_ROOT_RE` matchea los dos) lo proyecta a las dos hojas igual que el claro.
`data-theme` en `<html>` decide qué bloque gana. Nada fuera de color se
tocó: tipografía, espaciado, radios y las sombras `rgba(0,0,0,…)` son las
mismas en los dos temas — una sombra ya se nota menos sobre fondo oscuro
por sí sola.
El toggle: un script compartido por `{% include %}` (server-side, no suma
un pedido del navegador) en `_theme_toggle_head.html` decide el tema ANTES
de la hoja —`localStorage`, si no hay nada guardado sigue a
`prefers-color-scheme`— para no parpadear, y `_theme_toggle_script.html`
cablea el clic de cualquier `.theme-toggle` por clase, no por id.
`public_home.html` esconde `.m3-public-top` entero (su titular vive en la
barra del lienzo), así que necesita su propio botón — lo metió como un
`.ph-menu-item` más en el dock del compás, reusando exactamente el patrón
ya establecido por `.ph-legend-toggle` en vez de inventar una posición
flotante nueva sobre un layout que ya tuvo varias rondas de ajuste fino de
z-index/márgenes esta semana.
El ícono NO lo reescribe JS: son dos `.m3-icon` estáticos por botón
(`dark_mode`/`light_mode`) y el CSS alterna cuál se ve. `textContent` en
runtime habría dejado `light_mode` fuera del manifiesto del subsetter de
íconos (`tools/build_icon_font.py`, que solo lee nombres literales en las
plantillas) y el navegador habría dibujado el nombre crudo al pasar a
oscuro.
Paleta oscura: valores M3 estándar derivados de la teal/azul existentes —el
primario se invierte de tono oscuro a tono claro (`#00696e` → `#6cd4d9`),
igual que en cualquier esquema Material oscuro—, no una paleta nueva.
Contraste verificado con la fórmula WCAG (luminancia relativa): el texto
más bajo de catorce pares da 7,13:1 (mínimo exigido 4,5:1); el borde
(`--m3-outline`) da 5,6:1. `TestModoOscuro.test_ningun_token_de_color_se_queda_sin_version_oscura`
impide que un token de color quede sin par oscuro salvo que esté en una
lista cerrada y explicada (roles «fixed» de Material 3, que no cambian
entre temas, y las tres sombras).
Descartado: (a) `@media (prefers-color-scheme)` como mecanismo — perdería
contra el toggle explícito, el operador o el visitante tienen que poder
pisar al sistema. (b) Reescribir el ícono con JS — rompe el subsetter, ver
arriba. (c) Reusar `.m3-icon-btn` (operador) también en la capa pública —
`saimc-public.css` no la tenía (el split de 2026-08-30 la dejó afuera junto
con topnav/curaduría/botones) y sumarla ahí habría sido cruzar el split por
comodidad; se escribió `.ph-theme-toggle` propia, misma idea que
`--ph-accent` frente a `--m3-primary`.
Verificado con Chromium headless (mismo mecanismo que las sesiones
anteriores de esta semana) contra una página de prueba con la estructura
real de los dos shells y las hojas generadas: clic real, cambio de ícono,
contraste visual correcto en las dos identidades (teal del operador, azul
serif de lo público) sin mezclarlas. `public_home.html` no se pudo probar
en navegador esta sesión (sin credenciales de Postgres); se apoya en la
suite (1704 casos) y en que reusa un patrón (`.ph-menu-item`) ya probado en
producción.
`test_el_colapso_no_trae_javascript` (`/control`, 2026-08-XX) asumía CERO
`<script>` en la página como forma de probar que el colapso de
`<details>` no necesita JS. Dejó de ser cierto —el shell del operador
ahora siempre trae el suyo, compartido y ajeno al colapso— así que la
prueba se reescribió para exigir que CUALQUIER script presente sea el del
tema, no que no haya ninguno: sigue probando lo mismo, con la premisa
correcta.
Consecuencia: suite completa, 1704 casos (+10), ruff en el piso. La fuente
de íconos subsetted crece de 49 a 51 nombres (`dark_mode` y `light_mode`,
ninguno de los dos usado antes de este slice). `docs/lint-baseline.json` sin
cambios.

## 2026-09-16 — Fix: los polígonos del mapa no seguían al toggle de tema
Contexto: el operador probó el modo oscuro y preguntó si era normal que "el
mapa como tal" no cambiara. Investigando: `estiloDeProvincia()` en
`public_home.html` pinta cada polígono con `tokenCSS()`
(`getComputedStyle(...).getPropertyValue(...)`), que lee el color UNA vez y
se lo pasa a Leaflet como hex literal — a diferencia del resto del sistema,
que cita `var(--m3-x)` directo en CSS y el navegador re-evalúa solo. Nada
disparaba un repintado al tocar el toggle, así que los polígonos quedaban
con el color de cuando cargó la página.
Decisión: `_theme_toggle_script.html` dispara `guani:tema-cambiado`
(evento de `document`, no llamada directa: el script compartido no sabe si
`inicializarMapa` existe en la página) después de aplicar el tema nuevo, y
`public_home.html` lo escucha igual que ya escucha `htmx:afterSettle` para
repintar el mapa en cada swap. Mismo camino, no uno nuevo: recrea todo el
mapa, y la geometría sigue cacheada (`window.__cubaGeoJSON`) así que no
sale a la red de nuevo.
Consecuencia: cubre las cinco citas de `tokenCSS()` del archivo (son las
únicas en toda la capa de plantillas — `grep -rl getComputedStyle
templates/` no encuentra otra). Suite completa: 1704 casos, sin deuda
nueva.

## 2026-09-16 — Tiles del mapa: se quedan claros en modo oscuro, a propósito
Contexto: al cerrar el modo oscuro, quedó la pregunta de si los tiles base
de OpenStreetMap (calles, tierra, agua) debían oscurecerse también —son una
imagen, no CSS, así que necesitarían un proveedor de tiles distinto para
modo oscuro (ej. CartoDB dark_matter).
Decisión: se quedan claros siempre. El operador eligió no sumar el segundo
proveedor.
Descartado: un tile provider oscuro — habría sido un segundo apartamiento
de la decisión cerrada 1 (además del ya vigente para
`tile.openstreetmap.org`), sin necesidad clara que lo justifique.
Consecuencia: ninguna en código. Los polígonos de provincia, chips e
íconos sobre el mapa sí siguen al tema (ver la entrada anterior); la base
cartográfica no, por diseño.

## 2026-09-16 — Slice 1 del modo Tablero: `?vista=mapa|tablero`, tarjetas agrupadas por provincia
Contexto: el operador pidió un modo de presentación alternativo al mapa en
`/`, con gráficos y tablas. Se investigó y se propusieron tres direcciones
de diseño con el skill `design` (Claude Design) más una cuarta mezclando
piezas a pedido del operador; eligió **D — Tarjetas enriquecidas**. Este
slice es la base sin enriquecer: el toggle funcionando de punta a punta con
las tarjetas de siempre. La barra de rango y el sparkline de D quedan para
el slice 2.
Decisión: `?vista=mapa|tablero` (`product_nav.py`: `VISTA_CHOICES`,
`DEFAULT_VISTA`, `resolve_vista`), mismo criterio que `?capa=` — se omite de
la URL cuando es el default y se preserva en los enlaces que ya preservaban
`capa` (`_query`, `products_query`, `build_province_nav`,
`build_product_chips`, `build_window_nav`). NO se preservó en
`build_view_menu`/`build_layer_nav`/`build_product_panels`: son conceptos
exclusivos del lienzo del mapa (ventanas flotantes, capa del mapa, paneles),
que el tablero no usa ni usará — preservarlo ahí no protege nada.
`routers/public.py` elige la plantilla (`public_dashboard.html` vs
`public_home.html`) en vez de un `{% if %}` adentro de las ~2700 líneas ya
delicadas de `public_home.html`. Las tarjetas se agrupan con
`card_view.group_cards_by_province` (nueva), mismas `ProductCard` de
siempre — cero cómputo nuevo, el router ya las tenía.
La tarjeta (`<li class="ph-surface ph-card">…`) se extrajo de la ventana
flotante «Precios» a `_product_card_item.html`, compartida por las dos
plantillas: reimplementarla a mano en el tablero arriesgaba que las dos
copias se desincronizaran (mismo tipo de bug que `--m3-tertiary` citado sin
definir, 2026-09-04).
Bug real encontrado por la suite, no por revisión: el CSS de `.ph-card`,
`.ph-surface`, `.ph-note`, `.ph-chip`, `.ph-fpanel-icon`/
`.ph-fpanel-illustration` y `.ph-cifra-cambio` vivía ENTERO en el `<style>`
propio de `public_home.html`, con el comentario explícito «mientras sea la
única vista pública» — dejó de serlo con este slice, y las tarjetas del
tablero salían sin ningún estilo (bullets de lista nativos, íconos SVG sin
`width`/`height` ocupando cientos de píxeles). Se movieron a
`saimc-public.css`, la hoja compartida; lo que sigue siendo exclusivo del
mapa (`.ph-map`, `.ph-panel`, `.ph-legend`, `.ph-conc-*`, `.ph-series`/
`.ph-plot-*`, `.ph-filter*`) se quedó donde estaba.
Verificado renderizando la ruta real (FastAPI `TestClient` + SQLite en
memoria, mismo motor que la suite) a un archivo y mirándolo en Chromium
headless contra la hoja real, con el mapa y el tablero — no un mockup —, a
1200px y 420px. El mapa se re-verificó también, para confirmar que sacar
CSS de `public_home.html` no le rompió nada.
Descartado: duplicar el CSS de la tarjeta en el `<style>` del tablero en vez
de mover el original — es exactamente el patrón que ya causó
desincronización dos veces en este proyecto (tokens `--m3-*` antes del
2026-09-04, ver `TestTokensMaterial`).
Consecuencia: suite completa, 1716 casos (+12), ruff en el piso. La fuente
de íconos subsetted suma `dashboard` (52 nombres). Dos tests de
`TestVarianteEnTarjetaPublica` que buscaban la regla CSS inline en el HTML
de `/` se reescribieron para buscarla en `saimc-public.css`, donde vive
ahora.

## 2026-09-16 — Slice 2 del modo Tablero: tarjeta enriquecida (barra + sparkline)
Contexto: segundo slice de la Dirección D elegida sobre las propuestas de
diseño. Faltaban las dos piezas prestadas de las direcciones B y C que la
distinguen de A pura: una barra de rango por tarjeta y un sparkline de
tendencia.
Decisión: `ProductCard.median_pct` (nueva propiedad, `card_view.py`) — dónde
cae la mediana dentro del propio [mínimo, máximo] en escala logarítmica,
mismo criterio que `matrix.build_range_bar` pero autocontenido (la barra
siempre ocupa el 100% de la tarjeta; no hay fila con la que comparar el
ancho). Usa el rango de FORMATO cuando hay uno declarado, para que la barra
describa el mismo número que ya muestra el texto de al lado. `None` con una
sola observación — la plantilla ya trata ese caso aparte.
El sparkline reusa la `PriceSeries` que ya arma `series_view.build_series`
para «Cómo se movió» (`routers/public.py` arma `series_by_card`, un índice
por celda, cero cómputo nuevo) y la dibuja como puntos sueltos
(`<circle>`), **nunca una línea que los una**: es la decisión de fondo de
`series_view.py` (§12.6) — Aceite/La Habana puede tener 3 observaciones en
30 días, y una línea continua afirmaría veintisiete días de precio que
nadie observó. El `viewBox` del SVG grande (600×170) se reescala solo al
tamaño chico de la tarjeta; no hay geometría nueva que calcular, solo un
`width`/`height` de CSS distintos.
Las dos piezas son opt-in vía `tablero_extra` en `_product_card_item.html`
—lo pone `public_dashboard.html` antes del `for`, la ventana flotante
«Precios» del mapa nunca lo define—, así que siguen siendo una sola
plantilla de tarjeta, no dos copias.
Sin color por dirección en ninguna de las dos piezas: §12.6 ya decidió
(`card_view.py`, docstring del módulo) que el cambio se muestra como
número, nunca como semáforo. El primer borrador de este slice —hecho con
las propuestas de diseño iniciales, antes de leer `series_view.py`— tenía
justo eso: una línea continua entre puntos y color ámbar/verde según
suba o baje. Se corrigieron los dos antes de escribir una sola línea de
código real, al confirmar que contradecían decisiones ya tomadas.
Verificado igual que el slice 1: `TestClient` + SQLite en memoria
renderizado a archivo, mirado en Chromium headless contra la hoja real.
Consecuencia: suite completa, 1723 casos (+7), ruff y mypy en el piso.

## 2026-09-16 — Slice 3 del modo Tablero: recordar la vista con cookie, no localStorage
Contexto: cierre de la Dirección D (tablero de tarjetas) elegida sobre las
propuestas de diseño. Faltaba recordar la última vista elegida entre
visitas nuevas a `/`.
Decisión: cookie `vista` (slice 3), no `localStorage` como el toggle de
tema. Se le preguntó al operador porque el problema es real y de fondo:
`localStorage` no lo puede leer el servidor, así que una visita nueva a `/`
sin `?vista=` en la URL renderizaría igual el mapa completo —Leaflet,
tiles— y recién después un redirect en el cliente mandaría al tablero. Dos
cargas, la más pesada primero, exactamente en contra de por qué existe el
tablero (aliviar la conexión lenta). El operador eligió cookie: el toggle
la escribe al click (`_vista_cookie_script.html`, cableado por
`.js-vista-link`/`data-vista`, no por id — mismo criterio que
`_theme_toggle_script.html`) y `routers/public.py` la lee como fallback
cuando no hay `?vista=` explícito (`vista_efectiva = vista if vista is not
None else request.cookies.get("vista")`) — el servidor elige la plantilla
correcta de entrada, sin redirect.
`?vista=` explícito en la URL sigue ganando siempre a la cookie: un link
compartido con `?vista=mapa` tiene que abrir en mapa pase lo que pase. Una
cookie con basura cae al mapa igual que un `?vista=` con basura —mismo
`resolve_vista`, ninguna validación nueva.
Es la primera cookie del proyecto —hasta ahora todo era query string o
`localStorage`—, y queda documentada como tal en el docstring de
`public_home()` para que quede claro por qué se rompió el patrón.
Verificado en dos capas: `TestClient` + cookie seteada a mano para la
lógica del servidor (query string gana a la cookie, cookie inválida cae al
mapa), y Chromium headless disparando el evento de clic real sobre el
enlace del dock para confirmar que `document.cookie` queda en
`vista=tablero` después.
Consecuencia: suite completa, 1727 casos (+4), ruff y mypy en el piso.
Cierra la Dirección D — los tres slices completos.

## 2026-09-16 — Agrupación del tablero: índice de salto + secciones colapsables + color por provincia
Contexto: el tablero (Dirección D, ya cerrado) publica todas las tarjetas de
todas las provincias en una sola lista plana. El operador reportó que con
varias provincias «hay muchas y es fácil perderse», y pidió un mecanismo de
agrupación/navegación —colapsar, agrupar, usar colores— para la vista de
Tablero (no el mapa). Se investigó con el skill `design` (Claude Design):
tres direcciones (A: secciones colapsables + índice de salto; B: pivote por
producto; C: chips de filtro + borde de color) más una mezcla explícita A+C
pedida por el operador. Eligió esa mezcla («D: filtro + colapsables»).
Decisión: de esa Dirección D se implementó primero la mitad de agrupación —
`card_view.group_cards_by_province` (ya existía) ahora se acompaña de
`card_view.province_color_slots`: identidad de color por posición
alfabética en el catálogo completo de provincias (`_all_province_names`,
ordenado), mismo criterio que `matrix._province_slot`/`.m3-slot-N` en
`/control` pero acotado a 3 slots con nombre propio (`_NAMED_PROVINCE_SLOTS
= 3`) — el resto cae en el slot neutral `9`; sin paleta para dieciséis
provincias distintas. `public_dashboard.html` reemplaza `<section>` por
`<details class="ph-dash-section ph-prov-N">` con `<summary>` (punto +
nombre + conteo + chevron) — sin JS, mismo patrón ya usado en `/docs`—, la
primera (o la única, con `?provincia=` activo) abierta por defecto. Con más
de una provincia visible se agrega `<nav class="ph-dash-jump">`, un índice
con un enlace por provincia (punto de color + nombre + conteo) que ancla al
`id` de cada `<details>`. El borde de color de `.ph-card` dentro del
tablero hereda `--prov` del `<details>` contenedor —no repite la clase por
tarjeta.
El color por provincia se calcula sobre `all_province_names` (el catálogo
completo) y no sobre `cards_by_province` (el resultado ya filtrado): un
primer intento lo calculó sobre la lista filtrada y rompía la promesa del
propio `province_color_slots` (»identidad estable, no por los datos de la
carga actual«) — filtrar a una sola provincia con `?provincia=` la dejaba
sola en la lista y le asignaba el slot 0 por accidente de posición, no por
su lugar real en el catálogo. Corregido antes de cerrar el slice; test
`test_el_slot_de_color_no_cambia_al_filtrar_por_provincia` cubre el caso.
La primera implementación del token de color también citaba
`var(--m3-prov-{{ n }})` armado con interpolación de Jinja dentro de un
atributo `style` — rompía `TestTokensMaterial` (escanea el texto crudo del
archivo buscando tokens `--m3-*`/`--ph-*` citados y nunca definidos, y
`{{` corta el nombre del token antes del número). Corregido con el mismo
patrón que `.m3-slot-N`: clases CSS (`.ph-prov-0/1/2/9`) que fijan `--prov`
una sola vez, heredado por los hijos sin repetir la clase.
Descartado: agrandar la paleta a una identidad de color por cada una de las
16 provincias — inventaría una jerarquía visual que nadie decidió y que no
se sostiene con solo 3 nombres propios ya elegidos en `design_tokens.css`.
Descartado también forzar con JS que el índice de salto abra el `<details>`
de destino al navegar: el `id` vive en el propio `<details>`, no en un
descendiente, así que el auto-open nativo del navegador no aplica —el
salto lleva al `<summary>` (siempre visible aunque esté colapsado), un
clic más para expandir. Se mantiene la vía sin JS (agrega ~1727 líneas y
otro `<script>` que hoy no existe en esta vista) mientras nadie reporte que
el clic extra molesta.
Consecuencia: pendiente la otra mitad de la Dirección D (chips de filtro
por provincia/producto en el propio tablero — `product_chips`/
`province_nav` ya están en el contexto de la plantilla, sin usar todavía;
requiere migrar `.ph-filter`/`.ph-chip-toggle` a `saimc-public.css`, mismo
motivo que ya obligó a migrar `.ph-card*`/`.ph-surface` en el slice 1).
Suite completa: 1734 casos (+7 sobre los 1727 previos), ruff y mypy sin
deuda nueva propia (el drift de mypy en `cells.py`/`ocr.py` es de código no
tocado en este slice). Verificado con render real (`TestClient` + SQLite en
memoria, 5 provincias sembradas a mano) servido y capturado con Chromium
headless en 900px: índice de salto con los 5 puntos de color correctos,
primera sección alfabética abierta, resto colapsadas, y el color de La
Habana idéntico con y sin `?provincia=la-habana`.

## 2026-09-16 — Tablero: se revierte a la Dirección A pura, sin la barra ni el sparkline
Contexto: el tablero llevaba, desde el slice 2, la tarjeta «enriquecida»
(barra de mediana prestada de B + sparkline prestado de C) sobre la base de
la Dirección A. El operador pidió explícitamente volver a «la primera
opción sin mezclar» —Dirección A pura— para la tarjeta del tablero
(distinto de la Dirección D de agrupación, que sigue en pie).
Decisión: se quitó por completo la vía `tablero_extra` de
`_product_card_item.html` —vuelve a ser exactamente la misma tarjeta que ya
pinta la ventana flotante «Precios», sin ninguna bifurcación—, junto con
todo lo que solo existía para sostenerla: `ProductCard.median_pct`
(card_view.py), el cálculo de `series_by_card` en `_render_home`
(routers/public.py) y las clases `.ph-card-bar-track/-fill/-tick`,
`.ph-card-evo`, `.ph-card-spark` (saimc-public.css). `public_dashboard.html`
ya no fija `{% set tablero_extra = true %}`.
Descartado: dejar el código de la barra/sparkline sin usar por si se pide
de vuelta más adelante — muerto y sin tests, se pudre en silencio; está en
el historial de git (`b83132e`) si hace falta retomarlo.
Consecuencia: la Dirección D de agrupación (colapsables + índice de salto +
color por provincia) no se toca — son dos ejes independientes del mismo
tablero, uno de contenido de la tarjeta y otro de cómo se organizan.
Suite: 1727 casos (los 7 de la tarjeta enriquecida se quitan, ninguno
nuevo), ruff y mypy sin deuda nueva. Verificado con el mismo render real ya
usado para la agrupación: la tarjeta vuelve a mostrar solo rango, mediana y
muestra, sin barra ni sparkline.

## 2026-09-16 — Tablero: tarjetas planas, sin sombra ni borde de color
Contexto: el operador pidió que las tarjetas del Tablero fueran «más
minimalistas» en el diseño (no en el contenido) — «nada de elevations ni
border color» — como parte de un pedido más amplio (ver consecuencia).
Decisión: dentro de `.ph-dash-section` (el tablero, no la ventana flotante
«Precios» del mapa, que sigue con `.ph-surface` normal) las tarjetas pierden
`box-shadow` y el borde de color por provincia (`.ph-dash-section .ph-card`
ya no lleva `border-left: 3px solid var(--prov)`). La separación visual sin
sombra ni borde pasa a ser un relleno plano (`--m3-surface-container`,
el mismo gris ya usado en el toggle Mapa/Tablero) contra el blanco del
`<details>` contenedor. Primer intento fue un divisor `border-bottom` entre
tarjetas — descartado al verificar con render real: la grilla es
multi-columna (auto-fit), así que dos tarjetas en la misma fila quedan con
bordes inferiores disparejos según su posición, no según si son la última
de la lista.
Descartado: quitarle la sombra también al `<details>` contenedor de cada
provincia — el pedido fue sobre «las tarjetas», no sobre la sección que las
agrupa; esa sigue funcionando como unidad plegable con su propia superficie.
Consecuencia: primer slice de un pedido más grande («la vista tablero...
posibilidad de cambiar a tabla... filtros y búsquedas en todo momento sobre
tabla y tarjetas») — quedan pendientes el modo tabla y los filtros/búsqueda
visibles en el tablero (hoy `product_chips`/`province_nav` están en el
contexto de la plantilla sin usar). Suite: 1727 casos sin cambios, ruff y
mypy sin deuda nueva. Verificado con render real + Chromium: fila de varias
tarjetas y tarjeta única, ambas sin sombra ni borde de color.

## 2026-09-16 — Tablero: filtros de producto y provincia visibles como chips
Contexto: segundo slice del pedido «en todo momento es importante poder
aplicar filtros... sobre la tabla y sobre las cards». Antes del tablero
`product_chips`/`province_nav` ya se calculaban en `_render_home` pero
`public_dashboard.html` nunca los pintaba — la única forma de filtrar era
editar la URL a mano.
Decisión: chips planos (mismo criterio visual que las tarjetas del slice
anterior: relleno gris, sin sombra) para producto y provincia, arriba de
todo, ANTES del bloque que decide si hay tarjetas o el aviso de vacío —así
un filtro que deja cero resultados se puede deshacer desde la propia
vista. `province_nav` ya traía «Todas» como primera opción, sirve tal
cual. Para producto hizo falta un cambio real: `build_product_chips`
tenía el comportamiento «un chip prendido no se apaga solo» (decisión
2026-09-11, pensada para el riel flotante del mapa, que cierra su panel
con una × propia) — en el tablero no hay panel, así que un chip prendido
sin forma de apagarlo sería un filtro sin salida. Se agregó
`toggle: bool = False` a `build_product_chips` (default preserva el
comportamiento del mapa sin tocar sus tests) y `routers/public.py` pasa
`toggle=True` solo cuando `vista == "tablero"`.
Descartado: un `<select>` de provincia como en el mapa (`.ph-province-form`)
— ese patrón existe para convivir con el `<form>`/htmx del compás sobre el
canvas; el tablero es una página de contenido plano, chips de igual
jerarquía visual que los de producto son más simples y ya cubren el caso
(`province_nav` es corto, no necesita un desplegable).
Consecuencia: la búsqueda de texto libre y el modo tabla (mismo pedido)
siguen pendientes. Suite: 1732 casos (+5), ruff/mypy sin deuda nueva.
Verificado con render real + Chromium: chips visibles con y sin filtro
activo, y siguen visibles —con el filtro marcado— cuando el resultado
queda en cero tarjetas.

## 2026-09-16 — Tablero: búsqueda instantánea de texto libre
Contexto: tercer y último slice del pedido «en todo momento poder aplicar
filtros, búsquedas sobre la tabla y sobre las cards». El operador ya había
elegido búsqueda instantánea del lado del cliente (JS) frente a la
alternativa por formulario/recarga, cuando se le preguntó explícitamente.
Decisión: mismo patrón que ya usa `/docs` (`docs_index.html`,
`data-buscar` + `hidden` + grupo sin coincidencias se esconde): cada
`<li class="ph-card">` trae `data-buscar="producto provincia"` en
minúscula (`_product_card_item.html`, así que también sale con la
tarjeta de la ventana flotante «Precios», sin uso ahí). Una caja de texto
(`.ph-input`, nueva en `saimc-public.css` — ver más abajo) filtra sobre
eso. Con texto en la caja: una provincia sin ninguna coincidencia esconde
su `<details>` entero Y su entrada en el índice de salto; la que sí tiene
alguna, se fuerza a abierta (buscar y no ver el resultado por un
acordeón cerrado sería peor que no ofrecer la búsqueda). Al vaciar la
caja, cada sección vuelve exactamente al estado con el que llegó del
servidor (capturado una vez al cargar la página vía `dataset.eraAbierta`),
no a «todo abierto».
Dos bugs encontrados y corregidos ANTES de cerrar el slice, los dos por
verificación con render real + Chromium, ninguno por los tests (que no
ejecutan JS):
1. **`.ph-dash-jump a[hidden]` no escondía nada**: `.ph-dash-jump a {
   display: inline-flex; ... }`, una regla de esta misma plantilla, tiene
   más especificidad que el `[hidden] { display: none }` que trae el
   navegador por default, así que ganaba y el chip del índice de salto
   quedaba visible aunque JS le pusiera `hidden = true`. Se agregó
   `.ph-dash-jump a[hidden] { display: none; }` explícito.
2. **`.m3-input` no existe en la hoja pública**: vive solo en
   `saimc-material.css` (la del operador) y copiarla tal cual traía su
   anillo de foco en `--m3-primary` (teal), pisando la separación de
   acento ya decidida el 2026-09-04 (`--ph-accent`, azul, para toda esta
   hoja). Se creó `.ph-input` en `saimc-public.css` en vez de reusar o
   duplicar la del operador. De paso, `.visually-hidden` —que el `<label>`
   del buscador necesita y que `docs_index.html` ya usaba sin que
   existiera en ninguna hoja, un bug preexistente y ajeno a este slice,
   no tocado— se agregó a `saimc-public.css` (no a la del operador, fuera
   de alcance).
Descartado: mismo criterio que el resto del tablero — nada de mostrar el
texto buscado en un semáforo de color, y la búsqueda nunca toca lo que se
manda al servidor (§9.0/§12.6 no aplican acá, pero la disciplina de «un
solo criterio de verdad, sin JS decidiendo cifras» se mantiene igual).
Consecuencia: cierra el pedido de agrupación + minimalismo + filtros +
búsqueda del tablero. El modo de presentación tabla (mismo pedido
original, «también posibilidad de cambiar a tabla») queda para otra
sesión — no se abrió en este slice. Suite: 1735 casos (+3 sobre el slice
anterior), ruff y mypy sin deuda nueva. Verificado con render real +
Chromium: búsqueda que cae en una sola provincia, búsqueda sin
coincidencias, y vaciar la caja para confirmar que el estado vuelve
exacto al original.

## 2026-09-17 — Tablero: modo tabla, cierra el pedido de la vista alternativa
Contexto: cuarto y último slice del pedido «dentro de esta vista es necesario
tener también la posibilidad de cambiar de presentación a tabla». Al
retomarlo el operador señaló que ya existe `/producto/<slug>` (vista liviana
de un producto, 2026-09-14) — se investigó y es una cosa distinta: un
producto a la vez, agrupado por provincia en tarjetas, pensada para
compartir. El modo tabla es ortogonal —muchos productos y provincias a la
vez, una fila por celda— así que no la reemplaza, pero cada fila SÍ enlaza a
esa vista liviana para el detalle (`product_hrefs`, con `light_product_query`
que ya existía).
Decisión: `?presentacion=tarjetas|tabla` (nuevo eje, independiente de
`vista`; `PRESENTACION_CHOICES`/`DEFAULT_PRESENTACION`/`resolve_presentacion`
en `product_nav.py`, mismo criterio que `vista`) gobierna, DENTRO del
tablero, si `public_dashboard.html` pinta las secciones colapsables de
siempre o una `<table>` plana: provincia, producto (enlazado), precio,
mediana, muestra, cambio. Sin agrupar por `<details>` —una columna ya dice
la provincia— así que tampoco hay índice de salto en modo tabla, no tiene
sentido sin secciones que saltar. La búsqueda instantánea (slice anterior)
sigue funcionando SIN NINGÚN cambio de JS: filtra por `[data-buscar]`, que
no le importa si el elemento es un `<li>` o un `<tr>`. Toggle Tarjetas/Tabla
con el mismo componente visual que Mapa/Tablero (`.ph-vista-toggle`, pill
genérico) pero sin cookie propia —no se pidió recordar la presentación, y
agregar una segunda cookie sin pedido es alcance de más.
`presentacion` se hiló por `_query`/`products_query`/`build_province_nav`/
`build_product_chips` (mismo mecanismo ya usado para `vista` en el slice de
filtros): cambiar de producto o provincia estando en la tabla no devuelve a
tarjetas.
Descartado: agrupar la tabla por provincia con sub-encabezados — la columna
ya cumple esa función y una tabla sin agrupar es más fácil de escanear/
ordenar mentalmente que una con secciones intercaladas. Descartado también
que el modo tabla tenga su propia cookie de preferencia, por el mismo
motivo que el toggle Mapa/Tablero si tuviera dos ejes que recordar sería
alcance no pedido.
Consecuencia: cierra el pedido completo de la sesión (minimalismo + filtros
+ búsqueda + modo tabla) sobre la vista Tablero. Suite: 1744 casos (+9),
ruff y mypy sin deuda nueva. Verificado con render real + Chromium: la
tabla en 1000px y en 420px (el ancho de la tabla se contiene solo con
scroll horizontal PROPIO, sin empujar el body — confirmado con
`document.body.scrollWidth` == viewport), la búsqueda filtrando filas, el
toggle preservando filtro y provincia, y el enlace de cada fila apuntando a
`/producto/<slug>?provincia=...`.

## 2026-09-17 — Tarjeta del tablero: recorrido hacia atrás hasta el anuncio
Contexto: el operador reportó que las tarjetas del tablero no tenían el
enlace a los anuncios que arman esa cifra, cuando el panel flotante de
producto sobre el mapa sí lo tiene. Se investigó: el enlace ya existía como
funcionalidad (`.ph-cifra` + `.ph-evo-anuncios`, `panel_view.py` +
`price_charts.build_cell_ads`, pedido del 2026-09-14 comparando con «ver
anuncios» de `/control`), pero SOLO en el panel flotante de UN producto
enfocado —una pieza de UI distinta de la tarjeta simple (`.ph-card`) que
usan tanto el panel «Precios» del mapa como el tablero—; la tarjeta simple
nunca lo tuvo, en ninguna de las dos vistas.
Decisión: `_render_home` ya calculaba `panel_ads` (`build_cell_ads(obs)`)
para el panel de producto — se pasa también al contexto de plantilla como
`card_ads`, sin recalcular nada. `_product_card_item.html` (la tarjeta
compartida) busca sus anuncios con la misma clave de 5 que ya usa
`price_charts._Key` (producto, provincia, moneda, unidad, formato) y, si
hay alguno, agrega el mismo `<details class="ph-evo-anuncios">` con tabla
de día/precio/enlace que ya usaba `.ph-cifra`. Al ser una plantilla
compartida, el enlace aparece en las DOS vistas que la usan: el tablero
(lo que se pidió) y el panel «Precios» del mapa (que tampoco lo tenía, y
ahora queda consistente con el resto del sitio).
CSS migrada a `saimc-public.css` (`.ph-table-card`/`.ph-coverage`/
`.ph-evo-anuncios`): vivía en el `<style>` de `public_home.html` asumiendo
que ninguna otra plantilla pintaba `_product_card_item.html` con anuncios
—mismo tipo de migración que la de `.ph-card`/`.ph-surface` del primer
slice del tablero, mismo motivo (dejó de ser cierto).
Descartado: agregar el mismo enlace a las filas de la tabla (modo
`presentacion=tabla`) — el operador preguntó específicamente por «las
tarjetas»; queda para pedirlo aparte si hace falta ahí también.
Suite: 1746 casos (+2), ruff y mypy sin deuda nueva. Verificado con render
real + Chromium: el resumen "N anuncios detrás de esta cifra" en cada
tarjeta del tablero, y al abrirlo la tabla de día/precio/enlace con "ver
anuncio" funcionando.

## 2026-09-17 — El formato deja de ser una tarjeta: el tablero agrupa por producto
Contexto: el operador pidió rediseñar el tablero porque «deja mucho que
desear». Medido con `curl` a `/?vista=tablero`: **50 tarjetas, 50 `<table>`,
54 `<details>` y 362 filas «ver anuncio»** para 5 productos en 2 provincias.
El `<summary>` decía «32 productos» en La Habana cuando los productos son 5;
los 32 eran formatos. La causa es la decisión del 2026-09-04 («El rango
minorista público se separa por formato»), que metió `format_id` en la
identidad de la serie — correcta y con evidencia detrás (ninguna de las 39
filas de Pollo tenía `quantity = 1`), pero nadie había mirado qué pasaba
cuando esa identidad se pintaba como una tarjeta por cada valor.
Al diseñar el reemplazo la propuesta inicial era pasar el titular al precio
por libra. **Eso es exactamente lo que el operador ya había rechazado el
2026-09-04**, así que se descartó antes de escribir código: el problema no
era cuál es el titular, era cuántas tarjetas hay.
Decisión: `card_view.ProductGroup` + `group_products_by_province()` agrupan
las tarjetas por (producto, provincia, moneda, unidad) —la misma clave que ya
usa `attach_trends`— y el tablero pinta una tarjeta por grupo con los formatos
como filas adentro (`_product_group_item.html`). **Ninguna cifra cambia**:
cada formato conserva su titular, que sigue siendo el precio del envase con el
precio por unidad al lado como comparador. `ProductGroup.count` es la suma de
las muestras de sus formatos, que son conjuntos disjuntos — aritmética, no un
estadístico nuevo; no se recalcula ninguna mediana ni ningún rango.
El bloque de precio y la tabla de anuncios se extrajeron a
`_product_price_range.html` y `_product_card_ads.html`, incluidos tanto por la
tarjeta suelta (ventana «Precios» del mapa) como por la agrupada, para que las
dos no se desincronicen.
Descartado: (a) mover el titular al precio por unidad — revierte el
2026-09-04 y muestra una cifra que nadie paga; (b) tocar el modo
`presentacion=tabla`, donde una fila por formato no abrume: es lo que una
tabla hace, así que conviven las dos agrupaciones y cada presentación usa la
suya; (c) el flujo `openspec/` completo — no toca esquema ni motor de
validación, solo reagrupa para mostrar.
Consecuencia: el tablero pasa de 50 tarjetas a 9 con los mismos datos.
`cards_by_province` y `groups_by_province` conviven en el contexto de
`public_dashboard.html`. Queda pendiente el resto del rediseño apilado (tiras
de calor arriba, tabla abajo), que se apoya sobre esta agrupación.
Suite: 1759 casos (+13), ruff y mypy sin deuda nueva. Prueba de mutación
hecha: con la clave de agrupación rota caen 5 unitarios y 1 de integración.

## 2026-09-17 — Mobile first pasa a ser regla, y la primera medición del costo
Contexto: el operador enunció «mobile first» como regla del proyecto. No
estaba escrita en ningún lado —`CLAUDE.md`, `docs/` y `openspec/` no la
mencionan— así que hasta hoy no gobernaba nada. Se midió el sitio en vivo con
Chromium a 390x844 (iPhone 12/13/14) para saber qué costó su ausencia, en vez
de suponerlo.
Lo que salió BIEN, y conviene registrar para no «arreglarlo»: **cero desborde
horizontal** en las cuatro páginas medidas (`/`, tablero, tabla y
`/producto/<slug>`), y el mapa ocupa exactamente una pantalla de alto, sin
cromo fijo que lo tape, tanto vertical como acostado. El `100dvh` con
respaldo `100vh` está bien puesto. El pecado clásico de no hacer mobile first
—que la página se vaya de ancho— no está.
Lo que salió MAL: **blancos táctiles**. En el mapa, el disparador del
calendario mide 30x30, los atajos de días 32 de alto, el botón Ver 35 y el
selector de provincia 35 — todos por debajo del mínimo de 44px, y son los
controles principales de la página pública. El tablero suma 378 elementos por
debajo de 44px y el detalle de producto 99 (26 de ellos los enlaces «ver
anuncio», de 65x15).
Decisión: se arregla la isla de filtros del mapa con una clase `.ph-tap-44`
que agranda el área sensible con un `::after`, **sin mover el dibujo**. El
operador había pedido expresamente compactar estos controles (2026-09-14) y
la isla flota sobre el mapa: cada píxel que crecen es mapa que tapan, y en un
teléfono acostado el alto disponible es ~390px. `<select>` e `<input>` no
admiten pseudo-elementos, así que esos dos sí crecen de verdad (el del
calendario no cuesta nada: vive dentro del panel que sólo aparece al abrirlo).
El `gap` de los atajos pasa de .3rem a .5rem para que dos áreas extendidas
vecinas no se pisen.
Descartado: (a) agrandar los controles a 44px reales — revierte el pedido de
compactar y come mapa; (b) un corte responsive que los agrande solo en
teléfono — el área extendida no molesta en escritorio y un corte nuevo va
contra la línea de eliminarlos (2026-09-04, 2026-09-16).
Consecuencia: queda pendiente el resto del inventario —los 378 del tablero y
los 99 del detalle—, que son otro slice. La regla debería entrar a
`CLAUDE.md` para que gobierne de acá en adelante.
Verificación: banco aislado en Chromium con el `<style>` y el marcado reales
a 390px, midiendo con `elementFromPoint` el área que efectivamente recibe el
toque: 7 de 7 controles llegan a 44x44 y ninguno cambia de tamaño visible.
Esa es la prueba; los 7 casos nuevos en `test_static_assets.py` son la
guarda de regresión, no la medición.
Suite: 1766 casos (+7). Dos casos existentes de `TestDatePickerSiempreVisible`
comparaban el atributo `class` entero y se aflojaron a comparar por prefijo:
se rompían al sumar una clase sin que cambiara nada de lo que protegen.

## 2026-09-17 — Se desbloquea el color por precio, pero sólo como escala continua
Contexto: el tablero necesitaba un vistazo de arriba que contestara «dónde
conviene» sin leer tarjeta por tarjeta. Al ir a implementarlo apareció que el
docstring de `routers/public.py` declara bloqueado «el coloreado del mapa por
precio y los semáforos… por el umbral sin calibrar (§12.6)». Se verificó la
spec antes de tocar nada: **§12.6 no prohíbe el color**, prohíbe asignar valor
a cinco umbrales (N, período de sostenimiento, categorías mínimas, criterios
de fuente, ventana de la tasa) sin medirlos. Y §6.7, que es donde vive el
verde/amarillo/rojo contra el promedio, está marcado como **prosa** en
`spec-map.md`, no como vinculante.
Lo que realmente exigía un umbral inventado era el esquema de §6.7: decir qué
cuenta como «en el promedio» es fijar una banda que nadie midió. Eso sigue
bloqueado.
Decisión (autorizada explícitamente por el operador): se habilita una escala
**continua de un solo tono** para la tira del tablero, y se mantiene bloqueado
el semáforo. Las tres restricciones que la hacen compatible con §12.6:
(a) **Sin bandas.** El color sale de `color-mix` entre `--ph-price-low` y
    `--ph-price-high` con un porcentaje por celda, no de cinco escalones. No
    hay ningún corte que decida a qué categoría pertenece un precio.
(b) **Sin veredicto.** Dice posición relativa entre lo observado —esta
    provincia está más cara que aquella—, nunca si un precio «está bien». El
    pie de la tira lo dice con todas las letras.
(c) **El color nunca es lo único.** Cada celda lleva la cifra y el número de
    anuncios escritos (§9.0), y con menos de tres provincias la escala ni se
    pinta: dos puntos no son una escala, y pintarlos de punta a punta haría
    ver un 6% de diferencia como si fuera el rango entero del país.
La tira compara por **precio por unidad**, que es el rol que le asigna la
decisión del 2026-09-04; el titular de compra sigue siendo el precio del
envase, en la tarjeta. Como no existe un agregado producto×provincia
—`build_headlines` también agrupa por formato—, cada provincia queda
representada por su **formato mejor respaldado**: una regla de selección sobre
cifras ya publicadas, no un estadístico nuevo.
Descartado: (a) el semáforo de §6.7 — exige la banda que §12.6 prohíbe;
(b) rojo/verde — reservado a error/éxito, misma disciplina que la paleta de
provincias; (c) texto encima del color — medido: el medio de cualquier
degradado secuencial no llega a 4.5:1 ni con tinta ni con blanco, así que la
barra va arriba y la cifra debajo, sobre la superficie.
Consecuencia: `--ph-price-low`/`--ph-price-high` entran a `design_tokens.css`
(claro y oscuro, invertidos en luminancia: «más caro» es «más tinta», y sobre
fondo oscuro eso es más claro). El coloreado del **mapa** sigue sin tocarse.
Verificación: render real servido con los estáticos locales y fotografiado a
390x844 y 1440x1000 — 3 tiras, 9 celdas, cero desborde horizontal, ninguna
celda por debajo de 44px de alto, y la escala de arroz repartida en 0/35/54/100
con azúcar en plano por tener sólo dos provincias.
Suite: 1780 casos (+14).

## 2026-09-17 — Blanco táctil de 44px en el tablero, y la clase se comparte
Contexto: la auditoría del mismo día había medido 378 elementos del tablero
por debajo de 44px. Se cierra la parte de los controles de navegación
principales: los dos toggles (Mapa/Tablero, Tarjetas/Tabla), los chips de
filtro, el índice de salto por provincia y el producto dentro de cada fila de
tabla. Quedan afuera —a propósito— las filas de evidencia dentro de un
`<details>`, que ya exigen un clic para verse.
Decisión: la clase `.ph-tap-44` se muda de `public_home.html` a
`saimc-public.css`: dejó de ser exclusiva del mapa en cuanto el tablero la
necesitó, mismo camino que ya recorrieron `.ph-card` y `.ph-coverage`. Se
suma un modificador `.ph-tap-ancho` para el único caso que tampoco llega a 44
de ancho (el disparador del calendario, 30x30).
Al envolverse en varias filas, dos áreas extendidas vecinas podían pisarse:
`.ph-dash-jump` y `.ph-dash-filter-group` suben su `gap` de `--m3-space-2`
(8px) a `--m3-space-3` (12px), y la fila de la tabla sube su padding vertical
a 14px para que el área del enlace no se salga hacia la fila de al lado.
Descartado: repetir la regla en cada plantilla — es exactamente el problema
que ya resolvió mover `.ph-card` a la hoja compartida.
Verificación: banco aislado con el `<style>` real de `public_dashboard.html`
Y el espaciado real entre bloques (`.ph-dash-top`, `.ph-dash-toolbar`,
`.ph-dash-filters`) — la primera pasada, apilando los controles sin ese
espaciado, dio dos falsos «NO» por un artefacto del banco (las áreas
extendidas de dos toggles contiguos se pisaban entre sí sin el
`padding-bottom` real de por medio); reconstruido con el espaciado real,
9 de 9 controles llegan a 44px sin cambiar su tamaño visible.
Suite: 1790 casos (+9, más 5 aflojados que comparaban el atributo `class`
entero y se rompían al sumar cualquier clase).

## 2026-09-17 — Fuente mínima a 11px, y la fila de evidencia de /producto/<slug>
Contexto: quedaban dos pendientes de la auditoría mobile-first del mismo día:
la fuente más chica del sitio (10px) y la fila de evidencia del detalle de
producto (65x15, distinta de la que ya se arregló en el tablero).
Se encontró que `/producto/<slug>` tiene su **propia** copia de la tabla de
evidencia —`.ph-lite-tabla` en `public_product_light.html`, con `.3rem` de
padding— separada de `.ph-coverage` en `saimc-public.css`. Mismo problema,
dos lugares: el arreglo del tablero no la cubría.
Decisión: mismo criterio que ya se aplicó a `.ph-coverage` — sube el padding
de la fila entera (`.3rem` → `.75rem`) en vez de extender el enlace con
`::after`, porque las filas van pegadas sin espacio entre sí. Y el
`.625rem` (10px) del cartelito flotante del mapa (`.ph-map-badge-unidad`,
la unidad y el conteo de anuncios) sube a `.6875rem` (11px), el mismo tamaño
que ya usan las otras etiquetas secundarias del sitio (`.ph-card-place`,
`.ph-chip`) — no se inventó un tamaño nuevo. El cartelito es `white-space:
nowrap` y de ancho automático, así que crecer el texto no lo desborda.
Descartado: tocar `.ph-canvas-kicker` (también a 10px) — es el wordmark de
marca «Guani · SAIMC · Fase 0», cromo decorativo que nadie necesita leer
para usar el sitio, no dato. Fuera de alcance de esta auditoría, que mide
texto que el visitante necesita leer.
Verificación: la fila de `.ph-lite-tabla` se midió en Chromium contra el HTML
real de `/producto/arroz` (vía TestClient con `?provincia=` — sin eso el
panel no trae anuncios y la tabla no se pinta), servido con los estáticos
locales ya modificados: **45px de alto, confirmado.**
Suite: 1792 casos (+2).

## 2026-09-18 — Fidelidad de la tira de precio contra el artboard aprobado
Contexto: el operador comparó la implementación de la tira (`94aede6`) contra
el artboard `Apilado` (`ApiladoEscritorio.dc.html`/`ApiladoMovil.dc.html`) que
había aprobado en Claude Design y no se parecía. Tenía razón: la primera
versión simplificó demasiado sin decirlo con claridad — quedó en una grilla
de celdas de tamaño parejo, cuando la idea central del mockup era que el
**ancho** de cada bloque fuera la evidencia (cuántos anuncios lo sostienen).
Decisión: se corrige la fidelidad en dos ejes, manteniendo la restricción de
contraste medida en la ronda anterior (el medio de cualquier degradado
secuencial no llega a 4.5:1 con ningún color de texto, así que el color
sigue sin llevar texto encima):
1. **Ancho proporcional.** Cada celda es un `<li>` con `flex-grow:
   cell.count; flex-basis: 0; min-width: 8.5rem`. No se calcula ningún
   porcentaje en Python: el propio algoritmo de resolución de longitudes
   flexibles de CSS reparte el espacio sobrante proporcional al conteo
   cuando sobra espacio (escritorio) y lo aplasta contra el `min-width`
   cuando no sobra (teléfono) — la misma degradación mobile-first que el
   prototipo calculaba a mano con una función de redistribución iterativa,
   acá gratis y sin agregar ningún corte de ancho nuevo.
2. **Cinta de precios.** Arriba de la tira, un vistazo de todos los
   productos con su precio más barato — mismo espíritu que la cinta oscura
   del artboard. Se simplificó a propósito: se queda en el tono claro de la
   página, contenida al bloque de la tira, en vez de repintar el cromo de
   toda la vista (eso hubiera sido un slice de otro alcance, tocando el
   header entero del tablero). Y no calcula ningún agregado nuevo: muestra
   `strip.cells[0]`, la celda más barata que la tira ya tiene calculada.
También sube la altura de la barra de color (6px → 10px, ahora hace doble
trabajo de color y forma) y el tamaño del título de la sección, sin llegar al
tamaño del h1 real de la página —seguiría rompiendo la jerarquía de
encabezados si igualara al titular de arriba.
Descartado explícitamente, y avisado: (a) texto sobre el degradado — el
problema de contraste medido en la ronda anterior sigue sin resolverse, así
que la barra se queda como acento, no como fondo con texto encima; (b) una
cinta oscura a todo el ancho de la página — cambiaría el cromo de la vista
entera, fuera del alcance de «arreglar la tira».
Verificación: caso de test con conteos deliberadamente desparejos (16/3/1/6)
para que la diferencia de ancho sea visible, más render real en Chromium a
1440px (La Habana 471px vs. Holguín 136px, mismo mínimo) y a 390px (136–214px,
casi parejo: el aplastamiento contra el mínimo funciona). Prueba de mutación
hecha: `flex-grow` fijo en vez de `cell.count` tira el caso nuevo.
Suite: 1795 casos (+3).

## 2026-09-18 — Se retiran las tarjetas del tablero, entra el menú de acciones, se arregla el color por provincia
Contexto: tres pedidos del operador sobre el tablero, cerrados en un slice.

**1. Solo tabla.** El tablero tenía dos presentaciones (tarjetas agrupadas con
índice de salto, y tabla) detrás de un toggle `?presentacion=`. El operador
pidió sacar las tarjetas y dejar solo la tabla. Se retira el eje entero, no
solo el toggle visible: `PRESENTACION_CHOICES`/`DEFAULT_PRESENTACION`/
`resolve_presentacion` en `product_nav.py`, el parámetro `presentacion` en
`_query`/`products_query`/`build_product_chips`/`build_province_nav` y en las
dos rutas del router, `card_view.ProductGroup`/`group_products_by_province`
(código muerto sin la vista que los usaba) y la plantilla
`_product_group_item.html`. La tabla (`cards_by_province`,
`group_cards_by_province`) queda intacta: nunca dependió de la agrupación por
producto, sigue una fila por formato, igual que siempre.
Descartado: dejar el parámetro `?presentacion=` aceptado pero sin efecto —
sería un query string que miente sobre lo que hace.

**2. Menú de acciones en el tablero.** El compás del mapa tiene siete
acciones (Cobertura, Precios, Cómo se movió, CSV, Tablero, zoom, leyenda,
tema), ninguna alcanzable desde el tablero. Se agregó `<details class="ph-
actions">` en la cabecera, mismo avatar (`_guani_avatar.html`) que abre el
compás, con cuatro de las siete: Cobertura y Cómo se movió (enlazan al mapa
con esa ventana abierta), CSV (descarga directo) y tema (alterna in-place,
mismo `.theme-toggle` que ya cablea `_theme_toggle_script.html` por clase).
Quedan afuera Precios (es literalmente lo que ya muestra el tablero) y
zoom/leyenda (no hay mapa que acercar ni colorear acá). No se portan las
ventanas flotantes tal cual —están atadas a la posición absoluta del lienzo
Leaflet, que el tablero no tiene a propósito (147 KB menos)—, se enlaza al
mapa con la ventana pedida.
**Bug real encontrado construyendo esto**: los enlaces a Cobertura/Evolución
se armaban con `products_query(..., vista="mapa")`, pero `_query` OMITE
`vista=` de la URL cuando coincide con `DEFAULT_VISTA` (que es «mapa», para
no ensuciar el caso común) — así que el enlace nunca llevaba `vista=` en
absoluto, y quien está en el tablero casi seguro tiene la cookie `vista=
tablero` puesta: el clic devolvía al tablero en vez de abrir la ventana.
Se corrigió pegando `&vista=mapa` a mano al final del href, fuera del
mecanismo de omisión. Verificado con un test que reproduce el escenario real
completo: cookie en «tablero», seguir el enlace generado, confirmar que
efectivamente renderiza el mapa.

**3. Bug de color: la tabla se veía de un solo color.** No era percepción:
`province_slots` salía de `province_color_slots(sorted(all_province_names))`
—el catálogo completo de 16 provincias, alfabético— para que el color fuera
estable entre cargas (Dirección D, 2026-09-16). Con datos reales solo en dos
provincias hoy, `La Habana` cae en el índice 8 y `Santiago de Cuba` en el 14
de esa lista de 16 —las dos muy por fuera de los tres slots con nombre
propio (0/1/2)— así que las dos caían en el mismo neutral (9). La promesa de
estabilidad no valía nada si nunca hay más de un color visible. Se cambió a
calcular sobre `province_names` (las provincias CON dato en la vista actual,
ya alfabético): dos provincias reales son ahora dos slots reales y distintos.
Se pierde la estabilidad entre cargas —si mañana se suma una tercera
provincia, los colores de las dos actuales pueden correrse—; se vuelve a
evaluar cuando haya más de tres provincias reales.
Consecuencia menor: `province_slugs` (usado solo por el índice de salto que
se retiró) y `groups_by_province`/`ProductGroup` salen del contexto y del
código.
Verificación: render real con Chromium confirmando 0 tarjetas/secciones
residuales, la tabla con sus filas, el disparador de acciones visible, y los
dos puntos de color en rgb distinto (`rgb(63,95,214)` La Habana vs.
`rgb(112,72,232)` Santiago).
Suite: 1777 casos.

---

## 2026-09-18 — `n` en /control pasa a declarar de cuántos emisores y días viene

Contexto: revisando la matriz de pilotos, el operador cuestionó que «saco de
100 lb» y «saco de 100 lb criollo» sean dos series hermanas. Midiendo la
fragmentación para responder, reporté que el 71% de las filas eran reposteos
duplicados. **Eso estaba mal medido**: agrupé por `(producto, provincia,
precio, unidad, cantidad)`, que confunde tres cosas distintas — un raw con
varios productos (78 raws producen de 2 a 5 filas; son catálogos mayoristas),
el mismo vendedor reposteando en días distintos, y precios que coinciden de
verdad. No hay ni un `content_hash` repetido en `raw_ingestions`: el índice
único funciona y no existe duplicado de ingesta. La entrada del 2026-08-23
(«Arroz/Santiago 17.500×25kg no es duplicado») ya había investigado este mismo
grupo —entonces 11 filas, hoy 31, con 31 hashes distintos en 18 días— y sigue
vigente: no se borra ninguna fila. Lo que sí es real es otra cosa: la celda de
Arroz en Santiago tiene 27 mensajes de 2 emisores en 15 días con el 85% de un
solo canal, y la de Pollo en La Habana 20 mensajes con el 90% de uno. `n = 27`
afirma una densidad que la muestra no tiene. Es la señal que §10.7 C4 nombra
(«concentración inusual del aporte por fuente o canal en una celda»), y C1
pide para la Fase 0 justamente hacerla visible: «informativa: tamaño de
muestra visible».

Decisión: `Cell` gana tres campos de composición —`sources` (canales
distintos), `unidentified_sources` (filas cuya ingesta no declara canal) y
`days` (días distintos, con el repliegue ya existente `published_date or
created_at.date()`)— más `sample_label`, que los lee como «2 emisores · 15
días». `compute_cells` joinea `raw_ingestions` para traerlos. `/control` lo
pinta al lado de `n`. Sin umbral de cuántos emisores son pocos: §12.6 lo
prohíbe y quien juzga es el operador.

Descartado: (a) borrar o colapsar filas repetidas — contradice la decisión del
2026-08-23 y destruiría la serie temporal, que es el dato que el proyecto
busca; (b) contar las filas sin `channel_id` como un emisor —afirmaría que son
el mismo vendedor— o como un emisor cada una —afirmaría que son distintos—:
ninguna de las dos está declarada, así que se cuentan aparte y visibles, mismo
criterio que `unconverted`/`unquantified`. No es un borde: son 106 de 336
filas (65 de imagen del inbox, 37 de WhatsApp sin canal, 4 de texto).

Consecuencia: la pregunta que abrió la sesión —qué ejes merecen partir una
serie— queda en condiciones de decidirse con muestra honesta en vez de con `n`
inflada, y sigue abierta. `RetailRange` (tablero público) **no** recibe estos
campos: es otra vista y otro slice. Queda anotado que la prueba de separación
por eje, corrida sobre estos datos, dio ≤×1,19 para la variante y ≤×1,29 para
`sale_format` — por debajo del ruido interno de la celda, pero no concluyente
mientras la mitad de cada celda venga de un emisor.
Verificación: `compute_cells` corrido contra producción —79 celdas, 336
mensajes, 0 celdas donde `sources + unidentified_sources` supere `count`, 0 con
`days > count`, 0 con `days == 0`—. Suite: 1791 casos. Sin drift de ruff
(65 = piso); los 2 hallazgos nuevos de mypy en `cells.py` son los mismos del
piso corridos de línea, y los de `rapidocr` son dependencia opcional ausente.


## 2026-09-18 — Tablero como dashboard (dirección C), slice 1: lista de productos + cobertura
Contexto: el operador pidió que el tablero deje de ser «el mapa sin mapa» y
pase a ser un dashboard con todo lo que hoy vive en el menú del avatar del
mapa (Cobertura, Precios, Cómo se movió, CSV, tema); el mapa no se toca. Se
prototiparon tres direcciones en Claude Design (Consola, Informe, Por
producto) y eligió **C — Por producto**: lista de seguimiento a la izquierda,
detalle a la derecha. **Reemplaza** a la entrada del 2026-09-16 («Slice 1 del
modo Tablero») en el punto en que decía que las ventanas del mapa eran
«conceptos exclusivos del lienzo, que el tablero no usa ni usará».
Decisión: slice 1 de 2. `watchlist_view.build_watchlist` arma una fila por
producto piloto con la mediana de cada provincia y el cambio contra el período
anterior, tomados tal cual de `build_month_vs_month` —el mismo «¿subió o
bajó?» de «Cómo se movió»—, sin flecha ni color (§12.6). Una fila elige ese
producto y solo ese (`?productos=<slug>`); «Todos los productos» vuelve al
tablero completo. Los chips de producto se retiran (los reemplaza la lista);
los de provincia se quedan. El menú `<details>` del avatar se retira: Mapa,
CSV y tema quedan a la vista en la cabecera, Cobertura pasa al cuerpo, y
«Cómo se movió» sigue enlazando a la ventana del mapa hasta el slice 2
(detalle por producto con sus gráficos). La frescura del dato pasa a verse sin
abrir nada. Mobile first: una columna lista → contenido → cobertura; dos
columnas desde 64rem (el shell se abre a 84rem solo en esta plantilla).
Descartado: conservar la multi-selección de productos en la lista — la
dirección C elige un producto a la vez; una URL vieja con varios
(`?productos=a,b`) sigue funcionando y marca los dos, pero la lista no arma
selecciones nuevas. Descartado también portar las ventanas flotantes tal cual:
están atadas al lienzo Leaflet.
Consecuencia: dos cifras de «mediana por libra» conviven ahora en la misma
pantalla y no coinciden (la lista junta todos los formatos convertidos a
CUP/lb; la tira «dónde está más barato» usa las tarjetas por formato — p. ej.
Picadillo en La Habana 463 contra 530); queda para decidir cuál manda. La
columna «Cambio» de la tabla sigue con su propio cálculo (`attach_trends`,
que empareja titulares por producto × provincia × moneda × unidad y pisa los
formatos entre sí), distinto del de la lista; también pendiente.


## 2026-09-18 — Tablero como dashboard (dirección C), slice 2: detalle por producto
Contexto: cierra el pedido del slice 1. Con un producto elegido en la lista, el
tablero tiene que mostrar lo que en el mapa son las ventanas «Precios» y «Cómo
se movió», sin mandar al mapa.
Decisión: con `?productos=<slug>` de UN solo producto, la página agrega el
detalle (`#detalle-titulo`): rango por provincia, «¿subió o bajó?», mediana por
semana, calendario de días con anuncios y la tabla de los anuncios detrás, con
su enlace (§6.5). Reusa los mismos parciales del mapa (`_province_compare`,
`_slope_chart`, `_steps_chart`, `_calendar_chart`, `_chart_help`) y los mismos
datos que el router ya armaba (`province_comparisons`, `evolution_blocks`), así
el tablero y el mapa no pueden contradecirse; la lista de productos y el
«antes y ahora» del detalle salen de `build_month_vs_month`, la misma fuente.
Sin producto, o con varios (una URL heredada), no hay detalle: la lista ya los
resume. Se retira el enlace provisorio «Cómo se movió» de la cabecera y, con él,
`accion_href`/`evolucion_href` del router. El CSS de los gráficos se muda de
`public_home.html` a `saimc-public.css` —mismo motivo por el que `.ph-card`
se mudó el 2026-09-16: el tablero no carga el `<style>` del mapa—; en el mapa
quedan solo `.ph-prov-0/1`, su paleta propia.
Descartado: reescribir los gráficos para el tablero. Son parciales de SVG que
ya pasaron por la auditoría de accesibilidad del mapa; duplicarlos habría sido
dos copias que se despegan.
Consecuencia: los SVG escalan su texto al ancho del contenedor; en el mapa la
ventana los acota, acá se acotan a mano (`.ph-pasos` 30rem, `.ph-calendario`
12rem) — un calendario de ~113 unidades estirado a todo el panel llevaba sus
etiquetas a 35px y la última se salía del borde. Queda pendiente, y es del
mapa: con el ancho de la ventana flotante la etiqueta «14/09» del calendario
también roza el borde derecho. Siguen abiertas las dos decisiones del slice 1
(qué mediana manda entre lista y tira, y `attach_trends` pisando formatos).


## 2026-09-18 — El «Cambio» de la tarjeta y de la tabla es el del producto en la provincia (slice 3)
Contexto: comparando el tablero con datos de producción apareció una
contradicción en la misma pantalla: la lista de productos decía arroz en La
Habana −3% y todas las filas de la tabla decían −12%. Reproducido contra la
base: `build_headlines` arma un titular por formato (5 formatos de arroz en La
Habana, con cambios de −4%, +15%, sin dato, +1% y −12%) y `attach_trends`
armaba `{(producto, provincia, moneda, unidad): change_text}` —sin formato en
la clave—, así que el último titular pisaba a los demás. El −12% era el de
«saco de 50 kg», con **un solo anuncio**, pegado a las once tarjetas. El
docstring decía que la tarjeta comparte «el cambio agregado de todo el
producto en esa provincia»; la implementación no calculaba ningún agregado.
Decisión: `attach_trends(cards, slopes)` toma el cambio de
`price_charts.build_month_vs_month` —el mismo «¿subió o bajó?» de la lista, del
detalle y de «Cómo se movió»— por producto + provincia + moneda + unidad. Las
tres cifras salen de una sola llamada, que el router hace una vez y reparte.
Arroz en La Habana pasa a −3% en las once filas. Se retira de `card_view` la
dependencia de `Headline` para esto (queda para `attach_blocks`).
Descartado: (a) que cada fila de formato muestre su propio cambio, con «—»
cuando ese formato no tiene período anterior. Es defendible —es lo que ya hace
el panel flotante del mapa por celda— pero da cinco números distintos para un
mismo producto y varios de ellos con 1 a 3 anuncios de sustento; queda como
alternativa si el operador prefiere leer cada envase por separado.
(b) Arreglar solo el diccionario para que no pise: seguiría sin ser un
agregado.
Consecuencia: el arreglo llega también a las tarjetas de la ventana «Precios»
del mapa, que usan el mismo `attach_trends`; los números de esas tarjetas
cambian (para bien: dejan de mostrar el de un formato cualquiera). El panel
flotante del mapa conserva su cambio por celda (formato), porque su celda es
por formato. Sigue abierta la otra mitad de la contradicción del tablero: qué
mediana manda entre la lista/detalle (todos los formatos por libra) y la tira
«dónde está más barato» (por formato).


## 2026-09-19 — La tira «dónde está más barato» usa la misma mediana que la lista (slice 4)
Contexto: con datos de producción, la lista de productos y la tira del mismo
tablero decían cosas distintas para el mismo producto y provincia: picadillo en
La Habana 463 en la lista y 530 en la tira (10 anuncios contra 4), lo que
cambiaba el mensaje —La Habana un 19% más barata que Santiago, o casi igual—.
Causa: `build_price_strips` representaba a cada provincia con su «formato mejor
respaldado» (la tarjeta con más anuncios), con el argumento —escrito en su
docstring del 2026-09-17— de que «no existe un agregado producto×provincia» y
de que promediar formatos sería inventar un estadístico. Ese agregado sí
existe: `build_month_vs_month` lo calcula y las comparaciones entre provincias
lo usan desde el 2026-09-14 («¿dónde está más caro? es una pregunta de
provincia, no de formato»).
Decisión: `build_price_strips(slopes, *, products=(), province=None)` toma de
`build_month_vs_month` la mediana de ahora y la cantidad de anuncios de cada
producto × provincia, con todos los formatos juntos —la misma cifra de la
lista de productos, el detalle y el cambio de las tarjetas—. Los filtros de la
página (producto y provincia) se aplican al armarla, porque `slopes` trae todo;
una provincia con dato solo del período anterior no tiene celda. `PriceCell`
pierde `format_label` (la plantilla nunca lo mostraba). El router reusa los
mismos `slopes` que ya calculaba. Verificado contra producción: las 9 celdas de
la tira coinciden con la lista en mediana y en anuncios.
Descartado: (a) dejar la tira como estaba y aclarar en pantalla que compara «el
formato mejor respaldado» —dos números para lo mismo seguiría siendo un dato
que el visitante no puede reconciliar—; (b) cambiar la lista para que también
elija un formato —perdería la muestra completa, y la lista es donde el
visitante ve cuánto sostiene cada cifra.
Consecuencia: el ancho de cada bloque de la tira (`flex-grow` = anuncios) pasa
a reflejar la muestra completa de la provincia y no la de un solo formato, así
que los bloques se ven más parejos entre sí en los productos con muchos
formatos. Sigue abierta, y es del operador, la otra decisión del tablero: si
cada fila de formato de la tabla muestra el cambio del producto (hoy) o el
suyo propio.


## 2026-09-19 — Tablero: una cifra, un lugar (S5): la lista absorbe la tira y se quita lo repetido
Contexto: el operador rechazó el tablero («información repetida, no hay una
filosofía clara de qué debe ver el usuario») y pidió una vista coherente y útil.
Medido con Chromium contra datos reales: «Todos» a 390 px medía 6,3 pantallas;
la mediana de arroz en La Habana aparecía en tres lugares (lista, cinta, tira) y
el cambio en cinco; la tabla por formato ocupaba 2.452 px con «La Habana» 49
veces. Se prototipó la dirección E en Claude Design (una cifra, un lugar; tres
capas: cuánto cuesta y se mueve → de dónde sale → puedo confiar) y el operador
la aprobó para arrancar por S5. Reemplaza al diseño de cinta y tiras del
2026-09-17/18 (artboard «Apilado»), aprobado entonces por el operador.
Decisión: (1) la lista de productos absorbe lo único que la tira decía y la
lista no: cada celda lleva cuántos anuncios la sostienen, la base del cambio
—«−4% (base 52)», los anuncios del mes anterior con los que se compara— y la
etiqueta «más barata» en la provincia de menor mediana (solo con dos o más
provincias con dato y sin empate; texto, no semáforo: dice cuál es la menor, no
si «está bien», así que no fija umbral y no toca §12.6). (2) Se quitan la cinta
del más barato, las tiras por producto, la caja de búsqueda con su script
(quedó de cuando había 50 tarjetas; hoy son 5 productos) y la columna «Cambio»
de la tabla (repetía once veces el cambio del producto). (3) Código muerto
fuera: `build_price_strips`, `ProductStrip`, `PriceCell`, `strip_hrefs`, la
plantilla `_price_strips.html`, su CSS y sus tests. El mapa no cambia; sus
tarjetas siguen usando `attach_trends`. Medido después: 4,8 pantallas a 390 px
(de 6,3), sin desborde, sin restos.
Descartado: (a) dejar la tira y ordenar la lista alrededor —seguiría habiendo
dos maneras de leer la misma cifra—; (b) quitar la tira sin pasar «más barata»
y los anuncios a la lista —se perdía «dónde está más barato», la pregunta
central de §6.7—.
Consecuencia: la tabla por formato sigue en «Todos» (49 filas) hasta S6, que la
pasa al detalle del producto; el bloque «Sobre los datos» (cobertura, método,
fuente) es S7 y el selector «Mi provincia» que se recuerda, S8. Queda a la vista,
sin resolver, que el encabezado de la columna «Santiago de Cuba» se corta con
«…» en la lista a 390 px.


## 2026-09-19 — Tablero: la tabla por formato pasa al detalle como «Por envase» (S6)
Contexto: segunda entrega del plan «una cifra, un lugar» (dirección E, aprobada
por el operador). Después de S5, «Todos» seguía siendo una tabla de 49 filas de
2.452 px con el nombre de la provincia repetido en cada una: casi la mitad de
la página, y ninguna pregunta del usuario común («¿cuánto cuesta?») pasa por
leer envases.
Decisión: (1) «Todos» ya no lleva tabla: es la lista de productos y la
cobertura. (2) Con un producto elegido, el detalle agrega el panel «Por envase»:
un grupo por provincia con su nombre una sola vez en el encabezado, y por cada
envase el precio del envase (rango, o el único precio si hay un solo anuncio),
la mediana y los anuncios. Se ven los 3 de más muestra y el resto queda en un
`<details>` nativo («Ver los 11 envases de La Habana»), sin JS. Las dos tablas
de un grupo comparten `<colgroup>` para alinear columnas. (3) Cada fila muestra
su **variante** declarada («Criollo», «Argentino»): con datos reales «saco de
100 lb» aparecía dos veces en La Habana porque eran dos variantes que la tabla
vieja no decía, y parecían filas repetidas (`ProductCard.format_label_display`,
que ya existía en `Headline`, se agrega a la tarjeta). (4) §9.0: la nota «muestra
en formación» vivía bajo la tabla; se muda bajo la lista de productos, que está
en todas las vistas del tablero, así que el rótulo obligatorio sigue a la vista
(S7 la consolida en «Sobre los datos»). (5) Sale el enlace de cada fila a la
vista liviana `/producto/<slug>` (`product_hrefs`): ya no hay tabla de la que
salir; esa vista sigue existiendo y se alcanza desde el mapa.
Descartado: (a) dejar la tabla en «Todos» colapsada por defecto —seguiría siendo
un bloque que el usuario común no necesita y que compite con la lista—;
(b) mostrar todos los envases sin recorte —11 filas en La Habana repetirían el
problema dentro del detalle—.
Consecuencia: medido con Chromium contra datos reales, «Todos» a 390 px pasa de
6,3 pantallas (al empezar el plan) a 1,9; el detalle de Arroz mide 4,2 a 390 px
y 2,3 a 1280, sin desborde ni scroll interno. Siguen S7 («Sobre los datos»:
cobertura, método y fuente en un solo bloque) y S8 («Mi provincia» que se
recuerda).


## 2026-09-19 — Tablero: «Sobre los datos» reúne cobertura, lectura, fuente y descarga (S7)
Contexto: tercera entrega del plan «una cifra, un lugar» (dirección E). Después
de S6, lo que explica de dónde sale lo que se ve estaba repartido en cuatro
sitios: la Cobertura en un panel, la guía de lectura («sin flecha ni color»,
«(base N)») en un párrafo largo bajo el título de la lista, la descarga en un
ícono de la cabecera y la fuente en ninguna parte.
Decisión: (1) El panel «Cobertura» pasa a llamarse «Sobre los datos» y absorbe,
en este orden: la cobertura de siempre (observaciones, provincias con dato,
publicadas, barras por provincia y la nota de §9.0 de por qué se publica cuando
hay muestra suficiente), «Cómo leer las cifras» (qué es la mediana, que el
cambio va sin flecha ni color, qué es «(base N)» y «—»), «De dónde salen» (redes
sociales y grupos de mensajería del mercado informal, verificados a mano antes
de entrar en una cifra; ya lo dice el `og:description` del sitio) y la descarga
CSV con su etiqueta a la vista. (2) La cabecera queda con Mapa/Tablero y el tema:
el CSV sale de ella, así que hay un solo enlace de descarga. (3) La lista
conserva solo la frase que dice qué mide («Mediana de los últimos 30 días y su
cambio contra los 30 anteriores»). (4) **La frescura no se repite en el bloque:**
el prototipo E la traía, pero la cabecera ya la muestra («Datos verificados
hasta el…») y repetirla contradice «una cifra, un lugar». (5) **Apartamiento del
plan:** el plan decía que el bloque absorbe la nota de §9.0 «muestra en
formación». No lo hace: §9.0 y la entrada del 2026-08-22 exigen que el rótulo
acompañe a la cifra, y a 390 px el bloque queda al final de la página, lejos de
ella. El rótulo se queda bajo la lista, una sola vez.
Descartado: (a) dejar el CSV también en la cabecera —dos enlaces al mismo
archivo—; (b) mover el rótulo §9.0 al bloque, como decía el plan —lo separa de
las cifras que rotula—; (c) afirmar una ventana de días sobre «Observaciones»
(«187 anuncios de los últimos 30 días» del prototipo): el conteo del mapa no está
acotado a esa ventana y no se publica lo que no se mide (§12.6).
Consecuencia: medido con Chromium contra datos reales, «Todos» a 390 px pasa de
1,9 a 2,1 pantallas y el detalle de Arroz de 4,2 a 4,4 (el bloque explica más
que el panel anterior), sin desborde a 390 ni a 1280; la descarga mide 201×44 px.
El CSV queda al final de la página en el teléfono: si se echa de menos arriba,
es un cambio de una línea en la cabecera. Sigue S8 («Mi provincia» que se
recuerda).


## 2026-09-19 — Tablero: «Mi provincia», un selector arriba de la lista que se recuerda (S8)
Contexto: última entrega del plan «una cifra, un lugar» (dirección E). El
selector de provincia vivía junto al título del detalle, después de la lista en
un teléfono, y solo filtraba el detalle: la lista seguía mostrando todas las
provincias en columnas (la de «Santiago de Cuba» se corta con «…» a 390 px) y
quien vuelve cada día tenía que volver a elegir la suya.
Decisión: (1) Un solo selector, «¿En qué provincia estás?», arriba de la lista
—se quita el de junto al título—, con «Todas» y las provincias con dato piloto.
Sigue a la vista aunque el filtro deje cero resultados, que es como se deshace.
(2) Con una provincia elegida la lista se enfoca: una sola columna con esa
provincia, y las demás pasan a una línea chica bajo cada fila («La Habana: 298 ·
40 anuncios · más barata»), como en el prototipo E. «Más barata» se sigue
decidiendo entre todas las provincias con dato: sin la línea chica, la etiqueta
no tendría contra qué compararse a la vista. Si el slug no es una columna (una
provincia sin dato piloto, una cookie vieja) no hay foco y se ven todas.
(3) Se recuerda con la cookie `provincia` (un año, `SameSite=Lax`, misma forma
que `vista`), con esta regla: `?provincia=` explícito manda; sin él, **solo en
el tablero**, el servidor lee la cookie; un valor que no sea provincia se
ignora. El mapa no la lee, así que no cambia. (4) La cookie la escribe el clic
en un chip (JS, `a[data-provincia]`), y «Todas» la borra. No se escribe al abrir
un `?provincia=` de un enlace ajeno: se recuerda lo que el usuario eligió, no lo
que le mandaron. Sin JS no hay cookie que escribir, así que no queda un estado
del que no se pueda salir.
Descartado: (a) escribir la cookie desde el servidor cuando llega `?provincia=`
—un enlace compartido le cambiaría la provincia por defecto al que lo abre—;
(b) dejar las columnas de todas las provincias y solo resaltar la elegida —no
arregla el encabezado cortado ni acorta la lista en el teléfono—; (c) mostrar
solo la provincia elegida sin las otras —«más barata» quedaría sin sustento a la
vista—; (d) leer la cookie también en el mapa —cambiaría su encuadre por una
preferencia que nadie pidió allí.
Consecuencia: medido con Chromium contra datos reales, el clic en «Santiago de
Cuba» escribe la cookie y `/?vista=tablero` sin query vuelve enfocado en ella;
`?provincia=la-habana` explícito gana; «Todas» la borra; el mapa con la cookie
puesta sigue siendo el mapa. A 390 px la página pasa de 2,1 a 2,2 pantallas con
provincia elegida, sin desborde. Sin elegir, el encabezado «Santiago de Cuba»
de la lista sigue cortándose (2 columnas en 22 rem): elegir provincia lo evita,
no lo arregla. Con esto el plan «tablero coherente» (S5–S8) queda completo.


## 2026-09-19 — Corrección a S8: la cookie `provincia` solo se borra con un clic normal en «Todas»
Contexto: la revisión de S8 (`c0c0fde`) marcó que la entrada anterior afirma
«sin JS no hay cookie que escribir, así que no queda un estado del que no se
pueda salir». Solo vale si la cookie nunca se escribió: una cookie escrita en
una sesión con JS sigue ahí si después el JS falla. Además el clic en «Todas»
la borra en el evento `click`; el clic del botón del medio dispara `auxclick` y
Ctrl/Cmd-clic abre otra pestaña, así que en esos casos la cookie sigue y el
tablero vuelve a enfocarse en la provincia guardada aunque el chip diga «Todas».
Decisión: no se toca código. Queda como límite conocido: se sale con un clic
normal en «Todas», que es lo que hace casi todo el mundo. Reemplaza en ese
punto a la frase citada de la entrada de S8.
Descartado: darle a «Todas» un `?provincia=todas` explícito —ganaría a la cookie
en esa página, pero los enlaces de producto que salen de ella no llevan
provincia y la cookie volvería a aplicarse—; el arreglo completo es que el
servidor borre la cookie al recibir ese valor (`Set-Cookie` con `max-age=0`),
que cambia la regla «el servidor solo lee» y pide su propia revisión.
Consecuencia: si alguien reporta que «Todas» no suelta la provincia, la causa
es esta y el arreglo es el del párrafo anterior. La revisión también sugirió
asertar el contenido de la línea de las otras provincias (mediana, plural de
«anuncio», «más barata») a nivel HTML; hoy solo se asierta la presencia y el
nombre, y los valores se cubren en `tests/test_watchlist_view.py`.


## 2026-09-19 — Tablero: reconstruido sobre el prototipo E aprobado (corrige la disposición de S5–S8)
Contexto: al cerrar S5–S8 el operador dijo que la vista «no contiene nada que
ver con el artefacto de Claude Design». Renderizados el prototipo E y la página
a los mismos anchos, tenía razón: E es una cabecera blanca, una tarjeta por
producto en la columna principal y una barra lateral (frescura, «Productos» en
el detalle, «Sobre los datos» en viñetas); lo entregado era la lista compacta de
22 rem del prototipo C con la columna principal vacía en «Todos», un panel
«Cobertura» con barras y el selector de provincia dentro de la lista. S5–S8
habían aplicado la *filosofía* de E (una cifra, un lugar) como cambios
incrementales sobre la estructura de C, y se verificaron con pruebas y alturas
de página, nunca contra el prototipo; además se declaró «plan completo».
Decisión: se reconstruye `public_dashboard.html` con la disposición de E.
(1) Cabecera de borde a borde: marca «Guani», toggle Mapa/Tablero, CSV y tema
(en móvil, el toggle en su propia fila). (2) «Todos»: «Qué cuesta hoy», «¿En qué
provincia estás?» como píldoras (la activa oscura) y una tarjeta por producto
con las provincias lado a lado, cifra grande, cambio con su base y «más
barata»; con una provincia elegida, ella en grande y las demás en una línea
chica. (3) Detalle: «‹ Todos los productos», tarjeta resumen con la frase «X es
la más barata: 298 contra 318 en Y», «Cómo se movió» y «Cuánto varía» lado a
lado, «Por envase» (con la variante), y los anuncios en una fila plegada. (4)
Barra lateral en escritorio: chip de frescura, «Productos» (solo en el detalle)
y «Sobre los datos» en cinco viñetas con el CSV. (5) La provincia elegida
enfoca solo la lista de «Todos»; **el detalle compara siempre todas las
provincias** (`scope_province` en el router). (6) Datos nuevos en la lista:
`total_ads` (suma de anuncios de las celdas de la ventana: es el «sobre N
anuncios» del subtítulo y la primera viñeta), `todas`, `barata`, `contra`.
Se conserva de S5–S8: una cifra en un solo lugar, «Por envase» con variante, la
cookie `provincia` que escribe el clic y lee solo el tablero (y su límite
conocido, ver la corrección de S8).
Desviaciones deliberadas de E, para que no vuelvan a ser sorpresa: (a) el rótulo
§9.0 «muestra en formación» va una vez por vista bajo el título (E no lo traía y
es obligatorio); (b) el subtítulo no dice «en CUP» porque cada cifra lleva su
moneda; (c) los chips dicen «Santiago de Cuba» completo (E móvil abrevia); (d)
los productos van en orden alfabético (el de E era el de la maqueta); (e) los gráficos
son los reales, no los dibujos del prototipo; (f) sin «¿Subió o bajó?» ni el
calendario de días con anuncios en el detalle (E no los trae: el cambio ya está
en la tarjeta resumen; siguen en el mapa); (g) sin las barras de cobertura por
provincia (E las resume en «2 de 16 provincias tienen datos»).
Descartado: seguir corrigiendo S5–S8 por partes —más de lo mismo sin comparar
contra el prototipo—.
Consecuencia: verificado renderizando el prototipo y la página a 390 y 1280 px
en «Todos», «Mi provincia» y el detalle; sin desborde horizontal de 320 a 1600
px; ningún control por debajo de 44 px (el toggle Mapa/Tablero lo extiende
`ph-tap-44`); modo oscuro por los tokens del sitio. Regla de proceso: un slice
de interfaz no se cierra sin las capturas del prototipo y de la página lado a
lado.


## 2026-09-20 — Tablero: orden por muestra y el menú de productos desde la tableta (alineación E, 1/4)
Contexto: comparado el prototipo E aprobado contra la página, con la misma
ventana de datos, aparecieron doce diferencias. El operador pidió alinearlas
todas, en cuatro entregas. Esta es la primera.
Decisión: (1) `build_watchlist` ordena las filas de más a menos anuncios del
producto entero —todas las provincias, aunque haya una enfocada—, con el
alfabeto como desempate para que el orden no cambie de una carga a otra. Con
los datos de hoy da Arroz, Pollo, Picadillo, Aceite, Azúcar blanca, que es el
orden del prototipo; antes era alfabético. Arregla a la vez las tarjetas de
«Todos» y el menú lateral, que recorre las mismas filas. (2) El menú
«Productos» se escondía por debajo de 64 rem: en una tableta el detalle se
quedaba sin ningún modo de saltar de un producto a otro salvo volver a
«Todos». Ahora se muestra desde 48 rem, apilado entre el contenido y «Sobre
los datos», y sigue siendo barra lateral desde 64 rem. En el teléfono no está,
igual que en el prototipo.
(3) La grilla de una columna deja de nombrar filas. Una fila nombrada que
queda vacía —la navegación, escondida en el teléfono y ausente en «Todos»—
mide cero pero conserva su `gap` a los dos lados: medido, 32 px de aire entre
el contenido y «Sobre los datos» donde tocaban 16. En una columna alcanza el
orden del documento, así que las áreas viven solo en el bloque de 64 rem, con
un juego aparte (`.has-nav`, que pone la plantilla) para cuando hay un
producto elegido. El aire doble también estaba en «Todos» a 1280 desde la
reconstrucción sobre E; queda corregido de paso.
Descartado: (a) ordenar por el dato de la provincia enfocada —la lista se
reordenaría al elegir provincia y se perdería de vista el producto que se
venía mirando—; (b) mostrar el menú también en el teléfono: el prototipo no lo
trae y ahí se vuelve con «‹ Todos los productos»; (c) dejar la fila `nav`
declarada y confiar en que una fila vacía no ocupa: ocupa sus dos `gap`.
Consecuencia: el orden es estable mientras la muestra no cambie de posición;
cuando entren más provincias, el criterio sigue siendo el producto entero. El
aire entre paneles es de 16 px en las dos vistas y en todos los anchos
medidos (390, 768, 1024, 1280 y 1600), sin desborde horizontal.

## 2026-09-20 — Alineación con el prototipo E, 2 de 4: «Por envase»
Contexto: el panel «Por envase» del tablero se leía distinto al prototipo en
cuatro puntos: mostraba tres envases donde el prototipo muestra cuatro,
escribía «26.000–26.000 CUP» cuando todos los anuncios piden lo mismo,
repetía la moneda en cada fila, y el subtítulo hablaba de «envase completo»
sin decir cuál es la unidad que NO se está cobrando, que es justo la
confusión que el panel existe para deshacer.
Decisión: (1) `CardView.format_span_text` escribe una sola cifra cuando el
mínimo y el máximo del envase coinciden, y el rango cuando difieren. No
alcanzaba con `is_single_observation`: nueve anuncios pidiendo 26.000 son
nueve observaciones y un solo precio, y el guión inventaba una dispersión que
no se observó. (2) La moneda sale de las filas y pasa al encabezado de la
columna («Precio del envase (CUP)») y al subtítulo: el panel es de un
producto y su moneda es una sola, así que decirla doce veces es ruido. La
excepción es la fila sin envase declarado, que no es un envase sino el precio
por unidad y conserva su «/lb» a la vista para no quedar bajo un encabezado
que no le corresponde. (3) El subtítulo pasa a «Lo que se paga por el envase,
en CUP (no por libra)», con la unidad escrita en palabra. Para eso se agrega
`UNIT_NOUNS` y `unit_noun()` en `saimc.units`: `UNITS` nombra lo que se
almacena, esto nombra lo que se lee, y vive al lado y no en la plantilla
porque es vocabulario del dominio. Fuera del vocabulario devuelve `None` y
quien lo muestra cae al valor canónico: se lee peor, pero no miente. (4) El
corte del desplegable pasa de tres envases a cuatro, que es lo que muestra el
prototipo antes de «Ver los N envases de X».
Descartado: (a) formatear el rango en la plantilla con un `{% if %}`: la
regla «mínimo y máximo iguales son un precio, no un rango» es del dato, no
del HTML, y se prueba sola en `card_view`; (b) derivar el nombre de la unidad
con un diccionario dentro de la plantilla, que lo dejaría fuera del alcance
de los tests y duplicado en la próxima vista que lo necesite; (c) dejar la
moneda en cada fila «por si acaso un producto tuviera dos»: no puede tenerlas
en el mismo panel, porque `_chart_for` ya elige un solo gráfico —una moneda y
una unidad— por producto.
Consecuencia: el precio del envase y el de la mediana quedan en la misma
escala y sin moneda repetida, así que la tabla se puede leer en columna. Si
algún día un producto se publicara en dos monedas, el encabezado diría solo
una: el corte está en `_chart_for`, no acá, y habría que partir el panel.
Se aplican además las tres observaciones del repaso del slice A. (i) El
desempate de orden compara la letra base, sin tildes: comparar el texto crudo
mandaba «Ñame» detrás de «Pollo» —la Ñ es U+00D1 y la P es U+0050— y separaba
«Ají» de «Ajo». Queda con las enes, que es donde se lo busca; no es la
ordenación completa del español, donde la Ñ es letra propia después de la N,
pero no manda ningún nombre a un lugar donde nadie lo buscaría. (ii) La prueba
de la grilla de una columna mira todo el CSS anterior a los 64 rem, no el
primer bloque: así también cubre el tramo de 48 a 64 rem, donde «Todos» sigue
sin menú. (iii) Queda probado que `has-nav` se emite bajo la misma condición
que dibuja el menú, comparando las dos vistas renderizadas: la clase y el
`<nav>` aparecen juntos o no aparecen.

## 2026-09-20 — Por qué el panel «Por envase» tiene una sola moneda
Contexto: la entrada de hoy «Alineación con el prototipo E, 2 de 4» justificó
sacar la moneda de cada fila diciendo que un producto «no puede tenerlas en el
mismo panel, porque `_chart_for` ya elige un solo gráfico —una moneda y una
unidad— por producto». El repaso del slice preguntó qué pasa si una tarjeta
trae otra moneda que la del encabezado, y al ir a mirarlo la razón escrita no
se sostiene: `_chart_for` gobierna `fila` —la fila de la lista de productos,
de donde sale el encabezado—, no las tarjetas del panel, que salen de
`build_cards` y se agrupan por (producto, provincia, moneda, unidad).
Decisión: la conclusión se mantiene y el motivo se reemplaza. El panel tiene
una sola moneda porque `build_cards` descarta las filas `unconverted`
(`card_view.py`, «moneda extranjera cuya tasa no se resolvió») y `_resolve_row`
funde en CUP todo lo que sí se pudo convertir. Lo que sobrevive es CUP, sin
excepción. Queda probado por `test_el_panel_nunca_mezcla_dos_monedas`, que
siembra un anuncio en USD sin tasa junto a los de CUP y verifica que no llega
al panel y que el encabezado nombra una sola moneda.
Descartado: (a) dejar la razón vieja, que da la respuesta correcta apoyada en
el mecanismo equivocado y se cae sola el día que alguien toque `_chart_for`;
(b) imprimir la moneda en la fila cuando difiere de la del encabezado, que fue
lo primero que escribí: es una rama que no puede ejecutarse nunca, y este
proyecto muere por sobreingeniería antes que por falta de defensas.
Consecuencia: si algún día se publicara una tarjeta sin fundir a CUP —una tasa
resuelta por otra vía, o el filtro `unconverted` relajado—, el test cae y ahí
sí hay que devolverle la moneda a la fila. El encabezado es correcto mientras
ese filtro esté en pie, y el test es lo que lo sostiene.

## 2026-09-21 — Alineación con el prototipo E, 3 de 4: los gráficos
Contexto: «Cómo se movió» y «Cuánto varía» se leían distinto al prototipo, y
los dos parciales (`_steps_chart.html`, `_province_compare.html`) los comparte
el mapa, así que cada arreglo vale para las dos vistas. Al medir aparecieron
cinco diferencias y una mentira.
Decisión: (1) La mentira primero. La nota decía «La última semana llega hasta
el {fin de la ventana}». Con el último anuncio del 07/09 y la ventana cerrando
el 19/09, prometía doce días de dato que no existen. Ahora `StepChart` lleva
`last_day` —el día del último anuncio del producto dentro de la ventana— y la
nota dice ese. (2) Una fecha debajo de cada período en vez de solo el
principio y el fin de la ventana, que no marcaban ningún punto del dibujo. Se
ralean cuando no entran: con 90 días son trece períodos y «28/07» mide más que
uno, así que se cuenta hacia atrás desde el final y el último nunca se cae.
(3) Se fueron las etiquetas al final de cada línea: con dos líneas que
terminan cerca se pisaban, y pedían 70 px de margen derecho para 12 que hacen
falta. La leyenda —que ya estaba— queda como el único lugar donde se dice de
quién es cada tinta, y por eso deja de esconderse cuando hay una sola
provincia. El dibujo gana 58 px. (4) El tamaño del punto pasa a decir cuántos
anuncios sostienen esa mediana. La nota ya lo prometía y el radio estaba fijo
en 3: era otra cosa que el pie decía y el dibujo no hacía. Crece con la raíz,
porque lo que el ojo compara es el área. (5) A la derecha de cada provincia en
«Cuánto varía» va la mediana escrita y no «44 anuncios»: es la cifra que se
viene a buscar, y leerla del eje es trabajo. Cuántos anuncios la sostienen ya
está en la tarjeta de arriba y sigue en el `title` de la fila. (6) La leyenda
de «Cuánto varía» pasa debajo del dibujo, como el prototipo; la de «Cómo se
movió» se queda arriba.
Descartado: (a) rehacer la geometría del gráfico de pasos como la del
prototipo, que ancla los puntos y no dibuja el escalón a lo ancho del período:
el escalón es más fiel a «mediana por semana» y no era lo que se pidió
alinear; (b) dejar «N anuncios» a la derecha y agregar la mediana: son 74 px
de margen para repetir un dato que ya está dos veces en la pantalla; (c)
ralear las fechas con un número fijo de etiquetas: depende del ancho del
período, no de cuántos haya.
Consecuencia: `_province_compare.html` pasó a pintar cada fila con la tinta de
su provincia (`CompareRow.slot`). No estaba en la lista, pero al poner la
leyenda debajo quedó a la vista que era una leyenda de colores sobre un
dibujo de un solo color. En el mapa hay más provincias que tintas y el color
se repite; no confunde porque ahí cada fila lleva su nombre a la izquierda.
Medido en el navegador: sin desborde a 390 y 1280, y la mediana más ancha que
hay hoy («3.476», de Aceite) no se sale del dibujo ni toca la línea de su
fila —0 px de desborde, 29 px de aire—.

## 2026-09-21 — Alineación con el prototipo E, 4 de 4: textos y dos apartamientos
Contexto: quedaban dos viñetas de «Sobre los datos» que no decían lo que dice
el prototipo, y dos cosas que el prototipo no trae y el tablero sí: el rótulo
«muestra en formación» y el pie de atribución del shell. El operador autorizó
sacarlas del tablero.
Decisión: (1) La viñeta de la mediana pasa a «La cifra es la mediana en CUP
por libra o por litro», como el prototipo. La frase no es un literal: la arma
`Watchlist.medida` con la moneda y las unidades que hay en pantalla, y si
hubiera dos monedas se calla en vez de escribir una sola y mentir. (2) La
viñeta de la base pierde la coletilla «“sin mes anterior”, que no hubo
ninguno». (3) **Apartamiento de §6.5**: el pie de atribución no se dibuja en
`?vista=tablero`. El pie del shell pasó a ser `{% block footer %}` y esta
vista lo deja vacío. §6.5 pide que todo dato publicado conteste de dónde
salió, cuándo, qué procesos atravesó y por qué se confía: eso lo contesta el
panel «Sobre los datos», que ya estaba y ahora además se queda con el crédito
a elTOQUE. (4) **Apartamiento del 2026-08-22**: el rótulo «muestra en
formación» se va de las dos pantallas del tablero. Lo pedía aquella entrada,
no el texto de §9.0; lo vinculante de §9.0 (línea 1242) es que la interfaz
diga qué provincias están publicadas y cuáles en ingesta y que la ausencia se
lea como decisión metodológica, y eso lo sigue diciendo la viñeta «N de 16
provincias tienen datos. Ninguna está publicada todavía…».
Descartado: (a) apagar el pie entero incluido el crédito a elTOQUE, que era lo
pedido al pie de la letra: es un crédito a un tercero por la tasa con la que se
convierte a CUP lo que esta misma página muestra, y una maqueta no es motivo
para dejar de darlo. Se mudó a una viñeta; (b) dejar el rótulo «una vez por
vista» en chico, que es lo que había: seguía siendo un renglón que el
prototipo no trae y que la viñeta ya cubre; (c) escribir «en CUP» fijo en la
plantilla, que es cierto hoy sólo porque `build_cards` descarta lo no
convertido (ver la entrada de ayer) y sería falso el día que eso cambie.
Consecuencia: los dos apartamientos están acotados a `?vista=tablero` y
anotados en `docs/spec-map.md`. El mapa y el resto de la capa pública
conservan el pie entero y el rótulo, y hay un test por cada mitad: que el
tablero no los tiene y que el mapa sí. Si mañana se quisiera apagar el pie en
el mapa, el crédito a elTOQUE y la geometría de Natural Earth necesitan otro
lugar antes.

## 2026-09-21 — Los tres hallazgos del repaso del slice D
Contexto: el repaso del slice anterior devolvió un aviso y dos sugerencias, y
el operador pidió arreglar los tres.
Decisión: (1) El `{% block footer %}` vacío es de toda la plantilla, así que
apaga el pie en las dos pantallas del tablero, pero sólo la de lista probaba
que lo que lo compensa —el crédito a elTOQUE y la viñeta de §9.0— sigue en su
lugar. Las dos pruebas recorren ahora las dos URL. Quedó confirmado de paso
que «Sobre los datos» se dibuja también en el detalle, que era justo lo que el
repaso no podía ver desde el diff. (2) `Watchlist.medida` filtraba por
`r.unit` y escribía `r.unit_noun`. Hoy son equivalentes porque `unit_noun` cae
al valor canónico, así que el «en CUP por  o por libra» que el repaso describe
no se puede alcanzar; pero filtrar por una cosa y formatear otra es una trampa
puesta para el futuro, y ahora filtra por lo que escribe. El invariante que
los hace equivalentes queda fijado en `test_hay_palabra_exactamente_cuando_hay_unidad`.
(3) La entrada anterior decía «hay un test por cada mitad»; era cierto para el
pie y no para el rótulo, que sólo se probaba por ausencia en el tablero. Se
agrega la mitad que faltaba: que «muestra en formación» sigue estando en el
mapa.
Descartado: inventar una fila con `unit` lleno y `unit_noun` vacío para probar
el caso (2): es un estado que el código no produce, y un test así prueba una
ficción. Se fija el invariante que lo vuelve imposible, que es lo que de
verdad hay que cuidar.
Consecuencia: el aviso del repaso era una cobertura ausente y no un defecto
—las aserciones nuevas pasaron a la primera, así que el detalle ya estaba
bien—, y ahora está cubierta.

## 2026-09-21 — Los tres hallazgos del segundo repaso del slice D
Contexto: el repaso del candidato ya corregido devolvió otros tres, y uno era
una regresión que había metido yo.
Decisión: (1) La regresión. Al poner la medida entre `{% if %}` sin `else`, la
viñeta quedaba en «La cifra es la mediana: la única comparable» justo cuando
`medida` se calla —dos monedas—, que es precisamente cuando avisar que cada
cifra trae la suya es lo correcto. Vuelve la rama neutra «, con su moneda y su
unidad», que es lo que la viñeta decía antes de este slice. (2) La medida sólo
se probaba en un estado. Ahora se prueba en las dos pantallas y con dos
unidades, que es la frase exacta del prototipo («en CUP por libra o por
litro») y que la semilla no daba. (3) La prueba del invariante recorría ocho
unidades escritas a mano y comparaba contra `bool(unidad)`, que dentro del
bucle es siempre verdadero: media aserción no decía nada. Ahora recorre
`UNITS`, así que la unidad que se agregue al catálogo entra sola.
Descartado: probar la rama neutra renderizando la página. Con dos monedas no
se llega: `build_cards` descarta lo no convertido, así que no hay semilla real
que la dibuje. Se probó a nivel de propiedad —`medida` devuelve vacío con dos
monedas— y se descartó además un test que afirmara el texto de la plantilla:
pasa por mirar el código, no por lo que la página hace, y este repositorio ya
tiene bastante de eso.
Consecuencia: la rama neutra es una red para un estado que hoy no se alcanza.
Queda explícito acá para que nadie la lea como código muerto: el día que una
tarjeta llegue sin fundir a CUP, la viñeta sigue siendo cierta en vez de
quedarse muda.

## 2026-09-21 — «Todos» pasa de tarjetas a una lista compacta con buscador
Contexto: con 5 productos las tarjetas del prototipo E se ven bien, pero cada
una mide 175 px en el teléfono, y el catálogo ya tiene 60 productos con 5
marcados como piloto. Medido sobre una base sintética de 40 productos piloto
(la misma para los dos códigos): 8.788 px en el móvil, 10,4 pantallas de
scroll para encontrar uno. E había sacado la búsqueda por ruido —con 5
productos sobraba—; con 40 hace falta, y el operador pidió volver a la lista
compacta y al buscador que ya estaban diseñados en D.
Decisión: (1) La lista de «Todos» es la de D: una fila por producto, con la
cabecera de columnas una sola vez —pegada arriba al hacer scroll, porque una
columna sin nombre no dice de quién es la cifra— en vez del nombre de la
provincia repetido en cada tarjeta. La fila conserva todo lo que E le pedía a
la tarjeta: mediana, «más barata», cambio con su «(base N)», cuántos
anuncios, «sin datos todavía», «sin mes anterior» y el modo «Mi provincia»
con las otras en una línea. D no traía «(base N)» ni el conteo en la fila
(decía «poca base antes»); se agregaron porque son las dos honestidades que E
introdujo, y una lista más corta no es motivo para perderlas. Las cifras van
alineadas por el borde derecho y el nombre del producto en sans de 14 px,
como en D: alinear a la derecha es lo que deja comparar hacia abajo, y solo
funciona si la cifra está siempre en el borde, así que la pastilla «más
barata» va a su izquierda (`row-reverse`; en el HTML la cifra sigue primero,
que es como la lee un lector de pantalla). (2) Buscador: filtra en el
navegador las filas que ya llegaron, plegando tildes y mayúsculas —«azucar»
encuentra «Azúcar blanca», «name» encuentra «Ñame»— y buscando varias
palabras en cualquier orden. El nombre viene plegado del
servidor (`WatchRow.busqueda`, la misma función que ordena la lista) y el
script pliega la consulta igual. Nace `hidden` y lo muestra el script: sin
script no hay nada que lo haga andar, y un campo que no hace nada es peor que
no tenerlo. El script solo viaja a «Todos». (3) El chevrón y su columna solo
desde 48 rem: en el teléfono cada píxel de ancho es de una cifra, y las
filas de D tampoco lo traen. (4) El resumen del detalle sigue usando la
tarjeta de E (`columna`), sin cambios.
Medido (390 px de ancho y 1280, misma base): con 40 productos, de 8.788 a
3.961 px en el móvil (10,4 a 4,7 pantallas) y de 6.587 a 3.029 px en
escritorio (7,3 a 3,4). Con los 5 de hoy, la lista de 940 a 357 px y la
página de 1.846 a 1.436 px en el móvil (en escritorio, la lista de 764 a
312). Sin desborde horizontal en ningún ancho, y el buscador probado en el
navegador con 40 filas.
Descartado: (a) tarjetas hasta N productos y filas después: dos diseños que
mantener y probar, y la vista compacta no se vería en producción con 5
productos, así que no se podría verificar; forzarla con `?lista=` obligaba a
pasar el parámetro por las 11 funciones de `product_nav` que arman enlaces
(20 llamadas, compartidas con el mapa); (b) buscar también por provincia
(«Buscar producto o provincia», como en D):
en E la provincia se elige con los chips de «Mi provincia», que enfocan la
lista, y buscarla duplicaría el control sin un resultado que mostrar. El
campo dice «Buscar producto»; (c) buscar en el servidor con `?q=`: una ida y
vuelta por consulta con la conexión de un usuario cubano, contra un filtro
instantáneo que no agrega superficie al router; (d) agrupar por categoría:
entrada siguiente.
Consecuencia: es un apartamiento de E, elegido por el operador tras ver el
costo medido, y la tarjeta de E deja de existir en «Todos». La tabla asume
pocas provincias sin foco (hoy 2): con más, la lista debería arrancar
enfocada en «Mi provincia» (S8), que es como ya escalaban las tarjetas. Las
15 pruebas que dependían de las clases de la tarjeta se reapuntaron sin
cambiar lo que afirman, y una que habría quedado en verde para siempre
(«las tarjetas no aparecen en el mapa», sobre una clase que ya no existe) se
reapuntó también. Cada regla de CSS y cada pieza del buscador se probó
rompiéndola a propósito: 15 mutaciones, 15 cazadas.

## 2026-09-21 — Agrupar la lista de productos por categoría: anotado, no construido
Contexto: al pedir la lista compacta se planteó agrupar por categoría, con
secciones plegables sin JavaScript, como segunda salida al scroll con muchos
productos. Hoy no sirve: de los 60 productos del catálogo, 55 tienen
`category = 'sin categoría'`, y también 2 de los 5 pilotos (Picadillo y
Azúcar blanca). Las únicas categorías con contenido son `granos`, `aceites`,
`carnes`, `lacteos` y `huevos`, con un producto cada una.
Decisión: no se construye ahora. Se deja anotado con su condición: primero se
cura el catálogo, que es trabajo de datos y no de código; después el agrupado
es poco código —`build_watchlist` agrupa las filas por `Product.category` y
cada grupo va en un `<details open>`—, y la lista compacta ya lo admite sin
rediseñar nada.
Descartado: agruparlo hoy. Con 55 de 60 en «sin categoría» habría un bloque
enorme y un encabezado que no dice nada: se pagaría el código para no ganar
nada.
Consecuencia: el disparador para reabrirlo es más de ~15 productos piloto y
al menos 3 categorías con 3 productos o más; hasta entonces el buscador y la
fila compacta cubren el volumen. Quedan dos cosas por decidir cuando se abra:
qué hace el buscador con los grupos plegados (lo natural es abrir los que
tienen coincidencia) y cómo llega la categoría a `WatchRow`, que hoy no la
lleva —`build_watchlist` recibe los nombres piloto, no la categoría—. Aparte
del agrupado, curar la categoría de los 55 productos se va a pagar en más
lugares que este.

## 2026-09-21 — Los hallazgos del repaso de la lista compacta
Contexto: el repaso de la lista compacta y el buscador devolvió nueve
hallazgos —cinco avisos y cuatro sugerencias— y ninguno bloqueante. Se
verificó cada uno contra el código antes de tocar nada: seis eran reales, uno era
infundado pero barato de cubrir, uno era un artefacto de cómo se le pasó el
código al revisor y uno se acepta.
Decisión: (1) Real: con la lista vacía la página mostraba «Ningún producto
coincide… para ver los 0» al cargar, sin que nadie hubiera escrito, porque
cero filas visibles se leía como «sin resultados». Ahora sin filas no hay
campo, ni mensaje, ni script, y el script solo muestra el vacío cuando hay una
consulta. (2) Real: `test_ya_no_hay_cinta_ni_tiras_ni_busqueda_ni_columna_de_cambio`
decía «ni búsqueda» junto a un buscador nuevo. Se renombró y se explicó que la
búsqueda volvió con otro marcado; lo que vigila es que no vuelva el de S5.
(3) Real: `is-foco` seguía saliendo en cada fila y en una prueba, pero su única
regla se borró con la tarjeta. Se quitó la clase y la variable `foco`, que solo
la usaba. (4) Real: el guardia que impide un `display` sobre lo que esconde
`hidden` solo miraba el selector exacto y no habría visto
`.ph-lista .ph-buscar { display: … }`. Ahora mira toda regla que nombre la
clase, y una prueba del propio guardia lo comprueba contra el CSS real.
(5) `estado` se usaba sin la guarda que sí tenían `vacio` y `limpiar`. (6) El
vacío decía «para ver los N»: con un solo producto, «los 1». Ahora dice «para
ver todos». (7) El bloque `scripts` no llamaba a `super()`. El del padre está
vacío hoy, así que el aviso era infundado, pero se agregó igual: la vista que
sume HTMX u otro script lo hace desde ese bloque y no debe llevárselo puesto.
(8) Artefacto: el revisor leyó que el script escribe su expresión regular con
los caracteres combinantes literales y que por eso una prueba no podía
coincidir. No es así: el archivo tiene las secuencias de escape Unicode
escritas en ASCII —verificado en disco, y la prueba que «no podía coincidir»
pasa—. Quien las decodificó fue el transporte al armar el prompt del revisor;
el propio revisor lo advirtió («si el relevo alteró el escape, este hallazgo se
disuelve»). Se conserva su resultado tal cual, sin editar. (9) Aceptado: el
comportamiento del script no tiene prueba automática de ejecución, y las tres
pruebas que lo tocan afirman texto de fuente. Es la práctica del repositorio,
que no ejecuta JavaScript en sus pruebas —los otros dos scripts del sitio
tampoco—; se verificó a mano en Chromium, con 40 filas: consulta vacía y solo
de espacios (muestran todo, sin mensaje), «azucar» (2), «ñame» y «name» (1),
«jamon» (1), «zzz» (0, con el mensaje y «Borrarla», que limpia, vuelve a 40 y
devuelve el foco al campo), la cabecera pegada al bajar a la fila 21, y el
catálogo vacío.
Descartado: sumar un motor de JavaScript (Node, QuickJS) o Playwright a las
dependencias de desarrollo por quince líneas de script: es una dependencia y
un modo de fallo nuevos para algo que ya se comprobó a mano. Si el script
crece, el cálculo cambia.
Consecuencia: 22 mutaciones, todas cazadas —las 15 de antes y siete nuevas,
una de ellas el selector compuesto que el guardia viejo no veía—. Lección para
el próximo relevo: el transporte decodifica las secuencias de escape Unicode
de cualquier texto que se le pasa a una herramienta, así que un candidato que
las contenga llega alterado al revisor; hay que avisarle o pasarlas sin
escribirlas.

## 2026-09-21 — Mapa: ventana con pestañas por producto y capa «precio» (prototipo B)
Contexto: el operador sentía el mapa de `/` «muy técnico, poco llamativo» y pidió, con los chips de producto ya elegidos, ver el precio sobre el mapa y una tabla con sparklines al estilo AG Grid. Se hicieron cinco prototipos en Claude Design (A: líneas sobre el mapa; B: ventana con pestañas; C: las dos; y dos móviles) y eligió B.
Decisión: (1) `province_grid.py` arma, por producto activo, una fila por provincia con la mediana y el cambio de `build_month_vs_month` y una sparkline (línea, área y barras) de las medianas por período de `build_median_steps`: ninguna cifra nueva, para que tabla y «Cómo se movió» no se contradigan. (2) La pila de un panel por producto pasó a UNA ventana con una pestaña por producto (`ph-tabbed`, con × por pestaña); lo que decía cada panel sigue dentro del desplegable «Detalle por provincia y formato». Cambiar de pestaña, ordenar la tabla y elegir línea/área/barras son del cliente, sin ida y vuelta al server. (3) Capa nueva `?capa=precio`, la default cuando hay algún producto abierto (`effective_layer` la baja a «publicación» sin producto): cada provincia se tiñe entre `--ph-price-low` y `--ph-price-high` según su mediana del producto de la pestaña abierta, relativa a las provincias con dato del propio producto. Quien quiera la de siempre la pide y viaja como `?capa=publicacion`; la default es la única que no se escribe en la URL. (4) El mapa se encuadra en lo que deja libre la ventana (`zoomSnap: 0.25`, `mix-blend-mode: normal` en los tiles para que el zoom fraccionario no pinte rayas blancas).
Descartado: (a) tiñe por verde/amarillo/rojo contra el promedio nacional, como describe §6.7: exige decir qué es «caro», y §12.6 prohíbe inventar el umbral; además una baja es buena noticia para quien compra y mala para quien vende, por eso el cambio sigue sin flecha ni color. El tinte es de un solo matiz y solo compara. (b) La «mediana nacional» del prototipo: promediar medianas provinciales es una estadística que la aplicación no calcula en ningún otro lado; el titular es el rango entre la provincia más barata y la más cara. (c) Las filas «Sin datos» del prototipo: con datos reales solo unas pocas provincias tienen anuncios y la tabla quedaba casi vacía; las demás se nombran en una línea al pie, como ausencia de dato (§9.0) y no como cero. (d) Superponer las líneas sobre las provincias (A y C): a 390 px las provincias del oeste miden menos de 10 px y no caben.
Consecuencia: el mapa deja de ser «solo cobertura» (`map_view.py` y un test lo decían) y pasa a tener una capa que toca el precio; `DEFAULT_LAYER` es ahora «precio». Las tarjetas nacionales del prototipo B no se implementaron (dependían de la mediana nacional). Quedan las insignias del mapa por provincia, que repiten la cifra de la tabla: decisión abierta del operador. El JavaScript nuevo (pestañas, orden, tinte, encuadre) no tiene prueba automática —el repositorio no ejecuta JS en pytest— y se verificó a mano en Chromium con una base sintética: claro y oscuro, provincia elegida, escritorio y móvil, sin errores en consola.

## 2026-09-21 — Panel de precios anclado a un lado, no flotante (reemplaza la parte «ventana flotante» de la entrada anterior)
Contexto: el operador vio la ventana con pestañas ya implementada y no lo convenció que flotara; pidió algo como dash.gallery/dash-uber-rides-demo: el mapa y un espacio fijo que cuenta lo seleccionado. Se investigaron ejemplos reales (Google Maps, Airbnb, HungerMap, Our World in Data, OSM, Dash, Material Design 3) antes de decidir.
Decisión: el panel es un `<aside class="ph-side ph-tabbed">`, hijo del lienzo y no del muelle de ventanas: sin arrastrar, minimizar ni redimensionar. En escritorio es una columna a la derecha, a altura completa, de `--ph-side-w` (30–36rem, hasta el 32 % del ancho) que ENCOGE el mapa (`.has-side`) y se pliega con un botón —queda una pestaña «Precios» en el borde para reabrirla; el plegado se recuerda en `sessionStorage`—. Leaflet se encuadra en lo que queda libre (`encuadrarMapa()` con `invalidateSize`), así que dejó de haber cuenta de relleno y el oeste ya no queda bajo el panel. En angosto (≤720 px) es una hoja inferior con dos alturas —vista previa y «Ver todas»— con un botón y no solo arrastrando (Material Design 3 pide una alternativa a la manija); el mapa se encuadra arriba de la vista previa. Con una provincia elegida, su detalle (`ph-pane-mas`) llega abierto al pie de la columna. `--ph-side-w` y `--ph-bar-h` son tokens de medida en `design_tokens.css`.
Descartado: (a) dejar el mapa a todo el ancho y el panel encima con relleno en `fitBounds`, como HungerMap: no encoge el mapa pero deja provincias del oeste tapadas y exige cuentas de encuadre; (b) hoja móvil arrastrable con tres alturas: más JavaScript y más accesibilidad por resolver que «vista previa + Ver todas»; (c) anclar también las ventanas del menú (Cobertura, Precios, Cómo se movió): dos lógicas de layout es un costo real y no lo pidió; siguen flotando.
Consecuencia: Cuba se ve más chica en escritorio (el mapa pierde ≈33 % del ancho con el panel abierto; con el panel plegado ocupa todo). En angosto la barra de filtros de abajo mide unos 160 px en tres filas y la hoja se apoya encima (`--ph-bar-h`, medido por el script). Las insignias del mapa siguen repitiendo la cifra de la tabla: decisión abierta. Igual que antes, el JavaScript no tiene prueba automática; se verificó a mano en Chromium (escritorio, plegado, provincia elegida, 1100 px, móvil en vista previa y expandida, sin errores en consola).

## 2026-09-21 — «Detalle por provincia y formato» pasa a ser la fila expandible de la tabla
Contexto: el panel de precios tenía dos vistas de lo mismo: la tabla por provincia (mediana, cambio, tendencia) y, aparte, un desplegable «Detalle por provincia y formato» con una tarjeta por celda (cifra, rango por período y anuncios). El operador pidió aglutinarlo en una tabla, al estilo de AG Grid (filas que se agrupan y se expanden).
Decisión: cada fila de provincia lleva un botón que la expande (`ph-row-toggle`, `aria-expanded`); por dentro trae un bloque por formato con lo que antes era la tarjeta —cifra con su muestra, rango por período con `_range_chart.html` y los anuncios detrás con su enlace (§6.5)—. La fila sigue comparando provincias con los formatos fundidos (`price_charts._by_product`) y el detalle los separa: son dos preguntas distintas y la separación por formato del 2026-09-14 se conserva. `province_grid.group_detail` arma los bloques con las observaciones de TODAS las provincias, aunque haya una elegida: la elegida llega expandida y el resto se puede abrir; sin JavaScript todo se ve abierto (`.ph-js` es quien cierra, mejora progresiva). Ordenar la tabla mueve cada fila con su detalle. Sin dependencias nuevas.
Descartado: (a) Dash AG Grid, medido con una página mínima: ≈857 KB comprimidos de JS contra ≈71 KB de todo el JS+CSS estático de la home (12 veces más; 3,4 MB sin comprimir), agrupar filas, fila expandible y sparklines son Enterprise (de pago, con marca de agua sin licencia), y exige montar Dash (Flask + React) junto a FastAPI + Jinja + HTMX, contra la decisión cerrada de una sola pila y con un viaje al server por interacción; (b) Tabulator (≈102 KB comprimidos, MIT): trae filtros por columna, columnas ancladas y virtualización, que hoy no hacen falta con decenas de filas; queda como plan B; (c) una tabla plana provincia × formato: mezclaba unidades al ordenar y repetía la mediana que la fila ya muestra.
Consecuencia: `panel.blocks` ya no se pinta en la plantilla (sigue alimentando las insignias del mapa). El HTML de cada carga lleva el detalle de todas las filas cerradas, como antes lo llevaban los desplegables; si crece con más productos, el paso siguiente es pedirlo por HTMX al expandir. Se verificó a mano en Chromium: expandir, ordenar con filas abiertas, provincia elegida y móvil, sin errores en consola.

## 2026-09-21 — «Explorar»: una vista de trabajo con productos como pestañas (plan aprobado y P1, los datos)
Contexto: tras la fila expandible el operador dijo que no le resultaba fácil de entender y pidió una tabla condensada con filtros y árbol, como las de TradingView o AG Grid. Se hicieron cinco prototipos en Claude Design; eligió C («Explorar datos») con una mejora —los productos se seleccionan y cada uno abre una pestaña en el panel central— y, en móvil, M2 (tabla con la primera columna fija). Aprobó el diseño y un plan de ocho partes (P1 datos, P2 esqueleto, P3 tabla, P4 productos y pestañas, P5 filtros, P6 panel derecho, P7 móvil, P8 cierre de fidelidad), con una regla nueva: cada parte se cierra con una matriz de fidelidad contra el prototipo, capturas lado a lado a 1440 y a 390 px y la lista de desvíos, porque implementaciones anteriores se apartaron del diseño aprobado sin decirlo.
Decisión: (D1) vive en una vista nueva `?vista=explorar` y el selector pasa a Mapa | Tablero | Explorar; el mapa y el tablero aprobado no se tocan. (D2) la tendencia y el calor semanal miran siempre 13 semanas hacia atrás desde hoy, con series propias por formato; mediana, cambio y muestra siguen la ventana. (D3) el mapa del panel derecho es un SVG estático que sale de `cuba_provincias.geojson` (`cuba_svg.py`), sin Leaflet ni tiles. (D4) las filas salen del servidor y filtrar, ordenar y expandir se hace en el cliente con atributos `data-*`; cada producto se pide por HTMX al abrir su pestaña. (D5) el móvil es fiel a M2, sin mapa. P1 deja los datos: `explore_view.py` arma por producto el árbol provincia → formato → anuncios con la misma regla de cifra del resto de la aplicación (la fila de provincia funde los formatos como la lista del tablero, el detalle los separa como el panel del mapa; cerco robusto, cambio sin flecha ni color, sin «mediana nacional»), y `tools/explorar_arbol.py` imprime ese árbol de la base real y lo contrasta con el tablero y con el panel del mapa.
Descartado: (a) AG Grid / Dash AG Grid y Tabulator (entradas anteriores: peso, licencia y una segunda pila); (b) serializar todo a JSON y dibujar la tabla con plantillas de JavaScript: duplica en el cliente el reparto «la estructura se arma en Python y la plantilla la recorre» y no se puede probar con pytest; (c) que la tendencia siga la ventana: con 30 días son cinco semanas y la sparkline no dice nada.
Consecuencia: P2–P8 pendientes; ninguna afecta al mapa ni al tablero hasta que el operador lo decida en P8 (el panel de precios del mapa sigue como está). Los anuncios de julio dibujan la tendencia de agosto pero no cuentan en la muestra de la fila. Sobre datos con forma real (base sintética de 598 filas) cada fila de provincia coincide con la lista del tablero y cada fila de formato con el panel del mapa.

## 2026-09-25 — «Explorar» P4: pestañas por HTMX, orden estable y `?pestana=`
Contexto: D4 (entrada anterior) decía «cada producto se pide por HTMX al abrir su pestaña», pero P2/P3 solo navegaban con `<a href>` de siempre y, al tocar una pestaña ya abierta, la ponía primera en `?productos=` — mezclaba «orden de apertura» con «cuál se ve», algo que el prototipo (revisado en su script) nunca hace: mantiene `sel` (la lista, orden de apertura) y `act` (la que se ve) como dos estados separados.
Decisión: (1) separar los dos ejes en la URL: `?productos=` es ahora la lista ESTABLE (se agrega al final al abrir, se saca en su lugar al cerrar, nunca se reordena) y `?pestana=<slug>` dice cuál se ve; sin él cae a la primera de la lista, igual que antes de P4. `build_explore_nav` ganó un parámetro `active_slug` y `ExploreTab.is_first` pasó a `is_on` (ya no significa «primera», significa «la que se ve»). Cerrar la pestaña activa activa a su vecina (la que ocupaba su lugar, o la anterior si era la última); cerrar cualquier otra no mueve la aguja. (2) el cableado HTMX reusa el mecanismo que ya tiene `public_home.html` desde el slice 4 (`#ph-live` con `hx-target/hx-select/hx-swap="outerHTML"/hx-push-url`, heredado por cualquier `hx-get` de adentro) en vez de inventar un endpoint de fragmentos: se envolvió `.ph-ex` con `id="ph-live"` y se agregó `hx-get="{{ misma URL que el href}}"` a los enlaces de abrir/cerrar/cambiar de producto (checklist, pestañas, ×, «+», pastillas del estado vacío). Un producto que no estaba se pide al servidor; uno ya abierto también —no hay «pestañas vivas en el DOM que se ocultan», el árbol de esa pestaña se vuelve a pedir y arranca en su estado por defecto (una simplificación declarada, ver Descartado). (3) el JS de P2/P3 (pestañas Filtros/Columnas, árbol, orden, columnas) se reescribió como escuchas delegadas en `document`, registradas una sola vez: como el swap de htmx reemplaza `#ph-live` entero, cualquier estado que viviera en una variable de módulo (la tabla capturada, el sentido de orden) quedaba huérfano tras el primer swap; ahora todo se relee del DOM en cada clic (el sentido de orden sale de `data-s` del encabezado, no de una variable). CSV, la fila de provincia (P6) y el selector Mapa/Tablero/Explorar NO llevan `hx-get`: siguen fuera de alcance de P4.
Descartado: (a) mantener en el DOM el árbol de cada pestaña ya visitada (ocultarla en vez de re-pedirla) para que reabrir una pestaña conserve exactamente sus filas abiertas y su orden, como hace el prototipo (mock en memoria, sin red): exige que el panel de filtros y el derecho —hoy ligados a un solo producto «activo»— también vivan por pestaña, o un fragmento con varios `hx-swap-oob` por respuesta; con filtros todavía inertes (P5) no se justifica la complejidad. Declarado como diferencia con el prototipo: cambiar de pestaña y volver reinicia el orden y las filas abiertas de esa pestaña a los valores por defecto del servidor. (b) endpoint de fragmento propio devolviendo solo el `<div class="ph-ex-pane">`: exige entonces `hx-swap-oob` aparte para el panel de productos (estado marcado/desmarcado), el de filtros y el derecho, que TAMBIÉN cambian al abrir una pestaña nueva; el patrón `#ph-live` ya resuelve las cuatro regiones en una sola respuesta, gratis, porque ya las arma `_render_explore`.
Consecuencia: `products_query`/`_query` (`product_nav.py`) ganaron un `pestana: str = ""` opcional, de efecto nulo para mapa/tablero (se omite si está vacío). `public_home()`/`_render_home()`/`_render_explore()` ganaron el mismo parámetro, threaded sin tocar otras vistas. Verificado a mano en Chromium (escritorio y 390 px): abrir un producto nuevo no recarga la página (cero `framenavigated` de navegación completa, solo `pushState`), activar una pestaña ya abierta no reordena la lista, cerrar la activa activa a la vecina, el árbol/orden/columnas siguen funcionando después de cada swap, el indicador (`#ph-loading`) recibe `htmx-request` durante el vuelo, sin errores de consola.

## 2026-09-25 — «Explorar» P5: filtros del panel izquierdo, del lado del cliente
Contexto: el panel izquierdo (provincia, formato, «Anuncios ≥», «|Cambio| ≥ %») y el buscador del centro estaban dibujados desde P2 pero inertes, con el filtrado prometido para esta parte del plan.
Decisión: todo el filtrado corre en el cliente, sobre las filas que ya trajo el servidor —sin ida y vuelta, sin estado en la URL—, mismo criterio que el árbol (P3) y las columnas: `aplicarFiltros()` relee provincia(s) marcada(s), formato(s) marcado(s), el texto normalizado del buscador (`norm()` del prototipo: minúsculas, NFD, `\p{M}` fuera) y los dos steppers, y decide qué `<tbody>` de provincia se oculta —una provincia pasa si además tiene AL MENOS un formato que pasa el filtro de formato (`p.kids.some(passKid)` del prototipo)—. Los formatos que no pasan se marcan con la clase `.ph-ex-fuera` (oculta por CSS Y por `hidden`, no solo una de las dos) y `pintarGrupo` —ya existente desde P3/P4— la respeta junto con el estado de expandido, así que filtrar no pierde qué provincia estaba abierta. Los steppers replican el paso y el tope del prototipo (`nmin` ±2 hasta 60, `dmin` ±1 hasta 30, con `data-paso`/`data-max` en el HTML en vez de hardcodeados en JS). Los chips y «Limpiar todo» se arman enteramente en JS (no hay nada que renderizar server-side cuando no hay filtros activos); «Ninguna provincia cumple los filtros. Limpiar filtros» aparece cuando el filtrado deja el `<tbody>` de la tabla vacío.
Descartado: (a) mandar los filtros a la URL y volver a pedir la tabla al servidor: son controles de refinamiento visual sobre datos ya en pantalla (misma categoría que ordenar o expandir), no un cambio de qué se mide; además la URL ya carga con `productos`/`pestana`/`provincia`/`desde`/`hasta` y sumar cinco ejes más la vuelve ilegible para compartir. (b) Filtrar formato ocultando SOLO con `hidden` sin la clase CSS: un test existente (`test_ninguna_clase_asignada_por_js_quedo_huerfana`, `test_static_assets.py`) exige que toda clase que el JS asigna tenga una regla en alguna hoja —atrapa justo el error de renombrar una clase en el script y no en el CSS—; `.ph-ex-fuera{display:none}` cumple la regla y de paso es una segunda línea de defensa si algo pinta la fila antes de que corra `pintarGrupo`.
Consecuencia: sin cambios en Python (`explore_view.py`, `routers/public.py` intactos): P5 es enteramente plantilla + JavaScript, sin prueba automática de la interacción en sí (pytest no ejecuta JS) — se cubrió con tests del contrato (`data-fname`, `name`/`value` de las casillas, `data-stepper`/`data-max`/`data-paso`/`data-dir`, contenedores de chips y de «sin resultados») y se verificó a mano en Chromium (escritorio y 390 px): provincia sola aísla su fila con su chip, subir «Anuncios ≥» filtra hasta vaciar la tabla, «Limpiar todo» restaura todo, buscar «habana» sin tilde encuentra «La Habana», el estado vacío aparece y su propio «Limpiar filtros» funciona, sin errores de consola. Como el resto de la interacción de esta vista (P4), cambiar de pestaña reinicia los filtros: no hay «recordar el filtro de cada producto» todavía.

## 2026-09-25 — «Explorar» P6: elegir provincia desde el mapa o la fila, sin recargar
Contexto: desde P2 elegir una provincia (fila de la tabla o mapa) navegaba con `<a href>` de siempre; P3/P4/P5 lo declararon fuera de alcance («panel derecho: P6»). El mapa, además, no tenía ningún enlace: el `<path>` era pura decoración con un `<title>` de tooltip.
Decisión: (1) la fila de provincia (`.ph-ex-nmt`) suma `hx-get` igual a su `href` —mismo patrón `#ph-live` de P4/D4, sin cambios en Python: `_render_explore` ya recalculaba todo el panel derecho a partir de `?provincia=`, solo faltaba no perder la página completa en el viaje—. (2) cada provincia CON dato del mapa queda envuelta en `<a class="ph-ex-provlink" href="{{ province_link(c.slug) }}" hx-get="...">` (SVG inline admite `<a>` alrededor de un `<path>`); las provincias sin dato (`is_nodata`) se quedan sin envoltorio, no clicables —no hay nada que mostrar—. (3) el resalte de «elegida» pasa de `stroke-width: 2` a `2.5` y se suma un resalte de hover puro CSS (`a.ph-ex-provlink:hover .ph-ex-prov`, sin JS ni estado) para que el mapa se sienta interactivo sin repetir la máquina de estados del prototipo.
Descartado: (a) que el clic en el mapa TAMBIÉN marque la casilla de esa provincia en el filtro de P5 (el prototipo lo hace: un solo `click` handler toca `provs` y `selNode` a la vez): acoplar el filtro —100 % del cliente, efímero— con la selección —server-side, en la URL— es una máquina de estados nueva para una comodidad secundaria; queda declarado como diferencia, no como pendiente. (b) resaltado por hover con JS y un `st.hover` compartido entre fila y mapa (el prototipo sincroniza ambos): CSS puro alcanza para «se ve interactivo al pasar el mouse» sin estado; sincronizar fila↔mapa en hover no se implementó.
Consecuencia: sin cambios en `explore_view.py` (los `MapCell`/`ExploreDetail` ya traían todo lo necesario); el único cambio de Python fue ninguno —P6 es plantilla pura, reusando `province_link` que el router ya pasaba al contexto desde P2—. Verificado a mano en Chromium (escritorio y 390 px): clic en una fila y clic en el mapa cambian el panel derecho y la fila resaltada sin recarga completa (solo `pushState`), hover en el mapa engruesa el trazo, sin errores de consola.

## 2026-09-25 — «Explorar» P7: móvil fiel a M2 — hojas, presets y columna fija
Contexto: desde P2 el angosto era interino (tabla con scroll horizontal, sin panel izquierdo, sin forma de agregar un producto salvo el «+» heredado del escritorio). El prototipo aprobado M2 usa la MISMA lógica que C —el script es 99% idéntico, solo cambia `CFG` (variant, columnas por defecto, alto de fila)— con una presentación distinta: sin panel lateral, «+» y «Filtros» abren una hoja inferior, tres presets de columnas (Precio/Semanas/Todo) en vez de siete casillas, y la primera columna fija al desplazar.
Decisión: (1) el panel de productos y el de filtros (provincia, formato, steppers) se factorizaron en macros de Jinja (`lista_productos`, `filtros_de_producto`) y se llaman DOS veces —panel de escritorio y hoja del angosto—: una sola fuente, en vez de mantener el mismo HTML escrito dos veces a mano. (2) las dos hojas son `<details>` (mismo patrón que el «+» de escritorio desde P4): sin JavaScript, tocar el disparador muestra el contenido en el flujo normal de la página —no como hoja flotante, pero alcanzable—; con CSS, `[open]` las fija abajo con scrim. Abrir una cierra la otra. (3) los presets escriben las MISMAS casillas de «Columnas visibles» del escritorio (no hay estado aparte) — «Precio» reusa el default que el servidor ya manda (`rango`/`sem` ocultas, D3/P3), no el de M2 (que trae `rango` visible por defecto): copiar el de M2 exigía reaplicar el preset después de cada swap de htmx porque el servidor no sabe en qué ancho está el navegador; iguala el default que YA es consistente sin tocar nada más. (4) columna fija con `position: sticky` en la celda de nombre; encontrado y corregido en la verificación visual: `border-collapse: collapse` (la base de la tabla) hace que Chromium pinte las columnas de atrás por ENCIMA de la fija en vez de taparla, y la fila elegida/hover usaba `--ph-accent-wash` —semitransparente a propósito en el resto de la vista— como fondo de la celda fija, dejando pasar el texto de abajo; se arregló con `border-collapse: separate` en el angosto y una capa opaca (`linear-gradient` de la wash sobre `--m3-surface`) para esas dos filas. (5) dos steppers `nmin`/`dmin` quedan duplicados (escritorio oculto + hoja): `ponerStepper` ahora escribe las dos copias a la vez para que no se desincronicen.
Descartado: (a) reusar el popover «+» del escritorio (`ex_nav.add`, solo agrega) para la hoja del angosto: la hoja de M2 lista TODOS los productos —marcados los abiertos, para también poder cerrarlos, porque en el angosto no hay panel izquierdo desde donde desmarcar—; son casos distintos, quedan como dos `<details>` separados. (b) sincronizar checkboxes de provincia/formato entre las dos copias (desktop oculta + hoja) ante un resize del navegador cruzando los 720px sin recargar: escenario minúsculo —cambiar de ancho de ventana a mitad de sesión— cuyo peor caso es un filtro que queda «pegado» hasta tocarlo de nuevo, no una cifra incorrecta; no se justificaba la arquitectura de una sola fuente reposicionada por CSS que hubiera evitado el duplicado. (c) copiar el footer de M2 tal cual (pierde la explicación de §12.6, «sin flecha ni color»): se conserva el texto completo — el hint «desliza para ver más columnas» se agrega por CSS (`::after`, angosto únicamente), no se duplica el párrafo en la plantilla.
Consecuencia: dos tokens nuevos en `design_tokens.css` (`--ph-scrim`, `--ph-sheet-shadow`), exentos de versión oscura como ya lo estaba `--ph-elev` (mismo criterio: sombra/velo, no color de superficie). Sin cambios en `explore_view.py` ni en el router: P7 es plantilla y JavaScript únicamente. Verificado a mano en Chromium a 390×844: la hoja de productos y la de filtros abren y cierran (botón, scrim, y entre sí), la cuenta «Ver N provincias» sube y baja con los filtros, los tres presets cambian las columnas visibles, la columna fija se mantiene en 0 al desplazar sin superponerse con el resto (bug encontrado y corregido en esta misma verificación), sin desborde de página ni errores de consola; el escritorio a 1440 se re-verificó sin cambios.

## 2026-09-25 — «Explorar» P8: cierre de fidelidad
Contexto: última parte del plan de ocho aprobado el 2026-09-21 (docs/decisions.md, entrada de esa fecha). Cierre técnico —oscuro, accesibilidad, peso— y la única decisión que había quedado abierta desde el prototipo B del mapa: qué hacer con las insignias apiladas sobre cada provincia y el panel de precios anclado, que ahora repiten con menos profundidad lo que hace Explorar.
Decisión: (1) modo oscuro: sin cambios de código —toda la hoja de «Explorar» usa `var(--m3-*)`/`var(--ph-*)`, nunca un color a mano (ya lo exige un test), así que el tema oscuro sale gratis del sistema de tokens—; se verificó a mano tocando el propio interruptor (no forzado por cookie) en escritorio y en 390 px, con una fila expandida y la hoja de filtros abierta, sin errores de consola. (2) accesibilidad: encontrado en la verificación de P7 que quedó pendiente para acá —las hojas de `<details>` no respondían a Esc ni movían el foco, aunque llevan `role="dialog"`— se agregó manejo del evento `toggle` (no del `click`: `toggle` también capta abrir con teclado, algo que un `click` a secas no ve) para mover el foco al panel al abrir y devolverlo al botón que la abrió al cerrar, más un `Esc` global que cierra la hoja abierta; `aria-modal="true"` y `tabindex="-1"` en el panel. (3) peso: la página de Explorar con dos productos abiertos pesa ≈27 KB comprimidos (171 KB sin comprimir), entre el tablero (≈10 KB) y el mapa completo con Leaflet (≈54 KB) — razonable para lo que muestra, sin necesidad de recortar nada. (4) insignias del mapa y panel anclado: consultado el operador, decidió dejar el mapa como está — Explorar no lo reemplaza, es una vista de trabajo aparte para quien quiere profundizar; el mapa sigue siendo el vistazo rápido. No se tocó `public_home.html` ni `province_grid.py`.
Descartado: (a) tocar el mapa unilateralmente (quitar insignias, simplificar el panel): era la decisión abierta explícitamente reservada al operador desde 2026-09-21, no una llamada de implementación. (b) agregar una prueba automática del foco/Esc de las hojas: el repositorio no ejecuta JavaScript en pytest (igual que el resto de la interacción de «Explorar» desde P2); se verificó a mano con Playwright, igual que todo lo anterior.
Consecuencia: con esto se cierran las ocho partes del plan de «Explorar» (P1–P8, commits `ecdc3bf`…`fe83325` y este). El mapa y el tablero quedan exactamente como estaban; la vista pública gana `?vista=explorar` completa, con pestañas por producto, árbol por provincia/formato, filtros, panel derecho interactivo y una versión móvil fiel a M2.

## 2026-09-25 — Backtest del asistente LLM: línea base antes de curar toda la cola (slice 1)
Contexto: el operador decidió que toda la cola pase por el asistente LLM y que él solo verifique el formulario (plan en `docs/plan-asistente-llm-cola-completa.md`). Antes de construirlo se midió al modelo contra lo curado a mano. El asistente de 2026-09-01 hacía una llamada por fila sin decirle al modelo qué fila (en una lista de 27 precios se abstenía y tardaba ~100 s), y `max_tokens=1024` truncó una respuesta.
Decisión: `llm_propose_ad.py` (una llamada por anuncio: todas las filas y el veredicto de descarte) y `llm_backtest.py` (`python -m saimc.extraction.llm_backtest`, dry-run por defecto, `--execute` para gastar, reanudable). Se corrió sobre 100 anuncios (47 curados sin asistencia + 53 descartados, Sonnet 5), con dos versiones de prompt. Línea base v2, sobre 70 filas (reglas entre paréntesis): producto 97,1% (94,3), precio y moneda 74,3% (55,7), cantidad y unidad 82,9% (60,0), extracción completa 60,0% (40,0). Descarte: 0 falsos descartes en 50, recall 50/53 (los 3 fallos, imagen). 26 filas de más contra 70 esperadas. Costo real ~$0,006 por anuncio. Muestra chica: sin umbrales, la vara la fija el operador (`spec.md` §12.6); por eso la comparación de las reglas usa las mismas funciones que H0 (`measure.row_correctness`) pero esta medición NO es H0.
Descartado: (a) seguir iterando el prompt sobre esta muestra: las diferencias de 2–3 filas son ruido; los ajustes pasan al slice 2 y se vuelven a medir con una muestra mayor. (b) Que la API acepte un `enum` combinado con `type: ["string","null"]`: da HTTP 400; la nulabilidad va con `anyOf` (los tests con cliente falso no lo detectan, por eso existe el smoke real). (c) Una clave de API sin workspace: da 400 «not scoped to a workspace» y `classify_failure` lo etiquetaba `auth` por contener «api key».
Consecuencia: la v2 no aplica bien la regla de precio en dos monedas cuando la moneda viene marcada («42 usd o 28560» devolvió CUP): 7 filas peores y 4 mejores respecto de la v1; queda para el slice 2 con un ejemplo explícito. El costo por anuncio (~$7 al mes a 1.200 anuncios) reemplaza el supuesto de `docs/costos.md`. Los apartamientos de `EXT-07`, «solo a pedido», H0 sobre lo no asistido y la decisión cerrada 2 NO se registran acá: se registran al ejecutar el slice 2. La redefinición de H0 sigue pendiente. Los resultados viven en `data/llm-backtest-*.jsonl` (v1 = línea base, v2 = prompt `2026-09-25.2`), fuera de git.

## 2026-09-25 — «Completar con IA» propone el anuncio entero y el modelo manda (slice 2)
Contexto: el operador decidió que toda la cola pase por el asistente y que él solo verifique (`docs/plan-asistente-llm-cola-completa.md`). El backtest de la entrada anterior, repetido con el prompt `2026-09-25.3` sobre los mismos 100 anuncios, dio extracción completa 67,1% contra 40,0% de las reglas (70 filas) y mostró qué señales predicen el error: si modelo y reglas coinciden, la fila es correcta el 78,7%; si difieren, el 44,4%. La «moneda asumida» (94,4% contra 97,0% explícita) y la evidencia hallada (93 de 96 filas) no predicen nada.
Decisión: el botón «Completar con IA» sigue siendo un POST a pedido pero hace UNA llamada por anuncio (`propose_ad`) y las filas del formulario salen de las filas del modelo. Las reglas quedan como segunda opinión: marca «difiere» con «Reglas: x» visible bajo el campo. La cita que respalda cada fila va plegada («Ver cita») y resaltada en el texto de los anuncios de texto. El descarte sugerido reutiliza el banner existente con el motivo preseleccionado (solo `off_focus` o `no_data`, los únicos que admite el CHECK). Lo que el modelo no propone (base de precio, formato, variante…) sale de las reglas. El registro del operador (`llm_assist_log_path`) sigue sirviendo a H0 y a /control.
Descartado: (a) marca de «moneda asumida» y de «evidencia no hallada»: no discriminan en la muestra y la primera marcaría la mitad de las filas. (b) Evidencia por campo: una cita por fila alcanza y cuesta menos tokens. (c) Seguir con «solo huecos».
Consecuencia: apartamientos que quedan registrados. (1) `EXT-07` y `spec.md` §6.1: el LLM deja de ser «solo lo que las reglas no resuelven» y propone todo; la confirmación humana sigue. (2) Reemplaza la regla del 2026-09-01 «solo huecos, no pisa lo resuelto por las reglas». (3) Sigue siendo «a pedido»: el pre-cálculo por lotes es el slice 3. (4) Todo anuncio asistido queda fuera de la medición de H0 (`assisted_ingestion_ids`); con el botón en todos los anuncios ese conjunto se vacía a medida que se usa: la redefinición de H0 sigue pendiente. (5) Nada de la propuesta se guarda todavía (slice 3). Riesgos: el `price_basis` heredado de las reglas puede no cuadrar si el modelo cambió precio o cantidad, y el operador lo confirma; y el rubber-stamping sigue siendo el riesgo vivo, porque «coincide con las reglas» no significa «no mires» (10 de ~23 errores estaban en filas que coinciden).

## 2026-09-25 — La propuesta del modelo y la revisión del operador se guardan en tablas propias (slice 3a)
Contexto: la propuesta de «Completar con IA» vivía solo en la página: recargar volvía a pagar la llamada, al guardar nadie sabía qué propuso el modelo ni qué corrigió el operador, la respuesta cruda se tiraba y no había dónde anotar lo que el modelo no podía saber («4x10» = 4 paquetes de 10 lb). Especificación aprobada: `docs/spec-slice-3a.md`.
Decisión: dos tablas nuevas e inmutables (migración `0021`), sin camino de escritura sobre una fila existente. `llm_proposals`: una fila por llamada real, también degradada, con filas, veredicto, tokens y respuesta cruda (`AdProposal.raw_response`). `llm_reviews`: una fila al curar o descartar con `proposal_id` a la vista, con una entrada por fila propuesta y por fila agregada (`proposal_index`, `price_history_id`, `proposed`, `final`, `edited`) y la nota con teléfonos redactados. «Final» es el valor del primer guardado: `/curation/{id}/edit` no toca la captura. Abrir un anuncio con propuesta `ok` guardada la muestra sin llamar a la API. Un `proposal_id` que no es de ese anuncio se ignora y la curaduría se guarda igual. H0 y el backtest excluyen un anuncio si está en el registro del operador **o** tiene fila en `llm_reviews` (`measure.assisted_ingestion_ids`).
Descartado: (a) una fila por campo o por fila revisada: claves foráneas en todo, pero duplica escritura y consultas; con ~1200 anuncios al mes leer el JSON en Python alcanza. (b) Guardar la propuesta en un campo oculto del formulario: el operador podría alterarla y no sobrevive a una recarga.
Consecuencia: con propuesta a la vista, los botones de descarte mandan el formulario de curar entero a `/discard` (`formaction`, `formnovalidate`) para que viajen `proposal_id` y la nota. `edited` queda vacío cuando falta un lado (fila borrada o agregada a mano): el `null` ya lo dice. El índice `proposal_row_N` viaja en el formulario, así que uno inválido, repetido o fuera de rango cuenta como fila agregada a mano. En 3b la propuesta se precalcula sin pasar por el registro: desde ahí la exclusión de H0 descansa en `llm_reviews`. Requiere `alembic upgrade head` al desplegar.

## 2026-09-25 — Pre-cálculo de la cola: comando manual, uno a la vez, que suma consumo pero no excluye de H0 (slice 3b)
Contexto: con la propuesta guardada (3a), abrir un anuncio ya no paga la llamada, pero el operador igual espera la primera por cada anuncio. El plan pide precalcular el backlog (~267 pendientes) en un VPS de 2 vCPU, informar el costo antes y poder cortarlo y reanudarlo. La entrada de 3a decía que el pre-cálculo no pasaría por el registro del operador; así su gasto no se vería en `/control`.
Decisión: `python -m saimc.extraction.prefill`, en seco por defecto (cuenta texto/imagen y estima el costo con el promedio de tokens de `llm_proposals`, o con el documentado en `docs/costos.md` si no hay filas, y dice cuál usó); `--execute` procesa de a uno los pendientes sin propuesta `ok`, del más viejo al más nuevo, con commit por anuncio, `--limit` y `--max-cost`. Usa el mismo texto (`llm_propose_ad.ad_text`, OCR incluido) y la misma línea de registro (`record_operator_run`) que «Completar con IA», mudados desde `curation.py` para no duplicarlos. Una falla de saldo o de clave detiene la corrida; las demás quedan `degraded` y sigue. Reemplaza en parte a la entrada de 3a: el pre-cálculo **sí** escribe en el registro del operador, con `"source": "prefill"`; `consumption_summary` cuenta todas las líneas y `assisted_ingestion_ids` ignora las del pre-cálculo (las líneas viejas sin `source` siguen contando como asistidas; el botón ahora escribe `"source": "curation"`).
Descartado: (a) cron o un worker que precalcule solo: contradice la decisión cerrada 3 y gastaría en anuncios que el operador descarta en lote; se corre a mano antes de curar. (b) Paralelizar llamadas: el OCR satura las 2 vCPU. (c) Registrar el consumo en un archivo aparte: `/control` tendría que leer dos registros para un solo número.
Consecuencia: un anuncio precalculado y nunca abierto no sale de H0; uno curado con la propuesta a la vista sí (por `llm_reviews`). Por la regla de 3a de mostrar siempre la propuesta `ok`, en la práctica todo lo que se cure después del pre-cálculo queda fuera de H0: la redefinición de H0 sigue pendiente. Los pendientes fuera de foco también se precalculan (el comando no filtra por foco); si el gasto en ellos pesa, filtrarlos es el próximo ajuste.

## 2026-09-25 — El modelo propone también formato de venta, base del precio y envase (slice 3d)
Contexto: el modelo proponía seis campos por fila; minorista/mayorista (`sale_format`), precio por unidad o total del lote (`price_basis`) y envase (`packaging`) llegaban «sin responder» salvo la base, que las reglas solo leen cuando el anuncio la declara. `price_basis` sin responder saca la fila del rango público, y «Pollo Muslo 4x10: 27300» (caja de 40 lb, total del lote, minorista) es el tipo de anuncio que ninguna regla resuelve. Pedido del operador, aprobado sabiendo que contradice decisiones anteriores.
Decisión: el prompt `2026-09-25.4` pide por fila los tres campos, con vocabulario cerrado y `null` si no se puede saber (nulabilidad con `anyOf`), y las definiciones del operador copiadas del texto de ayuda del formulario («Mayorista es escala de contenedor, no vender un saco o paquete individual…»). Lo que no valida cae a `None` y cuenta en `dropped`. Los tres viajan en `ExtractionProposal` (no en `RowMeta`: son valores de la fila, y el formulario, `llm_proposals.rows` y el backtest ya consumen la fila así; las reglas los dejan en `None`). En `/curation` el modelo manda si contestó (contorno de asistido) y, si se abstuvo, queda lo de las reglas; la base que difiere de las reglas lleva «Reglas: …». La captura de la revisión compara los nueve campos, pero en una propuesta guardada antes de la v4 solo los seis que se le preguntaron. El backtest suma la sección (h): acierto por campo contra el oro, con el oro NULL aparte; «extracción completa» (H0) no cambia y `RowAgreement.status` sigue resumiendo solo sus cinco campos. Reemplaza, para estos tres campos y solo como propuesta que el operador confirma: (1) la regla del 2026-08-12 de no prellenar nunca `sale_format` ni el envase (entrada «`sale_format` por fila», design `2026-08-12-curation-unit-and-format-capture`, D4 y «never a fabricated retail»); (2) D5 del design `2026-08-21-price-basis` y su «El control MUST venir sin valor preseleccionado», ya relajado el 2026-08-25 para la base declarada (esa entrada lo cita como «D5/D4»); (3) en parte, «El prellenado por historial del canal se retira» (2026-08-21): aquella fuente copiaba la fila anterior del canal, esta lee el anuncio y se mide antes de fiarse.
Descartado: (a) que las reglas deriven formato o envase del texto: la entrada del 2026-08-12 ya lo descartó por ser la aritmética que la prueba de la jabita existe para detectar; (b) mostrar la propuesta del modelo como sugerencia aparte sin preseleccionar: duplica controles, y la regla de la interfaz es ocultar, no convivir; (c) sumar la base al `status` de acuerdo: cambiaría la señal que el slice 2 midió contra H0.
Consecuencia: el riesgo que retiró el prellenado del 2026-08-21 («un campo vacío se nota, uno mal prellenado no») vuelve para estos tres campos, y la base mal propuesta multiplica el precio por la cantidad; la defensa es el backtest v4 antes de fiarse (runbook, «Prompt v4») y el contorno de asistido. Hallazgo aparte: `propose_all`, que usan la cola y el backtest, no aplica las reglas de la base (`_declared_price_basis`, `_packaging_price_basis` corren solo en `propose`), así que hoy la columna «Reglas» de la base es casi siempre vacía y el «Reglas: …» de la base casi no aparece; conectarlas es un slice propio del motor de reglas. Sin cambio de esquema: los tres campos nuevos de `llm_proposals.rows` y de `llm_reviews` son JSON.

## 2026-09-26 — Prompt caching del prefijo fijo en `propose_ad`; `input_tokens` pasa a ser entrada equivalente
Contexto: cada llamada manda ~1.466 tokens que nunca cambian (el `system` y los catálogos) y ~1.600 del anuncio. Con tarifas de Sonnet 5 la entrada era ~61% del costo (~$0,0099 por anuncio), y el prefijo fijo casi la mitad de esa entrada. El pre-cálculo llama de a uno y seguido, así que la caché de 5 minutos se mantiene caliente.
Decisión: el mensaje de usuario va en dos bloques (`build_prompt_blocks`): catálogos con `cache_control: ephemeral` y el anuncio al final, sin marca. El texto que ve el modelo es idéntico (`build_prompt` lo concatena), así que no cambia `PROMPT_VERSION` y las mediciones previas siguen valiendo. Con caché, la API informa `input_tokens` solo del tramo posterior al último bloque cacheado y el resto aparte (`cache_read_input_tokens`, `cache_creation_input_tokens`): `AdProposal.input_tokens` guarda ahora la entrada **equivalente** (normales + lectura×0,1 + escritura×1,25), para que `consumption_summary`, el backtest y el pre-cálculo, que multiplican ese número por la tarifa, sigan acertando el costo sin tocarse. Los tokens de caché reales van aparte (`cache_read_tokens`, `cache_write_tokens`) en `AdProposal` y en la línea del registro, para poder verificar que funciona. Sin cambio de esquema: `llm_proposals` guarda la entrada equivalente.
Descartado: (a) guardar la entrada real y ajustar el costo en cada sitio que lo calcula (cuatro): más código y más lugares donde equivocarse; (b) reusar resultados de mensajes repetidos: `content_hash` ya es único y una lista repetida con otros precios no se puede reusar; (c) cachear el anuncio: cambia siempre.
Consecuencia: el número de «tokens» que muestran el pre-cálculo y `/control` deja de ser el conteo crudo de la API. Los factores 0,1 y 1,25 son las tarifas publicadas, no medidas acá: si cambian, se ajusta `_CACHE_READ_FACTOR` y `_CACHE_WRITE_FACTOR`. Sonnet 5 cachea desde 1.024 tokens (el prefijo mide ~1.466); con Haiku 4.5 (mínimo 4.096) no cachearía y no rompería nada. Un cambio del catálogo de productos invalida la caché una vez. Falta verificar contra la API real que `cache_read_tokens` sea mayor que cero desde la segunda llamada (los tests usan un cliente doble).

## 2026-09-26 — La home pública abre en Tablero; el Mapa vuelve a ser portada con dato en 6 o más provincias
Contexto: las tres vistas (Mapa, Tablero, Explorar) responden casi la misma pregunta con distinto envoltorio, y el Mapa era la portada por defecto. Antes del lanzamiento la base tiene 350 precios en 2 de 16 provincias: un mapa con 14 provincias en gris comunica «vacío», no inteligencia de mercado. El Mapa además es la vista más pesada (Leaflet y tiles), y `public.py` ya reconocía al Tablero como la opción para conexiones lentas.
Decisión: `DEFAULT_VISTA = "tablero"`. Sin `?vista=` ni cookie, `/` abre el Tablero; un `?vista=` inválido también cae ahí. Los constructores de enlaces del Mapa pasan a llevar `vista=mapa` (su default deja de ser «la default de la ruta») y `_query` emite `vista=` siempre que hay vista, también la default: sin `?vista=` manda la cookie (dura un año), y un enlace interno del Tablero sin el parámetro caía en el Mapa a quien tuviera `vista=mapa` guardada. La cookie de provincia del Tablero también se lee en `/` pelado. **Revisión: el Mapa pasa a ser portada cuando haya dato en 6 o más provincias** (el 6 es una propuesta a discutir, no una medición).
Descartado: (a) mantener el Mapa como portada, como pide `spec.md` §6.7 («ve un mapa de Cuba coloreado»): con 2 provincias con dato lo primero que se ve es un mapa casi vacío. Es un apartamiento consciente de §6.7, no un olvido. (b) Seguir omitiendo `vista=` cuando es la default (Tablero) y hacerlo explícito solo en el selector de vistas: era la primera versión y dejaba los enlaces internos del Tablero a merced de la cookie; se descartó al aparecer el bug.
Consecuencia: los enlaces ya compartidos sin `?vista=` que antes abrían el Mapa ahora abren el Tablero (salvo que la cookie diga otra cosa); las URLs internas del Tablero son un poco más largas. Los tests del Mapa fijan la cookie `vista=mapa` en su fixture. Pendiente de la estrategia de las tres vistas, sin decidir todavía: cintillo común con el pulso del mercado, producto/provincia/período compartidos entre vistas, un solo control de navegación y qué hacer con Explorar en móvil.

## 2026-09-26 — Rediseño del Tablero según el prototipo aprobado (cintillo, movimiento, móvil, cobertura y período)
Contexto: con la home abriendo en Tablero (entrada anterior de esta fecha), el operador pidió que se vea bien en todos los escenarios y con animación coherente con la estética del sitio, y aprobó un prototipo en Claude Design (canvas «Tablero — prototipo completo»). No adopta el look de las otras tres direcciones exploradas (Atlas, Terminal, Pizarra); toma solo el cintillo tipo bolsa. Además notó que el Tablero no dejaba cambiar el período, como sí el Mapa.
Decisión: nueve commits en `worktree-vista-default-tablero`. (1) Sparkline de 13 semanas por celda; la serie semanal y el trazo pasan a `web/weekly.py`, compartido con Explorar. (2) Escritorio: una columna sin producto elegido, celda a la izquierda con cifra grande, pastilla y trazo. (3) Movimiento: entrada escalonada, conteo de cifras (900 ms) y trazado, solo `opacity`/`transform`/trazo, apagado con «reducir movimiento», y `data-entra` se quita al terminar para que el buscador no repita la entrada. (4) Cintillo en las tres vistas (`ticker_view`): mediana de las medianas de las provincias con dato, no «nacional»; cambio solo entre provincias con los dos períodos, sin flecha ni color (2026-09-21); 44 px y no los 40 del prototipo, por el blanco táctil. (5) Detalle: navegación a la izquierda, resumen teñido por provincia, gráficos animados acotados a `.ph-graficos` para no tocar las ventanas del mapa. (6) Teléfono: barra inferior de vistas (≤720 px) en las tres vistas y tarjeta por producto con una caja teñida por provincia. (7) Estados: mosaico de cobertura de las 16 provincias (`coverage_view`, mismo estado que el mapa) y frescura en tres niveles (hoy, atrasado, viejo a más de 30 días, sin rojo). (8) Selector de período (7/30/90 días y Desde/Hasta) con los mismos atajos de `build_window_nav`, sin htmx.
Descartado: (a) fusionar el Tablero dentro de Explorar: se conservan las tres vistas y se revisa con uso real a las 3–4 semanas del lanzamiento (falta un contador de visitas por vista, sin datos personales). (b) El esqueleto de carga del prototipo: el Tablero es de servidor y navega con páginas completas, no hay momento de carga que lo dispare. (c) Cabecera de cintillo de 40 px: 44 px.
Consecuencia: no se hizo el estado compartido entre vistas (producto, provincia y período viajan por URL, pero cada vista tiene su selector), ni un control de navegación único en escritorio (la barra inferior es solo del teléfono); Explorar sigue sin selector de período (su móvil ya existe: P7, fiel a M2, 2026-09-25). Se reescribieron a propósito tests que fijaban la cifra a la derecha, las columnas de la lista y la barra lateral a la derecha del detalle. Un guardia de tokens exigió definir `--ph-barra-h` en `design_tokens.css`. El conteo de cifras es un adorno de JS: la cifra siempre viene escrita por el servidor. Hallazgo de datos, no del rediseño: «Saco de 100 lb Criollo» de Arroz en La Habana muestra un máximo de 17.888.000 CUP (probable mala extracción), a corregir antes de publicar.

## 2026-09-26 — Selector de período compartido: parcial único para el tablero y Explorar
Contexto: el mapa deja elegir 7 / 30 / 90 días o un rango de fechas; el tablero (ya arreglado en la entrada del rediseño) y Explorar solo heredaban `desde`/`hasta` de la URL. El selector del tablero vivía con clases propias (`ph-dash-chip`) y CSS dentro de la plantilla del tablero, que Explorar no carga (tiene su propio espacio `ph-ex-*`).
Decisión: un parcial `_selector_periodo.html` con clases `ph-periodo*` y su CSS en `static/saimc-public.css`, que cargan las dos vistas; el tablero y Explorar lo incluyen y le pasan `window_nav` y `period_hidden` (vista, productos, pestaña, provincia). `build_window_nav` gana `pestana`, para que cada atajo vuelva a la misma pestaña de Explorar. En Explorar va en una fila propia bajo las pestañas de producto, con chips de 32 px en escritorio y 44 px en el teléfono. Cambiar el período es una navegación completa, sin htmx.
Descartado: (a) htmx en Explorar: el cintillo de precios vive fuera de `#ph-live` y se recalcula con el período, así que un intercambio parcial lo dejaría con las cifras de la ventana anterior. (b) Ponerlo en `.ph-ex-head`: esa cabecera solo existe si el producto tiene datos en la ventana; con «7 días» sin anuncios el selector desaparecería y no habría cómo volver. (c) Duplicarlo en la hoja «Filtros» del teléfono: dos formularios iguales en el DOM para un control que cabe en una fila.
Consecuencia: el mapa conserva su propio selector (no se unificó: usa htmx y vive en su compás). Con una ventana sin anuncios el cintillo no se dibuja, por diseño. Se corrige además una afirmación errónea de la entrada del rediseño: el móvil de Explorar YA existía (P7, 2026-09-25, fiel a M2); lo que faltaba en Explorar era solo este selector. Quedan sin hacer el estado compartido entre vistas y la navegación única en escritorio.

## 2026-09-26 — Estado compartido entre vistas: auditoría de enlaces y dos descuidos corregidos
Contexto: la entrada del rediseño dejó pendiente el «estado compartido entre vistas». Antes de construir nada se auditó qué parámetros (vista, productos, pestaña, provincia, período) conserva cada enlace y formulario de las cuatro pantallas con un estado completo en la URL.
Decisión: el estado ya viajaba por la URL en casi todo —selector de vistas, barra inferior, cintillo, chips de provincia y de período, navegación de productos y pestañas—; lo que sueltan (`Todas`, `×`, `‹ Todos los productos`, la vista simple `/producto/…`) es por diseño. Se corrigieron dos descuidos. (1) Los dos formularios del mapa (provincia y rango de fechas) no llevaban `vista`: desde que la portada es el tablero (entrada de esta fecha), quien llegaba al mapa por un enlace directo y cambiaba de provincia o de fechas terminaba en el tablero, y con htmx el intercambio no encontraba `#ph-live`; ahora mandan `vista=mapa`. Estaba también en `master`. (2) Explorar → tablero: el tablero solo tiene detalle de UN producto y con todos los abiertos mostraba la lista completa; ahora recibe el producto de la pestaña activa (sin la pestaña). El mapa sigue recibiendo todos los abiertos.
Descartado: (a) una cookie para recordar período o provincia entre visitas al mapa y a Explorar: la del tablero (`provincia`) ya existe y `public_home` documenta por qué el mapa no la lee («su encuadre no cambia por una preferencia que nadie le pidió»); recordar el período no lo pidió nadie. (b) Un modelo único de «filtros globales» en el servidor: la URL ya es ese modelo y es compartible. (c) Un control de navegación único en escritorio: cada vista conserva el suyo (el dock del mapa, el segmentado del tablero, el de Explorar); la barra inferior unifica solo el teléfono.
Consecuencia: se actualiza a propósito el test que fijaba «todos los productos al tablero» desde Explorar. Cualquier formulario o enlace nuevo de una vista tiene que llevar `vista` explícita, o decide la cookie o la default de la ruta; el test `TestFormulariosDelMapaConservanLaVista` cubre el mapa. Queda sin hacer solo la navegación única en escritorio, por decisión.

## 2026-09-26 — Backtest v4 completo y caching verificado: cierra la medición del slice 3d
Contexto: el backtest del prompt `2026-09-25.4` se había cortado en 80 de 100 anuncios por saldo de la API, y el caching del prefijo estaba construido sin probar contra la API real.
Decisión: se reanudó el mismo comando sin mover archivos (100/100, $0,91 acumulado) y se probó el caching con `prefill --execute --limit 3`. Sobre 70 filas del oro: extracción completa 71,4% contra 40,0% de las reglas (67,1% con la v3); formato de venta 90,0%, base del precio 94,4%, envase 66,2%, sobre todo por abstenciones. `cache_read_tokens` fue 2733 en las tres llamadas. Las 7 filas de la lista de Santiago ya estaban corregidas en el oro (un saco individual es minorista). No se cambia el prompt: la línea sobre «por mayor» queda opcional.
Descartado: subir `PROMPT_VERSION` ahora por 2 filas de arroz; con muestra tan chica no se distingue de ruido y invalida la comparación con v4.
Consecuencia: en la prueba, un anuncio (`62cd0e01`) quedó degradado con 4096 tokens de salida, o sea truncado por `max_tokens`; se estudia en el slice 3c. El slice 3d sigue abierto solo por la verificación del operador en pantalla.

## 2026-09-26 — Pendiente: el log de visitas debe registrar la vista (`vista=`) para decidir sobre Tablero y Explorar
Contexto: con la home abriendo en Tablero y las tres vistas conservadas, la fusión de Tablero y Explorar quedó diferida a «mirar el uso real 3–4 semanas después del lanzamiento» (entrada del rediseño, misma fecha). Pero el log de `analytics.py` deja `public.pageview path=/ day=… visitor=…` y las tres vistas son `/`: hoy no hay forma de saber cuántos visitantes usan cada una.
Decisión: se hará más adelante, sin fecha, y NO se hizo hoy. Cuando se haga: agregar `vista=<mapa|tablero|explorar>` a la línea de `registrar_visitas_publicas`, resuelta como lo hace la ruta (`?vista=` explícito, luego la cookie `vista`, luego la default) con `resolve_vista`; para `/producto/…` y `/anuncio/…` un `-`. Sin datos personales nuevos, sin tabla, sin JS, sin cookies (los límites que ya fija el docstring de `analytics.py`). La agregación sigue siendo manual sobre el log, por ejemplo `grep public.pageview | grep -oP "vista=\K\S+" | sort | uniq -c`, o contando `visitor` distintos por vista.
Descartado: (a) una tabla o un producto de analítica: `analytics.py` lo excluye a propósito y tocaría el esquema. (b) Medir con JS en el cliente: cookies y tracking que el proyecto no quiere y peso extra para conexiones lentas. (c) Decidir la fusión sin datos, por intuición: ya se descartó dos veces en esta sesión.
Consecuencia: **retrasarlo cuesta datos que no se recuperan**. El log solo acumula desde que se despliega el cambio, y no se puede reconstruir la vista de las visitas anteriores; cada semana sin este campo es una semana menos de las 3–4 que se quieren mirar. Conviene hacerlo antes del lanzamiento público. Es un cambio de una línea con un test en `tests/` (no hay ninguno del middleware con la vista todavía). Sin él, la revisión de la fusión no tiene con qué comparar.

## 2026-09-27 — Slice 4: botón «Aceptar» con atajo y orden de la cola por acuerdo con las reglas
Contexto: con el asistente proponiendo el anuncio entero (slices 2/3a/3b/3d), curar pasó de completar un formulario a verificarlo. El plan (`docs/plan-asistente-llm-cola-completa.md`, slice 4) pedía un botón «Aceptar» con atajo y una cola que muestre primero lo que ya coincide con las reglas. Las imágenes (WhatsApp y la carpeta de entrada) no tienen texto hasta que se transcriben, y transcribir toda la cola para ordenarla pagaría el OCR de cientos de pendientes en cada carga de página — el mismo motivo por el que `_focus_rank` ya deja `_FOCUS_UNKNOWN` sin resolver.
Decisión: (1) El botón principal de `/curation/{id}` dice «Aceptar» (mismo endpoint que «Curar anuncio») solo cuando hay una propuesta del modelo a la vista (`accept_ready` en el contexto de la plantilla), con atajo `Ctrl+Enter`/`Cmd+Enter` vía `form.requestSubmit()`. (2) Dentro de cada nivel de foco, `_pending_in_focus` se reordena con `_order_by_agreement`: para texto, compara la propuesta guardada (`llm_proposals`, sin llamar a la API) contra `propose_all` con las mismas `RowAgreement`/`compare_with_rules` que ya usa el formulario — coincide, sin propuesta/sin regla, difiere, en ese orden. Para imagen, se ordena SOLO por el estado de la propuesta guardada (con filas / sugiere descartar / ninguna), nunca por acuerdo con las reglas.
Descartado: (a) transcribir la imagen para ordenarla — paga el OCR de la cola entera en cada carga; (b) guardar el acuerdo en `llm_proposals` al momento de la propuesta — toca el esquema y exige el flujo completo de especificación; queda anotado para retomar si el orden de las imágenes molesta en la práctica. (c) Tocar también `/curation/cola` (el listado completo): no estaba pedido y ese endpoint no calcula acuerdo hoy; sigue ordenado por `captured_at` dentro de cada foco.
Consecuencia: `_pending_in_focus` no cambió de firma ni de contrato (sigue devolviendo por foco, más viejo primero dentro de cada nivel); el reordenamiento es un paso aparte (`_order_by_agreement`) que solo se aplica donde se muestra el turno de curaduría. Falta la verificación del operador en pantalla (390 px y escritorio) antes de cerrar el slice.

## 2026-09-27 — Cierre de los slices 3d y 4 del plan del asistente LLM
Contexto: el 3d (formato de venta, base del precio y envase) y el 4 (botón «Aceptar», atajo y orden de la cola) quedaban abiertos solo por la verificación del operador en pantalla.
Decisión: el operador revisó ambos en pantalla (390 px y escritorio) y confirmó que están de acuerdo a lo planificado. Los dos slices quedan CERRADOS en `docs/plan-asistente-llm-cola-completa.md`.
Descartado: nada nuevo — es cierre de trabajo ya registrado en las entradas del 2026-09-26 (3d) y 2026-09-27 (slice 4).
Consecuencia: el próximo slice del plan es el 3c (glosario y recorte del texto), con el hallazgo del anuncio truncado (`62cd0e01`, 4096 tokens de salida) como primer dato a mirar en `raw_response`.

## 2026-09-27 — Slice 5: vigilancia del rubber-stamping en `/control`
Contexto: con el asistente proponiendo el anuncio entero, curar puede degradar en aceptación sin revisar — apretar «Aceptar» cada vez más rápido y cada vez sin tocar nada, sin que nada de la vista lo muestre. Faltaba una señal de esa deriva.
Decisión: columna nueva `shown_at` (nullable, sin backfill) en `llm_reviews`, capturada al armar la vista de curaduría y viajando oculta en el form. `src/saimc/web/rubber_stamp_metrics.py` mide, sobre `llm_reviews`: tasa de "no editado" por campo y agregada (numerador/denominador, no promedio de tasas) y mediana de tiempo por decisión (`created_at - shown_at`). `rubber_stamp_alert` compara la semana actual contra la anterior y avisa solo si SUBE la tasa sin editar Y BAJA el tiempo por decisión al mismo tiempo — puramente direccional, sin umbral de magnitud, mismo criterio de `spec.md` §12.6 contra métricas inventadas. Se le pregunté al operador si prefería la columna o un log de archivo aparte; eligió la columna: es queryable/joinable con el resto de la vigilancia, y `llm_reviews` es tabla de auditoría (no de datos crudos), así que no aplica el motivo original de "flujo completo de spec.md" con la misma fuerza que en el esquema núcleo.
Descartado: (a) un log de archivo aparte para `shown_at` — pierde la posibilidad de cruzarlo con `llm_reviews` en una sola consulta. (b) un umbral fijo de magnitud para la alerta — inventaría un número sin respaldo, lo mismo que `spec.md` §12.6 ya prohíbe para el resto de esta vista. (c) backfill de `shown_at` sobre filas viejas — no hay forma de reconstruir cuándo se mostró una fila ya curada; queda en NULL y sale de la muestra, por diseño.
Consecuencia: la alerta es puramente direccional — si en la práctica resulta ruidosa (dispara con movimientos chicos), el ajuste natural es agregar una magnitud mínima, no cambiar el criterio de las dos condiciones a la vez. `decision_time_stats` no filtra por `outcome`, pero hoy solo `submit_curation` (aceptar) captura `shown_at`; el flujo de descarte queda con `shown_at` en NULL y por lo tanto fuera de la métrica de tiempo, sin que haga falta excluirlo a mano.

## 2026-09-27 — Fix: `classify_failure` no confía en el texto cuando ya tiene status_code
Contexto: el backtest del 2026-09-25 encontró que una clave de API sin workspace da HTTP 400 con «This API key is not scoped to a workspace», y `classify_failure` lo etiquetaba `auth` por contener la frase «api key» en el mensaje — quedó anotado como hallazgo, sin corregir, en la entrada de esa fecha.
Decisión: el chequeo de texto («authentication», «api key») solo decide cuando `status_code` es `None`. Si el error trae `status_code`, manda el status (401/403 → `auth`; cualquier otro, sigue evaluando network/other) y el texto ya no puede pisarlo.
Descartado: agregar una categoría nueva (`config`/`workspace`) para este caso — `other` ya es honesto («falló, sin afirmar por qué»), y la lista de categorías la usa `/control` tal cual está.
Consecuencia: cualquier 400/404/etc. con "api key" en el mensaje (frecuente en errores de configuración de la cuenta, no de credencial inválida) cae en `other` en vez de `auth`. Cubierto en `tests/test_llm_assist.py::TestConsumoYFallas::test_clasifica_el_tipo_de_falla` (casos 401→auth y 400-workspace→other).

## 2026-09-27 — Costo real medido con `llm_proposals`: más alto que el backtest
Contexto: `docs/costos.md` proyectaba el costo mensual sobre el backtest de 100 anuncios curados a mano (2026-09-25, $0,0073/anuncio). Con «Completar con IA» ya en uso real sobre la cola, `llm_proposals` tiene 26 llamadas reales sobre 25 anuncios distintos (2026-09-25/26) — a diferencia del backtest, que escribe a un JSONL aparte y no toca esta tabla, así que es la fuente limpia de costo por anuncio real.
Decisión: se recalculó con esos datos: mediana 2.970 tokens de entrada / 681 de salida por llamada, **$0,0128/anuncio con Sonnet 5** (contra $0,0073 del backtest) — la cola real, con listas completas de WhatsApp, pesa más que la muestra curada. `docs/costos.md` suma la sección «Medido en producción real», que reemplaza la tabla de proyección mensual/backlog y corrige dos cifras que habían quedado desactualizadas desde que se agregó el backtest («diferencia con Haiku menos de un dólar», «Opus cuesta $2,79 más» — ambas citaban la tabla de supuestos anterior al backtest, no la del backtest).
Descartado: usar el promedio en vez de la mediana para la tabla de proyección — el promedio (2.858/1.098 tokens, $0,0167) está arrastrado por un caso que llegó al tope de `max_tokens` (4.096 tokens de salida, truncado, no es falla de la API) y por un anuncio real con muchas filas; la mediana representa mejor el caso típico y ambos valores quedan escritos en el documento para que se vea la diferencia.
Consecuencia: el caso truncado (`62cd0e01`, prompt `2026-09-25.4`) es el mismo que motivó el slice 3c (glosario + recorte de texto, tarea #7 del backlog), todavía sin implementar — mientras siga sin resolverse, algunos anuncios reales van a seguir costando más que la mediana. Muestra chica (n=25): re-medir cuando haya más volumen real acumulado.

## 2026-09-27 — Fix: `propose_all` aplica las reglas de `price_basis`
Contexto: `propose_all` (usado por `/curation` y el backtest para la cola diaria, un producto por línea) nunca llamaba a `_declared_price_basis` ni `_packaging_price_basis` — solo `propose()` (un solo producto por anuncio) lo hacía. La marca «Reglas: …» de `price_basis` casi nunca aparecía en un anuncio real de varios productos, aunque la línea trajera la misma pista que `propose()` sí hubiera leído (docs/plan-asistente-llm-cola-completa.md, pendiente suelto).
Decisión: el bucle línea por línea de `propose_all` corre las dos reglas contra **esa línea sola**, no contra el texto completo del anuncio — cada renglón es una oferta propia con su propio precio, y buscar contra el texto completo arriesgaría que la declaración de una línea confirme por casualidad la aritmética de otra.
Descartado: aplicar la misma corrección a `_tiered_proposals` (la lectura de bloque para precio por volumen, un solo producto). Fuera de alcance de este fix — el hallazgo original y el pendiente del plan hablan específicamente de `propose_all`, el bug es chico a propósito, y la lectura de bloque tiene su propia semántica de "Sale X" ya resuelta por los escalones (`_TIER_RE`); mezclar los dos conceptos sin medir primero es más riesgo del que el bug pedía.
Consecuencia: cubierto en `tests/test_extraction.py::TestPriceBasisEnProposeAll` (regla aritmética y marcador de envase, cada uno con una segunda línea de control que confirma que no hay fuga entre líneas). De paso se marcaron RESUELTOS en `docs/plan-asistente-llm-cola-completa.md` los otros tres pendientes sueltos ya cerrados en esta sesión (redacción de teléfonos, `classify_failure`, costo real) que habían quedado sin actualizar.

## 2026-09-27 — Fix: el pre-cálculo (`prefill`) filtra por foco
Contexto: `select_candidates` (`src/saimc/extraction/prefill.py`) traía todos los pendientes sin propuesta `ok`, sin filtrar por foco — a diferencia de `_pending_in_focus` en la cola interactiva. Pagaba propuesta del modelo también para anuncios que después se descartan en lote por `discard_out_of_focus` sin pasar nunca por curaduría (docs/plan-asistente-llm-cola-completa.md, pendiente suelto).
Decisión: `select_candidates` ahora recibe `products` (el catálogo del foco, `pilot_products`) y descarta los pendientes de texto que no mencionan ningún producto del foco (`mentions_any_product`, ya en `saimc.extraction.engine`), mismo criterio que `_pending_in_focus`. Una imagen sin transcribir (`original_text` nulo) sigue entrando: su foco no se sabe sin pagar el OCR, que la corrida ya paga al pedir el texto del anuncio.
Descartado: filtrar con `.limit()` en SQL antes de aplicar el foco — devolvería menos candidatos de los pedidos si los primeros N pendientes por fecha incluían anuncios fuera de foco. El filtro corre en Python sobre todos los pendientes, y el `--limit` se aplica DESPUÉS, sobre los ya filtrados.
Consecuencia: cubierto en `tests/test_prefill.py::test_no_procesa_pendientes_fuera_de_foco` (no llama para el fuera de foco) y el test ya existente `test_imagen_usa_el_texto_del_ocr` sigue en verde, confirmando que las imágenes no quedan excluidas por el nuevo filtro.

## 2026-09-27 — Fix: `propose_ad` desactiva `thinking` (tarea 7, investigación previa al glosario/recorte)
Contexto: el plan de la tarea 7 (slice 3c, glosario + recorte del texto) pedía primero «mirar en `llm_proposals.raw_response` en qué se van los tokens de salida». Medido: para respuestas chicas, ~2 caracteres por token de salida (normal); para las grandes (2000-4096 tokens), 0,16-0,5 caracteres por token — una porción creciente de cada llamada no aparece en el texto visible. `propose_ad` no fija el parámetro `thinking`, y Sonnet 5 corre *adaptive thinking* por defecto si no se lo pide desactivado explícitamente: ese razonamiento se factura como `output_tokens` pero no se guarda (`_response_text` solo toma el primer bloque `type: text`).
Decisión: se corrieron 6 llamadas reales (3 anuncios de producción × con/sin `thinking`, ~$0,25 autorizados por el operador) reconstruyendo el prompt exacto de `propose_ad`. Con el default: dos de tres agotaron `max_tokens` (4096) antes de terminar el JSON — uno de ellos es el mismo `62cd0e01` ya anotado como hallazgo el 2026-09-26 — y quedaron `degraded`. Con `thinking: {"type": "disabled"}`: los tres bajaron a 455-554 tokens de salida (7-8x menos), JSON completo y válido en los tres, contenido de las filas revisado a mano y sensato (producto/precio/evidencia coherentes con el texto). `propose_ad` ahora manda `thinking={"type": "disabled"}` en la llamada.
Descartado: el diseño original de la tarea 7 (glosario editable + recorte del texto de entrada) apuntaba a bajar tokens de ENTRADA; esta medición muestra que el costo no explicado estaba del lado de SALIDA. No se descarta el glosario/recorte — sigue pendiente como su propia decisión — pero se midió el `thinking` primero por ser más barato de confirmar (sin backtest completo) y por resolver de paso el truncamiento en producción.
Consecuencia: el costo real medido en la tarea 5 (mediana $0,0128/anuncio, `docs/costos.md`) se midió con `thinking` en su default — de acá en más debería bajar, cerca del `$0,0073` del backtest original o menos. Falta re-medir con `llm_proposals` una vez que este fix esté en producción real (no son los 3 anuncios de esta prueba, que no se guardaron como propuestas nuevas). El glosario editable y el recorte del texto de la tarea 7 siguen pendientes como decisión aparte.

## 2026-09-27 — Fix: el prompt aclara que "por mayor" en el título no alcanza para wholesale (tarea 8)
Contexto: el backtest v4 (2026-09-26) encontró 2 filas de arroz/azúcar "por mayor" a 50 kg mal etiquetadas `sale_format=wholesale` — el modelo dejó que la palabra "mayor" del título decidiera, aunque la fila describía un solo saco (regla ya existente en el prompt: "vender por saco... sigue siendo retail"). Antes de gastar en la re-medición se verificó que el fix del `thinking` (mismo día) no lo hubiera resuelto de paso: no lo resolvió, seguía fallando igual contra el anuncio real.
Decisión: se agregó una cláusula a la regla de `sale_format` con un ejemplo concreto (título "OFERTA ESPECIAL — POR MAYOR" vendiendo "el saco de 50 kg" con "contenedores de 600 sacos" disponibles → la fila del saco es `retail`), mismo patrón que ya usa la regla de `price_basis` (ejemplo "Pollo Muslo 4x10"). Un primer intento sin ejemplo concreto (solo la regla en abstracto) NO alcanzó — verificado contra el mismo anuncio real antes de gastar en el backtest completo. `PROMPT_VERSION` subió a `2026-09-27.1`.
Verificación (antes del backtest, 3 llamadas sueltas): (1) el anuncio real del hallazgo → `retail` (antes `wholesale`). (2) un caso análogo con otro producto y otros números (pollo/pallets en vez de arroz/contenedores) → `retail`, confirma que generaliza y no memoriza los números del ejemplo. (3) un caso de venta genuina del lote completo (contenedor entero, sin venta por saco suelto) → sigue `wholesale`, confirma que no hay sobrecorrección.
Re-medición (backtest v5, 100 anuncios, `--limit 100`, mismo seed=42 que v4, $0,27 reales — la estimación previa al fix de `thinking` daba $0,44): formato de venta 97,1% (68/70) contra 90,0% (63/70) de v4 — las 5 filas que se corrigen son justamente los casos "por mayor". Extracción completa sin cambios, 71,4% (50/70) — el campo no entra en esa métrica. Costo real 3,4x más bajo que v4 ($0,91→$0,27), consistente con el fix de `thinking` del mismo día. Quedan 2 errores de `sale_format`, de otro tipo (ítems de una sola unidad chica, no el sesgo del título) — no eran el objetivo de esta tarea.
Descartado: nada — la re-medición confirmó el fix sin efectos secundarios negativos visibles en las demás columnas (dentro del ruido esperable de una muestra de 70 filas).
Consecuencia: `data/llm-backtest-results-v5.jsonl`, `-calls-v5.jsonl` y `-report-v5.md` quedan como la medición vigente, sin pisar v4 (queda como referencia histórica pre-fix). Reportes anteriores a v5 corresponden a prompts previos a `2026-09-27.1`.

## 2026-09-27 — Glosario del operador (slice 3c, parte 1)
Contexto: la nota del operador se guarda desde el slice 3a (`llm_reviews.note`) pero se pierde en el anuncio que la generó — no hay forma de promoverla a regla general sin editar `_SYSTEM_PROMPT` a mano y desplegar. Toca esquema (tabla nueva): se especificó antes de programar en `docs/spec-slice-3c.md`.
Decisión: tabla `glossary_entries` (migración `0023`, editable — a diferencia de `llm_proposals`/`llm_reviews`) con CRUD en `/catalog` (mismo patrón que `ProductFormat`: alta, edición, desactivar sin borrar, borrar). Las entradas activas se inyectan en el bloque cacheado de `build_prompt_blocks` de `llm_propose_ad` (junto a los catálogos, no en el bloque del anuncio); sin entradas activas no agrega texto. El operador pidió que "cada cambio del glosario cambie la versión del prompt" (requisito del plan); en vez de una versión a mano, `saimc.glossary.effective_prompt_version` calcula `f"{PROMPT_VERSION}+glossary:{hash}"` automáticamente a partir del contenido activo (orden por `term`, sin depender de cuándo se insertó cada entrada). `AdProposal` gana el campo `prompt_version` (antes `llm_store.save_proposal` importaba la constante del módulo; ahora lee `result.prompt_version`).
Descartado: (1) versión a mano — el operador sube `PROMPT_VERSION` cada vez que edita el glosario; depender de la memoria de alguien no garantiza el requisito, decisión tomada con el operador antes de programar. (2) migrar el ejemplo ya hardcodeado de "Pollo Muslo 4x10" del `_SYSTEM_PROMPT` al glosario como dato semilla — ese texto ya está backteado y fijado por un test de regresión (prompt `2026-09-27.1`, formato de venta 97,1%); tocarlo no aporta nada y arriesga una regresión sobre algo ya medido. La tabla arranca vacía.
Consecuencia: cubierto en `tests/test_glossary.py` (lógica pura: sección del prompt y hash de versión), `tests/test_llm_propose_ad.py` (integración con `propose_ad`, incluida la versión base sin llamada real), `tests/test_catalog.py::TestCatalogoGlosario` (CRUD) y `tests/test_processing_layer.py::TestGlossaryEntriesMigration`. Queda pendiente, sin bloqueo, la otra mitad de 3c: el recorte del texto de entrada (`docs/plan-asistente-llm-cola-completa.md`).

## 2026-09-27 — Recorte del texto (slice 3c, parte 2): implementado, medido, RECHAZADO
Contexto: motivada primero por costo (bajar tokens de entrada), la medición contra `llm_proposals`/el backtest v5 mostró que la entrada es hoy ~50-85% del costo por llamada, concentrado en la cola de anuncios largos (20 llamadas de 100 concentran 47% del gasto) — justificaba intentarlo. `trim_text_for_model` (`llm_propose_ad.py`) implementado según el diseño del plan: conserva la línea que matchea un producto del foco (`engine._match_product`) más la línea siguiente SI trae un precio (`_extract_price`, para escalones de precio por volumen que no repiten el producto), más los encabezados que declaran moneda o unidad para todo el anuncio; sin ningún producto reconocido manda el texto entero. Apagado por defecto (`trim: bool = False` en `propose_ad`, sin pasarlo en `/curation` ni `prefill`); expuesto en el backtest como `--trim` para poder validarlo aparte, tal como pedía el plan ("el recorte entra solo si no pierde filas").
Decisión: **no se activa.** Backtest v6 (66 anuncios de texto, mismo oro y semilla=42 que v5, $0,15 reales) contra la referencia v5 (sin recorte, mismo prompt `2026-09-27.1`): extracción completa 71,2% (37/52) contra 76,9% (40/52) de v5 — 3 filas correctas de menos. Cantidad y unidad cayó de 96,2% (50/52) a 84,6% (44/52). Causa encontrada en un caso real (`ce7f3287…`):
```
🍗 Picadillo MDM de Pollo Gold Creek

📦 50 lb

💵 31 USD | 21 235 CUP
```
La regla de "línea siguiente" solo mira `lines[i+1]`; acá hay una línea en blanco entre el producto y su cantidad (patrón habitual: emoji, campo, línea en blanco, campo siguiente), así que `📦 50 lb` se pierde. La línea de precio sobrevivió por una razón distinta y no querida: el detector de «encabezado de moneda» la marcó como tal solo por contener «USD»/«CUP», aunque es la oferta puntual, no un encabezado general — ese detector es demasiado permisivo.
Descartado: arreglar la heurística de adyacencia (ampliar a 2-3 líneas siguientes, o agrupar por párrafo separado por líneas en blanco en vez de por línea) — es un rediseño, no un ajuste, y validarlo exige otra corrida paga sin garantía de que cubra todos los formatos reales. Con el ahorro de entrada ya moderado (no el 77% que sugería una muestra de n=1 mal leída) y esta regresión medida, no se justifica seguir iterando ahora.
Consecuencia: `trim_text_for_model`, el flag `trim` de `propose_ad` y `--trim` del backtest **quedan en el código, documentados como intento rechazado**, apagados en los tres llamadores de producción — cero efecto en lo que corre hoy. Cubierto por `tests/test_llm_propose_ad.py::TestTrimTextForModel` (8 casos sintéticos) y los tests de wiring en `test_llm_propose_ad.py`/`test_llm_backtest.py`. Si se retoma, empezar por el rediseño de párrafo, no por ampliar el rango de líneas. Datos de la corrida: `data/llm-backtest-{results,calls,report}-v6.jsonl/.md` (gitignorados, quedan en el VPS/checkout del operador, no en el repo).
## 2026-09-27 — Cintillo: acento de color por producto, no por categoría real
Contexto: se pidió más color en el cintillo del Tablero. Se armaron tres propuestas visuales en un canvas de Claude Design y el operador eligió la A (categorías con acento). El catálogo de productos tiene `category` en el modelo, pero no está poblada en el piloto (`_DEFAULT_CATEGORY = "sin categoría"` en `routers/catalog.py`), así que no había con qué colorear por categoría real sin inventar dato.
Decisión: `TickerItem.slot` (0–3), calculado con `zlib.crc32(nombre) % 4` — no con `hash()` built-in, que varía entre procesos por `PYTHONHASHSEED` y rompería la estabilidad entre workers. Es estable por producto, no por posición: si el orden del cintillo cambia (cambia el volumen de anuncios de la ventana), el color de un producto no cambia con él. Cuatro tokens nuevos `--m3-acento-0..3` en `design_tokens.css` (con su versión oscura), deliberadamente distintos de `--m3-prov-*` para no confundir "acento decorativo" con "identidad de provincia". El mockup del canvas usaba un layout de dos líneas y una franja más alta (~100px); se implementó en una sola línea dentro de los 2.75rem/44px ya fijados (comentario de `saimc-public.css`, decisión de accesibilidad táctil), agregando `border-top: 3px solid var(--m3-primary)` con `box-sizing: border-box` para no crecer el alto total. El rótulo pasó de gris (`--m3-surface-container`) a pastilla teal (`--m3-primary-container` / `--m3-on-primary-container`, token que ya existía y se usaba en la barra de vistas).
Descartado: (a) colorear por `category` real — hubiera exigido poblar el catálogo o inventar una taxonomía, y `category` no viaja hoy desde `build_ticker` (no tiene sesión de DB); queda para si el catálogo llega a tener categoría real. (b) slot por posición en la lista ordenada (primer intento) — descartado antes de cerrar el slice porque el color de un producto cambiaría de un día a otro según cuántos anuncios tuvo cada uno, rompiendo la asociación visual «Arroz es este color». (c) el layout de dos líneas del mockup — crece el alto del cintillo en las tres vistas (mapa, tablero, explorar) y en mobile, sin que el operador pidiera ese trade-off explícitamente.
Consecuencia: el acento sigue siendo decorativo (no hay categoría real detrás), pero ahora es una identidad estable por nombre de producto. Si el catálogo llega a poblar `category`, migrar de CRC32-por-nombre a color-por-categoría es un cambio contenido a `ticker_view.py` y la paleta de `design_tokens.css`.

## 2026-09-28 — Fix de tres bugs de `extraction/engine.py` hallados en revisión (1, 7 y 9)
Contexto: una revisión profunda de `engine.py` (2026-09-28) encontró nueve bugs; el operador eligió arrancar por los tres más caros. (1) `_normalize_number` devolvía `None` para todo precio con miles y decimales («$25,750.00», «1,986.36 cup», «12.500,50») y `_extract_price` no probaba otro patrón: el formato de los mayoristas perdía el precio. (7) `guess_published_date` lanzaba `ValueError` sin atrapar al correr un «29 de febrero» a un año no bisiesto, y tumbaba la vista de curaduría de ese anuncio. (9) Un alias vacío o en blanco en `Product.aliases` compilaba a `\b\b` y matcheaba todas las líneas; uno nulo lanzaba `AttributeError` en cada anuncio.
Decisión: (1) con los dos separadores en el mismo número, el de más a la derecha es el decimal y el otro es de miles, siempre que los miles vengan en grupos de tres; lo que no calza sigue devolviendo `None`, como antes. (7) se prueba el año de la captura y después el anterior, saltando el año donde la fecha no existe; gana la primera que no pase la captura, y si ninguna, `None`. Eso además resuelve bien «29 de febrero» capturado en enero de un año no bisiesto (antes `None`). (9) `_match_product` saltea todo alias que no sea texto con contenido. Casos definidos a mano antes del código, en `tests/test_extraction.py` (`TestPrecio`, `TestFechaPublicacion`, `TestAliasVacioONulo`); se vio el RED de los nueve antes del arreglo.
Descartado: (9) validar los alias al guardarlos en `/catalog` — no alcanza, porque el catálogo también se edita con DML a mano en producción, y el motor tiene que sobrevivir a lo que haya en la tabla. Filtrar también al guardar sigue siendo buena idea, pero es otro slice.
Consecuencia: quedan abiertos los otros seis bugs de la revisión (el «N o M» leído como precio doble, patrones de precio que `_guess_product_phrase` no conoce, la guarda de bloque escalonado solo con match de catálogo, unidades sin canonizar en `_declared_price_basis`, mínimo de lote sin unidad, división por cero con cantidad 0) y el costo de recompilar regex por línea. `llm_propose_ad.py:412` y `llm_assist.py:213` también recorren `p.aliases` para matchear la respuesta del modelo, sin este filtro: no se revisaron en este slice.

## 2026-09-28 — Fix de los bugs 2 y 3 de la revisión de `engine.py`: «N o M» y nombre candidato
Contexto: (2) `_DUAL_PRICE_TWO_CURRENCIES_RE` leía cualquier «N o M» como precio en dos monedas y le ganaba a todo otro patrón: «Pollo 3 o 4 lb a 5000» daba precio 4 y «Combo para 2 o 3 personas a 4500» daba 3. (3) `_guess_product_phrase` no conocía las marcas de precio `850$` ni «1.20 o 850», así que una línea sin producto de catálogo quedaba con precio pero sin nombre y `propose_all` la descartaba.
Decisión: (2) el segundo número del par no puede ir seguido de una palabra (solo espacio horizontal; un salto de línea no califica al número), y `(?![\d.,])` evita que el backtracking recorte «30 personas» a «3». (3) el nombre candidato corta también en `_PRICE_DOLLAR_SUFFIX_RE` y en el precio doble. Casos definidos a mano en `tests/test_extraction.py` (`TestPrecio`, `TestNombreCandidatoConPrecioSinMarcaDelantera`); se vio el RED de los de «N o M» y de «850$» antes del arreglo.
Descartado: que el número suelto sin ninguna marca («Papas prefritas 2500») también dé nombre, como proponía la revisión. Se probó y rompió dos tests existentes: la línea «📍 Tienda … Km 34» y una URL pasaban a ser ofertas. Que esas líneas no tengan nombre es justamente lo que hoy las filtra, y la dirección con «Km 34» fue el 60% de los precios equivocados medidos el 2026-09-10 (`TestCatalogoNoEsBloqueEscalonado`). El commit `e453996` buscaba llenar el campo precio, no conservar líneas sin nombre. `test_numero_suelto_sin_producto_sigue_descartado` fija el comportamiento.
Consecuencia: quedan abiertos los bugs 4 a 6 y 8 de la revisión (bloque escalonado sin match de catálogo, unidades sin canonizar en `_declared_price_basis`, mínimo de lote sin unidad, división por cero) y el costo de las regex. Proponer líneas con número suelto y sin producto sigue siendo posible, pero es otra decisión y hay que medirla contra la cola real.

## 2026-09-28 — Generador de tarjetas para redes: «Dato del día» (`/marketing`)
Contexto: la campaña de marketing (2026-09-26) diseñó 7 plantillas HTML para redes, pero armar cada tarjeta significaba copiar cifras a mano desde el sitio — con el riesgo ya anotado en el propio doc de campaña de repetir la trampa del promedio simple (arroz mezclando presentaciones da ~1,3 millones). El operador pidió una herramienta interna, no un servicio externo: generar y descargar la imagen, sin auto-publicar (esa parte se descartó, ver abajo).
Decisión: `/marketing` (sin `require_operator`, mismo criterio que `/control`: lectura, no muta curaduría). `saimc/marketing/cards.py::build_dato_del_dia` es puro y arma el contexto de la primera de las 7 plantillas («1 · Dato del día») a partir de las `ProductCard` que ya calcula `card_view.build_cards` — el mismo cómputo del tablero público, cero promedios nuevos. Un producto puede tener varias `ProductCard` por provincia (una por formato/variante, `RetailRange.format_id`); se muestra la de más muestra (`build_cards` ya ordena por conteo descendente), sin sumar formatos entre sí. La barra de rango usa una escala 0-100% común entre las provincias mostradas (no una escala independiente por fila, que es lo que sugería el placeholder de la plantilla pero sin una regla clara detrás). `saimc/marketing/render.py::render_png` usa Playwright (import perezoso, extra opcional `marketing` en `pyproject.toml`) para convertir el HTML ya armado en PNG 1080×1350; sin el paquete o sin Chromium, `/marketing/card.png` degrada a 503 con las instrucciones de instalación, la vista previa en HTML sigue andando.
Descartado: (1) publicar directo a Facebook/Instagram desde el sistema — la API de Meta pide revisión de la app (semanas), no encaja con la ventana de dos semanas del operador y el propio plan de campaña ya asume publicar a mano desde Meta Business Suite. (2) renderizar sin navegador (`weasyprint`/`imgkit`) para evitar la dependencia de Chromium — las 7 plantillas usan flexbox y SVG que esas herramientas no soportan bien, y perder fidelidad con el diseño ya aprobado no vale la pena solo para ahorrarse una instalación de sistema. (3) agregar entre formatos distintos de un mismo producto+provincia para dar una sola cifra "de verdad" — ningún otro lugar del sitio lo hace, y sumar unidades de formatos distintos inventaría una equivalencia que nadie definió.
Consecuencia: primera corrida en el VPS (`ubuntu-4gb-hel1-1`, Hetzner) sin Chromium instalado — hay que correr `.venv/bin/pip install -e ".[marketing]"`, `.venv/bin/playwright install chromium` y `sudo .venv/bin/playwright install-deps chromium` antes de poder exportar el PNG; la vista previa en HTML no depende de eso. Cubierto en `tests/test_marketing_cards.py` (lógica pura, sin base) y `tests/test_marketing_router.py` (router con datos reales vía fixtures, incluido el camino degradado sin Chromium). Los otros 6 formatos del canvas (`marketing-design/project/*.dc.html`) quedan para después de verificar que este primero ande bien en producción. El dominio final del sitio (`SITE_URL_PLACEHOLDER` en `routers/marketing.py`) sigue sin decidir — pendiente del propio doc de campaña.

## 2026-09-29 — Generador de tarjetas: las 9 piezas del canvas (`/marketing`)
Contexto: el operador verificó «Dato del día» en producción (con Playwright ya instalado) y pidió sumar de una el resto del canvas de campaña. El canvas tiene 9 tarjetas postables, no 7 (el tablero «Identidad» es referencia de diseño, no una pieza) — cifra corregida acá.
Decisión: `saimc/web/routers/marketing.py` pasa de una ruta fija a un registro `CARD_TYPES` (slug, plantilla, tamaño, si necesita producto/provincia); `/marketing/preview/{kind}` y `/marketing/card/{kind}.png` reemplazan las rutas sin `{kind}` del slice anterior (recién desplegado el mismo día, sin nada externo enlazando esas URLs todavía). Tres cifras nuevas en `saimc/marketing/cards.py`, todas puras y reusando cómputo ya existente: (a) `build_como_se_movio` reusa `price_charts.build_month_vs_month` — el mismo «¿subió o bajó?» del panel flotante — con coordenadas Y propias para el SVG 872×380 de la tarjeta (no las `SLOPE_*` del panel chico, pensadas para 360×176); (b) `build_de_donde_sale` lista los anuncios reales más recientes de un producto en una provincia, cruzando formatos a propósito (a diferencia de «Dato del día», acá la explicación es «varios anuncios, un rango», no un formato puntual). Las 6 piezas restantes (portada, consejo, story, portada de Facebook, cómo funciona, cierre) son branding fijo — sin producto ni provincia — con el detalle de que Story y Portada de Facebook ahora listan `pilot_product_names` real en vez de los cinco nombres hardcodeados del diseño original.
Descartado: (1) armar el enlace real "ver anuncio" en «De dónde sale este precio» — la plantilla original lo mostraba, pero decidir si conviene exponer un link público a un anuncio real es una decisión de privacidad aparte, sin tomar; la fila queda siempre en «captura». (2) sumar el badge "PLANTILLA · cifras de ejemplo" tal cual en las piezas ya listas para publicar (cómo leer los precios, cómo funciona) — se cambió a "EJEMPLO ILUSTRATIVO": esas dos explican un concepto general con cifras de ejemplo a propósito, no son un borrador de diseño todavía sin terminar.
Consecuencia: 21 tests nuevos en `test_marketing_router.py` (uno por tarjeta, más los casos de "sin comparación esta semana" y "provincia fuera del foco") y 16 en `test_marketing_cards.py`. Suite completa: 2545 passed. Las URLs viejas (`/marketing/preview?product=`, `/marketing/card.png?product=`) del slice del 2026-09-28 ya no existen — no había nada externo apuntándoles.

## 2026-09-29 — Registro tardío: Jev y json-render, descartados el 2026-09-21
Contexto: el 2026-09-21 el operador evaluó dos herramientas de un post sobre Generative UI: json-render (Vercel Labs, JSON→UI en React/Vue/Svelte) y Jev (TypeSafe AI, modelo «System One» de decisiones estructuradas). A Jev lo evaluó dos veces, primero como reemplazo del asistente LLM de extracción y después como pieza junto a WAHA o corriendo en el VPS. Las dos decisiones quedaron solo en Engram; se registran acá para que vivan en git.
Decisión: no adoptar ninguna. json-render choca con la decisión cerrada 1 (sin SPA; Jinja2 + HTMX). Jev no extrae números ni fragmentos, así que no puede reemplazar al asistente. Además es solo una API alojada en acceso anticipado, sin pesos para el VPS (Hetzner, 2 vCPU, sin GPU), trabaja solo con texto y no documenta español. Junto a WAHA no aporta nada: WAHA ya entrega JSON estructurado, Jev apunta a agentes de navegador y el scraping está vetado por la decisión cerrada 2.
Descartado: adoptar Jev por su precio, ~$0,02 por mes contra ~$1,86 de Sonnet 5 al volumen de entonces (`docs/costos.md`). El asistente ya costaba poco y el ahorro no justificaba sumar un proveedor.
Consecuencia: Jev no se reabre salvo que publique pesos autoalojables o soporte de español verificado. La entrada siguiente le abre otro papel sin integrarlo.

## 2026-09-29 — Jev como señal de confianza: prueba fuera de línea, siempre desconectable
Contexto: el estudio `docs/estudio-factor-humano.md` concluye que, para sacar al operador del camino de cada registro, falta una confianza calibrada que decida qué anuncio va a revisión humana, y que la confianza que declara un LLM no sirve para eso. Jev promete justamente probabilidades calibradas para decisiones cerradas: sí/no o elegir de una lista. El operador pidió sumarlo al estudio.
Decisión: Jev entra al estudio como prueba fuera de línea (A6) contra las decisiones de triage que el operador ya tomó, sin integrarse al sistema. Si algún día se integra, será como señal aditiva y desconectable: detrás de una interfaz propia, apagable por configuración y con tope de gasto. El enrutamiento base se calibra sin Jev. Si Jev falla, cierra o se deja de pagar, rige esa calibración base, sin error y sin frenar la ingesta ni la cola.
Descartado: ponerlo en el camino crítico, es decir, que la cola o la aceptación automática dependan de su respuesta. Sumaría un punto único de falla en un proveedor en acceso anticipado, con precio de lanzamiento y sin español documentado.
Consecuencia: es un costo más (del orden de $0,0001 por anuncio con cinco preguntas, estimado) y un proveedor más que recibe texto redactado. Las condiciones de la entrada anterior, pesos autoalojables o español verificado, siguen sin cumplirse. Lo que puede cambiar la decisión es la prueba A6 sobre anuncios propios.

## 2026-10-02 — Jev: A6 se mantiene y se amplía; la integración queda en A6b, en sombra
Contexto: ya hay clave de Jev. Se propuso reemplazar A6 (la prueba fuera de línea) por un slice que integra a Jev como validador tipado del extractor, con un orquestador propio que acepta, manda a revisión o descarta según umbrales τ₀ y τ₁.
Decisión: A6 no se reemplaza; pasa de BLOQUEADO a PENDIENTE y suma preguntas por campo cerrado (producto, moneda, provincia, formato de venta) contra las filas curadas. La integración entra como A6b en `docs/plan-factor-humano.md`: un adaptador desconectable que alimenta al motor de A5b como una señal más, en modo sombra, después de B1. El contrato de la señal se especifica en A5a.
Descartado: integrar sin medir antes. Los umbrales se aplicarían sobre una probabilidad cuya calibración en español cubano nadie verificó. Del borrador también se descartó el puntaje global de confianza (es la confianza autodeclarada que el estudio ya descarta), la pregunta de coherencia del precio contra el mercado (Jev no lo conoce; es la regla 2), el orquestador aparte del motor y escribir `origin = auto` antes de G1.
Consecuencia: los umbrales de Jev, si llega a entrar en un carril, se fijan en G1 con su n. A6 conviene cerrarla antes de A5a. Si A6 sale desfavorable, A6b se descarta y la entrada del 2026-09-29 queda como está.

## 2026-10-02 — A0: tests de Chromium sin depender de la máquina y costo por anuncio re-medido
Contexto: 2 tests de `/marketing` esperaban un 503 «sin Chromium» y fallaban en el VPS, que sí lo tiene. Además `docs/costos.md` y el runbook citaban $0,0128 por anuncio, medido cuando `propose_ad` corría `thinking` sin que se viera.
Decisión: los tests simulan un Chromium que no arranca con un `playwright` falso en `sys.modules` (fixture `sin_chromium`), de modo que ejercitan `render_png` real y no dependen de la instalación. El costo se recalculó con `llm_proposals` posteriores al fix (prompt `2026-09-27.1`, 101 llamadas): $0,0029 de promedio, $0,0044 texto y $0,0016 imagen. El runbook pasa a `--max-cost 2` para el backlog.
Descartado: parchear `render_png` en el router, porque dejaría sin probar la traducción de la falla de Chromium a `RendererUnavailable`. Tampoco se usó la mediana de texto ($0,0023): `--max-cost` corta por suma.
Consecuencia: muestra de dos días, a re-medir con más volumen. No se tocó código de producción.

## 2026-10-02 — A2a: bitácora de curaduría en tabla propia de solo altas (`curation_events`)
Contexto: no se puede medir el tiempo humano por anuncio. `shown_at` solo se guarda al curar con propuesta a la vista; el descarte sin propuesta, el lote sin foco y la eliminación de un curado no guardan ninguna hora, y editar o eliminar un curado no deja rastro. El mapa del código corrigió dos hechos del plan: el descarte con propuesta sí guarda hora (en `llm_reviews.created_at`), y `discard_curation` no «ignora» `shown_at`: tres de los cuatro formularios de descarte no lo mandan.
Decisión: `docs/spec-slice-A2.md`. Una fila por decisión del operador (`curated`, `discarded`, `deleted`, `edited`), con modo (`individual`, `batch_out_of_focus`), `shown_at`, `decided_at` y un `detail` en JSON que para editar y eliminar guarda el valor anterior. Se escribe en la misma transacción que el cambio. El reloj lo sella el servidor y «Completar con IA» deja de reiniciarlo. Las duraciones se guardan sin recortar; el recorte se decide al medir (A2d). No reemplaza a `llm_reviews`. Sin backfill.
Descartado: una columna `decided_at` en `raw_ingestions` (una sola hora por anuncio, no distingue lote de individual, no sirve para auditar ni para el historial de `/edit`). Tampoco se extendió `llm_reviews`, que exige una propuesta y la mayoría de los descartes no la tiene, ni se agregó `batch_id`: el lote se reconoce por `(mode, decided_at)`.
Consecuencia: A2b implementa con la migración 0024. Los tiempos de `llm_reviews.shown_at` posteriores a A2b no son comparables con los anteriores donde se pidió asistencia a mano. `catalog.delete_format` y los backfills por CLI siguen mutando `prices_history` sin rastro; quedan en «Pendientes sueltos» del plan.

## 2026-10-02 — A2b: la bitácora `curation_events` entra en producción y desde acá corre la línea de base
Contexto: A2a especificó la bitácora (`docs/spec-slice-A2.md`); faltaba escribirla desde los cinco caminos de la cola. Sin esa hora no hay línea de base de horas por 1.000 anuncios (A2d), ni auditoría de los carriles automáticos (B2), ni historial en `/edit` (K1).
Decisión: migración `0024` y modelo `CurationEvent`; un ayudante único de alta (`saimc/curation_events.py`) llamado desde `submit_curation`, `discard_curation`, `discard_out_of_focus`, `delete_curated` y `submit_edit`, siempre antes del único `db.commit()` del camino. Es una tabla propia de solo altas y no reemplaza a `llm_reviews`. El reloj `shown_at` lo sella el servidor, viaja en un campo oculto (ahora también en los tres formularios de descarte, en `/curation/sin-foco` y en `/curation/{id}/edit`) y sobrevive a «Completar con IA». Un `shown_at` vacío o malformado queda nulo; sin zona se toma como UTC. Las duraciones se guardan sin recortar y un 422 no escribe evento. Una edición sin cambios escribe su evento con `rows` vacío; eliminar un curado no pide motivo.
Descartado: lo ya descartado en A2a (columna en `raw_ingestions`, extender `llm_reviews`, `batch_id`, sellar la hora en el navegador). En el `detail` de una edición se comparan los valores por valor y no por texto, para que «10.00» y «10» no cuenten como cambio.
Consecuencia: la línea de base corre desde la fecha de este slice; no hay backfill, así que lo anterior no tiene hora. Los `llm_reviews.shown_at` posteriores a A2b no son comparables con los anteriores en los anuncios donde se pidió asistencia a mano. Un descarte con propuesta a la vista sigue escribiendo `llm_reviews` sin `shown_at` (no se tocó esa tabla).

## 2026-10-02 — A2c: «Aceptación sin revisar» se recalcula de `proposed` y `final`, con cuatro categorías
Contexto: la métrica de `/control` leía el `edited` guardado en `llm_reviews.rows`, que `llm_store._edited` deja vacío si la fila se borró: un rechazo contaba como «sin editar». Además la provincia fija del canal (que pisa la propuesta del modelo) contaba como edición del operador, y completar una abstención del modelo contaba igual que corregir un valor. Mostraba 80 % (106/132); sin las filas borradas, 78,3 % (94/120).
Decisión: `web/rubber_stamp_metrics.py` recalcula todo de `proposed` y `final`, que están guardados y no cambian, así que corrige también el pasado sin tocar filas inmutables. La unidad es la celda (fila propuesta × campo propuesto) y cae en una de cuatro categorías: sin cambios, completó un vacío, corrigió un valor, borró la fila. La tasa «sin editar» es sin cambios / todas las celdas, así que lo borrado ya no cuenta como aceptado. La provincia se excluye de las revisiones cuyo canal la fija (se resuelve con el canal de hoy). `/control` muestra el desglose. `llm_store._same` pasó a `same_value` para reutilizar la comparación por valor («10.00» = «10»).
Descartado: corregir `llm_store._edited` y dejar de leer solo el campo nuevo: el pasado seguiría mal y habría que reescribir filas inmutables. También se descartó contar por fila y no por celda: mezclaba una fila con un campo corregido y otra con ocho.
Consecuencia: el porcentaje de `/control` baja respecto del anterior y no es comparable con el histórico mostrado antes de este slice. La alerta direccional (sube «sin editar» y baja el tiempo) sigue igual pero ahora sobre la tasa corregida. Los descartes siguen sin `shown_at` en `llm_reviews`: el tiempo por decisión de descartes sale de `curation_events` (A2b), no de acá.

## 2026-10-02 — La redacción de teléfonos cubre el número agrupado anunciado con palabra de contacto
Contexto: `redact_personal_data` ya cubría el número agrupado con prefijo (`+53 5 234 5678`, `b574d0d`) y el suelto de ocho dígitos pegados. Quedaba un hueco: sin prefijo y con separadores, como «llamar al 52 34 56 78». La rama `worktree-prompt-caching` (2026-09-26) lo había medido en los 1.381 textos guardados: exactamente 1 teléfono escapado y ningún falso positivo. Esa rama no se pudo mergear (conflictos: `master` rehízo el filtro de foco y el prefijo agrupado por su cuenta) y se rescató solo lo que faltaba.
Decisión: un patrón nuevo en `saimc.privacy`. Un número de ocho dígitos desde 5, con separadores tolerados, se redacta si lo precede una palabra de contacto (`tel`, `teléfono`, `tlf`, `cel`, `móvil`, `whatsapp`/`watsap`/`wsp`, `contacto`, `llame`…) a lo sumo 15 caracteres no numéricos antes. Solo se reemplaza el número; la palabra queda. La palabra no puede ir en medio de otra («hotel»). Casos escritos a mano, positivos y negativos (precios, rangos, «Cel 5 kg»).
Descartado: redactar todo run agrupado de ocho dígitos desde 5, porque es indistinguible de dos precios seguidos y comerse un precio destruye la señal que el sistema mide. También se descartó mergear la rama entera por el flag `--include-off-focus` del prefill: es un extra sin urgencia y la rama queda como referencia.
Consecuencia: un número agrupado sin prefijo ni palabra de contacto sigue sin redactarse (límite declarado en el código). El texto ya guardado se limpia con `scripts/redact_existing.py --apply` (dry-run primero, mismo procedimiento del 2026-08-11: se redacta la columna, el archivo en `data/raw/` no se toca). Lo que se mandó al modelo antes de este cambio no se puede retirar.

## 2026-10-02 — A1b: primer lote con el veredicto del modelo (imágenes), sin apartarse de spec.md §6.1
Contexto: descartar imágenes una por una es el 32–41 % del tiempo humano (estudio del plan, §1). El modelo ya propone `discard` para cada imagen con propuesta `ok`, pero hasta hoy cada una costaba abrirla y un clic. Es la primera vez que un lote se arma con el veredicto del modelo y no con una regla determinista (como `/curation/sin-foco` de textos, 2026-08-31).
Decisión: segmento «Imágenes» dentro de `/curation/sin-foco` (`/curation/sin-foco/imagenes`), prototipo aprobado en A1a. Entran las imágenes `PENDING` cuya última propuesta `ok` dice `discard = True`; sin propuesta, degradada o que no descarta, quedan en la cola normal. Van **marcadas por defecto** y «Rescatar» las desmarca; lo desmarcado no viaja en el POST. El POST revalida cada id contra la misma selección (un id no elegible se ignora) y, en una transacción, pasa el anuncio a `DISCARDED` con el `discard_reason` del modelo, escribe `llm_reviews` (`outcome = discarded` y `proposal_id`, para que siga fuera de H0 como asistido) y un evento de bitácora `batch_model` (migración `0025` amplía el CHECK de `mode`). Si la propuesta descarta pero sin motivo (`discard_reason` nulo), la imagen NO entra al lote y sigue en la cola normal: el operador decidió (2026-10-02) no inventar el motivo por el modelo.
Descartado: apartarse de §6.1. El sistema propone y el operador confirma el lote viendo cada miniatura, el mismo contrato que `/curation/sin-foco` (que ya resolvió el 52 % de lo resuelto); el modelo nunca decide solo. Tampoco se dejaron desmarcadas por defecto: se perdía la velocidad que justifica el lote y el operador igual tiene que mirar la grilla.
Consecuencia: el riesgo es el sello automático (confirmar sin mirar). El freno hoy es solo «Rescatar» visible y la hora del lote en `curation_events`; la métrica A2c («Aceptación sin revisar») NO lo cubre, porque solo lee revisiones `curated`, y el descarte en lote no genera celdas. La auditoría por muestra de lo descartado recién existe desde B2. Las revisiones del lote guardan `rows` sin valor final, igual que un descarte individual con propuesta a la vista.

## 2026-10-02 — A7: la fecha de publicación de las fotos de WhatsApp se prellena con `captured_at`
Contexto: `_curation_view` prellenaba `captured_at.date()` solo para el texto de WhatsApp (2026-08-17). La foto no, aunque el webhook guarda en `captured_at` la hora del mensaje también para ella. No había motivo registrado: la entrada del 2026-08-17 es anterior a la captura de fotos; era una omisión. Cada imagen costaba teclear la fecha.
Decisión: una sola regla para texto e imagen (`_guess_published_date`). Fuente WhatsApp: en una imagen manda la fecha que traiga el OCR (`guess_published_date`) y, si no hay, `captured_at.date()`, incluso con el OCR vacío; en un texto sigue mandando `captured_at`. Otras fuentes: sin cambios (heurística de texto, vacío si no hay). El operador puede cambiarla siempre.
Descartado: prellenar `captured_at` también en la bandeja: ahí es el mtime del archivo, no una fecha confiable. Tampoco se hizo mandar a `captured_at` sobre el OCR en la foto: una captura reenviada trae la fecha del anuncio, que es la buena.
Consecuencia: A7 salió el mismo día que A1b; la medición de las semanas siguientes mezcla ambos efectos. Una foto de WhatsApp reenviada días después y sin fecha en el OCR queda prellenada con el día del reenvío; el operador la corrige al verla, como hoy con el texto.

## 2026-10-02 — A3a: las dos primeras convenciones del glosario
Contexto: el glosario tenía 0 entradas en producción y el modelo se abstenía en cantidad y unidad donde el operador ya sabe la respuesta. En la base local (418 revisiones, 3 notas) los vacíos de cantidad/unidad que el operador completó fueron 8: arroz 100 lb (4), azúcar blanca 100 lb (3), pollo 40 lb (3, «4x10»), azúcar blanca 25 kg (1). Las notas del operador dicen lo mismo.
Decisión: dos entradas, que el operador carga en `/catalog` → Glosario (`create_glossary_entry`). (1) Término «4x10»: «4 paquetes de 10 lb: la caja trae 40 lb en total.» (2) Término «Arroz o azúcar sin cantidad»: «Arroz o azúcar con precio pero sin cantidad ni peso: se asume saco de 100 lb. Si el anuncio dice otro peso (ej. 25 kg), manda el del anuncio.» La cláusula final existe por el caso de 25 kg: sin ella la regla contradice el 12 % de los vacíos observados.
Descartado: «sin peso = 100 lb» a secas. Un anuncio que sí trae el peso no es un vacío, y el modelo no debe pisarlo.
Consecuencia: el glosario es del operador, no del código: la carga en producción es manual y el efecto se mide en A3b con el backtest. La versión del prompt cambia sola al cargar entradas (`effective_prompt_version`), así que las propuestas anteriores quedan con la versión vieja.

## 2026-10-02 — A3b: el glosario llena los vacíos que apunta, pero una de sus reglas se sobreaplica
Contexto: A3a cargó dos entradas («4x10»; arroz o azúcar sin cantidad = saco de 100 lb salvo que el anuncio diga otro peso). Se corrió el backtest `--execute --limit 100 --seed 42` (Sonnet 5, 71 textos + 29 imágenes, costo real $0,27 de $0,44 estimados) contra la corrida v5 del 2026-09-27. Mismo prompt base `2026-09-27.1`; el glosario viajó (el registro de llamadas lo marca `+glossary:aa9d76dc` y el caché creció ~150 tokens).
Decisión: no se toca el código ni el prompt. Cifras v5 → v6 (n = 70 filas del oro, 47 anuncios): cantidad y unidad 62/70 (88,6 %) → 57/70 (81,4 %); extracción completa 50/70 (71,4 %) → 47/70 (67,1 %); anuncios sin fila del modelo 0/47 → 1/47; precio en null 1/70 → 3/70. Por tipo, cantidad y unidad: texto 96,2 % → 92,3 %, imagen 66,7 % → 50,0 %. Leídas fila por fila, las 8 filas con cantidad distinta: 2 son abstenciones que el glosario llenó bien (saco de arroz y de azúcar a 100 lb); 1 es una sobreaplicación del glosario; las otras 5 (conversión ml↔l, una fila omitida, una imagen leída distinto) no tienen relación con las entradas y son variación de una corrida a otra. Con n = 70 una diferencia de 5 filas no distingue el efecto del glosario de ese ruido: el porcentaje agregado no sirve para atribuirlo.
La sobreaplicación: un anuncio de imagen dice «Azúcar blanca (460 Ib)» y «Arroz Guyanez (350 Ib)», precios por libra; con el glosario el modelo propuso 100 lb por fila (en v5 había acertado 1 lb). La entrada dice «sin cantidad ni peso», y el «(460 Ib)» no se lee como peso sino como precio por libra.
Descartado: concluir «el glosario empeoró» por el −7 puntos agregado, y también «no hizo nada»: el efecto atribuible es +2 filas llenas y −1 por sobreaplicación, dentro del ruido.
Consecuencia: la entrada 2 necesita una cláusula que excluya el precio por libra; es decisión del operador y se carga a mano en `/catalog`. Los registros de `data/llm-backtest-results-v6.jsonl` guardan el `prompt_version` base sin el sufijo del glosario (solo el registro de llamadas lo trae): una corrida con glosario distinto no se distingue en los resultados. Para medir de verdad el efecto hace falta una comparación pareada con más filas, no una nueva corrida de 100.

## 2026-10-02 — A3b: cláusula de precio por libra en la entrada «Arroz o azúcar sin cantidad»
Contexto: el backtest v6 mostró que la entrada se sobreaplicaba a anuncios con «(460 Ib)», precio por libra (entrada anterior de A3b).
Decisión: se agrega al final de la definición: «Si el precio viene como «(460 lb)» o «/lb», es precio por libra: cantidad 1 lb.» Aplicada en la base local; en producción el operador la edita a mano en `/catalog` → Glosario.
Descartado: re-correr el backtest ahora. Con n = 70 una fila no se distingue del ruido entre corridas; el efecto de la cláusula se mira en la cola real o en una comparación pareada con más filas.
Consecuencia: el prompt efectivo cambia de versión (`effective_prompt_version`); las propuestas anteriores quedan con la versión vieja.

## 2026-10-02 — A4a: el panel por canal agrupa por id, descuenta lo fuera de foco y mide rendimiento y minutos
Contexto: la sección «Descarte por canal» de `/control` (`compute_channel_intake`) agrupaba por nombre (dos canales homónimos se fundían), no desglosaba por tipo de contenido ni por motivo, y comparaba contra el «<30 %» de `spec.md` §12.5 una tasa que incluía los fuera de foco, cuando la tarjeta global (`IntakeHealth`) ya los descuenta. En la base local, «Groupej Morales Surl» tiene 67 curados y 165 descartados, 154 de ellos fuera de foco: la tabla mostraba 71 % de descarte y la tasa de §12.5 es 14 % (11 sobre 78).
Decisión: `ChannelIntake` se agrupa por `channel_id`; desglosa texto e imagen (`StatusCounts`) y los descartes en fuera de foco, sin dato y sin motivo registrado. La tasa de descarte es la de §12.5 (sin los fuera de foco en numerador y denominador; sin motivo cuenta como «sin dato», lo conservador) y `above_objective` la compara con el «<30 %». Dos métricas nuevas: filas por anuncio resuelto (filas de precio de sus anuncios sobre curados más descartados) y, desde la bitácora, minutos humanos y filas por hora. El tiempo de una decisión es `decided_at − shown_at` (`decision_seconds`): se excluye si no hay `shown_at`, si es posterior a la decisión o si pasa de 15 minutos (`PAUSE_CAP_SECONDS`); un lote reparte su tiempo entre `batch_size` para sumarse una vez. Solo cuentan `curated` y `discarded`; `edited` y `deleted` no son tiempo de curar. Las filas por hora usan las filas de las mismas decisiones con tiempo medible.
Descartado: ordenar por pendientes (el orden por descartes absolutos no cambió); un umbral propio por canal (el único con veredicto sigue siendo §12.5); recortar las pausas sumando hasta un tope en vez de excluirlas, porque un tope inventa tiempo que nadie midió.
Consecuencia: los 15 minutos son una elección de medición, no del spec: A2d la reusa (`PAUSE_CAP_SECONDS`) para que `/control` cuente igual en todos lados y se puede cambiar en un solo lugar. Los minutos arrancan casi vacíos: la bitácora empezó a escribirse el 2026-10-02 (A2b) y hasta juntar días de uso el panel muestra «—» o pocos minutos. El resultado depende de que las decisiones nuevas traigan `shown_at`.

## 2026-10-02 — A4b: política de cada canal según el panel de A4a
Contexto: con el panel de A4a (`/control`, «Descarte por canal») se decidió qué hacer con cada canal. Cifras de la base local al 2026-10-02, filas de precio sobre anuncios resueltos (curados + descartados): Groupej Morales Surl 122/232, Tienda: The Boss 81/165, MEGALODON 19/89, La Carpa Blanca 21/366, SAN MIGUEL MAYORISTA 3 7/466, Habana ventas 0/229, Ventas Mayoristas (dos canales homónimos) 3/101 y 1/29, MAY SOLUTION PROVEEDORES 5/89, Productos en Plaza 3/80, MIPYME GUAMAY 0/220 (ya inactivo), Venta mayorista Por Contenedor 0/1.
Decisión: **mantener** Groupej Morales Surl, Tienda: The Boss, MEGALODON, La Carpa Blanca y Venta mayorista Por Contenedor (esta última hasta tener datos: tiene un solo descarte). **Desactivar** en `/catalog` (`is_active`) SAN MIGUEL MAYORISTA 3, Habana ventas, los dos Ventas Mayoristas, MAY SOLUTION PROVEEDORES y Productos en Plaza. MIPYME GUAMAY sigue inactivo. Ningún canal pasa a «solo texto».
Descartado: «solo texto» para los canales con muchas imágenes (The Boss, Productos en Plaza, San Miguel): en el panel el problema de estos canales no es el formato sino que casi nada de lo que traen entra al foco. A4c queda sin hacer.
Consecuencia: desactivar solo corta lo nuevo; lo pendiente de esos canales (por ejemplo las 20 imágenes de Productos en Plaza y 10 de San Miguel) sigue en la cola y lo resuelve el lote de imágenes de A1b. Con la Fase 0 centrada en productos piloto, un canal mayorista vuelve a convenir solo si el foco cambia: reactivarlo es cambiar `is_active`. La política se revisa cuando el panel junte días de minutos de bitácora, porque las decisiones de hoy se tomaron sobre filas y descartes, no sobre tiempo humano.

## 2026-10-02 — Vía rápida: la compuerta G1 se parte en dos y el carril 0 se adelanta
Contexto: el operador necesita un MVP automatizado que mostrar y no puede esperar las tres a seis semanas de acumulación del modo sombra antes de encender nada. El plan tenía una sola compuerta (G1) para el descarte automático (carril 0) y la aceptación automática (carril 1), aunque el riesgo es distinto: el primero pierde un dato, el segundo publica un precio falso.
Decisión: G1 se parte en G1a (carril 0) y G1b (carril 1). G1a se decide con la tasa de falsos descartes medida sobre las revisiones ya guardadas y el oro del backtest, sin esperar acumulación. B1 (procedencia) sube antes de A5c; B2b y B2c se entregan juntas. Orden: A2d → A5a → A5b → B1 → G1a → B2a → canales nuevos (3 a 5 por día, con histórico) → A5c → G1b → B2b + B2c. Se postergan A3c, A1c, A6, A6b, K1, K3, K4, B3, B4 y la fase C. El carril 1 se puede adelantar al n de la especificación solo con procedencia, auditoría semanal y freno automático ya entregados. `docs/plan-factor-humano.md`, «Vía rápida al MVP», lista además lo que no se mueve por apurar.
Descartado: encender los dos carriles ya y medir después sin procedencia ni freno (un error publicado no se podría ubicar ni retirar); mantener la compuerta única (frena el descarte automático, que es la mayor parte del volumen, por un riesgo que no es el suyo).
Consecuencia: sumar canales antes de C3 (controles de §10.7) es un apartamiento del orden C3 → C4; C3 sigue pendiente antes de escalar fuentes. El hito I3a (descarte automático auditado) llega antes que I2. El hito I1 queda débil: no hay línea de base previa a A1b en la bitácora. Si G1a da una cota alta, el carril 0 no se enciende.

## 2026-10-02 — A2d: definición de las métricas del panel «Horas y automatización»
Contexto: el plan pide en `/control` las métricas del estudio §9 semana a semana (hito I1) y que el recorte de pausas se fije mirando la distribución real. La bitácora local tiene un solo día (2026-10-02: 540 eventos, de los cuales solo 5 individuales con duración —3 resueltos y 2 ediciones—, entre 7 y 135 s): no hay distribución que mirar. Además el slice se abrió sin esperar «una semana de datos», por decisión del operador (prioriza el desarrollo).
Decisión: `compute_weekly_automation` devuelve una fila por semana ISO (lunes a domingo), con las vacías explícitas. (1) **Resueltos**: eventos de `curation_events` con acción `curated` o `discarded`, por semana de `decided_at`; `edited` y `deleted` no cuentan, igual que en A4a. (2) **Horas humanas por 1.000 anuncios**: suma de `decision_seconds` (un lote reparte su tiempo entre `batch_size`) sobre los resueltos con tiempo medible, dividida por la cantidad de esos resueltos, × 1000 / 3600; el panel muestra también las horas medidas y cuántos resueltos se pudieron medir sobre el total. (3) **Tasa de automatización**: resueltos con modo distinto de `individual` sobre los resueltos de la semana; hoy son `batch_out_of_focus` y `batch_model`, y un modo `auto` (B2) entra solo. (4) **Frescura**: mediana de días entre `published_date` y la fecha de `created_at` de las filas de `prices_history` dadas de alta en la semana, ignorando las que no tienen `published_date`; y, fuera de la serie, la antigüedad del pendiente más viejo: días entre hoy y el `created_at` del `raw_ingestions` pendiente más antiguo. (5) **Costo de cómputo por anuncio**: costo de las propuestas de `llm_proposals` de la semana (`created_at`; tokens × tarifa del modelo de cada propuesta, con la tabla de `llm_assist` vía `usage_cost_usd`) sobre los `ingestion_id` distintos con propuesta esa semana; una propuesta de un modelo sin tarifa no suma costo (su anuncio sí cuenta y la pantalla la declara «sin tarifa»); el OCR es local y no entra. Una métrica sin datos es «—», no cero. El recorte de pausas es el `PAUSE_CAP_SECONDS` de 15 minutos de A4a, con un cambio: se compara contra el tiempo por anuncio (duración del lote sobre `batch_size`), no contra la duración del lote entero. La decisión que lo pasa se excluye del tiempo, pero sigue contando como resuelta y en la tasa de automatización. Esto reemplaza, solo en ese punto, a la entrada de A4a del 2026-10-02: el panel por canal usa la misma función y cambia igual.
Descartado: contar el tiempo de `edited` (rompe la coherencia con A4a; son pocas); topear las pausas en vez de excluirlas (inventa tiempo no medido); dejar el tope sobre el lote entero: el 2026-10-02 dejaba sin medir dos lotes (161 imágenes en 2.018 s y 9 fuera de foco en 1.093 s), 170 de los 537 resueltos, y la semana daba 0,21 h por 1.000 anuncios en vez de 1,75.
Consecuencia: el panel se llena solo con el uso; G1 necesita varias semanas de esta tabla. El antes/después de A1b (hito I1) no se puede leer todavía: no hay línea de base previa a A1b en la bitácora, y A1b y A7 se cerraron el mismo día. Con el tope por anuncio, una pausa dentro de un lote grande no se detecta (el lote de 9 fuera de foco, a 121 s por anuncio, puede ser una): el error va hacia contar horas de más, el lado conservador para una cifra que se le muestra a un tercero. Los 15 minutos quedan para revisar cuando haya semanas de uso, y se cambian en un solo lugar. Las semanas anteriores al 2026-10-02 no tienen horas ni automatización, solo frescura y costo.

## 2026-10-02 — Vía rápida: las tareas postergadas vuelven al plan
Contexto: la entrada «Vía rápida» de hoy postergaba A3c, A1c, A6, A6b, K1, K3, K4, B3, B4 y la fase C. El operador las había leído como bloqueadas por falta de volumen; no lo estaban: se habían corrido solo para llegar antes al carril 0.
Decisión: reemplaza, en ese punto, a la entrada «Vía rápida». Ninguna tarea sale del plan. Después de A5c se hacen K1, A3c, A1c, K3, K4, A6, B5 y C1 mientras el modo sombra acumula; K6 va con la migración de B1. Solo B3, B4 y C2 esperan volumen, porque cuelgan de G1b o de B2b. El camino al carril 0 (A5a → A5b → B1 → G1a → B2a) no cambia.
Descartado: intercalarlas antes de B2a (atrasa el descarte automático, que es lo que absorbe el volumen nuevo).
Consecuencia: la cantidad de slices de desarrollo no es el límite; el único tramo que no se puede apurar es el n del modo sombra para el carril 1.

## 2026-10-02 — Revisión del plan: C3 antes del carril 1 y la evidencia de G1a sin el lote
Contexto: revisión completa de `docs/plan-factor-humano.md` después de la vía rápida. Dos fallas de fondo. (1) El plan ponía C3 (controles de §10.7) al final, después del carril 1, y llamaba «apartamiento» a sumar canales antes de C3. §10.7 dice lo contrario: en la Fase 0–1, C3 y C4 son «no aplicables» porque la barrera contra el dato falso es el operador que revisa cada fila; lo que la saca es el carril 1, no los canales. (2) G1a se calibraba con las revisiones existentes sin distinguir los descartes del lote de imágenes, aceptados con las miniaturas marcadas por defecto (A1a).
Decisión: C3 pasa al paso 9, después de B5, y bloquea B2b; adelantar G1b también exige C3. Sumar canales con el carril 1 apagado deja de figurar como apartamiento. A5a decide qué cuenta como evidencia para G1a, con la sugerencia de usar solo decisiones individuales y lo rescatado del lote. Además se corrigen inconsistencias: el hito I1 ya no promete un «antes» (la bitácora empezó el mismo día que el lote), G1b pierde la pregunta del carril 0, B2b suma K1 y C3 a sus dependencias, la tabla de apartamientos separa G1a y G1b, y la rutina de sesión toma el orden de la vía rápida.
Descartado: dejar C3 al final (publicaría filas sin revisión sin ninguna de las barreras de §10.7); contar los descartes del lote como acuerdo humano (infla el acierto del carril 0 con decisiones que nadie miró una por una).
Consecuencia: el camino al carril 0 no cambia. El carril 1 suma una dependencia más (C3, que depende de B5); ninguna de las dos espera volumen.

## 2026-10-02 — A5a: el motor de validación decide por predicados, y la evidencia de G1a es el oro
Contexto: hacía falta especificar una sola vez el motor de §6.2 que usan el modo sombra, la persistencia y los carriles automáticos. `spec.md` §6.2 nombra seis reglas pero no trae umbrales, pesos de `confidence_score`, lista de estados (solo `approved`) ni clave para la regla 6; §12.6 prohíbe inventar umbrales. Además, de las 418 revisiones de `llm_reviews`, 406 son descartes del lote de imágenes aceptados con las miniaturas marcadas por defecto.
Decisión: `docs/spec-motor-validacion.md`, aprobada por el operador con sus seis respuestas por defecto. El motor es código puro; cada regla devuelve `pass`, `fail` o `unknown`, y `unknown` nunca aprueba. Los carriles se deciden por predicados, no por puntaje: carril 0 si el modelo descarta y las reglas no ven ningún piloto; carril 1 solo para texto, con cita verificada, acuerdo entre modelo y reglas, sin abstenciones y las seis reglas en `pass`; el resto, carril 2. `confidence_score` es descriptivo (reglas en `pass` sobre seis). Todos los parámetros quedan sin fijar y el motor no trae valores por defecto: sin configuración, todo va al carril 2. Apartamientos: la regla 2 usa la banda robusta de la celda (`robust_fence`) en vez de la fórmula 70/30; `robust_fence` deja de ser solo «de dibujo»; el resultado suma la clave `quantity_price` y los valores `pass/fail/unknown`; se definen estados propios (`incomplete`, `anomaly`, `inconsistent`, `possible_duplicate`, `untrusted_source`, `unverified`); la reputación es por canal. Un canal sin historial no entra al carril 1. Los descartes de lote no cuentan como evidencia. Si el carril 1 se adelanta: auditoría de todas las filas automáticas hasta un `n0`, y freno por la cota inferior del error auditado hasta ese `n0`.
Descartado: un puntaje con pesos y un umbral único (la spec no da pesos y esconde por qué se aceptó una fila); la fórmula 70/30 (sin umbral, y pide una tendencia nacional que no existe); contar los descartes de lote como acuerdo humano; valores por defecto en el motor.
Consecuencia: G1a se decide con el oro del backtest, que hoy da 1 falso descarte en 49 (cota superior de 9,3% al 95%), no con las 418 revisiones. Los canales nuevos aportan al carril 0 de entrada y al carril 1 recién cuando junten historial. Las imágenes entran al carril 1 con C2. Los 23 casos esperados de la especificación son los tests de A5b.

## 2026-10-02 — A5b: precisiones de la especificación del motor que obligó el código
Contexto: al programar el motor (`saimc/validation/`) contra los 23 casos de `docs/spec-motor-validacion.md`, tres casos no salían aplicando las tablas al pie de la letra, y cuatro puntos no estaban dichos. (a) Casos 6, 8 y 22: la fila tiene las seis reglas en `pass` pero la tabla de casos pide `unverified`, y la de estados dice «`approved`: las seis reglas en `pass`». (b) Caso 14: el saco marcado `unit` falla la regla 2 (32.000 por libra está fuera de la banda) y la 6; por el orden de la tabla ganaría `anomaly`, y el caso pide `inconsistent`. (c) Caso 6: dice que «las reglas no dan moneda», pero `ExtractionProposal.currency` siempre trae CUP, así que reglas y modelo «coinciden» en una moneda que nadie leyó.
Decisión: (a) `approved` exige, además de las seis reglas en `pass`, que la fila cumpla las condiciones 1 a 3 del carril 1 (cita verificada, acuerdo con las reglas, sin abstenciones); si no, es `unverified`. El tipo de contenido y las señales externas son del anuncio y no cambian el estado de la fila (casos 17 y 21 siguen `approved`). (b) Si la regla 6 falla, la regla 2 queda en `unknown`: con la base del precio desmentida no se sabe cuál es el precio por unidad, igual que el «no se sabe si dividir» de `cells.resolve_row`. (c) El acuerdo en moneda solo cuenta si `currency_explicit` es verdadero. Además: (d) la celda usa la provincia propia si junta `min_cell_n` referencias y, si no, se repliega a todas las provincias; la misma muestra sirve a las reglas 2 y 6 (`cells.cell_sample`, `cells.evaluate_basis`); `/control` llama a lo mismo con mínimo 1 y no cambia. (e) La ventana de la regla 5 incluye las 24 h exactas. (f) Sin configuración tampoco hay carril 0. (g) Con la probabilidad «alta» de una señal sin fijar (`None`), cualquier desacuerdo saca del carril 1. (h) La provincia de `compare_with_rules` va en un campo aparte de `RowAgreement`, fuera de `status` y de `fields()`.
Descartado: dejar `approved` en los casos 6, 8 y 22 y `anomaly` en el 14 (contradice la tabla de casos, que es la que el operador aprobó fila por fila); replegar la celda solo cuando la provincia propia está vacía, como hace `/control` (una provincia con dos referencias quedaría peor que una con ninguna); sumar la provincia a `status` (cambia el orden de la cola y lo que mide el backtest).
Consecuencia: un anuncio sin moneda escrita nunca entra al carril 1, aunque sea CUP; A5c va a medir cuánto volumen deja afuera. Quien arma el `AdContext` (A5c, B1) es responsable de pasar solo referencias anteriores al anuncio: el motor recibe precios sin fecha y no puede comprobarlo. El cerco robusto vive en `saimc/validation/fence.py` y `price_charts` lo importa: cambiar sus constantes mueve a la vez los gráficos y la regla 2.

## 2026-10-02 — A5c: primera corrida del modo sombra, y el orden A5c → G1a → B2a
Contexto: primera corrida de `python -m saimc.validation.shadow --gold data/llm-backtest-results-v6.jsonl --grid`, en seco, sobre la base local. Calibración: el oro v6 (100 anuncios: 71 textos, 29 imágenes). Evaluación: las revisiones individuales de `llm_reviews` (12 de 418; las otras 406 son de lote). Grilla de 48 combinaciones: `min_cell_n` 3/5/10, `channel_min_history` 1/3/5/10, `channel_max_edit_rate` 0,10/0,25/0,50 y 1,00 como referencia «sin tope».
Decisión: se registran los números y no se fija ningún parámetro todavía. (1) **Carril 0**, igual en toda la grilla: toma 45 de 100 anuncios del oro (31 textos, 14 imágenes) con 0 falsos descartes; cota superior al 95%: 9,2% en texto (0/31) y 19,3% en imagen (0/14). El modelo quiso descartar 49; la segunda condición (las reglas no ven ningún piloto) mandó 4 a revisión, entre ellos el único falso descarte conocido. En evaluación el carril 0 no tiene muestra (0 de 12). (2) **Carril 1: vacío.** Con cualquier tope de edición de canal hasta 0,50 no entra ningún anuncio, en los dos conjuntos. Sin tope entra 1 por conjunto, sin error, y 0/1 no mide nada (cota de 95%). Motivos en el oro, sobre 55 anuncios en revisión: reglas y modelo no coinciden en moneda (41) ni en precio (32); canal con historial sucio (hasta 26: los canales con historial tienen 17 de 23 y 3 de 7 filas editadas o borradas) o sin historial (23 a 49); celda sin referencias suficientes (16 a 22); imagen (13); fila repetida del mismo canal en 24 h (13). (3) **Moneda no escrita** (precisión de A5b): 32 anuncios del oro tienen alguna fila sin moneda escrita, pero ninguno queda afuera solo por eso con un tope real; sin tope serían 3. (4) **Orden:** el número de G1a lo da este informe y un descarte no crea filas de precio, así que el plan pasa a A5c → G1a → B2a → canales → B1; B1 queda como requisito del carril 1 y deja de serlo de G1a y B2a. Reemplaza, en ese punto, a la entrada «Vía rápida» de hoy.
Descartado: fijar ya los parámetros de la regla 4 (con el carril 1 vacío no hay con qué elegirlos); aflojar ahora los predicados del carril 1 para que entre algo (es decisión del operador en G1b, con este desglose a la vista); usar las 406 revisiones de lote como evidencia.
Consecuencia: tal como está especificado, hoy el carril 1 no automatizaría nada: lo frena sobre todo exigir acuerdo con el extractor de reglas, que es el más débil de los dos, y el historial de los canales. El motivo «no coinciden» mezcla desacuerdo real con «las reglas no tienen fila que comparar»; separarlos es lo primero para G1b. La automatización alcanzable hoy es la del carril 0: 45% del oro (cota inferior 36,5%). `build_ad_context` aplica la provincia del canal a las filas, como el formulario de la cola, así que en un canal con provincia la regla 3 pasa casi por construcción; B1 y B2 lo heredan. `llm_proposals` no guarda las filas caídas (`dropped`): se recalculan contra el catálogo de hoy, y B1 debería guardarlas. Lo rescatado de un lote no deja rastro y no se puede contar. El oro tiene que estar copiado en el VPS para repetir la corrida.

## 2026-10-03 — G1a: el carril 0 se enciende para texto e imagen, con meta por tipo
Contexto: compuerta G1a del plan de factor humano. El dato es la primera corrida de A5c sobre el oro v6: el carril 0 toma 45 de 100 anuncios con 0 falsos descartes; cota superior de Clopper-Pearson al 95% de 9,2% en texto (0/31) y 19,3% en imagen (0/14). Con cero errores la cota depende solo del n (1 − 0,05^(1/n)), así que ninguna meta por debajo de 9,2% se puede confirmar hoy. Un falso descarte no publica ningún precio: pierde un anuncio útil, que la auditoría puede rescatar.
Decisión: el operador fija la meta de falsos descartes en 10% para texto y 20% para imagen, y enciende el carril 0 para los dos tipos (las dos cotas quedan por debajo de su meta). La auditoría semanal de B2a toma una muestra aleatoria de 30 textos y 15 imágenes descartados solos, o todos si hay menos: es la muestra más chica con la que una semana sin errores confirma por sí sola cada meta (9,5% y 18,1%). Reemplaza en parte a la decisión cerrada 2 de `CLAUDE.md`: el triage deja de ser manual anuncio por anuncio para lo que entra al carril 0. Es apartamiento de `spec.md` §6.1 («no como etapas autónomas del pipeline»), anotado en `docs/spec-map.md`.
Descartado: meta de 10% también en imagen, que dejaba las imágenes en el lote manual hasta que creciera su oro (más prudente: el único falso descarte que tuvo el oro fue una imagen, hoy desviado a revisión por la segunda condición del carril 0); el operador prefirió sacar ese trabajo ya y aceptar 20% como techo. Muestras más chicas, con las que una semana sola no confirma la meta.
Consecuencia: B2a implementa el carril 0 con un interruptor por tipo, apagado por defecto, y la auditoría con 30 + 15 por semana y semilla reproducible. El panel tiene que mostrar, por tipo, la cota acumulada de lo auditado contra su meta: si la supera, ese tipo se apaga o se estrecha (la compuerta se adelanta, no se saltea). La cota de imagen (19,3%) queda a 0,7 puntos de su meta: un solo falso descarte auditado en las primeras semanas la cruza. El carril 1 sigue con su compuerta propia, G1b.

## 2026-10-03 — B2a: el pre-cálculo descarta solo lo que el carril 0 manda, con auditoría semanal
Contexto: G1a encendió el carril 0 para texto e imagen. El pre-cálculo solo le mandaba al modelo los textos que nombran un piloto, y el carril 0 exige que las reglas no vean ninguno: el carril 0 de texto no se habría disparado nunca. Además, `validate_ad` niega el carril 0 sin `ValidationConfig`, aunque el carril 0 no usa ninguno de sus parámetros.
Decisión: especificación `docs/spec-slice-B2a.md`, aprobada por el operador. Dos interruptores, `SAIMC_LANE0_TEXT_ENABLED` y `SAIMC_LANE0_IMAGE_ENABLED`, apagados por defecto. Con el de texto encendido, el pre-cálculo también manda al modelo los textos sin piloto. Lo que da carril 0 (`decide_lane_0`, mismos predicados que `validate_ad`, sin configuración) se descarta en la misma transacción, con `llm_reviews` como el lote de imágenes y el evento `discarded/auto`. `/curation/auditoria` sortea 30 textos y 15 imágenes de la última semana cerrada con una semilla por semana; «Rescatar» devuelve a `pending` (evento `rescued`) y «Bien descartado» deja `audited`. La cota acumulada por tipo se compara con la meta y solo avisa. Migración 0026: `auto` en `mode`, `rescued` y `audited` en `action`. Lo rescatado sale de los dos lotes y se cura de a uno, para que un clic en el lote no lo vuelva a descartar sin mirarlo. El historial del canal y el modo sombra parten cada anuncio en tramos por los rescates: un descarte individual después de un rescate cuenta como individual.
Descartado: un solo `SAIMC_LANE0_ENABLED` como decía el plan (G1a fijó una meta por tipo y cada tipo se tiene que poder apagar solo); fijar valores de relleno en `ValidationConfig` para destrabar el carril 0 (fingiría parámetros que el carril no usa); mostrar la cota en `/control`, que es público.
Consecuencia: los pilotos dejan de filtrar qué texto va al modelo, pero siguen siendo el catálogo del modelo, el seguro del carril 0 y el foco de H0. Mandar los textos sin foco cuesta API ($0,0015–0,0029 por texto, estimado): la primera corrida con texto encendido se estima en seco y la aprueba el operador. El freno es manual: si una cota supera su meta, el operador apaga el interruptor de ese tipo. La cota de imagen arranca a 0,7 puntos de su meta.

## 2026-10-03 — B1: la validación se guarda en cada fila, con procedencia y como foto fechada
Contexto: `prices_history.confidence_score` existía desde la 0002 y nada lo escribía; no había estado, detalle ni procedencia, y sin procedencia el carril 1 no se puede encender. Además el motor (`validate_ad`) valida la propuesta del modelo, no la fila que se guarda: el operador corrige, agrega y borra filas, y al editar no hay propuesta. Tampoco había ningún parámetro fijado (A5c), así que las reglas 2 y 6 daban `unknown` y ninguna fila podía salir `anomaly` ni `inconsistent`. Y `llm_proposals.prompt_version` era `varchar(32)` con una versión que ya ocupaba 30 (K6).
Decisión: especificación `docs/spec-slice-B1.md`, aprobada por el operador con sus seis respuestas por defecto. Migración 0027: `validation_status`, `validation_detail` (JSON) y `provenance` (`human|auto|audited`, obligatoria y sin valor por defecto; las filas existentes quedan `human`), y `prompt_version` a 64. El motor suma `validate_stored_row`, función pura que aplica las mismas seis reglas a UNA fila ya guardada y no decide carril; `validate_ad` y `decide_lane_0` no cambian. Corre al curar y al editar (todas las filas que quedan del anuncio), en la misma transacción; si falla, la fila se guarda con la validación nula y el error va al log. `min_cell_n = 5` es una decisión del operador para poder describir las filas: no sale de ninguna medición que lo elija entre 3, 5 y 10 y no tiene efecto sobre ningún carril; es un apartamiento consciente de `spec.md` §12.6. Los dos parámetros de la regla 4 siguen sin fijar (G1b): la regla da `unknown`, ninguna fila sale `approved` y el puntaje máximo es 83. Una fila curada por una persona cuenta como verificada. La columna se llama `provenance` y no `origin`, que ya nombra tres cosas en el código. El estado es una foto: describe la fila contra el mercado conocido cuando se validó, con fecha, versión del motor (`ENGINE_VERSION`) y parámetros en el detalle; las altas y ediciones de otros anuncios no revalidan filas ajenas. `python -m saimc.validation.revalidate` (en seco por defecto) valida las filas anteriores a B1 y reaplica un cambio de versión o de parámetros. `/control` publica solo el conteo por estado. Apartamientos de `spec.md` §7.2 (columnas fuera de la lista) y §6.2, «Resultado de validación» (estado y puntaje en columnas, el resto en el JSON), anotados en `docs/spec-map.md`.
Descartado: validar la propuesta del modelo y copiar su resultado a la fila (no hay propuesta al editar ni en un anuncio sin modelo, y lo guardado es lo que corrigió el operador); fijar ya los parámetros de la regla 4 sin medición; dejar las reglas 2 y 6 en `unknown` hasta G1b (ninguna fila podría salir `anomaly`); llamar `origin` a la columna, como decía el plan; revalidar las filas ajenas en cada guardado (el estado dejaría de tener fecha); guardar en B1 las filas caídas (`dropped`) de `llm_proposals`, que A5c le había pedido: es una entrada del carril 1 y pasa a B2b.
Consecuencia: toda fila nueva lleva `provenance`, y quien cree filas por otro camino (B2b) tiene que decirla. Una fila sana queda `unverified`, no `approved`, hasta que G1b fije la regla 4. Cambiar `min_cell_n`, las reglas, el orden de los estados o el cerco obliga a subir `ENGINE_VERSION` y correr `revalidate`; lo mismo después de un backfill de tasas o de base de precio, que no revalidan solos. La celda de una fila guardada cuenta los precios de días estrictamente anteriores a su fecha de publicación (o al día de la captura si no la tiene) y usa el `price_cup` que quedó en la fila. Una fila sin provincia queda `incomplete` aunque `spec.md` §6.2 regla 3 admita «provincia no determinada»: lo exige la especificación del motor y no se reabre acá. Ningún carril usa lo que B1 guarda.
