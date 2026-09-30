// Client-held identity, not a real session. There is no backend session/JWT layer yet
// (architecture v2 §8) -- every request that needs numbers still asks the server for them
// fresh (GET /dashboard), so this never gates data access, it only remembers *who the
// dashboard is personalized for* across a page reload. Do not treat this as auth.

import type { PersonOut } from "./types";

const KEY = "milieu.session.person";

export function loadSession(): PersonOut | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as PersonOut) : null;
  } catch {
    return null;
  }
}

export function saveSession(person: PersonOut): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(person));
  } catch {
    // ignore -- private browsing / storage blocked; the person just has to re-enter email
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
