"use client";
import { useState } from "react";

export default function Home() {
  const [log, setLog] = useState<string[]>(["Welcome to Lorebound."]);
  const [input, setInput] = useState("");
  return (
    <main style={{ display: "grid", gridTemplateColumns: "280px 1fr 280px", minHeight: "100vh" }}>
      <aside style={{ padding: 16, borderRight: "1px solid #3a2f23" }}>
        <h2>Lorebound</h2>
        <p>Adventure · Character · Inventory · Skills · Journal · Map</p>
      </aside>
      <section style={{ padding: 16 }}>
        {log.map((l, i) => (
          <p key={i}>{l}</p>
        ))}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (input.trim()) setLog([...log, `> ${input}`, "(The world remembers.)"]);
            setInput("");
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Attempt anything…"
            aria-label="action input"
            style={{ width: "100%", padding: 12 }}
          />
        </form>
      </section>
      <aside style={{ padding: 16, borderLeft: "1px solid #3a2f23" }}>
        <h3>Lantern Inn</h3>
        <p>Time: Day 12, 21:34 · NPCs: Marla · Leads: Missing Travelers</p>
      </aside>
    </main>
  );
}
