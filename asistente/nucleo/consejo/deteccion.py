"""Is this a HARD decision? Deterministic, explainable rules (no model call).

The council is expensive (several models, several rounds), so it is offered only for decisions where independent
perspectives and a cross-critique are worth it. A decision is hard when it is a real CHOICE (alternatives «¿X o
Y?», «me conviene», «debería», a first-person «¿acepto la herencia?») AND something is at stake:

* it is hard to undo — accepting or renouncing an inheritance, selling or buying a home, cancelling a loan,
  appealing a tax assessment, signing a mortgage, retiring early, rescuing a pension plan;
* a large amount — written in the question or read from the case (the mortgage's capital when the question is
  about the mortgage), from ``CONSEJO_IMPORTE_GRANDE_EUR``;
* it crosses several domains (the brain's plan consults several specialists);
* the user declares doubt («no lo tengo claro», «me da miedo»);
* a weighty topic (home, mortgage, inheritance, savings, retirement, pension plan, investing).

Factual questions are NEVER hard decisions: «¿cuánto me ahorro si amortizo 10.000 €?», «¿qué pasa si renuncio a
la herencia?», «¿cuándo se presenta el 303?» are answered by the specialists (computing is their job, not a
council's). Only the user's explicit request («consúltalo con el consejo») overrides the rules — and even then
the user confirms the cost before anything runs.

Every signal adds a weight and a Spanish reason (``Deteccion.motivos``), so the verdict can be explained term by
term; ``tests/consejo/test_deteccion.py`` pins it on a table of Spanish phrasings.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.cerebro.contrato import Intencion
from asistente.plataforma import settings

UMBRAL_DIFICIL = 3.5
PESO_DELIBERACION = 2.0
PESO_ALTERNATIVAS_EXTRA = 0.5
PESO_IRREVERSIBLE = 2.0
PESO_TEMA = 1.0
PESO_COMPROMISO = 1.0
PESO_IMPORTE = 1.5
PESO_DOMINIOS = 1.0
PESO_DUDA = 1.0
PENALIZACION_IMPORTE_PEQUENO = 1.5
IMPORTE_PEQUENO = Decimal("1000")
MAX_OPCIONES_DETECTADAS = 6


class Deteccion(BaseModel):
    """The detector's verdict on one question (explainable: every reason is listed)."""

    model_config = ConfigDict(extra="forbid")

    dificil: bool
    motivos: list[str] = Field(default_factory=list)
    opciones_detectadas: list[str] = Field(default_factory=list)
    puntuacion: float = 0.0
    # The user asked for the council in so many words («consúltalo con el consejo»).
    pedido_por_usuario: bool = False
    # A factual / informational question (never a hard decision, unless the user asked for the council).
    factual: bool = False

    def como_pista(self) -> str:
        """One line for the brain's prompt: the verdict and why (data for the brain, not an order)."""
        if self.pedido_por_usuario:
            return "El usuario ha pedido expresamente que lo estudie el consejo."
        if not self.dificil:
            return "No parece una decisión difícil: no ofrezcas el consejo."
        return "Parece una decisión difícil (" + "; ".join(self.motivos[:4]) + ")."


# --------------------------------------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------------------------------------


def plano(texto: str) -> str:
    """Lower case, no accents (ñ kept), punctuation kept as spaces except «¿?,» (clauses matter here)."""
    minus = unicodedata.normalize("NFC", texto or "").lower().replace("ñ", "\x00")
    sin = "".join(c for c in unicodedata.normalize("NFKD", minus) if not unicodedata.combining(c)).replace("\x00", "ñ")
    sin = re.sub(r"[^a-z0-9ñ€%¿?,.\s]+", " ", sin)
    return " ".join(sin.split())


def _hay(patron: str, texto: str) -> re.Match[str] | None:
    return re.search(r"(?<![a-z0-9ñ])(?:" + patron + r")(?![a-z0-9ñ])", texto)


# --------------------------------------------------------------------------------------------------------------
# Vocabulary (normalised: no accents)
# --------------------------------------------------------------------------------------------------------------

# «Consúltalo con el consejo», «pregúntale al consejo», «que lo estudie el consejo». «Dame un consejo» is advice.
_PEDIDO = (
    r"(?:consult|pregunt|convoc|llev|pas|somet|plante|reun|traslad|eleva)\w*\s+(?:\w+\s+){0,3}?(?:al|el|del|con el)\s+"
    r"consejo|(?:al|el|del)\s+consejo\s+(?:de\s+(?:ias?|expertos|sabios|asesores)|que\s+(?:lo\s+)?(?:estudi|decid|"
    r"analic|vea|valor)\w*)|(?:opinion|dictamen|veredicto)\s+del\s+consejo|consejo\s+de\s+(?:ias?|sabios|expertos)|"
    r"que\s+(?:lo\s+)?(?:estudie|vea|analice|decida|valore)\s+el\s+consejo"
)
# How a factual question starts (after «¿», «oye», «y»…).
_INICIO_FACTUAL = (
    r"que es|que son|que significa|que quiere decir|cuando|cuanto|cuanta|cuantos|cuantas|cual es|cuales son|"
    r"como se|como funciona|como calculo|como hago|como presento|donde|quien|que plazo|que porcentaje|que tipo|"
    r"que dice|que pasa si|que pasaria si|que ocurre si|que ocurriria si|que impuestos|que necesito|que documentos|"
    r"hasta cuando|a partir de cuando|desde cuando|es obligatorio|tengo que|tengo obligacion|puedo|podria|se puede|"
    r"me puedo deducir|se puede deducir|cuanto me|cuanto pago|cuanto pagaria|cuanto tengo|como me afecta|"
    r"me afecta|explicame|dime|que requisitos|que pasos|en que consiste|que diferencia hay"
)
_PREFIJOS_CORTESIA = r"(?:oye|hola|buenas|por cierto|y|pero|entonces|vale|ok|una pregunta)[, ]+"
# Deliberation: the user is choosing.
_DELIBERACION = (
    r"me conviene|nos conviene|le conviene|conviene mas|me compensa|nos compensa|compensa mas|merece la pena|"
    r"vale la pena|deberia|deberiamos|tendria que|tendriamos que|que hago|que hacemos|que me recomiendas|"
    r"que nos recomiendas|que me aconsejas|que harias|que haria yo|que opcion|cual me conviene|cual nos conviene|"
    r"es mejor|seria mejor|que es mejor|que sera mejor|mejor opcion|dudo entre|dudamos entre|estoy dudando|"
    r"estamos dudando|no se si|no sabemos si|decidir|decidirme|decidirnos|que decision|me planteo|nos planteamos|"
    r"estoy pensando en|estoy pensando si|estamos pensando en|me lo estoy pensando|es buena idea|seria buena idea|"
    r"es sensato|es prudente|salgo ganando|sale mas a cuenta|me sale a cuenta|renta mas|que me interesa mas|"
    r"me interesa mas|que elijo|elijo|escojo|me quedo con|lo hago o no|lo hago ya|hacerlo o no"
)
# A first-person decision question at the start: «¿Acepto la herencia?», «¿Vendo el piso o espero?».
_VERBOS_PRIMERA = {
    "acepto": "aceptar",
    "aceptamos": "aceptar",
    "renuncio": "renunciar",
    "renunciamos": "renunciar",
    "vendo": "vender",
    "vendemos": "vender",
    "compro": "comprar",
    "compramos": "comprar",
    "amortizo": "amortizar",
    "amortizamos": "amortizar",
    "invierto": "invertir",
    "invertimos": "invertir",
    "recurro": "recurrir",
    "recurrimos": "recurrir",
    "firmo": "firmar",
    "firmamos": "firmar",
    "cancelo": "cancelar",
    "cancelamos": "cancelar",
    "alquilo": "alquilar",
    "alquilamos": "alquilar",
    "rescato": "rescatar",
    "rescatamos": "rescatar",
    "dono": "donar",
    "donamos": "donar",
    "reclamo": "reclamar",
    "reclamamos": "reclamar",
    "pido": "pedir",
    "pedimos": "pedir",
    "solicito": "solicitar",
    "espero": "esperar",
    "esperamos": "esperar",
    "refinancio": "refinanciar",
    "subrogo": "subrogar",
    "hipoteco": "hipotecar",
    "reformo": "reformar",
    "ahorro": "ahorrar",
    "pago": "pagar",
    "pagamos": "pagar",
    "adelanto": "adelantar",
    "traspaso": "traspasar",
    "contrato": "contratar",
    "contratamos": "contratar",
    "sigo": "seguir",
    "seguimos": "seguir",
    "dejo": "dejar",
    "dejamos": "dejar",
    "meto": "meter",
    "metemos": "meter",
    "mantengo": "mantener",
    "mantenemos": "mantener",
    "cambio": "cambiar",
    "cambiamos": "cambiar",
    "me jubilo": "jubilarme",
    "me hago": "hacerme",
    "me cambio": "cambiarme",
    "me quedo": "quedarme",
    "me doy": "darme",
    "me caso": "casarme",
    "nos casamos": "casarnos",
}
_CLITICOS = frozenset({"lo", "la", "los", "las", "le", "les"})
# Hard to undo (normalised stems): label → pattern.
_IRREVERSIBLE: dict[str, str] = {
    "aceptar o renunciar a una herencia": r"herencia\w*|hereda\w*|heredo|heredar|legado|renunci\w*",
    "vender un bien": r"vend\w*\s+(?:\w+\s+){0,3}?(?:piso|casa|vivienda|chalet|local|terreno|finca|acciones|empresa|"
    r"negocio|coche|parcela|plaza de garaje|inmueble)|venta de (?:la |mi |el |nuestra )?(?:piso|casa|vivienda|"
    r"inmueble|local|empresa|acciones)",
    "comprar una vivienda": r"compr\w*\s+(?:\w+\s+){0,3}?(?:piso|casa|vivienda|chalet|inmueble|segunda vivienda)|"
    r"compra de (?:la |una |mi )?(?:piso|casa|vivienda)|comprar o alquilar|alquilar o comprar",
    "cancelar un préstamo o contrato": r"cancel\w*\s+(?:\w+\s+){0,3}?(?:hipoteca|prestamo|credito|seguro|"
    r"deposito|plan)",
    "recurrir": r"recurr\w*|recurso de reposicion|reclamacion economico|reclamacion economica",
    "firmar": r"firm\w*\s+(?:\w+\s+){0,3}?(?:hipoteca|contrato|escritura|arras|prestamo|aval)|ser avalista|avalar",
    "jubilarse": r"jubila\w*|prejubila\w*",
    "rescatar un plan de pensiones": r"rescat\w*",
    "donar": r"don\w*\s+(?:\w+\s+){0,3}?(?:piso|casa|vivienda|dinero|hijos?|hijas?)|donacion\w*",
    "dejar el trabajo": r"dej\w* (?:el|mi) (?:trabajo|empleo)|excedencia|capitalizar el paro|despido voluntario",
    "divorcio o separación": r"divorci\w*|separacion de bienes|liquidar (?:la )?gananciales",
}
# A weighty topic (not irreversible by itself).
_TEMAS: dict[str, str] = {
    "hipoteca": r"hipotec\w*",
    "vivienda": r"vivienda\w*|piso|casa",
    "herencia": r"herencia\w*",
    "jubilación": r"jubilacion\w*",
    "ahorros": r"ahorros|colchon|fondo de emergencia",
    "inversión": r"invert\w*|invierto|inversion\w*|fondo indexado|fondos? de inversion|acciones|bolsa|etf|"
    r"letras del tesoro|deposito",
    "plan de pensiones": r"plan(?:es)? de pensiones|pias|plan de empleo",
    "trabajo por cuenta propia": r"autonomo\w*|montar (?:un|una|mi) (?:negocio|empresa)|emprender",
    "préstamo": r"prestamo\w*|credito\w*|financiar\w*|refinanci\w*",
}
# Commits savings for a long time (adds to the stakes, never decides alone).
_COMPROMISO = r"amortiz\w*|plan(?:es)? de pensiones|invert\w*|invierto|permanencia|a plazo fijo|bloquear el dinero"
# Declared doubt.
_DUDA = (
    r"no estoy segur[oa]|no estamos seguros|no lo tengo claro|no lo tenemos claro|tengo dudas|tenemos dudas|"
    r"me da miedo|nos da miedo|no se que hacer|no sabemos que hacer|incertidumbre|me preocupa|nos preocupa|"
    r"arriesgad[oa]|me da vertigo|si sube el euribor|si bajan los tipos|si suben los tipos|no se si|dudo|dudamos"
)
# Separators of alternatives that are not alternatives.
_NO_ALTERNATIVA = r"o sea|mas o menos|antes o despues de (?:la|el)|si o no|tarde o temprano|de una u otra forma"
_IMPORTE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,(\d{1,2}))?\s*(k|mil|millon(?:es)?|m€|€|euros?|eur)?(?![\w])",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------------------------------------------
# Pieces
# --------------------------------------------------------------------------------------------------------------


def pedido_por_usuario(pregunta: str) -> bool:
    """«Consúltalo con el consejo» (and its variants): the user asks for the council itself."""
    return _hay(_PEDIDO, plano(pregunta)) is not None


def _clausula(texto: str) -> str:
    """The question's decision clause: the last «¿…?» when there is one, else the whole text."""
    preguntas = re.findall(r"¿([^¿?]+)\??", texto)
    return preguntas[-1] if preguntas else texto


def _sin_cortesia(texto: str) -> str:
    previo = None
    while previo != texto:
        previo = texto
        texto = re.sub(r"^" + _PREFIJOS_CORTESIA, "", texto).strip()
    return texto


def es_factual(pregunta: str) -> bool:
    """An informational question (what, when, how much…) with no deliberation in it."""
    texto = plano(pregunta)
    if _hay(_DELIBERACION, texto):
        return False
    inicio = _sin_cortesia(_clausula(texto).strip(" ¿?,."))
    return re.match(r"(?:" + _INICIO_FACTUAL + r")(?![a-z0-9ñ])", inicio) is not None


def _verbo_inicial(clausula: str) -> tuple[str, str] | None:
    """A first-person decision verb at the start of the clause: (as written, infinitive)."""
    inicio = _sin_cortesia(clausula.strip(" ¿?,."))
    for verbo in sorted(_VERBOS_PRIMERA, key=len, reverse=True):
        if re.match(re.escape(verbo) + r"(?![a-z0-9ñ])", inicio):
            return verbo, _VERBOS_PRIMERA[verbo]
    return None


# Leading deliberation phrases removed before splitting alternatives (in the ORIGINAL text, accents optional).
_LIDER = re.compile(
    r"^\s*(?:(?:oye|hola|y|pero|entonces|vale)[, ]+)*(?:"
    r"(?:a|para)\s+(?:m[ií]|nosotros)[, ]+|"
    r"(?:qu[eé]|cu[aá]l)\s+(?:me|nos|le)?\s*(?:conviene|compensa|interesa|recomiendas|aconsejas)(?:\s+m[aá]s)?|"
    r"(?:me|nos|le)\s+(?:conviene|compensa|interesa)(?:\s+m[aá]s)?|"
    r"(?:qu[eé]|cu[aá]l)\s+(?:es|ser[aá]|ser[ií]a)\s+(?:mejor|m[aá]s\s+sensato|m[aá]s\s+prudente)(?:\s+para\s+(?:m[ií]|"
    r"nosotros))?|(?:es|ser[ií]a)\s+mejor|"
    r"(?:merece|vale)\s+la\s+pena|deber[ií]a(?:mos)?|tendr[ií]a(?:mos)?\s+que|"
    r"(?:qu[eé]\s+)?opci[oó]n\s+(?:es\s+)?mejor|dud[oa](?:mos)?\s+entre|estoy\s+dudando\s+entre|"
    r"no\s+s[eé]\s+si|me\s+planteo|estoy\s+pensando\s+(?:en|si)|qu[eé]\s+hago"
    r")[\s,:]*",
    re.IGNORECASE,
)


def _limpiar_opcion(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip(" ¿?¡!.,;:«»\"'")
    texto = re.sub(r"^(?:mejor|m[aá]s bien|bien)\s+", "", texto, flags=re.IGNORECASE)
    palabras = texto.split(" ")
    if not palabras or not palabras[0]:
        return ""
    for n in (2, 1):
        cabeza = plano(" ".join(palabras[:n]))
        if cabeza in _VERBOS_PRIMERA and len(palabras) >= n:
            palabras = [_VERBOS_PRIMERA[cabeza], *palabras[n:]]
            break
    else:
        # «lo alquilo», «los meto en…»: the clitic goes after the infinitive («alquilarlo», «meterlos en…»).
        if len(palabras) >= 2 and plano(palabras[0]) in _CLITICOS and plano(palabras[1]) in _VERBOS_PRIMERA:
            palabras = [_VERBOS_PRIMERA[plano(palabras[1])] + plano(palabras[0]), *palabras[2:]]
    limpio = " ".join(palabras)
    return limpio[:1].upper() + limpio[1:]


def opciones_de_texto(pregunta: str) -> list[str]:
    """The alternatives the user wrote («¿X o Y?», «dudo entre X y Y», «X, Y o Z»), cleaned; [] when none.

    Conservative: a split is kept only with 2–6 parts of at least 3 characters each, never on «o sea», «más o
    menos», numbers («2 o 3»).
    """
    clausula = _clausula(pregunta or "").strip()
    if not clausula:
        return []
    sin_lider = _LIDER.sub("", clausula, count=1).strip()
    entre = re.search(r"\bentre\s+(.+?)\s+y\s+(.+)$", clausula, flags=re.IGNORECASE)
    if entre and not re.search(r"\s(?:o|u)\s", entre.group(0)):
        partes = [entre.group(1), entre.group(2)]
    else:
        if _hay(_NO_ALTERNATIVA, plano(sin_lider)):
            return []
        if re.search(r"\d\s+(?:o|u)\s+\d", sin_lider):
            return []
        trozos = re.split(r"\s*,?\s+(?:o|u)\s+(?:bien\s+)?", sin_lider)
        if len(trozos) < 2:
            return []
        # «X, Y o Z»: the commas before the last «o» separate alternatives too.
        primeras = [t for t in re.split(r"\s*,\s*", trozos[0]) if t.strip()]
        partes = ([*primeras] if len(primeras) > 1 and len(trozos) == 2 else [trozos[0]]) + trozos[1:]
        # A trailing context after the last option («…o invertir, teniendo en cuenta X») stays with it.
    opciones = [_limpiar_opcion(p) for p in partes]
    opciones = [o for o in opciones if len(o) >= 3]
    vistas: list[str] = []
    for opcion in opciones:
        if plano(opcion) not in {plano(v) for v in vistas}:
            vistas.append(opcion[:400])
    if not 2 <= len(vistas) <= MAX_OPCIONES_DETECTADAS:
        return []
    return vistas


def _decimal(entero: str, decimales: str | None, escala: str | None) -> Decimal | None:
    try:
        valor = Decimal(re.sub(r"[.\s]", "", entero) + ("." + decimales if decimales else ""))
    except InvalidOperation:
        return None
    escala = (escala or "").lower()
    if escala in ("k", "mil"):
        valor *= 1000
    elif escala.startswith("millon") or escala == "m€":
        valor *= 1_000_000
    return valor


def importes_de_texto(texto: str) -> list[Decimal]:
    """Amounts in euros written in ``texto`` («60.000 €», «20 mil», «15k», «150.000»). Years, percentages and
    small counts without a currency are not amounts."""
    importes: list[Decimal] = []
    for m in _IMPORTE.finditer(texto or ""):
        entero, decimales, escala = m.group(1), m.group(2), m.group(3)
        siguiente = (texto or "")[m.end() : m.end() + 2]
        if "%" in siguiente:
            continue
        valor = _decimal(entero, decimales, escala)
        if valor is None:
            continue
        agrupado = bool(re.search(r"[.\s]\d{3}", entero))
        if not escala and not agrupado:
            # A bare number: a year (1990–2100) or a small count is not an amount.
            if valor < 1000 or (1990 <= valor <= 2100 and "." not in entero):
                continue
        importes.append(valor)
    return importes


def _umbral_grande() -> Decimal:
    try:
        return Decimal(str(getattr(settings, "CONSEJO_IMPORTE_GRANDE_EUR", "10000")))
    except InvalidOperation:
        return Decimal("10000")


def _capital_hipoteca(case: Any) -> Decimal | None:
    """The mortgage's initial capital from the case (``hipoteca_prestamo``), when there is one."""
    for fact in getattr(case, "facts", None) or ():
        if getattr(fact, "name", "") != "hipoteca_prestamo":
            continue
        valor = fact.value
        if isinstance(valor, str):
            try:
                valor = json.loads(valor)
            except json.JSONDecodeError:
                return None
        if isinstance(valor, dict):
            try:
                return Decimal(str(valor.get("capital_inicial")))
            except (InvalidOperation, TypeError, ValueError):
                return None
    return None


def _eur(valor: Decimal) -> str:
    entero = int(valor.to_integral_value())
    return f"{entero:,}".replace(",", ".") + " €"


# --------------------------------------------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------------------------------------------


def es_decision_dificil(pregunta: str, intencion: Intencion | None = None, case: Any = None) -> Deteccion:
    """The detector's verdict (pure, no model). ``intencion``: the brain's plan of the turn (several specialists is a
    signal); ``case``: the user's case (the mortgage's capital is a large amount for a question about it)."""
    texto = plano(pregunta)
    opciones = opciones_de_texto(pregunta)
    if not texto:
        return Deteccion(dificil=False, motivos=["mensaje vacío"])
    if pedido_por_usuario(pregunta):
        return Deteccion(
            dificil=True,
            pedido_por_usuario=True,
            motivos=["lo has pedido expresamente"],
            opciones_detectadas=opciones,
            puntuacion=10.0,
        )
    if es_factual(pregunta):
        return Deteccion(
            dificil=False,
            factual=True,
            motivos=["pregunta informativa: se responde con datos y cálculos, no hay que elegir"],
            opciones_detectadas=opciones,
        )

    motivos: list[str] = []
    puntuacion = 0.0
    clausula = plano(_clausula(pregunta))
    marcador = _hay(_DELIBERACION, texto)
    verbo = _verbo_inicial(clausula)
    deliberativa = bool(marcador or opciones or verbo)
    if not deliberativa:
        return Deteccion(dificil=False, motivos=["no plantea una elección entre opciones"])
    puntuacion += PESO_DELIBERACION
    if opciones:
        motivos.append("compara alternativas: " + " / ".join(opciones))
        if marcador or verbo:
            puntuacion += PESO_ALTERNATIVAS_EXTRA
    elif marcador:
        motivos.append(f"plantea una decisión («{marcador.group(0)}»)")
    elif verbo is not None:
        motivos.append(f"plantea una decisión («¿{verbo[0]}…?»)")

    en_juego = False
    irreversibles = [etiqueta for etiqueta, patron in _IRREVERSIBLE.items() if _hay(patron, texto)]
    if irreversibles:
        puntuacion += PESO_IRREVERSIBLE
        en_juego = True
        motivos.append("difícil de deshacer: " + ", ".join(irreversibles[:3]))
    temas = [etiqueta for etiqueta, patron in _TEMAS.items() if _hay(patron, texto)]
    if temas:
        puntuacion += PESO_TEMA
        en_juego = True
        motivos.append("tema de peso: " + ", ".join(temas[:3]))
    if _hay(_COMPROMISO, texto):
        puntuacion += PESO_COMPROMISO
        en_juego = True
        motivos.append("compromete ahorro a largo plazo")

    importes = importes_de_texto(pregunta)
    grande = _umbral_grande()
    mayor = max(importes, default=None)
    if mayor is not None and mayor >= grande:
        puntuacion += PESO_IMPORTE
        en_juego = True
        motivos.append(f"importe grande: {_eur(mayor)}")
    elif case is not None and _hay(r"hipotec\w*|amortiz\w*", texto):
        capital = _capital_hipoteca(case)
        if capital is not None and capital >= grande:
            puntuacion += PESO_IMPORTE
            en_juego = True
            motivos.append(f"importe grande: la hipoteca de tu expediente ({_eur(capital)})")
    if mayor is not None and mayor < IMPORTE_PEQUENO and not irreversibles[1:]:
        puntuacion -= PENALIZACION_IMPORTE_PEQUENO
        motivos.append(f"importe pequeño ({_eur(mayor)})")

    if intencion is not None:
        especialistas = intencion.especialistas()
        if intencion.tipo == "cruzada" or len(especialistas) >= 2:
            puntuacion += PESO_DOMINIOS
            en_juego = True
            motivos.append("cruza varios ámbitos: " + ", ".join(especialistas[:4]))
    if _hay(_DUDA, texto):
        puntuacion += PESO_DUDA
        en_juego = True
        motivos.append("declaras dudas")

    if not en_juego:
        motivos.append("no hay nada importante en juego (reversible, sin importes grandes)")
    dificil = en_juego and puntuacion >= UMBRAL_DIFICIL
    return Deteccion(
        dificil=dificil,
        motivos=motivos,
        opciones_detectadas=opciones,
        puntuacion=round(puntuacion, 2),
    )


__all__ = [
    "UMBRAL_DIFICIL",
    "Deteccion",
    "es_decision_dificil",
    "es_factual",
    "importes_de_texto",
    "opciones_de_texto",
    "pedido_por_usuario",
    "plano",
]
