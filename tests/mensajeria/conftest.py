"""Fixtures of the messaging gateway's tests: a private cases database, no accounts, the in-memory channel and a
gateway whose brain each test chooses (by default one that must never be reached).

Nothing here is autouse: the SimpleX adapter's tests (``simplex/``) set up their own environment.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from asistente.nucleo.consejo import servicio as servicio_consejo
from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
from asistente.plataforma import settings
from asistente.plataforma.mensajeria import emparejamiento
from asistente.plataforma.mensajeria.almacen import AlmacenMensajeria, Vinculo, almacen, olvidar_almacenes
from asistente.plataforma.mensajeria.pasarela import Pasarela
from asistente.plataforma.mensajeria.prueba import CanalPrueba, Enviado
from tests.cerebro.fakes import caso

CASE_ID = "case-cerebro"
USUARIO_CASO = "u-cerebro"
PREGUNTA_FISCAL = "¿Qué plazo tengo para reinvertir en vivienda habitual a efectos del IRPF?"


class AgenteVigilado:
    """Wraps the agent the gateway builds, counting how often a turn reached it."""

    def __init__(self) -> None:
        self.agente: Any = None
        self.construido = 0

    def __call__(self) -> Any:
        self.construido += 1
        if self.agente is None:
            raise AssertionError("the gateway reached the brain when it must not")
        return self.agente


@dataclass
class Montaje:
    db: str
    canal: CanalPrueba
    pasarela: Pasarela
    casos: SQLiteCaseStateStore
    agente: AgenteVigilado
    lectores: list[Any] = field(default_factory=list)
    case_id: str = CASE_ID

    @property
    def almacen(self) -> AlmacenMensajeria:
        return almacen(self.db)

    def codigo(self, *, case_id: str | None = None, user_id: str | None = None, canal: str = "prueba") -> str:
        return emparejamiento.nuevo_codigo(
            self.almacen, canal=canal, user_id=user_id, case_id=case_id or self.case_id, momento=datetime.now(UTC)
        ).codigo

    def emparejar(self, contacto: str = "c1", **opciones: Any) -> Vinculo:
        codigo = self.codigo(**opciones)
        self.canal.recibir(contacto, f"Hola, mi código es {codigo}")
        assert self.pasarela.esperar(10)
        vinculo = self.almacen.vinculo_de("prueba", contacto)
        assert vinculo is not None
        return vinculo

    def nuevos(self, contacto: str, desde: int) -> list[Enviado]:
        return self.canal.de(contacto)[desde:]

    def decir(self, texto: str = "", contacto: str = "c1", **opciones: Any) -> list[str]:
        """Send a message as ``contacto``, wait for the gateway, return the texts it sent back (edits applied)."""
        antes = len(self.canal.de(contacto))
        self.canal.recibir(contacto, texto, **opciones)
        assert self.pasarela.esperar(30)
        return [e.texto for e in self.nuevos(contacto, antes)]


@pytest.fixture
def entorno_mensajeria(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No accounts, the gateway on with the in-memory channel, no quiet hours, nothing in background threads."""
    monkeypatch.setattr(settings, "AUTH_MODE", "off")
    monkeypatch.setattr(settings, "MENSAJERIA_ACTIVA", True)
    monkeypatch.setattr(settings, "MENSAJERIA_CANALES", "prueba")
    monkeypatch.setattr(settings, "MENSAJERIA_HORAS_SILENCIO", "")
    # Generous pace limits: the test that checks them lowers them.
    monkeypatch.setattr(settings, "MENSAJERIA_MENSAJES_POR_MINUTO", 100)
    monkeypatch.setattr(settings, "MENSAJERIA_MENSAJES_POR_HORA", 1000)
    monkeypatch.setattr(settings, "CONSEJO_ACTIVO", True)
    monkeypatch.setattr(settings, "CONSEJO_EN_SEGUNDO_PLANO", False)
    monkeypatch.setattr(settings, "CONSEJO_FAMILIAS", "")
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    yield
    olvidar_almacenes()
    servicio_consejo._servicios.clear()


@pytest.fixture
def crear_montaje(
    tmp_path, monkeypatch: pytest.MonkeyPatch, entorno_mensajeria: None
) -> Iterator[Callable[..., Montaje]]:
    creados: list[Montaje] = []
    db = str(tmp_path / "estado.sqlite3")
    casos = SQLiteCaseStateStore(db)
    casos.save_case(caso())
    monkeypatch.setattr(settings, "DECISION_STATE_DB_PATH", db)

    def crear(*, timeout: float = 20, demora: float = 60, **opciones_canal: Any) -> Montaje:
        canal = CanalPrueba(**opciones_canal)
        agente = AgenteVigilado()
        lectores: list[Any] = []

        def lector() -> Any:
            if not lectores:
                raise AssertionError("the gateway reached the document reader when it must not")
            return lectores[-1]

        pasarela = Pasarela(
            db, canales=[canal], agente=agente, lector_documentos=lector, demora_progreso=demora, timeout=timeout
        )
        canal.iniciar_en_hilo(pasarela.manejar)
        montaje = Montaje(db=db, canal=canal, pasarela=pasarela, casos=casos, agente=agente, lectores=lectores)
        creados.append(montaje)
        return montaje

    yield crear
    for montaje in creados:
        montaje.canal.parar()
        montaje.pasarela.cerrar(5)
    casos.close()


@pytest.fixture
def montaje(crear_montaje: Callable[..., Montaje]) -> Montaje:
    return crear_montaje()
