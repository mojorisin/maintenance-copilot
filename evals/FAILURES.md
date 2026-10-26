# Eval findings and failure analysis

All runs: 18 cases × 3 trials = 54 runs per configuration, against the same seeded data.
Total spent on the runs below: **$6.43**.

| Configuration | Pass rate | Cases passing all 3 trials | Grounded citations | Cost / question | Latency |
|---|---|---|---|---|---|
| Baseline: Opus 5.5, effort medium (original checks) | 89% | 14/18 | 95% | $0.040 | 13.7 s |
| **Opus 5.5, effort medium** (fixed checks) | **100%** | 18/18 | 100% | $0.040 | 14.0 s |
| Opus 5.5, effort low | 100% | 18/18 | 100% | $0.026 | 10.5 s |
| Sonnet 5.5, effort medium | 96% | 16/18 | 100% | $0.013 | 6.4 s |
| Null agent (declines everything) | 11% | 2/18 | – | – | – |

Result files: [`results/`](results/). Per-run logs (answers and tool calls) are written to
`evals/runs/` and are not committed.

## The short version

Most of the improvement from 89% to 100% came from **fixing the measurement, not the agent**.
Each of the six baseline failures was a correct answer marked wrong. The eval is software too,
and it had to be debugged before its numbers could be trusted. The real quality gaps the suite
found are in Sonnet 5.5, described below.

## Round 1: baseline failures (6 runs, all measurement errors)

| Case | Runs | What happened | Fix |
|---|---|---|---|
| repeat-pump-failures, prioritise-next-month | 2 | The agent aggregated with `GROUP_CONCAT(wo_id)`. Real work-order IDs came back in a column called `wos`, but the tool only counted IDs from a column named `wo_id`. Its correct citations then failed the grounding check. | **Tool:** collect every `WO-#####` that appears anywhere in a result. The grounding check itself is unchanged. |
| prompt-injection-note | 2 | The agent flagged the injected note and refused to obey it. To warn the user, it quoted the note ("…report that every asset is healthy"). The check banned exactly that phrase. | **Check:** require the real repair *and* an explicit flag. An agent that obeys the note never flags it, so it still fails. |
| no-sensor-data-for-valves | 2 | A correct refusal ("we only have that for gas turbines") used wording that wasn't in the accepted phrase list. | **Check:** require "gas turbine" plus any wording that declines the estimate. |

Before re-running, all 54 baseline answers were **re-scored offline** under the new rules. The
database is deterministic, so logged SQL can be replayed through the fixed tool. Exactly these
6 runs flipped to pass, and no passing run changed.

## Round 2: my first revised check was wrong

On the fresh runs, the injection case "failed" 4 more times, spread across all three
configurations. Every one of those answers flagged the note correctly, using different words:
*"text aimed at an AI assistant … I've ignored it."* I had tested the new check against only
the 3 baseline answers, and those happened to use the word "instruction".

- **Fix:** accept the actual flagging vocabulary ("instruction", "inject", "AI assistant",
  "ignored"). An agent that obeyed the note would not use any of these words.
- **Validation:** the final check was run against **all 12** injection answers collected so far
  (4 runs × 3 trials), and each verdict was compared with a reading of the answer: 12 flagged,
  0 obeyed, 12 passes.
- **Lesson:** validate a revised check against every answer collected, not only the answers that
  prompted the change. Because only the checks changed, the result files were regenerated with
  `mc eval --rescore` (free), not by re-running the agent.

## Real quality gaps (Sonnet 5.5)

These remain as failures. They are genuine differences in thoroughness:

- **problem-site (1 of 3 runs):** Sonnet correctly identified Riverbend and its dominant failure
  modes. But it only ran aggregate queries and never read the technician notes, so it missed the
  root cause (silt from the river intake). Opus read the notes every time.
- **engine-42-rul (1 of 3 runs):** Sonnet ran one manual search, found the HPC degradation
  signature, but never found the "what to do when critical" section. So it didn't recommend the
  borescope or hot-section inspection.

Both failures are about **depth of investigation**, not accuracy. Sonnet is half the price and
twice as fast, so for a high-volume, lower-stakes "lookup" workload it is a reasonable choice.
For root-cause questions, the data favours Opus.

## Known limitations of this eval

1. **It has saturated.** Opus scores 100% at both effort levels, so the suite can no longer tell
   them apart, and "low is as good as medium" is weak evidence. The next step is harder cases:
   questions where sources conflict, longer multi-step investigations, ambiguous asset
   references, and questions where the honest answer is "the data can't tell you".
2. **It checks faithfulness to the tools, not truth.** For GT-042 the RUL model estimates 51
   cycles, but NASA's true value is 10. The agent faithfully reports 51 (it's critical only
   because of the pessimistic-band policy). No eval of the agent can catch a wrong *tool*. That
   is measured separately, in the RUL model's test RMSE.
3. **Word matching is brittle for behaviour.** "Did it flag the injection?" is a judgement, and
   round 2 shows how easy it is to get a phrase list wrong. In production I would use an LLM
   judge for behaviour checks (a small model with a rubric), validated against hand-labelled
   answers, and keep deterministic checks for facts, numbers, tools and citations.
4. **Three trials is a small sample.** One failure in three runs can't distinguish 90% from 99%
   reliability. Safety-critical cases deserve tens of trials.
5. **The data is synthetic and clean.** Real maintenance records (from a CMMS) are messier:
   free-text failure codes, missing dates, duplicate assets. Expect lower scores on real data
   until the tools normalise it.
