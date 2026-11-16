"""Declarative browser scripts ("guiones"): what the agent may do on a site, as versioned data.

A script is NOT code. It is a JSON document shaped like the format specs of the format watch
(:mod:`hacienda_gpt.ops.formatos`: ``fuente``, ``version``, ``descripcion``, ``origen``, ``reglas``) whose
``reglas`` validate against :class:`Guion`. The bundled scripts live in ``guiones/``; a reviewed repair
(a new selector after a site changed) is written to ``FORMAT_DIR/specs/<fuente>.json`` with a higher version
and wins over the bundled one, exactly like a format spec.

What a script can express is deliberately small, so that what it cannot express cannot happen:

* ``ir``: open a path of one of the script's declared origins;
* ``esperar``: wait for an element (optionally with a literal text);
* ``rellenar`` / ``seleccionar``: type a value into / choose an option of ONE form field, identified by its
  selector AND its ``name`` attribute AND the literal text of its label (all three are checked at run time);
* ``pulsar``: click an element whose visible text is literally ``texto`` and that does not change anything
  (a «Siguiente» or «Consultar» button; the static check refuses texts like «Enviar», «Firmar», «Pagar»,
  the run-time check refuses submit buttons and the network policy refuses any non-GET request);
* ``descargar``: click a download link and keep the file if its type and size are the declared ones;
* ``detener``: the LAST step of a procedure: locate the button that would submit/sign/pay, check its literal
  text, and stop there WITHOUT clicking it, with a screenshot and a summary of what would be sent.

There is no step type that submits, signs, pays or accepts anything, and no way to type into a credential
field: logins happen in a window the user controls (see ``ejecutor.py``).
"""

from __future__ import annotations

from datetime import date, datetime
import json
import logging
from pathlib import Path
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hacienda_gpt import settings

logger = logging.getLogger(__name__)

GUIONES_INCORPORADOS = Path(__file__).resolve().parent / "guiones"
# Format-watch source key of a script: ``nav_<clave>``.
PREFIJO_FUENTE = "nav_"
MAX_PASOS = 40
MAX_SELECTOR = 300
MAX_VALOR = 500

_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_PLANTILLA = re.compile(r"\{([a-z][a-z0-9_]{0,40})\}")
# Texts of controls that change state somewhere: submitting, signing, paying, accepting, deleting…
# Matched on the normalised (lower-case, unaccented) visible text of a control.
_CAMBIA_ESTADO = re.compile(
    r"\b(firm(?:ar|a|e|o)|envi(?:ar|a|e|o)|present(?:ar|a|e|o)|pag(?:ar|a|ue|o)|acept(?:ar|a|e|o)|"
    r"confirm(?:ar|a|e|o)|registr(?:ar|a|e)|tramit(?:ar|a|e)|solicit(?:ar|a|e)|contrat(?:ar|a|e)|"
    r"suscrib(?:ir|a|e|o)|borr(?:ar|a|e)|elimin(?:ar|a|e)|baja|autoriz(?:ar|a|e|o)|finaliz(?:ar|a|e)|"
    r"terminar|guardar|grabar|domicili(?:ar|a|e)|transferir|comprar|submit|send|sign|pay|accept|confirm|"
    r"delete|register|apply|order|buy|finish)\b"
)
# Words that point at a credential or payment field, in a selector or in a field name.
_CREDENCIAL = re.compile(
    r"(passw|contrasen|contraseñ|clave[_-]?(?:de[_-]?)?acceso|certificad|secret|tarjeta|"
    r"(?<![a-z])(?:pwd|pin|otp|cvv|cvc|card|iban|token)(?![a-z]))",
    re.IGNORECASE,
)
# Selector engines other than CSS are refused (no text=, xpath=, js=, internal:…): CSS is enough and is easy
# to review; ``>>`` chaining is refused for the same reason.
_MOTOR = re.compile(r"^\s*(?:[a-z_-]+=|internal:|//)", re.IGNORECASE)


def normalizar_texto(texto: str) -> str:
    """Lower case, no accents, single spaces: how literal texts are compared."""
    texto = " ".join((texto or "").split()).lower()
    for a, b in zip("áéíóúüñ", "aeiouun", strict=True):
        texto = texto.replace(a, b)
    return texto


def cambia_estado(texto: str) -> bool:
    """True when a control with this visible text would submit, sign, pay, accept or delete something."""
    return bool(_CAMBIA_ESTADO.search(normalizar_texto(texto)))


def parece_credencial(texto: str) -> bool:
    return bool(_CREDENCIAL.search(texto or ""))


def comprobar_selector(selector: str) -> str:
    """Validate a CSS selector that comes from a script (or from a repair proposal); return it unchanged."""
    if not isinstance(selector, str) or not selector.strip() or len(selector) > MAX_SELECTOR:
        raise ValueError(f"selector vacío o de más de {MAX_SELECTOR} caracteres")
    if _MOTOR.match(selector) or ">>" in selector:
        raise ValueError(f"solo se admiten selectores CSS simples: {selector!r}")
    if any(c in selector for c in "\n\r\x00{}"):
        raise ValueError(f"el selector tiene caracteres no permitidos: {selector!r}")
    if parece_credencial(selector) or re.search(r"type\s*=\s*['\"]?password", selector, re.IGNORECASE):
        raise ValueError(f"el selector apunta a un campo de credenciales o de pago: {selector!r}")
    return selector


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Parametro(_M):
    """A value the USER gives for a run (dates, a CUPS, a text of the procedure). Never a credential."""

    nombre: str
    tipo: Literal["fecha", "texto", "cups", "opcion"]
    etiqueta: str = Field(min_length=1)
    obligatorio: bool = True
    max_len: int = Field(default=200, ge=1, le=MAX_VALOR)
    patron: str | None = None
    opciones: list[str] = Field(default_factory=list)
    # Personal data (a CUPS, a name): masked wherever the run is described (audit, UI step texts).
    personal: bool = False

    @field_validator("nombre")
    @classmethod
    def _nombre(cls, v: str) -> str:
        if not _ID.match(v) or parece_credencial(v):
            raise ValueError(f"nombre de parámetro no válido: {v!r}")
        return v

    @field_validator("patron")
    @classmethod
    def _patron(cls, v: str | None) -> str | None:
        if v is not None:
            from hacienda_gpt.ops.formatos import patron_seguro

            patron_seguro(v)
        return v


class _Paso(_M):
    id: str
    # Spanish, shown live to the user ("Elegir el suministro").
    descripcion: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError(f"id de paso no válido: {v!r}")
        return v


class _ConSelector(_Paso):
    selector: str

    @field_validator("selector")
    @classmethod
    def _selector(cls, v: str) -> str:
        return comprobar_selector(v)


class PasoIr(_Paso):
    accion: Literal["ir"] = "ir"
    origen: str
    ruta: str = Field(pattern=r"^/[^\s]*$", max_length=400)


class PasoEsperar(_ConSelector):
    accion: Literal["esperar"] = "esperar"
    texto: str | None = None


class PasoRellenar(_ConSelector):
    accion: Literal["rellenar"] = "rellenar"
    # The field's ``name`` attribute and the literal text of its label: both must match at run time.
    campo: str = Field(min_length=1, max_length=100)
    etiqueta: str = Field(min_length=1, max_length=200)
    valor: str = Field(max_length=MAX_VALOR)
    # strftime format for ``fecha`` parameters (an <input type=date> wants %Y-%m-%d).
    formato_fecha: str = "%Y-%m-%d"

    @field_validator("campo")
    @classmethod
    def _campo(cls, v: str) -> str:
        if parece_credencial(v):
            raise ValueError(f"el campo {v!r} parece de credenciales o de pago: el agente no lo rellena")
        return v


class PasoSeleccionar(PasoRellenar):
    accion: Literal["seleccionar"] = "seleccionar"  # type: ignore[assignment]


class PasoPulsar(_ConSelector):
    accion: Literal["pulsar"] = "pulsar"
    # Literal visible text of the control.
    texto: str = Field(min_length=1, max_length=120)


class PasoDescargar(PasoPulsar):
    accion: Literal["descargar"] = "descargar"  # type: ignore[assignment]


class Resumen(_M):
    """Where the review page lists what would be sent: rows, and the label and value inside each row."""

    filas: str
    etiqueta: str
    valor: str

    @field_validator("filas", "etiqueta", "valor")
    @classmethod
    def _selector(cls, v: str) -> str:
        return comprobar_selector(v)


class PasoDetener(PasoPulsar):
    """The control that would submit/sign/pay: located and checked, NEVER clicked."""

    accion: Literal["detener"] = "detener"  # type: ignore[assignment]
    resumen: Resumen | None = None


Paso = Annotated[
    PasoIr | PasoEsperar | PasoRellenar | PasoSeleccionar | PasoPulsar | PasoDescargar | PasoDetener,
    Field(discriminator="accion"),
]


class Login(_M):
    """Where the user logs in, and how the agent knows they did. The agent never touches this page."""

    origen: str
    ruta: str = Field(pattern=r"^/[^\s]*$")
    # Visible only once the user is logged in.
    hecho_cuando: str
    espera_max_s: int = Field(default=600, ge=5, le=1800)
    # Other origins the login may go through (e.g. the Cl@ve gateway): reachable during the login only.
    origenes_login: list[str] = Field(default_factory=list)

    @field_validator("hecho_cuando")
    @classmethod
    def _selector(cls, v: str) -> str:
        return comprobar_selector(v)


class Descarga(_M):
    extensiones: list[str] = Field(min_length=1)
    max_bytes: int = Field(gt=0, le=20_000_000)
    # The module tool the file is handed to (through the module tool path), if any.
    destino: Literal["luz_importar_consumo"] | None = None

    @field_validator("extensiones")
    @classmethod
    def _ext(cls, v: list[str]) -> list[str]:
        for ext in v:
            if not re.fullmatch(r"\.[a-z0-9]{1,6}", ext):
                raise ValueError(f"extensión no válida: {ext!r}")
            if ext in {".exe", ".msi", ".bat", ".cmd", ".sh", ".js", ".html", ".htm", ".svg", ".jar", ".app"}:
                raise ValueError(f"extensión ejecutable no admitida: {ext}")
        return v


class PeticionPermitida(_M):
    """A non-GET request the agent's phase may make (a read-only search API that uses POST)."""

    metodo: Literal["POST"]
    origen: str
    ruta_prefijo: str = Field(pattern=r"^/[^\s]*$")


class RangoFechas(_M):
    desde: str
    hasta: str
    max_dias: int = Field(ge=1, le=800)


class Guion(_M):
    titulo: str = Field(min_length=1)
    descripcion: str = Field(min_length=1)
    # False until somebody ran it against the real site: the UI says so.
    verificado_contra_sitio_real: bool = False
    nota_verificacion: str = ""
    # A demonstration script for a mock site: only offered when its origins are substituted by local ones.
    demostracion: bool = False
    # name -> origin ("https://datadis.es"). The allow-list of the run is made of these and nothing else.
    origenes: dict[str, str] = Field(min_length=1)
    login: Login
    parametros: list[Parametro] = Field(default_factory=list)
    pasos: list[Paso] = Field(min_length=1, max_length=MAX_PASOS)
    descarga: Descarga | None = None
    peticiones_permitidas: list[PeticionPermitida] = Field(default_factory=list)
    rango_fechas: RangoFechas | None = None
    # Extra selectors masked in every stored or diagnostic screenshot (inputs are always masked).
    enmascarar: list[str] = Field(default_factory=list)
    # Pause between steps, to behave like a person and not hammer the site.
    pausa_ms: int = Field(default=800, ge=0, le=10_000)

    @field_validator("origenes")
    @classmethod
    def _origenes(cls, v: dict[str, str]) -> dict[str, str]:
        for nombre, origen in v.items():
            if not _ID.match(nombre):
                raise ValueError(f"nombre de origen no válido: {nombre!r}")
            partes = urlsplit(origen)
            if partes.scheme != "https" or not partes.hostname or partes.path not in ("", "/") or partes.query:
                raise ValueError(f"el origen {nombre} debe ser https://host sin ruta: {origen!r}")
        return {nombre: origen.rstrip("/") for nombre, origen in v.items()}

    @field_validator("enmascarar")
    @classmethod
    def _enmascarar(cls, v: list[str]) -> list[str]:
        return [comprobar_selector(s) for s in v]

    @model_validator(mode="after")
    def _coherente(self) -> Guion:
        problemas: list[str] = []
        nombres = {p.nombre for p in self.parametros}
        if len(nombres) != len(self.parametros):
            problemas.append("parámetros repetidos")
        ids = [p.id for p in self.pasos]
        if len(set(ids)) != len(ids):
            problemas.append("ids de paso repetidos")
        for origen in [self.login.origen, *self.login.origenes_login]:
            if origen not in self.origenes:
                problemas.append(f"origen no declarado en el login: {origen}")
        for peticion in self.peticiones_permitidas:
            if peticion.origen not in self.origenes:
                problemas.append(f"origen no declarado en peticiones_permitidas: {peticion.origen}")
        for i, paso in enumerate(self.pasos):
            if isinstance(paso, PasoIr) and paso.origen not in self.origenes:
                problemas.append(f"{paso.id}: origen no declarado {paso.origen}")
            if isinstance(paso, PasoRellenar):
                for nombre in _PLANTILLA.findall(paso.valor):
                    if nombre not in nombres:
                        problemas.append(f"{paso.id}: el valor usa un parámetro no declarado: {nombre}")
            if isinstance(paso, PasoDetener):
                if i != len(self.pasos) - 1:
                    problemas.append(f"{paso.id}: «detener» solo puede ser el último paso")
                if not cambia_estado(paso.texto):
                    problemas.append(f"{paso.id}: «detener» debe señalar un botón que envía, firma o paga")
            elif isinstance(paso, PasoPulsar):  # pulsar and descargar
                if cambia_estado(paso.texto):
                    problemas.append(
                        f"{paso.id}: el texto «{paso.texto}» es de un botón que cambia algo; "
                        "solo puede ser el paso «detener», que nunca lo pulsa"
                    )
            if isinstance(paso, PasoDescargar):
                if self.descarga is None:
                    problemas.append(f"{paso.id}: hay una descarga pero el guion no declara «descarga»")
                if i != len(self.pasos) - 1:
                    problemas.append(f"{paso.id}: la descarga debe ser el último paso")
        ultimo = self.pasos[-1]
        if not isinstance(ultimo, PasoDescargar | PasoDetener):
            problemas.append("el último paso debe ser «descargar» o «detener»")
        if self.rango_fechas is not None:
            for nombre in (self.rango_fechas.desde, self.rango_fechas.hasta):
                if not any(p.nombre == nombre and p.tipo == "fecha" for p in self.parametros):
                    problemas.append(f"rango_fechas usa {nombre}, que no es un parámetro de tipo fecha")
        if problemas:
            raise ValueError("; ".join(problemas))
        return self

    # ------------------------------------------------------------------ #

    def campos(self) -> list[tuple[str, str]]:
        """(field name, label) of every field the script touches: what a repair must keep identical."""
        return [(p.campo, p.etiqueta) for p in self.pasos if isinstance(p, PasoRellenar)]

    def forma(self) -> list[dict[str, Any]]:
        """The script without its selectors: step ids, actions, fields, labels and texts, in order.

        A repair may change selectors and nothing else, so a candidate is accepted only if its ``forma`` is
        identical to the one in force (see :func:`hacienda_gpt.capacidades.navegador.vigilancia.ensayar_guion`).
        """
        out: list[dict[str, Any]] = []
        for paso in self.pasos:
            datos = paso.model_dump(mode="json")
            datos.pop("selector", None)
            if isinstance(datos.get("resumen"), dict):
                datos["resumen"] = "presente"
            out.append(datos)
        return out


class SpecGuion(_M):
    """A script file: same envelope as a format spec."""

    fuente: str
    version: int = Field(ge=1)
    descripcion: str = ""
    origen: str = "incorporada"
    reglas: Guion


class GuionError(ValueError):
    """A script that cannot be used, or parameters that do not fit it (message in Spanish)."""


# --------------------------------------------------------------------------- #
# Loading (bundled, or a reviewed repair under FORMAT_DIR/specs)
# --------------------------------------------------------------------------- #


def fuente_de_guion(clave: str) -> str:
    return f"{PREFIJO_FUENTE}{clave}"


def _leer(path: Path) -> SpecGuion | None:
    try:
        texto = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return SpecGuion.model_validate(json.loads(texto))


def claves_incorporadas() -> list[str]:
    return sorted(p.stem for p in GUIONES_INCORPORADOS.glob("*.json"))


def guion_incorporado(clave: str) -> SpecGuion:
    if not _ID.match(clave):
        raise GuionError(f"Tarea desconocida: {clave}.")
    spec = _leer(GUIONES_INCORPORADOS / f"{clave}.json")
    if spec is None:
        raise GuionError(f"Tarea desconocida: {clave}.")
    return spec


def ruta_reparada(clave: str, directorio: str | Path | None = None) -> Path:
    base = Path(directorio) if directorio is not None else Path(settings.FORMAT_DIR)
    return base / "specs" / f"{fuente_de_guion(clave)}.json"


def guion_activo(clave: str, directorio: str | Path | None = None) -> SpecGuion:
    """The script in force: a reviewed repair with a higher version, else the bundled one.

    An unreadable or invalid repair never breaks a run: it is logged and the bundled script is used.
    """
    incorporado = guion_incorporado(clave)
    try:
        reparado = _leer(ruta_reparada(clave, directorio))
    except (ValueError, ValidationError) as exc:
        logger.warning("Repaired browser script %s is invalid, using the bundled one: %s", clave, exc)
        return incorporado
    if reparado is None or reparado.fuente != incorporado.fuente or reparado.version <= incorporado.version:
        return incorporado
    if reparado.reglas.forma() != incorporado.reglas.forma() or reparado.reglas.origenes != incorporado.reglas.origenes:
        logger.warning("Repaired browser script %s changes more than selectors: ignored", clave)
        return incorporado
    return reparado


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #

_CUPS = re.compile(r"^ES\d{16}[A-Z]{2}(\d[A-Z])?$")


def validar_parametros(guion: Guion, crudos: dict[str, Any], *, hoy: date | None = None) -> dict[str, Any]:
    """The user's parameters, validated against the script. Raises :class:`GuionError` (Spanish)."""
    if not isinstance(crudos, dict):
        raise GuionError("Los parámetros deben ser un objeto.")
    declarados = {p.nombre: p for p in guion.parametros}
    sobran = sorted(set(crudos) - set(declarados))
    if sobran:
        raise GuionError(f"Parámetros que esta tarea no usa: {', '.join(sobran)}.")
    valores: dict[str, Any] = {}
    for p in guion.parametros:
        crudo = crudos.get(p.nombre)
        if crudo is None or (isinstance(crudo, str) and not crudo.strip()):
            if p.obligatorio:
                raise GuionError(f"Falta «{p.etiqueta}».")
            valores[p.nombre] = None
            continue
        if p.tipo == "fecha":
            try:
                valores[p.nombre] = crudo if isinstance(crudo, date) else date.fromisoformat(str(crudo).strip())
            except ValueError as exc:
                raise GuionError(f"«{p.etiqueta}» debe ser una fecha AAAA-MM-DD.") from exc
            continue
        texto = str(crudo).strip()
        if len(texto) > p.max_len or any(ord(c) < 32 for c in texto):
            raise GuionError(f"«{p.etiqueta}» es demasiado largo o tiene caracteres no válidos.")
        if p.tipo == "cups":
            texto = re.sub(r"\s+", "", texto).upper()
            if not _CUPS.match(texto):
                raise GuionError("El CUPS tiene la forma ES + 16 cifras + 2 letras (a veces dos caracteres más).")
        if p.tipo == "opcion" and texto not in p.opciones:
            raise GuionError(f"«{p.etiqueta}» debe ser una de: {', '.join(p.opciones)}.")
        if p.patron is not None and not re.fullmatch(p.patron, texto):
            raise GuionError(f"«{p.etiqueta}» no tiene el formato esperado.")
        valores[p.nombre] = texto
    if guion.rango_fechas is not None:
        desde, hasta = valores.get(guion.rango_fechas.desde), valores.get(guion.rango_fechas.hasta)
        if isinstance(desde, date) and isinstance(hasta, date):
            if hasta < desde:
                raise GuionError("La fecha final es anterior a la inicial.")
            if (hasta - desde).days + 1 > guion.rango_fechas.max_dias:
                raise GuionError(f"El periodo pedido supera {guion.rango_fechas.max_dias} días.")
            if hoy is not None and hasta > hoy:
                raise GuionError("La fecha final no puede ser posterior a hoy.")
    return valores


def renderizar(plantilla: str, valores: dict[str, Any], formato_fecha: str = "%Y-%m-%d") -> str:
    """Fill ``{parametro}`` placeholders (no attribute access, no format specs: a plain substitution)."""

    def sustituir(m: re.Match[str]) -> str:
        valor = valores.get(m.group(1))
        if valor is None:
            return ""
        if isinstance(valor, date | datetime):
            return valor.strftime(formato_fecha)
        return str(valor)

    return _PLANTILLA.sub(sustituir, plantilla)


def enmascarar_valor(valor: Any) -> str:
    """How a personal value is shown in step texts: only its last four characters."""
    texto = str(valor or "")
    return "•" * max(0, min(len(texto) - 4, 8)) + texto[-4:] if len(texto) > 4 else "••••"


__all__ = [
    "GUIONES_INCORPORADOS",
    "Descarga",
    "Guion",
    "GuionError",
    "Login",
    "Parametro",
    "Paso",
    "PasoDescargar",
    "PasoDetener",
    "PasoEsperar",
    "PasoIr",
    "PasoPulsar",
    "PasoRellenar",
    "PasoSeleccionar",
    "SpecGuion",
    "cambia_estado",
    "claves_incorporadas",
    "comprobar_selector",
    "enmascarar_valor",
    "fuente_de_guion",
    "guion_activo",
    "guion_incorporado",
    "normalizar_texto",
    "parece_credencial",
    "renderizar",
    "ruta_reparada",
    "validar_parametros",
]
