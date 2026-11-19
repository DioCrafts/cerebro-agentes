"""Attachments from a paired chat: read through the documents capability under the user's consent (never stored);
voice notes acknowledged and deleted."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from asistente.ambitos.fiscal import documentos as documentos_fiscales
from asistente.ambitos.fiscal.documentos import AnalisisDocumento, HechoPropuesto
from asistente.capacidades.documentos.lectura import DocumentoNoLegibleError
from asistente.plataforma import settings
from asistente.plataforma.auth.store import AuthStore
from asistente.plataforma.mensajeria import pasarela as pasarela_mod
from asistente.plataforma.mensajeria.formato import a_canal
from asistente.plataforma.ops import consumo
from .conftest import CASE_ID, Montaje

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class Lector:
    """Stands for the document reader's provider; ``analizar_documento`` is replaced by :func:`_analizador`."""

    name, model = "fake", "fake-1"


def _carpeta(montaje: Montaje) -> Path:
    carpeta = Path(montaje.db).parent / "simplex-archivos"
    carpeta.mkdir(exist_ok=True)
    return carpeta


@pytest.fixture
def analisis(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    llamadas: list[dict[str, Any]] = []

    def analizar(provider: Any, *, filename: str, data: bytes) -> AnalisisDocumento:
        contexto = consumo._CONTEXTO.get()
        llamadas.append(
            {
                "provider": provider,
                "filename": filename,
                "data": data,
                "consumidor": (contexto.user_id, contexto.case_id) if contexto is not None else None,
            }
        )
        if data.startswith(b"%PDF-1.4\nroto"):
            raise DocumentoNoLegibleError("No se ha podido leer el PDF.")
        return AnalisisDocumento(
            filename=filename,
            tipo_documento="certificado de retenciones",
            paginas=1,
            caracteres=900,
            hechos_propuestos=[
                HechoPropuesto(
                    name="rendimiento_trabajo_bruto",
                    value="38250.40",
                    evidencia="Rendimientos íntegros del trabajo: 38.250,40",
                )
            ],
            avisos=["Comprueba el ejercicio del certificado."],
        )

    monkeypatch.setattr(documentos_fiscales, "analizar_documento", analizar)
    return llamadas


def _con_cuenta(montaje: Montaje, monkeypatch: pytest.MonkeyPatch, *, consiente: bool) -> str:
    monkeypatch.setattr(settings, "AUTH_MODE", "required")
    monkeypatch.setattr(settings, "AUTH_SCRYPT_N", 2**8)
    auth = AuthStore(montaje.db)
    try:
        usuario = auth.create_user(
            "ana@example.com",
            "una-clave-larga-1",
            consents={
                "tratamiento_datos": True,
                "envio_documentos_a_proveedor_llm": consiente,
                "avisos_por_correo": False,
            },
        )
        auth.set_case_owner(CASE_ID, usuario.id)
    finally:
        auth.close()
    montaje.emparejar(user_id=usuario.id)
    return usuario.id


def test_without_consent_the_document_is_refused_and_deleted_unread(
    montaje: Montaje, monkeypatch: pytest.MonkeyPatch, analisis: list[dict[str, Any]]
) -> None:
    _con_cuenta(montaje, monkeypatch, consiente=False)
    adjunto = montaje.canal.adjunto(_carpeta(montaje), "certificado.pdf", PDF)
    assert montaje.decir("mira", adjuntos=[adjunto]) == [a_canal(pasarela_mod.TEXTO_SIN_CONSENTIMIENTO, "simplex")]
    assert analisis == [] and not Path(adjunto.ruta_local).exists()


def test_with_consent_it_is_read_proposed_never_saved_and_deleted(
    montaje: Montaje, monkeypatch: pytest.MonkeyPatch, analisis: list[dict[str, Any]]
) -> None:
    usuario = _con_cuenta(montaje, monkeypatch, consiente=True)
    lector = Lector()
    montaje.lectores.append(lector)
    adjunto = montaje.canal.adjunto(_carpeta(montaje), "../../etc/Certificado 2025.pdf", PDF)
    antes = montaje.casos.get_case(CASE_ID)

    (texto,) = montaje.decir("", adjuntos=[adjunto])

    (llamada,) = analisis
    assert llamada["provider"] is lector and llamada["data"] == PDF
    assert llamada["filename"] == "Certificado_2025.pdf"  # the sender's name is never a path
    assert llamada["consumidor"] == (usuario, CASE_ID)  # metered to the owner (the per-user budget)
    assert "He leído *Certificado_2025.pdf* (certificado de retenciones, 1 página)." in texto
    assert "*no he guardado nada*" in texto
    assert "• rendimiento trabajo bruto: 38250.40 — «Rendimientos íntegros del trabajo: 38.250,40»" in texto
    assert "_Aviso: Comprueba el ejercicio del certificado._" in texto
    assert montaje.casos.get_case(CASE_ID) == antes  # proposals only
    assert not Path(adjunto.ruta_local).exists()
    (evento,) = [e for e in montaje.casos.list_audit_events(CASE_ID) if e.get("event_type") == "document_analyzed"]
    assert evento["canal"] == "prueba" and evento["proposed_facts"] == ["rendimiento_trabajo_bruto"]


def test_without_accounts_documents_need_no_consent_and_images_are_read_too(
    montaje: Montaje, analisis: list[dict[str, Any]]
) -> None:
    montaje.emparejar()
    montaje.lectores.append(Lector())
    imagen = montaje.canal.adjunto(_carpeta(montaje), "foto", PNG, mime="image/png")
    (texto,) = montaje.decir(adjuntos=[imagen])
    assert analisis[0]["filename"] == "foto.png" and texto.startswith("He leído *foto.png*")


def test_unsupported_unreadable_and_oversized_files(
    montaje: Montaje, analisis: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    montaje.emparejar()
    montaje.lectores.append(Lector())
    texto_plano = montaje.canal.adjunto(_carpeta(montaje), "notas.txt", b"hola")
    assert montaje.decir(adjuntos=[texto_plano]) == [pasarela_mod.TEXTO_TIPO_NO_ADMITIDO]
    roto = montaje.canal.adjunto(_carpeta(montaje), "roto.pdf", b"%PDF-1.4\nroto")
    assert montaje.decir(adjuntos=[roto]) == ["No se ha podido leer el PDF. He borrado el archivo."]
    from asistente.capacidades.documentos import lectura

    monkeypatch.setattr(lectura, "MAX_DOCUMENT_BYTES", 10)
    grande = montaje.canal.adjunto(_carpeta(montaje), "grande.pdf", PDF)
    assert montaje.decir(adjuntos=[grande])[0].startswith("El archivo es demasiado grande")
    assert not any(Path(a.ruta_local).exists() for a in (texto_plano, roto, grande))


def test_voice_notes_are_acknowledged_and_deleted_without_listening(montaje: Montaje) -> None:
    montaje.emparejar()
    nota = montaje.canal.adjunto(_carpeta(montaje), "nota.m4a", b"\x00\x00\x00 ftypM4A ")
    assert montaje.decir(es_voz=True, adjuntos=[nota]) == [pasarela_mod.TEXTO_VOZ]
    assert not Path(nota.ruta_local).exists()
    assert montaje.agente.construido == 0
