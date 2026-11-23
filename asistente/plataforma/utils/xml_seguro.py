"""Parsing of untrusted XML (files users upload): no DTD, no entity declarations, anywhere in the prolog.

``xml.etree.ElementTree`` never fetches external entities, and the bundled expat caps the amplification of
internal ones, but within that cap a 15 MB document can still expand to a gigabyte of text. A substring check
on the first kilobytes is not enough either: a long comment before the ``<!DOCTYPE`` pushes it out of reach.
:func:`rechazar_dtd` asks expat itself, and stops at the first element, so it only reads the prolog.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.parsers import expat


class XmlInseguroError(ValueError):
    """The document declares a DTD or entities and is not processed."""


class _DtdError(Exception):
    pass


class _PrimerElementoError(Exception):
    pass


def rechazar_dtd(datos: bytes | str) -> None:
    """Raise :class:`XmlInseguroError` when the document declares a DOCTYPE or an entity.

    Malformed XML is left for the real parser to report. Only the prolog is read: the scan stops at the
    first element, where a DOCTYPE can no longer appear.
    """
    parser = expat.ParserCreate()

    def _dtd(*_args: object) -> None:
        raise _DtdError

    def _elemento(*_args: object) -> None:
        raise _PrimerElementoError

    parser.StartDoctypeDeclHandler = _dtd
    parser.EntityDeclHandler = _dtd
    parser.StartElementHandler = _elemento
    try:
        parser.Parse(datos, True)
    except _DtdError as exc:
        raise XmlInseguroError("El XML declara un DOCTYPE o entidades; por seguridad no se procesa.") from exc
    except _PrimerElementoError, expat.ExpatError:
        return


def parse_seguro(datos: bytes | str) -> ET.Element:
    """``ET.fromstring`` after :func:`rechazar_dtd`. Raises ``XmlInseguroError`` or ``ET.ParseError``."""
    rechazar_dtd(datos)
    return ET.fromstring(datos)


__all__ = ["XmlInseguroError", "parse_seguro", "rechazar_dtd"]
