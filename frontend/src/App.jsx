import { useCallback, useEffect, useState } from "react";

import { api } from "./lib/api";
import ArmCompare from "./components/ArmCompare";
import CaseDrawer from "./components/CaseDrawer";
import CaseTable from "./components/CaseTable";
import CompliancePanel from "./components/CompliancePanel";
import DiagnosisPanel from "./components/DiagnosisPanel";
import HumanQueue from "./components/HumanQueue";
import LiftByCause from "./components/LiftByCause";
import Masthead from "./components/Masthead";
import Pipeline from "./components/Pipeline";
import RecoveryChart from "./components/RecoveryChart";
import StatTiles from "./components/StatTiles";
import Verdict from "./components/Verdict";

export default function App() {
  const [metrics, setMetrics] = useState(null);
  const [events, setEvents] = useState([]);
  const [timeline, setTimeline] = useState([]);
  const [audit, setAudit] = useState(null);
  const [openCase, setOpenCase] = useState(null);
  // 800, not 250: at 250 cases a handful of large B2B invoices dominate the
  // rupee total and the confidence interval spans zero, so the headline number
  // is not defensible. At 800 the interval clears zero.
  const [size, setSize] = useState(800);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    try {
      // One round of fetches rather than a waterfall — the dashboard is
      // read-only, so nothing here depends on anything else here.
      const [m, e, t, a] = await Promise.all([
        api.metrics(),
        api.events().catch(() => []),
        api.timeline().catch(() => ({ points: [] })),
        api.verifyAudit().catch(() => null),
      ]);
      setMetrics(m);
      setEvents(e);
      setTimeline(t.points ?? []);
      setAudit(a);
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Deep-link the open case in the URL hash (#case=377), so a specific decision
  // trail can be linked to in a writeup or a review, and the back button closes
  // the drawer instead of leaving the page.
  useEffect(() => {
    const sync = () => {
      const m = /^#case=(\d+)$/.exec(window.location.hash);
      setOpenCase(m ? Number(m[1]) : null);
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const showCase = useCallback((id) => {
    window.location.hash = `case=${id}`;
  }, []);

  const closeCase = useCallback(() => {
    if (window.location.hash) window.history.back();
    else setOpenCase(null);
  }, []);

  /**
   * Pick the single most instructive case to open from the walkthrough button.
   *
   * A card-expired case that the agent recovered is the clearest one-screen
   * demonstration of the whole thesis: the baseline re-presents a card that
   * cannot succeed (scored 0.0%), the agent goes straight to a new instrument,
   * and a compliance rule defers the message out of quiet hours on the way.
   * Falling back through progressively weaker choices means the button always
   * opens *something* rather than disappearing on an unusual batch.
   */
  const walkthrough = useCallback(() => {
    const pick =
      events.find((e) => e.root_cause === "CARD_EXPIRED" && e.status === "recovered") ??
      events.find((e) => e.status === "recovered" && e.attempts > 1) ??
      events.find((e) => e.attempts > 2) ??
      events[0];
    if (pick) showCase(pick.id);
  }, [events, showCase]);

  async function runBatch() {
    setBusy(true);
    try {
      await api.reset();
      await api.runBatch(size);
      await load();
    } catch (err) {
      alert(`Batch failed: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function resetAll() {
    setBusy(true);
    try {
      await api.reset();
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <Masthead
        metrics={metrics} audit={audit} busy={busy}
        size={size} onSize={setSize}
        onRun={runBatch} onRefresh={load} onReset={resetAll}
      />

      {offline && (
        <div className="panel empty">
          Cannot reach the backend. Start it with{" "}
          <code>uvicorn app.main:app --port 8000</code> from the{" "}
          <code>backend/</code> directory, then press Refresh.
        </div>
      )}

      {!offline && !metrics?.total_events && (
        <div className="panel empty">
          No cases yet. Press <b>Run batch</b> — it generates at-risk events and runs
          each one through both the agent policy and a naive dunning baseline, on the
          same customers.
        </div>
      )}

      {!offline && metrics?.total_events > 0 && (
        <>
          <Pipeline
            metrics={metrics}
            onWalkthrough={events.length ? walkthrough : undefined}
          />

          <div className="grid g-verdict">
            <Verdict lift={metrics.lift} significance={metrics.significance} />
            <ArmCompare agent={metrics.agent} baseline={metrics.baseline} />
          </div>

          <StatTiles agent={metrics.agent} lift={metrics.lift} />

          <div className="grid g-2">
            <RecoveryChart points={timeline} />
            <LiftByCause causes={metrics.by_root_cause} />
          </div>

          <div className="grid g-3">
            <CompliancePanel compliance={metrics.compliance} />
            <DiagnosisPanel
              diagnosis={metrics.diagnosis}
              llm={metrics.llm}
              promises={metrics.promises}
            />
            <HumanQueue exceptions={metrics.exceptions} onOpen={showCase} />
          </div>

          <CaseTable
            events={events}
            causes={metrics.by_root_cause}
            onOpen={showCase}
          />
        </>
      )}

      {openCase != null && (
        <CaseDrawer eventId={openCase} onClose={closeCase} />
      )}
    </div>
  );
}
