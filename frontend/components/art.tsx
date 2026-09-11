"use client";
// Pre-created SVG scene art + portraits. Async, non-blocking image slot (t_786084ac):
// narration renders immediately; art fades in ~600ms later and can be hidden.
import { useEffect, useState } from "react";
import { useStore } from "../lib/store";

export function SceneArt({ scene, label }: { scene: string; label: string }) {
  const { hideArtwork, reducedMotion } = useStore();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(false);
    const t = setTimeout(() => setReady(true), reducedMotion ? 0 : 600);
    return () => clearTimeout(t);
  }, [scene, reducedMotion]);
  if (hideArtwork) return <p className="sys">[Artwork hidden — text-first mode. Scene: {label}.]</p>;
  if (!ready) return <div className="scene-frame" aria-hidden="true" style={{ minHeight: 120 }}><p className="sys" style={{ padding: 12 }}>Painting the scene… (narration already below)</p></div>;
  return (
    <figure className="scene-frame" style={{ margin: "0 0 12px" }} role="img" aria-label={label}>
      {artFor(scene)}
      <figcaption className="sys" style={{ padding: "4px 10px" }}>{label}</figcaption>
    </figure>
  );
}

function artFor(scene: string) {
  const s = scene.toLowerCase();
  if (s.includes("forest") || s.includes("hollow") || s.includes("road"))
    return (
      <svg viewBox="0 0 640 220" aria-hidden="true">
        <defs><linearGradient id="sk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2c3a4a"/><stop offset="1" stopColor="#141a12"/></linearGradient></defs>
        <rect width="640" height="220" fill="url(#sk)" />
        <circle cx="520" cy="46" r="22" fill="#e8c766" opacity="0.8" />
        {[60, 140, 230, 330, 430, 540].map((x, i) => (
          <g key={x} opacity={0.9 - i * 0.06}>
            <rect x={x} y={70 + (i % 3) * 12} width="12" height="120" fill="#241a10" />
            <polygon points={`${x - 34},${110 + (i % 3) * 12} ${x + 46},${110 + (i % 3) * 12} ${x + 6},${30 + (i % 3) * 8}`} fill="#2e4028" />
          </g>
        ))}
        <path d="M0,200 Q200,170 340,192 T640,186 L640,220 L0,220 Z" fill="#3a2f1d" />
        <path d="M0,206 Q200,182 340,200 T640,196" stroke="#c9a227" strokeWidth="2" fill="none" opacity="0.5" />
      </svg>
    );
  if (s.includes("monastery"))
    return (
      <svg viewBox="0 0 640 220" aria-hidden="true">
        <rect width="640" height="220" fill="#1a1626" />
        <circle cx="120" cy="50" r="18" fill="#e8e2d0" opacity="0.9" />
        <rect x="250" y="80" width="140" height="110" fill="#2b241a" />
        <polygon points="240,80 400,80 320,40" fill="#3a2f23" />
        <rect x="310" y="30" width="20" height="50" fill="#2b241a" />
        {[300, 330].map((x) => (<rect key={x} x={x} y="120" width="12" height="26" fill="#c9a227" opacity="0.85" />))}
        <path d="M0,200 L640,200 L640,220 L0,220 Z" fill="#241d12" />
      </svg>
    );
  // default: tavern interior
  return (
    <svg viewBox="0 0 640 220" aria-hidden="true">
      <defs><radialGradient id="hg" cx="0.5" cy="0.6" r="0.7"><stop offset="0" stopColor="#6b4a1f"/><stop offset="1" stopColor="#191008"/></radialGradient></defs>
      <rect width="640" height="220" fill="url(#hg)" />
      <rect x="270" y="90" width="100" height="80" rx="6" fill="#0e0a05" stroke="#7a5c3e" strokeWidth="3" />
      <polygon points="300,60 340,60 320,100 310,100" fill="#e8c766" opacity="0.9" />
      <polygon points="326,70 346,70 332,104 322,104" fill="#e8c766" opacity="0.55" />
      {[80, 520].map((x) => (<g key={x}><rect x={x} y="140" width="70" height="14" fill="#3a2a16" /><rect x={x + 8} y="154" width="8" height="40" fill="#2b1f10" /><rect x={x + 54} y="154" width="8" height="40" fill="#2b1f10" /></g>))}
      <rect x="180" y="120" width="280" height="12" fill="#4a3620" />
    </svg>
  );
}

export function Portrait({ name, hue, size = 72 }: { name: string; hue: number; size?: number }) {
  const initials = name.split(" ").map((w) => w[0]).slice(0, 2).join("");
  return (
    <svg className="portrait" width={size} height={size} viewBox="0 0 72 72" role="img" aria-label={`Portrait of ${name}`}>
      <defs><radialGradient id={`p${hue}`} cx="0.4" cy="0.35" r="0.9"><stop offset="0" stopColor={`hsl(${hue},32%,38%)`} /><stop offset="1" stopColor={`hsl(${hue},28%,14%)`} /></radialGradient></defs>
      <circle cx="36" cy="36" r="34" fill={`url(#p${hue})`} stroke="#c9a227" strokeWidth="2" />
      <circle cx="36" cy="28" r="11" fill="#caa87a" opacity="0.9" />
      <path d="M18,58 Q36,40 54,58 L54,70 L18,70 Z" fill="#3a2c1c" />
      <text x="36" y="66" textAnchor="middle" fontSize="13" fill="#e8c766" fontFamily="Georgia,serif">{initials}</text>
    </svg>
  );
}
