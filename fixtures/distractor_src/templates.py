"""Sentence and paragraph templates. Slots: {role} {Role} {role2} {thing} {Thing} {thing2} {sys} {sys2} {site} {site2}
{wd} {cd} {mins} {hrs} {mo} {pct} {eur} {eurbig} {date} {iso} {clock} {ver} {code} {person} {n} {nbig} {kg} {kmh}
{month} {quarter} {supplier} {sub} {topic} {law}. No literal digits belong in a template: numbers come from slots so
that the generator can keep clear of the golden answers."""

PURPOSE = [
    "This document sets out how Northwind Logistics handles {thing} activity under the heading {topic}, so that every site works to the same rules.",
    "The purpose of this {kind} is to give {role}s and their teams one reference for {thing} and {thing2}, replacing the local practices that grew up at individual sites.",
    "It explains who is responsible for {thing}, which records are kept in {sys}, and what happens when a case does not fit the normal path.",
    "This {kind} was drafted by the {role} together with the {role2} and reviewed by the working group on {topic} in {month}.",
    "Its aim is to make {thing} predictable: the same request should receive the same answer at {site} as it does at {site2}.",
    "Earlier guidance on {thing} was spread over several notices; this {kind} consolidates it and withdraws the older notices from the date shown in the revision history.",
]
SCOPE = [
    "It applies to all permanent and temporary staff at {site}, {site2} and every other Northwind Logistics site that uses {sys}.",
    "The rules cover {thing}, {thing2} and any related request raised through {sys}. They do not cover matters handled under a separate customer agreement.",
    "Teams working from {site} follow the same rules as {site2}; a site may add stricter local steps but may not relax these ones.",
    "Visitors and staff of partner companies are covered only for the sections that mention them explicitly.",
    "Where a national rule is stricter than this {kind}, the national rule takes precedence at that site.",
    "Out of scope: purchases of {thing} for private use, and anything that is governed by a signed schedule to a supplier framework.",
]
RULES = [
    "The {role} must record every {thing} in {sys} before the end of the working day on which it occurs.",
    "A {thing} that remains open for more than {wd} is escalated to the {role}.",
    "Requests for a {thing} are submitted at least {wd} ahead of the planned date; shorter notice needs the agreement of the {role}.",
    "Each {thing} receives a reference of the form {code} and is kept for {mo} after it is closed.",
    "Where a {thing} involves more than {eur}, the {role} must confirm the figure in {sys} before the work starts.",
    "The {role} reviews the open {thing} list every week and removes entries that are older than {wd} unless a reason is noted.",
    "Staff may raise a {thing2} on behalf of a colleague, but the name of the person who asked for it must be entered in {sys}.",
    "A change to a {thing} takes effect on the first Monday after the {role} has approved it in {sys}.",
    "Photographs or scans supporting a {thing} should be attached to the record in {sys} within {hrs} of the event.",
    "No {thing} may be closed while a linked {thing2} is still open.",
    "If a {thing} is rejected, the {role} gives the reason in writing and the requester has {wd} to reply.",
    "Every {thing} is checked by a second person when the value exceeds {eur}; the second person must not be the one who raised it.",
    "The target response time for a {thing} is {mins} during opening hours and {hrs} outside them.",
    "At {site} the {role} publishes the {thing} schedule no later than {cd} before it starts.",
    "Records of {thing} are reviewed in a sample of {pct} each quarter by the {role2}.",
    "The {role} is the only person who may waive the second check on a {thing}, and every waiver is logged with its reason.",
    "A {thing2} that is found to be incomplete is returned to the sender with a note that lists the missing items.",
    "Where {sys} is unavailable, the {role} keeps a paper log and enters the items within {wd} of the system returning.",
    "Figures for {thing} are reported in whole euros and rounded to the nearest {eur} in management summaries.",
    "The {role} must tell the {role2} about any {thing} that is likely to exceed its planned cost by more than {pct}.",
    "New starters receive a short briefing on {thing} within {cd} of joining the team.",
    "A {thing} may be reassigned to another team only with the written agreement of both team leaders.",
    "Version {ver} of the {thing} template replaces all earlier versions; older copies must be withdrawn from the shared folder.",
    "Where two sites disagree on how to handle a {thing}, the {role} decides and records the outcome in {sys}.",
    "Items of {thing} that cannot be matched to a {thing2} after {cd} are listed in the monthly exception report.",
    "The {role} signs off the monthly summary of {thing} no later than the {n}th working day of the following month.",
    "All amounts are shown excluding VAT unless the document says otherwise.",
    "A reminder is sent automatically by {sys} when a {thing} has not been updated for {wd}.",
    "Only staff who completed the {thing} briefing may approve entries in {sys} for {site}.",
    "Temporary changes to the {thing} process during peak weeks are announced by the {role} at least {cd} beforehand.",
]
EXCEPTIONS = [
    "Exception: a {thing} raised during a planned shutdown may be closed by the {role} without the second check, provided the reason is logged.",
    "Exception: where the value is below {eur}, the {role} may approve the {thing} without involving the {role2}.",
    "This rule does not apply to {thing2} that were created before version {ver} came into use.",
    "However, the {role} may extend the period to {wd} when a supplier delay is documented in {sys}.",
    "Notwithstanding the above, urgent cases may be dealt with by phone, provided the entry in {sys} is completed within {hrs}.",
    "Exception: trial runs of a new {thing} at {site} follow the pilot rules attached to the project plan instead of this section.",
    "The limit above is doubled for {thing2} that are handled as part of a planned project and approved by the {role}.",
    "An exception applies to {thing} of a recurring nature: one approval covers up to {n} repeat cases within {mo}.",
]
DEF_TERMS = [
    ("{Thing}", "a record held in {sys} that describes one {thing} handled at a single site."),
    ("{Thing2}", "the document or entry that supports a {thing} and is kept with it."),
    ("Owner", "the {role} named in {sys} as accountable for the record."),
    ("Working day", "a day on which the site concerned is open for normal business, excluding weekends."),
    ("Escalation", "passing a case to the next role in the table in this document because the time limit has passed."),
    ("Reference", "the identifier of the form {code} that {sys} assigns when a record is created."),
    ("Closure", "the point at which the {role} marks the record as complete and no further action is expected."),
    ("Second check", "a review by a person other than the one who raised or prepared the entry."),
    ("Peak period", "the weeks that the {role} announces as high volume for {topic} at a site."),
    ("Exception report", "the monthly list of {thing} cases that did not follow the standard path."),
    ("Delegate", "a named colleague who acts for the {role} during absence, as recorded in {sys}."),
    ("Site", "an operating location of Northwind Logistics with its own manager, such as {site} or {site2}."),
]
RESPONSIBILITIES = [
    "The {Role} owns this {kind}, answers questions about {thing} and reports on it to the {role2} every quarter.",
    "Team leaders make sure that their staff know how to raise a {thing} and where to find {sys}.",
    "The {Role2} checks the monthly figures for {thing} and signs off the summary.",
    "Every employee who handles {thing2} is responsible for entering it correctly the first time.",
    "The service desk supports users of {sys} and logs faults against the {role}.",
    "Site managers at {site} and {site2} make time available for the quarterly review of {thing}.",
]
RECORDS = [
    "Records of {thing} are kept in {sys} for {mo} after closure and then archived by the {role}.",
    "Paper copies, where they exist, are scanned and attached to the entry; the paper is destroyed after {wd}.",
    "Access to the records is limited to the {role}, the {role2} and auditors with a written request.",
    "Monthly extracts from {sys} are stored in the shared reporting folder and kept for {mo}.",
    "Corrections to a closed entry are made as a new entry that refers to the original {code}; the original is never overwritten.",
]
ESCALATION_NOTES = [
    "Escalation is by phone first and in writing second; the entry in {sys} must show the time of the call.",
    "If the named person is absent, the delegate recorded in {sys} acts in their place.",
    "A case that reaches the top level is reviewed at the next meeting of the working group.",
]
NARRATIVE = [
    "During {quarter} the {thing} backlog at {site} fell from {nbig} items to {n} items, helped by the weekly review introduced in {month}.",
    "{Site} handled {nbig} cases of {thing} in the period, which is {pct} more than in the previous quarter.",
    "The {role} reports that the average time to close a {thing} was {wd}, against a target of {wd2}.",
    "Spending on {thing} was {eurbig}, which is {pct} below budget; the saving comes mainly from the new arrangement with {supplier}.",
    "At {site2} the introduction of {sys} reduced manual re-entry of {thing2} and freed up about {hrs} per week.",
    "Two sites missed the target for {thing}: {site} by {n} cases and {site2} by {nbig}. Both have an action plan.",
    "Staff feedback on the new {thing2} process was positive, although several teams asked for a shorter form.",
    "The {role} met {supplier} on {date} to agree how {thing} will be handled in the next quarter.",
    "An internal review in {month} found that {pct} of entries in {sys} lacked a {thing2}; reminders were sent to the teams concerned.",
    "Training on {thing} was completed by {nbig} employees, and a further group of {n} is booked for {month}.",
    "The pilot of the revised {thing} route at {site} ran for {cd} and produced no complaints from users.",
    "Forecast demand for {thing} in the next quarter is {pct} higher, driven by seasonal volumes at {site2}.",
    "Work on the {thing2} template was delayed by {wd} because the review by the {role} took longer than planned.",
    "The cost per {thing} was {eur} in {quarter}, down from {eur2} a year earlier.",
]
RISKS = [
    "Risk: the {role} is the only person trained on {thing}. Mitigation: a second person from {site2} will be trained by {date}.",
    "Risk: {sys} may not handle the volume expected in peak weeks. Mitigation: a load test is planned for {month}.",
    "Risk: late delivery from {supplier} could delay the {thing} schedule. Mitigation: an alternative supplier has been identified.",
    "Risk: unclear ownership of {thing2} between two teams. Mitigation: the steering group will confirm the owner on {date}.",
    "Risk: the budget for {thing} is exhausted before year end. Mitigation: monthly tracking by the {role2}.",
]
STEPS = [
    "Open {sys} and select the {thing} queue for {site}.",
    "Check that the {thing2} is attached and that the reference looks like {code}.",
    "Read the notes added by the previous shift and confirm that nothing is waiting for a reply.",
    "Enter the value of the {thing} in the amount field and choose the matching category from the list.",
    "If the value is above {eur}, tick the box that requests a second check and name the {role2}.",
    "Save the record. {Sys} shows a green banner and assigns the reference of the form {code}.",
    "Send the link to the {role} and wait for the confirmation, which normally arrives within {mins}.",
    "Print the {thing2} sheet, sign it and hand it to the {role} at the end of the shift.",
    "Move the {thing} to the next stage by pressing Advance; the status changes to In progress.",
    "Photograph the {thing2} and upload the image; images larger than {n} MB are rejected.",
    "Refresh the list after {mins} and confirm that the record no longer appears under Open.",
    "Tell the {role2} by phone if the {thing} is still open after {hrs}.",
    "Log out of {sys} when you leave the workstation so that the next user starts with a clean session.",
    "Compare the totals on screen with the paper sheet; if they differ by more than {eur}, stop and call the {role}.",
]
VERIFY = [
    "Confirm that the {thing} now appears in the weekly list with the status Closed.",
    "Check that {sys} has sent the summary e-mail to the {role}.",
    "Ask the requester whether the {thing2} reached them and note the answer in the record.",
    "Run the daily report and confirm that the count of open {thing} items has fallen by one.",
]
ROLLBACK = [
    "If the change causes errors in {sys}, restore the previous template from the shared archive and tell the {role}.",
    "Reopen the affected {thing} records and add a note explaining why they were reopened.",
    "Inform the {role2} within {hrs} so that the weekly summary can be corrected.",
]
FAQ = [
    ("How do I request a {thing}?", "Open {sys}, choose New request, pick {thing} and attach the {thing2}. The {role} confirms within {wd}."),
    ("Who can approve a {thing} above {eur}?", "The {role2} approves it, after the {role} has checked the supporting {thing2}."),
    ("What should I do if {sys} is not available?", "Keep a paper note of the {thing}, tell the {role}, and enter it in {sys} within {wd} of the system coming back."),
    ("Can I change a {thing} after it has been closed?", "No. Raise a new entry that refers to the original {code}; the {role} will link the two."),
    ("How long are records of {thing} kept?", "They stay in {sys} for {mo} after closure and are then archived by the {role}."),
    ("Where do I find the latest {thing2} template?", "It is on the {topic} page of the intranet. Version {ver} is the current one; delete older copies."),
    ("Who do I contact about a problem with {thing}?", "Call the service desk first; if it is not solved within {hrs}, ask the {role} to take it over."),
    ("Is there a deadline for {thing}?", "Yes. Entries must be made within {wd} of the event; later entries need a note from the {role}."),
    ("Does the process differ between {site} and {site2}?", "The steps are the same. Only the opening hours and the name of the local {role} differ."),
    ("What does the status Waiting mean on a {thing}?", "It means that the {role} has asked for more information and the clock is paused until the requester replies."),
    ("Can a colleague raise a {thing2} for me?", "Yes, provided your name is entered as the requester so that the reply reaches you."),
    ("What happens if I miss the cut-off?", "The {thing} moves to the next working day. In urgent cases call the {role}, who can decide to take it today."),
]
MINUTES_DISCUSS = [
    "{person} presented the proposal for {thing} and explained that the current process takes about {wd} longer than at {site2}.",
    "The group reviewed the open actions on {thing}. {person} reported that {n} of the {nbig} items are now closed.",
    "{person} raised a concern that {sys} does not yet support {thing2}; the {role} agreed to ask the supplier for a date.",
    "Members discussed whether the limit of {eur} for {thing} is still appropriate and asked the {role} to prepare options.",
    "The {role} gave an update on the agreement with {supplier}, which is due for renewal in {month}.",
    "A short discussion followed on how to report {thing2} to the sites; no objections were raised.",
    "{person} noted that complaints about {thing} fell in {quarter} and suggested keeping the weekly review in place.",
    "The chair reminded the group that the budget for {thing} must be committed by {date}.",
]
DECISIONS = [
    "It was agreed to introduce the revised {thing} template at all sites from {date}.",
    "The proposal to raise the limit for {thing} to {eur} was approved by the members present.",
    "The decision on {thing2} was deferred to the next meeting, pending a cost estimate from the {role}.",
    "The group agreed that the {role} will own the {thing} list and report on it each month.",
    "It was decided to run a pilot of the new {thing} route at {site} for {cd} before any wider change.",
    "The group declined the request for an extra {thing} budget and asked for a revised plan.",
]
ACTION_TEXT = [
    "Prepare options paper on {thing}", "Publish revised {thing} template", "Confirm owner for {thing2} list", "Update {sys} access list",
    "Collect quotes for {thing}", "Brief site managers on {thing}", "Review limit for {thing}", "Test {thing2} report in {sys}",
    "Agree schedule with {supplier}", "Draft notice about {thing}", "Close open {thing} items at {site}", "Report back on {thing2} pilot",
]
CHANGELOG = [
    "Added an export of {thing} data to {sys}.", "Fixed a bug that showed the wrong status for a {thing} after it was reopened ({inv}).",
    "Improved the search on the {thing2} screen; a record can now be found by its reference, for example {code}.", "Changed the default sort order of the {thing} list to newest first.",
    "Removed the old {thing2} field from the form; existing values are kept in the history.", "Fixed a timeout when loading more than {nbig} {thing} rows ({inv}).",
    "Added a reminder e-mail for {thing} items that are older than {wd}.", "The {thing} report now shows totals per site.",
    "Fixed the spelling of several labels on the {thing2} page.", "Added a column for the owner to the {thing} list.",
    "Security fix: session tokens now expire after {mins} of inactivity.", "Fixed a rounding error in the monthly summary of {thing} costs ({inv}).",
    "Added support for attaching up to {n} files to a {thing2}.", "Fixed duplicate entries when a {thing} was saved twice in quick succession ({inv}).",
    "Raised the upload limit for {thing2} files.", "Performance: the {thing} dashboard now loads in about {n} seconds.",
]
CAUSES = [
    "a scheduled job that did not release its lock after a timeout", "a mistyped value in a shared configuration file",
    "a certificate for the {sys} reporting service that was not renewed in time", "an upstream database running out of connections during the morning peak",
    "a firmware update that reset the network settings of several devices", "a change in the {thing} template that was published before it was tested",
    "a disk on the file server filling up because old exports were not removed", "a staff rota gap that left no one trained on {thing} at {site}",
    "a duplicate record created by two users saving the same {thing} at the same time", "a delayed delivery from {supplier} that was not reported to the {role}",
]
TIMELINE = [
    "First report received by the service desk.", "{Role} acknowledged the report and opened a ticket.", "Initial diagnosis: the problem was limited to {site}.",
    "Workaround communicated to the teams affected.", "Fix applied in {sys} and tested by the {role2}.", "Service confirmed normal; monitoring continued overnight.",
    "Customer-facing teams informed that the issue was resolved.", "Post-mortem meeting held with the {role} and the {role2}.",
]
LESSONS = [
    "Reminders for expiring items should be sent {wd} before the date, not on the day.", "Changes to the {thing} template need a test run at one site first.",
    "The {role} should have a named deputy for {thing} at every site.", "The monitoring check for {sys} must also alert the {role2}, not only the service desk.",
    "Runbooks for {thing} should be reviewed twice a year.", "Handovers between shifts must record open {thing2} items explicitly.",
]
CLAUSES = [
    "{Supplier} shall provide the services described in Schedule {n} to Northwind Logistics at {site} and the other sites named in that Schedule.",
    "The monthly fee is {eurbig}, invoiced on the {n}th working day of each month and payable within {cd} of the invoice date.",
    "{Supplier} guarantees a monthly availability of {avail} for the service, measured by {sys}, excluding maintenance announced at least {cd} in advance.",
    "A priority 1 request must receive a first response within {mins}, and a priority 2 request within {hrs}, in both cases during the support hours in Schedule {n}.",
    "Either party may terminate this Agreement for material breach if the breach is not remedied within {cd} of written notice.",
    "Northwind Logistics may terminate for convenience on {cd} written notice after the first {mo} of the term.",
    "Late payments accrue interest at {rate} per month on the overdue amount.",
    "Neither party's total liability in any contract year shall exceed {eurbig}, except for fraud or wilful misconduct.",
    "{Supplier} shall keep liability insurance of at least {eurbig} for the duration of the Agreement and provide a certificate on request.",
    "Notwithstanding clause {c1}, an invoice disputed in good faith within {wd} of receipt does not accrue late interest.",
    "The limit in clause {c1} does not apply to liability for breach of clause {c2} (Confidentiality), which is capped at {eurbig}.",
    "This Agreement is governed by {law} and the courts of {city} have exclusive jurisdiction.",
    "Each party shall keep the other's confidential information secret for {yrs} after the end of the Agreement.",
    "{Supplier} shall not subcontract any part of the services without prior written consent, which shall not be unreasonably withheld.",
    "Changes to this Agreement are valid only if made in writing and signed by both parties; a change request is answered within {wd}.",
    "Service credits apply to the next invoice and are capped at {pct} of the monthly fee.",
    "The initial term is {mo} from the effective date and renews for successive periods of {mo2} unless either party gives {cd} notice.",
]
ENDPOINT_DESCR = [
    "Fetch a {thing} by id", "Create a {thing}", "List {thing} entries for a site", "Update fields of a {thing}", "Cancel a {thing}",
    "Attach a {thing2} to a {thing}", "Export {thing} entries as a file", "Close a {thing}", "Search {thing2} by reference",
]
API_NOTES = [
    "All requests must be made over HTTPS and carry a bearer token in the Authorization header.",
    "Timestamps are returned in ISO 8601 format in UTC.",
    "Monetary amounts are integers in cents together with a three-letter currency code.",
    "Unknown fields in a request body are ignored; missing required fields return a validation error.",
    "List endpoints use cursor pagination; pass the value of next_cursor from the previous response to continue.",
    "Requests that change data accept an Idempotency-Key header so that a repeated request does not create a second record.",
    "The sandbox environment is reset every Sunday night; data created there is not kept.",
    "Breaking changes are announced at least {cd} before they take effect and are listed in the changelog.",
    "Responses include an X-Request-Id header; quote it when you contact the {role}.",
]
ERR_MESSAGES = [
    "Missing or malformed access token", "Token expired", "Token does not grant access to this site", "Resource not found", "Request body failed validation",
    "Resource is locked by another request", "Too many requests in the current window", "Upstream service did not answer in time", "Unsupported media type",
    "Conflict with the current state of the resource", "Resource has been archived",
]
GLOSSARY_DEFS = [
    "a {thing} that has been confirmed by the {role} and entered in {sys}",
    "the period after a {thing} is closed during which a linked {thing2} may still be corrected",
    "a list kept by the {role} of {thing} items that need attention this week",
    "the person who raises a {thing} in {sys} on behalf of a team",
    "a threshold of {eur} above which a {thing} needs a second check",
    "the {thing2} that is sent to the {role} at the end of each month",
    "a standing agreement with {supplier} covering recurring {thing} purchases",
    "the status shown in {sys} while the {role} waits for more information",
    "an identifier of the form {code} assigned to every {thing}",
    "the weekly meeting at which open {thing2} items are reviewed",
]
CHECKLIST_ITEMS = [
    "Is every {thing} entered in {sys} on the day it occurs?", "Does each {thing2} carry a reference of the form {code}?",
    "Is the second check recorded for entries above {eur}?", "Are open {thing} items older than {wd} escalated to the {role}?",
    "Is access to {sys} limited to staff who completed the briefing?", "Are records kept for {mo} and then archived?",
    "Does the {role} sign off the monthly summary on time?", "Were exceptions logged with a reason by the {role}?",
    "Are paper copies of the {thing2} destroyed after {wd}?", "Is the delegate for the {role} named in {sys}?",
    "Was the last review of the {thing} procedure within {mo}?", "Do staff at {site} know where to find the {thing} instructions?",
]
SERVICE_SCOPE = [
    "{Supplier} will deliver {thing} services to {site} and {site2}, including planning, execution and a monthly report in {sys}.",
    "The service desk of {Supplier} is open on weekdays from {clock} to {clock2}.",
    "Reports are delivered by the {n}th working day of each month and discussed at the monthly service review.",
]
INTRO_PARAS = [
    "Northwind Logistics runs operating sites in Europe and the United States. Questions about {topic} come up at most of them, which is why this {kind} is kept short and practical.",
    "The {role} welcomes comments on this {kind}. Send them through {sys} or raise them at the next meeting of the working group on {topic}.",
    "Printed copies of this {kind} are not controlled. The current version is always the one on the intranet page for {topic}.",
]
REVISION_NOTES = [
    "First issue", "Added section on {thing}", "Clarified second check", "Changed limit for {thing2}", "Updated names of roles",
    "Aligned with new version of {sys}", "Removed obsolete exceptions", "Annual review, no changes of substance",
]
