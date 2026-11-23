// First screen of a new consultation: what this is (a Blankslate), four concrete ways to start (a list of
// suggestions, like GitHub's "get started" cards) and how it earns trust.

import { STARTER_QUESTIONS } from "../lib/mock";
import { Blankslate, Caja } from "../ui";
import { ArrowRightIcon, FileIcon, PersonIcon, ShieldCheckIcon } from "../ui/iconos";
import { Monogram } from "./Brand";

const HOW_IT_WORKS = [
  { icon: FileIcon, title: "Busca en la normativa", text: "Leyes del BOE, consultas de la DGT, resoluciones del TEAC y manuales de la AEAT." },
  { icon: ShieldCheckIcon, title: "Comprueba lo que afirma", text: "Cada dato va con su fuente y se verifica contra el texto original antes de mostrártelo." },
  { icon: PersonIcon, title: "Se adapta a tu caso", text: "Recuerda lo que le cuentas y te pregunta lo que le falta en lugar de suponerlo." },
] as const;

export function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-col gap-6">
      <Blankslate spacious>
        <Blankslate.Visual>
          <Monogram size={48} />
        </Blankslate.Visual>
        <Blankslate.Heading as="h2">¿Qué duda fiscal tienes?</Blankslate.Heading>
        <Blankslate.Description>
          Cuéntame tu situación con tus palabras. Te respondo con la normativa aplicable y te digo hasta dónde puedes fiarte de cada
          respuesta.
        </Blankslate.Description>
      </Blankslate>

      <Caja as="section" aria-labelledby="sugerencias">
        <Caja.Cabecera titulo={<span id="sugerencias">Para empezar</span>} nivel={3} />
        <Caja.Lista>
          {STARTER_QUESTIONS.map((q) => (
            <Caja.Fila key={q.title} className="p-0!">
              <button
                type="button"
                onClick={() => onPick(q.prompt)}
                className="group flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-canvas-muted"
              >
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold text-fg-accent">{q.title}</span>
                  <span className="block text-fg-muted">{q.prompt}</span>
                </span>
                <ArrowRightIcon className="mt-1 shrink-0 text-fg-muted" />
              </button>
            </Caja.Fila>
          ))}
        </Caja.Lista>
      </Caja>

      <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-3">
        {HOW_IT_WORKS.map(({ icon: Icon, title, text }) => (
          <li key={title} className="flex gap-3">
            <Icon className="mt-0.5 shrink-0 text-fg-muted" />
            <div>
              <p className="m-0 font-semibold">{title}</p>
              <p className="m-0 text-xs text-fg-muted">{text}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
