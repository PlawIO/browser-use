"""
Veto integration for browser-use.

Provides pre-execution guardrails for browser agent actions using the Veto
policy engine. When enabled, every browser action (click, type, navigate, etc.)
is validated against Veto policies before execution.

Usage:
    from browser_use import Agent
    from browser_use.veto import VetoGuard

    veto = VetoGuard(api_key="veto_...", endpoint="https://api.veto.so")
    agent = Agent(task="...", llm=llm, veto=veto)
"""

from browser_use.veto.guard import VetoGuard, VetoConfig, VetoDecision

__all__ = ['VetoGuard', 'VetoConfig', 'VetoDecision']
