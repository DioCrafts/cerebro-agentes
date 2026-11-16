"""Agent tools (read-only, untrusted-data wrapping), proactive notices and «No me interesa»."""

from __future__ import annotations

from datetime import date

from asistente.capacidades.correo import MODULO, almacen, decisiones
from asistente.capacidades.correo.avisos import generar_avisos
from asistente.capacidades.correo.sincronizacion import sincronizar
from asistente.nucleo.modulos.contrato import ContextoModulo
from asistente.nucleo.modulos.registro import problemas_de
from tests.modulos.correo import corpus
from tests.modulos.correo.falso import GoogleFalso
from tests.modulos.correo.test_oauth_y_sincronizacion import conectar

HOY = date(2026, 11, 16)


def _preparar(google: GoogleFalso) -> ContextoModulo:
    google.mensajes = corpus.todos()
    conectar(google)
    sincronizar("case-1", hoy=HOY)
    return ContextoModulo(perfil={}, hoy=HOY, case_id="case-1")


def _tool(nombre: str):
    return next(h for h in MODULO.herramientas if h.spec.name == nombre)


def test_el_modulo_cumple_el_contrato_y_solo_lee() -> None:
    assert problemas_de(MODULO, paquete="correo") == []
    assert {h.spec.name for h in MODULO.herramientas} == {"correo_resumen", "correo_buscar", "correo_propuestas"}
    assert {h.efecto for h in MODULO.herramientas} == {"lectura"}
    assert "correo.clasificado" in MODULO.eventos


def test_resumen_de_la_semana(google: GoogleFalso) -> None:
    ctx = _preparar(google)
    r = _tool("correo_resumen").ejecutar({"periodo": "semana"}, ctx)
    assert not r.es_error
    texto = r.contenido
    assert (
        texto.index("<datos_de_correo_no_confiables>")
        < texto.index("Requieren una acción")
        < texto.index("</datos_de_correo_no_confiables>")
    )
    assert "plazo: 25/11/2026" in texto and "Agencia Tributaria" in texto and "plazo: 20/11/2026" in texto
    assert "importe: 129,99 € (leído en «Total: 129,99 €»)" in texto and "[m002luz]" not in texto  # 05/11: older
    assert "Posible fraude" in texto and "imita al dominio de Agencia Tributaria" in texto
    assert "1 promoción o boletín" in texto


def test_resumen_de_hoy_y_sin_conexion(google: GoogleFalso) -> None:
    sin = _tool("correo_resumen").ejecutar({}, ContextoModulo(perfil={}, hoy=HOY, case_id="case-x"))
    assert sin.es_error and "Conectar Gmail" in sin.contenido
    ctx = _preparar(google)
    r = _tool("correo_resumen").ejecutar({"periodo": "hoy"}, ctx)
    assert "Correo de hoy" in r.contenido and "Requieren una acción" in r.contenido  # live deadlines still listed
    assert _tool("correo_resumen").ejecutar({"periodo": "año"}, ctx).es_error


def test_buscar(google: GoogleFalso) -> None:
    ctx = _preparar(google)
    buscar = _tool("correo_buscar").ejecutar
    r = buscar({"categoria": "factura_luz"}, ctx)
    assert "[m002luz]" in r.contenido and "[m003compra]" not in r.contenido
    assert "[m003compra]" in buscar({"texto": "tienda ejemplo"}, ctx).contenido
    assert "[m001aeat]" in buscar({"dominio": "gob.es"}, ctx).contenido
    assert buscar({"con_plazo": True, "desde": "2026-11-15"}, ctx).resumen == "2 resultado(s)"
    assert buscar({"categoria": "nada"}, ctx).es_error and buscar({"desde": "ayer"}, ctx).es_error


def test_propuestas_no_se_aplican_desde_la_herramienta(google: GoogleFalso) -> None:
    ctx = _preparar(google)
    r = _tool("correo_propuestas").ejecutar({}, ctx)
    assert "Para Caducidades y garantías" in r.contenido and "solo el usuario puede confirmarlas" in r.contenido
    assert all(p.estado == "pendiente" for p in almacen.propuestas("case-1"))


def test_avisos_idempotentes(google: GoogleFalso) -> None:
    ctx = _preparar(google)
    avisos = {a.clave: a for a in generar_avisos(ctx)}
    assert set(avisos) == {
        "administracion:agenciatributaria.gob.es:m001aeat",
        "accion:colegio-ejemplo.es:m006accion",
        "phishing:agenciatributaria-reembolsos.com:m008phish",
        "phishing:aviso-cliente.net:m009spoof",
        "phishing:proveedor-ejemplo.es:m010inyec",
    }
    aeat = avisos["administracion:agenciatributaria.gob.es:m001aeat"]
    assert aeat.gravedad == "urgente" and aeat.vence == date(2026, 11, 25)
    assert (
        "diez" not in aeat.cuerpo
        and "10 días naturales" in aeat.cuerpo
        and "art. 43.2 de la Ley 39/2015" in aeat.cuerpo
    )
    assert any("BOE-A-2015-10565" in f for f in aeat.fuentes)
    assert avisos["accion:colegio-ejemplo.es:m006accion"].gravedad == "aviso"
    assert [a.clave for a in generar_avisos(ctx)] == [a.clave for a in generar_avisos(ctx)]
    # Deadline within 3 days: urgent.
    tarde = ContextoModulo(perfil={}, hoy=date(2026, 11, 18), case_id="case-1")
    assert {a.clave: a for a in generar_avisos(tarde)}["accion:colegio-ejemplo.es:m006accion"].gravedad == "urgente"


def test_no_me_interesa_por_categoria_y_remitente(google: GoogleFalso, db_path: str) -> None:
    ctx = _preparar(google)
    decisiones.silenciar(db_path, case_id="case-1", tipo="categoria", valor="accion", user_id=None)
    claves = {a.clave for a in generar_avisos(ctx)}
    assert "accion:colegio-ejemplo.es:m006accion" not in claves
    decisiones.silenciar(db_path, case_id="case-1", tipo="remitente", valor="proveedor-ejemplo.es", user_id=None)
    decisiones.silenciar(db_path, case_id="case-1", tipo="remitente", valor="agenciatributaria.gob.es", user_id=None)
    claves = {a.clave for a in generar_avisos(ctx)}
    # A muted sender stops its notices, but never a phishing warning in its name.
    assert "administracion:agenciatributaria.gob.es:m001aeat" not in claves
    assert "phishing:proveedor-ejemplo.es:m010inyec" in claves
    r = _tool("correo_resumen").ejecutar({}, ctx)
    assert "[m006accion]" not in r.contenido and "no te interesan" in r.contenido
    # The module's notice keys map onto its ámbitos for the core's inbox filter too.
    from asistente.nucleo.memoria.ambitos import ambito_de_aviso

    ambito = ambito_de_aviso("modulo:correo", "modulo:correo:accion:colegio-ejemplo.es:m006accion")
    assert ambito is not None and ambito.propuesta == "correo:accion:colegio-ejemplo.es"
