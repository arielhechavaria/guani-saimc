# Sacar al operador del camino de cada registro

*Estudio del 2026-09-29. Los números salen de producción (consulta de solo lectura
del 2026-09-29), salvo donde se indica. Lo marcado «(estimado)» o «(supuesto)» no
está medido.*

*Actualización del 2026-10-02: el operador ya tiene clave de Jev. Cambian el límite
de acceso (sección 3, punto 8) y la fila A6 (sección 5). El resto sigue como estaba.*

## Resumen

- **El inversor tiene razón en la forma.** Hoy cada anuncio pasa por las manos del
  operador, así que las horas humanas crecen en línea recta con el volumen. A la
  escala actual (2 provincias, 5 productos, ~400 anuncios por semana) son unas 2–3 h
  semanales (estimado): no hace falta una oficina. Lo que no escala es la forma. Con
  los tiempos de hoy, 20.000 anuncios por semana serían unas tres personas a tiempo
  completo, y más si se amplía el foco de productos.
- **Mejorar el extractor no alcanza.** El modelo ya acierta mucho: 71,4% de
  extracción completa contra 40,0% de las reglas, y cuando da un valor el operador
  lo deja igual en 94 de 97 campos. Aun así, el tiempo por anuncio curado no bajó
  de forma medible: 81 s de mediana con propuesta (n=5) contra 86 s sin ella. El
  tiempo no se va en corregir. Se va en revisarlo todo, siempre.
- **Los sistemas que operan a escala cambian la unidad del trabajo humano.** Pasan
  de «cada registro» a tres cosas: solo lo incierto, una auditoría aleatoria de
  tamaño fijo y los patrones nuevos (catálogo, glosario, canales), una sola vez.
  El análogo más cercano, la tasa informal de elTOQUE, es automático de punta a
  punta.
- **La spec ya diseñó la pieza que falta.** §6.2 define un motor de validación (seis
  reglas, `validation_status`, `confidence_score`) que nunca se construyó porque el
  humano era el validador. Es justo la señal de confianza que recomienda la
  literatura: validaciones deterministas y acuerdo entre fuentes, no la confianza
  que declara el modelo.
- **El camino tiene dos mitades.**
  - La **fase A** no rompe ninguna decisión cerrada y ataca un 35–45% del tiempo
    actual: descarte de imágenes en lote, instrumentación, glosario, política por
    canal y modo sombra, más una prueba fuera de línea de Jev, un clasificador
    con confianza calibrada que entraría como pieza desconectable.
  - La **fase B**, aceptación automática con auditoría, sí se aparta de la decisión
    cerrada 2 y de §6.1 y §10.7, que son vinculantes. Se decide con los datos del
    modo sombra y se registra antes de encenderla.

---

## 1. El problema en números

| Indicador | Valor |
| --- | --- |
| Anuncios ingeridos | 2.220: WhatsApp es el 94% (1.386 textos, 696 imágenes) |
| Entrada semanal | 300–785 anuncios |
| Filas curadas por semana | 69, 96, 48, 61, 51 y, en las últimas tres semanas, 11, 28 y 0 |
| Resueltos | 1.770: 230 curados y 1.540 descartados (87%) |
| Pendientes | 450: 154 textos (128 en foco) y 296 imágenes |
| Filas en `prices_history` | 364, de 229 anuncios |
| Frescura | el dato más nuevo es del 2026-09-13, unos 16 días de atraso |
| Rendimiento (qué parte termina en filas) | 66% del texto en foco, 13,8% de las imágenes, 13% en total |

La entrada supera a la curaduría desde que WAHA automatizó la captura
(2026-08-15). Es la paradoja de Jevons [1]: abaratar la captura multiplicó el
volumen, y como la revisión sigue siendo por registro, el trabajo humano creció en
vez de bajar. Abaratar también la extracción sin cambiar la unidad de trabajo repite
el efecto. El 2026-09-28 el modelo pre-procesó 100 anuncios y todavía nadie los
revisó.

### Dónde se va el tiempo, por cada 1.000 anuncios

- **Medido:** el tiempo por anuncio curado. Son 63 s en un texto de una fila, 95 s
  si tiene varias y 96–121 s en una imagen.
- **Supuesto:** 15 s por descarte de texto y 20–30 s por descarte de imagen, que hay
  que abrir, esperar el OCR (~3,5 s) y mirar. Los descartes no guardan marca de
  tiempo, así que ese costo no se puede medir hoy.

| Actividad | Anuncios | Minutos | % del total |
| --- | ---: | ---: | ---: |
| Descartar imágenes una por una | 322 | 107–161 | 32–41% |
| Curar texto en foco | 79 | ~112 | 29–34% |
| Curar imágenes | 52 | ~93 | 24–28% |
| Descartar textos de a uno | 76 | ~19 | ~5% |
| Descartar textos sin foco en lote | 471 | ~3 | ~1% |
| **Total** | **1.000** | **~335–390 (5,6–6,5 h)** | |

A 400 anuncios por semana eso da unas 2,2–2,6 h semanales, y la cuenta es lineal:

- a 5.000 anuncios por semana, unas 28–33 h;
- a 20.000, unas 110–130 h, o sea tres personas.

Y sube si el foco se amplía a más productos, porque baja el descarte en lote y
suben las filas por anuncio (supuestos: el volumen nacional real no está medido).

Tres observaciones sobre la tabla:

1. **El bloque más grande es descartar imágenes, y es el más fácil de eliminar.**
   - El modelo ya sugiere descartar 51 de las 55 imágenes que tienen propuesta, y en
     el backtest su veredicto de descarte acertó 50 de 50.
   - Un solo grupo aporta 170 de las 296 imágenes pendientes y rinde 3% (7 curadas de
     214 resueltas).
2. **Mejores propuestas no aceleran la curaduría de lo relevante.** Con propuesta el
   operador tarda 81 s (n=5); sin ella, 86 s. Igual lee el anuncio y revisa cada
   campo.
3. **El operador completa abstenciones más que corregir errores.**
   - De 19 filas revisadas, cantidad y unidad llegaron vacías 9 veces cada una. El
     operador las completó con convenciones que no están escritas en ningún lado:
     «4x10» son 40 lb, y un saco sin peso se toma como de 100 lb.
   - El glosario que debería guardarlas existe desde el 2026-09-27 y tiene 0
     entradas.

---

## 2. Qué tenemos y qué diseñó la spec

| Mecanismo de confianza | Spec | Estado |
| --- | --- | --- |
| Catálogos cerrados de producto, provincia y moneda | §6.2 reglas 1 y 3 | Hecho, pero sin municipios ni regiones |
| Deduplicación | §6.2 regla 5 | Solo por hash exacto del archivo. No hay similitud semántica ni de imagen |
| Precio esperado → VALIDADO o ANOMALÍA | §6.2 regla 2 | No hecho. Existe un cerco log-IQR×3, pero solo en la capa pública |
| Reputación de la fuente (`trust_score`) | §6.2 regla 4 | No hecho. El emisor de un grupo (`participant`) se descarta; guardarlo es un cambio de esquema pendiente |
| Coherencia cantidad/precio | §6.2 regla 6 | Solo como sospecha (`detect_basis_suspects`), no como validación |
| `validation_status` y `confidence_score` | §6.2, «Resultado de validación» | No hecho: `confidence_score` es NULL en las 364 filas |
| «¿Por qué el sistema confía en este dato?» | §6.5 | Hay linaje (archivo, canal, fechas, tasa, auditoría del LLM), pero no el porqué de cada fila |
| Controles contra manipulación C1–C4 | §10.7 | C2 parcial y C1 solo informativa. C3 y C4 no aplican en la Fase 0–1 |
| Verificación cruzada del modelo | — (propio) | Hecho, solo como señal y sin decidir: la cita se busca en el texto (`verify_evidence`) y se compara con las reglas (`compare_with_rules`) |

Hoy decide la barrera que §10.7 nombra para la Fase 0–1: un operador humano
que verifica cada registro contra su evidencia original. La spec ya previó qué
reemplaza esa barrera cuando no hay un humano por registro: los controles C1–C4 de
la Fase 2 y el motor de §6.2. Automatizar la aceptación es adelantar esa parte del
diseño, no inventar una nueva.

---

## 3. Qué hacen los sistemas modernos

**1. Nadie que opere a escala revisa cada registro.** Pasa igual en el procesamiento
de documentos (AWS A2I), en la estadística oficial (Statistics Norway, Eurostat), en
los índices de precios en línea (Billion Prices Project) y en el crowdsourcing de
precios del JRC en Nigeria. Todos repiten el mismo patrón [2][3][4][5][7]:
- automatizar;
- mandar al humano solo lo incierto o lo influyente;
- auditar una muestra aleatoria;
- vigilar los agregados.

El Programa Mundial de Alimentos plantea la disyuntiva de forma explícita [6]:
buscar la precisión durante la recolección (pocos datos, encuestadores entrenados)
o después (muchos datos y filtros estadísticos). SAIMC ya eligió el segundo camino
al automatizar la captura, pero sigue pagando el costo del primero.

**2. La confianza no se le pregunta al modelo: se construye con validaciones.** Un
trabajo de septiembre de 2026 [8] midió esto sobre extracción de campos en
documentos financieros:

| Señal | AUROC | Campos auto-aprobados con error <10% |
| --- | --- | --- |
| Las del propio modelo: confianza verbalizada, logprobs, autoconsistencia | 0,54–0,74 | 0,1–7% |
| Esas mismas, combinadas con validaciones deterministas y acuerdo entre lecturas | 0,90–0,99 | 49–72% |

Las facturas son mucho más regulares que un anuncio de WhatsApp: ese 49–72% es un
techo, no una expectativa.

**3. Los umbrales se miden y se recalibran.** El umbral de auto-aceptación se elige
sobre un conjunto de calibración, de modo que garantice un error máximo
(*Learn-then-Test* [9]). Los proveedores piden entre 2.000 y 5.000 campos revisados
antes de fijarlo, y después lo ajustan con los datos de control de calidad [10]. Es
lo mismo que exige §12.6: ningún umbral inventado, todos medidos.

**4. El agregado tolera error si hay suficientes observaciones.**
- Si el 10% de los registros son arbitrariamente malos, el sesgo máximo de una
  mediana es de 0,14σ. El ruido muestral de una mediana de 20 observaciones es
  mayor: 0,28σ [11][12].
- Pero hoy SAIMC tiene unas 5 filas por celda (producto × provincia) por semana, así
  que una fila mala es el 20% de su celda.

Por eso la revisión humana tiene que ir a donde la celda es fina, que es lo que hace
la edición selectiva por influencia [3][13], y no repartirse por igual.

**5. El análogo cubano ya funciona sin un humano por registro.** La tasa informal de
elTOQUE funciona así [14][15]:
- unos bots capturan las ofertas de grupos de Telegram y WhatsApp y de sitios de
  clasificados;
- toma la mediana de 24 h y filtra lo que se aleja más de ±3 desviaciones;
- cuenta una sola vez las ofertas repetidas del mismo usuario;
- desde 2024 excluye a los usuarios nuevos y a los que figuran en listas de
  manipuladores.

Todo el proceso es automático, y elTOQUE lo presenta como garantía contra la
manipulación.

Otros dos precedentes:
- En Indonesia se extrajeron precios de alimentos de 41.761 tuits, con una
  correlación de 0,76–0,87 contra los oficiales [16].
- En Nigeria, precios de crowdsourcing procesados sin revisión manual coincidieron
  con los de encuestadores para subtipos específicos [7][17].

**6. El costo del LLM no es el cuello de botella.**
- SAIMC paga hoy entre $0,0015 (imagen) y $0,0029 (texto) por anuncio con el prompt
  vigente.
- La API por lotes cuesta la mitad y la caché cobra las lecturas a 0,1× [18][19].
- Una captura de 1080×2400 enviada directo a un modelo de visión son ~3.350 tokens,
  unos $0,007 en Sonnet 5 [20].

El recurso escaso es el tiempo del operador.

**7. Modelos propios: más tarde, si acaso.** Afinar modelos chicos (GLiNER,
NuExtract, destilación) o usar supervisión débil (Snorkel) baja el costo por llamada
y la dependencia de un proveedor [21][22][23][24][25]. Pero no baja horas humanas, y
le suma a un solo desarrollador infraestructura, deriva y mantenimiento.

**8. Clasificadores con confianza calibrada: Jev.** Jev (TypeSafe AI, lanzado el
2026-09-15) no genera texto. Recibe texto y un esquema de preguntas cerradas —sí/no,
una opción de una lista, un puntaje— y devuelve la respuesta tipada junto con una
probabilidad que TypeSafe dice calibrada. Cobra $0,042 por millón de tokens de
entrada, la salida es gratis y responde en 70–500 ms [28][29].

Es justo la forma de la señal que le falta al enrutamiento (punto 2): una
probabilidad confiable por decisión.
- **Dónde cabe:** en las decisiones cerradas. Es el triage del carril 0 y, como
  segunda opinión en el carril 1, producto contra el catálogo, moneda, provincia y
  formato de venta.
- **Dónde no cabe:** no extrae números ni fragmentos, así que no reemplaza al
  extractor de precio, cantidad y unidad.

Límites a hoy [28][29]:
- acceso anticipado (el operador tiene clave desde el 2026-10-02, así que la lista
  de espera ya no frena la prueba), y un precio de lanzamiento sin garantía de que se
  sostenga;
- solo API alojada (Cloudflare Workers AI, DigitalOcean): no hay pesos para correrlo
  en el VPS;
- solo texto, con un contexto de 32k tokens y el español sin documentar;
- ninguna evidencia independiente de que la calibración se sostenga.

**Es un costo más.** En dinero es chico: del orden de $0,0001 por anuncio con cinco
preguntas (estimado), contra $0,0015–0,0029 de Claude. En dependencia es grande: un
proveedor nuevo y en acceso anticipado. Por eso, si entra, entra como pieza
desconectable (sección 4).

Ya se había descartado dos veces, el 2026-09-21:
- como reemplazo del asistente LLM, un papel que no puede cumplir;
- por ser solo API alojada y sin español verificado.

Los dos descartes están registrados en `decisions.md`, con fecha 2026-09-29. Este
estudio le abre otro papel, el de señal de confianza independiente, y ese papel se
decide con una prueba barata sobre datos propios (A6). Tener la clave no cambia eso:
habilita la prueba, no la integración. Si A6 sale favorable, Jev entra como una señal
más del motor en modo sombra (slice A6b de `docs/plan-factor-humano.md`).

---

## 4. Propuesta: tres carriles, auditoría y aprendizaje por patrón

```
anuncio → reglas + modelo + validaciones de §6.2
            │
            ├─ carril 0 · descarte automático ───┐
            ├─ carril 1 · aceptación automática ─┼─→ auditoría aleatoria semanal
            └─ carril 2 · revisión humana        │   (de tamaño fijo)
                (ordenada por influencia)        │     └─ si el error sube, el carril 1
                                                 │        se estrecha solo
correcciones con patrón → glosario · catálogo · política por canal
```

| Carril | Entra cuando… | Qué hace el humano | Si el sistema se equivoca |
| --- | --- | --- | --- |
| 0 · Descarte automático | el modelo dice `off_focus` o `no_data` **y** las reglas no encuentran producto piloto | nada por anuncio; audita una muestra | se pierde un dato, pero no se corrompe nada |
| 1 · Aceptación automática | todas las filas pasan todas las pruebas: cita verificada; reglas y modelo coinciden en producto, precio, moneda y provincia; ninguna abstención en cantidad, unidad ni base; precio dentro de la banda robusta de su celda (§6.2 regla 2); cantidad y precio coherentes (regla 6); canal con historial limpio (regla 4, a nivel canal) | nada por anuncio; audita una muestra | un error silencioso, que acotan la auditoría y la mediana |
| 2 · Revisión humana | todo lo demás | revisa primero celdas finas, productos nuevos y anomalías, y ve resaltado solo el campo dudoso | lo mismo que hoy |

Cada fila guarda su procedencia (humana, automática o auditada), su
`validation_status` y su `confidence_score`, los campos que §6.2 ya define. Con eso
la capa pública puede responder la pregunta de §6.5.

### Señales externas: siempre desconectables

Toda señal que dependa de un proveedor externo suma; nunca es un requisito.
- **Calibración base sin señales opcionales.** El enrutamiento base se calibra y se
  certifica sin ellas. Si una señal aporta (Jev, por ejemplo), la configuración que
  la usa se calibra aparte.
- **Si la señal falta, rige la base.** Da igual si el proveedor falla, cierra o se
  deja de pagar: rige la calibración base, sin error y sin frenar la ingesta ni la
  cola.
- **Aislada y con tope.** Cada señal vive detrás de una interfaz propia, se apaga
  por configuración y tiene un tope de gasto, como hoy el pre-cálculo con
  `--max-cost`.

Con Claude ya es así hoy. Si falta, los anuncios caen al carril 2 y el sistema
funciona como ahora, con más carga humana. La diferencia es de grado: sin Claude se
pierde la automatización; sin Jev se pierde una señal.

El aprendizaje ocurre por patrón, no por anuncio:
- una convención nueva («4x10») se escribe una vez en el glosario, y el modelo deja
  de abstenerse;
- un canal que rinde 3% se decide una vez.

El operador enseña reglas en vez de copiar datos.

### Modo sombra: el puente sin riesgo

Antes de encender nada, el sistema calcula el carril que le asignaría a cada anuncio
y lo muestra en la cola, pero el operador sigue validando todo. En 4 a 8 semanas, al
ritmo actual, se juntan 150–300 anuncios con el carril 1 predicho y la decisión del
operador al lado. Eso alcanza para tres cosas:

- **Estimar la tasa de automatización real.**
- **Medir el error que habría tenido el carril 1.**
  - Con 0 errores en 60 anuncios, el error real es de a lo sumo 5%, con 95% de
    confianza.
  - Con 3 errores en 150, de a lo sumo 5,2%.
- **Fijar los umbrales con evidencia**, como exige §12.6.

Mientras el operador siga validando cada registro, no hay apartamiento de nada.

### Qué se puede esperar (ilustrativo)

Con los mismos 1.000 anuncios de la tabla de la sección 1:

| Escenario | Minutos humanos | Reducción |
| --- | ---: | ---: |
| Hoy | 335–390 | — |
| Fase A: descarte en lote con modelo y política por canal | ~220 | 35–45% |
| Fase B: 50% de lo relevante auto-aceptado, más auditoría | ~125 | ~3× |
| Fase B: 70% auto-aceptado, revisión por campo, más auditoría | ~50–85 | ~4–7× |

Supuestos, ninguno medido (los mide el modo sombra):
- 95 s por anuncio revisado, o 45 s si se muestra solo el campo dudoso;
- confirmar un lote con miniaturas, 1–2 s por imagen;
- auditar 30 anuncios de cada 1.000, a 40 s cada uno.

Lo que cambia de fondo: las horas dejan de crecer con el volumen y pasan a crecer
con la fracción incierta, más una auditoría de tamaño fijo. La fracción incierta,
además, baja a medida que crece el glosario.

---

## 5. Plan por fases

### Fase A — sin apartarse de nada

Todo cabe dentro de §6.1: el sistema propone y el operador confirma, aunque
confirme en lote. Hay precedente: el descarte en lote de textos sin foco ya resolvió
929 de los 1.770 anuncios resueltos (52%).

| # | Qué | Por qué |
| --- | --- | --- |
| A1 | Descartar en lote las imágenes que el modelo marca para descarte, confirmando con miniaturas | es el bloque más grande de tiempo (32–41%) |
| A2 | Instrumentar: marca de tiempo en cada descarte, corregir la métrica de `/control` y separar «completó una abstención» de «corrigió un valor» | sin medición no hay calibración |
| A3 | Poblar el glosario con las convenciones del operador | la primera causa de edición son abstenciones de cantidad y unidad |
| A4 | Fijar una política por canal, decidida una sola vez; empezar por el grupo de 170 imágenes pendientes que rinde 3% | es curar fuentes, no anuncios |
| A5 | Correr el carril 1 en modo sombra | produce con evidencia los umbrales y el error esperado |
| A6 | Prueba de Jev fuera de línea (hay acceso desde el 2026-10-02). Se corre contra las decisiones de triage ya tomadas sobre texto: 1.235 anuncios, 156 curados y 1.079 descartados (las imágenes piden volver a correr el OCR local). Desde el 2026-10-02 suma preguntas por campo cerrado (producto, moneda, provincia, formato de venta) contra las filas curadas. Mide si su probabilidad está calibrada sobre anuncios cubanos (¿el 0,9 acierta el 90%?) y cuánto coincide con Claude. Solo va texto redactado con `redact_personal_data()`, igual que hoy hacia Anthropic, y cuesta menos de $0,20 | decide en firme si Jev entra como señal desconectable o queda descartado |

A1 depende de que el pre-cálculo del modelo haya corrido sobre las imágenes, y hoy
se lanza a mano. Correrlo con un temporizador no es Redis ni Celery, pero choca con
dos cosas:
- el motivo escrito de la decisión cerrada 3 («la extracción corre cuando el
  operador abre la cola»);
- el plan del asistente, que lo dejó fuera de alcance.

Si se hace, se registra.

### Fase B — apartamiento registrado

Toca el esquema de datos y el motor de validación, así que según `CLAUDE.md` lleva el
flujo de especificación completo.

| # | Qué |
| --- | --- |
| B1 | Construir el motor de validación de §6.2 (reglas 1, 2 y 6, y reputación por canal) para que llene `validation_status` y `confidence_score`, y guardar la procedencia de cada fila |
| B2 | Encender los carriles 0 y 1 con los umbrales medidos en A5, registrados en `decisions.md` |
| B3 | Redefinir la métrica de la Fase 0 como tasa de automatización más error auditado del carril automático. Los anuncios curados a mano quedan como conjunto de referencia congelado, y calibración y evaluación se hacen sobre conjuntos separados |
| B4 | Que la capa pública muestre por qué confía en cada dato (§6.5) |
| B5 | Guardar el emisor de grupo (`participant`) para deduplicar y medir reputación por vendedor, como hace elTOQUE. El JID de WhatsApp suele ser el número de teléfono: guardarlo en claro viola §10.6, así que va solo como hash con clave, nunca expuesto |

### Fase C — escala

- **Lotes:** API por lotes (−50%), disparada por el temporizador.
- **Imágenes:** mandarlas a un modelo de visión con redacción previa, tapando
  teléfonos y nombres con las cajas del OCR antes de enviar. Toca la regla «la
  imagen nunca sale del VPS» y §10.6.
- **Fuentes nuevas:** activar los controles de la Fase 2 (§10.7, C1–C4) antes de
  sumar fuentes nuevas o abiertas.
- **Modelos propios:** solo si el costo de la API o la dependencia del proveedor lo
  justifican.

---

## 6. Decisiones que esto toca

| Decisión | Qué dice | Qué la toca | Fase |
| --- | --- | --- | --- |
| Decisión cerrada 2 | la curaduría es manual | el carril 1 | B |
| spec §6.1 (vinculante) | OCR y extracción son asistentes de la cola, «no etapas autónomas» | los carriles 0 y 1 (A1 no, porque el operador confirma el lote) | B |
| spec §10.7 (vinculante) | en la Fase 0–1, la barrera contra datos falsos es un humano por registro | el carril 1: obliga a adelantar C1–C4 | B |
| `decisions.md`, 2026-08-12 | el dato persistido es correcto por construcción | deja de valer para el carril 1, que necesita procedencia por fila | B |
| spec §12.6 (vinculante) | ningún umbral se inventa | los umbrales del carril 1, que se miden en modo sombra | A5 → B2 |
| Decisión cerrada 3 | sin cola de trabajos | un temporizador para el pre-cálculo o los lotes: no es una cola, pero cambia el motivo de la decisión | A / C |
| Privacidad, §10.6 | no se guardan teléfonos; la imagen no sale del VPS | guardar el emisor; el modelo de visión directo | B5 / C |
| H0 | se mide sobre el conjunto etiquetado a mano | la auditoría continua pasa a ser la medición, con calibración y evaluación separadas | B |
| Descarte de Jev (2026-09-21) | no reabrir salvo pesos autoalojables o español verificado | A6 lo prueba en otro papel, sin integrarlo | A |

Ninguna se toca sin una entrada en `decisions.md` que diga a cuál reemplaza.

---

## 7. Riesgos

| Riesgo | Mitigación |
| --- | --- |
| Error sistemático silencioso, como un canal que publica en MLC y se lee como USD: la mediana no lo filtra | auditoría estratificada por canal, y comparar la mediana de cada canal contra la del resto |
| Deriva por jerga o productos nuevos | la tasa de error auditada la detecta; el carril 1 se estrecha solo y lo nuevo cae al carril 2 |
| Manipulación sin un humano por registro | lista blanca de canales (hoy 13, elegidos por el operador), reputación por canal y C1–C4 |
| Celdas finas: ~5 filas por celda y semana | revisión humana prioritaria donde la celda es chica, y el N de §12.6 medido |
| Aprobar sin mirar (*rubber-stamping*) | ya es el riesgo vivo del runbook, y la métrica que lo mide está inflada (ver sección 10) |
| Contaminar la medición | calibrar y evaluar sobre conjuntos distintos |

---

## 8. Qué no hacer ahora

- **Modelos propios, afinado, destilación o Snorkel:** no bajan horas humanas y suman
  infraestructura.
- **Un segundo LLM como verificador:** las validaciones deterministas rinden más,
  cuestan cero y no comparten los sesgos del modelo. Un LLM, además, favorece sus
  propias salidas al evaluarlas [8][26].
- **Redis o Celery:** alcanza con un temporizador.
- **Capturas sin redactar a un proveedor externo:** no se mandan.
- **Jev en producción antes de pasar la prueba A6:** sería un proveedor nuevo, en
  acceso anticipado, recibiendo datos que salen del VPS y con una calibración sin
  verificar.

---

## 9. Métricas para el inversor

| Métrica | Definición | Hoy |
| --- | --- | --- |
| Tasa de automatización | % de anuncios resueltos sin tocarlos uno por uno | 52% de lo resuelto (lote sin foco); 0% de las filas de precio |
| Error auditado del carril automático | errores en una muestra aleatoria, con intervalo al 95% | no existe todavía |
| Horas humanas por 1.000 anuncios | tiempo total sobre volumen | ~5,6–6,5 h (estimado) |
| Frescura | días entre la publicación del anuncio y su fila en la base | ~16 días de atraso |
| Costo de cómputo por anuncio | modelo + OCR | $0,0015–0,0029; el OCR es local y gratis |

La frase para él: las horas humanas dejan de crecer con el volumen. Pasan a crecer
con la novedad, es decir, con los patrones que el sistema todavía no conoce, más una
auditoría de tamaño fijo.

---

## 10. Hallazgos colaterales

- **Bug en `/control`.**
  - La métrica «Aceptación sin revisar» cuenta como aceptada una fila que el modelo
    propuso y el operador borró: `_edited` (`llm_store.py`) devuelve vacío cuando la
    fila final no existe.
  - Muestra 80% (106/132). Sin las filas borradas da 78,3% (94/120).
  - Al revés, cuando la provincia fija del canal pisa la propuesta, cuenta como
    edición aunque el operador no tocó nada.
- **`docs/costos.md` quedó alto.** Su tabla de $0,0128 por anuncio es de antes de
  apagar `thinking`; el costo real es 4–8 veces menor.
- **El glosario está vacío en producción**, justo donde está la primera causa de
  edición.
- **«Correcto por construcción» no está verificado.**
  - En 2 de las 3 correcciones a valores del modelo, el error parece del operador
    (inferido, con n muy chico).
  - El humano también se equivoca: en la ONS británica, los etiquetadores
    coincidieron con la mayoría en el 88,8% de los casos [27].

  La auditoría tiene que cubrir también lo curado a mano.
- **`/edit` modifica filas de `prices_history` en el lugar, sin historial.** Una
  corrección posterior borra la versión anterior, contra el espíritu de §6.5.
- **Hubo un hueco de ingesta entre el 2026-09-08 y el 2026-09-20.** El 2026-09-21
  hubo un backfill de texto; las fotos de ese período no se recuperan. No figura en
  `decisions.md` (inferido).

---

## Fuentes

Para este estudio verifiqué directamente [8] (el resumen), [14] (a través del
buscador, porque el sitio bloquea la descarga), [18]–[20], [28] y [29]. El resto
sale del relevamiento bibliográfico y no lo releí.

1. NPR Planet Money (2025), la paradoja de Jevons: https://www.npr.org/sections/planet-money/2025/02/04/g-s1-46018/ai-deepseek-economics-jevons-paradox
2. AWS, condiciones de activación de A2I con Textract: https://docs.aws.amazon.com/sagemaker/latest/dg/a2i-json-humantaskactivationconditions-textract-example.html
3. Statistics Norway (2024), *Principles and guidelines for data editing*: https://www.ssb.no/en/teknologi-og-innovasjon/informasjons-og-kommunikasjonsteknologi-ikt/artikler/principles-and-guidelines-for-data-editing/_/attachment/inline/d403545c-d5d7-471f-9340-9bb01ae1e342:9fff1f242d7cf0ba234c941e97365c1ec565942e/NOT2024-31.pdf
4. Cavallo y Rigobon, *The Billion Prices Project* (NBER WP 22111): https://www.nber.org/papers/w22111
5. Eurostat (2020), guía de web scraping para el IPCA: https://ec.europa.eu/eurostat/documents/272892/12032198/Guidelines-web-scraping-HICP-11-2020.pdf
6. PMA (2017), *Collecting Prices for Food Security Programming*: https://www.calpnetwork.org/wp-content/uploads/2020/03/1493893605.wfp291385-1.pdf
7. Arbia et al. (2023), FPCA, *Scientific Data*: https://www.nature.com/articles/s41597-023-02211-1
8. Jin et al. (2026), *Perception, Layout, and Validation: Calibrated Confidence for Reliable Straight-Through Processing of Financial Documents*: https://arxiv.org/abs/2609.20110
9. Angelopoulos et al., *Learn then Test*: https://arxiv.org/abs/2110.01052
10. Hyperscience, *Transcription Accuracy and Automation*: https://help.hyperscience.ai/v41/docs/transcription-accuracy-and-automation
11. UBC, notas de estadística robusta, «Maximum Bias»: https://www.stat.ubc.ca/~ruben/robustness/notes7.pdf
12. HyperStat, distribución muestral de la mediana: https://davidmlane.com/hyperstat/A106993.html
13. Spackman et al. (2025), Statistics Canada, control de calidad contra el sesgo de clasificación: https://unece.org/sites/default/files/2025-04/Spackman%20et%20al%20(2025)%20-%20QC%20for%20mitigating%20misclassification%20bias%20-%20paper.pdf
14. elTOQUE, metodología de la TRMI: https://eltoque.com/metodologia-de-la-tasa-representativa-del-mercado-informal-de-eltoque
15. elTOQUE (2024), refuerzo de controles de la tasa: https://eltoque.com/eltoque-refuerza-controles-para-calcular-la-tasa
16. Pulse Lab Jakarta, *nowcasting* de precios de alimentos con Twitter: https://pulselabjakarta.org/assets/uploadworks/2018-11-07-08-28-25.pdf
17. Adewopo et al. (2025), *PLOS ONE*: https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0320720
18. Anthropic, precios: https://platform.claude.com/docs/en/about-claude/pricing
19. Anthropic, procesamiento por lotes: https://platform.claude.com/docs/en/build-with-claude/batch-processing
20. Anthropic, visión: https://platform.claude.com/docs/en/build-with-claude/vision
21. Bucher y Martini (2024), modelos afinados frente a *zero-shot*: https://arxiv.org/abs/2406.08660
22. Zaratiana et al., GLiNER (NAACL 2024): https://aclanthology.org/2024.naacl-long.300/
23. NuMind, NuExtract 2.0: https://huggingface.co/numind/NuExtract-2.0-8B
24. Kang, Xu y Ritter, *Distill or Annotate?* (ACL 2023): https://arxiv.org/abs/2305.01645
25. Ratner et al., Snorkel (VLDB 2018): https://www.vldb.org/pvldb/vol11/p269-ratner.pdf
26. Panickssery, Bowman y Feng (NeurIPS 2024), autopreferencia de los evaluadores LLM: https://arxiv.org/abs/2404.13076
27. ONS (2020), clasificación automática de ropa obtenida por web scraping: https://www.ons.gov.uk/economy/inflationandpriceindices/articles/automatedclassificationofwebscrapedclothingdatainconsumerpricestatistics/2020-09-01
28. eesel (2026), *TypeSafe Jev review*: https://www.eesel.ai/blog/typesafe-jev-review
29. DigitalOcean (2026), *What is Jev*: https://www.digitalocean.com/resources/articles/what-is-jev
