# AI Revenue Recovery Agent

**Razorpay AI Buildathon — Track 03: Find revenue that's slipping away and win it back**

Detects revenue at risk, diagnoses *why* it failed, chooses a bounded intervention,
executes it on a schedule, and stops when it should — with a tamper-evident audit
trail behind every decision.

---

## The number

Running 250 at-risk cases through the full pipeline:

| | **Agent** | **Naive dunning baseline** |
|---|---:|---:|
| Recovery rate | **29.2%** | 17.6% |
| Revenue recovered | **₹8,14,093** | ₹3,85,997 |
| Net of contact cost | **₹8,09,090** | ₹3,85,915 |
| Messages sent to customers | **271** | 411 |
| Total attempts | **486** | 872 |
| Compliance rules violated | **0** | 610 |

> **Incremental revenue recovered: ₹4,28,096** — 90% CI ₹6,485 to ₹9,54,450.
> The agent won 44 cases the baseline lost and lost 15 the baseline won.

Reproduce it exactly: `python seed.py --n 250 --seed 42 --reset`. A seeded batch is
pinned to a fixed clock as well as a fixed RNG, so those figures are byte-identical
on any machine on any day.

### Why there are two columns

A recovery rate on its own isn't a measurement, because it has no counterfactual
in it. 30% of at-risk revenue recovered sounds good — but a dumb retry cron
recovers most transient gateway failures on its own, and it would be easy to
claim its work as the agent's.

So **every case is run twice**: once under the agent's policy, once under a naive
dunning baseline (retry the same instrument, blast an SMS, repeat — cause-blind
and clock-blind). Both arms face the *same customers*, with the same latent
willingness to pay, drawing the *same random numbers*
([common random numbers](backend/app/sim/outcome.py), the standard variance-reduction
technique for paired simulation). Any difference between the columns is
attributable to policy, not to luck.

The headline is the **difference**, with a bootstrap confidence interval, because
that's the only number that survives the question "compared to what?"

---

## Quick start

**Nothing is required to run this.** No API keys, no Razorpay account. Both keys
below are optional and each unlocks one specific thing:

| Key | Where from | What it turns on | Without it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | LLM diagnosis of the free-text failures the rule engine can't match (13.6% of cases), and per-customer Hinglish copy | Those cases route to a human; copy falls back to deterministic templates |
| `RAZORPAY_KEY_ID` + `_SECRET` + `_WEBHOOK_SECRET` | Razorpay Dashboard → Settings → API Keys / Webhooks | The **live** path: real webhooks, real test-mode Payment Links | The batch demo runs fully; it never calls Razorpay |

**Backend** (terminal 1):

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # every value is optional — it runs empty
uvicorn app.main:app --port 8000
```

**Dashboard** (terminal 2) — React + Vite:

```bash
cd frontend
npm install
npm run dev                   # opens http://localhost:5173
```

Press **Run batch**. ~16 seconds for 250 cases. No API keys needed.

Or skip the UI entirely: `cd backend && python seed.py --reset`

```bash
cd backend && python -m pytest tests -q     # 70 tests, ~40s
cd frontend && npm run build                # production bundle -> dist/
```

---

## How it works

```
                    ┌─────────── virtual clock ────────────┐
                    │  the scheduler advances time so that │
                    │  retries, backoff and quiet hours    │
                    │  are real rather than instantaneous  │
                    └──────────────────────────────────────┘
                                     │
  webhook ─┐                         ▼
           ├──►  DETECT ──►  DIAGNOSE ──►  DECIDE ──►  ACT ──►  OBSERVE
  batch  ──┘      │             │            │          │         │
                  │        rules → LLM   ┌───┴────┐     │      recovered?
                  │        → human       │stopping│     │      promise kept?
                  │                      │playbook│     │         │
                  └──────────────────────┤complian├─────┴─────────┘
                                         └───┬────┘   re-scheduled
                                             ▼
                                   hash-chained audit log
```

Every stage writes to an append-only audit log where each entry commits to the
hash of the one before it. `GET /audit/verify` recomputes the whole chain and
reports the exact sequence number where it diverges, if it does.

### Detect
Normalises a Razorpay webhook or a generated case into one shape, so the live
path and the demo path run **identical** downstream code.

### Diagnose — [`pipeline/diagnose.py`](backend/app/pipeline/diagnose.py)
Three tiers, in cost order:

| Tier | Handles | Share |
|---|---|---|
| Rule engine | exact match on Razorpay's `error.reason` | **86.4%** |
| LLM | free-text descriptions the rules can't match | ~13.6% |
| `UNKNOWN` → human | neither tier is confident | remainder |

The rule engine goes first because it is free, instant, deterministic and
auditable — an LLM would be worse on every axis that matters here. The model is
asked only when the rules *abstain*, is constrained to a closed enum by a JSON
schema, must return a confidence, and is overridden to `UNKNOWN` below a 70%
floor. With no API key configured the LLM tier is simply off and its cases go to
humans — the agent never guesses about money.

### Decide — [`pipeline/decide.py`](backend/app/pipeline/decide.py)
Three gates, in this order: **stopping rules** → **playbook** → **compliance**.

Compliance runs *last* on purpose. It can then **defer** an already-chosen action
to a legal time rather than veto the case — a quiet-hours message goes out at
09:00 instead of being cancelled. In this batch that's 159 deferrals that kept
both the rule and the revenue.

No LLM is consulted anywhere in this file. Which customer gets charged or chased
must be reconstructible from a table months later.

### Playbooks — [`policy/playbooks.py`](backend/app/policy/playbooks.py)
Each root cause has an ordered, time-spaced ladder, escalating by channel and by
cost: free and silent first (a gateway retry costs ₹0 and annoys nobody), then
messaging, then a ₹2.50 voice call, then a human.

The taxonomy carries *properties*, not just names — `auto_retry_safe`,
`needs_new_instrument`, `hard_stop` — so the agent reasons about a failure
rather than looking it up. **Retrying an expired card is structurally impossible
in this system**: no such step exists in that ladder, and compliance blocks it
independently.

### Act — [`pipeline/act.py`](backend/app/pipeline/act.py)
Executes it. In live mode that's a real Razorpay Payment Link whose `notes.case_key`
lets the `payment_link.paid` webhook close the case it belongs to. Copy is
generated per customer in Hinglish or English and **validated before sending** —
generated text that invents a discount, threatens, exceeds the channel's length
budget, or drops the payment link is discarded in favour of the deterministic
template.

### Stopping rules — [`policy/stopping.py`](backend/app/policy/stopping.py)
An agent that can start a recovery journey but never end one is a spam generator
with a database. Cases terminate as `recovered`, `escalated`, `suppressed`, or
`exhausted` — never silently dropped. Including a **cost ceiling**: the agent
refuses to commit a ₹45 human review to a ₹300 abandoned cart, because it is
measured on net recovery and has to be able to walk away.

---

## Where the lift actually comes from

The per-cause table is the honest version of the headline, and it includes the
rows where the agent adds nothing:

| Root cause | n | Agent | Baseline | Incremental |
|---|---:|---:|---:|---:|
| Overdue invoice — buyer cashflow | 3 | **66.7%** | 33.3% | ₹2,27,787 |
| Overdue invoice — stuck in AP process | 9 | **22.2%** | 11.1% | ₹99,991 |
| Insufficient funds | 28 | **46.4%** | 0.0% | ₹38,148 |
| OTP / 3DS auth failed | 24 | **29.2%** | 12.5% | ₹18,301 |
| Card expired | 15 | **46.7%** | 26.7% | ₹13,352 |
| Subscription auto-debit bounced | 10 | **60.0%** | 20.0% | ₹6,045 |
| Mandate revoked | 8 | **25.0%** | 0.0% | ₹3,137 |
| Gateway timeout | 18 | 83.3% | 77.8% | ₹2,913 |
| Card declined by bank | 17 | 29.4% | 29.4% | ₹286 |
| Risk blocked / disputed | 11 | 0.0% | 0.0% | ₹0 |
| Unclassified (LLM disabled) | 34 | 0.0% | 2.9% | −₹3,445 |

Four things worth reading off it:

**Timing is the biggest single lever.** Insufficient funds goes 0% → 46.4%. The
instrument works; the balance doesn't. The baseline re-presents five minutes
later into the same empty account. The agent defers the retry to the next likely
salary-credit date — a calendar heuristic, not a lookup of anyone's balance.

**The rupee total is dominated by a dozen invoices, and the interval says so.**
Those top two rows are 12 of 250 cases carrying ₹3.3 L of the ₹4.3 L headline,
because B2B invoices run ₹18k–₹2.4 L against retail's ₹200–₹13k. That's why the
confidence interval is wide and why the per-case win/loss count (44–15) is the
more robust signal.

**Gateway timeouts are where the agent is nearly worthless**, and it says so.
83.3% vs 77.8%. A transient fault just needs a retry, which the dumb cron already
does. Claiming credit there would be dishonest.

**Risk-blocked and disputed cases recover 0% in both arms — that's correct.**
Re-presenting a risk-declined payment is the behaviour card-scheme rules exist to
prevent, and dunning a live dispute is a legal risk. The compliant recovery rate
is zero, and encoding that is the feature.

---

## Compliance and stopping

| Guardrail | Rule |
|---|---|
| Quiet hours | No messaging 21:00–09:00 IST → **deferred**, not dropped |
| Voice window | Calls only 10:00–19:00 IST |
| Frequency cap | Max 3 contacts / 7 days, min 18h apart, per customer |
| Consent | Per channel (WhatsApp / SMS / voice), enforced in **both** arms |
| Do-not-contact | Absolute, enforced in **both** arms |
| Dead instrument | Never re-present an expired card or revoked mandate |
| Hard-stop causes | Risk-blocked, disputed, unclassified → humans only |
| Autonomy ceiling | Consumer collections over ₹50,000 need human approval |
| Cost ceiling | Never spend more than 12% of the amount at risk |
| Case age | Recovery windows: 3d checkout … 30d receivables |

The baseline arm runs the *same* evaluation but enforces only the legal floor
(do-not-contact, consent). Everything else it would have violated is **recorded**
— 610 violations in this batch, against the agent's 0. That comparison is the
compliance cost of the naive approach, in units of individual violations.

---

## What's real vs. simulated

Being precise about this is the point, not a disclaimer.

| Piece | Status |
|---|---|
| Webhook ingestion + HMAC signature verification | **Real** — constant-time compare, unverified webhooks rejected and logged |
| Root-cause diagnosis (rule engine) | **Real** — deterministic, 86.4% coverage |
| LLM diagnosis of the ambiguous tail | **Real** when `ANTHROPIC_API_KEY` is set; off and escalating to humans otherwise |
| Recovery Payment Link generation | **Real** Razorpay test-mode API call on the live path |
| Recovery confirmation | **Real** on the live path — closed by the `payment_link.paid` webhook |
| Hinglish message generation + validation | **Real** — generated and validated, or the deterministic template |
| Compliance, stopping rules, audit chain | **Real** — same code on both paths |
| **Whether a simulated customer pays** | **Simulated** — see below |
| Voice delivery (TTS) | **Not built** — `act.py` produces the spoken script; wiring it to ElevenLabs/Sarvam is the remaining step |
| SMS/WhatsApp delivery | **Not built** — the agent owns the message and the cadence; the send is a stub |

### The outcome model, stated plainly

In batch mode there is no real customer to pay us, so whether an attempt
succeeds comes from [`sim/outcome.py`](backend/app/sim/outcome.py). Its base
rates are plausible order-of-magnitude assumptions for the Indian payments
market — **not measured constants**, and I won't claim otherwise.

What makes the comparison meaningful anyway is that **both arms are scored by the
same model on the same customers with the same random draws**. The absolute
recovery rate inherits whatever bias the base rates carry; the *difference*
between two policies scored identically is far more robust to them.

Three commitments in that file:
1. **The action has to matter.** Probability is a function of cause, action,
   channel, timing, fatigue and customer. Retrying an expired card scores
   exactly `0.0` — a re-presentment against an expired card is declined
   deterministically, not unluckily.
2. **Common random numbers**, so the arms are paired.
3. **The agent cannot see any of it.** `sim_propensity`, `sim_funds_at` and
   `sim_reachable` are read only by the outcome model.
   [`tests/test_no_oracle_leak.py`](backend/tests/test_no_oracle_leak.py) greps
   every pipeline, policy and AI module to enforce that — an agent that could
   peek at the ground truth would post spectacular, meaningless numbers.

**Deliberately conservative choices:** escalated cases count as *not recovered*
even though a real collections desk closes a share of them (₹11.81 L handed off,
claimed as zero). Recovery is reported net of contact cost. Cases still open at
the horizon are counted as failures, not "pending".

---

## What broke while building this

The buildathon asks what broke and how it was fixed. These are the four that
changed the design, not typos.

**1. The agent's decisions had no effect on outcomes.** In the first version,
`act.py` rolled a dice keyed only on root cause — send a reminder or regenerate a
link, the probability was identical. The entire Diagnose stage was decorative,
and no measurement of "AI judgment" was possible. Fixed by making probability a
function of the action, the channel and the timing, and by adding the baseline
arm so the effect is *visible* rather than asserted.

**2. `MAX_RETRIES = 3` was unreachable code.** The pipeline ran once per event,
end to end, so `retry_count` never exceeded 1. There was no time dimension at
all, which meant no retry sequencer, no backoff, no quiet hours that could ever
bind, and no intent decay. Fixed with the virtual-clock scheduler in
[`orchestrator.py`](backend/app/pipeline/orchestrator.py); a batch now simulates
two weeks of recovery journeys in about a second.

**3. A compliance short-circuit let the baseline message do-not-contact
customers.** `test_do_not_contact_is_absolute` failed on 2 cases. The cause: the
advisory `hard_stop` rule was evaluated *before* the legal floor, and under the
baseline arm it returned ALLOW and returned early — skipping the DNC check
entirely. Fixed by moving do-not-contact and consent to the top of
`evaluate()`, unconditionally: **a rule that must never be softened cannot sit
downstream of one that can.**

**4. The audit chain failed to verify on a clean run.** Every entry's hash
mismatched. SQLite has no native datetime type and returns naive values, so
`occurred_at.isoformat()` produced `...+00:00` on write and a different string on
read. Fixed by normalising to UTC and dropping the offset before hashing, so the
digest depends on the instant rather than on how the driver spells it.

**5. "Deterministic for a given seed" was false, and a test said it was true.**
This README claimed a seeded batch reproduces exactly. It didn't: cases were
generated relative to `datetime.now()`, and several policy behaviours are
calendar-dependent — salary-cycle retries target the 1st/2nd/7th/15th,
receivables outreach skips weekends, quiet hours depend on local time. Running
`--seed 42` on two different days moved the headline by ~₹15,000. The existing
`test_batches_are_reproducible` couldn't catch it, because both of its runs
happen in the same second. Fixed by anchoring a seeded batch to a fixed epoch
(a Monday, mid-month, so the horizon spans two weekends and both salary
windows), plus a new test that asserts the anchor rather than re-running the
batch twice in a row.

Also worth noting: **the ₹50,000 autonomy ceiling initially made the agent lose
by ₹1,06,018.** It was blocking every B2B invoice reminder, so the agent escalated
receivables while the baseline chased them and won. A consumer-collections
control had been applied to contractually-due invoices against known
counterparties. Scoping it to non-receivables turned a ₹106k loss into a ₹432k
win — and the failure was only visible *because* the baseline arm existed.

And a smaller one caught by rendering the UI headlessly: the dashboard drew its
chart with Chart.js from a CDN, and `Chart is not defined` left the panel
silently blank when the CDN was unreachable. The chart is now hand-drawn SVG and
the whole page runs offline.

---

## Testing with real Razorpay failures

```bash
ngrok http 8000     # public URL for the webhook
```

Razorpay Dashboard → Settings → Webhooks → point at
`https://<ngrok>/webhooks/razorpay`, subscribe to `payment.failed`,
`payment_link.paid`, `payment_link.expired`, `subscription.charged.failed`.

1. Create a Payment Link or use hosted checkout in test mode
2. Pay with a documented failing test card/UPI ID
   ([test details](https://razorpay.com/docs/payments/payments/test-card-upi-details/))
3. The webhook arrives → signature verified → diagnosed → a recovery Payment
   Link is created by a real API call → the case appears on the dashboard
4. Pay the recovery link → `payment_link.paid` closes the case as recovered

`POST /recovery/tick` advances every case whose next action is due; in production
a scheduler calls it on an interval, which is what turns the single webhook-time
action into a multi-step journey.

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /webhooks/razorpay` | Live, signature-verified ingestion |
| `POST /recovery/tick` | Advance every case whose next action is due |
| `POST /batch/run?n=250&seed=42` | Run a batch through both policy arms |
| `POST /batch/reset` | Wipe all data |
| `GET /dashboard/metrics` | Headline numbers, lift, bootstrap CI, compliance |
| `GET /dashboard/events` | Case list, filterable, with the paired arm's status |
| `GET /dashboard/events/{id}/trail` | Full decision trail + the baseline's journey |
| `GET /dashboard/timeline` | Cumulative recovery per arm |
| `GET /dashboard/policy` | The entire policy, served as data |
| `GET /audit/verify` | Recompute the hash chain, report the first divergence |
| `GET /audit/log` | Paginated audit entries |

Interactive docs at `http://localhost:8000/docs`.

---

## Layout

```
backend/app/
  taxonomy.py            root causes and their PROPERTIES — the file to read first
  config.py              tunables + POLICY_VERSION, stamped on every decision
  audit.py               hash-chained, tamper-evident log + verifier
  metrics.py             arm comparison, per-cause attribution, bootstrap CI
  pipeline/
    detect.py            normalise webhook or batch into one shape
    diagnose.py          rule engine → LLM → human
    decide.py            stopping → playbook → compliance
    act.py               execute, score, record
    orchestrator.py      virtual-clock scheduler; the retry sequencer lives here
  policy/
    playbooks.py         per-cause intervention ladders (declarative)
    compliance.py        quiet hours, consent, caps, autonomy ceiling
    stopping.py          when to give up, and why
  ai/
    client.py            Claude wrapper: degrades to None, counts spend
    diagnose_llm.py      enum-constrained classification of the ambiguous tail
    message_llm.py       Hinglish copy + validation + deterministic fallback
  sim/
    generator.py         synthetic cases; never emits the true root cause
    outcome.py           the outcome model — read this before trusting a number
backend/tests/           70 tests: invariants, audit tampering, oracle leaks

frontend/                React 18 + Vite, Razorpay light theme
  src/App.jsx            composition + data loading + #case= deep links
  src/theme.css          design tokens taken from razorpay.com's own stylesheet
  src/lib/api.js         every backend call, through a proxied /api
  src/components/
    Verdict.jsx          the headline number and its confidence interval
    ArmCompare.jsx       agent vs baseline, four measures
    RecoveryChart.jsx    hand-drawn SVG step chart with crosshair
    LiftByCause.jsx      diverging bars, negatives included
    CaseTable.jsx        filterable case list
    CaseDrawer.jsx       the drill-down: diagnosis, ladder, trail, paired arm
    CompliancePanel.jsx  what the guardrails stopped, both arms
```

### On the dashboard

The page opens with **"What this agent does"** — the five pipeline stages as a
strip, each carrying its real count from the run that just happened. It's the
architecture and the throughput in one object, so a reviewer can see what the
system is before any of the numbers have to mean something. **"Walk through one
case"** jumps straight into a worked example: a card-expired failure where the
baseline re-presents a card that cannot succeed (scored 0.0%) while the agent
goes to a new instrument, deferring the message out of quiet hours on the way.

Colours are Razorpay's own — navy `#192839`, brand blue `#305eff`, page
`#f8fafc`, hairline `#dfe3e9` — read out of their live stylesheet rather than
eyeballed. Their red `#f0263c` is reserved strictly for error and negative
states, so it never doubles as a data series; the baseline series is a burnt
orange instead. That blue/orange pair was run through a colour-vision validator
against a white surface: worst-case CVD separation ΔE 33.8, normal-vision 38.3,
both above 3:1 contrast.

The chart is hand-drawn SVG with no charting library. It's a **step** function
because recovery arrives as discrete payments — a smoothed line would imply
money trickling in continuously between them.

Clicking any case opens its full decision trail, and the URL carries it
(`#case=29`), so a specific trail can be linked to in a writeup or a review.
