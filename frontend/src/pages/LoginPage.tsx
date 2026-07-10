import { LockKeyhole } from "lucide-react";
import { FormEvent, useState } from "react";

import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const { login, sessionExpired, clearSessionExpired } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);
    clearSessionExpired();

    try {
      await login(username, password);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel">
        <div className="login-icon" aria-hidden="true">
          <LockKeyhole size={24} />
        </div>
        <span className="eyebrow">Relay Operations</span>
        <h1>Admin login</h1>
        <p>Access the operational dashboard for events, retries and dead letter queue handling.</p>

        {sessionExpired ? <div className="inline-alert inline-alert-warning">Session expired. Sign in again.</div> : null}
        {error ? <div className="inline-alert inline-alert-error">{error}</div> : null}

        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>Username</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
          </label>
          <label>
            <span>Password</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              placeholder="relay_admin"
            />
          </label>
          <button className="command-button primary-command" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
