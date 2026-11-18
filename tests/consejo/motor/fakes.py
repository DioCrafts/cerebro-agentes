"""Scripted doubles for the council engine's tests: a dossier, valid outputs, and a provider per purpose."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
import json
import re
import threading
from typing import Any

from asistente.nucleo.agent.providers.base import Usage
from asistente.nucleo.cerebro.contrato import Cifra
from asistente.nucleo.consejo.contrato import ExpedienteConsejo, HechoExpediente, Opcion
from asistente.nucleo.consejo.miembros import PRESIDENTE, ROLES_POR_DEFECTO
from tests.agent.fakes import ScriptedProvider

SESION = "cons_" + "a" * 32
PREFERENCIA = "Prefiero amortizar: no soporto tener deudas."

Respuesta = dict[str, Any] | Exception | Callable[[dict[str, Any]], dict[str, Any]]


def expediente() -> ExpedienteConsejo:
    return ExpedienteConsejo(
        id="exp_prueba",
        pregunta="¿Conviene destinar los ahorros a amortizar parte de la hipoteca o a invertirlos?",
        opciones=[
            Opcion(id="o1", descripcion="Amortizar anticipadamente parte de la hipoteca"),
            Opcion(id="o2", descripcion="Invertir los ahorros en un fondo indexado"),
        ],
        hechos=[
            HechoExpediente(clave="tipo de interés", texto="La hipoteca es a tipo variable.", origen="especialista"),
            HechoExpediente(clave="colchón", texto="El hogar tiene un fondo de emergencia.", origen="usuario"),
        ],
        cifras=[
            Cifra(
                id="C1",
                especialista="hipoteca",
                concepto="cuota mensual actual",
                valor=Decimal("850"),
                unidad="EUR/mes",
                literal="850 €/mes",
            ),
            Cifra(
                id="C2",
                especialista="hipoteca",
                concepto="intereses ahorrados al amortizar",
                valor=Decimal("6400"),
                unidad="EUR",
                literal="6.400 €",
            ),
            Cifra(
                id="C3",
                especialista="fiscal",
                concepto="deducción anual que se perdería",
                valor=Decimal("1200"),
                unidad="EUR/año",
                literal="1.200 €/año",
            ),
        ],
        fragment_ids=["F1"],
        perfil_minimo={"edad": "45 años", "hijos": "2"},
        restricciones=["No quiere productos con permanencia."],
        incertidumbres=["La evolución del Euríbor."],
        preferencia_usuario=PREFERENCIA,
    )


def propuesta(
    recomendacion: str = "o1",
    *,
    confianza: float = 0.7,
    razonamiento: str | None = None,
    cifras: Sequence[str] = ("C1", "C2"),
    condicion: str = "",
    cambios: str | None = None,
) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "recomendacion": recomendacion,
        "condicion": condicion,
        "confianza": confianza,
        "razonamiento": razonamiento
        or (
            f"Recomiendo {recomendacion}: con una cuota de 850 €/mes (C1) y unos intereses ahorrados de 6.400 € (C2), "
            "la opción reduce el riesgo del hogar sin comprometer el colchón."
        ),
        "supuestos": ["El colchón de emergencia se mantiene."],
        "riesgos": ["Que el Euríbor baje y la amortización rinda menos."],
        "que_me_haria_cambiar": ["Un tipo de interés fijo muy bajo."],
        "cifras_usadas": list(cifras),
    }
    if cambios is not None:
        datos["cambios"] = cambios
    return datos


def revisada(recomendacion: str = "o1", **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("cambios", "Mantengo la recomendación: las críticas no cambian el análisis.")
    return propuesta(recomendacion, **kwargs)


def critica_de(etiqueta: str, *, errores: Sequence[dict[str, str]] = ()) -> dict[str, Any]:
    return {
        "etiqueta": etiqueta,
        "fortalezas": ["Usa las cifras del expediente."],
        "debilidades": ["Podría pesar más la incertidumbre."],
        "errores": list(errores),
        "puntuacion": {"rigor": 4, "completitud": 3, "prudencia": 4, "claridad": 4, "ajuste_al_caso": 3},
    }


def etiquetas_de(peticion: dict[str, Any]) -> list[str]:
    """The labels of the proposals a critique request shows, in order."""
    bloque = peticion["user"].split("<propuestas>", 1)[1].split("</propuestas>", 1)[0]
    return [json.loads(linea)["etiqueta"] for linea in bloque.strip().splitlines()]


def propuestas_de(peticion: dict[str, Any]) -> list[dict[str, Any]]:
    bloque = peticion["user"].split("<propuestas>", 1)[1].split("</propuestas>", 1)[0]
    return [json.loads(linea) for linea in bloque.strip().splitlines()]


def criticas(peticion: dict[str, Any]) -> dict[str, Any]:
    """A valid critique of every proposal shown, ranked in the order shown."""
    etiquetas = etiquetas_de(peticion)
    return {"criticas": [critica_de(e) for e in etiquetas], "ranking": etiquetas}


def criticas_por(clave: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A valid critique ranking the proposals by ``clave(proposal)`` (descending)."""

    def responder(peticion: dict[str, Any]) -> dict[str, Any]:
        vistas = propuestas_de(peticion)
        orden = [p["etiqueta"] for p in sorted(vistas, key=lambda p: (-clave(p), p["etiqueta"]))]
        return {"criticas": [critica_de(p["etiqueta"]) for p in vistas], "ranking": orden}

    return responder


def dictamen(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "resumen": "La mayoría del consejo recomienda amortizar: reduce el riesgo del hogar con la cuota actual (C1).",
        "condicion": "",
        "posturas": [
            {"opcion_id": "o1", "a_favor": ["Reduce la deuda."], "en_contra": ["Menos liquidez."]},
            {"opcion_id": "o2", "a_favor": ["Más rentabilidad esperada."], "en_contra": ["Volatilidad."]},
        ],
        "disenso": "La minoría sostiene que invertir aprovecha mejor el horizonte largo del hogar.",
        "supuestos_decisivos": ["El tipo variable seguirá alto."],
        "sensibilidad": ["Si el Euríbor bajara mucho, la recomendación pasaría a o2."],
        "datos_que_resolverian": ["El diferencial exacto de la hipoteca."],
    }
    datos.update(cambios)
    return datos


class ProveedorConsejo(ScriptedProvider):
    """``ScriptedProvider`` with what the council needs: responses that are callables of the request, usage
    reported through ``on_usage`` (priced with the project's table), a barrier to prove parallelism, and its own
    name and model."""

    def __init__(
        self,
        respuestas: Sequence[Respuesta],
        *,
        name: str = "scripted",
        model: str = "scripted-1",
        uso: tuple[int, int] | None = None,
        modelo_uso: str = "claude-sonnet-5-5",
        barrera: threading.Barrier | None = None,
        por_defecto: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__([], list(respuestas))  # type: ignore[arg-type]
        self._por_defecto = por_defecto
        self.name = name
        self.model = model
        self.on_usage: Callable[[Usage], None] | None = None
        self._uso = uso
        self._modelo_uso = modelo_uso
        self._barrera = barrera
        self._lock = threading.Lock()

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, attachments: Any = None
    ) -> dict[str, Any]:
        peticion = {"system": system, "user": user, "schema": schema, "schema_name": schema_name}
        with self._lock:
            self.json_requests.append({**peticion, "attachments": attachments})
            if not self.json_responses and self._por_defecto is not None:
                respuesta: Respuesta = self._por_defecto
            else:
                respuesta = self.json_responses.pop(0)
        if self._barrera is not None:
            self._barrera.wait(timeout=10)
        if self._uso is not None and self.on_usage is not None:
            self.on_usage(
                Usage(
                    provider=self.name,
                    model=self._modelo_uso,
                    input_tokens=self._uso[0],
                    output_tokens=self._uso[1],
                )
            )
        if isinstance(respuesta, Exception):
            raise respuesta
        if callable(respuesta):
            return respuesta(peticion)
        return respuesta

    def peticiones(self, schema_name: str) -> list[dict[str, Any]]:
        return [r for r in self.json_requests if r["schema_name"] == schema_name]


def proveedores_basicos(
    votos: Sequence[str] = ("o1", "o1", "o1", "o2"),
    *,
    revision: Sequence[str] | None = None,
    presidente: Sequence[Respuesta] | None = None,
    **kwargs: Any,
) -> dict[str, ProveedorConsejo]:
    """One scripted provider per purpose of the default council: round 1, critiques, revision; and the chair."""
    finales = list(revision or votos)
    proveedores = {
        rol.proposito: ProveedorConsejo(
            [propuesta(v), criticas, revisada(f)], name=f"fam{i % 2}", model=f"modelo-{rol.id}", **kwargs
        )
        for i, (rol, v, f) in enumerate(zip(ROLES_POR_DEFECTO, votos, finales, strict=True))
    }
    proveedores[PRESIDENTE.proposito] = ProveedorConsejo(
        list(presidente) if presidente is not None else [dictamen()], name="fam0", model="modelo-presidente", **kwargs
    )
    return proveedores


def para(proveedores: dict[str, ProveedorConsejo]) -> Callable[[str], ProveedorConsejo]:
    def proveedor_para(proposito: str) -> ProveedorConsejo:
        return proveedores[proposito]

    return proveedor_para


def por_esquema(votos: dict[str, str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A responder for a provider SHARED by several seats: it answers by schema, voting by the seat named in the
    system prompt (``votos``: seat name → option)."""

    def responder(peticion: dict[str, Any]) -> dict[str, Any]:
        voto = next((v for nombre, v in votos.items() if nombre in peticion["system"]), "o1")
        if peticion["schema_name"] == "criticas_consejo":
            return criticas(peticion)
        if peticion["schema_name"] == "propuesta_revisada_consejo":
            return revisada(voto)
        if peticion["schema_name"] == "dictamen_consejo":
            return dictamen()
        return propuesta(voto)

    return responder


def sin_numeros(texto: str) -> str:
    return re.sub(r"\d", "#", texto)
