# Presentación de Guani para instituciones

Guion de una presentación de dieciséis slides para mandar por correo a
instituciones (academia, ONG, prensa, organismos) y dar a conocer Guani: la
idea de fondo, el flujo de la ingesta, la medida y la estrategia. Tiene que
hacer dos cosas con solo leerla, sin nadie que la explique: que quien la lea
entienda qué es el sistema y que le den ganas de verlo. Por eso cada título es
una oración completa, abre con una pregunta que se responde con una cifra real
(slides 3 y 9) y cierra con tres enlaces para verlo en dos minutos (slide 15). Se arma en Gamma pegando el bloque de «Texto para pegar»
entero, instrucciones incluidas, y se manda como PDF.

La voz sale de `docs/narrativa-guani.md`: la idea madre en su versión neutra
(«parece caótico, pero tiene orden», que es la que la narrativa pide con
aliados), la vara como eslogan, las cuentas claras como prueba y «tú decides»
como cierre. La firma y el remitente salen de `docs/plan-marca-leira-studio.md`.
Escrito el 2026-10-18.

## Antes de enviarla

- [ ] **La puerta del sitio.** Con `SAIMC_SITE_GATE_ENABLED` encendida, quien
      abra el enlace cae en `/acceso` y no ve nada, y la slide 15 entera vive
      de esos enlaces. Apagarla antes del envío o darle acceso a cada
      institución; sin eso, la presentación no se manda.
- [ ] **Las cifras del día.** Las de las slides 7, 9 y 11 son del 2026-10-18.
      Se actualizan con lo que muestra el sitio el día del envío; nunca se
      dicen de memoria.
- [ ] **El correo de la marca.** Se envía desde el correo de Leira Studio, no
      desde el personal, y el remitente visible dice «Leira Studio». Reemplazar
      `[correo de la marca]` en la slide 16.
- [ ] **El enlace.** Confirmar que `guani.duckdns.org` es el dominio público
      vigente.
- [ ] **Lo que hizo Gamma.** Que no haya reescrito el texto ni cambiado una
      cifra, y que el bloque de instrucciones no haya quedado como tarjeta.
- [ ] **El peso del PDF.** Menos de 5 MB, para que llegue con una conexión
      lenta.

## Reglas de la presentación

- **Firma:** «Guani, por Leira Studio». Ningún nombre de persona.
- **Registro:** sobrio. «Mercado privado y no estatal», no «informal» ni
  «mercado negro». «Relajo» no, con una institución va la versión neutra.
- **El orden es del mercado, nunca de Guani:** no se dice «poner orden»,
  «ordenar el mercado» ni «ordenamiento».
- **No se dice:** «fiable», «en tiempo real», un porcentaje de precisión (H0
  todavía no está medido), «lo que se paga» (un anuncio dice lo que se pide),
  comparaciones con precios oficiales, predicciones ni ofertas para empresas.
- **Revisión:** no se dice «una persona revisa cada precio». Lo difícil lo
  decide una persona; lo claro lo acepta el sistema y una persona lo audita
  después (`/metodologia`).
- **Usos:** la diáspora y «contado por su gente» quedan fuera, como en la
  narrativa (sin validar, y prometen una participación que no existe).

## De dónde salen las cifras (2026-10-18)

| Cifra | Valor | Fuente |
| --- | --- | --- |
| Mensajes recibidos | 6.899 (6.760 de WhatsApp, 139 capturas y textos del operador) | `raw_ingestions` por `source_type` |
| Canales y grupos | 48 | `raw_ingestions.channel_id` distintos |
| Apartados por no ser un anuncio de precio | 5.637 (82 %) | `processing_status = discarded` |
| Anuncios aceptados | 722 | `processing_status = curated` |
| Esperando revisión | 540 | `processing_status = pending` |
| Texto / imagen | 4.624 / 2.275 | `content_type` |
| Precios publicados | 604 en 30 días, 992 en total | las observaciones visibles de la landing |
| Pollo en Santiago de Cuba | 34 anuncios, 705–850 CUP/lb, mediana 810 | la misma ventana de 30 días |

## Texto para pegar

Copiar desde «INSTRUCCIONES PARA GAMMA» hasta el final de la slide 16 y
pegarlo en Gamma con **Crear → Pegar texto**.

```
INSTRUCCIONES PARA GAMMA (no las pongas en las slides)
- Haz exactamente 16 tarjetas, una por cada bloque separado por "---".
- Conserva el texto tal cual: no lo reescribas, no lo resumas, no agregues frases ni cifras. Idioma: español.
- Formato horizontal 16:9, pensado para leerse solo como PDF, sin presentador.
- Estilo: sobrio, institucional y cálido. Mucho espacio en blanco. Tipografía sans serif limpia.
- Colores: fondo azul noche #0b2c43 en la portada, en la tarjeta 2, en la 15 y en el cierre; el resto, claras. Acentos: índigo #2f3a80, mamey #c8507a, naranja #f08a3c y amarillo guayaba #ffd072 para las cifras destacadas.
- Las tarjetas 6 y 7 son el flujo: dibuja la 6 como un diagrama de siete pasos en línea, de izquierda a derecha, y la 7 como un embudo con sus cifras.
- Imágenes: ilustraciones planas y abstractas de un atardecer cubano (palmas, siluetas de edificios, sol amarillo), sin personas, sin fotos de comida, sin banderas ni símbolos políticos. En las tarjetas 9 y 11, las cifras en grande como protagonistas, sin imagen.
- La tarjeta 15 es una invitación: los tres enlaces grandes y visibles, como botones.
- No inventes logos. Pie en todas las tarjetas: "Guani, por Leira Studio".

---

# Guani

**Medimos el mercado con la misma vara.**

Una referencia de precios hecha con los anuncios de quienes venden, que cualquiera puede comprobar.

Precios del mercado privado y no estatal cubano, producto por producto y provincia por provincia.

Guani, por Leira Studio · guani.duckdns.org

---

# El mercado cubano parece caótico, pero tiene orden

- Hay precios que se repiten, diferencias entre provincias que se sostienen y cambios que se pueden seguir semana a semana.
- Ese orden está disperso en miles de anuncios, y nadie lo ve completo.
- Guani no le impone orden al mercado: lo hace visible.
- Informal no quiere decir desordenado. El orden es de quien vende y de quien compra.

---

# ¿Cuánto cuesta hoy una libra de pollo en Santiago de Cuba?

Hasta ahora, nadie podía responderlo con una cifra que diga de dónde sale.

- Quien compra pregunta en varios grupos antes de saber si un precio es lo normal.
- Quien vende pone precio a ojo, mirando a la competencia uno por uno.
- Quien investiga o informa encuentra cifras sueltas, sin fuente ni método.

Guani lo responde con los anuncios a la vista. La respuesta está más adelante.

---

# Los precios los pone quien vende. Guani solo los junta.

- Emprendedores, TCP y negocios privados publican cada día sus anuncios en grupos y canales de WhatsApp.
- Guani los lee, los mide con la misma regla y los devuelve como una referencia común.
- No guarda teléfonos, nombres ni datos de contacto, y no publica quién puso el anuncio. Enseña el precio, no a la persona.

---

# Guani convierte esos anuncios en una referencia que cualquiera puede revisar

- Lleva todo a una misma unidad y a pesos cubanos.
- Publica el rango y la mediana de cada producto en cada provincia.
- Cada cifra dice cuántos anuncios tiene detrás.
- Es gratis y de acceso libre.

---

# De un mensaje de WhatsApp a una cifra: siete pasos

1. **Llega.** Los mensajes de 48 canales y grupos de venta entran solos, sin que nadie tenga que escribirle a Guani. Se suman las capturas que se guardan al usar las redes.
2. **Se guarda tal cual.** El mensaje original se guarda una vez y no se modifica ni se borra. Toda cifra conserva el enlace a él.
3. **Se lee.** Cada pocas horas, un asistente lee lo nuevo, texto e imágenes, y saca producto, precio, cantidad, moneda y provincia.
4. **Se aparta lo que no es un anuncio.** Saludos, avisos y ofertas sin precio quedan fuera y no generan ninguna cifra.
5. **Se acepta lo claro.** Un anuncio sin dudas entra solo, y una persona audita después lo que el sistema aceptó.
6. **Una persona decide lo dudoso.** En la cola de revisión, anuncio por anuncio, y corrige antes de guardar.
7. **Se publica con su cuenta.** Rango, mediana y cuántos anuncios hay detrás.

---

# La mayor parte de lo que llega es ruido, y por eso el filtro importa

- **6.899 mensajes** recibidos.
- **8 de cada 10** no eran un anuncio de precio y se apartaron.
- **722 anuncios** aceptados, que dan **992 precios** publicados.
- **540** esperan revisión.
- 2 de cada 3 mensajes son texto; 1 de cada 3, imagen.

Cifras al 18 de octubre de 2026.

---

# La vara: las mismas reglas para todos los precios

- **La misma unidad.** El litro y los 900 ml, la libra y el kilo: todo se lleva a la misma unidad antes de sacar la cuenta.
- **La misma moneda.** Lo anunciado en USD, EUR o MLC se pasa a CUP con la tasa informal de elTOQUE. Lo que no se puede convertir no se muestra.
- **El precio del medio.** Se resume con la mediana, que casi no se mueve por un anuncio fuera de lo común. La regla que señala esos precios está publicada.
- **Poco y cierto.** Con pocos anuncios, la cifra se rotula «muestra en formación». Una provincia se publica cuando acumula anuncios suficientes y sostenidos, no por calendario.

---

# La respuesta: así se ve el orden del pollo en Santiago de Cuba

- **34 anuncios** en los últimos 30 días.
- **De 705 a 850 CUP la libra.**
- **Mediana: 810 CUP la libra.**
- Ese rango no lo puso Guani: es el que el mercado ya tenía.

Cifras al 18 de octubre de 2026.

---

# Cada precio con su cuenta: todo se puede comprobar

- Cada cifra dice cuántos anuncios tiene detrás, y los de texto se pueden abrir y leer.
- Dice cuántos revisó una persona y cuántos aceptó el sistema.
- La metodología es pública: guani.duckdns.org/metodologia
- Los datos se descargan en CSV, un anuncio por fila.
- El anuncio original no se modifica nunca.

---

# Guani ya mide cinco productos en dos provincias

- **604 precios** en los últimos 30 días; **992** desde el inicio.
- **5 productos:** arroz, azúcar blanca, pollo, aceite y picadillo.
- **2 provincias:** La Habana y Santiago de Cuba.

Cifras al 18 de octubre de 2026.

---

# Guani enseña el precio y no opina sobre él

- No dice si un precio es caro o barato ni da consejos de compra.
- No compara con precios oficiales.
- No habla de culpables ni de inflación.
- La neutralidad no es solo prudencia: es lo que hace creíble la vara.

---

# Guani le habla a todo el pueblo y no decide para qué se usa

- **Quien compra:** ¿cuánto cuesta esto hoy en mi provincia?
- **Quien vende:** ¿dónde cae mi precio frente al de los demás?
- **Quien revende o abastece un negocio:** ¿qué se mueve y dónde?
- **Quien investiga o informa:** ¿de dónde sale este número?

Llega por un sitio gratis, por tarjetas para redes con el precio del día y cómo se movió en la semana, y por datos abiertos en CSV.

---

# Lo que viene

- **Medir qué tan bien lee la máquina sin ayuda**, contra 300 anuncios revisados a mano. Esa cifra dice cuánto puede crecer Guani sin crecer el trabajo manual.
- **Más productos, más provincias** e historial semana a semana.
- **Devolverle algo a los grupos:** un resumen semanal de cómo se movieron sus precios, para quien administra un grupo de ventas.

---

# No te pedimos que nos creas: míralo tú

Tres cosas para ver en dos minutos:

- **Los precios de hoy**, provincia por provincia: guani.duckdns.org/precios
- **Un precio con sus anuncios detrás:** guani.duckdns.org/producto/pollo
- **Cómo se calcula cada cifra:** guani.duckdns.org/metodologia

Gratis, sin registrarse.

---

# Guani enseña el precio. Tú decides qué hacer con él.

Formas de colaborar: difundirlo, sugerir grupos y canales de anuncios, o una alianza para investigación.

[correo de la marca] · guani.duckdns.org

Guani, por Leira Studio
```

## Imágenes por slide (opcional)

Si se quieren capturas reales en lugar de las ilustraciones de Gamma:

| Slide | Imagen | De dónde sale |
| --- | --- | --- |
| 1 | Avatar de Guani sobre el cielo de la portada | `src/saimc/web/static/seo/og-guani.png` o captura de `/` |
| 6 | El pase de un anuncio real, paso a paso | `src/saimc/web/static/landing/flujo-1.webp` a `flujo-5.webp` |
| 9 | La cifra del pollo en Santiago de Cuba | Captura de `/producto/pollo` con Santiago de Cuba elegida |
| 10 | Un anuncio de texto abierto desde su precio | Captura de `/anuncio/<id>` |
| 11 | El mapa con las provincias publicadas | Captura de `/precios?vista=mapa` |

## Correo que acompaña la presentación

Registro formal, porque va a una institución. El párrafo del medio se adapta a
lo que hace cada una.

> **Asunto:** Guani: una referencia de precios del mercado privado cubano, por provincia
>
> Estimados [nombre de la institución]:
>
> Les escribimos desde Leira Studio para presentarles Guani, un sitio gratuito
> que convierte los anuncios de emprendedores, TCP y negocios privados en una
> referencia de precios por producto y por provincia. Cada cifra dice cuántos
> anuncios tiene detrás, y la metodología es pública.
>
> [Por qué puede servirle a esta institución: investigación, seguimiento de
> precios, información al público.]
>
> Adjuntamos una presentación breve. Hoy cubre [5] productos en [2]
> provincias, y pueden consultarlo en guani.duckdns.org. Con gusto les
> contamos más o respondemos cualquier pregunta.
>
> Saludos,
> Leira Studio
