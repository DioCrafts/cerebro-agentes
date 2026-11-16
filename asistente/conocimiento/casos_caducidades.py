"""The ``caducidades`` rules in the agentic re-encoding loop: their family and the reviewer's test cases.

A reviewer proves (or disproves) a proposed rule by running cases through the module's own calculator
(``asistente.ambitos.caducidades.calculo``, used as it is): a DNI issued at 29 and at 30, a car on the day it
turns four, a purchase delivered on a given day… Each case names a calculation, its inputs and the result line it
compares. The calculator returns dates; a case compares them as AAAAMMDD numbers (``fecha``: 20310115), or as the
days from the start of the period (``vigencia_dias``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

_IGNORAR = frozenset({"fuente", "evidencia", "avisos", "recodificada_el", "tramite", "excepciones"})


class _Caso(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CasoDNI(_Caso):
    fecha_expedicion: date
    fecha_nacimiento: date


class CasoPasaporte(_Caso):
    fecha_expedicion: date
    fecha_nacimiento: date
    tiene_dni: bool | None = None


class CasoPermiso(_Caso):
    clases: list[str]
    fecha_inicio_vigencia: date
    fecha_nacimiento: date
    es_licencia: bool = False


class CasoITV(_Caso):
    hoy: date
    categoria: str
    fecha_primera_matriculacion: date
    fecha_ultima_itv: date | None = None


class CasoCertificado(_Caso):
    fecha_expedicion: date


class CasoSeguro(_Caso):
    hoy: date
    fecha_vencimiento: date
    periodicidad_meses: int = 12
    es_vida: bool = False


class CasoCompra(_Caso):
    hoy: date
    fecha_compra: date
    fecha_entrega: date | None = None
    tipo_bien: Literal["bien", "digital", "segunda_mano"] = "bien"
    modalidad: Literal["tienda", "distancia", "fuera_establecimiento", "visita_no_solicitada", "excursion"] = "tienda"


class ResultadoCaso(BaseModel):
    """Dates of a calculation as numbers a test case can compare (AAAAMMDD; 0 when there is none)."""

    model_config = ConfigDict(extra="forbid")

    fecha: int = 0
    # Days from the start of the period (issue, first registration, purchase) to ``fecha``.
    vigencia_dias: int = 0
    accion_hasta: int = 0
    permanente: bool = False
    fin_desistimiento: int = 0
    fin_presuncion: int = 0
    fin_garantia: int = 0


def _n(dia: date | None) -> int:
    return int(dia.strftime("%Y%m%d")) if dia else 0


def _vencimiento(v: Any, desde: date) -> ResultadoCaso:
    return ResultadoCaso(
        fecha=_n(v.fecha),
        vigencia_dias=(v.fecha - desde).days if v.fecha else 0,
        accion_hasta=_n(v.accion_hasta),
        permanente=bool(v.permanente),
    )


def _calculos() -> dict[str, tuple[type[BaseModel], Callable[[Any, Any], ResultadoCaso], str]]:
    from asistente.ambitos.caducidades import calculo as c

    return {
        "dni": (
            CasoDNI,
            lambda e, r: _vencimiento(
                c.calcular_dni(r, fecha_expedicion=e.fecha_expedicion, fecha_nacimiento=e.fecha_nacimiento),
                e.fecha_expedicion,
            ),
            "expiry of a DNI (art. 6.1 RD 1553/2005) from the issue date and the holder's birth date",
        ),
        "pasaporte": (
            CasoPasaporte,
            lambda e, r: _vencimiento(
                c.calcular_pasaporte(
                    r,
                    fecha_expedicion=e.fecha_expedicion,
                    fecha_nacimiento=e.fecha_nacimiento,
                    tiene_dni=e.tiene_dni,
                ),
                e.fecha_expedicion,
            ),
            "expiry of a passport (art. 5 RD 896/2003)",
        ),
        "permiso": (
            CasoPermiso,
            lambda e, r: _vencimiento(
                c.calcular_permiso(
                    r,
                    clases=e.clases,
                    fecha_inicio_vigencia=e.fecha_inicio_vigencia,
                    fecha_nacimiento=e.fecha_nacimiento,
                    es_licencia=e.es_licencia,
                ),
                e.fecha_inicio_vigencia,
            ),
            "expiry of a driving licence (art. 12 RGC): classes such as AM, A1, A2, A, B, C1, C, D1, D, BE, CE",
        ),
        "itv": (
            CasoITV,
            lambda e, r: _vencimiento(
                c.calcular_itv(
                    r,
                    hoy=e.hoy,
                    categoria=e.categoria,
                    fecha_primera_matriculacion=e.fecha_primera_matriculacion,
                    fecha_ultima_itv=e.fecha_ultima_itv,
                ),
                e.fecha_primera_matriculacion,
            ),
            "next periodic vehicle inspection (art. 6 RD 920/2017); `categoria` is a key of `categorias`",
        ),
        "certificado": (
            CasoCertificado,
            lambda e, r: _vencimiento(
                c.calcular_certificado(r, fecha_expedicion=e.fecha_expedicion), e.fecha_expedicion
            ),
            "maximum validity of a qualified electronic certificate (art. 4 Ley 6/2020)",
        ),
        "seguro": (
            CasoSeguro,
            lambda e, r: _vencimiento(
                c.calcular_seguro(
                    r,
                    hoy=e.hoy,
                    fecha_vencimiento=e.fecha_vencimiento,
                    periodicidad_meses=e.periodicidad_meses,
                    es_vida=e.es_vida,
                ),
                e.hoy,
            ),
            "next renewal of an insurance policy and the last day to oppose it (`accion_hasta`, art. 22 LCS)",
        ),
        "compra": (
            CasoCompra,
            lambda e, r: _compra(c, e, r),
            "withdrawal and legal guarantee of a consumer purchase (arts. 102-105 and 120-124 TRLGDCU): "
            "`fin_desistimiento`, `fin_presuncion`, `fin_garantia`",
        ),
    }


def _compra(c: Any, e: CasoCompra, reglas: Any) -> ResultadoCaso:
    r = c.calcular_compra(
        reglas,
        hoy=e.hoy,
        descripcion="caso de prueba",
        fecha_compra=e.fecha_compra,
        fecha_entrega=e.fecha_entrega,
        tipo_bien=e.tipo_bien,
        modalidad=e.modalidad,
    )
    return ResultadoCaso(
        fecha=_n(r.fin_garantia),
        vigencia_dias=(r.fin_garantia - r.fecha_entrega).days if r.fin_garantia else 0,
        fin_desistimiento=_n(r.fin_desistimiento),
        fin_presuncion=_n(r.fin_presuncion),
        fin_garantia=_n(r.fin_garantia),
    )


def evaluar(nombre: str, entrada: dict[str, Any], reglas: Any) -> ResultadoCaso:
    """Run one case through the module's calculator (for tests and for the adapter)."""
    modelo, funcion, _ = _calculos()[nombre]
    return funcion(modelo.model_validate(entrada), reglas)


def familia_caducidades() -> Any:
    """The ``Familia`` the generic adapter of the loop needs for ``modulos/caducidades/data/reglas.json``."""
    from asistente.ambitos.caducidades import reglas as modulo
    from asistente.conocimiento.recodificacion.adaptadores_otros import Calculo, Familia
    from asistente.conocimiento.recodificacion.control import Reglas

    def cargador(
        modelo: type[BaseModel], funcion: Callable[[Any, Any], ResultadoCaso]
    ) -> Callable[[], tuple[type[BaseModel], Callable[[Any, Any], Any]]]:
        return lambda: (modelo, funcion)

    calculos = {
        nombre: Calculo(cargar=cargador(modelo, funcion), que=que)
        for nombre, (modelo, funcion, que) in _calculos().items()
    }
    return Familia(
        clave="caducidades",
        titulo="Caducidades",
        fichero=modulo.DATA_PATH.name,
        modulo=modulo,
        raiz=modulo.Reglas,
        estilo="cifras",
        # Every figure is a whole number of years, months or days, an age or a count: the walker looks for it in
        # figures or words (n or n ± 1, "menores de 30" is encoded as 30); the file's own check, which knows the
        # spellings ("Bienal" = 24 months), has the last word on the frequencies.
        reglas=Reglas(euros=frozenset(), ignorar=_IGNORAR),
        cuentas=frozenset(),
        tolerados=frozenset({"meses"}),
        comprobar=lambda params, _ruta: modulo.comprobar_evidencias(params),
        prefijo=lambda ruta: ruta[0],
        calculos=calculos,
        # Art. 5.1 of the Civil Code (how periods are counted) has no figure a case could test.
        sin_evaluador=frozenset({"ReglaComputo"}),
        pistas={
            "ReglaDocumentoPorEdad": "`dni` (group `dni`) or `pasaporte` (group `pasaporte`) with birth dates that "
            "put the holder just below and at each age limit on the issue date; result line `fecha` (AAAAMMDD) "
            "or `vigencia_dias`, `permanente` (1/0).",
            "ReglaPermiso": "`permiso` for each group of classes and around the age limit; result line `fecha`.",
            "ReglaITV": "`itv` for each category with first registrations just below and at each age limit; "
            "result line `fecha`.",
            "ReglaGarantia": "`compra` with delivery dates before and after the date the regime applies from; "
            "result lines `fin_garantia`, `fin_presuncion`.",
            "ReglaDesistimiento": "`compra` at a distance (`modalidad`: distancia); result line `fin_desistimiento`.",
            "ReglaSeguro": "`seguro`; result lines `fecha` (renewal) and `accion_hasta` (last day to oppose).",
            "ReglaCertificado": "`certificado`; result line `fecha`.",
        },
        devengo="fin",
        leer_cifra=lambda literal, _ruta: modulo.cifra_a_numero(literal),
        hojas=modulo._hojas,
        limpiar=modulo.cargar_reglas.cache_clear,
    )


__all__ = ["ResultadoCaso", "evaluar", "familia_caducidades"]
