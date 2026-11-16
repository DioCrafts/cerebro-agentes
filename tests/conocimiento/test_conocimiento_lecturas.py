"""Reading dates of the modules' legal data: in the data files, like the tax files, and in the same flow.

A module's law is stale when the BOE has updated it after the reading its data file records; ``params-verify``
records a new reading when every group quoting it still matches and no group sits in an article changed since
(the change may be outside the quoted passage); ``params-lecturas`` accepts a file where only that changed. Nothing
depends on state kept between runs (the GitHub Actions runner keeps none).
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import shutil
from typing import Any

from hacienda_gpt.conocimiento import fichero_legal, lecturas_de_modulos
from hacienda_gpt.conocimiento.lecturas import anotar_en_texto, ficheros_de_modulos
from hacienda_gpt.conocimiento.registro import en_directorio
from hacienda_gpt.modulos import fuentes as fuentes_modulos
from hacienda_gpt.tax.params import DATA_DIR
from hacienda_gpt.tax.recodificacion.procedencia import cambios_de_lectura, refrescar_lo_que_se_pueda

LEY = "BOE-T-2015-1"
HOY = date(2026, 11, 25)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "modulos" / "correo" / "boe"
CITA_43 = (
    "Cuando la notificación por medios electrónicos sea de carácter obligatorio, o haya sido expresamente elegida "
    "por el interesado, se entenderá rechazada cuando hayan transcurrido diez días naturales desde la puesta a "
    "disposición de la notificación sin que se acceda a su contenido."
)


def _selector(fecha: str) -> str:
    return (
        '<form class="lista formBOE" method="GET"><fieldset><legend class="fuera">Seleccionar redacción:</legend>'
        '<p><input type="radio" name="p" value="1" checked="checked"/>'
        f"<label>Última actualización, publicada el {fecha}, en vigor a partir del {fecha}.</label></p>"
        "</fieldset></form>"
    )


def _articulo(numero: str, titulo: str, parrafos: list[str], fecha: str) -> str:
    cuerpo = "".join(f'<p class="parrafo">{p}</p>' for p in parrafos)
    return (
        f'<div class="bloque"><p class="bloque">[Bloque {numero}]</p>'
        f'<h5 class="articulo">Artículo {numero}. {titulo}</h5>{cuerpo}{_selector(fecha)}</div>'
    )


def consolidada(*, version43: str, version50: str) -> str:
    return (
        "<html><body><p>Texto consolidado. Última actualización publicada el 20/11/2026</p>"
        + _articulo(
            "43", "Práctica de las notificaciones.", ["1. Primer apartado del artículo.", "2. " + CITA_43], version43
        )
        + _articulo("50", "Otra cosa.", ["Un artículo que no cita ningún módulo."], version50)
        + "</body></html>"
    )


def _copia(tmp_path: Path) -> tuple[Any, Path]:
    """The mail module's rules, with the 10-day rule quoting a synthetic law read on 16-11-2026."""
    carpeta = tmp_path / "correo"
    carpeta.mkdir()
    original = fichero_legal("correo")
    assert original.ruta is not None
    shutil.copy(original.ruta, carpeta / original.ruta.name)
    copia = en_directorio(original, carpeta)
    path = carpeta / original.ruta.name
    texto = path.read_text(encoding="utf-8")
    raw = json.loads(texto)
    raw["fuentes_principales"][LEY] = {
        "norma": "Ley sintética 39/2015 (texto consolidado)",
        "boe_id": LEY,
        "url": f"https://www.boe.es/buscar/act.php?id={LEY}",
        "consolidado_a": "2024-11-06",
        "consultado_el": "2026-11-16",
        "sha256_html": "1" * 64,
    }
    regla = raw["reglas"]["plazo_rechazo_notificacion"]
    regla["evidencia"][0]["boe_id"] = LEY
    regla["evidencia"][0]["articulo"] = "art. 43.2"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return copia, path


def _texto_de(pagina: str) -> Any:
    def texto_de(boe_id: str, version: str | None = None) -> str | None:
        if boe_id == LEY:
            return pagina if version is None else None
        extracto = FIXTURES / f"{boe_id}.txt"
        return extracto.read_text(encoding="utf-8") if extracto.exists() and version is None else None

    return texto_de


def test_the_modules_record_their_readings_in_their_data_files() -> None:
    lecturas = lecturas_de_modulos()
    assert {x.modulo for x in lecturas} >= {"luz", "caducidades", "ayudas", "hipoteca", "suscripciones", "correo"}
    itv = next(x for x in lecturas if x.boe_id == "BOE-A-2017-12841")
    assert itv.fichero == "modulos/caducidades/data/reglas.json" and itv.consultado_el and not itv.fijada
    # A norm read from the daily BOE does not change: it is pinned.
    assert any(x.fijada for x in lecturas if x.modulo == "luz")
    # The package's own run covers every module file; a run over a copy of the tax files covers none.
    assert len(ficheros_de_modulos(DATA_DIR)) == 8
    assert ficheros_de_modulos(Path("/tmp/otra-carpeta")) == []


def test_params_check_compares_the_boe_with_the_date_in_the_data_file() -> None:
    vigiladas = [f for f in fuentes_modulos.fuentes_de_modulos() if f.boe_id == "BOE-A-2017-12841"]
    (itv,) = vigiladas
    assert itv.origen_lectura == "datos" and itv.consultado_el == date(2026, 11, 13)
    # No baseline needed: a later update is listed the first time it is seen, with no state file.
    pendientes, estado = fuentes_modulos.revisar({itv.boe_id: date(2026, 12, 1)}, {"fuentes": {}}, fuentes=vigiladas)
    assert [p["leida_el"] for p in pendientes] == ["2026-11-13"] and estado["fuentes"] == {}
    assert pendientes[0]["lectura_en"] == "datos del módulo (consultado_el)"
    assert fuentes_modulos.revisar({itv.boe_id: date(2026, 11, 13)}, {"fuentes": {}}, fuentes=vigiladas)[0] == []


def test_a_reading_is_recorded_in_the_module_file_when_everything_still_matches(tmp_path: Path) -> None:
    copia, path = _copia(tmp_path)
    antes = path.read_text(encoding="utf-8")
    vacia = tmp_path / "tax"
    vacia.mkdir()
    texto_de = _texto_de(consolidada(version43="06/11/2024", version50="20/11/2026"))
    informe = refrescar_lo_que_se_pueda(texto_de, hoy=HOY, data_dir=vacia, modulos=[copia])
    assert informe.bloqueadas == {}, informe.bloqueadas
    (hecho,) = informe.refrescadas
    assert hecho["boe_id"] == LEY and hecho["ficheros"] == [copia.nombre]
    despues = path.read_text(encoding="utf-8")
    fuente = json.loads(despues)["fuentes_principales"][LEY]
    assert (fuente["consolidado_a"], fuente["consultado_el"]) == ("2026-11-20", HOY.isoformat())
    # Only the three reading fields of that source changed, in place: the rest of the file is byte for byte the same.
    distintas = [(a, b) for a, b in zip(antes.splitlines(), despues.splitlines(), strict=True) if a != b]
    assert len(distintas) == 3 and all(
        '"consolidado_a"' in b or "consultado_el" in b or "sha256" in b for _, b in distintas
    )
    # What params-lecturas checks before the automatic pull request: only a reading, nothing else.
    cambios = cambios_de_lectura({copia.nombre: json.loads(antes)}, {copia.nombre: json.loads(despues)})
    assert cambios.otros_cambios == [] and [x.boe_id for x in cambios.lecturas] == [LEY]
    assert copia.comprobar() == []


def test_a_group_in_a_changed_article_blocks_the_reading_until_a_person_rereads_it(tmp_path: Path) -> None:
    copia, path = _copia(tmp_path)
    antes = path.read_text(encoding="utf-8")
    vacia = tmp_path / "tax"
    vacia.mkdir()
    # Article 43 has a wording published after the reading; its quoted passage is still there, word for word.
    texto_de = _texto_de(consolidada(version43="20/11/2026", version50="06/11/2024"))
    informe = refrescar_lo_que_se_pueda(texto_de, hoy=HOY, data_dir=vacia, modulos=[copia])
    assert informe.refrescadas == []
    (motivo,) = informe.bloqueadas[LEY]
    assert "datos legales de los módulos" in motivo and "plazo_rechazo_notificacion" in motivo and "43" in motivo
    assert path.read_text(encoding="utf-8") == antes
    # And a quote that is no longer in the law blocks it too.
    cambiada = _texto_de(
        consolidada(version43="06/11/2024", version50="20/11/2026").replace("diez días", "quince días")
    )
    informe = refrescar_lo_que_se_pueda(cambiada, hoy=HOY, data_dir=vacia, modulos=[copia])
    assert any("no coincide con el texto" in m for m in informe.bloqueadas[LEY])


def test_writing_a_reading_never_reformats_and_refuses_what_it_cannot_place() -> None:
    texto = '{\n  "a": {"boe_id": "X", "consolidado_a": "2020-01-01", "consultado_el": "2020-01-02",\n  "sha256_html": "ab"},\n  "b": [1, 2]\n}\n'
    nuevo = anotar_en_texto(texto, {("a",): {"consolidado_a": "2026-01-01", "consultado_el": "2026-01-02"}})
    assert nuevo == texto.replace("2020-01-01", "2026-01-01").replace("2020-01-02", "2026-01-02")
    repetido = texto.replace('"b": [1, 2]', '"b": {"sha256_html": "ab"}')
    assert anotar_en_texto(repetido, {("a",): {"consolidado_a": "2026-01-01"}}) is None
