// node --test (TS stripped natively) — content prefs vocabulary + normalization.
import assert from "node:assert/strict";
import { test } from "node:test";
import { defaultPrefs, normalizeContentPrefs } from "./store-types.ts";

test("defaults are canonical levels", () => {
  assert.deepEqual(Object.keys(defaultPrefs).sort(), ["horror", "language", "nsfw", "romance", "violence"]);
  for (const k of ["violence", "horror", "romance", "language"] as const) {
    assert.ok(["off", "reduced", "standard"].includes(defaultPrefs[k]), `${k}=${defaultPrefs[k]}`);
  }
  assert.equal(defaultPrefs.nsfw, false);
});

test("legacy values normalize onto canonical levels", () => {
  const p = normalizeContentPrefs({ violence: "low", horror: "low", romance: "off", language: "clean", nsfw: true });
  assert.equal(p.violence, "reduced");
  assert.equal(p.horror, "reduced");
  assert.equal(p.language, "reduced");
  assert.equal(p.romance, "off");
  assert.equal(p.nsfw, true);
});

test("unknown or malformed values fall back per-axis, nsfw only true when true", () => {
  const p = normalizeContentPrefs({ violence: "nonsense" as never, romance: 7 as never, nsfw: "yes" as never });
  assert.equal(p.violence, defaultPrefs.violence);
  assert.equal(p.romance, defaultPrefs.romance);
  assert.equal(p.nsfw, false);
  const q = normalizeContentPrefs(undefined);
  assert.deepEqual(q, defaultPrefs);
});

test("canonical values pass through untouched", () => {
  const p = normalizeContentPrefs({ violence: "standard", horror: "off", romance: "standard", language: "off", nsfw: true });
  assert.deepEqual(p, { violence: "standard", horror: "off", romance: "standard", language: "off", nsfw: true });
});
