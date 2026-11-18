"""The style-leak probe: can a small local classifier tell the author's model family from the normalised text?"""

from __future__ import annotations

import random

from asistente.nucleo.consejo.anonimato import normalizar_propuesta
from asistente.nucleo.consejo.contrato import Propuesta
from asistente.nucleo.evaluacion import sesgos as s
from asistente.nucleo.evaluacion import sesgos_falsos as f

CONFIG = s.ConfigSesgos(remuestras=300, permutaciones=300)

_FRASES = (
    "La cuota actual pesa bastante en el presupuesto del hogar",
    "El colchón de emergencia cubre varios meses de gastos",
    "La rentabilidad del fondo no está garantizada",
    "El ahorro de intereses es seguro si se amortiza",
    "Los ingresos del hogar se mantienen estables",
    "Conviene no comprometer el dinero que pueda hacer falta",
    "El plazo de la decisión es largo y admite cierta volatilidad",
    "La familia prefiere una cuota que no cambie",
)


def _contenido(i: int) -> str:
    rng = random.Random(i)
    return ". ".join(rng.sample(_FRASES, 4)) + "."


def test_twins_with_identical_normalised_text_stay_at_chance() -> None:
    textos = f.textos_autor(60, quitar_tics=True)
    aciertos, total = s.precision_cruzada(textos)
    assert total == 120 and aciertos == 60  # exactly chance: twins share a fold and a prediction
    assert s.medir_fuga_estilo(textos, CONFIG).veredicto == "sin_sesgo"
    # The same chance-level accuracy on too few texts cannot certify «no leak» within 10 points: inconclusive.
    pocos = s.medir_fuga_estilo(f.textos_autor(20, quitar_tics=True), CONFIG)
    assert pocos.detalles["precision"] == 0.5 and pocos.veredicto == "no_concluyente"


def test_obvious_tics_leak() -> None:
    resultado = s.medir_fuga_estilo(f.textos_autor(40, quitar_tics=False), CONFIG)
    assert resultado.veredicto == "sesgo" and resultado.detalles["precision"] > 0.9


def test_a_subtle_punctuation_habit_is_also_detected() -> None:
    """Family A writes «…» and «—», family B «...» and «-»: same words, different typography."""
    textos: list[s.TextoAutor] = []
    for i in range(50):
        base = _contenido(i)
        textos.append(s.TextoAutor(base.replace(".", "…", 1).replace(", ", " — ", 1), "anthropic", f"g{i}"))
        textos.append(s.TextoAutor(base.replace(".", "...", 1).replace(", ", " - ", 1), "openai", f"g{i}"))
    resultado = s.medir_fuga_estilo(textos, CONFIG)
    assert resultado.veredicto == "sesgo", resultado.detalles


def test_three_families_chance_is_the_majority_share() -> None:
    textos = f.textos_autor(50, familias=("anthropic", "openai", "local"), quitar_tics=True)
    resultado = s.medir_fuga_estilo(textos, CONFIG)
    assert resultado.detalles["azar"] == round(1 / 3, 4)
    assert resultado.veredicto == "sin_sesgo"


def test_one_family_or_one_dossier_is_not_measurable() -> None:
    una = [s.TextoAutor("texto cualquiera", "anthropic", f"g{i}") for i in range(10)]
    assert s.medir_fuga_estilo(una, CONFIG).veredicto == "no_aplicable"
    un_grupo = [s.TextoAutor("texto", fam, "g") for fam in ("anthropic", "openai")]
    assert s.medir_fuga_estilo(un_grupo, CONFIG).veredicto == "no_aplicable"


def _propuesta(seat: str, texto: str, riesgo: str) -> Propuesta:
    return Propuesta(
        miembro_id=seat,
        ronda=1,
        recomendacion="o1",
        confianza=0.7,
        razonamiento=texto,
        riesgos=[riesgo],
        supuestos=["Los ingresos se mantienen."],
    )


def test_the_councils_own_normalisation_removes_formatting_and_signatures() -> None:
    """Markdown, emojis and signatures are what F's normaliser promises to strip: after it, the probe must be at
    chance on texts whose ONLY family signal is that formatting."""
    crudos: list[s.TextoAutor] = []
    normalizados: list[s.TextoAutor] = []
    for i in range(60):
        contenido = _contenido(i)
        riesgo = "Quedarse sin liquidez ante un imprevisto."
        estilos = {
            "anthropic": (f"## Análisis\n\n{contenido}\n\n— Prudencia Financiera", f"**{riesgo}**"),
            "openai": (f"{contenido} ✅🙂\n\n*Atentamente, el consejero*", f"- {riesgo} 👍"),
        }
        for familia, (texto, riesgo_formateado) in estilos.items():
            propuesta = _propuesta("prudente", texto, riesgo_formateado)
            crudos.append(s.TextoAutor(f"{texto}\n{riesgo_formateado}", familia, f"g{i}"))
            normalizados.append(s.TextoAutor(s.texto_de(normalizar_propuesta(propuesta, "A")), familia, f"g{i}"))
    assert s.medir_fuga_estilo(crudos, CONFIG).veredicto == "sesgo"
    limpio = s.medir_fuga_estilo(normalizados, CONFIG)
    assert limpio.veredicto == "sin_sesgo", limpio.detalles
