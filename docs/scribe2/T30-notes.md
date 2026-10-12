# T30 eval run dashboard

Pages: `/eval` (runs), `/eval/[runId]` (detail, per-category table, failing questions), `/eval/compare?a=&b=`.

**Access rule.** `eval_runs`/`eval_results` are global (no workspace column). A signed-in user may open /eval only if they
own a `workspaces` row of kind `eval` (`owner_user_id`) or hold a `workspace_members` row in one. Everyone else, including signed-out
users, gets `notFound()` from `app/eval/layout.tsx` (same as a missing path). The sidebar link appears only when
`getEvalAccess()` (server action, registered as `session` in the T27 table) returns true. Grant access out of band, e.g.
`INSERT INTO workspace_members (workspace_id, user_id, role) VALUES ('<eval workspace id>', '<clerk user id>', 'member')`.
Only rows with `eval_runs.meta->>'workspace_kind' = 'eval'` are read; personal workspace data is never touched.

**Publishing.** `scribe-eval publish-run <run dir> --database-url URL [--dataset DIR] [--seeded] [--mark-fake] [--parse-lost DOC]`
loads `run.json` + `results.jsonl` (score) or `answers.jsonl` (answer) idempotently (run id is the key). Measurement/FAKE/seeded
flags, model ids, summary CIs live in `meta`; per-case category/question/failure/candidates live in `eval_results.metrics._case`
and `ranking`. Failure categories come from `scribe_eval/failures.py::classify_failure` (pure; unknown input -> `unclassified`).
`parse lost the content` is only assigned when `--parse-lost` names the document (the harness cannot detect it itself).

**Compare.** Runs must share kind, dataset version and split. Paired bootstrap is a seeded TypeScript implementation (2,000
resamples), not the Python 10,000-resample one; per-category deltas are plain means. Fake, seeded or meta-less runs are never
the "best measured run" and any comparison involving one is warned as not evidence.

**Trace links.** No trace inspector route exists for eval traces (traces are workspace-scoped); the drilldown shows the stored
top-10 candidate table and the trace id as text when recorded.
