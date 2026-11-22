"""The chair: what code fixes, what the chair writes, how the ruling is assembled and gated.

The chair is a model, and a model must not decide what the council decided. So the split is strict:

* **Code fixes** (:class:`ResultadoCalculado`): the recommendation and the consensus label (from the members' final
  votes, ``agregacion.consenso_de``), the council's confidence (``agregacion.confianza_del_consejo``), Kendall's W,
  the aggregate order of the proposals, the outlier reviewers, which seat backs which option, and authorship. The
  chair receives them as facts; if its JSON tries to set any of them, it is ignored and the ruling says so
  (:data:`CAMPOS_FIJOS`).
* **The chair writes** (:class:`RedaccionPresidente`): the summary for the user, the strongest arguments for and
  against each option, the minority's best case (MANDATORY when the council is not unanimous — dissent is reported,
  never smoothed over), the decisive assumptions, the sensitivity, the datum that would settle it, and the condition
  when the recommendation is «depende».
* **The figures gate** (:func:`revisar_cifras_consejo`, the brain's ``revisar_cifras`` over a closed world): every
  number in the ruling must be a figure of the dossier (or a number written in the dossier's own texts); no sum of
  figures of different specialists that no tool made. The chair gets one chance to fix a failing draft; what still
  fails is withdrawn from the text, with a warning (:func:`retirar_cifras`). The same gate checks every member's
  proposal (``deliberacion``).
* **No chair, still a ruling** (:func:`redaccion_determinista`): when the chair fails, or the session stops before
  the synthesis (cost cap, budget), the ruling is written by code from the members' own words — honest, marked as
  such, and gated the same way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.agent.evidence import EvidenceLedger, Fragment
from asistente.nucleo.cerebro.colaboracion.puerta import revisar_cifras
from asistente.nucleo.cerebro.contrato import MAX_CIFRAS_POR_INFORME, InformeEspecialista, ResultadoPuerta, Violacion
from asistente.nucleo.consejo.agregacion import DEPENDE, Atipico, Votacion
from asistente.nucleo.consejo.anonimato import recortar
from asistente.nucleo.consejo.contrato import (
    Consenso,
    Dictamen,
    ErrorSenalado,
    ExpedienteConsejo,
    Miembro,
    PosturaOpcion,
    Propuesta,
    PropuestaAnonima,
)
from asistente.nucleo.consejo.prompts import (
    INSTRUCCION_PRESIDENTE,
    REVISION_CIFRAS_PRESIDENTE,
    bloque_expediente,
    json_linea,
    neutralizar,
    vista_anonima,
)

MARCA_RETIRADA = "[cifra retirada: no está en el expediente]"
# Fields of the ruling only code may set: a chair that tries to set them is ignored (and the ruling says so).
CAMPOS_FIJOS = ("recomendacion", "consenso", "confianza", "acuerdo", "autoria", "miembros", "sesion_id", "puerta")
UMBRAL_ACUERDO_ALTO = 0.7
UMBRAL_ACUERDO_MODERADO = 0.4
TOPE_ARGUMENTO = 500
MAX_ARGUMENTOS = 8


class SalidaInvalidaError(ValueError):
    """A model output that cannot be used; the message (Spanish) is sent back in the repair attempt."""


# --------------------------------------------------------------------------------------------------------------
# The figures gate over the dossier (a closed world)
# --------------------------------------------------------------------------------------------------------------


class _LedgerDelExpediente:
    """The dossier's citable sources as the gate sees them: they exist, but their text is not here (no literals)."""

    def __init__(self, fragment_ids: Sequence[str]) -> None:
        self._ids = {f.upper() for f in fragment_ids}

    def get(self, fragment_id: str) -> Fragment | None:
        if fragment_id.upper() not in self._ids:
            return None
        return Fragment(fragment_id=fragment_id, title="expediente del consejo", locator="expediente", text="")


def informes_del_expediente(expediente: ExpedienteConsejo) -> list[InformeEspecialista]:
    """One pseudo-report per specialist holding its dossier figures (so cross-specialist sums are told apart)."""
    por_especialista: dict[str, list[Any]] = {}
    for cifra in expediente.cifras:
        por_especialista.setdefault(cifra.especialista, []).append(cifra)
    informes: list[InformeEspecialista] = []
    for clave, cifras in por_especialista.items():
        # A report holds at most MAX_CIFRAS_POR_INFORME figures: a specialist with more gets several reports.
        for inicio in range(0, len(cifras), MAX_CIFRAS_POR_INFORME):
            tramo = cifras[inicio : inicio + MAX_CIFRAS_POR_INFORME]
            informes.append(
                InformeEspecialista(
                    encargo_id="expediente",
                    especialista=clave,
                    nombre=clave,
                    estado="ok",
                    cifras=tramo,
                    fragment_ids=[c.fragment_id for c in tramo if c.fragment_id],
                )
            )
    return informes


def textos_del_expediente(expediente: ExpedienteConsejo, *, con_preferencia: bool) -> list[str]:
    """The dossier's own texts: a number written there (an age, an option's amount) is the dossier's datum."""
    textos = [expediente.pregunta, *(o.descripcion for o in expediente.opciones)]
    textos += [f"{h.clave}: {h.texto}" for h in expediente.hechos]
    textos += [f"{k}: {v}" for k, v in expediente.perfil_minimo.items()]
    textos += [*expediente.restricciones, *expediente.incertidumbres]
    if con_preferencia and expediente.preferencia_usuario:
        textos.append(expediente.preferencia_usuario)
    return textos


def revisar_cifras_consejo(
    texto: str,
    expediente: ExpedienteConsejo,
    *,
    con_preferencia: bool,
    cifras_desconocidas: Sequence[str] = (),
) -> ResultadoPuerta:
    """The brain's figures gate on ``texto`` against the dossier only (deterministic, no model).

    ``cifras_desconocidas``: figure ids a proposal declared that the dossier does not have (each a violation).
    """
    ledger = cast("EvidenceLedger", _LedgerDelExpediente(expediente.fragment_ids))
    resultado = revisar_cifras(
        texto,
        informes_del_expediente(expediente),
        ledger=ledger,
        textos_usuario=textos_del_expediente(expediente, con_preferencia=con_preferencia),
    )
    if cifras_desconocidas:
        extra = [
            Violacion(
                tipo="cifra_sin_origen",
                literal=cifra_id,
                detalle=f"Cita la cifra {cifra_id}, que no está en el expediente.",
            )
            for cifra_id in cifras_desconocidas
        ]
        resultado = resultado.model_copy(update={"violaciones": [*resultado.violaciones, *extra]})
    return resultado


def describir_violaciones(resultado: ResultadoPuerta | None) -> list[str]:
    """One Spanish line per violation, in the council's words (the critique context, the chair, the warnings)."""
    if resultado is None:
        return []
    lineas: list[str] = []
    for v in resultado.violaciones:
        if v.tipo == "suma_no_combinada":
            linea = (
                f"«{v.literal}» parece una cuenta con cifras del expediente de especialistas distintos "
                f"({', '.join(v.cifras)}) que nadie ha calculado."
            )
        elif v.tipo == "cita_inexistente":
            linea = f"Cita {v.literal}, que no es una fuente del expediente."
        elif v.tipo == "cifra_mal_atribuida":
            linea = v.detalle
        elif v.detalle.startswith("Cita la cifra"):
            linea = v.detalle
        elif v.cifras:
            linea = f"«{v.literal}» no es una cifra del expediente: parece una cuenta hecha a mano con {', '.join(v.cifras)}."
        else:
            linea = f"«{v.literal}» no es una cifra del expediente."
        if linea not in lineas:
            lineas.append(linea)
    return lineas


def retirar_cifras(texto: str | None, violaciones: Sequence[Violacion]) -> str | None:
    """``texto`` with every offending figure replaced by :data:`MARCA_RETIRADA` (an inexistent citation removed)."""
    if not texto:
        return texto
    resultado = texto
    for v in violaciones:
        literal = v.literal.strip()
        if not literal:
            continue
        if v.tipo == "cita_inexistente":
            resultado = resultado.replace(literal, "")
        else:  # only standalone: retiring «43» must not touch «143,00 €»
            resultado = re.sub(rf"(?<![\w.,]){re.escape(literal)}(?!\w|[.,]\d)", MARCA_RETIRADA, resultado)
    return re.sub(r"\s{2,}", " ", resultado).strip()


# --------------------------------------------------------------------------------------------------------------
# What code fixes, and the chair's message
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultadoCalculado:
    """Everything about the ruling that code decides (the chair explains it, never changes it)."""

    votacion: Votacion
    confianza: float
    acuerdo: float | None
    posiciones: dict[str, Propuesta]  # seat -> final position (round 2, or round 1 when it could not revise)
    orden_agregado: list[str] = field(default_factory=list)  # seats, best first, from the cross critiques
    atipicos: list[Atipico] = field(default_factory=list)
    empate_en_cabeza: bool = False

    @property
    def recomendacion(self) -> str:
        return self.votacion.recomendacion

    @property
    def consenso(self) -> Consenso:
        return self.votacion.consenso

    def votos_por_asiento(self) -> dict[str, str]:
        return {asiento: p.recomendacion for asiento, p in self.posiciones.items()}


def nivel_de_acuerdo(acuerdo: float | None) -> str:
    """Kendall's W in words (the chair gets words, not a number it could copy into the ruling)."""
    if acuerdo is None:
        return "no medible (hacen falta al menos tres valoraciones)"
    if acuerdo >= UMBRAL_ACUERDO_ALTO:
        return "alto"
    if acuerdo >= UMBRAL_ACUERDO_MODERADO:
        return "moderado"
    return "bajo"


_CONSENSO_TEXTO: dict[str, str] = {
    "unanime": "unánime",
    "mayoria": "mayoría",
    "dividido": "dividido",
    "insuficiente": "insuficiente",
}


def describir_opcion(expediente: ExpedienteConsejo, opcion_id: str) -> str:
    if opcion_id == DEPENDE:
        return "«depende»"
    for opcion in expediente.opciones:
        if opcion.id == opcion_id:
            return f"{opcion.id} («{opcion.descripcion}»)"
    return opcion_id


def mensaje_presidente(
    expediente: ExpedienteConsejo,
    anonimas: Sequence[PropuestaAnonima],
    mapa: Mapping[str, str],
    calculado: ResultadoCalculado,
    *,
    cambios: Mapping[str, str],
    errores: Mapping[str, Sequence[ErrorSenalado]],
    comprobaciones: Mapping[str, Sequence[str]],
) -> str:
    """The chair's message: the dossier (with the preference), the final proposals under the chair's labels, and
    the result fixed by code. ``mapa``: chair label → seat; the other mappings are by seat."""
    etiqueta_de = {asiento: etiqueta for etiqueta, asiento in mapa.items()}
    lineas = [bloque_expediente(expediente, con_preferencia=True), "", "<propuestas>"]
    for p in anonimas:
        asiento = mapa[p.etiqueta]
        extra = {"cambios_tras_las_criticas": neutralizar(cambios[asiento])} if cambios.get(asiento) else None
        lineas.append(json_linea(vista_anonima(p, extra=extra)))
    lineas.append("</propuestas>")
    lineas.append("<resultado>")
    if calculado.recomendacion == DEPENDE and calculado.consenso == "dividido":
        lineas.append("Recomendación del consejo (fijada por el recuento): «depende»: el consejo está dividido.")
    else:
        lineas.append(
            "Recomendación del consejo (fijada por el recuento): "
            f"{describir_opcion(expediente, calculado.recomendacion)}."
        )
    lineas.append(f"Consenso (fijado por el recuento): {_CONSENSO_TEXTO[calculado.consenso]}.")
    votos = "; ".join(f"{p.etiqueta} → {p.recomendacion}" for p in anonimas)
    lineas.append(f"Qué recomienda cada propuesta: {votos}.")
    if calculado.orden_agregado:
        orden = ", ".join(etiqueta_de[a] for a in calculado.orden_agregado if a in etiqueta_de)
        lineas.append(f"Orden de las propuestas según las críticas cruzadas (de mejor a peor): {orden}.")
        if calculado.empate_en_cabeza:
            lineas.append("En la cabeza de ese orden hay un empate.")
    else:
        lineas.append("Orden de las propuestas según las críticas cruzadas: no disponible.")
    lineas.append(f"Acuerdo entre las valoraciones de las críticas: {nivel_de_acuerdo(calculado.acuerdo)}.")
    if calculado.atipicos:
        lineas.append(
            "Valoraciones atípicas: alguna valoración se aparta mucho de las del resto; se ha tenido en cuenta igual."
        )
    con_errores = [(etiqueta_de[a], e) for a, e in errores.items() if e and a in etiqueta_de]
    if con_errores:
        lineas.append("Errores señalados por las críticas (sobre la primera versión de cada propuesta):")
        for etiqueta, senalados in sorted(con_errores, key=lambda par: par[0]):
            textos = "; ".join(f"[{e.tipo}] {neutralizar(e.texto)}" for e in senalados)
            lineas.append(f"- {etiqueta}: {textos}")
    con_avisos = [(etiqueta_de[a], c) for a, c in comprobaciones.items() if c and a in etiqueta_de]
    if con_avisos:
        lineas.append("Comprobación automática de cifras (versión final de cada propuesta):")
        for etiqueta, lineas_puerta in sorted(con_avisos, key=lambda par: par[0]):
            lineas.append(f"- {etiqueta}: {' '.join(neutralizar(c) for c in lineas_puerta)}")
    lineas.append("</resultado>")
    lineas.append("")
    lineas.append(INSTRUCCION_PRESIDENTE)
    return "\n".join(lineas)


def mensaje_correccion_presidente(mensaje: str, anterior: Mapping[str, Any], problemas: Sequence[str]) -> str:
    """The chair's message again, with its previous JSON and the numbers the gate rejected."""
    lista = "\n".join(f"- {neutralizar(p)}" for p in problemas)
    return "\n".join(
        [
            mensaje,
            "",
            "<tu_dictamen_anterior>",
            neutralizar(json_linea(anterior))[:8000],
            "</tu_dictamen_anterior>",
            REVISION_CIFRAS_PRESIDENTE.replace("{problemas}", lista),
        ]
    )


# --------------------------------------------------------------------------------------------------------------
# What the chair writes
# --------------------------------------------------------------------------------------------------------------


class PosturaRedactada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opcion_id: str
    a_favor: list[str] = Field(default_factory=list, max_length=MAX_ARGUMENTOS)
    en_contra: list[str] = Field(default_factory=list, max_length=MAX_ARGUMENTOS)


class RedaccionPresidente(BaseModel):
    """The prose of the ruling (the chair's, or code's when there is no chair)."""

    model_config = ConfigDict(extra="forbid")

    resumen: str = Field(min_length=20, max_length=4000)
    condicion: str | None = Field(default=None, max_length=600)
    posturas: list[PosturaRedactada] = Field(default_factory=list)
    disenso: str | None = Field(default=None, max_length=2000)
    supuestos_decisivos: list[str] = Field(default_factory=list, max_length=8)
    sensibilidad: list[str] = Field(default_factory=list, max_length=8)
    datos_que_resolverian: list[str] = Field(default_factory=list, max_length=6)

    def textos(self) -> list[str]:
        """Every text of the ruling the figures gate reads."""
        partes = [self.resumen, self.condicion or "", self.disenso or ""]
        for postura in self.posturas:
            partes += [*postura.a_favor, *postura.en_contra]
        partes += [*self.supuestos_decisivos, *self.sensibilidad, *self.datos_que_resolverian]
        return [p for p in partes if p]

    def retirando(self, violaciones: Sequence[Violacion]) -> RedaccionPresidente:
        """A copy with every offending figure withdrawn from every text."""

        def lista(textos: Sequence[str]) -> list[str]:
            return [t for t in (retirar_cifras(x, violaciones) for x in textos) if t]

        return RedaccionPresidente(
            resumen=retirar_cifras(self.resumen, violaciones) or self.resumen,
            condicion=retirar_cifras(self.condicion, violaciones),
            posturas=[
                PosturaRedactada(opcion_id=p.opcion_id, a_favor=lista(p.a_favor), en_contra=lista(p.en_contra))
                for p in self.posturas
            ],
            disenso=retirar_cifras(self.disenso, violaciones),
            supuestos_decisivos=lista(self.supuestos_decisivos),
            sensibilidad=lista(self.sensibilidad),
            datos_que_resolverian=lista(self.datos_que_resolverian),
        )


def _texto(datos: Mapping[str, Any], clave: str, tope: int) -> str:
    valor = datos.get(clave)
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise SalidaInvalidaError(f"«{clave}» debe ser un texto.")
    return recortar(" ".join(valor.split()), tope)


def _textos(datos: Mapping[str, Any], clave: str, maximo: int, tope: int = TOPE_ARGUMENTO) -> list[str]:
    valor = datos.get(clave)
    if valor is None:
        return []
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise SalidaInvalidaError(f"«{clave}» debe ser una lista de textos.")
    salida: list[str] = []
    for v in valor:
        limpio = recortar(" ".join(v.split()), tope)
        if limpio and limpio not in salida:
            salida.append(limpio)
    return salida[:maximo]


def validar_redaccion(
    datos: Any,
    *,
    expediente: ExpedienteConsejo,
    calculado: ResultadoCalculado,
) -> tuple[RedaccionPresidente, list[str]]:
    """The chair's JSON as a :class:`RedaccionPresidente`, plus warnings for the fixed fields it tried to set.

    Raises :class:`SalidaInvalidaError` (Spanish, for the repair attempt) on a summary that is too short, an unknown
    option, a missing dissent when the council is not unanimous, or a missing condition for «depende».
    """
    if not isinstance(datos, dict):
        raise SalidaInvalidaError("la respuesta debe ser un objeto JSON.")
    avisos = [
        f"El presidente intentó fijar «{campo}»; se mantiene el valor calculado por el recuento."
        for campo in CAMPOS_FIJOS
        if campo in datos
    ]
    resumen = _texto(datos, "resumen", 4000)
    if len(resumen) < 20:
        raise SalidaInvalidaError("«resumen» es obligatorio (al menos una frase completa para la persona).")
    validas = {o.id for o in expediente.opciones} | {DEPENDE}
    posturas: dict[str, PosturaRedactada] = {}
    crudas = datos.get("posturas") or []
    if not isinstance(crudas, list):
        raise SalidaInvalidaError("«posturas» debe ser una lista.")
    for cruda in crudas:
        if not isinstance(cruda, dict):
            raise SalidaInvalidaError("cada postura debe ser un objeto con opcion_id, a_favor y en_contra.")
        opcion = str(cruda.get("opcion_id") or "").strip().lower()
        if opcion not in validas:
            raise SalidaInvalidaError(f"«{opcion}» no es una opción del expediente ({', '.join(sorted(validas))}).")
        previa = posturas.get(opcion) or PosturaRedactada(opcion_id=opcion)
        a_favor = [*previa.a_favor, *_textos(cruda, "a_favor", MAX_ARGUMENTOS)][:MAX_ARGUMENTOS]
        en_contra = [*previa.en_contra, *_textos(cruda, "en_contra", MAX_ARGUMENTOS)][:MAX_ARGUMENTOS]
        posturas[opcion] = PosturaRedactada(opcion_id=opcion, a_favor=a_favor, en_contra=en_contra)
    disenso = _texto(datos, "disenso", 2000) or None
    if calculado.consenso == "unanime":
        disenso = None
    elif not disenso or len(disenso) < 20:
        raise SalidaInvalidaError(
            "el consejo no es unánime: «disenso» es obligatorio y debe exponer el mejor argumento de la minoría."
        )
    condicion = _texto(datos, "condicion", 600) or None
    if calculado.recomendacion == DEPENDE:
        if not condicion or len(condicion) < 10:
            raise SalidaInvalidaError("la recomendación es «depende»: «condicion» es obligatoria.")
    else:
        condicion = None
    redaccion = RedaccionPresidente(
        resumen=resumen,
        condicion=condicion,
        posturas=list(posturas.values()),
        disenso=disenso,
        supuestos_decisivos=_textos(datos, "supuestos_decisivos", 8),
        sensibilidad=_textos(datos, "sensibilidad", 8),
        datos_que_resolverian=_textos(datos, "datos_que_resolverian", 6),
    )
    return redaccion, avisos


# --------------------------------------------------------------------------------------------------------------
# The ruling without a chair
# --------------------------------------------------------------------------------------------------------------


def _primera_frase(texto: str, tope: int = 300) -> str:
    partes = re.split(r"(?<=[.!?])\s+", texto.strip(), maxsplit=1)
    return recortar(partes[0] if partes else texto, tope)


def _unicos(textos: Sequence[str], maximo: int) -> list[str]:
    salida: list[str] = []
    for t in textos:
        if t and t not in salida:
            salida.append(t)
    return salida[:maximo]


def _miembros(n: int) -> str:
    return "1 miembro" if n == 1 else f"{n} miembros"


def redaccion_determinista(
    expediente: ExpedienteConsejo,
    calculado: ResultadoCalculado,
    *,
    motivo: str,
    limpiar: Any | None = None,
) -> RedaccionPresidente:
    """The ruling's prose written by code from the members' own words (no chair: it failed, or was not reached).

    ``motivo``: one Spanish sentence saying why there is no chair's synthesis. ``limpiar(texto, tope)``: the
    anonymiser's cleaner (members' words reach the user without self-references).
    """

    def limpio(texto: str | None, tope: int) -> str:
        if not texto:
            return ""
        return str(limpiar(texto, tope)) if limpiar is not None else recortar(" ".join(texto.split()), tope)

    posiciones = calculado.posiciones
    votos = calculado.votacion.votos
    n = len(posiciones)
    rec = calculado.recomendacion
    if calculado.consenso == "unanime":
        frase = f"Los {n} miembros del consejo recomiendan {describir_opcion(expediente, rec)}."
    elif calculado.consenso == "mayoria":
        k = votos.get(rec, 0)
        frase = (
            f"La mayoría del consejo ({k} de {n} miembros) recomienda {describir_opcion(expediente, rec)}; "
            f"{_miembros(n - k)} {'discrepa' if n - k == 1 else 'discrepan'}."
        )
    elif calculado.consenso == "dividido":
        reparto = "; ".join(f"{describir_opcion(expediente, o)}: {_miembros(v)}" for o, v in votos.items())
        frase = (
            f"El consejo está dividido ({reparto}). No hay una recomendación clara: depende de los supuestos y los "
            "datos que se indican."
        )
    else:
        frase = "No hubo propuestas válidas suficientes para que el consejo llegara a una conclusión."
    resumen = f"{frase} {motivo}".strip()

    lider = calculado.votacion.lideres[0] if calculado.votacion.lideres else rec
    apoyos = [p for p in posiciones.values() if p.recomendacion == lider]
    orden = calculado.orden_agregado or sorted(posiciones)
    rango = {asiento: i for i, asiento in enumerate(orden)}
    minoria = sorted(
        (a for a, p in posiciones.items() if p.recomendacion != lider),
        key=lambda a: (rango.get(a, len(rango)), -posiciones[a].confianza, a),
    )

    posturas: list[PosturaRedactada] = []
    opciones = [o.id for o in expediente.opciones] + ([DEPENDE] if DEPENDE in votos else [])
    for opcion in opciones:
        quienes = [p for p in posiciones.values() if p.recomendacion == opcion]
        if not quienes:
            continue
        a_favor = _unicos([limpio(_primera_frase(p.razonamiento), 300) for p in quienes], 3)
        en_contra = _unicos([limpio(r, 300) for p in quienes for r in p.riesgos], 3)
        posturas.append(PosturaRedactada(opcion_id=opcion, a_favor=a_favor, en_contra=en_contra))

    disenso: str | None = None
    if calculado.consenso != "unanime" and minoria:
        mejor = posiciones[minoria[0]]
        disenso = recortar(
            f"El mejor argumento de la postura minoritaria ({describir_opcion(expediente, mejor.recomendacion)}): "
            f"{limpio(mejor.razonamiento, 1500)}",
            2000,
        )

    condicion: str | None = None
    if rec == DEPENDE:
        condiciones = [limpio(p.condicion, 600) for p in posiciones.values() if p.condicion]
        condicion = (
            condiciones[0]
            if condiciones
            else ("El consejo no llegó a una mayoría: la decisión depende de los supuestos y los datos señalados.")
        )
    return RedaccionPresidente(
        resumen=recortar(resumen, 4000),
        condicion=condicion,
        posturas=posturas,
        disenso=disenso,
        supuestos_decisivos=_unicos([limpio(s, 300) for p in apoyos for s in p.supuestos], 8),
        sensibilidad=_unicos([limpio(s, 300) for p in apoyos for s in p.que_me_haria_cambiar], 8),
        datos_que_resolverian=_unicos(
            [limpio(p.condicion, 300) for p in posiciones.values() if p.condicion]
            + [limpio(s, 300) for a in minoria for s in posiciones[a].que_me_haria_cambiar],
            6,
        ),
    )


# --------------------------------------------------------------------------------------------------------------
# The ruling
# --------------------------------------------------------------------------------------------------------------


def componer_dictamen(
    sesion_id: str,
    expediente: ExpedienteConsejo,
    calculado: ResultadoCalculado,
    redaccion: RedaccionPresidente,
    *,
    orden_asientos: Sequence[str],
    miembros: Sequence[Miembro],
    autoria: Mapping[str, str],
    puerta: ResultadoPuerta | None,
    avisos: Sequence[str],
) -> Dictamen:
    """The :class:`Dictamen`: code's fixed fields, the prose, each option's backers, authorship revealed."""
    votos = calculado.votos_por_asiento()
    rango = {a: i for i, a in enumerate(orden_asientos)}
    redactadas = {p.opcion_id: p for p in redaccion.posturas}
    opciones = [o.id for o in expediente.opciones] + ([DEPENDE] if DEPENDE in votos.values() else [])
    posturas = []
    for opcion in opciones:
        redactada = redactadas.get(opcion)
        posturas.append(
            PosturaOpcion(
                opcion_id=opcion,
                a_favor=list(redactada.a_favor) if redactada else [],
                en_contra=list(redactada.en_contra) if redactada else [],
                miembros=sorted((a for a, r in votos.items() if r == opcion), key=lambda a: (rango.get(a, 99), a)),
            )
        )
    return Dictamen(
        sesion_id=sesion_id,
        recomendacion=calculado.recomendacion,
        condicion=redaccion.condicion if calculado.recomendacion == DEPENDE else None,
        confianza=calculado.confianza,
        consenso=calculado.consenso,
        acuerdo=calculado.acuerdo,
        resumen=recortar(redaccion.resumen, 4000),
        posturas=posturas,
        disenso=recortar(redaccion.disenso, 2000) if redaccion.disenso and calculado.consenso != "unanime" else None,
        supuestos_decisivos=list(redaccion.supuestos_decisivos),
        sensibilidad=list(redaccion.sensibilidad),
        datos_que_resolverian=list(redaccion.datos_que_resolverian),
        puerta=puerta,
        miembros=list(miembros),
        autoria=dict(sorted(autoria.items())),
        avisos=list(dict.fromkeys(avisos)),
    )


__all__ = [
    "CAMPOS_FIJOS",
    "MARCA_RETIRADA",
    "UMBRAL_ACUERDO_ALTO",
    "UMBRAL_ACUERDO_MODERADO",
    "PosturaRedactada",
    "RedaccionPresidente",
    "ResultadoCalculado",
    "SalidaInvalidaError",
    "componer_dictamen",
    "describir_opcion",
    "describir_violaciones",
    "informes_del_expediente",
    "mensaje_correccion_presidente",
    "mensaje_presidente",
    "nivel_de_acuerdo",
    "redaccion_determinista",
    "retirar_cifras",
    "revisar_cifras_consejo",
    "textos_del_expediente",
    "validar_redaccion",
]
