"""Read the user's own tax documents and propose profile facts from them (the tax domain's document reader).

A withholding certificate, a payslip or the AEAT "datos fiscales" already hold the figures the calculator
needs. The ``documentos`` capability reads the upload (``capacidades/documentos``, through ``pedir``: the text
once, or a scan twice), with the instructions and schema below; this module keeps what only the tax domain knows
— which facts a document can establish and the check of each value — and returns them as *proposals*: nothing is
written to the case until the user confirms.

The safeguard against an invented number is mechanical, not a prompt: every proposed fact must come with the
literal passage it was read from, and a proposal whose passage does not appear in the document is discarded.

Scanned PDFs and photos have no text to check against, so the model reads the pages itself and the safeguard
changes: the document is read TWICE, independently, and only the facts on which both readings agree are
proposed — flagged as read from an image, for the user to check against the paper.

(Moved from ``tax/documentos.py``, which stays an alias of this module.)
"""

from __future__ import annotations

from datetime import date
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hacienda_gpt.capacidades.documentos.lectura import (
    MAX_DOCUMENT_BYTES,
    MAX_PAGES,
    MAX_SCAN_PAGES,
    DocumentoEscaneadoError,
    DocumentoNoLegibleError,
    decode_upload,
    extract_text,
    normalizar_pasaje,
    tipo_de_imagen,
)
from hacienda_gpt.decision.fact_extractor import EXPECTED_FACT_TYPE, _coerce_value
from hacienda_gpt.ops import formatos
from hacienda_gpt.ops.formatos_documentos import observar_lectura, palabras_permitidas
from hacienda_gpt.tax.profile import ACTIVIDAD_FACT_FIELDS, PROFILE_FACT_TYPES

logger = logging.getLogger(__name__)

# Facts a document can establish: the fiscal profile, the annual income and expenses of a
# self-employed activity (figures only: what the user must declare, such as the single-client
# requirements, is never read off a document) and the fiscal year.
DOCUMENT_ACTIVITY_FACTS: tuple[str, ...] = tuple(
    name for name in ACTIVIDAD_FACT_FIELDS if name != "actividad_rendimientos_irregulares"
)
DOCUMENT_FACTS: tuple[str, ...] = (*PROFILE_FACT_TYPES, *DOCUMENT_ACTIVITY_FACTS, "periodo_fiscal")


class HechoPropuesto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | float | bool
    # The literal passage of the document the value was read from.
    evidencia: str
    # "texto": the passage was found literally in the document. "imagen": read by
    # the model from a scan or photo and confirmed by a second, independent reading.
    origen: Literal["texto", "imagen"] = "texto"


class AnalisisDocumento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    tipo_documento: str
    paginas: int
    caracteres: int
    lectura: Literal["texto", "imagen"] = "texto"
    hechos_propuestos: list[HechoPropuesto] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """\
Lees un documento fiscal de un contribuyente español (certificado de retenciones, nómina, datos fiscales de la \
AEAT, certificado de aportaciones a un plan de pensiones, certificado de donativos, extracto de un bróker, libro \
registro de ingresos o de gastos de un autónomo, modelo 130…) y \
localizas en él los datos de su perfil fiscal. El resultado se le mostrará al usuario para que lo confirme antes de \
guardarlo, y cada dato se comprobará contra el texto del documento.

Para cada dato que el documento diga de forma expresa, devuelve:
- `name`: el campo del perfil al que corresponde.
- `value`: el valor como texto. Los importes, el total ANUAL en euros y solo con dígitos y punto decimal \
("38250.40"); si el documento es de un solo mes (una nómina), no lo anualices: no devuelvas ese importe.
- `evidencia`: el fragmento LITERAL del documento, copiado tal cual y sin resumir, donde aparece ese valor \
(la etiqueta y la cifra, una línea o parte de ella).

Campos y qué buscar:
- rendimiento_trabajo_bruto: retribuciones dinerarias y en especie íntegras del año (suma si hay varias).
- cotizaciones_seguridad_social: gastos deducibles por cotizaciones del trabajador.
- retenciones: retenciones e ingresos a cuenta del IRPF practicados en el año.
- rendimiento_capital_mobiliario, ganancias_patrimoniales, rendimiento_alquiler_neto, rendimiento_actividad_neto.
- aportacion_plan_pensiones (del partícipe), contribucion_empresa_plan_pensiones (del promotor), donativos, \
gastos_guarderia, pagos_vivienda_habitual_pre2013.
- Actividad de un autónomo (siempre totales del AÑO COMPLETO y sin IVA; si el documento solo cubre un mes o un \
trimestre, no devuelvas estos importes):
  - actividad_ingresos_integros: total anual de ingresos de la actividad. Búscalo en el total del libro registro \
de ingresos o de facturas emitidas (base imponible, sin IVA), en el apartado de actividades económicas de una \
declaración de la renta, o en los ingresos acumulados de un modelo 130 SOLO si es el del cuarto trimestre.
  - actividad_seguridad_social_autonomo (cuotas de autónomos/RETA del titular), actividad_suministros, \
actividad_alquileres (arrendamientos del local u oficina), actividad_servicios_profesionales (gestoría, asesoría y \
otros profesionales independientes), actividad_amortizaciones y actividad_otros_gastos: solo cuando el documento \
dé el total anual de ESE concepto (libro registro de gastos con totales por concepto, desglose de gastos de la \
actividad en una declaración de la renta). No repartas un total de gastos entre conceptos ni uses el total de \
gastos de un modelo 130 (puede incluir ya los gastos de difícil justificación). No uses un modelo 303 o 390 para \
estos campos: sus bases son de IVA, no los ingresos y gastos del IRPF.
- periodo_fiscal: el ejercicio al que se refiere el documento (año de cuatro cifras).
- comunidad_autonoma, estado_civil, edad, num_descendientes y similares solo si constan de forma expresa.

No deduzcas ni calcules nada que no esté escrito, salvo sumar importes del mismo concepto (cita entonces uno de \
ellos como evidencia). Si un dato no aparece, no lo incluyas. En `tipo_documento` describe en pocas palabras qué \
es el documento.
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tipo_documento": {"type": "string"},
        "hechos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": list(DOCUMENT_FACTS)},
                    "value": {"type": "string"},
                    "evidencia": {"type": "string"},
                },
                "required": ["name", "value", "evidencia"],
            },
        },
    },
    "required": ["tipo_documento", "hechos"],
}


SYSTEM_PROMPT_IMAGEN = SYSTEM_PROMPT + """
El documento te llega como imagen (escaneado o fotografiado): léelo tú. En `transcripcion` copia TODO el texto \
legible, tal cual, línea a línea, sin corregir ni completar nada. La `evidencia` de cada dato debe ser un fragmento \
literal de tu transcripción. Si una cifra no se lee con claridad (borrosa, cortada, tapada), NO la incluyas como dato.
"""

SCHEMA_IMAGEN: dict[str, Any] = {
    **SCHEMA,
    "properties": {"transcripcion": {"type": "string"}, **SCHEMA["properties"]},
    "required": ["transcripcion", *SCHEMA["required"]],
}


MAX_ETIQUETAS_ADICIONALES = 30


class EtiquetaAdicional(BaseModel):
    """One more label a profile field may appear under in a document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    campo: str
    etiqueta: str

    @field_validator("campo")
    @classmethod
    def _campo_conocido(cls, value: str) -> str:
        if value not in DOCUMENT_FACTS:
            raise ValueError(f"campo desconocido: {value}")
        return value

    @field_validator("etiqueta")
    @classmethod
    def _solo_etiquetas(cls, value: str) -> str:
        # The label ends up in the prompt of every later reading: only generic fiscal words may get there.
        value = " ".join(value.split())
        if not 3 <= len(value) <= 60 or not palabras_permitidas(value):
            raise ValueError("la etiqueta debe tener de 3 a 60 caracteres y solo palabras del vocabulario fiscal")
        return value


class ReglasDocumentos(BaseModel):
    """Reviewed additions to the reader's prompt, kept as data (the ``documentos`` format spec; empty in v1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vocabulario: tuple[EtiquetaAdicional, ...]

    @field_validator("vocabulario")
    @classmethod
    def _acotado(cls, value: tuple[EtiquetaAdicional, ...]) -> tuple[EtiquetaAdicional, ...]:
        if len(value) > MAX_ETIQUETAS_ADICIONALES:
            raise ValueError(f"como máximo {MAX_ETIQUETAS_ADICIONALES} etiquetas adicionales")
        return value


def anexo_vocabulario() -> str:
    """Text appended to the system prompt: empty unless a reviewed vocabulary proposal was applied."""
    try:
        reglas = formatos.reglas_de("documentos", ReglasDocumentos)
    except Exception:  # the watch must never stop a document from being read
        logger.warning("could not load the document vocabulary spec", exc_info=True)
        return ""
    if not reglas.vocabulario:
        return ""
    lineas = "\n".join(f"- {item.campo}: «{item.etiqueta}»" for item in reglas.vocabulario)
    return (
        "\nAlgunos documentos rotulan estos campos con otras etiquetas (añadidas tras revisar lecturas fallidas); "
        "sirven para localizar el dato, nunca para suponerlo:\n" + lineas + "\n"
    )


# Kept for existing callers.
_normalize = normalizar_pasaje


def _hechos_validos(raw: dict[str, Any], haystack: str) -> tuple[dict[str, HechoPropuesto], list[str]]:
    """Keep the facts whose passage is in ``haystack`` (normalised); return them and the discarded names."""
    proposed: dict[str, HechoPropuesto] = {}
    discarded: list[str] = []
    for item in raw.get("hechos") or []:
        if not isinstance(item, dict):
            continue
        name, value, evidence = item.get("name"), item.get("value"), item.get("evidencia")
        expected = EXPECTED_FACT_TYPE.get(name) if isinstance(name, str) else None
        if not isinstance(name, str) or expected is None or name not in DOCUMENT_FACTS:
            continue
        if not isinstance(evidence, str) or len(_normalize(evidence)) < 4 or _normalize(evidence) not in haystack:
            # The cited passage is not in the document: the value cannot be trusted.
            discarded.append(name)
            continue
        coerced = _coerce_value(
            expected=expected,
            value_string=value if isinstance(value, str) else None,
            value_number=value if isinstance(value, int | float) and not isinstance(value, bool) else None,
            value_boolean=value if isinstance(value, bool) else None,
        )
        if coerced is None:
            discarded.append(name)
            continue
        proposed.setdefault(name, HechoPropuesto(name=name, value=coerced, evidencia=" ".join(evidence.split())))
    return proposed, discarded


def _tipo(raw: dict[str, Any]) -> str:
    tipo = raw.get("tipo_documento")
    return tipo.strip() if isinstance(tipo, str) and tipo.strip() else "Documento"


def _transcripcion(raw: dict[str, Any]) -> str:
    value = raw.get("transcripcion")
    return value if isinstance(value, str) else ""


def _leer(provider: Any, *, filename: str, data: bytes) -> dict[str, Any]:
    """The readings of the ``documentos`` capability, asked for by the tax domain (``pedir``)."""
    from hacienda_gpt.capacidades.contrato import ContextoPeticion, pedir
    from hacienda_gpt.capacidades.documentos import contenido

    resultado = pedir(
        "documentos",
        "leer_campos",
        {
            "nombre_fichero": filename,
            "contenido_base64": contenido(data),
            "instrucciones": SYSTEM_PROMPT + anexo_vocabulario(),
            "esquema": SCHEMA,
            "nombre_esquema": "datos_documento_fiscal",
            "instrucciones_imagen": SYSTEM_PROMPT_IMAGEN + anexo_vocabulario(),
            "esquema_imagen": SCHEMA_IMAGEN,
            "nombre_esquema_imagen": "datos_documento_fiscal_escaneado",
            "que_localizar": "los datos del perfil fiscal",
        },
        ContextoPeticion(ambito="fiscal", hoy=date.today(), proveedor=provider),
    )
    return dict(resultado.datos)


def _leer_imagen(lecturas: list[dict[str, Any]], *, filename: str, paginas: int) -> AnalisisDocumento:
    """Two independent readings of a scan or photo; only what both agree on is proposed."""
    validadas = [(raw, *_hechos_validos(raw, _normalize(_transcripcion(raw)))) for raw in lecturas]
    (raw, primera, descartados), (_, segunda, descartados_2) = validadas

    coinciden = {name: fact for name, fact in primera.items() if name in segunda and segunda[name].value == fact.value}
    discrepan = sorted((set(primera) | set(segunda)) - set(coinciden))
    hechos = [fact.model_copy(update={"origen": "imagen"}) for fact in coinciden.values()]

    avisos = [
        "Documento leído a partir de la imagen (escaneado o fotografiado): los datos no se pueden comprobar contra un "
        "texto, así que revisa cada cifra con el papel antes de guardarla."
    ]
    if discrepan:
        avisos.append(
            "Se han descartado datos en los que dos lecturas independientes del documento no coinciden: "
            + ", ".join(discrepan)
            + ". Introdúcelos a mano."
        )
    sin_pasaje = sorted((set(descartados) | set(descartados_2)) - set(discrepan) - set(coinciden))
    if sin_pasaje:
        avisos.append("Se han descartado datos sin un pasaje legible que los respalde: " + ", ".join(sin_pasaje) + ".")
    if not hechos:
        avisos.append("No se han encontrado datos del perfil fiscal legibles en este documento.")
    # Format watch: counters and field names only (see ops/formatos_documentos.py).
    observar_lectura(
        tipo_documento=_tipo(raw), propuestos=len(hechos), descartados=[*discrepan, *sin_pasaje], lectura="imagen"
    )
    return AnalisisDocumento(
        filename=filename,
        tipo_documento=_tipo(raw),
        paginas=paginas,
        caracteres=len(_transcripcion(raw)),
        lectura="imagen",
        hechos_propuestos=hechos,
        avisos=avisos,
    )


def analizar_documento(provider: Any, *, filename: str, data: bytes) -> AnalisisDocumento:
    """Extract proposed facts. Raises ``DocumentoNoLegibleError`` or the provider's error."""
    leido = _leer(provider, filename=filename, data=data)
    if leido["lectura"] == "imagen":
        return _leer_imagen(list(leido["lecturas"]), filename=filename, paginas=int(leido["paginas"]))
    text, pages = str(leido["texto"]), int(leido["paginas"])
    (raw,) = leido["lecturas"]
    proposed, discarded = _hechos_validos(raw, _normalize(text))

    avisos: list[str] = []
    if discarded:
        avisos.append(
            "Se han descartado datos que no se pudieron localizar literalmente en el documento: "
            + ", ".join(sorted(set(discarded)))
            + "."
        )
    if not proposed:
        avisos.append("No se han encontrado datos del perfil fiscal en este documento.")
    # Format watch: counters and field names; the text only to derive an anonymised skeleton, if enabled.
    observar_lectura(
        tipo_documento=_tipo(raw), propuestos=len(proposed), descartados=discarded, lectura="texto", texto=text
    )
    return AnalisisDocumento(
        filename=filename,
        tipo_documento=_tipo(raw),
        paginas=pages,
        caracteres=len(text),
        hechos_propuestos=list(proposed.values()),
        avisos=avisos,
    )


__all__ = [
    "DOCUMENT_ACTIVITY_FACTS",
    "DOCUMENT_FACTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_ETIQUETAS_ADICIONALES",
    "MAX_PAGES",
    "MAX_SCAN_PAGES",
    "SCHEMA",
    "SCHEMA_IMAGEN",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_IMAGEN",
    "AnalisisDocumento",
    "DocumentoEscaneadoError",
    "DocumentoNoLegibleError",
    "EtiquetaAdicional",
    "HechoPropuesto",
    "ReglasDocumentos",
    "analizar_documento",
    "anexo_vocabulario",
    "decode_upload",
    "extract_text",
    "normalizar_pasaje",
    "tipo_de_imagen",
]
