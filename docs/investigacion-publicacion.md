# Investigación: publicar en Instagram (y Facebook) desde Guani

Slice `investigar-publicacion-ig`, 2026-10-18. Solo lectura y documentación: no se
implementa nada. Responde la verificación que exigen la entrada del 2026-10-16
(«Se publica en Instagram por la API de Meta», punto 7) y la tarea T8b de
`docs/plan-bloque-mvp.md`.

Todas las cifras salen de las páginas oficiales leídas el 2026-10-18 (lista de
fuentes al final). Ninguna de las páginas de Meta muestra fecha de
actualización. Donde una página contradice a otra, se anota.

## Resumen

- **Para la cuenta propia no hace falta App Review ni verificación de negocio.**
  Con Instagram Login y Standard Access, Meta marca App Review «Not required» en
  el caso «My app is only for a business I own or manage». La verificación de
  negocio solo se exige para Advanced Access. El supuesto que motivó el descarte
  del 2026-09-28 (una revisión de la app de varias semanas) **no aplica** a
  Instagram con cuenta propia.
- La app de Meta que ya usa `saimc.instagram` (Instagram Login, graph.instagram.com,
  token largo renovable) sirve. Hay que sumar el permiso de publicación y volver a
  generar el token.
- **Las tarjetas actuales son PNG; Meta solo acepta JPEG.** Hay que convertirlas.
  El tamaño ya cumple: 1080×1350 (4:5) para el feed y 1080×1920 (9:16) para la story.
- **Facebook:** exige una página y un token de página. Una app de Meta puede usar
  Facebook Login o Instagram Login, **pero no los dos**, así que Facebook pediría
  una segunda app. App Review tampoco hace falta si solo la usan personas con un
  rol en la app, pero una página de Meta lo contradice (ver abajo).
- **Metricool:** sin código desde el plan Starter (desde 16 €/mes). Su API solo
  viene en el plan Advanced (desde 43 €/mes). Suma un tercero y un pago con
  tarjeta internacional.

**Recomendación:** publicar en Instagram con la API propia, como decidió el
2026-10-16, ahora sin el riesgo del punto 7. Facebook queda fuera hasta que
haya página y motivo. Metricool no conviene como camino principal; sirve como
plan B manual si la API falla en producción.

## 1. Instagram API with Instagram Login: permisos y acceso

| Punto | Lo que dice Meta | Fuente |
|---|---|---|
| Permisos | `instagram_business_basic` + `instagram_business_content_publish` | content-publishing, overview, instagram-api-with-instagram-login |
| Discrepancia de nombre | app-review lista `instagram_business_content_publishing` (con «-ing»); las otras tres páginas dicen `..._publish` | app-review |
| Tipo de cuenta | Cuenta profesional («business or creator») | overview, content-publishing |
| Página de Facebook | «This API setup does not require a Facebook Page to be linked to the Instagram professional account.» | instagram-api-with-instagram-login |
| Host y token | `graph.instagram.com`, Instagram User access token. El token corto dura 1 h; el largo, «60 days and can be refreshed before they expire» | overview |
| Flujo de login | Para una app que sirve solo a cuentas propias, «you do not need to implement a login flow», pero hay que configurar el Business Login en el App Dashboard para obtener el app ID, el secreto y tokens largos | overview |
| Nivel de acceso | Standard Access «is all your app needs» si la app solo sirve a tu cuenta profesional o a una que administrás | app-review |
| App Review | Escenario «My app is only for a business I own or manage» + Instagram Login + Standard Access → **Not required**. Un proveedor que sirve a varios negocios necesita Advanced Access, y ahí App Review es **Required** | app-review |
| Verificación de negocio | «You must complete Business Verification if your app requires Advanced Access; if your app will be used by app users who do not have a Role on the app…» | overview |
| Página vinculada con PPA | Si la cuenta está vinculada a una página que exige Page Publishing Authorization, la publicación queda bloqueada hasta completarla | content-publishing |

Lo que no está dicho en ninguna página leída: que la verificación de negocio
**no** haga falta con Standard Access. Se deduce de que solo se exige para
Advanced Access y para usuarios sin rol. Tampoco se encontró nada sobre el modo
Live ni sobre el rol de «Instagram tester».

Estado del repo: `src/saimc/instagram/client.py` ya usa graph.instagram.com y
renueva el token (`refresh_access_token`). La sincronización de métricas
funciona con la cuenta de Guani, así que la app ya tiene Standard Access sobre
esa cuenta.

## 2. Formatos y límites

**Imagen** (referencia `ig-user/media`, «Image Specifications»):

- Formato: **solo JPEG**. «Extended JPEG formats such as MPO and JPS are not
  supported» (content-publishing, «Limitations»).
- 8 MB como máximo; relación de aspecto entre 4:5 y 1.91:1; ancho de 320 a
  1440 px (se reescala); sRGB (los otros espacios de color se convierten).
- Story: JPEG, 8 MB, 9:16 recomendado. La referencia muestra el ejemplo en
  graph.facebook.com. **No está confirmado explícitamente** que la story de
  imagen funcione con Instagram Login. La guía de publicación lista
  `media_type=STORIES` sin distinguir el tipo de login.

**Texto** (`caption`): 2200 caracteres como máximo, 30 hashtags y 20 menciones.
Las piezas de un carrusel no llevan texto propio.

**Formatos admitidos:** imagen, video, Reels, stories y carruseles de hasta 10
elementos (las imágenes se recortan según la primera; 1:1 por defecto). No se
admiten etiquetas de compras ni filtros.

**Límites:**

- Publicaciones: la sección «Rate Limit» dice «100 API-published posts within a
  24-hour moving period»; un apartado de «Create a carousel container» dice
  «50 published posts within a 24-hour period». **Las dos cifras están en la
  misma página y se contradicen.** Para Guani da igual: son 1 o 2 por día. El
  uso real se consulta con `GET /<IG_ID>/content_publishing_limit`.
- Contenedores: 400 por cuenta en 24 h móviles; vencen a las 24 h (`EXPIRED`).
- Estado: `GET /<IG_CONTAINER_ID>?fields=status_code` devuelve `IN_PROGRESS`,
  `FINISHED`, `PUBLISHED`, `ERROR` o `EXPIRED`. Meta recomienda consultarlo una
  vez por minuto, durante 5 minutos como máximo.
- Medio: «the media must be hosted on a publicly accessible server at the time of
  the attempt». La URL tiene que ser ASCII.

**Flujo:** `POST /<IG_ID>/media` (`image_url`, `caption`) → contenedor →
`POST /<IG_ID>/media_publish` (`creation_id`).

**Qué implica para Guani:**

| Punto | Estado | Qué falta |
|---|---|---|
| Tamaño | Feed 1080×1350 (4:5, en el borde del rango) y story 1080×1920 | Nada |
| Formato | `render_png` produce PNG | Convertir a JPEG en sRGB, a menos de 8 MB |
| URL pública | `var/marketing/AAAA-MM-DD/` no se sirve | Servir el día corriente por HTTPS desde guani.duckdns.org (Caddy), con nombres ASCII |
| Token | Se renueva | Volver a generarlo con el permiso de publicación |
| Idempotencia | No existe | Guardar el `creation_id` y el id publicado antes de reintentar (sigue en pie el punto 4 de la entrada del 2026-10-16) |

## 3. Facebook Pages

- Permisos: `pages_manage_posts`, `pages_read_engagement` y `pages_show_list`
  (más `pages_manage_engagement`, `pages_read_user_engagement` y `publish_video`
  según la guía de posts). El token es un **Page access token**, y quien lo pide
  tiene que poder hacer las tareas `CREATE_CONTENT` (y `MANAGE`/`MODERATE` según la
  guía). Fuentes: pages-api/posts y graph-api/reference/page/photos.
- Hace falta una **página**. La API de Pages publica en páginas; ninguna página
  leída admite publicar en un perfil personal.
- Se usa **Facebook Login** (for Business). Como «Your app can either use Facebook
  Login or Instagram Login but not both» (app-review de Instagram), la app actual
  no sirve: hace falta otra.
- App Review: «If your app will only be used by app users who have a role on the
  app itself, App Review is not required» (docs/app-review), y «All Business,
  Consumer, and Gaming apps are automatically approved for Standard Access»
  (graph-api/access-levels). **Contradicción:** pages-api/overview
  dice «All Page-related Permissions and Features require approval through the
  App Review process before your app can use them when your app goes live». No
  se resuelve sin probarlo en el App Dashboard.
- Verificación de negocio: solo para Advanced Access (graph-api/access-levels).
- Fotos: `POST /{page-id}/photos` con `url`; admite JPEG, PNG, BMP, GIF y TIFF de
  hasta 10 MB. Para programar: `published=false` + `scheduled_publish_time`, entre
  10 minutos y 30 días adelante (guía de posts, para `/feed`).

Lo que corrige: la entrada del 2026-10-16 decía «Facebook queda como estaba
(exige página y revisión)». La página es cierta; la revisión, para uso propio,
probablemente no, aunque las páginas de Meta no coinciden. El costo nuevo que
no estaba anotado es la **segunda app**.

## 4. Metricool

| Plan | Precio (pricing, sin fecha) | Qué trae |
|---|---|---|
| Free | 0 € | 1 marca, 20 publicaciones/mes, 30 días de analítica, sin API |
| Starter | Desde 16 €/mes (5 marcas; 29 € por 10) | Publicación «ilimitada» con uso justo, **sin API** |
| Advanced | Desde 43 €/mes (15 marcas; 69 € por 25, 130 € por 50) | Aprobación de posts, roles, **API** (también vía Zapier y Make) |
| Custom | A consultar | 50 marcas o más, marca blanca |

- Las cifras de arriba son el juego más bajo de la página; también aparecen
  54 € / 87 € / 172 € para Advanced. La página no rotula cuál es anual: lo más
  probable es que el más bajo sea el anual («save up to 24%»), pero **no está
  confirmado**. En dólares, Advanced aparece como 53 $ o 67 $.
- «The Metricool API is available only on Advanced and Custom plans»
  (plans-add-ons-and-api-access-explained). Es parte del plan, no un agregado.
- API: header `X-Mc-Auth` + `userId` + `blogId`;
  `POST /api/v2/scheduler/posts` con `autoPublish`. El medio va por una URL
  **pública y que no venza** («private or temporary URLs are skipped»). Es la
  misma superficie pública que exige Meta: Metricool no la evita.
- Instagram: publica solo posts, carruseles (hasta 10), Reels y stories, con
  cuenta profesional. Las stories con enlaces o stickers se publican a mano. Hay
  una contradicción: una página dice que solo las cuentas Business publican
  stories solas; la otra dice «business or creator».
- Límites: Instagram, 50 publicaciones cada 24 h (la misma API de Meta).
  Facebook, 30 Reels. Uso justo: 600 publicaciones por marca y mes; al pasarlo,
  revisión manual, durante la cual no se puede publicar.
- Facebook en Metricool: la conexión por Facebook exige que la cuenta de
  Instagram esté **vinculada a una página**. La conexión con usuario y
  contraseña no la exige, pero Metricool la describe como «less stable».
- Datos: Metricool actúa como encargado del tratamiento según el art. 28 del
  RGPD (DPA a pedido por correo). No dice dónde guarda los datos ni por cuánto
  tiempo.
- Pago: tarjeta de crédito o débito habilitada para pagos internacionales, o
  PayPal; no acepta prepagas ni virtuales. Solo euros o dólares. **Ninguna página
  leída menciona Cuba**, y los términos de servicio no se leyeron.

## 5. Comparación

| Criterio | API propia (Instagram Login) | Metricool Starter (manual/UI) | Metricool Advanced (API) |
|---|---|---|---|
| Costo mensual | 0 | 16–29 € | 43–54 € |
| App Review / verificación | No (Standard Access, cuenta propia) | No le toca al operador | No le toca al operador |
| Automatizable desde el lote | Sí | No: se sube a mano y se programa | Sí |
| URL pública del medio | Hace falta | No (se sube el archivo) | Hace falta, y que no venza |
| Datos con un tercero | No | Sí | Sí |
| Facebook | Segunda app + página | Sí, con página vinculada | Sí, con página vinculada |
| Código nuevo | Publicación, JPEG, URL pública, tabla de estado, kill switch | Ninguno | Publicación, URL pública, estado |
| Reutiliza lo existente | Cliente, token y renovación ya están | — | No |

El único argumento fuerte de Metricool era evitar App Review. Con cuenta propia
eso no existe. Su API exige igual la URL pública y la tabla de estado, cuesta
el plan más caro y suma un tercero: la razón del descarte del 2026-10-16 sigue
en pie. Metricool Starter conserva valor como **plan B sin código** para el
día en que la API falle, no como camino principal.

## 6. Pendientes que solo se resuelven probando

1. El nombre exacto del permiso (`..._publish` contra `..._publishing`): se ve en el
   App Dashboard al agregarlo.
2. Si el permiso de publicación aparece con Standard Access sin pedir nada más.
   Lo dice la documentación, pero se confirma generando el token nuevo.
3. Si la story de imagen funciona con Instagram Login (la referencia solo muestra
   graph.facebook.com).
4. 50 o 100 publicaciones por día: irrelevante para el volumen de Guani. Se lee en
   `content_publishing_limit`.
5. Facebook: si App Review hace falta para una página propia (las dos páginas de
   Meta se contradicen).

La primera prueba de T8b es manual y no publica nada: regenerar el token con
el permiso y llamar a `content_publishing_limit`. Si responde, el acceso está
confirmado.

## 7. Entrada propuesta para `docs/decisions.md`

No se anexa en este slice: es el texto para que el operador lo apruebe. Corrige
los supuestos de la entrada del 2026-10-16, que a su vez reemplaza el punto (1)
del «Descartado» del 2026-09-28. No la reescribe.

```
## 2026-10-18 — Publicar en Instagram con la cuenta propia no exige App Review ni verificación de negocio (corrige los supuestos de la entrada del 2026-10-16, que reemplaza el punto (1) del «Descartado» del 2026-09-28)
Contexto: el 2026-09-28 se descartó publicar porque «la API de Meta pide revisión de la app (semanas)»; el 2026-10-16 se revocó ese descarte para Instagram y se dejó pendiente verificar (punto 7) si Meta exige Advanced Access o verificación del negocio, y se dio por sentado que Facebook exige página y revisión. La verificación contra la documentación vigente (`docs/investigacion-publicacion.md`, 2026-10-18) muestra que la premisa del 2026-09-28 no vale para Instagram con cuenta propia.
Decisión: Guani publica en Instagram con la app de Meta que ya usa `saimc.instagram` (Instagram Login, graph.instagram.com), con Standard Access y sin App Review: Meta marca «Not required» para una app que solo sirve a una cuenta propia, y la verificación de negocio solo se exige para Advanced Access. Se suma `instagram_business_content_publish` (el nombre exacto se confirma en el App Dashboard: una página de Meta dice `..._publishing`) y se vuelve a generar el token. Las tarjetas se publican como JPEG en sRGB, a menos de 8 MB (Meta no acepta PNG), con el tamaño actual (1080×1350 en el feed, 1080×1920 en la story), desde una URL HTTPS pública del VPS. El acceso se confirma antes de programar con una llamada manual a `content_publishing_limit`. Facebook sigue fuera: exige una página, un Page access token y **otra app**, porque una app de Meta usa Facebook Login o Instagram Login pero no los dos; si App Review hace falta para una página propia no está claro (dos páginas de Meta se contradicen).
Descartado: Metricool como camino principal. La API solo viene en el plan Advanced (desde 43 €/mes), exige igual una URL pública que no venza, y suma un tercero con los datos y un pago con tarjeta internacional. Su plan Starter (desde 16 €/mes, sin API) queda como respaldo manual si la API propia falla.
Consecuencia: el punto (7) de la entrada del 2026-10-16 queda resuelto y T8b deja de ser opcional por riesgo de Meta. Los puntos (2) a (6) siguen en pie, y el (3) se concreta: JPEG, 8 MB, relación de aspecto de 4:5 a 1.91:1, 2200 caracteres y 30 hashtags, contenedores que vencen a las 24 h, y un tope de 50 o 100 publicaciones por día (la documentación se contradice; se lee en `content_publishing_limit`). La frase «Facebook exige página y revisión» de la entrada del 2026-10-16 y de T8b se corrige a «página y segunda app; la revisión está por confirmar».
```

## Fuentes (leídas el 2026-10-18)

Meta:

- https://developers.facebook.com/documentation/instagram-platform/content-publishing.md
- https://developers.facebook.com/documentation/instagram-platform/app-review.md
- https://developers.facebook.com/documentation/instagram-platform/overview.md
- https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login.md
- https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media.md
- https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-container.md
- https://developers.facebook.com/docs/pages-api/posts
- https://developers.facebook.com/docs/pages-api/overview
- https://developers.facebook.com/docs/graph-api/reference/page/photos
- https://developers.facebook.com/docs/graph-api/overview/access-levels
- https://developers.facebook.com/docs/app-review
- https://developers.facebook.com/docs/permissions (se leyó truncada)

Metricool:

- https://metricool.com/pricing/
- https://help.metricool.com/basic-guide-for-api-integration-r97af
- https://help.metricool.com/plans-add-ons-and-api-access-explained-xux1u
- https://help.metricool.com/schedule-and-post-on-instagram-6b6q5
- https://help.metricool.com/instagram-account-type-requirements-and-how-to-switch-d2zw1
- https://help.metricool.com/schedule-and-publish-on-facebook-ycu1b
- https://help.metricool.com/api-limitations-per-social-network-n7zlr
- https://help.metricool.com/daily-posting-limits-on-social-media-sh58j
- https://help.metricool.com/fair-use-policy-for-social-media-scheduling-e3tnm
- https://help.metricool.com/data-processing-agreement-dpa-8vc0d
- https://help.metricool.com/available-payment-methods-in-metricool-0siw2

No se leyeron: los términos de servicio de Metricool ni su página de requisitos
de imagen y video.
