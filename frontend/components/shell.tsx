"use client";
// App shell: nav (t_87bd1163), ambient driver (t_0c977269), keyboard shortcuts (t_4f820f0d).
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { StoreProvider, useStore } from "../lib/store";
import { useAmbient } from "../lib/audio";
import { CostBadge } from "./widgets";

const LINKS: [string, string][] = [
  ["/", "Adventure"],
  ["/character", "Character"],
  ["/inventory", "Inventory"],
  ["/skills", "Skills"],
  ["/journal", "Journal"],
  ["/map", "Map"],
  ["/companions", "Companions"],
  ["/settings", "Settings"],
];

function AmbientDriver() {
  const { ambient, muted } = useStore();
  useAmbient(ambient, muted);
  return null;
}

function Nav() {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // 1–8 jump between pages (advertised in A11yControls).
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      const n = Number(e.key);
      if (n >= 1 && n <= LINKS.length) router.push(LINKS[n - 1][0]);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [router]);
  useEffect(() => setOpen(false), [path]);
  return (
    <nav className={`topnav${open ? " open" : ""}`} aria-label="Chronicle">
      <Link href="/" className="brand">❖ Lorebound</Link>
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
          <Link key={href} href={href} aria-current={path === href ? "page" : undefined}>
            {label}
          </Link>
        ))}
      </span>
      <span style={{ marginLeft: "auto" }}><CostBadge /></span>
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
