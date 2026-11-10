# Golden-set review checklist (one page, for the human)

Nothing in this repository can mark a case `reviewed: true` for you, and no retrieval number is publishable until you have. All 200 cases are unreviewed today (`reviewed` is absent or false on every line of `benchmarks/datasets/golden-v1/cases.jsonl`; 80 of them are also machine-drafted, `origin: machine-drafted-t28`).

## What each phase unlocks

| Phase | Cases | Unlocks | Time (rough) |
|---|---|---|---|
| 1. `validation` | 40 (20 machine-drafted) | **Arming the retrieval gate** (`scripts/arm-gate.sh`). The gate scores only this split. | 1-2 h |
| 2. `dev` | 80 (30 machine-drafted) | Honest dev-split tuning numbers and the lexical `or_rank` evaluation | 2-3 h |
| 3. `test` | 80 (30 machine-drafted) | The one-time milestone report (`final.md`). **Review the labels; never look at test results to change them (INV-12).** | 2-3 h |

Do phase 1 first. Phases 2 and 3 can wait.

## Start

```bash
uv run --project intelligence scribe-eval review \
  --items benchmarks/datasets/golden-v1/cases.jsonl --kind case --corpus fixtures/corpus \
  --split validation,dev,test
```

Open the printed `http://127.0.0.1:8765/`. Keys: `a` accept, `e` edit, `d` drop (asks for a reason), `n` next, `p` previous. It resumes at the first case you have not decided, so you can stop and continue. `--split` puts the named splits first, in that order; inside a split the cases the author was least sure of come first.

## For every case, check four things against the source page shown beside it

1. **Quote.** The highlighted quote really says the fact (a red "QUOTE NOT FOUND ON THIS PAGE" means the label or page is wrong: edit or drop).
2. **Answer.** The reference answer follows from the quote(s) alone. Redo any arithmetic or date calculation yourself.
3. **`must_include`.** Each string is something every correct answer must contain, and is not so strict that a correct paraphrase fails.
4. **`forbidden`.** The listed document is genuinely the wrong one for this question, and no other lookalike is missing from the list.

Accept only if all four hold. If you edit, the tool re-validates against the corpus before writing. If you drop cases, re-balance and re-validate before any number exists:

```bash
uv run --project intelligence scribe-eval assign-splits benchmarks/datasets/golden-v1 --seed 20260101   # reassigns all splits
uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
```

## Phase 1: the 40 validation cases, in the order the tool shows them

`ua-12, nr-07, cd-04` come from the author's "least sure" list (`REVIEW_FIRST.md`); the next 14 are machine-drafted cases with specific concerns listed in `UNREVIEWED_T28.md`; then the rest.

| Look closest at | Why |
|---|---|
| `ua-12` | Unanswerable because the CloudNorth fee is absent. The author grepped; confirm. |
| `nr-07` | The Tier A glossary quote only establishes that Paris is Tier A. |
| `cd-04` | Reads `employer_pays_pct` as the employer's share of the premium. |
| `cd-18, cd-19, dm-14, dm-19, eq-15, eq-19, id-15, id-17` | Machine-drafted. Lookalike documents (`dm-19`), the same sentence as another case (`eq-19`), identifiers (`id-15/17`). |
| `nr-18, nr-19, ps-15, ps-18, sf-15, sf-16` | Machine-drafted. `nr-18`: 6 x 2.25 = 13.5 assumes 6 completed months, from the 2026 accrual rule only. |
| `tb-17, tb-20` | The answer depends on column headers the quoted row does not carry. |
| `ua-15, ua-20` | Machine-drafted "not stated" cases: confirm the fact really is absent (vests are not a uniform policy; the CEO is never named). |
| `vc-13, vc-14` | "No change / they agree": the same sentence exists once in each document. |

Full order for phase 1:

`ua-12, nr-07, cd-04, cd-18, cd-19, dm-14, dm-19, eq-15, eq-19, id-15, id-17, nr-18, nr-19, ps-15, ps-18, sf-15, sf-16, sf-01, sf-05, tb-05, tb-06, id-02, id-04, nr-10, cd-10, vc-07, vc-11, eq-04, eq-12, ps-04, ps-10, dm-04, dm-06, ua-07, tb-17, tb-20, ua-15, ua-20, vc-13, vc-14`

## Phase 2: dev, start with these

`ua-11, nr-08, cd-11, tb-04, tb-11, id-11, vc-09, eq-11, dm-07, dm-10, dm-11` (the author's concerns in the dev split: `nr-08` the 31 March 2029 end date is computed; `dm-07` mirrors `dm-08` for the meal allowance; `dm-10/11` are weak lookalikes), then the 30 machine-drafted dev cases (`cd-13, cd-16, cd-17, dm-16, dm-18, dm-20, eq-13, eq-17, eq-20, id-14, id-16, id-20, nr-13, nr-14, nr-16, ps-13, ps-14, ps-19, sf-13, sf-14, sf-17, ...`), then the rest in the tool's order. `eq-20`: the SEV2 answer (5 days) is an inference from a clause that names only SEV1.

## Phase 3: test

Concern cases in the test split: `ua-10, nr-09, cd-06, tb-10, vc-12, ps-05, dm-08, dm-12, eq-03` plus the machine-drafted ones. Review labels only. Do not run any scoring on the test split while you do this.

## Done when

```bash
uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1 --require-reviewed   # exit 0 = all 200 reviewed
python3 -c "import json;r=[json.loads(l) for l in open('benchmarks/datasets/golden-v1/cases.jsonl')];print(sum(1 for c in r if c['split']=='validation' and c.get('reviewed') is not True),'validation cases still unreviewed')"   # 0 = the gate can be armed
```

Commit the changed `cases.jsonl` (and `dropped.jsonl` if you dropped any) in a PR titled with your review; the diff is the audit trail. Then continue with `docs/scribe2/HUMAN_TODO.md` step 2.
