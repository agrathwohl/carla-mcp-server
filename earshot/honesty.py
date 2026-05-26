"""Honesty rule validator for commentary content.

The README's "Honesty rules" section is the load-bearing editorial
discipline of Earshot. Without enforcement, the scheduler drifts toward
generic-AI-fan territory and the project's value collapses. This module
turns the prose-side honesty rules into executable validation, applied to:

  1. Scheduler-generated action-text (level 2) — pre-rendered from the
     profile's catalog. The catalog itself should be authored to comply,
     but the validator catches phrasings that slip through.
  2. Orchestrator-submitted prose (levels 3–6) — via
     `earshot_submit_commentary`. The scheduler rejects submissions that
     fail validation rather than queue dishonest content.

Implemented rules (from README §"Honesty rules"):

  1. **Anti-spoiler discipline** — content must not reference what's
     about to happen. Phrases like "about to drop", "wait for it",
     "here it comes" are caught. The agent has 5 s of lookahead via the
     delay tower but must NEVER leak that to user-clock output.
  2. **No performed enthusiasm** — marketing/hype language ("amazing",
     "incredible", "fire", "magnificent", "blazingly fast" etc.) is
     forbidden unless an explicit measurement-grounded claim accompanies
     it. Today we just block the phrasings; the "with measurement"
     escape valve is a Phase I refinement.
  3. **No claimed feelings** — "I love", "this moves me", "I feel" etc.
     are forbidden. The agent doesn't have preferences; saying so is
     performance.
  4. **No taste claims** — "I love this kind of music" / "this isn't
     for me" / "my taste runs more towards X" — same category as #3.

Rules NOT enforced here (because they live elsewhere or aren't
text-pattern-detectable):

  - **Silence is an active choice** — the scheduler enforces this at the
    level-selection layer (SILENT events don't reach this validator).
  - **Anti-spoiler grounded in playback time** — the scheduler enforces
    this via `ts_user_clock` timing math, not via prose content. This
    validator only catches the more obvious phrase-level spoilers.
  - **Action-text is not feeling** — that's a documentation rule about
    how the user interprets `*nods*`, not a content check.
  - **Prediction-error grounding** — enforced at the event source
    (scheduler only emits prose for events that cleared the threshold).

Returns from `validate()`: `(ok: bool, reasons: list[str])`. Empty
reasons list when ok. `False` with one or more reasons when content
violates a rule. Callers decide whether to reject outright or surface
the reason back to the orchestrator for revision.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Forbidden phrasings — case-insensitive word/phrase patterns
# ----------------------------------------------------------------------

# Anti-spoiler: future-referencing constructs the agent must not use,
# because the user is hearing audio T-delay_seconds behind the agent's
# measurements. Phrases that reveal foreknowledge are spoilers.
ANTI_SPOILER_PATTERNS = [
    r"\babout to\b",
    r"\bwait for it\b",
    r"\bhere it comes\b",
    r"\bany second now\b",
    r"\bjust before\b",
    r"\bin a moment\b",
    r"\bcoming up\b",
    r"\bnext\s+(?:section|bar|chorus|drop|change)\b",
    r"\babout to (?:drop|hit|change|shift)\b",
]

# Performed enthusiasm: marketing / hype words used in the README's
# explicit examples. Add new ones if a profile catalog gets sloppy.
ENTHUSIASM_PATTERNS = [
    r"\bamazing\b",
    r"\bincredible\b",
    r"\bmagnificent\b",
    r"\bblazingly\b",
    r"\bphenomenal\b",
    r"\bawesome\b",
    r"\bepic\b",
    r"\bfire\b(?!\s*room)",  # "fire" the hype word, but allow "fire room" / "fireroom"
    r"\bsick\b(?!\s+of)",     # "sick" the hype word, but allow "sick of"
    r"\bperfect\b",
    r"\bgenius\b",
    r"\bbrilliant\b",
    r"\bgorgeous\b",
    r"\b100%\s+(?:secure|fire|perfect)\b",
]

# Feeling / taste claims — first-person experience statements forbidden
# per README rules #2, #6.
FEELING_PATTERNS = [
    r"\bi\s+love\b",
    r"\bi\s+feel\b",
    r"\bi\s+(?:adore|enjoy|hate|dislike)\b",
    r"\bthis moves me\b",
    r"\b(?:gets|got|going)\s+me\b",     # "this gets me", "going me"
    r"\bi\s+can\'?t\s+(?:get over|stop)\b",
    r"\bmy\s+(?:taste|preference)\b",
    r"\bthis\s+is\s+for\s+me\b",
    r"\bnot\s+(?:my|for)\s+(?:thing|me)\b",
    r"\bi\s+prefer\b",
]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reasons: list[str]    # empty when ok

    def __bool__(self) -> bool:
        return self.ok


# ----------------------------------------------------------------------
# Validator
# ----------------------------------------------------------------------

class HonestyValidator:
    """Pattern-based honesty validator.

    Stateless; safe to share across sessions. The compiled regex set is
    built once at construction. Subclass / patch the pattern lists to
    add profile-specific rules (Phase H may add per-profile honesty
    extensions for genres with their own clichés to avoid).
    """

    def __init__(
        self,
        *,
        anti_spoiler_patterns: Optional[list[str]] = None,
        enthusiasm_patterns: Optional[list[str]] = None,
        feeling_patterns: Optional[list[str]] = None,
    ):
        self._anti_spoiler = [
            re.compile(p, re.IGNORECASE)
            for p in (anti_spoiler_patterns or ANTI_SPOILER_PATTERNS)
        ]
        self._enthusiasm = [
            re.compile(p, re.IGNORECASE)
            for p in (enthusiasm_patterns or ENTHUSIASM_PATTERNS)
        ]
        self._feeling = [
            re.compile(p, re.IGNORECASE)
            for p in (feeling_patterns or FEELING_PATTERNS)
        ]

    def validate(self, content: str) -> ValidationResult:
        """Check content against all honesty rules.

        Returns a ValidationResult. `ok=False` when any rule matches;
        `reasons` lists the rule(s) that fired with the offending phrase
        excerpted so callers / orchestrators can revise.

        Empty / whitespace-only content is treated as `ok=True` (silence
        is honest; the scheduler enforces SILENT-level non-queueing
        separately).
        """
        if not content or not content.strip():
            return ValidationResult(ok=True, reasons=[])

        reasons: list[str] = []

        for rx in self._anti_spoiler:
            m = rx.search(content)
            if m:
                reasons.append(
                    f"anti-spoiler violation: {m.group(0)!r} references future content"
                )

        for rx in self._enthusiasm:
            m = rx.search(content)
            if m:
                reasons.append(
                    f"performed-enthusiasm violation: {m.group(0)!r} is marketing language"
                )

        for rx in self._feeling:
            m = rx.search(content)
            if m:
                reasons.append(
                    f"feeling/taste violation: {m.group(0)!r} claims experience the agent doesn't have"
                )

        return ValidationResult(ok=(not reasons), reasons=reasons)


# Module-level default instance — reused across the scheduler unless a
# profile needs custom patterns.
DEFAULT_VALIDATOR = HonestyValidator()
