# Hablar con el asistente por SimpleX Chat

Esta guía explica cómo poner en marcha el canal **SimpleX Chat** de la mensajería: con él, cada persona de la casa
puede escribir al asistente desde la app de SimpleX (Android, iOS o escritorio), con cifrado de extremo a extremo y
sin número de teléfono ni cuenta en ningún servicio. La pasarela que decide quién puede hablar con el asistente, el
emparejamiento, las confirmaciones y los avisos se describen en [arquitectura.md](arquitectura.md); aquí solo se
trata la pieza que mueve los mensajes entre SimpleX y la pasarela.

## Cómo funciona

```
App SimpleX (tu móvil) ──E2E──▶ servidores SMP/XFTP ──E2E──▶ simplex-chat (CLI, en tu servidor)
                                                                   │  WebSocket en 127.0.0.1:5225
                                                                   ▼
                                                     worker del asistente (CanalSimplex → pasarela → cerebro)
```

- En tu servidor corre la **CLI oficial de SimpleX Chat** (`simplex-chat -p 5225`) con un perfil propio del bot.
  La CLI es quien cifra y descifra; el asistente habla con ella por una API WebSocket local.
- El **worker** del asistente (`CanalSimplex`, en `asistente/plataforma/mensajeria/simplex`) se conecta a esa API,
  recibe los mensajes y se los pasa a la pasarela; las respuestas vuelven por el mismo camino.
- Al arrancar, el canal comprueba la versión de la CLI, activa el perfil del bot (lo crea si no existe, sin tocar
  ningún otro perfil), crea la **dirección del bot** si falta, activa la aceptación automática de contactos y el
  mensaje de bienvenida según la configuración, y desactiva la entrada automática en grupos. La dirección (un enlace
  `https://smp….`/`simplex:/…`) es lo que la app del asistente muestra como **código QR** en el panel «Mensajería».
- Si la CLI se cae o se reinicia, el canal reintenta solo (con esperas crecientes, hasta 60 s) sin perder la cola de
  mensajes que la CLI guarda mientras tanto.

### Modelo de privacidad

- **Entre tu móvil y tu servidor** todo va cifrado de extremo a extremo (doble ratchet de SimpleX, con cifrado
  poscuántico cuando los dos extremos lo admiten). Los servidores de retransmisión (SMP para mensajes, XFTP para
  archivos) solo ven paquetes cifrados, sin identificadores de usuario ni números de teléfono. Si quieres que ni
  siquiera esos paquetes pasen por servidores de terceros, usa [servidores propios](#servidores-propios-smp-y-xftp).
- **En tu servidor el texto está en claro**: la CLI descifra para que el asistente pueda leerlo. Es tu propio servidor,
  como lo es la base de datos del asistente. La CLI guarda la conversación en su base de datos; el canal le pide que
  borre los mensajes de más de `SIMPLEX_HISTORIAL_DIAS` días (7 por defecto). Los archivos aceptados se guardan en una
  carpeta privada (0700) solo hasta que la pasarela los procesa (los borra siempre, los lea o no).
- **Los registros nunca contienen texto**: el canal registra identificadores de contacto y de mensaje, tipos de
  evento, tamaños y códigos de error; nunca el texto, las citas, los nombres de archivo ni los nombres visibles.
  La salida estándar de la CLI se descarta (ver [Seguridad](#seguridad)).
- Que alguien conecte con el bot **no le da acceso a nada**: hasta que envía un código de emparejamiento válido
  generado en la app, la pasarela no le deja hablar con el asistente (ver [arquitectura.md](arquitectura.md)).

## Instalación en el servidor (systemd)

Requisitos: el despliegue con systemd de [docs/despliegue.md](../despliegue.md) (usuario `hacienda`, servicio
`hacienda-worker`) y un Linux x86-64 con glibc reciente (los binarios oficiales se compilan para Ubuntu 22.04/24.04).

1. **Descarga la CLI** de la [página de versiones](https://github.com/simplex-chat/simplex-chat/releases) (por
   ejemplo `v7.1.4`, el fichero `simplex-chat-ubuntu-22_04-x86_64` o el de 24.04), comprueba su suma SHA-256 contra la
   publicada y colócala en `/usr/local/bin`:

   ```sh
   curl -fLo simplex-chat https://github.com/simplex-chat/simplex-chat/releases/download/v7.1.4/simplex-chat-ubuntu-22_04-x86_64
   sha256sum simplex-chat          # compárala con la de la página de la versión
   sudo install -m 0755 simplex-chat /usr/local/bin/simplex-chat
   simplex-chat --version
   ```

   El canal admite desde la **6.4.3** (primera versión con la referencia de la API de bots) hasta antes de la **8.0**.

2. **Crea el usuario** de la CLI (sin shell ni privilegios):

   ```sh
   sudo useradd --system --home-dir /var/lib/simplex-chat --shell /usr/sbin/nologin simplex
   ```

3. **Instala el servicio** [`docs/despliegue/systemd/simplex-chat.service`](../despliegue/systemd/simplex-chat.service)
   en `/etc/systemd/system/`. Revisa el nombre de `--create-bot-display-name`: debe coincidir con
   `SIMPLEX_BOT_NOMBRE` (por defecto «Asistente del hogar»). Ese parámetro solo actúa la primera vez: crea el perfil
   del bot (con archivos permitidos por `--create-bot-allow-files`). Sin él, la CLI se quedaría esperando a que
   alguien escriba un nombre en la terminal.

   El servicio usa `/var/lib/simplex-chat` (0700) para su base de datos y crea `/var/lib/simplex-recepcion`
   (`simplex:hacienda`, 2770), la carpeta compartida donde la CLI escribe los archivos que llegan.

4. **Deja que el worker use la carpeta compartida** y activa el canal:

   ```sh
   sudo systemctl edit hacienda-worker
   #   [Service]
   #   ReadWritePaths=-/var/lib/simplex-recepcion
   ```

   y en `/etc/hacienda/entorno`:

   ```sh
   MENSAJERIA_ACTIVA=true
   MENSAJERIA_CANALES=simplex
   SIMPLEX_ARCHIVOS_RECEPCION_DIR=/var/lib/simplex-recepcion
   ```

5. **Arranca** la CLI y reinicia el worker:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now simplex-chat
   sudo systemctl restart hacienda-worker
   ss -ltnp | grep 5225            # debe verse 127.0.0.1:5225 (o [::1]), nunca 0.0.0.0 ni *
   ```

   En unos segundos el panel «Mensajería» de la app muestra «Conectado a simplex-chat 7.1.4 en 127.0.0.1:5225 con el
   perfil «Asistente del hogar»» y el código QR de la dirección del bot.

### Con Docker Compose

`docs/despliegue/docker-compose.yml` incluye el servicio `simplex` en el perfil opcional `simplex` (no arranca si no
lo pides). Su imagen ([`Dockerfile.simplex`](../despliegue/Dockerfile.simplex)) descarga el binario oficial y
**exige** su suma SHA-256:

```sh
# en docs/despliegue/.env
MENSAJERIA_ACTIVA=true
SIMPLEX_RELEASE=v7.1.4
SIMPLEX_SHA256=<la suma del fichero simplex-chat-ubuntu-22_04-x86_64 de esa versión>

docker compose --profile simplex -f docs/despliegue/docker-compose.yml --env-file docs/despliegue/.env up -d --build
```

La CLI comparte el espacio de red del worker (`network_mode: service:worker`): la API WebSocket solo existe en el
`127.0.0.1` de ese espacio, sin puertos publicados. Del volumen de datos solo ve la carpeta de recepción de archivos
(montaje `subpath`, Docker Engine 25 o posterior). Su base de datos queda en el volumen `simplex_datos`.
Si recreas el contenedor del worker, reinicia también `simplex` (`docker compose restart simplex`): pierde la red al
desaparecer la del worker.

## Emparejar tu chat desde la app SimpleX

1. En la app del asistente, abre el panel **«Mensajería»**: verás el código QR del bot (y el enlace, para copiarlo).
2. En la app **SimpleX Chat** del móvil, pulsa «+» → «Escanear / pegar enlace» y escanea el QR. El bot acepta la
   conexión y te saluda con el mensaje de bienvenida.
3. En el panel «Mensajería», genera un **código de emparejamiento** y envíaselo al bot desde SimpleX (caduca en
   unos minutos y sirve una sola vez).
4. El asistente confirma el emparejamiento. A partir de ahí puedes preguntarle lo mismo que en la app.

Cuando todos los chats de la casa estén emparejados puedes poner `SIMPLEX_AUTOACEPTAR=false`: el bot dejará de
aceptar contactos nuevos (los existentes siguen funcionando). Para desemparejar un chat, hazlo desde la app: el canal
borra además el contacto en SimpleX.

## Configuración

Bloque `# --- mensajeria.simplex ---` de `asistente/plataforma/settings.py` (variables de entorno):

| Variable | Por defecto | Para qué |
|---|---|---|
| `SIMPLEX_WS_URL` | `ws://127.0.0.1:5225` | API de la CLI. Solo local; otra máquina exige `SIMPLEX_PERMITIR_REMOTO` y `wss://`. |
| `SIMPLEX_PERMITIR_REMOTO` | `false` | Permite una CLI en otra máquina, solo tras un proxy con TLS (y autenticación). |
| `SIMPLEX_BOT_NOMBRE` | `Asistente del hogar` | Nombre del perfil del bot (se activa o se crea; los demás perfiles no se tocan). |
| `SIMPLEX_BIENVENIDA` | un saludo breve | Mensaje que la CLI envía a quien conecta (vacío: ninguno). |
| `SIMPLEX_AUTOACEPTAR` | `true` | Aceptar contactos nuevos por la dirección del bot. |
| `SIMPLEX_HISTORIAL_DIAS` | `7` | Días que la CLI guarda los mensajes en su base de datos (0: no tocar su ajuste). |
| `SIMPLEX_ARCHIVOS_DIR` | `data/mensajeria/simplex/archivos` | Copia privada (0700) de los archivos aceptados. |
| `SIMPLEX_ARCHIVOS_RECEPCION_DIR` | vacío (`<ARCHIVOS_DIR>/recepcion`) | Donde la CLI escribe lo que llega; compartida si la CLI tiene su propio usuario. |
| `SIMPLEX_ARCHIVOS_MAX_BYTES` | 15 MiB | Tamaño máximo de un archivo (nunca más de 15 MiB). |
| `SIMPLEX_ACEPTAR_VOZ` | `true` | Recibir notas de voz (la pasarela aún no las transcribe). |
| `SIMPLEX_ARCHIVOS_TIMEOUT_S` | `300` | Tiempo máximo de una descarga; después se cancela. |
| `SIMPLEX_SERVIDORES_SMP` / `_XFTP` | vacío | Tus servidores propios; el canal avisa si la CLI usa otros. |
| `SIMPLEX_TIMEOUT_CONEXION_S` / `_COMANDO_S` | `10` / `30` | Esperas de la conexión y de cada orden. |
| `SIMPLEX_PING_S` | `20` | Latido de la conexión (0: sin latido). |
| `SIMPLEX_RECONEXION_MAX_S` | `60` | Espera máxima entre reintentos. |

## Archivos

Se aceptan **imágenes** (JPEG, PNG, GIF, WebP, HEIC), **PDF** y, si está activado, **notas de voz** (M4A/AAC, Ogg,
WAV), hasta el tamaño máximo. Todo lo demás (vídeos, documentos de Office, ejecutables…) ni siquiera se descarga; el
mensaje llega a la pasarela con su texto y sin el adjunto. Para cada archivo:

1. Antes de descargar: tamaño declarado y tipo declarado (tipo de mensaje y extensión) dentro de lo permitido.
2. La CLI lo escribe en un nombre aleatorio que elige el canal, nunca en el nombre que puso el remitente.
3. Tras la descarga: debe ser un fichero normal (no un enlace), con el tamaño anunciado, y sus primeros bytes deben
   corresponder a un tipo permitido (no basta la extensión). El tipo que ve la pasarela sale de esos bytes.
4. Se copia a `SIMPLEX_ARCHIVOS_DIR/<aleatorio>/<nombre saneado>` (0600) y se borra el original.

Si un mensaje trae un archivo, los mensajes siguientes del mismo contacto esperan a que termine (o falle) su descarga,
para que la pasarela los reciba en el orden en que se enviaron («te mando la factura» → «¿qué dice?»).

## Servidores propios (SMP y XFTP)

Por defecto la CLI usa los servidores preconfigurados de la red SimpleX. Para máxima privacidad puedes montar los tuyos
([guía de SimpleX](https://simplex.chat/docs/server.html), [XFTP](https://simplex.chat/docs/xftp-server.html)) y
decirle a la CLI que los use con un override del servicio:

```sh
sudo systemctl edit simplex-chat
#   [Service]
#   ExecStart=
#   ExecStart=/usr/local/bin/simplex-chat -p 5225 -d /var/lib/simplex-chat/bot -y \
#       --create-bot-display-name "Asistente del hogar" --create-bot-allow-files \
#       --files-folder /var/lib/simplex-chat/archivos \
#       -s "smp://<huella>@smp.tudominio.org" --xftp-server "xftp://<huella>@xftp.tudominio.org"
```

Varias direcciones van separadas por espacios dentro de las mismas comillas. Pon las mismas en
`SIMPLEX_SERVIDORES_SMP` / `SIMPLEX_SERVIDORES_XFTP`: al conectar, el canal pregunta a la CLI qué servidores tiene
activos y avisa en su estado si hay alguno que no sea tuyo o si no usa ninguno de los tuyos. Recuerda que la app de
tu móvil elige sus propios servidores para su lado de la conexión: configúralos también allí si quieres que nada pase
por servidores de terceros.

## Copias de seguridad

La base de datos de la CLI (`/var/lib/simplex-chat/bot_chat.db` y `bot_agent.db`; en Docker, el volumen
`simplex_datos`) contiene **las claves del bot**: sin ella habría que crear una dirección nueva y volver a emparejar
todos los chats.

- Copia la carpeta con la CLI parada (`systemctl stop simplex-chat`, copia, `systemctl start simplex-chat`), o
  inclúyela en tu copia cifrada del servidor. Trátala como un secreto (contiene claves y la conversación reciente).
- Restaura solo la copia más reciente y **nunca ejecutes dos CLI con la misma base de datos** a la vez: el cifrado de
  SimpleX avanza con cada mensaje y una copia antigua o duplicada puede dejar contactos sin poder descifrar (habría
  que volver a conectar esos chats).
- Opcional: la CLI puede cifrar su base de datos con `--key <frase>`; tendrías que poner la frase en el `ExecStart`
  (visible para root) y guardarla aparte: sin ella la copia no sirve.

## Actualizar la CLI

1. Lee las notas de la versión nueva. El canal admite de la 6.4.3 hasta antes de la 8.0; con una versión fuera de ese
   rango se niega a funcionar y lo dice en su estado («Versión de simplex-chat no compatible…»): actualiza entonces
   también el asistente.
2. `systemctl stop simplex-chat`, haz una copia de `/var/lib/simplex-chat`, sustituye `/usr/local/bin/simplex-chat`
   por el binario nuevo (comprobando su SHA-256) y `systemctl start simplex-chat`.
3. La opción `-y` del servicio confirma las migraciones de la base de datos; la CLI guarda antes una copia propia.
   El worker reconecta solo.

En Docker: cambia `SIMPLEX_RELEASE` y `SIMPLEX_SHA256` en `.env` y vuelve a construir (`up -d --build`).

## Problemas frecuentes

- **«Sin conexión con simplex-chat en 127.0.0.1:5225; se reintentará»**: ¿está la CLI en marcha
  (`systemctl status simplex-chat`)? ¿Escucha en `127.0.0.1:5225` (`ss -ltnp | grep 5225`)? ¿Coincide el puerto con
  `SIMPLEX_WS_URL`?
- **La CLI no arranca la primera vez**: falta `--create-bot-display-name` y está esperando un nombre en la terminal.
- **«…rechazó la orden (error:userExists)»**: ya hay un perfil o un contacto con ese nombre en la CLI (por ejemplo un
  perfil oculto). Cambia `SIMPLEX_BOT_NOMBRE` (y `--create-bot-display-name`) o renombra el otro perfil.
- **Aviso «el perfil del bot no admite archivos»**: el perfil se creó sin `--create-bot-allow-files`. Actívalos en el
  perfil (desde la consola de la CLI o creando de nuevo la base de datos) o acepta que solo llegue texto.
- **Aviso «No se puede preparar la carpeta de archivos»** o archivos que nunca llegan: revisa los permisos de
  `/var/lib/simplex-recepcion` (`simplex:hacienda`, 2770) y el `ReadWritePaths` del worker.
- **Mensajes que se pierden o llegan a otro programa**: no conectes otras herramientas al puerto 5225 mientras el
  worker está conectado; la CLI reparte sus eventos entre todos los clientes conectados.
- **Necesito ver qué hace la CLI**: para el servicio y ejecútala un momento en primer plano como su usuario
  (`sudo -u simplex simplex-chat -d /var/lib/simplex-chat/bot -p 5225`). Ojo: esa salida incluye el texto de los
  mensajes que envía el asistente; no la guardes.
- **El estado muestra «Eventos descartados por sobrecarga»**: el worker no daba abasto y se perdieron eventos de la
  CLI; esos mensajes siguen en la base de datos de la CLI pero el asistente no los recibió. Revisa la carga del worker.

## Seguridad

- La API WebSocket de la CLI **no tiene autenticación ni cifrado**: quien llegue al puerto puede leer y enviar como el
  bot. La CLI solo escucha en `127.0.0.1`; no abras el puerto en el cortafuegos, no lo publiques en un proxy ni en
  `ports:` de Docker. El canal se niega a usar una URL que no sea local salvo con `SIMPLEX_PERMITIR_REMOTO=true`, y
  entonces solo `wss://` (detrás de un proxy con TLS y autenticación básica, como indica la guía oficial de bots).
- En modo `-p` la CLI **escribe en su salida cada orden que recibe**, con el texto de los mensajes que envía el
  asistente: el servicio la descarta (`StandardOutput=null`; en Docker, `logging: none`).
- El bot nunca se une a grupos: el canal desactiva la aceptación automática de invitaciones e ignora los mensajes de
  grupo.
- El canal no interpreta el texto que transporta: lo reenviado y lo citado se marcan como tales y la pasarela los trata
  como datos, nunca como instrucciones.

## Protocolo (referencia técnica)

El canal sigue la **referencia oficial de la API de bots** del repositorio
[simplex-chat/simplex-chat](https://github.com/simplex-chat/simplex-chat): `bots/README.md` y
`bots/api/COMMANDS.md`, `EVENTS.md`, `TYPES.md`, rama `stable` en el commit
`cb4b50db169a2638c78e0b41730d5759c559b473` (16-11-2026, simplex-chat **7.1.4**), contrastada con los mismos ficheros en
la etiqueta `v6.4.3`, con el servidor WebSocket de la CLI (`apps/simplex-chat/Server.hs`), su analizador de órdenes
(`src/Simplex/Chat/Library/Commands.hs`) y el cliente TypeScript oficial (`packages/simplex-chat-client/typescript`).

Formato de los mensajes WebSocket (texto JSON):

```json
{"corrId": "<identificador único>", "cmd": "<orden>"}                     // orden del bot
{"corrId": "<el mismo>", "resp": {"type": "<etiqueta>", "...": "..."}}     // respuesta
{"resp": {"type": "<etiqueta del evento>", "...": "..."}}                  // evento (sin corrId)
```

Una orden fallida responde `{"type": "chatCmdError", "chatError": {...}}`. La CLI procesa las órdenes de una en una
y, según la guía, el bot debe aceptar campos, etiquetas y eventos desconocidos: el canal los ignora y los cuenta.

Órdenes usadas (sintaxis de `COMMANDS.md`):

| Orden | Uso | Respuesta esperada |
|---|---|---|
| `/version` | comprobar la versión (no está en la referencia; CLI `ShowVersion`) | `versionInfo` (`versionInfo.version`) |
| `/user` | perfil activo | `activeUser` · error `noActiveUser` |
| `/users` | buscar el perfil del bot | `usersList` (`users[].user`) |
| `/_user <userId>` | activarlo | `activeUser` |
| `/_create user <json(newUser)>` | crearlo (`peerType: "bot"`) | `activeUser` · error `userExists` |
| `/_set accept group invitations <userId> off` | no entrar en grupos | `cmdOk` |
| `/_show_address <userId>` | dirección del bot | `userContactLink` · error `userContactLinkNotFound` |
| `/_address <userId>` | crearla | `userContactLinkCreated` |
| `/_address_settings <userId> <json(settings)>` | aceptación automática y bienvenida | `userContactLinkUpdated` |
| `/_ttl <userId>` · `/_ttl <userId> <segundos>` | conservación de mensajes (de `Commands.hs`) | `chatItemTTL` · `cmdOk` |
| `/_servers <userId>` | comprobar servidores propios | `userServers` |
| `/_send @<contactId> json <json([ComposedMessage])>` | enviar (con `quotedItemId` para responder) | `newChatItems` |
| `/_update item @<contactId> <chatItemId> json <json(UpdatedMessage)>` | editar | `chatItemUpdated` / `chatItemNotChanged` |
| `/freceive <fileId> approved_relays=on encrypt=off <ruta>` | recibir un archivo en la ruta elegida | `rcvFileAccepted` |
| `/fcancel <fileId>` | cancelar una descarga | `rcvFileCancelled` |
| `/_delete @<contactId> full` | borrar el contacto (desemparejar) | `contactDeleted` |

Eventos usados (`EVENTS.md`): `newChatItems` (mensajes recibidos: `chatItems[].chatInfo` de tipo `direct` con
`contact.contactId`; `chatItem.chatDir.type == "directRcv"`, `content.type == "rcvMsgContent"`,
`content.msgContent.{type,text}`, `meta.{itemId,itemTs,itemForwarded,itemDeleted}`, `quotedItem.content.text`,
`file.{fileId,fileName,fileSize,fileStatus}`), `rcvFileComplete`, `rcvFileError`, `rcvFileWarning`,
`rcvFileSndCancelled`, `contactConnected`, `contactDeletedByContact`, `receivedGroupInvitation` (ignorada). El resto
de eventos documentados se ignoran a propósito; los desconocidos se cuentan.

Límites aplicados: un mensaje de SimpleX cabe en 13 380 bytes codificados con cifrado poscuántico
(`maxEncodedMsgLengthPQ` en `Protocol.hs`), y una respuesta lleva dentro el mensaje citado. El canal parte el texto en
mensajes de hasta 4 000 caracteres y 12 000 bytes (codificado en JSON) y quita la cita si no cabe con la primera parte.

### Lo que no se ha podido verificar

Este entorno no puede ejecutar la CLI real ni llegar a los servidores de SimpleX; las pruebas usan un servidor
WebSocket falso (`tests/mensajeria/simplex/falso.py`) que reproduce el subconjunto documentado. Quedan sin verificar
contra una CLI real:

- la forma exacta de `versionInfo` (tomada del código, `CRVersionInfo`/`CoreVersionInfo`), y que `/_ttl` responda
  `chatItemTTL`/`cmdOk` (no están en la referencia de bots; tomadas del código);
- el error exacto de un mensaje demasiado grande (el canal no depende de él: reintenta sin cita y nunca supera el
  límite por diseño);
- que `fileStatus.type` sea `rcvInvitation` en todos los clientes al recibir un archivo nuevo, y que las notas de voz
  de iOS y Android lleguen como M4A (se aceptan también Ogg y WAV);
- los nombres de los binarios de cada versión (más allá de `simplex-chat-ubuntu-22_04-x86_64`, el que usa
  `install.sh`) y las bibliotecas que necesita el binario en la imagen Docker (gmp, zlib, openssl de Ubuntu 22.04);
- las opciones de endurecimiento del servicio contra el binario real (por eso no se incluyen
  `MemoryDenyWriteExecute` ni `SystemCallFilter`).

### Limitaciones conocidas

- Si la CLI se reinicia con eventos aún sin entregar, o el worker los descarta por sobrecarga, esos mensajes quedan en
  la base de datos de la CLI pero no llegan al asistente (no hay recuperación de mensajes atrasados).
- El canal no envía archivos (`adjuntos_salientes=false`).
- Un adjunto rechazado no se señala aparte: el mensaje llega con su texto y sin el adjunto (el contrato de la pasarela
  no tiene un campo para el motivo).
