"use client";
// App shell: nav (t_87bd1163), ambient driver (t_0c977269), keyboard shortcuts (t_4f820f0d).
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { StoreProvider, useStore } from "../lib/store";
import { useAmbient } from "../lib/audio";
import { getToken } from "../lib/api";
import { engineModeEnabled } from "../lib/engine";
import { CostBadge } from "./widgets";

const LINKS: [string, string][] = [
  ["/", "Menu"],
  ["/adventure", "Adventure"],
  ["/saves", "Saves"],
  ["/character", "Character"],
  ["/inventory", "Inventory"],
  ["/skills", "Skills"],
  ["/journal", "Journal"],
  ["/map", "Map"],
  ["/companions", "Companions"],
  ["/tutorial", "Tutorial"],
  ["/settings", "Settings"],
];
// The engine pilot shows up only when the deploy switches it on (plan §7);
// appended last so the 1–9,0 shortcuts keep pointing where they did.
if (engineModeEnabled()) LINKS.push(["/chronicle", "Chronicle"]);

function AmbientDriver() {
  const { ambient, muted } = useStore();
  useAmbient(ambient, muted);
  return null;
}

function AccountLink() {
  const path = usePathname();
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => setSignedIn(!!getToken()), [path]);
  return (
    <Link href="/login" prefetch title={signedIn ? "Your account and saves" : "Sign in or register"}>
      {signedIn ? "Account" : "Sign in"}
    </Link>
  );
}

function Nav() {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // 1–9,0 jump between pages (advertised in A11yControls).
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      const order = "1234567890";
      const idx = order.indexOf(e.key);
      if (idx >= 0 && idx < LINKS.length) router.push(LINKS[idx][0]);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [router]);
  useEffect(() => setOpen(false), [path]);
  return (
    <nav className={`topnav${open ? " open" : ""}`} aria-label="Chronicle">
      <Link href="/" className="brand" prefetch>❖ Lorebound</Link>
      <button
        className="nav-toggle btn btn-ghost"
        aria-expanded={open}
        aria-label={open ? "Collapse navigation" : "Expand navigation"}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "✕" : "☰"}
      </button>
      <span className="nav-links">
        {LINKS.map(([href, label]) => (
          <Link key={href} href={href} prefetch aria-current={path === href ? "page" : undefined}>
            {label}
          </Link>
        ))}
      </span>
      <span style={{ marginLeft: "auto", display: "inline-flex", gap: 12, alignItems: "center" }}>
        <AccountLink />
        <CostBadge />
      </span>
    </nav>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <StoreProvider>
      <AmbientDriver />
      <a className="skip-link" href="#main">Skip to the chronicle</a>
      <Nav />
      <main id="main">{children}</main>
    </StoreProvider>
  );
}
