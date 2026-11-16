"""The modules' legal data in the agentic re-encoding loop (model proposes, deterministic gate decides, reviewer
with test cases), with no change to the loop.

Every file declared in :mod:`hacienda_gpt.conocimiento.registro` with a :class:`~hacienda_gpt.conocimiento.registro.
Recodificable` gets, for each kind of group (pydantic model) it holds, an adapter :class:`GrupoDeModulo` — the
generic adapter of the files whose groups carry their own quotes (``GrupoConEvidencia``), told where the module
keeps its file and how a reviewer's case runs through the module's calculator. Importing this module registers
them; the loop imports it itself (``nucleo._cargar_adaptadores``), and its target search
(``adaptadores._otros_modulos``) calls the finders below, so ``params-recode``, ``params-autopilot``, ``params-gate``,
``params-apply`` and ``params-confirm`` cover the modules automatically.

Rules on top of the loop's, for data that is not the tax calculator's:

* a module's file has no tax year: it is filed under the pseudo-year ``nucleo.SIN_EJERCICIO`` (what is in force);
* a proposal on a module's group is NEVER applied without a person (``revisar`` always carries a line saying so),
  whatever the reviewer's cases say: the household calculators were not built to be re-checked by the loop;
* a run over a copy of the tax files (``data_dir`` other than the package's) leaves the modules alone.

The files without a ``Recodificable`` are left to a person, and their declaration says why (``solo_persona``):
:func:`solo_persona` lists them for the operator.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from hacienda_gpt.conocimiento.registro import FicheroLegal, ficheros_legales
from hacienda_gpt.tax.params import DATA_DIR
from hacienda_gpt.tax.recodificacion import adaptadores_otros as otros
from hacienda_gpt.tax.recodificacion.adaptadores import Hallazgo
from hacienda_gpt.tax.recodificacion.control import Control
from hacienda_gpt.tax.recodificacion.nucleo import Objetivo, registrar
from hacienda_gpt.conocimiento.texto import TextoDe

REVISION_HUMANA = (
    "datos legales de un módulo del hogar: una persona confirma la propuesta antes de aplicarla (nunca se aplica "
    "sola)"
)


def _es_el_del_paquete(data_dir: Path) -> bool:
    return Path(data_dir).resolve() == Path(DATA_DIR).resolve()


class GrupoDeModulo(otros.GrupoConEvidencia):
    """One kind of group of one module's legal data file."""

    def __init__(self, familia: Any, modelo: type[BaseModel], legal: FicheroLegal) -> None:
        super().__init__(familia, modelo)
        assert legal.ruta is not None
        self.legal = legal
        self.directorio = legal.ruta.parent
        self.que = (
            f"one group of legal rules of the Spanish household assistant's «{familia.titulo}» module: the pydantic "
            f"model `{modelo.__name__}` (the task names the group and the article it is read from)"
        )

    def directorio_de(self, data_dir: Path) -> Path:
        # The module's own directory for the package's data; an explicit other directory (a copy) is used as given.
        return self.directorio if _es_el_del_paquete(data_dir) else data_dir

    def _fichero_de(self, ejercicio: int, datos: dict[int, Any]) -> dict[str, Any] | None:
        propio = datos.get(ejercicio)
        if isinstance(propio, dict):
            return propio
        path = self.directorio / self.fichero.format(ejercicio=ejercicio)
        leido: dict[str, Any] | None = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        return leido

    def citas_de(self, data_dir: Path, objetivo: Objetivo, ejercicio: int) -> list[str]:
        return super().citas_de(self.directorio_de(data_dir), objetivo, ejercicio)

    def controlar(
        self, valor: Any, citas: list[str], objetivo: Objetivo, *, efectos_desde: date | None, datos: dict[int, Any]
    ) -> tuple[BaseModel | None, Control]:
        candidato, control = super().controlar(valor, citas, objetivo, efectos_desde=efectos_desde, datos=datos)
        control.revisar.append(REVISION_HUMANA)
        return candidato, control

    def guia_casos(self, objetivo: Objetivo) -> str:
        texto = super().guia_casos(objetivo)
        return texto.replace(
            "`importe_esperado` is the value THE LAW gives for that result line (euros with a dot; a yes/no line is "
            "1 or 0). `ejercicio` is filled in by the program",
            "`importe_esperado` is the value THE LAW gives for that result line: a date as the number AAAAMMDD "
            "(15 January 2031 -> 20310115), a number of days as it is, a yes/no line as 1 or 0. Dates in `entrada` "
            "are AAAA-MM-DD. There is no tax year",
        )

    def evaluar_caso(self, modelo: BaseModel, datos: Any, objetivo: Objetivo) -> tuple[Decimal, str | None]:
        assert isinstance(modelo, otros.Candidato)
        if not self.evaluable:
            raise ValueError("ninguna calculadora evalúa este grupo")
        if not isinstance(datos, dict) or not isinstance(datos.get("entrada"), dict):
            raise ValueError("datos_json debe ser {«calculo», «entrada», «resultado»}")
        ajenos = sorted(set(datos) - {"calculo", "entrada", "resultado"})
        if ajenos:
            raise ValueError(f"claves desconocidas: {', '.join(ajenos)}")
        calculos = self.familia.calculos
        nombre = datos.get("calculo") or (next(iter(calculos)) if len(calculos) == 1 else None)
        if nombre not in calculos:
            raise ValueError(f"«calculo» debe ser uno de: {', '.join(calculos)}")
        entrada_modelo, funcion = calculos[nombre].cargar()
        try:
            resultado = funcion(entrada_modelo.model_validate(dict(datos["entrada"])), modelo.params)
        except ValidationError as exc:
            raise ValueError("; ".join(otros._errores(exc))) from exc
        except Exception as exc:  # a case the calculator cannot run is the case's problem, reported as such
            raise ValueError(f"la calculadora no pudo evaluarlo ({type(exc).__name__}: {exc})") from exc
        return otros._linea(resultado, str(datos.get("resultado") or "")), None


def _construir() -> tuple[list[Any], dict[str, FicheroLegal], dict[tuple[str, str], GrupoDeModulo]]:
    familias: list[Any] = []
    por_clave: dict[str, FicheroLegal] = {}
    adaptadores: dict[tuple[str, str], GrupoDeModulo] = {}
    for legal in ficheros_legales():
        if legal.recodificable is None or legal.ruta is None:
            continue
        familia = legal.recodificable.familia()
        familias.append(familia)
        por_clave[familia.clave] = legal
        for modelo in familia.modelos():
            adaptador = GrupoDeModulo(familia, modelo, legal)
            registrar(adaptador)
            adaptadores[(familia.clave, modelo.__name__)] = adaptador
    # ``construir_objetivo`` finds them by their kind (``adaptadores_otros.adaptador_del_grupo``).
    return familias, por_clave, adaptadores


FAMILIAS, _LEGALES, ADAPTADORES = _construir()


def directorio_de_familia(familia: Any, data_dir: Path) -> Path:
    legal = _LEGALES[familia.clave]
    assert legal.ruta is not None
    return legal.ruta.parent if _es_el_del_paquete(data_dir) else data_dir


def _familias(data_dir: Path, familias: Sequence[Any] | None) -> list[tuple[Any, Path]]:
    """(family, directory of its file) for a run over ``data_dir``: none for a copy of the tax files unless named."""
    if familias is None and not _es_el_del_paquete(data_dir):
        return []
    return [(f, directorio_de_familia(f, data_dir)) for f in (FAMILIAS if familias is None else familias)]


# --------------------------------------------------------------------------- #
# Finders the loop's generic commands call (``adaptadores._otros_modulos``)
# --------------------------------------------------------------------------- #


def objetivos_de_verificacion(
    ejercicios: Sequence[int] | None,
    texto_de: TextoDe,
    *,
    data_dir: Path = DATA_DIR,
    ambito: str | None = None,
    tipos: Sequence[str] | None = None,
    familias: Sequence[Any] | None = None,
) -> Hallazgo:
    """The modules' groups whose quotes are no longer in their law, as targets for the loop (a module's file has no
    year: ``ejercicios`` does not filter it)."""
    hallazgo = Hallazgo()
    if ambito not in (None, "estado"):
        return hallazgo
    for familia, directorio in _familias(data_dir, familias):
        propio = otros.objetivos_de_verificacion(None, texto_de, data_dir=directorio, tipos=tipos, familias=[familia])
        hallazgo.objetivos += propio.objetivos
        hallazgo.sin_adaptador += propio.sin_adaptador
        hallazgo.errores += propio.errores
        hallazgo.fuera_de_alcance += propio.fuera_de_alcance
    return hallazgo


def objetivo_de_ruta(
    familia: str | Any,
    ejercicios: Sequence[int] | None,
    ruta: Sequence[str],
    *,
    data_dir: Path = DATA_DIR,
    boe_id: str | None = None,
    version: str | None = None,
    anotaciones: str = "",
) -> Objetivo:
    """A module's group asked for by the operator ("caducidades:itv")."""
    from hacienda_gpt.tax.recodificacion.nucleo import RecodificacionError

    if isinstance(familia, str):
        familia = next((f for f in FAMILIAS if f.clave == familia), None)
        if familia is None:
            raise RecodificacionError("Ningún módulo del hogar declara ese fichero como recodificable.")
    directorio = directorio_de_familia(familia, data_dir)
    return otros.objetivo_de_ruta(
        familia, None, ruta, data_dir=directorio, boe_id=boe_id, version=version, anotaciones=anotaciones
    )


def todos_los_objetivos(
    ejercicios: Sequence[int] | None = None, *, data_dir: Path = DATA_DIR, boe_id: str | None = None
) -> Hallazgo:
    """Every re-encodable module group (optionally of one law), whether or not it matches its law."""
    hallazgo = Hallazgo()
    for familia, directorio in _familias(data_dir, None):
        ficheros = otros._ficheros(familia, directorio)
        for ruta in list(dict.fromkeys(tuple(r) for raw in ficheros.values() for r in otros.rutas_de_grupos(raw))):
            grupo = next((otros.leer_ruta(raw, ruta) for raw in ficheros.values() if otros.leer_ruta(raw, ruta)), None)
            citas = grupo.get("evidencia", []) if isinstance(grupo, dict) else []
            if boe_id is not None and not any(isinstance(c, dict) and c.get("boe_id") == boe_id for c in citas):
                continue
            try:
                hallazgo.objetivos.append(
                    otros.construir_objetivo(familia, list(ruta), ficheros, boe_id=boe_id, origen="norma")
                )
            except Exception as exc:  # RecodificacionError, ValidationError: reported, the others go on
                hallazgo.fuera_de_alcance.append(str(exc)[:300])
    return hallazgo


def revisar_grupos(familia: Any, textos: dict[str, str]) -> list[otros.GrupoRevisado]:
    """Every shipped group of a module's file through the gate with its own quotes (``params-gate`` for modules)."""
    from hacienda_gpt.tax.recodificacion.nucleo import SIN_EJERCICIO

    directorio = directorio_de_familia(familia, DATA_DIR)
    raw = json.loads((directorio / familia.fichero).read_text(encoding="utf-8"))
    return otros.revisar_grupos_vigentes(familia, raw, textos, ejercicio=SIN_EJERCICIO)


def solo_persona() -> dict[str, str]:
    """The declared files the loop does not re-encode, and why (for the operator's report)."""
    return {f.nombre: f.solo_persona for f in ficheros_legales() if f.recodificable is None}


__all__ = [
    "ADAPTADORES",
    "FAMILIAS",
    "REVISION_HUMANA",
    "GrupoDeModulo",
    "directorio_de_familia",
    "objetivo_de_ruta",
    "objetivos_de_verificacion",
    "revisar_grupos",
    "solo_persona",
    "todos_los_objetivos",
]
