"use client";
// Global game store: settings, a11y, audio, cost metering, tutorial (t_4f820f0d, t_eff821f1, t_d2dd38a0, t_32d887a4).
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { defaultPrefs, normalizeContentPrefs, type ContentPrefs } from "./store-types";
import type { CostInfo } from "./api";

export type Ambient = "off" | "tavern" | "rain" | "forest" | "combat";

interface Prefs {
  fontSize: number; // px base
  highContrast: boolean;
  reducedMotion: boolean;
  hideArtwork: boolean;
  compactNarration: boolean;
  content: ContentPrefs;
  ambient: Ambient;
  muted: boolean;
  tutorialDone: boolean;
}

interface Store extends Prefs {
  set: (p: Partial<Prefs>) => void;
  totalCalls: number;
  totalCost: number;
  lastCached: boolean;
  addCost: (c: CostInfo) => void;
}

const Ctx = createContext<Store | null>(null);
const KEY = "lorebound-prefs-v1";

const DEFAULTS: Prefs = {
  fontSize: 16, highContrast: false, reducedMotion: false,
  hideArtwork: false, compactNarration: false, content: defaultPrefs,
  ambient: "off", muted: false, tutorialDone: false,
};

function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw), content: normalizeContentPrefs(JSON.parse(raw).content) };
  } catch { /* fixture default */ }
  return DEFAULTS;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  // First render is deterministic (matches the server markup); persisted prefs
  // load in an effect AFTER hydration. Reading localStorage during hydration
  // leaves controlled inputs (checkbox/select) showing stale SSR state until
  // an unrelated re-render patches them — the NSFW toggle was caught lying.
  const [prefs, setPrefs] = useState<Prefs>(DEFAULTS);
  const [hydrated, setHydrated] = useState(false);
  const [totalCalls, setCalls] = useState(0);
  const [totalCost, setCost] = useState(0);
  const [lastCached, setCached] = useState(true);

  useEffect(() => {
    setPrefs(load());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return; // never clobber stored prefs before they are loaded
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
    document.body.style.setProperty("--fs", `${prefs.fontSize}px`);
    document.body.classList.toggle("hc", prefs.highContrast);
    document.body.classList.toggle("rm", prefs.reducedMotion);
  }, [prefs, hydrated]);

  const value = useMemo<Store>(() => ({
    ...prefs,
    set: (p) => setPrefs((s) => ({ ...s, ...p })),
    totalCalls, totalCost, lastCached,
    addCost: (c) => { setCalls((n) => n + c.calls); setCost((n) => n + c.costUsd); setCached(c.cached); },
  }), [prefs, totalCalls, totalCost, lastCached]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}
