"""Masking before anything becomes a candidate case: personal data out, amounts in, and a diff of what went."""

from __future__ import annotations

import json

import pytest

from asistente.nucleo.evaluacion.redaccion import (
    Enmascarador,
    enmascarar_texto,
    identificadores_en,
    nombres_de_perfil,
    nombres_en,
)

NOMBRES = ["Ana Pérez", "Ana", "Pérez", "Luis"]


@pytest.mark.parametrize(
    ("texto", "debe_irse", "tipo"),
    [
        ("Soy Ana Pérez y te escribo por la renta", "Ana Pérez", "nombre"),
        ("Pregúntale a luis por la factura", "luis", "nombre"),
        ("Mi IBAN es ES91 2100 0418 4502 0005 1332", "ES91 2100", "iban"),
        ("Pago con la tarjeta 4111 1111 1111 1111", "4111 1111", "tarjeta"),
        ("Mi DNI es 12345678Z", "12345678Z", "documento"),
        ("El NIE de mi pareja es X1234567L", "X1234567L", "documento"),
        ("Llámame al 612 345 678.", "612 345 678", "telefono"),
        ("Mi fijo es +34 912 34 56 78", "912 34 56 78", "telefono"),
        ("Escríbeme a ana.perez@example.com", "example.com", "correo"),
        ("Vivo en C/ Mayor 12, 3º B, 28013 Madrid", "Mayor 12", "direccion"),
        ("La casa está en Avenida de la Paz 4, bajo", "Paz 4", "direccion"),
        ("El CUPS es ES0031405912345678JY0F", "ES0031405912345678JY0F", "cups"),
        ("La referencia del contrato es 99887766554", "99887766554", "codigo"),
    ],
)
def test_personal_data_is_masked_with_its_diff(texto: str, debe_irse: str, tipo: str) -> None:
    enmascarado, cambios = enmascarar_texto(texto, nombres=NOMBRES)
    assert debe_irse.casefold() not in enmascarado.casefold()
    assert any(c.tipo == tipo for c in cambios), cambios
    # The diff never carries the original: an abbreviated view only.
    assert all(debe_irse not in c.vista for c in cambios)
    assert not identificadores_en(enmascarado)


@pytest.mark.parametrize(
    "texto",
    [
        "Me sale a pagar 5.140,12 € en la renta de 2025.",
        "Gano 30000 € al año y pago 474,21 € al mes desde el 15/01/2020.",
        "Hipoteca de 100.000 € a 300 meses al 3 %, firmada el 2020-01-15.",
        "Pago 0,15 €/kWh con 4,6 kW contratados y gasté 270 kWh.",
        "Me devolvieron -300,00 € y el IRPF bajó un 18,5 %.",
        "Debo 912.345.678 € al banco.",
    ],
)
def test_amounts_dates_and_quantities_are_kept(texto: str) -> None:
    enmascarado, cambios = enmascarar_texto(texto, nombres=NOMBRES)
    assert enmascarado == texto and cambios == []


def test_names_come_from_the_profile_and_the_account() -> None:
    hechos = [
        ("datos_conyuge", json.dumps({"nombre": "Marta López", "rendimiento_trabajo_bruto": 20000})),
        ("comunidad_autonoma", "Madrid"),
        ("caducidades_documentos", [{"tipo": "dni", "titular": "titular", "fecha_caducidad": "2031-04-03"}]),
    ]
    nombres = nombres_de_perfil(hechos, extra=["ana.perez"])
    assert {"Marta López", "Marta", "López", "ana", "perez"} <= set(nombres)
    # Roles, regions and document kinds are not names.
    assert not {"Madrid", "titular", "dni"} & set(nombres)
    texto, cambios = enmascarar_texto("Marta y yo (Ana) vivimos en Madrid", nombres=nombres)
    assert texto == "[nombre] y yo ([nombre]) vivimos en Madrid" and len(cambios) == 2
    # Accents and case do not hide a name.
    assert nombres_en("hablé con MARTA lopez", ["Marta López"]) == ["Marta López"]


def test_json_values_are_masked_but_numbers_untouched() -> None:
    m = Enmascarador(["Ana"])
    valor = m.valor({"pregunta": "Soy Ana, DNI 12345678Z", "importe": 1234.5, "lista": ["612345678", 3]}, "x")
    assert valor == {"pregunta": "Soy [nombre], DNI [documento]", "importe": 1234.5, "lista": ["[teléfono]", 3]}
    assert [c.campo for c in m.cambios] == ["x.pregunta", "x.pregunta", "x.lista[0]"]
