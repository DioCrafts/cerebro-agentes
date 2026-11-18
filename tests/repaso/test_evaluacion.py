"""The evaluation's side of the pass (``repaso.evaluacion.ejecutar``): every suite case of the ``repaso`` category
passes with the deterministic rendering and with a model behind the gate; the model can never add a proposal."""

from __future__ import annotations

from typing import Any

import pytest

from asistente.nucleo.agent.providers.base import ProviderError
from asistente.nucleo.evaluacion import suite as s
from asistente.nucleo.evaluacion.ejecutores import SalidaRepaso, ejecutar_repaso
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion, Ejecucion
from asistente.nucleo.repaso.evaluacion import SIN_NOVEDADES, a_senal, ejecutar
from .conftest import Guionizado, puntos_de, salida_valida, valida

CASOS = [c for c in s.cargar_casos() if c.categoria == "repaso"]


class SinModelo:
    """What the suite's replay provider does for this call: it cannot answer it."""

    name = "reproduccion"
    model = "guion"

    def complete_json(self, **_: Any) -> Any:
        raise ProviderError("El modo reproducción no responde a «repaso».")


def _fallos(caso: CasoEvaluacion, salida: SalidaRepaso) -> list[str]:
    ejecucion = Ejecucion(respuesta=salida.texto, modo="verified", repaso=salida.model_dump(mode="json"))
    return s.comprobar(caso, ejecucion, s.cifras_esperadas(caso))


def test_the_suite_has_its_repaso_cases() -> None:
    assert len(CASOS) >= 8 and all(c.entrada_repaso is not None for c in CASOS)


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_every_repaso_case_passes_with_the_deterministic_rendering(caso: CasoEvaluacion) -> None:
    salida = ejecutar_repaso(caso, proveedor=SinModelo())
    assert _fallos(caso, salida) == []
    assert salida.propuestas == []  # no layer behind the evaluation's signals produced one


@pytest.mark.parametrize("caso", CASOS, ids=lambda c: c.id)
def test_every_repaso_case_passes_with_a_model_behind_the_gate(caso: CasoEvaluacion) -> None:
    modelo = Guionizado(valida, valida)
    salida = ejecutar_repaso(caso, proveedor=modelo)
    assert _fallos(caso, salida) == []
    if salida.texto != SIN_NOVEDADES:
        assert "Esta semana hay cosas que conviene mirar con calma." in salida.texto


def test_a_model_cannot_add_a_proposal_nor_mention_what_was_left_out() -> None:
    caso = next(c for c in CASOS if c.id == "repaso-plazo-303")
    assert caso.entrada_repaso is not None

    def tramposo(user: str) -> dict[str, Any]:
        salida = salida_valida(puntos_de(user))
        salida["puntos"][0]["propuesta_id"] = "accion:acc_inventada"
        salida["puntos"].append(
            {
                "punto_id": "p2",
                "texto": "Mira el boletín de ofertas.",
                "por_que": "Es publicidad.",
                "propuesta_id": None,
            }
        )
        return salida

    salida = ejecutar(caso.entrada_repaso.model_dump(mode="json"), proveedor=Guionizado(tramposo, tramposo))
    assert salida["propuestas"] == []
    assert {x for a in salida["afirmaciones"] for x in a["senales"]} == {"s1"}  # the newsletter is not a point
    assert "boletín" not in salida["texto"].lower()


def test_the_signal_kinds_map_to_the_pass() -> None:
    from datetime import date

    hoy = date(2026, 11, 16)
    plazo = a_senal({"id": "s1", "tipo": "plazo", "titulo": "Modelo 303", "fecha": "2026-11-20"}, hoy=hoy)
    assert plazo.oficial and plazo.irreversible and plazo.hechos.vence == date(2026, 11, 20) and plazo.hechos.dias == 4
    correo = a_senal({"id": "s2", "tipo": "correo", "titulo": "Boletín"}, hoy=hoy)
    assert correo.tipo == "novedad" and correo.no_confiable
    recibo = a_senal({"id": "s3", "tipo": "correo", "titulo": "Recibo", "importe": "60.00"}, hoy=hoy)
    assert recibo.tipo == "aviso" and str(recibo.hechos.importe) == "60.00"
    boe = a_senal({"id": "s4", "tipo": "boe", "titulo": "Deducción", "fecha": "2026-11-10"}, hoy=hoy)
    assert boe.normativa and boe.hechos.fecha == date(2026, 11, 10) and boe.hechos.vence is None
    detalle = a_senal({"id": "s5", "tipo": "luz", "titulo": "Factura", "detalle": "84,20 € frente a 61,02 €"}, hoy=hoy)
    assert detalle.titulo == "Factura — 84,20 € frente a 61,02 €"
