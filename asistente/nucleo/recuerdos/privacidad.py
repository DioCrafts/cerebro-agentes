"""The deterministic privacy gates of the conversation memory (no model involved).

The memory keeps the user's words for weeks (raw messages) and for good (summaries, «lo que sé de ti»). Every
text that reaches its tables, or the summariser's prompt, goes through here first:

* **Credentials are never stored.** Passwords, PINs, CVVs, one-time codes, tokens, API keys and private keys
  are replaced by :data:`SECRETO` in the raw messages, and any candidate item that still carries one is dropped
  (a credential is never «something I know about you»).
* **Identifiers are masked.** Full IBANs keep only their last four digits («IBAN terminado en 1234»: enough to
  tell accounts apart), card numbers (Luhn-valid) likewise; DNI/NIE, phone numbers, e-mail addresses and long
  digit runs (account, social-security or policy numbers) are replaced by a label. The memory needs the fact
  («cobra la nómina en ING»), not the identifier.
* **Special categories of data** (GDPR art. 9 and 10: health, religion, political opinion, union membership,
  sexual orientation, ethnic origin, biometric or genetic data, criminal records) are not remembered unless the
  user explicitly asked to («recuerda que…», «apúntalo», «tenlo en cuenta») in the very message that says it;
  then the item is kept and flagged ``sensible`` so the panel shows it as such.
* **Pasted text is not the user's words.** Quoted e-mail lines («> …»), fenced blocks and forwarded/original
  messages are cut out before looking for the user's quote: what an e-mail or a document says about the user is
  not something the user said.
* **Injection bait** (the project's shared detector, ``conocimiento/llm/security.py``) disqualifies a candidate
  and is neutralised in free text (summaries, titles).

These rules are conservative on purpose: a lost memory costs a question; a wrongly kept one is a privacy
incident.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import re

from asistente.conocimiento.llm.security import neutralizar_inyeccion, parece_inyeccion, quitar_invisibles
from asistente.nucleo.memoria.modelo import normalizar_texto

SECRETO = "[dato secreto omitido]"
TAPA_DOCUMENTO = "[DNI/NIE]"
TAPA_TELEFONO = "[teléfono]"
TAPA_CORREO = "[correo]"
TAPA_NUMERO = "[número oculto]"
# Neutralised injection bait in free text (summaries, titles): readable, and never a valid quote.
TAPA_INYECCION = "[texto retirado]"

# --------------------------------------------------------------------------------------------------------------
# Credentials (dropped / replaced, never stored)
# --------------------------------------------------------------------------------------------------------------

_PALABRA_CLAVE = (
    r"(?:contrase(?:ñ|n)as?|password|passwd|\bclaves?\b|"
    r"c[oó]digo (?:de )?(?:seguridad|verificaci[oó]n|acceso|desbloqueo|sms)|\bpin\b|\bcvv\b|\bcvc\b|\botp\b|"
    r"\btoken\b|api[ _-]?key)"
)
# «mi contraseña es Perro123», «el PIN de la tarjeta es 4821», «PIN: 1234», «password = hunter2», «pin 4821»: the
# value after the keyword (and up to four words and a connector).
_CREDENCIAL_CONECTOR = re.compile(
    _PALABRA_CLAVE
    + r"(?:(?:\s+[^\s:=]+){0,4}?(?:\s+(?:es|era|ser[aá]|sea)\b\s*|\s*[:=]\s*)|\s+)[\"'«“]?(?P<valor>[^\s\"'»”]{3,})",
    re.IGNORECASE,
)
_TOKENS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.DOTALL),
)
# A long opaque string mixing upper case, lower case and digits (a generated secret, not a word or an amount).
_OPACO = re.compile(
    r"(?<![\w/.-])(?=[A-Za-z0-9_+/=-]*[A-Z])(?=[A-Za-z0-9_+/=-]*[a-z])(?=[A-Za-z0-9_+/=-]*\d)"
    r"[A-Za-z0-9_+/=-]{24,}(?![\w/.-])"
)


def _es_valor_de_credencial(valor: str) -> bool:
    """«es Perro123» or «es hunter2!» is a credential; «es importante», «es Hacienda» or «pin para Hacienda» is
    not (no digit, no symbol, no capital inside the word)."""
    plano = valor.strip(".,;:!?)")
    if len(plano) < 3:
        return False
    return any(c.isdigit() for c in plano) or any(not c.isalnum() for c in plano) or plano[1:] != plano[1:].lower()


def redactar_credenciales(texto: str) -> str:
    """``texto`` with every credential VALUE replaced by :data:`SECRETO` (the keyword stays, for context)."""
    salida = texto
    for patron in _TOKENS:
        salida = patron.sub(SECRETO, salida)

    def _tapar(m: re.Match[str]) -> str:
        valor = m.group("valor")
        if not _es_valor_de_credencial(valor):
            return m.group(0)
        inicio = m.start("valor") - m.start()
        return m.group(0)[:inicio] + SECRETO

    salida = _CREDENCIAL_CONECTOR.sub(_tapar, salida)
    return _OPACO.sub(SECRETO, salida)


def tiene_credencial(texto: str) -> bool:
    return SECRETO in texto or redactar_credenciales(texto) != texto


# --------------------------------------------------------------------------------------------------------------
# Identifiers (masked)
# --------------------------------------------------------------------------------------------------------------

_IBAN = re.compile(r"\b([A-Z]{2})\s?(\d{2})((?:\s?[A-Z0-9]){11,30})\b", re.IGNORECASE)
_TARJETA = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_DNI = re.compile(r"(?<![\w])(?:\d{8}[ -]?[A-Za-z]|[XYZxyz][ -]?\d{7}[ -]?[A-Za-z])(?![\w])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
# Spanish phone numbers (9 digits starting with 6, 7, 8 or 9), optionally +34, in groups or not.
_TELEFONO = re.compile(r"(?<![\w+])(?:\+?34[ -]?)?[6789]\d{2}(?:[ -]?\d{2,3}){2,3}(?![\w])")
_DIGITOS_LARGOS = re.compile(r"(?<![\w.,])\d{10,}(?![\w])")


def _luhn(digitos: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(digitos)):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _iban_valido(pais: str, control: str, resto: str) -> bool:
    cuerpo = (resto + pais + control).upper()
    try:
        numero = "".join(str(int(c, 36)) for c in cuerpo)
    except ValueError:
        return False
    return int(numero) % 97 == 1


def enmascarar_identificadores(texto: str) -> str:
    """``texto`` with IBANs, card numbers, DNI/NIE, e-mails, phone numbers and long digit runs masked."""

    def _iban(m: re.Match[str]) -> str:
        resto = re.sub(r"\s", "", m.group(3))
        if not _iban_valido(m.group(1), m.group(2), resto) and not (m.group(1).upper() == "ES" and len(resto) == 20):
            return m.group(0)
        return f"[IBAN terminado en {resto[-4:]}]"

    def _tarjeta(m: re.Match[str]) -> str:
        digitos = re.sub(r"\D", "", m.group(0))
        if not 13 <= len(digitos) <= 19 or not _luhn(digitos):
            return m.group(0)
        return f"[tarjeta terminada en {digitos[-4:]}]"

    salida = _EMAIL.sub(TAPA_CORREO, texto)
    salida = _IBAN.sub(_iban, salida)
    salida = _TARJETA.sub(_tarjeta, salida)
    salida = _DNI.sub(TAPA_DOCUMENTO, salida)
    salida = _TELEFONO.sub(TAPA_TELEFONO, salida)
    return _DIGITOS_LARGOS.sub(TAPA_NUMERO, salida)


def limpiar(texto: str) -> str:
    """What the memory may keep of a text: no invisible characters, no credential values, identifiers masked."""
    return enmascarar_identificadores(redactar_credenciales(quitar_invisibles(texto)))


# --------------------------------------------------------------------------------------------------------------
# Special categories of personal data (GDPR art. 9 and 10)
# --------------------------------------------------------------------------------------------------------------

# Normalised (lower case, no accents) word stems per category. Chosen to name a condition, a belief or an
# affiliation, not the household paperwork around them: «seguro de salud», «gastos médicos» or «cuota» alone do
# not trigger; «diabetes», «sindicato» or «católico» do.
_ESPECIALES: dict[str, tuple[str, ...]] = {
    "salud": (
        r"enfermedad\w*",
        r"enferm[oa]s?",
        r"diagnostic\w*",
        r"cancer\w*",
        r"tumor\w*",
        r"diabet\w*",
        r"depresion\w*",
        r"deprimid[oa]s?",
        r"ansiedad",
        r"embaraz\w*",
        r"discapacidad\w*",
        r"minusvali\w*",
        r"medicacion\w*",
        r"medicamento\w*",
        r"quimioterapia",
        r"psiquiatr\w*",
        r"psicolog[oa]s?",
        r"terapia\w*",
        r"vih",
        r"sida",
        r"cirugia\w*",
        r"baja medica",
        r"incapacidad (?:temporal|permanente)",
        r"alzheimer",
        r"autismo",
        r"tdah",
        r"esclerosis",
    ),
    "religion": (
        r"religion\w*",
        r"religios[oa]s?",
        r"catolic[oa]s?",
        r"musulman\w*",
        r"islam\w*",
        r"judi[oa]s?",
        r"evangelic[oa]s?",
        r"protestante\w*",
        r"testigos? de jehova",
        r"ate[oa]s?",
        r"mezquita",
        r"sinagoga",
        r"creyente\w*",
    ),
    "opinion_politica": (
        r"psoe",
        r"vox",
        r"podemos",
        r"sumar",
        r"\berc\b",
        r"junts",
        r"bildu",
        r"pnv",
        r"partido popular",
        r"vot[oéeé] (?:a|al)",
        r"votante\w*",
        r"militant\w*",
        r"ideologia\w*",
        r"de (?:izquierdas|derechas)",
        r"independentista\w*",
    ),
    "afiliacion_sindical": (r"sindicat\w*", r"ccoo", r"comisiones obreras", r"\bugt\b", r"\bcgt\b", r"\bcsif\b"),
    "orientacion_sexual": (
        r"homosexual\w*",
        r"heterosexual\w*",
        r"bisexual\w*",
        r"gay",
        r"gais",
        r"lesbiana\w*",
        r"transexual\w*",
        r"lgt\w*",
        r"orientacion sexual",
    ),
    "origen_etnico": (r"etnia\w*", r"etnic[oa]s?", r"raza", r"gitan[oa]s?"),
    "biometrico_genetico": (
        r"huella dactilar",
        r"reconocimiento facial",
        r"biometric\w*",
        r"\badn\b",
        r"genetic[oa]s?",
    ),
    "antecedentes_penales": (r"antecedentes penales", r"condenad[oa]s?", r"condena\b", r"carcel", r"prision"),
}
_ESPECIALES_RE: dict[str, re.Pattern[str]] = {
    categoria: re.compile(r"\b(?:" + "|".join(patrones) + r")\b") for categoria, patrones in _ESPECIALES.items()
}

# The user explicitly asked to remember: in the same message as the quote.
_PIDE_RECORDAR = re.compile(
    r"\b(?:recuerda(?:lo|me)?|recordad|acuerdate|no (?:te )?olvides|no lo olvides|apunta(?:lo|te)?|anota(?:lo)?|"
    r"guarda(?:lo)?|memoriza(?:lo)?|tenlo en cuenta|ten en cuenta|quiero que (?:lo )?sepas|que lo sepas)\b"
)


def categoria_especial(texto: str) -> str | None:
    """The special category ``texto`` falls in («salud», «religion»…), or None."""
    plano = normalizar_texto(texto)
    for categoria, patron in _ESPECIALES_RE.items():
        if patron.search(plano):
            return categoria
    return None


def pidio_recordar(cita: str, mensajes_usuario: Sequence[str]) -> bool:
    """Whether a user message that contains ``cita`` also asks, in so many words, to remember it."""
    buscada = normalizar_texto(cita)
    if not buscada:
        return False
    for mensaje in mensajes_usuario:
        plano = normalizar_texto(mensaje)
        if buscada in plano and _PIDE_RECORDAR.search(plano):
            return True
    return False


# --------------------------------------------------------------------------------------------------------------
# The user's own words (without what they pasted)
# --------------------------------------------------------------------------------------------------------------

_BLOQUE_CODIGO = re.compile(r"```.*?(?:```|$)", re.DOTALL)
# The start of a forwarded or quoted message: everything from there on is somebody else's text.
_REENVIO = re.compile(
    r"^(?:-{2,}\s*(?:forwarded message|mensaje (?:reenviado|original)|original message)\s*-{0,}|"
    r"(?:de|from)\s*:.+\n(?:.*\n){0,3}?\s*(?:enviado|sent|fecha|date|asunto|subject|para|to)\s*:)",
    re.IGNORECASE | re.MULTILINE,
)


def quitar_pegado(mensaje: str) -> str:
    """The user's own words of one message: without fenced blocks, quoted lines («> …») and anything from the
    start of a forwarded / original message on."""
    sin_bloques = _BLOQUE_CODIGO.sub(" ", mensaje)
    corte = _REENVIO.search(sin_bloques)
    if corte is not None:
        sin_bloques = sin_bloques[: corte.start()]
    lineas = [linea for linea in sin_bloques.splitlines() if not linea.lstrip().startswith(">")]
    return "\n".join(lineas)


def palabras_propias(mensajes_usuario: Iterable[str]) -> list[str]:
    return [p for p in (quitar_pegado(m) for m in mensajes_usuario) if p.strip()]


# --------------------------------------------------------------------------------------------------------------
# Injection bait
# --------------------------------------------------------------------------------------------------------------


def es_inyeccion(texto: str) -> bool:
    return parece_inyeccion(texto)


def neutralizar(texto: str) -> str:
    return neutralizar_inyeccion(texto, TAPA_INYECCION)


__all__ = [
    "SECRETO",
    "TAPA_CORREO",
    "TAPA_DOCUMENTO",
    "TAPA_INYECCION",
    "TAPA_NUMERO",
    "TAPA_TELEFONO",
    "categoria_especial",
    "enmascarar_identificadores",
    "es_inyeccion",
    "limpiar",
    "neutralizar",
    "palabras_propias",
    "pidio_recordar",
    "quitar_pegado",
    "redactar_credenciales",
    "tiene_credencial",
]
