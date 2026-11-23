// «No me interesa» / «Ya lo hice» / «Recuérdamelo más tarde» on a notice or on a lever of the optimiser's plan. The
// answer is remembered (asistente/nucleo/memoria): the assistant will not propose it again unless something
// important changes, and then only once, saying what changed.
//
// An ActionMenu «Tu respuesta» (GitHub's menu on a comment); the chosen answer opens a small form below it to add a
// reason or pick when to be reminded, and nothing is saved until «Guardar».

import { useState } from "react";

import { crearDecision, responderAviso, type BotonAviso, type DecisionResponse } from "../apiMemoria";
import { errorMessage, isoEnDias } from "../lib/memoria";
import { ActionList, ActionMenu, Button, Flash, FormControl, Select, TextInput } from "../ui";
import type { Icon } from "../ui/iconos";
import { CheckIcon, ClockIcon, ReplyIcon, ThumbsdownIcon } from "../ui/iconos";

interface Palanca {
  id: string;
  opcion?: string | null;
  titulo: string;
  importe?: string | null;
}

interface MemoriaBotonesProps {
  caseId: string;
  /** A notice of the inbox… */
  notificationId?: string;
  /** …or a lever of the optimiser's plan (no «remind me later»: the plan is computed on demand). */
  palanca?: Palanca;
  onDone: (resp: DecisionResponse) => void;
}

const BOTONES: Array<{ boton: BotonAviso; label: string; icon: Icon }> = [
  { boton: "no_me_interesa", label: "No me interesa", icon: ThumbsdownIcon },
  { boton: "ya_lo_hice", label: "Ya lo hice", icon: CheckIcon },
  { boton: "recuerdamelo", label: "Recuérdamelo más tarde", icon: ClockIcon },
];

const PLAZOS: Array<{ dias: number; label: string }> = [
  { dias: 7, label: "dentro de una semana" },
  { dias: 30, label: "dentro de un mes" },
  { dias: 90, label: "dentro de tres meses" },
];

export function MemoriaBotones({ caseId, notificationId, palanca, onDone }: MemoriaBotonesProps) {
  const [abierto, setAbierto] = useState<BotonAviso | null>(null);
  const [razon, setRazon] = useState("");
  const [dias, setDias] = useState(7);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const botones = palanca ? BOTONES.filter((b) => b.boton !== "recuerdamelo") : BOTONES;
  const elegido = BOTONES.find((b) => b.boton === abierto);

  async function guardar() {
    if (!abierto) return;
    setEnviando(true);
    setError(null);
    try {
      const texto = razon.trim() || undefined;
      const resp = notificationId
        ? await responderAviso(caseId, notificationId, {
            boton: abierto,
            razon: texto,
            hasta: abierto === "recuerdamelo" ? isoEnDias(dias) : undefined,
          })
        : await crearDecision(caseId, {
            palanca: palanca?.id,
            opcion: palanca?.opcion ?? null,
            boton: abierto === "ya_lo_hice" ? "ya_lo_hice" : "no_me_interesa",
            razon: texto,
            propuesta: palanca ? { titulo: palanca.titulo, importe: palanca.importe ?? null } : undefined,
          });
      setAbierto(null);
      setRazon("");
      onDone(resp);
    } catch (err) {
      setError(`No se ha podido guardar. ${errorMessage(err)}`);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <ActionMenu>
          <ActionMenu.Button size="small" leadingVisual={elegido?.icon ?? ReplyIcon}>
            {elegido ? elegido.label : "Tu respuesta"}
          </ActionMenu.Button>
          <ActionMenu.Overlay width="medium">
            <ActionList selectionVariant="single" aria-label="Tu respuesta a esta propuesta">
              {botones.map(({ boton, label, icon: Icono }) => (
                <ActionList.Item key={boton} selected={abierto === boton} onSelect={() => setAbierto(boton)}>
                  <ActionList.LeadingVisual>
                    <Icono />
                  </ActionList.LeadingVisual>
                  {label}
                </ActionList.Item>
              ))}
            </ActionList>
          </ActionMenu.Overlay>
        </ActionMenu>
      </div>
      {abierto && (
        <form
          aria-label={elegido?.label}
          className="flex flex-col gap-2 rounded-medium border border-border-default bg-canvas-muted p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void guardar();
          }}
        >
          {abierto === "recuerdamelo" ? (
            <FormControl>
              <FormControl.Label>Vuelve a avisarme</FormControl.Label>
              <Select size="small" value={String(dias)} onChange={(e) => setDias(Number(e.target.value))}>
                {PLAZOS.map((p) => (
                  <Select.Option key={p.dias} value={String(p.dias)}>
                    {p.label}
                  </Select.Option>
                ))}
              </Select>
              <FormControl.Caption>Hasta entonces no te lo volveré a proponer. Puedes cambiarlo en «Lo que sé de tus preferencias».</FormControl.Caption>
            </FormControl>
          ) : (
            <FormControl>
              <FormControl.Label>{abierto === "no_me_interesa" ? "¿Por qué? (opcional, con tus palabras)" : "¿Algo que deba saber? (opcional)"}</FormControl.Label>
              <TextInput
                block
                size="small"
                value={razon}
                maxLength={300}
                onChange={(e) => setRazon(e.target.value)}
                placeholder={abierto === "no_me_interesa" ? "p. ej. tengo permanencia hasta marzo" : ""}
              />
              <FormControl.Caption>
                {abierto === "no_me_interesa"
                  ? "No te lo volveré a proponer durante un año, salvo que cambie algo importante (y entonces solo una vez, diciéndote qué ha cambiado)."
                  : "Lo tendré en cuenta y no te lo volveré a proponer en los próximos meses."}{" "}
                Puedes cambiarlo en «Lo que sé de tus preferencias».
              </FormControl.Caption>
            </FormControl>
          )}
          <div className="flex gap-2">
            <Button type="submit" size="small" variant="primary" disabled={enviando} loading={enviando}>
              Guardar
            </Button>
            <Button size="small" onClick={() => setAbierto(null)}>
              Cancelar
            </Button>
          </div>
        </form>
      )}
      {error && (
        <Flash variant="danger" role="alert">
          {error}
        </Flash>
      )}
    </div>
  );
}
