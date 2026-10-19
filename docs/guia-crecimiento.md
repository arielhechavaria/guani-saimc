# Guía de crecimiento en redes para Guani

Escrita el 2026-10-18 (tarea T15 de `docs/plan-bloque-mvp.md`) y actualizada
el mismo día, por la tarde, con lo que cambió después: la API de Meta se
descartó, `/marketing` pasó a ser una fábrica de publicaciones, la voz de la
marca quedó fijada en `docs/narrativa-guani.md` y el video de la landing ya
existe. Está pensada para quien no hizo nunca marketing: dice qué publicar,
cuándo, qué mirar y qué hacer durante el primer mes. Sigue la estructura de un
*campaign brief* (objetivo, audiencia, mensaje, canales, calendario, piezas,
métricas, presupuesto, riesgos, próximos pasos). Las cifras de plataformas
llevan fuente y fecha. Lo que no se pudo comprobar dice **«no verificado»**.

**Para qué sirve esta guía.** En Instagram cada perfil da un consejo distinto.
Esta guía es la única referencia: si un consejo de afuera choca con las tres
reglas de abajo, se ignora. Si parece bueno, se prueba como **el** cambio de
una semana y se mide (§7).

> **Antes de nada: la puerta del sitio.** La puerta está encendida a propósito
> y se apaga al empezar la Fase 1, que llega con el cierre de la Fase 0: T1
> aplicado y `revalidate --execute` corrido después (`docs/decisions.md`,
> «2026-10-18 — Criterio de cierre de la Fase 0 y de inicio de la Fase 1»).
> Mientras siga encendida, un enlace desde Instagram lleva a un formulario de
> contraseña. **No pongas el enlace en la biografía ni escribas «enlace en la
> biografía» hasta que la puerta esté apagada.** El plan de 30 días funciona
> igual: las tarjetas dan la respuesta sin enlace.

## Las tres reglas

1. **Cada publicación responde una pregunta sin que nadie haga clic.** La
   conexión en Cuba es lenta, y la pieza tiene que entenderse sola, también
   reenviada por WhatsApp sin el texto que la acompaña.
2. **«¿Alguien se la mandaría a su mamá?».** Lo que más pesa para llegar a
   gente que todavía no te sigue son los envíos (§4). Por eso la cifra que se
   mira es **compartidos ÷ alcance**.
3. **Un cambio por semana, anotado los lunes.** Si cambias la hora, el texto y
   el producto a la vez, no sabrás cuál funcionó.

**Lo que se ignora, lo diga quien lo diga:** comprar seguidores o
interacciones, seguir y dejar de seguir en masa, poner 30 hashtags, publicar
varias veces al día, subirse a tendencias que no tienen que ver con precios,
republicar contenido ajeno y entrar en debates políticos.

## Camino rápido

1. Perfil desde la marca: nombre «Guani», «Guani, por Leira Studio», sin
   ningún nombre personal (decisión del 2026-10-05). Pásalo a **cuenta
   profesional** para ver las estadísticas en la app (§7).
2. Publica entre 3 y 5 piezas por semana desde la sección «Hoy» de
   `/marketing`, donde el lote deja los borradores del día ([calendario](#5-calendario-semanal)).
3. Contesta todos los comentarios y mensajes del día.
4. Los lunes, 30 minutos: anota las cifras de la app de Instagram y del
   contador de visitas en la [planilla semanal](#planilla-semanal), y cambia
   **una sola** cosa para la semana siguiente.

---

## 0. El embudo, en tres palabras

Un **embudo** es el camino que hace una persona desde que no te conoce hasta
que te usa por costumbre. Para Guani tiene tres pasos:

| Paso | Qué pasa | Dónde ocurre | Qué lo empuja | Cómo se mide hoy |
|---|---|---|---|---|
| **Descubrir** | Alguien ve una tarjeta de Guani por primera vez | Instagram (feed, Explorar, un mensaje que le reenvió alguien) | Una cifra útil a primera vista y que alguien la comparta | **Alcance** y **compartidos** por publicación, en la app de Instagram |
| **Visitar** | Entra al sitio para ver su producto o su provincia | `/precios`, `/producto/<slug>` | El enlace de la biografía, una tarjeta que deja con ganas de más | **Visitantes únicos por día** en el log del servidor ([cómo](#visitas-al-sitio)) |
| **Volver** | Vuelve la semana siguiente sin que nadie se lo recuerde | Instagram y el sitio | Una cita fija («el lunes, cómo se movió») y dato fresco | **Seguidores** por semana, en la app. En el sitio **no se mide** ([por qué](#lo-que-no-se-mide)) |

La regla práctica: cada pieza trabaja **un** paso. Una tarjeta que intenta
hacer descubrir, visitar y volver a la vez no hace ninguno bien.

**Ojo con la conexión del público.** La mediana de velocidad de descarga
**fija** en Cuba era de 3,48 Mbps a fines de 2025; DataReportal no publica la
móvil ([Digital 2026: Cuba](https://datareportal.com/reports/digital-2026-cuba),
8 de noviembre de 2025). Por eso la regla 1: el enlace es un regalo para quien
quiere más, no una condición para entender la pieza.

---

## 1. Resumen de la campaña

| | |
|---|---|
| **Nombre** | «Precios de hoy» (de trabajo; cámbialo si quieres) |
| **En una frase** | Publicar casi todos los días un precio real de lo que se anuncia en Cuba, por provincia y con su cuenta, para que la gente lo comparta y vuelva a buscarlo. |
| **Objetivo principal** | Construir el hábito: que las mismas personas vuelvan cada semana. |
| **Objetivo del proyecto al que sirve** | `spec.md` §9, Fase 1: «100 usuarios activos diarios consultando el dashboard». Es la única meta numérica de público que fija la especificación. |
| **Objetivos secundarios** | Publicar con regularidad (3–5 piezas por semana) y sostenerlo con 20–30 minutos por día. |

**Sobre las metas numéricas.** Fuera de las 100 visitas diarias de §9, esta
guía **no inventa metas** de seguidores ni de alcance, porque no hay línea de
base. La primera semana se mide y las metas salen de ahí. Es el mismo criterio
que `spec.md` §12.6 aplica a los umbrales del motor: una cifra puesta por
intuición y presentada como objetivo es peor que no tener cifra.

---

## 2. Audiencia

**Decidido:** Guani le habla a todo el pueblo cubano y no decide el uso que se
le dé (`docs/narrativa-guani.md`, «A quién le hablamos»). En Instagram, la
puerta de entrada es quien compra, porque es la pregunta más inmediata.

> Una persona que hace las compras de la casa en La Habana o en Santiago de
> Cuba, que ve los precios saltar de un grupo de WhatsApp a otro y no sabe si
> lo que le piden es lo normal. Se entera de las cosas por WhatsApp, Facebook e
> Instagram, con datos caros y conexión lenta. Le importa una sola pregunta:
> **¿cuánto se pide hoy por esto, aquí?**

Otros usos que ya se ven venir (misma sección de la narrativa):

| Quién | Qué se pregunta | Qué pieza le sirve |
|---|---|---|
| Quien vende (emprendedor, TCP, negocio privado) | «¿Dónde cae mi precio frente al de los demás?» | Dato del día, «De dónde sale este precio» |
| Quien revende o abastece un negocio | «¿Qué se mueve y dónde?» | «Cómo se movió» |
| Quien investiga o informa | «¿De dónde sale este número?» | «De dónde sale este precio», `/metodologia` |

La diáspora queda para después: es un público grande, pero sin validar.

**Cuántos están en redes.** Cuba tenía 7,79 millones de personas usando
internet (71,3 % de la población) a fines de 2025 y 6,56 millones de
identidades en redes sociales en octubre de 2025
([DataReportal, Digital 2026: Cuba](https://datareportal.com/reports/digital-2026-cuba),
8 de noviembre de 2025). **Cuántos usan Instagram en particular: no
verificado.** Esa página no publica alcance por plataforma salvo TikTok.

---

## 3. Mensajes

La voz entera está en `docs/narrativa-guani.md`. En Instagram se usa así
(«Recomendación y próximos pasos» de ese documento):

| Capa | Frase | Dónde va |
|---|---|---|
| Idea madre | *Parece un relajo, pero tiene su orden.* | Portada del carrusel, vista previa al compartir. «Relajo» funciona en Instagram; con un aliado va la versión neutra. |
| Eslogan | *Medimos el mercado con la misma vara.* | Biografía y portada de la landing. |
| Prueba | *Cada precio con su cuenta.* | Cierre del carrusel, «De los grupos a la cifra». |
| Cierre | *Guani enseña el precio. Tú decides qué hacer con él.* | Texto genérico del post. |

Las tarjetas ya traen estas frases (`docs/decisions.md`, «Los textos de las
tarjetas siguen `docs/narrativa-guani.md`»).

| Mensaje de apoyo | Prueba que lo sostiene (todo comprobable hoy en el sitio) |
|---|---|
| **Cada precio se puede comprobar.** | Detrás de cada cifra están los anuncios que la originaron (tarjeta «De dónde sale este precio»). |
| **Tu provincia, no un promedio del país.** | Cada producto se mide en su provincia. |
| **Compara lo comparable.** | El litro y los 900 ml, la libra y el kilo: todo se lleva a la misma unidad antes de calcular. |
| **Sabes quién lo revisó.** | Cada cifra dice cuántos anuncios revisó una persona y cuántos aceptó el sistema. **No** se dice «una persona revisa cada precio»: lo claro lo acepta el sistema y una persona lo audita después (`/metodologia`). |

**Lo que no se dice todavía** (`docs/plan-marca-leira-studio.md`, paso 3):

| Tema | Cómo decirlo si preguntan |
|---|---|
| Predicción de precios | «Prevista en la hoja de ruta, sobre el histórico que se está acumulando.» |
| Oferta para empresas | «Hoy no la ofrecemos.» |
| Cobertura | Las cifras reales: cinco productos en dos provincias, La Habana y Santiago de Cuba. |
| Precisión de la lectura | No se da un porcentaje. Si preguntan: «El error de lo que el sistema acepta solo se audita y se publica en la metodología.» |

**Tono.** Al público se le habla de tú, nunca de vos: «¿Cuánto se pide hoy por
el aceite en tu provincia?». Guani habla de sí mismo en tercera persona. Sin
política, sin culpables: Guani muestra el precio y no opina de por qué está
así. Se dice «se pide», no «se paga», y «rango» y «mediana», no «precio
justo». Cuando aparece la tasa de cambio, se atribuye a elTOQUE.

---

## 4. Canales

| Canal | Para qué paso del embudo | Esfuerzo | Estado |
|---|---|---|---|
| **Instagram** (feed, Reels y Stories) | Descubrir y volver | Medio | **Canal principal.** Se publica siempre a mano, desde el teléfono, con lo que arma la fábrica de `/marketing`. La API de Meta se descartó el 2026-10-18: Meta for Developers no está disponible desde Cuba (`docs/decisions.md`, «Se descarta la API de Meta»). |
| **Reenvío por WhatsApp y Telegram** | Descubrir | Bajo | Lo hace el público, no tú. Es donde vive el mercado. Cada pieza se diseña para que valga la pena reenviarla. |
| **Sitio** (`/precios`, `/producto/<slug>`) | Visitar | — | Ya existe. Depende de apagar la puerta. |
| **Facebook** | — | — | **Fuera del MVP** (decisión del 2026-10-16). La tarjeta «Portada de Facebook» queda para el día que se abra la página. |
| **Pago (anuncios)** | — | — | **No se planifica.** Pasa por Meta y por un pago con tarjeta internacional. |
| **Prensa e instituciones** | Descubrir | Alto | Es otro plan: `docs/plan-marca-leira-studio.md` y `docs/presentacion-instituciones.md`. |

**Qué premia Instagram, según Instagram.** En enero de 2025 Adam Mosseri,
director de Instagram, nombró tres señales principales: tiempo de
visualización, me gusta por alcance y **envíos por alcance**. Dijo que los me
gusta pesan un poco más con quien ya te sigue, y los envíos con quien todavía
no te sigue ([Social Media Today](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/),
enero de 2025; fuente secundaria, el video original no se leyó). De ahí sale
la regla 2.

**Hashtags.** Mosseri dijo que ayudan a que Instagram entienda de qué trata una
publicación, pero que no son una forma de ganar alcance
([Hootsuite](https://blog.hootsuite.com/social-media-updates/instagram/hashtags-dont-affect-reach/);
la fecha de la declaración no está confirmada, las fuentes la ubican entre
2022 y 2025). El texto automático ya trae cuatro fijos (`#guani
#preciosencuba #cuba #mercadocubano`, `saimc.marketing.caption`). Si quieres,
suma uno descriptivo a mano (`#LaHabana`, el producto); no más.

**Contenido original.** Desde abril de 2024 Instagram saca de las
recomendaciones a las cuentas que suben contenido ajeno sin transformarlo
([Social Media Today](https://www.socialmediatoday.com/news/instagrams-updating-its-ranking-algorithm-to-put-more-focus-on-original-co/622424),
abril de 2024), y en 2026 lo extendió a fotos y carruseles
([Tubefilter](https://tubefilter.com/2026/04/30/instagram-removes-algorithm-recommendations-repost-content-aggregator),
30 de abril de 2026). Las tarjetas y el video de Guani son propios: es una
ventaja. **No republiques capturas de anuncios ni de otras cuentas.**

---

## 5. Calendario semanal

### Cómo se publica una pieza

Todo sale de la fábrica de `/marketing` (`docs/plan-fabrica-marketing.md`):

1. **Hoy.** El lote de las 06:00 deja los borradores del día en la sección
   «Hoy». Si una pieza no tiene datos, el lote no la crea.
2. **Ajustar.** En el editor se eligen los bloques y se retoca el texto del
   post. Las cifras no se editan.
3. **Aprobar.** Aprobar congela las cifras: lo que se publica es lo aprobado,
   aunque después se corrija un precio.
4. **Al teléfono.** «Copiar texto» y «Compartir» llevan la imagen y el texto al
   teléfono, y se publica desde la app de Instagram.
5. **Marcar como publicada**, pegando el enlace de la publicación.
   `/marketing/semana` muestra qué turnos faltan y el historial.

Para una pieza propia está la tarjeta **«Libre»**: título, texto y llamada
sobre la plantilla de marca. Ahí los números los escribes tú, así que
cualquier cifra que pongas tiene que salir del sitio ese día.

### Los formatos y para qué sirve cada uno

Son las piezas publicables de `/marketing` (`src/saimc/marketing/context.py`,
`CARD_TYPES`) más el video de la landing. Hay una más, «Vista previa al
compartir» (`og-compartir`), que no se publica: es la imagen que aparece
cuando alguien pega un enlace del sitio.

| # | Pieza | Medida | Paso del embudo | Cuándo | De dónde sale |
|---|---|---|---|---|---|
| 1 | **Dato del día** | 1080×1350 (4:5) | Descubrir | Casi todos los días | Lote, todos los días |
| 2 | **Cómo se movió** | 1080×1350 | Volver | Lunes | Lote, los lunes |
| 3 | **De dónde sale este precio** | 1080×1350 | Visitar / confianza | Jueves | Lote, los jueves |
| 4 | **Carrusel** (portada → cómo leer → dato → cierre) | 1080×1350 | Descubrir y volver | Miércoles | Lote, los miércoles, en un solo borrador |
| 5 | **De los grupos a la cifra** | 1080×1350 | Confianza | Una vez, fijada arriba del perfil | `/marketing`, a mano |
| 6 | **El video de la landing** (comercial «Cómo se escoge el arroz») | Vertical | Descubrir y confianza | Una vez, como Reel fijado | `static/landing/guani-flujo.mp4` (`docs/concepto-comercial-guani.md`) |
| 7 | **Story de precalentamiento** | 1080×1920 (9:16) | Volver | La víspera de algo nuevo | `/marketing`, a mano |
| 8 | **Libre** | 1080×1350 | Según la pieza | Cuando haga falta | `/marketing`, a mano |
| 9 | **Portada de Facebook** | 1640×624 | — | Cuando se abra la página de Facebook | `/marketing`, a mano |
| 10 | **Educativo** (carrusel de 4–5 diapositivas) | 1080×1350 | Confianza | Todos los días, segundo turno del lote | Lote, todos los días (`saimc.marketing.lessons`) |

Notas por pieza:

- **Dato del día.** Es la pieza de descubrimiento. El texto repite la
  respuesta en una línea («Aceite 1 L en La Habana: se pide entre X y Y CUP»)
  y termina con un pedido de envío: «Mándaselo a quien hace las compras».
- **Cómo se movió.** Es la cita fija. Siempre el lunes, siempre a la misma
  hora: eso es lo que hace volver.
- **De dónde sale este precio.** Es la que ningún competidor tiene (`spec.md`
  §5). **Antes de aprobarla, mírala entera:** no puede aparecer ningún
  teléfono, nombre de vendedor, grupo ni canal (`spec.md` §10.6). Si aparece
  algo así, no se publica y es un error a corregir en el código.
- **Carrusel.** El lote lo arma en un solo borrador con sus cuatro
  diapositivas; para sumar un segundo «Dato del día», agrégalo en el editor
  (hasta 10 diapositivas). La lámina «cómo leer» lleva cifras de ejemplo con
  el rótulo «EJEMPLO ILUSTRATIVO»: no lo tapes. Los carruseles fueron el
  formato con más interacción por seguidor en 2025: 0,55 %, contra 0,52 % de
  los Reels y 0,37 % de la imagen sola
  ([Socialinsider](https://www.socialinsider.io/de/social-media-benchmarks/instagram),
  35 millones de publicaciones de 2025; leído en un resumen, la cifra no se
  contrastó con el informe completo). La diferencia es chica: no cambies el
  plan por ella.
- **De los grupos a la cifra** y **el video.** Se publican una vez y se fijan
  arriba del perfil. Responden «¿qué es esto?» a quien llega por primera vez.
- **El video.** Cuenta el sistema como es hoy: una máquina lee, lo claro pasa
  y lo dudoso lo decide una persona, que también audita a la máquina. Sus
  cifras son las del día en que se produjo: si el texto del post las menciona,
  lleva esa fecha. Termina diciendo «Mira los precios de hoy en Guani»: con la
  puerta encendida, el texto no promete el enlace.
- **Otros Reels.** Fuera del video de la landing no hay formato de video. Según
  un análisis de Buffer citado por terceros, los Reels llegan a más gente y los
  carruseles generan más interacción (no verificado en la fuente original).
  Queda como experimento para después del primer mes.
- **Educativo.** Una lección corta que rota por día (de los grupos a la cifra,
  rango y mediana, muestra en formación, cada precio con sus anuncios). Es
  texto fijo, sin cifras: no depende de datos y siempre sale en el lote. El
  texto del post ya viene escrito; revísalo igual antes de aprobar.
- **Story de precalentamiento.** Para anunciar algo («Mañana, cómo se movió el
  pollo esta semana»). Las Stories duran 24 h.

### La semana tipo

El lote crea los borradores con este ritmo (`WEEKLY_CARDS` en
`src/saimc/marketing/daily.py`): «Dato del día» y «Educativo» todos los días,
más «Cómo se movió» los lunes, el carrusel los miércoles y «De dónde sale este precio» los
jueves. No hace falta publicar todo lo que el lote crea: la semana tipo es
esta, y lo que sobra se descarta (el «Educativo» diario no figura en la
tabla: se publica los días que haya tiempo).

| Día | Feed | Stories | Tiempo |
|---|---|---|---|
| Lunes | **Cómo se movió** | Recuerda la pieza del día | 20 min + 30 min de revisión semanal |
| Martes | Dato del día | — | 10 min |
| Miércoles | **Carrusel** | Encuesta: «¿Qué producto miramos el lunes?» | 30 min |
| Jueves | **De dónde sale este precio** | — | 15 min (incluye revisar privacidad) |
| Viernes | Dato del día | — | 10 min |
| Sábado | — (o Dato del día si la semana fue floja) | Respuestas a las preguntas de la semana | 10 min |
| Domingo | Descanso | **Story de precalentamiento** del lunes | 5 min |

Son **5 publicaciones de feed por semana**. Buffer, sobre unos 2 millones de
publicaciones, encontró que 3 a 5 por semana es el punto que da alcance y
crecimiento sin quemarse; publicar más ayuda, pero cada vez menos
([Buffer](https://buffer.com/resources/how-often-to-post-on-instagram/),
agosto de 2025; es un estudio de correlación y Buffer vende software para
programar publicaciones). Si una semana no llegas a 5, baja a 3. Lo que más
pesa es no dejar una semana entera vacía.

**Hora de publicar: no verificada para Cuba.** Los estudios de «mejor hora»
son de audiencias de otros países. Empieza a las 8:00 de La Habana (el lote
termina a las 06:00) y prueba la noche como el cambio de alguna semana.

---

## 6. Piezas que hay que preparar una sola vez

| Pieza | Para qué | Prioridad |
|---|---|---|
| Perfil de Instagram: nombre «Guani», avatar de la marca, **cuenta profesional** | Que se entienda qué es en 3 segundos y ver las estadísticas | Imprescindible |
| Biografía (150 caracteres como máximo): «Medimos el mercado con la misma vara. Lo que se anuncia en Cuba, provincia por provincia. Cada precio con su cuenta.» | La vara, de `docs/narrativa-guani.md` | Imprescindible |
| Enlace en la biografía a `/precios` | Paso «visitar» | **Solo cuando se apague la puerta** |
| El video de la landing y «De los grupos a la cifra», fijados arriba del perfil | Explicar a quien llega | Imprescindible |
| Tres respuestas guardadas en el teléfono | Contestar rápido: «¿de dónde sacan el precio?», «¿por qué no está mi provincia?», «el precio está mal» | Recomendable |
| Campo de Instagram en el pie del sitio | Hoy el pie enlaza Facebook, WhatsApp y Telegram desde configuración; **no tiene Instagram**. | Recomendable, **fuera de alcance de esta guía** (es un slice de código) |

---

## 7. Métricas

### Dónde se miran

En la **app de Instagram**, con la cuenta profesional: Estadísticas de la
cuenta y de cada publicación. Guani no lee métricas de Instagram desde el
sistema: la API de Meta no está disponible desde Cuba, y la sección
«Instagram» de `/marketing` se sacó. Cargar las cifras a mano en el sistema
es la tarea F5 de `docs/plan-fabrica-marketing.md`, opcional y sin hacer.
Mientras tanto, la planilla de abajo alcanza.

| Métrica | Qué significa | Paso del embudo | Cómo leerla |
|---|---|---|---|
| **Seguidores** | Cuánta gente eligió volver | Volver | Importa la tendencia semanal, no el día. |
| **Alcance** (cuentas alcanzadas) | Cuentas distintas que vieron la publicación | Descubrir | Es el denominador de casi todo lo demás. |
| **Visualizaciones** | Veces que se vio (una persona puede contar varias) | Descubrir | Mucho mayores que el alcance: la gente vuelve a mirarla. |
| **Compartidos** | Veces que alguien la mandó por mensaje o a su Story | Descubrir | **La métrica más importante** para crecer con gente que no te sigue. |
| **Guardados** | Veces que alguien la guardó para después | Volver | Alto en el carrusel «cómo leer»: va bien. |
| **Me gusta / comentarios** | Reacción de quien ya te sigue | Volver | Los comentarios, además, son la conversación de la comunidad. |
| **Tiempo de reproducción** | Solo Reels | — | Aplica al video de la landing. |

Los nombres exactos de cada métrica en la app pueden cambiar con las
versiones de Instagram: no verificado contra la versión que tengas instalada.

**Las dos cuentas que tienes que hacer tú.** La app da números sueltos.
Divide por el alcance para poder comparar una publicación con otra:

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
diarios» de `spec.md` §9. Con la puerta encendida solo cuenta a quien tiene
la contraseña: la línea de base empieza el día que se apague.

### Lo que no se mide

| Pregunta | Por qué no | Qué hacer mientras tanto |
|---|---|---|
| ¿Cuántas visitas vinieron de Instagram? | El log no guarda de dónde viene la visita ni hay parámetros UTM. | Mira si las visitas suben el día que publicas. Es una pista, no una prueba. |
| ¿Cuánta gente vuelve al sitio? | El identificador cambia cada día a propósito (privacidad). | Usa los seguidores de Instagram como señal de «volver». |
| ¿Cuántas personas hay detrás de una IP? | Muchas conexiones pueden compartir IP; cuánto pasa en Cuba: no verificado. | Toma el número como piso, no como cifra exacta. |

Medir el origen de las visitas sería un slice de código aparte, con su propia
decisión de privacidad. **Fuera de alcance de esta guía.**

### Planilla semanal

Una fila por semana, los lunes. Una hoja de cálculo alcanza.

| Semana | Publicaciones | Seguidores (lunes) | Alcance medio | Compartidos ÷ alcance (medio) | Guardados ÷ alcance (medio) | Visitantes únicos (media diaria) | Qué cambié |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | — (línea de base) |
| 2 | | | | | | | |

**Regla de oro: un cambio por semana.** Es la regla 3.

---

## 8. Comunidad

Comunidad no es tener seguidores: es que la gente te hable y se hable entre
sí sobre lo que publicas.

1. **Contesta todo el mismo día.** Cada comentario y cada mensaje, aunque sea
   con una de las respuestas guardadas.
2. **Pregunta solo lo que puedas cumplir.** La encuesta del miércoles («¿qué
   producto miramos el lunes?») ofrece solo los productos que Guani ya cubre.
   No prometas productos ni provincias nuevos.
3. **Cuando alguien dice «el precio está mal».** Agradécelo y pide, por mensaje
   privado, el producto, la provincia y el precio que vio. **Nunca** pidas ni
   guardes teléfonos, nombres ni capturas con datos de contacto (`spec.md`
   §10.6). Si tiene razón, corrígelo en el sitio y cuéntalo en una Story: un
   error reconocido en público suma confianza. Nunca borres una publicación en
   silencio.
4. **Muestra a la comunidad sin exponerla.** Puedes decir «Esta semana nos
   preguntaron por el pollo» sin nombrar a nadie. No publiques capturas de
   comentarios o mensajes con el nombre o la foto de la persona.
5. **Facilita el reenvío a WhatsApp.** Es donde se compra y se vende. Cada
   «Dato del día» tiene que entenderse solo, porque llega reenviado sin el
   texto de la publicación.
6. **A quién seguir.** Pocas cuentas que importan, no cientos: emprendedores y
   negocios de La Habana y Santiago, grupos de venta, medios de economía
   cubana. Comenta cuando tengas algo útil que decir, con la voz de Guani. No
   sigas y dejes de seguir en masa: atrae seguidores a los que no les importan
   los precios y puede activar bloqueos temporales de la cuenta (Instagram no
   publica el límite: no verificado).
7. **No uses el número de WhatsApp de la ingesta** para la marca: es el que
   usa WAHA para leer grupos (`docs/plan-marca-leira-studio.md`, paso 2).
8. **Sin peleas.** Si alguien discute de política, responde una vez con el
   dato y la metodología (`/metodologia`) y no sigas.

---

## 9. Plan de 30 días

**Requisito previo:** el lote `saimc-marketing.timer` corriendo (crea los
borradores a las 06:00) y acceso a `/marketing`. La publicación es siempre a
mano.

### Semana 1 — Preparar y medir la línea de base

| Día | Tarea | Listo cuando |
|---|---|---|
| 1 | Perfil con la marca (sin nombre personal), cuenta profesional, biografía de la vara y avatar | El perfil se entiende sin leer nada más |
| 1 | Publicar el video de la landing como Reel y fijarlo | Aparece arriba del perfil |
| 2 | Primer «Dato del día» | Publicado con texto y pedido de envío |
| 3 | «De los grupos a la cifra» (o el carrusel, si es miércoles) y fijarla | Fijada junto al video |
| 4 | «De dónde sale este precio», revisada a ojo por privacidad antes de aprobarla | Sin teléfonos, nombres, grupos ni canales a la vista |
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

- **Si la puerta del sitio ya se apagó:** pon `/precios` en la biografía y
  suma «enlace en la biografía» al cierre del carrusel y de «De dónde sale».
  Cuenta los visitantes únicos cada día de esta semana.
- **Si sigue encendida:** sigue sin enlace. El plan no se frena; el paso
  «visitar» espera.
- Publica el «Cómo se movió» del producto que ganó la encuesta.

### Semana 4 — Mirar y decidir

- Lunes de la semana 4: con tres filas en la planilla, mira cuál fue la pieza
  con mejor compartidos ÷ alcance y cuál la peor.
- Haz más de la mejor y menos de la peor.
- Si quieres probar el pie de las tarjetas que propone la narrativa (*Cada
  precio con su cuenta* contra *Medido con la misma vara*), es el cambio de
  una semana cada uno.
- **Día 30:** escribe en dos líneas qué funcionó y qué no, y fija las metas
  del mes 2 a partir de las cifras reales, no antes.

### Al día 30 tienes que poder contestar

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

Las herramientas de terceros para programar publicaciones (Metricool y
similares) también pasan por Meta y piden pago con tarjeta internacional: se
descartaron el 2026-10-18 junto con la API.

---

## 11. Riesgos

| Riesgo | Señal | Qué hacer |
|---|---|---|
| **La puerta del sitio sigue encendida** | Nadie puede entrar desde Instagram | No poner el enlace; las tarjetas dan la respuesta solas. |
| **Un dato sale con datos personales** | Un teléfono o un nombre en «De dónde sale» | No publicar. Borrar si ya salió, avisar en Story sin repetir el dato y corregir el código. |
| **Un precio publicado estaba mal** | Un comentario lo señala | Agradecer, corregir y contarlo. Nunca borrar en silencio. |
| **No hay datos para la tarjeta** | El lote no crea esa pieza en «Hoy» | Publicar otra pieza de la semana; nunca una tarjeta vacía ni con cifras de ejemplo sin rótulo. |
| **Una cifra escrita a mano** | Un número en una tarjeta «Libre» o en el texto del post | Sacarlo del sitio ese día, con su fecha. Nunca de memoria. |
| **Te quemas** | Dos semanas sin llegar a 3 publicaciones | Bajar a 3 por semana (lunes, miércoles, jueves). Constancia antes que volumen. |
| **Instagram limita la cuenta** | Alcance que cae de golpe sin cambio de contenido, o avisos de acción bloqueada | Revisar el «Estado de la cuenta» en la app y bajar el ritmo de seguir cuentas. Que una cuenta operada desde Cuba tenga restricciones propias: no verificado. |

---

## 12. Próximos pasos

1. **Operador:** dejar el perfil como cuenta profesional, con la biografía de
   la vara, y fijar el video de la landing.
2. **Operador:** confirmar que `saimc-marketing.timer` está corriendo en el
   VPS (`systemctl list-timers saimc-marketing.timer`).
3. **Operador:** apagar la puerta del sitio al cerrar la Fase 0 (T1 y
   `revalidate --execute`). Este plan asume que pasa durante el mes.
4. **Slices posibles, fuera de esta guía:** campo de Instagram en el pie del
   sitio; métricas cargadas a mano (F5); medir el origen de las visitas.

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
`docs/narrativa-guani.md`; `docs/plan-bloque-mvp.md` (T15);
`docs/plan-fabrica-marketing.md`; `docs/plan-marca-leira-studio.md`;
`docs/concepto-comercial-guani.md`; `src/saimc/marketing/`;
`src/saimc/web/analytics.py`; `docs/decisions.md` (2026-10-05, 2026-10-16 y
las entradas del 2026-10-18 sobre la API de Meta, la fábrica F1–F4, la
tarjeta «Libre», los textos de las tarjetas y el cierre de la Fase 0).
