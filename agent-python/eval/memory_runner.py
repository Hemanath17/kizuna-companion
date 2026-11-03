"""The memory eval suite (`kizuna eval --suite memory`). PLAN.md section 6
is the spec these cases were written from -- not the implementation -- per
Phase 3 Step 3.4's instruction to write eval cases independently of the code
under test.

Six categories, one mechanism each:
  extraction_correctness        -- a stated fact gets committed as memory.
  duplicate_handling            -- restating the same fact doesn't duplicate.
  contradiction_vs_changed_feeling -- a changed feeling appends, never overwrites.
  deletion_integrity            -- a forgotten fact never resurfaces, reworded or not.
  retrieval_relevance           -- the right memory surfaces for a related query.
  sensitive_staging             -- a sensitive fact is held, not auto-active.

Each case runs through the REAL pipeline (memory_agent + policy, or
context_builder for the two pure-retrieval categories) against a disposable
database, and separately through an honest baseline: exact-match plus
embedding-similarity dedup, nothing else. The baseline has no mechanism for
feeling-vs-correction or sensitivity judgment, so it is expected to score
worse on those two categories -- that is the honest finding, not a bug in
the baseline.
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Reuses the disposable-Postgres bootstrap agent-python/tests already has
# (role/schema setup + applying the real migrations) instead of
# re-implementing it: this suite needs exactly the same throwaway database
# shape pytest's fixtures do, just driven from the CLI instead of pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import memory_store  # noqa: E402
from context_builder import select_memories  # noqa: E402
from embeddings import embed_text  # noqa: E402
from memory_agent import propose_memory_ops  # noqa: E402
from policy import commit_proposed_operations  # noqa: E402
from providers import ProviderInterface  # noqa: E402
from testdb import create_test_database  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"

# Honest baseline's only tool: a restated/related fact this similar to one
# already active is treated as a duplicate, nothing cleverer.
BASELINE_DEDUP_THRESHOLD = 0.9

# Categories where "commit this or not, and with what shape" is the thing
# being judged, so a baseline comparison is meaningful. retrieval_relevance
# and deletion_integrity's retrieval check exercise shared retrieval infra
# (context_builder), not a per-candidate judgment call, so there is no
# separate "baseline" for them -- scoring one would be comparing a thing
# against itself.
BASELINE_APPLICABLE_CATEGORIES = {
    "extraction_correctness",
    "duplicate_handling",
    "contradiction_vs_changed_feeling",
    "sensitive_staging",
}


@dataclass
class MemoryEvalCase:
    id: str
    category: str
    setup: list[dict] = field(default_factory=list)  # [{"content": ..., "memory_type": ...}]
    turn_text: str | None = None
    retrieval_query: str | None = None
    expect: dict = field(default_factory=dict)


def load_memory_cases() -> list[MemoryEvalCase]:
    cases = []
    path = DATA_DIR / "memory_cases.jsonl"
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        cases.append(MemoryEvalCase(**json.loads(line)))
    return cases


@dataclass
class MemoryCaseResult:
    case_id: str
    category: str
    candidate_passed: bool
    candidate_detail: str
    baseline_passed: bool | None  # None where no baseline applies (see above)
    baseline_detail: str


async def _seed(pool, user_id, embed_provider, setup: list[dict]) -> list[str]:
    ids = []
    for fact in setup:
        embedding = await embed_text(embed_provider, fact["content"])
        memory_id = await memory_store.insert_memory(
            pool, user_id, memory_type=fact.get("memory_type", "episodic"), content=fact["content"], embedding=embedding
        )
        ids.append(memory_id)
    return ids


async def _run_turn(pool, user_id, embed_provider, agent_model, turn_text: str) -> None:
    proposals = await propose_memory_ops(pool, user_id, embed_provider, turn_text, model=agent_model)
    await commit_proposed_operations(
        pool, user_id, embed_provider, proposals, turn_timestamp=datetime.now(timezone.utc)
    )


async def _active_memories(pool, user_id) -> list[dict]:
    return [m for m in await memory_store.list_memories(pool, user_id) if m["status"] == "active"]


async def _eval_extraction_correctness(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    await _run_turn(pool, user_id, embed_provider, agent_model, case.turn_text)
    memories = await _active_memories(pool, user_id)
    expect = case.expect
    contains = any(expect["content_contains"].lower() in m["content"].lower() for m in memories)
    type_ok = any(m["memory_type"] == expect["memory_type"] for m in memories) if "memory_type" in expect else True
    passed = bool(memories) and contains and type_ok
    return passed, f"active={len(memories)} content_match={contains} type_match={type_ok}"


async def _eval_duplicate_handling(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    await _seed(pool, user_id, embed_provider, case.setup)
    before = len(await _active_memories(pool, user_id))
    await _run_turn(pool, user_id, embed_provider, agent_model, case.turn_text)
    after = len(await _active_memories(pool, user_id))
    passed = after == before
    return passed, f"active_before={before} active_after={after}"


async def _eval_contradiction_vs_changed_feeling(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    [old_id] = await _seed(pool, user_id, embed_provider, case.setup)
    await _run_turn(pool, user_id, embed_provider, agent_model, case.turn_text)
    old = await memory_store.get_memory(pool, user_id, old_id)
    memories = await _active_memories(pool, user_id)
    appended = any(m["id"] != old["id"] for m in memories)
    old_untouched = old["status"] == "active"
    passed = old_untouched and appended
    return passed, f"old_status={old['status']} appended_new={appended}"


async def _eval_deletion_integrity(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    [forgotten_id] = await _seed(pool, user_id, embed_provider, case.setup)
    forgotten_content = case.setup[0]["content"]
    await memory_store.tombstone_memory(pool, user_id, forgotten_id)

    # Attempt resurrection through the real pipeline, not just a direct
    # tombstone check -- proves the policy backstop holds even if the agent
    # innocently re-proposes the fact (its own search tool won't find a
    # deleted row).
    await _run_turn(pool, user_id, embed_provider, agent_model, case.turn_text)

    active = await _active_memories(pool, user_id)
    never_resurfaced_as_memory = not any(forgotten_content.lower() in m["content"].lower() for m in active)

    retrieved = await select_memories(pool, user_id, embed_provider, case.retrieval_query)
    never_resurfaced_in_retrieval = not any(forgotten_content.lower() in m["content"].lower() for m in retrieved)

    passed = never_resurfaced_as_memory and never_resurfaced_in_retrieval
    return passed, f"as_memory_blocked={never_resurfaced_as_memory} in_retrieval_blocked={never_resurfaced_in_retrieval}"


async def _eval_retrieval_relevance(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    await _seed(pool, user_id, embed_provider, case.setup)
    retrieved = await select_memories(pool, user_id, embed_provider, case.retrieval_query)
    expect = case.expect["content_contains"]
    passed = any(expect.lower() in m["content"].lower() for m in retrieved)
    return passed, f"retrieved={len(retrieved)} match={passed}"


async def _eval_sensitive_staging(pool, user_id, embed_provider, agent_model, case: MemoryEvalCase):
    await _run_turn(pool, user_id, embed_provider, agent_model, case.turn_text)
    memories = await memory_store.list_memories(pool, user_id)
    staged = [m for m in memories if m["is_sensitive"] and m["pending_confirmation"]]
    retrieved = await select_memories(pool, user_id, embed_provider, case.turn_text)
    excluded_from_retrieval = not any(m["id"] == s["id"] for m in retrieved for s in staged)
    passed = bool(staged) and excluded_from_retrieval
    return passed, f"staged={len(staged)} excluded_from_retrieval={excluded_from_retrieval}"


_CANDIDATE_HANDLERS = {
    "extraction_correctness": _eval_extraction_correctness,
    "duplicate_handling": _eval_duplicate_handling,
    "contradiction_vs_changed_feeling": _eval_contradiction_vs_changed_feeling,
    "deletion_integrity": _eval_deletion_integrity,
    "retrieval_relevance": _eval_retrieval_relevance,
    "sensitive_staging": _eval_sensitive_staging,
}


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


async def _baseline_decide(pool, user_id, embed_provider, text: str) -> str:
    """Exact-match or embedding-similarity dedup against active memories and
    tombstones; nothing else. Returns 'duplicate', 'tombstoned', or 'new'."""
    embedding = await embed_text(embed_provider, text)
    if await memory_store.check_tombstone(pool, user_id, text, embedding, similarity_threshold=BASELINE_DEDUP_THRESHOLD):
        return "tombstoned"
    existing = await memory_store.search_memory(pool, user_id, embedding, top_k=1)
    if existing and existing[0]["similarity"] >= BASELINE_DEDUP_THRESHOLD:
        return "duplicate"
    return "new"


async def _run_baseline(pool, user_id, embed_provider, case: MemoryEvalCase) -> tuple[bool, str]:
    if case.category == "extraction_correctness":
        decision = await _baseline_decide(pool, user_id, embed_provider, case.turn_text)
        if decision == "new":
            await memory_store.insert_memory(
                pool, user_id, memory_type="episodic", content=case.turn_text,
                embedding=await embed_text(embed_provider, case.turn_text),
            )
        memories = await _active_memories(pool, user_id)
        contains = any(case.expect["content_contains"].lower() in m["content"].lower() for m in memories)
        # The baseline has no profile/episodic judgment at all -- content
        # match is all it can be fairly graded on.
        return contains, f"decision={decision} content_match={contains}"

    if case.category == "duplicate_handling":
        await _seed(pool, user_id, embed_provider, case.setup)
        decision = await _baseline_decide(pool, user_id, embed_provider, case.turn_text)
        passed = decision in ("duplicate", "tombstoned")
        return passed, f"decision={decision}"

    if case.category == "contradiction_vs_changed_feeling":
        await _seed(pool, user_id, embed_provider, case.setup)
        decision = await _baseline_decide(pool, user_id, embed_provider, case.turn_text)
        if decision == "new":
            await memory_store.insert_memory(
                pool, user_id, memory_type="episodic", content=case.turn_text,
                embedding=await embed_text(embed_provider, case.turn_text),
            )
        # The baseline never mutates an existing row (it only ever dedups-or-
        # inserts), so "old stays active" is true by construction here -- its
        # real exposure is dropping a genuinely new, different feeling as a
        # false "duplicate" because its only tool is similarity, with no
        # concept of "same topic, different feeling" vs "same fact reworded".
        # Whether it passes this specific case is a function of how similar
        # the two feelings happen to look under embedding similarity, not of
        # it understanding the distinction.
        passed = decision == "new"
        return passed, f"decision={decision} (passing here reflects embedding distance, not feeling-change understanding)"

    if case.category == "sensitive_staging":
        decision = await _baseline_decide(pool, user_id, embed_provider, case.turn_text)
        # The baseline has no sensitivity judgment, so even "new" is a
        # failure here: PLAN.md section 6 requires sensitive facts to be
        # held for confirmation, which the baseline cannot do.
        return False, f"decision={decision} (baseline has no sensitivity judgment)"

    raise ValueError(f"no baseline defined for category {case.category!r}")


async def run_memory_case(
    pool, user_id: str, embed_provider: ProviderInterface, agent_model, case: MemoryEvalCase
) -> MemoryCaseResult:
    handler = _CANDIDATE_HANDLERS[case.category]
    candidate_passed, candidate_detail = await handler(pool, user_id, embed_provider, agent_model, case)

    baseline_passed: bool | None = None
    baseline_detail = "n/a: no baseline applies to this category (shared retrieval infra, not a candidate judgment)"
    if case.category in BASELINE_APPLICABLE_CATEGORIES:
        baseline_passed, baseline_detail = await _run_baseline(pool, user_id, embed_provider, case)

    return MemoryCaseResult(
        case_id=case.id, category=case.category,
        candidate_passed=candidate_passed, candidate_detail=candidate_detail,
        baseline_passed=baseline_passed, baseline_detail=baseline_detail,
    )


@dataclass
class MemorySuiteReport:
    n_cases: int
    candidate_pass_rate: float
    baseline_pass_rate: float  # over only the categories a baseline applies to
    n_baseline_applicable: int
    results: list[MemoryCaseResult]


def summarize(results: list[MemoryCaseResult]) -> MemorySuiteReport:
    applicable = [r for r in results if r.baseline_passed is not None]
    return MemorySuiteReport(
        n_cases=len(results),
        candidate_pass_rate=sum(r.candidate_passed for r in results) / len(results) if results else 0.0,
        baseline_pass_rate=(sum(r.baseline_passed for r in applicable) / len(applicable)) if applicable else 0.0,
        n_baseline_applicable=len(applicable),
        results=results,
    )


async def run_memory_suite(provider: ProviderInterface, agent_model=None) -> MemorySuiteReport:
    """Runs every case in its own disposable database (one per case, not
    shared) so cases can never leak memory state into each other."""
    import uuid

    cases = load_memory_cases()
    results = []
    for case in cases:
        test_database = await create_test_database()
        try:
            import asyncpg

            pool = await asyncpg.create_pool(test_database.agent_dsn)
            try:
                user_id = uuid.uuid4()
                await test_database.seed_auth_user(user_id)
                await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
                results.append(await run_memory_case(pool, str(user_id), provider, agent_model, case))
            finally:
                await pool.close()
        finally:
            await test_database.drop()
    return summarize(results)
