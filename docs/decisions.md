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
