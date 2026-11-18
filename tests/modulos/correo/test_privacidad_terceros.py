"""Third parties' e-mail addresses and phone numbers are masked in what is kept of a message."""

from __future__ import annotations

from datetime import date
import sqlite3

import pytest

from asistente.capacidades.correo import almacen
from asistente.capacidades.correo.extraccion import TAPA_DIRECCION, enmascarar
from asistente.capacidades.correo.sincronizacion import sincronizar
from tests.modulos.correo.corpus import AYER
from tests.modulos.correo.falso import GoogleFalso, correo
from tests.modulos.correo.test_oauth_y_sincronizacion import conectar


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Responde a juan.perez@gmail.com antes del viernes", f"Responde a {TAPA_DIRECCION} antes del viernes"),
        ("Reserva para maria+viajes@empresa.co.uk: 2 noches", f"Reserva para {TAPA_DIRECCION}: 2 noches"),
        ("Llama al 600 123 456", "Llama al ••• ••• •••"),
        ("o al +34 912 34 56 78.", "o al +•• ••• •• •• ••."),
        ("Fax 91-123-45-67", "Fax ••-•••-••-••"),
    ],
)
def test_addresses_and_phones_are_masked(texto: str, esperado: str) -> None:
    assert enmascarar(texto, "") == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "Importe 1.234,56 € el 16/11/2026",
        "Lectura del 01 11 2026 al 30 11 2026",
        "Potencia contratada 3,45 kW",
        "Total 12 345 € en 2026",
    ],
)
def test_amounts_dates_and_short_groups_are_kept(texto: str) -> None:
    assert enmascarar(texto, "") == texto


def test_the_extracted_literal_itself_is_never_masked() -> None:
    assert enmascarar("Teléfono de averías: 900 123 456", "900 123 456") == "Teléfono de averías: 900 123 456"
    assert enmascarar("Escribe a avisos@ejemplo.es", "avisos@ejemplo.es") == "Escribe a avisos@ejemplo.es"


def test_a_stored_subject_keeps_no_third_party_address(google: GoogleFalso, db_path: str) -> None:
    google.mensajes = [
        correo(
            "m100accion",
            de="Secretaría <secretaria@colegio-ejemplo.es>",
            asunto="Autorización: firmar y enviar a tutora.lopez@colegio-ejemplo.es o llamar al 600 123 456",
            texto="Debe entregar la autorización firmada antes del 20 de noviembre de 2026.\n",
            fecha=AYER,
        )
    ]
    conectar(google)
    sincronizar("case-1", hoy=date(2026, 11, 16))
    (item,) = almacen.elementos("case-1")
    assert item.asunto is not None and TAPA_DIRECCION in item.asunto
    with sqlite3.connect(db_path) as conn:
        volcado = "\n".join(conn.iterdump())
    assert "tutora.lopez" not in volcado and "600 123 456" not in volcado
