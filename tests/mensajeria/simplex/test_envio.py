"""Sending against the fake CLI: ids, splitting, reply-to, edits, contact removal; never raising."""

from __future__ import annotations

import threading

from asistente.plataforma.mensajeria.contrato import MensajeSaliente
from asistente.plataforma.mensajeria.simplex import CanalSimplex
from asistente.plataforma.mensajeria.simplex.canal import PRESUPUESTO_BYTES, bytes_json
from tests.mensajeria.simplex.falso import configuracion_para


def test_enviar_devuelve_el_id_del_mensaje(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    mensaje_id = arnes.canal.enviar(MensajeSaliente(contacto="7", texto="Hola, *Ana*"))
    assert mensaje_id is not None and mensaje_id.isdigit()
    assert cli.enviados == [{"contacto": 7, "texto": "Hola, *Ana*", "cita_id": None, "item_id": int(mensaje_id)}]


def test_responder_citando(cli, arrancar):
    arnes = arrancar(cli)
    item = cli.mensaje(7, "¿Cuándo vence el IBI?")
    arnes.mensajes(1)
    arnes.canal.enviar(MensajeSaliente(contacto="7", texto="El 5 de noviembre.", responde_a=str(item)))
    assert cli.enviados[-1]["cita_id"] == item


def test_cita_inexistente_se_envia_sin_cita(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    mensaje_id = arnes.canal.enviar(MensajeSaliente(contacto="7", texto="respuesta", responde_a="999999"))
    assert mensaje_id is not None
    assert [e["cita_id"] for e in cli.enviados] == [None]
    assert len(cli.comandos_que_empiezan("/_send @7")) == 2  # with the quote (refused), then without


def test_cita_demasiado_larga_no_se_incluye(cli, arrancar):
    arnes = arrancar(cli)
    item = cli.mensaje(7, "x" * 8000)
    arnes.mensajes(1)
    arnes.canal.enviar(MensajeSaliente(contacto="7", texto="ñ" * 3999, responde_a=str(item)))
    assert cli.enviados[-1]["cita_id"] is None
    assert len(cli.comandos_que_empiezan("/_send @7")) == 1  # decided before sending: no failed attempt


def test_texto_largo_se_divide(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    parrafos = [f"Párrafo {i}: " + "palabra " * 150 for i in range(10)]
    texto = "\n\n".join(parrafos)
    mensaje_id = arnes.canal.enviar(MensajeSaliente(contacto="7", texto=texto))
    partes = [e["texto"] for e in cli.enviados]
    assert len(partes) > 1 and mensaje_id == str(cli.enviados[0]["item_id"])
    assert all(len(p) <= arnes.canal.capacidades.max_texto and bytes_json(p) <= PRESUPUESTO_BYTES for p in partes)
    assert "".join(partes).replace("\n", "").replace(" ", "") == texto.replace("\n", "").replace(" ", "")
    assert all(p.startswith("Párrafo") for p in partes)  # cut at paragraph breaks


def test_editar_en_su_sitio(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    mensaje_id = arnes.canal.enviar(MensajeSaliente(contacto="7", texto="Pensando…"))
    assert mensaje_id is not None
    assert arnes.canal.editar("7", mensaje_id, "La respuesta final.")
    assert cli.editados == [{"contacto": 7, "item_id": int(mensaje_id), "texto": "La respuesta final."}]
    assert arnes.canal.editar("7", mensaje_id, "La respuesta final.")  # unchanged is still a success
    largo = "\n\n".join("bloque " * 300 for _ in range(4))
    assert arnes.canal.editar("7", mensaje_id, largo)  # the first part in place, the rest as new messages
    assert len(cli.enviados) > 1


def test_editar_un_mensaje_ajeno_falla_sin_excepcion(cli, arrancar):
    arnes = arrancar(cli)
    item = cli.mensaje(7, "mensaje del usuario")
    arnes.mensajes(1)
    assert arnes.canal.editar("7", str(item), "no") is False
    assert arnes.canal.editar("7", "abc", "no") is False
    assert arnes.canal.editar("7", "5", "   ") is False


def test_desconectar_contacto(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    assert arnes.canal.desconectar_contacto("7") is True
    assert cli.borrados == [7] and cli.comandos_que_empiezan("/_delete @7 full")
    assert arnes.canal.desconectar_contacto("7") is False  # no longer exists
    assert arnes.canal.desconectar_contacto("@7; /_delete @1") is False
    assert not cli.comandos_que_empiezan("/_delete @1")


def test_entradas_no_validas_no_lanzan(cli, arrancar):
    arnes = arrancar(cli)
    assert arnes.canal.enviar(MensajeSaliente(contacto="7 json", texto="x")) is None
    assert arnes.canal.enviar(MensajeSaliente(contacto="0", texto="x")) is None
    assert arnes.canal.enviar(MensajeSaliente(contacto="404", texto="contacto inexistente")) is None
    assert not cli.comandos_que_empiezan("/_send @0")


def test_sin_conexion_devuelve_none(tmp_path, cli):
    canal = CanalSimplex(configuracion_para(cli, tmp_path))  # never started
    assert canal.enviar(MensajeSaliente(contacto="7", texto="x")) is None
    assert canal.editar("7", "1", "x") is False
    assert canal.desconectar_contacto("7") is False


def test_envios_concurrentes(cli, arrancar):
    arnes = arrancar(cli)
    cli.contactos[7] = "Ana"
    ids: list[str | None] = []
    lock = threading.Lock()

    def enviar(i: int) -> None:
        r = arnes.canal.enviar(MensajeSaliente(contacto="7", texto=f"mensaje {i}"))
        with lock:
            ids.append(r)

    hilos = [threading.Thread(target=enviar, args=(i,)) for i in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(10)
    assert None not in ids and len(set(ids)) == 12
    assert sorted(e["texto"] for e in cli.enviados) == sorted(f"mensaje {i}" for i in range(12))
