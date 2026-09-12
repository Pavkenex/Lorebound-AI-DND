// Route skeleton while this screen streams in.
export default function Loading() {
  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 16 }} role="status" aria-label="Loading">
      <div className="skel" style={{ height: 28, width: "40%" }} />
      <div className="skel" style={{ height: 120, marginTop: 12 }} />
      <p className="sys">Unrolling the parchment…</p>
    </div>
  );
}
