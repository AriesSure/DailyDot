# DailyDot AI Upgrade Rules

## Project Goal

Upgrade DailyDot into an AI Application Engineer portfolio project.

Target capabilities:

- LLM Application
- RAG
- Function Calling
- Agent Workflow
- AI Reliability


## Scope Boundary

Allowed:

- Improve AI application architecture
- Add tests
- Improve reliability
- Refactor AI modules


Not allowed without approval:

- Multi-Agent systems
- LangGraph migration
- New AI frameworks
- Vector database migration
- Model fine-tuning
- AI infrastructure


## Engineering Principles

Prefer:

- Simple architecture
- Explicit control flow
- Testable components
- Explainable decisions


Avoid:

- Over abstraction
- Framework replacement
- Unnecessary dependencies


## Tool Safety

LLM must never:

- directly access database
- decide user identity
- bypass validation

Database operations must go through backend services.