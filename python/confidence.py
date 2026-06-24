"""Orchestration helper: turn the conversation so far into a structured decision.

This is where the agent stops 'chatting' and produces a machine-readable assessment
that governance can route on:
  - HIGH  (>= 0.85): resolve at the agent level
  - MEDIUM (0.5-0.85): triage, compile a packet, then escalate
  - LOW   (< 0.5): escalate immediately
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CaseAssessment(BaseModel):
    """Structured output the model must return at the decision point."""

    category: str = Field(description="charge_dispute | credit_card_application | loan_enquiry | other")
    confidence: float = Field(description="0.0-1.0 confidence in safely resolving at the agent level")
    recommended_action: str = Field(description="One concrete next action, e.g. 'issue provisional credit £84.30'")
    rationale: str = Field(description="Why this confidence and action, citing the evidence gathered")
    missing_info: str = Field(default="", description="What is still unknown, if anything")
    escalation_team: str = Field(default="", description="disputes | fraud | lending | '' if resolving at agent")


def tier_for(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


ROUTING_RULE = {
    "high": "resolve_at_agent",
    "medium": "triage_then_escalate",
    "low": "escalate_now",
}


ASSESSOR_SYSTEM_PROMPT = (
    "You are the governance checkpoint for Meridian Bank's CX agent. "
    "Read the conversation and the tool evidence gathered so far and return a structured "
    "CaseAssessment. Be conservative: if there is any sign of fraud, legal risk, or the "
    "action exceeds agent limits (e.g. credit over £100), lower the confidence so the case "
    "is escalated rather than auto-resolved. When in doubt, route down a tier."
)
