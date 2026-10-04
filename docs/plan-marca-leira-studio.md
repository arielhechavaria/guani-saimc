# Leira Studio — pasos para crear la marca y presentar Guani

Fecha: 2026-10-04. Copia local del documento de Claude Docs
(https://claude.ai/code/artifact/9d2c6688-51d7-48cc-8869-4eab7f5d83e7).
Vive fuera del repositorio a propósito: habla de tu nombre y de la exposición
del proyecto, y no debe viajar con el código.

## Resumen

Leira Studio se usa desde hoy como marca; la empresa legal se constituye cuando
Guani empiece a cobrar. Para promocionar sin tu nombre basta la marca. Una
entidad legal no añade discreción y sí añade trámites, contabilidad e impuestos.

Orden de trabajo:

1. Crear la identidad de Leira Studio.
2. Quitar tu nombre de todo lo público.
3. Fijar qué se dice de Guani.
4. Enviar el correo a CEMIS.
5. Consultar a un abogado sobre la entidad legal.
6. Traspasar los activos a la empresa cuando exista.

Los pasos 1 a 4 se pueden hacer esta semana. Los pasos 5 y 6 no bloquean la
promoción. A quién más escribir además de CEMIS: ver «Compendio de entidades»
al final.

## Paso 1 — Identidad de Leira Studio

La marca necesita cuatro cosas antes de escribirle a nadie: un nombre fijo, un
correo, una frase que la describa y una relación clara con Guani.

- [ ] **Fijar la grafía del nombre.** Elegir una sola forma («Leira Studio») y
      usarla igual en el correo, el tablero y cualquier perfil.
- [ ] **Comprobar que el nombre está libre.** Buscar «Leira Studio» en Google,
      en redes y como dominio (`leirastudio.com`, `leira.studio`). Si otra
      empresa ya lo usa en software, conviene saberlo antes.
- [ ] **Tener un correo de la marca.** El remitente debe decir «Leira Studio»,
      no tu nombre. Lo ideal es una dirección en dominio propio (`hola@...`);
      una cuenta de Gmail de la marca sirve para empezar.
- [ ] **Revisar el nombre visible del remitente.** En la configuración de la
      cuenta, el campo «Nombre» es lo que ve quien recibe el correo. Enviarte
      un correo de prueba a otra cuenta y mirarlo.
- [ ] **Escribir la descripción de una línea.** Propuesta: «Leira Studio es un
      estudio de desarrollo de software. Guani es su sistema de precios del
      mercado informal cubano.»
- [ ] **Decidir la relación marca–producto.** Leira Studio es quien firma;
      Guani es el producto. En el tablero se lee «Guani, por Leira Studio».

Un logotipo y una web del estudio ayudan, pero no hacen falta para el primer
correo.

## Paso 2 — Quitar tu nombre de lo público

Una marca da discreción, no anonimato: quien investigue un dominio o un
servidor puede llegar al titular. El objetivo es que tu nombre no aparezca a
simple vista.

| Dónde | Qué revisar | Qué hacer |
| --- | --- | --- |
| Tablero público de Guani | Pie de página, panel «Sobre los datos», créditos, metadatos de la página | Sustituir cualquier nombre personal por «Leira Studio» |
| Dominio | Consulta WHOIS del dominio del tablero | Activar la privacidad de WHOIS en el registrador |
| Repositorio | `github.com/arielhechavaria/guani-saimc`, hoy privado | No urge. Antes de abrirlo, moverlo a una organización `leirastudio` |
| Historial de commits | Autor «Ariel Hechavarria» en cada commit | Solo importa si el repositorio se hace público; decidirlo entonces |
| Correo | Nombre visible del remitente y firma | Cubierto en el paso 1 |
| Perfiles y redes de Guani | Nombre del administrador visible, biografía | Crear los perfiles desde la identidad de la marca |
| Canales de WhatsApp | El número que usa WAHA para leer grupos | No publicarlo ni usarlo para contacto de la marca |

El cambio en el tablero toca plantillas del repositorio. Es un trabajo aparte y
va después de cerrar el que está abierto (A6).

## Paso 3 — Qué se dice de Guani

El mensaje se apoya solo en lo que ya funciona y se puede comprobar en el
tablero. Lo que está en la hoja de ruta se presenta como hoja de ruta.

**Fortalezas que se pueden afirmar hoy**

- **Cada precio se puede comprobar.** Detrás de cada cifra está el anuncio
  original que la originó.
- **Por provincia.** Una matriz producto × provincia, no un promedio nacional.
- **Compara lo comparable.** «Aceite 1 L» y «aceite 900 ml» se normalizan a
  una misma unidad antes de calcular.
- **Dato verificado por una persona.** Cada precio aceptado pasa por revisión
  humana; el descarte de ruido es automático y se audita cada semana.
- **Estadística resistente a manipulación.** Medianas y detección de atípicos,
  con reglas declaradas.
- **Moneda común.** Los anuncios en CUP, USD, EUR y MLC se convierten con la
  tasa informal de elTOQUE, atribuida.
- **MVP construido y en producción.** Captura continua desde canales y grupos
  de WhatsApp, con tablero público.

**Lo que no se afirma todavía**

| Tema | Por qué no | Cómo decirlo |
| --- | --- | --- |
| Predicción de precios | Es la Fase 3 de la hoja de ruta y depende de medir la precisión de extracción | «Prevista en la hoja de ruta, sobre el histórico que se está acumulando» |
| Oferta para empresas (API, informes) | No está construida | «Hoy no la ofrecemos» |
| Código abierto | El repositorio está privado | No mencionarlo hasta abrirlo |
| Cobertura nacional | La meta actual es de 3 a 5 productos en 2 provincias | Dar las cifras reales |

**Cifras a rellenar antes de enviar nada**, tomadas de `/control` el mismo día:

- [ ] Anuncios procesados
- [ ] Precios validados
- [ ] Productos con dato
- [ ] Provincias con dato
- [ ] URL del tablero público

## Paso 4 — Correo a CEMIS

CEMIS es un sitio informativo independiente, lanzado en junio de 2026, que
lista oportunidades de negocio en Cuba. Su oportunidad OPP-003 pide «series de
precios reales del mercado», que es lo que Guani produce. Guani encaja como
fuente de datos, no como el software de costos para empresas que la
oportunidad describe.

No hay convocatoria ni fecha límite. Su página
[Acerca de](https://cemis.io/about.html) da un correo para «propuestas de
colaboración» y otro para aparecer en el directorio. La expectativa es baja: no
publican quién lo opera y solo 21 de 258 empresas del directorio están
verificadas.

**Texto**

> **Para:** hola@cemis.io
>
> **Asunto:** Guani — precios reales del mercado informal cubano, por provincia y con evidencia
>
> Hola.
>
> Somos Leira Studio, un estudio de desarrollo de software. Estamos
> construyendo Guani, un sistema que mide lo que cuestan los bienes de consumo
> en el mercado informal cubano.
>
> Lo que lo distingue de otros índices de precios:
>
> - Cada precio se puede comprobar. Detrás de cada cifra está el anuncio
>   original que la originó. No pedimos que nos crean: mostramos la evidencia.
> - Por provincia, no un promedio nacional. Una matriz producto × provincia,
>   porque el aceite no cuesta lo mismo en La Habana que en Holguín.
> - Compara lo comparable. «Aceite 1 L» y «aceite 900 ml» se normalizan a una
>   misma unidad antes de calcular nada. Ese es el problema difícil, y es el
>   núcleo del sistema.
> - Dato verificado por una persona. Cada precio aceptado pasa por revisión
>   humana; el descarte de ruido es automático y se audita cada semana.
> - Estadística resistente a manipulación. Medianas y detección de atípicos,
>   con reglas de validación declaradas.
> - Moneda común. Los anuncios en CUP, USD, EUR y MLC se convierten con la tasa
>   informal de elTOQUE, atribuida.
>
> El MVP está construido y en producción: captura continua desde canales y
> grupos de WhatsApp, [N] anuncios procesados, [N] precios validados de
> [N productos] en [N provincias], y un tablero público en [URL]. Ahora empieza
> la fase de lanzamiento público.
>
> Hacia dónde va: el histórico que se está acumulando es la base de una capa
> predictiva y de una oferta para empresas (API e informes), previstas en la
> hoja de ruta una vez consolidada la cobertura. Hoy no las ofrecemos; las
> mencionamos porque es donde Guani se cruza con OPP-003.
>
> Les escribimos justamente por esa oportunidad. OPP-003 exige «series de
> precios reales del mercado» como requisito, y eso es lo que Guani produce. No
> es un software de costos para empresas: es la capa de datos que una
> herramienta así necesita, y hoy nadie la ofrece con trazabilidad.
>
> ¿Les interesa que aparezca asociado a esa oportunidad, o conversar sobre una
> colaboración?
>
> Un saludo,
>
> Leira Studio
>
> [correo de contacto de la marca]

**Antes de enviar**

- [ ] Confirmar la dirección en la página Acerca de. La de arriba se obtuvo
      decodificando la protección de correo del sitio.
- [ ] Rellenar los cinco corchetes con las cifras del paso 3.
- [ ] Enviar desde el correo de la marca, no desde el personal.
- [ ] Comprobar que el tablero público ya no muestra tu nombre (paso 2).
- [ ] Escribir a la dirección de colaboración, no registrarse en el directorio
      de empresas: Leira Studio todavía no es una entidad constituida.

## Paso 5 — Entidad legal en Cuba

La entidad legal se constituye cuando Guani tenga un primer ingreso a la vista,
no antes. Lo único que conviene hacer ya es una consulta con un abogado o una
consultoría de mipymes, porque hay una pregunta que decide todo y no está
resuelta.

**Las dos figuras posibles**

| | Mipyme (S.R.L.) | Trabajador por cuenta propia |
| --- | --- | --- |
| Quién es el titular | La sociedad, con personalidad jurídica propia | Tú, como persona natural |
| Puede ser dueña de Guani | Sí | No: el dueño sigues siendo tú |
| Socios | Puede ser de un solo socio | No aplica |
| Capital mínimo | No se exige | No aplica |
| Aprobación | Dirección de Desarrollo Municipal, hasta 20 días hábiles | Puede aprobarse en el propio acto |
| Carga posterior | Contabilidad, impuestos de sociedad, registros | Menor |

La actividad de trabajador por cuenta propia aplicable es «programador de
equipos de cómputo», que permite desarrollar y comercializar aplicaciones y
contratar con personas naturales y jurídicas.

**Trámites de la mipyme, en orden**

| Paso | Organismo | Plazo | Costo |
| --- | --- | --- | --- |
| 1. Solicitud de creación | Dirección de Desarrollo Municipal, por la Plataforma de Actores Económicos | 20 días hábiles | Sin costo |
| 2. Cuenta de depósito para el capital | Banco | 5 días hábiles | Comisiones |
| 3. Escritura pública de constitución | Notaría | Hasta 20 días | 2 105 CUP |
| 4. Inscripción | Registro Mercantil provincial | 15 días hábiles | 3 100 CUP |
| 5. Registro tributario | ONAT | 10 días hábiles | 50 CUP |
| 6. Registro estadístico | ONEI | Sin dato | Sin costo |
| 7. Cuenta corriente | Banco | 7 días hábiles | Comisiones |

La sociedad adquiere personalidad jurídica al inscribirse en el Registro
Mercantil. Plazos y costos según la guía de Cubatrámite actualizada el 5 de
septiembre de 2026; confirmarlos al iniciar el trámite.

Marco vigente: Decreto-Ley 88 de 2024, modificado por el Decreto-Ley 133 de
2026, en vigor desde el 9 de septiembre de 2026.

**Preguntas para el abogado**

- [ ] ¿Se aprobaría un objeto social que incluye recopilar y publicar precios
      del mercado informal? Las mipymes pueden ejercer cualquier actividad
      lícita salvo las no autorizadas, y esa lista está pendiente de revisar.
- [ ] ¿Conviene redactar el objeto social como «desarrollo de software y
      servicios de datos», con Guani como un producto más?
- [ ] ¿Cómo se registra la marca «Leira Studio» y el nombre «Guani», y puede
      hacerse antes de constituir la sociedad?
- [ ] ¿Cómo puede cobrar una mipyme a clientes en el exterior?
- [ ] ¿Qué obligaciones tiene la sociedad sobre los datos que almacena?

## Paso 6 — Qué pasa a nombre de la empresa

Una empresa es propietaria de Guani solo cuando estos seis activos están a su
nombre. Mientras sigan al tuyo, la empresa existe en papel pero no es dueña de
nada.

| Activo | Dónde está hoy | Cómo se traspasa |
| --- | --- | --- |
| Código | Repositorio en tu cuenta personal de GitHub | Transferirlo a una organización de la empresa y firmar una cesión escrita de derechos de autor |
| Dominio | A nombre de quien lo registró | Cambiar el titular en el registrador |
| Marca «Guani» y «Leira Studio» | Sin registrar | Registrarlas a nombre de la sociedad |
| Datos | Base de datos e histórico en el VPS | Incluirlos en la cesión; son el activo principal |
| Infraestructura | Cuentas del VPS, WAHA y proveedores de modelos | Pasar titularidad y facturación a la empresa |
| Cobro | No existe todavía | Abrir la cuenta de cobro ya a nombre de la sociedad |

La cesión de derechos de autor y de datos es un documento que redacta el
abogado. Conviene firmarla el mismo día de la constitución.

## Riesgos y pendientes

El riesgo principal no es de marca sino de exposición: Guani mide el mercado
informal desde dentro de Cuba, y cada paso de promoción lo hace más visible.

- **Exposición ante CEMIS.** El sitio se basa en el documento oficial de
  transformaciones del Gobierno y no publica quién lo opera. No se sabe quién
  leerá el correo.
- **La marca no protege legalmente.** Leira Studio sin constituir no separa tu
  responsabilidad personal de la del proyecto.
- **Una marca sin persona genera menos confianza.** Si alguien pide una llamada
  o un nombre, hay que decidir antes qué se responde.
- **Objeto social sin confirmar.** No está verificado que se apruebe una
  sociedad dedicada a publicar precios del mercado informal. Es la primera
  pregunta para el abogado.
- **Datos de terceros.** Guani no guarda teléfonos ni datos de contacto de
  anunciantes; esa regla debe mantenerse en todo lo que se publique bajo la
  marca.
- **Nombre sin comprobar.** No se ha verificado que «Leira Studio» esté libre
  como marca o dominio.

Nada de esto es asesoría legal. La figura concreta y el objeto social los
confirma un abogado en Cuba.

---

# Compendio de entidades a contactar

Catorce entidades verificadas en seis categorías, cada una con su nivel de
exposición. CEMIS va primero porque su correo ya está escrito. Es una primera
pasada al 4 de octubre de 2026; los canales marcados «por confirmar» hay que
abrirlos antes de escribir.

## Lista

| Entidad | Tipo | Qué es | Qué pedirle | Canal | Exposición |
| --- | --- | --- | --- | --- | --- |
| [CEMIS](https://cemis.io/about.html) | Directorio | Sitio independiente de oportunidades de negocio, lanzado en junio de 2026; no publica quién lo opera | Aparecer asociado a OPP-003 como fuente de datos | hola@cemis.io | Media |
| [La Guía de Cuba](https://laguiadecuba.com/) | Directorio | Directorio gratuito de mipymes en todas las provincias, con planes de pago | Ficha de Leira Studio en servicios | Formulario «Agrega tu negocio» | Baja |
| [Directorio Cubano](https://www.directoriocubano.info/empresas/) | Directorio | Listado de empresas con 9 741 mipymes privadas, más sección de noticias | Ficha y, si publican, una nota sobre Guani | Por confirmar | Baja |
| [Deus Expertos Contables](https://deuscontables.com/quienes-somos/) | Consultora | Asesoría fiscal, contable y de constitución de empresas desde 2013 | La consulta legal del paso 5; después, si sus clientes usarían datos de precios | Web | Baja |
| [AUGE](https://oncubanews.com/cuba/economia/emprendimientos/las-mipymes-apenas-crecen-en-cuba-y-su-ralentizacion-frena-la-competencia-y-el-dinamismo-economico/) | Consultora | Consultora privada que ha acompañado a más de 400 negocios y publica informes sobre el sector | Usar datos de Guani en sus informes; segunda opinión legal | Por confirmar | Baja |
| [CubaEmprende](https://proyectocubaemprende.org/) | Formación | Proyecto del Arzobispado de La Habana: formación y asesoría empresarial | Presentar Guani a su red de emprendedores | Web | Baja |
| [Negolution](https://www.ipscuba.net/economia/negolution-una-revista-por-los-emprendedores-privados-en-cuba/) | Medio | Revista dedicada al sector privado cubano | Una reseña o entrevista sobre Guani | Por confirmar | Baja |
| [IPS Cuba](https://www.ipscuba.net/espacios/cuba-20/nuevo-directorio-de-actores-economicos-en-cuba/) | Medio | Agencia de noticias con directorio de emprendimientos | Ficha en su directorio y nota | Por confirmar | Media |
| [OnCuba](https://oncubanews.com/tag/emprendedores-cubanos/) | Medio | Medio con sección de emprendimientos y boletín semanal de economía y negocios | Nota en la sección de emprendimientos | Por confirmar | Media |
| [Guía Cubana de Negocios](https://www.negocioscuba.net/es) | Directorio | La de Instagram (@gnegocioscuba). Alianza de la ANEC, Páginas Amarillas de ETECSA y Dofleini, con apoyo de dos ministerios | Ficha de Leira Studio como desarrollador de software | info@negocioscuba.cu, Telegram | Alta |
| [Parque Científico Tecnológico de La Habana](https://www.uci.cu/universidad/noticias/parque-cientifico-tecnologico-de-la-habana-innovacion-para-el-desarrollo) | Incubación | Sociedad mercantil en el campus de la UCI, con más de 30 entidades tecnológicas | Información sobre incubación | Por confirmar | Alta |
| [Centro de Estudios de la Economía Cubana](https://www.cubaxcuba.com/cee) | Academia | Centro de la Universidad de La Habana; su informe semestral reconoce el peso del mercado informal | Ofrecer la serie de precios para investigación | Por confirmar | Alta |
| [Proyecto NAE (PNUD)](https://www.undp.org/es/cuba/noticias/convocatoria-del-proyecto-nae-apoyara-espacios-de-innovacion-para-fortalecer-el-ecosistema-empresarial-cubano) | Financiación | Convocatorias para incubadoras y espacios de innovación; la sexta cerró el 7 de agosto de 2026 | Nada todavía: es para espacios, no para productos. Vigilar la séptima | Web del PNUD | Media |
| [elTOQUE](https://eltoque.com/category/emprendimientos) | Medio | Medio independiente; Guani ya usa su tasa informal y su producto Markets es competencia | Avisar del uso de la tasa y de la atribución | Por confirmar | Alta |

## Cómo leer la columna de exposición

- **Baja:** actor privado o religioso sin vínculo estatal visible. Se puede
  escribir ya.
- **Media:** no se sabe quién lo opera, o es un medio que publica lo que recibe.
- **Alta:** vínculo estatal directo, o medio en conflicto abierto con el
  Gobierno. Escribir significa que Guani queda a la vista de una de las dos
  partes.

La exposición alta no es motivo para descartar a nadie. Es motivo para
decidirlo después de la consulta legal del paso 5, no antes. Los niveles son un
criterio de trabajo, no un dato verificado.

## Fuera de la lista a propósito

No se incluyen 14ymedio, CubaNet, Diario de Cuba ni Martí Noticias. Cubren la
economía cubana, pero un contacto con ellos desde dentro de Cuba expone a quien
escribe.

## Orden de envío

1. Las de exposición baja, con el mismo correo base adaptado en la primera y la
   última frase.
2. Esperar una o dos semanas y anotar quién responde.
3. Las de exposición media.
4. Las de exposición alta, solo después de hablar con el abogado.

El correo base es el del paso 4. Para cada entidad cambia el párrafo de OPP-003
por la petición de la columna «Qué pedirle».

## Fuentes

- [CEMIS — OPP-003](https://cemis.io/oportunidades/opp-003-software-de-inteligencia-de-precios-para-empresas-cubanas.html)
- [CEMIS — Acerca de](https://cemis.io/about.html)
- [CEMIS — Directorio de empresas](https://cemis.io/empresas/index.html)
- [Cubatrámite — Creación y constitución de mipymes en Cuba](https://cubatramite.com/creacion-y-constitucion-de-mipymes-en-cuba/)
- [Negocios Cuba — Gaceta Oficial No. 71 de 2026](https://blog.negocioscuba.net/gaceta-oficial-71-2026-cuba-mipymes-tcp-cna/)
- [MINCOM — Programador de equipos de cómputo](https://www.mincom.gob.cu/en/node/4670) (resultado de búsqueda, página no abierta)

Solo se abrieron las páginas de CEMIS, la Guía Cubana de Negocios, La Guía de
Cuba, Cubatrámite y Negocios Cuba. El resto de las entidades del compendio sale
de resultados de búsqueda.
