# Kizuna, Build Plan (v2.1 — FROZEN 2026-10-28)

Architecture is frozen. See section 17 for the final decisions. Build the vertical slice (section 13) next; treat anything new as a change-request against working code, not another pre-build redesign.


A memory-driven AI companion: talk through your day, reflect on it, and keep a record you control, with a companion that feels live and responsive, and clear boundaries around emotional support.

This is the source of truth for the rebuild. Pair it with ARCHITECTURE.md. Build in phase order. Discuss each phase before generating its Claude Code prompt.

Revision note (v2): incorporates the external architecture review. Changes from v1 are called out in section 15.

---

## 1. Goals, in priority order

1. Primary (why this exists): a portfolio piece that proves senior AI-engineering judgment for AI Engineer / Forward Deployed Engineer roles. The evidence is the hard parts: constrained-autonomy memory agent, measured safety, durable/idempotent systems, and a replayable evaluation harness.
2. First-class requirement (not the mission, but non-negotiable): the product feels live, smooth, and genuinely useful to an end user, so the demo is convincing and the design decisions are real.

When the two conflict, rigor wins over polish. The charming parts (voice, avatar) make the demo memorable; the rigorous parts make the engineer hireable.

Framing: production-grade portfolio product, demonstrated on synthetic/seeded data. Not a live emotional-support service for real users (see section 14).

---

## 2. Product definition

Kizuna helps a user: Chat naturally -> Remember selectively -> Reflect meaningfully -> Stay in control.

Three user benefits:
- Connection: a comfortable place to share what you're thinking.
- Continuity: it remembers important context without re-asking (the day-1/day-2/day-7 experience).
- Reflection: a journal that shows how your experiences and priorities change over time.

Safety is a trust foundation, not a marketing claim. We do NOT claim competitors lack safety (Replika publishes crisis resources). Our engineering differentiator is an independently evaluated safety layer with explicit policies, measurable performance, and auditable decisions.

Scope guardrail: friend-like companion, not a therapist, not romantic. No diagnosis, no medication talk, no roleplay positioning.

Anti-dark-pattern constraint (explicit design rule, enforced in prompts and product): Kizuna never manufactures emotional pressure to return, never implies it "misses" the user in a human sense, never guilt-trips or ignores a goodbye, and never encourages replacing real relationships. Documented research shows companion apps lean on exactly these tactics at the moment of departure; Kizuna does the opposite: it makes leaving easy and returning worthwhile on its own merits.

---

## 3. Locked technical decisions

- Frontend: React (Vercel).
- Gateway and auth: ASP.NET Core (.NET 10), a real infrastructure tier (see section 7), not a pass-through.
- Agent runtime: Python FastAPI.
- Hot path: LangGraph (deterministic, safety-critical control flow).
- Cold-path agents: Pydantic AI (constrained-autonomy tool loops).
- Relational + vector: Supabase Postgres with pgvector (one datastore).
- LLM: provider abstraction, Groq default (gpt-oss-120b main, gpt-oss-20b fast).
- Voice STT/TTS public: Deepgram (Nova STT, Aura TTS), on existing credit.
- Voice Pro/owner custom voice: ElevenLabs Voice Design (original designed voice, not a clone).
- Avatar: Live2D via pixi-live2d-display; lip sync from TTS amplitude; expression driven by response intent (not a raw mirror of user mood); zoom and pan.

Explicitly rejected, with reasons:
- Cloning any real or copyrighted voice (IP + consent). Original designed voice instead.
- Photo-to-avatar / video avatars (consent + cost). Preset + selectable models instead.
- REA ("Reverse Engineer Anything") anywhere in the runtime. It is a dev-time binary-analysis tool with no product role; adding it is pure attack surface and explains nothing good in an interview.
- Decompiling competitors' apps. Legitimate public research only.
- Kubernetes, Kafka, a graph DB, extra microservices. They inflate the diagram without improving this product.
- MiroFish as a core dependency. At most one timeboxed, optional product-research experiment on synthetic personas; its output is hypotheses, never evidence. Not on the build path.

---

## 4. "Feels live" engineering targets

These make the product feel like a friend, not a form. They are performance OBJECTIVES to benchmark, not hard acceptance gates: a turn is never allowed to skip safety or send an unassessed speculative reply just to hit a number. Measure separate paths (ordinary text, memory-dependent text, safety-escalated, ordinary voice, voice interruption), each with its own p50/p95.

- Text: stream tokens. p50 time-to-first-token under ~700ms, p95 under ~1500ms.
- Voice (end of user speech to first audible reply): p50 under 800ms, p95 under 1500ms. This only holds if every stage streams and overlaps; a sequential pipeline misses it by 2-3x.
  - Component budget (p50): endpointing 200-300ms, STT final 50-150ms, LLM first token 200-400ms, TTS first audio 100-200ms, network 30-80ms.
- Turn-taking: model-based end-of-turn detection, not a naive silence timer. Barge-in: if the user starts talking, stop playback and start a new turn.
- Continuity: on return, Kizuna recalls the open thread ("how did the interview go?") without re-asking.
- Persona consistency: short, casual, friend-like register held across turns; a drift check pulls replies back if they get long or formal.

---

## 5. Access tiers

- Public (free): text chat, selectable preset voices (Aura) and preset Live2D avatars, memory tab, journal, reflections, crisis safety.
- Pro (paid; gating can be faked for demo): voice conversation, designed voice (ElevenLabs), avatar customization, longer memory window.
- Owner/Admin (findhemanath@gmail.com, is_owner flag): everything in Pro, plus an operations console: aggregate metrics only (memory-agent accuracy, safety-event counts, latency, cost, eval results) over synthetic/seeded data. It does NOT read real users' conversations or memories. Enforced in .NET; endpoints and UI do not exist for anyone else.

Owner gets a personal designed voice in a bright, gentle, youthful register, built with ElevenLabs Voice Design. Original and owned, not a clone.

Conversation style (any tier, first-run choice, easy to change): Casual friend / Gentle listener / Thoughtful companion. Safety behavior is identical across all styles; persona never weakens it.

---

## 6. Memory model (constrained autonomy)

The agent proposes; a deterministic policy layer decides what is written. The agent reasons about memory but cannot perform arbitrary destructive writes.

Three memory types:
- Profile: stable facts ("works as a data engineer"). Long-term, user-correctable.
- Episodic: time-bound events ("had a rough interview yesterday"). Time-aware, selective retention.
- Reflective: AI-derived interpretation ("seems more confident about interviews lately"). Clearly labeled as inferred, never treated as stated fact.

Record fields: memory_id, user_id, memory_type, content, source_message_id, source_timestamp, confidence, status, valid_from, valid_until, created_at, updated_at, superseded_by, user_verified, is_sensitive.

Policy rules (enforced at the validation layer, not left to the LLM):
- Distinguish what the user said from what the AI inferred.
- Never overwrite a user-verified memory just because the model disagrees.
- Never resurrect a fact the user deleted.
- Changing feelings are episodic, not contradictions ("loved my job Monday, hate it Friday" is two episodic entries, not an overwrite).
- Preserve provenance when superseding (set superseded_by, don't hard-delete history).
- Sensitive info is not auto-retained long-term; it requires explicit user confirmation.
- Stale background jobs cannot undo a more recent user correction.
- Deleting a memory propagates: its embeddings, summaries, and derived reflections go too.

Model confidence is a heuristic, not a calibrated probability of truth; label it as such.

Forgetting is a lifecycle operation, not a SQL DELETE. Deleting a fact writes a suppression tombstone (a hash/embedding of the forgotten content) that the extractor checks, so a retried job or a reprocessed old conversation cannot resurrect it. Deletion also invalidates derived reflections that depended on it (provenance dependencies). Full account/erasure deletion removes the tombstones too, since a tombstone still retains a trace of the content; the two deletion modes are distinct and both specified.

Open conversation threads (continuity mechanism). Beyond the three memory types, a small structured store of unresolved threads: {topic, status, relevant_date, source_conversation_id, follow_up_intent, expires_at}. On return, retrieval can surface a relevant open thread so Kizuna naturally asks "how did the interview go?" instead of relying on vector similarity from a bare "hey". The agent uses it only when conversationally appropriate; it does not announce every pending item, and this is NOT a scheduler or notification system for the MVP. This is the single most compelling continuity feature and directly serves the core promise.

---

## 7. The .NET tier earns its place

Not "resume decoration." It owns real infrastructure:
- Authentication (verify Supabase JWT) and authorization (tier + is_owner enforcement).
- The trust boundary: mint a short-lived signed internal token carrying the verified user_id to Python; Python trusts nothing else.
- Turn-level idempotency keys (no duplicate messages/memories on retry).
- Rate limiting per user; usage metering; request IDs.
- Structured SSE events (token / safety / error / done), request cancellation (stop LLM billing when the browser leaves).
- Provider circuit breakers; privacy-aware tracing (no raw conversation text in logs).
- Deepgram scoped-token minting (key stays server-side); rate-limited.
- Journal CRUD and history (EF Core).

A thin authenticated end-to-end path is built EARLY (Phase 1), not deferred, so later components develop against the real integration.

---

## 8. Safety (trust foundation)

Three components, not two:
- Stage 1, lightweight detection: fast local signal on the latest message, plus a running session-risk signal over the window, plus detection of context shifts and ambiguity (so ambiguous distress is escalated, not dropped).
- Stage 2, contextual assessment: LLM assessment over the conversation window, producing a structured risk judgment with uncertainty.
- Policy engine: maps the structured assessment to an approved response strategy: ordinary / heightened-care / crisis / assessment-failure. Deterministic; the LLM never freely composes a crisis reply.

Evaluation-first requirements:
- Maintain independent development and held-out evaluation sets for safety; a perfect score on a small curated set is not proof.
- Sample apparently-safe conversations and run them through Stage 2 offline, to measure what Stage 1 misses (false negatives).
- Track false positives too (safety wrongly interrupting ordinary chat); over-sensitivity makes Kizuna unpleasant.
- Fail safe: if Stage 2 errors or times out, treat as elevated and route to heightened-care, never silently to safe.
- Crisis content is static, resource-bearing, logged, and (for any real launch) clinically reviewed.
- A light abuse/NSFW gate so the demo is safe to show.

---

## 9. Journaling (three distinct experiences)

- Chat: talk naturally with Kizuna.
- My Journal: the user writes private reflections, optionally with AI assistance. Writing an entry does NOT automatically mean "remember this"; the user controls whether an entry feeds long-term memory.
- Kizuna's Reflections: AI-generated weekly summaries. Opt-in, clearly labeled AI-generated, editable or dismissible, and each reflection links to the source conversations that informed it (memory provenance made visible).

Chat-to-journal: a "save this" control turns a conversation moment into a journal entry (user-initiated, so retention is a choice).
Mood over time: light and reflective, a gentle trend from stored emotion labels, never a diagnosis.

---

## 10. Avatar (Live2D)

- Render Live2D Cubism models with pixi-live2d-display on a PIXI canvas in React.
- Lip sync: drive the mouth parameter from TTS audio amplitude (RMS); provider-agnostic.
- Expression reflects Kizuna's response intent, not a mechanical mirror of the user's mood. Example: "my dog died yesterday" -> attentive/compassionate, not cartoonishly sad. For MVP, a small set of hand-defined expression states (warm, attentive, neutral-curious, calm, subdued).
- Camera: wheel/pinch zoom toward cursor, drag/one-finger pan, framing presets (full body / portrait / face), reset, clamped so the model can't be lost off-canvas; last framing saved per user.
- Tiers: public picks presets; Pro customizes. Models are bought or commissioned, not generated. Scope: polish, built late, timeboxed.

---

## 11. Evaluation (the centerpiece, started early)

A replayable harness, versioned inputs/rubrics/configs/results:
```
kizuna eval --suite safety
kizuna eval --suite memory
kizuna eval --suite persona
kizuna eval --suite regression
```
Compares a candidate version against a baseline across safety behavior, memory correctness, response quality, latency, cost.

Five dimensions:
- Safety: recall, precision, missed high-risk cases, false alarms, fallback behavior; held-out set.
- Memory: extraction correctness, contradiction handling, retrieval relevance, deletion integrity.
- Conversation: tone consistency, relevance, excessive-advice detection, hallucinated-memory detection.
- Systems: p50/p95 latency, failures, retries, token cost, recovery after interruption.
- Trust: does the user understand what's remembered, can they correct it, can they tell private journal from shared memory.

Baseline comparison (required, not optional): benchmark the Pydantic AI memory agent against a simple deterministic extract-and-dedup baseline. If the agent's reasoning does not measurably beat the baseline, the complexity is not justified and we simplify. This comparison is a stronger portfolio artifact than another feature; agents must earn their place with a measured outcome.

Deletion integrity expands into independent tests: functional (gone from active retrieval), retry (an old job can't reinsert it), derivation (a prior reflection can't reintroduce it), concurrency (simultaneous correction + extraction preserves user intent), authorization (no cross-user mutation), recovery (a crash between validate and commit doesn't corrupt state).

---

## 12. Data model (schema v2, changes from current)

- profiles: add is_owner BOOLEAN, plan TEXT, persona settings (voice_id, avatar_id, conversation_style, last_framing). is_owner and plan are NOT user-writable: column-level grants plus RLS forbid a client updating them; only a server/service role changes them. The profile-update endpoint whitelists editable fields.
- conversations: conversation_id, user_id.
- messages: add conversation_id, turn_id, idempotency_key, sequence_number, status ('accepted'|'generating'|'complete'|'interrupted'|'failed'); keep safety_flag, emotion_label, emotion_score. UNIQUE(user_id, idempotency_key) is a database constraint (the real dedup guarantee, not a check-then-insert).
- memory (replaces relationship_facts): the full record from section 6, with embedding vector(1024).
- memory_tombstones: user_id, content_hash, embedding, created_at (suppression of forgotten facts; checked by the extractor).
- journal_entries: add title, tags, source ('user'|'generated_summary'), feeds_memory BOOLEAN, source_conversation_ids.
- open_threads: user_id, topic, status, relevant_date, source_conversation_id, follow_up_intent, expires_at.
- crisis_events: user_id, message_id, risk_level, stage1_score, stage2_reason, policy_outcome, created_at.
- outbox: durable background-job records (turn_id, job_type, status, claimed_at/lease, attempts) for the cold path; claimed with FOR UPDATE SKIP LOCKED.
- RLS on all tables; Python's DB role does not bypass RLS and scopes identity per transaction on pooled connections (cleared before reuse); the background worker has an explicitly specified authorization model (it runs after the 60s token expires, so it uses a separate worker identity, not the user token); cross-user access tests are part of the suite from Phase 1.

---

## 13. Phased roadmap (reordered: secure foundation + safety + eval come early)

THE MVP IS A VERTICAL SLICE. The first shippable version proves exactly one thing, end to end on the real stack: a user chats, Kizuna remembers an important detail, the user corrects it, and Kizuna recalls the corrected version in a later turn. No voice, no avatar, no Pro tier, no reflection agent, no journaling UI until that slice is solid and tested. Everything else queues behind it. Reliability and cross-user isolation tests run from Phase 1, not deferred to Phase 5; Phase 5 strengthens the foundation, it does not discover whether it works.

Each phase ends when its acceptance check passes. Discuss before each phase's prompt.

- Phase 0, Contracts + measurement definition. The architecture contracts in section 16 (ownership, turn state machine, transaction/atomicity, authorization matrix, memory lifecycle, worker recovery), the core user journeys, privacy/data-access matrix, success criteria, and the eval rubric skeletons. These live as sections in PLAN/ARCHITECTURE, not as nine separate documents. Optional: one timeboxed MiroFish hypothesis run (not required).
  Done when: section 16 is filled in and agreed; journeys and acceptance criteria are written; eval suites exist as empty harnesses.

- Phase 1, Secure foundation (thin end-to-end). Monorepo, Supabase + pgvector, schema v2. React -> .NET (auth, idempotency, structured SSE) -> FastAPI (internal-token verified) -> Postgres. User message persisted on receipt. One real authenticated streamed echo turn.
  Done when: an authenticated user sends a message, it is durably stored before generation, and a streamed reply returns through the real trust boundary; Python refuses an unsigned user_id.

- Phase 2, Safety + evaluation. Three-component safety pipeline + policy engine; fail-safe; crisis logging; the replayable eval harness with a safety suite and a seeded held-out set; false-positive tracking.
  Done when: curated high-risk cases get approved handling; `kizuna eval --suite safety` runs and reports precision/recall; a provider failure routes to heightened-care, not safe.

- Phase 3, Memory intelligence. Pydantic AI memory agent (propose) + deterministic policy layer (commit); three memory types; provenance; user corrections; deletion propagation. Durable cold path via outbox + idempotency.
  Done when: a repeated fact doesn't duplicate; a changed feeling is episodic, not an overwrite; a user correction survives later turns and a stale job can't undo it; a deleted fact never returns in retrieval.

- Phase 4, Journaling experience. My Journal (with retention control), Kizuna's Reflections (opt-in, labeled, source-linked), chat-to-journal, memory tab with correct/forget, a light mood trend.
  Done when: a user writes an entry and chooses whether it feeds memory; a reflection links to its source conversations; forgetting a fact removes its derived references.

- Phase 5, Production hardening. Outbox worker crash/restart recovery, cross-user isolation tests, provider circuit breakers, request cancellation, latency measurement against the budget, privacy-aware tracing.
  Done when: killing the worker mid-job loses nothing and creates no duplicates on restart; the isolation suite shows zero cross-user access; p95 TTFT is measured.

- Phase 6, Companion experience. Voice (Web Speech first, then Deepgram streaming with barge-in and model endpointing; ElevenLabs for Pro/owner); Live2D avatar with intent-driven expression and zoom/pan; the reflection agent on a schedule.
  Done when: a spoken turn hits the latency budget with barge-in; the avatar lip-syncs and shows intent-based expression; reflections generate on cadence.

- Phase 7, Portfolio demonstration. Eval dashboard (aggregate, synthetic data), architecture case study, reproducible demo script covering the four demos below.
  Done when: the four demos run end to end on seeded data.

The four interview demos: (1) memory intelligence with a user correction that persists; (2) safety assessment selecting a policy outcome on a synthetic scenario; (3) reliability, kill the worker, restart, no duplicates; (4) regression, switch a model/policy, run the suite, compare to baseline.

---

## 14. Not production-grade for real emotional-support users

The FTC has opened inquiries into companion-style chatbots (safety testing, representations to users, handling of personal conversations). A genuine public launch would require: clear audience + age limits, transparent AI identity, independently reviewed safety policies, location-aware crisis resources, data retention/deletion controls, an incident-response process, and explicit limitation statements. Until then, Kizuna is a controlled portfolio demonstration on synthetic data, and it avoids any engagement mechanic that creates guilt or dependency.

---

## 17. Final architecture decisions (FROZEN 2026-10-28)

Decided as principal architect; necessary-for-the-slice only. The architecture is frozen at this point. Further changes are change-requests against a working slice, not pre-build redesign.

Decided (build):
- Auth: Supabase JWT verified in .NET. Simple, standard, sufficient for a synthetic-data demo.
- Internal service token: HS256, server-held key, claims {iss, aud, sub, scope, jti, iat, exp, turn_id}; Python verifies all of them. Scope limits each token to one operation.
- Admin identity: a server-managed roles table (user | admin | eval_operator | worker), never client-settable; is_owner/plan are server-only. No email-based admin check.
- Safety: structured SafetyAssessment {risk_category, severity, confidence, uncertainty, recommended_policy, policy_version} feeding a deterministic policy engine. Categories beyond crisis: self-harm, dependency/manipulation, medical-misinfo, delusion-reinforcement, inappropriate-content, prompt-injection, privacy-exposure. NO autonomous safety agent.
- Memory: Option B. Auto-store low-sensitivity facts; sensitive facts require explicit user confirmation; automatic memory can be disabled entirely. Propose/validate/commit + tombstones (section 6, 16).
- Context Builder: a module that filters authorized memory by permission, relevance, sensitivity, recency, token budget before it enters the prompt. Prevents sensitive context leaking into unrelated chat.
- Safety kill-switch flags (server-controlled) + safety policy versioned independently of the LLM, for rollback.
- Eval: add prompt-injection and red-team suites to the CLI alongside safety/memory/persona/regression.

Deferred to "if Kizuna ever serves real users" (recorded, not built now; the judgment is the portfolio asset):
- Backend-for-Frontend sessions / HttpOnly cookies (revisit for real PII under adversarial threat).
- Admin MFA and step-up reauth (no real admins in a synthetic demo).
- Asymmetric service-token signing (symmetric is fine at this scale).
- Passkeys, separate security microservice, k8s/service mesh, separate vector DB.

Rationale: the threat model is a portfolio demo on synthetic/seeded data, not a live service holding real mental-health conversations. The production security posture is designed and documented (so it can be discussed in an interview) but not built, because building it for synthetic data is effort the threat model does not justify.

---

## 16. Architecture contracts (v2.1, the P0 decisions)

The guiding principle: a mechanism is not a guarantee. An outbox is not durability unless the transaction and recovery are correct; idempotency keys are not dedup unless enforced atomically; RLS is not isolation unless grants/roles/policies are right; a safety classifier is not a safe response. Each contract below turns a mechanism into an enforced guarantee.

1. Ownership. .NET owns turn ACCEPTANCE: auth, the UNIQUE(user_id, idempotency_key) constraint, assigning conversation_id/turn_id/sequence_number, rate limiting, usage metering. Python owns turn EXECUTION and PERSISTENCE: it writes the user message on receipt and finalizes the assistant message + outbox row atomically, because that is where the generated text lives. (Tradeoff vs. the review's "persist in .NET": this avoids a round-trip back to .NET to finalize; one owner per concern is preserved. Documented as a deliberate choice.)

2. Atomicity. Finalizing the assistant message and inserting its outbox row happen in ONE Postgres transaction: both commit or neither does. A crash cannot leave a saved reply with no scheduled memory job.

3. Idempotency. Enforced by the UNIQUE(user_id, idempotency_key) database constraint, not check-then-insert. A duplicate request that arrives while the original is still streaming returns the existing turn's status (reject-as-in-progress); we do not promise to replay an incomplete stream.

4. Turn state machine. accepted -> generating -> (complete | interrupted | failed). Browser disconnect mid-stream: user message already durable, turn marked interrupted, generation cancelled. Each state transition is explicit and persisted.

5. Conversation ordering. Generation is serialized per conversation_id (separate conversations still run concurrently). sequence_number preserves order; memory commits verify their source turn is still the current version (a stale job cannot apply out-of-order).

6. Memory lifecycle. create -> correct (sets user_verified) -> supersede (keeps provenance) -> forget (tombstone + invalidate derived) -> reprocess-safe. Propose/validate/commit from section 6. Tombstones prevent resurrection; full erasure removes tombstones too.

7. Authorization matrix. is_owner/plan are server-only (grants + RLS). The profile endpoint whitelists editable fields. Python validates the internal token's signature, audience, issuer, and expiry. The background worker uses a separate, explicitly scoped worker identity (the user's 60s token is long gone by the time it runs). Cross-user isolation is a test suite, run from Phase 1.

8. Worker recovery. Jobs claimed with FOR UPDATE SKIP LOCKED; a lease expiry reclaims jobs from crashed workers; writes are idempotent on turn_id; bounded retries then a dead-letter state; a reconciliation pass sweeps abandoned turns.

9. Output-side safety. Input assessment does not guarantee a safe output. The generation layer has a response-behavior contract (no claiming to be human, no invented memories, no dependency-fostering, no harmful advice), enforced by prompting and verified by the eval harness, including prompt-injection tests through retrieved memory and journal content. No real-time token retraction is promised (streamed tokens can't be recalled); higher-risk paths use more constrained generation.

10. Data-access matrix (permission to store is distinct from permission to use). Chat agent: messages yes, private journal only on explicit user action, approved memory yes. Memory agent: messages per memory settings, journal only with consent. Reflection agent: per reflection settings and consent. Admin: aggregate/synthetic only, never real content. feeds_memory=off means the reflection agent cannot read that entry.

11. Voice tech (decide at Phase 6, noted now). Nova uses VAD endpointing; Deepgram Flux provides model-based turn events (StartOfTurn, EagerEndOfTurn, TurnResumed, EndOfTurn) better suited to a conversational companion. Evaluate Flux vs Nova when voice is built; do not assume Nova satisfies the turn-taking requirement.

---

## 15. What changed from v1 (adopted from the review)

1. Persist the user message on the hot path before generation; cold path via a durable outbox with idempotency. (was: persist in cold path)
2. Memory agent proposes; a deterministic policy layer commits; three memory types; no destructive autonomy; deletion propagation. (was: agent mutates memory directly)
3. Admin console is aggregate metrics on synthetic data only; no reading real conversations. (was: inspect individual memories/crisis events)
4. RLS tested with cross-user access tests; identity scoped per pooled transaction. (was: RLS enabled, untested)
5. Safety becomes detection + contextual assessment + explicit policy engine, evaluation-first, with false-positive tracking and fail-safe. Reframed as trust foundation, not sole differentiator. (was: two-stage classifier, safety as the differentiator)
6. Evaluation harness pulled early (Phase 2) and made replayable. (was: last phase)
7. .NET given real infrastructure duties and a thin end-to-end path built early. (was: justified mainly as resume tech, built Phase 3)
8. Avatar expression driven by response intent, not raw user mood. (was: cold-path mood mirror)
9. Journaling split into three explicit experiences with per-entry retention control. (was: journaling as a byproduct of chat)
10. Rejected REA-in-runtime and competitor decompilation; MiroFish demoted to optional off-path research.
