// PaletaComandos: GitHub's command palette. A dialog with a search box (combobox) over a filtered list of
// commands grouped by kind. ↑/↓ move, Enter runs, Escape closes (focus goes back to what opened it).
//
//   <PaletaComandos open={open} onClose={…} comandos={[{ id: "nueva", grupo: "Acciones", etiqueta: "Nueva consulta", icono: PlusIcon, ejecutar: … }]} />

import { SearchIcon } from "@primer/octicons-react";
import { Dialog, TextInput } from "@primer/react";
import type { KeyboardEvent, RefObject } from "react";
import { useId, useMemo, useRef, useState } from "react";

import estilos from "./PaletaComandos.module.css";
import { type Comando, filtrarComandos } from "./comandos";

interface PaletaComandosProps {
  open: boolean;
  onClose: () => void;
  comandos: Comando[];
  returnFocusRef?: RefObject<HTMLElement | null>;
}

export function PaletaComandos({ open, onClose, comandos, returnFocusRef }: PaletaComandosProps) {
  if (!open) return null;
  return <Paleta onClose={onClose} comandos={comandos} returnFocusRef={returnFocusRef} />;
}

function Paleta({ onClose, comandos, returnFocusRef }: Omit<PaletaComandosProps, "open">) {
  const [consulta, setConsulta] = useState("");
  const [activo, setActivo] = useState(0);
  const campo = useRef<HTMLInputElement | null>(null);
  const idLista = useId();
  const visibles = useMemo(() => filtrarComandos(comandos, consulta), [comandos, consulta]);
  const indice = Math.min(activo, Math.max(visibles.length - 1, 0));
  const idOpcion = (i: number) => `${idLista}-${i}`;

  function ejecutar(c: Comando | undefined) {
    if (!c) return;
    onClose();
    c.ejecutar();
  }

  function teclado(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") setActivo((indice + 1) % Math.max(visibles.length, 1));
    else if (e.key === "ArrowUp") setActivo((indice - 1 + visibles.length) % Math.max(visibles.length, 1));
    else if (e.key === "Enter") ejecutar(visibles[indice]);
    else return;
    e.preventDefault();
  }

  return (
    <Dialog
      title="Buscar o ejecutar un comando"
      onClose={onClose}
      width="xlarge"
      align="top"
      initialFocusRef={campo}
      returnFocusRef={returnFocusRef}
      renderBody={() => (
        <div>
          <div className={estilos.campo}>
            <TextInput
              ref={campo}
              block
              leadingVisual={SearchIcon}
              placeholder="Escribe para filtrar: avisos, simulador, nueva consulta…"
              value={consulta}
              onChange={(e) => {
                setConsulta(e.target.value);
                setActivo(0);
              }}
              onKeyDown={teclado}
              role="combobox"
              aria-label="Buscar un comando"
              aria-expanded="true"
              aria-controls={idLista}
              aria-autocomplete="list"
              aria-activedescendant={visibles.length > 0 ? idOpcion(indice) : undefined}
            />
          </div>
          {visibles.length === 0 ? (
            <p className={estilos.vacio} role="status">
              Nada coincide con «{consulta}».
            </p>
          ) : (
            <ul id={idLista} role="listbox" aria-label="Comandos" className={estilos.lista}>
              {visibles.map((c, i) => {
                const cabecera = i === 0 || visibles[i - 1]?.grupo !== c.grupo ? c.grupo : null;
                const Icono = c.icono;
                return (
                  <li key={c.id} role="presentation">
                    {cabecera && (
                      <div className={estilos.grupo} aria-hidden>
                        {cabecera}
                      </div>
                    )}
                    {/* Mouse only: the keyboard works from the search box (aria-activedescendant). */}
                    {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events */}
                    <div
                      id={idOpcion(i)}
                      role="option"
                      aria-selected={i === indice}
                      tabIndex={-1}
                      className={estilos.opcion}
                      onMouseMove={() => setActivo(i)}
                      onClick={() => ejecutar(c)}
                    >
                      <span className={estilos.icono}>
                        <Icono size={16} />
                      </span>
                      <span className={estilos.texto}>
                        {c.etiqueta}
                        {c.descripcion && <span className={estilos.descripcion}>{c.descripcion}</span>}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    />
  );
}
