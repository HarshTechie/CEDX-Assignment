"""Stage 4 — Review: approval state machine + CASE_ID amendment.

The state machine is explicit and its transitions are validated, so an item can only
reach `delivered` through `approved`. Delivery is refused SERVER-SIDE (deliver_guard)
for anything not approved, and — per the CASE_ID amendment — for any record whose
amount >= threshold that lacks a recorded approval by the amendment role. Every action
appends to the audit with actor + timestamp.
"""
from __future__ import annotations

from ..audit_log import AuditLog

VALID = {
    "draft": {"in_review"},
    "in_review": {"approved", "changes_requested", "blocked"},
    "changes_requested": {"in_review", "blocked"},
    "approved": {"delivered", "blocked"},
    "delivered": set(),
    "blocked": set(),
}


class ApprovalError(RuntimeError):
    pass


class DeliveryRefused(RuntimeError):
    pass


class Approval:
    """One record's approval lifecycle."""

    def __init__(self, record_id: str, amount: float | None,
                 amendment_role: str, amendment_threshold: float, audit: AuditLog):
        self.record_id = record_id
        self.amount = amount
        self.role = amendment_role
        self.threshold = amendment_threshold
        self.audit = audit
        self.state = "draft"
        self.trail: list[dict] = []

    def _entry(self, state: str, actor: str, reason: str | None):
        ts = self.audit.append(actor, f"approval:{state}", self.record_id)["ts"]
        self.trail.append({"state": state, "actor": actor, "ts": ts, "reason": reason})

    def transition(self, to: str, actor: str, reason: str | None = None):
        if to not in VALID.get(self.state, set()):
            raise ApprovalError(
                f"{self.record_id}: illegal transition {self.state} -> {to}")
        self.state = to
        self._entry(to, actor, reason)

    # --- amendment ------------------------------------------------------- #
    def amendment_applies(self) -> bool:
        return self.amount is not None and self.amount >= self.threshold

    def amendment_satisfied(self) -> bool:
        if not self.amendment_applies():
            return True
        return any(t["state"] == "approved" and t["actor"] == self.role
                   for t in self.trail)

    # --- the server-side delivery gate ----------------------------------- #
    def deliver_guard(self) -> None:
        """Raise DeliveryRefused (logged) unless every precondition holds."""
        if self.state != "approved":
            self.audit.append("system", f"delivery_refused:not_approved:{self.state}",
                              self.record_id)
            raise DeliveryRefused(
                f"{self.record_id}: not approved (state={self.state})")
        if not self.amendment_satisfied():
            self.audit.append("system",
                              f"delivery_refused:amendment:{self.role}>={self.threshold}",
                              self.record_id)
            raise DeliveryRefused(
                f"{self.record_id}: amount {self.amount} >= {self.threshold} requires "
                f"approval by {self.role}")

    def deliver(self, actor: str = "system"):
        self.deliver_guard()
        self.transition("delivered", actor, reason="all approvals present")


def approve_verified(appr: Approval, operator: str = "operator") -> None:
    """The scripted operator+amendment sign-off used by the automated demo. In real
    operation these calls come from the operator CLI (app.operator)."""
    appr.transition("in_review", operator)
    appr.transition("approved", operator, reason="verifier passed")
    # Amendment: a record at/above threshold needs the second approver on record.
    if appr.amendment_applies():
        appr._entry("approved", appr.role, reason=f"amendment: amount>={appr.threshold}")
