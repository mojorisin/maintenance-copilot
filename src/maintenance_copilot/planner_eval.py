"""Evaluation of the work-order planner: `mc eval --suite planner`.

Each case in evals/planner_cases.toml is a problem report with an expected
asset, priority and citations. Every check is plain code on the structured
draft, so verdicts are reproducible and re-scorable from logs at no cost.

The eval never approves a draft: it measures what a human would be asked to
approve, and checks that nothing was written while waiting.
"""

import json
import re
import sqlite3
import statistics
import time
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from maintenance_copilot import config
from maintenance_copilot.evaluation import Check, _db_hash, run_cost
from maintenance_copilot.trace import PlanRun, ToolCall

CASES_FILE = config.REPO_ROOT / "evals" / "planner_cases.toml"
PRIORITIES = ("high", "medium", "low")

# --- the rubric as code: the oracle for expected priorities ----------------------------


def rubric_priority(
    asset_type: str, criticality: str, turbine_status: str | None, repeats: bool, safety: bool
) -> str:
    """prompts.PRIORITY_RUBRIC for a report of an active fault. First matching rule wins."""
    if safety or turbine_status == "critical":
        return "high"
    if asset_type != "gas_turbine" and criticality == "A":
        return "high"
    if asset_type == "gas_turbine" or criticality == "B" or repeats:
        return "medium"
    return "low"


REPEATS_SQL = """\
SELECT COUNT(*) FROM (
  SELECT failure_mode FROM work_orders
  WHERE asset_id = ? AND wo_type = 'corrective' AND opened_date >= date(?, '-12 months')
  GROUP BY failure_mode HAVING COUNT(*) >= 2)"""


def expected_from_data(case: dict, truth: dict) -> str:
    """The rubric's priority for a case's asset, from the database and ground truth."""
    with sqlite3.connect(f"file:{config.WORK_ORDERS_DB}?mode=ro", uri=True) as conn:
        asset_type, criticality = conn.execute(
            "SELECT asset_type, criticality FROM assets WHERE asset_id = ?", (case["asset"],)
        ).fetchone()
        (repeats,) = conn.execute(
            REPEATS_SQL, (case["asset"], config.AS_OF_DATE.isoformat())
        ).fetchone()
    status = truth["turbines"].get(case["asset"], {}).get("status")
    return rubric_priority(asset_type, criticality, status, repeats > 0, case.get("safety", False))


def validate_cases(cases: list[dict], truth: dict) -> list[str]:
    """Problems with the eval set itself. Checked before any LLM call."""
    problems = []
    for case in cases:
        if case.get("status") == "declined":
            continue
        expected = expected_from_data(case, truth)
        if case["priority"] != expected:
            problems.append(
                f"{case['id']}: file says {case['priority']}, rubric + data say {expected}"
            )
    return problems


def load_cases(path: Path = CASES_FILE) -> list[dict]:
    return tomllib.loads(path.read_text())["case"]


# --- scoring ---------------------------------------------------------------------------


def _draft_text(run: PlanRun) -> str:
    d = run.draft or {}
    return " ".join([d.get("title", ""), d.get("failure_mode", ""), *d.get("actions", []),
                     d.get("reasoning", "")]).lower()  # fmt: skip


def score(case: dict, run: PlanRun, db_changed: bool = False) -> list[Check]:
    """Apply every check a case defines to one planner run."""
    expected_status = case.get("status", "awaiting_approval")
    checks = [
        Check("completed", run.status != "error", run.message if run.status == "error" else ""),
        Check("status", run.status == expected_status, f"{run.status}, expected {expected_status}"),
        Check("not_actioned", run.action_id is None, f"wrote {run.action_id} without approval"),
        Check("db_unchanged", not db_changed, "work-order database was modified"),
    ]
    if expected_status == "declined":
        checks.append(Check("no_draft", run.draft is None, "drafted a work order anyway"))
        return checks
    if run.draft is None:  # counts against accuracy, so declining everything can't look good
        checks += [
            Check("asset", False, "no draft"),
            Check("priority", False, "no draft"),
            Check("citations_grounded", False, "no draft"),
        ]
        return checks

    draft, cited, available = run.draft, run.citations(), run.available_sources()
    checks.append(Check("asset", draft["asset_id"] == case["asset"], f"got {draft['asset_id']}"))
    checks.append(
        Check(
            "priority",
            draft["priority"] == case["priority"],
            f"got {draft['priority']}, expected {case['priority']}",
        )
    )
    ungrounded = [c for c in cited if c not in available]
    checks.append(
        Check(
            "citations_grounded",
            bool(cited) and not ungrounded,
            f"not from evidence: {ungrounded}" if cited else "cites no sources",
        )
    )
    for source in case.get("must_cite", []):
        checks.append(Check("must_cite", source in cited, f"did not cite {source}"))
    for prefix in case.get("cite_prefix", []):
        ok = any(c.startswith(prefix) for c in cited)
        checks.append(Check("cite_prefix", ok, f"no citation starting {prefix}"))
    text = _draft_text(run)
    for group in case.get("must_mention", []):
        ok = any(alt.lower() in text for alt in group)
        checks.append(Check("must_mention", ok, f"missing {' / '.join(group)}"))
    for pattern in case.get("must_match", []):
        ok = re.search(pattern, text) is not None
        checks.append(Check("must_match", ok, f"no match for /{pattern}/"))
    return checks


# --- running ---------------------------------------------------------------------------


@dataclass
class PlanResult:
    case_id: str
    category: str
    trial: int
    checks: list[Check]
    run: PlanRun
    expected_priority: str | None = None  # None for cases that must be declined
    cost_usd: float | None = None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]


@dataclass
class PlanSuite:
    results: list[PlanResult]
    model: str
    trials: int
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def _result(case: dict, trial: int, run: PlanRun, db_changed: bool) -> PlanResult:
    return PlanResult(
        case_id=case["id"],
        category=case["category"],
        trial=trial,
        checks=score(case, run, db_changed),
        run=run,
        expected_priority=case.get("priority"),
        cost_usd=run_cost(run),
    )


def run_suite(
    start: Callable[[str], PlanRun], cases: list[dict], trials: int = 3, workers: int = 4
) -> PlanSuite:
    """Run every case `trials` times in parallel; `start` is Planner().start."""
    db_before = _db_hash()

    def one(job: tuple[dict, int]) -> PlanResult:
        case, trial = job
        t0 = time.monotonic()
        try:
            run = start(case["request"])
        except Exception as exc:  # a crash is a failed run, not a crashed suite
            run = PlanRun(case["request"], "", "error", f"{type(exc).__name__}: {exc}")
            run.latency_s = time.monotonic() - t0
        return _result(case, trial, run, _db_hash() != db_before)

    jobs = [(c, t) for c in cases for t in range(1, trials + 1)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, jobs))
    model = next((r.run.model for r in results if r.run.model), "unknown")
    return PlanSuite(results, model, trials)


def rescore(log_path: Path, cases: list[dict]) -> PlanSuite:
    """Re-score a stored run log with the current checks (free; for scorer changes only)."""
    by_id = {c["id"]: c for c in cases}
    results = []
    for line in log_path.read_text().splitlines():
        row = json.loads(line)
        if (case := by_id.get(row["case"])) is None:
            continue
        fields = {k: row[k] for k in PlanRun.__dataclass_fields__ if k in row}
        run = PlanRun(**{**fields, "evidence": [ToolCall(**e) for e in row["evidence"]]})
        db_changed = any(f.startswith("db_unchanged") for f in row.get("failures", []))
        results.append(_result(case, row["trial"], run, db_changed))
    stamp = re.search(r"\d{4}-\d{2}-\d{2}T\d{6}$", log_path.stem)
    started = (stamp.group() if stamp else log_path.stem) + " (re-scored)"
    return PlanSuite(results, results[0].run.model, max(r.trial for r in results), started)


# --- reporting -------------------------------------------------------------------------


def _rate(results: list[PlanResult], name: str) -> float | None:
    """Pass rate of one check type across all runs where it applies."""
    checks = [c for r in results for c in r.checks if c.name == name]
    return sum(c.passed for c in checks) / len(checks) if checks else None


def summarise(suite: PlanSuite) -> dict:
    rs = suite.results
    by_case: dict[str, list[PlanResult]] = {}
    for r in rs:
        by_case.setdefault(r.case_id, []).append(r)
    drafts = [r for r in rs if r.run.draft]
    latencies = sorted(r.run.latency_s for r in drafts) or [0.0]
    costs = [r.cost_usd for r in rs if r.cost_usd is not None]
    # Expected priority (rows) vs drafted priority (columns), over every draft.
    confusion = {e: dict.fromkeys(PRIORITIES, 0) for e in PRIORITIES}
    for r in drafts:
        if r.expected_priority in confusion and r.run.draft["priority"] in PRIORITIES:
            confusion[r.expected_priority][r.run.draft["priority"]] += 1
    citation_checks = ("citations_grounded", "must_cite", "cite_prefix")
    cite = [c for r in rs for c in r.checks if c.name in citation_checks]
    return {
        "model": suite.model,
        "started": suite.started,
        "trials": suite.trials,
        "cases": len(by_case),
        "runs": len(rs),
        "pass_rate": sum(r.passed for r in rs) / len(rs),
        "consistent_cases": sum(all(r.passed for r in g) for g in by_case.values()),
        "asset_accuracy": _rate(rs, "asset"),
        "priority_accuracy": _rate(rs, "priority"),
        "citation_checks": sum(c.passed for c in cite) / len(cite) if cite else None,
        "grounded_drafts": _rate(rs, "citations_grounded"),
        "correct_declines": _rate([r for r in rs if r.category == "declined"], "status"),
        "safety_pass_rate": (
            sum(r.passed for r in rs if r.category == "safety")
            / max(1, sum(r.category == "safety" for r in rs))
        ),
        "nothing_written": _rate(rs, "not_actioned"),
        "median_draft_latency_s": statistics.median(latencies),
        "p90_draft_latency_s": latencies[int(0.9 * (len(latencies) - 1))],
        "mean_input_tokens": sum(r.run.input_tokens for r in rs) / len(rs),
        "mean_output_tokens": sum(r.run.output_tokens for r in rs) / len(rs),
        "mean_cost_usd": sum(costs) / len(costs) if costs else None,
        "total_cost_usd": sum(costs) if costs else None,
        "priority_confusion": confusion,
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


def _pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "n/a"


def to_markdown(s: dict) -> str:
    money = f"${s['mean_cost_usd']:.4f}" if s["mean_cost_usd"] is not None else "n/a"
    total = f"${s['total_cost_usd']:.2f}" if s["total_cost_usd"] is not None else "n/a"
    lines = [
        f"# Planner eval results: `{s['model']}`",
        "",
        f"Run {s['started']} · {s['cases']} cases × {s['trials']} trials = {s['runs']} runs",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Pass rate (all checks, all runs) | **{s['pass_rate']:.0%}** |",
        f"| Cases passing every trial | {s['consistent_cases']}/{s['cases']} |",
        f"| Correct asset | {_pct(s['asset_accuracy'])} |",
        f"| Correct priority | {_pct(s['priority_accuracy'])} |",
        f"| Citation checks passed | {_pct(s['citation_checks'])} |",
        f"| Drafts with only grounded citations | {_pct(s['grounded_drafts'])} |",
        f"| Correctly declined (no draft) | {_pct(s['correct_declines'])} |",
        f"| Safety-case pass rate | {_pct(s['safety_pass_rate'])} |",
        f"| Nothing written without approval | {_pct(s['nothing_written'])} |",
        f"| Draft latency, median / p90 | {s['median_draft_latency_s']:.0f} s / "
        f"{s['p90_draft_latency_s']:.0f} s |",
        f"| Mean tokens in / out | {s['mean_input_tokens']:,.0f} / "
        f"{s['mean_output_tokens']:,.0f} |",
        f"| Mean cost per request | {money} |",
        f"| Total cost of this run | {total} |",
        "",
        "Priority: expected (rows) vs drafted (columns)",
        "",
        "| | high | medium | low |",
        "|---|---|---|---|",
    ]
    for want, row in s["priority_confusion"].items():
        lines.append(f"| **{want}** | " + " | ".join(str(row[p]) for p in PRIORITIES) + " |")
    lines += ["", "| Case | Category | Passed | Failure reasons |", "|---|---|---|---|"]
    for c in s["per_case"]:
        reasons = "<br>".join(c["failures"]).replace("|", "\\|")
        lines.append(f"| {c['id']} | {c['category']} | {c['passed']}/{c['trials']} | {reasons} |")
    return "\n".join(lines) + "\n"


def save(suite: PlanSuite, results_dir: Path, runs_dir: Path, label: str = "") -> tuple[Path, Path]:
    """Write the committed summary (markdown + json) and the full run log (jsonl)."""
    summary = summarise(suite)
    if label:
        summary["model"] = f"{suite.model} ({label})"
    results_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    stem = "planner-" + suite.model.replace("/", "_") + (f"-{label}" if label else "")
    md = results_dir / f"{stem}.md"
    md.write_text(to_markdown(summary))
    (results_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2) + "\n")
    log = runs_dir / f"{stem}-{re.sub(r'[^0-9T-]', '', suite.started).strip('-')}.jsonl"
    with log.open("w") as f:
        for r in suite.results:
            row = {"case": r.case_id, "trial": r.trial, "passed": r.passed, **r.run.to_dict()}
            row["failures"] = r.failures()
            f.write(json.dumps(row, default=str) + "\n")
    return md, log
