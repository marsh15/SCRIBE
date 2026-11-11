# T28 cases awaiting human review (80)

**All 80 cases below were drafted by a model (task T28), grounded in the existing corpus text, and have NOT been reviewed by a person.** Each carries `"origin": "machine-drafted-t28"` and `"reviewed": false` in `cases.jsonl`. The machine checks (`scribe-eval validate`) prove only that every quote occurs exactly once in its document, is within one sentence/row and at most 200 characters, and that the schema, split and balance rules hold. **They do not prove that the question is good, that the reference answer or `must_include` strings are right, that the grades are right, or that the `forbidden` list is complete. No reference answer here is verified.**

Splits: assigned to these 80 only, with `scribe-eval assign-splits benchmarks/datasets/golden-v1 --seed 20260101 --only-origin machine-drafted-t28` (stratified per category, 3 dev / 2 validation / 3 test each). The 120 original cases kept their splits.

## How to review

For each row: (1) read the quote in the source document and confirm it states the fact; (2) confirm the reference answer follows from the quote(s) alone (and for numeric cases, redo the arithmetic); (3) confirm every `must_include` string is something a correct answer must contain, and is not too strict; (4) for `forbidden`, confirm the listed document really is the wrong one for the question; (5) then set `"reviewed": true` in `cases.jsonl` for that case (do this only after the test split has not been used to tune anything; fixing a wrong test case is allowed, record it in the commit message).

## Cases needing the closest look

| Case | Concern |
|---|---|
| tb-13..tb-16, tb-18, tb-20 | Answer depends on column headers the quoted row does not carry (for tb-15 and tb-17 the annual-cap and price columns are inferred from the benefits and price-list headers). |
| nr-14 | Difference of two quotes from two policy versions; 'unused days' only. |
| nr-15 | Annual fee computed as 12 x 18,400; the liability sentence (grade 1) states the same total. |
| nr-18 | 6 x 2.25 = 13.5 assumes 6 completed months, from the 2026 accrual rule only. |
| nr-20 | End date 30 June 2028 is derived from '36 months from 1 July 2025'. |
| cd-14 | The CISO (SEC policy) is matched to the Board minutes action owner by name only. |
| cd-15 | Glossary quote has a trailing clause; check the quote is the full sentence. |
| cd-16 | The minutes quote is a long attendee list (under 200 characters). |
| cd-17 | Milan 'on-time rate' is the third numeric column of the Q3 hub table (93.7), which the row does not label. |
| vc-13, vc-18 | 'No change / they agree' cases; the identical sentence appears once in each document. |
| eq-16 | FAQ rule and carve-out are two separate sentences of the same document. |
| eq-19 | Same sentence as eq-01 (customs) but asks about the 'incorrect data' limb. |
| eq-20 | The 10-working-day exception names only SEV1; the SEV2 answer (5 days) is an inference from that scope. |
| dm-15, dm-18, dm-19 | Lookalike docs share the sentence shape (confidentiality notice / 36-month term / EUR 45 meals); check that `forbidden` captures the real distractor. |
| dm-17 | Mirror of dm-12 (cap pinned to the MSA, `forbidden` is the SLA). |
| ua-15 | High-visibility vests exist in the safety notice; confirm they are not a 'dress code or uniform policy'. |
| ua-20 | The CEO is mentioned (ransom approval) but never named; the Board chair is named but not as CEO. |

## All 80 cases

| Case | Split | Category | Question | Reference answer (UNVERIFIED) | Evidence docs |
|---|---|---|---|---|---|
| `cd-13` | dev | cross_document | Is the fuel surcharge in the customer FAQ the same as in the Fjordline MSA? | Yes. The FAQ applies 6.5% of freight to all shipments and the MSA table lists a 6.5% fuel surcharge. | faq-customers.md, master-services-agreement.docx |
| `cd-14` | test | cross_document | Which board action is owned by the CISO, and what is the deadline? | The CISO is Tomasz Kowalski; he owns running the phishing simulation, due 30 June 2026. | security-incident-response.pdf, meeting-minutes-2026-03.docx |
| `cd-15` | test | cross_document | Does the glossary definition of SEV1 match the incident response policy's acknowledgement time? | Yes, both give 15 minutes for acknowledging a SEV1 incident. | glossary.txt, security-incident-response.pdf |
| `cd-16` | dev | cross_document | Who is the HR contact in the handbook directory, and did that person attend the March 2026 Board meeting? | Elena Rossi (ext. 4100) is the HR and leave contact, and she was present at the 12 March 2026 meeting. | onboarding-handbook.pdf, meeting-minutes-2026-03.docx |
| `cd-17` | dev | cross_document | Who manages the Milan depot and what on-time rate did Milan post in Q3 2026? | Giulia Ferrara manages Milan, which posted 93.7 on the Q3 2026 table. | office-locations.csv, q3-operations-report.pdf |
| `cd-18` | validation | cross_document | Was Helios availability in November 2025 below the guaranteed level? | Yes. Helios guarantees 99.9% monthly availability but November 2025 was 99.31%. | vendor-sla.pdf, incident-2025-11-postmortem.txt |
| `cd-19` | validation | cross_document | In which country does Helios process data according to the addendum, and what is its contract number? | Helios Telematics GmbH processes the GPS and telemetry feed in Germany under contract number VND-8812. | data-processing-addendum.docx, vendor-sla.pdf |
| `cd-20` | test | cross_document | How often is the customer tracking page updated, and what format do tracking numbers have? | The tracking page updates every 15 minutes; tracking numbers are NW followed by 10 digits. | faq-customers.md, api-spec.md |
| `dm-13` | test | disambiguation | According to the travel guidelines, what is the hotel night cap in Tier A cities? | The travel guidelines cap hotel nights in Tier A cities at EUR 175. | travel-guidelines.docx |
| `dm-14` | validation | disambiguation | According to the expense policy, what is the hotel night cap in Tier A cities? | The expense policy caps hotel nights at EUR 160 in Tier A cities (EUR 120 elsewhere). | expense-policy.pdf |
| `dm-15` | test | disambiguation | In the data processing addendum, whose property is the document and what is the KvK number? | It is the property of Northwind Logistics B.V., Rotterdam (KvK 24081793). | data-processing-addendum.docx |
| `dm-16` | dev | disambiguation | In the security incident response policy, which Rotterdam control room number is given for a life-threatening situation? | +31 10 555 0199, after calling the site emergency line 0800-555-0112 first. | security-incident-response.pdf |
| `dm-17` | test | disambiguation | What is the Provider's total aggregate liability cap per contract year in the Fjordline MSA? | EUR 2,000,000 in any contract year. | master-services-agreement.docx |
| `dm-18` | dev | disambiguation | From what date does the 36-month term of the Helios agreement run? | The Helios contract runs 36 months from 1 July 2025. | vendor-sla.pdf |
| `dm-19` | validation | disambiguation | What does the onboarding handbook say domestic travel meals are reimbursed up to per day? | Up to EUR 45 per day. | onboarding-handbook.pdf |
| `dm-20` | dev | disambiguation | What on-time delivery did the March 2026 Board minutes record for Milan in Q4 2025? | 94.1% against a target of 95%. | meeting-minutes-2026-03.docx |
| `eq-13` | dev | exception_qualified | Can a hotfix be deployed on a Friday? | Yes, hotfixes may be deployed outside the standard windows with written approval from the CTO. | runbook-deploy.md |
| `eq-14` | test | exception_qualified | Can a standard deployment go out on Tuesday 23 December? | No. No deployments are allowed between 15 December and 5 January. | runbook-deploy.md |
| `eq-15` | validation | exception_qualified | Can a line manager fly business class on a 6-hour flight? | No. Business class on flights over 4 hours is allowed only for Directors and above. | travel-guidelines.docx |
| `eq-16` | test | exception_qualified | Does the standard EUR 20 per kg liability apply to a Fjordline shipment? | No. Customers with a Master Services Agreement are governed by that agreement instead of the standard terms. | faq-customers.md |
| `eq-17` | dev | exception_qualified | Can the CISO alone approve paying a ransom? | No. Ransom payments need written approval from both the CEO and the CISO. | security-incident-response.pdf |
| `eq-18` | test | exception_qualified | Does Helios's scheduled Sunday maintenance count against its availability guarantee? | No. Scheduled maintenance is excluded from availability, limited to Sundays 02:00-04:00 CET, at most 4 times per month. | vendor-sla.pdf |
| `eq-19` | validation | exception_qualified | Is a delay credit payable when the delay was caused by incorrect data the Customer supplied? | No. No delay credit is payable where the delay is caused by incorrect data supplied by the Customer. | master-services-agreement.docx |
| `eq-20` | dev | exception_qualified | How soon must the post-incident review be held for a SEV2 incident caused by a third-party outage? | Within 5 working days; the 10-working-day exception applies only to SEV1 incidents. | security-incident-response.pdf |
| `id-13` | test | identifier_exact | Which depot is vehicle NW-R-0119 based at? | NW-R-0119 is based at depot D-RTM. | fleet-inventory.csv |
| `id-14` | dev | identifier_exact | What does error ERR_RATE_429 mean? | ERR_RATE_429 is HTTP 429: rate limit exceeded. | api-spec.md |
| `id-15` | validation | identifier_exact | What is the problem described in ticket INV-2051? | INV-2051: label PDFs for Austin shipments render the address in a wrong font. | api-spec.md |
| `id-16` | dev | identifier_exact | What is FUEL-SUR on the price list and from when is it valid? | FUEL-SUR is the fuel surcharge, a percent of freight, valid from 2026-05-01. | price-list-2026.csv |
| `id-17` | validation | identifier_exact | BEN-DEN-01: what benefit is this and who qualifies? | BEN-DEN-01 is the dental benefit, for employees after probation. | benefits-table.csv |
| `id-18` | test | identifier_exact | What did the fix for INV-2061 address? | INV-2061 fixed label rendering for A6 thermal printers. | changelog.txt |
| `id-19` | test | identifier_exact | Which depot has the code D-LYS and where is it? | D-LYS is the Lyon depot, at Rue des Entrepots 9, 69007 Lyon. | office-locations.csv |
| `id-20` | dev | identifier_exact | What is the unit price for STO-PAL-WK? | STO-PAL-WK (pallet storage per week) is 3,20 per pallet. | price-list-2026.csv |
| `nr-13` | dev | numeric_reasoning | By how many million euros did fuel cost rise from Q2 to Q3 2026? | Fuel cost rose from EUR 31.2 million to EUR 33.0 million, an increase of EUR 1.8 million. | q3-operations-report.pdf |
| `nr-14` | dev | numeric_reasoning | How many more unused leave days can be carried over under the 2026 policy than under the 2024 policy? | The 2026 policy allows 8 days against 5 in 2024, so 3 more days. | hr-leave-policy-2024.pdf, hr-leave-policy-2026.pdf |
| `nr-15` | test | numeric_reasoning | What is the total annual fee Northwind pays Helios? | The monthly fee is EUR 18,400, so 12 months cost EUR 220,800. | vendor-sla.pdf |
| `nr-16` | dev | numeric_reasoning | A Fjordline freight charge is EUR 4,000. How much fuel surcharge is added? | The fuel surcharge is 6.5% of freight, so EUR 260. | master-services-agreement.docx |
| `nr-17` | test | numeric_reasoning | What is the largest delay credit Fjordline can receive on a EUR 2,000 shipment? | The credit is capped at 25% of the shipment charge, so EUR 500. | master-services-agreement.docx |
| `nr-18` | validation | numeric_reasoning | Under the 2026 leave policy, how many leave days does a new joiner accrue after 6 completed months? | At 2.25 days per completed month, 6 months give 13.5 days. | hr-leave-policy-2026.pdf |
| `nr-19` | validation | numeric_reasoning | How much higher is the hotel night cap in Tier A cities than elsewhere under the expense policy? | The cap is EUR 160 in Tier A cities and EUR 120 elsewhere, so EUR 40 higher. | expense-policy.pdf |
| `nr-20` | test | numeric_reasoning | On what date does the Helios contract end? | The contract runs 36 months from 1 July 2025, so it ends on 30 June 2028. | vendor-sla.pdf |
| `ps-13` | dev | paraphrase_semantic | What is the maximum number of calls per minute before my key gets throttled? | The rate limit is 600 requests per minute per API key. | api-spec.md |
| `ps-14` | dev | paraphrase_semantic | Within how many days of starting must I finish the privacy training? | The mandatory data protection course must be completed within the first 30 days (it takes 60 minutes). | onboarding-handbook.pdf |
| `ps-15` | validation | paraphrase_semantic | What happens to goods that have been left in the warm for more than twenty minutes? | A pallet above 8 degrees C for more than 20 minutes must be quarantined and cannot be shipped. | ops-manual.md |
| `ps-16` | test | paraphrase_semantic | How much warning must Northwind give to walk away from the telematics vendor early? | Northwind may terminate for convenience with 90 days' written notice. | vendor-sla.pdf |
| `ps-17` | test | paraphrase_semantic | What uptime is promised for the vehicle tracking data stream each month? | Helios guarantees a monthly availability of 99.9% for the GPS and telemetry feed. | vendor-sla.pdf |
| `ps-18` | validation | paraphrase_semantic | How big is the premises we rent in Texas and for how many years? | The Austin depot lease covers 2,400 square metres for 5 years. | meeting-minutes-2026-03.docx |
| `ps-19` | dev | paraphrase_semantic | When can I reach the tech help desk? | IT support is available Monday to Friday from 07:30 to 18:00 CET. | onboarding-handbook.pdf |
| `ps-20` | test | paraphrase_semantic | How long must a login secret be and how often does it need replacing? | Passwords must be at least 14 characters long and are changed every 180 days. | onboarding-handbook.pdf |
| `sf-13` | dev | single_fact | How long does Northwind keep forensic images after a security incident? | Forensic images are retained for 18 months. | security-incident-response.pdf |
| `sf-14` | dev | single_fact | What is the standard monthly limit on a corporate card? | The standard corporate card limit is EUR 3,000 per month. | expense-policy.pdf |
| `sf-15` | validation | single_fact | Which tool must be used to book business travel, and how far ahead? | Business travel is booked through the NorthTravel tool at least 14 days before departure. | travel-guidelines.docx |
| `sf-16` | validation | single_fact | How long can a truck wait at the gate before a yard alert is triggered? | Trucks that wait more than 30 minutes at the gate trigger a yard alert. | ops-manual.md |
| `sf-17` | dev | single_fact | How many lost-time injuries were recorded in Q3 2026 and how does that compare with Q2? | Three lost-time injuries were recorded in Q3, versus five in Q2. | q3-operations-report.pdf |
| `sf-18` | test | single_fact | How long is the safe handling course and who has to take it? | The safe handling course takes 4 hours and is mandatory for everyone working on a depot floor. | onboarding-handbook.pdf |
| `sf-19` | test | single_fact | In which months do performance reviews take place? | Performance reviews take place twice a year, in April and October. | onboarding-handbook.pdf |
| `sf-20` | test | single_fact | What must the deployment health endpoint return, and how quickly? | The health endpoint is /healthz and must return HTTP 200 within 2 seconds. | runbook-deploy.md |
| `tb-13` | test | table_lookup | What is the per-trip limit for taxis and ride-hailing, and who approves it? | Taxi and ride-hailing is limited to EUR 60 per trip, approved by the line manager. | expense-policy.pdf |
| `tb-14` | dev | table_lookup | What is the expense limit for conference tickets and which approver is needed? | Conference tickets are limited to EUR 1,200 per event and need Director approval. | expense-policy.pdf |
| `tb-15` | test | table_lookup | Who is eligible for the training budget benefit and what is the annual cap? | The training budget (BEN-LRN-01) is for all employees with an annual cap of 1000. | benefits-table.csv |
| `tb-16` | test | table_lookup | Who manages the Rotterdam depot and what is its phone number? | Dirk van Leeuwen manages the Rotterdam depot; the phone number is +31 10 555 0110. | office-locations.csv |
| `tb-17` | validation | table_lookup | What does the medium carton cost on the 2026 price list and how many come in a pack? | The medium carton (40x30x30 cm) is a pack of 25 priced at 11,20. | price-list-2026.csv |
| `tb-18` | dev | table_lookup | How long are proof-of-delivery signatures kept under the data processing addendum? | Proof-of-delivery signatures of consignees are kept for 36 months. | data-processing-addendum.docx |
| `tb-19` | dev | table_lookup | Which API endpoint prints a shipment label, and how many labels can one request print? | POST /shipments/{id}/labels prints a label, up to 250 labels per request. | api-spec.md |
| `tb-20` | validation | table_lookup | Which sub-processor handles transactional email and in which country? | MailRelay SAS handles transactional email, in France. | data-processing-addendum.docx |
| `ua-13` | test | unanswerable | Does Northwind pay relocation assistance when an employee moves to another site? | The documents do not mention relocation assistance. | (none: unanswerable) |
| `ua-14` | test | unanswerable | How large is the annual performance bonus for depot managers? | The documents do not mention bonuses. | (none: unanswerable) |
| `ua-15` | validation | unanswerable | What is the dress code on the warehouse floor? | The documents do not state a dress code or uniform policy (only high-visibility vests in loading areas are mentioned, not a dress code). | (none: unanswerable) |
| `ua-16` | dev | unanswerable | What customs duty rate applies to pallets shipped from the UK to the Netherlands? | The documents do not give customs duty or tariff rates. | (none: unanswerable) |
| `ua-17` | test | unanswerable | Is Northwind planning to acquire another logistics company? | The documents contain no information about acquisitions or mergers. | (none: unanswerable) |
| `ua-18` | dev | unanswerable | Is smoking allowed on the depot grounds? | The documents contain no smoking policy. | (none: unanswerable) |
| `ua-19` | dev | unanswerable | How many weeks of maternity leave do Austin employees get? | The documents do not state maternity leave for the Austin office. | (none: unanswerable) |
| `ua-20` | validation | unanswerable | Who is the CEO of Northwind Logistics? | The documents mention the CEO's role but never name the CEO (the chair of the Board is named, but not as CEO). | (none: unanswerable) |
| `vc-13` | validation | version_conflict | Did sick pay change between the 2024 and 2026 leave policies? | No. Both policies give 100% of base pay for the first 26 weeks of illness. | hr-leave-policy-2024.pdf, hr-leave-policy-2026.pdf |
| `vc-14` | validation | version_conflict | Under the 2026 leave policy, how many consecutive days of leave can a line manager approve? | Line managers may approve up to 15 consecutive days; longer requests need Director approval. | hr-leave-policy-2026.pdf |
| `vc-15` | dev | version_conflict | How many public holidays did the 2024 leave policy list per year? | The 2024 policy lists 11 public holidays per year. | hr-leave-policy-2024.pdf |
| `vc-16` | test | version_conflict | When does the 2026 leave policy take effect and which policy does it replace? | It is effective from 1 January 2026 and supersedes Leave Policy 2024 (Version 3.1). | hr-leave-policy-2026.pdf |
| `vc-17` | test | version_conflict | What parental leave did employees get under the 2024 policy? | Parental leave was 12 weeks at 80% of base pay. | hr-leave-policy-2024.pdf |
| `vc-18` | test | version_conflict | Do the expense policy and the travel guidelines agree on the private-car mileage rate? | Yes. Both give EUR 0.23 per km. | expense-policy.pdf, travel-guidelines.docx |
| `vc-19` | dev | version_conflict | How many days of unpaid leave does the current (2026) policy allow per calendar year? | Up to 60 days in total per calendar year. | hr-leave-policy-2026.pdf |
| `vc-20` | dev | version_conflict | How did the leave carry-over rules change between the 2024 and 2026 policies? | 2024: up to 5 unused days by 31 March. 2026: up to 8 unused days by 30 June. | hr-leave-policy-2024.pdf, hr-leave-policy-2026.pdf |
