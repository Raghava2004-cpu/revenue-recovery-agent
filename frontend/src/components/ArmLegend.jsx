/** Shared legend, so both series always mean the same thing on every panel. */
export default function ArmLegend() {
  return (
    <div className="legend">
      <span className="item">
        <span className="swatch" style={{ background: "var(--agent)" }} />
        Agent policy
      </span>
      <span className="item">
        <span className="swatch" style={{ background: "var(--baseline)" }} />
        Naive dunning baseline
      </span>
    </div>
  );
}
