# Ámbitos y capacidades

Esta carpeta documenta los **ámbitos** del hogar (`fiscal`, [luz](luz.md), [caducidades](caducidades.md),
[ayudas](ayudas.md), [suscripciones](suscripciones.md), [hipoteca](hipoteca.md)): cada uno es dueño de sus datos,
sus calculadoras y sus avisos, y declara sus integraciones en `ganchos` (contrato v2,
`hacienda_gpt/modulos/ganchos.py`).

Las **capacidades** son medios que cualquier ámbito usa, documentadas aparte o aquí cuando además tienen cara
en el chat:

| Capacidad | Código | Operaciones | Consentimiento | Quién la pide |
|---|---|---|---|---|
| `correo` ([doc](correo.md)) | `hacienda_gpt/capacidades/correo/` | `resumen`, `facturas_de_emisor` (lectura, datos de terceros) | el interruptor «Correo» de Módulos | luz (`luz_facturas_del_correo`), suscripciones (`suscripciones_cargos_del_correo`), tareas |
| `navegador` | `hacienda_gpt/capacidades/navegador/` | `preparar_descarga_consumo` (prepara; solo luz y el núcleo), `consumo_datadis` (explica; sin permiso) | el interruptor «Agente con navegador» | luz (`luz_descargar_consumo`), tareas |
| `documentos` | `hacienda_gpt/capacidades/documentos/` | `extraer_texto`, `leer_campos` (propósito `documentos`), `lista_de_comprobacion`, `borrador`, `pedir_documento` | ninguno (solo lo que el usuario sube; el envío al proveedor de IA lo autoriza su consentimiento de documentos) | caducidades («léeme este ticket»), fiscal (certificados, nóminas), tareas |
| `investigacion` ([doc](../capacidades/investigacion.md)) | `hacienda_gpt/capacidades/investigacion/` | ver su documentación | ver su documentación | — |

Un ámbito nunca importa a otro ni a una capacidad: la pide con `capacidades.pedir`, que comprueba en código el
ámbito, el consentimiento, el efecto (una operación `actua` exige la confirmación del usuario para esa misma
petición), los esquemas de entrada y salida, mide el gasto por propósito y envuelve como no confiables los
datos de terceros. `GET /capacidades` muestra cada capacidad con su estado de consentimiento; las rutas de
siempre (`/correo/*`, `/navegador/*`, `/modulos/*`) no cambian. Detalle y cómo escribir un ámbito o una
capacidad: README, «Ámbitos y capacidades».
