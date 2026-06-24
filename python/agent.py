"""Orchestration layer: the LangGraph agent loop + governance handoff.

Mirrors the basic workflow:
  user task -> assistant interprets & decides a tool -> tool runs -> result back to state
  -> need more? loop -> otherwise -> GOVERNANCE handoff (structured output + confidence
  routing + audit) -> END.

Run:  python agent.py            (runs the three charge-dispute demos)
Env:  ANTHROPIC_API_KEY (required), ANTHROPIC_MODEL (default claude-sonnet-4-6)
"""

from __future__ import annotations

import os
from typing_extensions import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_anthropic import ChatAnthropic
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from audit import AUDIT
from banking_tools import ALL_TOOLS, SESSION, start_session
from confidence import (CaseAssessment, ASSESSOR_SYSTEM_PROMPT, ROUTING_RULE, tier_for)

MODEL_NAME = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "agent_system_prompt.md")

llm = ChatAnthropic(model=MODEL_NAME, temperature=0, max_tokens=2048)
llm_with_tools = llm.bind_tools(ALL_TOOLS)
assessor = llm.with_structured_output(CaseAssessment)

with open(SYSTEM_PROMPT_PATH, encoding="utf-8") as fh:
    SYSTEM_PROMPT = fh.read()

system_message = SystemMessage(content=SYSTEM_PROMPT)


# --- State -------------------------------------------------------------------
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    conversation_id: str
    category: str
    confidence: float
    confidence_tier: str
    routing: str
    action_taken: str
    resolution_status: str


# --- Nodes -------------------------------------------------------------------
def assistant(state: AgentState) -> dict:
    """The agent 'brain': reads state, decides whether to call a tool or stop."""
    response = llm_with_tools.invoke([system_message] + state["messages"])
    return {"messages": [response]}


def route_core(state: AgentState) -> str:
    """If the last message asked for a tool, run it; else go to governance."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "governance"


def _transcript(messages) -> str:
    """Flatten the conversation into one plain-text summary.

    We send this as a single user message to the structured-output call instead of the
    raw tool/assistant turns. That avoids provider errors about empty or mis-ordered
    messages and gives the assessor a clean view of the evidence.
    """
    lines = []
    for m in messages:
        role = type(m).__name__
        content = m.content if isinstance(m.content, str) else str(m.content)
        if getattr(m, "tool_calls", None):
            lines.append(role + " called tools: " + ", ".join(tc["name"] for tc in m.tool_calls))
        if content and content.strip():
            lines.append(role + ": " + content)
    return "\n".join(lines) or "No conversation captured."


def governance(state: AgentState) -> dict:
    """Governance checkpoint: structured assessment + confidence routing + audit.

    This is the 'human review before external action' step of the workflow, made
    explicit. The assistant has already produced a draft answer; here we classify it,
    decide the tier, and log the decision to the audit trail.
    """
    assessment: CaseAssessment = assessor.invoke([
        SystemMessage(content=ASSESSOR_SYSTEM_PROMPT),
        HumanMessage(content="Conversation and tool evidence so far:\n" + _transcript(state["messages"])),
    ])
    tier = tier_for(assessment.confidence)
    routing = ROUTING_RULE[tier]

    AUDIT.log(
        conversation_id=state["conversation_id"],
        action="governance_decision",
        outcome=f"{routing}: {assessment.recommended_action}",
        category=assessment.category,
        confidence_level=f"{tier} ({assessment.confidence:.2f})",
        location=SESSION["location"],
        status="resolved" if tier == "high" else "escalated",
    )
    return {
        "category": assessment.category,
        "confidence": assessment.confidence,
        "confidence_tier": tier,
        "routing": routing,
        "action_taken": assessment.recommended_action,
        "resolution_status": (
            "Resolved at agent level." if tier == "high"
            else f"Triaged and escalated to {assessment.escalation_team or 'human team'}."
            if tier == "medium"
            else f"Escalated immediately to {assessment.escalation_team or 'human team'}."
        ),
    }


# --- Graph -------------------------------------------------------------------
def build_agent():
    workflow = StateGraph(AgentState)
    workflow.add_node("assistant", assistant)
    workflow.add_node("tools", ToolNode(ALL_TOOLS))
    workflow.add_node("governance", governance)

    workflow.add_edge(START, "assistant")
    workflow.add_conditional_edges("assistant", route_core,
                                   {"tools": "tools", "governance": "governance"})
    workflow.add_edge("tools", "assistant")
    workflow.add_edge("governance", END)
    return workflow.compile()


def run_case(agent, conversation_id: str, customer_message: str) -> dict:
    start_session(conversation_id)
    result = agent.invoke({
        "messages": [HumanMessage(content=customer_message)],
        "conversation_id": conversation_id,
        "category": "", "confidence": 0.0, "confidence_tier": "",
        "routing": "", "action_taken": "", "resolution_status": "",
    })
    return result


DEMOS = [
    ("CONV-HIGH-001",
     "Hi, I'm Daniel Okafor, customer CUST-88102, card ending 9920. I've been charged twice "
     "for the same £84.30 BrightMart shop on 21 June (TXN-500021 and TXN-500022). Please sort the duplicate."),
    ("CONV-MED-001",
     "Hello, this is Aisha Rahman, CUST-88210, DOB 1995-11-03. There's a £14.99 charge from "
     "'NimbusCloud Ltd' (TXN-500040) I don't recognise. Can you remove it?"),
    ("CONV-LOW-001",
     "Hi, George Whitfield here, CUST-88345, card last four 7705. There's a £1,890 charge from "
     "Manila I did not make (TXN-500077). I'm worried my card is compromised."),
]


def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY first.")
    agent = build_agent()
    for conv_id, msg in DEMOS:
        print("\n" + "=" * 80)
        print(f"{conv_id}\nCustomer: {msg}\n")
        result = run_case(agent, conv_id, msg)
        print("Mia:", result["messages"][-1].content)
        print(f"\n[decision] category={result['category']} "
              f"tier={result['confidence_tier']} ({result['confidence']:.2f}) "
              f"routing={result['routing']}")
        print(f"[outcome] {result['resolution_status']}")
    print("\n" + "=" * 80 + "\nAUDIT TRAIL\n" + "=" * 80)
    AUDIT.pretty_print()


if __name__ == "__main__":
    main()
