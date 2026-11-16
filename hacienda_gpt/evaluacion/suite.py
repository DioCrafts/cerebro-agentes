"""Loading the versioned suite and checking a run against its deterministic expectations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from pydantic import ValidationError

from hacienda_gpt.evaluacion.cifras import CalculoError, aparece, calcular_cifra, numeros_en, valor_de_cifra
from hacienda_gpt.evaluacion.modelos import (
    CasoEvaluacion,
    ConfigSuite,
    Ejecucion,
    ExpAccionPendiente,
    ExpCifra,
    ExpCitas,
    ExpHerramientaLlamada,
    ExpHerramientaNoLlamada,
    ExpModo,
    ExpModuloConsultado,
    ExpModuloNoConsultado,
    ExpSinAccionPendiente,
    ExpSumaAusente,
    ExpTextoAusente,
    ExpTextoPresente,
)
from hacienda_gpt.evaluacion.modulo_prueba import MODULOS_SINTETICOS
from hacienda_gpt.modulos.contrato import Modulo

CASOS_DIR = Path(__file__).resolve().parent / "casos"
CONFIG = "suite.json"
_MARCADOR_RE = re.compile(r"\{(cifra|fragmento):([A-Za-z0-9_]+)\}")


class SuiteInvalidaError(ValueError):
    """The suite cannot be loaded (bad JSON, duplicate id, unknown category or module). Spanish message."""


def cargar_config(directorio: Path = CASOS_DIR) -> ConfigSuite:
    ruta = directorio / CONFIG
    try:
        return ConfigSuite.model_validate_json(ruta.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise SuiteInvalidaError(f"No se puede leer la configuración de la suite {ruta}: {exc}") from exc


def ficheros_de_casos(directorio: Path = CASOS_DIR) -> list[Path]:
    return sorted(p for p in directorio.rglob("*.json") if p.name != CONFIG)


def cargar_casos(directorio: Path = CASOS_DIR, *, config: ConfigSuite | None = None) -> list[CasoEvaluacion]:
    """Every case of the suite, by id. Raises :class:`SuiteInvalidaError` with every problem found."""
    config = config or cargar_config(directorio)
    casos: list[CasoEvaluacion] = []
    problemas: list[str] = []
    vistos: dict[str, Path] = {}
    for ruta in ficheros_de_casos(directorio):
        try:
            caso = CasoEvaluacion.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            problemas.append(f"{ruta.name}: {exc}")
            continue
        if caso.id in vistos:
            problemas.append(f"{ruta.name}: id «{caso.id}» repetido (también en {vistos[caso.id].name})")
            continue
        vistos[caso.id] = ruta
        if caso.categoria not in config.categorias:
            problemas.append(f"{ruta.name}: categoría desconocida «{caso.categoria}»")
        try:
            modulos_de(caso)
        except SuiteInvalidaError as exc:
            problemas.append(f"{ruta.name}: {exc}")
        casos.append(caso)
    if problemas:
        raise SuiteInvalidaError("La suite tiene errores:\n- " + "\n- ".join(problemas))
    return sorted(casos, key=lambda c: c.id)


def huella_suite(directorio: Path = CASOS_DIR) -> str:
    """SHA-256 over every file of the suite: two reports are comparable only on the same suite."""
    digest = hashlib.sha256()
    for ruta in [directorio / CONFIG, *ficheros_de_casos(directorio)]:
        digest.update(ruta.relative_to(directorio).as_posix().encode())
        digest.update(b"\0")
        digest.update(ruta.read_bytes())
    return digest.hexdigest()


def modulos_de(caso: CasoEvaluacion) -> list[Modulo]:
    """The modules a case switches on (registered or synthetic). Raises :class:`SuiteInvalidaError`."""
    from hacienda_gpt.modulos.registro import registro

    modulos: list[Modulo] = []
    for clave in caso.modulos:
        modulo = MODULOS_SINTETICOS.get(clave) or registro().get(clave)
        if modulo is None:
            raise SuiteInvalidaError(f"módulo «{clave}» desconocido o desactivado")
        modulos.append(modulo)
    return modulos


def modulos_sinteticos_de(caso: CasoEvaluacion) -> list[Modulo]:
    return [MODULOS_SINTETICOS[c] for c in caso.modulos if c in MODULOS_SINTETICOS]


# --------------------------------------------------------------------------- #
# Expected figures of a case
# --------------------------------------------------------------------------- #


@dataclass
class CifrasEsperadas:
    cifras: dict[str, str] = field(default_factory=dict)
    # Sums that must not appear, one per ``suma_ausente`` expectation (None when not computable).
    sumas: list[Decimal | None] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    derivas: list[str] = field(default_factory=list)


def cifras_esperadas(caso: CasoEvaluacion) -> CifrasEsperadas:
    """Run every calculator the case's expectations name, on its snapshot. No model involved."""
    extra = modulos_sinteticos_de(caso)
    resultado = CifrasEsperadas()
    for expectativa in caso.expectativas:
        if isinstance(expectativa, ExpCifra):
            try:
                cifra = calcular_cifra(expectativa.calculo, caso.expediente, modulos_extra=extra)
            except CalculoError as exc:
                resultado.errores.append(f"cifra «{expectativa.nombre}»: {exc}")
                continue
            resultado.cifras[expectativa.nombre] = cifra
            registrada = expectativa.calculo.registrada
            if registrada is not None and registrada != cifra:
                resultado.derivas.append(
                    f"cifra «{expectativa.nombre}»: la calculadora da ahora {cifra} y al escribir el caso daba "
                    f"{registrada} (¿ha cambiado un parámetro o la calculadora?)."
                )
        elif isinstance(expectativa, ExpSumaAusente):
            total = Decimal(0)
            calculable = True
            for calculo in expectativa.calculos:
                try:
                    valor = valor_de_cifra(calcular_cifra(calculo, caso.expediente, modulos_extra=extra))
                except CalculoError as exc:
                    resultado.errores.append(f"suma ausente: {exc}")
                    calculable = False
                    break
                if valor is None:
                    resultado.errores.append(f"suma ausente: la cifra de {calculo.herramienta} no es un importe")
                    calculable = False
                    break
                total += valor
            resultado.sumas.append(total if calculable else None)
    return resultado


def problemas_de_guion(caso: CasoEvaluacion) -> list[str]:
    """Placeholders of the script that point at nothing."""
    if caso.guion is None:
        return []
    nombres = {e.nombre for e in caso.expectativas if isinstance(e, ExpCifra)}
    problemas: list[str] = []
    for pasos in caso.guion.values():
        for paso in pasos:
            for tipo, nombre in _MARCADOR_RE.findall(paso.texto or ""):
                if tipo == "cifra" and nombre not in nombres:
                    problemas.append(f"el guion usa {{cifra:{nombre}}} y no hay ninguna cifra esperada con ese nombre")
    return problemas


# --------------------------------------------------------------------------- #
# Checking a run
# --------------------------------------------------------------------------- #


def comprobar(caso: CasoEvaluacion, ejecucion: Ejecucion, esperadas: CifrasEsperadas) -> list[str]:
    """Failed deterministic expectations, in Spanish (empty = the run satisfies them all)."""
    fallos: list[str] = []
    if ejecucion.error:
        fallos.append(f"la ejecución falló: {ejecucion.error}")
    fallos.extend(f"no se pudo calcular lo esperado ({error})" for error in esperadas.errores)
    respuesta = ejecucion.respuesta
    llamadas = ejecucion.herramientas
    sumas = iter(esperadas.sumas)

    def consultado(modulo: str) -> bool:
        return any(c.herramienta == f"consultar_{modulo}" and c.agente == "asesor" for c in llamadas)

    for exp in caso.expectativas:
        if isinstance(exp, ExpHerramientaLlamada):
            veces = sum(
                1
                for c in llamadas
                if c.herramienta == exp.herramienta
                and not c.es_error
                and (exp.agente is None or c.agente == exp.agente)
            )
            if veces < exp.min_veces:
                quien = f" por {exp.agente}" if exp.agente else ""
                fallos.append(f"debía llamar a {exp.herramienta}{quien} (al menos {exp.min_veces}; llamadas: {veces})")
        elif isinstance(exp, ExpHerramientaNoLlamada):
            if any(c.herramienta == exp.herramienta for c in llamadas):
                fallos.append(f"no debía llamar a {exp.herramienta}")
        elif isinstance(exp, ExpModuloConsultado):
            if not consultado(exp.modulo):
                fallos.append(f"debía consultar al especialista «{exp.modulo}»")
        elif isinstance(exp, ExpModuloNoConsultado):
            if consultado(exp.modulo):
                fallos.append(f"no debía consultar al especialista «{exp.modulo}»")
        elif isinstance(exp, ExpCifra):
            cifra = esperadas.cifras.get(exp.nombre)
            if cifra is not None and not aparece(cifra, respuesta):
                fallos.append(f"falta la cifra «{exp.nombre}» que da la calculadora: {cifra}")
        elif isinstance(exp, ExpSumaAusente):
            total = next(sumas, None)
            if total is not None and total in numeros_en(respuesta):
                fallos.append(
                    f"suma cifras que ninguna herramienta ha combinado ({total}): {exp.motivo or 'no se suman'}"
                )
        elif isinstance(exp, ExpTextoAusente):
            encontrado = re.search(exp.patron, respuesta, flags=re.IGNORECASE)
            if encontrado:
                fallos.append(f"afirma lo que no debe («{encontrado.group(0)}»): {exp.motivo or exp.patron}")
        elif isinstance(exp, ExpTextoPresente):
            if not re.search(exp.patron, respuesta, flags=re.IGNORECASE):
                fallos.append(f"falta en la respuesta: {exp.motivo or exp.patron}")
        elif isinstance(exp, ExpCitas):
            citas = [c for c in ejecucion.citas if exp.autoridad is None or c.autoridad == exp.autoridad]
            if len(citas) < exp.minimo:
                de = f" de {exp.autoridad}" if exp.autoridad else ""
                fallos.append(f"debía citar al menos {exp.minimo} fuente(s){de} (cita {len(citas)})")
        elif isinstance(exp, ExpModo):
            if ejecucion.modo not in exp.permitidos:
                fallos.append(f"modo «{ejecucion.modo}» no admitido ({', '.join(exp.permitidos)})")
        elif isinstance(exp, ExpAccionPendiente):
            if exp.herramienta not in ejecucion.acciones_pendientes:
                fallos.append(f"{exp.herramienta} debía quedar como acción pendiente de confirmación")
        elif isinstance(exp, ExpSinAccionPendiente):
            if ejecucion.acciones_pendientes:
                fallos.append(f"no debía proponer acciones ({', '.join(ejecucion.acciones_pendientes)})")
    return fallos


@dataclass
class InformeValidacion:
    casos: int = 0
    deterministicos: int = 0
    con_guion: int = 0
    errores: list[str] = field(default_factory=list)
    derivas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def validar(directorio: Path = CASOS_DIR, *, casos: Sequence[CasoEvaluacion] | None = None) -> InformeValidacion:
    """Check the suite without any model: schema, categories, modules, every expected figure computable."""
    informe = InformeValidacion()
    try:
        lista = list(casos) if casos is not None else cargar_casos(directorio)
    except SuiteInvalidaError as exc:
        informe.errores.append(str(exc))
        return informe
    informe.casos = len(lista)
    for caso in lista:
        informe.deterministicos += caso.deterministico
        informe.con_guion += caso.guion is not None
        esperadas = cifras_esperadas(caso)
        informe.errores.extend(f"{caso.id}: {e}" for e in esperadas.errores)
        informe.errores.extend(f"{caso.id}: {e}" for e in problemas_de_guion(caso))
        informe.derivas.extend(f"{caso.id}: {d}" for d in esperadas.derivas)
    return informe


__all__ = [
    "CASOS_DIR",
    "CifrasEsperadas",
    "SuiteInvalidaError",
    "InformeValidacion",
    "cargar_casos",
    "cargar_config",
    "cifras_esperadas",
    "comprobar",
    "ficheros_de_casos",
    "huella_suite",
    "modulos_de",
    "problemas_de_guion",
    "validar",
]
