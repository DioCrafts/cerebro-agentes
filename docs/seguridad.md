# Seguridad: revisión antes de abrir el servicio a usuarios

> Revisión interna del código (noviembre de 2026), hecha antes de que el servicio reciba usuarios reales.
> No es una auditoría externa ni una prueba de intrusión. Cada hallazgo se ha **demostrado** (prueba
> automática que fallaba antes de la corrección, petición contra el servidor en marcha o medición) o se
> apoya en un camino de código concreto que se cita con fichero y línea. Lo que no se pudo comprobar
> está en la última sección. La privacidad (qué datos, a quién, cuánto tiempo) está en
> `docs/privacidad.md`; la revisión anterior, más general, en `docs/security/review.md`.

## Alcance y método

- **Código**: la API FastAPI (`hacienda_gpt/api/api.py` y `routers/*`), las cuentas
  (`hacienda_gpt/auth/`: contraseñas, sesiones, límites, dependencias, propiedad de expedientes,
  `AUTH_MODE`, clave de API), cada endpoint y su aislamiento entre usuarios (también los que reciben
  el expediente por *query* o en el cuerpo), las subidas de ficheros (documentos, extractos, tiques),
  los lectores de PDF/XLSX/XML/OFX, toda petición de red que dependa de datos de usuario, el agente
  con navegador, la entrega de avisos, los caminos de inyección de instrucciones hacia el modelo,
  secretos y errores, CORS, cabeceras, SQL, rutas de fichero, deserialización, el frontend, el coste
  de los modelos y los *workflows* de GitHub.
- **Cómo**: lectura del código; pruebas nuevas que reproducen cada problema (fallan con el código
  anterior —se ejecutaron contra el commit `25965c0`— y pasan con la corrección); la API arrancada
  en local con `AUTH_MODE=required` y bases de datos temporales, sondeada con `curl` (dos cuentas,
  accesos cruzados, cuerpos enormes, cabeceras); mediciones de memoria con límites de espacio de
  direcciones para no tumbar la máquina.
- **Pruebas**: `tests/auth/test_seguridad_api.py` (API) y
  `tests/modulos/suscripciones/test_seguridad_ficheros.py` (ficheros), además de las pruebas de
  aislamiento que ya existían en `tests/auth/test_auth_api.py`.

## Modelo de amenazas

**Qué se protege.** Los expedientes (datos fiscales, familiares y de salud —discapacidad—, del
cónyuge y de los hijos), el rastro de auditoría (con el texto de cada pregunta), los extractos y
documentos que se suben (se procesan en memoria y no se guardan), las sesiones y contraseñas, las
claves del proveedor del modelo y el presupuesto de gasto en modelos, y la disponibilidad del
servicio.

**Quién ataca.**

| Actor | Qué puede hacer de partida | Qué buscaría |
|---|---|---|
| Anónimo en Internet | Llamar a cualquier ruta; registrarse (no hay verificación de correo) | Datos de otros, gastar el presupuesto de modelos, tumbar el proceso, adivinar contraseñas |
| Usuario registrado malicioso | Todo lo anterior con sesión; subir ficheros; hablar con el asesor | Leer o cambiar expedientes ajenos, ficheros que agoten memoria, gasto sin límite |
| Contenido no fiable que llega al modelo | Texto de documentos subidos, fragmentos recuperados, descriptores de movimientos bancarios (`etiqueta` de un servicio), páginas web (solo para el reparador de selectores) | Que el modelo escriba datos falsos o prepare acciones que el usuario no pidió |
| Sitio web visitado por el agente con navegador | Responder lo que quiera, redirigir, ofrecer formularios | Sacar al navegador de la lista de orígenes, enviar datos |
| Colaborador o PR desde un *fork* | Ejecutar código en CI | Leer secretos, escribir en el repositorio |
| Quien obtiene el fichero SQLite o una copia | Leerlo todo | Todo |

**Fronteras de confianza.** (1) Navegador ↔ API: la sesión es un *bearer token* en una cabecera
(no hay *cookies*), así que CSRF no aplica; el token vive en `localStorage` y, por tanto, una XSS lo
robaría. (2) API ↔ proveedor del modelo: lo que se envía y con qué consentimiento está en
`docs/privacidad.md`. (3) Modelo ↔ herramientas: el modelo solo puede **leer**, **preparar** o
proponer; una herramienta `efecto="actua"` nunca se ejecuta desde el modelo, solo se convierte en
acción pendiente que confirma el dueño del expediente con su sesión (`POST /acciones/{id}/confirmar`).
(4) Navegador automatizado ↔ webs externas: lista de orígenes por guion, sin POST en la fase del
agente, *service workers* bloqueados y *websockets* interceptados (`hacienda_gpt/navegador/seguridad.py`,
`ejecutor.py`). (5) CI ↔ repositorio.

**Fuera de alcance del código** (lo resuelve el operador): TLS, el proxy inverso, el cifrado del
disco y de las copias, la custodia de las claves y el acceso a la máquina.

## Hallazgos

Escala: **crítica** (compromiso de datos de otros usuarios o de la máquina sin esfuerzo), **alta**
(caída del servicio o daño grave con una sola petición de un usuario cualquiera), **media**
(abuso con impacto real pero acotado, o fallo de una garantía declarada), **baja** (endurecimiento o
fuga de información menor). No se ha encontrado ningún hallazgo crítico: el aislamiento entre
usuarios se sostiene en todas las rutas probadas.

| ID | Sev. | Estado | Qué | Dónde |
|---|---|---|---|---|
| S-01 | alta | corregido | Un XLSX de pocos KB agota la memoria (columna `ZZZZZ`, hoja de 8 M filas, DOCTYPE oculto) | `hacienda_gpt/modulos/suscripciones/tablas.py` |
| S-02 | alta | corregido | Un camt.053 de 15 MB se expande a 1 GB: el control de DOCTYPE solo miraba los primeros 10 000 caracteres | `hacienda_gpt/modulos/suscripciones/camt.py` |
| S-03 | media | corregido | Sin límite de tamaño del cuerpo de las peticiones | `hacienda_gpt/api/security.py`, `api.py` |
| S-04 | media | corregido | El límite diario de gasto por usuario no se aplicaba a documentos ni a `/cases/{id}/turn` | `hacienda_gpt/api/api.py` |
| S-05 | media | corregido | El limitador de peticiones se esquivaba inventando `X-API-Key` | `hacienda_gpt/api/security.py` |
| S-06 | media | corregido | Altas de cuenta ilimitadas por dirección y *password spraying* sin límite | `hacienda_gpt/api/routers/auth.py`, `auth/ratelimit.py` |
| S-07 | media | corregido | Cada derivación scrypt reserva 128 MB y el acceso no exige sesión: ~5 GB con 40 peticiones simultáneas | `hacienda_gpt/auth/passwords.py` |
| S-08 | baja | corregido | `/usage` y `/eventos/salud` (cifras de todo el servicio) abiertas a cualquiera con `AUTH_MODE=required` | `routers/consumo.py`, `routers/eventos.py` |
| S-09 | baja | corregido | Respuestas sin cabeceras de seguridad ni `Cache-Control: no-store` | `hacienda_gpt/api/security.py` |
| S-10 | media | **abierto** (producto) | El consentimiento «avisos por correo» envía los avisos del usuario al buzón o *webhook* **del operador** | `hacienda_gpt/cli/ops.py:85`, `proactive/entrega.py` |
| S-11 | baja | corregido en parte | Mensajes de error con el texto de la excepción | `api.py` (flujo NDJSON corregido; 503 abiertos) |
| S-12 | baja | corregido | `llm_usage` conservaba usuario y expediente tras borrar la cuenta y no estaba documentada | `hacienda_gpt/ops/consumo.py`, `routers/auth.py` |
| S-13 | baja | corregido en parte | *Workflows* sin `permissions:` explícitos; acciones fijadas por etiqueta, no por SHA | `.github/workflows/*.yml` |
| S-14 | media | **abierto** | `/qa` (flujo antiguo) y el extractor de OpenAI llaman al modelo sin medir ni presupuesto | `api.py:433`, `llm/chain.py:261`, `decision/fact_extractor.py:423` |
| S-15 | media | **abierto** (producto) | Pregunta e historial sin límite de longitud: una sola pregunta puede costar una ventana de contexto completa por paso | `api.py:232`, `api.py:392-393`, `api.py:453-454` |
| S-16 | baja | **abierto** (fichero de otro agente) | `registrar_hechos` escribe hechos sin comprobar que salen de palabras del usuario | `hacienda_gpt/agent/tools.py:135` |
| S-17 | baja | **abierto** | El indexador incremental deserializa FAISS (pickle) sin `FAISS_TRUSTED_INDEX` | `hacienda_gpt/processor/document_loader.py:869` |
| S-18 | baja | **abierto** (producto) | `/docs`, `/redoc` y `/openapi.json` públicos | `api.py:130` |

### S-01 (alta, corregido): el lector de XLSX se podía tumbar con ficheros diminutos

`suscripciones_analizar_extracto` (`POST /modulos/suscripciones/herramientas/...`, cualquier usuario
que active el módulo) lee el XLSX con un lector propio. Tres problemas:

1. **Referencia de columna sin límite.** `_columna("ZZZZZZZZZ1")` devolvía 5·10¹² y la fila se
   rellenaba con `[None] * posición`. Un fichero de 487 bytes provocaba `MemoryError`; con 199 filas
   en la columna `ZZZZZ` (12 M celdas cada una) el proceso pediría ~19 GB y el sistema lo mataría.
2. **Amplificación por compresión.** Cada parte podía ocupar 50 MB descomprimida y se parseaba entera
   con `ET.fromstring`: un XLSX de **70 KB** con 8 M filas vacías tardó **16 s** y llegó a **1,3 GB**
   de memoria (medido).
3. **DOCTYPE oculto.** El control de `<!DOCTYPE`/`<!ENTITY` solo miraba los primeros 4096 bytes: un
   comentario largo delante lo esquivaba.

**Corrección** (`tablas.py`): columnas hasta `XFD` (16 384, el máximo de una hoja); la hoja y los
textos compartidos se leen en *streaming* (`_elementos`: cada elemento se separa del árbol al
terminar, profundidad máxima 32, como mucho 65 536 elementos por fila); topes de 100 000 filas,
2 M celdas y 500 000 textos; el libro, sus relaciones y los estilos, que se parsean enteros, hasta
2 MB; la lectura de cada parte se corta en el límite aunque el ZIP declare otro tamaño; celdas
malformadas → `ImportacionError` (antes, error 500); DOCTYPE rechazado en todo el prólogo con
`hacienda_gpt/utils/xml_seguro.py`. El mismo fichero de 70 KB se rechaza ahora en 0,3 s.

**Pruebas**: `test_a_cell_beyond_the_last_column_is_refused`,
`test_a_doctype_behind_a_long_comment_is_still_refused_in_xlsx`,
`test_too_many_rows_stop_early_and_in_bounded_memory`, `test_an_oversized_row_or_deep_nesting_is_refused`,
`test_malformed_cells_are_an_import_error_not_a_crash` (fallaban antes) y, como guardas de regresión,
`test_elements_outside_rows_do_not_accumulate` y `test_a_part_that_lies_about_its_size_cannot_decompress_past_the_limit`
(el `zipfile` de la biblioteca estándar ya cortaba por CRC; ahora además se corta en el límite).
Todas corren con un tope de espacio de direcciones de 1 GB, para que una regresión falle en vez de
tumbar la máquina de CI.

### S-02 (alta, corregido): entidades XML en camt.053

`leer_camt` rechazaba DOCTYPE solo en los primeros 10 000 caracteres. `xml.etree` no resuelve
entidades externas (no hay XXE) y el expat incluido (2.9.0) limita la amplificación de las internas
a 100 veces la entrada, pero con 20 MB de entrada permitidos eso son 2 GB: un camt.053 de **15 MB**
con un comentario delante y entidades anidadas llegó a **1,1 GB** de memoria en 2 s (medido), por
petición. **Corrección**: `hacienda_gpt/utils/xml_seguro.py` pregunta al propio expat si hay
declaración de DOCTYPE o de entidades en cualquier punto del prólogo y se detiene en el primer
elemento (coste despreciable); `camt.py` y `tablas.py` lo usan. **Prueba**:
`test_a_doctype_behind_a_long_comment_is_still_refused_in_camt`, `test_safe_parser_reads_ordinary_xml_and_rejects_any_dtd`.

Los demás lectores de XML (`ops/formatos_boe.py`, `proactive/novedades.py`, `modulos/hipoteca/indices.py`,
`modulos/ayudas/convocatorias.py`) leen respuestas del BOE por HTTPS, no ficheros de usuarios; se
pueden pasar a `parse_seguro` por defensa en profundidad, pero no hay camino de usuario hasta ellos.

### S-03 (media, corregido): sin límite de tamaño del cuerpo

Ni uvicorn ni Starlette limitan el cuerpo: FastAPI lee el JSON entero en memoria antes de validarlo
(los límites de 10 MB de documentos o 20 MB de extractos se comprobaban **después** de leer y
decodificar). **Corrección**: `BodySizeLimitMiddleware` (ASGI puro, el más externo): 413 si
`Content-Length` supera `API_MAX_BODY_BYTES` (32 MiB por defecto: el mayor cuerpo legítimo es un
extracto de 20 MB en base64, ~27 MB) sin leer nada; un cuerpo *chunked* se lee hasta el límite y
solo se entrega a la aplicación si cabe. Comprobado contra el servidor: 40 MB con y sin
`Content-Length` → 413. **Prueba**: `test_an_oversized_body_is_refused_before_it_is_read`.

### S-04 (media, corregido): presupuesto por usuario no aplicado

`LLM_DAILY_BUDGET_USD_PER_USER` solo se aplica a las llamadas hechas dentro de `contexto_consumo`
con un `user_id`. `POST /cases/{id}/documents` usaba `user_id=None` y `POST /cases/{id}/turn` no
abría contexto: un usuario podía gastar todo el presupuesto global analizando documentos. **Corrección**:
ambos atribuyen las llamadas a la sesión (o al dueño del expediente sin cuentas) con `_consumidor`;
un presupuesto agotado en `/turn` es un 429 con el mensaje en español (antes, un 503 genérico).
**Pruebas**: `test_document_and_turn_model_calls_are_charged_to_the_user`, `test_an_exhausted_budget_on_a_turn_is_a_429`.

### S-05 (media, corregido): el limitador de peticiones se esquivaba

`client_key` usaba como cubo el valor de `X-API-Key` que mandara el cliente, válido o no, y el
limitador corre antes de la autenticación: cada valor inventado estrenaba su propio cupo de
`API_RATE_LIMIT_PER_MIN`. **Corrección**: solo la clave configurada nombra un cubo; lo demás va por
dirección. **Prueba**: `test_made_up_api_keys_do_not_get_a_fresh_rate_limit_each` (antes: 200, 200, 200;
ahora 200, 200, 429).

### S-06 (media, corregido): altas y *password spraying*

El límite de altas solo contaba los correos ya registrados: desde una dirección se podían crear
cuentas sin fin, y cada cuenta trae su propio presupuesto diario de modelos. El límite de acceso iba
por correo **y** dirección: probar una contraseña común contra muchos correos no se frenaba.
**Corrección**: por dirección sola, 4 × `AUTH_LOGIN_MAX_ATTEMPTS` altas (cada alta cuenta) y
4 × `AUTH_LOGIN_MAX_ATTEMPTS` accesos fallidos en `AUTH_LOGIN_WINDOW_SECONDS`, además del límite por
correo y dirección que ya había. Detrás de un proxy hay que arrancar uvicorn con `--proxy-headers` (ver
endurecimiento), o todos los clientes comparten la dirección del proxy y ese cupo. **Pruebas**:
`test_registrations_from_one_address_are_limited`, `test_one_password_against_many_emails_is_limited_per_address`.

### S-07 (media, corregido): memoria de scrypt sin tope

`scrypt` con `n=2^17, r=8` reserva 128·n·r = **128 MB** por derivación; `/auth/login` y
`/auth/register` no exigen sesión y FastAPI ejecuta los endpoints síncronos en 40 hilos: 40
peticiones simultáneas con correos distintos (el límite de intentos se comprueba antes y no frena
las que están en vuelo) retienen ~5 GB. **Corrección**: como mucho 4 derivaciones a la vez
(`_KDF_CONCURRENCY`); las demás esperan turno. **Prueba**: `test_password_derivations_run_a_few_at_a_time`.

### S-08 (baja, corregido): cifras del operador abiertas

Con `AUTH_MODE=required` y sin `HACIENDA_API_KEY` (la aplicación web no la envía), `GET /usage`
(coste y llamadas del servicio por propósito) y `GET /eventos/salud` respondían 200 a una petición
anónima (comprobado con `curl`). Con la clave inyectada por el proxy, cualquier usuario las veía.
**Corrección**: `require_operator`: con cuentas, exigen `HACIENDA_OPERATOR_KEY` en `X-Operator-Key`
(404 si falta o no está configurada); sin cuentas no cambia nada. **Pruebas**:
`test_operator_figures_need_the_operator_key_once_accounts_are_on`, y la prueba de inventario
`test_every_route_is_public_by_design_operator_only_or_needs_a_session`, que recorre **todas** las
rutas de la aplicación: cada ruta nueva tiene que ser pública a propósito (lista `PUBLIC`), del
operador o responder 401 sin sesión.

### S-09 (baja, corregido): cabeceras de seguridad

Ninguna respuesta llevaba `X-Content-Type-Options`, `X-Frame-Options`, política de contenido ni
`Cache-Control`; las respuestas llevan datos fiscales y de salud y un proxy o caché compartido
podría guardarlas. **Corrección**: `security_headers_middleware` añade `nosniff`, `DENY`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`,
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (salvo en `/docs` y `/redoc`) y
`Cache-Control: no-store` salvo que el endpoint fije la suya. HSTS lo pone el proxy, que es quien
termina TLS. **Prueba**: `test_responses_carry_security_headers_and_are_not_cached`.

### S-10 (media, abierto, decisión de producto): «avisos por correo» va al operador

El usuario ve «Recibir por correo los avisos de plazos y cambios que te afecten»
(`frontend/src/components/PrivacyPanel.tsx`, `AuthScreen.tsx`). Con `AUTH_MODE=required`,
`ops deliver` (`hacienda_gpt/cli/ops.py:85`, `_con_consentimiento`) solo entrega los avisos de quien
ha marcado la casilla, pero los entrega a **un único destino del operador** (`NOTIFY_EMAIL_TO` o
`NOTIFY_WEBHOOK_URL`), con importes estimados e identificador del expediente
(`hacienda_gpt/proactive/entrega.py`, cabecera del módulo). Es decir, marcar la casilla hace que los
avisos de esa persona salgan hacia el buzón del operador (o el servicio del *webhook*), que no es lo
que dice el texto. `docs/privacidad.md` decía además que el envío no consultaba el consentimiento;
se ha corregido para describir lo que hace el código. **Propuesta**: o enviar a la dirección de cada
cuenta (con verificación de correo previa, que hoy no existe), o, hasta entonces, que `ops deliver`
con `AUTH_MODE=required` se niegue a entregar a un destino único y que la casilla diga «cuando el
servicio los ofrezca» sin efecto alguno. Ambas cambian el producto: no se ha tocado el código.

### S-11 (baja, corregido en parte): errores con el texto de la excepción

El flujo NDJSON de `/agent/stream` devolvía `Error interno: {exc}` para cualquier fallo inesperado
(rutas, SQL o datos del expediente pueden ir en ese texto). **Corrección**: al registro, y al
cliente solo «Error interno.». **Prueba**: `test_an_unexpected_failure_in_the_stream_does_not_leak_its_text`.
**Abierto**: los 503 de construcción (`api.py`: «El agente no está disponible: {exc}» y similares
del lector de documentos, del flujo `/qa` y del registro de consumo) y `/cases/{id}/turn` («No se
pudieron extraer los hechos…: {exc}», que puede incluir 200 caracteres de la salida del modelo)
muestran detalles de configuración al usuario. No hay secretos en esos textos (se revisaron los
mensajes de los proveedores), pero lo correcto es registrar el detalle y mostrar un mensaje fijo;
se deja al criterio de quien mantiene `api.py` porque esos mensajes también los ve el operador.
`POST /acciones/{id}/confirmar` guarda y devuelve `La acción falló: {tipo}: {exc}`
(`routers/modulos.py:378`): mismo criterio.

### S-12 (baja, corregido): registro de consumo tras borrar la cuenta

`llm_usage` (una fila por llamada al modelo con `user_id` y `case_id`) no se borraba ni se
anonimizaba con la cuenta y no figuraba en `docs/privacidad.md`. **Corrección**:
`anonimizar_consumo` deja las filas (los totales del operador) sin usuario ni expediente al
eliminar la cuenta; tabla documentada. **Prueba**: `test_deleting_the_account_detaches_the_usage_log`.

### S-13 (baja, corregido en parte): *workflows* de GitHub

`ci.yml`, `critical-regression.yml`, `evaluacion.yml` y `nightly-eval.yml` no declaraban
`permissions:`, así que el `GITHUB_TOKEN` tenía los permisos por defecto del repositorio (de escritura
en repositorios antiguos). Ahora declaran `contents: read`. Lo demás está bien: las PR desde *forks*
no reciben secretos (`evaluacion.yml` comprueba que la clave existe antes de usarla); el único trabajo
con escritura (`vigilancia.yml`, `lecturas-pr`) no ejecuta modelos ni descarga nada, vuelve a comprobar
que solo cambian lecturas y limita `add-paths` a `hacienda_gpt/tax/data/*.json`; ningún `run:`
interpola datos de la PR (`github.event.*`). **Abierto**: las acciones van fijadas por etiqueta
(`@v4`, `peter-evans/create-pull-request@v7` con un token de escritura); fijarlas por SHA protege
de una etiqueta movida.

### S-14 (media, abierto): llamadas al modelo sin medir

`POST /qa` construye la cadena de LangChain con `ChatOpenAI` (`llm/chain.py:261`), que no pasa por
`MeteredProvider`: ni se registra ni se aplica ningún presupuesto, y cualquier usuario con sesión
puede llamarla (la aplicación web ya no la usa: `postQA` en `frontend/src/api.ts` no tiene llamadas).
Lo mismo con `OpenAIFactExtractor` (`decision/fact_extractor.py:423`), que se usa en `/turn` cuando
`DECISION_EXTRACTOR=openai` o solo hay `OPENAI_API_KEY`. **Propuesta**: retirar `/qa` cuando
`AUTH_MODE=required` (o del todo) y pasar el extractor de OpenAI por el proveedor medido
(`_provider_extractor`). No se ha cambiado porque retirar una ruta pública es decisión de producto.

### S-15 (media, abierto, decisión de producto): tamaño de pregunta e historial

`AgentRequest.query`/`chat_history` (`api.py:453-454`), `QARequest` (`392-393`) y
`TurnRequest.user_input` (`232`) no tienen longitud máxima, y el historial entero se reenvía en cada
uno de los hasta `AGENT_MAX_STEPS` pasos. El presupuesto se comprueba **antes** de cada llamada con
lo ya gastado, así que una pregunta con un historial cercano a la ventana de contexto puede superar
el límite diario por usuario en el coste de una o dos llamadas completas. El límite de cuerpo (S-03)
acota el tamaño, no el coste. **Propuesta**: tope de caracteres para la pregunta (p. ej. 20 000) y para
el historial total, que el frontend recorte el historial (hoy envía la conversación entera) y una
estimación de tokens antes de llamar. Los números afectan a conversaciones largas legítimas: no se
han fijado.

### S-16 (baja, abierto, `agent/tools.py` lo edita otro agente): procedencia de `registrar_hechos`

`registrar_decision` exige que la cita y la razón sean palabras literales del usuario
(`memoria/agente.py`, `_en_palabras_del_usuario`). `registrar_hechos` (`agent/tools.py:135`) solo lo
pide en la descripción: un texto no fiable que llega al modelo —fragmentos recuperados o la
`etiqueta` (hasta 80 caracteres, sacada del descriptor del cargo) de los servicios que el módulo de
suscripciones guarda y que los especialistas reciben en `hechos_para_modulo`— podría inducirle a
escribir un hecho falso **en el expediente del propio usuario**. Impacto acotado: no cruza usuarios,
el hecho es visible y editable, los cálculos son deterministas y nada sale de la aplicación (las
herramientas `actua` solo crean acciones pendientes que confirma el usuario con su sesión, con todos
los argumentos a la vista). **Propuesta**: comprobar que cada valor aparece en los mensajes del
usuario (como `registrar_decision`), o guardar lo que no aparezca como propuesta a confirmar (igual
que los documentos). El módulo de correo que se está construyendo será, con diferencia, la mayor
superficie de este tipo: debe seguir la misma regla.

### S-17 (baja, abierto): pickle de FAISS en el indexador

`agent/search.py` y `llm/chain.py` exigen `FAISS_TRUSTED_INDEX=true` antes de `allow_dangerous_deserialization`;
el indexador incremental (`processor/document_loader.py:869`) no. Lo ejecuta el operador sobre su
propio directorio de salida, así que solo es explotable si ese directorio lo puede escribir otro.
**Propuesta**: la misma guarda, o comprobar la huella del índice antes de cargarlo.

### S-18 (baja, abierto): documentación de la API pública

`/docs`, `/redoc` y `/openapi.json` describen todas las rutas a cualquiera. No dan acceso a nada, pero
facilitan el reconocimiento. **Propuesta**: bloquearlas en el proxy o crear la aplicación con
`docs_url=None, redoc_url=None, openapi_url=None` cuando `AUTH_MODE=required`.

## Lo comprobado sin hallazgo

- **Aislamiento entre usuarios.** Todas las rutas con `{case_id}` (prueba de inventario que ya
  existía) y las que lo reciben por *query* o cuerpo (`/acciones`, `/propuestas`,
  `/acciones/{id}/confirmar|rechazar`, `/modulos/{clave}/herramientas/{nombre}`, `/modulos/{clave}/documento`,
  `/evaluacion/feedback` GET/POST/DELETE, `/planificar`, `/simulate`, `/optimize`, `/agent`,
  `/navegador/tareas*`) responden 404 a otra cuenta, igual que a un expediente inexistente, y no
  cambian nada (`test_another_users_case_is_404_on_the_query_and_body_endpoints`, más `curl` contra
  el servidor). Las decisiones de memoria se buscan por `id` **y** `case_id`. El dueño de un
  expediente es la sesión que lo crea, nunca el `user_id` del cuerpo.
- **Sesiones y contraseñas.** Token de 256 bits, solo su SHA-256 en la base de datos, caducidad y
  revocación; scrypt con sal; mismo mensaje y coste para correo desconocido; parámetros de una huella
  almacenada acotados (`_MAX_N`).
- **CSRF.** No aplica: la sesión va en `Authorization`, no hay *cookies* y CORS tiene
  `allow_credentials=False` con una lista cerrada de orígenes.
- **SQL.** Todo parametrizado. Las únicas cadenas construidas son marcadores `?` o nombres de columna
  que vienen del código (`eventos/bus.py`, `ops/consumo.py`, `proactive/store.py`).
- **Rutas de fichero.** `case_id` y `tarea_id` se validan con expresión regular antes de formar rutas
  (`navegador/vigilancia.py`); las claves de guion, también (`navegador/guion.py`) y además se
  buscan en la lista de disponibles; los ficheros de parámetros se nombran con enteros.
- **SSRF.** Ninguna URL que dependa del usuario se descarga en el servidor: el *webhook* y el SMTP
  los fija el operador (esquema `http(s)` validado), BOE/ESIOS/BDNS son hosts fijos. El agente con
  navegador solo alcanza los orígenes del guion (sustituciones solo a *loopback*), sin POST mientras
  actúa el agente, con *service workers* bloqueados y *websockets* interceptados.
- **XXE.** `xml.etree` no resuelve entidades externas; ahora además se rechaza cualquier DTD en los
  ficheros de usuarios (S-02).
- **Documentos.** Sin el consentimiento `envio_documentos_a_proveedor_llm` no se lee el fichero
  (403 antes de nada); 10 MB y 60 páginas como máximo; nada se guarda.
- **Acciones.** El modelo no puede confirmar: solo el endpoint con la sesión del dueño; cada acción
  se ejecuta una vez (transición `pendiente → ejecutando`) y solo si el módulo sigue activo.
- **Frontend.** No hay `dangerouslySetInnerHTML`, `innerHTML`, `eval` ni `document.write`; las
  respuestas se pintan con un analizador Markdown propio que produce elementos React (texto
  escapado); los enlaces de las fuentes solo se pintan si empiezan por `http://` o `https://`; no se
  cargan recursos de terceros. El único *script* en línea es el del tema (`index.html`).
- **Secretos.** Ninguno en el código ni en la documentación; el registro estructurado no anota cuerpos;
  la URL del *webhook* nunca se registra; la exportación de datos excluye las huellas de los tokens.

## Endurecimiento: lo que tiene que hacer el operador

1. **`AUTH_MODE=required`** siempre que haya más de una persona. Con `off` no hay control de acceso.
   No exponer la interfaz Streamlit (`hacienda_gpt/ui`) ni los CLI.
2. **Proxy inverso con TLS** (nginx, Caddy…) delante de uvicorn, que escuche solo en `127.0.0.1`:
   ```
   uvicorn hacienda_gpt.api.api:app --host 127.0.0.1 --port 8000 \
     --proxy-headers --forwarded-allow-ips 127.0.0.1 --limit-concurrency 64 --no-server-header
   ```
   `--proxy-headers` hace que los límites por dirección vean la IP del cliente y no la del proxy
   (`--forwarded-allow-ips` debe ser la del proxy y nada más). Un solo proceso: los límites viven en
   memoria (con varios procesos, un limitador en el proxy).
3. **Servir el frontend y la API en el mismo origen** (`/` y `/api/`), y ajustar `_ALLOWED_ORIGINS`
   (`hacienda_gpt/api/api.py`) al dominio real o dejarlo sin orígenes cruzados.
4. **Cabeceras en el proxy** para las páginas del frontend (la API ya pone las suyas):
   ```
   Strict-Transport-Security: max-age=31536000; includeSubDomains
   Content-Security-Policy: default-src 'self'; script-src 'self' 'sha256-LQ0zvfOJLyu1G7nu6rYxSTkHes1gSUZBBWcHHJgfYLo='; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'
   X-Content-Type-Options: nosniff
   Referrer-Policy: no-referrer
   Permissions-Policy: camera=(), microphone=(), geolocation=()
   ```
   El *hash* es el del *script* del tema de `frontend/index.html`: si ese *script* cambia, hay que
   recalcularlo. La política importa especialmente porque el token de sesión está en `localStorage`.
5. **Límites en el proxy**: `client_max_body_size 32m` (coherente con `API_MAX_BODY_BYTES`),
   limitación de peticiones por IP en `/api/auth/` y en las rutas del asesor, y tiempos de espera
   largos (y sin *buffering*) solo para `/api/agent/stream`. Bloquear `/api/docs`, `/api/redoc` y
   `/api/openapi.json` (S-18). `/metrics` y `/health` solo desde la red interna.
6. **Variables**: `LLM_DAILY_BUDGET_USD` y `LLM_DAILY_BUDGET_USD_PER_USER` siempre (sin ellas no hay
   techo de gasto), `LLM_MAX_CALLS_PER_QUESTION`, `API_RATE_LIMIT_PER_MIN`, `HACIENDA_OPERATOR_KEY`
   (aleatoria, ≥ 32 bytes, solo para el operador; sin ella `/usage` y `/eventos/salud` responden 404),
   `PRIVACY_NOTICE_URL`. No activar `NOTIFY_*` con usuarios reales mientras siga abierto S-10.
   `FAISS_TRUSTED_INDEX=true` solo con un índice construido por el propio operador.
7. **Secretos** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `NOTIFY_SMTP_PASSWORD`, `NOTIFY_WEBHOOK_URL`,
   `HACIENDA_API_KEY`, `HACIENDA_OPERATOR_KEY`) en un gestor de secretos o un fichero `0600` del
   usuario del servicio, nunca en el repositorio ni en la línea de órdenes; límites de gasto también
   en la consola del proveedor.
8. **Rotación**: claves del proveedor y del operador cada 90 días y en cuanto alguien deja el equipo
   o hay sospecha; tras rotar, reiniciar el proceso. Para cerrar todas las sesiones de golpe
   (sospecha de robo de tokens): `DELETE FROM auth_sessions;` con el servicio parado (los usuarios
   vuelven a entrar). `AUTH_SESSION_TTL_HOURS` corto (24 h por defecto).
9. **Datos en reposo**: disco o volumen cifrado para el SQLite (y sus `-wal`/`-shm`), permisos `0600`
   del usuario del servicio, que no sea `root`. **Copias** cifradas antes de salir de la máquina
   (p. ej. `sqlite3 state.sqlite3 ".backup /tmp/copia.sqlite3"` y después `age`/`gpg` con una clave
   que no esté en el mismo servidor), con retención fija y documentada: un borrado de cuenta no
   alcanza a las copias hasta que caducan (ver `docs/privacidad.md`). Probar la restauración.
10. **Registros**: el registro de accesos de uvicorn y del proxy guarda IP y ruta completa (con el
    identificador del expediente): retención corta y acceso restringido.
11. **Dependencias**: las versiones están fijadas en `pyproject.toml`/`uv.lock` y `package-lock.json`.
    Esta revisión no ha podido consultar bases de vulnerabilidades (sin red); hay que pasar
    `uvx pip-audit` (o `osv-scanner --lockfile uv.lock`) y `npm audit --omit=dev` antes de abrir y en
    CI de forma periódica, y actualizar Chromium/Playwright con frecuencia si `NAVEGADOR_ACTIVO=true`.
12. **CI**: fijar por SHA las acciones de terceros con permisos de escritura (S-13) y restringir quién
    puede lanzar `workflow_dispatch`.

## Lo que no se ha podido comprobar

- **Vulnerabilidades conocidas de las dependencias**: sin acceso a bases de datos de CVE; no se
  afirma nada sobre ninguna versión (punto 11).
- **Proveedores reales del modelo**: sin clave en esta máquina; la atribución del gasto se probó con
  proveedores falsos, no con facturación real. La inyección de instrucciones se analizó por caminos
  de código, no con un modelo real.
- **Agente con navegador contra sitios reales**: solo las pruebas existentes con páginas simuladas.
- **El despliegue**: TLS, proxy, permisos del sistema de ficheros y copias dependen del operador.
- **Módulos en construcción** en el momento de la revisión (correo, propuestas en el chat): fuera de
  esta revisión.
