// The conversation on screen, as a GitHub conversation (ui/Conversacion): each question is the user's
// comment and each verified answer the advisor's (sources, claim-by-claim check, obligations, activity,
// feedback), on the timeline line; the turn still running is an event plus a comment that fills in live
// (it subscribes to the turn on its own).

import type { RefObject } from "react";
import { useEffect, useState } from "react";

import { type AlmacenTurno, useTurnoEnCurso } from "../shell/turnoEnCurso";
import type { ChatTurn, Conversation } from "../lib/storage";
import { verificationSummary } from "../lib/presentation";
import { ActivitySummary, LiveActivity } from "./ActivityPanel";
import { Monogram } from "./Brand";
import { ConsejoEnHilo } from "./ConvocarConsejo";
import { DoctrineNotice } from "./DoctrineNotice";
import { EvaluacionFeedback } from "./EvaluacionFeedback";
import { ObligationCardList } from "./ObligationCard";
import { ConsultadosLinea, PlanEnVivo, ProcedenciaRespuesta, type TurnoConCerebro } from "./ProcedenciaRespuesta";
import { RichText } from "./RichText";
import { SourceCardList } from "./SourceCard";
import { TrustStrip } from "./TrustStrip";
import { VerificationPanel } from "./VerificationPanel";
import { AlertIcon, CheckIcon, CopyIcon, SyncIcon } from "../ui/iconos";
import { Button, Comentario, Conversacion, EventoConversacion, IconButton, Iniciales } from "../ui";

// Warnings the backend derives from individual claims; those are shown in the
// claim-by-claim panel, so they are not repeated as loose banners.
const CLAIM_WARNING_PREFIXES = ["Sin respaldo en la fuente citada", "Solo respaldada en parte"];

/** The last question that got an answer (to offer it to the council), if any. */
function ultimaPregunta(turns: ChatTurn[]): string | undefined {
  const ultimo = turns.at(-1);
  return ultimo && !ultimo.errored ? ultimo.userText : undefined;
}

function obligationKey(turn: ChatTurn | undefined): string {
  return (turn?.obligations ?? []).map((o) => o.obligation_id).sort().join("|");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      icon={copied ? CheckIcon : CopyIcon}
      aria-label={copied ? "Respuesta copiada" : "Copiar la respuesta"}
      variant="invisible"
      size="small"
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
          })
          .catch(() => undefined);
      }}
    />
  );
}

const avatarUsuario = (tamano: number) => <Iniciales texto="Tú" tamano={tamano} />;
const avatarAsesor = (tamano: number) => <Monogram size={tamano} />;

function segundos(ms: number): string {
  return ms >= 1000 ? `en ${Math.round(ms / 1000)} s` : "";
}

interface HiloProps {
  active: Conversation;
  /** The turn still running (only its live bubble subscribes to it). */
  turno: AlmacenTurno;
  busy: boolean;
  highlightedId: string | null;
  onSubmit: (text: string, replaceTurnId?: string) => void;
  onCite: (turnId: string, fragmentId: string) => void;
  lastTurnRef: RefObject<HTMLElement | null>;
  bottomRef: RefObject<HTMLDivElement | null>;
}

export function Hilo({ active, turno, busy, highlightedId, onSubmit, onCite, lastTurnRef, bottomRef }: HiloProps) {
  // An empty conversation shows the home page (Portada, in the shell) instead of the thread.
  if (active.turns.length === 0 && !busy) return null;
  return (
    <Conversacion aria-label="Conversación">
      {active.turns.map((t, index) => {
        const claimsSummary = verificationSummary(t.claims);
        const otherWarnings = (t.envelope.warnings ?? []).filter(
          (w) => t.claims.length === 0 || !CLAIM_WARNING_PREFIXES.some((prefix) => w.startsWith(prefix)),
        );
        // Obligations belong to the case, not the turn: show them only
        // when this turn changed them, not under every answer.
        const obligationsChanged = obligationKey(t) !== obligationKey(active.turns[index - 1]);
        return (
          <section
            key={t.id}
            ref={index === active.turns.length - 1 ? lastTurnRef : undefined}
            className="flex scroll-mt-4 flex-col gap-4"
            aria-label={`Consulta: ${t.userText}`}
          >
            <Comentario autor="Tú" accion="preguntaste" avatar={avatarUsuario} variante="propio">
              <p className="m-0 whitespace-pre-wrap wrap-break-word">{t.userText}</p>
            </Comentario>
            {t.errored ? (
              <Comentario autor="Cerebro" accion="no ha podido responder" avatar={avatarAsesor} variante="error">
                <div role="alert">
                  <p className="m-0 flex items-center gap-2 font-semibold text-fg-danger">
                    <AlertIcon />
                    {t.envelope.answer}
                  </p>
                  <p className="mb-3 mt-1 text-fg-muted">No se ha generado ninguna respuesta a partir de la normativa.</p>
                  <Button onClick={() => onSubmit(t.userText, t.id)} disabled={busy} leadingVisual={SyncIcon}>
                    Reintentar
                  </Button>
                </div>
              </Comentario>
            ) : (
              <Comentario
                autor="Cerebro"
                accion={`respondió ${segundos(t.elapsedMs)}`.trim()}
                avatar={avatarAsesor}
                acciones={<CopyButton text={t.envelope.answer} />}
                pie={<EvaluacionFeedback key={t.id} caseId={active.caseId} question={t.userText} answer={t.envelope.answer} />}
              >
                <RichText text={t.envelope.answer} citations={t.envelope.citations} onCite={(fragmentId) => onCite(t.id, fragmentId)} />
                <ConsultadosLinea cerebro={(t as TurnoConCerebro).cerebro} />
                <TrustStrip mode={t.envelope.mode} detail={claimsSummary} />
                <ProcedenciaRespuesta
                  cerebro={(t as TurnoConCerebro).cerebro}
                  citations={t.envelope.citations}
                  onCite={(fragmentId) => onCite(t.id, fragmentId)}
                />
                <DoctrineNotice conflicts={t.envelope.conflicts ?? []} unsettled={t.envelope.sinCriterioFirme ?? false} />
                <VerificationPanel claims={t.claims} otherWarnings={otherWarnings} onCite={(fragmentId) => onCite(t.id, fragmentId)} />
                <SourceCardList citations={t.envelope.citations} scope={t.id} highlightedId={highlightedId} />
                {obligationsChanged && <ObligationCardList obligations={t.obligations} />}
                <ActivitySummary rows={t.activity} elapsedMs={t.elapsedMs} />
              </Comentario>
            )}
          </section>
        );
      })}

      {/* The household council: convocations waiting for the user, sessions running, and the offer on a hard decision. */}
      <ConsejoEnHilo
        caseId={active.caseId}
        turnos={active.turns.length}
        ultimaPregunta={ultimaPregunta(active.turns)}
        ocupado={busy}
      />

      <TurnoPendiente turno={turno} bottomRef={bottomRef} />
      <div ref={bottomRef} />
    </Conversacion>
  );
}

/** The turn still running. The only part of the thread that re-renders as the answer streams in. */
function TurnoPendiente({ turno, bottomRef }: { turno: AlmacenTurno; bottomRef: RefObject<HTMLDivElement | null> }) {
  const pending = useTurnoEnCurso(turno);
  // Follow the progress at the bottom. The draft grows token by token: re-scroll only every ~120 characters.
  const activo = pending !== null;
  const eventos = pending?.events.length ?? 0;
  const tramo = Math.floor((pending?.draft.length ?? 0) / 120);
  useEffect(() => {
    if (activo) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activo, eventos, tramo, bottomRef]);
  if (!pending) return null;
  return (
    <section className="flex flex-col gap-4" aria-label={`Consulta en curso: ${pending.text}`}>
      <Comentario autor="Tú" accion="preguntaste" avatar={avatarUsuario} variante="propio">
        <p className="m-0 whitespace-pre-wrap wrap-break-word">{pending.text}</p>
      </Comentario>
      <EventoConversacion icono={SyncIcon} acento>
        <span className="font-semibold text-fg-default">Cerebro</span> está buscando en la normativa y comprobando la respuesta…
      </EventoConversacion>
      <Comentario autor="Cerebro" accion="está respondiendo" avatar={avatarAsesor} aria-busy="true">
        <PlanEnVivo eventos={pending.cerebro ?? []} />
        <LiveActivity events={pending.events} startedAt={pending.startedAt} draft={pending.draft} />
      </Comentario>
    </section>
  );
}
