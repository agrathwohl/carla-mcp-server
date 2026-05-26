"""Earshot — co-listening companion extension to carla-mcp-server.

This package implements the Phase 1 (oeuvre ingestion) tool. Phases 2–4 are
not implemented here; see earshot/README.md for the full design.

The Phase 1 entry point is the MCP tool `earshot_ingest_artist`, dispatched
through EarshotTools.execute(). One tool, three execution modes:

  1. Built-in parser path  — known hosts (Bandcamp, SoundCloud) handled by
     hand-coded parsers in earshot.parsers.
  2. Cached plan path      — unknown hosts that have been visited before;
     a cached scraping plan is executed by earshot.executor.
  3. Discovery path        — unknown hosts on first encounter; the tool
     returns a compressed DOM summary and a plan schema, asking the
     orchestrating LLM (Claude Code / harness) to emit a scraping plan.
     The orchestrator re-invokes the tool with `plan=...`; the executor
     self-checks the plan against the source page, caches it on success,
     then runs it.

The in-tree code never calls an LLM API directly — postmortem rule #7.
"""

__version__ = "0.1.0"
