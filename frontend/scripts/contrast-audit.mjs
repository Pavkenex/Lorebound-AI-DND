/**
 * WCAG contrast audit (t_55274097) — run it in a live page to list every piece
 * of text that reads below threshold against its effective background.
 *
 * Usage: evaluate `(${runContrastAudit.toString()})()` in the browser page
 * (DevTools console, or any CDP driver — returns a JSON string). Gradients are
 * approximated by the average of their colour stops; translucent layers are
 * blended down the ancestor chain; ::placeholder pseudo-colours are included.
 *
 * Every screen shipped by this repo passes it (menu/adventure/create/settings/
 * character/journal/inventory/skills/map/companions/saves/tutorial/login).
 */
export function runContrastAudit() {
  function parseColors(s) {
    const out = [];
    const rgbRe = /rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)/g;
    const hexRe = /#([0-9a-f]{6})\b/gi;
    let m;
    while ((m = rgbRe.exec(s))) out.push({ c: [Number(m[1]), Number(m[2]), Number(m[3])], a: m[4] === undefined ? 1 : Number(m[4]) });
    while ((m = hexRe.exec(s))) {
      const v = m[1];
      out.push({ c: [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)], a: 1 });
    }
    return out;
  }
  function blend(fg, bg, a) { return fg.map((v, i) => Math.round(v * a + bg[i] * (1 - a))); }
  function relLum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  }
  function ratio(a, b) { const l = [relLum(a), relLum(b)].sort((x, y) => y - x); return (l[0] + 0.05) / (l[1] + 0.05); }
  function effectiveBg(el) {
    let node = el;
    while (node) {
      const st = getComputedStyle(node);
      const p = parseColors(st.backgroundColor);
      if (p.length && p[0].a > 0.02) {
        return p[0].a >= 0.98 ? p[0].c : blend(p[0].c, effectiveBg(node.parentElement || node), p[0].a);
      }
      if (st.backgroundImage && st.backgroundImage !== "none") {
        const stops = parseColors(st.backgroundImage);
        if (stops.length) return [0, 1, 2].map((i) => Math.round(stops.reduce((s, x) => s + x.c[i], 0) / stops.length));
      }
      node = node.parentElement;
    }
    return [255, 255, 255];
  }
  const results = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("svg")) continue;
    const direct = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0) continue;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    const bg = effectiveBg(el);
    const checkOne = (fgColors, label) => {
      if (!fgColors.length) return;
      const { c, a } = fgColors[0];
      const r = ratio(blend(c, bg, a), bg);
      const size = parseFloat(st.fontSize);
      const bold = Number(st.fontWeight) >= 700;
      const min = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
      if (r < min) {
        results.push({
          label, text: (direct || "").slice(0, 60), tag: el.tagName, cls: String(el.className).slice(0, 70),
          ratio: Math.round(r * 100) / 100, min, color: st.color, bg: `rgb(${bg.join(",")})`, px: Math.round(size * 10) / 10,
        });
      }
    };
    if (direct) checkOne(parseColors(st.color), "text");
    if ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.getAttribute("placeholder")) {
      checkOne(parseColors(getComputedStyle(el, "::placeholder").color), "placeholder");
    }
  }
  return JSON.stringify(results.slice(0, 120));
}
