# Backlog

Documento vivo de tareas pendientes, en orden de prioridad. No reemplaza a
`docs/decisions.md` (evidencia de decisiones ya tomadas, mirando atrás) ni a
`docs/spec.md` (documento rector). Acá vive lo que falta, por qué importa, y
qué lo bloquea si algo.

Formato de entrada, sin ceremonia:

```
## Título corto
Por qué importa: ...
Bloqueado por: ... (o "nada")
```

Es un archivo de anexado igual que `decisions.md`: cuando una tarea se hace,
se saca de acá y su motivo queda en `decisions.md` si ameritó una decisión.

---

## Precio comparable por unidad canónica

Por qué importa: es la precondición de todo el dashboard (`spec.md` §6.7:
precio actual, gráfica de evolución, mapa de calor).

**Corrección del 2026-08-22:** una versión anterior de esta entrada decía que
«no existe una columna con la que comparar dos filas». Es falso y se corrige
acá. `src/saimc/cells.py` **ya resuelve los tres ejes**: funde por `price_cup`
y aísla lo no convertido, divide por `quantity` solo cuando `price_basis` es
`'total'`, deja la fila sin resolver en su propia celda, y separa `retail` de
`wholesale`. El diseño está hecho y es correcto. Lo que falta es más chico y
más concreto — ver «el nudo real» abajo.

Lo que sí es cierto es que promediar `price` crudo da un número sin sentido:
medido contra producción, la serie diaria de Arroz/La Habana da 29, 3.463,
15.787 y 30.885 en días consecutivos, porque `avg(price)` mezcla tres ejes:

- **Moneda**: 180 filas en CUP y 45 en USD en el mismo promedio. `price_cup`
  solo está poblada en las 45 filas USD (es la conversión); en las filas CUP es
  `NULL` por diseño, así que el valor comparable es
  `coalesce(price_cup, price)`, no `price`.
- **`price_basis` vacío en 202 de 225 filas (90%), pero es deuda histórica, no
  un hueco abierto.** Poblado por día de carga: 1 de 199 entre el 10 y el 20 de
  agosto, 8 de 11 el día 21, **14 de 14 el día 22**. Es un campo nuevo que ya se
  captura al 100%. `unit` siguió el mismo patrón (casi nulo el 10–11, 100% desde
  el 12). Lo que queda son 12 días de filas viejas, no un rediseño de captura.
  Sin el campo no se sabe si el número es total o unitario: caso real
  `price=30885 CUP, quantity=100, unit=lb, price_basis=NULL` — es el total de la
  paca (≈309 CUP/lb), indistinguible de 30.885 por libra.
- **Unidad heterogénea**: 103 filas en kg, 63 en lb, 5 en l, 2 en ml, 2 en caja,
  50 sin unidad.

**La unidad canónica no puede ser global, a diferencia de la moneda.** CUP como
base monetaria funciona para todo (`spec.md` §6.3 E, ya implementado en
`exchange_rate/eltoque.py`). Una unidad base única no: kg↔lb convierte con
factor fijo, pero **Aceite se anuncia en `l`/`ml`** (volumen, no peso) y no
convierte a libras sin densidad, y `caja` no convierte sin el peso de la caja.
La unidad canónica es por producto —la que declara `Product.unit`—, y la
conversión solo opera dentro de la misma magnitud. Resuelto así el 2026-08-22.

**Hecho el 2026-08-22: la conversión entre unidades de la misma magnitud.**
Era el nudo — `compute_retail_ranges` descartaba en silencio toda fila cuya
unidad no coincidiera con la del catálogo, citando mal §12.6, y eso costaba
~108 de 166 filas bandera. `units.py:convert_price_per_unit()` ya la resuelve
y Arroz/La Habana pasó de n=1 con mediana 30.885 a **n=17 con mediana 312,39
CUP/lb**. Motivo completo en `decisions.md`, 2026-08-22.

Lo que queda de esta entrada:

Bloqueado por: nada técnico. Hay que decidir dos cosas antes de tocar código:
(a) si el precio comparable es columna derivada persistida o cálculo en
consulta —hoy es cálculo en consulta y alcanza, la pregunta se vuelve real
cuando el dashboard público lo consulte por request—; (b) qué se hace con las
202 filas viejas sin `price_basis` (83 excluidas en la ventana de 30 días):
retro-completarlas en curaduría o excluirlas de los agregados. La segunda es
hoy la cola de exclusión más grande, muy por encima de la de unidad.

## Filas con `price_basis` incoherente envenenan el máximo

Por qué importa: destapado por la conversión de unidades del 2026-08-22, que
al dejar entrar las filas buenas dejó a la vista las malas. Una fila marcada
`price_basis='unit'` cuyo precio por unidad se aparta un orden de magnitud del
resto de su celda es casi siempre un error de curaduría. Caso real:
`Arroz · 30.885 · 100 lb · basis='unit'` en La Habana — es el total del saco
(≈309 CUP/lb), no el precio por libra. Con la conversión ya aplicada, la
mediana es robusta (312,39) pero el **máximo sigue en 30.885**, y el rango
mín–máx es justamente lo que el dashboard va a publicar (`spec.md` §6.7,
«banda gris»). Pollo/La Habana muestra el mismo síntoma: 806 a 8.500 CUP/lb.

**Ojo: `price_basis='unit'` con `quantity != 1` NO es de por sí un error.**
Medido el 2026-08-22 sobre las 13 filas que cumplen esa forma: las de Arroz y
Pollo están mal, pero las 4 de Picadillo están bien. En esas, `quantity` es el
tamaño del lote disponible y el precio sí es por libra —525/40 lb, 515/50 lb,
460/40 lb, tres filas coincidiendo en ~500 CUP/lb—. En cambio Pollo a 8.500/10
lb y 3.400/4 lb dan **las dos 850 CUP/lb** leídas como total: dos filas
independientes coincidiendo delatan que son totales mal marcados. El dato
mismo distingue un caso del otro; una regla estructural los confundiría.

**Detección hecha el 2026-08-22** (`cells.py:detect_basis_suspects`, tabla en
`/control`). Señala 7 filas contra producción, sin falsos positivos y sin
inventar ningún umbral. Picadillo no aparece, como debía.

Lo que **queda** es el trabajo de dato, y es tuyo, no de código: corregir esas 7
filas desde el historial de curaduría. Hasta entonces los agregados siguen
mostrando el máximo envenenado a propósito — el detector señala, no corrige.
Las peores: Aceite/La Habana `1,90 USD × 900 ml` (se aparta ×369, y por la
conversión ml→l mete un error de mil veces), Arroz/La Habana `30.885 × 100 lb`
(×98,9), y tres de Arroz/Santiago `17.500 × 25 kg` (×23,3).

Bloqueado por: nada. Es curaduría dirigida, ya no búsqueda a ciegas.

## Unidad de catálogo mal declarada en Picadillo

Por qué importa: `Product.unit` de Picadillo dice `unidad` mientras sus 5
filas vienen en `lb`. Masa y conteo no son convertibles, así que el producto
queda entero fuera de la vista minorista. Lo destapó el contador
`excluded_by_unit` del 2026-08-22: de las 7 filas que aún excluye a 30 días,
5 son estas y 2 son Pollo en `caja` (esas sí legítimamente no convertibles).

Bloqueado por: nada técnico — es un dato de catálogo, se corrige desde
`/catalog`. Conviene revisar de paso si algún otro producto tiene la unidad
declarada por defecto en vez de por observación.

Aparte y más barato: **detectar filas con `price_basis` incoherente**. Una fila
`'unit'` cuyo precio por unidad se aparta en un orden de magnitud del resto de
su celda es casi siempre un error de curaduría, y hoy una sola envenena la
mediana entera.

Nota de alcance: la **foto del presente** ("precio de hoy del aceite, cuántos
anuncios hay y en qué provincia") no depende de volumen histórico y se sostiene
con las filas actuales. La gráfica de evolución y la predicción sí escalan con
volumen. Conviene no atar lo primero a lo segundo.

## Prototipo de visualización de Fase 0 — mapa de calor y tarjetas de producto

Por qué importa: es el **único ítem del alcance de Fase 0 que no está
construido** (`spec.md` §9, «Alcance del MVP inicial»: «Visualización:
Prototipo de mapa de calor simple y tarjetas de producto»). No es Fase 1 —
Fase 1 es el lanzamiento público, la expansión a 10 productos y 5 provincias y
el Market Report. Hoy no hay ninguna ruta pública: los routers son `catalog`,
`control`, `curation` y `whatsapp_webhook` (`decisions.md`, 2026-08-22).

**Ya no está bloqueado por el precio comparable**: `cells.py` más la conversión
de unidades de `c7ae46e` lo resolvieron. Lo que queda son huecos de dato que
condicionan *qué* se puede publicar, no *si* se puede construir.

Medición contra producción del 2026-08-22: 225 filas, 58 productos, 166 filas en
los 5 bandera, La Habana 113 / Santiago 105, fechas del 2026-06-29 al 2026-08-22.
Cola: 142 curados / 230 descartados / 183 pendientes. **Siete celdas
producto × provincia con n≥5**: Arroz/Habana 63, Arroz/Santiago 36, Pollo/Habana
23, Azúcar/Habana 16, Azúcar/Santiago 9, Aceite/Habana 7, Pollo/Santiago 6.

### Huecos de dato que condicionan qué se publica

1. **`price_basis` poblado en 23 de 225 filas (10%).** Sin ese campo no se
   distingue un total de un precio unitario, y el **máximo** —justo lo que la
   banda mín–máx de §6.7 publica— queda envenenado. Decidir entre
   retro-completar en curaduría o excluir esas filas de los agregados. Ver la
   entrada «Filas con `price_basis` incoherente envenenan el máximo».
2. **`sale_format` vacío en 66 de 225 (29%)** — 141 `retail`, 18 `wholesale`.
   Separar minorista de mayorista es correcto y necesario (la paca de arroz a
   30.885 es mayorista y mezclarla es parte de por qué el promedio crudo da
   basura), pero hoy deja casi un tercio del dato sin clasificar.

### Hecho el 2026-08-22

- **Geometría de las 16 provincias** y **ruta pública con shell propio**. `GET /`
  existe, sin auth, con el mapa SVG inline coloreado por cobertura y el tamaño
  de muestra al lado. Motivo y fuente en `decisions.md`.
- **Detección de `price_basis` incoherente** (`cells.py:detect_basis_suspects`,
  tabla en `/control`): 7 filas señaladas, sin falsos positivos y sin umbrales.
- **Tarjeta de producto** (`web/card_view.py`): rango mín–máx como titular,
  mediana en prosa, `n` visible y rótulo «muestra en formación». Resuelto de
  paso que §9.0 **no** prohíbe publicar precio — prohíbe el valor puntual sin
  respaldo de muestra, y da ese rótulo como estado por debajo del umbral.

### Tareas, en orden de dependencia

3. **Curar las 7 filas que señala `/control`.** Es trabajo de dato y bloquea la
   publicación, no el código. Hasta hacerlo, 4 de las 6 tarjetas de `/` muestran
   máximos falsos: Aceite/La Habana publica «2.100 – 1.264.260 CUP/l» y
   Pollo/La Habana tiene además la mediana corrida a 3.400. **No conviene
   exponer `/` públicamente hasta entonces.**
4. **Rango mín–máx además del promedio** (§6.7, «banda gris»): con n bajo el
   rango es honesto y el promedio solo, engañoso. Depende del hueco 1.
5. **Colorear el mapa por precio** (§6.7): verde/amarillo/rojo contra el
   promedio nacional. Hoy el mapa distingue solo cobertura, porque el gris de
   §6.7 dice «sin datos **suficientes**» y la especificación nunca fija cuántas
   observaciones son suficientes — §12.6 prohíbe inventar ese umbral. Hace falta
   una medición que lo determine antes de agregar el tercer estado.
6. **Índice de disponibilidad** (§7.3, tabla `availability_index`): la cantidad
   de anuncios en los últimos N días es proxy directo de escasez y se calcula
   con el dato de hoy, sin campos nuevos.
7. **Semáforo de tendencia** y **alerta de variación >10%** (§6.7): solo para
   las celdas con densidad, que hoy son las siete de arriba.
8. **Gráfico de evolución** (§6.7): queda pendiente la decisión Chart.js vs.
   SVG generado en servidor, que el ADR 001 dejó abierta a propósito para
   medirla ahora que el mapa ya existe.

Siguiente slice recomendado: la **tarjeta de producto (3)**. Es lo primero que se
puede mostrar con dato honesto, porque la «foto del presente» no necesita volumen
histórico.

Bloqueado por: nada. Las decisiones de stack están cerradas en el ADR 001
(`docs/decisions/001-stack-python-unico.md`) y la infraestructura de entrega
—`GZipMiddleware`, htmx vendorizado, geometría— ya está puesta.

Deuda menor que dejó el slice del mapa: la vista pública carga
`saimc-material.css` entero, que arrastra los estilos de la curaduría que no usa
(~6 KB gzipeados). No justifica partir la hoja todavía, pero conviene tenerlo
anotado para cuando el dashboard crezca.

## `test_ninguna_vista_pide_assets_a_la_red` pasa en vacío para 3 de 4 rutas

Por qué importa: destapado el 2026-08-22 al agregar `GZipMiddleware`. El caso
recorre `VISTAS = ["/catalog", "/curation", "/control", "/curation/history"]` y
afirma que el HTML no contiene `fonts.googleapis.com` ni los otros CDN. Pero
`/catalog`, `/curation` y `/curation/history` van detrás de `require_operator` y
el fixture de `tests/test_static_assets.py` no manda credenciales: devuelven
**401**, y una página de error tampoco contiene esas cadenas. O sea que el caso
está verde sin haber mirado nunca esas tres vistas. Solo `/control` se prueba de
verdad.

No es urgente —`_shell.html` es la única plantilla que carga assets y sí está
cubierta vía `/control`— pero es un test que miente sobre su alcance, que es
justo la clase de cosa que este repo ya se comió dos veces (`measure.py` con
`data/raw`, `pytest` desde un worktree).

Bloqueado por: nada. El arreglo es `app.dependency_overrides[require_operator]`
en el fixture, como ya hace `tests/test_curation.py`. **Ojo**: `GET /curation`
ingesta la bandeja, así que hay que aislar `SAIMC_INBOX_DIR` y
`SAIMC_RAW_STORE_DIR` con `monkeypatch` en el mismo movimiento, o el test empieza
a mover archivos reales de `inbox/` a `data/raw/`.

## «Ver el anuncio que sustenta este precio» — Transparency Layer

Por qué importa: es el linaje del dato que pide `spec.md` §6.5 y la diferencia
más fuerte frente a elTOQUE — ellos publican un número, SAIMC puede publicar el
número **y su prueba**. Técnicamente es casi gratis: el endpoint
`/evidence/{ingestion_id}` ya existe y `prices_history.source_id` ya apunta a la
fila cruda.

Bloqueado por: una decisión de privacidad, no por código. Es la contradicción
#1 de `spec-map.md`, todavía abierta: la línea 321 dice que se publica
«literalmente el archivo capturado», pero §10.6 mitigación 2 exige mostrar el
anuncio y no la identidad del anunciante, y en una captura de WhatsApp los
píxeles traen el nombre y a menudo el teléfono. Lo que se publique tiene que ser
el texto redactado, no la imagen cruda. Hay que decidirlo antes de exponerlo.

## `provider` como entidad en el modelo de datos

Por qué importa: `spec.md` §6.2 (línea 581) lista **Proveedor** entre los
campos que la extracción debe sacar de cada anuncio, pero no existe en ninguna
tabla: ni en `products` ni en `prices_history` ni como tabla propia. Hoy lo más
cercano es `raw_ingestions.source_reference` (canal de origen), que es de dónde
salió el mensaje, no quién vende. Sin esto no hay seguimiento de vendedor:
ni reputación, ni "quién tiene el mejor precio", ni detección de un mismo
vendedor publicando en varios canales.

Ojo con la privacidad: §10.6 prohíbe almacenar datos de contacto, y en las
capturas de WhatsApp el identificador del vendedor **es el teléfono**, que hoy
se redacta antes de persistir (`decisions.md`, 2026-08-11). Un `provider` que
sea el número de teléfono contradice una mitigación ya cerrada. Hay que definir
un identificador estable que no sea dato personal, y `CLAUDE.md` ya fija que el
identificador de usuario origen nunca se expone en la capa pública.

Bloqueado por: nada técnico. Requiere decidir qué identifica a un proveedor sin
guardar datos personales. Cambio de esquema → flujo completo de especificación.

## Granularidad geográfica por debajo de provincia ("zona más cercana")

Por qué importa: la visión de producto pide que el dashboard ajuste los
anuncios a la zona más cercana al usuario. Hoy la unidad mínima es la
provincia: `prices_history.province_id` y nada más fino. `spec.md` §6.2 (línea
484) menciona que la validación reconoce municipios y regiones comerciales como
texto, pero no hay entidad `municipality` ni coordenadas en el modelo.

Son dos cosas distintas y conviene no confundirlas: geolocalizar el **anuncio**
por debajo de provincia, y geolocalizar al **usuario** para ordenar por
cercanía. Lo segundo no está contemplado en ninguna parte de `spec.md`.

Bloqueado por: decisión de privacidad, no falta de código. §10.6 lista las
ubicaciones aproximadas como dato personal, y cuanto más fina es la
granularidad, más fácil es re-identificar al anunciante. Subir de provincia a
municipio toca una mitigación de riesgo ya cerrada, así que hay que decidirlo
explícitamente antes de diseñarlo. Cambio de esquema → flujo completo de
especificación.

## Persistir `unit` por fila en `prices_history`

Por qué importa: hoy la métrica de cantidad/unidad de H0 se aproxima contra
la unidad canónica del catálogo, no contra una confirmación real por fila
(`decisions.md`, 2026-08-07). Si el catálogo de productos bandera no alcanza
para mantener unidad estable, esto se vuelve necesario.

Bloqueado por: evidencia de que la aproximación actual no alcanza. Si se
activa: cambio de esquema → flujo completo de especificación (CLAUDE.md).

## `provinces.aliases` como columna en vez de diccionario en código

Por qué importa: simetría con `products.aliases`, permitiría editar sin
deploy.

Bloqueado por: nada urgente — la jerga de provincia es acotada y las 16
provincias son fijas. Solo vale la pena si el diccionario en código se vuelve
difícil de mantener.

## Flag "conjunto de referencia" en `prices_history`

Por qué importa: `spec.md` (§6.1c) pide poder marcar un registro como
parte del conjunto de referencia. En Fase 0 es innecesario — el 100% de lo
curado ya es referencia, porque no hay otra fuente (`decisions.md`,
2026-08-07). Vuelve a ser relevante cuando existan fuentes automáticas.

Bloqueado por: Fase 2 — fuentes automáticas (`spec.md` §6.1, Etapa 3).

## `propose_all`: producto mencionado una vez, precio repetido en varias líneas

Por qué importa: patrón real (`decisions.md`, 2026-08-08) — un anuncio dice
el producto en el título o en una frase de venta ("Arroz Santiago de cuba",
"¡VENTA MAYORISTA DE PACAS DE ARROZ!") y después lista precios sin repetir
el nombre en cada línea. El modelo línea-por-línea de `propose_all` genera
tarjetas con producto sin precio (la línea del título) y tarjetas con precio
sin producto (las líneas de oferta) en vez de una sola tarjeta completa por
oferta.

Bloqueado por: volumen real que muestre qué tan común es el patrón contra
otras formas de anuncio, antes de rediseñar el modelo de matching de
`propose_all` (evaluado y pospuesto explícitamente el 2026-08-08).

## Poblar `confidence_score` — Validation Engine

Por qué importa: `prices_history.confidence_score` existe como columna (`CHECK
0..100`, `decisions.md` 2026-08-07) pero ningún código la llena. Es la salida
del Validation Engine completo (`spec.md` §6.2): estructura, coherencia de
precio contra promedio provincial + tendencia nacional, validación
geográfica, reputación de usuario, deduplicación semántica, coherencia
cantidad/precio — no solo "¿están todos los campos?".

Bloqueado por: Fase 2 (`spec.md` §9, Fase 2: "Validation Engine: Modo
activo"), no antes. En Fase 0 el operador humano verifica cada registro
contra su evidencia antes de guardarlo — es la misma barrera que hace que las
contramedidas C1-C4 tampoco sean bloqueantes todavía (`decisions.md`,
2026-08-06). Además necesita histórico de precios real (Regla 2) y un sistema
de reputación (Regla 4) que no existen hoy.

## Recalibrar `guess_source_reference` contra capturas de WhatsApp

Por qué importa: la heurística prellena el campo "Canal origen" buscando el
patrón "Nombre - Ayer a las H:MM" de Facebook. Contra el corpus real del
2026-08-11 prellena **0/29**: las capturas de WhatsApp, que son 18 de 29
anuncios, no traen ese patrón sino un encabezado de chat. Resultado: 26/29
anuncios curados tienen `source_reference` vacío, así que hoy no hay forma de
analizar la composición de fuentes desde la base — hubo que inferirla del texto
(`decisions.md`, 2026-08-11).

Bloqueado por: nada técnico. No se hizo en el slice del 2026-08-11 para no
mezclarlo con la redacción de teléfonos. Ojo al rediseñarla: el encabezado de
WhatsApp es justamente el teléfono, que ahora se redacta antes de persistir —
lo que quede como "canal origen" no puede ser el número.
