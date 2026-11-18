"""Doubles for the council's integration tests: fake specialists, a stand-in deliberation engine, a fake memory and a
factory that wires them (no network, no FAISS, no model)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
import threading
from typing import Any

from asistente.nucleo.cerebro.contrato import (
    BloqueMemoria,
    Cifra,
    ContextoEncargo,
    InformeEspecialista,
    OrigenRecuerdo,
    RecuerdoMemoria,
    TurnoConversacion,
)
from asistente.nucleo.consejo.contrato import (
    EVENTO_FASE,
    EVENTO_MIEMBRO,
    CosteSesion,
    Critica,
    Dictamen,
    ExpedienteConsejo,
    Miembro,
    PosturaOpcion,
    Propuesta,
    PuntuacionRubrica,
    Ranking,
    Rol,
    SesionConsejo,
)
from asistente.nucleo.consejo.servicio import EntornoSesion
from asistente.nucleo.decision.schemas import CaseState, Fact, FactValueType
from tests.agent.fakes import ScriptedProvider

HOY = date(2026, 11, 18)


def caso(case_id: str = "c1", user_id: str = "u1", **hechos: Any) -> CaseState:
    def tipo(valor: Any) -> FactValueType:
        if isinstance(valor, bool):
            return FactValueType.BOOLEAN
        if isinstance(valor, int | float):
            return FactValueType.NUMBER
        return FactValueType.STRING

    return CaseState(
        case_id=case_id,
        user_id=user_id,
        jurisdiction="ES",
        tax_period="2026",
        facts=[
            Fact(
                fact_id=f"fact_{nombre}",
                name=nombre,
                value=float(valor) if isinstance(valor, int) and not isinstance(valor, bool) else valor,
                value_type=tipo(valor),
                source="test",
                confidence=1.0,
            )
            for nombre, valor in hechos.items()
        ],
    )


class EspecialistaFalso:
    """A specialist that answers every encargo with a fixed text and figures (and records what it was asked)."""

    def __init__(
        self,
        clave: str,
        nombre: str,
        texto: str,
        cifras: Sequence[tuple[str, str, str | None, str]] = (),
        *,
        descripcion: str = "",
        estado: str = "ok",
        avisos: Sequence[str] = (),
    ) -> None:
        self.clave = clave
        self.nombre = nombre
        self.descripcion = descripcion or f"{nombre}: hipoteca, amortizar, préstamo, cuota, euríbor"
        self._texto = texto
        self._cifras = list(cifras)
        self._estado = estado
        self._avisos = list(avisos)
        self.encargos: list[str] = []
        self.lock = threading.Lock()

    def atender(self, contexto: ContextoEncargo) -> InformeEspecialista:
        with self.lock:
            self.encargos.append(contexto.encargo.pregunta)
        return InformeEspecialista(
            encargo_id=contexto.encargo.id,
            especialista=self.clave,
            nombre=self.nombre,
            estado=self._estado,  # type: ignore[arg-type]
            texto=self._texto,
            cifras=[
                Cifra(
                    id=f"C{i}",
                    especialista=self.clave,
                    concepto=concepto,
                    valor=Decimal(valor),
                    unidad=unidad,  # type: ignore[arg-type]
                    literal=literal,
                    herramienta=f"calc_{self.clave}",
                )
                for i, (concepto, valor, unidad, literal) in enumerate(self._cifras, start=1)
            ],
            avisos=list(self._avisos),
        )


def hipoteca(**kw: Any) -> EspecialistaFalso:
    return EspecialistaFalso(
        "hipoteca",
        "Hipoteca",
        "Amortizar 20.000 € reduce los intereses futuros en 6.400 €. Si sube el euríbor, el ahorro sería mayor. "
        "Invertir esa cantidad no reduce la cuota de 850 €/mes.",
        [
            ("intereses ahorrados al amortizar", "6400", "EUR", "6.400 €"),
            ("cuota mensual actual", "850", "EUR/mes", "850 €/mes"),
        ],
        **kw,
    )


def fiscal(**kw: Any) -> EspecialistaFalso:
    return EspecialistaFalso(
        "fiscal",
        "Impuestos",
        "La deducción por vivienda habitual no aplica a esta hipoteca [F1]. Los rendimientos de un fondo tributan en la "
        "base del ahorro al reembolsar.",
        [("tipo de la base del ahorro (primer tramo)", "19", "%", "19 %")],
        descripcion="Impuestos: IRPF, deducciones, herencias, declaración",
        **kw,
    )


class MemoriaFalsa:
    def __init__(self, recuerdos: Sequence[tuple[str, str]] = ()) -> None:
        self._recuerdos = list(recuerdos)
        self.consultas: list[dict[str, Any]] = []

    def recuperar(self, **kw: Any) -> BloqueMemoria:
        self.consultas.append(kw)
        return BloqueMemoria(
            recuerdos=[
                RecuerdoMemoria(
                    id=f"r_{i}",
                    tipo=tipo,  # type: ignore[arg-type]
                    texto=texto,
                    origen=OrigenRecuerdo(conversacion_id="conv-anterior-1", fecha=date(2026, 10, 1), cita=texto[:300]),
                )
                for i, (tipo, texto) in enumerate(self._recuerdos, start=1)
            ]
        )

    def registrar_turno(self, turno: TurnoConversacion) -> None:
        return None


def _rol(rol_id: str, nombre: str) -> Rol:
    return Rol(
        id=rol_id,
        nombre=nombre,
        mandato="Mandato de prueba suficientemente largo para el contrato.",
        proposito=f"consejo:miembro:{rol_id}",
    )


ROLES = [_rol("prudente", "Prudencia financiera"), _rol("fiscal", "Fiscalidad"), _rol("abogado", "Abogado del diablo")]


class MotorFalso:
    """A stand-in for agent F's engine: records the dossier, emits the phases, and rules for ``recomienda`` with a
    summary that uses ``literal`` (a dossier figure, or an invented one to test the figures gate)."""

    def __init__(
        self,
        *,
        recomienda: str = "o1",
        literal: str = "6.400 €",
        estado: str = "terminada",
        error: Exception | None = None,
        antes: Callable[[], None] | None = None,
        avisos: Sequence[str] = (),
        miembros_ronda1: int | None = None,
    ) -> None:
        self.avisos = list(avisos)
        self.miembros_ronda1 = miembros_ronda1
        self.recomienda = recomienda
        self.literal = literal
        self.estado = estado
        self.error = error
        self.antes = antes
        self.expedientes: list[ExpedienteConsejo] = []
        self.topes: list[Decimal | None] = []

    def deliberar(
        self,
        expediente: ExpedienteConsejo,
        *,
        sesion_id: str,
        emit: Callable[..., None] | None = None,
        tope_usd: Decimal | None = None,
    ) -> SesionConsejo:
        self.expedientes.append(expediente)
        self.topes.append(tope_usd)
        emitir = emit or (lambda *a, **k: None)
        if self.antes is not None:
            self.antes()
        if self.error is not None:
            raise self.error
        for fase in ("propuestas", "criticas", "revision", "sintesis"):
            emitir(EVENTO_FASE, fase=fase, estado="inicio")
            emitir(EVENTO_MIEMBRO, miembro_id="prudente", fase=fase, estado="ok")
            emitir(EVENTO_FASE, fase=fase, estado="fin")
        propuestas = [
            Propuesta(
                miembro_id=rol.id,
                ronda=ronda,  # type: ignore[arg-type]
                recomendacion="o1" if rol.id != "abogado" else "o2",
                confianza=0.7,
                razonamiento=f"Razonamiento de {rol.nombre} en la ronda {ronda}, con la cifra de {self.literal}.",
                cifras_usadas=["C1"],
            )
            for ronda in (1, 2)
            for rol in ROLES
        ]
        if self.miembros_ronda1 is not None:
            propuestas = [p for p in propuestas if p.ronda == 1][: self.miembros_ronda1]
        criticas = [
            Critica(
                evaluador_id=rol.id,
                etiqueta="A",
                fortalezas=["Usa las cifras del expediente"],
                puntuacion=PuntuacionRubrica(rigor=4, completitud=4, prudencia=3, claridad=4, ajuste_al_caso=4),
            )
            for rol in ROLES
        ]
        rankings = [Ranking(evaluador_id=rol.id, orden=["A", "B"]) for rol in ROLES]
        dictamen = Dictamen(
            sesion_id=sesion_id,
            recomendacion=self.recomienda,
            confianza=0.66,
            consenso="mayoria",
            acuerdo=0.5,
            resumen=f"La mayoría recomienda amortizar: los intereses ahorrados serían de {self.literal}.",
            posturas=[PosturaOpcion(opcion_id="o1", a_favor=["Menos deuda"], miembros=["prudente", "fiscal"])],
            disenso="El abogado del diablo defiende invertir por la liquidez.",
            supuestos_decisivos=["Que el euríbor no baje"],
            sensibilidad=["Si el euríbor baja, invertir gana"],
            datos_que_resolverian=["La rentabilidad esperada del fondo"],
            miembros=[Miembro(rol=rol, proveedor="scripted", modelo="scripted-1") for rol in ROLES],
            autoria={"A": "prudente", "B": "fiscal", "C": "abogado"},
            avisos=list(self.avisos),
        )
        ahora = datetime.now(UTC)
        return SesionConsejo(
            id=sesion_id,
            case_id="desconocido",
            estado=self.estado,  # type: ignore[arg-type]
            fase="sintesis",
            pregunta_usuario="(motor)",
            creada=ahora,
            actualizada=ahora,
            estimacion_usd=Decimal("0"),
            tope_usd=Decimal("0"),
            coste=CosteSesion(llamadas=13, usd=Decimal("0.12"), tokens_entrada=1000, tokens_salida=500),
            propuestas=propuestas,
            criticas=criticas,
            rankings=rankings,
            dictamen=dictamen if self.estado == "terminada" else None,
            error=None if self.estado == "terminada" else "El motor falló a propósito.",
        )


def respuesta_planteamiento(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "pregunta_neutral": "¿Qué le conviene más a este hogar: amortizar parte de la hipoteca o invertir ese dinero?",
        "opciones": [
            {"descripcion": "Amortizar 20.000 € de la hipoteca reduciendo plazo"},
            {"descripcion": "Invertir los 20.000 € en un fondo indexado"},
        ],
        "preferencia": {"texto": "", "cita": ""},
        "ambitos": ["hipoteca", "fiscal"],
    }
    datos.update(cambios)
    return datos


class FabricaFalsa:
    """The service's factory for tests: scripted providers, the fake specialists, the stand-in engine."""

    def __init__(
        self,
        *,
        motor: Any = None,
        especialistas: Mapping[str, Any] | None = None,
        respuestas: Sequence[dict[str, Any] | Exception] = (),
        memoria: Any = None,
        proveedor_para: Callable[[str], Any] | None = None,
    ) -> None:
        self.motor = motor or MotorFalso()
        self.especialistas = dict(especialistas) if especialistas is not None else {"hipoteca": hipoteca()}
        self.proveedor = ScriptedProvider([], list(respuestas) or [respuesta_planteamiento()])
        self.memoria = memoria
        self._proveedor_para = proveedor_para
        self.propositos: list[str] = []
        self.controles: list[Any] = []

    def entorno(self, registro: Any, *, case: Any, modulos: Any, control: Any) -> EntornoSesion:
        self.controles.append(control)

        def proveedor_para(proposito: str) -> Any:
            self.propositos.append(proposito)
            if self._proveedor_para is not None:
                return self._proveedor_para(proposito)
            return self.proveedor

        return EntornoSesion(
            proveedor_para=proveedor_para,
            motor=self.motor,
            especialistas=self.especialistas,
            memoria=self.memoria,
        )
