"""The browser scripts in the format watch, and what is kept when a page changed.

* Every script is a format-watch source ``nav_<clave>`` (:mod:`asistente.conocimiento.formatos`). Each run that got
  past the user's login is observed: a step whose element is missing or does not match is a grave failure, a
  step that only worked with a repaired selector a minor one. ``ops formatos status`` / ``ops doctor`` then
  say «formato nav_datadis_consumo: ROTA…» like for any other parser. No sample of the page is stored by the
  watch (``guarda_muestras=False``): pages behind a login are personal.
* A repaired selector that passed the gate (``reparacion.py``) is saved as a format-watch proposal
  (``tipo="spec"``) for a person to review; :func:`aplicar_reparacion` puts it in force after re-running
  :func:`ensayar_guion`, the deterministic gate for scripts: the candidate validates, keeps the same origins,
  steps, actions, field names, labels and texts (only selectors may change) and still passes the static
  safety checks. A proposal for a script is never applied automatically (``auto_aplicable=False``).
* For diagnosis, the redacted skeleton of the page where a step failed (``seguridad.redactar_esqueleto``:
  structure and generic labels, nothing typed, anything that looks personal masked) is kept per case for
  :data:`DIAS_DIAGNOSTICO` days, and erased with the case (:func:`borrar_de_caso`, :func:`purgar`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Any

from pydantic import ValidationError

from asistente.capacidades.navegador.ejecutor import FALLOS_DE_FORMATO, ResultadoEjecucion
from asistente.capacidades.navegador.guion import (
    PREFIJO_FUENTE,
    Guion,
    SpecGuion,
    claves_incorporadas,
    fuente_de_guion,
    guion_activo,
    guion_incorporado,
    ruta_reparada,
)
from asistente.conocimiento import formatos
from asistente.conocimiento.formatos import Ensayo, Fallo, Fuente, Lectura, Propuesta
from asistente.plataforma import settings

logger = logging.getLogger(__name__)

DIAS_DIAGNOSTICO = 30
_CASE_ID = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _id_valido(valor: str) -> bool:
    """A case or task id usable as ONE path component: «.» and «..» would name the diagnosis folder itself or
    its parent (``borrar_de_caso("..")`` erased the whole browser folder); ``fullmatch`` so a trailing newline
    does not slip through ``$``."""
    return bool(_CASE_ID.fullmatch(valor)) and valor not in {".", ".."}


# --------------------------------------------------------------------------- #
# Format-watch sources
# --------------------------------------------------------------------------- #


def _canarios(extraccion: Any, muestra: str, contexto: str, reglas: Any) -> Lectura:
    """Canaries of one run: what each step found on the page (no page content is ever passed here)."""
    pasos = extraccion.get("pasos", []) if isinstance(extraccion, dict) else []
    fallos: list[Fallo] = []
    ok = 0
    for paso in pasos:
        estado = paso.get("estado")
        if estado in FALLOS_DE_FORMATO:
            fallos.append(
                Fallo(
                    canario=f"paso_{paso.get('id')}",
                    gravedad="grave",
                    detalle=f"El paso «{paso.get('id')}» ({paso.get('accion')}): {paso.get('detalle') or estado}.",
                )
            )
        elif estado == "reparado":
            fallos.append(
                Fallo(
                    canario=f"selector_{paso.get('id')}",
                    gravedad="leve",
                    detalle=f"El paso «{paso.get('id')}» solo ha funcionado con un selector reparado (propuesta por revisar).",
                )
            )
            ok += 1
        elif estado == "ok":
            ok += 1
    if isinstance(extraccion, dict) and extraccion.get("descarga_rechazada"):
        fallos.append(Fallo(canario="descarga", gravedad="grave", detalle=str(extraccion["descarga_rechazada"])))
    return Lectura(fallos=fallos, metricas={"pasos_ok": float(ok)})


def ensayar_guion(clave: str, reglas_propuestas: dict[str, Any], directorio: str | Path | None = None) -> Ensayo:
    """Deterministic gate of a candidate script: only selectors may differ from the script in force."""
    try:
        candidato = Guion.model_validate(reglas_propuestas)
    except (ValidationError, ValueError, TypeError) as exc:
        return Ensayo(ok=False, motivos=[f"El guion propuesto no es válido: {exc}"])
    vigente = guion_activo(clave, directorio).reglas
    motivos: list[str] = []
    if candidato.origenes != vigente.origenes or candidato.login.model_dump() != vigente.login.model_dump() | {
        "hecho_cuando": candidato.login.hecho_cuando
    }:
        motivos.append("El guion propuesto cambia los sitios o el inicio de sesión: solo se admiten selectores.")
    if candidato.forma() != vigente.forma():
        motivos.append(
            "El guion propuesto cambia pasos, acciones, nombres de campo, etiquetas o textos: solo se admiten selectores."
        )
    resto = {"parametros", "descarga", "peticiones_permitidas", "rango_fechas", "pausa_ms", "demostracion"}
    if candidato.model_dump(include=resto) != vigente.model_dump(include=resto):
        motivos.append("El guion propuesto cambia parámetros, descargas o peticiones permitidas.")
    if candidato.model_dump(mode="json") == vigente.model_dump(mode="json"):
        motivos.append("El guion propuesto es idéntico al vigente.")
    return Ensayo(ok=not motivos, motivos=motivos)


def registrar_fuentes() -> None:
    """Register one format-watch source per bundled script (idempotent)."""
    for clave in claves_incorporadas():
        try:
            spec = guion_incorporado(clave)
        except (ValueError, ValidationError):
            logger.exception("Bundled browser script %s is invalid", clave)
            continue

        def ensayo(reglas: dict[str, Any], directorio: str | Path | None, _clave: str = clave) -> Ensayo:
            return ensayar_guion(_clave, reglas, directorio)

        formatos.registrar(
            Fuente(
                clave=spec.fuente,
                descripcion=f"agente con navegador: {spec.reglas.titulo}",
                expectativas=(
                    "Cada paso del guion encuentra un único elemento con el mismo nombre de campo, la misma "
                    "etiqueta o el mismo texto que declara el guion.",
                    "La descarga, si la hay, es del tipo y tamaño declarados.",
                ),
                canarios=_canarios,
                guarda_muestras=False,
                ensayo=ensayo,
                auto_aplicable=False,
                codigo=f"asistente/capacidades/navegador/guiones/{clave}.json",
            )
        )


def observar_ejecucion(
    clave: str,
    version: int,
    resultado: ResultadoEjecucion,
    *,
    state_path: str | Path | None = None,
    directorio: str | Path | None = None,
    forzar: bool = False,
) -> formatos.Observacion | None:
    """Record a run in the format watch (only runs that reached the agent's steps say anything about the page)."""
    if not resultado.llego_a_agente or resultado.estado == "cancelado":
        return None
    rechazo = None
    if resultado.estado == "fallido" and resultado.pasos and resultado.pasos[-1].accion == "descargar":
        if "descarga no válida" in resultado.pasos[-1].detalle:
            rechazo = resultado.pasos[-1].detalle
    extraccion = {
        "pasos": [{"id": p.id, "accion": p.accion, "estado": p.estado, "detalle": p.detalle} for p in resultado.pasos],
        "descarga_rechazada": rechazo,
    }
    return formatos.observar(
        fuente_de_guion(clave),
        None,
        extraccion=extraccion,
        contexto=f"v{version}",
        state_path=state_path,
        directorio=directorio,
        forzar=forzar,
    )


def proponer_reparacion(
    clave: str,
    spec: SpecGuion,
    reparaciones: dict[str, str],
    *,
    directorio: str | Path | None = None,
    ahora: datetime | None = None,
) -> Propuesta | None:
    """Save the selectors repaired during a run as a proposal for a person to review (never applied here)."""
    if not reparaciones:
        return None
    reglas = spec.reglas.model_dump(mode="json")
    for paso in reglas["pasos"]:
        if paso["id"] in reparaciones:
            paso["selector"] = reparaciones[paso["id"]]
    fuente = fuente_de_guion(clave)
    ensayo = ensayar_guion(clave, reglas, directorio)
    propuesta = Propuesta(
        id=formatos.nuevo_id(fuente, ahora, directorio),
        fuente=fuente,
        creada=(ahora or datetime.now(UTC)).isoformat(),
        tipo="spec",
        estado="propuesta" if ensayo.ok else "sin_converger",
        firma=json.dumps(sorted(reparaciones.items())),
        que_ha_cambiado="Selectores que ya no encontraban su elemento: "
        + ", ".join(f"{paso} → {selector}" for paso, selector in sorted(reparaciones.items())),
        causa="La página del sitio ha cambiado.",
        justificacion=(
            "Cada selector propuesto encontró un único elemento con el mismo nombre de campo y la misma etiqueta "
            "o texto que el guion (comprobación determinista durante la ejecución)."
        ),
        spec_base_version=spec.version,
        reglas_propuestas=reglas,
        ensayo=ensayo,
        llamadas_llm=len(reparaciones),
    )
    formatos.guardar_propuesta(propuesta, directorio)
    return propuesta


def aplicar_reparacion(propuesta_id: str, *, por: str = "manual", directorio: str | Path | None = None) -> Propuesta:
    """Put a reviewed repair in force (``FORMAT_DIR/specs/nav_<clave>.json``, version + 1). Gate run again."""
    propuesta = formatos.leer_propuesta(propuesta_id, directorio)
    if not propuesta.fuente.startswith(PREFIJO_FUENTE) or propuesta.reglas_propuestas is None:
        raise formatos.FormatoError("Esta propuesta no es la reparación de un guion del navegador.")
    if propuesta.estado != "propuesta":
        raise formatos.FormatoError(
            f"La propuesta está «{propuesta.estado}»; solo se aplican las que están «propuesta»."
        )
    clave = propuesta.fuente[len(PREFIJO_FUENTE) :]
    vigente = guion_activo(clave, directorio)
    if propuesta.spec_base_version != vigente.version:
        raise formatos.FormatoError("La propuesta parte de otra versión del guion: vuelve a ejecutarlo.")
    ensayo = ensayar_guion(clave, propuesta.reglas_propuestas, directorio)
    if not ensayo.ok:
        raise formatos.FormatoError("La propuesta no supera el ensayo: " + " ".join(ensayo.motivos))
    nueva = SpecGuion(
        fuente=propuesta.fuente,
        version=vigente.version + 1,
        descripcion=propuesta.que_ha_cambiado[:500],
        origen=f"propuesta {propuesta.id}",
        reglas=Guion.model_validate(propuesta.reglas_propuestas),
    )
    ruta = ruta_reparada(clave, directorio)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    historial = ruta.parent / "historial" / f"{propuesta.fuente}.v{vigente.version}.json"
    historial.parent.mkdir(parents=True, exist_ok=True)
    historial.write_text(vigente.model_dump_json(indent=1), encoding="utf-8")
    ruta.write_text(nueva.model_dump_json(indent=1), encoding="utf-8")
    propuesta.estado = "aplicada"
    propuesta.ensayo = ensayo
    propuesta.aplicada_el = datetime.now(UTC).isoformat()
    propuesta.aplicada_por = por
    propuesta.spec_version_aplicada = nueva.version
    formatos.guardar_propuesta(propuesta, directorio)
    return propuesta


# --------------------------------------------------------------------------- #
# Redacted diagnosis, per case
# --------------------------------------------------------------------------- #


def _dir_diagnostico(base: str | Path | None = None) -> Path:
    return Path(base if base is not None else settings.NAVEGADOR_DIR) / "diagnostico"


def guardar_diagnostico(
    case_id: str,
    tarea_id: str,
    datos: dict[str, Any],
    *,
    base: str | Path | None = None,
) -> Path | None:
    """Keep the REDACTED skeleton of a failed step for diagnosis. Nothing typed, nothing unmasked."""
    if not _id_valido(case_id) or not _id_valido(tarea_id):
        return None
    ruta = _dir_diagnostico(base) / case_id / f"{tarea_id}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = {"guardado": datetime.now(UTC).isoformat(), **datos}
    ruta.write_text(json.dumps(cuerpo, ensure_ascii=False, indent=1), encoding="utf-8")
    return ruta


def diagnosticos_de(case_id: str, *, base: str | Path | None = None) -> list[dict[str, Any]]:
    if not _id_valido(case_id):
        return []
    out: list[dict[str, Any]] = []
    for ruta in sorted((_dir_diagnostico(base) / case_id).glob("*.json")):
        try:
            out.append(json.loads(ruta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def borrar_de_caso(case_id: str, *, base: str | Path | None = None) -> int:
    """Erase everything the browser agent kept for a case (call it when the case or the account is erased)."""
    if not _id_valido(case_id):
        return 0
    carpeta = _dir_diagnostico(base) / case_id
    n = len(list(carpeta.glob("*.json"))) if carpeta.is_dir() else 0
    shutil.rmtree(carpeta, ignore_errors=True)
    return n


def purgar(
    existe_caso: Callable[[str], bool],
    *,
    base: str | Path | None = None,
    ahora: datetime | None = None,
    dias: int = DIAS_DIAGNOSTICO,
) -> int:
    """Erase diagnoses of cases that no longer exist (an erased account) and those older than ``dias``."""
    raiz = _dir_diagnostico(base)
    if not raiz.is_dir():
        return 0
    limite = (ahora or datetime.now(UTC)) - timedelta(days=dias)
    borrados = 0
    for carpeta in raiz.iterdir():
        if not carpeta.is_dir():
            continue
        if not existe_caso(carpeta.name):
            borrados += borrar_de_caso(carpeta.name, base=base)
            continue
        for ruta in carpeta.glob("*.json"):
            if datetime.fromtimestamp(ruta.stat().st_mtime, UTC) < limite:
                ruta.unlink(missing_ok=True)
                borrados += 1
    return borrados


def propuestas_de(claves: Iterable[str] | None = None, directorio: str | Path | None = None) -> list[Propuesta]:
    fuentes = {fuente_de_guion(c) for c in (claves or claves_incorporadas())}
    return [p for p in formatos.listar_propuestas(directorio) if p.fuente in fuentes]


registrar_fuentes()

__all__ = [
    "DIAS_DIAGNOSTICO",
    "aplicar_reparacion",
    "borrar_de_caso",
    "diagnosticos_de",
    "ensayar_guion",
    "guardar_diagnostico",
    "observar_ejecucion",
    "propuestas_de",
    "proponer_reparacion",
    "purgar",
    "registrar_fuentes",
]
