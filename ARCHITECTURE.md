# Kizuna, Architecture and Workflow (v2, detailed)

Companion to PLAN.md. Deep design: topology, trust boundary, graph state, workflows, API surface, memory policy layer, safety policy engine, durable cold path, the "live feel" latency budget, the avatar subsystem, failure modes, and deployment.

Revision note (v2): reflects the external review. Key shifts: persist-before-generate, propose/validate/commit memory, policy-engine safety, durable outbox, aggregate-only admin, tested RLS, and a measured latency budget. Changes listed in section 15.

---

## 1. System topology

```
                         Browser (React, Vercel)
                         - chat (SSE), voice (WS direct to Deepgram)
                         - Live2D canvas, journal, memory tab
                         - owner: aggregate ops dashboard
                               |
             Supabase JWT      |  HTTPS
                               v
                    ASP.NET Core gateway (.NET 10)
                    auth, authz, tier + is_owner enforcement,
                    idempotency, rate limit, request IDs, usage metering,
                    structured SSE, cancellation, circuit breakers,
                    privacy-aware tracing, Deepgram token mint,
                    journal CRUD, stream relay
                               |
             60s signed internal token (verified user_id)
                               v
                    Python agent (FastAPI, private network)
                    LangGraph hot path (streaming), Pydantic AI cold agents,
                    safety, RAG, provider abstraction
                               |
                               v
                    Supabase Postgres (+ pgvector)
                    RLS on; agent role does NOT bypass RLS

External: Groq (LLM), Deepgram (STT/TTS), ElevenLabs (Pro voice),
HuggingFace models (in the Python process).
```

Voice audio never transits .NET or Python. The browser streams audio straight to Deepgram using a short-lived scoped token .NET mints. Only transcribed text enters the backend. This is what keeps voice inside the latency budget.

---

## 2. Component responsibilities

### React
Auth (Supabase client). Chat with SSE streaming. Voice (scoped-token WS to Deepgram, barge-in). Live2D canvas (lip sync, intent expression, zoom/pan). Three journaling surfaces (Chat / My Journal / Kizuna's Reflections). Memory tab (view, correct, forget). Owner: aggregate ops dashboard only.

### ASP.NET Core (the infrastructure tier, section 7 of PLAN)
Sole identity authority. Mints the internal token and Deepgram scoped tokens. Idempotency, rate limiting, request IDs, usage metering, structured SSE, request cancellation, provider circuit breakers, privacy-aware tracing. Journal CRUD + history. Relays the Python stream. Enforces tier + is_owner.

### Python (FastAPI)
Verifies the internal token on every call; never trusts a raw user_id; the browser cannot reach it. Runs the LangGraph hot path and the Pydantic AI cold agents. Owns safety, RAG, embeddings, provider abstraction.

### Postgres
Relational + pgvector in one store. RLS on all tables; agent connects with a non-bypass role; identity scoped per transaction on pooled connections; cleared before reuse.

---

## 3. Trust boundary and internal token

1. Browser -> .NET with the Supabase JWT.
2. .NET verifies signature/expiry against Supabase JWKS; extracts the real user_id.
3. .NET reads plan + is_owner from profiles.
4. .NET mints an internal JWT (signed with INTERNAL_SIGNING_KEY; claims {sub: user_id, plan, is_owner, exp: now+60s}).
5. .NET -> Python with that token.
6. Python verifies it and reads user_id from the token only. Missing/invalid -> 401.

Must be tested: an automated cross-user suite asserts that user A can never read or write user B's rows, including through pooled connections and through retrieval/summaries.

---

## 4. Hot-path graph state (LangGraph)

```
KizunaState:
  turn_id: str                    # created by .NET, carried through
  idempotency_key: str
  user_id: str                    # from internal token
  plan: str; is_owner: bool
  session_id: str
  conversation_style: str         # casual | listener | thoughtful
  messages: list                  # hydrated window
  # safety
  stage1_signal: dict | None      # score + ambiguity + context-shift flags
  session_risk: float | None
  stage2_assessment: dict | None  # structured risk + uncertainty
  policy_outcome: str | None      # ordinary | care | crisis | assessment_failure
  # routing + context
  standalone_query: str | None
  needs_retrieval: bool | None
  memory_context: list[str] | None
  retrieved_context: list[str] | None
  # output
  response_intent: str | None     # drives avatar expression
  final_response: str | None
  message_status: str | None      # complete | interrupted
```

Emotion is computed cold; response_intent (for the avatar) is derived on the hot path from the safety/context assessment, not from a raw mood mirror.

---

## 5. Workflows

### 5.1 Text turn (persist-before-generate)

Ownership: .NET owns acceptance (auth, UNIQUE(user_id, idempotency_key), assign conversation_id/turn_id/sequence_number); Python owns execution + persistence (writes the user message, finalizes assistant message + outbox atomically). Generation is serialized per conversation_id.

```
Browser --POST /chat {text, idempotency_key}--> .NET
.NET: verify JWT; check plan
.NET: INSERT turn with UNIQUE(user_id, idempotency_key)  <== dup -> return existing turn status (reject-as-in-progress)
.NET: assign conversation_id, turn_id, sequence_number; mint internal token
.NET --/chat/stream (internal token)--> Python
Python: verify token (sig, aud, iss, exp); build state
  PERSIST user message durably (status accepted)   <== before any generation
  hydrate: window + profile + session_risk
  safety stage1: signal on latest + session_risk + ambiguity/context-shift
     clearly safe -> skip stage2
     else -> safety stage2 (assessment over window)
  policy engine: assessment -> policy_outcome
     crisis -> static crisis reply (stream) -> log crisis_event -> finalize -> outbox -> END
  ordinary/care:
     plan: one fast structured call -> {standalone_query, needs_retrieval}
     retrieve (if needed): embed -> pgvector (memory + corpus) -> top-k
     derive response_intent
     generate: STREAM tokens  <== SSE: token events
  ATOMIC: finalize assistant message (status complete) + INSERT outbox row  <== one transaction, both or neither
Python --structured SSE (token/safety/error/done)--> .NET --> Browser
```

Atomicity is the guarantee, not the outbox itself: a crash cannot leave a saved reply with no scheduled memory job. The .NET gateway emits the final `done` event only after that transaction commits.

If the browser disconnects mid-stream, the user message is already durable and the turn is marked interrupted; nothing is lost. Cancellation stops further LLM billing.

### 5.2 Crisis turn
Static, resource-bearing, non-generated reply on the same SSE channel. Logged to crisis_events with stage scores, reason, and policy_outcome. Fail-safe: if Stage 2 errors/times out, policy_outcome = care (never silently ordinary).

### 5.3 Voice turn (browser-direct, inside the 800ms budget)

```
Browser --GET /voice/token--> .NET: Deepgram scoped token (short TTL, rate-limited)
Browser --WS audio--> Deepgram STT (direct)
  turn detection: evaluate Deepgram Flux (model-based turn events: StartOfTurn,
  EagerEndOfTurn, TurnResumed, EndOfTurn) vs Nova (VAD endpointing) at Phase 6;
  Nova is NOT assumed to satisfy conversational turn-taking
Browser --final transcript + idempotency_key--> .NET  (then 5.1)
Streamed reply text --> Browser --> Deepgram Aura TTS (direct; ElevenLabs for Pro/owner)
Browser: Web Audio RMS -> Live2D mouth param
Barge-in: user speech during playback stops audio and opens a new turn
```
Budget (p50, must stream+overlap): endpointing 200-300 / STT 50-150 / LLM first token 200-400 / TTS first audio 100-200 / net 30-80 => ~600-850ms. Sequential pipelines miss by 2-3x.

### 5.4 Durable cold path (outbox, idempotent)

```
Worker claims outbox rows with FOR UPDATE SKIP LOCKED (no two workers take the same job)
 per turn_id (idempotent on turn_id; safe to retry):
   tag_emotion (analytics/journal; NOT the avatar driver)
   memory pipeline (5.5)
 mark outbox row done
 lease expiry reclaims jobs from crashed workers; bounded retries -> dead-letter; reconciliation sweeps abandoned turns
```
Worker identity is a separate, explicitly scoped role (the user's 60s internal token is long expired by the time cold work runs). A crash mid-job leaves the row claimed-but-un-done; the lease expires and it is reprocessed without duplicating.

### 5.5 Memory pipeline: propose -> validate -> commit (constrained autonomy)

```
Candidate extraction (fast LLM): stated facts, tagged stated vs inferred
  per candidate:
    embed -> search_memory(top_k)        # Pydantic AI tools, READ-ONLY to the agent
    agent proposes ONE op: NEW | UPDATE | SUPERSEDE | EPISODIC_APPEND | NOISE
  --- agent cannot write; it only proposes ---
POLICY VALIDATION LAYER (deterministic) enforces:
    - never overwrite user_verified on model disagreement
    - never resurrect a user-deleted fact
    - changed feelings -> EPISODIC_APPEND, not SUPERSEDE
    - sensitive -> do not auto-retain; require explicit user confirm
    - preserve provenance (set superseded_by; keep history)
    - a newer user correction beats a stale job (timestamp guard)
  --- before commit, extractor checks memory_tombstones: a forgotten fact is not re-created ---
COMMIT the validated op (profile | episodic | reflective); concurrency guard: the source turn must still be current
```

Memory types, tombstones, open threads, and record fields are in PLAN sections 6 and 16, and the schema in section 7 here. Forgetting writes a tombstone and invalidates derived reflections; a stale cold job cannot undo a newer user correction (timestamp/version guard).

### 5.6 Reflection agent (scheduled, opt-in)
Reads messages since last_summarized_at, plans 1-3 themes, gathers facts (+ optional corpus snippet), writes a labeled reflection into journal_entries with source_conversation_ids. Opt-in; editable/dismissible; shows its sources.

### 5.7 Journaling
- My Journal: user writes; feeds_memory defaults off; if on, the entry enters 5.5 as stated input.
- Chat-to-journal: user-initiated "save this" creates an entry from a message.
- Reflections: 5.6.

### 5.8 Memory tab + deletion propagation
View facts with provenance and stated/inferred labels. Correct -> sets user_verified. Forget -> deletes the row AND its embedding, any summary referencing it, and derived reflections; a retrieval regression test asserts it never returns.

### 5.9 Owner ops console (aggregate only)
```
/admin/overview  active sessions, turns (counts)
/admin/safety    safety-event metrics (counts, precision/recall on eval sets) - no identifiable content
/admin/memory    memory-agent accuracy/error rates (aggregate)
/admin/cost      tokens + voice minutes -> dollar estimate
/admin/eval      latest suite results vs baseline
```
No endpoint returns a real user's conversation or memory content. Demos use seeded synthetic accounts.

---

## 6. API surface

### .NET (public, JWT-gated)
POST /chat (SSE; idempotency_key) ; GET /history ; GET/PATCH/DELETE /memory ; GET/POST/PATCH/DELETE /journal ; POST /feedback ; GET /voice/token ; GET/PATCH /profile (style, voice_id, avatar_id; custom pro-gated) ; GET /admin/* (is_owner).

### Python (internal, internal-token-gated)
POST /chat/stream (SSE) ; POST /cold/run (outbox worker entry) ; POST /reflect/run (scheduled).

SSE event types: token, safety (policy_outcome), error, done.

---

## 7. Data model and access
Tables: profiles (is_owner/plan server-only), conversations, messages (conversation_id, turn_id, idempotency_key UNIQUE(user_id,idempotency_key), sequence_number, status), memory (typed, provenance, confidence, status, valid_from/until, superseded_by, user_verified, is_sensitive, embedding), memory_tombstones (content_hash, embedding), journal_entries (title, tags, source, feeds_memory, source_conversation_ids), open_threads (topic, status, relevant_date, follow_up_intent, expires_at), crisis_events, outbox (lease, attempts).
RLS on all; non-bypass agent role; per-transaction identity on pooled connections; cross-user tests in the suite. Secrets server-side only (Groq, Deepgram, ElevenLabs, INTERNAL_SIGNING_KEY, DATABASE_URL).

---

## 8. Avatar subsystem (Live2D)
Rendering: pixi-live2d-display on a PIXI.Application; idle breathing/blink loops.
Lip sync: Web Audio AnalyserNode RMS -> ParamMouthOpenY, smoothed/clamped; provider-agnostic.
Expression (intent, not mirror): response_intent from the hot path maps to a small hand-defined set (warm, attentive, neutral-curious, calm, subdued); crossfade; neutral baseline.
Camera: wheel/pinch zoom toward cursor; drag/one-finger pan; presets full-body/portrait/face; reset; offsets clamped; last framing saved to profile.
Sourcing: preset (free) / selectable (pro); models bought or commissioned. Built late, timeboxed.

---

## 9. Streaming mechanics
Text: structured SSE end to end (token/safety/error/done); React reads the stream; .NET can cancel on disconnect.
Voice: WS browser-direct to Deepgram; only text crosses the backend.

---

## 10. Concurrency and durability
Stateless hot path: hydrate from Postgres each turn (no in-memory history). Connection pool with per-transaction identity. Durable outbox for all cold work; idempotency keys make retries safe. Optional LangGraph checkpointer later; hydrate+persist is enough for MVP.

---

## 11. Failure modes and fallbacks
- Groq generation fails: retry/backoff -> fast model -> safe static message; turn still persisted.
- Stage 2 fails/times out: fail safe to care, never ordinary.
- Retrieval empty above threshold: generate ungrounded (legitimately empty).
- Deepgram token mint fails: fall back to Web Speech API for that session.
- Browser disconnects mid-stream: user message already durable; mark interrupted; cancel generation.
- Worker crash: outbox row stays un-done; reprocessed idempotently on restart.
- Provider outage: circuit breaker opens; degrade gracefully; no silent loss of accepted messages.
- Model OOM: single-worker lifespan load for MVP; documented path to hosted inference.

---

## 12. Deployment
Frontend: Vercel. .NET: Render or Azure. Python: Render, single worker, models loaded at startup. Postgres + pgvector: Supabase. Cold path: outbox worker process (MVP: a loop/BackgroundTask reading outbox; later arq/RQ). Reflection: scheduled trigger -> /reflect/run.

---

## 13. "Feels live" budget (reference, objectives not gates)
Objectives to benchmark, never a reason to skip safety or send an unassessed speculative reply. Text TTFT p50 ~700ms / p95 ~1500ms. Voice first-audio p50 ~800ms / p95 ~1500ms, only if every stage streams and overlaps. Measure separate paths (ordinary text, memory-dependent text, safety-escalated, ordinary voice, voice interruption), each p50/p95. Model-based turn detection (Flux), not silence timers. Barge-in mandatory. Continuity: surface a relevant open thread on return. Persona drift check keeps replies short and casual.

---

## 14. Build-order mapping
Phase 1 -> sections 1,2,3,5.1 (thin E2E, persist-before-generate, trust boundary). Phase 2 -> 5.2 + safety policy engine + eval harness. Phase 3 -> 5.4,5.5,7,8(memory parts) (outbox + propose/validate/commit). Phase 4 -> 5.6,5.7,5.8 (journaling + deletion propagation). Phase 5 -> 10,11 (hardening, isolation tests). Phase 6 -> 5.3,8,13 (voice + avatar + latency). Phase 7 -> 5.9 + the four demos. Keep the trust boundary and persist-before-generate from Phase 1 on; do not defer.

---

## 15. What changed from v1
persist-before-generate (was cold-path persist); propose/validate/commit memory with three types and deletion propagation (was direct agent mutation); policy-engine safety, evaluation-first, fail-safe, false-positive tracking (was two-stage classifier); durable outbox + idempotency (was BackgroundTasks); aggregate-only owner console on synthetic data (was individual inspection); tested RLS + cross-user suite + pooled-identity scoping (was RLS enabled only); avatar expression from response intent (was mood mirror); .NET as real infra tier built early (was deferred proxy); measured latency budget added; REA-in-runtime and competitor decompilation rejected; MiroFish off the build path.
