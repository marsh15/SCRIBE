# Cases to human-review first

**T28 update: 80 of the 200 cases (`origin: machine-drafted-t28`, `reviewed: false`) were drafted by a model and are completely unreviewed. Review them via [`UNREVIEWED_T28.md`](UNREVIEWED_T28.md), which lists every one with its question, UNVERIFIED reference answer and the specific concerns. No reference answer in this dataset, old or new, is verified. The original 120 cases below are also still pending human review. Do not publish milestone numbers from the test split until both sets are reviewed.**

Human review status: NOT YET REVIEWED. Every case needs a person to confirm the fact, quote and label before test-split numbers are published. Start with the cases the author was least sure of:

| Case | Concern |
|---|---|
| ua-10 | SLA gives a P2 response time but no credit; "not stated" is an inference. (Checked: no P2 credit text exists.) |
| ua-11, ua-12 | Unanswerable because Austin revenue and the CloudNorth fee are absent. (Checked by grep: neither is stated.) |
| nr-08 | End date 31 March 2029 is computed from "36 months from 1 April 2026". |
| nr-07 | Glossary Tier A quote (grade 1) only establishes that Paris is Tier A. |
| nr-09 | Used Milan. The Q3 report once said Warsaw missed the 95% target while its table showed 95.2; the prose was fixed in the corpus, re-check. |
| cd-04 | Reads `employer_pays_pct` as the employer's share of the premium. |
| cd-06 | Combines Board approval date, price-list valid_from and MSA rate. |
| cd-11 | One office-locations row holds both manager and cap, so only one evidence item. |
| tb-04, tb-11 | Answer depends on column headers that the quoted row does not carry. |
| tb-10 | Handbook directory row for finance (Finance Helpdesk, 4120). |
| id-11 | The heading "INC-2025-117" plus an outage sentence as the grade-2 quote. |
| vc-09, vc-12 | Rest on handbook staleness and cross-document date comparison. |
| ps-05, eq-11 | Share the same two non-compete sentences; differ only in framing. |
| dm-07, dm-08 | Mirror pair for the meal-allowance conflict. |
| dm-10, dm-11 | Weak lookalikes; dm-11's `forbidden` doc has the same value. |
| dm-12, eq-03 | Liability caps; dm-12 pins Helios (SLA) and forbids the MSA. |
