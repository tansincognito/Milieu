// "tan@tenet.gmail.com logs in, it checks I'm in engineering, brings me the dashboard"
// (pasted product spec). This resolves against the real `people` table (GET
// /people/resolve) -- not a fake mapping -- but there is no password/SSO/session layer
// behind it yet (architecture v2 §8 names Google SSO / magic-link as the real plan). Typing
// a known directory email and pressing Enter is the honest stand-in for that today.

import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { saveSession } from "../session";

const EXAMPLE_EMAILS = [
  "priya.shah@ourcompany.com",
  "sam.rivera@ourcompany.com",
  "jordan.lee@ourcompany.com",
  "morgan.ellis@ourcompany.com",
];

export function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const person = await api.resolvePerson(email.trim());
      saveSession(person);
      navigate("/me");
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.status === 404
            ? `No directory entry for ${email.trim()}. Try one of the examples below.`
            : e.message
          : String(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card glass">
        <div className="login-brand">Milieu</div>
        <h1>Sign in</h1>
        <p className="muted">
          We'll look up your team from the directory and bring you the dashboard for your
          part of the org.
        </p>
        <form onSubmit={submit}>
          <input
            className="login-input"
            type="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
          />
          <button className="btn primary login-submit" type="submit" disabled={busy}>
            {busy ? "Checking…" : "Continue"}
          </button>
        </form>
        {error && <div className="error">{error}</div>}
        <div className="login-examples">
          <span className="muted">Try:</span>
          {EXAMPLE_EMAILS.map((e) => (
            <button key={e} type="button" className="login-chip" onClick={() => setEmail(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
