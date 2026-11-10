# User-study protocol (milestone 6)

You run these sessions; nothing here has been run. The goal is evidence about realistic use, not a benchmark: can a person find a policy or procedure answer, open the exact source passage, and correctly read what the verification statuses mean. Report what you observe, including failures. With 5 to 8 participants this is qualitative evidence; do not present percentages as statistics.

## Before any session

- The deployment under test is the **promoted, migrated** app (or a preview with its own database, never one sharing the production database), with `INGEST_ENGINE=python` and `RETRIEVAL_ENGINE=python`, and the smoke script passing (`scripts/smoke.ts --lifecycle`).
- Use **non-sensitive, authorized documents only**: the synthetic Northwind set in `fixtures/corpus/`, or public policy documents (for example a public employee handbook). Never a participant's or an employer's real private documents.
- Create a fresh workspace per participant (a new Clerk user) so no one sees another's uploads, then upload the same set of 6 to 8 documents, including one scanned PDF, one CSV, and two documents that disagree (`hr-leave-policy-2024.*` and `hr-leave-policy-2026.*`). Wait until every Source shows `ready`.
- Prepare 8 tasks (below) and the answer key from `fixtures/corpus/FACTS.md`. Because FACTS.md is itself unreviewed, check each key answer against the source once before the first session.
- Screen recording is optional and needs separate, explicit consent (below). Record the screen only, never the participant's face or other windows.

## Consent note (read aloud, then give a copy; keep a signed or recorded-verbal record)

> You are helping test a tool that answers questions from documents and shows where each answer comes from. We are testing the tool, not you. The documents are sample, non-confidential documents. Taking part is voluntary and you can stop at any time without giving a reason, and ask me to delete your data. I will take notes on what you do and say, and note times. With your separate permission I will record the screen. I will not record your face or voice unless you agree to voice. Your name will not appear in any report; you will be identified as P1, P2 and so on. Notes and recordings are stored on my machine only and deleted by [date, within 90 days]. Do you agree to take part? Do you agree to a screen recording? (yes / no, recorded separately)

Do not collect names, employers or any personal information beyond a one-line role description the participant chooses to share.

## Tasks (give one at a time, read aloud, no hints)

| # | Task | Tests | Success check |
|---|---|---|---|
| 1 | "How many days of annual leave does the 2026 policy give an employee?" | basic answer plus citation | states the key value, and opened the citation |
| 2 | "Under the 2024 policy, how many days?" | version conflict, picks the right document | cites the 2024 document, not the 2026 one |
| 3 | "What does error ERR_AUTH_403 mean?" | identifier query | correct meaning and a citation |
| 4 | "Who manages the Austin depot, and what is its address?" | table or CSV row | correct row, citation shows the row |
| 5 | "Open the source for your answer to task 1 and show me the exact passage." | citation opening | reaches the highlighted passage unaided |
| 6 | "Is every statement in that answer supported? How can you tell?" | reading verification statuses | explains at least one status correctly (Verified, Partial, Unsupported, Uncited, Invalid citation, Unverified) |
| 7 | "Ask something the documents do not contain (for example the CEO's phone number)." | abstention | notices the system says it lacks evidence, and does not accept an invented answer |
| 8 | "Upload this changed copy of the policy, then ask task 1 again. Then delete the old document and ask once more." | update and delete | sees version 2 cited after the update; sees the deleted source withheld |

Stop a task after 4 minutes and record "not completed". Give no hints; if the participant asks, say "do what you would normally do". If the app errors, record the error text as shown and let them try to recover; recovery behaviour is data.

## What to record, per participant and task

| Field | How |
|---|---|
| Participant id, date, deployment URL and commit SHA, documents loaded | once per session |
| Completed (yes / partly / no) | against the success check, decided after the session from notes |
| Time to first useful answer, and time to evidence (from submitting the question to the highlighted passage open) | stopwatch, seconds |
| Wrong turns: wrong source opened, misread status, accepted a wrong answer | tally with a one-line description |
| Interpretation of each verification status they met | their own words, verbatim; mark correct or not against the status definitions in `README.md` |
| Errors and recovery | error text, what they tried, whether they recovered, seconds |
| One quote that shows confusion, one that shows trust | verbatim |
| Post-task answer to "How sure are you that this answer is right, 1 to 5?" and why | number plus words |
| Anything the system got wrong (answer or citation) | save the question, the answer and the cited passage |

Keep notes in a spreadsheet with one row per participant-task and the columns above. Do not paste document text longer than a quote into the notes.

## Analysis (after all sessions)

1. Completion per task out of N, listed as counts ("5 of 7"), with the failures described.
2. Median and range of time to evidence per task, noting N.
3. For each status: how many participants who met it interpreted it correctly.
4. Every system error (wrong answer, wrong citation, crash) as a numbered finding with the question and the evidence. Feed wrong answers into new golden-set candidates (a **new** dataset version; never edit the reviewed one).
5. Top 5 usability problems by frequency, each with a quote.

Report limits plainly: small N, synthetic documents, a single deployment, facilitator effects. Do not report any figure as a measured accuracy of the retrieval system; that belongs to `docs/scribe2/benchmarks/final.md`.

## Done when

The notes spreadsheet, the consent records and (if any) recordings exist on your machine; a short write-up with sections 1 to 5 exists; findings that need code changes are filed as issues.
