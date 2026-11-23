// A conversation in GitHub's style: the timeline line, comments (avatar, header, body, footer) and the
// small events between them.
//
//   <Conversacion aria-label="Conversación">
//     <Comentario autor="Tú" accion="preguntó" cuando="hace 2 min" avatar={<Iniciales texto="Tú" />} variante="propio">
//       …
//     </Comentario>
//     <EventoConversacion icono={SyncIcon}>HaciendaGPT está respondiendo…</EventoConversacion>
//   </Conversacion>

import type { Icon } from "@primer/octicons-react";
import { clsx } from "clsx";
import type { HTMLAttributes, ReactNode } from "react";

import estilos from "./Conversacion.module.css";

export function Conversacion({ className, ...resto }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx(estilos.conversacion, className)} {...resto} />;
}

interface ComentarioProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  /** Who wrote it (bold in the header). */
  autor: string;
  /** What they did, after the name: "preguntó", "respondió". */
  accion?: ReactNode;
  /** When (already formatted, e.g. relativeTime()). */
  cuando?: ReactNode;
  /** The avatar (40px) shown left of the box on wide screens, and a 20px one in the header on narrow ones. */
  avatar: (tamano: number) => ReactNode;
  /** Labels after the time (Primer `Label`, `StateLabel`…). */
  etiquetas?: ReactNode;
  /** Buttons at the right of the header (IconButton, ActionMenu). */
  acciones?: ReactNode;
  /** `propio`: written by the user (accent header, as GitHub marks the author); `error`: a failed answer. */
  variante?: "normal" | "propio" | "error";
  /** Below the body, over a thin line (activity, feedback). */
  pie?: ReactNode;
}

export function Comentario({ autor, accion, cuando, avatar, etiquetas, acciones, variante = "normal", pie, className, children, ...resto }: ComentarioProps) {
  return (
    <article className={clsx(estilos.comentario, className)} {...resto}>
      <div className={estilos.avatar} aria-hidden>
        {avatar(40)}
      </div>
      <div className={clsx(estilos.caja, variante !== "normal" && estilos[variante])}>
        <div className={estilos.cabecera}>
          <span className={estilos.avatarEnLinea} aria-hidden>
            {avatar(20)}
          </span>
          <h3 className={estilos.encabezado}>
            <span className={estilos.autor}>{autor}</span>
            {accion && <> {accion}</>}
            {cuando && <> · {cuando}</>}
          </h3>
          {etiquetas}
          {acciones && <div className={estilos.acciones}>{acciones}</div>}
        </div>
        <div className={estilos.cuerpo}>{children}</div>
        {pie && <div className={estilos.pie}>{pie}</div>}
      </div>
    </article>
  );
}

interface EventoProps extends HTMLAttributes<HTMLDivElement> {
  icono: Icon;
  /** `acento`: a filled badge for an event that is happening now. */
  acento?: boolean;
}

export function EventoConversacion({ icono: Icono, acento = false, className, children, ...resto }: EventoProps) {
  return (
    <div className={clsx(estilos.evento, className)} {...resto}>
      <span className={clsx(estilos.insignia, acento && estilos.insigniaAcento)}>
        <Icono size={14} />
      </span>
      <div>{children}</div>
    </div>
  );
}
