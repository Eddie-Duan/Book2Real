"""Test-suite defaults: offline and deterministic.

``.env`` may carry a developer's real API keys and ``cloud.load_env()`` loads it
with ``setdefault``, so pinning the LLM keys to "" here survives that load and
keeps QA on the rule path (tests that need a fake LLM set their own variables,
e.g. ``tests/test_qa.py``).
"""
from __future__ import annotations

import os

for _key in ("B2R_LLM_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"):
    os.environ[_key] = ""
