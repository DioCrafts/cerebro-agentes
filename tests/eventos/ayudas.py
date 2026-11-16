"""Builders shared by the event bus tests (fake modules only: never the real ones)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import threading
from typing import Any

from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
from hacienda_gpt.eventos.bus import Bus, Configuracion
from hacienda_gpt.modulos.contrato import AvisoModulo, ContextoModulo, HechoModulo, ModuloBase
from hacienda_gpt.modulos.fiscal import MODULO as FISCAL
from hacienda_gpt.proactive.store import NotificationStore
from tests.modulos_nucleo.fakes import Llamadas, ModuloFalso, agua, aviso, instalar, seguros

HOY = date(2026, 11, 16)
INICIO = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)

__all__ = [
    "FISCAL",
    "HOY",
    "INICIO",
    "Llamadas",
    "ModuloFalso",
    "Contador",
    "Reloj",
    "agua",
    "aviso",
    "contador",
    "crear_bus",
    "instalar",
    "seguros",
    "stores",
]


@dataclass
class Reloj:
    ahora: datetime = INICIO

    def __call__(self) -> datetime:
        return self.ahora

    def avanza(self, **kw: float) -> None:
        self.ahora += timedelta(**kw)


def crear_bus(
    db_path: str,
    *,
    modulos: Sequence[Any] | Callable[[str], Sequence[Any]] | None = None,
    reloj: Reloj | None = None,
    hoy: date = HOY,
    **config: Any,
) -> Bus:
    activos = modulos if callable(modulos) or modulos is None else (lambda _case_id, m=list(modulos): m)
    return Bus(
        db_path,
        config=Configuracion(**{"backoff_base_s": 30.0, "max_intentos": 3, "arriendo_s": 60.0, **config}),
        reloj=reloj or Reloj(),
        hoy=lambda: hoy,
        modulos_activos=activos,  # type: ignore[arg-type]
    )


@dataclass
class Contador(ModuloBase):
    """A fake module that counts its wake-ups per case (thread-safe) and can fail the first N times."""

    fallos: int = 0
    pausa: float = 0.0
    proxima: date | None = None
    llamadas_por_caso: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def avisos(self, contexto: ContextoModulo) -> list[AvisoModulo]:
        import time

        with self._lock:
            n = self.llamadas_por_caso.get(contexto.case_id or "", 0) + 1
            self.llamadas_por_caso[contexto.case_id or ""] = n
            fallar = self.fallos > 0
            if fallar:
                self.fallos -= 1
        if self.pausa:
            time.sleep(self.pausa)
        if fallar:
            raise RuntimeError("fallo pasajero del módulo")
        return [
            AvisoModulo(clave=f"aviso:{contexto.case_id}", titulo="Aviso", cuerpo="c", proxima_revision=self.proxima)
        ]


def contador(clave: str = "agua", **kw: Any) -> Contador:
    return Contador(
        clave=clave,
        nombre=clave.capitalize(),
        descripcion=f"Módulo de prueba {clave}.",
        instrucciones="Responde con tus herramientas.",
        hechos=(HechoModulo(f"{clave}_dato", "number", "Dato", "Un dato del módulo"),),
        **kw,
    )


def stores(tmp_path: Any) -> tuple[str, SQLiteCaseStateStore, NotificationStore]:
    db = str(tmp_path / "estado.sqlite3")
    return db, SQLiteCaseStateStore(db), NotificationStore(db)
