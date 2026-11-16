"""The tax module object the registry discovers (``hacienda_gpt.modulos.fiscal.MODULO``).

What is particular about it is expressed through the contract's optional hooks, never by its key:

* ``herramientas_directas``: the central agent offers its tools directly (right after the corpus search), with
  the run's evidence ledger and the stored case, instead of consulting a specialist;
* ``ayuda_registrar_hechos`` / ``hechos_estructurados``: the shapes of the structured tax facts of the profile
  (``descendientes``, ``datos_conyuge``, ``preferencias_optimizacion``), which the core's ``registrar_hechos``
  documents and validates;
* ``ejecutar_en_bandeja``: the daily inbox runs the tax jobs, which write the notifications exactly as before
  (kinds ``plazo`` / ``fin_de_ejercicio``, same keys). ``avisos`` gives the same notices through the contract,
  computed on a throwaway store: the contract's context has no access to the notices already written, which
  the year-end job needs to avoid repeating its request for figures.

Its facts are the core's tax vocabulary (``decision/fact_extractor.EXPECTED_FACT_TYPE``), shared by every
module and edited in «Tu expediente», so ``hechos`` is empty here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from functools import cached_property
from typing import Any

from hacienda_gpt.decision.schemas import CaseState
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo, FuenteModulo, HechoModulo, HerramientaModulo
from hacienda_gpt.modulos.fiscal.avisos import TIPOS_EN_BANDEJA, ejecutar_para_caso
from hacienda_gpt.modulos.fiscal.calculo.optimizador import detalle_a_texto
from hacienda_gpt.modulos.fiscal.especificaciones import AYUDA_REGISTRAR_HECHOS
from hacienda_gpt.modulos.fiscal.herramientas import HERRAMIENTAS, caso_desde_perfil
from hacienda_gpt.proactive.store import NotificationStore
from hacienda_gpt.tax.profile import DETALLE_FACT_TYPES


def _normalizador(nombre: str) -> Callable[[Any], str]:
    def normalizar(valor: Any) -> str:
        return detalle_a_texto(nombre, valor)  # DatosInvalidosError (a ValueError) with a Spanish sentence

    return normalizar


class ModuloFiscal:
    clave = "fiscal"
    nombre = "Impuestos"
    descripcion = (
        "Impuestos de las personas en España: IRPF (cálculo, deducciones estatales y autonómicas, declaración "
        "conjunta, optimización antes de fin de año), autónomos (modelos 130 y 303), Sociedades, Patrimonio, "
        "Sucesiones y Donaciones, calendario fiscal y normativa, doctrina y jurisprudencia tributarias."
    )
    instrucciones = (
        "Eres el asesor fiscal. Calcula siempre con las calculadoras deterministas y cita su resultado; nunca "
        "calcules impuestos de cabeza ni sumes ahorros calculados por separado."
    )
    hechos: Sequence[HechoModulo] = ()
    herramientas: Sequence[HerramientaModulo] = HERRAMIENTAS
    # The advisor's own tools: offered directly by the central agent (see agent/tools.py).
    herramientas_directas = True
    ayuda_registrar_hechos = AYUDA_REGISTRAR_HECHOS
    hechos_estructurados: Mapping[str, Callable[[Any], str]] = {
        nombre: _normalizador(nombre) for nombre in DETALLE_FACT_TYPES
    }
    tipos_en_bandeja: Sequence[str] = TIPOS_EN_BANDEJA

    @cached_property
    def fuentes(self) -> Sequence[FuenteModulo]:  # type: ignore[override]
        from hacienda_gpt.tax.fuentes import normas_de_parametros

        return tuple(
            FuenteModulo(
                identificador=norma.boe_id,
                descripcion=norma.norma,
                url=f"https://www.boe.es/buscar/act.php?id={norma.boe_id}",
            )
            for norma in normas_de_parametros()
        )

    def avisos(self, contexto: ContextoModulo) -> list[AvisoModulo]:
        """The tax notices for this profile (deadlines and year-end plan), through the contract."""
        caso = caso_desde_perfil(contexto.perfil, hoy=contexto.hoy, case_id=contexto.case_id)
        store = NotificationStore(":memory:")
        try:
            ejecutar_para_caso(caso, hoy=contexto.hoy, store=store)
            notas = store.list(caso.case_id)
        finally:
            store.close()
        return [
            AvisoModulo(
                clave=n.key,
                titulo=n.title,
                cuerpo=n.body,
                gravedad=n.severity,
                acciones=tuple(n.actions),
                fuentes=tuple(n.sources),
                importe=n.amount,
                vence=n.due_date,
                sustituye_prefijo=":".join(n.key.split(":")[:2]) + ":" if n.kind == "fin_de_ejercicio" else None,
            )
            for n in reversed(notas)
        ]

    def ejecutar_en_bandeja(self, case: CaseState, *, hoy: date, store: NotificationStore) -> dict[str, int]:
        """The tax jobs, written straight to the inbox exactly as before the modules existed."""
        return ejecutar_para_caso(case, hoy=hoy, store=store)


MODULO = ModuloFiscal()

__all__ = ["MODULO", "ModuloFiscal"]
