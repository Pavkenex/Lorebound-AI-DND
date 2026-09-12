"use client";
// Scene art + portraits with texture-atlas support.
//
// Loading order for portraits:
//   1. Legacy individual files (kaelis/bram/sister-pell exist today) via <img>.
//   2. Atlas slice (public/atlas/portraits-atlas.png + lib/atlas.ts coords) —
//      one HTTP request for all 12 NPCs once the image agent lands the PNG.
//   3. SVG initials fallback (always works, shows under the atlas layer if
//      the PNG 404s, so nothing breaks before the atlas is built).
// Faction sigils follow the same pattern via icons-atlas.png.
// Scenes stay as SEPARATE files (public/scenes/*.jpg) — too large to atlas.
import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import {
  ICONS_ATLAS,
  ICONS_ATLAS_URL,
  PORTRAIT_ATLAS,
  PORTRAIT_ATLAS_SIZE,
  PORTRAIT_ATLAS_URL,
  portraitIdForName,
} from "../lib/atlas";

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
      <SceneImage scene={scene} label={label} />
      <figcaption className="sys" style={{ padding: "4px 10px" }}>{label}</figcaption>
    </figure>
  );
}

const SCENE_IDS = [
  "ravenford",
  "lantern-inn",
  "market",
  "northern-road",
  "forest",
  "old-monastery",
  "watchtower",
] as const;

function sceneIdForScene(scene: string): string {
  const s = scene.toLowerCase();
  if (s.includes("monastery")) return "old-monastery";
  if (s.includes("forest") || s.includes("greywood") || s.includes("hollow")) return "forest";
  if (s.includes("road") || s.includes("northern")) return "northern-road";
  if (s.includes("market")) return "market";
  if (s.includes("watch") || s.includes("tower")) return "watchtower";
  if (s.includes("inn") || s.includes("lantern") || s.includes("tavern")) return "lantern-inn";
  if (s.includes("ravenford") || s.includes("town")) return "ravenford";
  return s
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
}

/** Photo scene if public/scenes/<id>.jpg exists, else the SVG placeholder. */
export function SceneImage({ scene, label }: { scene: string; label: string }) {
  const id = sceneIdForScene(scene);
  const known = (SCENE_IDS as readonly string[]).includes(id);
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [id]);
  if (!known || failed) return <>{artFor(scene)}</>;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/scenes/${id}.jpg`}
      alt={label}
      loading="lazy"
      onError={() => setFailed(true)}
      style={{ display: "block", width: "100%", aspectRatio: "640 / 220", objectFit: "cover" }}
    />
  );
}

function artFor(scene: string) {
  const s = scene.toLowerCase();
  if (s.includes("forest") || s.includes("hollow") || s.includes("road"))
    return (
      <svg viewBox="0 0 640 220" aria-hidden="true">
        <defs><linearGradient id="sk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2c3a4a" /><stop offset="1" stopColor="#141a12" /></linearGradient></defs>
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
      <defs><radialGradient id="hg" cx="0.5" cy="0.6" r="0.7"><stop offset="0" stopColor="#6b4a1f" /><stop offset="1" stopColor="#191008" /></radialGradient></defs>
      <rect width="640" height="220" fill="url(#hg)" />
      <rect x="270" y="90" width="100" height="80" rx="6" fill="#0e0a05" stroke="#7a5c3e" strokeWidth="3" />
      <polygon points="300,60 340,60 320,100 310,100" fill="#e8c766" opacity="0.9" />
      <polygon points="326,70 346,70 332,104 322,104" fill="#e8c766" opacity="0.55" />
      {[80, 520].map((x) => (<g key={x}><rect x={x} y="140" width="70" height="14" fill="#3a2a16" /><rect x={x + 8} y="154" width="8" height="40" fill="#2b1f10" /><rect x={x + 54} y="154" width="8" height="40" fill="#2b1f10" /></g>))}
      <rect x="180" y="120" width="280" height="12" fill="#4a3620" />
    </svg>
  );
}

// Individual files that exist TODAY. Once scripts/build-atlas.py composites
// the full atlas PNG, these stay as progressive-enhancement fallbacks.
const LEGACY_PORTRAITS: Record<string, string> = {
  kaelis: "/portraits/kaelis.png",
  bram: "/portraits/bram.png",
  "sister-pell": "/portraits/sister-pell.png",
  pell: "/portraits/sister-pell.png",
  sister: "/portraits/sister-pell.png",
};

function initialsFor(name: string): string {
  return name.split(" ").map((w) => w[0]).slice(0, 2).join("");
}

/**
 * One cell of portraits-atlas.png, sliced via background-position.
 * Initials render UNDERNEATH so a missing/404 atlas degrades to the
 * old SVG look instead of an empty circle. No JS atlas-exists check needed.
 */
export function AtlasPortrait({
  id,
  name,
  hue,
  size = 72,
}: {
  id: string;
  name: string;
  hue: number;
  size?: number;
}) {
  const cell = PORTRAIT_ATLAS[id];
  if (!cell) return <FallbackPortrait name={name} hue={hue} size={size} />;
  const scale = size / cell.w;
  return (
    <div
      className="portrait"
      role="img"
      aria-label={`Portrait of ${name}`}
      style={{ width: size, height: size, position: "relative", overflow: "hidden", background: `hsl(${hue},28%,14%)` }}
    >
      <span
        aria-hidden="true"
        style={{
          position: "absolute", inset: 0, display: "flex", alignItems: "center",
          justifyContent: "center", color: "#e8c766", fontFamily: "Georgia,serif", fontSize: size * 0.32,
        }}
      >
        {initialsFor(name)}
      </span>
      <div
        aria-hidden="true"
        style={{
          position: "absolute", inset: 0, borderRadius: "50%",
          backgroundImage: `url(${PORTRAIT_ATLAS_URL})`,
          backgroundPosition: `-${cell.x * scale}px -${cell.y * scale}px`,
          backgroundSize: `${PORTRAIT_ATLAS_SIZE * scale}px auto`,
          backgroundRepeat: "no-repeat",
        }}
      />
    </div>
  );
}

/**
 * One sigil of icons-atlas.png. Knows coordinates from lib/atlas.ts —
 * the PNG itself is built later by scripts/build-atlas.py from
 * public/factions/*.png. Until then falls back to the individual file,
 * then a gold initial.
 */
export function FactionSigil({
  faction,
  size = 40,
}: {
  faction: string;
  size?: number;
}) {
  const id = faction.toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-+|-+$/g, "");
  const cell = ICONS_ATLAS[id];
  const [imgFailed, setImgFailed] = useState(false);
  useEffect(() => setImgFailed(false), [id]);
  if (!cell) return null;
  const scale = size / cell.w;
  return (
    <div
      role="img"
      aria-label={`${faction} sigil`}
      title={faction}
      style={{
        width: size, height: size, position: "relative", overflow: "hidden",
        borderRadius: "50%", border: "1px solid #c9a227", background: "#171208", flex: "none",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: "absolute", inset: 0, display: "flex", alignItems: "center",
          justifyContent: "center", color: "#e8c766", fontFamily: "Georgia,serif",
          fontSize: size * 0.4, fontVariant: "small-caps",
        }}
      >
        {faction[0]?.toUpperCase()}
      </span>
      {!imgFailed && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`/factions/${id}.png`}
          alt=""
          aria-hidden="true"
          loading="lazy"
          onError={() => setImgFailed(true)}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
        />
      )}
      {imgFailed && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute", inset: 0, borderRadius: "50%",
            backgroundImage: `url(${ICONS_ATLAS_URL})`,
            backgroundPosition: `-${cell.x * scale}px -${cell.y * scale}px`,
            backgroundSize: `${384 * scale}px auto`,
            backgroundRepeat: "no-repeat",
          }}
        />
      )}
    </div>
  );
}

function FallbackPortrait({ name, hue, size = 72 }: { name: string; hue: number; size?: number }) {
  const initials = initialsFor(name);
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

export function Portrait({ name, hue, size = 72 }: { name: string; hue: number; size?: number }) {
  const id = portraitIdForName(name);
  const legacy = LEGACY_PORTRAITS[id];
  const [legacyFailed, setLegacyFailed] = useState(false);
  useEffect(() => setLegacyFailed(false), [id]);
  // Legacy individual file first (exists today); atlas the moment it lands.
  if (legacy && !legacyFailed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        className="portrait"
        src={legacy}
        alt={`Portrait of ${name}`}
        width={size}
        height={size}
        style={{ width: size, height: size }}
        onError={() => setLegacyFailed(true)}
      />
    );
  }
  if (PORTRAIT_ATLAS[id]) return <AtlasPortrait id={id} name={name} hue={hue} size={size} />;
  return <FallbackPortrait name={name} hue={hue} size={size} />;
}
