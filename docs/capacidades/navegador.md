# Agente con navegador: guiones, misiones y confinamiento

El agente con navegador (`asistente/capacidades/navegador`) tiene dos formas de trabajar. Un guion fija los
pasos de una tarea en un sitio, con selectores CSS revisados por una persona. Una misión es un objetivo escrito
por el usuario para un sitio declarado, y los pasos los elige un modelo viendo la página. Las dos comparten
navegador, inicio de sesión, política de red y gestor de tareas. El diseño está en el
[ADR 0008](../adr/0008-mcp-y-agente-de-navegador.md).

Cuando existe un guion para lo que se quiere hacer, es la mejor opción: no gasta modelo y hace siempre lo mismo.
Hoy hay dos, la descarga del consumo de Datadis y una sede de ejemplo.

## Misiones

Una misión necesita `NAVEGADOR_ACTIVO=true` y `NAVEGADOR_AGENTE=true`. La segunda está apagada por defecto,
porque con ella el texto de las páginas visitadas sale hacia el proveedor del modelo.

Se encarga desde el panel «Navegador» (sitio, objetivo y los datos que el agente podrá escribir), con
`POST /navegador/misiones`, o desde el chat con la herramienta `navegador_encargar_mision`. En el chat solo queda
preparada: la persona revisa en el panel el objetivo y los datos que escribió el modelo, acepta el texto de
consentimiento de las misiones y pulsa «Empezar».

En cada paso el modelo recibe el objetivo, los datos del usuario, la lista de sus acciones anteriores y la
instantánea ARIA de la página (`page.aria_snapshot(mode="ai")` de Playwright), donde cada elemento lleva una
referencia como `[ref=e12]`. Responde con una acción:

| Acción | Qué hace | Qué comprueba el código antes |
|---|---|---|
| `ir` | Abre una ruta de un origen del sitio | Que el origen sea del sitio y la página no redirija fuera |
| `pulsar` | Pulsa un elemento | Que su texto no envíe, firme, pague, acepte ni borre; que no sea un botón que manda un formulario por POST; que el enlace no salga del sitio |
| `rellenar` | Escribe en un campo de texto | Que no sea un campo de credenciales o de pago, y que el valor esté en el objetivo o en los datos del usuario |
| `seleccionar` | Elige una opción de un desplegable | Que la opción exista y sea única |
| `esperar` | Deja cargar la página | Nada |
| `descargar` | Pulsa un enlace de descarga | Que el sitio declare descargas; tipo y tamaño del fichero |
| `detener` | Localiza el botón que enviaría y para | Que el botón sea de los que envían, firman o pagan. No lo pulsa |
| `terminar` | Da la respuesta | Que no esté vacía |

Una acción rechazada no acaba la misión: el modelo recibe el motivo y elige otra. La misión sí acaba tras 6
rechazos, tras repetir tres veces la misma acción sobre la misma página o al llegar a `NAVEGADOR_AGENTE_MAX_PASOS`
(25 por defecto). Cada paso es una llamada al modelo, medida con el propósito `navegador:agente` y atribuida al
usuario y al expediente.

### Garantías que no dependen del modelo

- La red es la de los guiones: solo los orígenes del sitio y, después del inicio de sesión, solo GET. Un clic que
  se colara no podría enviar un formulario.
- Las referencias válidas son las de la instantánea que se le acaba de enseñar al modelo. No puede actuar sobre
  algo que no vio.
- En `rellenar` solo valen valores que escribió el usuario. El modelo no puede copiar a un campo algo que leyó
  en la página.
- La instantánea llega al modelo entre marcas de datos no confiables y con CUPS, IBAN, NIF, correos, teléfonos y
  números largos enmascarados. Las fechas y los importes se conservan, porque una misión suele tener que leerlos.
- Los nombres de los elementos que aparecen en el registro de pasos van saneados y recortados.
- Las capturas en vivo ocultan lo escrito en los formularios.
- Si la misión llega al botón que enviaría, deja una acción pendiente con las etiquetas de lo que se mandaría
  (sin los valores) y, en modo ventana, la ventana queda para que la persona lo envíe ella misma.

### Lo que una misión no garantiza

- La respuesta de `terminar` es lo que el modelo dice haber leído. Se enseña con el aviso «compruébalo» y solo
  vive en la memoria del proceso mientras la tarea está abierta.
- El enmascarado es por patrones. Un nombre propio en mitad de una tabla llega al modelo.
- Una misión preparada desde el chat lleva un objetivo escrito por un modelo. La revisión de la persona antes de
  «Empezar» es la comprobación.
- No se ha probado contra ningún sitio real: en esta máquina los tests no salen a la red.

## Sitios

Un sitio declara dónde puede ir el navegador, sin pasos. Hay tres fuentes: los JSON de `NAVEGADOR_SITIOS_DIR`,
los incluidos en `navegador/sitios/` (hoy `boe.json`, público y sin inicio de sesión) y los sitios de los guiones
disponibles, que sirven tal cual con la clave del guion.

```json
{
  "fuente": "sitio_ejemplo",
  "version": 1,
  "reglas": {
    "titulo": "Sede de ejemplo",
    "descripcion": "Consulta de expedientes.",
    "origenes": {"sede": "https://sede.ejemplo.es"},
    "login": {"origen": "sede", "ruta": "/acceso", "hecho_cuando": "#usuario"},
    "inicio": {"origen": "sede", "ruta": "/expedientes"},
    "enmascarar": ["#usuario"]
  }
}
```

Los orígenes son siempre `https`. `login` es opcional: sin él, la misión corre sin ventana. `descarga` declara
las extensiones y el tamaño máximo de fichero que una misión puede guardar.

## Confinamiento de Chromium

Vale para guiones y misiones (`navegador/confinamiento.py`).

- Cada ejecución tiene su carpeta temporal para descargas y para el lanzador, borrada al terminar. El contexto
  del navegador ya era efímero: sin perfil y sin cookies guardadas.
- `--host-resolver-rules` hace que Chromium no resuelva ningún nombre de host fuera de los del sitio, también
  para lo que no pasa por la interceptación de rutas. Se apagan además las actualizaciones de componentes, la
  sincronización y las métricas.
- En macOS el proceso arranca bajo `sandbox-exec` con un perfil que le niega leer y escribir en el directorio
  del usuario, salvo los ficheros del propio navegador. Una página que apunte a `file:///Users/…` recibe
  «acceso denegado». Hay un test que lo comprueba con y sin confinamiento.

`NAVEGADOR_CONFINAMIENTO` vale `auto` (lo aplica si puede), `sandbox-exec` (lo exige: sin él la tarea no arranca)
o `ninguno`. `GET /navegador/estado` dice cuál rige.

No cubre las conexiones a una dirección IP escrita en una página (las corta la interceptación, no la regla de
nombres), la lectura fuera del directorio del usuario, ni la comunicación con otros procesos del mismo usuario.
En Linux no hay equivalente incluido: la frontera es el contenedor del despliegue y el sandbox del propio
Chromium. Apple marca `sandbox-exec` como obsoleto, aunque lo sigue distribuyendo. El perfil se ha probado con el
Chromium de Playwright 1.65 en macOS 26: los tests corren sin ventana, y con ventana se comprobó a mano que
arranca, carga una página y hace la captura. Un inicio de sesión con certificado o con Cl@ve reales no se ha
probado bajo el perfil.

## Variables

| Variable | Por defecto | Qué hace |
|---|---|---|
| `NAVEGADOR_AGENTE` | `false` | Permite las misiones. |
| `NAVEGADOR_AGENTE_MAX_PASOS` | `25` | Pasos (llamadas al modelo) de una misión. |
| `NAVEGADOR_AGENTE_MAX_INSTANTANEA` | `12000` | Caracteres de página que recibe el modelo en cada paso. |
| `NAVEGADOR_SITIOS_DIR` | vacío | Carpeta con declaraciones de sitios del operador. |
| `NAVEGADOR_CONFINAMIENTO` | `auto` | `auto`, `sandbox-exec` o `ninguno`. |

## Pruebas

`tests/navegador/test_agente.py` y `test_misiones.py` usan un modelo de pega que lee el mismo mensaje que
recibiría uno real, contra las webs simuladas de `tests/navegador/simulacion.py` en un Chromium de verdad. Uno de
los casos es un modelo que obedece a una página hostil: intenta darse de baja, mandar un formulario, salir del
sitio, ir a otro origen, escribir un NIF que leyó y actuar sobre un elemento que no se le enseñó. Las seis
acciones se rechazan y el sitio no recibe nada más que el inicio de sesión del usuario. Estos tests llevan la
marca `navegador` y quedan fuera de `make test-rapido`:

```bash
uv run pytest -m navegador tests/navegador
```
