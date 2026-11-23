// Composer: GitHub's new-comment form. A text box that grows (Enter sends, Shift+Enter adds a line) and a
// footer with the hint and the primary button; while a turn is running the button stops it.

import { useEffect, useRef, useState } from "react";

import { Button, Textarea } from "../ui";
import { PaperAirplaneIcon, SquareFillIcon } from "../ui/iconos";

interface ComposerProps {
  onSubmit: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  // Text to start with (e.g. the question restored after the user pressed stop).
  // The parent remounts the composer (via `key`) to apply a new one.
  initialValue?: string;
}

const MAX_HEIGHT_PX = 220;

export function Composer({ onSubmit, onStop, busy, initialValue = "" }: ComposerProps) {
  const [value, setValue] = useState(initialValue);
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    onSubmit(trimmed);
    setValue("");
  }

  return (
    <form
      aria-label="Nueva consulta"
      className="rounded-medium border border-border-default bg-canvas-default"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="p-2">
        <Textarea
          ref={ref}
          id="consulta"
          block
          rows={2}
          resize="none"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Escribe tu consulta fiscal…"
          aria-label="Escribe tu consulta"
          aria-describedby="composer-ayuda"
          style={{ maxHeight: MAX_HEIGHT_PX }}
        />
      </div>
      <div className="flex items-center gap-2 px-2 pb-2">
        <p id="composer-ayuda" className="m-0 min-w-0 flex-1 text-xs text-fg-muted">
          <span className="hidden sm:inline">Intro para enviar · Mayús + Intro para salto de línea · </span>
          Puede equivocarse: contrasta lo importante con un profesional.
        </p>
        {busy ? (
          <Button type="button" onClick={onStop} leadingVisual={SquareFillIcon}>
            Detener
          </Button>
        ) : (
          <Button type="submit" variant="primary" disabled={!value.trim()} leadingVisual={PaperAirplaneIcon}>
            Preguntar
          </Button>
        )}
      </div>
    </form>
  );
}
