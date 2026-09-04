import { useLayoutEffect, useMemo, useRef, useState } from "react";
import ArmLegend from "./ArmLegend";
import { inrShort } from "../lib/format";

/*
  Hand-drawn SVG rather than a charting library.

  Two reasons. Practically, an earlier version used Chart.js from a CDN and a
  headless render caught `Chart is not defined` when the CDN was unreachable —
  the panel just went blank, which is exactly the failure you don't want on
  conference wifi. Substantively, this chart needs one specific shape: a step
  function. Recovery arrives as discrete payments, and a smoothed line would
  imply money trickling in continuously between them.
*/
const M = { top: 12, right: 18, bottom: 32, left: 60 };

export default function RecoveryChart({ points }) {
  const hostRef = useRef(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState(null);
  const height = 258;

  // Track the container so the chart reflows with the grid.
  useLayoutEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setWidth(Math.max(entry.contentRect.width, 280));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const geom = useMemo(() => {
    if (!points?.length) return null;

    const iw = width - M.left - M.right;
    const ih = height - M.top - M.bottom;
    const maxX = Math.max(...points.map((p) => p.hours), 1);
    const rawMaxY = Math.max(
      ...points.map((p) => Math.max(p.agent_total, p.baseline_total)),
      1
    );
    const maxY = Math.ceil(rawMaxY / 1e5) * 1e5 || rawMaxY;

    const sx = (h) => M.left + (h / maxX) * iw;
    const sy = (v) => M.top + ih - (v / maxY) * ih;

    const step = (key) => {
      let d = `M ${sx(0)} ${sy(0)}`;
      let prev = 0;
      for (const p of points) {
        d += ` L ${sx(p.hours)} ${sy(prev)} L ${sx(p.hours)} ${sy(p[key])}`;
        prev = p[key];
      }
      return `${d} L ${sx(maxX)} ${sy(prev)}`;
    };
    const area = (key) =>
      `${step(key)} L ${sx(maxX)} ${sy(0)} L ${sx(0)} ${sy(0)} Z`;

    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxY);
    const dayStep = Math.max(1, Math.ceil(maxX / 24 / 6));
    const xTicks = [];
    for (let d = 0; d * 24 <= maxX; d += dayStep) xTicks.push(d * 24);

    return { iw, ih, maxX, maxY, sx, sy, step, area, yTicks, xTicks };
  }, [points, width]);

  if (!points?.length || !geom) {
    return (
      <div className="panel">
        <h2>Cumulative revenue recovered</h2>
        <div className="empty">No recoveries to plot yet.</div>
      </div>
    );
  }

  const { sx, sy, step, area, yTicks, xTicks, maxX, iw, ih } = geom;

  function onMove(ev) {
    const box = ev.currentTarget.ownerSVGElement.getBoundingClientRect();
    const hours =
      (((ev.clientX - box.left) * (width / box.width)) - M.left) / iw * maxX;
    let cur = { hours: 0, agent_total: 0, baseline_total: 0 };
    for (const p of points) {
      if (p.hours <= hours) cur = p;
      else break;
    }
    setHover({ hours: Math.max(hours, 0), point: cur });
  }

  const delta = hover ? hover.point.agent_total - hover.point.baseline_total : 0;

  return (
    <div className="panel">
      <h2>Cumulative revenue recovered</h2>
      <ArmLegend />
      <div className="chart-box" ref={hostRef}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          height="100%"
          role="img"
          aria-label="Cumulative revenue recovered over time, agent policy versus naive baseline"
        >
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={M.left} x2={width - M.right} y1={sy(v)} y2={sy(v)}
                    stroke="var(--grid)" strokeWidth="1" />
              <text x={M.left - 10} y={sy(v) + 4} textAnchor="end"
                    fill="var(--muted)" fontSize="11">
                ₹{(v / 1e5).toFixed(1)}L
              </text>
            </g>
          ))}

          {xTicks.map((h) => (
            <text key={h} x={sx(h)} y={height - 12} textAnchor="middle"
                  fill="var(--muted)" fontSize="11">
              {(h / 24).toFixed(0)}d
            </text>
          ))}

          <line x1={M.left} x2={width - M.right} y1={sy(0)} y2={sy(0)}
                stroke="var(--axis)" strokeWidth="1" />

          <path d={area("baseline_total")} fill="var(--baseline-fill)" />
          <path d={area("agent_total")} fill="var(--agent-fill)" />
          <path d={step("baseline_total")} fill="none" stroke="var(--baseline)"
                strokeWidth="2" strokeLinejoin="round" />
          <path d={step("agent_total")} fill="none" stroke="var(--agent)"
                strokeWidth="2" strokeLinejoin="round" />

          {hover && (
            <g pointerEvents="none">
              <line x1={sx(hover.hours)} x2={sx(hover.hours)} y1={M.top} y2={M.top + ih}
                    stroke="var(--muted)" strokeWidth="1" strokeDasharray="3 3" />
              <circle cx={sx(hover.hours)} cy={sy(hover.point.agent_total)} r="4.5"
                      fill="var(--agent)" stroke="var(--surface)" strokeWidth="2" />
              <circle cx={sx(hover.hours)} cy={sy(hover.point.baseline_total)} r="4.5"
                      fill="var(--baseline)" stroke="var(--surface)" strokeWidth="2" />
            </g>
          )}

          <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent"
                style={{ cursor: "crosshair" }}
                onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
        </svg>

        {hover && (
          <div
            className="tooltip"
            style={{
              left: Math.min((sx(hover.hours) / width) * width + 14, width - 170),
              top: 8,
            }}
          >
            <div className="t-cap">day {(hover.hours / 24).toFixed(1)} of the recovery window</div>
            <div>Agent {inrShort(hover.point.agent_total)}</div>
            <div>Baseline {inrShort(hover.point.baseline_total)}</div>
            <div style={{ marginTop: 4, color: delta >= 0 ? "#7ee2b0" : "#ffb4bd" }}>
              {delta >= 0 ? "+" : ""}{inrShort(delta)} ahead
            </div>
          </div>
        )}
      </div>
      <div className="chart-note">
        Days since the first case was detected. Stepped, because recovery arrives as
        discrete payments — a smooth curve would imply money trickling in continuously.
      </div>
    </div>
  );
}
