"""The tax module object the registry discovers (``asistente.ambitos.fiscal.MODULO``).

A contract v2 domain (``modulos/ganchos.py``): what is particular about it is DECLARED in ``ganchos``, never
told apart by its key:

* ``obligatorio``: always on (the account was opened for it; the chat is its advisor);
* ``agente.directas`` (no specialist): the central agent offers its read-only tools directly (right after the
  corpus search), with the run's evidence ledger and the stored case, under the names they always had;
* ``agente.ayuda_registrar_hechos`` / ``agente.hechos_estructurados``: the shapes of the structured tax facts of
  the profile (``descendientes``, ``datos_conyuge``, ``preferencias_optimizacion``), which the core's
  ``registrar_hechos`` documents and validates;
* ``bandeja``: the daily inbox runs the tax jobs, which write the notifications exactly as before (kinds
  ``plazo`` / ``fin_de_ejercicio``, same keys); new tax law (a BOE norm, an analysed change of a consolidated law)
  is notified on the bus by :func:`al_evento_en_bandeja`, and :func:`proxima_revision` says when the case must be
  woken next (the day a filing deadline enters its reminder window, or 1 October for the year-end plan).
  ``avisos`` gives the same notices through the contract, computed on a throwaway store: the contract's context
  has no access to the notices already written, which the year-end job needs to avoid repeating its request for
  figures;
* ``eventos``: profile changes, dates, new BOE norms and analysed changes of the law; it reads every shared fact;
* ``ficheros_legales``: the passages kept in code (``conocimiento/citas_fiscales.py``);
* ``fuentes_vigiladas_aparte``: its sources are the tax laws, watched by the tax parameters' ``params-check``.

The v1 attribute names (``herramientas_directas``, ``ayuda_registrar_hechos``, ``hechos_estructurados``,
``tipos_en_bandeja``, ``ejecutar_en_bandeja``) are kept as views of the declaration, for older callers.

Its facts are the core's tax vocabulary (``decision/fact_extractor.EXPECTED_FACT_TYPE``), shared by every
module and edited in «Tu expediente», so ``hechos`` is empty here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from functools import cached_property
from typing import Any

from asistente.ambitos.fiscal.avisos import TIPOS_EN_BANDEJA, ejecutar_para_caso
from asistente.ambitos.fiscal.calculo.optimizador import detalle_a_texto
from asistente.ambitos.fiscal.especificaciones import AYUDA_REGISTRAR_HECHOS
from asistente.ambitos.fiscal.herramientas import HERRAMIENTAS, caso_desde_perfil
from asistente.ambitos.fiscal.profile import DETALLE_FACT_TYPES
from asistente.nucleo.decision.schemas import CaseState
from asistente.nucleo.eventos.tipos import CAMBIO_NORMATIVO, FECHA_UMBRAL, NORMA_BOE, PERFIL_CAMBIADO
from asistente.nucleo.modulos.contrato import AvisoModulo, ContextoModulo, FuenteModulo, HechoModulo, HerramientaModulo
from asistente.nucleo.modulos.ganchos import (
    DatoOfrecido,
    DatoPedido,
    Declaracion,
    Ganchos,
    GanchosAgente,
    GanchosBandeja,
    GanchosEventos,
)
from asistente.nucleo.proactive.store import NotificationStore

# The reminder window of ``recordatorios_de_plazos`` (its default ``dias``).
DIAS_AVISO_PLAZO = 30
# Event types (asistente/nucleo/eventos/tipos.py): profile changes, dates, new BOE norms, analysed law changes.
EVENTOS = (PERFIL_CAMBIADO, FECHA_UMBRAL, NORMA_BOE, CAMBIO_NORMATIVO)


def proxima_revision(case: CaseState, *, hoy: date) -> date | None:
    """The next day the tax jobs could write something new for this case without any event.

    A filing deadline is reminded when it is at most ``DIAS_AVISO_PLAZO`` days away, so the case is woken
    the day the next one enters that window; the year-end plan starts on 1 October of the case's year.
    """
    from asistente.ambitos.fiscal.avisos import _CIERRE_EJERCICIO_ID, facts_de
    from asistente.ambitos.fiscal.calculo.calendario import proximos_vencimientos
    from asistente.nucleo.decision.schemas import parse_fiscal_year

    candidatos: list[date] = []
    for vencimiento in proximos_vencimientos(facts_de(case), hoy=hoy, dias=400):
        if vencimiento.id.split(":", 1)[0] == _CIERRE_EJERCICIO_ID:
            continue  # never reminded on its own: the year-end plan covers it
        entra = vencimiento.fecha - timedelta(days=DIAS_AVISO_PLAZO)
        if entra > hoy:
            candidatos.append(entra)
    inicio_plan = date(hoy.year, 10, 1)
    if parse_fiscal_year(case.tax_period) == hoy.year and hoy < inicio_plan:
        candidatos.append(inicio_plan)
    return min(candidatos, default=None)


def al_evento_en_bandeja(evento: Any, case: CaseState, *, hoy: date, store: NotificationStore) -> int | None:
    """New tax law matched against this case (same criteria as ever); None: run the daily tax jobs."""
    tipo = getattr(evento, "tipo", None)
    if tipo == NORMA_BOE:
        from asistente.nucleo.proactive.novedades import Novedad, notificar_novedad

        novedad = Novedad.model_validate(evento.payload["novedad"])
        return int(notificar_novedad(novedad, [case], store))
    if tipo == CAMBIO_NORMATIVO:
        from asistente.nucleo.proactive.impacto import CambioNormativo, notificar_cambio

        cambio = CambioNormativo.model_validate(evento.payload["cambio"])
        return int(notificar_cambio(cambio, [case], store))
    return None


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

    @cached_property
    def ganchos(self) -> Ganchos:
        """Contract v2: everything particular about the tax module, declared (see the module docstring)."""
        from asistente.conocimiento.citas_fiscales import FICHEROS_LEGALES

        return Ganchos(
            obligatorio=True,
            eventos=GanchosEventos(tipos=EVENTOS, hechos_leidos=("*",)),
            bandeja=GanchosBandeja(
                ejecutar=self.ejecutar_en_bandeja,
                tipos=TIPOS_EN_BANDEJA,
                # Written on its behalf by the core's law watch (proactive/novedades.py, proactive/impacto.py).
                otros_tipos=("cambio_normativo",),
                al_evento=al_evento_en_bandeja,
                proxima_revision=proxima_revision,
            ),
            agente=GanchosAgente(
                # The advisor's own tools (all read-only), offered directly under the names they always had.
                directas=tuple(h.spec.name for h in HERRAMIENTAS),
                especialista=False,
                ayuda_registrar_hechos=AYUDA_REGISTRAR_HECHOS,
                hechos_estructurados={nombre: _normalizador(nombre) for nombre in DETALLE_FACT_TYPES},
            ),
            ficheros_legales=tuple(FICHEROS_LEGALES),
            # Tax documents (certificates, payslips…) are read by the documents capability.
            capacidades=("documentos.leer_campos", "investigacion"),
            # Its laws are the tax parameters' (``tax/fuentes.py``), checked by ``params-check`` on their own.
            fuentes_vigiladas_aparte=True,
            # What it promises (capabilities), its own evaluation and the data it serves to / asks of others.
            declaracion=Declaracion(
                capacidades=(
                    "Calcula IRPF, declaración conjunta, modelos 130 y 303, Sociedades, Patrimonio y Sucesiones y Donaciones con los parámetros del BOE.",
                    "Dice si hay obligación de declarar y propone el plan de fin de ejercicio.",
                    "Mantiene el calendario fiscal del hogar.",
                ),
                evaluacion="eval_data/modulos/fiscal.jsonl",
                ofrece=(
                    DatoOfrecido(
                        nombre="calendario",
                        descripcion="Obligaciones fiscales de los próximos 90 días.",
                        origen="herramienta:calendario_fiscal",
                        argumentos={"dias": 90},
                    ),
                ),
                pide=(
                    DatoPedido(
                        modulo="hipoteca",
                        dato="pagos_del_ejercicio",
                        para="Deducción transitoria por vivienda habitual adquirida antes de 2013.",
                    ),
                    DatoPedido(
                        modulo="ayudas",
                        dato="prestaciones",
                        para="Saber qué prestaciones públicas cobra o puede cobrar el hogar (rentas exentas y deducciones del art. 81 LIRPF).",
                    ),
                ),
            ),
        )

    # v1 views of the declaration (older callers and tests read them).
    @property
    def herramientas_directas(self) -> bool:
        return bool(self.ganchos.agente.directas)

    @property
    def ayuda_registrar_hechos(self) -> str:
        return self.ganchos.agente.ayuda_registrar_hechos

    @property
    def hechos_estructurados(self) -> Mapping[str, Callable[[Any], str]]:
        return self.ganchos.agente.hechos_estructurados

    @property
    def tipos_en_bandeja(self) -> Sequence[str]:
        return TIPOS_EN_BANDEJA

    @property
    def eventos(self) -> Sequence[str]:
        return EVENTOS

    @cached_property
    def fuentes(self) -> Sequence[FuenteModulo]:  # type: ignore[override]
        from asistente.conocimiento.fuentes import normas_de_parametros

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

__all__ = ["DIAS_AVISO_PLAZO", "EVENTOS", "MODULO", "ModuloFiscal", "al_evento_en_bandeja", "proxima_revision"]
