"""The two redactions agree on identifiers and disagree on figures, and both of those are on purpose."""

from __future__ import annotations

from asistente.capacidades.navegador.instantanea import redactar
from asistente.capacidades.navegador.personales import IDENTIFICADORES
from asistente.capacidades.navegador.seguridad import redactar_texto

# One per identifier pattern, each with the shape a real page prints. The German IBAN is here because the two
# copies of the pattern had drifted: it was masked whole in the snapshot and left showing its DE89 in the
# skeleton. Keep a case per country length, since the Spanish one has 24 characters and this one 22.
IDENTIFICADORES_REALES = (
    "ES0021000000000001AA",
    "ES91 2100 0418 4502 0005 1332",
    "DE89 3704 0044 0532 0130 00",
    "12345678Z",
    "X1234567L",
    "ana@example.org",
)


def test_los_identificadores_se_enmascaran_igual_en_el_esqueleto_y_en_la_instantanea() -> None:
    for dato in IDENTIFICADORES_REALES:
        assert dato not in redactar_texto(f"Titular: {dato} (cliente)"), dato
        assert dato not in redactar(f'  - cell "{dato}" [ref=e7]'), dato


def test_las_cifras_que_una_mision_necesita_leer_solo_sobreviven_en_la_instantanea() -> None:
    linea = '  - cell "Del 2026-10-01 al 2026-10-31: 1.234,56 kWh · 99,10 €" [ref=e4]'
    enmascarada = redactar(linea)
    for cifra in ("2026-10-01", "1.234,56 kWh", "99,10 €"):
        assert cifra in enmascarada, cifra
    # The skeleton feeds a selector diagnosis, which never needs a figure, so there the long runs go too.
    assert "2026" not in redactar_texto("Del 2026-10-01 al 2026-10-31")


def test_la_instantanea_no_pierde_la_referencia_ni_la_comilla_de_cierre() -> None:
    enmascarada = redactar('  - cell "Hola, María Ejemplo · ana@example.org" [ref=e5]')
    assert "María" not in enmascarada and "ana@example.org" not in enmascarada
    assert enmascarada.endswith("[ref=e5]") and enmascarada.count('"') == 2


def test_cada_patron_compartido_se_usa_en_los_dos_lados() -> None:
    from asistente.capacidades.navegador import instantanea, seguridad

    for patron in IDENTIFICADORES:
        assert patron in instantanea._PERSONALES, patron.pattern
        assert patron in seguridad._PATRONES_PERSONALES, patron.pattern
