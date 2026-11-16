// The conversation the web app sends with each question (docs/seguridad.md, S-15). The whole history is re-sent
// on every step of the agent, so it is what multiplies the cost of one question. The server trims it with the
// same rule (API_MAX_HISTORY_MESSAGES / API_MAX_HISTORY_CHARS in hacienda_gpt/api/security.py); trimming here
// too keeps the request small.

export const MAX_HISTORY_MESSAGES = 40;
export const MAX_HISTORY_CHARS = 60_000;

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

/** The newest messages within both limits, oldest dropped first, never cut in half, starting with a question. */
export function trimHistory<T extends HistoryMessage>(history: readonly T[]): T[] {
  const kept: T[] = [];
  let total = 0;
  for (let i = history.length - 1; i >= 0; i--) {
    total += history[i].content.length;
    if (kept.length >= MAX_HISTORY_MESSAGES || total > MAX_HISTORY_CHARS) break;
    kept.push(history[i]);
  }
  kept.reverse();
  while (kept.length > 0 && kept[0].role !== "user") kept.shift();
  return kept;
}
