"""Capabilities a task step can use: e-mail, browser, documents, research.

A capability is a small adapter with a CLOSED list of operations, each with a JSON Schema for its parameters
(checked by the plan gate, :mod:`hacienda_gpt.tareas.validacion`). Every operation is deterministic, never
sends or submits anything, and degrades to «the user does it» when the module behind it is off:

* ``correo`` — ``resumen``: what the Gmail module (read-only) classified, through its own tool, when the user
  switched it on; otherwise the step says so.
* ``navegador`` — ``consumo_datadis``: tells the user how to get the hourly consumption into the electricity
  module: with the browser agent (operator switch and module on) from the «Navegador» panel, where THEY press
  «Empezar» and log in; otherwise downloading the CSV themselves. The browser task itself lives in the API
  process, so the step never starts it: it explains and a later ``espera`` step waits for the data to arrive.
* ``documentos`` — ``lista_de_comprobacion`` (what to have at hand: for passports and ID cards from the
  expiry module's data, with its BOE references; for the income tax return an orientative list derived from
  the case's facts), ``borrador`` (a draft letter, marked as such, never sent), ``pedir_documento`` (ask the
  user to have a document ready).
* ``investigacion`` — ``webs_oficiales``: official information about a procedure, ONLY under the official
  site roots in :func:`raices_oficiales` (from the modules' versioned data). Without a configured searcher
  (none on this machine: only www.boe.es is reachable) it points the user to those roots and says it did not
  read them; a searcher's result outside the roots is dropped.

Tests replace any capability with a fake through :class:`hacienda_gpt.tareas.motor.Motor`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
import logging
from typing import Any, Literal, Protocol

from hacienda_gpt import settings
from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.modulos.contrato import ContextoModulo, Modulo, ResultadoHerramienta

logger = logging.getLogger(__name__)

RAIZ_BOE = "https://www.boe.es/"


@dataclass(frozen=True)
class Operacion:
    nombre: str
    descripcion: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ContextoCapacidad:
    case: CaseState
    hoy: date
    modulos_activos: Sequence[Modulo]
    # Stable per (task, step): what an operation creates elsewhere must use it, so a retry repeats nothing.
    clave: str
    perfil: Mapping[str, Any] = field(default_factory=dict)

    def modulo(self, clave: str) -> Modulo | None:
        return next((m for m in self.modulos_activos if m.clave == clave), None)


@dataclass(frozen=True)
class ResultadoCapacidad:
    estado: Literal["hecho", "esperando_usuario"] = "hecho"
    resumen: str = ""
    evidencias: tuple[str, ...] = ()
    borrador: str | None = None
    # A notice for the user (title, body), created once per step.
    aviso: tuple[str, str] | None = None
    # A failure to retry (with backoff) and, after the last attempt, to show the user.
    es_error: bool = False


class Capacidad(Protocol):
    @property
    def clave(self) -> str: ...

    @property
    def nombre(self) -> str: ...

    @property
    def operaciones(self) -> Mapping[str, Operacion]: ...

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad: ...


def _schema(propiedades: dict[str, Any], requeridas: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": propiedades,
        **({"required": list(requeridas)} if requeridas else {}),
    }


def _ejecutar_herramienta(modulo: Modulo, nombre: str, argumentos: Mapping[str, Any], ctx: ContextoCapacidad) -> Any:
    herramienta = next((h for h in modulo.herramientas if h.spec.name == nombre), None)
    if herramienta is None or herramienta.efecto == "actua":
        return None
    contexto = ContextoModulo(perfil=dict(ctx.perfil), hoy=ctx.hoy, case_id=ctx.case.case_id, registrar=None)
    return herramienta.ejecutar(dict(argumentos), contexto)


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #


class CapacidadCorreo:
    clave = "correo"
    nombre = "Correo (solo lectura)"
    operaciones = {
        "resumen": Operacion(
            "resumen",
            "Resumen de lo que el módulo de correo ha clasificado (solo lectura).",
            _schema({"dias": {"type": "integer", "minimum": 1, "maximum": 365}}),
        )
    }

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        modulo = ctx.modulo("correo")
        if modulo is None:
            return ResultadoCapacidad(
                resumen="El módulo de correo no está activado: no se ha leído ningún correo. Si quieres que se use, "
                "actívalo en «Módulos»; si no, aporta tú los datos."
            )
        argumentos = {"dias": parametros["dias"]} if "dias" in parametros else {}
        resultado = _ejecutar_herramienta(modulo, "correo_resumen", argumentos, ctx)
        if not isinstance(resultado, ResultadoHerramienta):
            return ResultadoCapacidad(resumen="El módulo de correo no ofrece su resumen.", es_error=True)
        return ResultadoCapacidad(
            resumen=resultado.contenido[:3000],
            evidencias=("modulo://correo/correo_resumen", *resultado.citas),
            es_error=resultado.es_error,
        )


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #


class CapacidadNavegador:
    clave = "navegador"
    nombre = "Agente con navegador"
    operaciones = {
        "consumo_datadis": Operacion(
            "consumo_datadis",
            "Explica cómo traer el consumo horario de Datadis al módulo de luz (el usuario inicia la descarga).",
            _schema({}),
        )
    }

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        con_navegador = bool(getattr(settings, "NAVEGADOR_ACTIVO", False)) and ctx.modulo("navegador") is not None
        if con_navegador:
            cuerpo = (
                "Abre «Navegador», elige «Consumo de Datadis» y pulsa «Empezar»: iniciarás sesión tú en Datadis y el "
                "agente descargará el CSV y lo pasará al módulo de luz. No se cambia nada en Datadis."
            )
        else:
            cuerpo = (
                "Descarga de Datadis el CSV de tu consumo horario (los últimos meses) y súbelo en «Módulos» → Luz "
                "(importar consumo). El agente con navegador no está disponible o no lo has activado."
            )
        return ResultadoCapacidad(
            resumen=cuerpo,
            evidencias=("modulo://navegador" if con_navegador else "modulo://luz/luz_importar_consumo",),
            aviso=("Trae tu consumo de luz", cuerpo),
        )


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #

ORIGENES_LISTA = ("caducidades:pasaporte", "caducidades:dni", "renta")
MODELOS_BORRADOR = ("solicitud_cambio_comercializadora",)

# The income tax checklist: (fact names that make the item relevant, item). Orientative, said so: it is a
# list of papers the user's own data point to, not a legal list of requirements.
_LISTA_RENTA: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("rendimiento_trabajo_bruto", "retenciones", "num_pagadores_trabajo"),
        "Certificado de retenciones de cada pagador",
    ),
    (
        ("intereses_prestamo_vivienda", "pagos_vivienda_habitual_pre2013"),
        "Certificado del banco de lo pagado del préstamo de la vivienda",
    ),
    (
        ("alquiler_vivienda_habitual", "dias_alquiler_vivienda_habitual"),
        "Contrato y recibos del alquiler de tu vivienda",
    ),
    (
        ("rendimiento_alquiler_neto", "rendimiento_alquiler_vivienda_sin_reducir"),
        "Ingresos y gastos de los inmuebles que alquilas",
    ),
    (
        ("aportacion_plan_pensiones", "contribucion_empresa_plan_pensiones"),
        "Certificado de aportaciones a planes de pensiones",
    ),
    (("donativos",), "Certificados de los donativos"),
    (("rendimiento_capital_mobiliario",), "Información fiscal de tus cuentas e inversiones"),
    (
        ("ganancias_patrimoniales", "perdidas_patrimoniales_pendientes"),
        "Justificantes de compras y ventas de inversiones o inmuebles",
    ),
    (
        ("es_autonomo", "actividad_economica", "actividad_ingresos_integros"),
        "Libros de ingresos y gastos de tu actividad",
    ),
)


class CapacidadDocumentos:
    clave = "documentos"
    nombre = "Documentos"
    operaciones = {
        "lista_de_comprobacion": Operacion(
            "lista_de_comprobacion",
            "Lista de lo que hay que tener a mano para un trámite.",
            _schema({"origen": {"type": "string", "enum": list(ORIGENES_LISTA)}}, ["origen"]),
        ),
        "borrador": Operacion(
            "borrador",
            "Prepara un borrador de escrito para que el usuario lo revise y lo envíe él (nunca se envía).",
            _schema({"modelo": {"type": "string", "enum": list(MODELOS_BORRADOR)}}, ["modelo"]),
        ),
        "pedir_documento": Operacion(
            "pedir_documento",
            "Pide al usuario que tenga listo un documento.",
            _schema({"documento": {"type": "string", "minLength": 3, "maxLength": 120}}, ["documento"]),
        ),
    }

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        if operacion == "lista_de_comprobacion":
            return self._lista(str(parametros["origen"]), ctx)
        if operacion == "borrador":
            return self._borrador(str(parametros["modelo"]), ctx)
        documento = str(parametros["documento"])
        cuerpo = f"Ten preparado: {documento}. Cuando lo tengas, márcalo como hecho en «Tareas»."
        return ResultadoCapacidad(estado="esperando_usuario", resumen=cuerpo, aviso=(f"Prepara: {documento}", cuerpo))

    def _lista(self, origen: str, ctx: ContextoCapacidad) -> ResultadoCapacidad:
        if origen.startswith("caducidades:"):
            from hacienda_gpt.modulos.caducidades.reglas import cargar_reglas

            regla = getattr(cargar_reglas(), origen.split(":", 1)[1])
            tramite = regla.tramite
            lineas = [f"{tramite.nombre} ({tramite.organismo}):"] + [f"- {p}" for p in tramite.pasos]
            evidencias = tuple(dict.fromkeys(f"{e.boe_id} ({e.articulo})" for e in regla.evidencia))
            return ResultadoCapacidad(resumen="\n".join(lineas), evidencias=evidencias)
        perfil = ctx.perfil
        elementos = [
            texto for nombres, texto in _LISTA_RENTA if any(perfil.get(n) not in (None, "", 0, False) for n in nombres)
        ]
        if not elementos:
            elementos = ["Tus datos fiscales de la Agencia Tributaria, para comprobarlos con tu expediente"]
        resumen = (
            "Lista orientativa de papeles, según los datos de tu expediente (no es una relación legal de "
            "requisitos):\n" + "\n".join(f"- {e}" for e in elementos)
        )
        return ResultadoCapacidad(resumen=resumen, evidencias=("expediente",))

    def _borrador(self, modelo: str, ctx: ContextoCapacidad) -> ResultadoCapacidad:
        del modelo  # one model today: the closed enum is checked by the plan gate
        cups = ctx.perfil.get("luz_cups")
        texto = (
            "BORRADOR — revísalo y envíalo tú a la comercializadora que elijas; HaciendaGPT no lo envía.\n\n"
            "Asunto: Solicitud de contratación del suministro eléctrico\n\n"
            "Solicito contratar con ustedes el suministro eléctrico de mi vivienda con la oferta [nombre de la "
            "oferta elegida], manteniendo la potencia contratada actual.\n"
            f"CUPS: {cups if isinstance(cups, str) and cups else '[tu CUPS, viene en la factura]'}\n"
            "Titular: [nombre y apellidos] — DNI/NIE: [número]\n"
            "Dirección del suministro: [dirección]\n"
            "Cuenta para la domiciliación: [IBAN]\n\n"
            "Comprueba antes en tu contrato actual si tienes permanencia o penalización por baja."
        )
        return ResultadoCapacidad(
            resumen="Borrador preparado para que lo revises y lo envíes tú (no se ha enviado nada).",
            borrador=texto,
            aviso=(
                "Revisa el borrador de tu solicitud",
                "Tienes un borrador listo en «Tareas». No se ha enviado nada.",
            ),
        )


# --------------------------------------------------------------------------- #
# Research (official sites only)
# --------------------------------------------------------------------------- #

# Searcher: (topic, roots) -> [(url, extract)]. None on this machine.
Buscador = Callable[[str, Sequence[str]], Sequence[tuple[str, str]]]


@lru_cache(maxsize=1)
def raices_oficiales() -> frozenset[str]:
    """The official site roots research may use: the BOE and the procedures' sites of the modules' data."""
    raices = {RAIZ_BOE}
    try:
        from hacienda_gpt.modulos.caducidades.reglas import cargar_reglas

        for grupo in cargar_reglas().grupos().values():
            tramite = getattr(grupo, "tramite", None)
            raices.update(str(w) for w in (getattr(tramite, "webs", None) or ()))
    except Exception:  # the expiry module's data unreadable: only the BOE
        logger.exception("Official roots of the expiry module could not be read")
    return frozenset(r if r.endswith("/") else r + "/" for r in raices)


def bajo_raiz(url: str, raices: Sequence[str]) -> bool:
    return any(url == r.rstrip("/") or url.startswith(r) for r in raices)


class CapacidadInvestigacion:
    clave = "investigacion"
    nombre = "Investigación en webs oficiales"

    def __init__(self, buscador: Buscador | None = None) -> None:
        self.buscador = buscador
        self.operaciones = {
            "webs_oficiales": Operacion(
                "webs_oficiales",
                "Información oficial de un trámite, solo de las webs oficiales indicadas.",
                _schema(
                    {
                        "tema": {"type": "string", "minLength": 3, "maxLength": 120},
                        "raices": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 5,
                            "items": {"type": "string", "enum": sorted(raices_oficiales())},
                        },
                    },
                    ["tema", "raices"],
                ),
            )
        }

    def ejecutar(self, operacion: str, parametros: Mapping[str, Any], ctx: ContextoCapacidad) -> ResultadoCapacidad:
        tema = str(parametros["tema"])
        raices = [str(r) for r in parametros["raices"] if str(r) in raices_oficiales()]
        if self.buscador is None:
            return ResultadoCapacidad(
                resumen=(
                    f"No he consultado automáticamente estas webs (desde este servidor no se llega a ellas). Para «{tema}», "
                    "la información oficial está en: " + ", ".join(raices) + "."
                ),
                evidencias=tuple(raices),
            )
        try:
            encontrados = list(self.buscador(tema, raices))
        except Exception:
            logger.exception("Official-site search failed")
            return ResultadoCapacidad(resumen="La búsqueda en las webs oficiales falló.", es_error=True)
        validos = [(u, t) for u, t in encontrados if bajo_raiz(u, raices)]
        descartados = len(encontrados) - len(validos)
        lineas = [f"- {t.strip()[:400]} ({u})" for u, t in validos[:8]] or ["- (no se ha encontrado nada)"]
        if descartados:
            lineas.append(f"Se han descartado {descartados} resultado(s) de webs no oficiales.")
        return ResultadoCapacidad(
            resumen=f"Información oficial sobre «{tema}»:\n" + "\n".join(lineas),
            evidencias=tuple(u for u, _t in validos[:8]) or tuple(raices),
        )


def capacidades_por_defecto(buscador: Buscador | None = None) -> dict[str, Capacidad]:
    return {
        "correo": CapacidadCorreo(),
        "navegador": CapacidadNavegador(),
        "documentos": CapacidadDocumentos(),
        "investigacion": CapacidadInvestigacion(buscador),
    }


__all__ = [
    "MODELOS_BORRADOR",
    "ORIGENES_LISTA",
    "RAIZ_BOE",
    "Buscador",
    "Capacidad",
    "CapacidadCorreo",
    "CapacidadDocumentos",
    "CapacidadInvestigacion",
    "CapacidadNavegador",
    "ContextoCapacidad",
    "Operacion",
    "ResultadoCapacidad",
    "bajo_raiz",
    "capacidades_por_defecto",
    "raices_oficiales",
]
