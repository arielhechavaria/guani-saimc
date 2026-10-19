# Presentación de Guani para instituciones

Presentación de dieciséis slides para mandar por correo a instituciones de
La Habana, que son las de la primera fase, y dar a conocer Guani: la idea de
fondo, el flujo de la ingesta, la medida y la estrategia. Tiene que hacer dos
cosas con solo leerla, sin nadie que la explique: que quien la lea entienda qué
es el sistema y que le den ganas de verlo. Por eso cada título es una oración
completa, abre con una pregunta que se responde con una cifra real (slides 3
y 9, el arroz en La Habana) y cierra con tres enlaces para verlo en dos minutos
(slide 15). Está armada como presentación de Claude y se exporta como PDF
(ver «La presentación», al final).

La voz sale de `docs/narrativa-guani.md`: la idea madre en su versión neutra
(«parece caótico, pero tiene orden», que es la que la narrativa pide con
aliados), la vara como eslogan, las cuentas claras como prueba y «cada quien
decide» como cierre. La firma y el remitente salen de `docs/plan-marca-leira-studio.md`.
Escrito el 2026-10-18.

## Antes de enviarla

**Bloqueante: no se manda antes de la Fase 1.** La presentación invita a ver el
sitio sin contraseña, y la puerta solo se apaga después del cierre de la
Fase 0 (`docs/decisions.md`, «2026-10-18 — Criterio de cierre de la Fase 0 y
de inicio de la Fase 1»). Antes de eso, abrirlo publicaría precios convertidos
con una tasa equivocada (las 266 filas en USD con `elTOQUE (cache)`).

- [ ] **T1 aplicado** y `revalidate --execute` corrido después.
- [ ] **La puerta apagada** (`SAIMC_SITE_GATE_ENABLED=false`). Dar acceso por
      contraseña a una institución sirve solo para un adelanto, y entonces
      «gratis, sin registrarse» no es cierto.
- [ ] **Las cifras, otra vez desde la base.** Las de las slides 7, 9 y 11 son
      del 2026-10-18 y anteriores a T1: la mediana del arroz y los 604
      precios pueden moverse. Nunca se dicen de memoria. Si la cifra cambia,
      se cambia también la captura de la slide 9.
- [ ] **Cuántos anuncios de la slide 9 revisó una persona.** Mirarlo en la
      página del arroz antes de enviar: si ninguno, cambiar de ejemplo o
      decirlo.
- [ ] **Los destinatarios, en La Habana y por nivel de exposición** (`docs/plan-marca-leira-studio.md`,
      «Orden de envío»): primero los de exposición baja, esperar una o dos
      semanas, después los de media. Los de alta, y cualquier organismo, solo
      después de la consulta con el abogado. Los medios de «Fuera de la lista»
      no reciben nada.
- [ ] **Qué se responde si piden un nombre o una llamada**
      (`docs/plan-marca-leira-studio.md`, «Riesgos»).
- [ ] **El correo de la marca.** Se envía desde el correo de Leira Studio, no
      desde el personal, y el remitente visible dice «Leira Studio». Reemplazar
      `[correo de la marca]` en la slide 16.
- [ ] **Los enlaces.** Abrir los tres de la slide 15 por `https://` desde fuera
      de la red propia (lo que está delante de uvicorn sigue sin constar en el
      repo, `docs/plan-bloque-mvp.md`, T10).
- [ ] **El peso del PDF.** Menos de 5 MB, para que llegue con una conexión
      lenta.

## Reglas de la presentación

- **Firma:** «Guani, por Leira Studio». Ningún nombre de persona.
- **Registro:** formal, sobrio y elegante, en español sin voseo ni tuteo:
  impersonal («No hace falta creerlo: basta con verlo», «Cada quien decide»).
  «Mercado privado y no estatal», no «informal» ni «mercado negro». «Relajo»
  no, con una institución va la versión neutra.
- **El orden es del mercado, nunca de Guani:** no se dice «poner orden»,
  «ordenar el mercado» ni «ordenamiento».
- **No se dice:** «fiable», «en tiempo real», «lo que se paga» (un anuncio dice
  lo que se pide), «datos abiertos» (el CSV trae 30 días y el uso comercial
  pide acuerdo), comparaciones con precios oficiales, predicciones ni ofertas
  para empresas.
- **Precisión:** H0 ya está medida (38,8 %, n = 220, no oficial; `docs/analisis-fase.md`
  §3.2), pero no gobierna nada desde el 2026-08-12 y no se publica. A un
  tercero se le da el error auditado, nunca uno estimado
  (`docs/plan-mvp-inversores.md`, «Qué quiere decir listo para inversores»).
  `docs/narrativa-guani.md` todavía dice «no hay número»: está desactualizada
  en eso.
- **Lo que no está construido** va como horizonte sin fecha, no como plan
  (`docs/plan-marca-leira-studio.md`, paso 3). Ni Instagram ni el resumen
  para administradores de grupos se nombran: el perfil no existe y el resumen
  no tiene tarea.
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
| Arroz en La Habana, paquete de 1 kg | 53 anuncios (3 fuera de escala), 297–408 CUP/lb, mediana 327 | `/producto/arroz?provincia=la-habana`, ventana de 30 días |

## La presentación

- **Dónde está:** https://claude.ai/artifact/5J4TUb41Gxvz8jqR1ZvxvD (privada;
  se exporta a PDF desde su menú).
- **Su fuente:** `docs/presentacion-instituciones/` (`deck.json`, una slide por
  archivo en `slides/` y las tres imágenes). Si cambia el texto, se cambia ahí
  y se vuelve a publicar.
- **También se publica en Instagram:** el turno «Educativo» diario muestra 14
  de estas láminas, tal cual y sin su contador de página, en tres secciones que
  rotan por día (`saimc.marketing.deck`): idea (`cover`, `orden`, `pregunta`,
  `quienvende`, `quees`), cómo funciona (`flujo`, `embudo`, `vara`,
  `respuesta`, `cuentas`) y a quién le habla (`noopina`, `publico`, `viene`,
  `miralo`). No se publican `hoy` (cifras fijas del 18 de octubre) ni `cierre`
  (marcador «[correo de la marca]» sin rellenar). Cambiar una lámina aquí
  cambia lo que se publica mañana, no lo ya aprobado.
- **Imágenes:** `anuncio.png` es la recreación de un anuncio de
  `static/landing/flujo-1.webp`; `arroz-habana.png` es una captura de las
  tarjetas de `/producto/arroz?provincia=la-habana` sin la cabecera (su rango
  general incluye los precios fuera de escala); `avatar.png` es
  `static/seo/apple-touch-icon.png`.

| # | Slide | Lo que dice |
| --- | --- | --- |
| 1 | Portada | Medimos el mercado con la misma vara. |
| 2 | La idea de fondo | El mercado cubano parece caótico, pero tiene orden. |
| 3 | La pregunta | ¿Cuánto se pide hoy por una libra de arroz en La Habana? |
| 4 | El origen de los precios | Los precios los fijan quienes venden; Guani los reúne. |
| 5 | Qué es Guani | Una referencia que cualquiera puede revisar. |
| 6 | Cómo funciona | Siete pasos, de un mensaje de WhatsApp a una cifra publicada. |
| 7 | El embudo | 8 de cada 10 mensajes se descartan; 722 anuncios, 992 precios. |
| 8 | La vara | Las mismas reglas para todos los precios. |
| 9 | La respuesta | Arroz en La Habana: 297–408 CUP/lb, mediana 327, 53 anuncios. |
| 10 | Cuentas claras | Cada precio con su cuenta: todo es verificable. |
| 11 | Dónde está | Cinco productos en dos provincias. |
| 12 | Neutralidad | Guani muestra el precio y no emite opinión sobre él. |
| 13 | A quién se dirige | A toda la sociedad, sin decidir el uso. |
| 14 | Próximos pasos | Crecer sin aumentar el trabajo manual. |
| 15 | Invitación | No hace falta creerlo: basta con verlo. |
| 16 | Cierre | Guani muestra el precio. Cada quien decide qué hacer con él. |

## Correo que acompaña la presentación

Registro formal, porque va a una institución. El párrafo del medio se adapta a
lo que hace cada una.

> **Asunto:** Guani: una referencia de precios del mercado privado cubano, por provincia
>
> Estimados [nombre de la institución]:
>
> Les escribimos desde Leira Studio, un estudio de desarrollo de software,
> para presentarles Guani, un sitio gratuito
> que convierte los anuncios de emprendedores, TCP y negocios privados en una
> referencia de precios por producto y por provincia. Cada cifra dice cuántos
> anuncios tiene detrás, y la metodología es pública.
>
> [Por qué puede servirle a esta institución: investigación, seguimiento de
> precios, información al público.]
>
> Adjuntamos una presentación breve. Hoy cubre [5] productos en [2]
> provincias, y pueden consultarlo en guani.app. Con gusto les
> contamos más o respondemos cualquier pregunta.
>
> Saludos,
> Leira Studio
