"""Governance layer: an immutable-style audit trail for every agent action.

This is the top of the AI Agents pyramid (Security, access control, auditability).
Every tool the agent calls appends one record here. In production this would be an
append-only store (e.g. a write-once table or event log) retained for compliance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional


def _new_reference_id() -> str:
    """Short, human-quotable reference for a single audited action."""
    return "REF-" + uuid.uuid4().hex[:8].upper()


@dataclass
class AuditRecord:
    reference_id: str
    conversation_id: str
    timestamp: str
    customer_id: Optional[str]
    location: str
    category: str
    confidence_level: str
    action: str
    outcome: str
    status: str  # logged | resolved | escalated | rejected

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class AuditTrail:
    """Collects audit records for a session. Append-only by convention."""

    records: list = field(default_factory=list)

    def log(
        self,
        *,
        conversation_id: str,
        action: str,
        outcome: str,
        category: str = "general",
        confidence_level: str = "n/a",
        customer_id: Optional[str] = None,
        location: str = "unknown",
        status: str = "logged",
    ) -> AuditRecord:
        record = AuditRecord(
            reference_id=_new_reference_id(),
            conversation_id=conversation_id,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            customer_id=customer_id,
            location=location,
            category=category,
            confidence_level=confidence_level,
            action=action,
            outcome=outcome,
            status=status,
        )
        self.records.append(record)
        return record

    def to_table(self):
        """Return records as a list of dicts (ready for pandas.DataFrame)."""
        return [r.as_row() for r in self.records]

    def pretty_print(self) -> None:
        for r in self.records:
            print(
                f"[{r.timestamp}] {r.reference_id} | conv={r.conversation_id} | "
                f"{r.category} | conf={r.confidence_level} | {r.action} -> "
                f"{r.outcome} ({r.status})"
            )


# A single shared trail for the running session (POC convenience).
AUDIT = AuditTrail()
