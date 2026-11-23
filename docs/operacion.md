# Operación: tareas programadas, avisos fuera de la aplicación y vigilancia

Esta guía cubre tres cosas:

1. cómo programar las tareas diarias de forma que quede constancia de cada ejecución;
2. cómo recibir por correo o webhook los avisos que hoy solo se ven en la web;
3. cómo enterarse de que una tarea **no** ha corrido o ha fallado.

Todo se maneja con un único comando:

```bash
uv run python -m asistente.plataforma.cli.ops --help
```

| Subcomando | Qué hace |
|---|---|
| `run <nombre> -- <comando...>` | Ejecuta el comando bajo vigilancia: apunta el resultado y avisa al *healthcheck*. |
| `deliver` | Envía por los canales configurados los avisos que aún no han salido. |
| `doctor` | Revisa que las tareas han corrido y han terminado bien; avisa de las que no. |
| `formatos …` | Vigila si los formatos externos (BOE, boletines, documentos) siguen entendiéndose; ver la sección 6. |

## 1. Líneas de cron

Cada tarea se envuelve con `ops run <nombre> --`. El nombre es el que usa `doctor`
para saber qué tareas espera (`run-daily`, `boe-watch`, `boe-impact`, `boe-novedades`, `deliver`).

```cron
# Variables: cron no carga .envrc ni el perfil de la shell. Defínelas aquí o en un
# fichero que solo pueda leer el usuario del servicio y que cada línea cargue antes.
SHELL=/bin/bash

# Normas del catálogo que han cambiado en el BOE, y aviso a los usuarios afectados.
30 6 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run boe-watch -- uv run python -m asistente.plataforma.cli.boe_watch >> logs/boe_watch.log 2>&1
45 6 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run boe-impact -- uv run python -m asistente.plataforma.cli.proactive boe-impact --changes-log data/html/_boe_changes.jsonl --snapshot-dir data/html --yes >> logs/proactive.log 2>&1

# Normas nuevas con contenido tributario en el BOE de cada día.
50 6 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run boe-novedades -- uv run python -m asistente.plataforma.cli.proactive boe-novedades >> logs/proactive.log 2>&1

# Piloto de parámetros: normas que modifican leyes de las que salen parámetros → grupos
# afectados → verificación de todos los ficheros → recodificación (si hay clave de LLM) →
# anotación de la lectura de las leyes que quedan al día → parte al operador. Escribe en
# asistente/ambitos/fiscal/data/ (fechas de lectura; y lo aplicado, con RECODE_AUTO_APPLY=true).
# --no-detect porque la línea anterior ya ha leído el BOE del día; sin ella, quita la opción
# y el piloto lo lee él mismo. Ver el apartado 5.
55 6 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run params-autopilot -- uv run python -m asistente.plataforma.cli.proactive params-autopilot --no-detect >> logs/proactive.log 2>&1

# Recordatorios de plazos, plan de fin de ejercicio y avisos de los módulos del hogar activados.
0 7 * * *   cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run run-daily -- uv run python -m asistente.plataforma.cli.proactive run-daily >> logs/proactive.log 2>&1

# Índices de referencia de los préstamos hipotecarios (módulo hipoteca): resoluciones del Banco de España
# publicadas en el BOE, los días 4 y 19 de cada mes. Escribe en asistente/ambitos/hipoteca/data/
# (revísalo y súbelo al repositorio). Para que doctor lo vigile: «--max-horas hipoteca-indices=400».
20 6 4,19 * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run hipoteca-indices -- uv run python -m asistente.ambitos.hipoteca.indices actualizar >> logs/proactive.log 2>&1

# Resumen semanal de cada caso (lo que vence, importes, acciones por decidir), los lunes.
# Para que doctor lo vigile: «ops doctor --max-horas run-weekly=192».
30 7 * * 1  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run run-weekly -- uv run python -m asistente.plataforma.cli.proactive run-weekly >> logs/proactive.log 2>&1

# Vigilancia de los datos legales (apartado 4), los lunes; formatos-probe, de lunes a viernes (apartado 6).
# params-check, params-verify y formatos-probe terminan con error si encuentran algo que revisar, y doctor lo avisa.
5 7 * * 1   cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run params-check -- uv run python -m asistente.plataforma.cli.proactive params-check --fail-on-stale >> logs/proactive.log 2>&1
15 7 * * 1  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run params-verify -- uv run python -m asistente.plataforma.cli.proactive params-verify --fail-on-mismatch --no-anotar-lecturas >> logs/proactive.log 2>&1
25 7 * * 1  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run normas-check -- uv run python -m asistente.plataforma.cli.proactive normas-check >> logs/proactive.log 2>&1
45 7 * * 1-5  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run formatos-probe -- uv run python -m asistente.plataforma.cli.ops formatos probe --fail-on-problem >> logs/ops.log 2>&1

# Bus de eventos, solo si NO tienes el trabajador en marcha continua (ver «Bus de eventos»):
# una pasada cada 5 minutos (temporizadores, reparto y entregas pendientes).
*/5 * * * * cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.eventos worker --una-vez >> logs/eventos.log 2>&1

# Entrega de los avisos generados arriba. Se registra sola con el nombre «deliver»;
# envuélvela con «ops run deliver --» solo si quieres además su ping al healthcheck
# cuando la variable genérica HEALTHCHECK_URL no está definida (ver apartado 3).
15 7 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops deliver >> logs/ops.log 2>&1

# Revisión: ¿ha corrido todo? Una vez al día (cada ejecución con problemas envía un mensaje).
0 9 * * *   cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops run doctor -- uv run python -m asistente.plataforma.cli.ops doctor >> logs/ops.log 2>&1
```

Detalles de `ops run`:

- Lo que va detrás de `--` se ejecuta tal cual, sin shell intermedia: no admite tuberías ni
  redirecciones (las de la línea de cron se aplican al conjunto, como en el ejemplo).
- No toca la salida del comando y termina con su mismo código de salida.
- Si el comando no existe o no se puede lanzar, también queda apuntado como fallo.
- Las opciones propias (`--state-path`) van **antes** de `--`.

## 2. Avisos fuera de la aplicación (`ops deliver`)

Las tareas proactivas guardan los avisos en SQLite y la web los muestra. `ops deliver`
los envía además por los canales que estén configurados.

### Qué hace

- Un mensaje por aviso y por canal. Asunto: el título del aviso. Cuerpo: el texto del aviso
  en texto plano (sin las marcas `**`), los pasos a seguir, las fuentes y el identificador del caso.
- **Nunca envía dos veces** el mismo aviso por el mismo canal: cada entrega se apunta en la
  tabla `notification_deliveries` (misma base de datos que los avisos) solo después de que el
  canal la acepte. Si un envío falla, se reintenta en la siguiente ejecución.
- Los canales son independientes: si el correo falla, el webhook recibe igualmente todo.
  Tras tres fallos seguidos de un canal, se deja ese canal para la siguiente ejecución.
- Imprime un resumen en JSON y termina con código 1 si algún envío ha fallado.
- Sin ningún canal configurado no hace nada, lo dice y termina con código 0.

### Qué no hace

- **No envía a cada usuario.** Todavía no hay cuentas de usuario y los casos no tienen
  correo propio: todos los avisos de todos los casos van al único destinatario configurado
  (el propietario u operador de la instalación). Está pensado para uso personal o de un solo
  despacho.
- No marca los avisos como leídos en la web; son registros distintos.
- No agrupa varios avisos en un solo mensaje ni filtra por gravedad.
- No retira lo ya enviado si un aviso se sustituye por otro más reciente (por ejemplo, un
  plan de fin de ejercicio recalculado): el nuevo se envía como un aviso más.
- La entrega se apunta por nombre de canal (`webhook`, `email`), no por destino: si cambias la
  URL o la dirección, lo ya entregado no se vuelve a enviar al destino nuevo.

### Primera activación

Si la base de datos ya tiene avisos antiguos, la primera ejecución los enviaría todos.
Para darlos por entregados sin enviarlos:

```bash
uv run python -m asistente.plataforma.cli.ops deliver --marcar-entregadas
```

### Variables de entorno

Todas son opcionales; vacía = canal apagado. Los secretos (la URL del webhook, la contraseña
SMTP) van solo en el entorno, nunca en el código ni en el repositorio.

| Variable | Para qué | Por defecto |
|---|---|---|
| `NOTIFY_WEBHOOK_URL` | URL `http(s)` a la que se hace POST de cada mensaje. | vacía |
| `NOTIFY_WEBHOOK_FORMAT` | `json` o `text` (ver abajo). | `json` |
| `NOTIFY_SMTP_HOST` | Servidor SMTP. | vacía |
| `NOTIFY_SMTP_PORT` | Puerto. Con 465 se usa TLS implícito. | `587` |
| `NOTIFY_SMTP_USER` | Usuario. Vacío = sin autenticación. | vacía |
| `NOTIFY_SMTP_PASSWORD` | Contraseña (o contraseña de aplicación). | vacía |
| `NOTIFY_SMTP_STARTTLS` | STARTTLS antes de autenticarse. | `true` |
| `NOTIFY_EMAIL_FROM` | Remitente. | vacía |
| `NOTIFY_EMAIL_TO` | Destinatario (uno). | vacía |
| `DECISION_STATE_DB_PATH` | Base de datos de casos y avisos (ya existente). | `./data/decision_state.sqlite3` |

El correo necesita a la vez `NOTIFY_SMTP_HOST`, `NOTIFY_EMAIL_FROM` y `NOTIFY_EMAIL_TO`. Si
solo hay alguna, `deliver` falla con un mensaje que dice cuál falta, en lugar de callar.

### Formato del webhook

- `json` (por defecto): `POST` con `Content-Type: application/json` y cuerpo
  `{"title": "<asunto>", "text": "<cuerpo>"}`. Sirve para webhooks entrantes compatibles con
  Slack (Slack, Mattermost, Rocket.Chat…), que muestran `text`, y para receptores genéricos,
  que reciben ambos campos. En los compatibles con Slack el título no se muestra.
- `text`: `POST` con el cuerpo en `text/plain` y el asunto en la cabecera `Title`. Es lo que
  espera un tema de ntfy.sh (`NOTIFY_WEBHOOK_URL=https://ntfy.sh/<tema>`).

## 3. Saber que una tarea no ha corrido o ha fallado

Hay dos mecanismos y hacen falta los dos.

### El fichero de estado y `ops doctor`

Cada tarea envuelta con `ops run` (y `ops deliver`) apunta en `data/_jobs_state.json`
(`JOBS_STATE_PATH`) su última ejecución, el último éxito y el último error:

```json
{
  "jobs": {
    "boe-novedades": {
      "last_run": "2026-11-12T05:50:04+00:00",
      "last_ok": true,
      "last_detail": "",
      "last_success": "2026-11-12T05:50:04+00:00",
      "last_error": "2026-11-09T05:50:03+00:00",
      "last_error_detail": "código de salida 1"
    }
  }
}
```

`ops doctor` lee ese fichero y señala:

- tareas esperadas que no se han ejecutado nunca;
- tareas cuyo último éxito es demasiado antiguo;
- tareas cuya última ejecución falló (cualquiera que figure en el fichero).

Imprime los problemas en JSON, los envía por los canales de `NOTIFY_*` con el asunto
«HaciendaGPT: tarea programada con problemas» y termina con código 1 si hay alguno.

Por defecto espera, con un máximo de 36 horas desde el último éxito: `run-daily`,
`boe-novedades`, `boe-impact`, `boe-watch` y `deliver`. Para cambiarlo:

```bash
# Otra antigüedad, una tarea más, y dejar de exigir una que no usas.
uv run python -m asistente.plataforma.cli.ops doctor \
    --max-horas boe-watch=72 --max-horas mi-tarea-semanal=200 --max-horas boe-impact=0
```

`--no-notify` solo imprime, sin enviar nada.

### El interruptor de hombre muerto (*healthcheck*)

`doctor` es otra tarea de cron. Si lo que falla es cron (la máquina está apagada, se ha
perdido el crontab, el disco está lleno), `doctor` tampoco corre y nadie avisa. La única
forma de enterarse de que algo **no** ha ocurrido es que lo vigile alguien de fuera: un
servicio que espera una señal periódica y avisa cuando deja de llegar.

Funciona con la convención de [healthchecks.io](https://healthchecks.io) (o cualquier
servicio compatible, también autoalojado):

1. En el servicio se crea una comprobación por tarea, con su periodo (1 día) y un margen.
   El servicio da una URL.
2. Esa URL se pone en la variable `HEALTHCHECK_URL_<NOMBRE>`, con el nombre de la tarea en
   mayúsculas y los guiones como guiones bajos.
3. Al terminar la tarea, `ops run` llama a la URL si ha ido bien y a `<URL>/fail` si ha
   fallado (con el motivo en el cuerpo de la petición).
4. Si la tarea falla, el servicio avisa enseguida. Si la señal no llega a tiempo —porque
   la tarea no ha corrido—, avisa también. Ese segundo caso es el que cubre el hueco.

| Variable | Para qué |
|---|---|
| `HEALTHCHECK_URL_RUN_DAILY`, `HEALTHCHECK_URL_BOE_NOVEDADES`, `HEALTHCHECK_URL_BOE_IMPACT`, `HEALTHCHECK_URL_BOE_WATCH`, `HEALTHCHECK_URL_DELIVER`, `HEALTHCHECK_URL_DOCTOR`… | URL de la comprobación de esa tarea. |
| `HEALTHCHECK_URL` | URL genérica para las tareas sin variable propia. Todas comparten entonces una sola comprobación: el servicio no distingue cuál ha dejado de correr y un éxito de una tapa el silencio de otra. Úsala solo si vigilas una única tarea. |
| `JOBS_STATE_PATH` | Fichero de estado. Por defecto, `./data/_jobs_state.json`. |

Un fallo de red al avisar al healthcheck nunca hace fallar la tarea: se anota en el log y
se sigue. Lo mismo si no se puede escribir el fichero de estado.

Lo mínimo recomendable: una comprobación para `doctor` (`HEALTHCHECK_URL_DOCTOR`, con la
línea de cron del apartado 1). Así, si cron muere, lo sabrás por el servicio externo; y si
cron vive pero falla una tarea, lo sabrás por `doctor`.

### Desde código

Para vigilar una función en lugar de un comando:

```python
from asistente.plataforma.ops.vigia import vigilado


@vigilado("mi-tarea")
def mi_tarea() -> None: ...


# o bien
with vigilado("mi-tarea"):
    ...
```

Apunta el resultado, avisa al healthcheck y deja pasar la excepción sin tocarla.

## 4. Vigilancia semanal de los datos legales

El worker ejecuta cada lunes tres órdenes que solo necesitan acceso a `www.boe.es` (con cron, son
las líneas del apartado 1):

| Hora | Tarea | Orden (`proactive …`) | Termina con error si… |
|---|---|---|---|
| 07:05 | `params-check` | `params-check --fail-on-stale` | el BOE ha actualizado una norma de la que salen los parámetros o los datos de un módulo (hay que releerla; no implica que las cifras hayan cambiado), o no ha podido consultar alguna ley. |
| 07:15 | `params-verify` | `params-verify --fail-on-mismatch --no-anotar-lecturas` | una cita o una cifra de los ficheros ya no aparece en el texto vigente de su ley. Solo comprueba: no escribe en los ficheros de datos. |
| 07:25 | `normas-check` | `normas-check` | nunca. Informa de si cada real decreto-ley del que dependen parámetros está pendiente, convalidado o derogado. |

Una tarea que termina con error queda apuntada en el fichero de estado, y `ops doctor` la avisa al
operador (apartado 3). Si `params-check` no puede consultar alguna ley, la pone en `errores` de su
informe JSON y, con `--fail-on-stale`, también termina con error.

`params-verify` cubre todos los ficheros de parámetros (IRPF, autónomos, sociedades, patrimonio e
ISD) y los datos legales de todos los módulos del hogar, bajo `modulos`. `params-autopilot`
(apartado 5) es otra tarea del worker y está desactivada por defecto.

**Los módulos del hogar, en el mismo ciclo.** Cada módulo declara sus datos legales en la
biblioteca común `asistente/conocimiento/` (`FICHEROS_LEGALES` en su fichero de comprobación):
luz (`modulos/luz/data/regulados.json`), caducidades, ayudas (`prestaciones_<año>.json`),
hipoteca (normativa, fundamento de la deducción y serie de índices), suscripciones y correo; más
los pasajes que citan el planificador y el módulo fiscal desde el código. Con eso, sin nada que
cablear:

- `params-verify` comprueba cada grupo contra el BOE de hoy con el mismo comprobador que las
  pruebas del módulo (cita literal en su ley y en su versión; cada cifra en su cita), y
  `--fichero luz` (o la clave de cualquier módulo) limita la ejecución a uno;
- `params-check` compara la fecha de actualización de cada ley de un módulo con la **fecha de
  lectura guardada en el propio fichero del módulo** (`fuentes_principales[*].consultado_el`), como
  hace con los ficheros fiscales. El fichero de estado `MODULOS_FUENTES_STATE_PATH` ya solo sirve
  para una fuente que ningún fichero de datos registra (un módulo fuera de la biblioteca), y
  `--marcar-modulos-revisados` solo mueve esas;
- `params-verify` / `params-autopilot` anotan la nueva lectura en el fichero del módulo con las
  mismas condiciones que en los fiscales (abajo), cambiando solo `consolidado_a`, `consultado_el`
  y `sha256_html` en su sitio, sin reformatear el fichero; y `params-lecturas` los revisa junto a
  los fiscales (`asistente/ambitos/*/data/*.json`).

`params-check` compara la fecha en que el BOE actualizó cada ley con `consultado_el`, la fecha en
que los ficheros la leyeron. Esa fecha ya no hay que editarla a mano: `params-verify` y
`params-autopilot` la anotan solos, sin modelo, cuando la ley está al día (apartado 5, «La fecha
de lectura de cada ley»). La anotan en la copia de trabajo donde corren. No hay un PR automático:
las lecturas se suben al repositorio a mano.

1. En una copia de trabajo del repositorio, ejecuta
   `uv run python -m asistente.plataforma.cli.proactive params-verify`. También vale la copia del
   servidor después de la tarea del lunes.
2. Ejecuta `uv run python -m asistente.plataforma.cli.proactive params-lecturas --body lecturas.md`.
   Compara los ficheros de parámetros y de los módulos con `HEAD` (`--base` para otra versión),
   lista cada ley cuya lectura se anotó y termina con error si un fichero cambió en algo más que sus
   fuentes y su registro `revisiones` (una cifra, una cita, una regla). Si falla, no subas nada y
   mira qué cambió. Si no, `lecturas.md` queda con una tabla de leyes, fecha de consolidación
   anterior y nueva y ficheros tocados.
3. Haz commit de `asistente/ambitos/fiscal/data/` y `asistente/ambitos/*/data/` y abre un PR con
   `lecturas.md` como descripción.
4. Al fusionarlo, `params-check --fail-on-stale` deja de señalar esas leyes.

`params-verify` no anota una ley que tenga una propuesta abierta en `RECODE_PROPOSALS_PATH`. Una
fuente fijada a una redacción con fecha (el fichero de autónomos de un ejercicio cerrado lee la ley
«a 31-12-2025») ya no retiene la alarma: su texto no cambia.

## 5. Recodificación automática de parámetros

Cuando un grupo de parámetros deja de coincidir con su ley (`params-verify`),
`params-recode` hace que un modelo relea el artículo y **proponga** datos
nuevos. Nada llega a `asistente/ambitos/fiscal/data/` sin pasar un control determinista
y, salvo que actives la auto-aplicación, sin que tú lo apliques. Explicación y
garantías en el README («Recodificación automática»).

**Módulos del hogar.** El bucle cubre también las reglas de `caducidades`
(`asistente/ambitos/caducidades/data/reglas.json`): sus grupos aparecen en
`params-recode`, `params-autopilot`, `params-apply` y `params-confirm` como
`caducidades:<Modelo>` (por ruta: `params-recode --ruta caducidades:itv`), con
casos del revisor que pasan por la calculadora del propio módulo. Un fichero sin
año se archiva bajo el pseudoejercicio `9999` («lo vigente»). **Una propuesta
sobre datos de un módulo nunca se aplica sola**, ni con `RECODE_AUTO_APPLY=true`:
siempre la aplica una persona con `params-apply`. Los demás ficheros de los
módulos los relee una persona, y su declaración dice por qué
(`asistente.conocimiento.recodificacion.de_modulos.solo_persona()`); la verificación
semanal sí los cubre a todos.

**Nada de esto se ha probado contra un modelo real**: las pruebas usan un
proveedor guionizado. Antes de activar la auto-aplicación, mide el bucle con tu
modelo (`params-eval`, más abajo).

### El piloto: de la norma publicada al dato, sin que nadie lo pida

`params-autopilot` es el punto de entrada único para cron (línea en el
apartado 1). Cada ejecución:

1. **Detecta** (salvo `--no-detect`): lee el BOE de los días pendientes, como
   `boe-novedades`, y actualiza su registro (`_boe_novedades.json`).
2. **Normas → artículos → grupos.** Para cada norma del registro cuyo análisis
   del BOE dice que modifica una ley de la que salen parámetros, averigua qué
   artículos cambia y lo deja anotado, artículo a artículo, con la vía por la
   que lo supo (`articulos` de cada norma en el parte y en el estado):
   - `análisis`: los que nombra el análisis («MODIFICA los arts. 5 y 9 bis…»);
   - `versión del consolidado` / `anotaciones del consolidado`: una vez
     consolidada, los bloques del texto consolidado cuya nota la nombra («Se
     modifica por el art. 2 de la Ley 3/2026… Ref. BOE-A-…», o un «Téngase en
     cuenta…»), con o sin versión fechada ese día;
   - `texto de la norma`: antes de la consolidación, las frases de la propia
     norma («Se modifica el artículo 5…», «Uno. El apartado 2 del artículo 9
     queda redactado…», «Se añade una disposición adicional tercera…»), sin
     leer la nueva redacción entre «» y solo las que se refieren a esa ley por
     su número (o están dentro de un artículo de la norma que la nombra);
   - `modelo`: solo si nada de lo anterior da ningún artículo (el análisis dice
     «determinados preceptos» y la norma usa una fórmula que el programa no
     reconoce), un modelo **lista** los artículos (una llamada por norma), cada
     uno con una cita literal de la norma que lo nombra; lo que no se comprueba
     se descarta. El modelo nunca codifica aquí.

   Luego busca, en cualquier fichero, **todos** los grupos que se leen de esos
   artículos, por su propio artículo o porque sus citas están en ellos, y los
   relee **aunque su comprobación pase**: una condición nueva puede estar fuera
   de los pasajes citados, donde ninguna verificación mira. Esa relectura recibe
   el artículo entero y la codificación vigente, y el lector puede responder
   `sin_cambios`: el control exige que todas las citas anteriores sigan en el
   texto y que cada cifra de la codificación vigente esté en alguna cita, y el
   revisor busca expresamente una condición, excepción o límite nuevo y lanza
   sus casos con la codificación vigente. Si no, propone, como siempre.
   - Si el identificador de la norma ya aparece en el texto consolidado de la
     ley, los relee del consolidado, con el análisis como anotación.
   - Si aún no (el BOE consolida días después), los relee **del texto de la
     propia norma** (`diario_boe/txt.php`). Esa propuesta lleva siempre una
     nota en `control.revisar` y **nunca se auto-aplica**. Cuando llega la
     consolidación: si la aplicaste y sus citas están ya en el consolidado,
     queda respaldada sin llamar al modelo; si no, el grupo se relee del
     consolidado.
   - Si la norma **añade** un artículo a la ley de una comunidad y nada lo
     codifica, el lector lista las deducciones que contiene (una llamada) y
     cada una entra en el bucle como deducción nueva.
   - Una norma anterior al texto del que se leyeron los parámetros
     (`fuente_principal.consolidado_a`) ya está en los datos y no se relee: al
     activar el piloto sobre un registro antiguo no se paga por el pasado. Una
     norma que a los 90 días sigue sin aparecer en el consolidado se cierra con
     aviso.
3. **Cambios que nadie anunció.** Si el BOE ha actualizado una ley después de
   la lectura que anotan los ficheros, los grupos de los artículos cuya
   redacción se publicó después de esa lectura se releen igual que en el paso
   2 (salvo que ya haya una propuesta aceptada hecha con el texto de hoy).
4. **Verifica** todos los ficheros de parámetros contra el texto vigente (sin
   modelo). Lo que no coincide entra como objetivo, lo anuncie una norma o no.
5. **Recodifica** con el bucle, con los topes de `RECODE_MAX_PER_RUN` y del
   presupuesto diario; lo que no cabe queda para la siguiente ejecución. Con
   `RECODE_AUTO_APPLY=true` aplica lo que no necesita a nadie (también un
   `sin_cambios` conforme, que no escribe nada).
6. **Anota la lectura** de cada ley que ha quedado al día (ver «La fecha de
   lectura de cada ley», más abajo).
7. **Parte** al operador por los canales `NOTIFY_*` (asunto «parte del piloto
   de parámetros»): normas nuevas, estado de cada una y sus artículos, grupos
   que no coinciden o que se releen, lo que el bucle no puede releer,
   propuestas de la ejecución, leyes anotadas y leyes que aún no se pueden
   anotar y por qué. No se repite mientras la situación no cambie.

Su estado está en `<output-dir>/_params_autopilot.json` (una entrada por norma
y ley: `sin_consolidar` → `consolidada` → `cerrada`). Bórralo solo si quieres
que vuelva a tratar las normas del registro.

Sin credenciales de LLM (o con `--no-llm`) hace los pasos 1 a 4, el 6 y el
parte, y dice en `omitido` cuántos grupos esperan (una ley con grupos que
esperan no se anota). Termina con código 0 salvo con
`--fail-on-error` (errores o presupuesto agotado → 1). `boe-novedades
--autopilot`, o la variable `RECODE_AUTOPILOT=true`, lanza el piloto al
terminar la revisión del día, si prefieres una sola línea de cron.

Qué sigue necesitando a una persona, aunque actives la auto-aplicación:

- propuestas con algo en `control.revisar`: leídas de una norma sin
  consolidar, de una versión histórica, de un grupo sin casos de prueba
  ejecutables o con cifras que imprime otra ley, o con la cláusula de efectos
  dudosa;
- retiradas y `sin_converger` (entre ellas, un cambio de *estructura* en un
  grupo de cifras: hay que cambiar la calculadora);
- lo listado en `fuera_de_alcance` y `sin_adaptador`;
- `params-confirm` de lo aplicado;
- una ley que no se puede anotar (ver abajo): `params-check` sigue en rojo
  hasta que se resuelva lo que impide anotarla.

### La fecha de lectura de cada ley

Cada fichero de parámetros dice de qué texto de cada ley salen sus cifras
(`fuente_principal` en el IRPF y en cada comunidad; `fuentes_principales`,
`fuentes_complementarias` y la `fuente_principal` de cada comunidad en los
demás) con `consolidado_a`, `consultado_el` y `sha256_html`. `params-autopilot`
(paso 6) y `params-verify` (al terminar) **anotan solos la lectura de hoy** de
cada ley que el BOE ha actualizado después de la lectura anterior (por la fecha
de sus metadatos, la misma que mira `params-check`, o por la «Última
actualización» de la página), sin modelo y solo si:

- todas las comprobaciones de todos los grupos que la citan, en todos los
  ficheros, coinciden con el texto de hoy;
- ningún grupo leído de un artículo cuya redacción se publicó después de la
  lectura anterior queda sin releer contra el texto de hoy (con una propuesta
  aceptada, `recodificada` o `sin_cambios`, hecha con ese mismo texto), ni
  cuelga de uno de esos artículos un grupo que el bucle no relee (importes
  calculados a partir de otros, como los mínimos de Baleares): ese caso lo
  revisa una persona, que anota la lectura a mano;
- no hay sobre ella ninguna propuesta pendiente de decisión ni `sin_converger`,
  ningún grupo marcado como no verificado que no compruebe nada, ni —en el
  piloto— una norma en curso o un grupo de la ejecución sin resolver.

Lo mismo vale para las leyes de las que leen sus datos los módulos del hogar
(`asistente/conocimiento/`), con una condición más: si algún grupo de un
módulo se lee de un artículo cuya redacción se publicó después de la lectura
anterior, no se anota aunque su cita siga literal (el cambio puede estar fuera
de ella); lo relee una persona —o el bucle, para las reglas de `caducidades`—
y la lectura se anota después.

Entonces escribe en **todos** los ficheros que la citan la nueva fecha de
consolidación (nunca hacia atrás), `consultado_el` de hoy y la huella de la
página, y una línea en `revisiones` (los ficheros de autónomos, sociedades,
patrimonio e ISD la empiezan la primera vez; los de los módulos no tienen
registro y solo cambian esos tres campos, en su sitio). Valida cada fichero con su modelo (y los de los
módulos, con su autocontrol) antes de escribir ninguno. Nunca
toca una cifra ni una fuente fijada a una redacción con fecha o al PDF de una
orden. La salida (`procedencia`) dice qué leyes se anotaron, cuáles están al
día y, de las demás, todos los motivos. Con `--snapshot-dir` solo se simula
(esos textos no se han leído hoy del BOE); `--no-anotar-lecturas` lo desactiva.

### Ajustes

| Variable | Por defecto | Qué hace |
|---|---|---|
| `RECODE_PROPOSALS_PATH` | `./data/recodificacion` | Carpeta de las propuestas (un JSON por propuesta). Haz copia: es el registro de quién propuso qué. |
| `RECODE_AUTO_APPLY` | `false` | `true`: aplica sin esperar lo que supera el control sin nada por revisar y con la conformidad del revisor. Nunca retiradas ni versiones históricas. |
| `RECODE_MAX_ITER` | `3` | Pasadas del lector por grupo antes de `sin_converger`. |
| `RECODE_MAX_PER_RUN` | `10` | Grupos por ejecución; el resto espera a la siguiente. |
| `RECODE_MODEL` / `RECODE_REVIEWER_MODEL` | (modelo del proveedor) | Modelo del lector y del revisor. Un revisor distinto hace la revisión más independiente. |
| `RECODE_AUTOPILOT` | `false` | `true`: `boe-novedades` lanza el piloto al terminar (equivale a `--autopilot`). |

Usa el proveedor de `LLM_PROVIDER` y cuenta contra `LLM_DAILY_BUDGET_USD`
(propósitos `recodificacion` y `recodificacion-revisor` en `ops consumo`). Si
el presupuesto se agota a mitad, la ejecución se detiene, lo dice en
`presupuesto_agotado` y termina con código 1; lo que no se procesó figura en
`sin_procesar` y se retoma en la siguiente.

### Paso a paso

```bash
# 1. Qué se releería y cuánto texto se enviaría. No llama a ningún modelo.
python -m asistente.plataforma.cli.proactive params-recode --dry-run

# 2. Lanzar el bucle (todos los ejercicios; --comunidad / --tipo para acotar).
python -m asistente.plataforma.cli.proactive params-recode --comunidad galicia

# 3. Ver las propuestas y leer una entera.
python -m asistente.plataforma.cli.proactive params-proposals --estado propuesta
python -m asistente.plataforma.cli.proactive params-proposals --show <id>

# 4. Decidir.
python -m asistente.plataforma.cli.proactive params-apply <id>
python -m asistente.plataforma.cli.proactive params-reject <id> --motivo "…"

# 5. Tras revisar a mano lo aplicado, quitar la marca «pendiente de revisión humana».
python -m asistente.plataforma.cli.proactive params-proposals --pendientes
python -m asistente.plataforma.cli.proactive params-confirm <id>
```

Qué mirar en una propuesta antes de aplicarla:

- `ejercicios` y `ejercicios_excluidos`: qué ficheros toca y por qué deja los
  demás. Salen de `efectos_desde` y `cita_efectos`; comprueba que la cita es la
  cláusula que rige («con efectos desde…», «períodos impositivos iniciados a
  partir de…») y no solo la entrada en vigor.
- `antes` y `despues`, con `citas`: cada cifra de `despues` está en alguna cita
  (lo garantiza el control); lo que debes juzgar es si la cita dice eso.
- `control.revisar`: lo que el control no pudo decidir con sus reglas. Con algo
  aquí la propuesta nunca se auto-aplica.
- `veredicto.discrepancias` (las `observacion` no bloquean) y `casos`: los
  contribuyentes de prueba del revisor, con lo que da la ley y lo que da la
  calculadora.
- `resultado: "retirada"`: el lector cree que la deducción ya no encaja en el
  esquema (`motivo_lector`). Aplicarla la quita de las calculables y la deja,
  por título, en `otras_deducciones`. Nunca se auto-aplica.
- `resultado: "sin_cambios"`: tras una modificación del artículo, el lector
  sostiene que el grupo no cambia; el control comprobó que siguen todas las
  citas anteriores y el revisor no encontró ninguna condición nueva.
  `params-apply` repite el control y no escribe nada en los ficheros: solo
  deja la propuesta como aceptada (eso permite anotar la lectura de la ley).
- `estado: "sin_converger"`: `objeciones_abiertas` dice dónde se atascó. No se
  puede aplicar; corrígelo a mano o recházala. No se reintenta sola mientras ni
  la ley ni los datos cambien (`--force` lo fuerza).

`params-apply` relee la ley. Se niega, sin escribir nada, si el texto del
artículo ha cambiado desde la propuesta, si el fichero ya no contiene lo que
iba a sustituir, o si la propuesta no supera hoy el control. Al aplicar añade
una línea a `revisiones` del fichero y actualiza las citas:
`evidencia_deducciones.json` para las deducciones, `evidencia_parametros.json`
para las escalas y los grupos de cifras del IRPF (los demás ficheros llevan las
citas dentro del grupo). Las escalas y los grupos de cifras recodificados
quedan con `fuente.verificado: false` (la calculadora ya los lista en
`parametros_no_verificados`) hasta `params-confirm`.

Desde ese momento `params-verify` comprueba el grupo **por esas citas**, contra
la ley y la versión de las que se leyeron, mientras el grupo conserve las
cifras para las que se leyeron (`sha256_valor`): si alguien edita el grupo a
mano, las citas dejan de valer y vuelve la comprobación por cifras.

### Qué grupos hay y cómo se nombran

| Fichero | `--tipo` | `--ruta` |
|---|---|---|
| `irpf_<año>.json` | `deduccion_autonomica`, `escala`, `cifras` (todos los grupos de cifras) o `cifras:<grupo>` (`cifras:minimos`, `cifras:trabajo`, `cifras:madrid_alquiler`…) | `minimos`, `trabajo`, `reduccion_arrendamiento/regimen_2026`, `comunidades/galicia/escala_general`, `comunidades/madrid/deducciones_madrid/alquiler`… |
| `autonomos_`, `sociedades_`, `patrimonio_`, `isd_<año>.json` | `<familia>:<Modelo>` (`sociedades:TiposGravamen`) | `<familia>:<ruta>` (`sociedades:tipos_gravamen`, `isd:comunidades/madrid/…`) |

`params-recode --dry-run` lista tipo y ruta de cada grupo que revisaría;
`fuera_de_alcance` dice qué grupos no asume el bucle y por qué. Para los cuatro
ficheros que no son del IRPF hay además una comprobación sin modelo de lo
publicado, grupo a grupo, con sus propias citas:

```bash
python -m asistente.conocimiento.recodificacion.adaptadores_otros <autonomos|sociedades|patrimonio|isd> <ejercicio> <carpeta con <ID>.html>
```

y para las deducciones autonómicas del IRPF, el mismo control que debe superar
una propuesta (termina con código 1 si alguna falla):

```bash
python -m asistente.plataforma.cli.proactive params-gate              # lee las leyes del BOE
python -m asistente.plataforma.cli.proactive params-gate --offline    # solo modelo y cifras dentro de las citas
```

### Casos que no son el habitual

```bash
# Releer un artículo aunque su comprobación pase, o codificar uno nuevo.
params-recode --comunidad galicia --articulo "art. 5"

# Un artículo que crea deducciones: el lector las lista y cada una que no esté codificada se propone.
params-recode --comunidad galicia --articulo "art. 5" --desglosar

# Un grupo concreto por su ruta (cualquier fichero).
params-recode --ruta comunidades/galicia/escala_general
params-recode --ruta minimos
params-recode --ruta sociedades:tipos_gravamen

# Una norma que el BOE aún no ha consolidado: leer la nueva redacción de su propio texto.
params-recode --comunidad galicia --articulo "art. 5" --norma BOE-A-2026-12345 --sin-consolidar

# Dar a leer el análisis del BOE de la norma modificadora («MODIFICA…, con efectos de…»).
params-recode --comunidad galicia --norma BOE-A-2026-12345

# Un ejercicio al que la redacción vigente no alcanza: indica TÚ la redacción aplicable.
params-recode --ejercicio 2025 --comunidad galicia --articulo "art. 5" --version 20251231

# Al abrir un ejercicio: intentar recodificar lo que el rollover retira.
params-rollover 2027 --recode
```

Con `--version` la propuesta siempre lleva una nota en `control.revisar` y no
se auto-aplica: que una redacción estuviera vigente el 31 de diciembre no
prueba que sea la aplicable a ese ejercicio (una ley posterior puede haberla
cambiado con efectos retroactivos). Compruébalo antes de aplicar. Lo mismo
vale para `--sin-consolidar`: compárala con el texto consolidado cuando salga
(el piloto lo hace solo).

En las leyes que meten todas sus deducciones en un artículo (Galicia art. 5,
Valencia art. 4, Murcia art. 1, La Rioja art. 32, Cantabria art. 2) el modelo
recibe solo el apartado de la deducción: `articulos_ley` de la propuesta lo
dice («5.dos», «4.uno.n», «32.12»). Si ahí figura el artículo entero («5») es
que la numeración no permitió aislar el apartado sin dudas.

### Medir el bucle antes de fiarte (`params-eval`)

```bash
# Qué grupos se evaluarían y cuánto texto se enviaría. Sin modelo.
python -m asistente.plataforma.cli.proactive params-eval -n 20 --semilla 1 --dry-run

# 20 deducciones autonómicas publicadas, sorteadas con esa semilla.
python -m asistente.plataforma.cli.proactive params-eval -n 20 --semilla 1 --usd-por-millon 3

# Otros tipos, una comunidad, o grupos concretos.
python -m asistente.plataforma.cli.proactive params-eval -n 10 --tipo escala --tipo cifras
python -m asistente.plataforma.cli.proactive params-eval --ruta comunidades/galicia/deducciones_calculables/articulo="art. 5.Siete"
```

Oculta al lector la codificación publicada y sus citas, lanza el bucle normal
y compara. No escribe en los parámetros ni guarda propuestas. Qué mirar:

- `convergieron` / `evaluados`: cuántas veces lector y revisor llegan a una
  propuesta. `identicos`: de esas, cuántas igualan a la humana en todos los
  campos comparados.
- **`aplicables_sin_persona_con_diferencias`**: propuestas que se habrían
  auto-aplicado y difieren de la codificación humana. Es la cifra que decide
  si activar `RECODE_AUTO_APPLY`: lee cada una en `grupos[].campos_distintos`
  (`humano` frente a `bucle`); a veces quien se equivoca es la codificación
  publicada.
- `acuerdo` por grupo y `acuerdo_medio`: proporción de campos iguales. Los
  textos (título, resumen, requisitos no comprobados) se listan aparte y no
  cuentan.
- `llamadas_llm`, `tokens_entrada_aprox` y, con `--usd-por-millon`, el coste
  de entrada estimado; el coste real con salida está en `ops consumo`.

Cuenta contra el presupuesto diario como cualquier otra ejecución.

### Dónde corre

En el servidor: la línea de cron del apartado 1 o la tarea `params-autopilot` del worker (06:55,
desactivada por defecto; se activa con `PLATAFORMA_TRABAJOS`, ver `docs/despliegue.md`). No hay una
ejecución semanal en GitHub ni un artefacto `recode-proposals`: las propuestas quedan en
`RECODE_PROPOSALS_PATH` y se aplican con `params-apply`. Las lecturas que anota se suben a mano,
como en el apartado 4.

### Límites

- El modelo solo lee los artículos que se le muestran (el del grupo —o su
  apartado—, aquellos donde estaban las citas anteriores y los que pida). Una
  regla en otra norma no la ve.
- El control comprueba que las cifras están en las citas, no que signifiquen lo
  que la codificación dice. Para eso están el revisor, sus casos y tú.
- Un grupo de cifras tiene la forma que su modelo y la calculadora le dan. Si
  la ley cambia la forma de la regla, el bucle debe rendirse («no calculable»);
  no puede cambiar la calculadora.
- De una norma sin consolidar solo se lee lo que su texto dice del artículo.
  Si no imprime la fecha de efectos («entrará en vigor el día siguiente al de
  su publicación») la propuesta no supera el control y hay que esperar al
  texto consolidado, que sí la imprime.
- Si el análisis del BOE dice «determinados preceptos», los artículos salen
  del texto consolidado o del de la propia norma; el análisis sintáctico de
  la norma reconoce las fórmulas habituales («Se modifica…», «… queda
  redactado…», «Se añade…») y, si no reconoce ninguna, decide un modelo que
  solo lista. Una norma que cambia un artículo sin nombrarlo en ninguna parte
  queda para la verificación, que ve cifras y citas que cambian pero no una
  condición nueva.
- Antes de la consolidación no se puede probar que un grupo «no cambia»: el
  texto de la norma solo trae el apartado que reescribe. Hay que esperar al
  consolidado o decidir a mano.
- Una relectura tras una modificación envía el artículo entero, aunque mida
  70.000 caracteres: es más cara que una relectura por verificación.
- El paso 3 y la anotación de lecturas comparan fechas de publicación de
  versiones con la fecha de lectura. Una norma publicada antes de la lectura y
  consolidada después la cubre el registro de novedades (paso 2), no el 3.
- Los artículos que una norma añade solo se desglosan en deducciones nuevas en
  las leyes autonómicas del IRPF; un grupo estatal nuevo exige código.
- Grupos leídos de una redacción ya no vigente, o con importes calculados:
  fuera del alcance del bucle (`fuera_de_alcance`).

## 6. Deriva de formatos: saber que una fuente ha dejado de entenderse

Una tarea puede correr, terminar bien y no haber leído nada, porque el BOE (o un boletín, o el
TEAC) ha cambiado una clave, una ruta o un rótulo. `ops doctor` no lo veía. Ahora cada lector
informa, en cada lectura real, de si lo que ha leído cumple lo que da por supuesto. El inventario
de fuentes, supuestos y síntomas está en [`docs/formatos_inventario.md`](formatos_inventario.md).

| Subcomando | Qué hace | ¿Usa un modelo? |
|---|---|---|
| `ops formatos check [--diagnose]` | Comprueba el calendario fiscal (coherencia, sin red) y da el estado de cada fuente (`ok`, `degradada`, `rota`) según las últimas lecturas; avisa por los canales `NOTIFY_*`. En `otros_avisos`, la tabla de precios. Con `--diagnose`, además diagnostica lo que falla. | Solo con `--diagnose` y si hay modelo configurado |
| `ops formatos probe` | Lee ahora del BOE una muestra de cada formato con los lectores reales, y los artículos que fijan los plazos del calendario. Para CI o para comprobar una spec. | No |
| `ops formatos diagnose [--fuente X] [--force] [--dry-run] [--auto-apply]` | Pide el diagnóstico de las fuentes que fallan y deja una propuesta o un informe. | Sí |
| `ops formatos proposals [--estado E] [--show ID]` | Lista propuestas e informes. | No |
| `ops formatos apply ID` / `reject ID --motivo …` / `confirm ID` | Pone en vigor una spec, la descarta (restaurando la anterior si ya estaba aplicada) o la da por revisada. | No |
| `ops formatos sources` | Qué fuentes se vigilan, qué supone cada lector y con qué versión de spec. | No |

### Puesta en marcha

1. No hay nada que activar: `FORMAT_WATCH` está encendido por defecto y las tareas que ya tienes
   (`boe-novedades`, `boe_watch`, `boe-impact`, `params-verify`, `normas-check`, los rastreadores
   —TEAC, boletines, CENDOJ, sede y PDF de la AEAT, códigos autonómicos—, `supremo_seed`,
   `teac_seed`, `dgt_seed`, `boe_seed`, la ingesta del corpus y la subida de documentos) empiezan a
   dejar observaciones en `FORMAT_STATE_PATH` y muestras en `FORMAT_DIR`. Ambas rutas deben estar en
   un volumen persistente, como `JOBS_STATE_PATH`. La ingesta (Docling) informa desde varios procesos
   a la vez: el fichero de estado se escribe con bloqueo.
   Del CENDOJ solo se observa lo que ya se lee por orden tuya (`supremo_seed --from-dir` o
   `--urls-file`, el rastreador con su lista de URL): la vigilancia no hace ninguna petición propia
   y de cada sentencia guarda solo un esqueleto estructural, sin una palabra de la resolución fuera de
   los rótulos ni una cifra (ver `docs/privacidad.md`); el diagnóstico trabaja sobre ese esqueleto.
2. Añade una línea de cron **después** de las tareas diarias:

   ```cron
   30 9 * * *  cd /srv/hacienda-asesor && uv run python -m asistente.plataforma.cli.ops formatos check --diagnose >> logs/ops.log 2>&1
   ```

   Sin clave de modelo, `--diagnose` no hace nada salvo decirlo (`"llm": "no configurado…"`); el
   estado determinista se informa igual.
3. `ops doctor` ya incluye las fuentes degradadas o rotas entre sus problemas: si lo tienes
   programado, te llegará por el mismo canal sin más cambios.
4. El worker ejecuta `ops formatos probe --fail-on-problem` de lunes a viernes a las 07:45 (tarea
   `formatos-probe`; con cron, la línea del apartado 1). Termina con error si un formato del BOE ya
   no se entiende, si un artículo que fija un plazo del calendario ha cambiado o si no se pudo leer,
   y `ops doctor` lo avisa. Las fuentes que no son del BOE (CENDOJ, AEAT, DGT,
   TEAC, boletines) solo se vigilan donde corren sus rastreadores.

### Ajustes

| Variable | Por defecto | Para qué |
|---|---|---|
| `FORMAT_WATCH` | `true` | Registrar observaciones en cada lectura. |
| `FORMAT_STATE_PATH` | `./data/_formatos_state.json` | Estado y últimas 40 observaciones por fuente. |
| `FORMAT_DIR` | `./data/formatos` | Muestras, specs aplicadas (`specs/`, con historial), propuestas e informes. |
| `FORMAT_AUTO_APPLY` | `false` | Aplicar sin persona lo que supere el ensayo determinista **y** la reflexión. |
| `FORMAT_MAX_ITER` | `3` | Rondas de diagnóstico por fuente (hasta 2 llamadas cada una). |
| `FORMAT_MODEL`, `FORMAT_REVIEWER_MODEL` | vacío | Modelo del diagnóstico y de la reflexión (distintos = revisión más independiente). |
| `FORMAT_DOCUMENT_SKELETONS` | `false` | Guardar esqueletos anónimos de documentos mal leídos (ver `docs/privacidad.md`). |

Las llamadas se miden con el propósito `formatos` / `formatos-revisor` y respetan
`LLM_DAILY_BUDGET_USD`: si el presupuesto está agotado, el diagnóstico lo dice y no deja nada a
medias; se reintenta en la siguiente ejecución.

### Cuando llega un aviso

1. **Mira qué falla**: `ops formatos check --no-notify`. Cada problema nombra el canario y da el
   detalle («No se ha leído ninguna disposición de la sección I…»).
2. **Si hay una propuesta** (`resultado: spec_propuesta`): `ops formatos proposals --show ID`.
   Lee `que_ha_cambiado`, compara `reglas_propuestas` con la spec vigente (`ops formatos sources`)
   y comprueba que el cambio es el mínimo que explica el fallo. El ensayo ya ha comprobado que la
   muestra antigua se lee igual y que la nueva cumple los canarios; lo que te toca a ti es decidir
   si la explicación es verdadera.
3. **Aplica** con `ops formatos apply ID`. La siguiente lectura real de esa fuente la devuelve a
   `ok` por sí sola; hasta entonces sigue marcada. Si no te convence: `ops formatos reject ID
   --motivo "…"`.
4. **Recupera lo perdido**. La spec arregla las lecturas futuras, no las pasadas: si el sumario
   estuvo sin leerse unos días, repite `boe-novedades --desde AAAA-MM-DD`.
5. **Si es un informe** (`informe_para_desarrollador`, estado `sin_converger`): el cambio no se
   resuelve con datos o el agente no ha convergido. En `FORMAT_DIR/informes/<ID>/` están el
   informe, la muestra buena y la nueva. La fuente sigue rota hasta que alguien toque el lector.
   `ops formatos confirm ID` lo da por leído.
6. Con `FORMAT_AUTO_APPLY=true` el paso 3 ocurre solo, pero la propuesta queda en estado
   `aplicada` hasta que la revises (`confirm`) o la deshagas (`reject`).

### El calendario fiscal

`tax/data/calendario.json` se mantiene a mano. Dos fuentes lo vigilan:

- `calendario_fiscal` (en cada `check`): si avisa de `fecha_imposible`, `anio_sin_plazo`,
  `vencido_como_proximo` o `modelo_desconocido`, el dato está mal y hay que corregirlo; con
  `sin_verificar`, un plazo marcado `verificado: false` vence en 90 días o menos: compruébalo en la
  fuente que cita (la Renta: la orden ministerial de la campaña, que publica el BOE) y actualiza el
  dato y su `fuente`; con `antiguedad`, el conjunto no se revisa desde hace un año.
- `calendario_boe` (en cada `probe`): las citas de su spec deben seguir en los artículos del BOE. Si
  falla `cita` o `plazo`, lee el artículo (el detalle dice cuál). Si solo ha cambiado la redacción, el
  diagnóstico puede proponer la redacción nueva como alternativa de la cita; esa propuesta **nunca se
  aplica sola**. Si ha cambiado el plazo, no hay propuesta posible (el ensayo exige que la muestra
  antigua se siga leyendo igual y que los días de `calendario.json` estén en lo que dice el BOE): en un
  mismo cambio revisado, corrige el plazo en `calendario.json` (dato legal, con su fuente) y la cita
  en `asistente/conocimiento/data/formatos/calendario_boe.json`; el siguiente `probe` lo da por bueno.

### Documentos de usuarios

De los documentos subidos solo se cuentan resultados por tipo (cuántos datos se proponen y los
nombres de los descartados). Si un tipo degrada, el diagnóstico sin esqueletos es un informe con
esos contadores y **no llama a ningún modelo**. Con `FORMAT_DOCUMENT_SKELETONS=true` el agente
recibe esqueletos anónimos y puede proponer etiquetas adicionales para el *prompt* del lector;
esa propuesta nunca se aplica sola.

### Antes de fiarte

Nada de esto se ha ejecutado contra un modelo real: las pruebas usan un proveedor guionizado. Con
una clave, valida al menos una vez:

1. Provoca una deriva controlada en un entorno de pruebas (por ejemplo, copia la muestra buena de
   `FORMAT_DIR/muestras/boe_sumario/buena.json` como `mala.json` renombrando una clave en su
   `texto` y añadiendo el fallo correspondiente) y ejecuta `ops formatos diagnose --fuente
   boe_sumario --no-auto-apply`.
2. Comprueba que el modelo devuelve `reglas_json` con **todas** las claves (si las omite, el
   ensayo lo rechaza y verás intentos fallidos: es señal de que hay que afinar el *prompt*), que
   usa alternativas (`["nombre_viejo", "nombre_nuevo"]`) en vez de sustituir, y que la reflexión
   no se limita a asentir.
3. Mira el coste en `consumo` con el propósito `formatos`: cada muestra enviada se recorta a
   12 000 caracteres, y van dos (buena y nueva) por llamada.

### Límites

- Un canario dice que algo no cuadra, no por qué: una caída de la fuente y un cambio de formato
  dan el mismo síntoma. Por eso el diagnóstico distingue causas y solo propone spec para la segunda.
- Los umbrales (dos lecturas seguidas, caída del 80 %, 15 disposiciones sin análisis) son
  conservadores y no están calibrados con historia real.
- Las expresiones regulares propuestas se acotan con una heurística (longitud, sin grupos
  cuantificados anidados, sin retrorreferencias), no con una prueba formal.
- Fuera del BOE no hay muestras reales (CENDOJ por sus condiciones de reutilización; sede de la AEAT,
  DGT y TEAC no accesibles desde donde se escribió esto): sus pruebas usan páginas sintéticas.
- Lo que aún no es spec o no se vigila: ver «Lo que queda» en el inventario.

## Consumo de modelos de IA: medirlo y limitarlo

Una sola pregunta puede costar muchas llamadas al modelo: el agente da varias vueltas, en modo
equipo intervienen además un planificador, dos investigadores y un crítico, después pasa el
verificador, y un documento escaneado se lee dos veces. `asistente/plataforma/ops/consumo.py` apunta
cada llamada y, si se configuran límites, corta antes de hacer la siguiente.

### Qué se guarda

Una fila por llamada en la tabla `llm_usage` de la misma base de datos SQLite que los casos
(`DECISION_STATE_DB_PATH`): fecha y hora (UTC), usuario y caso si se conocen, propósito
(`agente`, `verificador`, `documentos`…), proveedor, modelo, tokens de entrada, de salida, leídos
de caché y escritos en caché, y el coste en USD. No se guarda ningún texto de la consulta ni de
la respuesta.

Los tokens son los que devuelve la propia API. Si la API no los devuelve (pasa con algunos
servidores locales, sobre todo en respuestas en streaming), la llamada se apunta igualmente, con
los tokens y el coste vacíos.

### De dónde sale el coste

De una tabla de precios que es un fichero de datos, `asistente/plataforma/ops/data/precios_llm.json`: USD
por millón de tokens de entrada, salida, lectura de caché y escritura de caché para cada modelo,
con la fecha en que se consultaron (`consultado_el`) y la fuente.

- **Los precios cambian.** La tabla es una foto de esa fecha: revísala contra la página de precios
  del proveedor. Para actualizarla sin tocar el código, copia el fichero, corrígelo y apunta
  `LLM_PRICES_PATH` a la copia.
- **Nunca se estima.** Un modelo que no está en la tabla, o con el precio a `null`, se apunta sin
  coste. Hoy tienen precio `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5` y
  `claude-fable-5-2`; `gpt-4o-mini` está a `null` (no se pudo consultar) y los modelos locales no
  figuran. Si cambias `ANTHROPIC_MODEL`, `VERIFIER_MODEL`, `OPENAI_AGENT_MODEL` o un modelo de
  nivel (`LLM_MODEL_*`), añade el modelo a la tabla.
- Cada tipo de token tiene su precio: entrada, salida, lectura de caché, escritura de caché a 5
  minutos y a 1 hora. `contexto_largo` es el tramo de precio de las peticiones largas (Haiku 5.5 por
  encima de 100.000 tokens de prompt) y `factor_lote` el descuento de las llamadas por lotes (0,50).
  La nota de cada modelo dice qué cifras son literales de la fuente y cuáles **derivadas** de una
  regla general (las escrituras de caché, la lectura de caché de Haiku): compruébalas.
- El coste es una estimación para operar, no una factura: no incluye el modo rápido ni las tarifas
  de Bedrock o Vertex.

En los informes, `llamadas_sin_coste` dice cuántas llamadas no suman coste: si no es cero, el
total en USD es un mínimo.

### Límites

Todos opcionales; vacío significa sin límite. Un valor que no sea un número mayor que cero impide
arrancar el agente (mejor eso que un límite que no limita).

| Variable | Qué limita |
|---|---|
| `LLM_DAILY_BUDGET_USD` | Gasto total del servicio en un día. |
| `LLM_DAILY_BUDGET_USD_PER_USER` | Gasto de cada usuario en un día. Solo se aplica a las llamadas atribuidas a un usuario. |
| `LLM_MAX_CALLS_PER_QUESTION` | Llamadas al modelo en una misma pregunta (agente, equipo y verificador juntos). |
| `LLM_PRICES_PATH` | Tabla de precios alternativa. |

El día es el día natural de la península (Europe/Madrid): los límites diarios se restablecen a las
00:00. Al llegar a un límite, la consulta recibe un error 429 con un mensaje en español que dice
qué límite es y cuándo se restablece.

Lo que conviene saber antes de fiarse de ellos:

- Se comprueban **antes** de cada llamada, con lo ya gastado. La llamada que cruza el límite se
  completa, y si hay varias a la vez pueden cruzarlo todas: el exceso es de unas pocas llamadas,
  no de un día entero.
- Los límites en USD solo cuentan las llamadas con coste conocido. Con un modelo sin precio en la
  tabla **no protegen nada** (queda un aviso en el log la primera vez); el tope de llamadas por
  pregunta sí funciona siempre.
- Si el límite salta a mitad de una pregunta, lo ya gastado no se recupera. Cuando salta en el
  verificador, la respuesta se entrega marcada como no verificada, igual que si el verificador
  hubiera fallado.
- Un tope de llamadas por pregunta demasiado bajo empeora las respuestas. Como referencia, sin
  modo equipo una pregunta usa como mucho `AGENT_MAX_STEPS` vueltas por intento, más una
  verificación por intento.

### Consultarlo

```bash
# Total y desglose por día, propósito, usuario y caso de los últimos 7 días, en JSON.
uv run python -m asistente.plataforma.cli.consumo resumen --dias 7
```

`GET /usage` (con la cabecera `X-API-Key` si `HACIENDA_API_KEY` está definida) devuelve los
totales de hoy y de los últimos 30 días, en conjunto y por propósito, junto con los límites
configurados y la fecha de la tabla de precios. Son cifras de todo el servicio, pensadas para
quien lo opera.

El resumen de la línea de órdenes trae además el desglose por modelo (`por_modelo`), y cada grupo
dice qué parte del prompt salió de la caché (`proporcion_cache`), cuántas llamadas fueron por lotes
(`llamadas_lote`) y cuántas fueron el reintento de una salida no válida en el nivel superior
(`llamadas_escaladas`). La tabla `llm_usage` gana las columnas `tier`, `escalated`, `batch` y
`cache_write_1h_tokens` al abrirse por primera vez (las filas anteriores quedan con `0` / vacío).

### Varios modelos según la tarea, caché y lotes

Toda llamada al modelo tiene un **propósito** (el de `ops consumo`) y cada propósito un **nivel**,
en una tabla declarativa (`asistente/nucleo/agent/enrutado.py`, `TABLA`):

| Nivel | Propósitos |
|---|---|
| `pequeno` (clasificar, enrutar, extraer, redactar por lotes) | `equipo:planificador`, `cerebro:intencion`, `cerebro:repaso`, `memoria:resumen`, `consejo:expediente`, `hechos`, `ayudas*`, `correo:*`, `investigacion:planificar` |
| `mediano` (especialistas, lectores) | `especialista:*`, `consejo:miembro:*`, `equipo:investigador`, `documentos`, `recodificacion`, `formatos` |
| `grande` (razonar, criticar, revisar) | `cerebro`, `consejo:presidente`, `agente`, `verificador`, `equipo:critico`, `recodificacion-revisor`, `formatos-revisor`, `evaluacion-juez`, y cualquier propósito que no esté en la tabla |

`documentos` (leer cifras de un documento subido, escaneos incluidos) está un nivel por encima de la
extracción a propósito: nada comprueba la cifra leída contra la imagen.

**Por defecto no cambia nada**: sin ninguna de las variables de abajo, cada propósito usa el modelo
de su proveedor, como hasta ahora, y las peticiones son idénticas byte a byte (hay una prueba que lo
comprueba). El orden de preferencia para un propósito es: `LLM_ROUTE_<PROPÓSITO>` o `LLM_ROUTES` →
el ajuste propio que ya existía (`RECODE_MODEL`, `RECODE_REVIEWER_MODEL`, `VERIFIER_MODEL`,
`FORMAT_MODEL`, `FORMAT_REVIEWER_MODEL`, `DECISION_EXTRACTOR_MODEL` para `hechos`; el `--judge-model` de
`eval_agent run` para `evaluacion-juez`) → el modelo del nivel (`LLM_MODEL_*`, si no el del perfil)
→ el modelo del proveedor. El extractor de hechos del chat (`hechos`), el juez de `eval_agent`
(`evaluacion-juez`) y el clasificador opcional de convocatorias (`ayudas:clasificador`) se miden y
enrutan como el resto.

| Variable | Qué hace |
|---|---|
| `LLM_ROUTING_PROFILE` | Vacío (por defecto): todo como hasta ahora. `recomendado`: el perfil de abajo. |
| `LLM_MODEL_SMALL`, `LLM_MODEL_MEDIUM`, `LLM_MODEL_LARGE` | Modelo de cada nivel; gana al perfil. Vale para cualquier proveedor (con OpenAI, p. ej. un modelo pequeño para `pequeno`). |
| `LLM_EFFORT_SMALL`, `LLM_EFFORT_MEDIUM`, `LLM_EFFORT_LARGE` | Esfuerzo de razonamiento de Claude por nivel (`low` … `max`). |
| `LLM_ROUTES` | JSON propósito (admite `*`) → nivel o modelo, p. ej. `{"especialista:*": "pequeno"}`. |
| `LLM_ROUTE_<PROPÓSITO>` | Lo mismo para un propósito: mayúsculas y lo que no sea letra o cifra cambiado por `_` (`LLM_ROUTE_ESPECIALISTA_LUZ=claude-haiku-5-5`). |
| `LLM_ESCALATION` | `true` (por defecto): una salida no válida se repite **una vez** en el nivel superior, si es otro modelo. |
| `LLM_CACHE_TTL` | `5m` (por defecto) o `1h`: vida de la caché de prompts de Claude. |
| `RECODE_BATCH` | `true`: `params-recode`, el piloto y `params-eval` van por lotes. Por defecto `false`. |
| `LLM_BATCH_STATE_PATH` | Carpeta del estado de los lotes en curso (por defecto `./data/_lotes`). |
| `LLM_BATCH_POLL_SECONDS`, `LLM_BATCH_MAX_WAIT_HOURS` | Cada cuánto se pregunta por un lote (60 s) y cuánto se espera antes de dejarlo para la siguiente ejecución (24 h). |

Un valor no válido (perfil desconocido, `LLM_ROUTES` que no es JSON) impide arrancar el agente.

**Perfil recomendado** (`LLM_ROUTING_PROFILE=recomendado`, solo con Claude; con otro proveedor no
cambia nada): `pequeno` → `claude-haiku-5-5` con esfuerzo `low`; `mediano` → `claude-sonnet-5-5`
con el esfuerzo de siempre; `grande` → `ANTHROPIC_MODEL` (Opus 5.5 por defecto). El asesor, el
verificador, el crítico y los revisores siguen en el modelo grande. Antes de activarlo en
producción, mide la calidad con tus propias consultas (`params-eval` para la recodificación): un
modelo más barato puede leer peor, y el perfil no se ha probado contra la API real (desde aquí no
hay clave).

**Escalado.** Cuando la salida de un nivel es inservible (JSON roto o truncado), no cumple el
esquema pedido o no pasa la comprobación determinista de quien llama, se pide otra vez **una sola
vez** al nivel superior, si allí hay otro modelo; si tampoco vale, se falla como antes. En la
recodificación, un grupo que acaba «sin converger» después de que el control determinista
rechazara al lector se relee una vez con el lector del nivel superior. Las dos llamadas se apuntan,
la segunda con `escalated=1`. Sin perfil ni modelos de nivel no hay nivel superior distinto y no se
reintenta nada. `params-eval` nunca escala: mide el lector configurado.

**Caché de prompts.** Claude recibe un punto de caché explícito al final del prompt de sistema cuando
mide al menos 2.048 caracteres (por debajo del mínimo cacheable no sirve): cubre herramientas +
sistema, el prefijo estable de cada petición (instrucciones del asesor y de cada especialista con sus
herramientas, reglas y esquema de la recodificación —hasta ~36.700 caracteres—, instrucciones del
verificador y de formatos). Los bucles con herramientas conservan además la caché automática de la
conversación. Las llamadas de una sola respuesta (`complete_json`) ya no la usan: ahí escribía en
caché, a 1,25 veces el precio, un texto final distinto en cada llamada que nunca se volvía a leer.
Con OpenAI se envía `prompt_cache_key` (huella del sistema y las herramientas) para que las
peticiones con el mismo prefijo aprovechen su caché automática; a los servidores locales no.

**Lotes** (`RECODE_BATCH=true`). Las llamadas van por la API de lotes de Claude (Message Batches):
la mitad de precio y respuesta en horas, no en segundos. El bucle de recodificación no cambia: se
ejecuta por rondas; en cada una, cada grupo avanza con las respuestas ya recibidas hasta su
siguiente llamada, que se envía en un lote; cuando ningún grupo pide nada más, se ejecuta el bucle
normal con esas respuestas (mismo control determinista, mismo revisor, mismas propuestas, mismo
`RECODE_AUTO_APPLY`). Un grupo que converge en una pasada son dos rondas (lector y revisor). El
estado (respuestas, lotes en curso) se guarda en `LLM_BATCH_STATE_PATH/<tipo>.json` después de cada
paso: si el proceso se corta o un lote tarda más de `LLM_BATCH_MAX_WAIT_HOURS`, la siguiente
ejecución recoge el mismo lote sin volver a pagarlo (el informe lo dice y deja los grupos en
`sin_procesar`). Se borra al terminar; respuestas de más de 6 días no se reutilizan. El coste se
apunta con `batch=1` al precio de lote, y antes de enviar cada lote se comprueba el presupuesto
diario. Con un proveedor sin lotes (OpenAI, local) se trabaja en modo síncrono, con un aviso en el
log. Solo se usa para textos públicos (la ley), nunca para datos de un usuario: el estado guarda
las peticiones. No ejecutes dos procesos sobre el mismo fichero de estado.

**Ahorro esperado (estimaciones).** Calculadas con `calcular_coste` y la tabla de precios, con
supuestos que pueden no cumplirse: 3,5 caracteres por token; una ejecución semanal de 10
deducciones autonómicas que convergen en una pasada (sistema del lector ~10.500 tokens y del revisor
~11.000, texto legal ~1.700, salida con razonamiento de 3.000 y 2.500 tokens); llamadas seguidas a
menos de 5 minutos, para que la caché se lea.

| Recodificación semanal (10 grupos) | Coste estimado | Ahorro |
|---|---:|---:|
| Antes de este cambio (Opus 5.5, caché automática) | 2,35 USD | — |
| Caché explícita del sistema | 1,38 USD | 41 % |
| + perfil recomendado (lector Sonnet 5.5, revisor Opus 5.5) | 1,01 USD | 57 % |
| + lotes, sin ningún acierto de caché dentro del lote | 0,86 USD | 63 % |
| + lotes, con aciertos como arriba (no garantizados en lotes) | 0,51 USD | 78 % |

En modo equipo con un especialista (planificador, dos investigadores de 3 vueltas, crítico de 2,
especialista de 3; entradas de 1.200 a 6.000 tokens), el perfil recomendado pasa de ~0,31 a ~0,18
USD por pregunta (−40 %), sin contar el asesor ni el verificador, que no cambian de modelo. Mira
el ahorro real en `ops consumo` (`por_modelo`, `proporcion_cache`) después de una semana.


## 6. Módulos del hogar

Los módulos (`asistente/ambitos/<clave>/`) se descubren al arrancar; no hay que registrarlos.

- `uv run python -m asistente.plataforma.cli.proactive modulos` lista los módulos cargados, sus hechos,
  herramientas (con su efecto) y fuentes, y **los que se han dejado fuera y por qué** (un módulo que no se
  importa o incumple el contrato se registra en el log con nivel ERROR y la aplicación sigue sin él).
- `MODULOS_DESACTIVADOS=luz,ayudas` apaga módulos para todos los usuarios sin tocar su código (el fiscal
  no se puede apagar).
- `MODULOS_ACTIVOS_POR_DEFECTO` (por defecto `fiscal`): los módulos activos para quien no ha elegido y, con
  `AUTH_MODE=off`, para todos los casos. `todos` los activa todos. Con cuentas, cada usuario decide en
  «Módulos» y su elección se guarda en `user_module_choices`.
- `ESPECIALISTA_MAX_STEPS` (4): pasos de modelo de cada especialista. Su consumo se registra con el
  propósito `especialista:<clave>` y cuenta en `LLM_MAX_CALLS_PER_QUESTION`.
- `run-daily` añade al resumen `modulos` (avisos creados por módulo) y `errores_modulos` solo cuando algún
  caso tiene activado un módulo además del fiscal. Un módulo que falla cuenta en `errores_modulos` y no
  para a los demás ni a los avisos fiscales.
- `run-weekly` escribe un resumen por caso y semana ISO (`resumen_semanal:AAAA-Wnn`); repetirlo no
  duplica. Lo entrega `ops deliver` como cualquier aviso, con el mismo consentimiento.
- `params-check` comprueba también las normas del BOE de las que leen los módulos (apartado `modulos` del
  informe). El contrato aún no dice cuándo leyó cada módulo su norma, así que la primera fecha vista queda
  como referencia en `MODULOS_FUENTES_STATE_PATH`; cuando se haya releído la norma y actualizado el módulo,
  `params-check --marcar-modulos-revisados` mueve la referencia. `--fail-on-stale` falla también por ellas.
  `boe-novedades` vigila esas normas como las de la calculadora. Las fuentes que no son del BOE se listan en
  `fuera_del_boe`: su formato lo vigila el adaptador de cada módulo.
- Acciones pendientes (`acciones_pendientes`, mismo fichero SQLite): las crea un especialista cuando llama a
  una herramienta `actua`; solo se ejecutan con `POST /acciones/{id}/confirmar` del dueño del caso.
- **Tarea diaria de cada módulo.** `run-daily` ejecuta, una vez y **antes** de los avisos por caso, la tarea
  diaria de cada módulo activado para algún caso (hoy: `ayudas` → `actualizar_convocatorias(hoy)`, que lee
  del BOE las convocatorias nuevas de los días aún no leídos y nunca lanza excepciones; un día que falla se
  reintenta al siguiente). El resultado aparece en `tareas_modulos` del resumen. Si nadie tiene el módulo
  activado, no se ejecuta (ni se hace ninguna petición).
- **Datos que el usuario sube a un módulo** (un extracto bancario para `suscripciones`) van por
  `POST /modulos/{clave}/herramientas/{nombre}` directamente a la herramienta: no pasan por el modelo de
  lenguaje, ni por los registros, ni por la auditoría (solo se audita el nombre de los argumentos); lo que
  se guarda es lo que la herramienta deduce (sus hechos).
- **Resumen semanal y avisos repetidos.** Dos avisos de módulos distintos con fecha de vencimiento a 3 días
  o menos y una palabra significativa común en el título (p. ej. la renovación de un seguro que avisan
  `caducidades` y `suscripciones`) se muestran juntos en el resumen («probablemente lo mismo»). No se borra
  ninguno: cada módulo conserva su aviso en la bandeja.
- **Propuestas para la declaración** (`GET /propuestas?case_id=`): cifras que un módulo conoce y que un
  dato fiscal necesita. Hoy: `hipoteca` → `pagos_vivienda_habitual_pre2013` (capital + intereses +
  amortizaciones anticipadas + sus comisiones del ejercicio según el cuadro: lo que el art. 68.1.1.º LIRPF en
  su redacción a 31-12-2012 cuenta de la financiación ajena), solo si el préstamo es de la vivienda habitual
  comprada antes de 2013 y el dato falta. Nunca se escribe sola: el usuario la acepta en «Módulos» o en el
  chat (`aceptar_propuesta`, con su «sí» literal a la cifra mostrada); un «no» queda en la memoria de
  decisiones (`fiscal:propuesta:<puente>`) y deja de ofrecerse. Fundamento y autocomprobación:
  `modulos/hipoteca/data/deduccion_vivienda.json` (para comprobarlo contra las páginas completas del BOE,
  `DEDUCCION_VIVIENDA_BOE_DIR=<carpeta con BOE-A-2006-20764.html y BOE-A-2006-20764_p20121031.html>
  uv run pytest tests/modulos/hipoteca/test_hipoteca_deduccion.py`). `params-verify` comprueba el 15 % y los
  9.040 € de `vivienda_habitual_transitoria` contra la redacción de 31-10-2012 (`--snapshot-dir` necesita
  `BOE-A-2006-20764_p20121031.html`).

### Variables de entorno de los módulos

Todas opcionales; los detalles, en `docs/modulos/<clave>.md`.

| Variable | Por defecto | Módulo | Para qué |
|---|---|---|---|
| `AYUDAS_DATA_DIR` | `./data/modulos/ayudas` | ayudas | Almacén de convocatorias leídas del BOE (fuera de git). |
| `AYUDAS_BOE_AUTOMATICO` | desactivada | ayudas | `1`: el propio `avisos` lanza la revisión diaria del BOE (innecesario con `run-daily`, que ya la lanza antes). |
| `AYUDAS_BDNS` | desactivada | ayudas | `1`: consulta la BDNS para el plazo exacto (adaptador probado solo con datos de ejemplo). |
| `AYUDAS_CLASIFICADOR_LLM` | desactivada | ayudas | `1`: clasificador LLM en los casos dudosos, si el núcleo registra uno (hoy no lo registra). |
| `LUZ_ESIOS_TOKEN` | — | luz | Token de la API de REE (ESIOS) para los precios; sin él, el módulo trabaja con lo que el usuario aporta. |
| `LUZ_UMBRAL_AHORRO_EUR` | (ver doc del módulo) | luz | Ahorro anual mínimo para avisar de un cambio de tarifa. |
| `SUSCRIPCIONES_DIAS_AVISO_RENOVACION` | `30` | suscripciones | Días de antelación del aviso de renovación (1 a 120). |
| `SUSCRIPCIONES_ETIQUETADOR_LLM` | apagado | suscripciones | `1`: etiquetador LLM de comercios, si el núcleo registra la función (hoy no la registra). |
| `HIPOTECA_DIAS_AVISO_REVISION` | `45` | hipoteca | Días de antelación del aviso de revisión del tipo. |
| `HIPOTECA_UMBRAL_AVISO_EUR` | `20` | hipoteca | Cambio de cuota mínimo para avisar de un índice nuevo. |

## Bus de eventos

Los avisos se generan cuando algo pasa (un dato del expediente cambia, se activa un módulo, sale una
norma en el BOE, se publica un índice hipotecario, se importa un extracto, llega una fecha) y solo para
los módulos afectados. Qué eventos hay y a quién despiertan: README, «Arquitectura orientada a eventos».
Todo vive en el mismo SQLite que los expedientes (`DECISION_STATE_DB_PATH`): una copia de seguridad y un
borrado de cuenta cubren también el bus.

### Quién procesa los eventos

1. **La API, en su propio proceso.** Al arrancar instala el gancho que escribe el evento en la misma
   transacción que guarda el expediente y lanza un hilo que procesa el bus nada más ocurrir un cambio (y
   cada `EVENTOS_INTERVALO_SEGUNDOS` aunque no lo haya). Basta para lo que pasa dentro de la API.
2. **El trabajador**, para lo que emiten otros procesos (`boe-novedades`, `hipoteca.indices actualizar`)
   cuando la API no está en marcha, para los temporizadores y para recalcularlos una vez al día:

   ```bash
   uv run python -m asistente.plataforma.cli.eventos worker            # continuo; SIGTERM lo para limpiamente
   uv run python -m asistente.plataforma.cli.eventos worker --una-vez  # una pasada (cron cada 5 min, ver apartado 1)
   ```

   Ejemplo con systemd (`/etc/systemd/system/hacienda-eventos.service`):

   ```ini
   [Service]
   WorkingDirectory=/srv/hacienda-asesor
   EnvironmentFile=/srv/hacienda-asesor/.env
   ExecStart=/usr/local/bin/uv run python -m asistente.plataforma.cli.eventos worker
   Restart=always
   ```

   Puede haber varios trabajadores (y la API) a la vez sobre la misma base de datos: cada entrega se
   reclama con un arriendo (`EVENTOS_ARRIENDO_SEGUNDOS`) y no la ejecutan dos a la vez. Si uno muere a
   mitad, otro la retoma al vencer el arriendo; repetirla no duplica avisos (tienen clave).
3. **`run-daily` sigue siendo la red de seguridad** (apartado 1): sin API ni trabajador, los avisos
   llegan igual, una vez al día. Si algo no se enruta bien, la pasada diaria lo recoge.

### Vigilarlo

- `ops doctor` incluye la clave `eventos` (cuando el bus se ha usado) y avisa si hay entregas en la cola
  de errores o trabajo esperando más de `EVENTOS_ATRASO_MAX_MINUTOS` (por defecto 30): suele significar
  que no hay ni API ni trabajador en marcha.
- `uv run python -m asistente.plataforma.cli.eventos estado`: atraso, entregas por estado, temporizadores,
  latido de cada trabajador, métricas por tipo de evento y módulo (número, reintentos, fallos, latencia
  desde el evento hasta el aviso y duración del módulo, p50/p95) y las 50 primeras entregas de la cola de
  errores con su error. Lo mismo, sin errores detallados, en `GET /eventos/salud`.
- Ningún informe del bus contiene datos personales: solo recuentos (y en `estado`, el nombre del módulo,
  el tipo de evento y el error del módulo).

### Cola de errores

Una entrega que falla se reintenta a los 30 s, 60 s, 120 s… (máximo 1 h; `EVENTOS_BACKOFF_SEGUNDOS`) y,
tras `EVENTOS_MAX_INTENTOS` (5) intentos, queda como `muerto`. Corrige la causa (el error está en
`estado`) y:

```bash
uv run python -m asistente.plataforma.cli.eventos reintentar          # todas
uv run python -m asistente.plataforma.cli.eventos reintentar 12 15    # solo esas
```

Un consumidor que no puede repartir un evento lo reintenta igual y, al agotar los intentos, lo deja en la
cola como `consumidor:<nombre>` y sigue con los siguientes; esa entrada es un registro, no se reencola.

### Mantenimiento

El trabajador borra a diario (y en cada `--una-vez`) las entregas terminadas y los eventos ya consumidos de
hace más de 30 días; la cola de errores se conserva hasta que la reencoles o la purgues a mano.

```bash
uv run python -m asistente.plataforma.cli.eventos purgar --dias 30    # entregas terminadas y eventos ya consumidos
uv run python -m asistente.plataforma.cli.eventos reconciliar         # recalcula los temporizadores ahora
```

### Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `EVENTOS_ACTIVOS` | `true` | `false`: no se escribe ningún evento (todo queda a la pasada diaria). |
| `EVENTOS_DESPACHO_EN_PROCESO` | `true` | `false`: la API solo escribe eventos; los procesa el trabajador. |
| `EVENTOS_INTERVALO_SEGUNDOS` | `5` | Pausa entre pasadas sin trabajo (trabajador e hilo de la API). |
| `EVENTOS_MAX_INTENTOS` | `5` | Intentos antes de la cola de errores. |
| `EVENTOS_BACKOFF_SEGUNDOS` | `30` | Primera espera tras un fallo (se dobla en cada intento, máx. 1 h). |
| `EVENTOS_ARRIENDO_SEGUNDOS` | `300` | Tiempo tras el que una entrega reclamada por un trabajador caído se retoma. |
| `EVENTOS_ATRASO_MAX_MINUTOS` | `30` | Atraso a partir del cual `ops doctor` avisa (`0`: nunca). |

## Evaluación continua

Código en `asistente/nucleo/evaluacion/`, órdenes en `python -m asistente.plataforma.cli.evaluacion` (resumen en el README,
«Evaluación continua»). El diseño completo —candidatos, revisión, base privada, estadística de la puerta— está en
[`docs/cerebro/aprendizaje.md`](cerebro/aprendizaje.md).

### Sin modelo

No las ejecuta ninguna tarea programada ni `make quality`. Lánzalas a mano antes de fusionar un cambio en
*prompts*, casos, calculadoras o el consejo:

```bash
uv run python -m asistente.plataforma.cli.evaluacion validar          # esquema, cifras esperadas, registro de prompts
uv run python -m asistente.plataforma.cli.evaluacion run --reproduccion   # guiones de referencia por el agente real
uv run python -m asistente.nucleo.evaluacion.consejo_eval validar-sesgos   # el arnés del consejo detecta cada sesgo
uv run python -m asistente.nucleo.evaluacion.consejo_eval guiones --consumo-dir reports/evaluacion/consejo
uv run python -m asistente.plataforma.cli.check_critical_regressions \
    --dataset eval_data/critical_cases/tramite_year_critical_cases.jsonl \
    --min-intent-accuracy 0.80 --min-fact-f1 0.75 --max-unsafe-rate 0.05   # casos críticos, flujo determinista
uv run python -m asistente.plataforma.cli.drift_check --rules-dir rules --snapshot-root ./data \
    --output reports/drift.json                                              # deriva de las fuentes de las reglas
```

`consejo_eval guiones` reproduce cada caso `decision` y `sesgo` con su dictamen de referencia
([`docs/consejo/evaluacion.md`](consejo/evaluacion.md)).

`validar` vuelve a ejecutar cada calculadora que nombra un caso. Si una cifra ya no coincide con la que tenía al
escribirse el caso («Deriva»), no es un fallo: la expectativa siempre es la cifra de hoy de la calculadora (puede
haber cambiado un parámetro leído del BOE), pero conviene mirar por qué (`--estricto` la convierte en error).

Los casos del cerebro del hogar declaran `requiere` (`cerebro`, `recuerdos`, `repaso`): no tienen guion, así que
`run --reproduccion` los **omite** con el motivo (no cuentan ni como aprobados ni como fallos) y se evalúan con
modelo. Un caso cuyo código no está instalado también se omite con el motivo. `validar` dice cuántos hay de cada uno.

### Con modelo

```bash
uv run python -m asistente.plataforma.cli.evaluacion run --out reports/evaluacion/base.json            # variante activa
uv run python -m asistente.plataforma.cli.evaluacion run --config candidata.json --baseline reports/evaluacion/base.json
uv run python -m asistente.plataforma.cli.evaluacion run --motor cerebro --presupuesto-usd 10   # todo por el cerebro
uv run python -m asistente.plataforma.cli.evaluacion promote --config candidata.json --presupuesto-usd 10
```

- Una **variante** es un JSON: `{"id": "breve", "prompts": {"agente": "agente@breve"}, "modelo": "…"}`; el texto
  de `agente@breve` va en `prompts/agente@breve.txt` junto al fichero o en `textos`. Puede cambiar cualquier *prompt*
  registrado: `agente`, `modulos`, `especialista`, `cerebro`, `cerebro_intencion`, `cerebro_sintesis`, `repaso` y
  `memoria_resumen` (los que son plantillas deben conservar sus campos, como `{especialistas}`).
- El agente evaluado se mide con los MISMOS propósitos que en producción (`agente`, `verificador`,
  `especialista:<clave>`, `cerebro`, `cerebro:intencion`…), así que la tabla de enrutado elige el mismo modelo que el
  asistente desplegado; el juez usa el propósito `evaluacion-juez` y el modelo configurado (el mismo para base y
  candidata). El gasto queda en `/usage` con el caso `eval:<ejecución>:<caso>:<repetición>`; rigen los límites
  diarios y `--presupuesto-usd`. Un informe que se quedó sin presupuesto está marcado como incompleto y no sirve
  para promover.
- `run` y `promote` incluyen los **casos reales aceptados** de la base privada si existe (`--sin-casos-reales` para
  no hacerlo; `--casos-reales-db` para otra ruta). Un informe que los incluye lo dice: guárdalo fuera del
  repositorio (`reports/` ya está ignorado).
- Sin índice FAISS (`--sin-corpus` o máquina sin índice) los casos que necesitan buscar fuentes (los `dgt`) no
  pueden aprobar; los de calculadoras y módulos sí.

### La puerta de `promote`: solo si no empeora

`promote` ejecuta la variante activa y la candidata sobre los mismos casos (suite + casos reales; la huella cubre
ambos) y escribe `asistente/nucleo/evaluacion/variante_activa.json` (o `EVALUACION_VARIANTE_ACTIVA_PATH`) solo si se
cumplen TODAS estas reglas:

1. **Comparables**: las dos ejecuciones completas, sobre los mismos casos, contra un modelo.
2. **Muestra mínima**: al menos `EVALUACION_GATE_MIN_CASOS` (30) casos emparejados; con menos, la decisión es
   «evidencia insuficiente» y **nunca** se promueve (`--min-casos` la cambia para una ejecución).
3. **Ningún caso crítico empeora**: ni un solo caso de una categoría `critica` en `casos/suite.json` (cálculo fiscal,
   enrutado, no sumar, confirmación de lo que actúa, regresiones reales, intención del cerebro, procedencia de las
   cifras, memoria, paso directo fiscal) puede puntuar menos que con la variante activa, mejore lo que mejore la media.
4. **No empeora en conjunto**: el intervalo del 95 % (*bootstrap* pareado, 10 000 remuestras, semilla derivada de los
   informes: reproducible) de la diferencia media por caso no baja de 0, y la prueba exacta de McNemar sobre
   aprobado/suspenso no muestra que rompa más de lo que arregla (p < 0,05). Una sola regresión sin compensar en 40
   casos ya baja el intervalo de 0; un comportamiento idéntico da [0, 0] y pasa.
5. **Presupuesto**: coste ≤ `EVALUACION_GATE_COSTE_MAX_RELATIVO` (1,10) y latencia p95 ≤
   `EVALUACION_GATE_LATENCIA_MAX_RELATIVA` (1,25) veces los de la variante activa en los mismos casos (diferencias de
   menos de 0,5 s no cuentan).

La regla es «no empeorar», no «ser estrictamente mejor»: una variante igual de buena y más barata puede ganar. La
decisión y su evidencia (estadísticos, regresiones, mejoras, coste, latencia, motivos) quedan en
`reports/evaluacion/promote-<marca>-decision.json` y su resumen en castellano en `…-decision.txt`, se promueva o no.
La variante activa guarda la huella de cada texto evaluado: si un *prompt* cambia después, `variante` y `validar`
avisan de que hay que volver a evaluarla. Para volver a la de siempre, borra ese fichero.

### Cambiar un *prompt*

El registro `asistente/nucleo/evaluacion/prompts/registro.json` guarda la huella SHA-256 de cada *prompt* integrado —
los de `asistente/nucleo/agent/prompts.py` y los del cerebro (`cerebro/prompts.py`), el repaso (`repaso/prompts.py`) y
la memoria (`recuerdos/prompts.py`)—; la prueba `tests/evaluacion/test_variantes.py` falla mientras el registro y el
código no coinciden, también con un *prompt* nuevo sin registrar. Al cambiar un *prompt*:

1. Evalúa el texto nuevo como candidata (`promote` con una variante que lo lleve en un fichero de *prompts*).
2. Copia el texto al código y regístralo con la decisión de `promote`:
   `prompts registrar --rol <rol> --informe reports/evaluacion/promote-…-decision.json` (comprueba que la candidata
   se promovió y que su informe evaluó exactamente ese texto). Borra después la variante activa si ya no hace falta.
3. Si no se puede evaluar (sin clave de modelo), `prompts sincronizar --motivo "…"` registra todos los que han
   cambiado o son nuevos y deja el motivo escrito en el registro, a la vista en la revisión del PR.

### Aprender de los errores reales

Cada «esto está mal» se convierte en un **caso candidato enmascarado** (nombre, correos, DNI/NIE, IBAN, tarjetas,
teléfonos, direcciones y códigos ocultos; las cifras se conservan) en una cola de revisión. Vienen de:

- un 👎 / «corregir» con la casilla de consentimiento de ESA respuesta marcada (como siempre);
- un 👎 sin la casilla, o las **señales débiles** de la auditoría (la puerta de cifras aún descontenta con la respuesta
  final, afirmaciones que el verificador rechazó, que el usuario vuelva a preguntar lo mismo, una abstención), solo si
  el dueño activó «aprender de los fallos» para ese expediente (`PUT /evaluacion/aprendizaje`; desactivado por
  defecto, revocable, y solo cuenta lo posterior).

La cola agrupa los duplicados (misma pregunta enmascarada y mismo tipo de fallo) y los ordena por una prioridad
explicada (categoría crítica, lo que dijo el usuario frente a lo inferido, frecuencia y dinero en juego). La revisa el
dueño del expediente en la web («Revisión de casos») o quien opera, desde la línea de órdenes o con `X-Operator-Key`
(`HACIENDA_OPERATOR_KEY`) en la API; el candidato de otra persona responde 404.

```bash
uv run python -m asistente.plataforma.cli.evaluacion senales                  # recuentos, sin contenido
uv run python -m asistente.plataforma.cli.evaluacion casos detectar           # señales débiles de quien consintió
uv run python -m asistente.plataforma.cli.evaluacion casos cola               # grupos, prioridad y motivos
uv run python -m asistente.plataforma.cli.evaluacion casos candidato cand_…   # conversación enmascarada, plan, fallo
uv run python -m asistente.plataforma.cli.evaluacion casos aceptar cand_… --desde edicion.json
uv run python -m asistente.plataforma.cli.evaluacion casos rechazar cand_… --motivo "…"
uv run python -m asistente.plataforma.cli.evaluacion casos-reales listar
uv run python -m asistente.plataforma.cli.evaluacion casos-reales comprobar   # cada caso sigue detectando su error
uv run python -m asistente.plataforma.cli.evaluacion casos-reales reproducir --config candidata.json \
    --modelo-real --presupuesto-usd 2                                         # contra el modelo: explícito y con tope
```

- **Aceptar** es describir el comportamiento esperado (`edicion.json`: categoría y `esperado`: especialistas que debe
  o no consultar, citas mínimas, cifras que no debe dar, una cifra esperada que SIEMPRE es una receta de calculadora,
  abstenerse, preguntar antes, puerta de cifras limpia, textos que debe o no decir). Se rechaza si algo aún parece
  personal (identificadores o nombres del perfil: edita la pregunta y los datos con valores ficticios que reproduzcan
  el caso), si una cifra esperada no se puede calcular o si **la respuesta que se dio pasaría** esas comprobaciones
  (control negativo; se puede forzar con `--aunque-no-reproduzca` y el aviso queda con el caso). **Rechazar** exige
  un motivo. El usuario ve ambas decisiones en su auditoría (evento `evaluacion_revision`).
- Los casos aceptados van a una **base privada** (`EVALUACION_CASOS_REALES_DB_PATH`; por defecto
  `evaluacion_casos_reales.sqlite3` junto a la de expedientes, dentro de `data/`), **nunca al repositorio**: la suite
  versionada rechaza casos de origen `feedback` o `senal`. Se ejecutan en cada `run` y `promote`, salen en
  `/me/export` y se borran con el expediente o la cuenta; borrar un dato del expediente los retira (y redacta ese dato
  en los candidatos). Inclúyela en las copias de seguridad.
- El flujo anterior sigue disponible para una valoración guardada (`casos pendientes`, `casos ver fb_…`,
  `casos borrador fb_… --out /tmp/borrador.json`, `casos descartar fb_…`), pero `casos promover fb_… --desde … \
  --confirmo-anonimizado --revisor "Nombre"` ya **no escribe en la suite**: guarda el caso en la base privada, como
  aceptar un candidato.
- El contenido de una valoración guardada sigue en la auditoría del expediente del usuario (evento
  `evaluacion_valoracion`), ahora con el informe del cerebro (plan, especialistas y su modo, cifras, puerta; de la
  memoria, solo los identificadores), la variante y el modelo de cada propósito: quien opera la revisión lee datos que
  el usuario aceptó compartir para esto; hazlo solo con ese fin. Las filas de revisión y los candidatos están en la
  misma base que los expedientes y se borran con el expediente.
- Para probar la reproducción sin datos de usuarios hay casos sintéticos con la misma forma:
  `casos-reales comprobar --desde tests/evaluacion/datos/casos_reales_sinteticos.json` (sin modelo) y, con clave,
  `casos-reales reproducir --desde tests/evaluacion/datos/casos_reales_sinteticos.json --modelo-real
  --presupuesto-usd 2 --sin-corpus --yes`. Los reales se reproducen en el servidor con las mismas órdenes, sin
  `--desde`.

### Variables de entorno

| Variable | Por defecto | Qué hace |
|---|---|---|
| `EVALUACION_VARIANTE_ACTIVA_PATH` | `asistente/nucleo/evaluacion/variante_activa.json` | Variante que ejecuta el agente; sin fichero, la de siempre. |
| `EVALUACION_CASOS_REALES_DB_PATH` | vacío (= `evaluacion_casos_reales.sqlite3` junto a `DECISION_STATE_DB_PATH`) | Base privada de los casos reales aceptados. |
| `EVALUACION_GATE_MIN_CASOS` | `30` | Casos emparejados mínimos para que `promote` decida; con menos, «evidencia insuficiente». |
| `EVALUACION_GATE_COSTE_MAX_RELATIVO` | `1.10` | Coste máximo de la candidata respecto a la variante activa. |
| `EVALUACION_GATE_LATENCIA_MAX_RELATIVA` | `1.25` | Latencia p95 máxima de la candidata respecto a la variante activa. |
| `EVALUACION_CONSEJO` | `false` | Incluye los casos del consejo (`decision`, `sesgo`) en una ejecución con modelo de la suite (son caros); `consejo_eval comparar` los ejecuta siempre. |
| `EVALUACION_CONSEJO_COSTE_RELATIVO_SIN_MEJORA` | `3.0` | «¿Merece la pena el consejo?»: veces el coste del cerebro solo que se admiten al consejo si no es peor. |
| `EVALUACION_CONSEJO_COSTE_RELATIVO_POR_PUNTO` | `0.5` | Veces adicionales por cada punto porcentual de mejora demostrada (cota inferior del IC 95 %). |
| `EVALUACION_CONSEJO_COSTE_RELATIVO_MAX` | `12.0` | Tope del coste relativo del consejo, mejore lo que mejore. |
| `EVALUACION_CONSEJO_LATENCIA_MAX_RELATIVA` | `40.0` | Latencia p95 máxima del consejo respecto al cerebro solo (trabaja en segundo plano). |
