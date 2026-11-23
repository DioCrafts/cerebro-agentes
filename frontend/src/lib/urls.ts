// Addresses that come from the server (sources of a report, the mail consent link) are checked before
// the browser follows them: a `javascript:` or `data:` link must never reach an href or a redirect.

/** The address if it is http(s), otherwise null. */
export function enlaceWeb(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Whether the address is Google's consent screen (the only place «Conectar Gmail» may send the user). */
export function esConsentimientoGoogle(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "accounts.google.com" && parsed.username === "" && parsed.password === "";
  } catch {
    return false;
  }
}
