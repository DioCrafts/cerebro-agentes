"""Robustness: disconnection mid-request, reconnection, a CLI that is down, slow consumers, clean stop; and privacy:
the logs never carry message text."""

from __future__ import annotations

import logging
import threading
import time

from asistente.plataforma.mensajeria.contrato import MensajeSaliente
from tests.mensajeria.simplex.conftest import esperar
from tests.mensajeria.simplex.falso import ServidorSimplexFalso

PDF = b"%PDF-1.4\n" + b"y" * 300


def test_desconexion_a_mitad_de_peticion_falla_rapido_y_reconecta(cli, arrancar):
    arnes = arrancar(cli, timeout_comando_s=10.0)
    cli.contactos[7] = "Ana"
    cli.retener("/_send")
    resultado: list[str | None] = []
    hilo = threading.Thread(
        target=lambda: resultado.append(arnes.canal.enviar(MensajeSaliente(contacto="7", texto="x")))
    )
    inicio = time.monotonic()
    hilo.start()
    assert esperar(lambda: bool(cli.comandos_que_empiezan("/_send")))
    cli.desconectar()
    hilo.join(5)
    assert resultado == [None]
    assert time.monotonic() - inicio < 3  # failed at once, not after the 10 s timeout
    cli._retener.clear()
    # It comes back on its own and works again.
    assert esperar(lambda: cli.conexiones_aceptadas >= 2 and arnes.canal.estado().conectado)
    assert arnes.canal.enviar(MensajeSaliente(contacto="7", texto="otra vez")) is not None
    cli.mensaje(7, "tras reconectar")
    assert [m.texto for m in arnes.mensajes(1)] == ["tras reconectar"]


def test_eventos_emitidos_sin_conexion_llegan_al_reconectar(cli, arrancar):
    arnes = arrancar(cli)
    cli.caido = True
    cli.desconectar()
    assert esperar(lambda: not arnes.canal.estado().conectado)
    cli.mensaje(7, "mientras tanto")  # the CLI keeps it in its output queue
    cli.caido = False
    assert [m.texto for m in arnes.mensajes(1)] == ["mientras tanto"]


def test_cli_caida_al_arrancar_se_reintenta(arrancar):
    with ServidorSimplexFalso() as cli:
        cli.caido = True
        arnes = arrancar(cli, esperar_conexion=False)
        assert esperar(lambda: arnes.canal.estado().ultimo_error is not None)
        estado = arnes.canal.estado()
        assert not estado.conectado and "Sin conexión" in estado.detalle
        cli.caido = False
        assert esperar(lambda: arnes.canal.estado().conectado)
        assert arnes.canal.estado().ultimo_error is None


def test_descarga_iniciada_sobrevive_a_una_reconexion(cli, arrancar):
    cli.retraso_archivo_s = 0.5
    arnes = arrancar(cli)
    cli.mensaje(7, "doc", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF})
    assert esperar(lambda: bool(cli.comandos_que_empiezan("/freceive")))
    cli.desconectar()  # the completion event is emitted while reconnecting, or right after
    [m] = arnes.mensajes(1, timeout=8)
    assert m.adjuntos and m.adjuntos[0].mime == "application/pdf"


def test_un_consumidor_lento_no_bloquea_al_lector(cli, arrancar):
    bloqueo = threading.Event()

    def manejador(m):
        bloqueo.wait(10)

    arnes = arrancar(cli, manejador=manejador, manejadores_max=1, pendientes_max=1, cola_eventos_max=10)
    cli.contactos[7] = "Ana"
    for i in range(30):
        cli.mensaje(7, f"m{i}")
    # The event loop is stuck behind the handler and the event queue overflows (counted), yet the socket reader
    # still delivers responses: sending works.
    assert esperar(lambda: arnes.canal.contador.eventos_descartados > 0)
    assert arnes.canal.enviar(MensajeSaliente(contacto="7", texto="sigo vivo")) is not None
    assert "descartados" in arnes.canal.estado().detalle
    bloqueo.set()


def test_parada_limpia(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "hola")
    arnes.mensajes(1)
    inicio = time.monotonic()
    arnes.parar()
    assert time.monotonic() - inicio < 5
    assert not arnes.hilo.is_alive()
    estado = arnes.canal.estado()
    assert not estado.conectado and estado.detalle == "Detenido."
    assert esperar(lambda: cli.conectados == 0)
    vivos = [h.name for h in threading.enumerate() if h.name.startswith("simplex-") and h.is_alive()]
    assert not [n for n in vivos if n in ("simplex-lector",) or n.startswith("simplex-manejador")]
    arnes.canal.parar()  # idempotent


def test_parar_desde_el_manejador_no_se_bloquea(cli, arrancar):
    hecho = threading.Event()

    def manejador(m):
        arnes.canal.parar()
        hecho.set()

    arnes = arrancar(cli, manejador=manejador)
    cli.mensaje(7, "para")
    assert hecho.wait(5)
    arnes.hilo.join(15)
    assert not arnes.hilo.is_alive()


def test_segundo_iniciar_se_ignora(cli, arrancar):
    arnes = arrancar(cli)
    arnes.canal.iniciar(lambda m: None)  # returns at once
    assert arnes.canal.estado().conectado


def test_los_registros_nunca_contienen_el_texto(cli, arrancar, caplog):
    secreto = "SECRETO-7Q"
    caplog.set_level(logging.DEBUG)

    def manejador(m):
        raise RuntimeError(f"fallo con {m.texto}")

    arnes = arrancar(cli, manejador=manejador)
    item = cli.mensaje(7, f"mi texto {secreto}", nombre=f"Nombre {secreto}", cita=f"cita {secreto}")
    cli.mensaje(
        7, f"pie {secreto}", tipo="file", archivo={"nombre": f"{secreto}.pdf", "contenido": PDF}, nombre=f"N {secreto}"
    )
    cli.mensaje(
        7, f"no permitido {secreto}", tipo="file", archivo={"nombre": f"{secreto}.exe", "contenido": b"MZ" * 40}
    )
    cli.mensaje(7, f"en grupo {secreto}", grupo=True)
    arnes.mensajes(3)
    mensaje_id = arnes.canal.enviar(MensajeSaliente(contacto="7", texto=f"respuesta {secreto}", responde_a=str(item)))
    assert mensaje_id is not None
    arnes.canal.editar("7", mensaje_id, f"editado {secreto}")
    arnes.canal.enviar(MensajeSaliente(contacto="404", texto=f"fallo {secreto}"))
    cli.desconectar()
    assert esperar(lambda: cli.conexiones_aceptadas >= 2 and arnes.canal.estado().conectado)
    time.sleep(0.2)
    assert caplog.records, "the scenario must produce logs"
    # The scenario really went through the paths that could leak: they log ids, codes and places instead.
    assert "el manejador falló: RuntimeError en test_robustez.py" in caplog.text
    assert "mensaje recibido contacto=7" in caplog.text and "archivo rechazado" in caplog.text
    assert "mensaje enviado contacto=7" in caplog.text and "editado contacto=7" in caplog.text
    assert secreto not in caplog.text
    assert all(secreto not in str(r.args) for r in caplog.records)


def test_parar_antes_de_iniciar_no_deja_el_canal_en_marcha(cli, tmp_path):
    from asistente.plataforma.mensajeria.simplex import CanalSimplex
    from tests.mensajeria.simplex.falso import configuracion_para

    canal = CanalSimplex(configuracion_para(cli, tmp_path))
    canal.parar()  # the stop arrives before the thread that starts it gets to run
    hilo = threading.Thread(target=canal.iniciar, args=(lambda m: None,), daemon=True)
    hilo.start()
    hilo.join(5)
    assert not hilo.is_alive() and cli.conexiones_aceptadas == 0
    # The stop was consumed: a later start runs normally.
    hilo = threading.Thread(target=canal.iniciar, args=(lambda m: None,), daemon=True)
    hilo.start()
    try:
        assert esperar(lambda: canal.estado().conectado)
    finally:
        canal.parar()
        hilo.join(5)
    assert not hilo.is_alive()
