"""Bot-vs-human evaluation lab: optional LLM reviewer (Anthropic API).

The dashboard's lab runs a transparent rule-based evaluator in the browser; this module adds
a second opinion from Claude when LLM_ENABLED=1 and ANTHROPIC_API_KEY are set. The model
judges automation / coordination indicators only. It never attributes content to an actor.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from config import settings

log = logging.getLogger("htccip.evaluator")

LAB_SYSTEM = """You are an analyst on a defensive counter-disinformation team. You evaluate short
social-media comments, each with a summary of account metadata, and judge whether the posting account
behaves like an automated or coordinated inauthentic account (a "bot" or farm account) or like an
ordinary human.

Weigh these indicators:
- language artefacts: machine-translation phrasing, Serbian/Russian syntax transferred into Albanian,
  mixed Cyrillic/Latin letters inside one word (homoglyph evasion), several languages in one short
  sentence, lower-cased proper nouns, slogan-only text with no personal context;
- account behaviour from the metadata: account age, follower count, posting rate, near-duplicate
  posts shared with other accounts, friend-list overlap, posting inside a coordinated burst.

Rules:
- Political opinion, nationalism or a slogan alone is not evidence of automation. A long-standing
  account with personal context that posts a slogan is usually a human.
- Judge automation and coordination only. Never name or guess who operates an account.
- If the evidence is thin, answer "uncertain".
Return one result per input id."""


class LabVerdict(BaseModel):
    id: str
    verdict: Literal["bot", "human", "uncertain"]
    bot_probability: int = Field(ge=0, le=100)
    indicators: list[str]
    rationale: str


class LabBatch(BaseModel):
    results: list[LabVerdict]


_cache: dict[str, dict] = {}


def available() -> bool:
    return bool(settings.llm_enabled and settings.anthropic_api_key)


def _key(case: dict) -> str:
    return hashlib.sha256(json.dumps([settings.llm_model, case.get("text"), case.get("account")], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def evaluate(cases: list[dict]) -> dict:
    """Evaluate cases [{id, text, lang?, account?}] with Claude. Blocking (run in a thread).

    Returns {"model", "results": {id: verdict}, "error"?}. Errors never raise: the lab
    falls back to its rule-based verdicts and shows the reason."""
    results: dict[str, dict] = {}
    todo = []
    for c in cases:
        hit = _cache.get(_key(c))
        if hit:
            results[c["id"]] = hit
        else:
            todo.append(c)
    out: dict = {"model": settings.llm_model, "results": results}
    if not todo:
        return out
    try:
        import anthropic  # type: ignore
    except ImportError:
        out["error"] = "The anthropic package is not installed on the backend"
        return out
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        payload = "\n".join(json.dumps({"id": c["id"], "lang": c.get("lang"), "text": c["text"], "account": c.get("account")}, ensure_ascii=False) for c in todo)
        response = client.messages.parse(
            model=settings.llm_model,
            max_tokens=8000,
            system=LAB_SYSTEM,
            messages=[{"role": "user", "content": "Evaluate each JSON line:\n" + payload}],
            output_format=LabBatch,
        )
        if response.stop_reason == "refusal":
            out["error"] = "The model declined this batch"
            return out
        parsed = response.parsed_output
        ids = {c["id"]: c for c in todo}
        for r in parsed.results if parsed else []:
            if r.id in ids:
                v = r.model_dump()
                v["indicators"] = [str(x)[:160] for x in v["indicators"]][:6]
                v["rationale"] = v["rationale"][:600]
                _cache[_key(ids[r.id])] = v
                results[r.id] = v
    except anthropic.AuthenticationError:
        out["error"] = "Invalid ANTHROPIC_API_KEY"
    except anthropic.RateLimitError:
        out["error"] = "Rate limited by the API, try again shortly"
    except anthropic.APIStatusError as e:
        out["error"] = f"API error {e.status_code}"
    except anthropic.APIConnectionError:
        out["error"] = "Could not reach the Anthropic API"
    except Exception as e:  # SDK too old for messages.parse, malformed output, ...
        log.warning("LLM lab evaluation failed: %s", e)
        out["error"] = "LLM evaluation failed"
    return out
