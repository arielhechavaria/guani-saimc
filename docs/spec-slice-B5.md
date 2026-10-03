# Slice B5 — Emisor de grupo como hash con clave

Parte del plan `docs/plan-factor-humano.md`. Toca esquema (migración 0029): esta
especificación se aprueba antes de programar. No toca el motor de validación.

Implementa lo que `spec.md` §10.6 ya pide (mitigación 2: el identificador de
usuario origen se almacena para reputación y no se publica) sin romper la
mitigación 3 (no se guardan teléfonos).

## Problema

1. En un grupo de WhatsApp, `from` es el JID del grupo. El emisor viaja aparte, en
   `participant`, y hoy se descarta (`ingestion/whatsapp_backfill.py`,
   `parse_group_history`). No hay forma de saber si dos anuncios son del mismo
   vendedor.
2. Guardarlo en claro viola §10.6: el JID suele ser el número de teléfono.
3. Un hash simple tampoco sirve: un móvil cubano tiene ocho dígitos, y recorrer
   todos los números posibles contra un SHA-256 lleva minutos. Hace falta una clave.
4. Sin emisor, la regla 5 (duplicado) compara por canal y marcó 92 de 368 filas
   (25%) en la primera revalidación, y la regla 4 no puede ser por vendedor.

## Qué se construye

### Columna `raw_ingestions.sender_hash`

| Columna | Tipo | Nota |
| --- | --- | --- |
| `sender_hash` | varchar(64), nulo | HMAC-SHA256 en hexadecimal; con índice |

- La escribe la ingesta, una sola vez, junto con el resto de la fila. No hay camino
  de escritura posterior ni backfill: `raw_ingestions` es inmutable. Es el mismo
  precedente que `channel_id` (`decisions.md`, 2026-08-19).
- Las filas anteriores quedan en nulo para siempre.
- Nulo también cuando no hay emisor (canales de difusión, archivos de la bandeja,
  texto pegado) o cuando no hay clave configurada.

### El hash

- `HMAC-SHA256(clave, emisor normalizado)`, en hexadecimal.
- **Clave:** `SAIMC_SENDER_HASH_KEY` en `.env`, de al menos 32 caracteres. Más corta
  se trata como ausente y se avisa en el log al arrancar. Se genera con
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Normalización:** minúsculas, sin el sufijo de dispositivo (`:3`), y
  `@s.whatsapp.net` igualado a `@c.us`, para que el mismo número dé el mismo hash
  venga del webhook o del backfill.
- **Global, no por grupo:** el mismo vendedor en dos grupos da el mismo hash. Es lo
  que permite ver un reenvío entre grupos.

### Dónde se calcula

Dentro de `parse_message`, que recibe la clave. `ParsedMessage` lleva `sender_hash`
y nunca el JID: el identificador en claro no sale del parser, así que no puede
llegar a un log, a un archivo de la bandeja ni a la base.

- El emisor se lee de `participant`, solo cuando `from` es un grupo (`@g.us`).
- El webhook y el backfill de grupos pasan el hash a `ingest_file`, igual que hoy
  pasan `channel_id`.

### Privacidad (`spec.md` §10.6)

- El JID del emisor no se guarda, no se registra en ningún log y no se escribe en el
  archivo de evidencia.
- El hash no se expone: ninguna plantilla, respuesta JSON ni exportación lo incluye.
  Tampoco el `__repr__` de `RawIngestion`.
- La clave no se registra en logs ni aparece en `/control`.

## Límites conocidos

- **Rotar la clave corta la continuidad:** los hashes nuevos no coinciden con los
  viejos y el historial por vendedor empieza de cero. No se guarda un identificador
  de clave; si algún día se rota, se registra la fecha en `decisions.md`.
- **Perder la clave es lo mismo que rotarla.** Va en el respaldo de `.env`.
- **Un mismo vendedor puede dar dos hashes** si WhatsApp lo identifica una vez por
  número (`@c.us`) y otra por identificador interno (`@lid`). No se puede unir sin
  guardar la correspondencia, y no se guarda.
- **Texto idéntico de dos vendedores:** `content_hash` es único, así que el segundo
  mensaje con el mismo texto no entra y la fila conserva el emisor del primero.
- **El nombre del campo:** `participant` es lo que documenta el código
  (verificado contra la instancia el 2026-08-18 para el histórico). Para el webhook
  se confirma en la verificación del operador, paso 3.

## Qué no entra

- Usar el emisor en la regla 5 o en la regla 4: toca el motor y lleva su propia
  especificación. Es el pendiente suelto de la regla 5.
- Mostrar el emisor, o un seudónimo, en cualquier pantalla.
- Reputación por vendedor.
- Completar las filas viejas.

## Tests primero

Casos definidos acá, antes del código:

1. El mismo JID con la misma clave da el mismo hash, de 64 caracteres hexadecimales.
2. Otra clave da otro hash.
3. `5355512345@c.us`, `5355512345@s.whatsapp.net` y `5355512345:3@s.whatsapp.net`
   dan el mismo hash. `5355512345@lid` da otro.
4. Sin clave, o con una de menos de 32 caracteres, `sender_hash` es nulo y el
   mensaje se ingiere igual.
5. Un mensaje de grupo por el webhook deja la fila con su `sender_hash`.
6. Un mensaje de un canal de difusión, sin `participant`, deja `sender_hash` nulo.
7. El backfill de un grupo deja el `sender_hash` en cada fila.
8. El JID del emisor no aparece en ninguna columna de `raw_ingestions`, ni en el
   archivo guardado en el almacén, ni en los logs capturados durante la ingesta.
9. `repr(ParsedMessage)` y `repr(RawIngestion)` no contienen el JID ni el hash.
10. Dos mensajes del mismo emisor en dos grupos distintos comparten `sender_hash`.
11. La migración sube y baja.

## Verificación del operador

1. `pg_dump`, push, `alembic upgrade head`.
2. Generar la clave, agregar `SAIMC_SENDER_HASH_KEY` al `.env` del VPS, guardarla
   también en el respaldo, y reiniciar `saimc.service`.
3. Esperar un mensaje nuevo de un grupo y correr:
   `SELECT count(*), count(sender_hash) FROM raw_ingestions WHERE created_at > now() - interval '1 hour';`
   Las filas de grupos tienen hash. Si todas quedan en nulo con la clave puesta,
   WAHA manda el emisor en otro campo: avisar antes de seguir.
4. `SELECT sender_hash, count(*) FROM raw_ingestions WHERE sender_hash IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5;`
   muestra hashes repetidos: son vendedores que publican más de una vez.
5. `journalctl -u saimc.service --since "1 hour ago" | grep -c "@c.us"` no cuenta
   ningún JID de persona; solo aparecen los de grupo (`@g.us`).
6. **Lo que no tiene que pasar:** un número de teléfono en `raw_ingestions`, el hash
   en una pantalla, o una ingesta que falle por no tener clave.

## `decisions.md`

Sí: el emisor se guarda como HMAC con clave, global entre grupos, nulo sin clave; y
rotar o perder la clave corta la continuidad del historial por vendedor.
