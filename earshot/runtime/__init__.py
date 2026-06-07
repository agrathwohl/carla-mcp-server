"""Earshot autonomous runtime.

Components that let a listening session run WITHOUT a human-in-the-loop
orchestrator. The commentary worker fulfills the scheduler's ProseRequests
in-process (Claude Haiku over httpx) and renders delivered commentary to a
plain-text feed file the user tails in a separate terminal. The agent sets a
session up and leaves; the runtime drives the experience to the end.
"""
