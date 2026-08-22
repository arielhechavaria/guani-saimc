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
precio actual, gráfica de evolución, mapa de calor). Hoy **no existe una
columna con la que se puedan comparar dos filas**, y promediar `price` da un
número sin sentido. Medido contra producción el 2026-08-22, la serie diaria de
Arroz/La Habana da promedios de 29, 3.463, 15.787 y 30.885 en días
consecutivos, porque `avg(price)` mezcla tres ejes a la vez:

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
La unidad canónica tiene que ser por categoría de producto —peso→lb,
volumen→l, conteo→unidad—, y el aceite es producto bandera.

Solo **23 de 225 filas (10%)** tienen `quantity` + `unit` + `price_basis`
completos, que es el mínimo para derivar precio por unidad. Ese porcentaje sube
solo con cada día de carga nueva.

Bloqueado por: nada técnico. Hay que decidir tres cosas antes de tocar código:
(a) si el precio comparable es columna derivada persistida o cálculo en
consulta, (b) qué se hace con las 202 filas viejas sin `price_basis` —
retro-completarlas en curaduría o excluirlas de los agregados, y (c) la unidad
canónica por categoría. Es cambio de esquema/motor de agregación → flujo
completo de especificación (CLAUDE.md).

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

Contenido, todo definido ya por la especificación y sostenible con el dato
actual (225 filas, 5 productos bandera, 2 provincias):

- **Tarjeta de producto** (§6.7): precio de hoy, muestra que lo respalda,
  provincias con oferta.
- **Mapa de calor** (§6.7): Habana y Santiago coloreadas, el resto en gris
  «sin datos suficientes». El gris es el estado real, no un defecto.
- **Tamaño de muestra visible** (§9.0): exigido desde el día 1 en lugar de un
  titular de precio. No es opcional.
- **Rango mín–máx además del promedio** (§6.7, «banda gris»): con n bajo el
  rango es honesto y el promedio solo, engañoso.
- **Índice de disponibilidad** (§7.3, tabla `availability_index`): la cantidad
  de anuncios en los últimos N días es proxy directo de escasez y se calcula
  con el dato de hoy, sin campos nuevos.
- **Semáforo de tendencia** y **alerta de variación >10%** (§6.7): solo para
  las series con densidad — hoy Arroz/Habana (16 días), Pollo/Habana (15),
  Arroz/Santiago (11).
- **Separar minorista de mayorista**: 141 filas `retail` contra 18
  `wholesale`. Son dos mercados con precios de órdenes de magnitud distintos
  (la paca de arroz a 30.885 es mayorista) y mezclarlos es parte de por qué el
  promedio crudo da basura. Separarlos es más correcto y más útil.

Bloqueado por: **precio comparable por unidad canónica** (entrada anterior).
Las siete vistas lo consumen; construirlas antes es levantar seis pantallas
sobre un número que miente.

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
