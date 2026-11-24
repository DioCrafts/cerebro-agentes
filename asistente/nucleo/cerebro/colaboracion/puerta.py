"""The figures gate: every number in the brain's final answer says where it came from.

The model writes the answer; this deterministic gate decides whether its figures may reach the user. It reads every
number of the answer (``cifras.escanear``) and applies these rules, in this order:

1. **Not a figure → skipped, and listed.** Dates, years, article and norm numbers («art. 68», «Ley 35/2006»),
   ordinals, identifiers, numbers inside ``[F#]``… are not quantities anyone computed; they go to
   ``ResultadoPuerta.ignorados`` with the reason, so the trace shows what was skipped and why (no silent holes).
2. **A specialist's figure → attributed.** A number equal to a ``Cifra`` of the turn's reports, at the precision it
   is written with («1.234 €» shows 1.234,40 rounded and 1.234,56 truncated) and with a compatible unit («€/mes»
   never matches a «€/año» figure, money never matches a percentage), is attributed to that figure: its specialist,
   its tool and its ledger fragment (``AtribucionCifra``). Why: «code computes, the model reasons» — every figure must
   be one a tool produced, so the user can trace it.
3. **The user's own words → attributed to the user.** A number the user wrote in the conversation may be repeated
   («con tus 30.000 € de sueldo»): it is the user's datum, not an invention.
4. **A literal of a cited source → attributed to the fragment.** A number written in the same sentence as an
   ``[F#]`` whose ledger text contains that number (a BOE article, a tool result) is the source's literal. A source
   may write it in words: «menores de cuarenta años» is the literal of «40 años [F2]» (``numeros_en_letras``, read
   on the fragment side only). A number in a sentence that cites NOTHING («para quedar por debajo de 26.414,22 €»,
   after «…no supere 26.414,22 € [F17]») is the literal of a fragment the answer cites in another sentence when
   that fragment contains it. Only fragments the answer cites count, never the rest of the ledger; and a sentence
   that cites a fragment answers for its own numbers (a number next to «[F22]» found only in F17 is not credited).
5. **Otherwise → a violation**, classified so the revision message can say exactly what to fix:

   * ``suma_no_combinada`` — the number equals a sum or difference of two or three figures of DIFFERENT
     specialists, or such a sum ×12 or ÷12 (monthly ↔ yearly), and no tool produced it (rule 2 would have matched
     it otherwise). Why: two specialists' figures rest on different assumptions, periods and bases (a mortgage saving
     per year, a tax saving per return); adding them is a calculation nobody verified — the classic «ahorras en
     total 2.350 €». A tool that DID combine them produced a ``Cifra`` with that total, and rule 2 passes it.
   * ``cifra_sin_origen`` — any other unmatched number; the detail says when it looks like the model's own
     arithmetic on ONE specialist's figures (a sum, ×12) or like a figure with the wrong unit, so the revision can
     fix it instead of guessing.

   Independently of the above:

   * ``cifra_mal_atribuida`` — the answer says whose figure it is («según Hipoteca, 23 %», «Fiscal calcula…»,
     «23 % (Fiscal)», a «**Hipoteca**:» line label) and the figure matched belongs to another specialist. Why: the
     user decides on the basis of who said it (the tax advisor's figures are verified against the BOE; a module's are
     estimates over the household data).
   * ``cita_inexistente`` — an ``[F#]`` that is not in the ledger. Why: a citation must point at evidence the turn
     actually holds; an invented id is a fabricated source.

The sum detector is bounded: it looks at most at the first ``MAX_NUMEROS_COMBINADOS`` unmatched numbers; pairs over
at most ``MAX_OPERANDOS_PARES`` figures (O(n²)), triples over at most ``MAX_OPERANDOS_TRIOS`` (O(n² log n): every
pair, then a binary search for the third operand), each for the factors 1, 12 and 1/12. The detector never decides
WHETHER there is a violation — every unmatched number already is one — only which kind, so a coincidental match can
mislabel a violation but never let one through or invent one.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
import re

from asistente.nucleo.agent.evidence import EvidenceLedger, extract_cited_ids
from asistente.nucleo.cerebro.colaboracion.cifras import (
    FAMILIA_EUR,
    Numero,
    coincide,
    escanear,
    familia,
    formatear_valor,
    numeros_de,
    numeros_en_letras,
    plano,
    unicas,
    unidades_compatibles,
)
from asistente.nucleo.cerebro.contrato import (
    AtribucionCifra,
    Cifra,
    InformeEspecialista,
    ResultadoPuerta,
    Violacion,
)

MAX_NUMEROS_COMBINADOS = 20
MAX_OPERANDOS_PARES = 120
MAX_OPERANDOS_TRIOS = 40
# (multiplier, divisor) applied to a combination: as is, a monthly figure ×12, a yearly one ÷12.
_FACTORES: tuple[tuple[int, int], ...] = ((1, 1), (12, 1), (1, 12))


# --------------------------------------------------------------------------------------------------------------
# Sum detector
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Combinacion:
    """A combination of figures that equals a number of the answer: Σ(signo·cifra) × multiplicador ÷ divisor."""

    operandos: tuple[tuple[int, Cifra], ...]
    multiplicador: int = 1
    divisor: int = 1

    @property
    def especialistas(self) -> frozenset[str]:
        return frozenset(c.especialista for _s, c in self.operandos)

    @property
    def cruzada(self) -> bool:
        """It mixes figures of at least two different specialists."""
        return len(self.especialistas) > 1

    def valor(self) -> Decimal:
        total = sum((Decimal(s) * c.valor for s, c in self.operandos), Decimal(0))
        return total * self.multiplicador / self.divisor

    def describir(self, nombres: Mapping[str, str]) -> str:
        """Spanish: «la suma de C2 (Hipoteca, 1.500 €/año) y C5 (Fiscal, 850 €)», «C3 (Luz, 50 €/mes) × 12»…"""

        def una(cifra: Cifra) -> str:
            quien = nombres.get(cifra.especialista, cifra.especialista)
            return f"{cifra.id} ({quien}, {cifra.literal or formatear_valor(cifra.valor, cifra.unidad)})"

        if len(self.operandos) == 1:
            texto = una(self.operandos[0][1])
        else:
            restas = any(s < 0 for s, _c in self.operandos)
            partes = [una(c) for _s, c in self.operandos]
            nucleo = ", ".join(partes[:-1]) + " y " + partes[-1]
            if restas:
                signos = " ".join(("+" if s > 0 else "−") + " " + una(c) for s, c in self.operandos)
                texto = f"la combinación {signos.lstrip('+ ')}"
            else:
                texto = f"la suma de {nucleo}"
        if self.multiplicador != 1:
            texto += f" multiplicada por {self.multiplicador}"
        if self.divisor != 1:
            texto += f" dividida entre {self.divisor}"
        return texto


def _intervalo(numero: Numero) -> tuple[Decimal, Decimal]:
    """A superset of the absolute values ``numero`` can show (both rounding and truncation)."""
    assert numero.mantisa is not None
    paso = Decimal(1).scaleb(-numero.decimales)
    escrito = abs(numero.mantisa)
    return max(Decimal(0), (escrito - paso) * numero.escala), (escrito + paso) * numero.escala


def _grupos(numero: Numero, cifras: Sequence[Cifra]) -> list[list[Cifra]]:
    """Operand sets: figures of the number's unit family (or without unit), never zero, without repeats."""
    utiles = [c for c in unicas(cifras) if c.valor != 0]
    objetivo = familia(numero.unidad)
    if objetivo is not None:
        return [[c for c in utiles if familia(c.unidad) in (objetivo, None)]]
    familias = sorted({f for c in utiles if (f := familia(c.unidad)) is not None})
    sin_unidad = [c for c in utiles if c.unidad is None]
    grupos = [[c for c in utiles if familia(c.unidad) in (f, None)] for f in familias]
    return grupos or [sin_unidad]


def buscar_combinacion(
    numero: Numero, cifras: Sequence[Cifra], *, max_operandos: int = 3, presupuesto: int = 60_000
) -> Combinacion | None:
    """The simplest combination of ``cifras`` (1 operand ×12 or ÷12; 2 or 3 operands added or subtracted, then
    optionally ×12 or ÷12) that ``numero`` shows, preferring one that mixes specialists; None when there is none.

    ``presupuesto`` bounds the pair iterations of the triple search (deterministic: the same input always stops at
    the same place).
    """
    if not numero.es_cifra or numero.mantisa is None:
        return None
    hallada: Combinacion | None = None
    for grupo in _grupos(numero, cifras):
        dinero = familia(numero.unidad) in (None, "EUR") and any(c.unidad in FAMILIA_EUR for c in grupo)
        factores = _FACTORES if dinero else _FACTORES[:1]
        for tam in range(1, max_operandos + 1):
            encontrada = _buscar_tamano(numero, grupo, tam, factores, presupuesto)
            if encontrada is not None and (hallada is None or _mejor(encontrada, hallada)):
                hallada = encontrada
            if hallada is not None and len(hallada.operandos) <= tam:
                break
    return hallada


def _mejor(a: Combinacion, b: Combinacion) -> bool:
    return (len(a.operandos), not a.cruzada) < (len(b.operandos), not b.cruzada)


def _buscar_tamano(
    numero: Numero, grupo: list[Cifra], tam: int, factores: Sequence[tuple[int, int]], presupuesto: int
) -> Combinacion | None:
    primera: Combinacion | None = None
    for mult, div in factores:
        if tam == 1 and (mult, div) == (1, 1):
            continue  # a single figure as is would have matched directly
        if tam == 1:
            for c in grupo:
                if coincide(numero, c.valor * mult / div):
                    return Combinacion(((1, c),), mult, div)
        elif tam == 2:
            ops = grupo[:MAX_OPERANDOS_PARES]
            for i in range(len(ops)):
                for j in range(i + 1, len(ops)):
                    for signo in (1, -1):
                        combinacion = Combinacion(((1, ops[i]), (signo, ops[j])), mult, div)
                        if coincide(numero, combinacion.valor()):
                            if combinacion.cruzada:
                                return combinacion
                            primera = primera or combinacion
        elif tam == 3 and len(grupo) >= 3:
            encontrada = _trios(numero, grupo[:MAX_OPERANDOS_TRIOS], mult, div, presupuesto)
            if encontrada is not None:
                if encontrada.cruzada:
                    return encontrada
                primera = primera or encontrada
    return primera


def _trios(numero: Numero, ops: list[Cifra], mult: int, div: int, presupuesto: int) -> Combinacion | None:
    """a = (±x ± y ± z)·mult/div: every pair, then the third operand by binary search on the sorted values."""
    bajo, alto = _intervalo(numero)
    bajo, alto = bajo * div / mult, alto * div / mult  # bounds of |x ± y ± z|
    orden = sorted(range(len(ops)), key=lambda k: ops[k].valor)
    valores = [ops[k].valor for k in orden]
    primera: Combinacion | None = None
    pasos = 0
    for i in range(len(ops)):
        for j in range(i + 1, len(ops)):
            pasos += 1
            if pasos > presupuesto:
                return primera
            for sj in (1, -1):
                parcial = ops[i].valor + sj * ops[j].valor
                # z with s·z in [bajo−p, alto−p] or in [−alto−p, −bajo−p], for s = ±1.
                for lo, hi in ((bajo - parcial, alto - parcial), (-alto - parcial, -bajo - parcial)):
                    for sk, (zlo, zhi) in ((1, (lo, hi)), (-1, (-hi, -lo))):
                        for pos in range(bisect_left(valores, zlo), bisect_right(valores, zhi)):
                            k = orden[pos]
                            if k in (i, j):
                                continue
                            combinacion = Combinacion(((1, ops[i]), (sj, ops[j]), (sk, ops[k])), mult, div)
                            if coincide(numero, combinacion.valor()):
                                if combinacion.cruzada:
                                    return combinacion
                                primera = primera or combinacion
    return primera


# --------------------------------------------------------------------------------------------------------------
# Sentences and attribution cues
# --------------------------------------------------------------------------------------------------------------

_ABREVIATURAS = frozenset(
    "art arts num pag apdo apdos aprox max min sr sra dr dra etc ej vs n ss cfr vid admon dna d p rd lo ee uu".split()
)
_FIN_FRASE_RE = re.compile(r"[.!?…]+(?=\s|$)|\n")
_CORTE_ALCANCE_RE = re.compile(
    r";|\bpero\b|\bmientras\b|\ben cambio\b|\bademas\b|\baunque\b|\bpor otro lado\b|\bpor su parte\b|\bsin embargo\b"
)


def _frases(texto: str) -> list[tuple[int, int]]:
    """Sentence spans (a period after a known abbreviation, «art. 68», does not end one; a newline always does)."""
    spans: list[tuple[int, int]] = []
    inicio = 0
    for m in _FIN_FRASE_RE.finditer(texto):
        if m.group() != "\n" and m.group().startswith("."):
            palabra = re.search(r"(\w+)$", texto[max(0, m.start() - 12) : m.start()])
            if palabra is not None and plano(palabra.group(1)) in _ABREVIATURAS:
                continue
        spans.append((inicio, m.end()))
        inicio = m.end()
    if inicio < len(texto):
        spans.append((inicio, len(texto)))
    return spans


@dataclass(frozen=True)
class _Pista:
    """«según X», «X calcula», «(X)», «X:» — the answer saying whose figure follows (or precedes)."""

    inicio: int
    fin: int
    especialista: str
    tipo: str  # "previa" | "posterior" | "parentesis" | "etiqueta"


def _variantes(nombres: Mapping[str, str]) -> dict[str, str]:
    variantes: dict[str, str] = {}
    for clave, nombre in nombres.items():
        for forma in (nombre, clave, clave.replace("_", " ")):
            forma = " ".join(plano(forma).split())
            if len(forma) >= 3:
                variantes.setdefault(forma, clave)
    return variantes


def _pistas(texto: str, nombres: Mapping[str, str]) -> list[_Pista]:
    variantes = _variantes(nombres)
    if not variantes:
        return []
    alternativas = "|".join(
        r"\s+".join(re.escape(p) for p in forma.split()) for forma in sorted(variantes, key=len, reverse=True)
    )
    nombre = rf"[«\"“]?(?P<n>{alternativas})[»\"”]?"
    articulo = r"(?:(?:el|la|los|las)\s+)?"
    especialista = rf"(?:(?:especialista|modulo|asesor|experto)\s+(?:(?:en|de)\s+{articulo})?)?"
    patrones: tuple[tuple[str, re.Pattern[str]], ...] = (
        # «Hipoteca: …» at the start of a line or a sentence, «**Hipoteca**: …» anywhere.
        (
            "etiqueta",
            re.compile(
                rf"(?m)(?:^|(?<=[.!?;]\s))[ \t]*(?:[-*•>][ \t]*)?(?:\*\*|__)?{nombre}(?:\*\*|__)?[ \t]*(?:\*\*)?:"
            ),
        ),
        ("etiqueta", re.compile(rf"(?:\*\*|__){nombre}(?:(?:\*\*|__)[ \t]*:|[ \t]*:(?:\*\*|__))")),
        ("parentesis", re.compile(rf"\((?:segun\s+)?{articulo}{especialista}{nombre}(?:\s*,?\s*\[f\d+\])?\s*\)")),
        ("previa", re.compile(rf"\bsegun\s+{articulo}{especialista}{nombre}(?!\w)")),
        (
            "previa",
            re.compile(rf"\b(?:especialista|modulo|asesor|experto)\s+(?:(?:en|de)\s+{articulo})?{nombre}(?!\w)"),
        ),
        (
            "previa",
            re.compile(
                rf"(?<!\w){nombre}\s+(?:calcula|estima|indica|dice|senala|informa|cifra|preve|da|aporta|apunta|"
                r"ha\s+calculado|ha\s+estimado|calculo|estimo)\b"
            ),
        ),
    )
    llano = plano(texto)
    pistas: list[_Pista] = []
    for tipo, patron in patrones:
        for m in patron.finditer(llano):
            clave = variantes.get(" ".join(m.group("n").split()))
            if clave is None:
                continue
            pistas.append(_Pista(m.start(), m.end(), clave, tipo))
    pistas.sort(key=lambda p: (p.inicio, p.fin))
    return pistas


def _atribuido(numero: Numero, frase: tuple[int, int], pistas: Sequence[_Pista], texto_plano: str) -> str | None:
    """The specialist the answer says ``numero`` is from (None when it does not say)."""
    for pista in pistas:
        hueco = texto_plano[numero.fin : pista.inicio]
        if pista.tipo == "parentesis" and pista.inicio >= numero.fin and len(hueco) <= 3 and not hueco.strip():
            return pista.especialista
        if (
            pista.tipo == "previa"
            and texto_plano[pista.inicio :].startswith("segun")
            and frase[0] <= pista.inicio < frase[1]
            and pista.inicio >= numero.fin
            and re.fullmatch(r"[\s,]*", hueco)
        ):
            return pista.especialista
    elegida: str | None = None
    for pista in pistas:
        if pista.fin > numero.inicio or pista.tipo not in ("previa", "etiqueta"):
            continue
        if pista.inicio < frase[0]:
            continue  # every cue speaks for its own sentence only
        if pista.tipo == "previa" and _CORTE_ALCANCE_RE.search(texto_plano, pista.fin, numero.inicio):
            continue  # «según Hipoteca, X; pero Y»: Y is not said to be Hipoteca's
        elegida = pista.especialista  # the latest cue in scope wins
    return elegida


# --------------------------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------------------------


def nombres_de(informes: Sequence[InformeEspecialista]) -> dict[str, str]:
    """Display name of every specialist of the reports (and of the figures they carry, received ones included)."""
    nombres: dict[str, str] = {}
    for informe in informes:
        nombres.setdefault(informe.especialista, informe.nombre or informe.especialista)
    for informe in informes:
        for cifra in informe.cifras:
            nombres.setdefault(cifra.especialista, cifra.especialista.replace("_", " ").capitalize())
    return nombres


def _violacion(
    violaciones: list[Violacion], vistas: set[tuple[str, str, tuple[str, ...]]], violacion: Violacion
) -> None:
    clave = (violacion.tipo, violacion.literal, tuple(violacion.cifras))
    if clave not in vistas:
        vistas.add(clave)
        violaciones.append(violacion)


def revisar_cifras(
    respuesta: str,
    informes: Sequence[InformeEspecialista],
    *,
    ledger: EvidenceLedger,
    textos_usuario: Sequence[str],
) -> ResultadoPuerta:
    """The figures gate's verdict on ``respuesta`` (see the module docstring for each rule and why).

    ``informes``: every report of the turn whose figures the answer may use (the plan's and, when the brain used its
    own tools, a report with ``especialista=CLAVE_CEREBRO`` carrying those tools' figures). ``textos_usuario``: the
    user's messages of the conversation. Deterministic; never calls a model.
    """
    nombres = nombres_de(informes)
    cifras = unicas([c for informe in informes for c in informe.cifras])
    duenos: dict[str, str] = {}
    for informe in informes:
        for fid in informe.fragment_ids:
            duenos.setdefault(fid.upper(), informe.especialista)
    for cifra in cifras:
        if cifra.fragment_id:
            duenos.setdefault(cifra.fragment_id.upper(), cifra.especialista)

    violaciones: list[Violacion] = []
    vistas: set[tuple[str, str, tuple[str, ...]]] = set()
    for fid in extract_cited_ids(respuesta):
        if ledger.get(fid) is None:
            _violacion(
                violaciones,
                vistas,
                Violacion(
                    tipo="cita_inexistente",
                    literal=f"[{fid}]",
                    detalle=f"La cita [{fid}] no corresponde a ningún fragmento de esta consulta.",
                ),
            )

    texto_plano = plano(respuesta)
    frases = _frases(respuesta)
    pistas = _pistas(respuesta, nombres)
    del_usuario = [n for texto in textos_usuario for n in numeros_de(texto)]
    en_fragmento: dict[str, list[Numero]] = {}
    atribuciones: list[AtribucionCifra] = []
    ignorados: list[str] = []
    sin_origen: list[Numero] = []
    sin_cita: list[Numero] = []  # unmatched numbers of sentences without citations: the second pass of rule 4

    def frase_de(numero: Numero) -> tuple[int, int]:
        for frase in frases:
            if frase[0] <= numero.inicio < frase[1]:
                return frase
        return (0, len(respuesta))

    def atribuir(atribucion: AtribucionCifra) -> None:
        if atribucion not in atribuciones:
            atribuciones.append(atribucion)

    for numero in escanear(respuesta):
        if not numero.es_cifra or numero.valor is None:
            etiqueta = f"{numero.literal} ({numero.motivo})"
            if etiqueta not in ignorados:
                ignorados.append(etiqueta)
            continue
        frase = frase_de(numero)
        citados = extract_cited_ids(respuesta[frase[0] : frase[1]])
        candidatas = [c for c in cifras if unidades_compatibles(numero.unidad, c.unidad) and coincide(numero, c.valor)]
        atribuido = _atribuido(numero, frase, pistas, texto_plano)
        if candidatas:
            elegida = _elegir(candidatas, atribuido, citados)
            if atribuido is not None and elegida.especialista != atribuido:
                de_quien = ", ".join(sorted({nombres.get(c.especialista, c.especialista) for c in candidatas}))
                _violacion(
                    violaciones,
                    vistas,
                    Violacion(
                        tipo="cifra_mal_atribuida",
                        literal=numero.literal,
                        detalle=(
                            f"La respuesta atribuye «{numero.literal}» a {nombres.get(atribuido, atribuido)}, pero esa "
                            f"cifra es de {de_quien} ({', '.join(c.id for c in candidatas)})."
                        ),
                        cifras=[c.id for c in candidatas],
                    ),
                )
            atribuir(
                AtribucionCifra(
                    literal=numero.literal,
                    valor=numero.valor,
                    cifra_id=elegida.id,
                    especialista=elegida.especialista,
                    fragment_id=elegida.fragment_id,
                )
            )
            continue
        if any(
            unidades_compatibles(numero.unidad, u.unidad) and coincide(numero, u.valor)
            for u in del_usuario
            if u.valor is not None
        ):
            atribuir(AtribucionCifra(literal=numero.literal, valor=numero.valor, del_usuario=True))
            continue
        fuente = _en_fragmento_citado(numero, citados, ledger, en_fragmento)
        if fuente is not None:
            atribuir(
                AtribucionCifra(
                    literal=numero.literal, valor=numero.valor, fragment_id=fuente, especialista=duenos.get(fuente)
                )
            )
            continue
        if not citados:
            sin_cita.append(numero)
            continue
        sin_origen.append(numero)

    # Rule 4, second pass: a number of a sentence without citations, found in a fragment the answer cites elsewhere.
    citados_respuesta = [fid for fid in extract_cited_ids(respuesta) if ledger.get(fid) is not None]
    for numero in sin_cita:
        fuente = _en_fragmento_citado(numero, citados_respuesta, ledger, en_fragmento)
        if fuente is None or numero.valor is None:  # valor: never None here (figures only reach this list)
            sin_origen.append(numero)
            continue
        atribuir(
            AtribucionCifra(
                literal=numero.literal, valor=numero.valor, fragment_id=fuente, especialista=duenos.get(fuente)
            )
        )
    sin_origen.sort(key=lambda n: n.inicio)

    for orden, numero in enumerate(sin_origen):
        combinacion = buscar_combinacion(numero, cifras) if orden < MAX_NUMEROS_COMBINADOS else None
        _violacion(violaciones, vistas, _clasificar(numero, combinacion, cifras, nombres))
    return ResultadoPuerta(violaciones=violaciones, atribuciones=atribuciones, ignorados=ignorados)


def _elegir(candidatas: Sequence[Cifra], atribuido: str | None, citados: Sequence[str]) -> Cifra:
    """The figure a matched number stands for: the one of the specialist the answer names, else the one whose
    fragment the sentence cites, else the first (plan order)."""
    if atribuido is not None:
        for cifra in candidatas:
            if cifra.especialista == atribuido:
                return cifra
    for cifra in candidatas:
        if cifra.fragment_id and cifra.fragment_id.upper() in citados:
            return cifra
    return candidatas[0]


def _en_fragmento_citado(
    numero: Numero, citados: Sequence[str], ledger: EvidenceLedger, cache: dict[str, list[Numero]]
) -> str | None:
    for fid in citados:
        if fid not in cache:
            fragmento = ledger.get(fid)
            cache[fid] = numeros_de(fragmento.text) + numeros_en_letras(fragmento.text) if fragmento is not None else []
        for literal in cache[fid]:
            if (
                literal.valor is not None
                and unidades_compatibles(numero.unidad, literal.unidad)
                and coincide(numero, literal.valor)
            ):
                return fid
    return None


def _clasificar(
    numero: Numero, combinacion: Combinacion | None, cifras: Sequence[Cifra], nombres: Mapping[str, str]
) -> Violacion:
    if combinacion is not None and combinacion.cruzada:
        quienes = " y ".join(sorted(nombres.get(e, e) for e in combinacion.especialistas))
        return Violacion(
            tipo="suma_no_combinada",
            literal=numero.literal,
            detalle=(
                f"«{numero.literal}» es {combinacion.describir(nombres)}: cifras de especialistas distintos ({quienes}) "
                "que ninguna herramienta ha combinado."
            ),
            cifras=[c.id for _s, c in combinacion.operandos],
        )
    if combinacion is not None:
        return Violacion(
            tipo="cifra_sin_origen",
            literal=numero.literal,
            detalle=(
                f"«{numero.literal}» no lo ha calculado ninguna herramienta: parece {combinacion.describir(nombres)}, "
                "una cuenta hecha a mano."
            ),
            cifras=[c.id for _s, c in combinacion.operandos],
        )
    otra_unidad = [
        c for c in cifras if numero.mantisa is not None and coincide(numero, c.valor) and c.unidad != numero.unidad
    ]
    if otra_unidad:
        cifra = otra_unidad[0]
        return Violacion(
            tipo="cifra_sin_origen",
            literal=numero.literal,
            detalle=(
                f"«{numero.literal}» coincide con {cifra.id} ({nombres.get(cifra.especialista, cifra.especialista)}, "
                f"{cifra.literal or formatear_valor(cifra.valor, cifra.unidad)}) pero con otra unidad."
            ),
            cifras=[cifra.id],
        )
    return Violacion(
        tipo="cifra_sin_origen",
        literal=numero.literal,
        detalle=f"«{numero.literal}» no sale de ningún informe, de ninguna fuente citada ni de lo que dijo el usuario.",
    )


# --------------------------------------------------------------------------------------------------------------
# What the brain is told when the gate fails
# --------------------------------------------------------------------------------------------------------------

_QUE_HACER: dict[str, str] = {
    "suma_no_combinada": (
        "No sumes, restes ni multipliques cifras de especialistas distintos: da cada cifra por separado, diciendo de "
        "quién es y con su [F#]. Si hace falta el total, pídeselo a un especialista con una herramienta que lo calcule."
    ),
    "cifra_sin_origen": (
        "Usa solo cifras de las tablas de los informes (o las que dijo el usuario), tal como están y con su [F#]; si "
        "una cifra no está, quítala o di que no la tienes. No hagas cuentas tú."
    ),
    "cifra_mal_atribuida": "Atribuye cada cifra al especialista que la calculó, según las tablas de los informes.",
    "cita_inexistente": "Cita solo los fragmentos [F#] que aparecen en los informes.",
}
_TITULOS: dict[str, str] = {
    "suma_no_combinada": "Cifras combinadas sin herramienta",
    "cifra_sin_origen": "Cifras sin origen",
    "cifra_mal_atribuida": "Cifras atribuidas a quien no es",
    "cita_inexistente": "Citas que no existen",
}


def mensaje_de_revision(resultado: ResultadoPuerta) -> str:
    """The Spanish message the brain sends back to the model when the gate fails ("" when it passes)."""
    if resultado.ok:
        return ""
    lineas = [
        "La comprobación de cifras ha encontrado problemas en tu respuesta. Corrígelos antes de enviarla:",
    ]
    for tipo in ("suma_no_combinada", "cifra_sin_origen", "cifra_mal_atribuida", "cita_inexistente"):
        del_tipo = [v for v in resultado.violaciones if v.tipo == tipo]
        if not del_tipo:
            continue
        lineas.append("")
        lineas.append(f"{_TITULOS[tipo]}:")
        lineas.extend(f"- {v.detalle}" for v in del_tipo)
        lineas.append(f"Qué hacer: {_QUE_HACER[tipo]}")
    lineas.append("")
    lineas.append(
        "Reescribe la respuesta completa corrigiendo solo estos puntos; no añadas cifras nuevas ni cambies las que "
        "están bien."
    )
    return "\n".join(lineas)


__all__ = [
    "MAX_NUMEROS_COMBINADOS",
    "MAX_OPERANDOS_PARES",
    "MAX_OPERANDOS_TRIOS",
    "Combinacion",
    "buscar_combinacion",
    "mensaje_de_revision",
    "nombres_de",
    "revisar_cifras",
]
