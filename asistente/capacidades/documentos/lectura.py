"""Reading an uploaded document: decoding, text extraction, image detection and the model readings.

Moved here from ``tax/documentos.py`` (the tax module's reader) so that every domain reads documents the same
way through the ``documentos`` capability. What a domain looks for, and the check of every value against the
literal passage it was read from, stay the domain's (``modulos/fiscal/documentos.py``,
``modulos/caducidades/ticket.py``): this file only reads.

* A text PDF or a text file: its text is extracted here and read by the model ONCE; the domain then discards
  every value whose passage is not in that text.
* A scan or a photo has no text to check against: the model reads the pages itself, TWICE, independently, each
  time with a transcription; the domain keeps only what both readings agree on, each checked against its own
  transcription.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Literal
import unicodedata

from asistente.nucleo.agent.providers.base import Attachment

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PAGES = 60
# Scanned documents are read by the model page by page: fewer pages allowed.
MAX_SCAN_PAGES = 20
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class DocumentoNoLegibleError(ValueError):
    """The upload cannot be read (bad encoding, unsupported format, too large…)."""


class DocumentoEscaneadoError(DocumentoNoLegibleError):
    """A PDF with no text layer: it must be read as images."""

    def __init__(self, message: str, *, paginas: int) -> None:
        super().__init__(message)
        self.paginas = paginas


def decode_upload(content_base64: str) -> bytes:
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DocumentoNoLegibleError("El contenido del documento no es base64 válido.") from exc
    if not data:
        raise DocumentoNoLegibleError("El documento está vacío.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentoNoLegibleError("El documento supera el tamaño máximo de 10 MB.")
    return data


def extract_text(data: bytes, filename: str) -> tuple[str, int]:
    """Return (text, pages). A PDF without a text layer raises ``DocumentoEscaneadoError``."""
    if data[:5] == b"%PDF-":
        try:
            import pypdfium2
        except ImportError as exc:  # pragma: no cover - ships with docling
            raise DocumentoNoLegibleError("No está disponible el lector de PDF en este servidor.") from exc
        try:
            pdf = pypdfium2.PdfDocument(data)
        except Exception as exc:
            raise DocumentoNoLegibleError(
                "No se pudo abrir el PDF (¿está dañado o protegido con contraseña?)."
            ) from exc
        try:
            pages = len(pdf)
            if pages > MAX_PAGES:
                raise DocumentoNoLegibleError(f"El PDF tiene {pages} páginas; el máximo es {MAX_PAGES}.")
            parts: list[str] = []
            for index in range(pages):
                textpage = pdf[index].get_textpage()
                parts.append(textpage.get_text_range())
        finally:
            pdf.close()
        text = "\n".join(parts).strip()
        if len(text) < 40:
            raise DocumentoEscaneadoError("El PDF no contiene texto seleccionable (parece escaneado).", paginas=pages)
        return text, pages
    if b"\x00" in data[:2000]:
        raise DocumentoNoLegibleError(
            f"Formato no admitido ({filename}). Sube un PDF, una imagen (PNG, JPEG, WebP) o un archivo de texto."
        )
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding).strip(), 1
        except UnicodeDecodeError:
            continue
    raise DocumentoNoLegibleError("No se pudo leer el archivo como texto.")  # pragma: no cover


def tipo_de_imagen(data: bytes) -> str | None:
    """Media type of an image upload, by its signature (the file name is not trusted)."""
    for signature, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def normalizar_pasaje(text: str) -> str:
    """Case-, accent- and whitespace-insensitive form for passage matching (public: domains reading
    documents check their literal evidence the same way)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", "", stripped).lower()


def leer(
    provider: Any,
    *,
    filename: str,
    data: bytes,
    instrucciones: str,
    esquema: dict[str, Any],
    nombre_esquema: str,
    instrucciones_imagen: str,
    esquema_imagen: dict[str, Any],
    nombre_esquema_imagen: str,
    que_localizar: str,
    max_caracteres: int | None = None,
    max_paginas_escaneo: int = MAX_SCAN_PAGES,
    aviso_escaneo_largo: str | None = None,
) -> dict[str, Any]:
    """Read ``data`` with the model: ``{"lectura", "paginas", "texto", "lecturas": [raw, …]}``.

    ``texto`` is the extracted text (``lectura="texto"``, one reading) or ``""`` (``lectura="imagen"``, two
    independent readings, each with its own ``transcripcion``). Raises ``DocumentoNoLegibleError`` or the
    provider's error.
    """
    lectura: Literal["texto", "imagen"]
    imagen = tipo_de_imagen(data)
    adjunto: Attachment | None = None
    paginas = 1
    texto = ""
    if imagen is not None:
        adjunto = Attachment(imagen, data, filename)
    else:
        try:
            texto, paginas = extract_text(data, filename)
        except DocumentoEscaneadoError as exc:
            if exc.paginas > max_paginas_escaneo:
                raise DocumentoNoLegibleError(
                    aviso_escaneo_largo
                    or (
                        f"El PDF está escaneado y tiene {exc.paginas} páginas; el máximo para documentos escaneados "
                        f"es {max_paginas_escaneo}."
                    )
                ) from exc
            adjunto = Attachment("application/pdf", data, filename)
            paginas = exc.paginas
    if adjunto is not None:
        lectura = "imagen"
        lecturas = [
            provider.complete_json(
                system=instrucciones_imagen,
                user=f'El documento adjunto se llama "{filename}". Transcríbelo y localiza {que_localizar}.',
                schema=esquema_imagen,
                schema_name=nombre_esquema_imagen,
                attachments=[adjunto],
            )
            for _ in range(2)
        ]
        return {"lectura": lectura, "paginas": paginas, "texto": "", "lecturas": lecturas}
    lectura = "texto"
    cuerpo = texto[:max_caracteres] if max_caracteres is not None else texto
    raw = provider.complete_json(
        system=instrucciones,
        user=f'<documento nombre="{filename}">\n{cuerpo}\n</documento>',
        schema=esquema,
        schema_name=nombre_esquema,
    )
    return {"lectura": lectura, "paginas": paginas, "texto": texto, "lecturas": [raw]}


__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_PAGES",
    "MAX_SCAN_PAGES",
    "DocumentoEscaneadoError",
    "DocumentoNoLegibleError",
    "decode_upload",
    "extract_text",
    "leer",
    "normalizar_pasaje",
    "tipo_de_imagen",
]
