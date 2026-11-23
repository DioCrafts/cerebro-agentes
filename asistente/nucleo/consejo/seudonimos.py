"""Pseudonymisation of everything the council reads about the household (pure, deterministic, no model).

The council's members are models of possibly several providers; they must weigh the OPTIONS, not the person. Two
reasons to strip what the decision does not depend on:

* **privacy** (data minimisation): a provider never needs the user's name, documents or contact data to weigh
  «amortizar o invertir»;
* **fairness**: an attribute the decision does not depend on (sex, nationality or origin, religion…) can only bias
  the ruling. ``tests/consejo/test_seudonimos.py`` checks the counterfactual: swap the user's sex or origin and the
  dossier is byte-for-byte the same.

What stays, and why:

* :func:`seudonimizar_perfil` keeps a per-domain ALLOWLIST of profile facts the decision may depend on (age,
  dependants, autonomous community, marital status without grammatical gender, legal categories such as a
  recognised disability ≥33 % — never the diagnosis or the exact grade —, tenure of the home, the mortgage's type);
  income becomes a band (the state IRPF brackets) because the exact figures that matter are the specialists'
  calculations, which travel as exact ``Cifra`` with their provenance. Everything else is dropped and only COUNTED
  in ``anonimizado`` (never its value).
* :func:`seudonimizar_texto` cleans free text that reaches the dossier (the user's words, the conversation memory,
  the specialists' reports, decisions): credentials redacted, identifiers masked (the memory's own helpers: IBAN,
  cards, DNI/NIE, phones, e-mails, long numbers), names replaced (kinship kept: «mi padre»), origin and nationality
  removed, gendered self-descriptions made neutral, injection bait neutralised (text from mail, web or memory is
  DATA, never instructions).

Limits (documented in docs/consejo/arquitectura.md): names are found by context («me llamo…», «mi hermano X»,
«Sr./Sra.») and by a list of common given names, not by a model; a rare name written alone may pass. The masking is
conservative where it can be (a false positive costs a word; a leak costs privacy).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

from asistente.conocimiento.llm.security import neutralizar_inyeccion, quitar_invisibles
from asistente.nucleo.recuerdos.privacidad import enmascarar_identificadores, redactar_credenciales

PERSONA = "[persona]"
ORIGEN = "[origen omitido]"
DATO_OMITIDO = "[dato omitido]"
INYECCION = "[texto retirado]"

# What ``anonimizado`` says (Spanish, for the audit trail and the UI: never the original values).
NOTA_IDENTIFICADORES = "Nombre, documentos y datos de contacto: no se incluyen."
NOTA_SEXO = "Sexo o género: no se incluye (la decisión no depende de él)."
NOTA_ORIGEN = "Nacionalidad u origen: no se incluye (la decisión no depende de él)."
NOTA_RELIGION = "Religión o creencias: no se incluyen."
NOTA_SALUD = "Datos de salud: no se incluyen."
NOTA_DISCAPACIDAD = (
    "Discapacidad: solo la categoría legal («reconocida ≥33 %» o «≥65 %»), sin diagnóstico ni grado exacto."
)
NOTA_PROTEGIDOS = "Situaciones personales especialmente protegidas: no se incluyen."
NOTA_NACIMIENTO = "Fechas de nacimiento: no se incluyen (basta la edad)."
NOTA_INGRESOS = (
    "Ingresos: por tramos del IRPF; las cifras exactas que importan están en los cálculos de los especialistas."
)
NOTA_ESTADO_CIVIL = "Estado civil: sin género gramatical."
NOTA_TEXTO_IDENTIFICADORES = "Identificadores en los textos (DNI, IBAN, tarjetas, teléfonos, correos): enmascarados."
NOTA_TEXTO_SECRETOS = "Contraseñas o códigos en los textos: retirados."
NOTA_TEXTO_NOMBRES = "Nombres de personas en los textos: sustituidos (se mantiene el parentesco)."
NOTA_TEXTO_ORIGEN = "Origen o nacionalidad mencionados en los textos: retirados."
NOTA_TEXTO_GENERO = "Descripciones personales con género gramatical: neutralizadas."
NOTA_TEXTO_INYECCION = "Texto con apariencia de instrucciones (de correo, web o memoria): neutralizado."
NOTA_RECUERDO_ESPECIAL = (
    "Recuerdos con datos especialmente protegidos (salud, religión, ideología…): no se usan en el expediente."
)


def nota_otros(n: int) -> str:
    return (
        f"{n} dato{'s' if n != 1 else ''} del expediente que esta decisión no necesita: "
        f"no se incluye{'n' if n != 1 else ''}."
    )


# --------------------------------------------------------------------------------------------------------------
# The profile
# --------------------------------------------------------------------------------------------------------------

# State IRPF brackets (art. 63 LIRPF): enough to reason about the marginal rate without the exact figure.
_TRAMOS = (
    (Decimal("12450"), "menos de 12.450 €/año"),
    (Decimal("20200"), "entre 12.450 y 20.200 €/año"),
    (Decimal("35200"), "entre 20.200 y 35.200 €/año"),
    (Decimal("60000"), "entre 35.200 y 60.000 €/año"),
    (Decimal("300000"), "entre 60.000 y 300.000 €/año"),
)
_TRAMO_SUPERIOR = "más de 300.000 €/año"

_ESTADO_CIVIL = {
    "casado": "casado/a",
    "casada": "casado/a",
    "soltero": "soltero/a",
    "soltera": "soltero/a",
    "viudo": "viudo/a",
    "viuda": "viudo/a",
    "divorciado": "divorciado/a",
    "divorciada": "divorciado/a",
    "separado": "separado/a",
    "separada": "separado/a",
    "pareja de hecho": "pareja de hecho",
}


def _numero(valor: Any) -> Decimal | None:
    if isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return numero if numero.is_finite() else None


def tramo_de_ingresos(valor: Any) -> str | None:
    numero = _numero(valor)
    if numero is None or numero < 0:
        return None
    for tope, etiqueta in _TRAMOS:
        if numero < tope:
            return etiqueta
    return _TRAMO_SUPERIOR


def _entero(valor: Any) -> str | None:
    numero = _numero(valor)
    if numero is None or numero < 0:
        return None
    return str(int(numero))


def _si_no(valor: Any) -> str | None:
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    if isinstance(valor, str) and valor.strip().lower() in ("true", "false", "si", "sí", "no"):
        return "sí" if valor.strip().lower() in ("true", "si", "sí") else "no"
    return None


def _edad(valor: Any) -> str | None:
    numero = _entero(valor)
    return f"{numero} años" if numero is not None and 0 < int(numero) < 120 else None


def _estado_civil(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    clave = " ".join(valor.strip().lower().split())
    return _ESTADO_CIVIL.get(clave, "otro" if clave else None)


def _discapacidad(valor: Any) -> str | None:
    numero = _numero(valor)
    if numero is None or numero < 33:
        return None
    return "reconocida ≥65 %" if numero >= 65 else "reconocida ≥33 %"


def _texto_corto(valor: Any) -> str | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    return seudonimizar_texto(valor.strip())[:80]


def _positivo(valor: Any) -> str | None:
    numero = _numero(valor)
    if numero is None:
        return None
    return "sí" if numero > 0 else "no"


# fact name → (label in the minimal profile, formatter). The formatter returns None to leave the fact out.
_Regla = tuple[str, Any]
_COMUN: dict[str, _Regla] = {
    "edad": ("edad", _edad),
    "comunidad_autonoma": ("comunidad autónoma", _texto_corto),
    "residencia_fiscal": ("residencia fiscal", _texto_corto),
    "estado_civil": ("estado civil", _estado_civil),
    "num_descendientes": ("descendientes a cargo", _entero),
    "num_descendientes_menores_3": ("descendientes menores de 3 años", _entero),
    "num_ascendientes_mayores_65": ("ascendientes mayores de 65 años a cargo", _entero),
    "num_ascendientes_mayores_75": ("ascendientes mayores de 75 años a cargo", _entero),
    "miembros_unidad_familiar": ("miembros de la unidad familiar", _entero),
    "familia_numerosa": ("familia numerosa", _si_no),
    "familia_monoparental": ("familia monoparental", _si_no),
    "grado_discapacidad": ("discapacidad", _discapacidad),
    "necesita_asistencia": ("necesita ayuda de terceros (reconocida)", _si_no),
    "grado_discapacidad_conyuge": ("discapacidad del cónyuge", _discapacidad),
    "num_descendientes_discapacidad": ("descendientes con discapacidad reconocida", _entero),
    "num_ascendientes_discapacidad": ("ascendientes con discapacidad reconocida", _entero),
    "rendimiento_trabajo_bruto": ("ingresos del trabajo", tramo_de_ingresos),
    "importe_renta_aproximado": ("ingresos aproximados", tramo_de_ingresos),
    "rendimiento_actividad_neto": ("rendimiento neto de actividad", tramo_de_ingresos),
    "es_autonomo": ("trabaja por cuenta propia", _si_no),
}
_POR_DOMINIO: dict[str, dict[str, _Regla]] = {
    "fiscal": {
        "base_imponible_resto_unidad_familiar": ("ingresos del resto de la unidad familiar", tramo_de_ingresos),
        "rendimiento_alquiler_neto": ("rendimientos netos de alquiler", tramo_de_ingresos),
        "rendimiento_capital_mobiliario": ("rendimientos del ahorro", tramo_de_ingresos),
        "ganancias_patrimoniales": ("ganancias patrimoniales del año", tramo_de_ingresos),
        "aportacion_plan_pensiones": ("aporta a un plan de pensiones", _positivo),
        "pagos_vivienda_habitual_pre2013": ("deducción transitoria por vivienda (compra antes de 2013)", _positivo),
        "primera_vivienda": ("primera vivienda", _si_no),
        "vivienda_protegida": ("vivienda protegida", _si_no),
        "alquiler_vivienda_habitual": ("paga alquiler de su vivienda", _positivo),
        "bienes_en_el_extranjero": ("bienes en el extranjero", _si_no),
    },
    "hipoteca": {},
    "ayudas": {
        "beneficiario_ingreso_minimo_vital": ("cobra el ingreso mínimo vital", _si_no),
    },
}
_DOMINIOS_CONOCIDOS = frozenset(_POR_DOMINIO)

# Facts dropped with a named reason (patterns over the fact name).
_IDENTIFICADORES = re.compile(
    r"nombre|apellido|\bdni\b|_dni|nif|\bnie\b|_nie|pasaporte|email|correo_e|telefono|movil|direccion|domicilio|"
    r"iban|cuenta_bancaria|tarjeta|matricula|codigo_postal|firma"
)
_SEXO = re.compile(r"sexo|genero(?!_)")
_ORIGEN = re.compile(r"nacionalidad|pais|origen|etnia|lugar_nacimiento|nacido")
_RELIGION = re.compile(r"religion|confesion|creencia")
_SALUD = re.compile(r"enfermedad|diagnostico|patologia|tratamiento|medicacion|salud_mental")
_PROTEGIDOS = re.compile(r"victima_|violencia|antecedentes")
_NACIMIENTO = re.compile(r"fecha_nacimiento|fechas_nacimiento|nacimiento")


def _hipoteca(valor: Any, hoy: date | None) -> dict[str, str]:
    """The mortgage as the decision needs it: its type, the home it pays, the years left (rounded). The exact
    pending capital and instalment are the mortgage specialist's figures, not the profile's."""
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except json.JSONDecodeError:
            return {"vivienda": "en propiedad con hipoteca"}
    if not isinstance(valor, Mapping):
        return {"vivienda": "en propiedad con hipoteca"}
    salida = {"vivienda": "en propiedad con hipoteca"}
    tipo = str(valor.get("tipo") or "").strip().lower()
    if tipo in ("fijo", "variable", "mixto"):
        salida["hipoteca: tipo de interés"] = tipo
    if isinstance(valor.get("vivienda_habitual"), bool):
        salida["hipoteca: es de la vivienda habitual"] = "sí" if valor["vivienda_habitual"] else "no"
    if isinstance(valor.get("adquisicion_antes_2013"), bool):
        salida["hipoteca: vivienda comprada antes de 2013"] = "sí" if valor["adquisicion_antes_2013"] else "no"
    plazo = _numero(valor.get("plazo_meses"))
    firma = valor.get("fecha_firma")
    if plazo is not None and isinstance(firma, str) and hoy is not None:
        try:
            inicio = date.fromisoformat(firma[:10])
        except ValueError:
            inicio = None
        if inicio is not None:
            transcurridos = (hoy.year - inicio.year) * 12 + (hoy.month - inicio.month)
            restantes = max(0, int(plazo) - max(0, transcurridos))
            anos = round(restantes / 12)
            salida["hipoteca: años que quedan (aprox.)"] = f"unos {anos}" if anos else "menos de uno"
    return salida


def seudonimizar_perfil(
    perfil: Mapping[str, Any], *, dominios: Iterable[str] | None = None, hoy: date | None = None
) -> tuple[dict[str, str], list[str]]:
    """``(perfil_minimo, anonimizado)``: the minimum profile the decision needs, and what was removed.

    ``perfil``: the case's facts (name → value, JSON facts parsed or as text: ``modulos.perfil.perfil_de``).
    ``dominios``: the domains the decision touches (the specialists consulted); the shared allowlist always applies.
    Deterministic and pure: the same input gives the same output, and the output never holds a dropped value.
    """
    reglas: dict[str, _Regla] = dict(_COMUN)
    for dominio in dominios or ():
        reglas.update(_POR_DOMINIO.get(dominio, {}))
    minimo: dict[str, str] = {}
    notas: list[str] = []
    otros = 0

    def anotar(nota: str) -> None:
        if nota not in notas:
            notas.append(nota)

    for nombre in sorted(perfil):
        valor = perfil[nombre]
        clave = nombre.lower()
        if valor is None or valor == "":
            continue
        if _IDENTIFICADORES.search(clave):
            anotar(NOTA_IDENTIFICADORES)
            continue
        if _SEXO.search(clave):
            anotar(NOTA_SEXO)
            continue
        if _ORIGEN.search(clave) and clave != "residencia_fiscal":
            anotar(NOTA_ORIGEN)
            continue
        if _RELIGION.search(clave):
            anotar(NOTA_RELIGION)
            continue
        if _PROTEGIDOS.search(clave):
            anotar(NOTA_PROTEGIDOS)
            continue
        if _SALUD.search(clave):
            anotar(NOTA_SALUD)
            continue
        if _NACIMIENTO.search(clave):
            anotar(NOTA_NACIMIENTO)
            continue
        if clave == "hipoteca_prestamo":
            if dominios is None or "hipoteca" in set(dominios) or "fiscal" in set(dominios):
                minimo.update(_hipoteca(valor, hoy))
            else:
                otros += 1
            continue
        regla = reglas.get(clave)
        if regla is None:
            otros += 1
            continue
        etiqueta, formato = regla
        texto = formato(valor)
        if clave in ("grado_discapacidad", "grado_discapacidad_conyuge"):
            anotar(NOTA_DISCAPACIDAD)
        if formato is tramo_de_ingresos:
            anotar(NOTA_INGRESOS)
        if clave == "estado_civil" and texto is not None:
            anotar(NOTA_ESTADO_CIVIL)
        if texto is None:
            otros += 1
            continue
        minimo[etiqueta] = texto
    if "vivienda" not in minimo and _numero(perfil.get("alquiler_vivienda_habitual") or 0) not in (None, Decimal(0)):
        minimo["vivienda"] = "de alquiler"
    if otros:
        notas.append(nota_otros(otros))
    return minimo, notas


# --------------------------------------------------------------------------------------------------------------
# Free text
# --------------------------------------------------------------------------------------------------------------

_MAY = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+"
_NOMBRE_PROPIO = rf"{_MAY}(?:\s+(?:de\s+(?:la\s+|los\s+)?)?{_MAY}){{0,3}}"
_PARENTESCO = (
    r"padre|madre|hermano|hermana|hijo|hija|t[ií]o|t[ií]a|abuelo|abuela|marido|mujer|esposo|esposa|pareja|primo|"
    r"prima|suegro|suegra|cu[ñn]ado|cu[ñn]ada|sobrino|sobrina|novio|novia|nieto|nieta|vecino|vecina|amigo|amiga|"
    r"jefe|jefa|socio|socia|casero|casera|inquilino|inquilina|ex"
)
_CON_PARENTESCO = re.compile(
    rf"\b((?i:mi|su|tu|nuestro|nuestra|mis|sus|nuestros|nuestras|el|la|al|del)\s+(?i:{_PARENTESCO})s?),?\s+"
    rf"(?:{_NOMBRE_PROPIO})(?=[\s,.;:!?)»]|$)",
)
_ME_LLAMO = re.compile(rf"\b(me llamo|mi nombre es|ll[aá]mame|soy el|soy la)\s+{_NOMBRE_PROPIO}", re.IGNORECASE)
_TRATAMIENTO = re.compile(rf"\b(?:Sr\.?|Sra\.?|Srta\.?|Don|Doña|D\.|Dña\.?)\s+{_NOMBRE_PROPIO}")
# Common given names (Spain, INE) and some frequent among residents born abroad: replaced when written alone.
_NOMBRES_COMUNES = frozenset(
    """
    Antonio Manuel José Jose Francisco David Juan Javier Daniel Carlos Jesús Jesus Alejandro Miguel Rafael Pedro
    Pablo Ángel Angel Sergio Fernando Jorge Luis Alberto Álvaro Alvaro Adrián Adrian Diego Raúl Raul Enrique Ramón
    Ramon Vicente Iván Ivan Rubén Ruben Óscar Oscar Andrés Andres Joaquín Joaquin Santiago Eduardo Víctor Victor
    Roberto Jaime Mario Ignacio Alfonso Salvador Ricardo Marcos Jordi Emilio Julián Julian Julio Guillermo Gabriel
    Tomás Tomas Agustín Agustin Mohamed Mohammed Ahmed Hugo Martín Martin Lucas Mateo Leo Gonzalo Nicolás Nicolas
    María Maria Carmen Ana Isabel Laura Cristina Antonia Marta Dolores Lucía Lucia Pilar Elena Sara Paula Josefa
    Raquel Rosa Manuela Mercedes Teresa Beatriz Alba Silvia Julia Patricia Irene Nuria Andrea Rocío Rocio Montserrat
    Mónica Monica Rosario Concepción Concepcion Sandra Alicia Sonia Yolanda Marina Eva Inmaculada Sofía Sofia
    Claudia Natalia Ángela Angela Noelia Carolina Susana Victoria Lorena Esther Amparo Verónica Veronica Gloria
    Fatima Fátima Nerea Carla Daniela Valeria Martina Lola Inés Ines Olga Fernanda Gabriela Aitana Lidia Miriam
    """.split()
)
_PALABRA_MAYUSCULA = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+\b")
_NACIONALIDADES = (
    r"espa[ñn]ol[a]?(?:es|as)?|marroqu[ií](?:es)?|rumano|rumana|ecuatorian[oa]|colombian[oa]|venezolan[oa]|"
    r"argentin[oa]|peruan[oa]|bolivian[oa]|chin[oa]|italian[oa]|francesa|franc[eé]s|alem[aá]n|alemana|brit[aá]nic[oa]|"
    r"ingl[eé]s|inglesa|portugu[eé]s|portuguesa|ucranian[oa]|rus[oa]|argelin[oa]|senegal[eé]s|senegalesa|nigerian[oa]|"
    r"pakistan[ií]|indi[oa]|hind[uú]|filipin[oa]|dominican[oa]|cuban[oa]|mexican[oa]|chilen[oa]|uruguay[oa]|"
    r"paraguay[oa]|hondure[ñn][oa]|nicaragüense|nicaraguense|salvadore[ñn][oa]|guatemaltec[oa]|bulgar[oa]|polac[oa]|"
    r"georgian[oa]|árabe|arabe|africano|africana|latin[oa]|latinoamerican[oa]|gitan[oa]|extranjer[oa]|inmigrante|"
    r"musulm[aá]n|musulmana|cat[oó]lic[oa]|judí[oa]?|jud[ií][oa]|evang[eé]lic[oa]|ate[oa]"
)
_ORIGEN_TEXTO = re.compile(
    rf"\b(soy|es|somos|son|eres|como|siendo)\s+(?:de\s+origen\s+|de\s+nacionalidad\s+)?(?:{_NACIONALIDADES})\b"
    rf"|\b(nacid[oa]s?\s+en|nac[ií]\s+en|nacimos\s+en|vengo\s+de|venimos\s+de|soy\s+de|somos\s+de|de\s+origen|de\s+nacionalidad|"
    rf"originari[oa]s?\s+de)\s+(?:{_NOMBRE_PROPIO}|(?:{_NACIONALIDADES}))",
    re.IGNORECASE,
)
_GENERO_PROPIO = {
    "viuda": "viudo/a",
    "viudo": "viudo/a",
    "casada": "casado/a",
    "casado": "casado/a",
    "soltera": "soltero/a",
    "soltero": "soltero/a",
    "divorciada": "divorciado/a",
    "divorciado": "divorciado/a",
    "separada": "separado/a",
    "separado": "separado/a",
    "jubilada": "jubilado/a",
    "jubilado": "jubilado/a",
    "autónoma": "autónomo/a",
    "autónomo": "autónomo/a",
    "autonoma": "autónomo/a",
    "autonomo": "autónomo/a",
    "pensionista": "pensionista",
    "madre": "progenitor/a",
    "padre": "progenitor/a",
    "mujer": DATO_OMITIDO,
    "hombre": DATO_OMITIDO,
    "una mujer": DATO_OMITIDO,
    "un hombre": DATO_OMITIDO,
    "madre soltera": "progenitor/a sin pareja",
    "padre soltero": "progenitor/a sin pareja",
}
_GENERO_RE = re.compile(
    r"\b(soy|estoy|somos|estamos|como|siendo|me quedé|me quede)\s+("
    + "|".join(sorted((re.escape(k) for k in _GENERO_PROPIO), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


# Marital and retirement adjectives say the speaker's sex wherever they are («Soy de aquí, viuda y con dos hijos»).
_GENERO_SUELTO = re.compile(r"\b(viud|casad|solter|divorciad|jubilad)([oa])s?\b", re.IGNORECASE)


def _nombres_sueltos(texto: str) -> str:
    def cambiar(m: re.Match[str]) -> str:
        return PERSONA if m.group(0) in _NOMBRES_COMUNES else m.group(0)

    return _PALABRA_MAYUSCULA.sub(cambiar, texto)


def _colapsar_personas(texto: str) -> str:
    return re.sub(rf"{re.escape(PERSONA)}(?:\s+{re.escape(PERSONA)})+", PERSONA, texto)


def seudonimizar_texto(texto: str, notas: list[str] | None = None) -> str:
    """``texto`` as the council may read it (see the module docstring). ``notas`` collects what was done (Spanish,
    no values), deduplicated."""

    def anotar(nota: str) -> None:
        if notas is not None and nota not in notas:
            notas.append(nota)

    if not texto:
        return ""
    salida = quitar_invisibles(texto)
    sin_secretos = redactar_credenciales(salida)
    if sin_secretos != salida:
        anotar(NOTA_TEXTO_SECRETOS)
    salida = sin_secretos
    enmascarado = enmascarar_identificadores(salida)
    if enmascarado != salida:
        anotar(NOTA_TEXTO_IDENTIFICADORES)
    salida = enmascarado
    sin_nombres = _CON_PARENTESCO.sub(lambda m: m.group(1), salida)
    sin_nombres = _ME_LLAMO.sub(lambda m: f"{m.group(1)} {PERSONA}", sin_nombres)
    sin_nombres = _TRATAMIENTO.sub(PERSONA, sin_nombres)
    sin_nombres = _colapsar_personas(_nombres_sueltos(sin_nombres))
    if sin_nombres != salida:
        anotar(NOTA_TEXTO_NOMBRES)
    salida = sin_nombres
    sin_origen = _ORIGEN_TEXTO.sub(lambda m: f"{m.group(1) or m.group(2)} {ORIGEN}", salida)
    if sin_origen != salida:
        anotar(NOTA_TEXTO_ORIGEN)
    salida = sin_origen
    neutro = _GENERO_RE.sub(lambda m: f"{m.group(1)} {_GENERO_PROPIO[m.group(2).lower()]}", salida)
    neutro = _GENERO_SUELTO.sub(lambda m: f"{m.group(1)}o/a", neutro)
    if neutro != salida:
        anotar(NOTA_TEXTO_GENERO)
    salida = neutro
    sin_inyeccion = neutralizar_inyeccion(salida, INYECCION)
    if sin_inyeccion != salida:
        anotar(NOTA_TEXTO_INYECCION)
    return " ".join(sin_inyeccion.split()) if "\n" not in sin_inyeccion else sin_inyeccion.strip()


__all__ = [
    "DATO_OMITIDO",
    "INYECCION",
    "NOTA_RECUERDO_ESPECIAL",
    "ORIGEN",
    "PERSONA",
    "nota_otros",
    "seudonimizar_perfil",
    "seudonimizar_texto",
    "tramo_de_ingresos",
]
