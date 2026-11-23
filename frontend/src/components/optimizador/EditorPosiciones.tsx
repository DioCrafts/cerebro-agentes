// The latent positions of «Tus datos»: investments the user could sell before 31 December.

import type { TipoActivo } from "../../apiOptimizador";
import type { Position } from "../../lib/optimizador/campos";
import { TIPO_ACTIVO, reglaRecompra } from "../../lib/optimizador/textos";
import { Button, Caja, FormControl, IconButton, InlineMessage, TextInput } from "../../ui";
import { PlusIcon, TrashIcon } from "../../ui/iconos";
import { CampoCifra, CampoSelector } from "../calculos/piezas";
import type { Optimizador } from "./useOptimizador";

const TITULARES = [
  { value: "contribuyente", label: "Mía" },
  { value: "conyuge", label: "De mi cónyuge" },
];

export function EditorPosiciones({ o }: { o: Optimizador }) {
  const { positions } = o.vista;
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="mb-3 w-full border-b border-border-default pb-2 text-base font-semibold">Inversiones que podrías vender antes del 31 de diciembre</legend>
      <p className="m-0 mb-3 text-sm text-fg-muted">
        Una <strong>pérdida latente</strong> es lo que perderías si vendieras hoy: venderla puede compensar tus ganancias del año. Las ganancias latentes no bajan el impuesto, pero el
        plan te avisa si realizarlas este año te sale gratis.
      </p>
      {positions.length > 0 && (
        <ul className="m-0 mb-3 flex list-none flex-col gap-3 p-0">
          {positions.map((pos, index) => (
            <FilaPosicion key={pos.id} o={o} pos={pos} index={index} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="small" leadingVisual={PlusIcon} onClick={() => o.acciones.addPosition("perdida")} disabled={positions.filter((p) => p.clase === "perdida").length >= 20}>
          Añadir una pérdida latente
        </Button>
        <Button size="small" leadingVisual={PlusIcon} onClick={() => o.acciones.addPosition("ganancia")} disabled={positions.filter((p) => p.clase === "ganancia").length >= 20}>
          Añadir una ganancia latente
        </Button>
      </div>
    </fieldset>
  );
}

function FilaPosicion({ o, pos, index }: { o: Optimizador; pos: Position; index: number }) {
  const { positions } = o.vista;
  const { problems, spouseGiven } = o.peticion;
  const { setPosition, setPositions } = o.acciones;
  const base = `opt-pos-${pos.id}`;
  const n = positions.filter((p, i) => p.clase === pos.clase && i <= index).length;
  const name = `${pos.clase === "perdida" ? "Pérdida" : "Ganancia"} latente ${n}`;
  return (
    <Caja as="li" aria-label={name} condensada>
      <Caja.Cabecera
        titulo={name}
        nivel={4}
        acciones={
          <IconButton
            icon={TrashIcon}
            size="small"
            variant="invisible"
            aria-label={`Quitar la ${name.toLowerCase()}`}
            onClick={() => setPositions((prev) => prev.filter((p) => p.id !== pos.id))}
          />
        }
      />
      <Caja.Cuerpo className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <FormControl id={`${base}-descripcion`}>
            <FormControl.Label>Qué es (opcional)</FormControl.Label>
            <TextInput block value={pos.descripcion} maxLength={200} onChange={(e) => setPosition(pos.id, { descripcion: e.target.value })} placeholder="Fondo indexado, acciones de…" />
          </FormControl>
          <CampoCifra
            id={`${base}-importe`}
            etiqueta={pos.clase === "perdida" ? "Pérdida si vendieras hoy" : "Ganancia si vendieras hoy"}
            valor={pos.importe}
            onCambio={(importe) => setPosition(pos.id, { importe })}
            placeholder="—"
            ayuda="Valor de venta menos lo que te costó, gastos incluidos. En positivo."
            error={problems[`${base}-importe`]}
          />
          <CampoSelector id={`${base}-tipo`} etiqueta="¿Es un valor cotizado?" valor={pos.tipo} onCambio={(tipo) => setPosition(pos.id, { tipo: tipo as TipoActivo })} opciones={TIPO_ACTIVO} />
          {(spouseGiven || pos.titular === "conyuge") && (
            <CampoSelector
              id={`${base}-titular`}
              etiqueta="De quién es"
              valor={pos.titular}
              onCambio={(titular) => setPosition(pos.id, { titular: titular === "conyuge" ? "conyuge" : "contribuyente" })}
              opciones={TITULARES}
              error={problems[`${base}-titular`]}
            />
          )}
        </div>
        {pos.clase === "perdida" && (
          <InlineMessage variant="warning" size="small">
            {reglaRecompra(pos.tipo)}
          </InlineMessage>
        )}
      </Caja.Cuerpo>
    </Caja>
  );
}
