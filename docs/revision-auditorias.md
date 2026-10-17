# Revisión de las auditorías (carril 0 y carril 1)

Slice `revisar-auditorias`, **solo lectura**: no se tocó código, esquema ni datos.
Cifras tomadas el **2026-10-17** (≈12:06 UTC) con `SELECT` y con las propias
funciones del repo (`lane1.audit_state`, `lane0.audit_bounds`) contra la base
de este host. Corresponde a la tarea T9 de `docs/plan-bloque-mvp.md`.

Alcance leído: `validation/lane0.py`, `validation/lane1.py`, `automation.py`,
`alerting.py`, la ruta `/curation/auditoria` (`web/routers/curation.py`) y las
entradas de `docs/decisions.md` del 2026-10-13 y del 2026-10-16.

## Resumen

1. **Error auditado del carril 1 desde el reinicio (`.env`, 2026-10-15): 0 errores
   en 60 auditados**, cota superior 4,9 % contra meta de 10 %; el freno está
   apagado. Carril 0: 0 falsos descartes en 60 textos y 0 en 30 imágenes.
2. **Ese 0/60 depende de la fecha y del criterio de «error», no solo de la
   calidad del carril.** Con el reinicio en 2026-10-14 daría 10/60 (cota
   superior 26,6 %, freno puesto). Los 10 son correcciones del mismo tipo
   (completar `format_id`) hechas el 10-14; el 10-15 se dio «Está bien» a 26 de
   104 filas que también tenían el formato vacío. Falta una regla escrita.
3. **La discrepancia de fecha ya está resuelta** (decisions.md, 2026-10-16: manda
   el `.env`, 10-15). Lo que no se resolvió es lo del punto 2.
4. **Los dos «No era un anuncio válido» comparten causa**: mismo canal, misma
   línea («Aceite COAMO caja de 20 u (3.25 usd)») dentro de una lista mezclada, y
   un precio ambiguo entre pomo y caja que el modelo lee distinto cada vez.
   Aparecieron **otros 4 anuncios con esa misma línea**, ya sin filas.
5. **Telegram: avisar sí conviene, pero hoy no hay nada que avise**: este host
   no tiene `SAIMC_TELEGRAM_BOT_TOKEN`/`CHAT_ID` en el `.env` ni el temporizador
   `saimc-alerts`. Y hay 225 aceptados sin auditar que el contador del propio
   carril muestra como 0.

## 1. Error auditado posterior al reinicio

### Estado según el código

| Reinicio (`since`) | Auditados contados | Errores | Cota inf. | Cota sup. | Freno |
|---|---|---|---|---|---|
| **2026-10-15 (`.env`, vigente)** | 60 | 0 | 0,0 % | 4,9 % | apagado |
| 2026-10-14 (decisión original) | 60 | 10 | 9,3 % | 26,6 % | **puesto** |
| 2026-10-13 / sin fecha | 60 | 2 | 0,6 % | 10,1 % | puesto |

Los 60 contados son los primeros 60 auditados desde la fecha (`AUDIT_N0`): en el
arranque cuenta todo lo que se audita, sin muestra al azar.

### Todo el historial auditado del carril 1 (198 respuestas)

`ok` 174 · `corrected` 22 · `invalid` 2 (del 10-10 al 10-15). Las 22
correcciones, leídas del evento `edited` anterior a cada respuesta:

| Tipo de corrección | n | Qué cambió |
|---|---|---|
| Solo completar compra mínima | 1 | ya no cuenta como error (decisión 10-13) |
| Solo `format_id` vacío → completado | 11 | 10 el 10-14 (07:59–08:04), 1 el 10-13 |
| Solo `format_id` cambiado a otro | 5 | 10-13, 19:50–20:00 |
| Cambio de base de precio y formato | 1 | `unit` → `total` |
| Cantidad/unidad corregida y fila duplicada borrada | 3 | 2 lb → 1 kg |
| Precio corregido (10,70 → 1,07 USD) + formato | 1 | error de decimal |

Según qué se llame «error», sobre 198 auditados (no es muestra al azar, ver §2):

| Criterio | Errores | Tasa | IC 95 % (Clopper–Pearson) |
|---|---|---|---|
| Estricto: cualquier cambio salvo compra mínima (el del código hoy) | 23 | 11,6 % | 8,1 – 16,1 % |
| A: completar un formato vacío **no** cuenta (como la compra mínima) | 12 | 6,1 % | 3,5 – 9,6 % |
| Solo cambios sustantivos (precio, cantidad, base, filas) + inválidos | 7 | 3,5 % | 1,7 – 6,5 % |

Con el criterio A la ventana 10-14 también da 0 errores en 60: **la discrepancia
10-14 vs 10-15 deja de importar**.

### Por qué el formato no es un detalle

`cells.py` usa `format_id` como eje de la celda: una fila sin formato se agrega
«en su propia celda igual que `format_id = NULL`» (no contamina otra), pero un
formato **cambiado** sí mueve el precio a otra celda. Por eso completar un
vacío se parece a la compra mínima (no cambia ningún precio publicado) y
cambiar un formato por otro no.

## 2. Discrepancia `SAIMC_LANE1_AUDIT_SINCE`

- `.env`: `SAIMC_LANE1_AUDIT_SINCE=2026-10-15`.
- `decisions.md`: la entrada con título «2026-10-13 — La medición del error del
  carril 1 se reinicia el 2026-10-14». (Está fechada 10-13; la fecha 10-14 es la
  del reinicio. Conviene citarla por título.)
- `decisions.md`, **2026-10-16**: «manda la del `.env` (2026-10-15)», con el
  motivo «no mover la medición hacia atrás sin motivo». **Ya estaba resuelta**;
  esta revisión usa 10-15 como pide esa entrada.

Pero esa entrada no miró qué había entre el 10-14 y el 10-15: los 10 `corrected`
del 10-14. Mover el reinicio un día los saca de la cuenta. No es una acusación
de intención: el 10-15 era además el día en que se hicieron las 60 auditorías
(§3). Pero **es el cambio que levantó el freno**, y `/metodologia` y `/control`
lo publican como 0 errores en 60.

### Sesgos de los 60 contados (consultado, no supuesto)

- Los 60 `ok` del 10-15 son **todos del lote de 333 aceptados del 10-13**
  (04:24 UTC). Se auditaron del más viejo al más nuevo (así ordena la cola de
  arranque), no al azar.
- Se hicieron en 17 minutos (13:35–13:52 UTC): ≈17 s por anuncio.
- De sus 104 filas, **26 (25 %) quedaron con `format_id` vacío**. El 10-14 la
  misma situación contó como corrección.
- Uno (`fcd8a195`) **no tiene ninguna fila** en `prices_history` y recibió `ok`.

## 3. Los dos «No era un anuncio válido» (`8cb31516`, `230a3abb`)

Ambos del canal **EL BODEGÓN DOBLE VÍA**, `invalid` el 10-13 a las 01:05–01:06
UTC, descartados con `no_data`. Son listas mezcladas (aceite, cabilla,
diésel/gasolina por tanque, cerveza, harina). Lo único que extrajo el modelo de
cada una es una fila de **Aceite** a 3,25 USD, desde la línea:

> Aceite COAMO  caja de 20 u (3.25 usd)

Mismo modelo y mismo prompt (`2026-09-27.1+glossary:f2b535ff`), mismo texto de
la línea, **dos lecturas distintas**: `8cb31516` → 1 unidad, base `unit`;
`230a3abb` → 20 unidades, base `total` (3,25 USD por caja, ≈0,16 USD el pomo).
Otros anuncios del canal aclaran que 3,25 es «c/u»: «2 400 CUP el pomo /
48 000 CUP la caja (3.25 c/u)». La línea no trae volumen (900 ml) ni dice si el
precio es del pomo o de la caja.

**Causa común:** línea de precio ambigua (pomo vs. caja, sin volumen) dentro de
un anuncio mixto de un canal mayorista de insumos; el motor la acepta porque la
cita se verifica y el precio no cae fuera de banda. Es un tipo de anuncio, no
un fallo aislado.

Qué más hay con esa línea (consultado):

| Anuncio | Estado | Filas hoy |
|---|---|---|
| `fcd8a195` (10-05) | aceptado 10-13, auditado **`ok` el 10-15** | **0** |
| `ab2868dc` (10-06) | aceptado 10-13, sin auditar | **0** |
| `505ca23d` (10-08) | aceptado 10-13, sin auditar | **0** |
| `409d1213` (10-09) | aceptado 10-13, sin auditar | **0** |

Cuatro anuncios `curated` con `row_count: 1` en su evento y sin filas, y un
quinto `curated` sin filas de agosto (`d6a708fb`, sin eventos). Los tres
caminos de la app que borran filas (`lane1.mark_invalid`, `POST
/curation/{id}/delete` y la edición) dejan evento (`audited`, `deleted`,
`edited`); estos cuatro solo tienen el `curated/auto` y, uno, el `ok`.
**No averigüé quién las borró** (hipótesis sin verificar: limpieza manual por SQL
del operador tras los dos `invalid`). Sea quien sea, dejó filas `curated` sin
filas y un `ok` sobre un anuncio vacío que cuenta como acierto.

No verifiqué si el `invalid` de auditoría suma a la regla 4 (canal sucio) del
canal. Si no suma, el canal sigue aceptando la misma línea cada día.

## 4. Carril 0

- Texto: 0 rescatados en 60 auditados, cota superior 4,9 % (meta 10 %).
- Imagen: 0 en 30, cota superior 9,5 % (meta 20 %).
- Semanas W40 y W41 cerradas y auditadas al 100 % (30/30 textos, 15/15
  imágenes cada una). La W42 lleva 556 textos y 372 imágenes descartados solos;
  su muestra se abre el lunes 2026-10-19.
- La cota de Jev (`jev_bound`) está en n = 0: `SAIMC_LANE0_JEV_ENABLED` está
  apagado por defecto, así que no hay nada que medir todavía.
- Lo observado no pide cambios. Una nota sin medir: `/curation/auditoria` llama
  cuatro veces a `_population`, que lee todos los descartes automáticos; hoy
  son ~2.300 y no se nota.

## 5. `automation.py` y lo que se publica

`automation_summary` toma `lane1.audit_state` tal cual: lo que muestran
`/control` y `/metodologia` es el 0/60 del §1. `provisional` solo se prende con
`audited == 0`, así que **ya no se rotula provisional**. Sin una definición
escrita de «error» y sin decir que los 60 salieron del lote del 10-13 en orden
de antigüedad, la cifra pública afirma más de lo que mide.

## 6. Avisar por Telegram cuando faltan auditorías

**Riesgo vigente** (decisions.md 2026-10-13): sin tope de aceptados sin auditar
(`MAX_UNAUDITED = None`) y con el freno solo por evidencia, **si nadie audita el
freno no salta**. Pasado `AUDIT_N0`, además, `AuditState.unaudited` cuenta solo la
muestra de semanas cerradas: hoy da 0 con **225 aceptados sin auditar** (el más
viejo, del 10-13 04:24).

Estado del aviso:

- `alerting.py` ya recorre la franja de `/control` y avisa los ámbar/rojo; del
  carril 1 solo avisa `FRENADO`. No hay aviso de «falta auditar».
- En este host el `.env` no tiene `SAIMC_TELEGRAM_BOT_TOKEN` ni
  `SAIMC_TELEGRAM_CHAT_ID` y `systemctl list-timers` no lista `saimc-alerts`
  (solo `prefill` y `backup`). Sin eso cualquier aviso nuevo no sale. Falta
  confirmar si en el VPS de producción es igual.

**Evaluación:** sí conviene, con dos condiciones.

1. Avisar sobre lo que *toca* auditar, no sobre el total. Los 225 son permanentes
   y se pegarían en ámbar para siempre (el mismo error que llevó a quitar
   «Pendiente más viejo», decisions.md 2026-10-13). Dos señales útiles:
   - muestra de una semana ya cerrada con respuestas pendientes pasadas 48 h
     del lunes (la medición se está quedando sin datos);
   - en la fase de arranque, auditados < 60 con el más viejo sin auditar > 3 días.
2. Que sea un indicador de la franja de `/control` (como «Últimas 24 h»), no un
   caso especial en `alerting.py`: así lo ve el operador y Telegram lo hereda.

Descartado por ahora: **frenar** el carril cuando la muestra queda sin
auditar. Corta el flujo que el operador pidió no cortar; mejor avisar primero y
decidir el freno si el aviso se ignora.

## 7. Cambios propuestos

Nada de esto está hecho. Orden por urgencia; modelo sugerido según el criterio
de tareas.

| # | Cambio | Quién decide | Tamaño | Modelo |
|---|---|---|---|---|
| 1 | **Definir «error» para el formato** y registrarlo en `decisions.md`. Recomiendo A: formato vacío → completado no cuenta (precedente de la compra mínima); cambiar un formato por otro, sí. Con A, el 10-14 vs 10-15 deja de importar. Antes del lunes 10-19 (cierra la W42, 30 anuncios al azar). | Operador | decisión + ~20 líneas en `_reclassify_minimum_only` | opus (criterio) / sonnet (código) |
| 2 | Decidir qué hacer con los 5 `curated` sin filas y con el `ok` de `fcd8a195`: restaurar las filas, descartar los anuncios, o dejarlos. Averiguar quién las borró. | Operador | S | sonnet |
| 3 | `mark_ok` no acepta anuncios sin filas; `/control` o la auditoría cuentan «curados sin filas». | — | S | sonnet |
| 4 | La cola de arranque de la auditoría se ordena al azar (semilla fija) en vez de por antigüedad, o al menos se rotula que la fase de arranque no es muestra. | Operador | S | sonnet |
| 5 | Mostrar aparte el **total** de aceptados sin auditar (hoy 225) en `/curation/auditoria` y en `/control`; indicador de la franja del §6. | — | M | sonnet |
| 6 | Poner en marcha el aviso: token y chat en el `.env`, instalar `saimc-alerts.timer`, probar en seco. | Operador | S (operativo) | — |
| 7 | La línea ambigua del Bodegón (caja/pomo sin volumen): que el carril 1 se abstenga cuando `packaging` es caja, `quantity` > 1 y la base `total` da un precio unitario fuera de la banda del producto; y revisar si el `invalid` cuenta contra el canal. Toca el motor de validación: pide el flujo de especificación completo. | Operador | M | opus |
| 8 | Al presentar el error auditado, decir que la medición se reinició, que los 60 son del lote del 10-13 en orden de antigüedad y qué criterio de «error» rige (ya lo pedía la entrada del 10-13). | Operador | texto | sonnet |

Orden sugerido: 1 → 2 → 6 → 5 → 3 → 4 → 8; el 7 es otro slice.

## 8. Cómo reproducir

Todo es de lectura (`default_transaction_read_only`), contra la base de este host:

```sql
-- veredictos del carril 1, por día
select decided_at::date d, detail->>'verdict' v, count(*)
from curation_events
where action='audited' and detail->>'lane'='1' group by 1,2 order by 1,2;

-- filas vacías en curados
select r.id, r.source_reference from raw_ingestions r
where r.processing_status='curated'
  and not exists (select 1 from prices_history h where h.source_id=r.id);
```

Las cotas salen de `saimc.validation.stats.clopper_pearson_*`; el estado del
carril, de `lane1.audit_state(session, settings.model_copy(update={"lane1_audit_since": <fecha>}))`.

## 9. Fuera de alcance / sin verificar

- No se verificó en el VPS de producción (si es otro host): `.env`, temporizadores,
  ni si coincide la base.
- No se vieron las reglas del motor que dejaron pasar a `8cb31516`/`230a3abb`.
- No se miró cuántos aceptados sin auditar traen la línea del Bodegón u otras
  ambiguas fuera de ese canal.
