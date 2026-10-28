"""Polymarket executor — BUILD-READY, dry-run by default (iron rail 3).

The executor consumes the SAME instrument instances the shadow engine
records, turns them into capped maker order intents, and either logs them
(dry_run) or places them (live). Live requires the arming gate: keys in the
secrets file, compliance record present, and GRIDBOT_LIVE=1 set by the user's
own hand. No code path in this package flips dry_run off on its own.

No LLM touches anything here (iron rail 1): every function is deterministic.
"""
