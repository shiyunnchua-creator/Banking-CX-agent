# Mia — A Banking CX Agent (Meridian Bank)

A teaching reference for building a customer-experience (CX) conversational agent for a retail bank, taken **from proof-of-concept to a deployment plan**. It mirrors the structure of an RFP-agent lecture example, retargeted to banking with three journeys — **charge disputes**, **credit card applications**, and **loan enquiries** — and three production-grade design specifications: an **audit trail**, **verify-before-acting**, and **confidence-based routing**.

Built with **LangChain + LangGraph + Claude (Anthropic `claude-sonnet-4-6`)**, with a fully mocked banking backend so it runs end to end with no real systems.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/shiyunnchua-creator/Banking-CX-agent/blob/main/notebooks/Banking_CX_Agent_Session.ipynb)

> **Run it now (no local setup, no disk space needed):** click the **Open in Colab** badge above — Colab runs entirely in the cloud — then add your Anthropic API key when prompted and run all cells. The three use cases play out as a scripted show-and-tell.
>

> **Your API key is never stored or published.** The notebook reads it from a `getpass` prompt at runtime; nothing is written to a file or a cell. See [SECURITY.md](SECURITY.md) for the full explanation and a 60-second pre-publish checklist.

---

## The two frameworks this build follows

Everything below hangs off two pictures.

### 1. The AI Agents Modules pyramid

A useful agent is five layers stacked bottom-to-top. Each layer in this project:

| Layer | What it is | How it shows up here |
| --- | --- | --- |
| **Model** | LLM reasoning and generation | Claude `claude-sonnet-4-6` via LangChain, temperature 0 |
| **Context** | Internal knowledge, documents, databases | mocked customers, transactions, and a policy knowledge base in `data/` |
| **Tools** | APIs, search, business actions | verify, read-transaction, raise-dispute, escalate, and scaffolded card/loan tools |
| **Orchestration** | State, routing, loops, approvals | the LangGraph loop + the confidence router + the verify/confirm approval gate |
| **Governance** | Security, access control, auditability | identity gate on every read + an immutable audit trail + escalation routing |

### 2. The basic agent workflow

1. A user (customer) gives the agent a business task.
2. The agent interprets the goal and its category.
3. The agent **loops until it has enough evidence**: it decides which tool to use, the tool retrieves context or performs an action, the result goes back into the agent's state, and the agent decides whether it needs another step.
4. The agent produces a **structured output**.
5. Before any external action is taken, it goes through **review** — here, the governance checkpoint and human escalation.

The three design specifications sit on the top two layers: the **audit trail** is Governance; **verify-before-acting** and **confidence routing** are Orchestration.

---

## Part 1 — The steps to build the agent (explained)

This is the method, in the order you actually build it. Each step maps to a pyramid layer.

### Step 0 — Scope one journey first
Before any code, pick the single journey to launch first by *volume × tractability*. Disputes are high-volume, well-documented, and have clear policy — so disputes are built end to end here, while card applications and loan enquiries are scaffolded. Resist building all three at once; a narrow agent that works beats a broad one that doesn't.

### Step 1 — Model (the reasoning engine)
Choose the LLM and wire it up. The model does no banking by itself; it only reasons and decides which tool to call. Keep the key out of the notebook — load it from an environment variable or a prompt, never hard-code it.

### Step 2 — Context (give it ground truth)
An agent is only as good as the business context it can reach. We mock a small but realistic slice: customer profiles (identity factors, accounts, cards), transactions (with risk flags like `possible_duplicate` and `possible_fraud`), a policy knowledge base, and a product catalogue. In production these become calls to core banking, the card processor, KYC, and a knowledge service.

### Step 3 — Tools (let it read and act)
Each tool is a normal Python function with `@tool` metadata so the model can request it. Two rules are enforced **inside** the tools so they can never be skipped:

- **Verify before acting.** Account reads require a prior successful `verify_identity` in the same session.
- **Confirm before changing.** State-changing tools (`raise_dispute`, `issue_provisional_credit`, `freeze_card`) reject any call where `confirmed` is not `True` — the agent must restate the action and get an explicit "yes" first.

Every tool call writes one record to the **audit trail**.

### Step 4 — Orchestration (the loop, routing, and approvals)
LangGraph wires the pieces into a workflow:

- an **assistant** node (the model decides: answer or call a tool),
- a **tool** node (executes whatever the model requested),
- a **router** (`tool_calls` present → run tools; otherwise → go to governance),
- a **governance** node that produces the structured decision.

This is the basic workflow made real: decide → act → feed result back → decide again → stop → review.

### Step 5 — Confidence routing (the decision rule)
At the decision point the model returns a structured `CaseAssessment` (category, confidence 0–1, recommended action, rationale, escalation team). Confidence maps to a tier and a route:

| Tier | Confidence | Route | Example |
| --- | --- | --- | --- |
| **HIGH** | ≥ 0.85 | **Resolve at agent level** | clear duplicate charge under £100 → raise dispute + provisional credit |
| **MEDIUM** | 0.5–0.85 | **Triage, compile a packet, then escalate** | unrecognised recurring subscription → gather evidence, hand to Disputes team |
| **LOW** | < 0.5 | **Escalate immediately** | suspected fraud (foreign, high value, card-not-present) → freeze card, hand to Fraud |

The rule of thumb encoded in the prompt: **when in doubt, route down a tier.** Customer safety beats speed.

### Step 6 — Governance (audit + escalation)
Every read or change is logged with: reference ID, conversation ID, timestamp, customer location, category, confidence level, action taken, and outcome. Escalations always carry the reference ID, conversation ID, category, confidence, and a compiled case summary so the human never has to re-ask the customer.

### Step 7 — Test, then plan deployment
Run scripted conversations across all three confidence tiers and read the audit trail. Then follow the deployment path below.

---

## Part 2 — The three use cases

## Workflow

![Banking CX agent workflow across the three use cases](assets/banking-cx-agent-workflow.png)

| Use case | Built? | Typical tiers | Tools used |
| --- | --- | --- | --- |
| **Charge disputes** | End to end | high / medium / low | verify_identity, get_transaction, check_dispute_policy, raise_dispute, issue_provisional_credit, freeze_card, escalate_to_human |
| **Credit card applications** | Scaffold | medium (always escalates the credit decision) | get_card_products, check_card_eligibility, start_card_application |
| **Loan enquiries** | Scaffold | high for quotes, medium for formal enquiry | get_loan_products, estimate_repayment, start_loan_enquiry |

The scaffolds deliberately **never make a credit decision** — eligibility and affordability always route to the Lending team. Extending them means giving them the same verify gate, confirm gate, and governance route that disputes already have.

---

## Part 3 — POC to deployment

The notebook is the POC. To get to production:

1. **Replace the mock data with real integrations.** Swap the `data/` dictionaries for calls to core banking, the card processor, KYC/identity, and the knowledge service. Keep the tool *signatures* stable so the agent logic doesn't change.
2. **Make the audit trail real.** Move it from an in-memory list to an append-only, immutable store (write-once table or event log) with the retention your compliance team requires.
3. **Secrets management.** Replace `getpass`/`.env` with a managed secret store. Rotate keys; never commit them.
4. **Human-in-the-loop.** Wire `escalate_to_human` into your real case-management queues. Keep a human reviewing every medium and low case before launch.
5. **Evaluation.** Build a test set per journey and per confidence tier. Track resolution rate, escalation accuracy (did low-confidence really need a human?), and false auto-resolves (the metric that matters most in banking).
6. **Guardrails / red-team.** Probe for identity-bypass, over-confident auto-resolution, prompt injection via merchant names or free text, and currency/amount edge cases. Turn each finding into a tool-level check.
7. **Rollout.** Start in suggest-only mode (agent drafts, human sends), then auto-resolve only the highest-confidence, lowest-risk slice (e.g. sub-£100 duplicate disputes), and widen from there on evidence.

---

## Project layout

```
meridian-cx-agent/
  README.md                         # this file (also the build-steps explainer)
  SECURITY.md                       # how your API key stays safe + pre-publish checklist
  LICENSE                           # MIT
  requirements.txt
  .gitignore                        # blocks .env from being committed
  .env.example                      # copy to .env, add your key (never commit it)
  notebooks/
    Banking_CX_Agent_Session.ipynb  # the Colab POC, runs top to bottom
  assets/
    agent_system_prompt.md          # Mia's role, journeys, and guardrails
  data/
    customers.json                  # identity factors, accounts, cards
    transactions.json               # with risk flags
    bank_knowledge.json             # policies + escalation + audit standard
    products.json                   # card + loan catalogue
  python/
    audit.py                        # Governance: the audit trail
    banking_tools.py                # Tools: dispute tools + scaffolded card/loan
    confidence.py                   # Orchestration: structured assessment + routing
    agent.py                        # the LangGraph loop + governance handoff
```

## Quick start

### Colab (recommended for the demo)
Upload `notebooks/Banking_CX_Agent_Session.ipynb` to [Google Colab](https://colab.research.google.com/), run all cells, and paste your Anthropic API key when prompted.

### Local (the modular version)
```bash
cd meridian-cx-agent
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then edit .env and add your key
export ANTHROPIC_API_KEY=sk-ant-...   # or rely on .env
python python/agent.py            # runs the three dispute demos + prints the audit trail
```

## Publishing to GitHub

This folder is a self-contained repo. To publish it safely:

```bash
cd meridian-cx-agent
git init
git add .
git status            # confirm .env is NOT listed (only .env.example should appear)
git commit -m "Banking CX agent: POC + deployment scaffold"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

Then update the **Open in Colab** badge at the top with your `USERNAME/REPO`.

## Security note
Do **not** hard-code API keys in notebooks or commit them to GitHub. This project loads the key from an environment variable or a `getpass` prompt, and `.gitignore` blocks `.env`. If a key is ever exposed, rotate it immediately in the [Anthropic Console](https://console.anthropic.com/). Full details and a pre-publish checklist are in [SECURITY.md](SECURITY.md).

## Resources
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [LangChain tools](https://python.langchain.com/docs/concepts/tools/)
- [LangChain ChatAnthropic](https://python.langchain.com/docs/integrations/chat/anthropic/)
- [Anthropic tool use](https://docs.claude.com/en/docs/build-with-claude/tool-use)

---

**Difficulty:** Intermediate · **Mode:** Text agent (LangGraph + tool use + structured output) · **Language:** Python
