# Módulos del cerebro: contrato común y evaluación propia

Regla del plan: **un módulo solo cuenta si supera su propia evaluación**. Cada ámbito de `asistente/ambitos/` declara
en sus ganchos v2 (`asistente/nucleo/modulos/ganchos.py`) una `Declaracion` con:

| Campo | Qué es |
|---|---|
| `capacidades` | Frases en español con lo que el módulo promete hacer. |
| herramientas / `fuentes` | Las del contrato v1 (`contrato.py`): herramientas deterministas y fuentes oficiales (BOE primero). |
| `evaluacion` | Sus casos propios, `eval_data/modulos/<clave>.jsonl`. |
| `umbral_evaluacion` | Parte de casos que debe superar (por defecto, el 100 %). |
| `ofrece` | Datos que sirve a otros módulos (`DatoOfrecido`: uno de sus hechos o una herramienta de solo lectura). |
| `pide` | Datos que puede pedir a otros (`DatoPedido`: módulo, dato y para qué). |

Los datos entre módulos pasan solo por `asistente.nucleo.modulos.datos.pedir_dato`, que los sirve únicamente si los
**dos** lados los declararon; la herramienta del dueño se ejecuta sin `registrar` (no escribe nada). El cambio es
retrocompatible: un módulo v1, o uno v2 sin declaración, recibe una `Declaracion` vacía y sigue cargando. El
registro valida su forma (`registro.problemas_de_declaracion`).

## Casos de evaluación

Cada caso ejecuta una herramienta del módulo (o sus `avisos` diarios) sobre un perfil y una fecha fijos, sin red
ni modelo, y comprueba el texto: `contiene`, `no_contiene`, `error` y número de `avisos`. Tipos:

- `normal`: el uso habitual;
- `dificil`: el borde de una regla o una trampa donde la respuesta ingenua es errónea (DNI renovado antes de los
  30, festivo nacional en martes, pago anual que sube de precio, amortizar más de lo que se debe…);
- `abstencion`: faltan datos o la pregunta queda fuera de su alcance; el módulo debe decirlo y **no** dar una cifra.

`tests/modulos_nucleo/test_contrato_ambitos.py` recorre el registro real y falla si un ámbito no cumple el
contrato, no declara capacidades, herramientas, fuentes o casos (mínimo 6, con difíciles y de abstención), no
supera su evaluación, o pide un dato que su dueño no ofrece.

Para ver las puntuaciones:

```bash
.venv/bin/python -m asistente.nucleo.evaluacion.modulos
```

## Estado

| Módulo | Contrato | Casos | Difíciles | Abstención | Superados | Puntuación | Umbral | Ofrece | Pide | Estado |
|---|---|---|---|---|---|---|---|---|---|---|
| fiscal | v2 completo | 12 | 4 | 4 | 12 | 100 % | 100 % | calendario | hipoteca.pagos_del_ejercicio, ayudas.prestaciones | cumple |
| ayudas | v2 completo | 10 | 4 | 3 | 10 | 100 % | 100 % | prestaciones | luz.tarifa_actual | cumple |
| caducidades | v2 completo | 9 | 3 | 3 | 9 | 100 % | 100 % | proximas, contratos | suscripciones.servicios | cumple |
| hipoteca | v2 completo | 10 | 4 | 3 | 10 | 100 % | 100 % | pagos_del_ejercicio, prestamo | — | cumple |
| luz | v2 completo | 11 | 3 | 4 | 11 | 100 % | 100 % | tarifa_actual, potencia_contratada | — | cumple |
| suscripciones | v2 completo | 9 | 2 | 4 | 9 | 100 % | 100 % | servicios, gasto_anual | — | cumple |

Límites conocidos: las evaluaciones son deterministas y prueban las herramientas, no la conversación (eso lo cubre
la suite de `asistente/nucleo/evaluacion`). Quedan sin casos las herramientas que necesitan red o servicios externos
(PVPC de REE, descarga de Datadis, convocatorias del BOE y facturas del correo).
