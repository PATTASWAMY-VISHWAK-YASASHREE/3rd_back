"""
Deduplication algorithm for test cases using Jaccard Similarity.

For each pair of test cases with the same scenario type, compute
word-level Jaccard similarity on step actions. If > 0.85, mark as
duplicate and keep the more detailed one.
"""

import re
from app.models.test_case_models import TestCase


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, strip punctuation."""
    return set(re.findall(r'\b\w+\b', text.lower()))


def _steps_text(tc: TestCase) -> str:
    """Concatenate all step actions into one string."""
    return " ".join(step.action for step in tc.steps)


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def deduplicate_test_cases(
    cases: list[TestCase],
    threshold: float = 0.85,
) -> list[TestCase]:
    """Remove near-duplicate test cases."""
    if len(cases) <= 1:
        return cases

    token_sets = [_tokenize(_steps_text(tc)) for tc in cases]
    keep = [True] * len(cases)

    for i in range(len(cases)):
        if not keep[i]:
            continue
        for j in range(i + 1, len(cases)):
            if not keep[j]:
                continue
            if cases[i].scenario_type != cases[j].scenario_type:
                continue

            sim = _jaccard_similarity(token_sets[i], token_sets[j])

            if sim >= threshold:
                if len(cases[j].steps) > len(cases[i].steps):
                    _merge_preconditions(cases[j], cases[i])
                    keep[i] = False
                    break
                else:
                    _merge_preconditions(cases[i], cases[j])
                    keep[j] = False

    return [tc for tc, k in zip(cases, keep) if k]


def _merge_preconditions(keeper: TestCase, removed: TestCase):
    """Add unique preconditions from removed case to keeper."""
    existing = set(keeper.preconditions)
    for pre in removed.preconditions:
        if pre not in existing:
            keeper.preconditions.append(pre)
