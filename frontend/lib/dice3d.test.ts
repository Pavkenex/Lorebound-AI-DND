/**
 * Unit tests for the 3D dice engine (t_06f54e57, t_4ae64d0a, t_a1b7e3ae).
 *
 * Run with: npm test   (node --test — no test framework dependency;
 * Node >= 22.6 strips the TypeScript types natively).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildFxPlan,
  buildRollPlan,
  cameraTilt,
  d20Mesh,
  faceByNumber,
  isCrit,
  mulberry32,
  orientForFace,
  projectPoint,
  quatFromUnitVectors,
  quatMul,
  quatNorm,
  quatRotate,
  quatSlerp,
  rollKind,
  sampleRoll,
  visibleFaces,
  vDot,
  vLen,
  vNorm,
  type Quat,
  type Vec3,
} from "./dice3d.ts";

const EPS = 1e-6;

function assertClose(actual: number, expected: number, eps = EPS, msg?: string) {
  assert.ok(
    Math.abs(actual - expected) < eps,
    msg ?? `expected ${actual} ≈ ${expected} (±${eps})`,
  );
}

function assertVecClose(actual: Vec3, expected: Vec3, eps = EPS) {
  for (let i = 0; i < 3; i++) assertClose(actual[i], expected[i], eps, `component ${i}`);
}

// --- mesh ---------------------------------------------------------------------

test("d20 mesh: 12 vertices, 20 faces, outward unit normals", () => {
  const mesh = d20Mesh();
  assert.equal(mesh.vertices.length, 12);
  assert.equal(mesh.faces.length, 20);
  for (const f of mesh.faces) {
    assertClose(vLen(f.normal), 1, 1e-9, "normal is unit length");
    assert.ok(vDot(f.normal, vNorm(f.centroid)) > 0.99, "normal points outward");
    for (const i of f.indices) {
      assert.ok(i >= 0 && i < 12, "vertex index in range");
    }
  }
});

test("d20 mesh: every face carries a unique number 1..20", () => {
  const nums = d20Mesh().faces.map((f) => f.number);
  assert.deepEqual([...nums].sort((a, b) => a - b), Array.from({ length: 20 }, (_, i) => i + 1));
});

test("d20 mesh: opposite faces sum to 21 (physical d20 convention)", () => {
  const mesh = d20Mesh();
  for (const f of mesh.faces) {
    const mirror = mesh.faces.find((g) => vDot(g.normal, f.normal) < -1 + 1e-6);
    assert.ok(mirror, `face ${f.number} has an antipodal face`);
    assert.equal(f.number + mirror!.number, 21);
  }
  // faceByNumber resolves the same faces.
  for (const f of mesh.faces) assert.equal(faceByNumber(mesh, f.number).number, f.number);
  assert.throws(() => faceByNumber(mesh, 21));
});

test("d20 mesh: no two edge-adjacent faces sit closer than 3 apart (d20 spread)", () => {
  const mesh = d20Mesh();
  for (let i = 0; i < mesh.faces.length; i++) {
    for (let j = i + 1; j < mesh.faces.length; j++) {
      const shared = mesh.faces[j].indices.filter((v) => mesh.faces[i].indices.includes(v));
      if (shared.length !== 2) continue; // not edge-adjacent
      const diff = Math.abs(mesh.faces[i].number - mesh.faces[j].number);
      assert.ok(diff >= 3, `faces ${mesh.faces[i].number}/${mesh.faces[j].number} adjacent but only ${diff} apart`);
    }
  }
});

// --- orientation ----------------------------------------------------------------

test("orientForFace: any face can be presented to the camera, anchor vertex on top", () => {
  const mesh = d20Mesh();
  for (const face of mesh.faces) {
    const q = orientForFace(mesh, face.number);
    const qlen = Math.hypot(q[0], q[1], q[2], q[3]);
    assertClose(qlen, 1, 1e-9, "orientation is a unit quaternion");
    const normal = quatRotate(q, face.normal);
    assertVecClose(normal, [0, 0, 1], 1e-9);
    const anchor = quatRotate(q, mesh.vertices[face.indices[0]]);
    assertClose(Math.atan2(anchor[1], anchor[0]), Math.PI / 2, 1e-9, "anchor vertex rolled to the top");
  }
});

test("quatFromUnitVectors / slerp behave", () => {
  const q1 = quatFromUnitVectors([1, 0, 0], [0, 1, 0]);
  assertVecClose(quatRotate(q1, [1, 0, 0]), [0, 1, 0], 1e-9);
  const q2 = quatFromUnitVectors([1, 0, 0], [0, 0, 1]);
  assertVecClose(quatRotate(q2, [1, 0, 0]), [0, 0, 1], 1e-9);
  const qFlip = quatFromUnitVectors([1, 0, 0], [-1, 0, 0]);
  assertClose(vDot(quatRotate(qFlip, [1, 0, 0]), [-1, 0, 0]), 1, 1e-9, "antipodal case rotates");
  const a: Quat = [1, 0, 0, 0];
  const b = quatNorm([0.5, 0.5, 0.5, 0.5]);
  assert.deepEqual(quatSlerp(a, b, 0), a);
  assert.deepEqual(quatSlerp(a, b, 1), b);
  const mid = quatSlerp(a, b, 0.5);
  assertClose(Math.hypot(mid[0], mid[1], mid[2], mid[3]), 1, 1e-9);
  // Half-way through a 180° spin about z lands at 90°.
  const spin = quatMul(quatFromUnitVectors([1, 0, 0], [0, 1, 0]), quatFromUnitVectors([1, 0, 0], [0, 1, 0]));
  assertVecClose(quatRotate(spin, [1, 0, 0]), [-1, 0, 0], 1e-9);
});

// --- visibility + projection ----------------------------------------------------

test("visibleFaces: front-facing only, painter-sorted, shaded", () => {
  const mesh = d20Mesh();
  const faces = visibleFaces(mesh, orientForFace(mesh, 20));
  assert.ok(faces.length >= 3 && faces.length <= 12, `plausible face count (${faces.length})`);
  for (const f of faces) {
    assert.ok(f.normal[2] > 0, "all returned faces are front-facing");
    assert.ok(f.shade >= 0.35 - EPS && f.shade <= 1.0001, "shade in range");
    assert.equal(f.points.length, 3);
  }
  for (let i = 1; i < faces.length; i++) {
    assert.ok(faces[i - 1].depth <= faces[i].depth, "painter order: far faces first");
  }
  // The rolled face must be among the visible ones and actually face the camera.
  const front = faces.find((f) => f.number === 20);
  assert.ok(front, "rolled face visible");
  assertClose(front!.normal[2], 1, 1e-6, "rolled face faces the camera");
});

test("projectPoint: centre maps to centre, perspective grows nearer points", () => {
  const view = { width: 200, height: 120, zoom: 0.4, distance: 4 };
  const c = projectPoint([0, 0, 0], view);
  assertClose(c[0], 100, 1e-9);
  assertClose(c[1], 60, 1e-9);
  const near = projectPoint([1, 0, 1], view);
  const far = projectPoint([1, 0, -1], view);
  const nearOffset = Math.hypot(near[0] - 100, near[1] - 60);
  const farOffset = Math.hypot(far[0] - 100, far[1] - 60);
  assert.ok(nearOffset > farOffset, "perspective: closer points project larger");
  assert.ok(farOffset > 0, "far point still offset");
});

// --- PRNG + roll plan ------------------------------------------------------------

test("mulberry32: deterministic, uniform-ish, seed-sensitive", () => {
  const a = mulberry32(42);
  const b = mulberry32(42);
  const c = mulberry32(43);
  let identical = true;
  for (let i = 0; i < 10; i++) {
    const x = a();
    assert.ok(x >= 0 && x < 1);
    if (x !== b()) identical = false;
  }
  assert.ok(identical, "same seed → same stream");
  assert.notEqual(mulberry32(42)(), c(), "different seed → different stream");
});

test("roll plan: deterministic and lands exactly on the rolled face", () => {
  const mesh = d20Mesh();
  const target = orientForFace(mesh, 17);
  const p1 = buildRollPlan(target, 1234);
  const p2 = buildRollPlan(target, 1234);
  assert.equal(JSON.stringify(p1), JSON.stringify(p2), "same seed → identical plan");
  const p3 = buildRollPlan(target, 9999);
  assert.notEqual(JSON.stringify(p1), JSON.stringify(p3), "different seed → different plan");

  assert.ok(p1.durationMs >= 1200 && p1.durationMs <= 2400, "bounded duration");
  assert.ok(p1.settleFrom > 0.3 && p1.settleFrom < 0.8, "settle window sane");

  const start = sampleRoll(p1, 0);
  assertClose(start.settle, 0, 1e-9, "settle starts at 0");
  assertClose(start.hop, 0, 1e-9, "rests on the table at t=0");
  const end = sampleRoll(p1, 1);
  assert.deepEqual(end.quat, p1.target, "ends exactly on the target orientation");
  assertClose(end.settle, 1, 1e-9, "fully settled at t=1");
  assertClose(end.hop, 0, 1e-9, "back on the table at t=1");

  // Unit quaternions across the whole animation; settle weight monotonic.
  let prevSettle = -1;
  for (let t = 0; t <= 1.0001; t += 0.05) {
    const frame = sampleRoll(p1, t);
    const len = Math.hypot(frame.quat[0], frame.quat[1], frame.quat[2], frame.quat[3]);
    assertClose(len, 1, 1e-6, `unit quat at t=${t.toFixed(2)}`);
    assert.ok(frame.settle >= prevSettle - 1e-9, "settle is monotonic");
    prevSettle = frame.settle;
    assert.ok(frame.hop >= 0 && frame.hop <= p1.hopHeight + 1e-9, "hop bounded");
  }
});

test("camera tilt leaves the rolled face visible", () => {
  const mesh = d20Mesh();
  const q = quatMul(cameraTilt(), orientForFace(mesh, 20));
  const faces = visibleFaces(mesh, q);
  const front = faces.find((f) => f.number === 20);
  assert.ok(front, "rolled face still front-facing under the cosmetic tilt");
  assert.ok(front!.normal[2] > 0.8, "and still mostly toward the camera");
});

// --- classification ---------------------------------------------------------------

test("rollKind: naturals are criticals; engine outcome drives the rest", () => {
  assert.equal(rollKind(20, "Exceptional"), "crit-success");
  assert.equal(rollKind(20, null), "crit-success");
  assert.equal(rollKind(1, "CriticalFailure"), "crit-miss");
  assert.equal(rollKind(1, undefined), "crit-miss");
  assert.equal(rollKind(7, "Success"), "success");
  assert.equal(rollKind(7, "SuccessWithCost"), "success");
  assert.equal(rollKind(7, "Exceptional"), "success");
  assert.equal(rollKind(7, "Failure"), "fail");
  assert.equal(rollKind(7, "CriticalFailure"), "fail", "margin-critical failure stays a plain fail");
  assert.equal(rollKind(7, "success"), "success", "case-insensitive outcome");
  assert.equal(rollKind(7, null), "neutral");
  assert.equal(rollKind(7, "SomethingElse"), "neutral");
  assert.equal(rollKind(null, "Success"), null, "no die → no classification");
  assert.equal(rollKind(undefined, "Success"), null);
  assert.equal(rollKind(NaN, "Success"), null);
});

test("isCrit narrows correctly", () => {
  assert.ok(isCrit("crit-success"));
  assert.ok(isCrit("crit-miss"));
  assert.ok(!isCrit("success"));
  assert.ok(!isCrit("fail"));
  assert.ok(!isCrit(null));
});

// --- critical effects ---------------------------------------------------------------

test("fx plan (crit success): golden burst, deterministic", () => {
  const a = buildFxPlan("crit-success", 777);
  const b = buildFxPlan("crit-success", 777);
  assert.equal(JSON.stringify(a), JSON.stringify(b), "same seed → identical burst");
  assert.ok(a.rays >= 8, "radiating rays");
  assert.equal(a.shakeAmp, 0);
  assert.ok(a.particles.length >= 24 && a.particles.length <= 40);
  for (const p of a.particles) {
    assert.ok(p.hue >= 38 && p.hue <= 56, "gold hue band");
    assert.ok(p.speed > 0.5 && p.speed < 2.5);
    assert.ok(p.life > 300 && p.life < 2100);
    assert.ok(p.angle >= 0 && p.angle <= Math.PI * 2);
  }
});

test("fx plan (crit miss): dire slam with shake and embers, deterministic", () => {
  const a = buildFxPlan("crit-miss", 777);
  const b = buildFxPlan("crit-miss", 777);
  assert.equal(JSON.stringify(a), JSON.stringify(b));
  assert.equal(a.rays, 0);
  assert.ok(a.shakeAmp > 0, "the miss slams and shakes");
  assert.ok(a.particles.length >= 14 && a.particles.length <= 26);
  for (const p of a.particles) {
    assert.ok(p.hue < 34, "ash / ember hues only");
    assert.ok(p.angle > 0 && p.angle < Math.PI, "downward drift");
  }
});
