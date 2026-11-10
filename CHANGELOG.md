# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Work-order planner** (`mc plan "P-104 is leaking"`): a new LangGraph workflow next to the
  unchanged Q&A agent. An LLM triages the request, plain-code specialists gather evidence in
  parallel (work-order history, manuals, and sensor data for gas turbines), an LLM drafts a
  work order with priority and citations, a code check verifies every citation, and the graph
  pauses (LangGraph `interrupt`) for a human to approve or reject it.
- Approved work orders are written to a separate `data/actions.db`. It is the project's only
  write path: a fixed, parameterised INSERT run by code after approval. The work-order
  database stays read-only.
- Written priority rubric shared by the planner prompt and (upcoming) planner evals.
- **"Plan a work order" mode in the Streamlit app**, next to the unchanged Q&A chat: live
  progress per step, the draft with its evidence and guardrail warnings, Approve / Reject
  buttons that resume the paused run, and this session's approved orders. Both modes share
  the per-session question limit.
- LangSmith: planner runs are grouped by `thread_id`, with readable inputs and outputs (the
  request and a draft summary; the reviewer's decision) instead of raw graph state.
- `LLM_TIMEOUT_S` (default 120): per-request model timeout for every provider.

### Changed
- Tests force LangSmith tracing off, so `pytest` never sends runs even when `.env` enables it.

## [1.1.0] - 2026-10-27

An MCP server on top of v1.0: the same tools, guardrails and citations, usable from Claude
Desktop, Claude Code or any MCP client.

### Added
- **MCP server** (`mc mcp`), built on the official MCP Python SDK v2. It serves over stdio
  (default) or Streamable HTTP bound to `127.0.0.1`, and makes no LLM calls itself.
  - Tools: `query_work_orders`, `get_sensor_trend`, `search_manuals`, annotated read-only,
    non-destructive and closed-world. Expected errors (blocked SQL, a non-turbine asset) are
    MCP error results whose message reaches the client's model.
  - Resources: `schema://work-orders`, `manual://index`, `manual://{manual_id}/{section}`
    (citation IDs are readable URIs), `model-card://rul`.
  - Prompts: `investigate_asset`, `prioritise_maintenance`.
- `.mcp.json`, so Claude Code offers the server when the repo is opened.
- **MCP parity eval** (`mc eval --agent copilot-mcp`): the unchanged agent with every tool
  call sent over MCP to a real stdio subprocess. Claude Opus 5.5 scored 100% (18/18 cases in
  every trial), the same as calling the tools directly; MCP added about 3 ms per tool call
  (median).
- 24 new offline tests (127 in total). They include the 10 SQL attacks sent over MCP, a
  path-traversal resource URI, the real `mc mcp` command over stdio, and a check that the
  model sees identical tools on both paths.

### Changed
- Tool descriptions live in one module shared by the agent and the MCP server. The SQL tool's
  description lost a trailing newline, which LangChain trimmed but MCP sent verbatim.
- `build_agent()` accepts a tool list.

## [1.0.0] - 2026-10-27

First release.

### Added
- **Agent:** a LangGraph agent with an explicit agent-and-tools loop (at most 8 rounds) that
  cites a source for every claim. The model is switchable with `LLM_PROVIDER` (Claude by
  default; OpenAI, Gemini and OpenRouter optional).
- **Tools:**
  - `query_work_orders`: model-written SQL with four read-only guardrail layers.
  - `get_sensor_trend`: remaining-life estimate, range, status and sensor drift for gas turbines.
  - `search_manuals`: BM25 search with citation IDs.
- **Data:** a seeded synthetic work-order database (40 assets, 291 work orders, planted
  patterns); NASA C-MAPSS FD001, downloaded at setup and checked against a checksum; three
  synthetic equipment manuals.
- **Remaining-life model:** an explainable ridge-regression baseline. Test RMSE 21.8 cycles
  (true RUL capped at 125), with physically consistent weights and status assigned from the
  pessimistic end of the range.
- **Evaluation:** 18 cases × 3 trials with deterministic checks (facts, numbers, tools,
  grounded citations, database unchanged), a null-agent baseline and free re-scoring of stored
  runs. Claude Opus 5.5 scores 100% (`evals/results/`, `evals/FAILURES.md`).
- **Interfaces:** `mc` CLI (`setup`, `ask`, `eval`, `app`); a Streamlit chat UI with live tool
  progress and a "How I got this" trace; deployed at https://maintenance-copilot.streamlit.app.
- **Engineering:** uv with pinned dependencies, 103 offline tests, CI with gitleaks secret
  scanning, ruff and pytest.

[Unreleased]: https://github.com/mojorisin/maintenance-copilot/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/mojorisin/maintenance-copilot/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/mojorisin/maintenance-copilot/releases/tag/v1.0.0
