# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
