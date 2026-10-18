# Guía de crecimiento en redes para Guani

Escrita el 2026-10-18 (tarea T15 de `docs/plan-bloque-mvp.md`). Está pensada
para quien no hizo nunca marketing: dice qué publicar, cuándo, qué mirar en
`/marketing` y qué hacer durante el primer mes. Sigue la estructura de un
*campaign brief* (objetivo, audiencia, mensaje, canales, calendario, piezas,
métricas, presupuesto, riesgos, próximos pasos). Las cifras de plataformas
llevan fuente y fecha. Lo que no se pudo comprobar dice **«no verificado»**.

> **Antes de nada: la puerta del sitio.** Desde el 2026-10-16 la puerta del
> sitio está encendida a propósito (`docs/plan-bloque-mvp.md`, decisión 1) y se
> apaga cuando termine el bloque MVP. Mientras siga encendida, un enlace desde
> Instagram lleva a un formulario de contraseña. **No pongas el enlace en la
> biografía hasta que la puerta esté apagada.** El plan de 30 días de abajo
> funciona igual: las dos primeras semanas no dependen del enlace.

## Camino rápido

1. Creá el perfil desde la marca: «Guani, por Leira Studio», sin ningún nombre
   personal (decisión del 2026-10-05).
2. Publicá entre 4 y 5 piezas por semana con las tarjetas que el lote deja
   cada mañana en `var/marketing/AAAA-MM-DD/` ([calendario](#5-calendario-semanal)).
3. Contestá todos los comentarios y mensajes del día.
4. Los lunes, 30 minutos: anotá las cifras de `/marketing` y del contador de
   visitas en la [planilla semanal](#planilla-semanal), y cambiá **una sola**
   cosa para la semana siguiente.

---

## 0. El embudo, en tres palabras

Un **embudo** es el camino que hace una persona desde que no te conoce hasta
que te usa por costumbre. Para Guani tiene tres pasos:

| Paso | Qué pasa | Dónde ocurre | Qué lo empuja | Cómo se mide hoy |
|---|---|---|---|---|
| **Descubrir** | Alguien ve una tarjeta de Guani por primera vez | Instagram (feed, Explorar, un mensaje que le reenvió un amigo) | Una cifra útil a primera vista y que alguien la comparta | **Alcance** y **compartidos** por publicación, en `/marketing` |
| **Visitar** | Entra al sitio para ver su producto o su provincia | `/precios`, `/producto/<slug>` | El enlace de la biografía, una tarjeta que deja con ganas de más | **Visitantes únicos por día** en el log del servidor ([cómo](#visitas-al-sitio)) |
| **Volver** | Vuelve la semana siguiente sin que nadie se lo recuerde | Instagram y el sitio | Una cita fija («el lunes, cómo se movió») y dato fresco | **Seguidores** por día en `/marketing`. En el sitio **no se mide** ([por qué](#lo-que-no-se-mide)) |

La regla práctica: cada pieza trabaja **un** paso. Una tarjeta que intenta
hacer descubrir, visitar y volver a la vez no hace ninguno bien.

**Ojo con la conexión del público.** La mediana de velocidad de descarga
**fija** en Cuba era de 3,48 Mbps a fines de 2025; DataReportal no publica la
móvil ([Digital 2026: Cuba](https://datareportal.com/reports/digital-2026-cuba),
8 de noviembre de 2025). Por eso **la tarjeta tiene que dar la respuesta sin
que nadie haga clic**. El enlace es un regalo para el que quiere más, no una
condición para entender la pieza.

---

## 1. Resumen de la campaña

| | |
|---|---|
| **Nombre** | «Precios de hoy» (de trabajo; cambialo si querés) |
| **En una frase** | Publicar todos los días un precio real del mercado informal, por provincia y con su cuenta, para que la gente lo comparta y vuelva a buscarlo. |
| **Objetivo principal** | Construir el hábito: que las mismas personas vuelvan cada semana. |
| **Objetivo del proyecto al que sirve** | `spec.md` §9, Fase 1: «100 usuarios activos diarios consultando el dashboard». Es la única meta numérica de público que fija la especificación. |
| **Objetivos secundarios** | Publicar con regularidad (4–5 piezas por semana); sostenerlo con 20–30 minutos por día. |

**Sobre las metas numéricas.** Fuera de las 100 visitas diarias de §9, esta
guía **no inventa metas** de seguidores ni de alcance: no hay una línea de base.
La primera semana se mide y las metas salen de ahí. Es el mismo criterio que
`spec.md` §12.6 aplica a los umbrales del motor: una cifra puesta por intuición
y presentada como objetivo es peor que no tener cifra.

---

## 2. Audiencia

**Principal: el hogar cubano que compra.** «Cualquier cubano, en 5 segundos,
entienda la situación de su producto en su provincia» (`spec.md` §6.7). Fase 1
apunta al «usuario doméstico» (`spec.md` §9).

> Una persona que hace las compras de la casa en La Habana o en la otra
> provincia piloto, que ve los precios saltar de un grupo de WhatsApp a otro y
> no sabe si le están cobrando de más. Se entera de las cosas por WhatsApp,
> Facebook e Instagram, con datos caros y conexión lenta. Le importa una sola
> pregunta: **¿cuánto cuesta esto hoy, acá?**

**Secundarias** (`spec.md` §5; `docs/propuesta-narrativa-landing.md`):

| Quién | Qué busca | Qué pieza le sirve |
|---|---|---|
| Quien revende o tiene una mipyme | Qué se mueve y dónde | «Cómo se movió» |
| Quien investiga o informa (medios, academia) | Datos con metodología | «De dónde sale este precio», `/metodologia`, `/datos.csv` |

**Cuántos están en redes.** Cuba tenía 7,79 millones de personas usando
internet (71,3 % de la población) a fines de 2025 y 6,56 millones de
identidades en redes sociales en octubre de 2025
([DataReportal, Digital 2026: Cuba](https://datareportal.com/reports/digital-2026-cuba),
8 de noviembre de 2025). **Cuántos usan Instagram en particular: no
verificado.** Esa página no publica alcance por plataforma salvo TikTok.

---

## 3. Mensajes

**Mensaje central:** *Precios reales del mercado informal cubano, provincia por
provincia, y cada cifra enseña de dónde sale.*

Es la diferencia que fija `spec.md` §5: la **trazabilidad por dato** (se ve el
anuncio original), no la transparencia metodológica en abstracto.

| Mensaje de apoyo | Prueba que lo sostiene (todo comprobable hoy en el sitio) |
|---|---|
| **Cada precio se puede comprobar.** | Detrás de cada cifra están los anuncios que la originaron (tarjeta «De dónde sale este precio»). |
| **Tu provincia, no un promedio del país.** | Matriz producto × provincia. |
| **Compara lo comparable.** | «Aceite 1 L» y «aceite 900 ml» se llevan a la misma unidad antes de calcular. |
| **Una persona lo revisa.** | Cada cifra dice cuántos anuncios revisó una persona y cuántos aceptó el sistema. |

Fuente de esta lista: `docs/plan-marca-leira-studio.md`, paso 3.

**Lo que no se dice todavía** (mismo documento):

| Tema | Cómo decirlo si preguntan |
|---|---|
| Predicción de precios | «Prevista en la hoja de ruta, sobre el histórico que se está acumulando.» |
| Oferta para empresas | «Hoy no la ofrecemos.» |
| Cobertura nacional | Las cifras reales: la meta actual es de 3 a 5 productos en 2 provincias (`spec.md` §9, Fase 0). |
| Código abierto | No se menciona. |

**Tono.** El público lee en tú: «¿Cuánto cuesta el aceite hoy en tu
provincia?». Sin política, sin culpables: Guani muestra el precio, no opina de
por qué está así. Cuando la tasa de cambio aparece, se atribuye a elTOQUE.

---

## 4. Canales

| Canal | Para qué paso del embudo | Esfuerzo | Estado |
|---|---|---|---|
| **Instagram** (feed + Stories) | Descubrir y volver | Medio | **Canal principal.** Publicación por API aprobada el 2026-10-16, todavía no construida (T8b). Mientras tanto, a mano desde el teléfono. |
| **Reenvío por WhatsApp y Telegram** | Descubrir | Bajo | Lo hace el público, no vos. Es donde vive el mercado. Cada pieza se diseña para que valga la pena reenviarla. |
| **Sitio** (`/precios`, `/producto/<slug>`) | Visitar | — | Ya existe. Depende de apagar la puerta. |
| **Facebook** | — | — | **Fuera del MVP** (decisión del 2026-10-16 y `docs/investigacion-publicacion.md`). La tarjeta «Portada de Facebook» queda para el día que se abra la página. |
| **Pago (anuncios)** | — | — | **No se planifica.** Si Meta deja segmentar anuncios hacia Cuba y cómo se pagarían: no verificado. |
| **Prensa e instituciones** | Descubrir | Alto | Es otro plan: `docs/plan-marca-leira-studio.md`. |

**Qué premia Instagram, según Instagram.** En enero de 2025 Adam Mosseri,
director de Instagram, nombró tres señales principales: tiempo de
visualización, me gusta por alcance y **envíos por alcance**. Dijo que los me
gusta pesan un poco más con quien ya te sigue, y los envíos con quien todavía
no te sigue ([Social Media Today](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/),
enero de 2025; fuente secundaria, el video original no se leyó). Para Guani
eso se traduce en una sola pregunta por pieza: **¿alguien se la mandaría a su
mamá?**

**Hashtags.** Mosseri dijo que ayudan a que Instagram entienda de qué trata una
publicación, pero que no son una forma de ganar alcance
([Hootsuite](https://blog.hootsuite.com/social-media-updates/instagram/hashtags-dont-affect-reach/);
la fecha de la declaración no está confirmada, las fuentes la ubican entre
2022 y 2025). Usá 3 a 5 descriptivos (#Cuba, #LaHabana, #PreciosCuba, el
producto), no 30.

**Contenido original.** Desde abril de 2024 Instagram saca de las
recomendaciones a las cuentas que suben contenido ajeno sin transformarlo
([Social Media Today](https://www.socialmediatoday.com/news/instagrams-updating-its-ranking-algorithm-to-put-more-focus-on-original-co/622424),
abril de 2024), y en 2026 lo extendió a fotos y carruseles
([Tubefilter](https://tubefilter.com/2026/04/30/instagram-removes-algorithm-recommendations-repost-content-aggregator),
30 de abril de 2026). Las tarjetas de Guani son propias: es una ventaja. **No
republiques capturas de anuncios ni de otras cuentas.**

---

## 5. Calendario semanal

### Los nueve formatos y para qué sirve cada uno

Son las nueve piezas publicables de `/marketing` (`src/saimc/marketing/context.py`,
`CARD_TYPES`). Hay una décima, «Vista previa al compartir» (`og-compartir`),
que no se publica: es la imagen que aparece cuando alguien pega un enlace del
sitio.

| # | Pieza | Medida | Paso del embudo | Cuándo | De dónde sale |
|---|---|---|---|---|---|
| 1 | **Dato del día** | 1080×1350 (4:5) | Descubrir | Casi todos los días | Lote diario, todos los días |
| 2 | **Cómo se movió** | 1080×1350 | Volver | Lunes | Lote diario, los lunes |
| 3 | **De dónde sale este precio** | 1080×1350 | Visitar / confianza | Jueves | Lote diario, los jueves |
| 4 | **Carrusel · portada** | 1080×1350 | Descubrir | Miércoles, 1.ª lámina | Lote diario, los miércoles (carrusel) |
| 5 | **Carrusel · cómo leer los precios** | 1080×1350 | Volver (se guarda) | Miércoles, 2.ª lámina | Lote diario, los miércoles (carrusel) |
| 6 | **Carrusel · cierre** | 1080×1350 | Visitar | Miércoles, última lámina | Lote diario, los miércoles (carrusel) |
| 7 | **De los grupos a la cifra** | 1080×1350 | Confianza | Una vez, fijada arriba del perfil | `/marketing`, a mano |
| 8 | **Story de precalentamiento** | 1080×1920 (9:16) | Volver | La víspera de algo nuevo | `/marketing`, a mano |
| 9 | **Portada de Facebook** | 1640×624 | — | Cuando se abra la página de Facebook | `/marketing`, a mano |

Notas por pieza:

- **Dato del día.** Es la pieza de descubrimiento. El texto que la acompaña
  repite la respuesta en una línea («Aceite 1 L en La Habana: entre X y Y
  CUP») y termina con un pedido de envío: «Mándaselo a quien hace las compras».
- **Cómo se movió.** Es la cita fija. Siempre el lunes, siempre a la misma
  hora: eso es lo que hace volver.
- **De dónde sale este precio.** Es la que ningún competidor tiene (`spec.md`
  §5). **Antes de publicarla, mirala entera:** no puede aparecer ningún
  teléfono, nombre de vendedor, grupo ni canal (`spec.md` §10.6). Si aparece
  algo así, no se publica y es un error a corregir en el código.
- **Carrusel (piezas 4, 5 y 6).** Va armado como portada → cómo leer →
  uno o dos «Dato del día» → cierre. La lámina «cómo leer» lleva cifras de
  ejemplo con el rótulo «EJEMPLO ILUSTRATIVO»: no lo tapes. Los carruseles son
  el formato que más interacción por seguidor tuvo en 2025: 0,55 %, contra
  0,52 % de los Reels y 0,37 % de la imagen sola
  ([Socialinsider](https://www.socialinsider.io/de/social-media-benchmarks/instagram),
  35 millones de publicaciones de 2025; leído en un resumen, la cifra no se
  contrastó con el informe completo). La diferencia es chica: no cambies el
  plan por ella.
- **De los grupos a la cifra.** Se publica una vez y se fija arriba del perfil
  junto con un carrusel. Responde «¿qué es esto?» a quien llega por primera
  vez.
- **Story de precalentamiento.** Para anunciar algo («Mañana, cómo se movió el
  pollo esta semana»). Las Stories duran 24 h.
- **Reels.** Guani no tiene ningún formato de video aprobado (el comercial de
  `docs/concepto-comercial-guani.md` es una propuesta). Según un análisis de
  Buffer citado por terceros, los Reels llegan a más gente y los carruseles
  generan más interacción (no verificado en la fuente original). Queda como
  experimento para después del primer mes, no para ahora.

### La semana tipo

El lote ya deja como borrador las piezas 1, 2 y 3 y, los miércoles, el
carrusel entero (piezas 4, 5, un «Dato del día» y 6, en ese orden) con este
ritmo (`WEEKLY_CARDS` en `src/saimc/marketing/daily.py`). Para un segundo
«Dato del día» en el carrusel, agregalo desde el editor. El resto es a mano.

| Día | Feed | Stories | Tiempo |
|---|---|---|---|
| Lunes | **Cómo se movió** | Recordá la pieza del día | 20 min + 30 min de revisión semanal |
| Martes | Dato del día | — | 10 min |
| Miércoles | **Carrusel** (portada + cómo leer + datos + cierre) | Encuesta: «¿Qué producto miramos el lunes?» | 30 min |
| Jueves | **De dónde sale este precio** | — | 15 min (incluye revisar privacidad) |
| Viernes | Dato del día | — | 10 min |
| Sábado | — (o Dato del día si la semana fue floja) | Respuestas a las preguntas de la semana | 10 min |
| Domingo | Descanso | **Story de precalentamiento** del lunes | 5 min |

Son **5 publicaciones de feed por semana**. Buffer, sobre unos 2 millones de
publicaciones, encontró que 3 a 5 por semana es el punto que da alcance y
crecimiento sin quemarse; publicar más ayuda, pero cada vez menos
([Buffer](https://buffer.com/resources/how-often-to-post-on-instagram/),
agosto de 2025; es un estudio de correlación y Buffer vende software para
programar publicaciones). Si una semana no llegás a 5, bajá a 3. Lo que más
pesa es no dejar una semana entera vacía.

**Hora de publicar: no verificada para Cuba.** Los estudios de «mejor hora»
son de audiencias de otros países. Empezá a las 8:00 de La Habana (el lote
termina a las 06:00) y mirá en la [planilla](#planilla-semanal) si cambia algo
probar a la noche.

---

## 6. Piezas que hay que preparar una sola vez

| Pieza | Para qué | Prioridad |
|---|---|---|
| Perfil de Instagram: nombre «Guani», biografía de dos líneas, avatar de la marca | Que se entienda qué es en 3 segundos | Imprescindible |
| Biografía, propuesta: «Precios reales del mercado informal cubano. Por provincia, con su cuenta.» | — | Imprescindible |
| Enlace en la biografía a `/precios` | Paso «visitar» | **Solo cuando se apague la puerta** |
| «De los grupos a la cifra» + un carrusel, fijados arriba del perfil | Explicar a quien llega | Imprescindible |
| Tres respuestas guardadas (en el teléfono) | Contestar rápido: «¿de dónde sacan el precio?», «¿por qué no está mi provincia?», «el precio está mal» | Recomendable |
| Campo de Instagram en el pie del sitio | Hoy el pie enlaza Facebook, WhatsApp y Telegram desde configuración; **no tiene Instagram**. | Recomendable, **fuera de alcance de esta guía** (es un slice de código) |

---

## 7. Métricas

### Lo que ya mide `/marketing`

La sección «Instagram» de `/marketing` (con la contraseña de operador) lee lo
que guarda el lote `saimc.instagram.sync` cada 3 horas. Meta puede tardar
hasta 48 horas en completar las cifras de una publicación.

| Métrica | Qué significa | Paso del embudo | Cómo leerla |
|---|---|---|---|
| **Seguidores** (y seguidores por día, 30 días) | Cuánta gente eligió volver | Volver | Importa la tendencia semanal, no el día. |
| **Alcance** | Cuentas distintas que vieron la publicación | Descubrir | Es el denominador de casi todo lo demás. |
| **Vistas** | Veces que se vio (una persona puede contar varias) | Descubrir | Vistas mucho mayores que el alcance: la gente vuelve a mirarla. |
| **Compartidos** | Veces que alguien la mandó por mensaje o a su Story | Descubrir | **La métrica más importante** para crecer con gente que no te sigue. |
| **Guardados** | Veces que alguien la guardó para después | Volver | Alto en el carrusel «cómo leer»: va bien. |
| **Me gusta / comentarios** | Reacción de quien ya te sigue | Volver | Los comentarios además son la conversación de la comunidad. |
| **Interacciones** | Suma que calcula Meta | — | Mirá las partes, no la suma. |
| **Segundos medios de reproducción** | Solo Reels | — | No aplica mientras no haya Reels. |

**Los carruseles salen «sin cifras»** en el panel: Meta no las da por la API
que usa Guani. Para el carrusel del miércoles, mirá las estadísticas en la
propia app de Instagram y anotalas a mano.

**Las dos cuentas que tenés que hacer vos.** El panel da números sueltos.
Dividí por el alcance para poder comparar una publicación con otra:

- **Compartidos ÷ alcance** (envíos por alcance, la señal que nombró Mosseri).
- **Guardados ÷ alcance.**

Una publicación con 200 de alcance y 10 compartidos (5 %) funcionó mejor que
una con 1.000 de alcance y 10 compartidos (1 %).

### Visitas al sitio

El sitio no tiene analítica con cookies ni JS: deja una línea en el log por
cada visita a `/`, `/precios`, `/producto/*` y `/anuncio/*`, con un
identificador anónimo que cambia cada día (`src/saimc/web/analytics.py`).
Visitantes únicos de hoy:

```
journalctl -u saimc.service --since today | grep 'public.pageview' | grep -oP 'visitor=\K\S+' | sort -u | wc -l
```

Está en `docs/comandos.md`, «Visitantes únicos del sitio», con la variante
para un día puntual. Ese número es lo más cercano a los «usuarios activos
diarios» de `spec.md` §9.

### Lo que no se mide

| Pregunta | Por qué no | Qué hacer mientras tanto |
|---|---|---|
| ¿Cuántas visitas vinieron de Instagram? | El log no guarda de dónde viene la visita ni hay parámetros UTM. | Mirá si las visitas suben el día que publicás. Es una pista, no una prueba. |
| ¿Cuánta gente vuelve al sitio? | El identificador cambia cada día a propósito (privacidad). | Usá los seguidores de Instagram como señal de «volver». |
| ¿Cuántas personas detrás de una IP? | Muchas conexiones pueden compartir IP; cuánto pasa en Cuba: no verificado. | Tomá el número como piso, no como cifra exacta. |

Medir el origen de las visitas sería un slice de código aparte, con su propia
decisión de privacidad. **Fuera de alcance de esta guía.**

### Planilla semanal

Una fila por semana, los lunes. Una hoja de cálculo alcanza.

| Semana | Publicaciones | Seguidores (lunes) | Alcance medio | Compartidos ÷ alcance (medio) | Guardados ÷ alcance (medio) | Visitantes únicos (media diaria) | Qué cambié |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | — (línea de base) |
| 2 | | | | | | | |

**Regla de oro: un cambio por semana.** Si cambiás la hora, el texto y el
producto a la vez, no vas a saber cuál funcionó.

---

## 8. Comunidad

Comunidad no es tener seguidores: es que la gente te hable y se hable entre
sí sobre lo que publicás.

1. **Contestá todo el mismo día.** Cada comentario y cada mensaje, aunque sea
   con una de las respuestas guardadas.
2. **Preguntá lo que puedas cumplir.** La encuesta del miércoles («¿qué
   producto miramos el lunes?») ofrece solo productos que Guani ya cubre. No
   prometas productos ni provincias fuera de la meta de Fase 0 (`spec.md` §9).
3. **Cuando alguien dice «el precio está mal».** Agradecé y pedí, por mensaje
   privado, el producto, la provincia y el precio que vio. **Nunca** pidas ni
   guardes teléfonos, nombres ni capturas con datos de contacto (`spec.md`
   §10.6). Si tiene razón, corregilo en el sitio y contalo en una Story: un
   error reconocido en público suma confianza. Nunca borres una publicación
   en silencio.
4. **Mostrá a la comunidad sin exponerla.** Podés decir «Esta semana nos
   preguntaron por el pollo» sin nombrar a nadie. No publiques capturas de
   comentarios o mensajes con el nombre o la foto de la persona.
5. **Facilitá el reenvío a WhatsApp.** Es donde se compra y se vende. Cada
   «Dato del día» tiene que entenderse solo, sin texto alrededor, porque
   llega reenviado sin el texto de la publicación.
6. **No uses el número de WhatsApp de la ingesta** para la marca: es el que
   usa WAHA para leer grupos (`docs/plan-marca-leira-studio.md`, paso 2).
7. **Sin peleas.** Si alguien discute de política, respondé una vez con el
   dato y la metodología (`/metodologia`) y no sigas.

---

## 9. Plan de 30 días

**Requisito previo:** el lote `saimc-marketing.timer` corriendo (deja los PNG
a las 06:00) y acceso a `/marketing`. La publicación es a mano hasta que exista
T8b.

### Semana 1 — Preparar y medir la línea de base

| Día | Tarea | Listo cuando |
|---|---|---|
| 1 | Crear el perfil con la marca (sin nombre personal), biografía y avatar | El perfil se entiende sin leer nada más |
| 1 | Publicar «De los grupos a la cifra» y fijarla | Aparece arriba del perfil |
| 2 | Primer «Dato del día» | Publicado con texto + pedido de envío |
| 3 | Primer carrusel (portada → cómo leer → 2 datos → cierre) y fijarlo | Fijado junto a la anterior |
| 4 | «De dónde sale este precio», revisada a ojo por privacidad | Sin teléfonos, nombres, grupos ni canales a la vista |
| 5 | «Dato del día» | — |
| 6 | Guardar las tres respuestas en el teléfono | — |
| 7 | Story de precalentamiento del lunes | — |

Al cierre de la semana 1 no hay nada que juzgar: solo anotar la primera fila
de la planilla.

### Semana 2 — Ritmo

- La semana tipo completa: 5 publicaciones de feed.
- Primera encuesta en Stories (miércoles).
- Contestar todo el mismo día.
- Lunes: segunda fila de la planilla. **Primer cambio:** el que creas que más
  ayuda (por ejemplo, el texto del pedido de envío).

### Semana 3 — Abrir la puerta al sitio

- **Si la puerta del sitio ya se apagó:** poné `/precios` en la biografía y
  sumá «enlace en la biografía» al cierre del carrusel y de «De dónde sale».
  Contá visitantes únicos cada día de esta semana.
- **Si sigue encendida:** seguí sin enlace. El plan no se frena; el paso
  «visitar» espera.
- Publicá el «Cómo se movió» del producto que ganó la encuesta.

### Semana 4 — Mirar y decidir

- Lunes de la semana 4: con tres filas en la planilla, mirá cuál fue la pieza
  con mejor compartidos ÷ alcance y cuál la peor.
- Hacé más de la mejor y menos de la peor en la semana 4.
- **Día 30:** escribí en dos líneas qué funcionó y qué no, y fijá las metas del
  mes 2 a partir de las cifras reales (no antes).

### Al día 30 tenés que poder contestar

- [ ] ¿Cuántas publicaciones hiciste? (meta del proceso: unas 20)
- [ ] ¿Qué pieza tiene mejor compartidos ÷ alcance?
- [ ] ¿Los seguidores subieron, bajaron o quedaron igual, semana a semana?
- [ ] Si la puerta se apagó: ¿cuál es la media de visitantes únicos por día, y
  qué tan lejos está de 100 (`spec.md` §9)?
- [ ] ¿Cuántas personas escribieron o comentaron, y qué preguntaron?

---

## 10. Presupuesto

No hay presupuesto de dinero: todo es orgánico. El costo real es **tiempo**:
unos 20–30 minutos por día y 30 minutos los lunes (estimación de esta guía,
no medida), además de la hora diaria de curaduría que ya existe.

Si en algún momento hace falta una herramienta para programar publicaciones,
`docs/investigacion-publicacion.md` compara Metricool (desde 16 €/mes, sin API)
con publicar por la API propia (lo decidido el 2026-10-16).

---

## 11. Riesgos

| Riesgo | Señal | Qué hacer |
|---|---|---|
| **La puerta del sitio sigue encendida** | Nadie puede entrar desde Instagram | No poner el enlace; las tarjetas dan la respuesta solas. |
| **Un dato sale con datos personales** | Un teléfono o un nombre en «De dónde sale» | No publicar. Borrar si ya salió, avisar en Story sin repetir el dato, y corregir el código. |
| **Un precio publicado estaba mal** | Un comentario lo señala | Agradecer, corregir, contarlo. Nunca borrar en silencio. |
| **No hay datos para la tarjeta** | El lote salta la pieza y la carpeta del día viene incompleta | Publicar otra pieza de la semana; nunca una tarjeta vacía ni con cifras de ejemplo sin rótulo. |
| **Te quemás** | Dos semanas sin llegar a 3 publicaciones | Bajar a 3 por semana (lunes, miércoles, jueves). Constancia antes que volumen. |
| **Instagram limita la cuenta** | Alcance que cae de golpe sin cambio de contenido | Revisar el «Estado de la cuenta» en la app. Que una cuenta operada desde Cuba tenga restricciones propias: no verificado. |

---

## 12. Próximos pasos

1. **Operador:** elegir el nombre de usuario de Instagram (no está en el repo)
   y crear el perfil desde la marca.
2. **Operador:** confirmar que `saimc-marketing.timer` está corriendo en el
   VPS (`systemctl list-timers saimc-marketing.timer`).
3. **Operador:** decidir cuándo se apaga la puerta del sitio (ya decidido: al
   cerrar el bloque MVP). Este plan asume que pasa durante el mes.
4. **Slices posibles, fuera de esta guía:** campo de Instagram en el pie del
   sitio; publicación por API (T8b); medir el origen de las visitas.

---

## Fuentes

Consultadas el 2026-10-18.

| Fuente | Qué se tomó | Fecha del dato | Estado |
|---|---|---|---|
| [DataReportal, Digital 2026: Cuba](https://datareportal.com/reports/digital-2026-cuba) | Usuarios de internet, identidades en redes, velocidad fija | Octubre–diciembre de 2025 (publicado el 8-11-2025) | Leído en la página. Sin cifra de Instagram. |
| [Buffer, «How Often Should You Post on Instagram in 2025?»](https://buffer.com/resources/how-often-to-post-on-instagram/) | 3–5 publicaciones por semana | Agosto de 2025 | Leído en resumen; los multiplicadores de crecimiento que circulan en otros sitios, no verificados. |
| [Socialinsider, Instagram benchmarks](https://www.socialinsider.io/de/social-media-benchmarks/instagram) | Interacción por formato (carrusel, Reel, imagen) | Año 2025 | Leído en resumen; no contrastado con el informe completo. |
| [Social Media Today, señales de Instagram 2025](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/) | Tiempo de visualización, me gusta y envíos por alcance | Enero de 2025 | Fuente secundaria; el video de Mosseri no se leyó. |
| [Hootsuite, hashtags y alcance](https://blog.hootsuite.com/social-media-updates/instagram/hashtags-dont-affect-reach/) | Los hashtags no dan alcance | Fecha no confirmada | Fuente secundaria. |
| [Social Media Today, contenido original](https://www.socialmediatoday.com/news/instagrams-updating-its-ranking-algorithm-to-put-more-focus-on-original-co/622424) | Penalización a cuentas que republican | Abril de 2024 | Fuente secundaria. |
| [Tubefilter, extensión a fotos y carruseles](https://tubefilter.com/2026/04/30/instagram-removes-algorithm-recommendations-repost-content-aggregator) | Misma penalización en 2026 | 30 de abril de 2026 | Fuente secundaria. |

Del repositorio: `docs/spec.md` §5, §6.7, §9, §10.6 y §12.6;
`docs/plan-bloque-mvp.md` (T15 y decisión 1);
`docs/plan-marca-leira-studio.md`; `docs/propuesta-narrativa-landing.md`;
`docs/investigacion-publicacion.md`; `src/saimc/marketing/`;
`src/saimc/web/analytics.py`; `docs/decisions.md` (2026-10-05, 2026-10-13,
2026-10-16 y 2026-10-18).
