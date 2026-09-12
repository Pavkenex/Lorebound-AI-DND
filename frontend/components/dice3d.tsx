"use client";
// The 3D die (t_06f54e57 renderer, t_4ae64d0a roll, t_a1b7e3ae crits).
//
// A real icosahedral d20 drawn on a 2D canvas from projected 3D geometry —
// no WebGL, no dependencies. It tumbles with decaying angular velocity and
// settles exactly on the rolled face. Natural 20s get a golden burst, natural
// 1s a dire slam with shake and embers. Quiet when prefers-reduced-motion or
// the reducedMotion setting is on: the die just shows its result.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../lib/store";
import { critStinger, diceClatter, diceSettleSound } from "../lib/audio";
import {
  buildFxPlan,
  buildRollPlan,
  cameraTilt,
  d20Mesh,
  isCrit,
  orientForFace,
  projectPoint,
  quatMul,
  rollKind,
  sampleRoll,
  visibleFaces,
  type FxPlan,
  type Quat,
  type RollKind,
  type RollPlan,
} from "../lib/dice3d";

const GOLD_HI = "232, 199, 102";
const BLOOD = "160, 52, 38";

interface Scene {
  size: number;
  dpr: number;
  quat: Quat;
  hop: number;
  kind: RollKind | null;
  fx: FxPlan | null;
  fxT: number | null;
  shakeX: number;
}

function faceFill(shade: number, kind: RollKind | null, fxOn: boolean): string {
  const l = 24 + shade * 40;
  if (kind === "crit-success") return `hsl(46, 64%, ${Math.min(66, l + 6)}%)`;
  if (kind === "crit-miss") return `hsl(30, ${fxOn ? 36 : 24}%, ${Math.max(20, l - 8)}%)`;
  if (kind === "fail") return `hsl(40, 28%, ${l}%)`;
  return `hsl(42, 55%, ${l}%)`;
}

/** Draw one complete frame: table, die, and any critical flourish. */
function drawScene(ctx: CanvasRenderingContext2D, scene: Scene) {
  const { size, dpr, kind, fx, fxT } = scene;
  const mesh = d20Mesh();
  const view = { width: size, height: size, zoom: 0.165, distance: 4.4 };
  const cx = size / 2;
  const cy = size * 0.55;
  const t = fxT == null ? 0 : Math.min(1, fxT);
  const crit = fx != null && fxT != null && t < 1;
  const fade = crit ? Math.pow(1 - t, 1.2) : 0;

  ctx.clearRect(0, 0, size, size);

  // Warm pool of light under the die (stronger while a critical plays).
  const glowRgb = crit && fx!.kind === "crit-miss" ? BLOOD : GOLD_HI;
  const glow = ctx.createRadialGradient(cx, cy + size * 0.16, size * 0.02, cx, cy + size * 0.16, size * 0.55);
  glow.addColorStop(0, `rgba(${glowRgb}, ${crit ? 0.2 : 0.12})`);
  glow.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, size, size);

  const shake = scene.shakeX;
  const dy = -scene.hop * size * 0.42;
  const pop = crit && fx!.kind === "crit-success" ? 1 + 0.05 * Math.sin(Math.PI * Math.min(1, t * 1.6)) : 1;

  // Contact shadow: soft, dark when low; larger and fainter mid-hop.
  const shScale = 1 - scene.hop * 0.5;
  ctx.save();
  ctx.globalAlpha = Math.max(0.1, 0.55 * (1 - scene.hop * 0.85));
  ctx.translate(cx + shake * 0.4, size * 0.82);
  ctx.scale(1, 0.26);
  const sh = ctx.createRadialGradient(0, 0, 0, 0, 0, size * 0.26 * shScale);
  sh.addColorStop(0, "rgba(0, 0, 0, 0.9)");
  sh.addColorStop(0.6, "rgba(0, 0, 0, 0.55)");
  sh.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = sh;
  ctx.beginPath();
  ctx.arc(0, 0, size * 0.26 * shScale, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();

  // Critical flash + rays glow behind the die (additive light).
  if (crit) {
    const slowFade = Math.pow(1 - t, 0.9);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    const flash = ctx.createRadialGradient(cx, cy + dy, size * 0.03, cx, cy + dy, size * 0.62);
    flash.addColorStop(0, `rgba(${glowRgb}, ${(fx!.flashAlpha * slowFade).toFixed(3)})`);
    flash.addColorStop(0.55, `rgba(${glowRgb}, ${(fx!.flashAlpha * slowFade * 0.35).toFixed(3)})`);
    flash.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = flash;
    ctx.fillRect(0, 0, size, size);

    // Hot white flare right at the moment of impact.
    if (fx!.kind === "crit-success" && t < 0.22) {
      const hot = ((0.22 - t) / 0.22) * 0.5;
      const flare = ctx.createRadialGradient(cx, cy + dy, 0, cx, cy + dy, size * 0.34);
      flare.addColorStop(0, `rgba(255, 246, 214, ${hot.toFixed(3)})`);
      flare.addColorStop(1, "rgba(255, 246, 214, 0)");
      ctx.fillStyle = flare;
      ctx.fillRect(0, 0, size, size);
    }

    if (fx!.rays > 0) {
      const rot = (fx!.particles[0]?.angle ?? 0) + t * 0.55;
      ctx.save();
      ctx.translate(cx, cy + dy);
      // Long thin spokes radiating out from behind the die, reaching toward the edges.
      ctx.strokeStyle = `rgba(${GOLD_HI}, ${(0.75 * slowFade).toFixed(3)})`;
      ctx.lineWidth = size * 0.009;
      for (let i = 0; i < fx!.rays; i++) {
        const a = rot + (i * Math.PI * 2) / fx!.rays;
        const r0 = size * 0.3;
        const r1 = Math.min(size * 0.52, r0 + size * (0.18 + 0.16 * Math.sin(t * Math.PI)));
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
        ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.stroke();
      }
      // Short thick inner spokes, offset half a step.
      ctx.strokeStyle = `rgba(${GOLD_HI}, ${(0.55 * slowFade).toFixed(3)})`;
      ctx.lineWidth = size * 0.022;
      for (let i = 0; i < fx!.rays; i++) {
        const a = rot + ((i + 0.5) * Math.PI * 2) / fx!.rays;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * size * 0.22, Math.sin(a) * size * 0.22);
        ctx.lineTo(Math.cos(a) * size * 0.34, Math.sin(a) * size * 0.34);
        ctx.stroke();
      }
      // Expanding ring around the die.
      ctx.beginPath();
      ctx.strokeStyle = `rgba(${GOLD_HI}, ${(0.6 * slowFade).toFixed(3)})`;
      ctx.lineWidth = size * 0.018;
      ctx.arc(0, 0, size * (0.35 + t * 0.16), 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    ctx.restore();
  }

  // The die itself, painter order.
  const rot = quatMul(cameraTilt(), scene.quat);
  const faces = visibleFaces(mesh, rot);
  const projected = faces.map((f) => ({
    face: f,
    pts: f.points.map((p) => projectPoint(p, view)) as [readonly [number, number], readonly [number, number], readonly [number, number]],
  }));

  ctx.save();
  ctx.translate(cx + shake, cy + dy);
  if (pop !== 1) ctx.scale(pop, pop);
  if (crit) {
    const slowFadeGlow = Math.pow(1 - t, 0.9);
    // Critical light bleeds onto the die itself.
    ctx.shadowColor = fx!.kind === "crit-success"
      ? `rgba(240, 208, 118, ${(0.85 * slowFadeGlow).toFixed(3)})`
      : `rgba(180, 60, 44, ${(0.8 * slowFadeGlow).toFixed(3)})`;
    ctx.shadowBlur = size * (fx!.kind === "crit-success" ? 0.14 : 0.09) * slowFadeGlow * dpr;
  }

  for (const { face, pts } of projected) {
    const [a, b, c] = pts;
    ctx.beginPath();
    ctx.moveTo(a[0] - cx, a[1] - cy);
    ctx.lineTo(b[0] - cx, b[1] - cy);
    ctx.lineTo(c[0] - cx, c[1] - cy);
    ctx.closePath();
    ctx.fillStyle = faceFill(face.shade, kind, crit);
    ctx.fill();
    if (crit && fx!.kind === "crit-success") {
      // Golden rim light around every facet.
      ctx.strokeStyle = `rgba(255, 226, 140, ${(0.8 * fade).toFixed(3)})`;
      ctx.lineWidth = size * 0.012;
    } else if (crit && fx!.kind === "crit-miss") {
      ctx.strokeStyle = `rgba(170, 58, 44, ${(0.75 * fade).toFixed(3)})`;
      ctx.lineWidth = size * 0.01;
    } else {
      ctx.strokeStyle = "rgba(24, 14, 4, 0.5)";
      ctx.lineWidth = size * 0.006;
    }
    ctx.stroke();

    // Engraved number: affine-mapped onto the projected face, fading out as
    // the face turns edge-on (like real dice you can't quite read).
    const area = Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
    const areaFrac = area / (size * size);
    if (areaFrac > 0.04) {
      const numAlpha = Math.min(1, (areaFrac - 0.04) / 0.03);
      ctx.save();
      ctx.globalAlpha = numAlpha;
      ctx.transform(b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1], a[0] - cx, a[1] - cy);
      ctx.translate(1 / 3, 1 / 3);
      ctx.rotate(-Math.PI / 4);
      ctx.font = '700 0.36px Georgia, "Times New Roman", serif';
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = kind === "crit-success" && crit ? "rgba(64, 38, 2, 0.98)" : "rgba(28, 18, 6, 0.92)";
      ctx.fillText(String(face.number), 0, 0);
      ctx.restore();
    }
  }
  ctx.restore();

  // Sparks / embers in front of the die.
  if (crit) {
    const dieR = size * 0.165 * 1.9;
    const scale = size / 128;
    ctx.save();
    if (fx!.kind === "crit-success") ctx.globalCompositeOperation = "lighter";
    for (const p of fx!.particles) {
      const lifeT = Math.min(1, (t * fx!.durationMs) / p.life);
      if (lifeT >= 1) continue;
      const ease = 1 - Math.pow(1 - lifeT, 3);
      const dist = p.speed * dieR * 1.5 * ease;
      const dirx = Math.cos(p.angle);
      const diry = Math.sin(p.angle);
      let px = cx + dirx * dist;
      let py = cy + dy + diry * dist;
      if (fx!.kind === "crit-miss") py += Math.pow(lifeT, 1.5) * size * 0.3; // embers sink
      const alpha = Math.pow(1 - lifeT, 0.75);
      const r = p.size * scale * (1 + lifeT * 0.5);
      if (fx!.kind === "crit-success") {
        // Motion streak + bright head.
        const streak = dieR * 0.28 * p.speed;
        ctx.strokeStyle = `rgba(${GOLD_HI}, ${(alpha * 0.6).toFixed(3)})`;
        ctx.lineWidth = Math.max(1.2, r * 0.75);
        ctx.beginPath();
        ctx.moveTo(px - dirx * streak, py - diry * streak);
        ctx.lineTo(px, py);
        ctx.stroke();
        ctx.fillStyle = `rgba(${GOLD_HI}, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(px, py, r, 0, Math.PI * 2);
        ctx.fill();
      } else {
        const ember = p.hue < 24;
        if (ember) {
          ctx.shadowColor = `rgba(214, 92, 52, ${(alpha * 0.8).toFixed(3)})`;
          ctx.shadowBlur = size * 0.03 * dpr;
        } else {
          ctx.shadowBlur = 0;
        }
        ctx.fillStyle = ember
          ? `rgba(216, 96, 54, ${(alpha * 0.92).toFixed(3)})`
          : `rgba(148, 140, 124, ${(alpha * 0.7).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(px, py, ember ? r : r * 0.85, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }
}

export interface DiceProps {
  /** Rolled face 1..20. */
  d20: number;
  /** Engine outcome, e.g. "Success", "CriticalFailure" (display only). */
  outcome?: string | null;
  /** CSS pixel size of the square canvas. */
  size?: number;
  /** Play the roll animation on mount (fresh rolls); otherwise show it settled. */
  autoPlay?: boolean;
  /** Fired when a roll animation finishes. */
  onSettled?: () => void;
}

export function Dice({ d20, outcome, size = 132, autoPlay = false, onSettled }: DiceProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const { reducedMotion, muted } = useStore();
  const animRef = useRef<{ plan: RollPlan; fx: FxPlan | null; start: number; rolled: boolean; fxStarted: boolean } | null>(null);
  const rafRef = useRef<number | null>(null);
  const settledQuat = useMemo(() => orientForFace(d20Mesh(), Math.min(20, Math.max(1, Math.round(d20)))), [d20]);
  const kind = useMemo(() => rollKind(d20, outcome), [d20, outcome]);

  const soundOn = !muted;
  const reduce = reducedMotion || (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

  const paint = useCallback(
    (opts: { quat: Quat; hop: number; fx: FxPlan | null; fxT: number | null; shakeX: number }) => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;
      // Crisp on high-DPI screens without inflating memory (cap 2x).
      const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
      const px = Math.round(size * dpr);
      if (canvas.width !== px || canvas.height !== px) {
        canvas.width = px;
        canvas.height = px;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawScene(ctx, { size, dpr, quat: opts.quat, hop: opts.hop, kind, fx: opts.fx, fxT: opts.fxT, shakeX: opts.shakeX });
    },
    [size, kind],
  );

  const paintSettled = useCallback(
    (fx: FxPlan | null = null, fxT: number | null = null, shakeX = 0) => {
      paint({ quat: settledQuat, hop: 0, fx, fxT, shakeX });
    },
    [paint, settledQuat],
  );

  const stop = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const roll = useCallback(() => {
    if (!canvasRef.current) return;
    if (reduce) {
      paintSettled();
      return;
    }
    stop();
    const seed = (Math.random() * 0x7fffffff) >>> 0;
    const plan = buildRollPlan(settledQuat, seed);
    const fx = isCrit(kind) ? buildFxPlan(kind, seed) : null;
    animRef.current = { plan, fx, start: performance.now(), rolled: false, fxStarted: false };
    if (soundOn) diceClatter();

    const tick = (now: number) => {
      const a = animRef.current;
      if (!a) return;
      const canvasEl = canvasRef.current;
      const ctx2 = canvasEl?.getContext("2d");
      if (!canvasEl || !ctx2) return;
      const t = (now - a.start) / a.plan.durationMs;
      if (t < 1) {
        const frame = sampleRoll(a.plan, t);
        const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
        ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawScene(ctx2, { size, dpr, quat: frame.quat, hop: frame.hop, kind, fx: null, fxT: null, shakeX: 0 });
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      if (!a.rolled) {
        a.rolled = true;
        if (soundOn) diceSettleSound();
      }
      const fxT = a.fx ? (now - a.start - a.plan.durationMs) / a.fx.durationMs : 1;
      if (a.fx && fxT < 1) {
        if (!a.fxStarted) {
          a.fxStarted = true;
          if (soundOn) critStinger(a.fx.kind);
        }
        const shook = Math.sin(fxT * 50) * a.fx.shakeAmp * size * (1 - fxT);
        const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
        ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawScene(ctx2, { size, dpr, quat: settledQuat, hop: 0, kind, fx: a.fx, fxT, shakeX: a.fx.kind === "crit-miss" ? shook : 0 });
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      paintSettled();
      animRef.current = null;
      rafRef.current = null;
      onSettled?.();
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [reduce, stop, settledQuat, kind, soundOn, size, paintSettled, onSettled]);

  // Initial paint + optional autoplay; repaint when the face changes.
  useEffect(() => {
    if (autoPlay && !reduce) roll();
    else paintSettled();
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settledQuat, autoPlay, reduce]);

  const onClick = useCallback(() => {
    if (animRef.current) {
      // Mid-flight click finishes the roll immediately.
      stop();
      animRef.current = null;
      paintSettled();
      if (soundOn && !reduce) diceSettleSound();
      onSettled?.();
      return;
    }
    roll();
  }, [stop, paintSettled, roll, soundOn, reduce, onSettled]);

  const label =
    kind === "crit-success" ? `Die showing ${d20} — a natural twenty, critical success!`
    : kind === "crit-miss" ? `Die showing ${d20} — a natural one, critical miss.`
    : `Die showing ${d20}${outcome ? ` — ${outcome}` : ""}.`;

  return (
    <button
      type="button"
      className="dice-canvas"
      onClick={onClick}
      aria-label={`${label} Replay roll.`}
      title="Replay the roll"
    >
      <canvas ref={canvasRef} width={size} height={size} style={{ width: size, height: size }} aria-hidden="true" />
    </button>
  );
}

/** Throwable table-die: a real d20 you can hurl any time (t_84c31095).
 *
 * The chronicle rolls its own d20 for checks, but the die is also yours to
 * throw — click the die or the button and it tumbles and settles on a fresh
 * face. Keeps the last few throws so you can see what the bones said.
 */
export function DiceTray() {
  const [face, setFace] = useState(20);
  const [run, setRun] = useState(0);
  const [history, setHistory] = useState<number[]>([]);

  const throwDice = useCallback(() => {
    const rolled = 1 + Math.floor(Math.random() * 20);
    setFace(rolled);
    setRun((r) => r + 1);
    setHistory((h) => [rolled, ...h].slice(0, 4));
  }, []);

  return (
    <div className="dice-tray">
      <Dice key={run} d20={face} outcome={null} size={140} autoPlay={run > 0} />
      <button type="button" className="btn" onClick={throwDice}>
        🎲 Throw the dice
      </button>
      <p className="sys" style={{ margin: 0, textAlign: "center" }} role="status">
        {history.length === 0
          ? "Untouched — the d20 waits. Throw it, or let a check call it."
          : <>Last throw: <strong>{history[0]}</strong>
            {history.length > 1 ? <> · before: {history.slice(1).join(", ")}</> : null}</>}
      </p>
      <p className="sys" style={{ margin: 0, textAlign: "center" }}>
        Checks roll a d20 in the chronicle on their own; this tray is for your own throws — no cost, no consequences.
      </p>
    </div>
  );
}
