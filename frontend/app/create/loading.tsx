export default function Loading() {
  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: 16 }} aria-label="Loading creation" role="status">
      <div className="skel" style={{ height: 90 }} />
      <div className="skel" style={{ height: 160, marginTop: 8 }} />
      <p className="sys">Opening the ledger of names…</p>
    </div>
  );
}
