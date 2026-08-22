# ADR 001 — Stack Python único y arquitectura de la capa de visualización

- **Fecha:** 2026-08-22
- **Estado:** aceptada
- **Rige:** Fase 0 y Fase 1. La Fase 2 queda descrita, no decidida.
- **Reemplaza a:** nada. Es el primer ADR del repositorio.

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
FastAPI + PostgreSQL + Jinja2 (server-rendered) + JS propio + SVG inline
```

| Capa | Elección | Estado |
| :--- | :--- | :--- |
| API y lógica de negocio | FastAPI | Implementado |
| Base de datos | PostgreSQL | Implementado |
| Interfaz de curaduría | Jinja2 server-rendered + JS propio | Implementado |
| Dashboard público | Jinja2 server-rendered + JS propio | **Sin construir** |
| Mapa de calor | SVG inline de las 16 provincias | **Sin construir** |
| Gráfico de evolución | Sin decidir. Ver «Lo que este ADR no decide» | **Sin construir** |
| Cola de trabajos | Ninguna | Correcto para la fase |

## El camino, por fase

| | Fase 0 (hoy) | Fase 1 | Fase 2 |
| :--- | :--- | :--- | :--- |
| **Objetivo** | Prototipo de mapa de calor y tarjetas de producto | Lanzamiento público, 10 productos, 5 provincias, Market Report | Comportamiento de aplicación real |
| **Frontend** | Jinja2 server-rendered | Jinja2 server-rendered | **Angular**, contra la API ya estabilizada |
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

§13.1 nombra `Jinja2/HTMX + MapLibre + Chart.js`. Dos de esas piezas no van.
Registrarlo acá es el requisito de `CLAUDE.md`: apartarse es legítimo, apartarse
sin saberlo no.

### 1. HTMX no se usa — y el apartamiento ya era un hecho, no una propuesta

Verificado el 2026-08-22 contra el código: no existe la dependencia, no existe un
solo atributo `hx-*`, no existe la etiqueta `<script>` que lo cargue. La única
mención en todo `src/` es un docstring en `src/saimc/web/__init__.py:1` que dice
«FastAPI + Jinja2 + HTMX» y describe algo que no está.

Lo que hay en su lugar es mejor y ya está probado: HTML renderizado en servidor
con JS propio delegado en `document`, escrito explícitamente como mejora
progresiva. El comentario de `_price_row_scripts.html` lo dice sin ambigüedad:
«Si este script nunca corre, la etiqueta puede quedar desactualizada pero el
valor enviado y toda regla del servidor siguen intactos».

Se adopta el estado real: **server-rendered con JS propio como mejora
progresiva**. HTMX resolvería un problema que la curaduría no tuvo, y sumar una
dependencia para alinearse con una lista es la clase de ceremonia que este
proyecto no puede pagar. El docstring de `web/__init__.py` queda desactualizado y
hay que corregirlo.

### 2. El mapa de calor es SVG inline, no MapLibre GL

**MapLibre GL necesita un servidor de tiles vectoriales** —externo o
autohospedado— más su motor de render en el navegador. Lo que hay que dibujar
son 16 polígonos que no cambian nunca, coloreados por una categoría que el
servidor ya calculó. Un basemap de calles no aporta nada a «¿está caro el aceite
en Santiago?», y cuesta justo el recurso más escaso.

La comparación honesta contra lo medido: **el frontend entero de hoy pesa 120K**
(28K de CSS vendorizado + 92K de fuentes propias) y **no hace una sola petición a
un tercero**. Cualquier motor de mapas más su fuente de tiles es un salto de
categoría respecto de eso, no un incremento. No se midió el peso exacto de
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
3. **Cero assets de terceros en tiempo de ejecución.** Todo se vendoriza. Ya es
   el caso y `_shell.html` lo documenta citando el motivo: «una descarga menos en
   conexión lenta».
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
- **Adoptar HTMX para alinearse con la lista de §13.1.** Descartada: agregar una
  dependencia para que el código coincida con un documento, cuando el documento
  puede anotarse, es ceremonia.

## Lo que este ADR no decide

- **Cómo se dibuja el gráfico de evolución de §6.7.** Chart.js sigue siendo la
  opción de §13.1 y no se aparta acá: es autocontenido y no llama a ningún
  servicio externo, así que el argumento del mapa no le aplica. Pero un gráfico
  de líneas con banda mín–máx también es SVG generado en servidor, y eso
  mantendría el frontend en cero dependencias. Se decide al construir esa vista,
  con la implementación del mapa ya hecha como evidencia.
- **De dónde sale la geometría de las provincias**, ni con qué herramienta se
  simplifica. Es trabajo de la implementación.
- **Nada de la Fase 2.** La tabla la describe para dar dirección. Cuando llegue,
  se decide contra el producto que exista entonces, no contra este documento.

## Consecuencias

- La línea de visualización de Fase 0 arranca sin decisiones de stack pendientes.
- `src/saimc/web/__init__.py:1` tiene un docstring que hay que corregir.
- `docs/spec-map.md` gana dos apartamientos deliberados sobre §13.1.
- El próximo desarrollador —o la próxima sesión— lee este archivo en vez de
  reconstruir el camino desde cinco fuentes.
