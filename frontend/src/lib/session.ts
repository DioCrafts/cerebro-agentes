// The signed-in session on this browser (only used when the backend runs with AUTH_MODE=required).
//
// The session token lives in an HttpOnly cookie set by the server: this script never reads, stores or
// sends it by hand, so an injected script cannot steal it either. What stays in localStorage is not
// secret: whose conversation history this is, and the last auth mode the backend reported.
//
// Older versions kept the token itself in localStorage: `tomarTokenAntiguo` takes it out (once) so the
// app can revoke it and ask the user to sign in again, which sets the cookie.

const LEGACY_TOKEN_KEY = "haciendagpt_session_token";
const OWNER_KEY = "haciendagpt_history_owner";
const MODE_KEY = "haciendagpt_auth_mode";

export type AuthMode = "off" | "required";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage disabled: nothing is remembered between reloads.
  }
}

/** The token an older version left in localStorage, removed from there now; null when there was none. */
export function tomarTokenAntiguo(): string | null {
  const token = read(LEGACY_TOKEN_KEY);
  if (token !== null) write(LEGACY_TOKEN_KEY, null);
  return token && token.trim() ? token.trim() : null;
}

/**
 * The conversation history in localStorage belongs to whoever was signed in
 * when it was written: the id of that account, or null when it predates accounts.
 */
export function historyOwner(): string | null {
  return read(OWNER_KEY);
}

export function setHistoryOwner(userId: string | null): void {
  write(OWNER_KEY, userId);
}

/** Last auth mode the backend reported, so a reload does not flash the wrong screen. */
export function cachedAuthMode(): AuthMode | null {
  const mode = read(MODE_KEY);
  return mode === "off" || mode === "required" ? mode : null;
}

export function cacheAuthMode(mode: AuthMode): void {
  write(MODE_KEY, mode);
}
