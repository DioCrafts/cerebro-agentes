# Despliegue: procesos, variables, proxy con TLS, copias, claves y salud

Esta guía explica cómo poner HaciendaGPT en un servidor: qué procesos corren, cómo se migra el esquema de
las bases de datos, todas las variables de entorno, el proxy inverso con TLS, las copias de seguridad, la
rotación de claves y las comprobaciones de salud. Los ficheros de ejemplo están en
[`docs/despliegue/`](despliegue/): unidades de systemd, `docker-compose.yml`, `Dockerfile`, `Caddyfile`,
`nginx.conf` y una plantilla de variables (`entorno.ejemplo`).

Las tareas programadas y su vigilancia (`ops doctor`, *healthchecks*) se explican en
[operacion.md](operacion.md); la seguridad de la API, en [seguridad.md](seguridad.md); la privacidad, en
[privacidad.md](privacidad.md).

> **Qué se ha comprobado.** `docker compose config` valida la sintaxis de `docker-compose.yml` (Docker
> Compose v5.6.1, en esta máquina). **No** se han construido las imágenes ni se ha arrancado el conjunto con
> Docker, ni se han probado las unidades de systemd ni las configuraciones de Caddy y nginx en un servidor
> real: son ejemplos que hay que revisar antes de usarlos.

---

## 1. Procesos

```
                Internet (HTTPS)
                       │
          ┌────────────▼─────────────┐
          │ proxy inverso con TLS    │  Caddy o nginx: sirve la interfaz (frontend/dist)
          │  /        → estáticos    │  y pasa /api/* a la API quitando el prefijo
          │  /api/*   → api:8000     │
          └────────────┬─────────────┘
                       │ X-Forwarded-For / -Proto
          ┌────────────▼─────────────┐        ┌───────────────────────────────────┐
          │ api (uvicorn)            │        │ worker                            │
          │ peticiones, escribe      │        │ bus de eventos (servicio)         │
          │ eventos; tareas del      │        │ tareas programadas (antes, cron)  │
          │ navegador que arranca    │        │ servicios de otros paquetes       │
          └────────────┬─────────────┘        └─────────────────┬─────────────────┘
                       │        misma máquina, disco local      │
                       └──────────────► data/*.sqlite3 ◄────────┘
                                        (SQLite en modo WAL)
```

| Proceso | Orden | Qué hace |
|---|---|---|
| `api` | `uvicorn asistente.plataforma.api.api:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1` | Atiende la interfaz y la API. Escribe los eventos (cambios de perfil, módulos, normas…) en la base de datos de casos. Con `EVENTOS_DESPACHO_EN_API=true` (por defecto, como siempre) además los reparte él mismo en un hilo, para que el aviso salga en segundos. Las tareas del agente con navegador corren en el proceso de la API que las arrancó. Al arrancar, migra la base de datos de casos. |
| `worker` | `python -m asistente.plataforma.cli.plataforma worker` | Un único punto de entrada para todo lo que no es una petición: el **bus de eventos** (temporizadores, reparto a los módulos, reintentos), las **tareas programadas** que antes eran líneas de cron (tabla siguiente) y los **servicios** que registren otros paquetes (por ejemplo, un motor de tareas largas). Al arrancar, migra la base de datos de casos. Escribe su latido cada `PLATAFORMA_LATIDO_SEGUNDOS`. |
| interfaz | ficheros estáticos de `frontend/dist` (`npm run build`) | Los sirve el proxy inverso. La interfaz llama a la API en `/api` (`VITE_API_BASE` en la compilación, por defecto `/api`). |

`python -m asistente.plataforma.cli.plataforma procesos` imprime este modelo tal como está configurado (servicios,
tareas activas y su próxima ejecución, extensiones cargadas).

### 1.1 Las tareas programadas del worker

Cada tarea ejecuta **la misma orden** que su línea de cron en [operacion.md](operacion.md), envuelta en
`ops run <nombre> --` (así `ops doctor` y el *healthcheck* las ven igual que con cron), en un proceso hijo:
una tarea que falla no tumba al worker. Las horas son peninsulares (`Europe/Madrid`).

| Nombre | Cuándo | Orden (`python -m …`) | Por defecto |
|---|---|---|---|
| `hipoteca-indices` | 06:20 los días 4 y 19 | `asistente.ambitos.hipoteca.indices actualizar` | sí |
| `boe-watch` | 06:30 | `asistente.plataforma.cli.boe_watch` | sí |
| `boe-impact` | 06:45 | `asistente.plataforma.cli.proactive boe-impact --changes-log data/html/_boe_changes.jsonl --snapshot-dir data/html --yes` | sí |
| `boe-novedades` | 06:50 | `asistente.plataforma.cli.proactive boe-novedades` | sí |
| `params-autopilot` | 06:55 | `asistente.plataforma.cli.proactive params-autopilot --no-detect` | **no** (escribe en `asistente/ambitos/fiscal/data`) |
| `run-daily` | 07:00 | `asistente.plataforma.cli.proactive run-daily` | sí |
| `params-check` | 07:05 los lunes | `asistente.plataforma.cli.proactive params-check --fail-on-stale` | sí |
| `deliver` | 07:15 | `asistente.plataforma.cli.ops deliver` (se registra sola) | sí |
| `params-verify` | 07:15 los lunes | `asistente.plataforma.cli.proactive params-verify --fail-on-mismatch --no-anotar-lecturas` | sí (solo comprueba; las lecturas se anotan a mano, ver docs/operacion.md) |
| `normas-check` | 07:25 los lunes | `asistente.plataforma.cli.proactive normas-check` | sí |
| `run-weekly` | 07:30 los lunes | `asistente.plataforma.cli.proactive run-weekly` | sí |
| `formatos-probe` | 07:45 de lunes a viernes | `asistente.plataforma.cli.ops formatos probe --fail-on-problem` | sí |
| `doctor` | 09:00 | `asistente.plataforma.cli.ops doctor` | sí |
| `correo-sincronizar` | cada 30 min | `asistente.capacidades.correo sincronizar` | sí (no hace nada sin cuentas conectadas) |
| `indice-corpus` | 03:40 | `asistente.plataforma.cli.processor --incremental` | **no** (necesita el corpus y los modelos) |
| `repaso` | cada 30 min | `asistente.plataforma.cli.repaso trabajo` | sí (sin `REPASO_ACTIVO` no hace nada; recoge los lotes en curso y, cuando toca por `REPASO_DIA`/`REPASO_HORA`, escribe el repaso priorizado de cada caso: [cerebro/repaso.md](cerebro/repaso.md)) |
| `consejo` | cada 5 min | `asistente.plataforma.cli.consejo ejecutar` | sí (sin `CONSEJO_ACTIVO` no hace nada; caduca las convocatorias sin confirmar, recupera las sesiones interrumpidas, ejecuta las confirmadas —una por caso a la vez— y aplica la retención: [consejo/arquitectura.md](consejo/arquitectura.md)) |

- **Una sola ejecución por turno, con varios workers.** Antes de lanzar una tarea, el worker reclama su
  turno en la tabla `plataforma_trabajos` dentro de una transacción `BEGIN IMMEDIATE`. Si hay dos workers
  (alta disponibilidad, o un despliegue que solapa el viejo y el nuevo), solo uno la ejecuta. Un turno que no
  termina en 6 horas se da por abandonado y puede reclamarlo otro worker.
- **Turnos perdidos.** Si el worker estaba parado a la hora de una tarea (reinicio, despliegue), la ejecuta
  al volver si no han pasado más de `PLATAFORMA_RECUPERAR_HORAS` (6 por defecto); si han pasado más, espera
  al turno siguiente.
- **Elegir las tareas.** `PLATAFORMA_TRABAJOS` (vacío = las de «por defecto»; `todos`; `ninguno`; o una lista
  separada por comas) y `PLATAFORMA_TRABAJOS_DESACTIVADOS` (lista que se quita).
- **Sin worker continuo.** `python -m asistente.plataforma.cli.plataforma worker --una-vez` hace una pasada (bus de
  eventos y tareas debidas) y termina: una sola línea de cron cada 5 minutos sustituye a todas las de
  [operacion.md](operacion.md). Si ya tienes esas líneas de cron, **no** arranques además el worker con las
  mismas tareas: usa `PLATAFORMA_TRABAJOS=ninguno` (solo bus de eventos) o quita las líneas de cron.

### 1.2 El reparto de eventos: ¿en la API o solo en el worker?

`EVENTOS_DESPACHO_EN_API` (por defecto `true`, el comportamiento de siempre) deja que la API reparta los
eventos en un hilo propio justo después de cada cambio. Con un worker en marcha, ponerlo a `false` deja a la
API solo atendiendo peticiones; el aviso tarda como mucho `EVENTOS_INTERVALO_SEGUNDOS` (5 s) más. Las dos
opciones son seguras: cada entrega se reclama con un arriendo, así que nunca se ejecuta dos veces. Los
ejemplos de systemd y compose lo ponen a `false` porque arrancan el worker.

### 1.3 Cuántos procesos de API

Recomendado: **un** proceso de uvicorn (sin `--workers`). Motivos:

- El limitador de intentos de inicio de sesión y el de peticiones por minuto viven en la memoria de cada
  proceso: con N procesos, los límites efectivos se multiplican por N.
- Una tarea del navegador corre en el proceso que la arrancó (allí está la ventana de inicio de sesión o la
  sesión entregada). Su estado se comparte por la base de datos (§1.4), así que cualquier proceso responde
  a «¿cómo va?», «Detener» o «Cerrar»; pero la **captura de pantalla** y el **resumen de lo que se
  enviaría** (con valores) solo existen en la memoria del proceso que la ejecuta y nunca se guardan.
- SQLite admite un escritor a la vez; un proceso basta para una instalación doméstica o pequeña.

### 1.4 Tareas del navegador en la base de datos

Las tareas del agente con navegador (`asistente/capacidades/navegador`) están en la tabla `navegador_tareas` de la
base de datos de casos, no solo en memoria:

- Un **reinicio no las pierde**: una tarea preparada (aún sin navegador) se puede empezar después del
  reinicio; una tarea que estaba en marcha se cierra como `fallida` con el mensaje «El servicio se reinició
  mientras la tarea estaba en marcha: el navegador se cerró y no se ha enviado nada», y su final queda en el
  registro del caso.
- **Dos procesos no chocan**: arrancar una tarea la reclama en una transacción `BEGIN IMMEDIATE`, y en esa
  misma transacción se comprueban, para todo el servicio, «ya empezada», «una tarea a la vez por usuario»,
  `NAVEGADOR_MAX_SIMULTANEAS` y `NAVEGADOR_MAX_TAREAS_DIA`. «Detener» y «Cerrar» desde otro proceso quedan
  como petición y el proceso dueño las obedece en menos de un segundo (su latido).
- **Qué se guarda**: estado, pasos, mensajes y el latido del proceso dueño. Los **parámetros** que escribió
  el usuario solo mientras la tarea está `preparada` (para que otro proceso pueda empezarla), y se borran al
  empezar, al cancelarla o a los 90 minutos. Nunca la captura, el resumen con valores, la sesión ni
  credenciales. Los arranques por usuario (límite de 24 h) se guardan con la clave del usuario resumida
  (SHA-256) y se borran a las 24 horas. Todo se borra con el caso (disparador sobre `case_states`).

---

## 2. Migraciones del esquema

Antes, cada paquete creaba o alteraba sus tablas por su cuenta al abrir la conexión («`CREATE TABLE IF NOT
EXISTS`», «`ALTER` si falta la columna»). Ahora el esquema está versionado en
`asistente/plataforma/migraciones/`:

- Cada **propietario** (el paquete dueño de unas tablas) tiene migraciones numeradas 1, 2, 3… que se aplican
  en orden y una sola vez por base de datos. Lo aplicado queda en la tabla `schema_migrations`
  (propietario, versión, nombre, suma de comprobación, fecha).
- **Transaccional**: las migraciones pendientes de una llamada se aplican en una sola transacción
  `BEGIN IMMEDIATE` (en SQLite el DDL es transaccional): todas o ninguna.
- **Idempotente y segura con varios procesos**: lo aplicado se relee dentro del bloqueo de escritura; dos
  procesos que arrancan a la vez aplican cada migración una sola vez.
- **Línea base exacta**: la versión 1 de cada propietario reproduce byte a byte el esquema que creaba el
  código anterior (SQLite guarda el texto de cada `CREATE`, sangría incluida), con sus «`ALTER` si falta».
  Una base de datos creada por el código anterior se adopta tal cual. Lo comprueba
  `tests/plataforma/test_migraciones.py`: una base creada por los almacenes con las migraciones apagadas y
  otra creada solo por las migraciones tienen el mismo volcado de `sqlite_master`; y una base antigua con
  datos se actualiza sin tocar sus tablas.
- **Dónde se aplican**: al arrancar la API y el worker (toda la base de casos), y cada almacén al abrir su
  conexión (solo su propietario y aquello de lo que depende; si todo está al día, es una sola lectura).
  `MIGRACIONES_AUTOMATICAS=false` apaga ambas cosas (los almacenes vuelven a crear sus tablas como antes) y
  deja solo la orden manual.

```bash
uv run python -m asistente.plataforma.cli.plataforma migrar --estado      # versión de cada propietario
uv run python -m asistente.plataforma.cli.plataforma migrar --dry-run     # qué se aplicaría, con el SQL; no escribe
uv run python -m asistente.plataforma.cli.plataforma migrar               # aplicar
uv run python -m asistente.plataforma.cli.plataforma migrar --base doctrina   # el grafo de doctrina (otro fichero)
```

**No hay migraciones hacia atrás.** Antes de desplegar una versión con migraciones nuevas, haz una copia
(§5); para volver atrás, restaura la copia. Una migración aplicada no se edita nunca: un cambio es una
migración nueva. `migrar --estado` avisa si el código de una migración aplicada ha cambiado.

**Añadir una tabla o una columna.** En el módulo del propietario en
`asistente/plataforma/migraciones/catalogo/`, añade `Migracion(n + 1, "nombre", ("ALTER TABLE …",))`. Un
paquete nuevo añade su módulo al catálogo (o llama a `registrar(Propietario(...))`) y pone
`asegurar_esquema(conn, "<propietario>")` donde abre su conexión.

### 2.1 Inventario de tablas

Base `casos` = `DECISION_STATE_DB_PATH`; base `doctrina` = `DOCTRINE_GRAPH_PATH`.

| Propietario | Paquete | Base | Tablas | Índices y disparadores | Versión | Contenido |
|---|---|---|---|---|---|---|
| `decision` | `asistente.nucleo.decision` | casos | `case_states`, `audit_events` | `idx_case_states_user`, `idx_case_states_updated`, `idx_audit_case_time` | 1 | Expedientes (estado del caso en JSON) y su registro de auditoría. |
| `auth` | `asistente.plataforma.auth` | casos | `users`, `auth_sessions`, `user_consents`, `case_owners`, `user_module_choices` | `idx_auth_sessions_user`, `idx_user_consents_user`, `idx_case_owners_user`, `idx_user_module_choices_user` | 1 | Cuentas, sesiones (hash del token), consentimientos, dueño de cada caso y módulos elegidos (las preferencias de módulos viven aquí). |
| `proactive` | `asistente.nucleo.proactive` | casos | `notifications` | `idx_notifications_case` | 1 | Avisos de cada caso (bandeja), únicos por (case_id, key). |
| `proactive_entrega` | `asistente.nucleo.proactive` | casos | `notification_deliveries` | | 1 | Qué aviso se entregó por qué canal. |
| `acciones` | `asistente.nucleo.modulos` | casos | `acciones_pendientes` | `idx_acciones_case` | 1 | Acciones de los módulos pendientes de confirmación. |
| `memoria` | `asistente.nucleo.memoria` | casos | `decisiones`, `decisiones_historial` | `idx_decisiones_case`, `idx_decisiones_historial_case`, `decisiones_borrar_con_caso` | 2 | Decisiones del usuario y su historial. La v2 (disparador) espera a que exista `case_states`. |
| `evaluacion` | `asistente.nucleo.evaluacion` | casos | `evaluacion_feedback`, `evaluacion_candidatos`, `evaluacion_aprendizaje` | `idx_evaluacion_feedback_case`, `evaluacion_feedback_borra_con_el_caso`, `idx_evaluacion_candidatos_case`, `idx_evaluacion_candidatos_grupo`, `evaluacion_candidatos_borra_con_el_caso` | 4 | Valoraciones de respuestas (el contenido vive en `audit_events`), casos candidatos enmascarados de la cola de revisión y el consentimiento por expediente para aprender de los fallos. Los casos reales ACEPTADOS van a una base privada aparte (`EVALUACION_CASOS_REALES_DB_PATH`, por defecto `evaluacion_casos_reales.sqlite3` junto a esta): cópiala también. |
| `eventos` | `asistente.nucleo.eventos` | casos | `eventos`, `eventos_cursores`, `eventos_entregas`, `eventos_temporizadores`, `eventos_trabajadores` | `idx_eventos_case`, `idx_entregas_estado`, `idx_entregas_case`, `idx_temporizadores_fecha` | 1 | Bus de eventos. |
| `correo` | `asistente.capacidades.correo` | casos | `correo_conexiones`, `correo_oauth_estados`, `correo_elementos`, `correo_propuestas`, `correo_contactos` | `idx_correo_elementos_fecha` | 1 | Gmail: token cifrado, estados OAuth, elementos derivados, propuestas, contactos. |
| `consumo` | `asistente.plataforma.ops` | casos | `llm_usage` | `idx_llm_usage_ts` | 1 | Llamadas al LLM: tokens y coste estimado. |
| `navegador` | `asistente.capacidades.navegador` | casos | `navegador_tareas`, `navegador_inicios` | `idx_navegador_tareas_case`, `idx_navegador_inicios`, `navegador_borrar_con_caso` | 2 | Tareas del agente con navegador (§1.4). |
| `plataforma` | `asistente.plataforma` | casos | `plataforma_procesos`, `plataforma_trabajos` | | 1 | Latido de los workers y turnos de las tareas programadas. |
| `tareas` | `asistente.nucleo.tareas` | casos | `tareas`, `tareas_historial`, `tareas_senales` | `idx_tareas_case`, `idx_tareas_estado`, `idx_tareas_historial`, `idx_tareas_historial_case`, `idx_tareas_senales`, `tareas_borrar_con_caso` | 2 | Tareas de larga duración: plan, pasos, historial (sin valores) y señales del bus. La v2 (disparador) espera a que exista `case_states`. |
| `push` | `asistente.plataforma.push` | casos | `push_suscripciones`, `push_consentimientos` | `idx_push_suscripciones_propietario`, `idx_push_consentimientos`, `push_borrar_suscripciones_con_usuario` | 1 | Avisos en el móvil (Web Push): dispositivos suscritos y consentimiento de cada usuario. |
| `recuerdos_hindsight` | `asistente.nucleo.recuerdos` | casos | `recuerdos_pausa` | `recuerdos_pausa_borrar_con_caso`, `recuerdos_pausa_borrar_con_usuario` | 2 | Memoria de conversaciones ([cerebro/memoria.md](cerebro/memoria.md)). Aquí solo está el interruptor «pausar la memoria» de cada usuario; los recuerdos viven en Hindsight, fuera de SQLite (§5). La v1 borra las cinco tablas y los dos disparadores de la memoria anterior y sus filas de `schema_migrations`. Se borra con el caso y con la cuenta (la v2, disparadores, espera a que existan `case_states` y `users`). Depende de `decision` y `auth`. |
| `repaso` | `asistente.nucleo.repaso` | casos | `repaso_pasadas` | `idx_repaso_pasadas_lote`, `repaso_borrar_con_caso` | 2 | Repaso proactivo del cerebro ([cerebro/repaso.md](cerebro/repaso.md)): una fila por caso y periodo con los puntos priorizados tal como se redactaron, el texto aceptado por la puerta, el aviso que escribió y, mientras hay un lote en curso, su identificador (los `custom_id` son *hashes*, sin datos del caso). Marca de agua de la pasada siguiente. Se borra con el caso (la v2, disparador, espera a que exista `case_states`); se purga a los 120 días salvo la última de cada caso. |
| `consejo` | `asistente.nucleo.consejo` | casos | `consejo_sesiones` | `idx_consejo_sesiones_caso`, `idx_consejo_sesiones_estado`, `consejo_borrar_con_caso`, `consejo_borrar_con_usuario` | 1 | Consejo del hogar ([consejo/arquitectura.md](consejo/arquitectura.md)): una fila por sesión con su estado, el arriendo del trabajador, la estimación y el tope de coste que aceptó el usuario, el coste gastado y, en JSON, el expediente seudonimizado, las propuestas, las críticas anónimas, los rankings y el dictamen. Se borra con el caso y con la cuenta (disparadores); las terminadas se purgan a los `CONSEJO_RETENCION_DIAS`. Depende de `decision` y `auth`. |
| `doctrina` | `asistente.conocimiento.doctrine` | doctrina | `nodos`, `alias`, `aristas` | `aristas_destino` | 1 | Grafo de doctrina (datos públicos; se reconstruye con `cli.doctrine_graph build`). |

Además, `sqlite_sequence` (de SQLite, por los `AUTOINCREMENT`) y `schema_migrations` (el registro).

**Datos que no están en SQLite** (cópialos también, §5): ficheros JSON y directorios bajo `data/`
(`_jobs_state.json`, `_formatos_state.json`, `formatos/`, `_estado_normas.json`, `recodificacion/`,
`_lotes/`, `_modulos_fuentes_state.json`, `navegador/` con diagnósticos redactados, `modulos/ayudas/`
con las convocatorias del BOE), el índice de búsqueda (`FAISS_INDEX_PATH`, reconstruible) y los datos
legales versionados del repositorio (`asistente/ambitos/fiscal/data`, `asistente/ambitos/*/data`). La memoria de
conversaciones tampoco está aquí: vive en Hindsight, por defecto en el PostgreSQL embebido de
`~/.pg0/instances/hindsight-embed-<HINDSIGHT_PERFIL>/` del usuario que ejecuta la API, o en el servidor de
`HINDSIGHT_URL` ([cerebro/memoria.md](cerebro/memoria.md)).

Cada almacén con tablas propias está en el catálogo (`asistente/plataforma/migraciones/catalogo/`), con su
línea base y la llamada `asegurar_esquema`; `tests/plataforma/test_migraciones.py` comprueba que esta tabla
dice lo mismo que el catálogo (propietarios, paquetes, tablas, índices y disparadores, versión).

### 2.2 Preparación para PostgreSQL (no implementado)

El código sigue siendo solo SQLite. Lo que cambiaría, en general:

- `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT GENERATED ALWAYS AS IDENTITY`.
- Fechas en `TEXT` ISO 8601 → `timestamptz` (las comparaciones de texto `latido < ?` pasan a ser de fechas).
- `payload_json TEXT` → `jsonb` (permite índices sobre campos del JSON).
- Enteros usados como booleanos (`granted`, `activo`, `detener`…) → `boolean`.
- `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`; `MAX(a, b)` escalar → `GREATEST(a, b)`.
- `BEGIN IMMEDIATE` (bloqueo de escritura de toda la base) → transacciones normales con
  `SELECT … FOR UPDATE [SKIP LOCKED]` en la fila reclamada, o `pg_advisory_xact_lock` para el motor de
  migraciones.
- Disparadores «borrar con el caso» → claves foráneas `REFERENCES case_states(case_id) ON DELETE CASCADE`.
- `PRAGMA journal_mode=WAL`, `PRAGMA secure_delete`, `PRAGMA foreign_keys`, `PRAGMA table_info` y las
  consultas a `sqlite_master` desaparecen (`information_schema` / `to_regclass` para las comprobaciones;
  borrado seguro: disco cifrado y `VACUUM`; las claves foráneas siempre se aplican).
- Copias: `pg_dump` / recuperación a un instante (PITR) en lugar de la API de copia de SQLite.
- `schema_migrations` y el motor sirven tal cual, con el bloqueo consultivo.

Por tabla:

| Tabla | Qué cambiaría en PostgreSQL |
|---|---|
| `case_states` | `payload_json` → `jsonb`; `updated_at` → `timestamptz`; `version` sigue sirviendo para la concurrencia optimista. |
| `audit_events` | `id` identidad; `event_json` → `jsonb`; la FK `ON DELETE CASCADE` ya existe y en PostgreSQL siempre se aplica. La redacción de un hecho (reescribir eventos) es un `UPDATE … SET event_json = …` igual. |
| `users` | `email` → `citext UNIQUE` (o índice único sobre `lower(email)`); `created_at` → `timestamptz`. |
| `auth_sessions` | fechas → `timestamptz`; FK a `users` con `ON DELETE CASCADE`. |
| `user_consents`, `user_module_choices` | `seq` identidad; `granted`/`activo` → `boolean`; FK a `users`. |
| `case_owners` | FK a `users` y a `case_states`. |
| `notifications` | `seq` identidad; `INSERT OR IGNORE` → `ON CONFLICT (case_id, key) DO NOTHING`; `payload_json` → `jsonb`. |
| `notification_deliveries` | FK a `notifications(id)`; `delivered_at` → `timestamptz`. |
| `acciones_pendientes` | `seq` identidad; `payload_json` → `jsonb`; FK a `case_states` en vez del borrado explícito. |
| `decisiones`, `decisiones_historial` | `seq` identidad; `jsonb`; el disparador pasa a FK `ON DELETE CASCADE` (la redacción al borrar una decisión sigue en código). |
| `evaluacion_feedback` | `seq` identidad; `jsonb`; disparador → FK `ON DELETE CASCADE` (`case_id` admite NULL). |
| `evaluacion_candidatos`, `evaluacion_aprendizaje` | `seq` identidad; `jsonb`; disparador → FK `ON DELETE CASCADE`; `INSERT OR IGNORE` por `clave` → `ON CONFLICT (clave) DO NOTHING`. |
| `eventos` | `id` identidad: el cursor por `id` supone que el orden de `id` es el de confirmación, cierto en SQLite (un escritor) pero **no** en PostgreSQL; hay que pasar a una secuencia por confirmación (p. ej. `pg_current_xact_id` con *snapshot*) o a `LISTEN/NOTIFY` + tabla de entregas. |
| `eventos_cursores` | fechas → `timestamptz`. |
| `eventos_entregas` | el arriendo (`bloqueado_por`, `bloqueado_hasta`) → `SELECT … FOR UPDATE SKIP LOCKED`; fechas → `timestamptz`; `duracion_ms`/`latencia_ms` → `double precision`. |
| `eventos_temporizadores`, `eventos_trabajadores` | fechas → `timestamptz`; `ON CONFLICT … DO UPDATE` funciona igual. |
| `correo_conexiones`, `correo_oauth_estados` | fechas → `timestamptz`; el token sigue cifrado en la aplicación (Fernet). |
| `correo_elementos`, `correo_propuestas`, `correo_contactos` | `jsonb`; FK a `case_states` en vez del gancho de borrado. |
| `llm_usage` | `id` identidad; `cost_usd REAL` → `numeric(12,6)`; los cuatro `ALTER … ADD COLUMN` de la línea base se integran en el `CREATE`. |
| `navegador_tareas` | `jsonb`; fechas → `timestamptz`; la reclamación → `SELECT … FOR UPDATE` de la fila y recuento de activas en la misma transacción (nivel `SERIALIZABLE` o bloqueo consultivo para el límite global). |
| `navegador_inicios` | `seq` identidad; `momento REAL` (segundos) → `timestamptz`. |
| `plataforma_procesos`, `plataforma_trabajos` | fechas → `timestamptz`; la reclamación del turno es un `UPDATE … WHERE` atómico que funciona igual. |
| `nodos`, `alias`, `aristas` | sin cambios relevantes (datos públicos, reconstruibles); `confianza REAL` → `double precision`. |

---

## 3. Variables de entorno

Todas las que lee el código: `asistente/plataforma/settings.py` y las que leen los módulos al usarse. «Secreto»
quiere decir: solo en el entorno o en un gestor de secretos, nunca en el repositorio ni en registros.
Las rutas relativas lo son al directorio de trabajo del proceso (`WorkingDirectory`).

### 3.1 Almacenamiento y rutas

| Variable | Por defecto | Para qué |
|---|---|---|
| `DECISION_STATE_DB_PATH` | `./data/decision_state.sqlite3` | Base de datos de casos: casos, cuentas, avisos, eventos, correo, tareas del navegador… (§2.1). |
| `DOCTRINE_GRAPH_PATH` | `./data/doctrine_graph.sqlite3` | Grafo de doctrina (opcional). |
| `NORM_STATUS_PATH` | `./data/_estado_normas.json` | Estado de convalidación de los decretos-leyes (`proactive normas-check`). |
| `RULES_DIR` | `<repositorio>/rules` | Reglas de decisión. |
| `FAISS_INDEX_PATH` | `.faiss` | Índice de búsqueda del corpus. |
| `FAISS_TRUSTED_INDEX` | `false` | Permitir cargar un índice con *pickle* (solo de origen propio). |
| `JOBS_STATE_PATH` | `./data/_jobs_state.json` | Estado de las tareas programadas (`ops doctor`). |
| `FORMAT_STATE_PATH` | `./data/_formatos_state.json` | Estado de la vigilancia de formatos. |
| `FORMAT_DIR` | `./data/formatos` | Muestras, especificaciones y propuestas de formatos. |
| `RECODE_PROPOSALS_PATH` | `./data/recodificacion` | Propuestas de recodificación de parámetros. |
| `LLM_BATCH_STATE_PATH` | `./data/_lotes` | Estado reanudable de los lotes del LLM. |
| `MODULOS_FUENTES_STATE_PATH` | `./data/_modulos_fuentes_state.json` | Fecha base de las fuentes del BOE de los módulos. |
| `NAVEGADOR_DIR` | `./data/navegador` | Diagnósticos redactados del agente con navegador (por caso, 30 días). |
| `AYUDAS_DATA_DIR` | `./data/modulos/ayudas` | Convocatorias detectadas en el BOE (datos públicos). |
| `EVALUACION_VARIANTE_ACTIVA_PATH` | vacío (= `asistente/nucleo/evaluacion/variante_activa.json`) | Variante de prompt/modelo activa. |
| `PRJ_DATA_DIR` | `~` | Raíz de datos del procesador del corpus (`cli.processor`). |

### 3.2 Plataforma, procesos y bus de eventos

| Variable | Por defecto | Para qué |
|---|---|---|
| `MIGRACIONES_AUTOMATICAS` | `true` | Migrar al arrancar la API/worker y al abrir cada almacén (§2). |
| `EVENTOS_DESPACHO_EN_API` | `true` | La API reparte eventos en su propio hilo (§1.2). |
| `PLATAFORMA_TRABAJOS` | vacío (= los de por defecto) | Tareas del worker: `todos`, `ninguno` o lista (§1.1). |
| `PLATAFORMA_TRABAJOS_DESACTIVADOS` | vacío | Tareas que se quitan de esa lista. |
| `PLATAFORMA_RECUPERAR_HORAS` | `6` | Ventana para ejecutar un turno perdido. |
| `PLATAFORMA_LATIDO_SEGUNDOS` | `30` | Latido del worker y frecuencia del planificador. |
| `PLATAFORMA_EXTENSIONES` | vacío | Módulos extra con `registrar_en_plataforma(registro)` (servicios o tareas del worker). |
| `PLATAFORMA_CLAVE_COPIAS` | vacío | **Secreto.** Claves Fernet de las copias cifradas, separadas por comas; la primera cifra (§5). |
| `EVENTOS_ACTIVOS` | `true` | Escribir eventos (con `false`, solo la pasada diaria produce avisos). |
| `EVENTOS_DESPACHO_EN_PROCESO` | `true` | Interruptor general del reparto dentro de la API (debe estar a `true` además del anterior). |
| `EVENTOS_INTERVALO_SEGUNDOS` | `5` | Pausa entre pasadas del bus sin trabajo. |
| `EVENTOS_MAX_INTENTOS` | `5` | Intentos de una entrega antes de la cola de errores. |
| `EVENTOS_BACKOFF_SEGUNDOS` | `30` | Espera base entre reintentos (se dobla; máximo 1 h). |
| `EVENTOS_ARRIENDO_SEGUNDOS` | `300` | Arriendo de una entrega reclamada. |
| `EVENTOS_ATRASO_MAX_MINUTOS` | `30` | `ops doctor` avisa si el trabajo espera más (0 = nunca). |
| `HEALTHCHECK_URL` | vacío | URL de «hombre muerto» que se llama tras cada tarea vigilada ([operacion.md](operacion.md)). |
| `HEALTHCHECK_URL_<TAREA>` | vacío | La misma URL para una tarea concreta (p. ej. `HEALTHCHECK_URL_RUN_DAILY`). |

### 3.3 API, seguridad y cuentas

| Variable | Por defecto | Para qué |
|---|---|---|
| `AUTH_MODE` | `off` | `required` en cualquier servicio con más de un usuario: cuentas y aislamiento de casos. |
| `HACIENDA_API_KEY` | vacío (sin clave) | **Secreto.** Clave en `X-API-Key` para toda la API. |
| `HACIENDA_OPERATOR_KEY` | vacío | **Secreto.** Clave en `X-Operator-Key` para los datos globales (`/usage`, `/eventos/salud`, `/plataforma/salud`) con `AUTH_MODE=required`. |
| `AUTH_SESSION_TTL_HOURS` | `24` | Validez de una sesión. |
| `AUTH_SCRYPT_N` | `131072` | Coste de scrypt para las contraseñas. |
| `AUTH_LOGIN_MAX_ATTEMPTS` | `5` | Intentos fallidos de inicio de sesión por ventana (por proceso). |
| `AUTH_LOGIN_WINDOW_SECONDS` | `900` | Ventana de esos intentos. |
| `PRIVACY_NOTICE_URL` | vacío | Aviso de privacidad del operador que se enlaza en el registro. |
| `API_RATE_LIMIT_PER_MIN` | `0` (sin límite) | Peticiones por minuto y cliente (por proceso). |
| `API_MAX_BODY_BYTES` | `33554432` (32 MB) | Tamaño máximo de una petición. |
| `API_MAX_QUERY_CHARS` | `20000` | Longitud máxima de una pregunta. |
| `API_MAX_HISTORY_CHARS` | `60000` | Historial que se reenvía al modelo (caracteres). |
| `API_MAX_HISTORY_MESSAGES` | `40` | Historial que se reenvía al modelo (mensajes). |
| `API_DOCS_PUBLICAS` | vacío (= solo sin cuentas) | Servir `/docs`, `/redoc`, `/openapi.json`. |
| `VITE_API_BASE` | `/api` | (Compilación de la interfaz React) prefijo de la API. |
| `VITE_API_PROXY` | `http://127.0.0.1:8000` | (Solo desarrollo con Vite) destino del proxy `/api`. |
| `MEMORIA_CONVERSACIONES` | `true` | Memoria de conversaciones ([cerebro/memoria.md](cerebro/memoria.md)): guardar en Hindsight lo que escribe el usuario y recordarlo en las preguntas siguientes. Con `false` no se guarda ni se recuerda nada nuevo y no arranca el demonio (lo guardado se puede seguir viendo, exportando y borrando, con Hindsight en marcha). |
| `HINDSIGHT_URL` | vacío | Un servidor de Hindsight ya en marcha (`http://host:puerto`). Vacío: la app arranca y reutiliza un demonio local embebido (`hindsight-all`, proceso propio en `127.0.0.1` con PostgreSQL embebido en `~/.pg0/`). Con un servidor externo, `HINDSIGHT_LLM_*`, `HINDSIGHT_EMBEDDINGS` y `HINDSIGHT_IDIOMA` no se aplican: valen los ajustes de ese servidor. En Docker, obligatorio (§8). |
| `HINDSIGHT_PERFIL` | `cerebro` | Perfil del demonio embebido: nombre de su configuración (`~/.hindsight/profiles/`) y de su PostgreSQL (`~/.pg0/instances/hindsight-embed-<perfil>/`). |
| `HINDSIGHT_LLM_PROVEEDOR` | `anthropic` | Proveedor del modelo con el que Hindsight extrae los hechos (`anthropic` u `openai`). La clave sale de `ANTHROPIC_API_KEY` u `OPENAI_API_KEY` por el entorno; no se escribe en el perfil. |
| `HINDSIGHT_LLM_MODELO` | `claude-haiku-5-5` | Modelo de esas llamadas de extracción y consolidación (se pagan aparte de las del asistente). |
| `HINDSIGHT_EMBEDDINGS` | `BAAI/bge-m3` | Modelo local de *embeddings* de Hindsight, multilingüe (el de Hindsight por defecto solo sirve para inglés). |
| `HINDSIGHT_IDIOMA` | `spanish` | Diccionario de PostgreSQL para la búsqueda BM25 de Hindsight. |
| `HINDSIGHT_PRESUPUESTO` | `mid` | Esfuerzo de cada recuperación (`low`, `mid` o `high`). |
| `HINDSIGHT_TIMEOUT_S` | `8` | Segundos como mucho por llamada a Hindsight; si se pasa, ese turno va sin memoria. |
| `REPASO_ACTIVO` | `false` | Repaso proactivo del cerebro ([cerebro/repaso.md](cerebro/repaso.md)): con `true` y un modelo configurado, el resumen semanal (o diario) lo escribe el repaso priorizado, con su puerta; sin modelo, su versión determinista. Con `false`, el resumen semanal determinista de siempre. |
| `REPASO_FRECUENCIA` | `semanal` | `semanal` (un resumen por semana ISO, la clave del resumen semanal) o `diaria` (uno por día). |
| `REPASO_DIA` | `0` | Día de la pasada semanal (0 = lunes … 6 = domingo); si ese día no corrió, se hace el siguiente de la misma semana. |
| `REPASO_HORA` | `07:00` | Hora peninsular (`HH:MM`) a partir de la que la tarea `repaso` hace la pasada del periodo. |
| `REPASO_POR_LOTES` | `true` | La tarea `repaso` envía las peticiones de todos los casos en UN lote de la API de lotes del proveedor (más barato; lo recoge en una pasada posterior, como mucho 12 h). Sin lotes o si algo falla, una llamada síncrona por caso. |
| `REPASO_MAX_PUNTOS` | `7` | Puntos como mucho en un repaso (1–12); el resto se cuenta («y N avisos más en tu bandeja»). |
| `CONSEJO_ACTIVO` | `true` | Consejo del hogar ([consejo/arquitectura.md](consejo/arquitectura.md)) para decisiones difíciles: el cerebro lo propone (o el usuario lo pide) con su coste estimado y solo se ejecuta si el usuario lo confirma. Con `false` no se ofrece ni se puede convocar (lo guardado se sigue viendo, exportando y borrando). |
| `CONSEJO_FAMILIAS` | vacío | Familias de modelos de los miembros, separadas por comas (`anthropic,openai`); se usan las que tienen credenciales. Vacío: todos con el proveedor configurado (diversidad por papel, instrucciones y esfuerzo). |
| `CONSEJO_MIEMBROS` | `prudente,fiscal,liquidez_familia,abogado_del_diablo` | Asientos del consejo, en orden (entre 3 y 7; también `largo_plazo`); un identificador desconocido se ignora con un aviso ([consejo/deliberacion.md](consejo/deliberacion.md)). |
| `CONSEJO_SEMILLA` | vacío | Semilla (entero) del orden anónimo en que cada miembro ve las propuestas; vacío: cada sesión la deriva de su identificador (reproducible y distinta entre sesiones). |
| `CONSEJO_MAX_PARALELO` | `3` | Miembros trabajando a la vez en una fase. |
| `CONSEJO_FACTOR_TOPE` / `CONSEJO_TOPE_MAX_USD` | `2.0` / `3.00` | Tope duro de una sesión = estimación × factor, nunca por encima del máximo; una sesión cuya estimación supera el máximo no se ofrece. Al llegar al tope no se hace ninguna llamada más: el consejo dictamina con lo que tiene (un dictamen parcial escrito por código, que lo dice) o la sesión termina como fallida y lo explica. |
| `CONSEJO_MAX_LLAMADAS` | `150` | Llamadas al modelo de una sesión entera (incluidos los especialistas del expediente). |
| `CONSEJO_EN_SEGUNDO_PLANO` | `true` | Despliegues de un solo proceso: al confirmar, la sesión empieza ya en un hilo de la API (la tarea `consejo` es la red de seguridad). |
| `CONSEJO_PLAZO_FASE_S` | `600` | Segundos que tiene una fase de la deliberación para todos sus miembros a la vez; el miembro que no ha terminado se queda fuera de esa fase. Debe ser menor que `CONSEJO_ARRIENDO_S`. |
| `CONSEJO_ARRIENDO_S` / `CONSEJO_MAX_INTENTOS` | `900` / `2` | Segundos que un trabajador retiene una sesión en marcha (se renuevan en cada fase) y ejecuciones como mucho de una sesión interrumpida. |
| `CONSEJO_CADUCIDAD_HORAS` | `72` | Una convocatoria sin confirmar ni rechazar caduca a las tantas horas. |
| `CONSEJO_RETENCION_DIAS` | `365` | Días que se guardan las sesiones terminadas (con su expediente y deliberación). |
| `CONSEJO_IMPORTE_GRANDE_EUR` | `10000` | Importe a partir del cual el detector de decisiones difíciles lo cuenta como «importe grande». |

### 3.4 Modelos de lenguaje

| Variable | Por defecto | Para qué |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | Proveedor del agente: `anthropic`, `openai` o `local`. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | vacío | **Secreto.** Credencial de Anthropic. |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Modelo de Claude. |
| `ANTHROPIC_EFFORT` | `high` | Esfuerzo de razonamiento. |
| `ANTHROPIC_REFUSAL_FALLBACK` | `true` | Reintento en otro modelo si un clasificador rechaza la petición. |
| `OPENAI_API_KEY` | vacío | **Secreto.** Credencial de OpenAI. |
| `OPENAI_AGENT_MODEL` | `gpt-4o-mini` | Modelo de OpenAI (agente y extractor de hechos si no hay otro). |
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | Servidor local compatible con OpenAI. |
| `LOCAL_LLM_MODEL` | vacío | Modelo local. |
| `LOCAL_LLM_API_KEY` | `local` | Clave del servidor local. |
| `LOCAL_LLM_TIMEOUT` | `300` | Tiempo máximo de una llamada local (s). |
| `VERIFIER_PROVIDER` / `VERIFIER_MODEL` | vacío (= el del agente) | Proveedor y modelo del verificador. |
| `AGENT_MAX_STEPS` | `8` | Turnos del agente por pregunta. |
| `AGENT_MAX_REVISIONS` | `1` | Reescrituras tras un rechazo del verificador. |
| `AGENT_SEARCH_DEFAULT_K` | `6` | Resultados por búsqueda del agente. |
| `AGENT_TEAM_MODE` | `auto` | Equipo de investigadores: `auto`, `always`, `never`. |
| `SUBAGENT_MAX_STEPS` | `4` | Turnos de cada investigador y del crítico. |
| `SUBAGENTS_PARALLEL` | `true` | Investigadores en paralelo. |
| `ESPECIALISTA_MAX_STEPS` | `4` | Turnos de un especialista de módulo. |
| `DECISION_EXTRACTOR` | vacío (automático) | Extractor de hechos (`anthropic`, `openai`, `none`…). |
| `DECISION_EXTRACTOR_MODEL` | vacío | Modelo del extractor. |
| `DECISION_EXTRACTOR_EFFORT` | `low` | Esfuerzo del extractor con Claude. |
| `DECISION_EXTRACTOR_TIMEOUT` | `30` | Tiempo máximo del extractor (s). |
| `DECISION_EXTRACTOR_MAX_RETRIES` | `2` | Reintentos del extractor. |
| `DECISION_DEBUG_MODE` | `false` | Trazas de depuración del motor de decisión. |
| `PLANNER_WEIGHT_SANCTION_RISK` / `_URGENCY` / `_USER_EFFORT` / `_COMPLIANCE_IMPACT` | `0.35` / `0.25` / `0.15` / `0.25` | Pesos del planificador de preguntas. |

### 3.5 Enrutado de modelos y gasto

| Variable | Por defecto | Para qué |
|---|---|---|
| `LLM_ROUTING_PROFILE` | vacío | Perfil de enrutado por propósito (`recomendado`). |
| `LLM_MODEL_SMALL` / `_MEDIUM` / `_LARGE` | vacío | Modelo de cada nivel. |
| `LLM_EFFORT_SMALL` / `_MEDIUM` / `_LARGE` | vacío | Esfuerzo de cada nivel con Claude. |
| `LLM_ROUTES` | vacío | JSON propósito → nivel o modelo. |
| `LLM_ROUTE_<PROPOSITO>` | vacío | Lo mismo para un propósito. |
| `LLM_ESCALATION` | `true` | Reintentar en el nivel siguiente una salida inválida. |
| `LLM_CACHE_TTL` | `5m` | Vida de la caché de prompts de Claude (`5m` o `1h`). |
| `LLM_DAILY_BUDGET_USD` | vacío | Presupuesto diario del servicio. |
| `LLM_DAILY_BUDGET_USD_PER_USER` | vacío | Presupuesto diario por usuario. |
| `LLM_MAX_CALLS_PER_QUESTION` | vacío | Llamadas máximas por pregunta. |
| `LLM_PRICES_PATH` | vacío | Tabla de precios propia. |
| `RECODE_BATCH` | `false` | Recodificación por la API de lotes. |
| `LLM_BATCH_POLL_SECONDS` | `60` | Consulta de lotes en curso. |
| `LLM_BATCH_MAX_WAIT_HOURS` | `24` | Espera máxima de un lote. |

### 3.6 Búsqueda en el corpus

| Variable | Por defecto | Para qué |
|---|---|---|
| `EMBEDDING_MODEL` | `data/models/qwen3-emb-mlx-bf16` | Modelo de *embeddings* (MLX). |
| `EMBEDDING_BATCH_SIZE` | `32` | Lote del *embedder*. |
| `EMBEDDING_MAX_SEQ_LENGTH` | `512` | Tokens máximos por fragmento. |
| `RERANKER_ENABLED` | `true` | Reordenación con *cross-encoder*. |
| `RERANKER_MODEL` | `data/models/qwen3-reranker-mlx-bf16` | Modelo de reordenación. |
| `RERANKER_TOP_K` | `8` | Documentos tras reordenar. |
| `RERANKER_FIRST_STAGE_K` | `30` | Candidatos de la primera fase. |
| `CONTEXTUAL_EMBEDDINGS_ENABLED` | `false` | Prefijo contextual al indexar (invalida el índice). |
| `CONTEXTUAL_MODEL_PATH` | `data/models/qwen3-1.7b-instruct-mlx-bf16` | Modelo del prefijo. |
| `CONTEXTUAL_MAX_TOKENS` | `80` | Tokens del prefijo. |

### 3.7 Avisos fuera de la aplicación

| Variable | Por defecto | Para qué |
|---|---|---|
| `NOTIFY_WEBHOOK_URL` | vacío | **Secreto.** Webhook del operador. |
| `NOTIFY_WEBHOOK_FORMAT` | `json` | `json` o `text` (ntfy). |
| `NOTIFY_SMTP_HOST` / `NOTIFY_SMTP_PORT` | vacío / `587` | Servidor de correo. |
| `NOTIFY_SMTP_USER` / `NOTIFY_SMTP_PASSWORD` | vacío | **Secreto** (la contraseña). |
| `NOTIFY_SMTP_STARTTLS` | `true` | STARTTLS tras conectar. |
| `NOTIFY_EMAIL_FROM` / `NOTIFY_EMAIL_TO` | vacío | Remitente y destinatario. |
| `PUSH_VAPID_PRIVATE_KEY` | vacío (sin push) | **Secreto.** Clave VAPID privada (Web Push). |
| `PUSH_VAPID_PUBLIC_KEY` | vacío (se deriva) | Clave VAPID pública. |
| `PUSH_VAPID_SUBJECT` | vacío | Contacto VAPID (`mailto:` o `https://`). |
| `PUSH_HOSTS_PERMITIDOS` | vacío | Servicios de push extra permitidos. |

### 3.8 Recodificación de parámetros y vigilancia de formatos

| Variable | Por defecto | Para qué |
|---|---|---|
| `RECODE_AUTO_APPLY` | `false` | Aplicar sin persona una propuesta que pasó todo. |
| `RECODE_MAX_ITER` | `3` | Pasadas por grupo. |
| `RECODE_MAX_PER_RUN` | `10` | Grupos por ejecución. |
| `RECODE_MODEL` / `RECODE_REVIEWER_MODEL` | vacío | Modelos del lector y del revisor. |
| `RECODE_AUTOPILOT` | `false` | `boe-novedades` lanza el piloto al terminar. |
| `FORMAT_WATCH` | `true` | Vigilar si cada formato externo se sigue entendiendo. |
| `FORMAT_AUTO_APPLY` | `false` | Aplicar sin persona una especificación nueva. |
| `FORMAT_MAX_ITER` | `3` | Rondas de diagnóstico. |
| `FORMAT_MODEL` / `FORMAT_REVIEWER_MODEL` | vacío | Modelos del diagnóstico y de la reflexión. |
| `FORMAT_DOCUMENT_SKELETONS` | `false` | Guardar esqueletos anonimizados de documentos ilegibles. |

### 3.9 Módulos del hogar

| Variable | Por defecto | Para qué |
|---|---|---|
| `MODULOS_DESACTIVADOS` | vacío | Módulos que el operador quita a todos. |
| `MODULOS_ACTIVOS_POR_DEFECTO` | `fiscal` | Módulos activos sin elección del usuario (`todos` posible). |
| `AYUDAS_BOE_AUTOMATICO` | vacío (no) | `1`/`true`: leer el BOE de ayudas en la pasada diaria. |
| `AYUDAS_BDNS` | vacío (no) | `1`/`true`: consultar la BDNS. |
| `AYUDAS_CLASIFICADOR_LLM` | vacío (no) | `1`/`true`: clasificador con LLM de convocatorias. |
| `HIPOTECA_DIAS_AVISO_REVISION` | `45` | Días de antelación del aviso de revisión del tipo. |
| `HIPOTECA_UMBRAL_AVISO_EUR` | vacío | Ahorro mínimo para avisar. |
| `SUSCRIPCIONES_DIAS_AVISO_RENOVACION` | `30` | Antelación del aviso de renovación. |
| `SUSCRIPCIONES_ETIQUETADOR_LLM` | vacío (no) | `1`: etiquetado con LLM. |
| `LUZ_ESIOS_TOKEN` | vacío | **Secreto.** Token de la API de ESIOS (precios de la luz). |
| `LUZ_UMBRAL_AHORRO_EUR` | `50` | Ahorro anual mínimo para avisar de un cambio de tarifa. |

### 3.10 Correo (Gmail)

| Variable | Por defecto | Para qué |
|---|---|---|
| `CORREO_GOOGLE_CLIENT_ID` | vacío | Cliente OAuth de Google. |
| `CORREO_GOOGLE_CLIENT_SECRET` | vacío | **Secreto.** |
| `CORREO_REDIRECT_URI` | vacío | `https://<dominio>/api/correo/oauth/callback` (detrás del proxy, con `/api`). |
| `CORREO_CLAVE_CIFRADO` | vacío | **Secreto.** Claves Fernet (separadas por comas) que cifran los *refresh tokens* (§6). |
| `CORREO_URL_RETORNO` | `/` | Adónde vuelve el navegador tras el consentimiento. |
| `CORREO_VENTANA_DIAS` | `30` (1–90) | Días de correo que se leen. |
| `CORREO_MAX_MENSAJES` | `300` (1–2000) | Mensajes por sincronización. |
| `CORREO_CONSERVACION_DIAS` | `180` (30–730) | Conservación de los elementos derivados. |
| `CORREO_CLASIFICADOR_LLM` | vacío (no) | `1`: clasificador con LLM. |
| `CORREO_DOMINIOS_OFICIALES_EXTRA` | vacío | Dominios oficiales extra (separados por `;`). |
| `CORREO_AUTHSERV_ID` | `mx.google.com` | Servidor de autenticación cuyos resultados se creen. |
| `CORREO_GOOGLE_AUTH_URL` / `_TOKEN_URL` / `_REVOKE_URL`, `CORREO_GMAIL_API_URL` | URLs de Google | Solo para pruebas con un doble local. |

### 3.11 Agente con navegador

| Variable | Por defecto | Para qué |
|---|---|---|
| `NAVEGADOR_ACTIVO` | `false` | Interruptor general. |
| `NAVEGADOR_MODOS` | `ventana,sesion` | Cómo inicia sesión el usuario. |
| `NAVEGADOR_CHROMIUM` | vacío | Ejecutable de Chromium. |
| `NAVEGADOR_MAX_TAREAS_DIA` | `6` | Tareas por usuario en 24 h (en todo el servicio). |
| `NAVEGADOR_MAX_SIMULTANEAS` | `2` | Tareas a la vez en todo el servicio. |
| `NAVEGADOR_REPARAR_SELECTORES` | `false` | Reparación de selectores con LLM. |
| `NAVEGADOR_ORIGENES_SUSTITUTOS` | vacío | Solo pruebas y demostraciones. |
| `NAVEGADOR_EXIGIR_CHROMIUM` | vacío | Solo pruebas: fallar si no hay Chromium. |

### 3.12 Recolección y mantenimiento del corpus

| Variable | Por defecto | Para qué |
|---|---|---|
| `BOE_DOWNLOAD_DELAY` | `1.0` | Pausa entre descargas del BOE (s). |
| `BOLETIN_DOWNLOAD_DELAY` | `1.5` | Boletines autonómicos. |
| `TEAC_DOWNLOAD_DELAY` | `1.5` | TEAC. |
| `CENDOJ_DOWNLOAD_DELAY` | `2.0` | CENDOJ. |
| `DGT_CA_BUNDLE` | vacío | Certificados para la DGT. |
| `PLAYWRIGHT_BROWSERS_BINARY_PATH` | vacío | Navegador del *crawler*. |

---

## 4. Proxy inverso con TLS

La API no se expone directamente: escucha en `127.0.0.1:8000` (systemd) o en la red interna de compose, y
un proxy inverso termina TLS, sirve la interfaz y pasa `/api/*` quitando el prefijo.

- **uvicorn `--proxy-headers --forwarded-allow-ips <ip del proxy>`**: así la API ve la IP real del cliente
  (`X-Forwarded-For`) para los límites de peticiones y de inicio de sesión, y el esquema `https`
  (`X-Forwarded-Proto`). Pon en `--forwarded-allow-ips` **solo** la dirección del proxy: con `*` en un puerto
  accesible desde fuera, cualquiera falsearía su IP y esquivaría los límites. En compose, `*` es aceptable
  porque el puerto 8000 no se publica (solo `expose`).
- **Tamaño de las peticiones**: el proxy debe admitir algo más que `API_MAX_BODY_BYTES` (32 MB), para que
  sea la API la que responda 413 con su mensaje (`client_max_body_size 40m` / `max_size 40MB`).
- **Streaming**: la respuesta del agente (`/agent/stream`) y el progreso del consejo y del navegador (`…/eventos`)
  llegan como Server-Sent Events (`text/event-stream`): sin búfer en
  el proxy (`proxy_buffering off` / `flush_interval -1`) y un tiempo de lectura amplio.
- **PWA**: `sw.js`, `index.html` y `manifest.webmanifest` con `Cache-Control: no-cache` (cada versión nueva
  se instala); `/assets/*` con caché larga (llevan *hash*).
- **HSTS** y TLS 1.2+; la API ya pone sus propias cabeceras de seguridad.
- **OAuth de Gmail**: `CORREO_REDIRECT_URI=https://<dominio>/api/correo/oauth/callback`.
- **Misma origen**: la interfaz y la API comparten dominio, así que no hace falta CORS (la lista de
  orígenes de la API solo incluye los de desarrollo).

Ejemplos: [`despliegue/Caddyfile`](despliegue/Caddyfile) (TLS automático con Let's Encrypt) y
[`despliegue/nginx.conf`](despliegue/nginx.conf) (certificados de certbot).

---

## 5. Copias de seguridad

Las bases SQLite están en modo WAL y la API y el worker escriben a la vez: **no copies el fichero con `cp`**
(la copia puede quedar a medias o sin lo que está en el `-wal`). Usa la copia en caliente:

```bash
# Clave de las copias (una vez): guárdala en el gestor de secretos Y fuera de la máquina.
uv run python -m asistente.plataforma.cli.plataforma nueva-clave
export PLATAFORMA_CLAVE_COPIAS=...

uv run python -m asistente.plataforma.cli.plataforma copia --destino /var/backups/hacienda
uv run python -m asistente.plataforma.cli.plataforma copia --base doctrina --destino /var/backups/hacienda
```

- Usa la API de copia de SQLite (consistente aunque haya escrituras), comprueba la copia con
  `PRAGMA integrity_check` y la cifra con Fernet (AES-128-CBC + HMAC-SHA256) con la primera clave de
  `PLATAFORMA_CLAVE_COPIAS`. La copia en claro solo existe en un directorio temporal privado; el fichero
  final (`<nombre>-<fecha>.sqlite3.fernet`) tiene permisos `0600`.
- Sin clave, la orden se niega; `--sin-cifrar` solo si el destino es un disco ya cifrado.
- El ejemplo [`hacienda-copia.timer`](despliegue/systemd/hacienda-copia.timer) la lanza a diario y conserva
  30 días. Sube además las copias **fuera de la máquina** (otro proveedor, otra cuenta): ya van cifradas.
- La base de casos tiene datos personales y de salud fiscal de los usuarios: las copias están sujetas al
  RGPD como los originales (registro de tratamientos, plazo de conservación, borrado: una cuenta borrada
  sigue en las copias hasta que caducan; decláralo en el aviso de privacidad).
- Las copias grandes se cifran en memoria; para bases de varios GB, usa `--sin-cifrar` hacia un directorio
  temporal privado y cifra con `age` o `gpg` en flujo.
- Copia también lo que no está en SQLite (§2.1): `data/` completo salvo las bases (que van por esta vía).
- La memoria de conversaciones (Hindsight) no entra en `copia`. Con el demonio embebido, páralo
  (`uv run hindsight-embed -p cerebro daemon stop`), copia `~/.pg0/instances/hindsight-embed-cerebro/` y cifra la
  copia como las demás: guarda las palabras de los usuarios. Con `HINDSIGHT_URL`, copia el PostgreSQL de ese
  servidor con su propio procedimiento.

**Restaurar** (con los procesos parados):

```bash
systemctl stop hacienda-worker hacienda-api
uv run python -m asistente.plataforma.cli.plataforma descifrar-copia /var/backups/hacienda/decision_state-20261116T031700Z.sqlite3.fernet /tmp/restaurada.sqlite3
sqlite3 /tmp/restaurada.sqlite3 'PRAGMA integrity_check'
mv data/decision_state.sqlite3 data/decision_state.sqlite3.antes && rm -f data/decision_state.sqlite3-wal data/decision_state.sqlite3-shm
install -m 600 -o hacienda /tmp/restaurada.sqlite3 data/decision_state.sqlite3 && shred -u /tmp/restaurada.sqlite3
uv run python -m asistente.plataforma.cli.plataforma migrar --estado      # la versión del código puede pedir migraciones
systemctl start hacienda-api hacienda-worker
```

Prueba la restauración de vez en cuando: una copia que nunca se ha restaurado no es una copia.

---

## 6. Rotación de claves

| Clave | Cómo rotarla |
|---|---|
| `CORREO_CLAVE_CIFRADO` | Admite varias claves separadas por comas (la primera cifra, todas descifran). 1) `python -m asistente.capacidades.correo nueva-clave`; 2) pon `CORREO_CLAVE_CIFRADO=<nueva>,<antigua>` y reinicia api y worker; 3) `python -m asistente.capacidades.correo rotar-clave` (vuelve a cifrar cada *refresh token* con la nueva); 4) quita la antigua y reinicia. Si la clave se ha filtrado: además, revoca las conexiones (los usuarios vuelven a conectar Gmail). |
| `PLATAFORMA_CLAVE_COPIAS` | Igual: `nueva-clave`; `PLATAFORMA_CLAVE_COPIAS=<nueva>,<antigua>`; `python -m asistente.plataforma.cli.plataforma recifrar-copias /var/backups/hacienda/*.fernet`; quita la antigua cuando no quede ninguna copia cifrada con ella (también las de fuera de la máquina). |
| Sesiones de usuario | No hay un secreto de servidor que rotar: cada sesión es un token aleatorio del que solo se guarda el SHA-256, y caduca a las `AUTH_SESSION_TTL_HOURS`. Para cerrar **todas** las sesiones (sospecha de robo, cambio de política): `python -m asistente.plataforma.cli.plataforma revocar-sesiones`. |
| `HACIENDA_API_KEY`, `HACIENDA_OPERATOR_KEY` | Admiten una sola clave: cambia el valor en el entorno y en quien la envía (el proxy o el operador) a la vez, y reinicia la API (unos segundos de 401 para quien use la antigua). |
| `PUSH_VAPID_*` | Cambiar la clave invalida las suscripciones existentes: cada dispositivo debe volver a activar los avisos. Hazlo solo si la clave se ha filtrado. |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LUZ_ESIOS_TOKEN`, `NOTIFY_SMTP_PASSWORD`, `NOTIFY_WEBHOOK_URL`, `CORREO_GOOGLE_CLIENT_SECRET` | Crea la nueva en el proveedor, cámbiala en el entorno, reinicia api y worker, y revoca la antigua en el proveedor. |

El fichero de entorno (`/etc/hacienda/entorno` o `.env`) debe ser legible solo por root y el usuario del
servicio (`chmod 640`, `root:hacienda`).

---

## 7. Comprobaciones de salud

| Qué | Cómo | Para |
|---|---|---|
| La API responde | `GET /health` (sin clave ni límite) | Balanceador, `healthcheck` de compose, monitor externo. |
| La API y su base | `GET /plataforma/salud` (clave de operador con cuentas) | Migraciones pendientes, workers vivos, última ejecución de cada tarea programada. |
| El worker late | `python -m asistente.plataforma.cli.plataforma salud --exigir-worker` (código 1 si no hay un worker con latido reciente o hay migraciones pendientes) | `healthcheck` del contenedor del worker, monitor. |
| El bus de eventos | `GET /eventos/salud` (operador) o `python -m asistente.plataforma.cli.eventos estado` | Atraso, cola de errores, temporizadores. |
| Las tareas corrieron | `ops doctor` (tarea `doctor` del worker) y `HEALTHCHECK_URL` | Avisa si una tarea no ha corrido o ha fallado ([operacion.md](operacion.md)). |
| Métricas | `GET /metrics` (Prometheus) | Peticiones, errores, latencias. |
| Esquema | `python -m asistente.plataforma.cli.plataforma migrar --estado` | Versión de cada propietario. |

El `healthcheck` de `docker-compose.yml` usa `/health` para la API y `salud --exigir-worker` para el
worker.

---

## 8. Puesta en marcha paso a paso

### Con systemd

```bash
# Usuario y código
useradd --system --home /srv/hacienda-asesor hacienda
git clone <repositorio> /srv/hacienda-asesor && cd /srv/hacienda-asesor
uv sync --frozen --no-dev
(cd frontend && npm ci && npm run build)
install -d -o hacienda -m 700 /srv/hacienda-asesor/data /var/backups/hacienda
# Entorno (plantilla: docs/despliegue/entorno.ejemplo)
install -d /etc/hacienda && install -m 640 -g hacienda docs/despliegue/entorno.ejemplo /etc/hacienda/entorno
# Unidades
cp docs/despliegue/systemd/* /etc/systemd/system/ && systemctl daemon-reload
sudo -u hacienda .venv/bin/python -m asistente.plataforma.cli.plataforma migrar
systemctl enable --now hacienda-api hacienda-worker hacienda-copia.timer
# Proxy: docs/despliegue/nginx.conf o Caddyfile
```

Si antes usabas las líneas de cron de [operacion.md](operacion.md), quítalas al arrancar el worker (o deja
el worker con `PLATAFORMA_TRABAJOS=ninguno`).

### Con Docker Compose

```bash
cp docs/despliegue/entorno.ejemplo docs/despliegue/.env && chmod 600 docs/despliegue/.env   # y rellénalo
docker compose -f docs/despliegue/docker-compose.yml --env-file docs/despliegue/.env up -d --build
docker compose -f docs/despliegue/docker-compose.yml exec worker python -m asistente.plataforma.cli.plataforma salud
```

- `api` y `worker` usan la misma imagen y el mismo volumen `datos` (la base SQLite): deben correr en la
  **misma máquina** y sobre un disco local; nunca un volumen de red (NFS, SMB), que rompe los bloqueos de
  SQLite.
- Las copias: `docker compose … exec worker python -m asistente.plataforma.cli.plataforma copia --destino
  /srv/hacienda-asesor/data/copias` y sácalas del volumen; o monta un volumen de copias.
- Los datos legales del repositorio que actualizan `hipoteca-indices` y `params-autopilot` (el trabajo
  `params-verify` del worker solo comprueba) se escriben dentro del contenedor y se pierden al reconstruir la imagen. Nada los sube al repositorio:
  ejecuta esas órdenes en una copia de trabajo, revisa el cambio (`params-lecturas` para las lecturas) y
  súbelo a mano (ver [operacion.md](operacion.md), apartado 4).
- El agente con navegador necesita Chromium en la imagen de la API (`playwright install --with-deps
  chromium`) y, en modo `ventana`, una pantalla: en un servidor, usa solo el modo `sesion`.
- La memoria de conversaciones necesita un servidor de Hindsight: pon su dirección en `HINDSIGHT_URL`. Sin ella,
  `api` y `worker` arrancarían cada uno su propio demonio embebido, con los datos dentro del contenedor, y se
  perderían al reconstruirlo. Si no vas a montar ese servidor, deja `MEMORIA_CONVERSACIONES=false`.

### Actualizar desde `hacienda_gpt` (paquete renombrado a `asistente`)

- Las unidades de systemd, el `Dockerfile` y `docker-compose.yml` de `docs/despliegue/` ya usan
  `asistente.plataforma.…`: vuelve a copiarlos (`cp docs/despliegue/systemd/* /etc/systemd/system/ &&
  systemctl daemon-reload`) y `ReadWritePaths` apunta ahora a `asistente/ambitos/hipoteca/data`.
- Las líneas de cron y los scripts con `python -m hacienda_gpt.…` siguen funcionando durante una versión (el
  paquete de compatibilidad `hacienda_gpt` las redirige y avisa en stderr con la ruta nueva); cámbialas antes
  de la siguiente.
- Nada de lo guardado cambia: ni las bases de datos (las sumas de las migraciones ya aplicadas son las mismas),
  ni los nombres de los ficheros legales en el registro de lecturas y en el estado de la recodificación
  (`modulos/<ámbito>/data/…`), ni los consumidores del bus. Una variante promovida en
  `hacienda_gpt/evaluacion/variante_activa.json` se sigue leyendo mientras no exista la nueva
  (`asistente/nucleo/evaluacion/variante_activa.json`).

---

## 9. App instalable (PWA) y avisos en el móvil

La interfaz (`frontend/dist`) es una aplicación instalable: `manifest.webmanifest`, iconos en `icons/` y un
*service worker* en `sw.js`. Lo que el despliegue tiene que cumplir:

- **HTTPS** y la interfaz en la **raíz del dominio** (`/`): el manifiesto declara `scope` y `start_url` `/`, y
  el *service worker* se registra en `/sw.js` con ámbito `/`. Sin HTTPS no hay *service worker* ni *push*.
- **Cabeceras**: `sw.js`, `index.html` y `manifest.webmanifest` sin caché larga (`no-cache`), `assets/*` con
  caché larga (ver §4; los ejemplos de Caddy y nginx ya lo hacen). Si `sw.js` se sirviera con caché larga,
  los usuarios tardarían en ver «Hay una versión nueva».
- **La API, siempre en `/api/`** (o en otro dominio): el *service worker* no intercepta nada que no sea un
  fichero de la build o la página de inicio, así que `/api/*` y las vueltas de OAuth van a la red.
- **Avisos en el móvil (Web Push)**: genera el par de claves una vez y guárdalas como secreto:

  ```bash
  uv run python -m asistente.plataforma.push.vapid
  # PUSH_VAPID_PRIVATE_KEY=…  (secreto)   PUSH_VAPID_PUBLIC_KEY=…   PUSH_VAPID_SUBJECT=mailto:…
  ```

  `ops deliver` (o el worker) entrega entonces por el canal `push` los avisos `urgente` y `aviso` de quien los
  haya activado, con una frase fija sin datos personales. El servidor necesita salida HTTPS a los servicios de
  *push* (`fcm.googleapis.com`, `updates.push.services.mozilla.com`, `web.push.apple.com` y `*.push.apple.com`,
  `*.notify.windows.com`); otro servicio: `PUSH_HOSTS_PERMITIDOS=host1,.sufijo2`.
- **Rotar las claves VAPID** invalida todas las suscripciones: cada usuario tiene que volver a activar los
  avisos en sus dispositivos. Hazlo solo si la clave privada se ha expuesto.
- **`ops doctor`** con el canal `push`: sin cuentas, avisa en los dispositivos del operador con una frase fija
  («Hay una tarea programada con problemas…»); con cuentas no hay dispositivo del operador y el canal `push`
  cuenta como error de envío del informe (los demás canales siguen funcionando).
- **Pendiente**: el envío inmediato de los avisos urgentes al escribirse en la bandeja
  (`asistente.plataforma.push.envio.avisar_al_escribir`) está escrito y probado, pero falta conectarlo en
  `NotificationStore.add`; mientras tanto salen con la siguiente ejecución de `ops deliver`.
