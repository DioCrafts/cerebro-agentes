"""Declarative scripts: what a script cannot express cannot happen (no browser needed)."""

from __future__ import annotations

from datetime import date
import json
from typing import Any

import pytest

from hacienda_gpt.navegador import guion as g
from hacienda_gpt.navegador.guion import Guion, GuionError, guion_activo, renderizar, validar_parametros


def _base(**cambios: Any) -> dict[str, Any]:
    datos = guion_activo("datadis_consumo").reglas.model_dump(mode="json")
    datos.update(cambios)
    return datos


def _con_paso(i: int, **cambios: Any) -> dict[str, Any]:
    datos = _base()
    datos["pasos"][i] = {**datos["pasos"][i], **cambios}
    return datos


def test_bundled_scripts_validate_and_are_versioned_like_format_specs() -> None:
    assert g.claves_incorporadas() == ["datadis_consumo", "sede_ejemplo"]
    for clave in g.claves_incorporadas():
        spec = g.guion_incorporado(clave)
        assert (spec.fuente, spec.version, spec.origen) == (f"nav_{clave}", 1, "incorporada")
        assert spec.reglas.verificado_contra_sitio_real is False and spec.reglas.nota_verificacion
    datadis = guion_activo("datadis_consumo").reglas
    assert list(datadis.origenes.values()) == ["https://datadis.es"]
    assert datadis.descarga is not None and datadis.descarga.destino == "luz_importar_consumo"
    sede = guion_activo("sede_ejemplo").reglas
    assert sede.demostracion and sede.pasos[-1].accion == "detener" and sede.pasos[-1].texto == "Firmar y enviar"


@pytest.mark.parametrize(
    "texto", ["Enviar", "Firmar y enviar", "Pagar", "Aceptar condiciones", "Presentar", "Confirmar"]
)
def test_a_click_on_a_state_changing_control_cannot_be_written(texto: str) -> None:
    with pytest.raises(ValueError, match="cambia algo"):
        Guion.model_validate(_con_paso(5, texto=texto))


def test_stop_step_must_be_last_and_point_at_a_submit_control() -> None:
    sede = guion_activo("sede_ejemplo").reglas.model_dump(mode="json")
    sede["pasos"].append(dict(sede["pasos"][0], id="otro"))
    with pytest.raises(ValueError, match="último paso"):
        Guion.model_validate(sede)
    sede = guion_activo("sede_ejemplo").reglas.model_dump(mode="json")
    sede["pasos"][-1]["texto"] = "Siguiente"
    with pytest.raises(ValueError, match="envía, firma o paga"):
        Guion.model_validate(sede)


@pytest.mark.parametrize(
    "selector",
    ["#password", "input[type=password]", "#clave-acceso", "text=Enviar", "xpath=//input", "#a >> #b", "#pin"],
)
def test_selectors_are_plain_css_and_never_credentials(selector: str) -> None:
    with pytest.raises(ValueError):
        Guion.model_validate(_con_paso(3, selector=selector))


@pytest.mark.parametrize("campo", ["password", "contrasena", "pin", "cvv", "numero_tarjeta", "iban"])
def test_credential_or_payment_fields_cannot_be_filled(campo: str) -> None:
    with pytest.raises(ValueError, match="credenciales o de pago"):
        Guion.model_validate(_con_paso(3, campo=campo))


def test_origins_are_https_and_steps_only_use_declared_ones() -> None:
    with pytest.raises(ValueError, match="https"):
        Guion.model_validate(_base(origenes={"datadis": "http://datadis.es"}))
    with pytest.raises(ValueError, match="origen no declarado"):
        Guion.model_validate(_con_paso(0, origen="otro"))
    with pytest.raises(ValueError, match="parámetro no declarado"):
        Guion.model_validate(_con_paso(3, valor="{contrasena_usuario}"))


def test_a_download_needs_declared_types_and_no_executables() -> None:
    with pytest.raises(ValueError, match="ejecutable"):
        Guion.model_validate(_base(descarga={"extensiones": [".exe"], "max_bytes": 10}))
    with pytest.raises(ValueError, match="no declara «descarga»"):
        Guion.model_validate(_base(descarga=None))


def test_parameters_are_validated_against_the_script() -> None:
    datadis = guion_activo("datadis_consumo").reglas
    valores = validar_parametros(
        datadis,
        {"cups": "es 0021 0000 0000 0001 AA", "desde": "2026-09-01", "hasta": "2026-09-03"},
        hoy=date(2026, 11, 16),
    )
    assert valores == {"cups": "ES0021000000000001AA", "desde": date(2026, 9, 1), "hasta": date(2026, 9, 3)}
    for crudos, error in (
        ({"desde": "2026-09-01", "hasta": "2026-09-03", "password": "x"}, "no usa"),
        ({"cups": "ES12", "desde": "2026-09-01", "hasta": "2026-09-03"}, "CUPS"),
        ({"desde": "2026-09-03", "hasta": "2026-09-01"}, "anterior"),
        ({"desde": "2024-01-01", "hasta": "2026-09-01"}, "400 días"),
        ({"desde": "2026-11-01", "hasta": "2026-12-01"}, "posterior a hoy"),
        ({"hasta": "2026-09-01"}, "Falta"),
    ):
        with pytest.raises(GuionError, match=error):
            validar_parametros(datadis, crudos, hoy=date(2026, 11, 16))
    sede = guion_activo("sede_ejemplo").reglas
    with pytest.raises(GuionError, match="formato"):
        validar_parametros(sede, {"nombre": "A", "nif": "1234", "correo": "a@b.es", "motivo": "queja", "expone": "x"})
    with pytest.raises(GuionError, match="una de"):
        validar_parametros(
            sede, {"nombre": "A", "nif": "12345678Z", "correo": "a@b.es", "motivo": "otra", "expone": "x"}
        )


def test_templates_are_plain_substitution() -> None:
    assert renderizar("{desde}", {"desde": date(2026, 9, 1)}, "%d/%m/%Y") == "01/09/2026"
    assert renderizar("{x.__class__}", {"x": "a"}) == "{x.__class__}"
    assert renderizar("{falta}", {}) == ""


def test_a_repaired_script_wins_only_with_a_higher_version_and_only_new_selectors(tmp_path) -> None:
    base = g.guion_incorporado("datadis_consumo")
    ruta = g.ruta_reparada("datadis_consumo", tmp_path)
    ruta.parent.mkdir(parents=True)
    reglas = base.reglas.model_dump(mode="json")
    reglas["pasos"][2]["selector"] = "#suministro"
    ruta.write_text(json.dumps({"fuente": base.fuente, "version": 2, "origen": "propuesta x", "reglas": reglas}))
    assert guion_activo("datadis_consumo", tmp_path).version == 2
    assert guion_activo("datadis_consumo", tmp_path).reglas.pasos[2].selector == "#suministro"  # type: ignore[union-attr]
    # A "repair" that also changes a label is ignored: the bundled script stays in force.
    reglas["pasos"][2]["etiqueta"] = "Otra cosa"
    ruta.write_text(json.dumps({"fuente": base.fuente, "version": 3, "origen": "propuesta y", "reglas": reglas}))
    assert guion_activo("datadis_consumo", tmp_path).version == 1
    ruta.write_text("{ roto")
    assert guion_activo("datadis_consumo", tmp_path).version == 1


def test_state_changing_texts() -> None:
    for texto in ("Firmar y enviar", "Pagar ahora", "ACEPTO las condiciones", "Darse de baja", "Presentar solicitud"):
        assert g.cambia_estado(texto), texto
    for texto in ("Siguiente", "Consultar", "Descargar CSV", "Página siguiente", "Ver detalle", "Volver"):
        assert not g.cambia_estado(texto), texto
