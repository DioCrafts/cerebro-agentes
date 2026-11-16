"""Where every module declares its legal data, so that the maintenance loop covers it without being told.

Each module's checker file declares its legal data files as ``FICHEROS_LEGALES`` — a tuple of
:class:`FicheroLegal`: where the JSON lives, how to load it, and the adapter that turns it into
:class:`~asistente.conocimiento.modelo.GrupoLegal` objects (plus the checks only that module knows how to do).
Nobody lists them by hand: every domain hands its files to the core in ``Ganchos.ficheros_legales``
(``modulos/ganchos.py``) and every capability in its ``ficheros_legales`` attribute (``capacidades/contrato.py``),
both found by package discovery; :data:`DECLARANTES` only names the core's own files that belong to neither (the
cross-domain planner); :func:`registrar` adds more at run time.

From that declaration, with nothing else to wire:

* ``params-verify`` re-verifies every group against today's BOE (:func:`verificar_fichero`);
* ``params-check`` compares the BOE's update date of each law with the reading date the file records
  (``fuentes_principales[*].consultado_el``), and ``params-verify``/``params-autopilot`` record a new reading in the
  file when every group that quotes the law still matches (:mod:`asistente.conocimiento.recodificacion.procedencia`), so
  the weekly pull request of readings (``params-lecturas``) carries it;
* the files that declare a re-encoding (:class:`Recodificable`) join the agentic loop
  (:mod:`asistente.conocimiento.recodificacion.de_modulos`); the others say why a person keeps them (``solo_persona``).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
import importlib
import json
import logging
from pathlib import Path
import re
from typing import Any

from asistente.conocimiento.comprobacion import SIN_TEXTO, Comprobacion, Resultado, comprobar_grupos
from asistente.conocimiento.modelo import GrupoLegal, Ortografia
from asistente.conocimiento.texto import TextoDe, texto_de_html

logger = logging.getLogger(__name__)
_ES_HTML = re.compile(r"<(?:html|body|p|div|h\d|table|tr|pre)\b", re.IGNORECASE)

# The package root: files are named relative to it in reports ("modulos/luz/data/regulados.json").
RAIZ = Path(__file__).resolve().parents[1]
# A file's name is an IDENTIFIER, stored in the reading log, the re-encoding state and its proposals: it keeps the
# spelling it had under the old package layout (``hacienda_gpt/modulos/<domain>/…``) although the domains now live
# in ``asistente/ambitos/<domain>/…``. Prefix of the path below :data:`RAIZ` -> prefix of the stored name.
NOMBRES_ESTABLES: tuple[tuple[str, str], ...] = (("ambitos/", "modulos/"),)


def nombre_estable(relativa: str) -> str:
    """The stored name of a data file whose path below :data:`RAIZ` is ``relativa`` (POSIX)."""
    for actual, historico in NOMBRES_ESTABLES:
        if relativa.startswith(actual):
            return historico + relativa[len(actual) :]
    return relativa


# The core's own files that declare ``FICHEROS_LEGALES`` and belong to no domain or capability (the domains' and
# capabilities' files are found through their declarations: :func:`_de_ambitos`, :func:`_de_capacidades`).
DECLARANTES: tuple[str, ...] = ("asistente.nucleo.planificador.textos",)


@dataclass(frozen=True)
class Recodificable:
    """What the agentic loop needs to re-encode a module's groups (see :mod:`asistente.conocimiento.recodificacion.de_modulos`).

    ``familia`` builds, lazily, the :class:`~asistente.conocimiento.recodificacion.adaptadores_otros.Familia` of the file:
    its root model, how its figures are printed, its self-check and the calculators a reviewer's test case runs
    through.
    """

    familia: Callable[[], Any]


@dataclass(frozen=True)
class FicheroLegal:
    """One file of legal data of one module (or a set of passages kept in code)."""

    clave: str
    # Household module it belongs to ("luz", "caducidades"…; "fiscal" for the tax module's passages).
    modulo: str
    # Spanish, for reports.
    titulo: str
    # The JSON file; None for passages kept in code (they have no reading dates to record).
    ruta: Path | None
    # Raw JSON (None when ``ruta`` is None) -> the validated data.
    cargar: Callable[[Any], Any]
    # Validated data -> its groups.
    grupos: Callable[[Any], list[GrupoLegal]]
    ortografia: Ortografia
    # Validated data -> keys of the sources it lists (``<BOE-ID>`` or ``<BOE-ID>@<version>``); None: not checked.
    fuentes: Callable[[Any], Collection[str]] | None = None
    # Checks only this module knows how to do: (data, texts or None) -> problems, prefixed like the groups'.
    propias: Callable[[Any, Mapping[str, str] | None], list[str]] | None = None
    # Drops the module's caches of this file after it has been written.
    limpiar: Callable[[], None] = field(default=lambda: None)
    recodificable: Recodificable | None = None
    # Why the loop leaves this file to a person (when it is not ``recodificable``).
    solo_persona: str = ""

    @property
    def nombre(self) -> str:
        """Name in reports and in the reading log: the path below the package ("modulos/luz/data/regulados.json")."""
        if self.ruta is None:
            return f"{self.clave} (citas en el código)"
        try:
            return nombre_estable(self.ruta.resolve().relative_to(RAIZ).as_posix())
        except ValueError:
            return self.ruta.name

    def leer_crudo(self) -> Any:
        return json.loads(self.ruta.read_text(encoding="utf-8")) if self.ruta is not None else None

    def leer(self) -> Any:
        return self.cargar(self.leer_crudo())

    def problemas_por_grupo(self, datos: Any = None, textos: Mapping[str, str] | None = None) -> dict[str, list[str]]:
        """Problems of every group (the shared checker plus the module's own checks), by group name."""
        datos = self.leer() if datos is None else datos
        grupos = self.grupos(datos)
        por_grupo = comprobar_grupos(
            grupos,
            ortografia=self.ortografia,
            fuentes=self.fuentes(datos) if self.fuentes else None,
            textos=textos,
        )
        if self.propias is not None:
            nombres = sorted(por_grupo, key=len, reverse=True)
            for problema in self.propias(datos, textos):
                suyo = next((n for n in nombres if problema.startswith((n + ":", n + ".", n + " "))), "")
                por_grupo.setdefault(suyo, []).append(problema)
        return por_grupo

    def comprobar(self, datos: Any = None, textos: Mapping[str, str] | None = None) -> list[str]:
        """Every problem of the file (empty: its data is consistent with the text it claims to come from)."""
        return [p for problemas in self.problemas_por_grupo(datos, textos).values() for p in problemas]


_REGISTRADOS: dict[str, FicheroLegal] = {}


def registrar(fichero: FicheroLegal) -> FicheroLegal:
    """Add (or replace, by ``clave``) a file declared outside :data:`DECLARANTES`."""
    _REGISTRADOS[fichero.clave] = fichero
    return fichero


def _de_ambitos() -> list[FicheroLegal]:
    """Every domain package's ``Ganchos.ficheros_legales`` (read through the v1 adapter for a v1 domain).

    Found by the registry's package discovery, not through the cached registry: an operator's switch, a domain
    refused by validation or a registry replaced by a test never hides legal data from the weekly verification.
    """
    from asistente.nucleo.modulos.ganchos import ganchos_de
    from asistente.nucleo.modulos.registro import descubrir

    encontrados, errores = descubrir()
    for nombre, motivo in errores.items():
        logger.warning("The legal data of domain %s could not be read: %s", nombre, motivo)
    out: list[FicheroLegal] = []
    for _origen, modulo in encontrados:
        try:
            out += [f for f in ganchos_de(modulo).ficheros_legales if isinstance(f, FicheroLegal)]
        except Exception:  # one broken declaration must not hide the others
            logger.exception("Could not read the legal data files of domain %s", _origen)
    return out


def _de_capacidades() -> list[FicheroLegal]:
    """Every capability package's ``ficheros_legales`` (optional attribute)."""
    from asistente.capacidades.registro import descubrir

    encontrados, errores = descubrir()
    for nombre, motivo in errores.items():
        logger.warning("The legal data of capability %s could not be read: %s", nombre, motivo)
    out: list[FicheroLegal] = []
    for _origen, capacidad in encontrados:
        out += [f for f in getattr(capacidad, "ficheros_legales", ()) or () if isinstance(f, FicheroLegal)]
    return out


@lru_cache(maxsize=1)
def _declarados() -> tuple[FicheroLegal, ...]:
    encontrados: list[FicheroLegal] = [*_de_ambitos(), *_de_capacidades()]
    for nombre in DECLARANTES:
        try:
            modulo = importlib.import_module(nombre)
        except ModuleNotFoundError as exc:  # pragma: no cover - a module removed from the installation
            if exc.name != nombre:
                raise
            logger.warning("The legal data of %s is not installed", nombre)
            continue
        encontrados += list(getattr(modulo, "FICHEROS_LEGALES", ()))
    # A file declared twice (a domain and the capability it moved to) counts once: the first declaration wins.
    unicos: dict[str, FicheroLegal] = {}
    for fichero in encontrados:
        unicos.setdefault(fichero.clave, fichero)
    return tuple(unicos.values())


def ficheros_legales() -> list[FicheroLegal]:
    """Every declared file of legal data, in declaration order (those registered at run time replace by ``clave``)."""
    por_clave = {f.clave: f for f in _declarados()}
    por_clave.update(_REGISTRADOS)
    return list(por_clave.values())


def fichero_legal(clave: str) -> FicheroLegal:
    for fichero in ficheros_legales():
        if fichero.clave == clave:
            return fichero
    raise KeyError(f"No hay ningún fichero de datos legales «{clave}».")


def con_datos(ficheros: Iterable[FicheroLegal] | None = None) -> list[FicheroLegal]:
    """The declared files that are JSON data files (they record reading dates)."""
    return [f for f in (ficheros_legales() if ficheros is None else ficheros) if f.ruta is not None]


def en_directorio(fichero: FicheroLegal, directorio: Path) -> FicheroLegal:
    """The same declaration over a copy of its file in ``directorio`` (for tests and dry runs)."""
    if fichero.ruta is None:
        return fichero
    return replace(fichero, ruta=directorio / fichero.ruta.name)


# --------------------------------------------------------------------------- #
# Weekly re-verification
# --------------------------------------------------------------------------- #


def textos_para(grupos: Iterable[GrupoLegal], texto_de: TextoDe) -> dict[str, str]:
    """Plain text of every law (and version) the groups quote, from ``texto_de``; a law it cannot read is left out."""
    pedidos: dict[str, tuple[str, str | None]] = {}
    for grupo in grupos:
        for cita in grupo.citas:
            pedidos.setdefault(cita.clave_texto, (cita.boe_id, cita.version))
    textos: dict[str, str] = {}
    for clave, (boe_id, version) in pedidos.items():
        pagina = texto_de(boe_id, version)
        if pagina:
            if version is None:
                from asistente.conocimiento import formatos

                formatos.observar("boe_consolidado_html", pagina, contexto=boe_id)
            # A page from boe.es is reduced to its text, one paragraph per line; an extract kept as text stays as it is.
            textos[clave] = texto_de_html(pagina) if _ES_HTML.search(pagina) else pagina
    return textos


def verificar_fichero(fichero: FicheroLegal, texto_de: TextoDe, *, datos: Any = None) -> list[Resultado]:
    """One result per group of ``fichero``: its passages verbatim in today's text and its figures in its passages.

    The same checker the module's tests run on extracts (:meth:`FicheroLegal.comprobar`), over the pages
    ``texto_de`` returns. A group whose law cannot be read fails with ``["sin texto"]``.
    """
    datos = fichero.leer() if datos is None else datos
    grupos = [g for g in fichero.grupos(datos) if g.citas]
    textos = textos_para(grupos, texto_de)
    por_grupo = fichero.problemas_por_grupo(datos, textos)
    resultados: list[Resultado] = []
    for grupo in grupos:
        primera = grupo.citas[0]
        check = Comprobacion(
            boe_id=grupo.fuente.boe_id or primera.boe_id,
            version=grupo.fuente.version if grupo.fuente.boe_id else primera.version,
            ruta=grupo.ruta,
            descripcion=f"{fichero.titulo}: {grupo.descripcion or grupo.nombre}",
            tipo="cita",
            citas=[c.cita for c in grupo.citas],
            cortes=any(c.cortes for c in grupo.citas),
        )
        if any(c.clave_texto not in textos for c in grupo.citas):
            resultados.append(Resultado(comprobacion=check, ok=False, faltan=[SIN_TEXTO]))
            continue
        faltan = por_grupo.get(grupo.nombre, [])
        resultados.append(Resultado(comprobacion=check, ok=not faltan, faltan=faltan))
    # Problems of the module's own checks that belong to no group (a whole-file rule) fail the file's first group.
    sueltos = [] if any(g.nombre == "" for g in grupos) else por_grupo.get("", [])
    if sueltos and resultados:
        primero = resultados[0]
        resultados[0] = primero.model_copy(update={"ok": False, "faltan": [*primero.faltan, *sueltos]})
    return resultados


def verificar_modulos(
    texto_de: TextoDe, *, claves: Collection[str] | None = None, ficheros: Iterable[FicheroLegal] | None = None
) -> dict[str, list[Resultado]]:
    """Every declared file (or those of ``claves``): file name -> one result per group."""
    out: dict[str, list[Resultado]] = {}
    for fichero in ficheros_legales() if ficheros is None else ficheros:
        if claves and fichero.clave not in claves and fichero.modulo not in claves:
            continue
        try:
            out[fichero.nombre] = verificar_fichero(fichero, texto_de)
        except Exception as exc:  # one broken module must not hide the others' report
            logger.exception("Could not verify %s", fichero.nombre)
            check = Comprobacion(boe_id="-", ruta=[], descripcion=f"{fichero.titulo}: no se pudo cargar", tipo="cita")
            out[fichero.nombre] = [Resultado(comprobacion=check, ok=False, faltan=[f"{type(exc).__name__}: {exc}"])]
    return out


# --------------------------------------------------------------------------- #
# Reading dates
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LecturaDeModulo:
    """When a module's file read a law: what ``params-check`` compares with the BOE's update date."""

    modulo: str
    fichero: str
    boe_id: str
    norma: str
    url: str
    consolidado_a: str
    consultado_el: str
    # Not the current consolidated text (the daily BOE, a version pinned to a date): it does not change.
    fijada: bool


def lecturas_de_modulos(ficheros: Iterable[FicheroLegal] | None = None) -> list[LecturaDeModulo]:
    """Every reading of a law recorded in a module's data file (``boe_id`` with ``consolidado_a`` and
    ``consultado_el``, wherever it sits in the file)."""
    from asistente.conocimiento.recodificacion.procedencia import fuentes_del_fichero

    out: list[LecturaDeModulo] = []
    for fichero in con_datos(ficheros):
        try:
            raw = fichero.leer_crudo()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s: %s", fichero.nombre, exc)
            continue
        for fuente in fuentes_del_fichero(fichero.nombre, raw):
            nodo: Any = raw
            for paso in fuente.ruta:
                nodo = nodo[int(paso)] if isinstance(nodo, list) else nodo[paso]
            out.append(
                LecturaDeModulo(
                    modulo=fichero.modulo,
                    fichero=fichero.nombre,
                    boe_id=fuente.boe_id,
                    norma=fuente.norma,
                    url=str(nodo.get("url") or ""),
                    consolidado_a=fuente.consolidado_a,
                    consultado_el=fuente.consultado_el,
                    fijada=fuente.fijada,
                )
            )
    return out


__all__ = [
    "DECLARANTES",
    "FicheroLegal",
    "LecturaDeModulo",
    "Recodificable",
    "con_datos",
    "en_directorio",
    "fichero_legal",
    "ficheros_legales",
    "lecturas_de_modulos",
    "registrar",
    "textos_para",
    "verificar_fichero",
    "verificar_modulos",
]
