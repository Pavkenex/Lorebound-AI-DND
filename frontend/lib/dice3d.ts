/**
 * 3D dice engine — pure math, zero dependencies, no DOM (t_06f54e57).
 *
 * A real-time icosahedral d20 rendered on a 2D canvas: the mesh is built here
 * (opposite faces sum to 21, like a physical d20), orientations can target any
 * rolled face, and the renderer consumes painter-ordered, shaded faces.
 * Everything is deterministic — given the same inputs you get the same output —
 * so the animation math is unit-testable in plain Node (`npm test`).
 *
 * Coordinate system: x right, y up, z toward the camera. The die sits at the
 * origin; the viewer looks down -z at a small perspective distance.
 */

export type Vec3 = readonly [number, number, number];
/** Quaternion [w, x, y, z]. */
export type Quat = readonly [number, number, number, number];

// --- small vector helpers ----------------------------------------------------

export const vAdd = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const vSub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const vScale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
export const vDot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const vCross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
export const vLen = (a: Vec3): number => Math.hypot(a[0], a[1], a[2]);
export const vNorm = (a: Vec3): Vec3 => {
  const l = vLen(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

// --- quaternion helpers ------------------------------------------------------

export const QUAT_IDENTITY: Quat = [1, 0, 0, 0];

export function quatFromAxisAngle(axis: Vec3, angleRad: number): Quat {
  const [x, y, z] = vNorm(axis);
  const h = angleRad / 2;
  const s = Math.sin(h);
  return [Math.cos(h), x * s, y * s, z * s];
}

export function quatMul(a: Quat, b: Quat): Quat {
  const [aw, ax, ay, az] = a;
  const [bw, bx, by, bz] = b;
  return [
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ];
}

export function quatNorm(q: Quat): Quat {
  const l = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / l, q[1] / l, q[2] / l, q[3] / l];
}

export function quatRotate(q: Quat, v: Vec3): Vec3 {
  // v' = v + 2 * cross(q.xyz, cross(q.xyz, v) + q.w * v)
  const qv: Vec3 = [q[1], q[2], q[3]];
  const t = vScale(vCross(qv, vAdd(vCross(qv, v), vScale(v, q[0]))), 2);
  return vAdd(v, t);
}

/** Shortest-arc rotation taking unit vector `u` to unit vector `v`. */
export function quatFromUnitVectors(u: Vec3, v: Vec3): Quat {
  const d = Math.min(1, Math.max(-1, vDot(u, v)));
  if (d > 1 - 1e-12) return QUAT_IDENTITY;
  if (d < -1 + 1e-12) {
    let axis = vCross(u, [0, 1, 0]);
    if (vLen(axis) < 1e-6) axis = vCross(u, [1, 0, 0]);
    return quatFromAxisAngle(axis, Math.PI);
  }
  const c = vCross(u, v);
  return quatNorm([1 + d, c[0], c[1], c[2]] as Quat);
}

/** Spherical linear interpolation; endpoints are returned exactly. */
export function quatSlerp(a: Quat, b: Quat, t: number): Quat {
  if (t <= 0) return a;
  if (t >= 1) return b;
  let [bw, bx, by, bz] = b;
  let dot = a[0] * bw + a[1] * bx + a[2] * by + a[3] * bz;
  if (dot < 0) {
    // Take the short way around.
    bw = -bw; bx = -bx; by = -by; bz = -bz;
    dot = -dot;
  }
  if (dot > 1 - 1e-9) {
    // Nearly identical — linear blend is safe and stable here.
    return quatNorm([
      a[0] + (bw - a[0]) * t,
      a[1] + (bx - a[1]) * t,
      a[2] + (by - a[2]) * t,
      a[3] + (bz - a[3]) * t,
    ] as Quat);
  }
  const theta0 = Math.acos(Math.min(1, Math.max(-1, dot)));
  const theta = theta0 * t;
  const sin0 = Math.sin(theta0);
  const s0 = Math.sin(theta0 - theta) / sin0;
  const s1 = Math.sin(theta) / sin0;
  return [
    a[0] * s0 + bw * s1,
    a[1] * s0 + bx * s1,
    a[2] * s0 + by * s1,
    a[3] * s0 + bz * s1,
  ];
}

// --- icosahedron mesh (a physical d20) ---------------------------------------

export interface DieFace {
  /** Vertex indices into the mesh's vertex list. */
  readonly indices: readonly [number, number, number];
  /** Outward unit normal (mesh space). */
  readonly normal: Vec3;
  /** Face centroid (mesh space). */
  readonly centroid: Vec3;
  /** Face number engraved on the die, 1..20. */
  readonly number: number;
}

export interface DieMesh {
  readonly vertices: readonly Vec3[];
  readonly faces: readonly DieFace[];
}

const PHI = (1 + Math.sqrt(5)) / 2;

const ICO_VERTICES: readonly Vec3[] = [
  [-1, PHI, 0], [1, PHI, 0], [-1, -PHI, 0], [1, -PHI, 0],
  [0, -1, PHI], [0, 1, PHI], [0, -1, -PHI], [0, 1, -PHI],
  [PHI, 0, -1], [PHI, 0, 1], [-PHI, 0, -1], [-PHI, 0, 1],
];

const ICO_FACE_INDICES: readonly (readonly [number, number, number])[] = [
  [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
  [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
  [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
  [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
];

let cachedMesh: DieMesh | null = null;

/**
 * The d20 mesh: 12 vertices, 20 outward-wound triangular faces, numbered 1..20
 * so that every pair of opposite faces sums to 21 (standard dice convention).
 */
export function d20Mesh(): DieMesh {
  if (cachedMesh) return cachedMesh;

  const faces: {
    indices: [number, number, number];
    normal: Vec3;
    centroid: Vec3;
    number: number;
  }[] = [];

  for (const raw of ICO_FACE_INDICES) {
    let [i0, i1, i2] = raw;
    const a = ICO_VERTICES[i0];
    const b = ICO_VERTICES[i1];
    const c = ICO_VERTICES[i2];
    let centroid = vScale(vAdd(vAdd(a, b), c), 1 / 3);
    let normal = vNorm(vCross(vSub(b, a), vSub(c, a)));
    // Winding guard: the normal must point away from the centre.
    if (vDot(normal, centroid) < 0) {
      [i1, i2] = [i2, i1];
      normal = vNorm(vCross(vSub(ICO_VERTICES[i1], a), vSub(ICO_VERTICES[i2], a)));
      centroid = vScale(vAdd(vAdd(a, ICO_VERTICES[i1]), ICO_VERTICES[i2]), 1 / 3);
    }
    faces.push({ indices: [i0, i1, i2], normal, centroid, number: 0 });
  }

  // Numbering: a fixed d20-style layout, derived once by search — every pair
  // of opposite faces sums to 21 AND no two edge-adjacent faces sit closer
  // than 3 apart (so "20" shows low neighbours, like a physical die). Index →
  // number: [2,16,7,15,12,3,13,4,11,20,6,14,5,19,9,10,1,18,8,17].
  const LAYOUT: readonly number[] = [2, 16, 7, 15, 12, 3, 13, 4, 11, 20, 6, 14, 5, 19, 9, 10, 1, 18, 8, 17];
  for (let i = 0; i < faces.length; i++) faces[i].number = LAYOUT[i] ?? 0;

  cachedMesh = { vertices: ICO_VERTICES, faces } as DieMesh;
  return cachedMesh;
}

export function faceByNumber(mesh: DieMesh, n: number): DieFace {
  const f = mesh.faces.find((face) => face.number === n);
  if (!f) throw new Error(`d20 has no face ${n}`);
  return f;
}

// --- orientation solver ------------------------------------------------------

/**
 * Orientation that presents face `n` toward the camera (normal → +z) with the
 * face's first vertex rolled to the top of the screen, so the engraved number
 * reads upright. Exact to floating-point precision.
 */
export function orientForFace(mesh: DieMesh, n: number): Quat {
  const face = faceByNumber(mesh, n);
  const qBase = quatFromUnitVectors(face.normal, [0, 0, 1]);
  // Roll about the view axis until the anchor vertex sits at the top.
  const anchor = quatRotate(qBase, mesh.vertices[face.indices[0]]);
  const theta = Math.atan2(anchor[1], anchor[0]);
  let delta = Math.PI / 2 - theta;
  // Wrap to (-pi, pi] for the shortest roll.
  delta = ((delta + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
  const qRoll = quatFromAxisAngle([0, 0, 1], delta);
  return quatNorm(quatMul(qRoll, qBase));
}

/** Fixed tilt applied on top of the settled orientation for depth (cosmetic). */
export function cameraTilt(): Quat {
  return quatMul(
    quatFromAxisAngle([1, 0, 0], -0.22),
    quatFromAxisAngle([0, 1, 0], 0.12),
  );
}

// --- visibility, shading, projection ----------------------------------------

export interface RenderedFace {
  readonly number: number;
  /** Rotated face vertices, in mesh winding order. */
  readonly points: readonly [Vec3, Vec3, Vec3];
  readonly normal: Vec3;
  /** View-space depth of the centroid (painter's sort key). */
  readonly depth: number;
  /** Flat-shading factor in [0, 1]. */
  readonly shade: number;
}

const LIGHT_DIR: Vec3 = vNorm([-0.45, 0.85, 0.8]);

/**
 * Front-facing, shaded faces of the rotated mesh, sorted back-to-front so a
 * canvas renderer can paint them in order (painter's algorithm).
 */
export function visibleFaces(mesh: DieMesh, rot: Quat): RenderedFace[] {
  const out: RenderedFace[] = [];
  for (const face of mesh.faces) {
    const normal = quatRotate(rot, face.normal);
    if (normal[2] <= 0.02) continue; // back-facing (with a sliver guard)
    const centroid = quatRotate(rot, face.centroid);
    const points = face.indices.map((i) => quatRotate(rot, mesh.vertices[i])) as [Vec3, Vec3, Vec3];
    const diff = Math.max(0, vDot(normal, LIGHT_DIR));
    out.push({
      number: face.number,
      points,
      normal,
      depth: centroid[2],
      shade: 0.35 + 0.65 * diff,
    });
  }
  return out.sort((a, b) => a.depth - b.depth);
}

export interface ViewSpec {
  readonly width: number;
  readonly height: number;
  /** Screen fill factor, ~1 = the die spans the canvas edge to edge. */
  readonly zoom?: number;
  /** Camera distance in mesh units (mesh radius ≈ 1.9). */
  readonly distance?: number;
}

/** Perspective projection of a view-space point onto the canvas. */
export function projectPoint(p: Vec3, view: ViewSpec): readonly [number, number] {
  const zoom = view.zoom ?? 0.36;
  const dist = view.distance ?? 4.4;
  const persp = dist / Math.max(0.3, dist - p[2]);
  const scale = zoom * Math.min(view.width, view.height) * persp;
  return [view.width / 2 + p[0] * scale, view.height / 2 - p[1] * scale];
}

// --- seeded PRNG (deterministic animations) ----------------------------------

/** mulberry32 — tiny, fast, good enough for dice theatrics. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function randomUnitVector(rnd: () => number): Vec3 {
  const z = rnd() * 2 - 1;
  const theta = rnd() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  return [r * Math.cos(theta), r * Math.sin(theta), z];
}

function randomQuat(rnd: () => number): Quat {
  const u1 = rnd();
  const u2 = rnd();
  const u3 = rnd();
  return quatNorm([
    Math.sqrt(1 - u1) * Math.sin(2 * Math.PI * u2),
    Math.sqrt(1 - u1) * Math.cos(2 * Math.PI * u2),
    Math.sqrt(u1) * Math.sin(2 * Math.PI * u3),
    Math.sqrt(u1) * Math.cos(2 * Math.PI * u3),
  ] as Quat);
}

// --- roll animation plan ------------------------------------------------------

const TAU = Math.PI * 2;

export interface RollPlan {
  readonly seed: number;
  readonly durationMs: number;
  /** Normalized t at which the tumble starts blending into the settle. */
  readonly settleFrom: number;
  readonly target: Quat;
  readonly spin0: Quat;
  readonly axisA: Vec3;
  readonly axisB: Vec3;
  readonly spinTurns: number;
  readonly wobbleAmp: number;
  readonly wobbleFreq: number;
  /** Hop height in die-radius units (multiply by die size when drawing). */
  readonly hopHeight: number;
  readonly hops: number;
}

const easeOutQuint = (t: number) => 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 5);
const smoothstep = (t: number) => {
  const x = Math.max(0, Math.min(1, t));
  return x * x * (3 - 2 * x);
};

export interface RollPlanOptions {
  durationMs?: number;
  settleFrom?: number;
}

/**
 * Deterministic tumble plan: the die spins up, tumbles with decaying angular
 * velocity, then blends (settle) onto `target` — landing exactly on the rolled
 * face. Same seed → identical plan and samples.
 */
export function buildRollPlan(target: Quat, seed: number, opts: RollPlanOptions = {}): RollPlan {
  const rnd = mulberry32(seed);
  const durationMs = opts.durationMs ?? Math.round(1500 + rnd() * 500);
  const settleFrom = opts.settleFrom ?? 0.5 + rnd() * 0.12;
  return {
    seed,
    durationMs,
    settleFrom: Math.max(0.3, Math.min(0.8, settleFrom)),
    target: quatNorm(target),
    spin0: randomQuat(rnd),
    axisA: randomUnitVector(rnd),
    axisB: randomUnitVector(rnd),
    spinTurns: 3 + rnd() * 2.2,
    wobbleAmp: 0.5 + rnd() * 1.0,
    wobbleFreq: 2 + rnd() * 1.5,
    hopHeight: 0.34 + rnd() * 0.18,
    hops: 1.6 + rnd() * 0.7,
  };
}

export interface RollFrame {
  readonly quat: Quat;
  /** Hop offset in die-radius units (0 = resting on the table). */
  readonly hop: number;
  /** 0 = tumbling freely, 1 = fully settled. */
  readonly settle: number;
}

/** Sample the roll plan at normalized time t (0..1). */
export function sampleRoll(plan: RollPlan, t: number): RollFrame {
  const tt = Math.max(0, Math.min(1, t));
  const spin = quatFromAxisAngle(plan.axisA, plan.spinTurns * TAU * easeOutQuint(tt));
  const wobble = quatFromAxisAngle(plan.axisB, plan.wobbleAmp * Math.sin(Math.PI * tt * plan.wobbleFreq));
  const live = quatMul(quatMul(plan.spin0, spin), wobble);
  const s = smoothstep((tt - plan.settleFrom) / (1 - plan.settleFrom));
  const quat = tt >= 1 ? plan.target : (s <= 0 ? live : quatSlerp(live, plan.target, s));
  const hop = Math.abs(Math.sin(Math.PI * tt * plan.hops)) * Math.pow(1 - tt, 1.6) * plan.hopHeight;
  return { quat, hop, settle: s };
}

// --- outcome classification ---------------------------------------------------

export type RollKind = "crit-success" | "crit-miss" | "success" | "fail" | "neutral";

const SUCCESS_OUTCOMES = new Set(["exceptional", "success", "successwithcost"]);
const FAIL_OUTCOMES = new Set(["failure", "criticalfailure"]);

/**
 * Classify a resolved check for presentation. The die's own extremes decide the
 * criticals (nat 20 / nat 1, as at any table); everything else follows the
 * engine outcome. `null` d20 (no die rolled) → null.
 */
export function rollKind(d20?: number | null, outcome?: string | null): RollKind | null {
  if (d20 == null || !Number.isFinite(d20)) return null;
  if (d20 === 20) return "crit-success";
  if (d20 === 1) return "crit-miss";
  const o = (outcome ?? "").trim().toLowerCase();
  if (SUCCESS_OUTCOMES.has(o)) return "success";
  if (FAIL_OUTCOMES.has(o)) return "fail";
  return "neutral";
}

export function isCrit(kind: RollKind | null): kind is "crit-success" | "crit-miss" {
  return kind === "crit-success" || kind === "crit-miss";
}

// --- critical flourishes ------------------------------------------------------

export interface FxParticle {
  /** Emission angle, radians. */
  readonly angle: number;
  /** Initial speed in die-radius units per second. */
  readonly speed: number;
  /** Sprite size in px at 128px die scale. */
  readonly size: number;
  /** Hue for hsl() colouring. */
  readonly hue: number;
  /** Lifetime in ms. */
  readonly life: number;
}

export interface FxPlan {
  readonly kind: "crit-success" | "crit-miss";
  readonly particles: readonly FxParticle[];
  /** Number of radiating spokes behind the die (0 = none). */
  readonly rays: number;
  /** Peak flash alpha behind the die, 0..1. */
  readonly flashAlpha: number;
  /** Horizontal shake amplitude in die-radius units (miss only). */
  readonly shakeAmp: number;
  readonly durationMs: number;
}

/**
 * Deterministic special-effect plan for the two critical moments:
 * - nat 20 (critical success): a golden burst — rays + sparks.
 * - nat 1 (critical miss): a dire slam — red flash, shake, drifting embers.
 */
export function buildFxPlan(kind: "crit-success" | "crit-miss", seed: number): FxPlan {
  const rnd = mulberry32(seed ^ (kind === "crit-success" ? 0x5eed : 0xd1ce));
  if (kind === "crit-success") {
    const count = 30 + Math.floor(rnd() * 9);
    const particles: FxParticle[] = Array.from({ length: count }, () => ({
      angle: rnd() * TAU,
      speed: 1.0 + rnd() * 1.0,
      size: 1.5 + rnd() * 1.6,
      hue: 38 + rnd() * 18,
      life: 900 + rnd() * 1100,
    }));
    return { kind, particles, rays: 14, flashAlpha: 0.6, shakeAmp: 0, durationMs: 2400 };
  }
  const count = 18 + Math.floor(rnd() * 9);
  const particles: FxParticle[] = Array.from({ length: count }, () => ({
    angle: Math.PI * (0.15 + rnd() * 0.7), // mostly downward drift
    speed: 0.35 + rnd() * 0.6,
    size: 1.2 + rnd() * 2.0,
    hue: rnd() < 0.6 ? rnd() * 16 : 18 + rnd() * 14,
    life: 800 + rnd() * 800,
  }));
  return { kind, particles, rays: 0, flashAlpha: 0.5, shakeAmp: 0.07, durationMs: 1600 };
}
