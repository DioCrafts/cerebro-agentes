"""Receiving: text, flags, filters, dedupe, handler isolation and files, against the fake CLI."""

from __future__ import annotations

import contextvars
from datetime import UTC
import os
from pathlib import Path
import stat
import threading
import time

from asistente.plataforma.mensajeria.contrato import MAX_TEXTO_ENTRANTE
from tests.mensajeria.simplex.conftest import esperar

PDF = b"%PDF-1.7\n" + b"x" * 2000 + b"\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 500
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + b"\x00" * 300
EXE = b"MZ\x90\x00" + b"\x00" * 600


def test_texto_simple(cli, arrancar):
    arnes = arrancar(cli)
    item = cli.mensaje(7, "¿Cuánto pagué de IBI?", nombre="Lucía")
    [m] = arnes.mensajes(1)
    assert m.canal == "simplex" and m.contacto == "7" and m.mensaje_id == str(item)
    assert m.texto == "¿Cuánto pagué de IBI?" and not m.truncado
    assert not m.reenviado and m.cita is None and not m.es_voz and m.adjuntos == []
    assert m.nombre_mostrado == "Lucía"
    assert m.recibido.tzinfo is not None and m.recibido.utcoffset() == UTC.utcoffset(None)


def test_reenviado_y_cita_son_datos_marcados(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "mira esto", reenviado=True)
    cli.mensaje(7, "¿y esto qué es?", cita="Texto citado de otro mensaje")
    a, b = arnes.mensajes(2)
    assert a.reenviado and a.cita is None
    assert not b.reenviado and b.cita == "Texto citado de otro mensaje"


def test_texto_largo_se_trunca_y_se_marca(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "a" * (MAX_TEXTO_ENTRANTE + 500), cita="c" * 5000)
    [m] = arnes.mensajes(1)
    assert len(m.texto) == MAX_TEXTO_ENTRANTE and m.truncado
    assert m.cita is not None and len(m.cita) == 4000


def test_ignora_grupos_otros_perfiles_y_duplicados(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "en un grupo", grupo=True)
    cli.mensaje(7, "para otro perfil", usuario=99)
    item = cli.mensaje(7, "uno")
    cli.mensaje(7, "uno", item_id=item)  # the same chat item again (e.g. delivered twice)
    cli.mensaje(7, "dos")
    recibidos = arnes.mensajes(2)
    time.sleep(0.2)
    assert [m.texto for m in arnes.recibidos] == ["uno", "dos"]
    assert recibidos[0].mensaje_id == str(item)


def test_contacto_conectado_solo_se_registra(cli, arrancar):
    arnes = arrancar(cli)
    cli.contacto_conectado(9, "Nuevo")
    cli.mensaje(9, "hola")
    assert [m.texto for m in arnes.mensajes(1)] == ["hola"]


def test_una_excepcion_del_manejador_no_detiene_el_canal(cli, arrancar):
    def manejador(m):
        if m.texto == "rompe":
            raise RuntimeError("fallo del manejador")

    arnes = arrancar(cli, manejador=manejador)
    cli.mensaje(7, "rompe")
    cli.mensaje(8, "sigue")
    cli.mensaje(7, "y sigue")
    assert sorted(m.texto for m in arnes.mensajes(3)) == ["rompe", "sigue", "y sigue"]
    assert arnes.canal.estado().conectado


def test_eventos_desconocidos_y_registros_rotos_se_cuentan(cli, arrancar):
    arnes = arrancar(cli)
    cli.emitir({"type": "algoDelFuturo", "user": {"userId": 1}})
    cli.emitir({"type": "newChatItems", "user": {"userId": 1}, "chatItems": [{"roto": True}]})
    cli.mensaje(7, "válido")
    assert [m.texto for m in arnes.mensajes(1)] == ["válido"]
    resumen = arnes.canal.contador.resumen()
    assert resumen["eventos_desconocidos"].get("algoDelFuturo") == 1
    assert resumen["registros_invalidos"] >= 1


def test_mismo_contacto_en_orden_y_contactos_distintos_en_paralelo(cli, arrancar):
    liberar = threading.Event()
    orden: list[str] = []

    def manejador(m):
        if m.texto == "lento":
            liberar.wait(5)
        orden.append(m.texto)

    arnes = arrancar(cli, manejador=manejador)
    cli.mensaje(7, "lento")
    cli.mensaje(7, "después del lento")
    cli.mensaje(8, "otro contacto")
    assert esperar(lambda: orden == ["otro contacto"])  # contact 8 does not wait for contact 7
    liberar.set()
    assert esperar(lambda: orden == ["otro contacto", "lento", "después del lento"])
    assert len(arnes.recibidos) == 3


def test_el_contexto_llega_al_hilo_del_manejador(cli, tmp_path):
    from asistente.plataforma.mensajeria.simplex import CanalSimplex
    from tests.mensajeria.simplex.falso import configuracion_para

    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="sin")
    visto: list[str] = []
    canal = CanalSimplex(configuracion_para(cli, tmp_path))

    def iniciar():
        var.set("del-servicio")
        canal.iniciar(lambda m: visto.append(var.get()))

    hilo = threading.Thread(target=iniciar, daemon=True)
    hilo.start()
    try:
        assert esperar(lambda: canal.estado().conectado)
        cli.mensaje(7, "hola")
        assert esperar(lambda: visto == ["del-servicio"])
    finally:
        canal.parar()
        hilo.join(5)


# --- files ---


def _archivos(tmp_path: Path) -> list[Path]:
    raiz = tmp_path / "archivos"
    return [p for p in raiz.rglob("*") if p.is_file() and "recepcion" not in p.parts]


def test_pdf_admitido_se_guarda_en_privado(cli, arrancar, tmp_path):
    arnes = arrancar(cli)
    cli.mensaje(7, "mi recibo", tipo="file", archivo={"nombre": "recibo luz.pdf", "contenido": PDF})
    [m] = arnes.mensajes(1)
    [adjunto] = m.adjuntos
    assert m.texto == "mi recibo"
    assert adjunto.nombre == "recibo luz.pdf" and adjunto.mime == "application/pdf" and adjunto.tamano == len(PDF)
    ruta = Path(adjunto.ruta_local)
    assert ruta.read_bytes() == PDF
    assert ruta.resolve().is_relative_to((tmp_path / "archivos").resolve())
    assert ruta.name == "recibo luz.pdf"
    assert stat.S_IMODE(ruta.stat().st_mode) == 0o600
    assert stat.S_IMODE(ruta.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "archivos").stat().st_mode) == 0o700
    # The CLI was told a path we chose (random, in the staging folder), never one built from the sender's name.
    [orden] = cli.comandos_que_empiezan("/freceive ")
    ruta_cli = orden.split(" ")[-1]
    assert orden.startswith("/freceive 500 approved_relays=on encrypt=off ")
    assert Path(ruta_cli).parent == (tmp_path / "archivos" / "recepcion").resolve()
    assert "recibo" not in ruta_cli
    assert not os.path.exists(ruta_cli)  # the staged copy is removed


def test_imagen_y_nota_de_voz(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="image", archivo={"nombre": "IMG_1.jpg", "contenido": JPEG})
    cli.mensaje(7, "", tipo="voice", archivo={"nombre": "voz.m4a", "contenido": M4A})
    imagen, voz = arnes.mensajes(2)
    assert imagen.adjuntos[0].mime == "image/jpeg" and not imagen.es_voz
    assert voz.es_voz and voz.adjuntos[0].mime == "audio/mp4"


def test_voz_desactivada_no_se_descarga_pero_se_marca(cli, arrancar):
    arnes = arrancar(cli, aceptar_voz=False)
    cli.mensaje(7, "", tipo="voice", archivo={"nombre": "voz.m4a", "contenido": M4A})
    [m] = arnes.mensajes(1)
    assert m.es_voz and m.adjuntos == []
    assert not cli.comandos_que_empiezan("/freceive")


def test_archivo_demasiado_grande_no_se_descarga(cli, arrancar):
    arnes = arrancar(cli, archivos_max_bytes=1000)
    cli.mensaje(7, "pie de foto", tipo="file", archivo={"nombre": "grande.pdf", "contenido": PDF})
    [m] = arnes.mensajes(1)
    assert m.texto == "pie de foto" and m.adjuntos == []
    assert not cli.comandos_que_empiezan("/freceive")


def test_tipo_no_permitido_no_se_descarga(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "programa.exe", "contenido": EXE})
    cli.mensaje(7, "", tipo="video", archivo={"nombre": "v.mp4", "contenido": M4A})
    arnes.mensajes(2)
    assert all(m.adjuntos == [] for m in arnes.recibidos)
    assert not cli.comandos_que_empiezan("/freceive")


def test_contenido_que_no_coincide_con_lo_declarado_se_descarta(cli, arrancar, tmp_path):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "factura.pdf", "contenido": EXE})
    [m] = arnes.mensajes(1)
    assert m.adjuntos == []
    assert cli.comandos_que_empiezan("/freceive")
    assert _archivos(tmp_path) == []
    assert list((tmp_path / "archivos" / "recepcion").iterdir()) == []


def test_nombre_con_ruta_no_escapa_de_la_carpeta(cli, arrancar, tmp_path):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "../../../etc/passwd.pdf", "contenido": PDF})
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "..\\..\\win\\x.pdf", "contenido": PDF})
    a, b = arnes.mensajes(2)
    for m, esperado in ((a, "passwd.pdf"), (b, "x.pdf")):
        ruta = Path(m.adjuntos[0].ruta_local)
        assert ruta.name == esperado
        assert ruta.resolve().is_relative_to((tmp_path / "archivos").resolve())
    assert not Path("/etc/passwd.pdf").exists()


def test_enlace_simbolico_en_la_recepcion_se_rechaza(cli, arrancar, tmp_path):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF, "modo": "enlace"})
    [m] = arnes.mensajes(1)
    assert m.adjuntos == []
    assert _archivos(tmp_path) == []  # nothing accepted
    assert not any(p.is_symlink() for p in (tmp_path / "archivos" / "recepcion").iterdir())  # the link is removed


def test_tamano_real_distinto_del_declarado_se_rechaza(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF, "modo": "crece"})
    [m] = arnes.mensajes(1)
    assert m.adjuntos == []


def test_error_de_descarga_entrega_el_mensaje_sin_adjunto(cli, arrancar):
    arnes = arrancar(cli)
    cli.mensaje(7, "con error", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF, "modo": "error"})
    [m] = arnes.mensajes(1)
    assert m.texto == "con error" and m.adjuntos == []


def test_descarga_que_no_termina_se_cancela(cli, arrancar):
    arnes = arrancar(cli, timeout_archivo_s=0.3)
    cli.mensaje(7, "nunca llega", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF, "modo": "nunca"})
    [m] = arnes.mensajes(1, timeout=5)
    assert m.adjuntos == []
    assert esperar(lambda: bool(cli.comandos_que_empiezan("/fcancel 500")))


def test_los_mensajes_posteriores_esperan_al_archivo(cli, arrancar):
    cli.retraso_archivo_s = 0.4
    arnes = arrancar(cli)
    cli.mensaje(7, "el documento", tipo="file", archivo={"nombre": "a.pdf", "contenido": PDF})
    cli.mensaje(7, "¿qué dice este documento?")
    cli.mensaje(8, "otro contacto no espera")
    recibidos = arnes.mensajes(3)
    assert recibidos[0].texto == "otro contacto no espera"
    assert [m.texto for m in recibidos[1:]] == ["el documento", "¿qué dice este documento?"]
    assert recibidos[1].adjuntos
