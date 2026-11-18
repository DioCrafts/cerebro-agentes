"""Scripted providers for running the council's REAL engine (F's ``Deliberacion``) and the single pass on cases."""

from __future__ import annotations

from collections.abc import Callable
import json
import threading
from typing import Any

from asistente.nucleo.evaluacion import consejo_eval as ce
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion
from asistente.nucleo.evaluacion.modelos_consejo import RespuestaDecision

Puntuador = Callable[[int, dict[str, Any]], int]


def neutro(_posicion: int, _propuesta: dict[str, Any]) -> int:
    return 4


def primacia(posicion: int, _propuesta: dict[str, Any]) -> int:
    """A reviewer model that likes whatever it reads first."""
    return 5 if posicion == 0 else 3


class ProveedorGuionizado:
    """Answers every council schema with valid JSON built from a ruling (by default the case's reference ruling):
    proposals and revisions recommend it, critiques score every proposal with ``puntuar(position, proposal)`` and
    rank them by that score, the chair writes the ruling's prose; ``decision_un_paso`` answers the single pass."""

    name = "scripted"
    model = "scripted-1"

    def __init__(
        self, caso: CasoEvaluacion, *, respuesta: RespuestaDecision | None = None, puntuar: Puntuador = neutro
    ) -> None:
        self.caso = caso
        self.ref = respuesta or ce.respuesta_referencia(caso)
        self.puntuar = puntuar
        self.peticiones: list[str] = []
        self._lock = threading.Lock()

    def start(self, **_: Any) -> Any:
        raise RuntimeError("solo JSON")

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, attachments: Any = None
    ) -> dict[str, Any]:
        with self._lock:
            self.peticiones.append(schema_name)
        r = self.ref
        if schema_name in ("propuesta_consejo", "propuesta_revisada_consejo"):
            datos: dict[str, Any] = {
                "recomendacion": r.recomendacion,
                "condicion": r.condicion or "",
                "confianza": 0.7,
                "razonamiento": "Recomiendo esta opción por lo que el expediente dice del hogar y de sus riesgos.",
                "supuestos": ["Los hechos no cambian."],
                "riesgos": ["Puede salir mal si cambian los ingresos."],
                "que_me_haria_cambiar": ["Un cambio en los ingresos."],
                "cifras_usadas": [],
            }
            if schema_name == "propuesta_revisada_consejo":
                datos["cambios"] = "Mantengo mi propuesta tras leer las críticas."
            return datos
        if schema_name == "criticas_consejo":
            bloque = user.split("<propuestas>", 1)[1].split("</propuestas>", 1)[0]
            vistas = [json.loads(linea) for linea in bloque.strip().splitlines()]
            puntos = {p["etiqueta"]: self.puntuar(i, p) for i, p in enumerate(vistas)}
            return {
                "criticas": [
                    {
                        "etiqueta": p["etiqueta"],
                        "fortalezas": ["Usa el expediente."],
                        "debilidades": ["Podría concretar más."],
                        "errores": [],
                        "puntuacion": dict.fromkeys(
                            ("rigor", "completitud", "prudencia", "claridad", "ajuste_al_caso"), puntos[p["etiqueta"]]
                        ),
                    }
                    for p in vistas
                ],
                "ranking": sorted(puntos, key=lambda e: (-puntos[e], e)),
            }
        if schema_name == "dictamen_consejo":
            return {
                "resumen": r.resumen,
                "condicion": r.condicion or "",
                "posturas": [p.model_dump(include={"opcion_id", "a_favor", "en_contra"}) for p in r.posturas],
                "disenso": r.disenso or "",
                "supuestos_decisivos": r.supuestos_decisivos,
                "sensibilidad": r.sensibilidad,
                "datos_que_resolverian": r.datos_que_resolverian,
            }
        if schema_name == "decision_un_paso":
            return ce.ProveedorOraculo(self.caso, r).complete_json(
                system=system, user=user, schema=schema, schema_name=schema_name
            )
        raise RuntimeError(f"esquema inesperado: {schema_name}")
