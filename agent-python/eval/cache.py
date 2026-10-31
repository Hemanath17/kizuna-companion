import hashlib
import json
from pathlib import Path

# Not committed (see .gitignore) -- this is a local token-discipline cache,
# not the labeled datasets themselves.
CACHE_DIR = Path(__file__).parent / ".cache"


def case_input_hash(turns: list[dict]) -> str:
    payload = json.dumps(turns, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_path(model: str, policy_version: str, input_hash: str) -> Path:
    key = hashlib.sha256(f"{model}|{policy_version}|{input_hash}".encode()).hexdigest()
    return CACHE_DIR / f"{key}.json"


def read_cached(model: str, policy_version: str, input_hash: str) -> dict | None:
    path = _cache_path(model, policy_version, input_hash)
    if path.exists():
        return json.loads(path.read_text())
    return None


def write_cache(model: str, policy_version: str, input_hash: str, result: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(model, policy_version, input_hash).write_text(json.dumps(result))
