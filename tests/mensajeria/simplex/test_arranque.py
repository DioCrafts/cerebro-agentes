"""Start-up against the fake CLI: version gate, the bot profile, the address and auto-accept — created only when missing."""

from __future__ import annotations

import json

from asistente.plataforma.mensajeria.contrato import Canal
from asistente.plataforma.mensajeria.simplex import CanalSimplex
from tests.mensajeria.simplex.conftest import esperar
from tests.mensajeria.simplex.falso import NOMBRE_BOT, ServidorSimplexFalso, configuracion_para


def test_cumple_el_contrato_del_canal(tmp_path):
    with ServidorSimplexFalso() as cli:
        canal = CanalSimplex(configuracion_para(cli, tmp_path))
        assert isinstance(canal, Canal)
        assert canal.nombre == "simplex"
        assert canal.capacidades.edicion and canal.capacidades.adjuntos_entrantes
        assert not canal.capacidades.adjuntos_salientes
        assert canal.capacidades.cifrado_extremo_a_extremo and canal.capacidades.formato == "simplex"
        estado = canal.estado()
        assert not estado.conectado and estado.canal == "simplex"


def test_con_todo_configurado_no_crea_ni_cambia_nada(cli, arrancar):
    arnes = arrancar(cli)
    estado = arnes.canal.estado()
    assert estado.conectado
    assert estado.version == "7.1.4.0"
    assert estado.direccion == "https://smp.ejemplo.org/a#direccion-1"  # the short link (what the app shows as QR)
    assert estado.ultimo_error is None
    assert "7.1.4.0" in estado.detalle and "127.0.0.1" in estado.detalle
    for orden in ("/_create user", "/_address ", "/_address_settings", "/_user ", "/_set accept group invitations"):
        assert not cli.comandos_que_empiezan(orden), orden
    assert cli.comandos[:3] == ["/version", "/user", "/_show_address 1"]


def test_sin_perfil_crea_el_bot_la_direccion_y_la_autoaceptacion(arrancar):
    with ServidorSimplexFalso(perfiles=(), activo=None, con_direccion=False) as cli:
        arnes = arrancar(cli, bienvenida="Hola, envíame tu código.")
        estado = arnes.canal.estado()
        assert estado.conectado
        assert estado.direccion == "https://smp.ejemplo.org/a#nueva-1"
        [crear] = cli.comandos_que_empiezan("/_create user ")
        nuevo = json.loads(crear[len("/_create user ") :])
        assert nuevo["profile"]["displayName"] == NOMBRE_BOT
        assert nuevo["profile"]["peerType"] == "bot"
        assert nuevo["profile"]["preferences"]["files"] == {"allow": "yes"}
        assert nuevo["profile"]["preferences"]["calls"] == {"allow": "no"}
        assert nuevo["pastTimestamp"] is False and nuevo["userChatRelay"] is False and nuevo["clientService"] is False
        assert cli.comandos_que_empiezan("/_address 1")
        ajustes = cli.direcciones[1]["ajustes"]
        assert ajustes["autoAccept"] == {"acceptIncognito": False}
        assert ajustes["businessAddress"] is False
        assert ajustes["autoReply"] == {"type": "text", "text": "Hola, envíame tu código."}


def test_activa_el_perfil_del_bot_sin_tocar_los_demas(arrancar):
    with ServidorSimplexFalso(perfiles=("Persona", NOMBRE_BOT), activo=0, con_direccion=False) as cli:
        persona = cli.usuarios[0]
        arrancar(cli)
        assert cli.usuario_activo is not None and cli.usuario_activo.nombre == NOMBRE_BOT
        assert cli.comandos_que_empiezan("/_user 2")
        assert not cli.comandos_que_empiezan("/_create user")
        # Nothing addressed to the other profile (id 1): no address, settings or preferences changed for it.
        assert not [c for c in cli.comandos if c.split(" ")[-1] == "1" or " 1 " in c]
        assert 1 not in cli.direcciones and persona.nombre == "Persona"


def test_no_elige_un_perfil_oculto_con_el_mismo_nombre(arrancar):
    with ServidorSimplexFalso(perfiles=("Persona", NOMBRE_BOT), activo=0, con_direccion=False) as cli:
        cli.usuarios[1].oculto = True
        arnes = arrancar(cli, esperar_conexion=False)
        # It tries to create its own profile; the CLI refuses the repeated name and the reason is shown.
        assert esperar(lambda: "userExists" in (arnes.canal.estado().ultimo_error or ""))
        assert not cli.comandos_que_empiezan("/_user 2")
        assert cli.usuario_activo is cli.usuarios[0]


def test_ajustes_de_direccion_segun_configuracion(arrancar):
    with ServidorSimplexFalso() as cli:
        cli.direcciones[1]["ajustes"]["chatLayout"] = {"x": 1}  # a field the channel does not manage: kept
        arrancar(cli, autoaceptar=False)
        ajustes = cli.direcciones[1]["ajustes"]
        assert "autoAccept" not in ajustes
        assert ajustes["chatLayout"] == {"x": 1} and ajustes["futuro"] == 1


def test_desactiva_la_aceptacion_automatica_de_grupos(arrancar):
    with ServidorSimplexFalso(auto_grupos=True) as cli:
        arrancar(cli)
        assert cli.comandos_que_empiezan("/_set accept group invitations 1 off")
        assert cli.usuario_activo is not None and cli.usuario_activo.auto_grupos is False


def test_avisa_si_el_perfil_no_admite_archivos(arrancar):
    with ServidorSimplexFalso(admite_archivos=False) as cli:
        arnes = arrancar(cli)
        assert "no admite archivos" in arnes.canal.estado().detalle


def test_version_no_soportada_se_rechaza_con_claridad(arrancar):
    for version in ("6.4.2.0", "8.0.0.0", "basura"):
        with ServidorSimplexFalso(version=version) as cli:
            arnes = arrancar(cli, esperar_conexion=False)
            assert esperar(lambda a=arnes: a.canal.estado().ultimo_error is not None)
            estado = arnes.canal.estado()
            assert not estado.conectado
            assert "no compatible" in (estado.ultimo_error or "") and "6.4.3" in (estado.ultimo_error or "")
            assert cli.comandos and set(cli.comandos) == {"/version"}  # nothing else is done with it
            arnes.parar()


def test_version_minima_aceptada(arrancar):
    with ServidorSimplexFalso(version="6.4.3.1") as cli:
        assert arrancar(cli).canal.estado().conectado


def test_comprueba_los_servidores_propios(arrancar):
    with ServidorSimplexFalso(servidores_smp=("smp://h@smp.ejemplo.org", "smp://h@smp.otro.net")) as cli:
        arnes = arrancar(cli, servidores_smp=("smp://huella@smp.ejemplo.org",), servidores_xftp=("xftp.ejemplo.org",))
        assert "SIMPLEX_SERVIDORES_SMP" in arnes.canal.estado().detalle
        assert "SIMPLEX_SERVIDORES_XFTP" not in arnes.canal.estado().detalle
    with ServidorSimplexFalso() as cli:
        arnes = arrancar(cli, servidores_smp=("smp://otra@smp.ejemplo.org:5223",))
        assert "Aviso" not in arnes.canal.estado().detalle


def test_fija_cuanto_guarda_la_cli_los_mensajes(arrancar):
    with ServidorSimplexFalso() as cli:
        arrancar(cli)  # already 7 days: nothing to change
        assert cli.comandos_que_empiezan("/_ttl 1") == ["/_ttl 1"]
    with ServidorSimplexFalso() as cli:
        cli.ttl = 0  # the CLI's default: keep forever
        arrancar(cli, historial_dias=2)
        assert cli.comandos_que_empiezan("/_ttl 1 ") == [f"/_ttl 1 {2 * 86400}"] and cli.ttl == 2 * 86400
    with ServidorSimplexFalso() as cli:
        arrancar(cli, historial_dias=0)  # leave it as it is
        assert not cli.comandos_que_empiezan("/_ttl")
    with ServidorSimplexFalso() as cli:
        cli.fallar("/_ttl", {"type": "chatCmdError", "chatError": {"type": "error", "errorType": {"type": "x"}}})
        arnes = arrancar(cli)
        assert arnes.canal.estado().conectado and "No se pudo fijar" in arnes.canal.estado().detalle
