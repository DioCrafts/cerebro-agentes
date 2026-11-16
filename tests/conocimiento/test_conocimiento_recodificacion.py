"""A module's legal data through the agentic re-encoding loop, end to end: the ``caducidades`` rules.

No network and no real model. The "law" is a small synthetic BOE page with article 22 of the insurance contract act
rewritten (the policyholder now has to oppose a renewal two months ahead, the insurer three); a temporary copy of
``reglas.json`` has its ``seguro`` group quoting that law. The providers replay a script. What decides is
deterministic: the gate (quotes verbatim, every figure in a quote, the file's own self-check) and the module's own
calculator run on the reviewer's cases. A module's proposal is never applied without a person.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
from pathlib import Path
import shutil
from typing import Any

import pytest

from asistente.ambitos.caducidades.reglas import DATA_PATH, Reglas, cargar_reglas
from asistente.conocimiento.casos_caducidades import evaluar
from asistente.conocimiento.recodificacion import (
    AlmacenPropuestas,
    ControlFallidoError,
    adaptador_de,
    aplicar_propuesta,
    confirmar,
    pendientes_de_confirmar,
    recodificar,
)
from asistente.conocimiento.recodificacion import de_modulos as rec
from asistente.conocimiento.recodificacion.adaptadores import objetivos_de_verificacion as objetivos_de_todo
from asistente.conocimiento.recodificacion.nucleo import SIN_EJERCICIO, ejercicios_en

LEY = "BOE-T-2020-9"
HOY = date(2026, 11, 20)
EFECTOS = "Se modifica, con efectos desde el 1 de enero de 2026, por el art. 1 de la Ley 1/2026, de 20 de junio."
P1 = (
    "1. La duración del contrato será determinada en la póliza, la cual no podrá fijar un plazo superior a diez años. "
    "Sin embargo, podrá establecerse que se prorrogue una o más veces por un período no superior a un año cada vez."
)
P2_NUEVO = (
    "2. Las partes pueden oponerse a la prórroga del contrato mediante una notificación escrita a la otra parte, "
    "efectuada con un plazo de, al menos, dos meses de anticipación a la conclusión del período del seguro en curso "
    "cuando quien se oponga a la prórroga sea el tomador, y de tres meses cuando sea el asegurador."
)
P3 = (
    "3. El asegurador deberá comunicar al tomador, al menos con dos meses de antelación a la conclusión del período "
    "en curso, cualquier modificación del contrato de seguro."
)
P5 = (
    "5. Lo dispuesto en los apartados precedentes no será de aplicación en cuanto sea incompatible con la regulación "
    "del seguro sobre la vida."
)


def pagina(parrafos: list[str]) -> str:
    cuerpo = "".join(f'<p class="parrafo">{p}</p>\n' for p in parrafos)
    return f"""<html><body>
<div class="bloque" id="a22"><p class="bloque">[Bloque 22: #a22]</p>
<h5 class="articulo">Artículo 22. Duración del contrato.</h5>
{cuerpo}<p class="nota_pie">{EFECTOS}</p>
<form class="lista formBOE" method="GET" action="/buscar/act.php#a22"><fieldset>
<legend class="fuera">Seleccionar redacción:</legend>
<p><input type="radio" name="p" value="20260630" checked="checked"/>
<label>Última actualización, publicada el 30/06/2026, en vigor a partir del 01/07/2026.</label></p>
</fieldset></form></div>
<div class="bloque" id="df"><p class="bloque">[Bloque 99: #df]</p>
<h5 class="articulo">Disposición final única. Entrada en vigor.</h5><p>Esta ley entra en vigor al día siguiente.</p></div>
</body></html>"""


def texto_de(contenido: str) -> Callable[..., str | None]:
    return lambda boe_id, version=None: contenido if boe_id == LEY and version is None else None


class Guion:
    name = "guion"
    model = "modelo-de-prueba"

    def __init__(self, *respuestas: dict[str, Any]) -> None:
        self.respuestas = list(respuestas)
        self.llamadas: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, **_: Any) -> Any:
        self.llamadas.append({"system": system, "user": user, "schema_name": schema_name})
        if not self.respuestas:
            raise AssertionError(f"llamada no prevista en el guion ({schema_name})")
        return dict(self.respuestas.pop(0))


def lectura(valor: dict[str, Any], citas: list[str]) -> dict[str, Any]:
    return {
        "resultado": "codificada",
        "valor_json": json.dumps(valor, ensure_ascii=False),
        "evidencia": citas,
        "efectos_desde": "2026-01-01",
        "efectos_hasta": "",
        "cita_efectos": EFECTOS,
        "articulos_necesarios": [],
        "motivo": "",
        "cambios": "El tomador debe oponerse con dos meses de antelación y el asegurador con tres.",
    }


def caso(descripcion: str, entrada: dict[str, Any], linea: str, esperado: str) -> dict[str, Any]:
    return {
        "descripcion": descripcion,
        "datos_json": json.dumps({"calculo": "seguro", "entrada": entrada, "resultado": linea}),
        "importe_esperado": esperado,
        "fundamento": P2_NUEVO,
    }


REVISION = {
    "conforme": True,
    "discrepancias": [],
    "efectos_desde": "2026-01-01",
    "cita_efectos": EFECTOS,
    "casos": [
        caso(
            "Póliza que vence el 31-3-2027: el tomador debe oponerse como tarde el 31-1-2027",
            {"hoy": "2026-11-20", "fecha_vencimiento": "2027-03-31"},
            "accion_hasta",
            "20270131",
        ),
        caso(
            "Póliza que vence el 15-6-2027",
            {"hoy": "2026-11-20", "fecha_vencimiento": "2027-06-15"},
            "accion_hasta",
            "20270415",
        ),
    ],
}


@pytest.fixture()
def datos(tmp_path: Path) -> Path:
    """A copy of ``reglas.json`` whose ``seguro`` group quotes the synthetic law (with its old wording)."""
    carpeta = tmp_path / "caducidades"
    carpeta.mkdir()
    shutil.copy(DATA_PATH, carpeta / DATA_PATH.name)
    raw = json.loads((carpeta / DATA_PATH.name).read_text(encoding="utf-8"))
    raw["fuentes_principales"][LEY] = {
        "norma": "Ley sintética 1/2020 (texto consolidado)",
        "boe_id": LEY,
        "url": f"https://www.boe.es/buscar/act.php?id={LEY}",
        "consolidado_a": "2026-06-30",
        "consultado_el": "2026-11-12",
        "sha256_html": "0" * 64,
    }
    for evidencia in raw["seguro"]["evidencia"]:
        evidencia["boe_id"] = LEY
        evidencia["articulo"] = evidencia["articulo"].replace("Ley 50/1980", "Ley 1/2020")
    (carpeta / DATA_PATH.name).write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return carpeta


def _familia() -> Any:
    (familia,) = rec.FAMILIAS
    return familia


def test_the_rules_are_registered_and_every_shipped_group_passes_the_gate() -> None:
    tipos = {a.tipo for a in rec.ADAPTADORES.values()}
    assert {"caducidades:ReglaITV", "caducidades:ReglaSeguro", "caducidades:ReglaDocumentoPorEdad"} <= tipos
    for adaptador in rec.ADAPTADORES.values():
        assert adaptador_de(adaptador.tipo) is adaptador
        assert adaptador.directorio == DATA_PATH.parent
    # A file without a year is one pseudo-year: what is in force.
    assert ejercicios_en(DATA_PATH.parent, DATA_PATH.name) == [SIN_EJERCICIO]
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "modulos" / "caducidades"
    textos = {p.stem: p.read_text(encoding="utf-8") for p in fixtures.glob("*.txt")}
    revisados = rec.revisar_grupos(_familia(), textos)
    assert len(revisados) == 9 and not [(r.ruta, r.fallos) for r in revisados if r.fallos]
    # Never automatic: every module group asks for a person.
    assert all(r.estado == "persona" and rec.REVISION_HUMANA in r.revisar for r in revisados)
    # The other files say why a person keeps them.
    assert set(rec.solo_persona()) >= {"modulos/luz/data/regulados.json", "modulos/hipoteca/data/normativa.json"}


def test_the_case_evaluator_is_the_modules_own_calculator() -> None:
    reglas = cargar_reglas()
    r = evaluar("seguro", {"hoy": "2026-11-20", "fecha_vencimiento": "2027-03-31"}, reglas)
    assert (r.fecha, r.accion_hasta) == (20270331, 20270228)  # one month before, as the law reads today
    dni = evaluar("dni", {"fecha_expedicion": "2026-01-15", "fecha_nacimiento": "1990-05-01"}, reglas)
    assert dni.fecha == 20360115 and not dni.permanente


def test_a_changed_rule_is_found_re_encoded_checked_by_cases_and_applied_only_by_a_person(
    datos: Path, tmp_path: Path
) -> None:
    ley = pagina([P1, P2_NUEVO, P3, P5])
    # The package-wide search leaves a copy of the files alone; asked for this family and folder, it finds it.
    assert not [
        o for o in objetivos_de_todo(None, texto_de(ley), data_dir=tmp_path).objetivos if "caducidades" in o.tipo
    ]
    hallazgo = rec.objetivos_de_verificacion(None, texto_de(ley), data_dir=datos, familias=[_familia()])
    (objetivo,) = [o for o in hallazgo.objetivos if o.ruta == ["seguro"]]
    assert objetivo.tipo == "caducidades:ReglaSeguro" and objetivo.ejercicios == [SIN_EJERCICIO]
    assert {a.split(".")[0] for a in objetivo.articulos_ley} == {"22"}

    anterior = objetivo.antes[SIN_EJERCICIO]
    valor = {k: v for k, v in anterior.items() if k not in ("fuente", "evidencia", "avisos")}
    valor.update(oposicion_tomador_meses=2, oposicion_asegurador_meses=3)
    lector = Guion(lectura(valor, [P1, P2_NUEVO, P3, P5]))
    revisor = Guion(REVISION)
    ficheros = {SIN_EJERCICIO: json.loads((datos / DATA_PATH.name).read_text(encoding="utf-8"))}
    propuesta = recodificar(objetivo, lector=lector, revisor=revisor, texto_de=texto_de(ley), datos=ficheros)
    assert propuesta.estado == "propuesta", (propuesta.control, propuesta.iteraciones[-1])
    assert propuesta.control.fallos == [] and rec.REVISION_HUMANA in propuesta.control.revisar
    assert not propuesta.aplicable_sin_persona
    # The reviewer's cases went through the module's calculator with the PROPOSED rule.
    casos = propuesta.iteraciones[-1].casos
    assert [c.obtenido for c in casos] == [c.esperado for c in casos]

    almacen = AlmacenPropuestas(tmp_path / "propuestas")
    almacen.guardar(propuesta)
    with pytest.raises(ControlFallidoError, match="revisar a mano"):
        aplicar_propuesta(propuesta, texto_de(ley), almacen=almacen, data_dir=datos, hoy=HOY, automatica=True)
    aplicada = aplicar_propuesta(propuesta, texto_de(ley), almacen=almacen, data_dir=datos, hoy=HOY)
    assert aplicada.estado == "aplicada"
    escrito = Reglas.model_validate(json.loads((datos / DATA_PATH.name).read_text(encoding="utf-8")))
    assert (escrito.seguro.oposicion_tomador_meses, escrito.seguro.oposicion_asegurador_meses) == (2, 3)
    assert not escrito.seguro.fuente.verificado and "pendiente de revisión humana" in escrito.seguro.fuente.referencia
    # The real file was not touched.
    assert cargar_reglas().seguro.oposicion_tomador_meses == 1
    # Pending until a person confirms it.
    (pendiente,) = pendientes_de_confirmar(datos)
    assert pendiente.ruta == ["seguro"] and pendiente.ejercicio == SIN_EJERCICIO
    assert confirmar([pendiente], data_dir=datos, almacen=almacen) == 1
    confirmado = Reglas.model_validate(json.loads((datos / DATA_PATH.name).read_text(encoding="utf-8")))
    assert confirmado.seguro.fuente.verificado and pendientes_de_confirmar(datos) == []


def test_a_reviewer_case_that_the_calculator_contradicts_stops_the_proposal(datos: Path) -> None:
    ley = pagina([P1, P2_NUEVO, P3, P5])
    hallazgo = rec.objetivos_de_verificacion(None, texto_de(ley), data_dir=datos, familias=[_familia()])
    (objetivo,) = [o for o in hallazgo.objetivos if o.ruta == ["seguro"]]
    anterior = objetivo.antes[SIN_EJERCICIO]
    valor = {k: v for k, v in anterior.items() if k not in ("fuente", "evidencia", "avisos")}
    # The reader keeps the old month; the cases (two months, as the law says) show it.
    lector = Guion(*[lectura(valor, [P1, P2_NUEVO, P3, P5])] * 3)
    revisor = Guion(*[REVISION] * 3)
    ficheros = {SIN_EJERCICIO: json.loads((datos / DATA_PATH.name).read_text(encoding="utf-8"))}
    propuesta = recodificar(
        objetivo, lector=lector, revisor=revisor, texto_de=texto_de(ley), datos=ficheros, max_iter=2
    )
    assert propuesta.estado == "sin_converger"
