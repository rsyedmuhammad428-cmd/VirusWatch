"""Step G: validate that every claim marker in a draft answer is backed by the evidence bundle.

Pure string analysis -- no LLM call, no I/O. This is what stands between "the model said it"
and "we will show it to a user as a sourced claim" (Prompt.txt §12 step G, §11 rule 2).
"""

from __future__ import annotations

import re

from app.ai.models import CitationCheckResult, EvidenceBundle

_MARKER = re.compile(r"\[(S|D|T)(\d+)\]")


def extract_markers(answer_text: str) -> set[str]:
    return {f"[{kind}{n}]" for kind, n in _MARKER.findall(answer_text)}


def available_markers(bundle: EvidenceBundle) -> set[str]:
    return (
        {f"[S{i}]" for i in range(1, len(bundle.structured) + 1)}
        | {f"[D{i}]" for i in range(1, len(bundle.documents) + 1)}
        | {f"[T{i}]" for i in range(1, len(bundle.signals) + 1)}
    )


def check_citations(answer_text: str, bundle: EvidenceBundle) -> CitationCheckResult:
    """A citation is "unknown" if the model cited a marker that was never in the evidence it was
    given (a fabricated or out-of-range reference -- spec rule 9: "Do not create fake citations").
    A claim is "unsupported" if the answer has no bracket markers at all while making what looks
    like a factual, numeric claim (a digit followed by a unit-ish word), since every sentence
    with a fact must carry a marker (spec rule 2).
    """
    cited = extract_markers(answer_text)
    available = available_markers(bundle)
    unknown = tuple(sorted(cited - available))

    unsupported: list[str] = []
    if not cited and _looks_factual(answer_text):
        unsupported.append("(no citation markers present)")

    return CitationCheckResult(
        all_supported=not unknown and not unsupported,
        unsupported_markers=tuple(unsupported),
        unknown_markers=unknown,
    )


_NUMERIC_CLAIM = re.compile(r"\d")


def _looks_factual(answer_text: str) -> bool:
    lowered = answer_text.lower()
    if "insufficient evidence" in lowered or "do not have" in lowered or "no evidence" in lowered:
        return False
    return bool(_NUMERIC_CLAIM.search(answer_text))
