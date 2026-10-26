"""Evaluation harness: deterministic scoring of agent runs against ground truth.

Every check is a plain function of (answer, tool calls, ground truth), so a
verdict is reproducible and explainable; there is no LLM judge to drift.
Each case runs several trials because the agent is non-deterministic.
"""

import hashlib
import json
import re
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from maintenance_copilot import config
from maintenance_copilot.trace import AgentRun, ToolCall

CASES_FILE = config.REPO_ROOT / "evals" / "cases.toml"

# USD per million tokens (input, output, cache read). Anthropic list prices.
MODEL_PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*[kK]?")


# --- ground-truth references ---------------------------------------------------------


def resolve(ref, truth: dict):
    """Resolve "gt:a.b[0].c" / "gt:a[*].c" against the ground truth; literals pass through."""
    if not isinstance(ref, str) or not ref.startswith("gt:"):
        return ref
    values = [truth]
    for part in re.findall(r"[^.\[\]]+|\[\*\]|\[\d+\]", ref[3:]):
        if part == "[*]":
            values = [item for v in values for item in v]
        elif part.startswith("["):
            values = [v[int(part[1:-1])] for v in values]
        else:
            values = [v[part] for v in values]
    return values if "[*]" in ref else values[0]


def numbers_in(text: str) -> list[float]:
    """All numbers in text: '186,400', '$186.4k' and '51' -> 186400.0, 186400.0, 51.0."""
    out = []
    for raw in NUMBER_RE.findall(text):
        clean = raw.replace(",", "").strip()
        scale = 1000 if clean[-1] in "kK" else 1
        try:
            out.append(float(clean.rstrip("kK").strip()) * scale)
        except ValueError:
            continue
    return out


# --- scoring -------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    category: str
    trial: int
    passed: bool
    checks: list[Check]
    run: AgentRun
    cost_usd: float | None = None

    def failures(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]


def _expand(item, truth: dict) -> list[list[str]]:
    """Turn a must_mention item into groups of alternatives (any-of within a group)."""
    if isinstance(item, list):
        return [[str(resolve(alt, truth)) for alt in item]]
    value = resolve(item, truth)
    if isinstance(value, list):
        return [[str(v)] for v in value]
    return [[str(value)]]


def score(case: dict, run: AgentRun, truth: dict, db_changed: bool = False) -> list[Check]:
    """Apply every check a case defines to one agent run."""
    answer = run.answer.lower()
    called = {c.name for c in run.tool_calls}
    cited = run.citations()
    available = run.available_sources()
    checks = [Check("completed", run.error is None and bool(run.answer), run.error or "")]
    checks.append(Check("db_unchanged", not db_changed, "work-order database was modified"))

    for item in case.get("must_mention", []):
        for group in _expand(item, truth):
            ok = any(alt.lower() in answer for alt in group)
            checks.append(Check("must_mention", ok, f"missing {' / '.join(group)}"))

    for phrase in case.get("must_not_mention", []):
        ok = str(resolve(phrase, truth)).lower() not in answer
        checks.append(Check("must_not_mention", ok, f"contains '{phrase}'"))

    found = numbers_in(run.answer)
    for spec in case.get("numbers", []):
        target = float(resolve(spec["value"], truth))
        tol = spec.get("abs_tol", abs(target) * spec.get("rel_tol", 0))
        ok = any(abs(n - target) <= tol for n in found)
        checks.append(Check("number", ok, f"no number within {tol:g} of {target:g}"))

    for tool in case.get("tools_required", []):
        checks.append(Check("tool_used", tool in called, f"{tool} was not called"))

    for source in case.get("must_cite", []):
        checks.append(Check("must_cite", source in cited, f"did not cite {source}"))

    if prefix := case.get("cite_prefix"):
        ok = any(c.startswith(prefix) for c in cited)
        checks.append(Check("cite_prefix", ok, f"no citation starting {prefix}"))

    ungrounded = [c for c in cited if c not in available]
    checks.append(Check("citations_grounded", not ungrounded, f"not from any tool: {ungrounded}"))
    if case.get("citations", "required") == "required":
        checks.append(Check("has_citation", bool(cited), "answer cites no sources"))
    return checks


def run_cost(run: AgentRun) -> float | None:
    """Dollar cost of a run at list prices, or None for an unpriced model.

    `input_tokens` includes cache reads, which are billed at the cache rate.
    Cache writes are priced as ordinary input (a slight underestimate).
    """
    prices = MODEL_PRICES.get(run.model)
    if prices is None:
        return None
    p_in, p_out, p_cache = prices
    uncached = run.input_tokens - run.cache_read_tokens
    return (uncached * p_in + run.output_tokens * p_out + run.cache_read_tokens * p_cache) / 1e6


# --- running -------------------------------------------------------------------------


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return tomllib.loads(path.read_text())["case"]


def null_agent(question: str) -> AgentRun:
    """A do-nothing baseline that declines everything.

    Whatever it scores is the floor: evals a null agent passes aren't measuring
    capability. It should pass only refusal-style cases.
    """
    return AgentRun(question=question, answer="I can't help with that.", model="null-agent")


def _db_hash() -> str:
    return hashlib.sha256(config.WORK_ORDERS_DB.read_bytes()).hexdigest()


@dataclass
class SuiteResult:
    results: list[CaseResult]
    model: str
    trials: int
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def run_suite(
    agent: Callable[[str], AgentRun],
    cases: list[dict],
    truth: dict,
    trials: int = 3,
    workers: int = 4,
) -> SuiteResult:
    """Run every case `trials` times (in parallel) and score each run."""
    db_before = _db_hash()

    def one(job: tuple[dict, int]) -> CaseResult:
        case, trial = job
        start = time.monotonic()
        try:
            run = agent(case["question"])
        except Exception as exc:  # a crash is a failed run, not a crashed suite
            run = AgentRun(question=case["question"], error=f"{type(exc).__name__}: {exc}")
        run.latency_s = run.latency_s or time.monotonic() - start
        checks = score(case, run, truth, db_changed=_db_hash() != db_before)
        return CaseResult(
            case_id=case["id"],
            category=case["category"],
            trial=trial,
            passed=all(c.passed for c in checks),
            checks=checks,
            run=run,
            cost_usd=run_cost(run),
        )

    jobs = [(c, t) for c in cases for t in range(1, trials + 1)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, jobs))
    model = next((r.run.model for r in results if r.run.model), "unknown")
    return SuiteResult(results=results, model=model, trials=trials)


def rescore(log_path: Path, cases: list[dict], truth: dict) -> SuiteResult:
    """Re-score the stored answers in a run log with the current checks.

    Use this when only the *scorer* changed: the answers are identical, so
    re-scoring gives exactly the verdicts a new run of those answers would,
    at no cost. If the agent, prompt or tools change, run the suite again.
    """
    by_id = {c["id"]: c for c in cases}
    results = []
    for line in log_path.read_text().splitlines():
        row = json.loads(line)
        case = by_id.get(row["case"])
        if case is None:
            continue
        calls = [ToolCall(**c) for c in row["tool_calls"]]
        fields = {k: row[k] for k in AgentRun.__dataclass_fields__ if k in row}
        run = AgentRun(**{**fields, "tool_calls": calls})
        db_changed = any(f.startswith("db_unchanged") for f in row.get("failures", []))
        checks = score(case, run, truth, db_changed=db_changed)
        results.append(
            CaseResult(
                case_id=case["id"],
                category=case["category"],
                trial=row["trial"],
                passed=all(c.passed for c in checks),
                checks=checks,
                run=run,
                cost_usd=run_cost(run),
            )
        )
    trials = max(r.trial for r in results)
    started = log_path.stem.rsplit("-", 1)[-1] + " (re-scored)"
    return SuiteResult(results=results, model=results[0].run.model, trials=trials, started=started)


# --- reporting -----------------------------------------------------------------------


def summarise(suite: SuiteResult) -> dict:
    """Headline metrics plus a per-case breakdown."""
    rs = suite.results
    by_case: dict[str, list[CaseResult]] = {}
    for r in rs:
        by_case.setdefault(r.case_id, []).append(r)
    cited = [c for r in rs for c in r.run.citations()]
    grounded = sum(1 for r in rs for c in r.run.citations() if c in r.run.available_sources())
    costs = [r.cost_usd for r in rs if r.cost_usd is not None]
    return {
        "model": suite.model,
        "started": suite.started,
        "trials": suite.trials,
        "cases": len(by_case),
        "runs": len(rs),
        "pass_rate": sum(r.passed for r in rs) / len(rs),
        "consistent_cases": sum(all(r.passed for r in g) for g in by_case.values()),
        "citation_grounding_rate": grounded / len(cited) if cited else None,
        "mean_latency_s": sum(r.run.latency_s for r in rs) / len(rs),
        "mean_input_tokens": sum(r.run.input_tokens for r in rs) / len(rs),
        "mean_output_tokens": sum(r.run.output_tokens for r in rs) / len(rs),
        "mean_cost_usd": sum(costs) / len(costs) if costs else None,
        "total_cost_usd": sum(costs) if costs else None,
        "per_case": [
            {
                "id": cid,
                "category": g[0].category,
                "passed": sum(r.passed for r in g),
                "trials": len(g),
                "failures": sorted({f for r in g for f in r.failures()}),
            }
            for cid, g in by_case.items()
        ],
    }


def to_markdown(summary: dict) -> str:
    """A results table suitable for committing and for the README."""
    s = summary
    money = f"${s['mean_cost_usd']:.4f}" if s["mean_cost_usd"] is not None else "n/a"
    total = f"${s['total_cost_usd']:.2f}" if s["total_cost_usd"] is not None else "n/a"
    grounding = (
        f"{s['citation_grounding_rate']:.0%}" if s["citation_grounding_rate"] is not None else "n/a"
    )
    lines = [
        f"# Eval results: `{s['model']}`",
        "",
        f"Run {s['started']} · {s['cases']} cases × {s['trials']} trials = {s['runs']} runs",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Pass rate (all runs) | **{s['pass_rate']:.0%}** |",
        f"| Cases passing every trial | {s['consistent_cases']}/{s['cases']} |",
        f"| Citations grounded in tool output | {grounding} |",
        f"| Mean latency | {s['mean_latency_s']:.1f} s |",
        f"| Mean tokens in / out | {s['mean_input_tokens']:,.0f} / "
        f"{s['mean_output_tokens']:,.0f} |",
        f"| Mean cost per question | {money} |",
        f"| Total cost of this run | {total} |",
        "",
        "| Case | Category | Passed | Failure reasons |",
        "|---|---|---|---|",
    ]
    for c in s["per_case"]:
        reasons = "<br>".join(c["failures"]) if c["failures"] else ""
        lines.append(f"| {c['id']} | {c['category']} | {c['passed']}/{c['trials']} | {reasons} |")
    return "\n".join(lines) + "\n"


def save(
    suite: SuiteResult, results_dir: Path, runs_dir: Path, label: str = ""
) -> tuple[Path, Path]:
    """Write the committed summary (markdown + json) and the full run log (jsonl).

    `label` distinguishes runs of the same model with different settings,
    e.g. "effort-low"; it is appended to the file names and report title.
    """
    summary = summarise(suite)
    if label:
        summary["model"] = f"{suite.model} ({label})"
    results_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = suite.model.replace("/", "_") + (f"-{label}" if label else "")
    md = results_dir / f"{stem}.md"
    md.write_text(to_markdown(summary))
    (results_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2) + "\n")
    log = runs_dir / f"{stem}-{suite.started.replace(':', '')}.jsonl"
    with log.open("w") as f:
        for r in suite.results:
            row = {"case": r.case_id, "trial": r.trial, "passed": r.passed, **r.run.to_dict()}
            row["failures"] = r.failures()
            f.write(json.dumps(row, default=str) + "\n")
    return md, log
