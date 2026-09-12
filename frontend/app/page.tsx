"use client";
// Main menu: New Journey, Continue (save list), Tutorial, resume-last banner.
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LAST_SAVE_KEY, RESUME_KEY, creationStateApi, getCampaignId, ensureCampaign } from "../lib/api";
import { useStore } from "../lib/store";

interface Resume { label: string; at: string; saveId: string }

export default function MenuPage() {
  const router = useRouter();
  const { tutorialDone } = useStore();
  const [resume, setResume] = useState<Resume | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(LAST_SAVE_KEY);
      if (raw) {
        const s = JSON.parse(raw) as Resume;
        if (s?.label) {
          setResume(s);
          return;
        }
      }
      const r = localStorage.getItem(RESUME_KEY);
      if (r) {
        const s = JSON.parse(r) as Resume;
        if (s?.label) setResume(s);
      }
    } catch { /* fresh table */ }
  }, []);

  function newJourney() {
    try {
      localStorage.setItem(RESUME_KEY, JSON.stringify({ label: "A new road from Ravenford", at: new Date().toISOString(), saveId: "", campaignId: getCampaignId() }));
      localStorage.removeItem(LAST_SAVE_KEY);
    } catch { /* ignore */ }
    // Live play when signed in + backend up: make sure a campaign exists first,
    // then send campaigns without a hero through the choose-your-hero step
    // (prebuilt sheets or the six-stage wizard) before the road (t_e71475f8).
    void ensureCampaign().then(async (cid) => {
      if (!cid) { router.push("/adventure"); return; }
      const doc = await creationStateApi();
      router.push(doc && !doc.applied ? "/create" : "/adventure");
    });
  }

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "48px 16px", textAlign: "center" }}>
      <p className="sys" aria-hidden="true">❖ ❖ ❖</p>
      <h1 style={{ fontSize: "2.6rem", margin: "8px 0" }}>Lorebound</h1>
      <p style={{ fontStyle: "italic", color: "var(--parch-1)" }}>
        A persistent role-playing world — a living fantasy chronicle.
      </p>

      {resume && (
        <div className="parchment card" role="status" style={{ marginTop: 20, textAlign: "left" }}>
          <strong>❧ The tale waits where you left it</strong>
          <p className="sys" style={{ margin: "4px 0 10px" }}>
            {resume.label}{resume.at ? ` · ${new Date(resume.at).toLocaleString()}` : ""}
          </p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Link className="btn" href="/adventure" prefetch>Resume the chronicle</Link>
            <Link className="btn btn-ghost" href="/saves" prefetch>Choose a save</Link>
          </div>
        </div>
      )}

      <nav aria-label="Begin" style={{ display: "grid", gap: 12, marginTop: 28 }}>
        <button className="btn" style={{ padding: "14px", fontSize: "1.15rem" }} onClick={newJourney} autoFocus={!resume}>
          ✦ New Journey
        </button>
        <Link className="btn btn-ghost" style={{ padding: "12px" }} href="/saves" prefetch>
          ◈ Continue — open the save list
        </Link>
        <Link className="btn btn-ghost" style={{ padding: "12px" }} href="/tutorial" prefetch>
          {tutorialDone ? "❖ Revisit the Tutorial" : "❖ Begin with the Tutorial"}
        </Link>
        <Link className="btn btn-ghost" style={{ padding: "12px" }} href="/create" prefetch>
          ✦ Forge your own hero
        </Link>
      </nav>

      <p className="sys" style={{ marginTop: 24 }}>
        No wrong verbs. No timed decisions. The chronicler remembers.
      </p>
      <p className="sys">
        <Link href="/login">Sign in</Link> to keep your saves beyond this browser.
      </p>
    </div>
  );
}
