"""The compatibility package ``hacienda_gpt`` (one release): every old module path is an alias of its new module,
``python -m hacienda_gpt.…`` still runs, and what was stored under the old layout keeps its identifiers."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import warnings

import pytest

RAIZ = Path(__file__).resolve().parents[1]
ANTIGUOS = (RAIZ / "tests" / "fixtures" / "compat" / "modulos_antiguos.txt").read_text(encoding="utf-8").split()


def _importar(nombre: str) -> ModuleType:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return importlib.import_module(nombre)


def test_every_old_module_path_has_a_new_module() -> None:
    from hacienda_gpt._alias import nuevo_nombre

    assert len(ANTIGUOS) > 400
    sin_destino = [v for v in ANTIGUOS if nuevo_nombre(v) is None]
    assert sin_destino == []
    faltan = [v for v in ANTIGUOS if importlib.util.find_spec(str(nuevo_nombre(v))) is None]
    assert faltan == []


@pytest.mark.parametrize(
    ("viejo", "nuevo"),
    [
        ("hacienda_gpt.settings", "asistente.plataforma.settings"),
        ("hacienda_gpt.tax.irpf", "asistente.ambitos.fiscal.calculo.irpf"),
        ("hacienda_gpt.modulos.fiscal.calculo.irpf", "asistente.ambitos.fiscal.calculo.irpf"),
        ("hacienda_gpt.tax.documentos", "asistente.ambitos.fiscal.documentos"),
        ("hacienda_gpt.tax.fuentes", "asistente.conocimiento.fuentes"),
        ("hacienda_gpt.tax.recodificacion.nucleo", "asistente.conocimiento.recodificacion.nucleo"),
        ("hacienda_gpt.conocimiento.recodificacion", "asistente.conocimiento.recodificacion.de_modulos"),
        ("hacienda_gpt.modulos.registro", "asistente.nucleo.modulos.registro"),
        ("hacienda_gpt.modulos.luz", "asistente.ambitos.luz"),
        ("hacienda_gpt.modulos.correo.almacen", "asistente.capacidades.correo.almacen"),
        ("hacienda_gpt.navegador.persistencia", "asistente.capacidades.navegador.persistencia"),
        ("hacienda_gpt.ops.formatos", "asistente.conocimiento.formatos"),
        ("hacienda_gpt.ops.consumo", "asistente.plataforma.ops.consumo"),
        ("hacienda_gpt.cli.proactive", "asistente.plataforma.cli.proactive"),
    ],
)
def test_an_old_path_is_the_same_module_object(viejo: str, nuevo: str) -> None:
    assert _importar(viejo) is _importar(nuevo)


def test_split_packages_never_pollute_the_new_ones() -> None:
    # Importing the old ``conocimiento.recodificacion`` (now ``…recodificacion.de_modulos``) leaves the new
    # ``asistente.conocimiento.recodificacion`` package alone.
    _importar("hacienda_gpt.conocimiento.recodificacion")
    assert _importar("asistente.conocimiento").recodificacion is _importar("asistente.conocimiento.recodificacion")
    # A name of the old package that is not a child module is read from where it went.
    viejo = _importar("hacienda_gpt.conocimiento")
    assert viejo.FicheroLegal is _importar("asistente.conocimiento").FicheroLegal
    from hacienda_gpt.modulos.correo import MODULO

    assert MODULO.clave == "correo"


def test_the_old_paths_warn_and_run_as_scripts() -> None:
    codigo = "import warnings; warnings.simplefilter('error'); import hacienda_gpt"
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, cwd=RAIZ)
    assert salida.returncode != 0 and "DeprecationWarning" in salida.stderr and "asistente" in salida.stderr
    # A cron line naming the old module still runs, and says what to write instead.
    salida = subprocess.run(
        [sys.executable, "-m", "hacienda_gpt.cli.proactive", "--help"], capture_output=True, text=True, cwd=RAIZ
    )
    assert salida.returncode == 0, salida.stderr
    assert "params-verify" in salida.stdout and "obsoleto" in salida.stderr


def test_nothing_in_asistente_imports_the_compatibility_package() -> None:
    culpables = [
        str(p.relative_to(RAIZ))
        for p in (RAIZ / "asistente").rglob("*.py")
        if "import hacienda_gpt" in (texto := p.read_text(encoding="utf-8")) or "from hacienda_gpt" in texto
    ]
    assert culpables == []


def test_stored_identifiers_keep_their_old_spelling() -> None:
    from asistente.conocimiento import ficheros_legales
    from asistente.plataforma.migraciones.catalogo import PROPIETARIOS

    # Legal data files are named in the reading log and the re-encoding state as before the move.
    nombres = {f.nombre for f in ficheros_legales() if f.ruta is not None}
    assert nombres == {
        "modulos/luz/data/regulados.json",
        "modulos/caducidades/data/reglas.json",
        "modulos/ayudas/data/prestaciones_2026.json",
        "modulos/hipoteca/data/normativa.json",
        "modulos/hipoteca/data/deduccion_vivienda.json",
        "modulos/hipoteca/data/indices_referencia.json",
        "modulos/suscripciones/data/derechos_consumidor.json",
        "capacidades/correo/data/notificaciones.json",
    }
    # Applied migrations are recorded with their checksum: moving the catalogue must not change any.
    sumas = {f"{p.nombre} {m.version}": m.suma() for p in PROPIETARIOS for m in p.migraciones}
    assert sumas == {
        "decision 1": "6ef68e144f865a61",
        "auth 1": "b89aadbe3bcaed80",
        "proactive 1": "83e6a7ee0b33deec",
        "proactive_entrega 1": "ff3755c0632c5172",
        "acciones 1": "7f9968279d92abd3",
        "memoria 1": "0cb0bced291c86fb",
        "memoria 2": "e53d5c4e77dee948",
        "evaluacion 1": "d34dbc5d87f177bc",
        "evaluacion 2": "e01ac62538f1755b",
        "evaluacion 3": "f7f16c6fe63fb3bc",
        "evaluacion 4": "fa7ec65f740bcc51",
        "eventos 1": "21d807f7ee398548",
        "correo 1": "8ac18767022127f3",
        "consumo 1": "55b5056650a59169",
        "navegador 1": "5195fcdca7eb721b",
        "navegador 2": "6988a9cff6c3e619",
        "plataforma 1": "b71c81f299a6cceb",
        "doctrina 1": "c17a42549f417d4e",
        "tareas 1": "da842990bd5c9477",
        "tareas 2": "3acb9fe0ce3e77a4",
        "push 1": "8b5ebcf6c7f52b68",
        "recuerdos 1": "181703d8a4ab901b",
        "repaso 1": "27d453200f190642",
        "repaso 2": "f9add8ad0766157a",
    }
    # Bus consumers and migration owners are identifiers, not paths.
    from asistente.nucleo.eventos.bus import consumidores_del_trabajador

    assert [c.nombre for c in consumidores_del_trabajador()] == ["modulos", "tareas"]
    assert {p.nombre for p in PROPIETARIOS} >= {"decision", "auth", "acciones", "correo", "navegador", "tareas"}


def test_the_registry_finds_the_mail_and_browser_fronts_in_the_capabilities() -> None:
    from asistente.nucleo.modulos.registro import descubrir

    encontrados, errores = descubrir()
    assert errores == {}
    claves = [m.clave for _o, m in encontrados]
    assert {"correo", "navegador", "fiscal", "luz"} <= set(claves)
    assert len(claves) == len(set(claves))


def test_an_alias_leaves_the_new_module_its_own_spec() -> None:
    nuevo = _importar("asistente.plataforma.settings")
    assert _importar("hacienda_gpt.settings") is nuevo
    assert nuevo.__spec__ is not None and nuevo.__spec__.name == "asistente.plataforma.settings"
    assert importlib.reload(nuevo) is nuevo
