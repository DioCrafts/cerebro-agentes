"""Phase 0 of a council session: the verified, pseudonymised dossier (``ExpedienteConsejo``).

The council never computes a figure and never decides what the law says: the brain's own specialists do, here,
before any member reads anything. Steps:

1. **The question, neutral, and its options** (:func:`plantear`): ONE strict-JSON call to the small model (purpose
   ``consejo:expediente``, prompt role ``consejo_expediente``) rewrites the question neutrally, lists 2–6 options
   and extracts the user's own leaning with its literal quote. A deterministic gate (:func:`validar_planteamiento`)
   accepts it only if the options are mutually exclusive and cover the question (every alternative the user wrote
   is among them), the wording is neutral (no leading phrases, not the user's quote) and the leaning rests on the
   user's literal words. Otherwise rules: the user's «X o Y» split (``deteccion.opciones_de_texto``), the options
   the brain suggested, or «do it / keep things as they are», with a template question. WHY: a leading question
   invites the members to agree with the user; options that overlap or miss one make the vote meaningless.
2. **Figures** (:func:`instruir`): the specialists the question needs (the brain's understanding rules, no model)
   run through the collaboration runtime (``Despachador``: in parallel, bounded, budget-aware) on encargos derived
   from the options («calcula para CADA opción…, no recomiendes»). Every ``Cifra`` keeps its provenance
   (specialist, tool, fragment); the figures gate later checks the ruling against exactly these. Their reports
   become facts (``origen="especialista"``), their declared doubts ``incertidumbres``. Case changes and actions a
   specialist proposes in this phase are discarded: the council never writes to the case.
3. **Facts**: the minimum profile (``seudonimos.seudonimizar_perfil``, per-domain allowlist), the decisions memory
   as ``restricciones`` (in force, of the domains involved, with their date), the conversation memory as facts
   with their origin (an item with special-category data is left out).
4. **The user's preference**, SEALED in ``preferencia_usuario`` (the explicit one the user gave when convening,
   else the model's quote-checked one, else the rules'): members see it only in the revision round. Sentences of
   the specialists' reports that echo it are left out of the facts.
5. **Privacy**: every text that reaches the dossier goes through ``seudonimos.seudonimizar_texto`` (identifiers
   masked, names replaced, origin and gendered self-descriptions removed, injection bait from mail/web/memory
   neutralised); what was removed is listed in ``anonimizado`` (never the values). The specialists' figures stay
   EXACT: they are the decision's substance and come from tools, not from the user's identity.

Nothing here raises for a degraded part (memory, decisions, a specialist): the dossier says what is missing. A spent
budget (``PresupuestoAgotadoError``) propagates: the session then fails honestly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import logging
import re
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from asistente.nucleo.agent.evidence import EvidenceLedger
from asistente.nucleo.cerebro.contrato import (
    CLAVE_FISCAL,
    MAX_ENCARGOS,
    BloqueMemoria,
    Cifra,
    ContextoTurno,
    Encargo,
    Especialista,
    ExpedienteCompartido,
    InformeEspecialista,
)
from asistente.nucleo.consejo.contrato import (
    EVENTO_FASE,
    MAX_OPCIONES,
    ExpedienteConsejo,
    HechoExpediente,
    Opcion,
)
from asistente.nucleo.consejo.deteccion import opciones_de_texto, plano
from asistente.nucleo.consejo.prompts_expediente import EXPEDIENTE_SCHEMA, EXPEDIENTE_SYSTEM_PROMPT
from asistente.nucleo.consejo.seudonimos import (
    NOTA_RECUERDO_ESPECIAL,
    seudonimizar_perfil,
    seudonimizar_texto,
)

if TYPE_CHECKING:  # pragma: no cover
    from asistente.nucleo.agent.providers.base import LLMProvider
    from asistente.nucleo.cerebro.contrato import ProveedorMemoria
    from asistente.nucleo.decision.schemas import CaseState
    from asistente.nucleo.memoria.modelo import Decision
    from asistente.nucleo.modulos.contrato import Modulo

logger = logging.getLogger(__name__)

PROPOSITO_EXPEDIENTE = "consejo:expediente"
ROL_PROMPT = "consejo_expediente"
MAX_ESPECIALISTAS = 3
MAX_TROZOS_INFORME = 3
MAX_HECHO = 600
MAX_INCERTIDUMBRE = 300
MAX_CIFRAS = 200
_PALABRAS_VACIAS = frozenset(
    "de la el los las del y o u en con para por que se su sus mi mis tu un una al lo como mas ya a este esta "
    "esto estos estas ese esa eso me nos le les si no".split()
)
# A leading wording (the members must not be told what to think).
_NO_NEUTRAL = re.compile(
    r"\b(?:no crees|verdad que|obviamente|claramente|sin duda|evidentemente|lo mejor es|lo sensato|deberias|"
    r"prefiere|se inclina|le gustaria|ya ha decidido|ya he decidido|como es logico|hay que)\b"
)
# The user's own leaning, in their words.
_PREFERENCIA = re.compile(
    r"(?P<cita>(?:prefiero|preferiria|me inclino (?:por|a)|creo que (?:voy a|lo mejor es|deberia|me conviene)|"
    r"mi idea es|tengo pensado|estoy tentad[oa] de|lo que quiero es|me gustaria|quiero)\s+[^.?!;\n]{3,200})",
    re.IGNORECASE,
)
# A sentence of a report that echoes the user's leaning (left out of the facts: the preference is sealed).
_ECO_PREFERENCIA = re.compile(
    r"\b(?:prefieres|prefiere|te inclinas|se inclina|tu idea|tu intencion|tu intención|como quieres|has decidido|"
    r"te gustaria|te gustaría|quieres)\b",
    re.IGNORECASE,
)
_INCERTIDUMBRE = re.compile(
    r"\b(?:incertidumbre|depende de|dependera|dependerá|si (?:sube|baja|suben|bajan|cambia|cambian)|no se sabe|"
    r"no es seguro|podr[ií]a (?:subir|bajar|cambiar)|previsi[oó]n|supuesto|suponiendo|si se mantiene|"
    r"no se puede saber|riesgo de|estimaci[oó]n)\b",
    re.IGNORECASE,
)
_ACCION = re.compile(
    r"^(?:(?:me|nos)\s+(?:conviene|compensa)|merece la pena|vale la pena|deber[ií]a(?:mos)?|tendr[ií]a(?:mos)?\s+que|"
    r"es (?:buena idea|sensato|prudente)|no s[eé] si|me planteo|estoy pensando en)\s+(?P<accion>.{3,300})$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------------------------------------------
# The question and its options
# --------------------------------------------------------------------------------------------------------------

OrigenPlanteamiento = Literal["modelo", "reglas", "mixto"]


@dataclass
class Planteamiento:
    """The neutral question, its options and the user's leaning (still unsealed: the caller seals it)."""

    pregunta_neutral: str
    opciones: list[str]
    origen: OrigenPlanteamiento
    preferencia: str | None = None
    cita_preferencia: str | None = None
    ambitos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _palabras(texto: str) -> set[str]:
    """Content words, reduced to a 5-letter stem (enough to match «hipoteca» with «hipotecario»)."""
    return {p[:5] for p in plano(texto).replace(",", " ").replace(".", " ").split() if p not in _PALABRAS_VACIAS}


def _cap(texto: str) -> str:
    texto = " ".join(texto.split()).strip(" ¿?¡!.,;:«»\"'")
    return texto[:1].upper() + texto[1:]


def opciones_excluyentes(opciones: Sequence[str]) -> str | None:
    """Why ``opciones`` are not a valid set of mutually exclusive options (None when they are)."""
    if not 2 <= len(opciones) <= MAX_OPCIONES:
        return f"hacen falta entre 2 y {MAX_OPCIONES} opciones"
    vistas: list[set[str]] = []
    for opcion in opciones:
        texto = " ".join(opcion.split())
        if not 3 <= len(texto) <= 400:
            return "una opción está vacía o es demasiado larga"
        palabras = _palabras(texto)
        for otra in vistas:
            if palabras == otra:
                return "dos opciones dicen lo mismo"
            if palabras and otra:
                union = palabras | otra
                if len(palabras & otra) / len(union) >= 0.8:
                    return "dos opciones son casi iguales"
                menor, mayor = (palabras, otra) if len(palabras) <= len(otra) else (otra, palabras)
                if menor < mayor and len(mayor - menor) <= 1:
                    return "una opción está contenida en otra (no son excluyentes)"
        vistas.append(palabras)
    return None


def cubre(opciones: Sequence[str], alternativas: Sequence[str]) -> bool:
    """Every alternative the user wrote is among ``opciones`` (≥ half of its content words in one option)."""
    conjuntos = [_palabras(o) for o in opciones]
    for alternativa in alternativas:
        suyas = _palabras(alternativa)
        if not suyas:
            continue
        if not any(len(suyas & c) * 2 >= len(suyas) for c in conjuntos):
            return False
    return True


def es_neutral(texto: str, cita_preferencia: str | None = None) -> bool:
    plana = plano(texto)
    if _NO_NEUTRAL.search(plana):
        return False
    if cita_preferencia and len(plano(cita_preferencia)) >= 8 and plano(cita_preferencia) in plana:
        return False
    return True


def preferencia_por_reglas(mensajes_usuario: Sequence[str]) -> tuple[str, str] | None:
    """The user's leaning in their own words («prefiero no vender», «creo que voy a amortizar»): (texto, cita)."""
    for mensaje in reversed(list(mensajes_usuario)):
        m = _PREFERENCIA.search(mensaje or "")
        if m is not None:
            cita = " ".join(m.group("cita").split())
            return f"Dice: «{cita}».", cita
    return None


def _pregunta_plantilla(opciones: Sequence[str], contexto: str | None = None) -> str:
    lista = "; ".join(f"({chr(ord('a') + i)}) {o}" for i, o in enumerate(opciones))
    texto = f"Este hogar tiene que elegir entre estas opciones: {lista}. ¿Cuál le conviene más y por qué?"
    if contexto:
        texto = f"Planteamiento: {contexto}. {texto}"
    return texto[:1500]


def _accion_si_no(pregunta: str) -> str | None:
    """«¿Debería vender el piso?» → «vender el piso» (the yes/no options' action)."""
    clausula = re.findall(r"¿([^¿?]+)\??", pregunta)
    texto = (clausula[-1] if clausula else pregunta).strip(" ¿?.")
    m = _ACCION.match(texto)
    if m is not None:
        return m.group("accion").strip(" ,.")
    from asistente.nucleo.consejo.deteccion import _VERBOS_PRIMERA

    palabras = texto.split()
    for n in (2, 1):
        cabeza = plano(" ".join(palabras[:n]))
        if len(palabras) >= n and cabeza in _VERBOS_PRIMERA:
            return " ".join([_VERBOS_PRIMERA[cabeza], *palabras[n:]])
    return None


def plantear_por_reglas(
    pregunta: str, *, opciones_sugeridas: Sequence[str] = (), mensajes_usuario: Sequence[str] = ()
) -> Planteamiento:
    """The rules' framing: the user's alternatives, else the brain's suggested options, else «do it or not»."""
    avisos: list[str] = []
    alternativas = opciones_de_texto(pregunta)
    sugeridas = [_cap(o) for o in opciones_sugeridas if isinstance(o, str) and o.strip()][:MAX_OPCIONES]
    contexto: str | None = None
    if alternativas and opciones_excluyentes(alternativas) is None:
        opciones = alternativas
    elif sugeridas and opciones_excluyentes(sugeridas) is None:
        opciones = sugeridas
    else:
        accion = _accion_si_no(pregunta)
        if accion:
            opciones = [_cap(accion), f"No {accion.strip()} y mantener la situación actual"]
        else:
            opciones = ["Llevar adelante lo que se plantea", "No hacerlo y mantener la situación actual"]
            sin_preferencia = _PREFERENCIA.sub("", pregunta)
            contexto = seudonimizar_texto(sin_preferencia)[:600].strip(" .") or None
        avisos.append("Opciones deducidas de la pregunta (sí / no).")
    preferencia = preferencia_por_reglas(mensajes_usuario or [pregunta])
    return Planteamiento(
        pregunta_neutral=_pregunta_plantilla(opciones, contexto),
        opciones=[o[:400] for o in opciones],
        origen="reglas",
        preferencia=preferencia[0] if preferencia else None,
        cita_preferencia=preferencia[1] if preferencia else None,
        avisos=avisos,
    )


def validar_planteamiento(
    datos: Any,
    *,
    pregunta: str,
    mensajes_usuario: Sequence[str],
    opciones_sugeridas: Sequence[str] = (),
) -> tuple[Planteamiento | None, str]:
    """The model's framing if it passes the gate, else (None, why). Partial repairs are made (and said)."""
    from asistente.nucleo.agent.procedencia import cita_valida

    if not isinstance(datos, Mapping):
        return None, "la salida no es un objeto"
    crudas = datos.get("opciones")
    if not isinstance(crudas, list):
        return None, "faltan las opciones"
    opciones = [_cap(str(o.get("descripcion") or "")) if isinstance(o, Mapping) else _cap(str(o or "")) for o in crudas]
    opciones = [o for o in opciones if o]
    motivo = opciones_excluyentes(opciones)
    if motivo is not None:
        return None, motivo
    alternativas = opciones_de_texto(pregunta) or [o for o in opciones_sugeridas if isinstance(o, str)]
    if alternativas and not cubre(opciones, alternativas):
        return None, "las opciones no cubren lo que plantea el usuario"
    avisos: list[str] = []
    preferencia: str | None = None
    cita: str | None = None
    crudo = datos.get("preferencia")
    if isinstance(crudo, Mapping):
        texto_pref = " ".join(str(crudo.get("texto") or "").split())
        cita_pref = " ".join(str(crudo.get("cita") or "").split())
        if texto_pref and cita_pref and cita_valida(cita_pref, list(mensajes_usuario)):
            preferencia, cita = texto_pref[:600], cita_pref
        elif texto_pref:
            avisos.append("La preferencia propuesta no está en las palabras del usuario: se descarta.")
    neutral = " ".join(str(datos.get("pregunta_neutral") or "").split())
    origen: OrigenPlanteamiento = "modelo"
    if len(neutral) < 10 or not es_neutral(neutral, cita):
        neutral = _pregunta_plantilla(opciones)
        origen = "mixto"
        avisos.append("La pregunta propuesta no era neutral: se usa una plantilla con sus opciones.")
    ambitos = [str(a).strip().lower() for a in datos.get("ambitos") or [] if isinstance(a, str) and a.strip()]
    return (
        Planteamiento(
            pregunta_neutral=neutral[:1500],
            opciones=[o[:400] for o in opciones],
            origen=origen,
            preferencia=preferencia,
            cita_preferencia=cita,
            ambitos=ambitos[:6],
            avisos=avisos,
        ),
        "",
    )


def _prompt_expediente() -> str:
    from asistente.nucleo.cerebro.prompts import prompt

    return prompt(ROL_PROMPT, EXPEDIENTE_SYSTEM_PROMPT)


def _mensaje_planteamiento(pregunta: str, historial: Sequence[str], sugeridas: Sequence[str]) -> str:
    partes = []
    if historial:
        anteriores = "\n".join(f"- {seudonimizar_texto(m)[:500]}" for m in historial[-4:])
        partes.append(f"<mensajes_anteriores_del_usuario>\n{anteriores}\n</mensajes_anteriores_del_usuario>")
    detectadas = opciones_de_texto(pregunta) or list(sugeridas)
    if detectadas:
        partes.append("Opciones ya detectadas: " + "; ".join(detectadas))
    partes.append(f"<mensaje_del_usuario>\n{seudonimizar_texto(pregunta)}\n</mensaje_del_usuario>")
    return "\n\n".join(partes)


def plantear(
    pregunta: str,
    *,
    proveedor: LLMProvider | None,
    mensajes_usuario: Sequence[str] = (),
    opciones_sugeridas: Sequence[str] = (),
) -> Planteamiento:
    """The neutral question and its options: the small model behind the gate, else the rules. Never raises except
    for a spent budget."""
    reglas = plantear_por_reglas(pregunta, opciones_sugeridas=opciones_sugeridas, mensajes_usuario=mensajes_usuario)
    if proveedor is None:
        return reglas
    from asistente.nucleo.agent.providers.base import ProviderError
    from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

    anteriores = [m for m in mensajes_usuario if m != pregunta]
    try:
        datos = proveedor.complete_json(
            system=_prompt_expediente(),
            user=_mensaje_planteamiento(pregunta, anteriores, opciones_sugeridas),
            schema=EXPEDIENTE_SCHEMA,
            schema_name="planteamiento",
        )
    except PresupuestoAgotadoError:
        raise
    except ProviderError as exc:
        logger.warning("dossier model failed (%s): rules framing", exc)
        reglas.avisos.append("El modelo no respondió: planteamiento por reglas.")
        return reglas
    except Exception:  # a broken provider never breaks the dossier
        logger.exception("dossier model raised: rules framing")
        reglas.avisos.append("El modelo falló: planteamiento por reglas.")
        return reglas
    planteamiento, motivo = validar_planteamiento(
        datos, pregunta=pregunta, mensajes_usuario=mensajes_usuario or [pregunta], opciones_sugeridas=opciones_sugeridas
    )
    if planteamiento is None:
        logger.info("dossier framing rejected (%s): rules framing", motivo)
        reglas.avisos.append(f"Planteamiento del modelo descartado ({motivo}): planteamiento por reglas.")
        return reglas
    if planteamiento.preferencia is None and reglas.preferencia is not None:
        planteamiento.preferencia, planteamiento.cita_preferencia = reglas.preferencia, reglas.cita_preferencia
    return planteamiento


# --------------------------------------------------------------------------------------------------------------
# The specialists' figures
# --------------------------------------------------------------------------------------------------------------


def elegir_especialistas(
    texto: str,
    especialistas: Mapping[str, Especialista],
    *,
    modulos: Sequence[Modulo] = (),
    case: CaseState | None = None,
    ambitos: Sequence[str] = (),
) -> list[str]:
    """The specialists the decision needs: the brain's understanding rules over the question and its options (no
    model), plus the domains the framing named; the tax specialist when nothing else matches (most household
    decisions have a tax side). At most :data:`MAX_ESPECIALISTAS`."""
    from asistente.nucleo.cerebro.intencion import FichaEspecialista, senales

    por_modulo = {getattr(m, "clave", ""): m for m in modulos}
    fichas = [FichaEspecialista.de(e, por_modulo.get(c)) for c, e in especialistas.items()]
    elegidos: list[str] = []
    try:
        elegidos = senales(texto, fichas, case=case).candidatos()
    except Exception:  # the vocabulary is optional here: fall back to the named domains
        logger.exception("understanding rules unavailable for the dossier")
    for ambito in ambitos:
        if ambito in especialistas and ambito not in elegidos:
            elegidos.append(ambito)
    if not elegidos and CLAVE_FISCAL in especialistas:
        elegidos = [CLAVE_FISCAL]
    return [c for c in elegidos if c in especialistas][:MAX_ESPECIALISTAS]


def encargo_para(clave: str, i: int, pregunta_neutral: str, opciones: Sequence[Opcion]) -> Encargo:
    lista = "\n".join(f"- ({o.id}) {o.descripcion}" for o in opciones)
    texto = (
        f"Un hogar tiene que tomar esta decisión: {pregunta_neutral}\nOpciones:\n{lista}\n\n"
        "Calcula con tus herramientas, para CADA opción y con los datos de este hogar, lo que corresponda a tu ámbito "
        "(importes, costes, ahorros, impuestos, plazos). Di qué supuestos usas y qué incertidumbres hay (lo que podría "
        "cambiar el resultado). No recomiendes ninguna opción: tu informe es un dato para quien decide."
    )
    return Encargo(id=f"e{i}", especialista=clave, pregunta=texto[:2000], motivo="Expediente del consejo")


def _frases(texto: str) -> list[str]:
    return [f.strip() for f in re.split(r"(?<=[.!?])\s+|\n+", texto or "") if f.strip()]


def _trozos(texto: str, maximo: int = MAX_HECHO, cuantos: int = MAX_TROZOS_INFORME) -> list[str]:
    trozos: list[str] = []
    actual = ""
    for frase in _frases(texto):
        frase = frase[:maximo]
        if actual and len(actual) + 1 + len(frase) > maximo:
            trozos.append(actual)
            actual = ""
            if len(trozos) >= cuantos:
                return trozos
        actual = f"{actual} {frase}".strip()
    if actual and len(trozos) < cuantos:
        trozos.append(actual)
    return trozos


@dataclass
class ResultadoInstruccion:
    """What phase 0 produced: the dossier, plus what the gates need later (kept in memory, never stored)."""

    expediente: ExpedienteConsejo
    informes: list[InformeEspecialista]
    ledger: EvidenceLedger
    planteamiento: Planteamiento


def _decisiones(case: CaseState | None) -> list[Decision]:
    if case is None:
        return []
    try:
        from asistente.nucleo.memoria.servicio import decisiones_del_caso
        from asistente.plataforma import settings

        return decisiones_del_caso(str(settings.DECISION_STATE_DB_PATH), case.case_id)
    except Exception:  # the decisions memory is optional: the dossier says nothing about it then
        logger.exception("decisions memory unavailable for the dossier")
        return []


def restricciones_de(
    decisiones: Sequence[Decision],
    *,
    dominios: Sequence[str],
    hoy: date,
    hechos: Mapping[str, Any] | None,
    notas: list[str],
) -> list[str]:
    """The decisions in force of the domains involved, as «what the user decided, when» (pseudonymised)."""
    from asistente.nucleo.memoria.modelo import descripcion_corta, evaluar

    salida: list[str] = []
    for decision in decisiones:
        try:
            if dominios and decision.modulo not in dominios:
                continue
            evaluacion = evaluar(decision, hoy=hoy, hechos=dict(hechos) if hechos is not None else None)
        except Exception:
            logger.exception("decision %s unreadable for the dossier", getattr(decision, "id", "?"))
            continue
        if evaluacion.estado not in ("vigente", "reabrible"):
            continue
        linea = descripcion_corta(decision)
        if evaluacion.estado == "reabrible" and evaluacion.motivo:
            linea += f" (se puede reconsiderar: {evaluacion.motivo})"
        salida.append(seudonimizar_texto(linea, notas)[:600])
        if len(salida) >= 30:
            break
    return salida


# A remembered fact that states a leaning («prefiere…», «no quiere…», «le gusta…»): Hindsight stores preferences as
# ordinary facts, so the dossier recognises them by their wording.
_ES_INCLINACION = re.compile(
    r"\b(prefier\w*|no\s+quier\w*|quier\w*|le\s+gust\w*|no\s+le\s+gust\w*|evit\w*|descart\w*|odi\w*)\b",
    re.IGNORECASE,
)


def _recuerdos(
    memoria: ProveedorMemoria | None, *, case: CaseState | None, user_id: str | None, consulta: str
) -> BloqueMemoria:
    if memoria is None:
        return BloqueMemoria()
    try:
        bloque = memoria.recuperar(
            case_id=case.case_id if case is not None else None,
            user_id=user_id or (case.user_id if case is not None else None),
            consulta=consulta,
            limite=8,
        )
    except Exception:  # the conversation memory is optional
        logger.exception("conversation memory unavailable for the dossier")
        return BloqueMemoria()
    return bloque if isinstance(bloque, BloqueMemoria) else BloqueMemoria()


def instruir(
    pregunta: str,
    *,
    case: CaseState | None,
    modulos: Sequence[Modulo] | None,
    proveedor_para: Callable[[str], LLMProvider],
    preferencia_usuario: str | None,
    hoy: date,
    especialistas: Mapping[str, Especialista] | Callable[[str], Mapping[str, Especialista]] | None = None,
    memoria: ProveedorMemoria | None = None,
    user_id: str | None = None,
    historial: Sequence[Mapping[str, Any]] = (),
    opciones: Sequence[str] = (),
    emit: Callable[..., None] | None = None,
    expediente_id: str | None = None,
    decisiones: Sequence[Decision] | None = None,
    max_paralelo: int | None = None,
) -> ResultadoInstruccion:
    """Phase 0 (see the module docstring). ``especialistas``: the specialists by key, or a factory called with the
    NEUTRAL question (the tax advisor takes it as its query, so it never reads the user's leaning); default: the
    household specialists of ``modulos`` (no tax advisor). ``decisiones``: the decisions memory (default: read from
    the cases' database)."""
    from asistente.nucleo.agent.procedencia import mensajes_del_usuario
    from asistente.nucleo.privacidad import categoria_especial

    def emitir(tipo: str, **datos: Any) -> None:
        if emit is None:
            return
        try:
            emit(tipo, **datos)
        except Exception:  # a progress callback never breaks the dossier
            logger.exception("dossier progress callback failed")

    emitir(EVENTO_FASE, fase="instruccion", estado="inicio")
    historial_dicts = [dict(m) for m in historial if isinstance(m, Mapping)]
    mensajes = mensajes_del_usuario(pregunta, historial_dicts)
    notas: list[str] = []

    # 1. The question, neutral, and its options.
    try:
        proveedor = proveedor_para(PROPOSITO_EXPEDIENTE)
    except Exception:
        logger.exception("no provider for the dossier: rules framing")
        proveedor = None
    planteamiento = plantear(pregunta, proveedor=proveedor, mensajes_usuario=mensajes, opciones_sugeridas=opciones)
    pregunta_neutral = seudonimizar_texto(planteamiento.pregunta_neutral, notas)
    if len(pregunta_neutral) < 10:
        pregunta_neutral = _pregunta_plantilla(planteamiento.opciones)
    lista_opciones = [
        Opcion(id=f"o{i}", descripcion=seudonimizar_texto(o, notas)[:400] or o[:400])
        for i, o in enumerate(planteamiento.opciones[:MAX_OPCIONES], start=1)
    ]

    # 2. The specialists' figures.
    if callable(especialistas) and not isinstance(especialistas, Mapping):
        try:
            disponibles = dict(especialistas(pregunta_neutral))
        except Exception:
            logger.exception("specialists unavailable for the dossier")
            disponibles = {}
    elif especialistas is not None:
        disponibles = dict(especialistas)
    else:
        from asistente.nucleo.cerebro.construir import especialistas_del_hogar

        try:
            disponibles = dict(especialistas_del_hogar(list(modulos or [])))
        except Exception:
            logger.exception("household specialists unavailable for the dossier")
            disponibles = {}
    texto_eleccion = " ".join([pregunta_neutral, *(o.descripcion for o in lista_opciones)])
    claves = elegir_especialistas(
        texto_eleccion, disponibles, modulos=list(modulos or []), case=case, ambitos=planteamiento.ambitos
    )
    ledger = EvidenceLedger()
    informes: list[InformeEspecialista] = []
    incertidumbres: list[str] = []
    if claves:
        encargos = [encargo_para(c, i, pregunta_neutral, lista_opciones) for i, c in enumerate(claves, start=1)]
        contexto = ContextoTurno(
            expediente=ExpedienteCompartido(case.model_copy(deep=True) if case is not None else None),
            ledger=ledger,
            hoy=hoy,
            emit=emit or (lambda *a, **k: None),
            proveedor_para=proveedor_para,
            mensajes_usuario=(),
            memoria=BloqueMemoria(),
        )
        from asistente.nucleo.cerebro.colaboracion import Despachador

        despachador = Despachador(
            {c: disponibles[c] for c in claves}, max_paralelo=max_paralelo or len(claves), emit=contexto.emit
        )
        informes = list(despachador.ejecutar(encargos[:MAX_ENCARGOS], contexto))
        agotado = getattr(despachador, "presupuesto_agotado", None)
        if not any(i.estado == "ok" for i in informes):
            from asistente.plataforma.ops.consumo import PresupuestoAgotadoError

            if isinstance(agotado, PresupuestoAgotadoError):
                raise agotado
    hechos: list[HechoExpediente] = []
    cifras: list[Cifra] = []
    fragmentos: list[str] = []
    for informe in informes:
        nombre = seudonimizar_texto(informe.nombre or informe.especialista)[:120]
        if informe.estado != "ok":
            incertidumbres.append(f"{nombre}: no pudo responder; falta esa parte del análisis.")
            continue
        frases = [f for f in _frases(informe.texto) if not _ECO_PREFERENCIA.search(f)]
        for n, trozo in enumerate(_trozos(" ".join(frases)), start=1):
            hechos.append(
                HechoExpediente(
                    clave=f"informe_{informe.especialista}_{n}"[:80],
                    texto=seudonimizar_texto(trozo, notas)[:MAX_HECHO] or "(sin texto)",
                    origen="especialista",
                    referencia=nombre,
                )
            )
        for frase in frases:
            if _INCERTIDUMBRE.search(frase):
                incertidumbres.append(f"{nombre}: {seudonimizar_texto(frase, notas)[: MAX_INCERTIDUMBRE - 40]}")
        for aviso in informe.avisos:
            if aviso.strip():
                incertidumbres.append(f"{nombre}: {seudonimizar_texto(aviso, notas)[: MAX_INCERTIDUMBRE - 40]}")
        for cifra in informe.cifras:
            cifras.append(
                cifra.model_copy(
                    update={"concepto": (seudonimizar_texto(cifra.concepto, notas) or cifra.concepto)[:200]}
                )
            )
        fragmentos += [f for f in informe.fragment_ids if f not in fragmentos]

    # 3. The household's facts: minimum profile, decisions in force, conversation memory.
    from asistente.nucleo.modulos.perfil import perfil_de

    try:
        perfil = perfil_de(case)
    except Exception:
        logger.exception("profile unreadable for the dossier")
        perfil = {}
    dominios = list(dict.fromkeys([*claves, *planteamiento.ambitos]))
    perfil_minimo, anonimizado = seudonimizar_perfil(perfil, dominios=dominios, hoy=hoy)
    lista_decisiones = list(decisiones) if decisiones is not None else _decisiones(case)
    restricciones = restricciones_de(lista_decisiones, dominios=dominios, hoy=hoy, hechos=perfil, notas=notas)
    preferencia = preferencia_usuario.strip() if preferencia_usuario and preferencia_usuario.strip() else None
    preferencia = preferencia or planteamiento.preferencia
    bloque = _recuerdos(memoria, case=case, user_id=user_id, consulta=pregunta)
    palabras_opciones = set().union(*(_palabras(o.descripcion) for o in lista_opciones)) if lista_opciones else set()
    anadidos_pref: list[str] = []
    for n, recuerdo in enumerate(bloque.recuerdos, start=1):
        if categoria_especial(recuerdo.texto) or categoria_especial(recuerdo.origen.cita or ""):
            if NOTA_RECUERDO_ESPECIAL not in notas:
                notas.append(NOTA_RECUERDO_ESPECIAL)
            continue
        texto = seudonimizar_texto(recuerdo.texto, notas)[:MAX_HECHO]
        if not texto:
            continue
        cuando = recuerdo.origen.fecha.strftime("%d/%m/%Y") if recuerdo.origen.fecha else "fecha desconocida"
        if _ES_INCLINACION.search(recuerdo.texto) and _palabras(recuerdo.texto) & palabras_opciones:
            # A remembered leaning about THIS decision is part of the user's preference: sealed with it.
            anadidos_pref.append(f"En otra conversación ({cuando}): {texto}")
            continue
        hechos.append(
            HechoExpediente(
                clave=f"memoria_{n}",
                texto=texto,
                origen="memoria",
                referencia=f"{recuerdo.id} · {cuando}"[:120],
            )
        )
    if anadidos_pref:
        preferencia = " ".join([p for p in [preferencia, *anadidos_pref] if p])
    preferencia_sellada = seudonimizar_texto(preferencia, notas)[:600] if preferencia else None

    for nota in notas:
        if nota not in anonimizado:
            anonimizado.append(nota)
    expediente = ExpedienteConsejo(
        id=expediente_id or f"exp_{uuid4().hex}",
        pregunta=pregunta_neutral[:1500],
        opciones=lista_opciones,
        hechos=hechos[:80],
        cifras=cifras[:MAX_CIFRAS],
        fragment_ids=fragmentos,
        perfil_minimo=perfil_minimo,
        restricciones=restricciones[:30],
        incertidumbres=list(dict.fromkeys(i[:MAX_INCERTIDUMBRE] for i in incertidumbres))[:30],
        preferencia_usuario=preferencia_sellada,
        anonimizado=anonimizado,
    )
    emitir(EVENTO_FASE, fase="instruccion", estado="fin")
    return ResultadoInstruccion(expediente=expediente, informes=informes, ledger=ledger, planteamiento=planteamiento)


def construir_expediente(
    pregunta: str,
    *,
    case: CaseState | None,
    modulos: Sequence[Modulo] | None,
    proveedor_para: Callable[[str], LLMProvider],
    preferencia_usuario: str | None,
    hoy: date,
    **opciones: Any,
) -> ExpedienteConsejo:
    """The dossier of ``pregunta`` (the shared interface; :func:`instruir` also returns the reports and the ledger
    the gates need). Extra keyword arguments are :func:`instruir`'s."""
    return instruir(
        pregunta,
        case=case,
        modulos=modulos,
        proveedor_para=proveedor_para,
        preferencia_usuario=preferencia_usuario,
        hoy=hoy,
        **opciones,
    ).expediente


__all__ = [
    "PROPOSITO_EXPEDIENTE",
    "Planteamiento",
    "ResultadoInstruccion",
    "construir_expediente",
    "cubre",
    "elegir_especialistas",
    "encargo_para",
    "es_neutral",
    "instruir",
    "opciones_excluyentes",
    "plantear",
    "plantear_por_reglas",
    "preferencia_por_reglas",
    "restricciones_de",
    "validar_planteamiento",
]
