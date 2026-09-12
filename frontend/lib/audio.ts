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

/** Dice clatter — quick wooden ticks as the die tumbles (t_4ae64d0a, no assets). */
export function diceClatter() {
  try {
    const c = ac();
    const now = c.currentTime;
    for (let i = 0; i < 7; i++) {
      const t0 = now + i * 0.06 + Math.random() * 0.03;
      const o = c.createOscillator(); o.type = "square";
      o.frequency.value = 1150 + Math.random() * 1500;
      const g = c.createGain(); g.gain.value = 0;
      o.connect(g); g.connect(c.destination);
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.025, t0 + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.05);
      o.start(t0); o.stop(t0 + 0.06);
    }
  } catch { /* muted or blocked — the die still lands */ }
}

/** Soft wooden thud as the die settles onto the table. */
export function diceSettleSound() {
  try {
    const c = ac();
    const t0 = c.currentTime;
    const o = c.createOscillator(); o.type = "sine"; o.frequency.value = 165;
    const g = c.createGain(); g.gain.value = 0;
    o.connect(g); g.connect(c.destination);
    g.gain.setValueAtTime(0, t0);
    g.gain.linearRampToValueAtTime(0.09, t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.22);
    o.start(t0); o.stop(t0 + 0.24);
    const o2 = c.createOscillator(); o2.type = "triangle"; o2.frequency.value = 720;
    const g2 = c.createGain(); g2.gain.value = 0;
    o2.connect(g2); g2.connect(c.destination);
    g2.gain.setValueAtTime(0, t0);
    g2.gain.linearRampToValueAtTime(0.03, t0 + 0.004);
    g2.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.09);
    o2.start(t0); o2.stop(t0 + 0.1);
  } catch { /* muted or blocked */ }
}

/** Critical stingers (t_a1b7e3ae): rising gold chime for a nat 20, dire thud for a nat 1. */
export function critStinger(kind: "crit-success" | "crit-miss") {
  try {
    const c = ac();
    const t0 = c.currentTime;
    const note = (freq: number, at: number, type: OscillatorType, vol: number, decay: number) => {
      const o = c.createOscillator(); o.type = type; o.frequency.value = freq;
      const g = c.createGain(); g.gain.value = 0;
      o.connect(g); g.connect(c.destination);
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(vol, at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, at + decay);
      o.start(at); o.stop(at + decay + 0.05);
    };
    if (kind === "crit-success") {
      [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => note(f, t0 + i * 0.09, "triangle", 0.07, 0.45));
    } else {
      [220, 174.61, 130.81].forEach((f, i) => note(f, t0 + i * 0.13, "sawtooth", 0.045, 0.4));
      note(60, t0, "sine", 0.11, 0.5); // sub thump
    }
  } catch { /* muted or blocked */ }
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
