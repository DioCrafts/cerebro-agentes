"""Stable keys of the IRPF computation lines (kept apart from ``irpf`` so ``obligacion`` can import them)."""

from __future__ import annotations


class ClaveLinea:
    """Stable keys of the lines other calculations read back from an :class:`IRPFResult`."""

    MINIMO_DESCENDIENTES = "minimo_descendientes"
    MINIMO_ASCENDIENTES = "minimo_ascendientes"
    MINIMO_DISCAPACIDAD_DESCENDIENTES = "minimo_discapacidad_descendientes"
    MINIMO_DISCAPACIDAD_ASCENDIENTES = "minimo_discapacidad_ascendientes"
    REDUCCION_ARRENDAMIENTO_VIVIENDA = "reduccion_arrendamiento_vivienda"
    REDUCCION_TRABAJO = "reduccion_trabajo"
    RENDIMIENTO_NETO_TRABAJO = "rendimiento_neto_reducido_trabajo"
    REDUCCION_CONJUNTA = "reduccion_tributacion_conjunta"
    REDUCCION_PREVISION_SOCIAL = "reduccion_prevision_social"
    DEDUCCION_DONATIVOS = "deduccion_donativos"
    DEDUCCION_VIVIENDA_TRANSITORIA = "deduccion_vivienda_transitoria"
    DEDUCCION_ALQUILER_ESTATAL = "deduccion_alquiler_estatal"
    DEDUCCION_TRABAJO = "deduccion_trabajo"
    DEDUCCION_MATERNIDAD = "deduccion_maternidad"
    MADRID_NACIMIENTO = "madrid_nacimiento"
    MADRID_ALQUILER = "madrid_alquiler"
    MADRID_GASTOS_EDUCATIVOS = "madrid_gastos_educativos"
    MADRID_CUIDADO = "madrid_cuidado"
    MADRID_DONATIVOS = "madrid_donativos"

    @staticmethod
    def autonomica(articulo: str) -> str:
        """Key of a data-driven regional deduction (one per article of its community)."""
        return f"autonomica:{articulo}"
