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
