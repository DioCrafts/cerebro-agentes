"""Each domain's OWN evaluation: deterministic cases over its tools and notices, no model involved.

The plan's rule: a module only counts if it passes its own evaluation. A domain declares its cases in
``Ganchos.declaracion.evaluacion`` (``eval_data/modulos/<clave>.jsonl``) and the minimum share it must pass
(``umbral_evaluacion``). Every case runs one of the domain's tools (or its daily ``avisos``) over a household
profile on a fixed date and checks the result's text:

* ``tipo``: ``normal``, ``dificil`` (edge of a rule, tricky input, a trap where a naive answer is wrong) or
  ``abstencion`` (the data are missing or out of the domain's scope: it must say so — an error or a request for the
  data — and must NOT produce a figure);
* ``espera.error`` (``true``/``false``, omitted: either), ``espera.contiene`` (every snippet, case and spacing
  insensitive), ``espera.no_contiene`` (none of them), ``espera.avisos`` (number of notices, for ``avisos`` cases).

``python -m asistente.nucleo.evaluacion.modulos`` prints every domain's score.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.modulos.contrato import ContextoModulo, HerramientaModulo, ResultadoHerramienta
from asistente.nucleo.modulos.ganchos import declaracion_de

RAIZ = Path(__file__).resolve().parents[3]
AVISOS = "avisos"


class Espera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: bool | None = None
    contiene: list[str] = Field(default_factory=list)
    no_contiene: list[str] = Field(default_factory=list)
    avisos: int | None = None


class CasoModulo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tipo: Literal["normal", "dificil", "abstencion"]
    descripcion: str
    hoy: date
    perfil: dict[str, Any] = Field(default_factory=dict)
    # A tool of the domain, or ``avisos`` for its daily notices.
    herramienta: str
    argumentos: dict[str, Any] = Field(default_factory=dict)
    espera: Espera


@dataclass(frozen=True)
class ResultadoCaso:
    caso: CasoModulo
    supera: bool
    fallos: tuple[str, ...] = ()


@dataclass
class InformeModulo:
    clave: str
    umbral: float
    ruta: str | None
    resultados: list[ResultadoCaso] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.resultados)

    @property
    def superados(self) -> int:
        return sum(r.supera for r in self.resultados)

    @property
    def puntuacion(self) -> float:
        return self.superados / self.total if self.total else 0.0

    @property
    def aprobado(self) -> bool:
        return self.error is None and self.total > 0 and self.puntuacion >= self.umbral

    def por_tipo(self, tipo: str) -> int:
        return sum(r.caso.tipo == tipo for r in self.resultados)


def _normal(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace(" ", " ").replace(" ", " ")).strip().casefold()


def cargar_casos(ruta: str | Path) -> list[CasoModulo]:
    camino = Path(ruta)
    if not camino.is_absolute():
        camino = RAIZ / camino
    casos = []
    for numero, linea in enumerate(camino.read_text(encoding="utf-8").splitlines(), start=1):
        if linea.strip() and not linea.lstrip().startswith("//"):
            try:
                casos.append(CasoModulo.model_validate_json(linea))
            except Exception as exc:
                raise ValueError(f"{camino.name}:{numero}: {exc}") from exc
    return casos


def _herramienta(modulo: Any, nombre: str) -> HerramientaModulo | None:
    return next((h for h in modulo.herramientas if h.spec.name == nombre), None)


def ejecutar_caso(modulo: Any, caso: CasoModulo) -> ResultadoCaso:
    guardado: dict[str, Any] = {}
    contexto = ContextoModulo(
        perfil=dict(caso.perfil), hoy=caso.hoy, case_id=f"eval-{caso.id}", registrar=guardado.update
    )
    fallos: list[str] = []
    num_avisos: int | None = None
    try:
        if caso.herramienta == AVISOS:
            avisos = modulo.avisos(contexto)
            num_avisos = len(avisos)
            resultado = ResultadoHerramienta(
                contenido="\n".join(f"{a.titulo}\n{a.cuerpo}" for a in avisos), resumen=f"{num_avisos} avisos"
            )
        else:
            herramienta = _herramienta(modulo, caso.herramienta)
            if herramienta is None:
                return ResultadoCaso(caso, False, (f"el módulo no tiene la herramienta {caso.herramienta!r}",))
            resultado = herramienta.ejecutar(dict(caso.argumentos), contexto)
    except Exception as exc:  # a crash is a failed case, never a passed abstention
        return ResultadoCaso(caso, False, (f"excepción {type(exc).__name__}: {exc}",))
    texto = _normal(resultado.contenido)
    espera = caso.espera
    if espera.error is not None and resultado.es_error != espera.error:
        fallos.append(f"es_error={resultado.es_error}, se esperaba {espera.error}")
    for trozo in espera.contiene:
        if _normal(trozo) not in texto:
            fallos.append(f"falta «{trozo}»")
    for trozo in espera.no_contiene:
        if _normal(trozo) in texto:
            fallos.append(f"no debía aparecer «{trozo}»")
    if espera.avisos is not None and num_avisos != espera.avisos:
        fallos.append(f"{num_avisos} avisos, se esperaban {espera.avisos}")
    return ResultadoCaso(caso, not fallos, tuple(fallos))


def evaluar_modulo(modulo: Any) -> InformeModulo:
    declaracion = declaracion_de(modulo)
    informe = InformeModulo(clave=modulo.clave, umbral=declaracion.umbral_evaluacion, ruta=declaracion.evaluacion)
    if declaracion.evaluacion is None:
        informe.error = "no declara casos de evaluación"
        return informe
    try:
        casos = cargar_casos(declaracion.evaluacion)
    except (OSError, ValueError) as exc:
        informe.error = f"no se pudieron leer los casos: {exc}"
        return informe
    informe.resultados = [ejecutar_caso(modulo, caso) for caso in casos]
    return informe


def evaluar(modulos: Iterable[Any]) -> list[InformeModulo]:
    return [evaluar_modulo(m) for m in modulos]


def tabla(informes: Sequence[InformeModulo]) -> str:
    filas = ["| Módulo | Casos | Difíciles | Abstención | Superados | Puntuación | Umbral | Estado |", "|---" * 8 + "|"]
    for i in informes:
        estado = "cumple" if i.aprobado else (i.error or "no supera su evaluación")
        filas.append(
            f"| {i.clave} | {i.total} | {i.por_tipo('dificil')} | {i.por_tipo('abstencion')} | {i.superados} | "
            f"{i.puntuacion:.0%} | {i.umbral:.0%} | {estado} |"
        )
    return "\n".join(filas)


def main() -> int:
    from asistente.nucleo.modulos.registro import PAQUETE, construir_registro, descubrir

    candidatos, _ = descubrir(PAQUETE)
    informes = evaluar(m for m in construir_registro(candidatos))
    print(tabla(informes))
    for informe in informes:
        for r in informe.resultados:
            if not r.supera:
                print(f"  {informe.clave}/{r.caso.id}: {'; '.join(r.fallos)}")
    return 0 if all(i.aprobado for i in informes) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CasoModulo", "InformeModulo", "cargar_casos", "ejecutar_caso", "evaluar", "evaluar_modulo", "tabla"]
