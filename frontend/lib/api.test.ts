import assert from "node:assert/strict";
import test from "node:test";

import { normalizeApiBase } from "./api-base.ts";

test("normalizeApiBase adds http:// when the scheme is missing (live deploy env bug)", () => {
  assert.equal(normalizeApiBase("130.61.50.108:8001"), "http://130.61.50.108:8001");
  assert.equal(normalizeApiBase("lorebound.example.com:9000"), "http://lorebound.example.com:9000");
});

test("normalizeApiBase keeps explicit schemes and trims trailing slashes", () => {
  assert.equal(normalizeApiBase("http://localhost:8001"), "http://localhost:8001");
  assert.equal(normalizeApiBase("https://api.example.com/"), "https://api.example.com");
  assert.equal(normalizeApiBase("  http://x:1///  "), "http://x:1");
});

test("normalizeApiBase ignores empty values so the default base kicks in", () => {
  assert.equal(normalizeApiBase(""), "");
  assert.equal(normalizeApiBase("   "), "");
  assert.equal(normalizeApiBase(undefined), "");
  assert.equal(normalizeApiBase(null), "");
});
