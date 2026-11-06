"""Compatibility shim. The conversational brain now lives in ``driveos.agent.harness`` (OpenRouter tool agent).

``ClaudeAgent`` and ``TOOLS`` remain importable for older call sites; both resolve to the harness.
"""

from __future__ import annotations

from .harness.brain import HarnessBrain
from .harness.tools import ToolRegistry

ClaudeAgent = HarnessBrain
TOOLS = ToolRegistry().schemas()

__all__ = ["TOOLS", "ClaudeAgent", "HarnessBrain"]
