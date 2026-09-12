"use client";
// Saves screen: list (labels, checkpoints, dates), Save Now, Load restores
// snapshot banner + routes to adventure. Owner Bearer header via lib/api.ts.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getCampaignId, setCampaignId, LAST_SAVE_KEY, RESUME_KEY, type SaveRow } from "../../lib/api";
import { useStore } from "../../lib/store";
import { uiBlip } from "../../lib/audio";

function fmtDate(iso: string): string {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

export default function SavesPage() {
  const router = useRouter();
  const { content, addCost } = useStore();
  const [campaignId, setCid] = useState(getCampaignId());
  const [saves, setSaves] = useState<SaveRow[]>([]);
  const [live, setLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  function load(cid: string) {
    setLoading(true); setError(null);
    api.listSaves(cid, content).then((r) => {
      setSaves(r.data); setLive(!r.fromFixture); addCost(r.cost);
      setLoading(false);
      if (r.fromFixture && r.data.length === 0) setError(null);
    });
  }

  useEffect(() => { load(campaignId); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  function useCampaign() {
    setCampaignId(campaignId.trim() || "demo-campaign");
    load(campaignId.trim() || "demo-campaign");
  }

  async function saveNow() {
    const name = label.trim() || `Save — ${new Date().toLocaleString()}`;
    setSaving(true); setError(null); setNotice(null);
    const r = await api.createSave(campaignId, name, content);
    addCost(r.cost);
    setSaving(false);
    if (!r.fromFixture || r.data) {
      const row = r.data;
      setSaves((s) => [row, ...s.filter((x) => x.id !== row.id)]);
      try {
        localStorage.setItem(LAST_SAVE_KEY, JSON.stringify({ label: row.label, at: new Date().toISOString(), saveId: row.id, campaignId }));
        localStorage.setItem(RESUME_KEY, JSON.stringify({ label: row.label, at: new Date().toISOString(), saveId: row.id, campaignId }));
      } catch { /* ignore */ }
      setLabel("");
      uiBlip(740);
      setNotice(`◈ Saved — “${row.label}”.`);
    } else {
      setError("The chronicler could not set the quill down. Try again.");
    }
  }

  async function loadSave(row: SaveRow) {
    setError(null); setNotice(null);
    const r = await api.loadSave(row.id, content);
    addCost(r.cost);
    try {
      localStorage.setItem(LAST_SAVE_KEY, JSON.stringify({ label: row.label, at: new Date().toISOString(), saveId: row.id, campaignId }));
      localStorage.setItem(RESUME_KEY, JSON.stringify({ label: row.label, at: new Date().toISOString(), saveId: row.id, campaignId }));
    } catch { /* ignore */ }
    uiBlip(880);
    setNotice(
      r.fromFixture
        ? `❧ Restored snapshot — “${row.label}”. Turning the page…`
        : `❧ The chronicle turns back — “${row.label}”. The world remembers where you were.`
    );
    setTimeout(() => router.push("/adventure"), 650);
  }

  return (
    <div style={{ maxWidth: 820, margin: "0 auto", padding: 16 }}>
      <h1>Saves</h1>
      <p className="sys">
        {live ? "Kept by the chronicler." : "Local pages (backend unreachable) — saves below are kept in this browser's memory."}
      </p>

      {notice && <div className="level-toast" role="status">{notice}</div>}
      {error && (
        <div className="error-banner" role="alert">
          <strong>The chronicler stumbled —</strong> {error}
          <div style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => load(campaignId)}>Retry</button>
          </div>
        </div>
      )}

      <section className="parchment card" aria-label="Save now">
        <h2 style={{ marginTop: 0 }}>Set the quill down</h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <label className="sr-only" htmlFor="save-label">Save label</label>
          <input
            id="save-label"
            className="input-parch"
            style={{ flex: "1 1 240px" }}
            placeholder="Name this moment — e.g. Before the Hollow Road"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={120}
          />
          <button className="btn" onClick={saveNow} disabled={saving}>
            {saving ? "Saving…" : "◈ Save Now"}
          </button>
        </div>
        <p className="sys" style={{ marginBottom: 0 }}>
          Campaign:{" "}
          <label className="sr-only" htmlFor="campaign">Campaign id</label>
          <input
            id="campaign"
            className="input-parch"
            style={{ width: 220, display: "inline-block", padding: "4px 8px" }}
            value={campaignId}
            onChange={(e) => setCid(e.target.value)}
          />{" "}
          <button className="btn btn-ghost" style={{ padding: "4px 10px" }} onClick={useCampaign}>Use</button>
        </p>
      </section>

      <h2 style={{ marginTop: 20 }}>The shelf of yesterdays</h2>
      {loading && (
        <div aria-label="Loading saves" role="status">
          <div className="skel" style={{ height: 64 }} />
          <div className="skel" style={{ height: 64, marginTop: 8 }} />
          <p className="sys">Opening the save chest…</p>
        </div>
      )}
      {!loading && saves.length === 0 && (
        <div className="parchment card" role="status">
          <p><strong>No saves yet.</strong></p>
          <p className="sys">The shelf is empty — name a moment above and press Save Now, or begin a New Journey.</p>
        </div>
      )}
      {!loading && saves.length > 0 && (
        <div style={{ display: "grid", gap: 10 }}>
          {saves.map((s) => (
            <article key={s.id} className="parchment card" aria-label={s.label} style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 260px" }}>
                <strong>{s.label}</strong>
                <p className="sys" style={{ margin: "2px 0 0" }}>
                  <span className="tag gold">{s.slot}</span>{" "}
                  <span className="tag">checkpoint: {s.checkpoint}</span>{" "}
                  {fmtDate(s.created_at)}
                </p>
              </div>
              <button className="btn" onClick={() => loadSave(s)}>Load</button>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
