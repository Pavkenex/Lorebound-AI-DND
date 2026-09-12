"use client";
// Character creation: 6-stage wizard over /character/creation (t_42372415).
// Identity -> Background -> Drives -> Attributes -> Skills & Traits -> Review.
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { advanceCreationApi, commitCreationApi, creationStateApi, getToken, type CreationStateDoc } from "../../lib/api";
import { uiBlip } from "../../lib/audio";

export default function CreatePage() {
  const router = useRouter();
  const [doc, setDoc] = useState<CreationStateDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Stage drafts
  const [name, setName] = useState("");
  const [pronouns, setPronouns] = useState("");
  const [ageRange, setAgeRange] = useState("");
  const [homeland, setHomeland] = useState("");
  const [appearance, setAppearance] = useState("");
  const [background, setBackground] = useState("");
  const [drive1, setDrive1] = useState("");
  const [drive2, setDrive2] = useState("");
  const [attrs, setAttrs] = useState<Record<string, number>>({});
  const [skills, setSkills] = useState<string[]>([]);
  const [traits, setTraits] = useState<string[]>([]);

  const load = useCallback(() => {
    setLoading(true); setError(null);
    creationStateApi().then((d) => {
      setDoc(d);
      if (d?.options) {
        setAttrs((a) => (Object.keys(a).length ? a : Object.fromEntries(d.options.attributes.names.map((n) => [n, d.options.attributes.default]))));
        const id = d.data?.identity ?? {};
        setName((v) => v || String(id.name ?? ""));
        setAgeRange((v) => v || String(id.age_range ?? ""));
        setHomeland((v) => v || String(id.homeland ?? ""));
        setAppearance((v) => v || String(id.appearance ?? ""));
        const bg = d.data?.background ?? {};
        setBackground((v) => v || String(bg.background ?? ""));
        const dv = (d.data?.drives?.drives as string[]) ?? [];
        setDrive1((v) => v || (dv[0] ?? ""));
        setDrive2((v) => v || (dv[1] ?? ""));
        const st = d.data?.skills_traits ?? {};
        setSkills((v) => (v.length ? v : ((st.skills as string[]) ?? [])));
        setTraits((v) => (v.length ? v : ((st.traits as string[]) ?? [])));
      }
      setLoading(false);
    });
  }, []);

  useEffect(() => { load(); }, [load]);

  function toggle(list: string[], set: (v: string[]) => void, item: string) {
    set(list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
  }

  async function submit(stage: number, payload: Record<string, unknown>) {
    if (!doc) return;
    setBusy(true); setError(null);
    const r = await advanceCreationApi(stage, payload);
    setBusy(false);
    if (!r.ok || !r.state) { setError(r.error ?? "The stage would not take."); return; }
    uiBlip(700);
    setDoc(r.state);
  }

  async function seal() {
    setBusy(true); setError(null);
    const r = await commitCreationApi();
    setBusy(false);
    if (!r.ok) { setError(r.error ?? "The chronicle refused the seal."); return; }
    uiBlip(880);
    router.push("/adventure");
  }

  if (!getToken() && !loading) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
        <h1>Forge your hero</h1>
        <p className="sys"><Link href="/login">Sign in</Link> first — the chronicle binds characters to accounts.</p>
      </div>
    );
  }

  if (loading) return <div style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}><p className="sys">Opening the ledger of names…</p></div>;

  if (!doc) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
        <h1>Forge your hero</h1>
        <p className="sys">The chronicler is out of reach right now. {""}
          <button className="entity" onClick={load}>Try again</button>.</p>
      </div>
    );
  }

  if (doc.applied) {
    return (
      <div style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
        <h1>Already sworn</h1>
        <p className="sys">This campaign&apos;s hero is already written into the chronicle.</p>
        <div style={{ display: "flex", gap: 8 }}>
          <Link className="btn" href="/character" prefetch>See the character</Link>
          <Link className="btn btn-ghost" href="/adventure" prefetch>Back to the road</Link>
        </div>
      </div>
    );
  }

  const stage = doc.stage;
  const stageName = doc.options.stages[Math.min(stage, doc.options.stages.length) - 1] ?? "review";
  const poolUsed = Object.values(attrs).reduce((a, b) => a + b, 0);

  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 16 }}>
      <h1>Forge your hero</h1>
      <p className="sys">
        Stage {Math.min(stage, 6)} of 6 — <em>{stageName}</em>. The chronicle keeps every choice.
      </p>
      {error && <div className="error-banner" role="alert"><strong>The ink blotted —</strong> {error}</div>}

      {stage === 1 && (
        <form className="parchment card" style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(1, { name, pronouns, age_range: ageRange, homeland, appearance }); }}>
          <h2>Who walks the road?</h2>
          <label>Name <input className="input-parch" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoFocus /></label>
          <label>Pronouns <input className="input-parch" value={pronouns} onChange={(e) => setPronouns(e.target.value)} placeholder="e.g. she/her" /></label>
          <label>Years <select className="input-parch" value={ageRange} onChange={(e) => setAgeRange(e.target.value)}>
            <option value="">— say nothing —</option>
            {doc.options.age_ranges.map((a) => <option key={a} value={a}>{a}</option>)}
          </select></label>
          <label>Homeland <input className="input-parch" value={homeland} onChange={(e) => setHomeland(e.target.value)} placeholder="Ravenford, the vale, somewhere worse" /></label>
          <label>Bearing <textarea className="input-parch" value={appearance} onChange={(e) => setAppearance(e.target.value)} rows={2} placeholder="what the rain sees first" /></label>
          <button className="btn" type="submit" disabled={busy || !name.trim()}>{busy ? "Writing…" : "Continue"}</button>
        </form>
      )}

      {stage === 2 && (
        <form className="parchment card" style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(2, { background }); }}>
          <h2>What did the road make of them first?</h2>
          {doc.options.backgrounds.map((b) => (
            <label key={b.name} style={{ display: "flex", gap: 10, alignItems: "start" }}>
              <input type="radio" name="bg" checked={background === b.name} onChange={() => setBackground(b.name)} required />
              <span><strong>{b.name}</strong><br /><small className="sys">{String((b.grants as { knowledge?: string[] }).knowledge ?? "")} · gear: {((b.grants as { equipment?: string[] }).equipment ?? []).join(", ")}</small></span>
            </label>
          ))}
          <button className="btn" type="submit" disabled={busy || !background}>{busy ? "Writing…" : "Continue"}</button>
        </form>
      )}

      {stage === 3 && (
        <form className="parchment card" style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(3, { drives: [drive1, drive2] }); }}>
          <h2>What pulls them forward?</h2>
          <p className="sys">Two motivations — the chronicle will test both.</p>
          <label>First drive <input className="input-parch" value={drive1} onChange={(e) => setDrive1(e.target.value)} required /></label>
          <label>Second drive <input className="input-parch" value={drive2} onChange={(e) => setDrive2(e.target.value)} required /></label>
          <button className="btn" type="submit" disabled={busy || !drive1.trim() || !drive2.trim() || drive1.trim() === drive2.trim()}>{busy ? "Writing…" : "Continue"}</button>
        </form>
      )}

      {stage === 4 && (
        <form className="parchment card" style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(4, { attributes: attrs }); }}>
          <h2>What are they made of?</h2>
          <p className="sys">Points spent: {poolUsed} of {doc.options.attributes.pool} · each attribute {doc.options.attributes.min}–{doc.options.attributes.max}</p>
          {doc.options.attributes.names.map((n) => (
            <label key={n} style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ minWidth: 110 }}>{n}</span>
              <input
                type="number" className="input-parch" style={{ width: 100 }}
                min={doc.options.attributes.min} max={doc.options.attributes.max}
                value={attrs[n] ?? doc.options.attributes.default}
                onChange={(e) => setAttrs((a) => ({ ...a, [n]: Number(e.target.value) }))}
              />
            </label>
          ))}
          <button className="btn" type="submit" disabled={busy}>{busy ? "Writing…" : "Continue"}</button>
        </form>
      )}

      {stage === 5 && (
        <form className="parchment card" style={{ display: "grid", gap: 10 }} onSubmit={(e) => { e.preventDefault(); void submit(5, { skills, traits }); }}>
          <h2>What have they learned — and what haunts them?</h2>
          <div>
            <h3>Skills</h3>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {doc.options.skills.map((s) => (
                <label key={s} className="tag" style={{ cursor: "pointer" }}>
                  <input type="checkbox" checked={skills.includes(s)} onChange={() => toggle(skills, setSkills, s)} /> {s}
                </label>
              ))}
            </div>
          </div>
          <div>
            <h3>Traits</h3>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {doc.options.traits.map((t) => (
                <label key={t} className="tag" style={{ cursor: "pointer" }}>
                  <input type="checkbox" checked={traits.includes(t)} onChange={() => toggle(traits, setTraits, t)} /> {t}
                </label>
              ))}
            </div>
          </div>
          <button className="btn" type="submit" disabled={busy || skills.length === 0}>{busy ? "Writing…" : "Continue"}</button>
        </form>
      )}

      {stage >= 6 && (
        <section className="parchment card" style={{ display: "grid", gap: 8 }}>
          <h2>Read it back before it is true</h2>
          <p><strong>{name}</strong>{pronouns ? `, ${pronouns}` : ""}{ageRange ? ` · ${ageRange}` : ""}{homeland ? ` · of ${homeland}` : ""}</p>
          {appearance && <p className="sys" style={{ fontStyle: "italic" }}>{appearance}</p>}
          <p><strong>Background:</strong> {background}</p>
          <p><strong>Drives:</strong> {drive1} · {drive2}</p>
          <p><strong>Attributes:</strong> {Object.entries(attrs).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
          <p><strong>Skills:</strong> {skills.join(", ") || "—"}</p>
          <p><strong>Traits:</strong> {traits.join(", ") || "—"}</p>
          <button className="btn" onClick={() => void seal()} disabled={busy}>{busy ? "Sealing…" : "✦ Seal the chronicle"}</button>
        </section>
      )}

      <p className="sys" style={{ marginTop: 12 }}>
        <Link href="/adventure">Leave the ledger</Link> — creation can wait; the road will hold your place.
      </p>
    </div>
  );
}
