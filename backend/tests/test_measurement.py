"""
Tests for the properties the headline number depends on.

If any of these break, the reported lift stops being a measurement.
"""
from datetime import datetime, timedelta, timezone

from app import taxonomy as tx
from app.models import RevenueEvent
from app.sim import outcome


def test_batches_are_reproducible(db):
    """Same seed, same numbers. A lift that moves between runs isn't evidence."""
    from app.db import SessionLocal, engine
    from app.metrics import compute_metrics
    from app.models import Base, init_db
    from app.pipeline import orchestrator
    from app.sim.generator import generate_batch

    def run():
        Base.metadata.drop_all(bind=engine)
        init_db()
        s = SessionLocal()
        orchestrator.run_batch(s, generate_batch(n=30, seed=5))
        m = compute_metrics(s)
        s.close()
        return m["lift"], m["agent"]["recovery_rate_pct"]

    assert run() == run()


def test_seeded_batches_do_not_drift_with_the_wall_clock():
    """
    A seeded batch must be anchored to a fixed epoch, not to `datetime.now()`.

    This is the bug `test_batches_are_reproducible` cannot catch: both of its
    runs happen in the same second, so wall-clock coupling is invisible to it.
    But salary-cycle retries target specific days of the month, receivables
    outreach skips weekends, and quiet hours depend on local time — so an
    unanchored batch silently produces different results tomorrow, and the
    headline lift stops being checkable.
    """
    from app.sim.generator import generate_batch

    first = generate_batch(n=20, seed=3)
    second = generate_batch(n=20, seed=3)
    assert [c["detected_at"] for c in first] == [c["detected_at"] for c in second]

    # The anchor must be the fixed epoch, not "roughly now".
    from app.sim.generator import REFERENCE_EPOCH
    assert REFERENCE_EPOCH.weekday() == 0, "epoch should be a Monday"
    assert max(c["detected_at"] for c in first) <= REFERENCE_EPOCH

    # An unseeded batch still tracks real time, for ad-hoc runs.
    from datetime import datetime, timedelta, timezone
    live = generate_batch(n=5, seed=None)
    assert datetime.now(timezone.utc) - max(
        c["detected_at"] for c in live
    ) < timedelta(days=6)


def test_both_arms_face_identical_customers(batch):
    """Common random numbers only work if the pairing is exact."""
    pairs: dict[str, dict] = {}
    for e in batch.query(RevenueEvent).all():
        pairs.setdefault(e.case_key, {})[e.arm] = e

    assert pairs, "no cases generated"
    for case_key, arm in pairs.items():
        assert set(arm) == {"agent", "baseline"}, f"{case_key} is unpaired"
        a, b = arm["agent"], arm["baseline"]
        assert a.amount == b.amount
        assert a.customer_id == b.customer_id
        assert a.sim_propensity == b.sim_propensity
        assert a.raw_error_reason == b.raw_error_reason


def test_first_attempt_draws_the_same_randomness_in_both_arms():
    assert outcome.draw("case-1", 1) == outcome.draw("case-1", 1)
    assert outcome.draw("case-1", 1) != outcome.draw("case-1", 2)
    assert outcome.draw("case-1", 1) != outcome.draw("case-2", 1)
    assert 0.0 <= outcome.draw("case-9", 3) < 1.0


def test_retrying_an_expired_card_is_impossible_not_merely_unlikely():
    """
    The single most important number in the outcome model. If this drifts above
    zero, the baseline's "just retry it" strategy starts looking viable on
    cases where it is physically guaranteed to fail.
    """
    expired = tx.root_cause("CARD_EXPIRED")
    assert outcome.action_fit(expired, tx.RETRY_SAME_INSTRUMENT) == 0.0
    assert outcome.action_fit(expired, tx.REGENERATE_PAYMENT_LINK) > 0.9

    revoked = tx.root_cause("MANDATE_REVOKED")
    assert outcome.action_fit(revoked, tx.RETRY_MANDATE) == 0.0


def test_retrying_before_funds_arrive_is_heavily_penalised():
    """The mechanism behind the salary-cycle retry's entire value."""
    now = datetime.now(timezone.utc)
    event = RevenueEvent(
        case_key="k", arm="agent", amount=1000.0, root_cause="INSUFFICIENT_FUNDS",
        detected_at=now, sim_propensity=0.9, sim_funds_at=now + timedelta(days=2),
        sim_reachable=True, contact_count=0, segment="returning",
    )
    early, _ = outcome.timing_fit(event, tx.root_cause("INSUFFICIENT_FUNDS"), now)
    late, _ = outcome.timing_fit(event, tx.root_cause("INSUFFICIENT_FUNDS"),
                                 now + timedelta(days=3))
    assert early < 0.1
    assert late == 1.0
    assert late > early * 10


def test_recovered_amounts_never_exceed_amount_at_risk(batch):
    from app.metrics import compute_metrics
    m = compute_metrics(batch)
    for arm in ("agent", "baseline"):
        assert m[arm]["amount_recovered_gross"] <= m[arm]["amount_at_risk"]


def test_significance_reports_an_interval_not_just_a_point(batch):
    from app.metrics import compute_metrics
    sig = compute_metrics(batch)["significance"]
    assert sig["ci90_low"] <= sig["observed_total_delta"] <= sig["ci90_high"]
    assert sig["paired_cases"] == 40
    assert sig["cases_agent_won"] + sig["cases_agent_lost"] + sig["cases_tied"] == 40
