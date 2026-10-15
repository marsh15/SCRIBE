# Scribe 2.0 demo script (15 scenes)

This script is scaled to what exists on branch `scribe2/m0-foundation`. The original plan text with its scene list is not in the repository, so the 15 scenes below are reconstructed from the feature set. Scenes the original plan may have contained but that depend on unavailable work are listed at the end as REPLACED or SKIPPED.

## Before you record: what must be live-verified first

Nothing in this script has been run against a deployed app or real providers. Work through these prerequisites in order, and do not demo a scene whose prerequisite failed.

| # | Prerequisite | How | Needed by scenes |
|---|---|---|---|
| P1 | Vercel preview deploy works (Python co-deploys, `/api/py/health` answers 200 with a minted token) | `docs/scribe2/T02-verification.md` | all |
| P2 | Real keys set: `GOOGLE_GENERATIVE_AI_API_KEY`, `VOYAGE_API_KEY`, `INTERNAL_SERVICE_SECRET`, `BLOB_READ_WRITE_TOKEN` | host settings; never in git | 2-12 |
| P3 | Demo database is a Neon branch or a local DB (never production); migrations applied incl. `0013`-`0019` | README Quick start; Neon check in README status | all |
| P4 | `RETRIEVAL_ENGINE=python` and `INGEST_ENGINE=python` set for the demo environment (both default to `legacy`) | env | 3-9 |
| P5 | Demo account created by signing in once, then seeded: `scripts/demo-seed/demo_seed.py --apply` | `README.md` Demo data | 2-12 |
| P6 | Rehearse every question once and write down what the app actually answers; compare with `fixtures/corpus/FACTS.md` (itself still unreviewed) | by hand | 3-9 |

Seeded Sources have no stored original (no Blob object), so the Original tab and PDF highlighting do not work for them. For scenes 8 and 10 upload at least one document through the browser instead (for example `fixtures/corpus/onboarding-handbook.pdf` and `hr-leave-policy-2026.pdf`).

Do not make any accuracy, speed or "verified" claim aloud that you have not measured. Say "not yet measured" instead. The golden set is unreviewed and the answerability threshold is uncalibrated.

## The scenes

| # | Scene | What you do | What the viewer should see | Depends on | Status |
|---|---|---|---|---|---|
| 1 | The problem and the corpus | Show `fixtures/corpus` (24 Northwind documents, 5 formats, one scanned PDF) and `FACTS.md` | A realistic mixed-format set with planted facts | none | ready |
| 2 | Seed and sign in | Run the seed dry run, then `--apply`; open the app signed in as the demo account | Sources list with all documents `ready` | P1-P5 | needs live check |
| 3 | A plain factual question | Ask: "What time should I show up on my first day to collect my access badge?" | Streamed answer with `[E1]`-style citations; stage timeline in the inspector | P4, P6 | needs live check |
| 4 | Identifier query | Ask: "What does ERR_AUTH_403 mean?" | Answer from the exact identifier match (lexical search) | P4 | needs live check |
| 5 | Table lookup | Ask: "Who runs the Austin depot and what is its street address?" | Answer from a CSV or table row; citation shows the row | P4 | needs live check |
| 6 | Cross-document question | Ask: "Who manages the depot that vehicle NW-T-0417 is based at?" | Two sources cited | P4 | needs live check |
| 7 | Version conflict | Ask for the annual leave days "under the 2024 leave policy" and then "under the 2026 policy" | Each answer cites the right document, not the other | P4 | needs live check; check the answers against FACTS.md |
| 8 | Open a citation | Click a citation | The source opens at the exact page and passage (PDF highlight, text quote or CSV row); "page unavailable" appears where position is unknown | upload via browser, P1 | needs live check |
| 9 | Claim verification | Look at the badges after an answer finishes streaming | Per-claim status (Verified, Partial, Unsupported, Uncited, Invalid citation, Unverified); a note when verification is unavailable | P2 (Gemini) | needs live check; verifier never run against live Gemini |
| 10 | Upload a new version | Upload a changed copy of a document; while it builds, search still answers from the old version | Versions tab shows v1 and v2; the old one stays searchable until activation | P1, P2 | needs live check |
| 11 | Delete a source | Delete a Source, then ask a question it alone could answer | It disappears from retrieval at once; old citations say "source deleted" | P4 | needs live check |
| 12 | Scanned page | Upload `facility-safety-notice-scanned.pdf` | OCR text flagged "OCR text, verify against original" | P2, Gemini OCR (never run live) | needs live check |
| 13 | Retrieval lab | Open `/compare`, run one query across 2-5 profiles | Side-by-side results with rank movement, per stage | P2 (Voyage for rerank profiles) | needs live check |
| 14 | Eval dashboard | Open `/eval` (needs an eval workspace membership) with a run published via `scribe-eval publish-run` | Run list, per-category table, failing questions with failure categories | a real scored run | SUBSTITUTED: only possible with a real (not fake or seeded) run; use dev/validation split only |
| 15 | Safety and honesty close | Show `threat-model.md` residual risks and the status list in the README | An explicit statement of what is proven by tests and what is not | none | ready |

## Replaced or skipped scenes

- **Calibrated "I don't know" (abstention) demo: SKIPPED.** It needs T17 (threshold calibration); today `tau` is 0.0 and the gate rejects nothing.
- **Benchmark results slide (Scribe 1.0 vs 2.0 numbers): SKIPPED.** No baseline run and no final run exist; `docs/scribe2/benchmarks/final.md` is a placeholder. Show the method instead (`rankings-format.md`, paired bootstrap) and say "not yet measured".
- **CI regression gate blocking a bad PR: SKIPPED.** The gate is unarmed. You may show `release-rules.md` and `scribe-eval gate --status` output (reports not armed).
- **Scaling slide: SUBSTITUTED** with the synthetic `performance.md` findings, labelled synthetic, one Mac, 1k/10k/30k chunks, 100k not measured.
- **Two-user isolation live demo: SUBSTITUTED** with the test results (`intelligence/tests/security`, `__tests__/security`) because FORCE RLS is deferred and no second account has been exercised on a deployment.
