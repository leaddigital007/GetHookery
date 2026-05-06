# Kubricon Outreach Playbook

> Single source of truth for what we send, on what channel, with what
> message. The `llm_draft_outreach` command renders this playbook into
> per-partner drafts; this document is the human-readable spec of the
> same logic so anyone can audit a draft, fix it, or write one by
> hand.

---

## 0. Founder facts (the brief)

These are the only facts that may appear in any outreach copy.

- **Founder**: Igor Skobletskyi — **solo founder**.
- **Partner-investor**: ~20% equity, acts as a **strategic advisor**
  on capital strategy. Mentioned in deck only; not surfaced in cold
  outreach.
- **Operating team** (not founders): PM, motion designer, 2× frontend,
  QA. Mentioned only in deck Q&A, not in outreach.
- **Round**: $1M Pre-seed on a SAFE with **$10M valuation cap** (May
  2026). Check size $50k–$500k.
- **Product**: AI creative production tooling. Multi-model AI
  generation (Veo, Kling, Seedance, Flux, GPT Image) + director-level
  control (reference characters, products, brand kits) across four
  studios (Reference Studio, Marketing Studio, Storyframes, Kubricon
  Cinema).
- **Differentiator vs Runway / Pika / Veo**: brand-controlled studio
  workflow, not single-shot generation.
- **ICP**: performance-marketing teams (DTC brands, SMM/growth,
  eCommerce running paid social on Meta/TikTok/Google) + prosumer
  creators producing brand-consistent short-form.
- **Comparables**: Higgsfield ($200M run-rate), Freepik ($230M ARR),
  Artlist ($300M ARR), InVideo ($70M ARR).
- **Traction (use one fact per message, sized to channel)**:
  - 256 active users at 37.8% activation
  - $202 MRR (6 paying)
  - 659 signups, US is the #1 organic geo
  - $61K spent over 9 months
- **Voice**: first-person singular only. **"I am building"**, **"I
  have"**, **"I am raising"**.

---

## 1. Forbidden (instant fail)

- ❌ "co-founder", "my co-founder and I", "we built", "we have",
     "operating duo", any "we / our" framing as if multiple founders.
- ❌ "MyHomeQuote", "ClickDealer", any prior-employer credit.
- ❌ "Hope this finds you well", "I came across your fund",
     "I am a big fan of your work", any other AI tell / generic filler.
- ❌ Emojis. Markdown. Bullet points.
- ❌ Inventing partner-specific facts, claiming features not in the
     brief, aggregating numbers we don't have.
- ❌ Begging language ("would mean a lot", "would love the
     opportunity"). Peer-level only.

---

## 2. The 5-step body (every channel uses the same skeleton)

| # | Step | What goes here |
|---|---|---|
| 1 | **Hook** (1 sentence) | Quote a phrase from the fund's stated thesis verbatim ("creative class", "consumer brands", "creator economies", "commerce enablement", "applied AI", "performance marketing"). If nothing in the thesis maps to Kubricon, open with a category-level hook ("AI creative tooling for performance marketing teams") and lower confidence. |
| 2 | **What Kubricon is** (1–2 sentences) | "Brand-controlled AI creative production for performance-marketing teams. Multi-model + reference characters + brand kits + studio workflow." Add one differentiator: "vs Runway / Pika / Veo: control + consistency, not single-shot generation." |
| 3 | **Traction** (1 fact) | Pick one from the brief, sized to channel. NEVER aggregate. |
| 4 | **Round** (optional, 1 short clause) | "Raising $1M pre-seed on a $10M SAFE cap." Drop in DM-short. |
| 5 | **Soft CTA** | "Open to a 15-min call?" or "Happy to share the deck if useful." |

Sign-off: emails close with `Best, Igor`. DMs do not need a sign-off.

---

## 3. Channel-specific length budgets

| Channel | Max length | Steps included | Notes |
|---|---:|---|---|
| **X / Twitter DM** | ≤270 chars | 1, 2 (folded), 3, 5 | Drop step 4. Often fold steps 2 and 3 into one sentence. |
| **LinkedIn DM** | 600–1100 chars | 1, 2, 3, 4, 5 | All five steps fit. No greeting line, no sign-off. |
| **Email** | 150–200 words | 1, 2, 3, 4, 5 | Paragraph form, sign-off "Best, Igor". Subject line under 70 chars, ideally encoding step 1's hook. |
| **Submission form** | per-form | the email body, plus structured fields from `submission_kit.md` | Use the email_body for "Why now / Why us" free-text. Use `submission_kit.md` for structured fields (problem, solution, traction, team, ask, etc.). |

---

## 4. Channel selection rule (which to pick first)

For a given Person + Fund, the suggested channel is picked in this
priority order — implemented in `apps/investors/admin_worklist.py`:

1. **`fund.submission_url` is set** → use the submission form. The
   form usually expects a long-form pitch — paste the email_body into
   the "Why now / Why us" field, fill structured fields from
   `submission_kit.md`.
2. **`fund.contact_email` is set** → cold email to that address with
   the email_body and the subject_line.
3. **`person.linkedin_url` is set** → LinkedIn DM with the long DM body.
4. **`person.twitter_handle` is set** → X DM with the short DM body.
5. **`person.email` is personal/known** → cold email to the partner
   directly (rarer; usually only for angels).
6. **None of the above** → manual research; the worklist flags this
   row in red.

Why this order: a published submission form is the highest-signal
intent ("we read these"); a published `info@`/`partners@` is second
(reads but lower priority); a partner's LinkedIn is third (real
person, formal); X is fourth (real person, informal); none means we
haven't earned a channel yet.

---

## 5. Personalisation calibration (confidence)

The `confidence` flag on every draft tells the human reviewer how
much trust to put in step 1 of the body.

| Level | When | Reviewer action |
|---|---|---|
| **high** | Hook quotes a fund-stated phrase that directly maps to Kubricon's space (consumer brands / creator economy / AI creative / DTC / commerce / performance marketing). | Send mostly as-is; sanity-check spelling of the partner name. |
| **medium** | Fund's thesis is in a broader applied-AI / SaaS / growth space; the hook is plausible but partly inferred. | Read once, tighten one sentence if needed. |
| **low** | Thesis is empty, generic, or in an unrelated vertical (biotech, climate hardware, robotics). The hook is category-level, not fund-specific. | **Manual review required**. Either replace the hook with something you know about the fund / partner, or skip this row and use the slot on a higher-confidence target. |

---

## 6. The cycle a Person walks through

1. **Identified** (default for every imported NFX/OpenVC row).
2. **Researched** — we know fund, role, channel.
3. **Contacted** — Igor or partner clicked "Mark sent" in the
   worklist. Timestamp + channel + +7d follow-up auto-set.
4. **Replied** — partner answered. Click "Mark replied" in the
   worklist; advance the rest of the conversation in the Person
   admin (Replied → Meeting → DD → Term sheet → Closed).
5. **Passed / Closed-lost** — they declined or went silent past two
   follow-ups. Move to Passed.

The worklist surfaces:

- "Today / primary contacts" — Tier-S/1 PRIMARY persons who haven't
  been contacted yet.
- "Today / other partners" — same funds, secondary contacts (use
  only after primary doesn't respond in 5–7 days, or as a parallel
  touch on a different channel).
- "Follow-up overdue" — sent, follow-up date passed, no reply.
- "Awaiting reply" — sent, follow-up still in the future.
- "Replied" — conversation started.

---

## 7. Two-person collaboration

`Person.assigned_to` ∈ {Unassigned, Igor, Partner, Shared}.

Workflow: at the start of each work session, filter the worklist to
your `owner` and grab a slice (the page already sorts by tier > LLM
score > check size). Mark sent as you go. The other owner sees the
remaining queue minus your taken rows.

If a fund has multiple partners, only the **PRIMARY** contact should
be assigned for the first touch. Add a secondary partner only after
the primary has gone silent for 5+ days.

---

## 8. What "looks right" — a worked example

Hook quoting Sugar Capital's stated thesis:

> Since you invest in the future of commerce, I wanted to share what
> I am building for DTC and retail performance marketing teams. I am
> building Kubricon, a brand-controlled AI creative production
> platform. Unlike Runway or Veo which focus on single-shot
> generation, I give growth teams director-level control through
> reference characters, brand kits, and a multi-model studio
> workflow. This allows eCommerce operators to produce
> brand-consistent video ads at scale. I currently have 256 active
> users with a 37.8 percent activation rate, and the US is my top
> organic geography. I am raising a $1M pre-seed on a $10M SAFE cap
> to scale the platform. Are you open to a 15-min call, or happy to
> share the deck if useful?

Why this works:

- Step 1 (hook) quotes "future of commerce" verbatim from Sugar
  Capital's thesis.
- Step 2 names the product and the differentiator vs Runway/Veo in
  one sentence.
- Step 3 uses one traction fact (256 active / 37.8%).
- Step 4 has the round line.
- Step 5 has a low-friction CTA.
- "I am building" / "I have" / "I am raising" — first-person
  singular, no "we".
- No "co-founder", no MyHomeQuote, no AI filler.

---

## 9. What "looks wrong" — anti-examples

- "My co-founder and I previously scaled MyHomeQuote from zero to
  $3.5M in monthly revenue" → factually wrong + forbidden.
- "Hope this finds you well — I came across your fund and was
  impressed by your portfolio" → AI tell + zero personalisation.
- "We are building Kubricon, the future of AI video" → "we" framing
  + hyperbole, no specifics.
- "I would love the opportunity to discuss further" → begging.

---

## 10. Where to change this

Anything in this document that's wrong should be changed in two
places:

1. This file (`data/outreach_playbook.md`) — the human spec.
2. `apps/llm/prompts.py` — the `KUBRICON_THESIS` and
   `DRAFT_OUTREACH_SYSTEM` constants. After changing those, run
   `python manage.py llm_draft_outreach --tiers S,1 --force-refresh
   --apply --concurrency 8` to regenerate every draft.

If a fact about Kubricon (round size, traction number, founder
structure) changes, update **both** places before sending more
outreach. The CRM does not auto-detect drift; humans do.
