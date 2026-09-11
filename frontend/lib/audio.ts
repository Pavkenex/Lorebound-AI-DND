"use client";
// WebAudio ambient + UI sounds, no assets. Fully playable muted (t_0c977269).
// tavern: warm filtered noise + low murmur LFO; rain: bright noise wash;
// forest: slow-chirping bandpass; combat: low pulse drum + tension drone.
import { useEffect, useRef } from "react";
import type { Ambient } from "./store";

let ctx: AudioContext | null = null;
let nodes: AudioNode[] = [];
let timer: ReturnType<typeof setInterval> | null = null;

function ac(): AudioContext {
  if (!ctx) ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function noiseBuffer(c: AudioContext): AudioBuffer {
  const b = c.createBuffer(1, c.sampleRate * 2, c.sampleRate);
  const d = b.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
  return b;
}

function stop() {
  if (timer) { clearInterval(timer); timer = null; }
  for (const n of nodes) { try { (n as AudioScheduledSourceNode).stop?.(); } catch { /* */ } try { n.disconnect(); } catch { /* */ } }
  nodes = [];
}

export function startAmbient(kind: Exclude<Ambient, "off">) {
  stop();
  const c = ac();
  const master = c.createGain();
  master.gain.value = 0.12;
  master.connect(c.destination);
  nodes.push(master);
  const nb = noiseBuffer(c);

  const loop = (filterType: BiquadFilterType, freq: number, q: number, gain: number) => {
    const src = c.createBufferSource();
    src.buffer = nb; src.loop = true;
    const f = c.createBiquadFilter(); f.type = filterType; f.frequency.value = freq; f.Q.value = q;
    const g = c.createGain(); g.gain.value = gain;
    src.connect(f); f.connect(g); g.connect(master);
    src.start();
    nodes.push(src, f, g);
    return g;
  };

  if (kind === "rain") {
    loop("highpass", 2500, 0.6, 0.5);
    loop("lowpass", 900, 0.4, 0.35);
  } else if (kind === "tavern") {
    const g = loop("lowpass", 420, 0.5, 0.8);
    const lfo = c.createOscillator(); lfo.frequency.value = 0.13;
    const lg = c.createGain(); lg.gain.value = 0.25;
    lfo.connect(lg); lg.connect(g.gain); lfo.start();
    nodes.push(lfo, lg);
    loop("bandpass", 1200, 2, 0.12);
  } else if (kind === "forest") {
    loop("bandpass", 3000, 3, 0.10);
    loop("lowpass", 500, 0.5, 0.5);
    // sparse chirps
    timer = setInterval(() => {
      try {
        const o = c.createOscillator(); o.type = "sine";
        o.frequency.value = 2400 + Math.random() * 1600;
        const g = c.createGain(); g.gain.value = 0;
        o.connect(g); g.connect(master);
        const t = c.currentTime;
        g.gain.linearRampToValueAtTime(0.05, t + 0.08);
        g.gain.linearRampToValueAtTime(0, t + 0.3);
        o.start(t); o.stop(t + 0.35);
      } catch { /* */ }
    }, 2600);
  } else if (kind === "combat") {
    const drone = c.createOscillator(); drone.type = "sawtooth"; drone.frequency.value = 55;
    const df = c.createBiquadFilter(); df.type = "lowpass"; df.frequency.value = 160;
    const dg = c.createGain(); dg.gain.value = 0.5;
    drone.connect(df); df.connect(dg); dg.connect(master); drone.start();
    nodes.push(drone, df, dg);
    timer = setInterval(() => {
      try {
        const o = c.createOscillator(); o.frequency.value = 70;
        const g = c.createGain(); g.gain.value = 0.6;
        o.connect(g); g.connect(master);
        const t = c.currentTime;
        g.gain.exponentialRampToValueAtTime(0.001, t + 0.28);
        o.start(t); o.stop(t + 0.3);
      } catch { /* */ }
    }, 620);
  }
}

export function stopAmbient() { stop(); }

export function uiBlip(freq = 660) {
  try {
    const c = ac();
    const o = c.createOscillator(); o.type = "triangle"; o.frequency.value = freq;
    const g = c.createGain(); g.gain.value = 0.06;
    o.connect(g); g.connect(c.destination);
    const t = c.currentTime;
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
    o.start(t); o.stop(t + 0.13);
  } catch { /* muted or blocked — game continues */ }
}

/** React hook: drive ambient from store state. */
export function useAmbient(ambient: Ambient, muted: boolean) {
  const ref = useRef<Ambient>("off");
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (muted || ambient === "off") { stopAmbient(); ref.current = "off"; return; }
    if (ref.current !== ambient) { ref.current = ambient; startAmbient(ambient); }
    return () => { /* keep playing across screens */ };
  }, [ambient, muted]);
}
