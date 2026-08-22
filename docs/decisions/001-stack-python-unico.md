# ADR 001 — Stack Python único y arquitectura de la capa de visualización

- **Fecha:** 2026-08-22
- **Estado:** aceptada
- **Rige:** Fase 0 y Fase 1. La Fase 2 queda descrita, no decidida.
- **Reemplaza a:** nada. Es el primer ADR del repositorio.
- **Revisado el 2026-08-22**, el mismo día, sobre HTMX. La primera versión lo
  descartaba; medido, el argumento no se sostenía. Ver «Apartamientos», punto 1.

## Por qué existe este archivo

`spec.md` §13.1 cierra el stack y termina con una instrucción explícita:

> Esta decisión debe registrarse como ADR en el repositorio
> (`docs/decisions/001-stack-python-unico.md`) junto con las alternativas
> descartadas.

Nunca se escribió. `docs/decisions/` contenía solo `SETUP.md`, el handoff de
puesta en marcha. La consecuencia fue medible: en la sesión del 2026-08-22, al
abrir la línea de visualización, se reabrió como pregunta un stack que llevaba
tres meses cerrado, y se propuso como acordado («Angular y OpenStreetMap»)
algo que el documento rector no dice. Este archivo existe para que el camino se
lea de una vez, sin reconstruirlo de §13.1 más `CLAUDE.md` más nueve entradas de
`docs/decisions.md`.

Este ADR **no** reemplaza a `docs/decisions.md`, que sigue siendo el log de
anexado. Registra el camino; el log registra los pasos.

## Contexto

Tres restricciones mandan sobre esta decisión, y ninguna es de gusto técnico:

1. **Un solo desarrollador.** Cada stack en paralelo multiplica la superficie de
   mantenimiento sin aportar nada al usuario.
2. **El problema es de Python.** Extracción de entidades en lenguaje natural y
   series temporales. Partir el stack obliga a comunicación entre procesos desde
   el día 1.
3. **El usuario final tiene conexión lenta e intermitente.** No es un detalle de
   despliegue: es el escenario real y define qué significa «bien hecho».

La tercera es la que se malinterpreta. La visualización es la superficie que
vende, y de ahí se salta fácil a «entonces poné la herramienta más grande». Es
al revés. En este escenario, *vender* es que la página abra. Cada dependencia de
red que se agrega es una forma nueva de que no abra.

## Decisión

**Fase 0–1: Python de extremo a extremo. Un runtime, un despliegue, cero
dependencias de frontend servidas por terceros.**

```
FastAPI + PostgreSQL + Jinja2 (server-rendered) + HTMX vendorizado + SVG inline
```

| Capa | Elección | Estado |
| :--- | :--- | :--- |
| API y lógica de negocio | FastAPI | Implementado |
| Base de datos | PostgreSQL | Implementado |
| Interfaz de curaduría | Jinja2 server-rendered + JS propio. **No se retrofitea a HTMX** | Implementado |
| Dashboard público | Jinja2 server-rendered + **HTMX vendorizado** para swaps parciales | **Sin construir** |
| Compresión de respuestas | `GZipMiddleware`, `compresslevel=6` | Implementado |
| Mapa de calor | SVG inline de las 16 provincias | Geometría y ruta pública implementadas; falta el coloreado por precio |
| Gráfico de evolución | Sin decidir. Ver «Lo que este ADR no decide» | **Sin construir** |
| Cola de trabajos | Ninguna | Correcto para la fase |

## El camino, por fase

| | Fase 0 (hoy) | Fase 1 | Fase 2 |
| :--- | :--- | :--- | :--- |
| **Objetivo** | Prototipo de mapa de calor y tarjetas de producto | Lanzamiento público, 10 productos, 5 provincias, Market Report | Comportamiento de aplicación real |
| **Frontend** | Jinja2 server-rendered + HTMX | Jinja2 server-rendered + HTMX | **Angular**, contra la API ya estabilizada |
| **Mapa** | SVG inline | SVG inline | Se reevalúa si aparece interacción que lo exija |
| **Cola** | Ninguna | Redis + Celery cuando haya carga que lo justifique | — |
| **Base** | PostgreSQL | PostgreSQL (+TimescaleDB cuando la serie lo justifique) | — |

**Angular llega en Fase 2, no antes.** Está en §13.1 textualmente: «Angular queda
diferido a Fase 2, cuando el dashboard público necesite comportamiento de
aplicación real (filtros complejos, estado compartido, PWA offline,
notificaciones)». La condición no es que el dashboard sea importante —siempre lo
fue— sino que necesite *comportamiento de aplicación*. Hoy no lo necesita: siete
vistas de solo lectura sobre agregados que el servidor ya calcula.

Adelantarlo ya se rechazó una vez. El pitch (§14.1) pedía financiar «Dashboard
Angular SSR + TimescaleDB» contra §13.1; al congelarse el pitch el 2026-08-12,
eso quedó registrado como deuda del pitch, no como decisión vigente
(`decisions.md`, 2026-08-12).

## Apartamientos de §13.1

§13.1 nombra `Jinja2/HTMX + MapLibre + Chart.js`. Solo MapLibre no va.

### 1. HTMX: no es un apartamiento. Se adopta en el dashboard (revisión)

**La primera versión de este ADR lo descartaba, y el argumento era malo.** Queda
acá el razonamiento corregido, porque el error es instructivo.

Lo que sí es un hecho verificado: **HTMX no existe hoy en el repo**. No está la
dependencia en `pyproject.toml`, no hay un solo atributo `hx-*`, no hay
`<script>` que lo cargue. La única mención en todo `src/` es un docstring en
`src/saimc/web/__init__.py:1` que dice «FastAPI + Jinja2 + HTMX» y describe algo
que no está.

De ese hecho la primera versión sacó la conclusión equivocada, por tres motivos:

1. **Argumento trasladado indebidamente.** El problema de MapLibre es una
   **dependencia de red en tiempo de ejecución**: un servicio de tiles que
   responde por cada pan y cada zoom. HTMX no tiene nada de eso: es **un archivo
   estático**, vendorizado igual que `saimc-material.css` y las tres fuentes.
   Satisface el invariante 3 —«cero peticiones a terceros»— sin excepción ni
   matiz. El argumento del mapa no le aplica y usarlo fue un error.
2. **Generalización desde el componente equivocado.** «La curaduría no lo
   necesitó» es un hecho sobre la curaduría, que ya está construida. El
   dashboard no está construido y su patrón de interacción es otro.
3. **La alternativa real no es «HTMX o nada», es «HTMX o JS propio».** El
   dashboard con swaps parciales necesita `fetch` + manipulación de DOM escrita a
   mano. Con un solo desarrollador, una biblioteca probada de 16 KB le gana a
   código DOM a medida por mantenibilidad, no por moda.

Y sobre el peso, medido el 2026-08-22 en vez de citado de memoria:

| | En disco | gzip -9 |
| :--- | ---: | ---: |
| `saimc-material.css` | 26.390 B | **6.079 B** |
| Fuentes (3 × woff2) | 81.688 B | ya comprimidas |
| htmx 2.0.10 minificado | 51.238 B | **16.539 B** |

> **Corrección del 2026-08-22.** La primera versión de esta tabla decía 28.672 B
> de CSS y «92 KB» de fuentes. Esos números salían de `du`, que informa bloques
> de disco redondeados, no bytes. Los de arriba son de `stat`. La conclusión no
> cambia; los números sí estaban mal.

El hallazgo que cierra la discusión: **`src/saimc/web/app.py` no tiene
`GZipMiddleware`**. Hoy el CSS viaja sin comprimir. Activarlo ahorra 20.311 B;
HTMX gzipeado cuesta 16.539 B. Contando la primera carga entera —CSS + fuentes,
que no comprimen— son **108.078 B hoy contra 104.306 B con las dos cosas**. O
sea: **con HTMX y compresión la primera carga pesa menos que hoy sin ninguna de
las dos**, y de ahí en adelante cada interacción parcial pesa una fracción de la
página. El argumento de la conexión lenta —que es el correcto— apuntaba en
sentido contrario al que le hice decir.

Decisión, entonces:

- **El dashboard usa HTMX**, vendorizado en `/static` como todo lo demás. Nunca
  desde CDN. Alineado con §13.1, así que no requiere justificación: la requería
  apartarse.
- **Cada interacción degrada.** `hx-get` va sobre un `<a href>` que funciona como
  enlace común con JS deshabilitado. Es el invariante 4, y HTMX está diseñado
  justo para eso.
- **La curaduría no se retrofitea.** Funciona, está testeada, y su JS propio ya
  está escrito como mejora progresiva — el comentario de
  `_price_row_scripts.html` lo dice: «Si este script nunca corre, la etiqueta
  puede quedar desactualizada pero el valor enviado y toda regla del servidor
  siguen intactos». Reescribirla es churn sin beneficio.
- **Se agrega `GZipMiddleware`**, independientemente de HTMX. Es la optimización
  más barata que quedaba y estaba a la vista sin que nadie la mirara.

El docstring de `web/__init__.py` resulta ser **profético, no falso**: describe
el destino, no el estado. Igual hay que ajustarlo para que se lea como tal.

### 2. El mapa de calor es SVG inline, no MapLibre GL

**MapLibre GL necesita un servidor de tiles vectoriales** —externo o
autohospedado— más su motor de render en el navegador. Lo que hay que dibujar
son 16 polígonos que no cambian nunca, coloreados por una categoría que el
servidor ya calculó. Un basemap de calles no aporta nada a «¿está caro el aceite
en Santiago?», y cuesta justo el recurso más escaso.

La comparación honesta contra lo medido: **el frontend entero pesa 108.078 B**
(26.390 de CSS vendorizado + 81.688 de fuentes propias) y **no hace una sola
petición a un tercero**. Cualquier motor de mapas más su fuente de tiles es un
salto de categoría respecto de eso, no un incremento. No se midió el peso exacto de
MapLibre porque la decisión no depende de la cifra: depende de que introduce una
dependencia de red en tiempo de ejecución donde hoy no hay ninguna.

Con SVG inline el mapa es HTML: el color de cada provincia es una clase CSS que
Jinja escribe en el `<path>`, el clic es un enlace, y funciona con JS
deshabilitado. Se apoya en el patrón que las tres vistas actuales ya usan.

**OpenStreetMap sí entra, pero como fuente de datos, no como dependencia de
runtime.** La geometría de las 16 provincias se deriva una vez de datos abiertos
(OSM o Natural Earth), se simplifica al detalle que un choropleth necesita, y se
vendoriza como archivo estático junto al CSS y las fuentes. Es el mismo criterio
que ya se aplicó a Roboto: usar el recurso abierto, servirlo uno mismo.

Nota: `spec.md` §13.1 no menciona OpenStreetMap en ninguna parte. Se verificó el
2026-08-22 (`openstreetmap`, `osm`, `leaflet`, `mapbox`, `geojson`, `tiles`: cero
coincidencias). La única pieza cartográfica que el documento nombra es MapLibre.

## Invariantes que hacen barata la Fase 2

Esto es lo que de verdad protege el camino a Angular, y no es la elección de
framework. §13.1 pone la condición: en Fase 2 el frontend se construye «contra
una API ya definida y estabilizada, en lugar de contra un backend en
movimiento». Eso se gana hoy o no se gana.

1. **La agregación no vive en las plantillas.** `cells.py` calcula
   (`compute_cells`, `compute_retail_ranges`), `web/matrix.py` arma la
   presentación (`build_matrix`, `RangeBar`, `ProvinceColumn`, `select_province`)
   y la plantilla solo pinta. Ya se cumple sin haberlo declarado; queda declarado.
   Toda vista nueva del dashboard respeta la misma separación.
2. **Toda vista nueva calcula en una función pura testeable, no en Jinja.** Es
   la condición práctica de la anterior: si el número se arma en la plantilla, en
   Fase 2 hay que reescribirlo, y mientras tanto no se puede testear.
3. **Cero peticiones a terceros en tiempo de ejecución.** No prohíbe usar
   bibliotecas: prohíbe que el navegador del usuario le pida algo a un servidor
   que no es el nuestro. Una biblioteca vendorizada en `/static` lo cumple; un
   CDN o un servicio de tiles no. Ya es el caso con el CSS y las fuentes, y
   `_shell.html` documenta el motivo: «una descarga menos en conexión lenta».
   HTMX entra por esta puerta; MapLibre con tiles remotos no.
4. **El servidor decide, el JS mejora.** Ninguna afirmación que el usuario lea
   puede depender de que un script haya corrido.

Cumplidos los cuatro, la Fase 2 es escribir un frontend contra funciones que ya
devuelven estructuras cerradas. Incumplidos, es una reescritura.

## Alternativas descartadas

§13.1 pide registrarlas. Van con su motivo:

- **Angular ahora, en Fase 0.** Descartada. Dos stacks en paralelo con un solo
  desarrollador, y un frontend construido contra un backend todavía en
  movimiento. Además, exactamente lo que el pitch pedía y §13.1 rechazó.
- **PHP/Symfony o Laravel**, donde la experiencia del desarrollador es más
  profunda. Descartada: la extracción de entidades y el forecasting viven en
  Python, y sostener los dos runtimes es el costo que la restricción 1 prohíbe.
- **MapLibre GL con tiles remotos.** Descartada por lo dicho arriba: dependencia
  de red en runtime para dibujar 16 polígonos fijos.
- **MapLibre GL con tiles autohospedados.** Descartada por costo de operación —
  es un servicio más que desplegar y mantener, contra la decisión 4 de
  `CLAUDE.md` (nada de infraestructura hasta validar H0).
- **Leaflet + tiles raster de OpenStreetMap.** Descartada: una petición de red
  por tile, por pan y por zoom, para un basemap que la pregunta del usuario no
  necesita.
- **Empezar por el mapa con precios crudos «para tener algo que mirar».**
  Descartada el 2026-08-22: un mapa coloreado con promedios que mezclan moneda,
  base y unidad enseña una conclusión falsa, y desmentirla después es más caro
  que no publicarla.
- **Escribir los swaps parciales del dashboard con `fetch` y DOM a mano, en vez
  de HTMX.** Descartada: es la alternativa real a HTMX, y con un solo
  desarrollador pierde. 16,5 KB de biblioteca probada contra código a medida sin
  tests, para el mismo comportamiento.
- **Descartar HTMX por peso.** Descartada por la medición: cuesta 16,5 KB
  gzipeados, y activar `GZipMiddleware` —que faltaba— ahorra ~22 KB. El saldo es
  favorable. Fue la posición de la primera versión de este ADR y se corrigió el
  mismo día.

## Lo que este ADR no decide

- **Cómo se dibuja el gráfico de evolución de §6.7.** Chart.js sigue siendo la
  opción de §13.1 y no se aparta acá: es autocontenido y vendorizable, así que
  cumple el invariante 3 igual que HTMX. Pero un gráfico de líneas con banda
  mín–máx también es SVG generado en servidor. Se decide al construir esa vista,
  midiendo el peso como se midió el de HTMX — no argumentando de memoria.
- ~~**De dónde sale la geometría de las provincias**, ni con qué herramienta se
  simplifica.~~ Resuelto el 2026-08-22: Natural Earth 10m 5.1.1, dominio
  público, simplificado con Douglas-Peucker a 0,008° por
  `tools/build_cuba_map.py`. El motivo de descartar geoBoundaries —mismas 16
  unidades pero ODbL, con atribución y share-alike— está en `decisions.md`.
- **Nada de la Fase 2.** La tabla la describe para dar dirección. Cuando llegue,
  se decide contra el producto que exista entonces, no contra este documento.

## Consecuencias

- La línea de visualización de Fase 0 arranca sin decisiones de stack pendientes.
- Entra HTMX como dependencia vendorizada, y con él la regla de que **toda
  interacción del dashboard tiene que funcionar sin JS**.
- `GZipMiddleware` **ya está en `src/saimc/web/app.py`** (2026-08-22), hecho
  antes que HTMX justamente para que el saldo de bytes sea favorable desde el
  primer commit. `compresslevel=6`, no el 9 del default: medido, el 9 gana 34
  bytes sobre el CSS real a cambio de bastante más CPU por respuesta.
- **htmx 2.0.10 ya está vendorizado** en `src/saimc/web/static/htmx.min.js`
  (2026-08-22), con su procedencia verificada contra el `integrity` de npm y su
  sha256 fijado en `tests/test_static_assets.py`. Ninguna plantilla lo carga
  todavía: la vista que lo necesite opta desde su `{% block scripts %}` usando
  `htmx_version()`. El primer consumidor será el dashboard.
- `src/saimc/web/__init__.py:1` describe el destino, no el estado. Ajustar la
  redacción cuando HTMX entre de verdad.
- `docs/spec-map.md` gana **un** apartamiento deliberado sobre §13.1 —el mapa— y
  una nota de que HTMX está nombrado pero todavía sin implementar.
- El próximo desarrollador —o la próxima sesión— lee este archivo en vez de
  reconstruir el camino desde cinco fuentes.
