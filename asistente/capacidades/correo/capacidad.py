"""The mail as a CAPABILITY any domain can use (``capacidades/contrato.py``): read-only operations.

* ``resumen`` — what the mail classified (the ``correo_resumen`` tool), for a long-running task's step;
* ``facturas_de_emisor`` — «facturas de este emisor»: the bills, receipts and charges the mail found from one
  sender's domain (``luz``, ``suscripciones``…), as DERIVED items (date, sender domain, category, figures each
  with the literal fragment it was read from). Suspected phishing and what the user silenced never come out.

Both need the user's consent to the mail (the same switch as the ``correo`` module) and return third-party data,
which ``pedir`` wraps as untrusted before any model sees it. Nothing here writes, sends or proposes: proposals
from the mail to a domain are ``propuestas.py`` (each confirmed by the user in the «Correo» panel).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from asistente.capacidades.contrato import CapacidadBase, ContextoPeticion, OperacionCapacidad, ResultadoCapacidad
from asistente.capacidades.correo import almacen, decisiones
from asistente.capacidades.correo import resumen as resumenes
from asistente.capacidades.correo.clasificacion import CATEGORIAS
from asistente.capacidades.correo.normativa import FICHEROS_LEGALES
from asistente.capacidades.correo.seguridad import envolver
from asistente.nucleo.modulos.contrato import ContextoModulo, ResultadoHerramienta

CLAVE = "correo"
MAX_FACTURAS = 50
# The categories a domain may ask «facturas de este emisor» about.
CATEGORIAS_FACTURA = ("factura_luz", "compra", "cargo")

ENTRADA_RESUMEN: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"dias": {"type": "integer", "minimum": 1, "maximum": 365}},
}
SALIDA_TEXTO: dict[str, Any] = {"type": "object"}

ENTRADA_FACTURAS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "emisor": {"type": "string", "minLength": 3, "maxLength": 120},
        "categorias": {
            "type": "array",
            "items": {"type": "string", "enum": [c for c in CATEGORIAS_FACTURA if c in CATEGORIAS]},
            "maxItems": len(CATEGORIAS_FACTURA),
        },
        "desde": {"type": "string", "maxLength": 10},
        "limite": {"type": "integer", "minimum": 1, "maximum": MAX_FACTURAS},
    },
    "required": ["emisor"],
}
SALIDA_FACTURAS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "elementos": {
            "type": "array",
            "maxItems": MAX_FACTURAS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "fecha": {"type": "string"},
                    "dominio": {"type": "string"},
                    "categoria": {"type": "string"},
                    "campos": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["fecha", "dominio", "categoria", "campos"],
            },
        },
        "total": {"type": "integer", "minimum": 0},
    },
    "required": ["elementos", "total"],
}


def _resumen(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    from asistente.capacidades.correo.herramientas import correo_resumen

    if ctx.case_id is None:
        return ResultadoCapacidad(resumen="Hace falta un expediente para leer el correo.", es_error=True)
    argumentos = {"dias": args["dias"]} if "dias" in args else {}
    resultado = correo_resumen(
        argumentos, ContextoModulo(perfil=dict(ctx.perfil or {}), hoy=ctx.hoy, case_id=ctx.case_id)
    )
    if not isinstance(resultado, ResultadoHerramienta):  # pragma: no cover - the tool's own contract
        return ResultadoCapacidad(resumen="El módulo de correo no ofrece su resumen.", es_error=True)
    return ResultadoCapacidad(
        resumen=resultado.contenido[:3000],
        citas=("modulo://correo/correo_resumen", *resultado.citas),
        es_error=resultado.es_error,
        texto_para_modelo=resultado.contenido,
    )


def _del_emisor(dominio: str, emisor: str) -> bool:
    return dominio == emisor or dominio.endswith("." + emisor)


def _facturas(args: Mapping[str, Any], ctx: ContextoPeticion) -> ResultadoCapacidad:
    if ctx.case_id is None:
        return ResultadoCapacidad(datos={"elementos": [], "total": 0}, resumen="sin expediente", es_error=True)
    emisor = str(args["emisor"]).strip().lower().removeprefix("www.")
    categorias = set(args.get("categorias") or CATEGORIAS_FACTURA)
    try:
        desde = date.fromisoformat(str(args["desde"])) if args.get("desde") else None
    except ValueError:
        return ResultadoCapacidad(
            datos={"elementos": [], "total": 0}, resumen="«desde» debe ser AAAA-MM-DD.", es_error=True
        )
    limite = int(args.get("limite") or 20)
    vigentes = decisiones.vigentes(almacen.ruta_db(), ctx.case_id, ctx.hoy)
    encontrados = [
        e
        for e in almacen.elementos(ctx.case_id, desde=desde)
        if e.categoria in categorias
        and not e.sospechoso
        and e.categoria != "phishing"
        and _del_emisor(e.dominio, emisor)
        and not decisiones.suprimido(e, vigentes)
    ]
    mostrados = encontrados[:limite]
    elementos = [
        {
            "fecha": e.fecha.isoformat(),
            "dominio": e.dominio,
            "categoria": e.categoria,
            "campos": {c.campo: c.valor for c in e.campos},
        }
        for e in mostrados
    ]
    texto = (
        envolver([resumenes.linea(e) for e in mostrados])
        if mostrados
        else f"No hay facturas ni cargos guardados del correo de {emisor}."
    )
    return ResultadoCapacidad(
        datos={"elementos": elementos, "total": len(encontrados)},
        resumen=f"{len(encontrados)} elemento(s) de {emisor}",
        texto_para_modelo=texto,
    )


CAPACIDAD = CapacidadBase(
    clave=CLAVE,
    nombre="Correo (Gmail, solo lectura)",
    descripcion=(
        "Lee tu Gmail en solo lectura para el ámbito que lo necesita: el resumen de lo importante y las facturas, "
        "compras y cargos de un emisor, con cada cifra y su fragmento literal. Nunca envía, borra ni mueve correos."
    ),
    consentimiento="usuario",
    operaciones=(
        OperacionCapacidad(
            clave="resumen",
            descripcion="Resumen de lo que el correo ha clasificado (solo lectura).",
            entrada=ENTRADA_RESUMEN,
            salida=SALIDA_TEXTO,
            ejecutar=_resumen,
        ),
        OperacionCapacidad(
            clave="facturas_de_emisor",
            descripcion="Facturas, compras y cargos que el correo ha encontrado de un emisor (por su dominio).",
            entrada=ENTRADA_FACTURAS,
            salida=SALIDA_FACTURAS,
            ejecutar=_facturas,
        ),
    ),
    # The rules of administrative notifications the mail explains (``normativa.py``), re-verified every week.
    ficheros_legales=tuple(FICHEROS_LEGALES),
)

__all__ = ["CAPACIDAD", "CATEGORIAS_FACTURA", "MAX_FACTURAS"]
