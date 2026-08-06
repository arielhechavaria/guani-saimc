# 📄 Sistema Abierto de Inteligencia de Mercado para Cuba (SAIMC)

## Investor & Technical Whitepaper

### Versión 3.4 – Curaduría Primero: Validar la Hipótesis Antes de Automatizar

**Autor:** Ariel Hechavarria
**Fecha:** Agosto 2026  
**Estado:** Propuesta para Inversión / Implementación — Entrando en desarrollo

---

## Cambios respecto a la versión 3.3

Esta versión no modifica la visión ni el modelo de negocio. Corrige una **inconsistencia estructural** que hacía el plan más riesgoso de lo necesario:

1. **Ingesta manual como modelo operativo real de la Fase 0**, no como paso preliminar simbólico. La v3.3 describía ingesta manual en la sección 6.1 pero luego el roadmap asumía captura automática de Telegram desde el mes 1. Se unifica el criterio: **primero curaduría humana, después APIs oficiales, después automatización asistida.**
2. **Hipótesis de extracción elevada a hipótesis crítica (H0)** con métrica de precisión medible contra un conjunto etiquetado a mano. Todo el resto del sistema depende de ella.
3. **Decisión de stack cerrada** (sección 13): Python de extremo a extremo en Fase 0–1, con Angular reservado para la Fase 2.
4. **Nuevo riesgo 10.6**: términos de servicio de plataformas externas y tratamiento de datos personales.
5. **Nuevas métricas de calidad de extracción** en la sección 12.
6. **Patrón de bandeja de entrada + cola de curaduría** (sección 6.1): la captura y la curaduría se separan en el tiempo. El OCR y la extracción por reglas pasan a ser asistentes de la cola, no etapas autónomas del pipeline. La cola de curaduría es simultáneamente herramienta de trabajo e instrumento de medición de H0.
7. **Aclaración del alcance de H0** (sección 3): H0 no bloquea el lanzamiento del dashboard público, bloquea la capacidad de escalar hacia forecasting y capa B2B.

---

# Índice

1. Resumen Ejecutivo  
2. Contexto y Problema del Mercado Cubano  
3. Hipótesis de Negocio (incluye H0 – Hipótesis Crítica)  
4. Usuarios Objetivo y Casos de Uso  
5. Diferenciación Estratégica Frente a Competidores Reales  
6. Arquitectura General del Sistema  
   - 6.1 Data Ingestion Engine (Modelo por etapas)  
   - 6.2 Validation Engine  
   - 6.3 Intelligence Engine  
   - 6.4 Transparency Layer  
   - 6.5 Data Lineage Framework  
   - 6.6 Marketplace Intelligence Graph  
   - 6.7 Diseño de Interfaces y Visualización de Datos (NUEVO)  
7. Modelo de Datos  
8. Producto Comercial y Modelo de Monetización  
9. Roadmap de Implementación Realista  
10. Riesgos y Mitigaciones  
11. Anexo: Madurez del Forecast  
12. Métricas de Éxito (KPIs)  
13. Stack Tecnológico y Estrategia de Escalabilidad  
14. Solicitud de Inversión  
15. Conclusión Estratégica

---

# 1. Resumen Ejecutivo

El mercado cubano opera actualmente bajo un entorno caracterizado por alta volatilidad, fragmentación de información y ausencia de mecanismos confiables de inteligencia económica.

Millones de decisiones comerciales se toman diariamente basándose en información incompleta proveniente de canales informales como:

- grupos de Telegram,
- WhatsApp,
- Facebook Marketplace,
- redes sociales,
- comunidades comerciales privadas.

Estos canales contienen un enorme volumen de información económica, pero actualmente permanecen en un estado no estructurado: datos sin historial, sin validación, sin trazabilidad y sin capacidad predictiva.

El problema fundamental no es la falta de información.  
El problema es la falta de **inteligencia estructurada sobre esa información**.

---

## La oportunidad

SAIMC propone construir una infraestructura abierta de inteligencia de mercado capaz de transformar datos comunitarios desorganizados en:

- indicadores económicos confiables,
- tendencias regionales,
- predicciones de precios,
- modelos de riesgo,
- alertas comerciales,
- datasets históricos,
- APIs para integración empresarial.

**Todo presentado con un diseño visual, intuitivo y accesible para cualquier usuario, sin necesidad de conocimientos técnicos.** Mapas, semáforos de colores y gráficos claros que permiten tomar decisiones en segundos.

---

## El modelo operativo: curaduría primero

Un punto que distingue a SAIMC de un proyecto de scraping: **el sistema no nace automatizado, y esto es deliberado.**

La captura de datos evoluciona en tres etapas, y cada una solo se habilita cuando la anterior demostró funcionar:

| Etapa | Modo de captura | Qué valida | Cuándo se habilita |
| :--- | :--- | :--- | :--- |
| **E1 — Curaduría** | Un operador lee anuncios reales y los carga por interfaz administrativa | Que el mercado sea legible: que producto, precio y provincia se puedan extraer de forma consistente | Desde el día 1 |
| **E2 — Asistida** | El operador pega texto en bruto; el sistema propone la extracción y el humano confirma o corrige | Que las reglas de extracción alcancen precisión suficiente | Cuando E1 acumuló un conjunto etiquetado de referencia |
| **E3 — Automática** | APIs oficiales (Telegram Bot API sobre canales propios o autorizados) y fuentes con permiso explícito | Volumen y frescura | Cuando E2 supera el umbral de precisión definido en la sección 12.5 |

La curaduría manual no es una limitación temporal que hay que superar rápido. Es el mecanismo que **genera el conjunto de verdad (*ground truth*) contra el cual se mide todo lo demás.** Sin ese conjunto, la precisión del sistema es una afirmación sin evidencia.

Adicionalmente, este orden reduce riesgo legal y operativo: se construye valor sin depender desde el inicio de la extracción automatizada de plataformas de terceros (ver riesgo 10.6).

---

La plataforma no busca competir directamente con modelos orientados exclusivamente a publicar precios.  
La propuesta de valor es diferente:

> **SAIMC no vende solamente datos. Convierte datos dispersos en inteligencia económica verificable, predictiva y auditable, presentada de forma visual e inmediata.**

---

# Posicionamiento Estratégico

La plataforma se construye sobre cinco principios fundamentales:

## 1. Transparencia Radical

Cada dato mantiene una relación directa con su fuente original:

- texto capturado,
- imagen original,
- fecha,
- ubicación,
- usuario origen,
- reglas aplicadas,
- nivel de confianza.

El usuario puede conocer no solamente el resultado, sino también cómo fue generado.

---

## 2. Validación Autónoma

El sistema utiliza un motor de reglas configurable capaz de filtrar automáticamente:

- errores humanos,
- spam,
- duplicados,
- datos inconsistentes,
- anomalías estadísticas.

El operador del sistema no valida manualmente cada dato.  
Administra las reglas que permiten al sistema validar la información.

---

## 3. Inteligencia Predictiva

SAIMC evoluciona desde un simple repositorio histórico hacia un sistema de predicción:

Ejemplo:

```
Producto: Pollo congelado
Provincia: Santiago de Cuba
Situación actual: Disponibilidad baja
Tendencia: Incremento esperado +12%
Horizonte: Próximos 7 días
Nivel de confianza: 87%
```

---

## 4. Infraestructura Abierta y Visual

La información procesada se consume mediante un dashboard **diseñado para ser leído en segundos**, no para requerir una clase magistral.

- **Mapas interactivos por provincia** donde el color indica el precio (verde = barato, rojo = caro, gris = sin datos).
- **Semáforos de tendencia**: flecha roja hacia arriba (sube), flecha verde hacia abajo (baja), círculo amarillo (estable).
- **Tarjetas de producto**: cada producto tiene su propia ficha con precio actual, variación semanal y recomendación simple ("Comprar ahora", "Esperar", "Escasez alta").
- **Nada de gráficos de velas ni indicadores complejos.** La información salta a los ojos.

Esto garantiza que cualquier cubano, sin importar su nivel técnico, pueda entender de un vistazo qué está pasando con los precios en su provincia.

---

## 5. Modelo B2B Sostenible

El sistema mantiene una capa pública gratuita para generar confianza y una capa premium orientada a:

- importadores,
- comerciantes,
- investigadores,
- agencias,
- startups.

---

# 2. Contexto y Problema del Mercado Cubano

## 2.1 Situación actual

El mercado cubano presenta características particulares:

- elevada variabilidad de precios,
- diferencias regionales significativas,
- cadenas de suministro poco transparentes,
- dependencia de mercados informales,
- dificultad para anticipar cambios.

Un comerciante puede conocer el precio actual de un producto, pero normalmente no puede responder preguntas estratégicas:

- ¿Subirá o bajará?
- ¿Dónde existe mayor disponibilidad?
- ¿Cuándo conviene comprar?
- ¿Qué productos tienen riesgo de escasez?
- ¿Qué provincia presenta mejores oportunidades?

---

## 2.2 El problema de los datos actuales

La información existente presenta cinco grandes limitaciones:

| Problema                | Consecuencia                        |
| ----------------------- | ----------------------------------- |
| Datos dispersos         | Imposibilidad de análisis histórico |
| Sin validación          | Baja confianza                      |
| Sin trazabilidad        | Difícil verificar origen            |
| Sin estructura          | Imposible automatizar               |
| Sin modelos predictivos | Solo describe el pasado             |

---

## 2.3 El problema desde la mirada del hogar (El verdadero motor)

Más allá del comercio B2B, existe una necesidad cotidiana y masiva que la plataforma resuelve:

> **Ejemplo real:** Una familia en Santiago de Cuba depende del aceite de cocina. En los grupos de Telegram se publican precios dispersos: "Aceite 500 CUP en tal tienda", "Aceite 620 CUP en el otro lado". Sin un sistema que centralice y muestre la tendencia, la familia no sabe si comprar hoy (pagando 600 CUP) o esperar a mañana (cuando quizá baje a 550 CUP). **Toman decisiones a ciegas.**

El usuario doméstico no necesita un modelo predictivo avanzado; necesita **visibilidad clara y confiable de lo que está pasando en su provincia, presentada de forma visual y rápida.** Este usuario es el que, al usar el dashboard diariamente, genera el tráfico, la confianza y la masa crítica que valida el sistema antes de que lleguen los grandes clientes B2B.

---

# 3. Hipótesis de Negocio

SAIMC parte de una hipótesis central:

> Si la información económica informal puede ser capturada, estructurada, validada y analizada mediante inteligencia artificial, entonces puede convertirse en una infraestructura comercial de alto valor para empresas e investigadores.

---

---

## H0 — La hipótesis crítica (debe validarse primero)

> **Los anuncios reales del mercado informal cubano contienen producto, precio y ubicación extraíbles de forma consistente y con precisión medible.**

Toda hipótesis posterior está río abajo de esta. Si la extracción sobre texto real —con abreviaturas, jerga regional, precios por unidades no estandarizadas, mezcla de monedas e información en imágenes— no alcanza precisión suficiente, entonces no hay dataset, no hay validación, no hay predicción y no hay producto.

**Cómo se valida:** durante la Etapa 1 (curaduría), el operador etiqueta manualmente un conjunto de referencia de al menos **300 anuncios reales**. Las reglas de extracción se miden contra ese conjunto.

**Criterio de decisión:**

| Precisión sobre el conjunto de referencia | Decisión |
| :--- | :--- |
| ≥ 85% | La hipótesis se sostiene. Avanzar a Etapa 2. |
| 70% – 85% | Viable con revisión humana obligatoria. Ajustar reglas antes de avanzar. |
| < 70% | **Detener y replantear.** El problema no es de ingeniería sino de legibilidad del mercado. |

Este número es el primer artefacto que un inversor puede evaluar: no una promesa de precisión, sino una medición.

**Qué bloquea y qué no bloquea H0.** H0 no condiciona el lanzamiento del dashboard público: un dashboard alimentado por curaduría humana entrega valor real al usuario doméstico desde el primer día, y esa es la estrategia de arranque. Lo que H0 condiciona es la **capacidad de escalar**: sin extracción precisa, el volumen del sistema queda limitado a lo que el operador pueda procesar a mano, y sin volumen no hay dataset histórico, ni forecasting, ni capa B2B. En otras palabras: el producto puede existir sin H0; la tesis del whitepaper no.

---

## Hipótesis secundarias

### H1
Los negocios están dispuestos a pagar por reducir incertidumbre.

### H2
Los datos históricos tienen más valor que los datos puntuales.

### H3
La confianza del dato es un activo comercial.

### H4
La predicción tiene mayor valor económico que la observación.

### H5
**El usuario doméstico es el motor de arranque.** Su necesidad diaria de consultar precios para su despensa genera el tráfico inicial, la retroalimentación y los datos comunitarios que dan viabilidad al sistema antes de que la capa B2B madure. Este usuario contribuye por reciprocidad: al ver inteligencia útil, se convierte en reportador activo.

---

# 4. Usuarios Objetivo y Casos de Uso

| Usuario        | Necesidad                  | Solución SAIMC          |
| -------------- | -------------------------- | ----------------------- |
| **Hogares / Consumidores finales** | Saber cuándo comprar para la casa sin especular. | Dashboard público gratuito con precios actuales y tendencia por provincia. **Mapas y semáforos visuales.** |
| Importadores   | Decidir cuándo comprar     | Predicciones y alertas  |
| Mayoristas     | Optimizar inventario       | Índices regionales      |
| Investigadores | Analizar economía informal | Dataset histórico       |
| Startups       | Crear aplicaciones         | API comercial           |
| Agencias       | Estudios de mercado        | Reportes especializados |

---

# 5. Diferenciación Estratégica Frente a Competidores Reales

A diferencia de lo que plantea el modelo conceptual genérico, el mercado cubano ya cuenta con actores tecnológicos relevantes en el espacio de inteligencia de precios. **SAIMC no compite en el mismo plano, sino que ocupa un nicho desatendido.**

## Mapa de competidores actuales

| Competidor | Enfoque | Modelo de Negocio | Fortaleza | Debilidad para SAIMC |
| :--- | :--- | :--- | :--- | :--- |
| **El Toque Markets** | Precios reales de productos, combustible, pasarelas de pago y márgenes comerciales. | Suscripción internacional (Tropipay) + vía local. | **Ya cobra.** Tiene equipo mixto (técnico + periodístico) y supervisión académica (Pavel Vidal). | Caja negra: no muestra fuentes originales. Interfaz funcional pero no visualmente intuitiva. |
| **CiberCuba Índice** | Precios barriendo miles de anuncios de Telegram/Facebook. | Publicidad / Gratuito. | **Validación automática anti-spam** en producción (similar a nuestro Validation Engine). | No transparenta el proceso de descarte. Sin enfoque B2B predictivo. |
| **A Ver Que Sale** | Comparador de precios en comercio formal (>100 tiendas). | Afiliación / Tráfico. | Catálogo masivo de productos formales. | No cubre mercado informal ni predicciones. |

---

## Posicionamiento diferencial de SAIMC

| Atributo | El Toque Markets | CiberCuba | A Ver Que Sale | **SAIMC** |
| :--- | :--- | :--- | :--- | :--- |
| **Mercado objetivo** | Consumidor final + comerciante | Consumidor general | Comprador online | **Hogares + B2B + Investigadores** |
| **Fuente de datos** | Interna / Mixta | Scraping público | Scraping formal | **Comunitaria + Scraping + Operador** |
| **Transparencia** | Opaca (no muestra origen) | Opaca | Transparente (tienda) | **Radical (anuncio original visible)** |
| **Validación** | Interna | Automática (caja negra) | Directa de tienda | **Reglas públicas + Reputación + Lógica de negocio abierta** |
| **Valor principal** | Precio actual y margen | Precio actual | Precio formal | **Predicción + Riesgo + Dataset histórico** |
| **Visualización** | Funcional / Tablas | Listados | Catálogo | **Mapas, semáforos, tarjetas intuitivas** |
| **Modelo de ingresos** | Suscripción | Publicidad | Afiliación | **Freemium + API + Datasets** |

---

## Declaración de nicho

> **SAIMC no es "otro agregador de precios". Es la primera infraestructura abierta de inteligencia predictiva para el mercado informal cubano, con trazabilidad total y foco en decisiones estratégicas B2B, pero que nace resolviendo la necesidad diaria del hogar cubano con una interfaz visual que cualquier persona puede entender en segundos.**

Mientras El Toque Markets cobra por decir "cuánto cuesta hoy", SAIMC cobrará por decir "qué va a pasar mañana y por qué", mientras empodera al usuario común para decidir su compra del día a día con mapas y colores que saltan a la vista.

---

# 6. Arquitectura General del Sistema

La arquitectura SAIMC 3.0 está diseñada bajo un principio fundamental:

> **Separar la captura de información, la validación, el procesamiento inteligente y la distribución del conocimiento.**

Esto permite que el sistema pueda comenzar como un MVP pequeño y evolucionar progresivamente hacia una infraestructura nacional de inteligencia económica.

La arquitectura utiliza un modelo basado en eventos (*event-driven architecture*) donde cada nueva información genera un evento que atraviesa diferentes capas de procesamiento.

---

# Arquitectura de Alto Nivel

```mermaid
flowchart TD

A[Fuentes de Información]
A --> B[Data Ingestion Engine]

B --> C[Raw Data Storage]

C --> D[Event Queue]

D --> E[Validation Engine]

E -->|Rechazado| F[Rejected Data]

E -->|Pendiente| G[Anomaly Review]

E -->|Validado| H[Clean Market Dataset]

H --> I[Intelligence Engine]

I --> J[Forecast Models]

I --> K[Risk Models]

I --> L[Availability Index]

H --> M[Transparency Layer]

J --> N[API Layer]

K --> N

L --> N

M --> N

N --> O[Public Dashboard]

N --> P[Premium API]

N --> Q[Datasets]
```

---

## 6.1 Data Ingestion Engine

### Objetivo

Capturar información económica desde múltiples fuentes y convertirla en eventos estructurados sin perder el origen original del dato.

El principio fundamental es:

> "Primero almacenar, después interpretar."

Ningún dato recibido se modifica directamente.  
Cada entrada inicial se conserva como evidencia original.

---

### Fuentes de información — Modelo por etapas

El Data Ingestion Engine se construye una sola vez, pero se **alimenta de forma distinta según la etapa**. La interfaz de entrada al sistema es siempre la misma: un evento crudo en `raw_ingestions`. Lo que cambia es quién o qué lo produce.

Esto es una decisión de arquitectura, no un atajo: significa que pasar de curaduría manual a APIs no requiere reescribir el pipeline, solo agregar un productor de eventos.

---

#### Etapa 1 — Captura y curaduría diferida (desde el día 1)

**Principio de diseño:** la captura y la curaduría son actos separados en el tiempo. Unificarlos en un solo formulario obliga al operador a detenerse a estructurar información justo cuando está navegando, lo que hace la rutina insostenible.

```
Captura (segundos, en el móvil)  →  Bandeja de entrada  →  Cola de curaduría (en bloque)
```

**a) Captura.** El operador guarda el anuncio en el formato que sea más rápido en ese momento: captura de pantalla, copia del texto, foto. Sin estructurar nada. El único requisito es que el material llegue al directorio de entrada.

**b) Bandeja de entrada.** Un directorio observado por el sistema. Cada archivo nuevo se registra como un `raw_ingestion` con:

- referencia al archivo original (evidencia preservada),
- hash del contenido para deduplicación,
- tipo de contenido (imagen / texto),
- fecha de captura,
- estado de procesamiento (`pending` / `curated` / `discarded`).

El archivo original **nunca se modifica ni se elimina**. Es la base de la Transparency Layer: el anuncio que respalda cada precio publicado es literalmente el archivo capturado.

**c) Cola de curaduría.** Interfaz que presenta un elemento pendiente por vez:

- muestra la imagen o el texto original,
- ofrece los campos a completar: producto, precio, moneda, cantidad, unidad, provincia, canal origen, fecha de publicación,
- permite descartar el elemento si no contiene información económica útil,
- permite marcarlo como parte del conjunto de referencia.

**Volumen objetivo:** aproximadamente una hora diaria de curaduría. La captura no cuenta como tiempo de trabajo porque ocurre durante el uso normal de las redes.

**Ventaja adicional del patrón:** la bandeja de entrada es una interfaz genérica. Cuando en la Etapa 3 se habiliten fuentes automáticas, estas escriben en la misma bandeja. El pipeline de curaduría no cambia: solo cambia quién deposita los elementos.

---

#### Rol del OCR y la extracción asistida

Tanto el OCR (para imágenes) como la extracción por reglas (para texto) se incorporan **como asistentes de la cola de curaduría**, no como etapas autónomas del pipeline.

El sistema propone; el operador confirma o corrige. Esto evita que el error de OCR se acumule sobre el error de extracción sin supervisión, y convierte cada acción de curaduría en un dato de evaluación de precisión.

```
Elemento pendiente → OCR/extracción propone → Operador confirma o corrige → Registro validado
                                                            ↓
                                              Evento de evaluación (métrica H0)
```

**La cola de curaduría es simultáneamente la herramienta de trabajo y el instrumento de medición.** No hay que construir un sistema de evaluación aparte: la rutina diaria lo genera.

---

#### Etapa 2 — Extracción asistida (validación de H0)

El operador pega **solo el texto en bruto**. El sistema propone la extracción estructurada y el humano confirma, corrige o rechaza.

```
Texto pegado:   "🐔 pollo congelado cajas de 40lb a 12000 stgo entrega dom"

Propuesta del sistema:
  producto:   pollo congelado    ✓ confirmar   ✗ corregir
  precio:     12000              ✓ confirmar   ✗ corregir
  moneda:     CUP (inferida)     ✓ confirmar   ✗ corregir
  cantidad:   40 lb              ✓ confirmar   ✗ corregir
  provincia:  Santiago de Cuba   ✓ confirmar   ✗ corregir
```

Cada confirmación o corrección alimenta directamente la métrica de precisión. **La interfaz de curaduría se convierte en el instrumento de medición.** Esto es lo que hace que la Etapa 1 no sea trabajo desechable.

**Tecnología:** reglas y diccionarios (regex, catálogo de productos con alias, catálogo geográfico oficial) como primera línea; LLM únicamente para los casos que las reglas no resuelven, con el resultado siempre sujeto a confirmación humana.

---

#### Etapa 3 — Fuentes automáticas (solo tras superar el umbral de H0)

Se habilita **exclusivamente** cuando la Etapa 2 supera el umbral de precisión, y siguiendo un orden de menor a mayor riesgo legal:

**3a. Telegram vía API oficial.** Bot API sobre canales propios o canales de terceros donde exista autorización explícita del administrador. Es la vía de menor riesgo y debe agotarse primero.

**3b. Aportes de la comunidad.** Botón "subir dato" en el dashboard público, sujeto al Validation Engine y al sistema de reputación. Es la fuente que escala mejor y la que no depende de terceros.

**3c. Otras fuentes.** Cualquier fuente adicional requiere evaluación previa de términos de servicio y tratamiento de datos personales (ver riesgo 10.6). **No se asume viable por defecto.**

**Nota sobre el OCR.** El OCR ya opera desde la Etapa 1 como asistente de la cola de curaduría (ver arriba). Lo que se habilita en la Etapa 3 no es el OCR sino su ejecución **sin confirmación humana**, y solo para los tipos de anuncio donde la precisión medida lo justifique.

---

### Modelo de Evento Inicial

Toda información capturada genera un evento:

```json
{
  "event_id": "evt_001245",
  "source": {
    "type": "telegram",
    "origin": "grupo_publico_xyz"
  },
  "captured_at": "2026-07-28T10:30:00",
  "raw_content": {
    "text": "Pollo 950 CUP Santiago",
    "image": null
  },
  "status": "pending_validation"
}
```

---

## 6.2 Validation Engine

### El núcleo diferencial de SAIMC

El Validation Engine es responsable de responder una pregunta crítica:

> ¿Podemos confiar en este dato?

El sistema no asume que una publicación es correcta.  
Cada entrada pasa por un pipeline de validación.

---

### Pipeline de Validación

```mermaid
flowchart LR

A[Nuevo dato]

A --> B[Validación estructura]

B --> C[Validación producto]

C --> D[Validación precio]

D --> E[Validación ubicación]

E --> F[Validación usuario]

F --> G[Detección duplicados]

G --> H[Score confianza]

H --> I[Publicado]
```

---

### Reglas de Validación

#### Regla 1 — Estructura mínima

El dato debe contener: producto reconocido, valor numérico, unidad monetaria, contexto suficiente.

Ejemplo válido: `Pollo 950 CUP Santiago`  
Ejemplo inválido: `Disponible barato`

---

#### Regla 2 — Coherencia del precio

El sistema no utiliza solamente un promedio nacional.  
Modelo:

```
Precio esperado = 70% promedio provincial + 30% tendencia nacional
```

Esto permite reflejar diferencias reales entre regiones.

Ejemplo:  
Producto: Arroz  
Provincia: Santiago  
Precio histórico: 200 CUP/kg  
Tendencia nacional: +10%  
Precio esperado: 210 CUP/kg  

Si llega `Arroz 220 CUP/kg` → **VALIDADO**  
Si llega `Arroz 900 CUP/kg` → **ANOMALÍA**

---

#### Regla 3 — Validación geográfica

El sistema verifica: provincias oficiales, municipios, regiones comerciales.  
Ejemplo:  
Entrada: `Pollo 950 CUP Oriente` → el sistema puede mantener `Zona: Oriente, Provincia: No determinada`  
Pero no acepta: `Pollo 950 CUP Wakanda`

---

#### Regla 4 — Sistema de reputación dinámica

Cada usuario posee:

```json
{
  "user_id": "usr_123",
  "trust_score": 78,
  "level": "trusted_source"
}
```

**Estados:**

| Score  | Estado            |
| ------ | ----------------- |
| 0-30   | Bloqueado         |
| 31-60  | Usuario normal    |
| 61-80  | Usuario confiable |
| 81-100 | Fuente verificada |

**Evolución del score:**

| Evento                  | Cambio |
| ----------------------- | -----: |
| Dato confirmado         |    +10 |
| Historial consistente   |     +5 |
| Precio falso confirmado |    -20 |
| Spam                    |    -15 |
| Duplicado               |     -5 |

---

#### Regla 5 — Deduplicación

El sistema compara: hash del texto, similitud semántica, imagen, fecha, fuente.

Ejemplo:  
Publicaciones: `"Pollo congelado 950 CUP"` y `"Vendo pollo 950 pesos"`  
El motor NLP determina similitud del 94% → **Posible duplicado**

---

#### Regla 6 — Coherencia cantidad/precio

Ejemplo:  
Entrada: `Caja pollo 40 libras, Precio 500 CUP`  
El sistema compara historial, peso, unidad, precio esperado.  
Resultado: **Inconsistencia detectada**

---

### Resultado de validación

Cada dato obtiene:

```json
{
  "validation_status": "approved",
  "confidence_score": 94,
  "rules": {
    "structure": true,
    "price": true,
    "location": true,
    "trust": true,
    "duplicate": true
  }
}
```

---

## 6.3 Intelligence Engine

### Objetivo

Transformar datos validados en inteligencia accionable.  
El sistema deja de responder "¿Cuánto cuesta?" y empieza a responder "¿Qué probablemente ocurrirá?"

---

### Componentes principales

#### A) Extracción Inteligente de Entidades

Tecnologías:

- **MVP:** Regex, Diccionarios, LLMs
- **Escala:** spaCy, modelos entrenados con lenguaje cubano

Extrae: Producto, Precio, Moneda, Cantidad, Ubicación, Fecha, Proveedor

---

#### B) Forecasting

Modelos: Prophet, ARIMA, modelos estadísticos personalizados

Predicción:

```json
{
  "product": "pollo",
  "province": "Santiago",
  "current_price": 950,
  "forecast_7_days": {
    "expected": 1065,
    "confidence": 0.87
  }
}
```

---

#### C) Índice de Disponibilidad (Availability Index 0-100)

Combina: frecuencia de publicaciones, velocidad de desaparición, volumen histórico, menciones.

Ejemplo:  
Pollo → Disponibilidad: 32/100 → **Escasez alta**

---

#### D) Modelo de Riesgo Comercial

Calcula sensibilidad: Producto → Dependencia dólar informal → Importación → Disponibilidad → Riesgo

Ejemplo:  
Producto: Aceite vegetal  
Riesgo: Alto  
Motivo: Alta correlación USD informal

---

## 6.4 Transparency Layer

La confianza es un producto.  
SAIMC no oculta el origen.  
Cada dato tiene una cadena visible:

```
Fuente original
       |
       ↓
Dato extraído
       |
       ↓
Validaciones aplicadas
       |
       ↓
Resultado inteligente
```

---

### Pirámide de Transparencia

```
           PREMIUM
              ▲
              |
     Predicciones y modelos
              ▲
              |
     Métricas e indicadores
              ▲
              |
     Datos estructurados
              ▲
              |
     Fuente original
```

---

## 6.5 Data Lineage Framework

### Sistema de Trazabilidad Completa del Dato

Uno de los principales problemas de los mercados informales es que la información pierde contexto rápidamente.  
Un precio sin origen, fecha o evidencia tiene un valor limitado.

SAIMC introduce un modelo de **Data Lineage** donde cada dato mantiene una historia completa desde su captura hasta su transformación en inteligencia.

---

### Principio fundamental

> **Todo dato publicado debe poder responder cuatro preguntas:**
> 1. ¿De dónde salió?
> 2. ¿Cuándo fue capturado?
> 3. ¿Qué procesos atravesó?
> 4. ¿Por qué el sistema confía en él?

---

### Flujo de vida del dato

```mermaid
flowchart TD

A[Fuente Original]

A --> B[Evento Capturado]

B --> C[Extracción de Entidades]

C --> D[Validación Automática]

D --> E[Score de Confianza]

E --> F[Dataset Histórico]

F --> G[Modelo Predictivo]

G --> H[Información Comercial]
```

---

### Ejemplo completo de trazabilidad

Un usuario publica: `"Pollo congelado 950 CUP Santiago"`

El sistema almacena:

```json
{
  "record_id": "SAIMC-20260728-00001",
  "source": {
    "type": "telegram",
    "channel": "mercado_santiago",
    "captured_at": "2026-07-28T10:30:00"
  },
  "original_data": {
    "text": "Pollo congelado 950 CUP Santiago"
  },
  "extracted_data": {
    "product": "pollo congelado",
    "price": 950,
    "currency": "CUP",
    "province": "Santiago de Cuba"
  },
  "validation": {
    "structure": true,
    "price_range": true,
    "location": true,
    "duplicate_check": true
  },
  "trust": {
    "user_score": 82,
    "confidence_score": 94
  },
  "status": "approved"
}
```

---

### Beneficios estratégicos

- **Para usuarios públicos (hogares):** Permite saber si el precio de hoy es justo o está inflado, y anticipar si conviene comprar ahora, todo visto en un mapa o semáforo de colores.
- **Para investigadores:** Permite construir estudios económicos, análisis históricos, modelos sociales.
- **Para empresas:** Permite justificar decisiones con evidencia verificable.

---

## 6.6 Marketplace Intelligence Graph

### Grafo de Inteligencia del Mercado

SAIMC no solamente almacena datos. Construye relaciones.  
El mercado completo puede representarse como un grafo dinámico donde cada elemento está conectado.

---

### Modelo conceptual

```mermaid
graph LR

A[Producto]

A --> B[Provincia]

A --> C[Precio]

A --> D[Disponibilidad]

A --> E[Fuente]

B --> F[Usuario]

C --> G[Tendencia]

D --> H[Predicción]

E --> I[Reputación]
```

---

### Ejemplo: Producto Pollo congelado

```
                Pollo
                  |
     -----------------------------
     |             |             |
 Santiago       Habana       Holguín
     |             |             |
  950 CUP      1100 CUP      980 CUP
     |             |             |
 Telegram      Marketplace    Usuario X
```

El sistema puede responder:

**Pregunta tradicional:** ¿Cuál es el precio del pollo?  
Respuesta: 1.000 CUP promedio.

**Pregunta inteligente:** ¿Dónde conviene comprar pollo esta semana?  
Respuesta: Santiago presenta menor precio promedio y mayor disponibilidad proyectada durante los próximos 7 días.

---

### Valor competitivo del grafo

Con el tiempo SAIMC acumula un activo difícil de replicar:

- relaciones históricas,
- comportamiento regional,
- confiabilidad de fuentes,
- patrones de oferta,
- comportamiento estacional.

El verdadero activo no es la base de datos. Es el conocimiento acumulado sobre cómo funciona el mercado.

---

## 6.7 Diseño de Interfaces y Visualización de Datos (NUEVO)

### Principio de diseño

> **La información debe saltar a los ojos. No se necesita una maestría para entender un gráfico.**

A diferencia de plataformas financieras como TradingView, donde los gráficos de velas y los indicadores técnicos requieren conocimiento especializado, SAIMC está diseñado para que **cualquier cubano, en 5 segundos, entienda la situación de su producto en su provincia.**

---

### Componentes visuales del Dashboard

#### 1. Mapa de Calor por Provincia

- Cada provincia tiene un color según el precio promedio del producto seleccionado.
- **Verde:** Precio por debajo del promedio nacional.
- **Amarillo:** Precio en el promedio.
- **Rojo:** Precio por encima del promedio.
- **Gris:** Sin datos suficientes.

El usuario puede hacer clic en una provincia para ver el detalle.

#### 2. Tarjeta de Producto (Vista Principal)

Cada producto tiene una tarjeta que resume la información clave:

```
┌─────────────────────────────────────┐
│  🥩 POLLO CONGELADO                 │
│  Santiago de Cuba                   │
│                                      │
│  Precio actual:  950 CUP            │
│  Promedio semana: 920 CUP           │
│                                      │
│  Tendencia:  ▲  +3.2%  (últimos 7d)│
│                                      │
│  Recomendación:  ⚠️ COMPRAR AHORA   │
│  (Se espera subida a 1065 CUP)      │
│                                      │
│  Disponibilidad: 🔴 Baja (32/100)   │
│                                      │
│  Confianza del dato:  ████████░░ 94%│
└─────────────────────────────────────┘
```

#### 3. Semáforo de Tendencia (Sin números complicados)

- **🟢 Flecha verde hacia abajo:** El precio está bajando. Buen momento para esperar.
- **🟡 Círculo amarillo:** El precio está estable. Puedes comprar sin urgencia.
- **🔴 Flecha roja hacia arriba:** El precio está subiendo. Conviene comprar ahora.

#### 4. Gráfico de Evolución Simple (No velas)

En lugar de gráficos de velas japonesas, se usa un gráfico de líneas simple con:

- **Eje X:** Fechas (últimos 30 días).
- **Eje Y:** Precio.
- **Línea azul:** Precio promedio.
- **Banda gris:** Rango mínimo y máximo.
- **Línea punteada naranja:** Predicción a 7 días.

Esto muestra de forma clara si el precio está subiendo o bajando, sin necesidad de interpretar patrones complejos.

#### 5. Alertas Visuales (Destellos)

- Si un producto tiene una variación del precio > 10% en 24 horas, la tarjeta **parpadea en naranja** con la palabra "ALERTA".
- Si la disponibilidad cae por debajo de 30/100, la tarjeta **parpadea en rojo** con "ESCASEZ".

---

### Filosofía de diseño

> **No se necesita saber de finanzas para entender si el aceite está caro o barato hoy en Santiago.**

El usuario abre la página, ve un mapa de Cuba coloreado, busca su provincia, ve el producto y en 3 segundos sabe:
- ¿Está caro o barato?
- ¿Sube o baja?
- ¿Compro ahora o espero?

Esa es la verdadera inteligencia de mercado: **información que se entiende sin esfuerzo.**

---

# 7. Modelo de Datos Inicial

La arquitectura de datos está diseñada para separar:

1. Datos originales.
2. Datos procesados.
3. Inteligencia generada.

---

## Capas principales

```mermaid
flowchart TB

A[Raw Layer]

A --> B[Processing Layer]

B --> C[Business Intelligence Layer]
```

---

## 7.1 Raw Layer

Contiene información sin modificar.

Tabla: **raw_ingestions**

```sql
CREATE TABLE raw_ingestions (
  id UUID PRIMARY KEY,
  source_type VARCHAR(50),        -- inbox_image | inbox_text | telegram_api | community
  source_reference TEXT,          -- canal o grupo de origen
  file_path TEXT,                 -- ruta a la evidencia original preservada
  content_hash VARCHAR(64),       -- deduplicación de capturas repetidas
  content_type VARCHAR(20),       -- image | text
  original_text TEXT,             -- texto pegado o transcrito
  processing_status VARCHAR(20),  -- pending | curated | discarded
  captured_at TIMESTAMP,
  created_at TIMESTAMP
);

CREATE UNIQUE INDEX idx_raw_ingestions_hash ON raw_ingestions (content_hash);
```

Características: solo lectura, auditoría completa, nunca se elimina.

---

## 7.2 Processing Layer

Datos extraídos y normalizados.

**products** – Catálogo maestro:

```sql
products
id
name
category
unit
aliases
```

Ejemplo:

| Campo    | Valor                 |
| -------- | --------------------- |
| name     | Pollo congelado       |
| aliases  | pollo, muslo, pechuga |
| category | alimentos             |

**prices_history** – Serie temporal principal:

```sql
prices_history
id
product_id
province_id
price
currency
quantity
source_id
confidence_score
created_at
```

Ejemplo:

| Fecha | Producto | Provincia | Precio  |
| ----- | -------- | --------- | ------- |
| 28/07 | Pollo    | Santiago  | 950 CUP |
| 29/07 | Pollo    | Santiago  | 980 CUP |

---

## 7.3 Intelligence Layer

Contiene resultados analíticos.

**forecasts**:

```sql
forecasts
id
product_id
province_id
prediction_date
expected_price
confidence
model_version
```

Ejemplo:

```json
{
  "product": "pollo",
  "province": "Santiago",
  "prediction": "1065 CUP",
  "confidence": "87%"
}
```

**availability_index**:

```sql
availability_index
product_id
province_id
score
trend
calculated_at
```

Ejemplo:  
Pollo, Santiago, Score: 32/100, Estado: Escasez

---

# 8. Producto Comercial y Modelo de Monetización

SAIMC adopta un modelo **Freemium B2B Intelligence**, donde la capa pública funciona como mecanismo de adopción, confianza y generación de datos, mientras que los productos premium convierten el conocimiento generado en valor económico.

**Principio económico rector:**

> Un precio actual responde "qué ocurre". Una predicción confiable responde "qué debo hacer". El segundo tiene mayor valor comercial.

**Nota estratégica:** La capa gratuita no es un "gasto" para atraer B2B. Es el **producto principal** para el usuario doméstico, y su uso masivo es lo que valida los datos y genera la comunidad que alimenta el sistema. El B2B es la consecuencia, no el origen.

---

## 8.1 Familia de productos

### Nivel 0 — Free Community (Público)

**Objetivo:** Construir audiencia, confianza y cobertura del mercado. **Resolver la necesidad diaria del hogar cubano con visuales intuitivos.**

**Usuarios:** Hogares, ciudadanos, estudiantes, investigadores independientes.

**Incluye:**
- Dashboard público con **mapa de calor de Cuba** por provincia.
- **Tarjetas de producto** con precio actual, tendencia y recomendación.
- **Semáforos visuales** (verde/amarillo/rojo) para tendencia.
- **Gráfico de líneas simple** (no velas).
- **Anuncios originales visibles** (diferenciador clave).
- Certificado de confianza (score visible).

**Limitaciones:** Historial limitado a 30 días. Sin modelos predictivos avanzados. Sin exportación masiva.

---

### Nivel 1 — Market Intelligence Reports

**Producto:** Informe Semanal de Inteligencia de Mercado Cubano.  
**Formato:** PDF descargable o correo electrónico.

Ejemplo:
```
SAIMC Market Report
Semana: 28 Julio 2026
Productos analizados: 250
Fuentes procesadas: 12.500

Principales alertas:
🥩 Pollo congelado
Tendencia: Incremento esperado +12%
Riesgo: Alto
Recomendación: Comprar antes del aumento esperado
```

**Clientes objetivo:** Importadores pequeños, comerciantes, consultores, agencias.  
**Precio:** **20-50 USD por reporte.**  
**Ventaja estratégica:** Permite validar disposición de pago rápidamente sin construir una plataforma compleja.

---

### Nivel 2 — Market Intelligence API

**Producto:** API REST para integración empresarial.  
**Clientes:** Startups, comerciantes digitales, desarrolladores.

**Características:**
- Datos históricos (hasta 2 años).
- Predicciones a 7, 14 y 30 días.
- Alertas configurables (webhooks).
- Índice de disponibilidad por provincia.

**Planes:**

| Plan         | Cliente      | Precio/mes | Incluye |
| ------------ | ------------ | ---------: | :--- |
| Developer    | Startups     |  $15       | 500 req/día, 1 año histórico |
| Professional | Comerciantes |  $50       | 5.000 req/día, predicciones |
| Business     | Empresas     | $250       | Ilimitado, alertas, soporte |

---

### Nivel 3 — Enterprise Intelligence

**Clientes:** Grandes importadores, distribuidores, organizaciones económicas.  
**Incluye:** Fuentes privadas, dashboards personalizados, análisis sectoriales, alertas específicas, modelos propios.  
**Precio:** **500 USD+/mes** (cotización según alcance).

---

## 8.2 Canales de cobro y estructura legal

Dada la **Orden Ejecutiva 14404** (mayo 2026), que amplía sanciones contra GAESA y CUPET y permite sancionar a entidades extranjeras que operen con Cuba, **SAIMC no operará como una entidad jurídica cubana para efectos de facturación internacional.**

**Modelo operativo recomendado:**

1. **Entidad offshore:** Una LLC en Estados Unidos, Europa o Latinoamérica (ej. México) es la que emite facturas y cobra a los clientes internacionales.
2. **Proveedores de pago:** Utilizar **Tropipay** (ya validado por El Toque Markets) para tarjetas internacionales, y **QvaPay** / **RevoluPay** para pagos locales o desde la isla.
3. **Transferencia de valor:** El trabajo de desarrollo y operación se realiza desde Cuba bajo un contrato de servicios B2B entre la entidad offshore y el equipo local (cumpliendo con la normativa cubana para trabajadores por cuenta propia).

---

## 8.3 Fuentes adicionales de ingresos

- **Venta de datasets históricos** (CSV/Parquet) a universidades e investigadores (ej. $100-$500 por dataset anual).
- **API White Label:** Empresas que integran SAIMC bajo su propia marca (precio a negociar).
- **Servicios de análisis:** Consultoría personalizada para cadenas de suministro.

---

# 9. Roadmap de Implementación Realista

El roadmap está diseñado para validar negocio antes de realizar grandes inversiones técnicas, **comenzando con un alcance mínimamente viable pero funcional.**

**La estrella de la Fase 1 será el usuario doméstico.** Su adopción diaria es la métrica que validará el producto antes de buscar grandes clientes.

---

## Fase 0 — Fundación del Sistema (Mes 1)

**Objetivo:** Construir la primera versión funcional con un alcance **extremadamente acotado** para evitar sobreingeniería.

**Alcance del MVP inicial:**
- **Modo de captura:** **Bandeja de entrada + cola de curaduría (Etapa 1)**. Sin automatización de fuentes.
- **Fuentes observadas:** anuncios reales de canales públicos, capturados por el operador como imagen o texto y depositados en el directorio de entrada.
- **Rutina objetivo:** captura oportunista durante el uso normal de las redes; aproximadamente una hora diaria de curaduría en bloque.
- **Productos:** **3-5 productos bandera** (ej. Pollo, Arroz, Aceite, Leche, Huevos).
- **Provincias:** **2 provincias** (La Habana y Santiago de Cuba).
- **Visualización:** Prototipo de mapa de calor simple y tarjetas de producto.
- **Validación:** Reglas 1, 2, 4 y 5.

**Actividades:**
- Backend FastAPI básico + PostgreSQL.
- Modelo de datos mínimo (`raw_ingestions`, `products`, `provinces`, `prices_history`).
- **Interfaz de curaduría** para carga manual con evidencia original.
- Motor de reglas en modo "entrenamiento" (propone, no decide).
- **Conjunto de referencia etiquetado** de 300+ anuncios.

**Resultado esperado:**
- 500 registros validados de 3–5 productos en 2 provincias.
- **Primera medición de precisión de extracción contra el conjunto de referencia.**

**Criterio de salida de la Fase 0:** no es "el código funciona", es **"H0 quedó respondida con un número"**. Si la precisión cae por debajo del 70%, la Fase 1 no arranca: se replantea la extracción.

---

## Fase 1 — Lanzamiento Público Inicial (Mes 2-3)

**Objetivo:** Demostrar utilidad para el usuario doméstico con una interfaz visual atractiva.

**Implementación:**
- Dashboard público con mapa de calor de Cuba.
- Tarjetas de producto con semáforos y recomendaciones.
- Expansión a 10 productos y 5 provincias.
- Lanzamiento del **SAIMC Market Report** (primer producto pagador a $20).

**Métrica de éxito principal:** **100 usuarios activos diarios** consultando el dashboard.

---

## Fase 2 — Comunidad y Automatización (Mes 4-6)

**Objetivo:** Escalar datos y abrir la ingesta comunitaria.

**Activación:** Botón "Subir dato" para usuarios.  
**Validation Engine:** Modo activo.  
**Expansión:** 50 productos, 10 provincias.

**Objetivos:** 5.000 datos/mes, 500 usuarios registrados, 90% validación automática.

---

## Fase 3 — Inteligencia Predictiva (Mes 7-12)

**Objetivo:** Convertirse en plataforma de inteligencia.

**Implementación:**
- Modelos Prophet/ARIMA.
- API pública (plan Developer a $15/mes).
- Lanzamiento del plan Professional ($50/mes).

---

# 10. Riesgos y Mitigaciones

---

## 10.1 Calidad de datos

**Riesgo:** Usuarios introducen información falsa.  
**Impacto:** Alto.  
**Mitigación:** Capas múltiples de validación + reputación + detección de anomalías.

---

## 10.2 Ataques dirigidos (DDoS)

**Riesgo:** El Toque Markets sufrió un ataque de **124 millones de peticiones en 3 horas**.  
**Mitigación:** Cloudflare (WAF + DDoS protection), rate limiting, geo-bloqueo.

---

## 10.3 Riesgo regulatorio y financiero

**Riesgo:** Orden Ejecutiva 14404.  
**Mitigación:** Modelo offshore (entidad fuera de Cuba factura y cobra).

---

## 10.4 Costos de inteligencia artificial

**Riesgo:** Uso intensivo de LLM.  
**Mitigación:** Estrategia híbrida (LLM solo para casos complejos, modelos propios con spaCy).

---

## 10.5 Dependencia de plataformas externas

**Riesgo:** Cambios en Telegram/Facebook.  
**Mitigación:** Arquitectura multi-fuente.

---

## 10.6 Términos de servicio y datos personales

**Riesgo:** La extracción automatizada de plataformas de terceros puede infringir sus términos de servicio, exponiendo al proyecto a bloqueo de cuentas, acciones legales o pérdida repentina de la fuente principal. Adicionalmente, los anuncios contienen **datos personales** (nombres de usuario, números de contacto, imágenes, ubicaciones aproximadas) cuyo almacenamiento y publicación tiene implicaciones de privacidad, especialmente si el proyecto factura a través de una entidad en jurisdicción con regulación de protección de datos.

**Impacto:** Alto. Puede invalidar la fuente de datos principal o comprometer la estructura legal descrita en la sección 8.2.

**Mitigaciones:**

1. **Orden de fuentes por riesgo creciente.** APIs oficiales y canales autorizados primero; aportes de comunidad segundo; cualquier otra fuente solo tras evaluación explícita.
2. **Seudonimización en la capa pública.** El identificador de usuario origen se almacena para el cálculo de reputación pero **no se publica**. La Transparency Layer muestra el anuncio y su contenido económico, no la identidad del anunciante.
3. **Minimización.** No se capturan ni almacenan números de teléfono, direcciones exactas ni datos de contacto: no aportan valor al modelo económico y concentran todo el riesgo.
4. **Consulta legal antes de la Etapa 3.** La transición a fuentes automáticas es el punto donde corresponde una revisión legal formal, no antes: en Etapa 1 y 2 el riesgo es marginal.

**Nota estratégica:** este riesgo es la segunda razón —además de la validación de H0— por la que el modelo de curaduría primero es la decisión correcta. Permite construir producto, tracción y evidencia **antes** de asumir exposición legal.

---

# 11. Anexo: Madurez del Forecast

| Nivel de madurez | Registros históricos | Precisión esperada | Etiqueta visible |
| :--- | :--- | :--- | :--- |
| **Baja** | < 50 | < 60% | "Predicción exploratoria" |
| **Media** | 50 - 500 | 60% - 80% | "Predicción en desarrollo" |
| **Alta** | > 500 | > 80% | "Predicción consolidada" |

---

# 12. Métricas de Éxito (KPIs)

## 12.1 Métricas de datos

| Métrica                |           Objetivo año 1 |
| ---------------------- | -----------------------: |
| Registros procesados   |               1.000.000+ |
| Productos monitoreados |                     500+ |
| Provincias cubiertas   | 16 + Isla de la Juventud |
| Fuentes activas        |                     100+ |

---

## 12.2 Métricas de calidad

| Métrica                         | Objetivo |
| ------------------------------- | -------: |
| Datos aprobados automáticamente |     >90% |
| Precisión validada              |     >95% |
| Duplicados eliminados           |     >80% |
| Score promedio confianza        |      >85 |

---

## 12.5 Métricas de extracción (H0) — las primeras que importan

Estas son las únicas métricas relevantes durante la Fase 0. Se calculan contra el conjunto de referencia etiquetado manualmente.

| Métrica | Definición | Umbral mínimo | Objetivo |
| :--- | :--- | ---: | ---: |
| Precisión de producto | Registros con producto correctamente identificado | 85% | >92% |
| Precisión de precio | Valor numérico y moneda correctos | 90% | >95% |
| Precisión de provincia | Ubicación correctamente asignada | 80% | >90% |
| Precisión de cantidad/unidad | Unidad de medida correcta y normalizada | 70% | >85% |
| **Extracción completa correcta** | **Los cuatro campos correctos en el mismo registro** | **70%** | **>85%** |
| Tasa de descarte | Anuncios sin información económica extraíble | — | <30% |

La métrica que decide es **extracción completa correcta**: un registro con precio correcto pero provincia equivocada no sirve para inteligencia geográfica, que es el núcleo de la propuesta.

**Nota sobre unidades:** en el mercado cubano un mismo producto se anuncia por libra, kilogramo, caja, paquete o unidad, frecuentemente sin especificar. La normalización de cantidad es previsiblemente la métrica más difícil y por eso tiene el umbral más bajo. Si resulta ser el cuello de botella, la respuesta correcta es acotar los productos bandera a aquellos con unidad de venta estable, no bajar el estándar.

---

## 12.3 Métricas comerciales y de adopción

| Métrica                    |           Objetivo |
| -------------------------- | -----------------: |
| Usuarios registrados       |             50.000 |
| Usuarios activos mensuales |             10.000 |
| Clientes premium           |                100 |
| Ingresos recurrentes       | $5.000-$10.000/mes |

---

## 12.4 Métricas de inteligencia

| Indicador                 |   Objetivo |
| ------------------------- | ---------: |
| Productos con forecasting |       300+ |
| Alertas generadas         | 10.000/mes |
| Precisión predictiva      |       >85% |

---

# 13. Stack Tecnológico y Estrategia de Escalabilidad

## 13.1 Decisión de stack (cerrada)

**Fase 0–1: Python de extremo a extremo. Un solo runtime, un solo despliegue.**

```
FastAPI + PostgreSQL + Jinja2/HTMX + MapLibre + Chart.js
```

| Capa | Elección | Razón |
| :--- | :--- | :--- |
| API y lógica de negocio | FastAPI | El ecosistema de extracción (regex, diccionarios, spaCy) y forecasting (Prophet, ARIMA, statsmodels) vive en Python. Partir el stack obligaría a comunicación entre procesos desde el día 1. |
| Base de datos | PostgreSQL (+ TimescaleDB cuando la serie temporal lo justifique) | Serie temporal, JSONB para datos crudos, búsqueda de texto. Una sola pieza cubre todo el MVP. |
| Interfaz de curaduría | Jinja2 + HTMX | Es una herramienta interna para un operador. No justifica una SPA. |
| Dashboard público (Fase 0–1) | Jinja2 + HTMX + MapLibre GL + Chart.js | El mapa de calor, las tarjetas y el gráfico de líneas descritos en la sección 6.7 no requieren un framework de aplicación. Renderizado en servidor da mejor rendimiento sobre conexiones lentas, que es el escenario real del usuario cubano. |
| Cola de trabajos | Ninguna en Fase 0. Redis + Celery en Fase 1. | Con curaduría manual no hay trabajo asíncrono que justificar. Introducir una cola antes de tener carga es sobreingeniería. |

**Angular queda diferido a Fase 2**, cuando el dashboard público necesite comportamiento de aplicación real (filtros complejos, estado compartido, PWA offline, notificaciones). En ese momento la API ya está definida y estabilizada, y el frontend se construye contra un contrato conocido en lugar de contra un backend en movimiento.

**Fundamento de la decisión.** Aunque la experiencia profesional del equipo fundador es más profunda en PHP/Angular que en Python, la naturaleza del problema —extracción de entidades en lenguaje natural y modelos de series temporales— ancla el sistema en Python de forma no negociable. Sostener dos stacks en paralelo siendo un solo desarrollador multiplica la superficie de mantenimiento sin aportar valor al usuario. La decisión correcta es **un runtime durante la fase de validación**, y reincorporar Angular cuando exista un producto validado que justifique el frontend especializado.

> Esta decisión debe registrarse como ADR en el repositorio (`docs/decisions/001-stack-python-unico.md`) junto con las alternativas descartadas.

---

## 13.2 Escala media

Migración: Redis → RabbitMQ → Kafka. Introducción de Angular para el dashboard público. Separación del worker de forecasting.

## 13.3 Escala avanzada

Kubernetes, procesamiento distribuido, data lake.

---

# 14. Solicitud de Inversión

## 14.1 Objetivo

Desarrollar el **SAIMC MVP — Market Intelligence Foundation**.

**Infraestructura:**
- Backend FastAPI.
- PostgreSQL + TimescaleDB.
- Dashboard Angular SSR con visualizaciones (mapas, semáforos, tarjetas).
- Motor de reglas y extracción de entidades.

---

## 14.2 Equipo inicial

- **Arquitecto/Desarrollador principal.**
- **Data Analyst / Market Research.**
- **Community & Data Operations.**
- **Especialista IA (fase posterior).**

---

## 14.3 Presupuesto estimado

### Escenario Lean (validación)

| Concepto              |              Estimado |
| --------------------- | --------------------: |
| Desarrollo software   |      $8.000 - $15.000 |
| Infraestructura cloud |       $100 - $300/mes |
| Herramientas IA/API   |       $100 - $500/mes |
| Diseño y UX (visual)  |       $1.000 - $3.000 |
| Operación inicial     |                $2.000 |
| **Total aproximado**  | **$12.000 - $25.000** |

### Escenario Growth: **$50.000 - $100.000**

---

## 14.4 Uso del capital

```mermaid
pie title Distribución inicial
"Desarrollo tecnológico" : 45
"Datos e inteligencia" : 25
"Infraestructura" : 15
"Marketing y comunidad" : 15
```

---

## 14.5 Hitos de liberación

**Hito 1:** 1.000 registros, 100 productos, 16 provincias.  
**Hito 2:** Dashboard operativo, primeros 100 usuarios activos.  
**Hito 3:** Primeros clientes pagando.  
**Hito 4:** Forecasting activo, API comercial.

---

# 15. Conclusión Estratégica

## SAIMC como infraestructura económica digital

Los mercados con alta incertidumbre no carecen necesariamente de información.  
Carecen de sistemas capaces de transformar información dispersa en conocimiento útil.

Cuba posee un ecosistema digital informal con millones de señales económicas diarias. SAIMC propone capturar ese conocimiento colectivo y convertirlo en una infraestructura permanente.

---

## La propuesta diferencial

SAIMC combina:

1. **Datos comunitarios** – Información generada desde el propio mercado.
2. **Validación automática** – Reglas configurables, reputación dinámica.
3. **Transparencia total** – Cada dato mantiene una historia verificable.
4. **Inteligencia predictiva** – Anticipar el futuro.
5. **Visualización intuitiva** – Mapas, semáforos y colores que cualquiera entiende en segundos.
6. **Modelo económico sostenible** – Capa pública genera confianza; capa profesional genera ingresos.

---

## El rol del usuario simple

El usuario doméstico no es un "espectador"; es el primer validador y la principal fuente de tracción. Al resolver su necesidad cotidiana ("¿cuándo compro aceite en Santiago?"), la plataforma demuestra su utilidad real, gana confianza y atrae la masa crítica que luego da valor a los servicios B2B.

---

## Declaración final

> **SAIMC no es una plataforma de precios. Es una infraestructura abierta de inteligencia económica que empieza en el hogar y escala hasta el mercado, transformando información informal en decisiones comerciales verificables para todos. Y lo hace con mapas, colores y visuales que saltan a los ojos.**

---

**Fin del Whitepaper – Versión 3.4**
