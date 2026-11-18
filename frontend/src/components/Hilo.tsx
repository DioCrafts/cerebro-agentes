// The conversation on screen: each question with its verified answer (sources, claim-by-claim check,
// obligations, activity, feedback), and the turn still running. Moved out of App.tsx unchanged.

import { Check, Copy, RotateCcw, TriangleAlert } from "lucide-react";
import type { RefObject } from "react";
import { useState } from "react";

import type { PendingTurn } from "../shell/useConsulta";
import type { ChatTurn, Conversation } from "../lib/storage";
import { verificationSummary } from "../lib/presentation";
import { ActivitySummary, LiveActivity } from "./ActivityPanel";
import { AssistantBubble, UserBubble } from "./ChatBubbles";
import { DoctrineNotice } from "./DoctrineNotice";
import { EmptyState } from "./EmptyState";
import { EvaluacionFeedback } from "./EvaluacionFeedback";
import { ObligationCardList } from "./ObligationCard";
import { ConsultadosLinea, PlanEnVivo, ProcedenciaRespuesta, type TurnoConCerebro } from "./ProcedenciaRespuesta";
import { RichText } from "./RichText";
import { SourceCardList } from "./SourceCard";
import { TrustStrip } from "./TrustStrip";
import { VerificationPanel } from "./VerificationPanel";

// Warnings the backend derives from individual claims; those are shown in the
// claim-by-claim panel, so they are not repeated as loose banners.
const CLAIM_WARNING_PREFIXES = ["Sin respaldo en la fuente citada", "Solo respaldada en parte"];

function obligationKey(turn: ChatTurn | undefined): string {
  return (turn?.obligations ?? []).map((o) => o.obligation_id).sort().join("|");
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
          })
          .catch(() => undefined);
      }}
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px] font-medium text-muted transition hover:bg-line/60 hover:text-ink-soft"
    >
      {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
      {copied ? "Copiada" : "Copiar respuesta"}
    </button>
  );
}

interface HiloProps {
  active: Conversation;
  pending: PendingTurn | null;
  busy: boolean;
  highlightedId: string | null;
  onSubmit: (text: string, replaceTurnId?: string) => void;
  onCite: (turnId: string, fragmentId: string) => void;
  lastTurnRef: RefObject<HTMLElement | null>;
  bottomRef: RefObject<HTMLDivElement | null>;
}

export function Hilo({ active, pending, busy, highlightedId, onSubmit, onCite, lastTurnRef, bottomRef }: HiloProps) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-7">
      {active.turns.length === 0 && !pending && <EmptyState onPick={(prompt) => onSubmit(prompt)} />}

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
            <UserBubble text={t.userText} />
            <AssistantBubble>
              {t.errored ? (
                <div role="alert" className="rounded-xl border border-risk-high-fg/30 bg-risk-high-bg px-4 py-3.5">
                  <p className="flex items-center gap-2 text-[14.5px] font-bold text-risk-high-fg">
                    <TriangleAlert size={17} aria-hidden />
                    No he podido responder
                  </p>
                  <p className="mt-1 text-[13.5px] leading-snug text-ink-soft">{t.envelope.answer}</p>
                  <p className="mt-1 text-[12.5px] text-muted">No se ha generado ninguna respuesta a partir de la normativa.</p>
                  <button
                    type="button"
                    onClick={() => onSubmit(t.userText, t.id)}
                    disabled={busy}
                    className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-surface px-3 py-1.5 text-[13px] font-semibold text-ink shadow-card transition hover:bg-line/60 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RotateCcw size={14} aria-hidden />
                    Reintentar
                  </button>
                </div>
              ) : (
                <>
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
                  <div className="mt-3 flex flex-wrap items-start justify-between gap-x-4">
                    <ActivitySummary rows={t.activity} elapsedMs={t.elapsedMs} />
                    <div className="mt-2">
                      <CopyButton text={t.envelope.answer} />
                    </div>
                  </div>
                  <EvaluacionFeedback key={t.id} caseId={active.caseId} question={t.userText} answer={t.envelope.answer} />
                </>
              )}
            </AssistantBubble>
          </section>
        );
      })}

      {pending && (
        <section className="flex flex-col gap-4">
          <UserBubble text={pending.text} />
          <AssistantBubble>
            <PlanEnVivo eventos={pending.cerebro ?? []} />
            <LiveActivity events={pending.events} startedAt={pending.startedAt} draft={pending.draft} />
          </AssistantBubble>
        </section>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
