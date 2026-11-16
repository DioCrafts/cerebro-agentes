"""What the browser may reach and touch: the network policy, the element checks and the redaction.

Everything here is deterministic and independent of Playwright, so every rule has a unit test; the
executor (``ejecutor.py``) applies them through route interception and before each action.

Network policy, per run:

* only the script's declared origins (scheme + host + port) are reachable; any other request — a tracker, a
  CDN, a redirect to another site, a link the page offers — is aborted by the browser context itself;
* while the USER logs in (phase ``login``) any method is allowed on those origins: it is the user who submits
  the login form, in a window they control;
* while the AGENT works (phase ``agente``) only GET/HEAD requests are allowed, plus the POST prefixes the
  script declares for read-only APIs: even a click that slipped past every other check could not submit a
  form, because the POST it would cause never leaves the browser;
* after the agent has stopped before submitting, in a window the user controls, the page is handed over to
  the user (phase ``usuario``): the agent no longer runs anything, and the user may submit it themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from asistente.capacidades.navegador.guion import (
    Descarga,
    Guion,
    cambia_estado,
    normalizar_texto,
    parece_credencial,
)

Fase = Literal["login", "agente", "usuario", "cerrada"]
METODOS_LECTURA = frozenset({"GET", "HEAD", "OPTIONS"})
_ESQUEMAS_INTERNOS = ("data:", "blob:", "about:", "chrome-error:")
# Origins a substitution may point to: only this machine (local mock sites for tests and demos).
_HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})


class PoliticaError(ValueError):
    """A configuration that would weaken the policy (message in Spanish)."""


def origen_de(url: str) -> str | None:
    """``scheme://host[:port]`` of a URL with explicit default ports removed; None when it has no host."""
    try:
        partes = urlsplit(url)
        puerto = partes.port
    except ValueError:
        return None
    if not partes.scheme or not partes.hostname:
        return None
    esquema = partes.scheme.lower()
    host = partes.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if puerto is None or (esquema, puerto) in (("https", 443), ("http", 80)):
        return f"{esquema}://{host}"
    return f"{esquema}://{host}:{puerto}"


def sustitutos_validos(crudo: str | dict[str, str] | None) -> dict[str, str]:
    """Parse ``NAVEGADOR_ORIGENES_SUSTITUTOS``: real origin -> LOCAL origin (mock sites).

    Only loopback targets are accepted, so a misconfiguration can never point a script at a third site.
    """
    if not crudo:
        return {}
    datos = json.loads(crudo) if isinstance(crudo, str) else crudo
    if not isinstance(datos, dict):
        raise PoliticaError("NAVEGADOR_ORIGENES_SUSTITUTOS debe ser un objeto JSON {origen real: origen local}.")
    out: dict[str, str] = {}
    for real, local in datos.items():
        o_real, o_local = origen_de(str(real)), origen_de(str(local))
        if o_real is None or o_local is None:
            raise PoliticaError(f"Sustitución no válida: {real!r} -> {local!r}.")
        host = urlsplit(o_local).hostname or ""
        if host not in _HOSTS_LOCALES:
            raise PoliticaError(f"Solo se puede sustituir un origen por uno local (127.0.0.1/localhost): {local!r}.")
        out[o_real] = o_local
    return out


def origenes_efectivos(guion: Guion, sustitutos: dict[str, str]) -> dict[str, str]:
    """The script's origins after substitution (name -> origin actually visited)."""
    return {nombre: sustitutos.get(origen_de(o) or o, origen_de(o) or o) for nombre, o in guion.origenes.items()}


@dataclass
class PoliticaRed:
    """The allow-list of one run. ``fase`` changes as the run goes; everything else is fixed."""

    origenes: frozenset[str]
    # (origin, path prefix) where the agent may POST (read-only APIs the script declares).
    posts_permitidos: tuple[tuple[str, str], ...] = ()
    fase: Fase = "login"
    # Origins of blocked requests (never the full URL: a query string can carry personal data).
    bloqueadas: list[str] = field(default_factory=list)

    @classmethod
    def para(cls, guion: Guion, origenes: dict[str, str]) -> PoliticaRed:
        posts = tuple((origenes[p.origen], p.ruta_prefijo) for p in guion.peticiones_permitidas)
        return cls(origenes=frozenset(origenes.values()), posts_permitidos=posts)

    def decidir(self, url: str, metodo: str) -> tuple[bool, str]:
        """(allowed, reason). Every request of the browser context goes through here."""
        if url.startswith(_ESQUEMAS_INTERNOS):
            return True, "interno"
        if self.fase == "cerrada":
            return False, "la tarea ha terminado"
        origen = origen_de(url)
        if origen is None or origen not in self.origenes:
            return False, "origen fuera de la lista permitida"
        metodo = metodo.upper()
        if self.fase in ("login", "usuario") or metodo in METODOS_LECTURA:
            return True, "permitido"
        ruta = urlsplit(url).path or "/"
        if metodo == "POST" and any(origen == o and ruta.startswith(p) for o, p in self.posts_permitidos):
            return True, "consulta declarada"
        return False, f"el agente no envía datos ({metodo}): solo consulta"

    def anotar_bloqueo(self, url: str) -> str:
        origen = origen_de(url) or "(desconocido)"
        self.bloqueadas.append(origen)
        return origen


def comprobar_descarga(nombre: str, tamano: int, descarga: Descarga | None) -> str | None:
    """None when a downloaded file may be kept; otherwise why not (Spanish)."""
    if descarga is None:
        return "esta tarea no descarga ficheros"
    nombre = (nombre or "").lower()
    if not any(nombre.endswith(ext) for ext in descarga.extensiones):
        return f"tipo de fichero no esperado ({nombre.rsplit('.', 1)[-1] if '.' in nombre else 'sin extensión'})"
    if tamano <= 0:
        return "el fichero está vacío"
    if tamano > descarga.max_bytes:
        return f"el fichero ocupa {tamano} bytes, más de los {descarga.max_bytes} permitidos"
    return None


def texto_de_fichero(datos: bytes) -> str:
    """A downloaded text file as text: UTF-8 (BOM allowed) or Latin-1; binary content is refused."""
    if b"\x00" in datos[:4096]:
        raise ValueError("el fichero descargado no es de texto")
    try:
        return datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        return datos.decode("latin-1")


# --------------------------------------------------------------------------- #
# Element checks (the facts are read from the page by JS_ELEMENTO)
# --------------------------------------------------------------------------- #

# Evaluated on ONE element; returns what the checks below need. Never returns the field's value.
JS_ELEMENTO = """
(el) => {
  const txt = (n) => (n ? (n.innerText || n.textContent || "") : "").replace(/\\s+/g, " ").trim();
  let etiqueta = "";
  if (el.labels && el.labels.length) etiqueta = Array.from(el.labels).map(txt).join(" ");
  if (!etiqueta && el.getAttribute("aria-label")) etiqueta = el.getAttribute("aria-label");
  if (!etiqueta && el.id) {
    const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (l) etiqueta = txt(l);
  }
  const tag = el.tagName.toLowerCase();
  let texto = txt(el);
  if (!texto && tag === "input") texto = el.getAttribute("value") || "";
  const form = el.form || null;
  return {
    tag,
    tipo: (el.getAttribute("type") || (tag === "button" ? "submit" : "")).toLowerCase(),
    tipo_efectivo: (el.type || "").toLowerCase(),
    nombre: el.getAttribute("name") || "",
    id: el.id || "",
    autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
    etiqueta,
    texto,
    en_formulario: !!form,
    metodo_formulario: form ? (form.getAttribute("method") || "get").toLowerCase() : "",
    formaction: el.getAttribute("formaction") || "",
    href: tag === "a" ? (el.href || "") : "",
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
  };
}
"""

_AUTOCOMPLETE_CREDENCIAL = frozenset(
    {"current-password", "new-password", "one-time-code", "cc-number", "cc-csc", "cc-exp", "cc-name"}
)


def es_campo_credencial(info: dict[str, Any]) -> bool:
    """A password, PIN, one-time code, card or certificate field: the agent never types into it."""
    if info.get("tipo") == "password" or info.get("tipo_efectivo") == "password":
        return True
    if info.get("autocomplete") in _AUTOCOMPLETE_CREDENCIAL:
        return True
    return any(parece_credencial(str(info.get(k) or "")) for k in ("nombre", "id"))


def contiene_literal(esperado: str, encontrado: str) -> bool:
    """The expected literal text appears in what the page shows (case, accents and spacing ignored)."""
    return normalizar_texto(esperado) in normalizar_texto(encontrado)


def problemas_campo(info: dict[str, Any], *, campo: str, etiqueta: str, seleccion: bool) -> list[str]:
    """Why a form field must not be filled (empty list: it is the field the script describes)."""
    problemas: list[str] = []
    tags = {"select"} if seleccion else {"input", "textarea"}
    if info.get("tag") not in tags:
        problemas.append(f"no es un campo {'desplegable' if seleccion else 'de texto'} ({info.get('tag')})")
    if es_campo_credencial(info):
        problemas.append("es un campo de contraseña, PIN, código o pago: el agente nunca lo rellena")
    if info.get("tipo_efectivo") in {"submit", "button", "image", "reset", "file", "hidden", "checkbox", "radio"}:
        problemas.append(f"no es un campo de texto ({info.get('tipo_efectivo')})")
    if info.get("nombre") != campo:
        problemas.append(f"el campo se llama «{info.get('nombre')}», no «{campo}»")
    if not contiene_literal(etiqueta, str(info.get("etiqueta") or "")):
        problemas.append(f"su etiqueta es «{info.get('etiqueta')}», no «{etiqueta}»")
    return problemas


def problemas_pulsable(
    info: dict[str, Any], *, texto: str, origenes: Iterable[str], es_parada: bool = False
) -> list[str]:
    """Why a control must not be clicked (``es_parada``: it is only being located, never clicked)."""
    problemas: list[str] = []
    visible = str(info.get("texto") or "")
    if not contiene_literal(texto, visible):
        problemas.append(f"su texto es «{visible[:80]}», no «{texto}»")
    if es_parada:
        if not cambia_estado(visible):
            problemas.append("no es un botón que envíe, firme o pague")
        return problemas
    if cambia_estado(visible):
        problemas.append(f"«{visible[:80]}» cambiaría algo (enviar, firmar, pagar, aceptar…): el agente no lo pulsa")
    if info.get("tipo_efectivo") in {"submit", "image"} and info.get("en_formulario"):
        problemas.append("es un botón que envía un formulario: el agente no lo pulsa")
    if info.get("formaction"):
        problemas.append("el botón envía datos a otra dirección")
    href = str(info.get("href") or "")
    if href and not href.startswith("#") and not href.startswith("javascript:"):
        if origen_de(href) not in set(origenes) and not href.startswith(("blob:", "data:")):
            problemas.append("el enlace lleva fuera de los sitios permitidos")
    return problemas


# --------------------------------------------------------------------------- #
# Redaction: what may leave the run (diagnosis, a repair prompt)
# --------------------------------------------------------------------------- #

# A skeleton of the page's forms and controls: tags, types, names, ids and the texts of labels, legends,
# headings and buttons. NO field value and no other text of the page is read.
JS_ESQUELETO = """
() => {
  const txt = (n) => (n ? (n.innerText || n.textContent || "") : "").replace(/\\s+/g, " ").trim().slice(0, 120);
  const out = [];
  const sel = "h1,h2,h3,legend,label,input,select,textarea,button,a[href],[role=button]";
  for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 300)) {
    const tag = el.tagName.toLowerCase();
    const item = { tag };
    if (el.id) item.id = el.id;
    if (el.getAttribute("name")) item.name = el.getAttribute("name");
    if (el.getAttribute("type")) item.type = el.getAttribute("type");
    if (el.className && typeof el.className === "string") item.class = el.className.slice(0, 80);
    if (["h1", "h2", "h3", "legend", "label", "button", "a"].includes(tag) || el.getAttribute("role") === "button") {
      item.texto = txt(el);
    }
    if (tag === "label" && el.htmlFor) item.for = el.htmlFor;
    if (tag === "select") item.opciones = el.options.length;
    if (tag === "input" && ["submit", "button"].includes((el.type || "").toLowerCase())) {
      item.texto = el.getAttribute("value") || "";
    }
    out.push(item);
  }
  return out;
}
"""

_PATRONES_PERSONALES = (
    re.compile(r"\bES\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?[A-Z]{2}(?:\s?\d[A-Z])?\b", re.IGNORECASE),  # CUPS
    re.compile(r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){5}\b"),  # IBAN
    re.compile(r"\b[0-9XYZxyz]\d{7}[A-Za-z]\b"),  # NIF / NIE
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),  # e-mail
    re.compile(r"\+?\d[\d .-]{6,}\d"),  # phone numbers, long figures
)
_TRAS_SALUDO = re.compile(r"\b(hola|bienvenid[oa]s?|usuario|usuaria|titular|sesi[oó]n de)\b[,:]?\s+.*", re.IGNORECASE)


def redactar_texto(texto: str) -> str:
    """Mask what could be personal data in a label or button text (identifiers, e-mails, numbers, names)."""
    out = texto
    for patron in _PATRONES_PERSONALES:
        out = patron.sub("•••", out)
    return _TRAS_SALUDO.sub(lambda m: m.group(1) + " •••", out)


def redactar_esqueleto(items: Any, *, enmascarar_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The skeleton read by JS_ESQUELETO with every text redacted and masked elements' texts removed."""
    ocultos = {s.lstrip("#") for s in enmascarar_ids if s.startswith("#")}
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        limpio: dict[str, Any] = {}
        for clave in ("tag", "id", "name", "type", "class", "for", "opciones", "texto"):
            if clave not in item:
                continue
            valor = item[clave]
            if isinstance(valor, str):
                valor = redactar_texto(valor)[:120]
            limpio[clave] = valor
        if limpio.get("id") in ocultos and "texto" in limpio:
            limpio["texto"] = "•••"
        out.append(limpio)
    return out


__all__ = [
    "JS_ELEMENTO",
    "JS_ESQUELETO",
    "METODOS_LECTURA",
    "Fase",
    "PoliticaError",
    "PoliticaRed",
    "comprobar_descarga",
    "contiene_literal",
    "es_campo_credencial",
    "origen_de",
    "origenes_efectivos",
    "problemas_campo",
    "problemas_pulsable",
    "redactar_esqueleto",
    "redactar_texto",
    "sustitutos_validos",
    "texto_de_fichero",
]
