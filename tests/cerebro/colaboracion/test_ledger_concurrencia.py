"""The evidence ledger under contention: ids stay unique, dense and stable; reads never see a broken dict."""

from __future__ import annotations

import threading

from asistente.nucleo.agent.evidence import EvidenceLedger

HILOS = 8
POR_HILO = 250


def test_parallel_registrations_and_reads_are_consistent() -> None:
    ledger = EvidenceLedger()
    salida = threading.Barrier(HILOS + 2)
    errores: list[BaseException] = []
    ids: dict[str, str] = {}
    lock = threading.Lock()
    terminado = threading.Event()

    def escribir(h: int) -> None:
        try:
            salida.wait()
            for i in range(POR_HILO):
                texto = f"hilo {h} fragmento {i}"
                fragmento = ledger.register(text=texto, metadata={"source": f"modulo://t/{h}/{i}"})
                comun = ledger.register(text="el mismo texto para todos", metadata={"source": "modulo://comun"})
                with lock:
                    ids[texto] = fragmento.fragment_id
                    ids.setdefault("comun", comun.fragment_id)
                    assert ids["comun"] == comun.fragment_id
        except BaseException as exc:  # pragma: no cover - a failure is reported below
            errores.append(exc)

    def leer() -> None:
        try:
            salida.wait()
            while not terminado.is_set():
                fragmentos = ledger.all()
                assert len(fragmentos) <= len(ledger)
                for f in fragmentos[-5:]:
                    assert ledger.get(f.fragment_id) is f
        except BaseException as exc:  # pragma: no cover
            errores.append(exc)

    escritores = [threading.Thread(target=escribir, args=(h,)) for h in range(HILOS)]
    lectores = [threading.Thread(target=leer) for _ in range(2)]
    for hilo in (*escritores, *lectores):
        hilo.start()
    for hilo in escritores:
        hilo.join()
    terminado.set()
    for hilo in lectores:
        hilo.join()
    assert errores == []
    total = HILOS * POR_HILO + 1
    assert len(ledger) == total
    assert sorted(int(f.fragment_id[1:]) for f in ledger.all()) == list(range(1, total + 1))
    assert len(set(ids.values())) == total
    # Behaviour unchanged: the same chunk always gets the same id.
    assert (
        ledger.register(text="el mismo texto para todos", metadata={"source": "modulo://comun"}).fragment_id
        == ids["comun"]
    )
