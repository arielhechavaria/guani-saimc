# Presentación de Guani para instituciones

Guion de una presentación de diez slides para mandar por correo a
instituciones (academia, ONG, prensa, organismos) y dar a conocer Guani. Se
lee sola, sin nadie que la explique: cada título es una oración completa y
cada slide dice una sola cosa. Se arma en Gamma (o Presenton) pegando la
sección «Texto para pegar» y se manda como PDF.

La voz sale de `docs/narrativa-guani.md`; la firma y el remitente, de
`docs/plan-marca-leira-studio.md`. Escrito el 2026-10-18.

## Antes de enviarla

- [ ] **La puerta del sitio.** Con `SAIMC_SITE_GATE_ENABLED` encendida, quien
      abra el enlace cae en `/acceso` y no ve nada. Apagarla antes del envío,
      darle acceso a cada institución o quitar los enlaces de las slides 1, 7
      y 10.
- [ ] **Las cifras del día.** Las de las slides 6 y 8 son del 2026-10-18.
      Se actualizan con lo que muestra el sitio el día del envío (el mapa de
      `/precios` y la página del producto); nunca se dicen de memoria.
- [ ] **El correo de la marca.** Se envía desde el correo de Leira Studio, no
      desde el personal, y el remitente visible dice «Leira Studio». Reemplazar
      `[correo de la marca]` en la slide 10.
- [ ] **El enlace.** Confirmar que `guani.duckdns.org` es el dominio público
      vigente.
- [ ] **El peso del PDF.** Menos de 5 MB, para que llegue con una conexión
      lenta. Sin video.

## Reglas de la presentación

- **Firma:** «Guani, por Leira Studio». Ningún nombre de persona.
- **Registro:** impersonal y sobrio. A una institución se le dice «mercado
  privado y no estatal», no «informal», y nunca «mercado negro».
- **No se dice:** «fiable», «en tiempo real», «poner orden», un porcentaje de
  precisión (H0 todavía no está medido), «lo que se paga» (un anuncio dice lo
  que se pide) ni comparaciones con precios oficiales.
- **Revisión:** no se dice «una persona revisa cada precio». Lo difícil lo
  decide una persona; lo claro lo acepta el sistema y una persona lo audita
  después (`/metodologia`).

## Imágenes por slide

| Slide | Imagen | De dónde sale |
| --- | --- | --- |
| 1 | Avatar de Guani sobre el cielo de la portada | `static/seo/og-guani.png` o captura de `/` |
| 5 | El pase de un anuncio real, paso a paso | `static/landing/flujo-1.webp` a `flujo-5.webp` |
| 6 | La cifra del pollo en Santiago de Cuba | Captura de `/producto/pollo` con Santiago de Cuba elegida |
| 7 | Un anuncio de texto abierto desde su precio | Captura de `/anuncio/<id>` |
| 8 | El mapa con las provincias publicadas | Captura de `/precios?vista=mapa` |

Las capturas se toman a 390 px de ancho si la slide es vertical, o a 1280 px
si es horizontal. Las imágenes de `static/` están en
`src/saimc/web/static/`.

## Texto para pegar

En Gamma: crear desde texto y pegar desde aquí hasta el final de la slide 10.
Cada `---` es una slide.

---

# Guani

**Medimos el mercado con la misma vara.**

Precios del mercado privado y no estatal cubano, producto por producto y
provincia por provincia.

Guani, por Leira Studio · guani.duckdns.org

---

# Guani convierte los anuncios de quienes venden en una referencia de precios que cualquiera puede revisar

- Lee lo que emprendedores, TCP y negocios privados publican en grupos y canales de WhatsApp.
- Lo lleva todo a una misma unidad y a pesos cubanos.
- Publica el rango y la mediana de cada producto en cada provincia, con los anuncios detrás.
- Es gratis y de acceso libre.

---

# En el mercado privado cubano no hay una referencia de precios común

- Quien compra pregunta en varios grupos antes de saber si un precio es lo normal.
- Quien vende pone precio a ojo, mirando a la competencia uno por uno.
- Quien investiga o informa encuentra cifras sueltas, sin fuente ni método.

---

# Los precios ya están publicados: falta juntarlos y medirlos igual

- Cada día se publican miles de anuncios con producto, precio y lugar.
- Están dispersos en muchos grupos, en unidades y monedas distintas.
- Medirlos con la misma regla los convierte en una referencia común.

---

# Guani trabaja en cuatro pasos

1. **Escucha.** Lee los grupos y canales donde ya se vende. Nadie tiene que escribirle nada.
2. **Entiende.** Saca producto, precio, cantidad y provincia, y lo lleva todo a la misma unidad y moneda.
3. **Filtra.** Aparta lo que no es un anuncio de precio y frena lo dudoso. Lo difícil lo decide una persona; lo claro lo acepta el sistema y una persona lo audita después.
4. **Lo publica con su cuenta.** Rango, mediana y cuántos anuncios hay detrás.

---

# Así se lee una cifra: el pollo en Santiago de Cuba

- **De 705 a 850 CUP la libra.**
- **Mediana: 810 CUP la libra**, el precio del medio.
- **34 anuncios** en los últimos 30 días, cada uno con su fecha.
- Un precio fuera de lo común no mueve la mediana, y la regla que lo señala está publicada.

Cifras al 18 de octubre de 2026.

---

# Cada cifra de Guani se puede comprobar

- Dice cuántos anuncios tiene detrás, y los de texto se pueden abrir y leer.
- Dice cuántos revisó una persona y cuántos aceptó el sistema.
- La metodología es pública: guani.duckdns.org/metodologia
- Los datos se descargan en CSV, un anuncio por fila.
- No guarda teléfonos, nombres ni datos de contacto de quien vende, y no publica quién puso el anuncio.

---

# Guani ya mide cinco productos en dos provincias

- **604 anuncios** en los últimos 30 días; **992** desde el inicio.
- **5 productos:** arroz, azúcar blanca, pollo, aceite y picadillo.
- **2 provincias:** La Habana y Santiago de Cuba.
- Una provincia se publica cuando acumula anuncios suficientes y sostenidos, no por calendario.

Cifras al 18 de octubre de 2026.

---

# Guani enseña el precio y no opina sobre él

- No dice si un precio es caro o barato ni da consejos de compra.
- No compara con precios oficiales.
- No predice precios.
- No da una cifra sin decir cuántos anuncios la sostienen.

---

# Lo que viene y cómo colaborar

- **Lo próximo:** medir qué tan bien lee la máquina sin ayuda, contra 300 anuncios revisados a mano. Esa cifra dice cuánto puede crecer Guani sin crecer el trabajo manual.
- **Después:** más productos, más provincias e historial semana a semana.
- **Formas de colaborar:** difundirlo, sugerir grupos y canales de anuncios, o una alianza para investigación.

[correo de la marca] · guani.duckdns.org

Guani, por Leira Studio

---

## Correo que acompaña la presentación

Registro formal, porque va a una institución. Se adapta el párrafo del medio
a lo que hace cada una.

> **Asunto:** Guani: una referencia de precios del mercado privado cubano, por provincia
>
> Estimados [nombre de la institución]:
>
> Les escribimos desde Leira Studio para presentarles Guani, un sitio gratuito que
> convierte los anuncios de emprendedores, TCP y negocios privados en una
> referencia de precios por producto y por provincia. Cada cifra dice cuántos
> anuncios tiene detrás, y la metodología es pública.
>
> [Por qué puede servirle a esta institución: investigación, seguimiento de
> precios, información al público.]
>
> Adjunto una presentación breve. Hoy cubre [5] productos en [2] provincias, y
> pueden consultarlo en guani.duckdns.org. Con gusto les contamos más o
> respondemos cualquier pregunta.
>
> Saludos,
> Leira Studio
