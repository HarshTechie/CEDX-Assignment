"""Agent base + fleet roster.

Every agent declares its role, the model tier(s) it may use, and an explicit
`can_call` allow-list. The allow-list is ENFORCED at runtime (call_guard): an agent
attempting to invoke an agent not on its list raises. That is what makes this a typed
fleet with real contracts rather than three prompts in one function.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentSpec:
    name: str
    role: str            # orchestrator | worker | verifier | ...
    models: list[str]
    prompt_version: str
    can_call: list[str] = field(default_factory=list)

    def roster_entry(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "models": self.models,
            "prompt_version": self.prompt_version,
            "can_call": self.can_call,
        }


class CallGuardError(RuntimeError):
    pass


class Fleet:
    """Holds the roster and enforces the can_call contract between agents."""

    def __init__(self, specs: list[AgentSpec]):
        self.specs = {s.name: s for s in specs}

    def roster(self) -> list[dict]:
        return [s.roster_entry() for s in self.specs.values()]

    def guard(self, caller: str, callee: str) -> None:
        spec = self.specs.get(caller)
        if spec is None:
            raise CallGuardError(f"unknown caller agent {caller!r}")
        if callee not in spec.can_call:
            raise CallGuardError(
                f"contract violation: {caller!r} may not call {callee!r} "
                f"(allowed: {spec.can_call})")
