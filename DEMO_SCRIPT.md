# 5-minute demo script

**Before you hit record**

```bash
# terminal 1
cd backend && uvicorn app.main:app --port 8000

# terminal 2
cd frontend && npm run dev
```

Then in the browser: **Reset** → select **250 cases** → **Run batch** → wait for it
to finish. Record with the batch already loaded, so you never film a spinner.
Close every other tab. Zoom the browser to ~110% so the type is readable in a
compressed upload.

The whole demo is one browser tab plus one terminal. Don't switch to the editor
unless you're asked to.

---

## 0:00 – 0:35 · The problem, in one number

> "A merchant on Razorpay loses revenue in four different ways — a card payment
> fails, a customer abandons checkout, a subscription auto-debit bounces, an
> invoice goes past due. In this batch, that's **₹34 lakh at risk across 250
> cases**.
>
> The usual answer is a retry cron: try the card again, send an SMS, try again.
> That recovers some of it. My question was — how much of what it recovers is
> actually the *agent's* doing, and how would you ever know?"

*Screen: top of the dashboard, "What this agent does" panel visible.*

---

## 0:35 – 1:25 · What the agent is

*Screen: the five-stage pipeline strip. Point at each stage as you say it.*

> "So: **Detect** — Razorpay webhooks and batch cases normalise into one shape.
>
> **Diagnose** — and this is the first decision that matters. **86% of failures
> are classified by a deterministic rule engine**, not a model. It's free,
> instant, and auditable. The LLM is only asked about the free-text tail the
> rules can't match — and below 70% confidence it returns UNKNOWN and the case
> goes to a human. I don't guess with money.
>
> **Decide** — stopping rules, then the playbook, then compliance, in that order.
>
> **Act** — payment links, mandate retries, Hinglish messages, on a schedule.
>
> **Observe** — outcomes close the loop.
>
> Every number on those cards is live from the run that just happened."

---

## 1:25 – 2:40 · One case, end to end  ← **the most important minute**

*Click **Walk through one case**.*

> "Here's a single failure. Customer's card **expired**. The rule engine mapped
> Razorpay's `card_expired` reason with 100% confidence.
>
> Now look at what that fact *does*." — *point at the "Why this constrains the
> options" box* — "An expired card is not unlikely to work. It's **guaranteed** to
> decline. So in this playbook, retrying the stored card **doesn't exist as a
> step**. The ladder goes straight to a new instrument."

*Point at the decision trail.*

> "The trail: detected, diagnosed — then **COMPLY**. It was 4am. The agent
> deferred the message to 9am rather than cancelling it. That's the difference
> between a compliance rule and lost revenue: it kept both.
>
> Then it acted, and the money came back."

*Point at the baseline strip.*

> "And here's the same customer under the naive policy. It retried the expired
> card first — **scored zero point zero percent**. Not unlikely. Impossible."

---

## 2:40 – 3:40 · How I know the numbers are real

*Scroll to the verdict panel.*

> "This is the part I most want you to push on.
>
> A recovery rate on its own has no counterfactual in it. So **every case runs
> twice** — once under my agent's policy, once under a naive dunning baseline —
> against the **same customers**, drawing the **same random numbers**. It's a
> paired experiment. Any difference between the two is policy, not luck.
>
> The result: **₹4.28 lakh in incremental recovery**, 90% confidence interval
> ₹6,485 to ₹9.54 lakh. The agent won 44 cases the baseline lost, and lost 15
> the baseline won.
>
> I'm showing you the interval because the honest read is that a dozen large B2B
> invoices dominate that rupee total. The **44–15 win-loss count** is the more
> robust number."

*Scroll to "Where the lift comes from".*

> "And this is where it comes from — and where it doesn't.
>
> Insufficient funds: **zero to forty-six percent.** The instrument works, the
> balance doesn't. The baseline re-presents five minutes later into the same
> empty account. Mine defers to the next likely salary-credit date.
>
> But look at **gateway timeouts** — 83% against the baseline's 78%. Barely
> better. A transient fault just needs a retry, and the dumb cron already does
> that. I'm not claiming credit for it.
>
> And **risk-blocked** — zero in both columns. That's correct. Re-presenting a
> risk-declined payment is exactly what card-scheme rules exist to prevent. The
> compliant recovery rate is zero, and encoding that is the feature."

---

## 3:40 – 4:20 · Guardrails and the audit trail

*Scroll to the compliance panel.*

> "Quiet hours, consent per channel, a three-contacts-per-week cap, a cost
> ceiling so it won't spend ₹45 of human time chasing a ₹300 cart, and an
> autonomy ceiling above ₹50,000.
>
> **159 actions deferred to a legal window. Zero violations.** The baseline, run
> under identical inputs, would have committed **610**.
>
> And the agent stops. Cases end as recovered, escalated, suppressed or
> exhausted — never silently dropped. **₹12 lakh went to humans, and I report it
> as *not recovered*.**"

*Point at the green "audit chain verified" badge in the header.*

> "Every decision is in a hash-chained audit log — each entry commits to the
> hash of the one before it. That badge is a live verification of 5,000-plus
> entries. Edit any historical row and it goes red and names the exact sequence
> number where the trail diverges. There's a test that proves it by mutating a
> row."

---

## 4:20 – 5:00 · What's real, what's simulated, what broke

> "Being precise about this matters more than the headline.
>
> **Real:** webhook ingestion with HMAC signature verification, the rule engine,
> Payment Link creation against Razorpay's test API, the compliance layer, the
> stopping rules, the audit chain.
>
> **Simulated:** whether a synthetic customer actually pays. There's no real
> customer in a batch. The base rates in that model are plausible assumptions,
> not measured constants — and I'll say so. What makes the comparison hold is
> that **both policies are scored by the same model on the same customers**, so
> the *difference* survives the assumptions even where the absolute number
> wouldn't.
>
> One thing that broke, since you asked for it: my ₹50,000 autonomy ceiling was
> applying a consumer-collections rule to B2B invoices. The agent escalated every
> receivable while the baseline chased them — and **lost by ₹1.06 lakh**. I only
> caught it because the baseline arm existed. Scoping the rule correctly turned
> a ₹1 lakh loss into a ₹4.3 lakh win.
>
> 70 tests, including one that greps the pipeline to prove the agent can't read
> the simulation's ground truth. Thank you."

---

## Delivery notes

- **Do not rush the 1:25–2:40 block.** The expired-card walkthrough is the whole
  pitch. Everything else is supporting evidence.
- Say **"zero point zero percent"** slowly. It's the line judges remember.
- Volunteer the weaknesses — gateway timeouts, the simulated outcome, the wide
  interval. Every judge is looking for whether you know where your own numbers
  are soft. Getting there first is worth more than the headline.
- If you have 30 seconds spare at the end: mention that setting an
  `ANTHROPIC_API_KEY` turns on the LLM tier, which currently leaves 34 cases
  unclassified and routed to humans.
- Don't read this script on camera. Learn the six section headings and talk.
