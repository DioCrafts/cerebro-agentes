// Simulator: change a figure, see the tax change. Everything here is the
// deterministic calculator (POST /simulate) — no model call, so it answers as
// you type. The saved case is the baseline; edits are compared against it.
//
// Shown as a page (registro.ts: presentacion "pagina"): the shell puts the title and the frame. On wide
// screens the result and the ways to pay less sit in a sidebar that follows the form, like a repository's
// «About» column; on phones the result comes first.

import { Fragment } from "react";

import type { Benefit, Fact } from "../api";
import { parseAmount } from "../lib/formulario";
import type { ObligacionDeclarar } from "../lib/obligacion";
import { COMUNIDADES, formatMoney } from "../lib/presentation";
import { FIELDS, type FieldDef, LEVER_PRESET, TRIBUTACION, gruposDeComunidad } from "../lib/simulador/campos";
import { AvisoError, Button, Caja, CargandoBloque, Flash, Label } from "../ui";
import { HistoryIcon, LightBulbIcon } from "../ui/iconos";
import { CampoCasilla, CampoCifra, CampoSelector, Plegable, Resultado, SeccionFormulario, TablaCifras } from "./calculos/piezas";
import { ObligacionDeclararCard } from "./ObligacionDeclararCard";
import { EditorDescendientes } from "./simulador/EditorDescendientes";
import { type SimulatorSeed, useSimulacion } from "./simulador/useSimulacion";

export type { SimulatorSeed };

interface SimulatorPanelProps {
  /** The page is on screen: the calculator only runs while it is. */
  open: boolean;
  caseId: string | null;
  facts: Fact[];
  taxPeriod: string | null;
  // Persist edited figures to the case (null removes the fact). Resolves true when saved.
  onSaveFact: (factName: string, value: number | string | boolean | null) => Promise<boolean>;
  busy: boolean;
  seed?: SimulatorSeed | null;
}

function ayudaComunidad(comunidad: string): string {
  if (comunidad === "Madrid") return "Se aplican la escala, los mínimos y varias deducciones de Madrid.";
  if (comunidad === "Otra") {
    return "Los territorios forales tienen su propio impuesto y Ceuta y Melilla una deducción específica: aquí la parte autonómica es solo una aproximación.";
  }
  if (comunidad) return "Se aplican la escala, los mínimos y las deducciones más habituales de tu comunidad (abajo).";
  return "Sin comunidad, la parte autonómica se aproxima con la escala estatal.";
}

export function SimulatorPanel({ open, caseId, facts, taxPeriod, onSaveFact, busy, seed = null }: SimulatorPanelProps) {
  const s = useSimulacion({ open, caseId, facts, taxPeriod, onSaveFact, seed });
  const { state, acciones, baseline, derived, value, partsSum, totalProblem, invalid, cambios, changed, comunidad, year } = s;
  const { edits, tributacion, childrenEdit, openParts, simulation, error, saving, saveError } = state;
  const groups = gruposDeComunidad(comunidad);

  const base = simulation?.escenarios[0];
  const sim = simulation?.escenarios[1] ?? base;
  const resultado = sim ? Number(sim.resultado.resultado) : 0;
  const ahorro = simulation?.escenarios[1] ? Number(simulation.escenarios[1].ahorro_frente_a_base) : 0;
  const levers = (simulation?.beneficios ?? []).filter((b) => b.estado === "disponible" || b.estado === "por_confirmar");
  // Whether the user must file (art. 96 LIRPF): computed by the backend with each individual result.
  const obligacion = sim ? ((sim.resultado as { obligacion_declarar?: ObligacionDeclarar | null }).obligacion_declarar ?? null) : null;
  const hasIncome = FIELDS.some((f) => (edits[f.name] ?? baseline[f.name] ?? "") !== "" && f.name.startsWith("rendimiento"));

  function lever(benefit: Benefit) {
    const preset = LEVER_PRESET[benefit.id];
    return (
      <Caja.Fila key={benefit.id} className="flex flex-col gap-1 text-sm">
        <div className="flex items-start justify-between gap-2">
          <p className="m-0 font-semibold">{benefit.nombre}</p>
          {benefit.ahorro_estimado && Number(benefit.ahorro_estimado) > 0 && <Label variant="success">hasta −{formatMoney(benefit.ahorro_estimado)}</Label>}
        </div>
        <p className="m-0">{benefit.detalle}</p>
        <p className="m-0 text-xs text-fg-muted">
          {benefit.referencia}
          {!benefit.verificado && " · cifras pendientes de contrastar"}
        </p>
        {preset && (
          <div className="mt-1">
            <Button size="small" onClick={() => acciones.setEdit(preset.field, preset.value)}>
              Probar en la simulación
            </Button>
          </div>
        )}
      </Caja.Fila>
    );
  }

  function renderField(field: FieldDef) {
    const current = value(field.name);
    const dirty = field.name in cambios;
    if (field.kind === "flag") {
      return (
        <CampoCasilla
          key={field.name}
          etiqueta={field.label}
          ayuda={field.hint}
          marcada={current === "true"}
          onCambio={(marcada) => acciones.setEdit(field.name, String(marcada))}
          cambiado={dirty}
        />
      );
    }
    const fromChildren = field.name in derived;
    const sum = partsSum(field);
    const problem = totalProblem(field);
    const autoTotal = !!(field.parts || field.sumOf) && sum > 0 && !(parseAmount(current) ?? 0);
    const ayuda = fromChildren
      ? "Sale del detalle de cada hijo."
      : autoTotal
        ? field.sumOf
          ? `Vacío, se toma la suma de los indicados en «Familia»: ${sum}.`
          : `Sin total, se toma la suma del desglose: ${formatMoney(sum)}.`
        : field.hint;
    const input = (
      <CampoCifra
                etiqueta={field.label}
        tipo={field.kind === "money" ? "dinero" : field.kind === "year" ? "ano" : "numero"}
        valor={current}
        onCambio={(raw) => acciones.setEdit(field.name, raw)}
        soloLectura={fromChildren}
        maxLength={field.kind === "year" ? 4 : undefined}
        placeholder={autoTotal ? String(Math.round(sum * 100) / 100).replace(".", ",") : field.kind === "year" ? "AAAA" : "0"}
        ayuda={ayuda}
        error={problem}
        cambiado={dirty}
      />
    );
    if (!field.parts) return input;
    const isOpen = openParts[field.name] ?? (sum > 0 || problem !== null);
    return (
      <div key={field.name} className="flex flex-col gap-2 sm:col-span-2">
        {input}
        <Plegable
          abierto={isOpen}
          onAlternar={() => acciones.setDesglose(field.name, !isOpen)}
          titulo={`Desglose por tipo${sum > 0 ? ` · ${formatMoney(sum)}` : " (opcional)"}`}
        >
          <div className="grid gap-4 sm:grid-cols-2">{field.parts.map(renderField)}</div>
          <p className="m-0 mt-3 text-xs text-fg-muted">Algunas deducciones solo admiten una parte de estos gastos. Cada importe va en una sola casilla.</p>
        </Plegable>
      </div>
    );
  }

  const resultadoCaja = sim && (
    <Resultado
      titulo={`Resultado estimado${simulation ? ` · IRPF ${simulation.ejercicio}` : ""}`}
      cifra={formatMoney(Math.abs(resultado))}
      sufijo={resultado > 0 ? "a pagar" : resultado < 0 ? "a devolver" : undefined}
      favorable={resultado <= 0}
    >
      <p className="m-0 text-sm text-fg-muted">
        Cuota del año: {formatMoney(sim.resultado.cuota_liquida)} · tipo medio {sim.resultado.tipo_medio_efectivo.replace(".", ",")} %
      </p>
      {invalid ? (
        <Flash variant="danger" role="status">
          Hay datos que no cuadran (marcados en rojo). Este resultado no incluye tus últimos cambios.
        </Flash>
      ) : (
        changed && (
          <Flash variant={ahorro > 0 ? "success" : ahorro < 0 ? "warning" : "default"}>
            <strong>
              {ahorro > 0 ? `Con estos cambios pagas ${formatMoney(ahorro)} menos` : ahorro < 0 ? `Con estos cambios pagas ${formatMoney(-ahorro)} más` : "Estos cambios no alteran el resultado"}
            </strong>
            {base && <> (antes: {formatMoney(base.resultado.resultado)})</>}
          </Flash>
        )
      )}
      {!hasIncome && <p className="m-0 text-sm text-fg-muted">Introduce al menos tu salario bruto para obtener una estimación.</p>}
    </Resultado>
  );

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <aside aria-label="Resultado y formas de pagar menos" className="flex min-w-0 flex-col gap-4 lg:sticky lg:top-4 lg:order-2">
        {error !== null && !invalid && (
          <AvisoError frase="No he podido calcular la simulación." detalle={error} onReintentar={acciones.reintentar}>
            {simulation && <p className="m-0 text-xs">El resultado es el del último cálculo válido.</p>}
          </AvisoError>
        )}
        {seed?.note && changed && (
          <Flash role="status" variant="default">
            {seed.note}
          </Flash>
        )}
        {resultadoCaja}
        {/* Until the first result: its place, at its height, so the form does not jump when it arrives. */}
        {!sim && error === null && <CargandoBloque alto="8.25rem" etiqueta="Calculando tu declaración…" />}
        {levers.length > 0 && (
          <Caja as="section" aria-labelledby="sim-palancas">
            <Caja.Cabecera>
              <LightBulbIcon className="text-fg-muted" />
              <h3 id="sim-palancas" className="m-0 flex-1 text-sm font-semibold">
                Formas de pagar menos
              </h3>
            </Caja.Cabecera>
            <Caja.Lista>{levers.map(lever)}</Caja.Lista>
          </Caja>
        )}
      </aside>

      <div className="flex min-w-0 flex-col gap-6">
        {obligacion && hasIncome && (
          <ObligacionDeclararCard
            obligacion={obligacion}
            value={(name) => edits[name] ?? baseline[name] ?? ""}
            onChange={acciones.setEdit}
            dirty={(name) => name in cambios}
          />
        )}

        {groups.map((group) => (
          <SeccionFormulario key={group.title} titulo={group.title}>
            {group.fields.map((field) => (
              <Fragment key={field.name}>{renderField(field)}</Fragment>
            ))}
            {group.title === "Familia" && (
              <>
                <EditorDescendientes rows={s.children} problems={s.childProblems} year={year} brokenSaved={s.savedChildren.broken && childrenEdit === null} onChange={acciones.setChildren} />
                <CampoSelector
                  etiqueta="Comunidad autónoma"
                  valor={comunidad}
                  onCambio={acciones.setComunidad}
                  opciones={COMUNIDADES}
                  ayuda={ayudaComunidad(comunidad)}
                  className="sm:col-span-2"
                />
                <CampoSelector
                  etiqueta="Tipo de declaración"
                  valor={tributacion}
                  onCambio={acciones.setTributacion}
                  opciones={TRIBUTACION}
                  ayuda={tributacion !== "individual" ? "En conjunta, las cifras deben sumar las rentas de toda la unidad familiar." : undefined}
                  className="sm:col-span-2"
                />
              </>
            )}
          </SeccionFormulario>
        ))}

        {changed && (
          <div className="flex flex-col gap-2 border-t border-border-default pt-4">
            <div className="flex flex-wrap gap-2">
              {caseId && (
                <Button variant="primary" onClick={() => void acciones.saveAll()} disabled={saving || busy || invalid} loading={saving}>
                  Guardar en mi expediente
                </Button>
              )}
              <Button leadingVisual={HistoryIcon} onClick={acciones.deshacer}>
                Deshacer cambios
              </Button>
            </div>
            {saveError && (
              <Flash variant="danger" role="alert">
                {saveError}
              </Flash>
            )}
          </div>
        )}

        {sim && (
          <Plegable titulo="Desglose del cálculo" sinRelleno>
            <TablaCifras
              titulo="Desglose del cálculo"
              filas={sim.resultado.lineas.map((line) => ({ concepto: line.concepto, referencia: line.referencia, cifras: [formatMoney(line.importe)] }))}
            />
          </Plegable>
        )}

        {sim && sim.resultado.avisos.length > 0 && (
          <Plegable tono="aviso" abiertoAlInicio titulo={`Qué no incluye esta estimación (${sim.resultado.avisos.length})`}>
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5">
              {sim.resultado.avisos.map((aviso, i) => (
                <li key={i}>{aviso}</li>
              ))}
            </ul>
          </Plegable>
        )}

        <p className="m-0 text-xs text-fg-muted">
          Calculado con fórmulas fijas y las reglas del año, sin inteligencia artificial. No sustituye al borrador de la Agencia Tributaria.
          {sim && <span className="block">{sim.resultado.fuente}.</span>}
        </p>
      </div>
    </div>
  );
}
