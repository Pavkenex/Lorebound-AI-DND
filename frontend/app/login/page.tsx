"use client";
// Sign in: register + login against POST /auth/*, Bearer token in localStorage.
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { loginApi, registerApi, setToken, getToken } from "../../lib/api";
import { uiBlip } from "../../lib/audio";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(() => (typeof window !== "undefined" ? !!getToken() : false));

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      if (mode === "login") {
        const t = await loginApi(email.trim(), password);
        setToken(t.access_token);
      } else {
        const r = await registerApi(email.trim(), password, displayName.trim() || email.split("@")[0]);
        setToken(r.token.access_token);
      }
      uiBlip(740);
      setSignedIn(true);
      router.push("/saves");
    } catch {
      setError(
        mode === "login"
          ? "Sign-in failed — check the email and password, or try while the backend is awake."
          : "Registration failed — the email may be taken, the password too short (8+), or the backend unreachable."
      );
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    setToken(null);
    setSignedIn(false);
  }

  if (signedIn) {
    return (
      <div style={{ maxWidth: 520, margin: "0 auto", padding: 16 }}>
        <h1>Signed in</h1>
        <p className="sys">Your saves travel with the Bearer token kept in this browser.</p>
        <div style={{ display: "flex", gap: 8 }}>
          <Link className="btn" href="/saves" prefetch>Open saves</Link>
          <button className="btn btn-ghost" onClick={signOut}>Sign out</button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 520, margin: "0 auto", padding: 16 }}>
      <p className="sys"><Link href="/">Menu</Link></p>
      <h1>{mode === "login" ? "Sign in" : "Join the chronicle"}</h1>
      <p className="sys">
        {mode === "login"
          ? "Your saves belong to your account — sign in to carry them."
          : "One account, many tales. Passwords need 8+ characters."}
      </p>
      {error && <div className="error-banner" role="alert"><strong>The gate held —</strong> {error}</div>}
      <form onSubmit={onSubmit} className="parchment card" style={{ display: "grid", gap: 10 }}>
        {mode === "register" && (
          <div>
            <label htmlFor="display">Display name</label>
            <input id="display" className="input-parch" value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="nickname" maxLength={120} />
          </div>
        )}
        <div>
          <label htmlFor="email">Email</label>
          <input id="email" className="input-parch" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        </div>
        <div>
          <label htmlFor="pw">Password</label>
          <input id="pw" className="input-parch" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </div>
        <button className="btn" type="submit" disabled={busy || !email || !password}>
          {busy ? "At the gate…" : mode === "login" ? "Sign in" : "Register"}
        </button>
      </form>
      <p className="sys" style={{ marginTop: 10 }}>
        {mode === "login" ? (
          <>New to the road? <button className="entity" onClick={() => setMode("register")}>Register instead</button>.</>
        ) : (
          <>Already sworn? <button className="entity" onClick={() => setMode("login")}>Sign in instead</button>.</>
        )}
      </p>
    </div>
  );
}
