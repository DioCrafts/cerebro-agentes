"""Must this taxpayer file an IRPF return? (art. 96 LIRPF)

A pure function from the calculator's input to a three-way answer — «sí», «no» or «no se puede saber» — with
the rule of the law that decides it (literal quote and reference), the data still missing when it cannot be
decided, and a recommendation: a taxpayer who is not obliged may still want to file to get a refund, which is
worked out with :func:`hacienda_gpt.tax.irpf.calcular_irpf`.

The figures (22.000 / 15.876 / 1.500 / 1.600 / 1.000 / 500 euros) are data, read for each tax year from
``data/irpf_<year>.json`` (group ``obligacion_declarar``, quotes in ``evidencia_parametros.json``). The rules
applied, in this order:

1. Cases that oblige to file whatever the income: registered as self-employed at any time of the year (art. 96.2
   last paragraph); contributions to pension schemes or protected estates that reduce the taxable base, or the
   international double-taxation deduction, when the right is exercised (art. 96.4 LIRPF and 61.1 RIRPF); the
   transitional deduction for the main home (DT 18.ª.3 LIRPF); beneficiaries of the ingreso mínimo vital (art. 36
   Ley 19/2021, an obligation of that law rather than of the IRPF one).
2. Never obliged with only work, capital, business income and gains up to 1.000 euros together and capital losses
   below 500 euros (art. 96.2, penultimate paragraph).
3. Not obliged with income only from the sources of art. 96.2 a) and b) within their limits — gross work income
   up to 22.000 euros, or 15.876 euros in the cases of art. 96.3 (several payers when the second and further ones
   paid more than 1.500 euros, compensatory pensions, a payer not obliged to withhold, a fixed withholding rate),
   and capital income plus gains subject to withholding up to 1.600 euros.

Not modelled, and said so in ``avisos``: the sources of art. 96.2.c) (imputed real-estate income, Treasury bills,
subsidies for protected housing), which the calculator has no input for; the gross figures of business and rental
income (the calculator only holds net figures, so the 1.000-euro rule cannot always be decided); joint returns
(the obligation is checked person by person).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from hacienda_gpt.tax.params import IRPFParams, ObligacionDeclarar, load_params

if TYPE_CHECKING:
    from hacienda_gpt.tax.irpf import IRPFInput, IRPFResult

ZERO = Decimal(0)
LIRPF = "BOE-A-2006-20764"
RIRPF = "BOE-A-2007-6820"
LEY_IMV = "BOE-A-2021-21007"

Obligado = Literal["si", "no", "no_se_sabe"]
TipoDato = Literal["si_no", "importe", "numero"]

# Lines of ``calcular_irpf`` whose amount means a right that obliges to file when it is exercised.
LINEA_REDUCCION_PREVISION = "Reducción por aportaciones a sistemas de previsión social"
LINEA_VIVIENDA_TRANSITORIA = "Deducción por inversión en vivienda habitual (régimen transitorio)"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cita(_Model):
    """A passage of a law, copied literally from its consolidated text on boe.es."""

    referencia: str
    boe_id: str
    texto: str


# --------------------------------------------------------------------------- #
# The passages applied (checked against tests/fixtures/boe_obligacion)
# --------------------------------------------------------------------------- #

_INTRO_96_2 = (
    "2. No obstante, no tendrán que declarar los contribuyentes que obtengan rentas procedentes exclusivamente de "
    "las siguientes fuentes, en tributación individual o conjunta:"
)
CITAS: dict[str, Cita] = {
    "96.1": Cita(
        referencia="art. 96.1 LIRPF",
        boe_id=LIRPF,
        texto=(
            "1. Los contribuyentes estarán obligados a presentar y suscribir declaración por este Impuesto, con los "
            "límites y condiciones que reglamentariamente se establezcan."
        ),
    ),
    "96.2.a": Cita(
        referencia="art. 96.2.a) LIRPF",
        boe_id=LIRPF,
        texto=f"{_INTRO_96_2} […] a) Rendimientos íntegros del trabajo, con el límite de 22.000 euros anuales.",
    ),
    "96.2.b": Cita(
        referencia="art. 96.2.b) LIRPF",
        boe_id=LIRPF,
        texto=(
            f"{_INTRO_96_2} […] b) Rendimientos íntegros del capital mobiliario y ganancias patrimoniales sometidos "
            "a retención o ingreso a cuenta, con el límite conjunto de 1.600 euros anuales."
        ),
    ),
    "96.2.b.iic": Cita(
        referencia="art. 96.2.b) LIRPF",
        boe_id=LIRPF,
        texto=(
            "Lo dispuesto en esta letra no será de aplicación respecto de las ganancias patrimoniales procedentes de "
            "transmisiones o reembolsos de acciones o participaciones de instituciones de inversión colectiva en las "
            "que la base de retención, conforme a lo que se establezca reglamentariamente, no proceda determinarla "
            "por la cuantía a integrar en la base imponible."
        ),
    ),
    "96.2.c": Cita(
        referencia="art. 96.2.c) LIRPF",
        boe_id=LIRPF,
        texto=(
            "c) Rentas inmobiliarias imputadas en virtud del artículo 85 de esta Ley, rendimientos íntegros del "
            "capital mobiliario no sujetos a retención derivados de letras del Tesoro y subvenciones para la "
            "adquisición de viviendas de protección oficial o de precio tasado y demás ganancias patrimoniales "
            "derivadas de ayudas públicas, con el límite conjunto de 1.000 euros anuales."
        ),
    ),
    "96.2.minimas": Cita(
        referencia="art. 96.2 LIRPF (penúltimo párrafo)",
        boe_id=LIRPF,
        texto=(
            "En ningún caso tendrán que declarar los contribuyentes que obtengan exclusivamente rendimientos "
            "íntegros del trabajo, de capital o de actividades económicas, así como ganancias patrimoniales, con el "
            "límite conjunto de 1.000 euros anuales y pérdidas patrimoniales de cuantía inferior a 500 euros."
        ),
    ),
    "96.2.reta": Cita(
        referencia="art. 96.2 LIRPF (último párrafo)",
        boe_id=LIRPF,
        texto=(
            "No obstante lo anterior, estarán en cualquier caso obligadas a declarar todas aquellas personas físicas "
            "que en cualquier momento del período impositivo hubieran estado de alta, como trabajadores por cuenta "
            "propia, en el Régimen Especial de Trabajadores por Cuenta Propia o Autónomos, o en el Régimen Especial "
            "de la Seguridad Social de los Trabajadores del Mar."
        ),
    ),
    "96.3.a.1": Cita(
        referencia="art. 96.3.a) 1.º LIRPF",
        boe_id=LIRPF,
        texto=(
            "3. El límite a que se refiere la letra a) del apartado 2 anterior será de 15.876 euros para los "
            "contribuyentes que perciban rendimientos íntegros del trabajo en los siguientes supuestos: a) Cuando "
            "procedan de más de un pagador. No obstante, el límite será de 22.000 euros anuales en los siguientes "
            "supuestos: 1.º Si la suma de las cantidades percibidas del segundo y restantes pagadores, por orden de "
            "cuantía, no supera en su conjunto la cantidad de 1.500 euros anuales."
        ),
    ),
    "96.3.a.2": Cita(
        referencia="art. 96.3.a) 2.º LIRPF",
        boe_id=LIRPF,
        texto=(
            "2.º Cuando se trate de contribuyentes cuyos únicos rendimientos del trabajo consistan en las "
            "prestaciones pasivas a que se refiere el artículo 17.2.a) de esta Ley y la determinación del tipo de "
            "retención aplicable se hubiera realizado de acuerdo con el procedimiento especial que "
            "reglamentariamente se establezca."
        ),
    ),
    "96.3.b": Cita(
        referencia="art. 96.3.b) LIRPF",
        boe_id=LIRPF,
        texto=(
            "3. El límite a que se refiere la letra a) del apartado 2 anterior será de 15.876 euros para los "
            "contribuyentes que perciban rendimientos íntegros del trabajo en los siguientes supuestos: […] b) "
            "Cuando se perciban pensiones compensatorias del cónyuge o anualidades por alimentos diferentes de las "
            "previstas en el artículo 7 de esta ley."
        ),
    ),
    "96.3.c": Cita(
        referencia="art. 96.3.c) LIRPF",
        boe_id=LIRPF,
        texto=(
            "3. El límite a que se refiere la letra a) del apartado 2 anterior será de 15.876 euros para los "
            "contribuyentes que perciban rendimientos íntegros del trabajo en los siguientes supuestos: […] c) "
            "Cuando el pagador de los rendimientos del trabajo no esté obligado a retener de acuerdo con lo previsto "
            "reglamentariamente."
        ),
    ),
    "96.3.d": Cita(
        referencia="art. 96.3.d) LIRPF",
        boe_id=LIRPF,
        texto=(
            "3. El límite a que se refiere la letra a) del apartado 2 anterior será de 15.876 euros para los "
            "contribuyentes que perciban rendimientos íntegros del trabajo en los siguientes supuestos: […] d) "
            "Cuando se perciban rendimientos íntegros del trabajo sujetos a tipo fijo de retención."
        ),
    ),
    "96.4": Cita(
        referencia="art. 96.4 LIRPF y art. 61.1 RIRPF",
        boe_id=LIRPF,
        texto=(
            "4. Estarán obligados a declarar en todo caso los contribuyentes que tengan derecho a deducción por "
            "doble imposición internacional o que realicen aportaciones a patrimonios protegidos de las personas con "
            "discapacidad, planes de pensiones, planes de previsión asegurados o mutualidades de previsión social, "
            "planes de previsión social empresarial y seguros de dependencia que reduzcan la base imponible, en las "
            "condiciones que se establezcan reglamentariamente."
        ),
    ),
    "61.1": Cita(
        referencia="art. 61.1 RIRPF",
        boe_id=RIRPF,
        texto=(
            "A efectos de lo dispuesto en el apartado 4 de dicho artículo, estarán obligados a declarar en todo caso "
            "los contribuyentes que tengan derecho a deducción por doble imposición internacional o que realicen "
            "aportaciones a patrimonios protegidos de las personas con discapacidad, planes de pensiones, planes de "
            "previsión asegurados, planes de previsión social empresarial, seguros de dependencia o mutualidades de "
            "previsión social que reduzcan la base imponible, cuando ejerciten tal derecho."
        ),
    ),
    "dt18.3": Cita(
        referencia="disposición transitoria 18.ª.3 LIRPF",
        boe_id=LIRPF,
        texto=(
            "3. Los contribuyentes que por aplicación de lo establecido en esta disposición ejerciten el derecho a "
            "la deducción estarán obligados, en todo caso, a presentar declaración por este Impuesto"
        ),
    ),
    "imv.36": Cita(
        referencia="art. 36.1.f) y 36.2.c) de la Ley 19/2021, del ingreso mínimo vital",
        boe_id=LEY_IMV,
        texto=(
            "1. Las personas titulares del ingreso mínimo vital estarán sujetas durante el tiempo de percepción de la "
            "prestación a las siguientes obligaciones: […] f) Presentar anualmente declaración correspondiente al "
            "Impuesto sobre la Renta de las Personas Físicas. […] 2. Las personas integrantes de la unidad de "
            "convivencia estarán obligadas a: […] c) Presentar anualmente declaración correspondiente al Impuesto "
            "sobre la Renta de las Personas Físicas."
        ),
    ),
    "60.5": Cita(
        referencia="art. 60.5.3.º RIRPF",
        boe_id=RIRPF,
        texto=(
            "En el supuesto de contribuyentes no obligados a declarar deberán comunicar, a estos efectos, a la "
            "Administración tributaria la información que determine la persona titular del Ministerio de Hacienda y "
            "Función Pública"
        ),
    ),
    "60bis.3": Cita(
        referencia="art. 60 bis.3.3.º RIRPF",
        boe_id=RIRPF,
        texto=(
            "En el supuesto de contribuyentes no obligados a declarar, tal regularización se efectuará mediante el "
            "ingreso de las cantidades percibidas en exceso"
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


class Regla(_Model):
    """A rule of the law and how it applies to these figures."""

    referencia: str
    boe_id: str
    cita: str
    aplicacion: str


class DatoQueFalta(_Model):
    """A fact the answer depends on and nobody gave."""

    # The calculator input (``IRPFInput`` field / profile fact) that answers it; None: no input holds it.
    campo: str | None
    pregunta: str
    tipo: TipoDato


class LineaObligacion(_Model):
    concepto: str
    importe: Decimal
    referencia: str | None = None


class ResultadoObligacion(_Model):
    ejercicio: int
    obligado: Obligado
    # One sentence for the user.
    respuesta: str
    # The rule that decides (for «no_se_sabe», the one the missing data would decide).
    regla: Regla | None = None
    # Other rules that also apply (e.g. several reasons to file).
    otras_reglas: list[Regla] = Field(default_factory=list)
    datos_que_faltan: list[DatoQueFalta] = Field(default_factory=list)
    recomendacion: str | None = None
    # Result of the return if it is filed (``calcular_irpf``: positive to pay, negative refund) and the refund.
    resultado_si_declara: Decimal | None = None
    devolucion_estimada: Decimal | None = None
    lineas: list[LineaObligacion] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)
    parametros_no_verificados: list[str] = Field(default_factory=list)
    fuente: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _eur(x: Decimal) -> str:
    """22000 → '22.000 €'; 21999.99 → '21.999,99 €'."""
    entero = x == x.to_integral_value()
    texto = f"{abs(x):,.0f}" if entero else f"{abs(x):,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if x < 0 else ''}{texto} €"


def _regla(clave: str, aplicacion: str) -> Regla:
    cita = CITAS[clave]
    return Regla(referencia=cita.referencia, boe_id=cita.boe_id, cita=cita.texto, aplicacion=aplicacion)


def _linea(resultado: IRPFResult, concepto: str) -> Decimal:
    return sum((abs(linea.importe) for linea in resultado.lineas if linea.concepto == concepto), ZERO)


_PREGUNTAS: dict[str, tuple[str, TipoDato]] = {
    "num_pagadores_trabajo": (
        "¿Cuántos pagadores te abonaron rendimientos del trabajo en el año (empresas, la prestación por desempleo, "
        "la pensión…)?",
        "numero",
    ),
    "importe_segundo_y_restantes_pagadores": (
        "¿Cuánto cobraste en total, en bruto, de todos tus pagadores menos el que más te pagó?",
        "importe",
    ),
    "pensiones_procedimiento_especial": (
        "¿Son pensiones todos tus rendimientos del trabajo y la retención se te fijó por el procedimiento especial "
        "para perceptores de varias pensiones (art. 89.A del Reglamento del IRPF)?",
        "si_no",
    ),
    "pension_compensatoria_o_anualidades": (
        "¿Cobras una pensión compensatoria de tu cónyuge o expareja, o anualidades por alimentos distintas de las "
        "exentas del art. 7 de la Ley del IRPF?",
        "si_no",
    ),
    "pagador_no_obligado_a_retener": (
        "¿Te pagó rendimientos del trabajo alguien que no estaba obligado a retener, por ejemplo un particular que "
        "no te paga en el ejercicio de una actividad económica o una entidad no residente sin establecimiento "
        "permanente en España (art. 76 del Reglamento del IRPF)?",
        "si_no",
    ),
    "trabajo_tipo_fijo_retencion": (
        "¿Cobraste rendimientos del trabajo con un tipo fijo de retención, por ejemplo como administrador o "
        "consejero, o por impartir cursos o conferencias (art. 80.1 del Reglamento del IRPF)?",
        "si_no",
    ),
    "ganancias_sometidas_a_retencion": (
        "¿Tus ganancias patrimoniales estaban sometidas a retención, como el reembolso de participaciones en fondos "
        "de inversión?",
        "si_no",
    ),
    "alta_autonomo_en_el_ejercicio": (
        "¿Estuviste de alta como autónomo (RETA) o en el Régimen Especial de Trabajadores del Mar en algún momento "
        "del año?",
        "si_no",
    ),
}


class _Estado:
    """What one rule says: ``True`` it holds (not obliged by it), ``False`` it fails, ``None`` cannot tell."""

    def __init__(self) -> None:
        self.cumple: bool | None = True
        self.motivo: Regla | None = None


class _Contexto:
    def __init__(self, inp: IRPFInput, o: ObligacionDeclarar) -> None:
        self.inp = inp
        self.o = o
        self.lineas: list[LineaObligacion] = []
        self.avisos: list[str] = []
        self.faltan: dict[str, DatoQueFalta] = {}

    def falta(self, campo: str) -> None:
        pregunta, tipo = _PREGUNTAS[campo]
        self.faltan.setdefault(campo, DatoQueFalta(campo=campo, pregunta=pregunta, tipo=tipo))

    def falta_sin_campo(self, clave: str, pregunta: str, tipo: TipoDato = "importe") -> None:
        self.faltan.setdefault(clave, DatoQueFalta(campo=None, pregunta=pregunta, tipo=tipo))


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #


def _obligan_en_todo_caso(inp: IRPFInput, resultado: IRPFResult, c: _Contexto) -> list[Regla]:
    """Cases that oblige to file whatever the income."""
    reglas: list[Regla] = []
    if inp.alta_autonomo_en_el_ejercicio:
        reglas.append(
            _regla(
                "96.2.reta",
                "Estuviste de alta como trabajador por cuenta propia en algún momento del año: tienes que declarar "
                "sean cuales sean tus rentas.",
            )
        )
    reduccion = _linea(resultado, LINEA_REDUCCION_PREVISION)
    if reduccion > 0:
        regla = _regla(
            "96.4",
            f"Tus aportaciones a sistemas de previsión social reducen tu base imponible en {_eur(reduccion)}: si "
            "aplicas esa reducción (este cálculo la aplica) tienes que declarar. El Reglamento lo precisa: «cuando "
            "ejerciten tal derecho» (art. 61.1 RIRPF).",
        )
        reglas.append(regla)
    elif inp.aportacion_plan_pensiones or inp.contribucion_empresa_plan_pensiones:
        c.avisos.append(
            "Tus aportaciones a planes de pensiones no reducen la base imponible de este ejercicio según el cálculo, "
            "así que no te obligan a declarar por sí solas (art. 96.4 LIRPF: las que «reduzcan la base imponible»)."
        )
    if inp.aportaciones_patrimonio_protegido:
        reglas.append(
            _regla(
                "96.4",
                "Hiciste aportaciones a un patrimonio protegido de una persona con discapacidad: si aplicas la "
                "reducción que dan, tienes que declarar («cuando ejerciten tal derecho», art. 61.1 RIRPF).",
            )
        )
    if inp.deduccion_doble_imposicion_internacional:
        reglas.append(
            _regla(
                "96.4",
                "Tienes derecho a la deducción por doble imposición internacional: si la aplicas, tienes que "
                "declarar («cuando ejerciten tal derecho», art. 61.1 RIRPF).",
            )
        )
    vivienda = _linea(resultado, LINEA_VIVIENDA_TRANSITORIA)
    if vivienda > 0:
        reglas.append(
            _regla(
                "dt18.3",
                f"Aplicas la deducción por inversión en vivienda habitual del régimen transitorio ({_eur(vivienda)} "
                "en este cálculo): quien la ejerce tiene que declarar en todo caso.",
            )
        )
    if inp.beneficiario_ingreso_minimo_vital:
        reglas.append(
            _regla(
                "imv.36",
                "Cobras el ingreso mínimo vital o formas parte de una unidad de convivencia que lo cobra: la ley del "
                "ingreso mínimo vital obliga a presentar cada año la declaración del IRPF, sean cuales sean tus "
                "rentas.",
            )
        )
    return reglas


def _limite_trabajo(t: Decimal, c: _Contexto) -> _Estado:
    """Art. 96.2.a) with the lower limit of art. 96.3 when one of its cases holds."""
    inp, o = c.inp, c.o
    estado = _Estado()
    alto, bajo = o.limite_trabajo_un_pagador, o.limite_trabajo_varios_pagadores
    if t <= bajo:
        estado.motivo = _regla(
            "96.2.a",
            f"Tus rendimientos íntegros del trabajo ({_eur(t)}) no superan {_eur(bajo)}, así que entran en el límite "
            "aunque tengas varios pagadores o se dé otro supuesto del art. 96.3.",
        )
        return estado
    if t > alto:
        c.lineas.append(
            LineaObligacion(
                concepto="Límite de rendimientos del trabajo aplicable",
                importe=alto,
                referencia=CITAS["96.2.a"].referencia,
            )
        )
        estado.cumple = False
        estado.motivo = _regla(
            "96.2.a", f"Tus rendimientos íntegros del trabajo ({_eur(t)}) superan el límite de {_eur(alto)}."
        )
        return estado

    # Between the two limits: which one applies decides.
    causas: list[Regla] = []
    desconocido = False
    importe, pagadores = inp.importe_segundo_y_restantes_pagadores, inp.num_pagadores_trabajo
    varios: bool | None
    if importe is not None:
        varios = importe > o.limite_segundo_pagador
        if pagadores is not None and pagadores <= 1 and importe > 0:
            c.avisos.append(
                f"Indicas {pagadores} pagador(es) y a la vez {_eur(importe)} cobrados del segundo y restantes "
                "pagadores: se ha usado este importe."
            )
    elif pagadores is not None and pagadores <= 1:
        varios = False
    else:
        varios = None
        if pagadores is None:
            c.falta("num_pagadores_trabajo")
        c.falta("importe_segundo_y_restantes_pagadores")
        desconocido = True
    if varios:
        if inp.pensiones_procedimiento_especial is None:
            c.falta("pensiones_procedimiento_especial")
            desconocido = True
        elif not inp.pensiones_procedimiento_especial:
            causas.append(
                _regla(
                    "96.3.a.1",
                    f"Tienes más de un pagador y el segundo y los restantes te pagaron {_eur(importe or ZERO)}, más "
                    f"de {_eur(o.limite_segundo_pagador)}: tu límite es {_eur(bajo)} y tus rendimientos del trabajo "
                    f"({_eur(t)}) lo superan.",
                )
            )
    for campo, clave, aplicacion in (
        (
            "pension_compensatoria_o_anualidades",
            "96.3.b",
            "Cobras una pensión compensatoria o anualidades por alimentos no exentas",
        ),
        ("pagador_no_obligado_a_retener", "96.3.c", "Uno de tus pagadores no estaba obligado a retener"),
        ("trabajo_tipo_fijo_retencion", "96.3.d", "Cobraste rendimientos del trabajo con un tipo fijo de retención"),
    ):
        valor = getattr(inp, campo)
        if valor is None:
            c.falta(campo)
            desconocido = True
        elif valor:
            causas.append(
                _regla(
                    clave,
                    f"{aplicacion}: tu límite es {_eur(bajo)} y tus rendimientos del trabajo ({_eur(t)}) lo superan.",
                )
            )
    limite = bajo if causas else alto if not desconocido else None
    if limite is not None:
        c.lineas.append(
            LineaObligacion(
                concepto="Límite de rendimientos del trabajo aplicable",
                importe=limite,
                referencia=(causas[0] if causas else _regla("96.2.a", "")).referencia,
            )
        )
    if causas:
        estado.cumple = False
        estado.motivo = causas[0]
    elif desconocido:
        estado.cumple = None
        estado.motivo = _regla(
            "96.3.a.1",
            f"Tus rendimientos íntegros del trabajo ({_eur(t)}) están entre {_eur(bajo)} y {_eur(alto)}: depende de "
            "cuántos pagadores tuviste y de los demás supuestos del art. 96.3.",
        )
    else:
        varios_y_pensiones = bool(varios) and inp.pensiones_procedimiento_especial
        estado.motivo = (
            _regla(
                "96.3.a.2",
                f"Aunque tienes varios pagadores, solo cobras pensiones con la retención fijada por el procedimiento "
                f"especial: tu límite sigue siendo {_eur(alto)} y tus rendimientos del trabajo ({_eur(t)}) no lo "
                "superan.",
            )
            if varios_y_pensiones
            else (
                _regla(
                    "96.3.a.1",
                    f"Lo que te pagaron el segundo y los restantes pagadores ({_eur(importe)}) no supera "
                    f"{_eur(o.limite_segundo_pagador)}: tu límite sigue siendo {_eur(alto)} y tus rendimientos del "
                    f"trabajo ({_eur(t)}) no lo superan.",
                )
                if importe is not None and importe > 0
                else _regla(
                    "96.2.a",
                    f"Tienes un solo pagador y ningún otro supuesto del art. 96.3: tu límite es {_eur(alto)} y tus "
                    f"rendimientos íntegros del trabajo ({_eur(t)}) no lo superan.",
                )
            )
        )
    return estado


def _fuentes_exentas(c: _Contexto) -> tuple[_Estado, list[Regla]]:
    """Art. 96.2 a) and b): income only from those sources, within their limits."""
    inp, o = c.inp, c.o
    estado = _Estado()
    reglas: list[Regla] = []
    trabajo = inp.rendimiento_trabajo_bruto
    capital = inp.rendimiento_capital_mobiliario
    ganancias = max(inp.ganancias_patrimoniales, ZERO)
    perdidas = max(-inp.ganancias_patrimoniales, ZERO)
    alquiler = inp.rendimiento_alquiler_neto + inp.rendimiento_alquiler_vivienda_sin_reducir

    ajenas = [
        nombre
        for nombre, importe in (
            ("rendimientos de actividades económicas", inp.rendimiento_actividad_neto),
            ("rendimientos de alquileres", alquiler),
        )
        if importe
    ]
    if ganancias and inp.ganancias_sometidas_a_retencion is False:
        ajenas.append("ganancias patrimoniales no sometidas a retención")
    if ajenas:
        estado.cumple = False
        estado.motivo = _regla(
            "96.1",
            f"Tienes {' y '.join(ajenas)}, que no están entre las rentas del art. 96.2 a) a c) que eximen de "
            "declarar.",
        )
        return estado, reglas

    inciertas: list[str] = []
    if ganancias and inp.ganancias_sometidas_a_retencion is None:
        c.falta("ganancias_sometidas_a_retencion")
        inciertas.append("si tus ganancias patrimoniales estaban sometidas a retención")
    if inp.otras_rentas_base_general:
        c.falta_sin_campo(
            "otras_rentas_base_general",
            "¿Qué son tus «otras rentas de la base general»? La obligación de declarar depende de su tipo.",
        )
        inciertas.append("de qué tipo son tus «otras rentas»")
    if perdidas:
        inciertas.append("cómo cuentan tus pérdidas patrimoniales")
        c.avisos.append(
            "El art. 96.2 a) a c) LIRPF no incluye las pérdidas patrimoniales entre las rentas que eximen de "
            f"declarar; con pérdidas de {_eur(perdidas)} y rentas por encima de {_eur(o.limite_conjunto_rentas_minimas)} "
            "este programa no puede asegurar que no estés obligado."
        )

    a = _limite_trabajo(trabajo, c) if trabajo else None
    if a is not None and a.motivo is not None:
        reglas.append(a.motivo)
    con_retencion = max(capital, ZERO) + (ganancias if inp.ganancias_sometidas_a_retencion else ZERO)
    b_falla = con_retencion > o.limite_capital_y_ganancias_con_retencion
    if con_retencion:
        c.lineas.append(
            LineaObligacion(
                concepto="Capital mobiliario y ganancias sometidos a retención",
                importe=con_retencion,
                referencia=CITAS["96.2.b"].referencia,
            )
        )
        reglas.append(
            _regla(
                "96.2.b",
                f"Tus rendimientos del capital mobiliario y ganancias sometidos a retención ({_eur(con_retencion)}) "
                + ("superan" if b_falla else "no superan")
                + f" el límite conjunto de {_eur(o.limite_capital_y_ganancias_con_retencion)}.",
            )
        )
        if ganancias and inp.ganancias_sometidas_a_retencion:
            c.avisos.append(
                "La letra b) no se aplica a las ganancias por reembolsos de fondos de inversión cuya base de "
                "retención no se determine por la cuantía que se integra en la base imponible "
                f"({CITAS['96.2.b.iic'].referencia}); si es tu caso, estás obligado a declarar."
            )
    if capital < 0:
        c.avisos.append(
            "Tus rendimientos del capital mobiliario son negativos: el art. 96.2.b) mira los rendimientos íntegros, "
            "que este cálculo no tiene; se han tomado como cero."
        )

    if a is not None and a.cumple is False:
        estado.cumple, estado.motivo = False, a.motivo
    elif b_falla:
        estado.cumple, estado.motivo = False, reglas[-1]
    elif a is not None and a.cumple is None:
        estado.cumple, estado.motivo = None, a.motivo
    elif inciertas:
        estado.cumple = None
        estado.motivo = _regla(
            "96.2.a" if trabajo else "96.2.b",
            "El resto de tus rentas entra en los límites, pero falta saber " + " y ".join(inciertas) + ".",
        )
    else:
        estado.motivo = reglas[0] if reglas else None
    return estado, reglas


def _rentas_minimas(c: _Contexto) -> _Estado:
    """Art. 96.2, penultimate paragraph: up to 1.000 euros and losses below 500 euros."""
    inp, o = c.inp, c.o
    estado = _Estado()
    ganancias = max(inp.ganancias_patrimoniales, ZERO)
    perdidas = max(-inp.ganancias_patrimoniales, ZERO)
    alquiler = inp.rendimiento_alquiler_neto + inp.rendimiento_alquiler_vivienda_sin_reducir
    netos = (inp.rendimiento_actividad_neto, alquiler)
    # Gross income is at least the net figures given (expenses are never negative).
    cota = (
        inp.rendimiento_trabajo_bruto
        + max(inp.rendimiento_capital_mobiliario, ZERO)
        + ganancias
        + sum((max(x, ZERO) for x in netos), ZERO)
        + max(inp.otras_rentas_base_general, ZERO)
    )
    limite, limite_perdidas = o.limite_conjunto_rentas_minimas, o.limite_perdidas_patrimoniales
    if perdidas >= limite_perdidas:
        estado.cumple = False
        estado.motivo = _regla(
            "96.2.minimas",
            f"Tus pérdidas patrimoniales ({_eur(perdidas)}) no son inferiores a {_eur(limite_perdidas)}.",
        )
    elif cota > limite:
        estado.cumple = False
        estado.motivo = _regla(
            "96.2.minimas", f"Tus rentas ({_eur(cota)} al menos) superan {_eur(limite)} en conjunto."
        )
    elif any(netos) or inp.rendimiento_capital_mobiliario < 0 or inp.otras_rentas_base_general:
        estado.cumple = None
        estado.motivo = _regla(
            "96.2.minimas",
            f"Tus rentas netas suman {_eur(cota)}, pero el límite de {_eur(limite)} se mide con los ingresos íntegros "
            "(antes de gastos), que este cálculo no tiene.",
        )
        if any(netos):
            c.falta_sin_campo(
                "ingresos_integros",
                "¿Cuánto ingresaste en bruto (antes de gastos) por tu actividad económica o tus alquileres? El "
                f"límite de {_eur(limite)} se mide con los ingresos íntegros.",
            )
    else:
        estado.motivo = _regla(
            "96.2.minimas",
            f"Tus rentas suman {_eur(cota)}, sin superar {_eur(limite)}"
            + (
                f", y tus pérdidas patrimoniales ({_eur(perdidas)}) son inferiores a {_eur(limite_perdidas)}."
                if perdidas
                else "."
            ),
        )
    return estado


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def obligado_a_declarar(
    inp: IRPFInput, params: IRPFParams | None = None, *, resultado: IRPFResult | None = None
) -> ResultadoObligacion:
    """Whether the taxpayer of ``inp`` must file the IRPF return of ``inp.ejercicio``.

    ``resultado``: the calculator's result for ``inp`` when the caller already has it (``calcular_irpf`` passes
    its own); otherwise it is computed, for the pension-scheme reduction, the housing deduction and the refund.
    """
    from hacienda_gpt.tax.irpf import calcular_irpf

    p = params or load_params(inp.ejercicio)
    o = p.obligacion_declarar
    fp = p.fuente_principal
    fuente = f"art. 96 LIRPF — {fp.norma}, {fp.boe_id}, consolidado a {fp.consolidado_a}"
    no_verificados = [] if o.fuente.verificado else [f"obligacion_declarar ({o.fuente.referencia})"]

    if inp.tributacion != "individual":
        return ResultadoObligacion(
            ejercicio=inp.ejercicio,
            obligado="no_se_sabe",
            respuesta=(
                f"Con las cifras sumadas de la unidad familiar no se puede saber si estáis obligados a declarar el "
                f"IRPF de {inp.ejercicio}: la obligación se comprueba persona a persona, con las rentas de cada uno."
            ),
            regla=_regla("96.2.a", "Los límites se aplican a las rentas de cada contribuyente."),
            recomendacion="Haz la comprobación en tributación individual con las cifras de cada miembro.",
            parametros_no_verificados=no_verificados,
            fuente=fuente,
        )
    if resultado is None:
        resultado = calcular_irpf(inp, p)

    c = _Contexto(inp, o)
    for concepto, importe in (
        ("Rendimientos íntegros del trabajo", inp.rendimiento_trabajo_bruto),
        ("Rendimientos del capital mobiliario", inp.rendimiento_capital_mobiliario),
        ("Ganancias y pérdidas patrimoniales", inp.ganancias_patrimoniales),
        ("Rendimiento neto de actividades económicas", inp.rendimiento_actividad_neto),
        (
            "Rendimiento neto de alquileres",
            inp.rendimiento_alquiler_neto + inp.rendimiento_alquiler_vivienda_sin_reducir,
        ),
        ("Otras rentas de la base general", inp.otras_rentas_base_general),
    ):
        if importe:
            c.lineas.append(LineaObligacion(concepto=concepto, importe=importe))

    obligado: Obligado
    regla: Regla | None
    otras: list[Regla] = []
    forzosas = _obligan_en_todo_caso(inp, resultado, c)
    if forzosas:
        obligado, regla, otras = "si", forzosas[0], forzosas[1:]
    else:
        minimas = _rentas_minimas(c)
        exentas, reglas_exentas = _fuentes_exentas(c)
        if minimas.cumple:
            obligado, regla = "no", minimas.motivo
        elif exentas.cumple:
            obligado, regla = "no", exentas.motivo
            otras = [r for r in reglas_exentas if r is not regla]
        elif minimas.cumple is False and exentas.cumple is False:
            obligado, regla = "si", exentas.motivo
            if minimas.motivo is not None:
                otras = [minimas.motivo]
        else:
            obligado = "no_se_sabe"
            regla = exentas.motivo if exentas.cumple is None and exentas.motivo is not None else minimas.motivo
        if obligado != "si" and inp.rendimiento_actividad_neto and inp.alta_autonomo_en_el_ejercicio is None:
            # With business income the registration as self-employed decides before anything else.
            c.falta("alta_autonomo_en_el_ejercicio")
            obligado = "no_se_sabe"
            regla = _regla(
                "96.2.reta",
                "Tienes rendimientos de una actividad económica: si estuviste de alta como autónomo en algún momento "
                "del año, tienes que declarar.",
            )
        elif obligado == "no" and inp.alta_autonomo_en_el_ejercicio is None:
            c.avisos.append(
                "Si estuviste de alta como autónomo (RETA) o en el Régimen Especial de Trabajadores del Mar en algún "
                f"momento del año, sí tienes que declarar ({CITAS['96.2.reta'].referencia})."
            )
        if obligado == "si" and regla is None:
            regla = minimas.motivo

    if obligado != "si" or not forzosas:
        c.avisos.append(
            "No se han tenido en cuenta rentas inmobiliarias imputadas (inmuebles a tu disposición que no son tu "
            "vivienda ni están alquilados), rendimientos de Letras del Tesoro ni subvenciones para vivienda protegida "
            f"({CITAS['96.2.c'].referencia}): si tienes alguna, la respuesta solo se mantiene si en conjunto no "
            f"superan {_eur(o.limite_imputadas_letras_subvenciones)}."
        )
    if obligado != "si" and (
        inp.hijos_deduccion_maternidad or inp.familia_numerosa or inp.num_familiares_discapacidad_a_cargo
    ):
        c.avisos.append(
            "Si cobraste por anticipado la deducción por maternidad o las de familia numerosa o personas con "
            "discapacidad a cargo, no estar obligado a declarar no te exime de regularizarlas: el Reglamento prevé "
            f"cómo hacerlo sin declaración ({CITAS['60.5'].referencia}; {CITAS['60bis.3'].referencia})."
        )

    # ---- Recommendation ---------------------------------------------------- #
    neto = resultado.resultado
    devolucion = -neto if neto < 0 else None
    lineas_recomendacion: list[str] = []
    if obligado == "si":
        lineas_recomendacion.append(
            f"Según este cálculo, la declaración te saldría a devolver {_eur(-neto)}."
            if neto < 0
            else (
                f"Según este cálculo, la declaración te saldría a pagar {_eur(neto)}."
                if neto > 0
                else "Según este cálculo, la declaración te saldría a cero."
            )
        )
    elif devolucion is not None:
        lineas_recomendacion.append(
            ("Aunque no estás obligado, te conviene declarar" if obligado == "no" else "Te conviene declarar")
            + f": según este cálculo te devolverían {_eur(devolucion)} (cuota líquida {_eur(resultado.cuota_liquida)}; "
            f"retenciones e ingresos a cuenta {_eur(resultado.retenciones)}). La devolución se pide presentando la "
            "declaración."
        )
    elif obligado == "no":
        lineas_recomendacion.append(
            "No te hace falta presentarla: según este cálculo no te devolverían nada"
            + (f" (te saldría a pagar {_eur(neto)})." if neto > 0 else ".")
        )
    else:
        lineas_recomendacion.append(
            (
                "Contesta a lo que falta para saberlo"
                if any(dato.campo for dato in c.faltan.values())
                else "Ante la duda, lo prudente es presentar la declaración"
            )
            + (f". Si declaras, según este cálculo te saldría a pagar {_eur(neto)}." if neto > 0 else ".")
        )
    if (
        inp.ganancias_patrimoniales < 0
        or inp.rendimientos_negativos_pendientes
        or inp.perdidas_patrimoniales_pendientes
    ):
        lineas_recomendacion.append(
            "Con pérdidas o saldos negativos del ahorro, declarar te permite compensarlos en los años siguientes "
            "(art. 49 LIRPF)."
        )

    datos = list(c.faltan.values())
    if obligado == "si":
        assert regla is not None
        respuesta = f"Sí, estás obligado a declarar el IRPF de {inp.ejercicio}. {regla.aplicacion}"
    elif obligado == "no":
        respuesta = f"No estás obligado a declarar el IRPF de {inp.ejercicio}." + (
            f" {regla.aplicacion}" if regla is not None else ""
        )
    else:
        respuesta = f"Con estos datos no se puede saber si estás obligado a declarar el IRPF de {inp.ejercicio}." + (
            f" {regla.aplicacion}" if regla is not None else ""
        )
    return ResultadoObligacion(
        ejercicio=inp.ejercicio,
        obligado=obligado,
        respuesta=respuesta,
        regla=regla,
        otras_reglas=otras,
        datos_que_faltan=datos,
        recomendacion=" ".join(lineas_recomendacion) or None,
        resultado_si_declara=neto,
        devolucion_estimada=devolucion,
        lineas=c.lineas,
        avisos=c.avisos,
        parametros_no_verificados=no_verificados,
        fuente=fuente,
    )
