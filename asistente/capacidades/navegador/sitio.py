"""Sites a MISSION may run on: where the browser may go, how the user logs in, what is masked. No steps.

A script (``guion.py``) says what to do, step by step, with selectors. A site says only where: the agent of
``agente.py`` chooses the steps over the page's accessibility snapshot, behind the same network policy and element
checks. Declaring a site is therefore much less work than writing a script, and it is still data a person reviews:

* ``origenes`` — the allow-list of the run (https, scheme + host);
* ``login`` — optional: where the USER logs in and how the agent knows they did (a public site has none);
* ``inicio`` — the page the agent starts from;
* ``descarga`` — optional: what kind of file a mission may keep;
* ``enmascarar`` — extra selectors hidden in every screenshot (form fields always are).

Three sources, by precedence: the operator's directory (``NAVEGADOR_SITIOS_DIR``), the bundled ``sitios/`` and
every available script, whose site (origins, login, masks, download) is reused as it is under the script's key.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from asistente.capacidades.navegador.guion import (
    Descarga,
    Guion,
    Login,
    PeticionPermitida,
    SpecGuion,
    comprobar_selector,
)

logger = logging.getLogger(__name__)

SITIOS_INCORPORADOS = Path(__file__).resolve().parent / "sitios"
_ID = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_RUTA = r"^/[^\s]*$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Inicio(_M):
    origen: str
    ruta: str = Field(default="/", pattern=_RUTA, max_length=400)


class Sitio(_M):
    titulo: str = Field(min_length=1, max_length=120)
    descripcion: str = Field(min_length=1, max_length=400)
    verificado_contra_sitio_real: bool = False
    nota_verificacion: str = ""
    # A mock site: only offered when its origins are substituted by local ones.
    demostracion: bool = False
    origenes: dict[str, str] = Field(min_length=1)
    login: Login | None = None
    inicio: Inicio
    descarga: Descarga | None = None
    peticiones_permitidas: list[PeticionPermitida] = Field(default_factory=list)
    enmascarar: list[str] = Field(default_factory=list)
    pausa_ms: int = Field(default=500, ge=0, le=10_000)

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
    def _coherente(self) -> Sitio:
        usados = [self.inicio.origen, *(p.origen for p in self.peticiones_permitidas)]
        if self.login is not None:
            usados += [self.login.origen, *self.login.origenes_login]
        fuera = sorted({origen for origen in usados if origen not in self.origenes})
        if fuera:
            raise ValueError(f"origen no declarado: {', '.join(fuera)}")
        return self

    @property
    def pasos(self) -> tuple[()]:
        """A site has no steps (what makes it usable wherever a script's envelope is expected)."""
        return ()

    @classmethod
    def de_guion(cls, guion: Guion) -> Sitio:
        """The site of a script: same origins, login, masks and download; the agent starts after the login."""
        return cls(
            titulo=guion.titulo,
            descripcion=guion.descripcion,
            verificado_contra_sitio_real=guion.verificado_contra_sitio_real,
            nota_verificacion=guion.nota_verificacion,
            demostracion=guion.demostracion,
            origenes=dict(guion.origenes),
            login=guion.login,
            inicio=Inicio(origen=guion.login.origen, ruta=guion.login.ruta),
            descarga=guion.descarga,
            peticiones_permitidas=list(guion.peticiones_permitidas),
            enmascarar=list(guion.enmascarar),
            pausa_ms=guion.pausa_ms,
        )


class SpecSitio(_M):
    """A site file: same envelope as a script's (``fuente``, ``version``, ``reglas``)."""

    fuente: str
    version: int = Field(ge=1)
    descripcion: str = ""
    origen: str = "incorporada"
    reglas: Sitio


class SitioError(ValueError):
    """A site that does not exist or cannot be used (message in Spanish)."""


def _leer(path: Path) -> SpecSitio | None:
    try:
        return SpecSitio.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except OSError:
        return None
    except (ValueError, ValidationError) as exc:
        logger.error("Browser site %s is invalid and is not offered: %s", path.name, exc)
        return None


def _de_ficheros(directorio_operador: str | Path | None) -> dict[str, SpecSitio]:
    operador = str(directorio_operador or "").strip()
    directorios = ([Path(operador)] if operador else []) + [SITIOS_INCORPORADOS]
    encontrados: dict[str, SpecSitio] = {}
    for directorio in directorios:
        if not directorio.is_dir():
            continue
        for path in sorted(directorio.glob("*.json")):
            clave = path.stem
            if not _ID.match(clave) or clave in encontrados:
                continue
            spec = _leer(path)
            if spec is not None:
                encontrados[clave] = spec
    return encontrados


def sitios(guiones: list[SpecGuion] | None = None, *, directorio: str | Path | None = None) -> dict[str, SpecSitio]:
    """Every declared site by key: the files (``directorio``, the operator's ``NAVEGADOR_SITIOS_DIR``, then the
    bundled ones), then the sites of ``guiones`` that no file declares."""
    encontrados = _de_ficheros(directorio)
    for spec in guiones or []:
        clave = spec.fuente.removeprefix("nav_")
        if clave in encontrados:
            continue
        try:
            reglas = Sitio.de_guion(spec.reglas)
        except ValueError, ValidationError:
            logger.exception("the site of browser script %s cannot be derived", clave)
            continue
        encontrados[clave] = SpecSitio(fuente=f"sitio_{clave}", version=spec.version, origen="guion", reglas=reglas)
    return encontrados


def describir(spec: SpecSitio) -> dict[str, Any]:
    """What the UI shows of a site."""
    reglas = spec.reglas
    return {
        "titulo": reglas.titulo,
        "descripcion": reglas.descripcion,
        "sitios": sorted({urlsplit(o).hostname or o for o in reglas.origenes.values()}),
        "requiere_login": reglas.login is not None,
        "admite_descarga": reglas.descarga is not None,
        "verificado_contra_sitio_real": reglas.verificado_contra_sitio_real,
        "nota_verificacion": reglas.nota_verificacion,
        "demostracion": reglas.demostracion,
    }


__all__ = ["SITIOS_INCORPORADOS", "Inicio", "Sitio", "SitioError", "SpecSitio", "describir", "sitios"]
