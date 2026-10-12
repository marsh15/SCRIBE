#!/usr/bin/env python3
"""Build the Scribe 2.0 DISTRACTOR corpus: ~300 Northwind Logistics documents that make retrieval hard.

Run:  uv run --no-project --with reportlab --with python-docx --with pillow --with pypdfium2 \
          python fixtures/build_distractors.py

Output (git-ignored): fixtures/distractors/*  plus the committed fixtures/distractors.manifest.json.
Word banks and sentence templates live in fixtures/distractor_src/. Rendering reuses the helpers of
fixtures/build_corpus.py (imported, not copied). Output is deterministic: every document draws from its own
RNG seeded by (SEED, file name, attempt), PDFs use reportlab invariant mode, DOCX zips use fixed timestamps.

Hard constraints (see docs/scribe2/benchmarks/distractors.md). A document is regenerated with the next attempt
seed when it (a) contains a gold evidence quote, (b) contains a must_include string of an answerable case,
(c) contains a gold identifier or an absent-topic keyword, or (d) matches a "soft-avoid" phrase that would
make an unnamed golden question ambiguous. Names never collide with gold documents. The same detection code
(scribe_eval.distractor_check) is used here and in the checker.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO / "intelligence" / "src"))

import build_corpus as bc  # noqa: E402  (reuse the gold corpus rendering helpers)
from distractor_src import banks as B  # noqa: E402
from distractor_src import templates as TP  # noqa: E402
from scribe_eval import distractor_check as dc  # noqa: E402

SEED = 20260410
GENERATOR_VERSION = "1.0.0"
OUT = ROOT / "distractors"
MANIFEST = ROOT / "distractors.manifest.json"
DATASET = REPO / "benchmarks" / "datasets" / "golden-v1"
GOLD_CORPUS = ROOT / "corpus"
FOOTER = "Northwind Logistics - Confidential - Internal use only"
MAX_ATTEMPTS = 40

GOLD = dc.Gold.load(DATASET, GOLD_CORPUS)

# Phrases that would give an UNNAMED golden question a plausible competing answer (see the ps-*, nr-* cases).
# Not part of the hard rules; the generator simply stays away from them. Lower-case regex.
SOFT_AVOID = re.compile(
    r"on-time|headcount|revenue|payload|reimburs|repaid|last day|final day|leaver|employment|deploy|release window|"
    r"pushing|webhook|callback|retried|retries|page size|per page|by default|degrees|damag|broken|parcels per|evacuat|"
    r"fire drill|rack load|pallet level|expense|alcohol|hotel|proof of delivery|delivery receipt|signed receipt|"
    r"cross-dock|harbour|northexpense|northtravel|sev1|sev2|benelux|standard delivery|business secret|hr portal|"
    r"hr and leave|non-compete|garden leave|probation|annual leave|parental|post-incident|ransom|medical certificate|"
    r"\bsick|public holiday|founders|control room|assembly point|assembly-point|yard speed|forklifts? must|"
    r"liability is limited|early termination|work computer|access badge|tier a|helios|dutch law|helpdesk ext|base url|rate limit|requests per minute|per api key",
    re.I)

SITES = B.CORE_SITES
MONTHS = B.MONTHS


def seed_for(name: str, attempt: int) -> int:
    h = hashlib.sha256(f"{SEED}:{name}:{attempt}".encode()).digest()
    return int.from_bytes(h[:8], "big")


# ----------------------------------------------------------------------------
# Value pools (pre-filtered so literal gold answers never appear)
# ----------------------------------------------------------------------------
def _pool(items):
    return [x for x in items if GOLD.phrase_ok(x) and not SOFT_AVOID.search(x)]


POOLS = {
    "wd": _pool([f"{n} working days" for n in range(2, 31)]),
    "cd": _pool([f"{n} calendar days" for n in (7, 10, 15, 20, 21, 28, 30, 45, 60, 75, 90, 120)] + [f"{n} days" for n in (4, 6, 8, 9, 11, 12, 20, 25, 40, 45, 75, 100)]),
    "mins": _pool([f"{n} minutes" for n in (5, 10, 12, 15, 20, 25, 35, 40, 45, 50, 55, 60, 75, 90, 120)]),
    "hrs": _pool([f"{n} hours" for n in (2, 3, 4, 6, 8, 12, 16, 18, 36, 72)]),
    "mo": _pool([f"{n} months" for n in (2, 4, 5, 8, 9, 12, 15, 18, 24, 30, 48, 60)]),
    "yrs": _pool([f"{n} years" for n in (2, 4, 5, 7, 10)]),
    "pct": _pool([f"{n}%" for n in (2, 3, 4, 6, 7, 8, 9, 12, 15, 18, 20, 22, 30, 35, 40, 45, 50, 60)]),
    "avail": _pool(["99.5%", "99.7%", "99.2%", "98.5%", "99.8%", "99.95%", "98.9%", "99.3%"]),
    "rate": _pool(["1.2%", "2%", "0.8%", "1.1%", "0.9%", "1.8%"]),
    "kmh": _pool([f"{n} km/h" for n in (5, 6, 7, 9, 10, 11, 13, 14, 16, 18, 20)]),
    "kg": _pool([f"{n:,} kg" for n in (250, 300, 450, 600, 750, 800, 900, 1100, 1300, 1450, 1600, 2000)]),
    "eur": _pool([f"EUR {v:,}" for v in (20, 30, 35, 40, 60, 80, 120, 150, 200, 250, 300, 350, 400, 500, 600, 750, 800, 1000, 1200, 1250, 1800, 2500, 3000, 4000, 5000, 7500, 9000)]),
    "eurbig": _pool([f"EUR {v:,}" for v in range(6500, 400001, 3250)]),
    "ver": _pool([f"{a}.{b}" for a in range(1, 8) for b in range(0, 10)]),
    "clock": _pool([f"{h:02d}:{m:02d}" for h in range(6, 21) for m in (0, 15, 30, 45)]),
    "clause": _pool([f"{a}.{b}" for a in range(2, 12) for b in range(1, 7) if f"{a}.{b}" not in ("4.2", "5.1", "7.1")]),
}
for k, v in POOLS.items():
    assert len(v) >= 5, k


KIND_WORD = {"faq": "guide", "minutes": "record", "contract": "agreement", "register": "register", "digest": "bulletin", "api": "specification",
             "pricelist": "price list", "training": "catalogue", "postmortem": "review", "changelog": "changelog"}
_AN = re.compile(r"\b([Aa]) (?=[aeioAEIO])")


class Gen:
    """Seeded text generator for one document attempt."""

    def __init__(self, name: str, attempt: int, topic: dict, kind: str = "policy"):
        self.r = random.Random(seed_for(name, attempt))
        self.t = topic
        self.kind = KIND_WORD.get(kind, kind)
        r = self.r
        self.things = r.sample(topic["things"], k=min(11, len(topic["things"])))
        self.roles = r.sample(topic["roles"], k=min(4, len(topic["roles"])))
        self.systems = r.sample(topic["systems"], k=2)
        sup = B.SUPPLIERS[r.randrange(len(B.SUPPLIERS))]
        self.supplier, self.city = sup
        self.law = r.choice(B.LAWS)
        self.sites = r.sample(SITES, k=4)

    # ---- primitives ------------------------------------------------------------------
    def pick(self, seq):
        return seq[self.r.randrange(len(seq))]

    def person(self) -> str:
        return f"{self.pick(B.FIRST)} {self.pick(B.LAST)}"

    def date(self, lo_year=2025, hi_year=2026) -> str:
        for _ in range(60):
            y = self.r.randint(lo_year, hi_year)
            m = self.r.randint(1, 12 if y < hi_year else 9)
            s = f"{self.r.randint(1, 28)} {MONTHS[m - 1]} {y}"
            if GOLD.phrase_ok(s):
                return s
        raise RuntimeError("date")

    def iso(self, lo_year=2025, hi_year=2026) -> str:
        for _ in range(60):
            y = self.r.randint(lo_year, hi_year)
            m = self.r.randint(1, 12 if y < hi_year else 9)
            s = f"{y}-{m:02d}-{self.r.randint(1, 28):02d}"
            if GOLD.phrase_ok(s):
                return s
        raise RuntimeError("iso")

    def code(self, prefix: str | None = None, lo=1000, hi=9899) -> str:
        pfx = prefix or self.t["prefix"]
        for _ in range(60):
            s = f"{pfx}-{self.r.randint(lo, hi)}"
            if s.lower() not in GOLD.identifiers:
                return s
        raise RuntimeError("code")

    def inv(self) -> str:
        return f"INV-{self.pick([n for n in range(2038, 2080) if f'inv-{n}' not in GOLD.identifiers])}"

    def err(self, fam: str) -> str:
        for _ in range(60):
            s = f"ERR_{fam}_{self.pick([402, 404, 405, 406, 408, 409, 410, 412, 413, 415, 418, 421, 423, 424, 426, 428, 430, 431])}"
            if s.lower() not in GOLD.identifiers:
                return s
        raise RuntimeError("err")

    # ---- slot filling ----------------------------------------------------------------
    def slot(self, name: str) -> str:
        if name == "Supplier":
            return self.supplier
        cap = name[0].isupper()
        v = self._slot(name.lower() if cap else name)
        return v[:1].upper() + v[1:] if cap else v

    def _slot(self, n: str) -> str:
        t = self.t
        if n in POOLS:
            return self.pick(POOLS[n])
        if n == "wd2":
            return self.pick(POOLS["wd"])
        if n == "mo2":
            return self.pick(POOLS["mo"])
        if n == "eur2":
            return self.pick(POOLS["eur"])
        if n == "thing":
            return self.pick(self.things)
        if n == "thing2":
            return self.pick(self.things)
        if n in ("role", "role2"):
            return self.pick(self.roles)
        if n == "sys":
            return self.systems[0]
        if n == "sys2":
            return self.systems[1]
        if n in ("site", "site2"):
            return self.pick(self.sites)
        if n == "code":
            return self.code()
        if n == "code_pfx":
            return t["prefix"] + "-"
        if n == "inv":
            return self.inv()
        if n == "person":
            return self.person()
        if n == "n":
            return str(self.r.randint(2, 9))
        if n == "nbig":
            return str(self.r.randint(12, 480))
        if n == "month":
            return self.pick(MONTHS)
        if n == "quarter":
            return self.pick(B.QUARTERS)
        if n == "supplier":
            return self.supplier
        if n == "sub":
            return self.pick(t["subs"])
        if n == "topic":
            return t["name"].lower()
        if n == "law":
            return self.law
        if n == "city":
            return self.city
        if n == "c1":
            return self.pick(POOLS["clause"])
        if n == "c2":
            return str(self.r.randint(9, 14))
        if n == "date":
            return self.date()
        if n == "iso":
            return self.iso()
        if n == "kind":
            return self.kind
        if n == "clock2":
            return self.pick(POOLS["clock"])
        raise KeyError(n)

    def fill(self, tpl: str) -> str:
        out = re.sub(r"\{(\w+)\}", lambda m: self.slot(m.group(1)), tpl)
        return _AN.sub(lambda m: m.group(1) + "n ", out)

    def one(self, pool) -> str:
        return self.fill(self.pick(pool))

    def sents(self, pool, k: int) -> str:
        chosen = self.r.sample(pool, k=min(k, len(pool)))
        out = []
        for i, s in enumerate(chosen):
            f = self.fill(s)
            if i > 0 and self.r.random() < 0.22 and f[:4] in ("The ", "A ", "Eac", "Eve", "All"):
                f = self.pick(["In practice, ", "As a rule, ", "Where possible, ", "In addition, ", "For clarity, "]) + f[0].lower() + f[1:]
            out.append(f)
        return " ".join(out)


# ----------------------------------------------------------------------------
# Document model helpers (blocks as in build_corpus.parse)
# ----------------------------------------------------------------------------
def H1(t): return ("h1", t)
def H2(t): return ("h2", t)
def H3(t): return ("h3", t)
def P(t): return ("p", t)
def LI(t): return ("li", t)
def OL(t): return ("ol", t)
def CODE(lines): return ("code", lines)
def TABLE(rows): return ("table", rows)


def blocks_len(blocks) -> int:
    n = 0
    for k, p in blocks:
        if k in ("table",):
            n += sum(len(c) + 3 for r in p for c in r)
        elif k == "code":
            n += sum(len(x) + 1 for x in p)
        elif p:
            n += len(p) + 1
    return n


def doc_text(doc) -> str:
    """Plain text the way an extractor sees it (header and footer included)."""
    out = [doc["header"], FOOTER]
    for k, p in doc["blocks"]:
        if k == "table":
            out += [" | ".join(r) for r in p]
        elif k == "code":
            out += list(p)
        elif p:
            out.append(p)
    return "\n".join(out)


def hm(h: int, m: int) -> str:
    """HH:MM that is not a golden answer (09:00, 10:00, 10:15, 15:00)."""
    s = f"{h:02d}:{m:02d}"
    while not GOLD.phrase_ok(s):
        m = (m + 1) % 60
        s = f"{h:02d}:{m:02d}"
    return s


def doc_header(g: Gen, title: str) -> str:
    return f"Northwind Logistics | {title} | Version {g.pick(POOLS['ver'])}"


def meta_line(g: Gen) -> str:
    return (f"Owner: {g.person()}, {g.pick(g.roles)} | Effective: {g.date()} | Next review: {g.date(2026, 2027)} | "
            f"Applies to: {g.pick(g.sites)}, {g.pick(g.sites)} and all other sites using {g.systems[0]}")


# ----------------------------------------------------------------------------
# Section builders
# ----------------------------------------------------------------------------
def sec_purpose(g: Gen, num: int):
    return [H2(f"{num}. Purpose"), P(g.sents(TP.PURPOSE, 3)), P(g.one(TP.INTRO_PARAS))]


def sec_scope(g: Gen, num: int):
    return [H2(f"{num}. Scope"), P(g.sents(TP.SCOPE, 3))]


def sec_definitions(g: Gen, num: int, table=True):
    items = g.r.sample(TP.DEF_TERMS, k=7)
    rows = [["Term", "Meaning"]] + [[g.fill(a), g.fill(b)] for a, b in items]
    if table:
        return [H2(f"{num}. Definitions"), P("The following terms have a fixed meaning in this document."), TABLE(rows)]
    return [H2(f"{num}. Definitions")] + [LI(f"{a}: {b}") for a, b in rows[1:]]


def sec_roles(g: Gen, num: int):
    return [H2(f"{num}. Roles and responsibilities")] + [LI(g.fill(s)) for s in g.r.sample(TP.RESPONSIBILITIES, 5)]


def sec_rules(g: Gen, num: int, title: str | None = None, items=6):
    out = [H2(f"{num}. {title or g.pick(g.t['subs'])}")]
    for j in range(1, items + 1):
        out.append(OL(f"{num}.{j} " + g.sents(TP.RULES, g.r.randint(2, 3))))
        if g.r.random() < 0.25:
            out.append(P(g.fill(g.pick(TP.EXCEPTIONS))))
    return out


def sec_exceptions(g: Gen, num: int):
    return [H2(f"{num}. Exceptions")] + [OL(f"{num}.{i + 1} " + g.fill(s)) for i, s in enumerate(g.r.sample(TP.EXCEPTIONS, 4))]


def sec_escalation(g: Gen, num: int):
    rows = [["Level", "Role", "Response time", "Contact"]]
    for lv in range(1, 5):
        rows.append([f"Level {lv}", g.pick(g.roles).capitalize(), g.pick(POOLS["mins"] if lv < 3 else POOLS["hrs"]),
                     f"ext. {g.r.randint(5000, 5999)}"])
    return [H2(f"{num}. Escalation"), P(g.sents(TP.ESCALATION_NOTES, 2)), TABLE(rows)]


def sec_thresholds(g: Gen, num: int):
    rows = [["Item", "Limit", "Approver"]]
    for th in g.r.sample(g.things, 5):
        rows.append([th.capitalize(), g.pick(POOLS["eur"]), g.pick(g.roles).capitalize()])
    return [H2(f"{num}. Limits and approvals"), P("Amounts are per case unless stated otherwise."), TABLE(rows)]


def sec_records(g: Gen, num: int):
    return [H2(f"{num}. Records and retention"), P(g.sents(TP.RECORDS, 3))]


def sec_revision(g: Gen, num: int):
    rows = [["Version", "Date", "Author", "Change"]]
    v = g.r.randint(1, 3)
    for i in range(g.r.randint(3, 6)):
        rows.append([f"{v}.{i}", g.iso(2023, 2026), g.person(), g.fill(g.pick(TP.REVISION_NOTES))])
    return [H2(f"{num}. Revision history"), TABLE(rows)]


def sec_narrative(g: Gen, title: str, k=4):
    return [H3(title), P(g.sents(TP.NARRATIVE, k))]


def sec_metrics(g: Gen, title: str, cols=("Cases", "Closed", "Avg days", "Cost (EUR k)")):
    rows = [["Site", *cols]]
    for s in g.sites + [g.pick(B.EXTRA_SITES).replace("the ", "").capitalize()]:
        rows.append([s, str(g.r.randint(12, 480)), str(g.r.randint(8, 470)), f"{g.r.uniform(1, 19):.1f}", f"{g.r.uniform(2, 380):.1f}"])
    return [H3(title), TABLE(rows)]


def sec_actions(g: Gen):
    rows = [["Action", "Owner", "Due"]]
    for _ in range(g.r.randint(5, 10)):
        rows.append([g.fill(g.pick(TP.ACTION_TEXT)), g.person(), g.date(2026, 2026)])
    return [H3("Actions"), TABLE(rows)]


def sec_checklist(g: Gen, num: int, items: int):
    rows = [["Ref", "Check", "Evidence", "Result"]]
    for i in range(1, items + 1):
        rows.append([f"{g.t["prefix"]}-C{num:02d}-{i:02d}", g.fill(g.pick(TP.CHECKLIST_ITEMS))[:70], g.pick(["record in system", "sample of 10", "walkthrough", "interview", "document review"]), g.pick(["Pass", "Pass", "Pass", "Partial", "Fail", "N/A"])])
    return [H2(f"{num}. {g.pick(g.t['subs'])}"), TABLE(rows)]


# ----------------------------------------------------------------------------
# Document kinds
# ----------------------------------------------------------------------------
KIND_NOUN = {"policy": ["policy", "procedure", "guideline", "standard", "code of practice"]}
TITLE_FORMS = {
    "policy": ["{n} Policy", "{n} Procedure", "{n} Guidelines", "{n} Standard"],
    "contract": ["Service Level Agreement: {n}", "Framework Agreement: {n}", "Statement of Work: {n}"],
    "api": ["{n} API Specification", "{n} Service API Reference"],
    "runbook": ["Runbook: {n}", "Operating Procedure: {n}", "Playbook: {n}"],
    "minutes": ["Minutes of the {n} Working Group", "Minutes: {n} Steering Meeting", "{n} Committee Minutes"],
    "changelog": ["{n} Release Notes", "{n} Changelog"],
    "postmortem": ["Post-mortem: {n} disruption", "Incident Review: {n}"],
    "report": ["{n} Quarterly Report", "{n} Status Report", "{n} Review"],
    "faq": ["{n}: Frequently Asked Questions", "{n} How-to Guide"],
    "glossary": ["{n} Glossary"],
    "register": ["{n} Register", "{n} Log Extract"],
    "checklist": ["{n} Audit Checklist", "{n} Review Checklist"],
    "training": ["{n} Course Catalogue", "{n} Learning Programme"],
    "pricelist": ["{n} Price List", "{n} Catalogue Prices"],
    "digest": ["{n} Bulletin Digest", "{n} Notices"],
}


def title_for(g: Gen, kind: str) -> str:
    return g.pick(TITLE_FORMS[kind]).format(n=g.t["name"])


def build_policy(g: Gen, target: int):
    g.kind = g.pick(KIND_NOUN["policy"])
    title = title_for(g, "policy")
    b = [H1(title), P(meta_line(g))]
    b += sec_purpose(g, 1) + sec_scope(g, 2) + sec_definitions(g, 3) + sec_roles(g, 4)
    num = 5
    subs = g.r.sample(g.t["subs"], k=len(g.t["subs"]))
    while blocks_len(b) < target * 0.8:
        b += sec_rules(g, num, subs[(num - 5) % len(subs)] if num - 5 < len(subs) else None, items=g.r.randint(5, 8))
        if g.r.random() < 0.3:
            b += sec_thresholds(g, num + 1)
            num += 1
        num += 1
    b += sec_exceptions(g, num) + sec_escalation(g, num + 1) + sec_records(g, num + 2)
    if g.r.random() < 0.6:
        b.append(P("This document is the property of Northwind Logistics and may not be copied outside the company. Printed copies are not controlled."))
    b += sec_revision(g, num + 3)
    return title, b


def build_contract(g: Gen, target: int):
    title = f"Service Level Agreement: {g.supplier.split()[0]} {g.t['name']}" if g.r.random() < 0.6 else title_for(g, "contract")
    vnd = g.code("VND", 8801, 8899)
    b = [H1(title), P(f"Contract number {vnd}. Effective {g.date()}. This document is the property of Northwind Logistics and may not be copied outside the company."),
         P(f"This Agreement is made between Northwind Logistics (the Customer) and {g.supplier}, {g.city} (the Supplier).")]
    clause = 1
    names = ["Definitions", "Services", "Service levels", "Charges and payment", "Support", "Liability and insurance", "Term and termination",
             "Confidentiality", "Data protection", "Subcontracting", "Change control", "General"]
    for nm in names:
        b.append(H2(f"{clause}. {nm}"))
        for j in range(1, g.r.randint(4, 7)):
            pool = TP.CLAUSES if g.r.random() < 0.7 else TP.RULES
            txt = g.fill(g.pick(pool))
            b.append(OL(f"{clause}.{j} {txt}"))
        if nm == "Service levels":
            lo = g.pick(["98.50%", "98.80%", "99.20%", "97.90%"])
            rows = [["Monthly availability", "Service credit"], [f"{g.pick(POOLS['avail'])} or higher", "0%"], [f"{lo} to {g.pick(['99.19%', '99.39%', '99.44%'])}", g.pick(["3%", "4%", "7%"])],
                    [f"{g.pick(['97.00%', '96.50%', '97.50%'])} to {g.pick(['98.49%', '98.79%'])}", g.pick(["12%", "15%", "18%"])], [f"Below {g.pick(['97.00%', '96.50%'])}", g.pick(["30%", "35%", "40%"])]]
            b += [P("Service credits are calculated on the monthly fee as follows."), TABLE(rows)]
        if nm == "Charges and payment":
            rows = [["Service", "Unit", "Charge (EUR)"]]
            for th in g.r.sample(g.things, 4):
                rows.append([th.capitalize(), g.pick(["per month", "per case", "per site", "per day"]), f"{g.r.uniform(8, 940):.2f}"])
            b += [TABLE(rows)]
        clause += 1
    sched = 1
    while blocks_len(b) < target * 0.85:
        b.append(H2(f"Schedule {sched}: {g.pick(g.t['subs'])}"))
        for j in range(1, g.r.randint(5, 9)):
            b.append(OL(f"{sched}.{j} " + g.sents(TP.SERVICE_SCOPE + TP.RULES, 2)))
        if sched % 2 == 0:
            b += sec_thresholds(g, 90 + sched)[1:]
        sched += 1
    b += [H2("Signatures"), P(f"Signed for Northwind Logistics by {g.person()} and for {g.supplier} by {g.person()} on {g.date()}.")]
    return title, b


def build_api(g: Gen, target: int):
    slug = g.t["slug"].split("-")[0]
    title = title_for(g, "api")
    fam = g.t["prefix"][:4]
    b = [H1(title), P(f"Gateway root: https://internal-api.northwind-logistics.example/{slug}/v1 | Owner: {g.person()} | Document version {g.pick(POOLS['ver'])}"),
         H2("Overview"), P(g.sents(TP.PURPOSE, 2)), H2("Conventions")]
    b += [LI(g.fill(s)) for s in g.r.sample(TP.API_NOTES, 6)]
    b += [H2("Throttling"), P(f"Clients are throttled above {g.r.randint(40, 480)} calls per minute. Excess calls receive the error {g.err('RATE')} with a Retry-After header.")]
    eps = []
    th = g.r.sample(g.things, 7)
    for t_ in th:
        slug_t = t_.replace(" ", "-")
        eps.append(("GET", f"/{slug_t}s/{{id}}", g.fill(f"Fetch a {t_} by id")))
        eps.append((g.pick(["POST", "PUT", "PATCH", "DELETE"]), f"/{slug_t}s", g.fill(g.pick(TP.ENDPOINT_DESCR))))
    b += [H2("Endpoints"), TABLE([["Method", "Path", "Description"]] + [list(e) for e in eps])]
    for m, path, desc in eps[: g.r.randint(5, 9)]:
        b += [H3(f"{m} {path}"), P(desc + ". " + g.sents(TP.NARRATIVE[:2] + TP.RULES, 2)),
              CODE([f"curl -H 'Authorization: Bearer <token>' https://internal-api.northwind-logistics.example/{slug}/v1{path.replace('{id}', '1042')}",
                    "", "{", f'  "id": "{g.code()}",', f'  "status": "{g.pick(["open", "closed", "waiting"])}",', f'  "site": "{g.pick(g.sites).lower()}",',
                    f'  "updated_at": "{g.iso()}T{g.pick(POOLS["clock"])}:00Z"', "}"])]
        if blocks_len(b) > target * 0.8:
            break
    rows = [["Code", "HTTP", "Meaning"]]
    seen = set()
    for _ in range(g.r.randint(7, 11)):
        f_ = g.pick(["AUTH", "AUTH", fam, fam, "SHIP", "VAL", "RATE", "DOCK"])
        c = g.err(f_)
        if c in seen:
            continue
        seen.add(c)
        rows.append([c, c.rsplit("_", 1)[1] if int(c.rsplit("_", 1)[1]) < 500 else "400", g.pick(TP.ERR_MESSAGES)])
    b += [H2("Error codes"), P("Errors are returned as JSON with a code, a message and the request id."), TABLE(rows)]
    b += [H2("Versioning and support"), P(g.fill(g.pick(TP.API_NOTES[-2:]))), P(g.sents(TP.RECORDS, 2))]
    return title, b


def build_runbook(g: Gen, target: int):
    title = title_for(g, "runbook").replace(g.t["name"], g.pick(g.things).title())
    cmdbase = "nwctl " + g.t["slug"].split("-")[0]
    b = [H1(title), P(meta_line(g)), H2("Overview"), P(g.sents(TP.PURPOSE, 2)), H2("Prerequisites")]
    b += [LI(g.fill(s)) for s in ["Access to {sys} with the {role} profile.", "A completed {thing2} form for the case.", "The mobile number of the {role2} on call.", "Network access to the {sys2} console."]]
    n = 1
    while blocks_len(b) < target * 0.8:
        b += [H2(f"Procedure {n}: {g.pick(g.t['subs'])}")]
        for i in range(1, g.r.randint(5, 9)):
            b.append(OL(f"{i}. " + g.one(TP.STEPS)))
            if g.r.random() < 0.3:
                b.append(CODE([f"{cmdbase} {g.pick(['status', 'list', 'drain', 'resume', 'export', 'check'])} --site {g.pick(g.sites).lower()} --older-than {g.pick(['6h', '12h', '2d', '4d', '1w'])}"]))
        b += [H3("Verification")] + [LI(g.fill(s)) for s in g.r.sample(TP.VERIFY, 3)]
        n += 1
    b += [H2("Rollback")] + [LI(g.fill(s)) for s in TP.ROLLBACK]
    rows = [["Role", "Name", "Phone"]] + [[r_.capitalize(), g.person(), f"+31 10 555 {g.r.randint(200, 399)}"] for r_ in g.roles]
    b += [H2("Contacts"), TABLE(rows)]
    return title, b


def build_minutes(g: Gen, target: int):
    cm = g.pick(["Working Group", "Steering Group", "Committee", "Review Panel"])
    title = f"Minutes of the {g.t['name']} {cm}, {g.date(2025, 2026)}"
    pres = [g.person() for _ in range(g.r.randint(5, 8))]
    b = [H1(title), P(f"The {g.t['name']} {cm} met on {g.date()} at {g.pick(g.sites)}; present were {', '.join(pres[:-1])} and {pres[-1]}. Apologies were received from {g.person()}."),
         H2("1. Minutes of the previous meeting"), P(g.fill("The minutes of the previous meeting were approved without changes. Open actions on {thing} were reviewed and {n} were closed."))]
    i = 2
    while blocks_len(b) < target * 0.8:
        b += [H2(f"{i}. {g.pick(g.t['subs'])}"), P(g.sents(TP.MINUTES_DISCUSS, 3)), P("Decision: " + g.one(TP.DECISIONS))]
        if g.r.random() < 0.3:
            b.append(P(g.sents(TP.NARRATIVE, 2)))
        i += 1
    b += [H2(f"{i}. Actions")] + sec_actions(g)[1:]
    b += [H2(f"{i + 1}. Any other business"), P(g.fill("{person} asked for the {thing2} list to be shared before the next meeting. No other items were raised.")),
          P(f"The next meeting is on {g.date(2026, 2026)} at {g.pick(g.sites)}.")]
    return title, b


def build_changelog(g: Gen, target: int):
    title = title_for(g, "changelog").replace(g.t["name"], g.systems[0])
    b = [H1(title), P("All notable changes to the service are listed here, newest first.")]
    entries = []
    maj, mnr, pat = g.r.randint(1, 3), g.r.randint(0, 6), 0
    year, month = 2024, g.r.randint(1, 12)
    size = 0
    while size < target * 0.85 and len(entries) < 120:
        ver = f"v{maj}.{mnr}.{pat}"
        if GOLD.phrase_ok(ver):
            lines = [g.one(TP.CHANGELOG) for _ in range(g.r.randint(2, 6))]
            day = g.r.randint(1, 28)
            entries.append((ver, f"{year}-{month:02d}-{day:02d}", lines))
            size += sum(len(x) for x in lines) + 30
        if g.r.random() < 0.65:
            pat += 1
        else:
            mnr += 1
            pat = 0
            if g.r.random() < 0.12:
                maj += 1
                mnr = 0
        month += g.r.choice([0, 1, 1, 2])
        if month > 12:
            month -= 12
            year += 1
        if year > 2026 or (year == 2026 and month > 9):
            year, month = 2026, 9
    for ver, d, lines in reversed(entries):
        b += [H2(f"{ver} ({d})")] + [LI(x) for x in lines]
    return title, b


def build_postmortem(g: Gen, target: int):
    title = title_for(g, "postmortem")
    incid = f"INC-{g.pick([2025, 2026])}-{g.r.randint(118, 240)}"
    b = [H1(title), P(f"Reference {incid} | Date of incident: {g.date()} | Author: {g.person()} | Status: closed"),
         H2("Summary"), P(f"On {g.date()} the {g.pick(g.things)} service at {g.pick(g.sites)} was affected for {g.pick(POOLS['mins'])}. The cause was {g.one(TP.CAUSES)}."),
         H2("Impact"), P(g.sents(TP.NARRATIVE, 3)), H2("Timeline")]
    rows = [["Time (UTC)", "Event"]]
    h, m = g.r.randint(5, 18), g.r.randint(0, 59)
    for ev in g.r.sample(TP.TIMELINE, 7):
        rows.append([hm(h % 24, m), g.fill(ev)])
        m += g.r.randint(4, 40)
        h += m // 60
        m %= 60
    b += [TABLE(rows), H2("Root cause"), P(f"The root cause was {g.one(TP.CAUSES)}."), P(g.sents(TP.NARRATIVE + TP.RISKS, 3)), H2("Contributing factors")]
    b += [LI(g.fill(c)[:1].upper() + g.fill(c)[1:] + ".") for c in g.r.sample(TP.CAUSES, 4)]
    b += [H2("What went well")] + [LI(g.fill(s)) for s in g.r.sample(TP.VERIFY + TP.ROLLBACK, 3)]
    b += [H2("Lessons learned")] + [LI(g.fill(s)) for s in g.r.sample(TP.LESSONS, 4)]
    rows = [["Ticket", "Action", "Owner", "Due"]]
    for _ in range(g.r.randint(4, 7)):
        rows.append([g.inv(), g.fill(g.pick(TP.ACTION_TEXT)), g.person(), g.iso(2026, 2026)])
    b += [H2("Follow-up actions"), TABLE(rows)]
    while blocks_len(b) < target * 0.85:
        b += [H2(f"Appendix: {g.pick(g.t['subs'])}")]
        mrows = [["Time (UTC)", "Queue depth", "Errors", "Notes"]]
        for _ in range(g.r.randint(12, 30)):
            mrows.append([hm(g.r.randint(0, 23), g.r.randint(0, 59)), str(g.r.randint(0, 900)), str(g.r.randint(0, 60)), g.pick(["normal", "elevated", "alert sent", "recovering", "stable"])])
        b += [TABLE(mrows), P(g.sents(TP.NARRATIVE, 3))]
    return title, b


def build_report(g: Gen, target: int):
    q = g.pick(B.QUARTERS)
    title = f"{g.t['name']} Report {q}"
    b = [H1(title), P(meta_line(g)), H2("1. Executive summary"), P(g.sents(TP.NARRATIVE, 4)), H2("2. Key figures")]
    b += sec_metrics(g, "Cases by site")
    b += sec_metrics(g, "Cost by site", ("Budget (EUR k)", "Actual (EUR k)", "Variance (%)", "Open items"))
    k = 3
    while blocks_len(b) < target * 0.8:
        b += [H2(f"{k}. {g.pick(g.t['subs'])}")]
        for s in g.r.sample(g.sites, k=3):
            b += [H3(s), P(g.sents(TP.NARRATIVE, g.r.randint(3, 5)))]
        if g.r.random() < 0.5:
            b += sec_metrics(g, f"{g.pick(g.things).capitalize()} by site")
        k += 1
    b += [H2(f"{k}. Risks")] + [LI(g.fill(s)) for s in g.r.sample(TP.RISKS, 4)]
    b += [H2(f"{k + 1}. Outlook"), P(g.sents(TP.NARRATIVE + TP.MINUTES_DISCUSS, 3))] + sec_actions(g)
    return title, b


def build_faq(g: Gen, target: int):
    title = title_for(g, "faq")
    b = [H1(title), P(g.sents(TP.INTRO_PARAS, 2))]
    i = 1
    pool = list(TP.FAQ)
    while blocks_len(b) < target * 0.8:
        b.append(H2(f"{i}. {g.pick(g.t['subs'])}"))
        for q, a in g.r.sample(pool, k=g.r.randint(4, 7)):
            b += [H3(g.fill(q)), P(g.fill(a) + " " + g.sents(TP.RULES, g.r.randint(1, 2)))]
        i += 1
    return title, b


def build_glossary(g: Gen, target: int):
    title = title_for(g, "glossary")
    b = [H1(title), P("Terms are listed alphabetically. Use the term exactly as written when you raise a request.")]
    terms: dict[str, str] = {}
    words = ["queue", "slot", "window", "batch", "cycle", "trigger", "sheet", "ledger", "pass", "lane", "token", "round", "pool", "tag", "feed", "stage", "tier", "grade", "wave", "check"]
    for th in g.things:
        for w in g.r.sample(words, k=4):
            terms[f"{th} {w}".capitalize()] = g.fill(g.pick(TP.GLOSSARY_DEFS)) + "."
    for k in sorted(terms):
        b.append(P(f"{k}: {terms[k]}"))
        if blocks_len(b) > target * 0.9:
            break
    return title, b


def build_register(g: Gen, target: int):
    title = title_for(g, "register")
    spec = g.pick(g.t["csv"])[1]
    cols = [c for c, _ in spec]
    b = [H1(title), P(g.sents(TP.PURPOSE, 2)), P("Entries are extracted from " + g.systems[0] + " on " + g.date() + ". Statuses are as at the extraction date.")]
    part = 1
    while blocks_len(b) < target * 0.85:
        rows = [cols] + [[csv_cell(g, s, i + part * 100, True) for _, s in spec] for i in range(g.r.randint(25, 45))]
        b += [H2(f"Part {part}: {g.pick(g.t['subs'])}"), TABLE(rows), P(g.sents(TP.NARRATIVE, 2))]
        part += 1
    return title, b


def build_checklist(g: Gen, target: int):
    title = title_for(g, "checklist")
    b = [H1(title), P(meta_line(g)), P("Mark each line Pass, Partial, Fail or N/A and attach the evidence reference. Ratings are agreed with the process owner at the closing meeting.")]
    k = 1
    while blocks_len(b) < target * 0.8:
        b += sec_checklist(g, k, g.r.randint(8, 14))
        b.append(P(g.sents(TP.RULES, 2)))
        k += 1
    b += sec_actions(g)
    return title, b


def build_training(g: Gen, target: int):
    title = title_for(g, "training")
    b = [H1(title), P(meta_line(g)), P(g.sents(TP.PURPOSE, 2))]
    n = 1
    while blocks_len(b) < target * 0.85:
        th = g.pick(g.things).title()
        b += [H2(f"Course {g.t['prefix']}-{100 + n}: {th}"),
              P(f"Format: {g.pick(['classroom', 'e-learning', 'on the job', 'workshop'])} | Duration: {g.pick(['2 hours', '3 hours', '6 hours', '1 day', '4 days', '45 minutes'])} | Trainer: {g.person()} | Languages: {g.pick(['English', 'English and German', 'English and Dutch', 'English and French'])}"),
              P(g.sents(TP.PURPOSE + TP.NARRATIVE, 3)), P(g.sents(TP.RULES, 2))]
        n += 1
    rows = [["Course", "Site", "Date", "Seats"]] + [[f"{g.t['prefix']}-{100 + i}", g.pick(g.sites), g.date(2026, 2026), str(g.r.randint(6, 24))] for i in range(1, 9)]
    b += [H2("Next sessions"), TABLE(rows)]
    return title, b


def build_pricelist(g: Gen, target: int):
    title = title_for(g, "pricelist")
    b = [H1(title), P(f"Valid from {g.date(2026, 2026)} unless a line says otherwise. Prices exclude VAT. Supplier: {g.supplier}."), P(g.sents(TP.RECORDS + TP.RULES, 3))]
    groups = ["Packaging", "Labels and tape", "Handling aids", "Office and print", "Safety items", "Cleaning"]
    items = ["carton", "pack", "roll", "sheet", "box", "tape", "pen", "wrap", "film", "strap", "label", "bag", "glove", "pad", "tray", "bin", "clip", "seal"]
    mods = ["small", "medium", "large", "extra strong", "clear", "recycled", "reinforced", "self-adhesive", "waterproof", "standard"]
    for grp in groups:
        rows = [["Code", "Description", "Unit", "Price (EUR)", "Min qty"]]
        for _ in range(g.r.randint(18, 40)):
            rows.append([f"NWC-{g.r.randint(1000, 9899)}", f"{g.pick(mods).capitalize()} {g.pick(items)}", g.pick(["each", "pack of 10", "pack of 50", "roll", "box of 100"]), f"{g.r.uniform(0.6, 98):.2f}", str(g.r.randint(1, 60))])
        b += [H2(grp), TABLE(rows)]
        if blocks_len(b) > target:
            break
    return title, b


def build_digest(g: Gen, target: int):
    title = title_for(g, "digest")
    b = [H1(title), P("Short notices for site teams. Questions go to the named contact in each notice.")]
    i = 1
    while blocks_len(b) < target * 0.85:
        b += [H2(f"Notice {i}: {g.pick(g.things).capitalize()}"), P(f"Issued {g.date()} by {g.person()}, {g.pick(g.roles)}. " + g.sents(TP.NARRATIVE + TP.RULES, 3)), P(g.sents(TP.EXCEPTIONS + TP.RECORDS, 2))]
        i += 1
    return title, b


BUILDERS = {
    "policy": build_policy, "contract": build_contract, "api": build_api, "runbook": build_runbook, "minutes": build_minutes,
    "changelog": build_changelog, "postmortem": build_postmortem, "report": build_report, "faq": build_faq, "glossary": build_glossary,
    "register": build_register, "checklist": build_checklist, "training": build_training, "pricelist": build_pricelist, "digest": build_digest,
}

PLAN = {
    "pdf": {"policy": 10, "contract": 8, "report": 10, "minutes": 4, "postmortem": 4, "runbook": 4, "checklist": 6, "training": 6, "register": 4, "pricelist": 4},
    "docx": {"policy": 10, "contract": 8, "minutes": 10, "report": 6, "faq": 4, "runbook": 4, "checklist": 6, "training": 4, "postmortem": 3, "register": 3, "pricelist": 2},
    "md": {"api": 14, "runbook": 10, "policy": 8, "faq": 10, "changelog": 4, "postmortem": 6, "minutes": 6, "glossary": 3, "digest": 5, "checklist": 2, "pricelist": 2},
    "txt": {"changelog": 10, "postmortem": 8, "glossary": 8, "policy": 8, "minutes": 8, "runbook": 6, "faq": 5, "digest": 6, "register": 3, "report": 3},
}
assert {f: sum(v.values()) for f, v in PLAN.items()} == {"pdf": 60, "docx": 60, "md": 70, "txt": 65}
CSV_COUNT = 45

# target body size per format (characters of block text): drawn per document
def target_chars(r: random.Random, fmt: str, kind: str) -> int:
    if kind == "changelog":
        return r.randint(14000, 34000)
    if kind == "glossary":
        return r.randint(8000, 20000)
    if fmt == "pdf":
        return r.choice([r.randint(9000, 17000), r.randint(19000, 30000), r.randint(30000, 46000), r.randint(19000, 38000)])
    return r.randint(14000, 36000)


# ----------------------------------------------------------------------------
# Renderers (reuse build_corpus helpers)
# ----------------------------------------------------------------------------
def render_md(path: Path, doc):
    out: list[str] = []
    for k, p in doc["blocks"]:
        if k in ("h1", "h2", "h3", "h4"):
            out += ["#" * int(k[1]) + " " + p, ""]
        elif k == "p":
            out += [p, ""]
        elif k == "li":
            out.append("- " + p)
        elif k == "ol":
            out += [p, ""]
        elif k == "code":
            out += ["```"] + list(p) + ["```", ""]
        elif k == "table":
            out += bc.table_lines(p) + [""]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    # list items are separated from the following paragraph by a blank line
    text = re.sub(r"(- [^\n]*)\n(?=[^\n-])", r"\1\n\n", text)
    path.write_text(text.strip() + "\n", encoding="utf-8")


@contextlib.contextmanager
def footer_without_legal_suffix():
    """The gold renderer hard-codes 'Northwind Logistics B.V.' in the page footer; a must_include string of a golden
    case ('Northwind Logistics B.V.') then would sit in every distractor PDF. Swap it while drawing."""
    from reportlab.pdfgen import canvas

    orig = canvas.Canvas.drawString

    def draw(self, x, y, text, *a, **k):
        return orig(self, x, y, text.replace("Northwind Logistics B.V.", "Northwind Logistics"), *a, **k)

    canvas.Canvas.drawString = draw
    try:
        yield
    finally:
        canvas.Canvas.drawString = orig


def render_pdf(path: Path, doc):
    with footer_without_legal_suffix():
        bc.render_pdf(path, doc)


def render_docx(path: Path, doc):
    from docx import Document

    bc.render_docx(path, doc)
    d = Document(str(path))
    d.sections[0].footer.paragraphs[0].text = FOOTER
    d.save(str(path))
    bc.normalize_zip(path)


def render_txt(path: Path, doc):
    path.write_text(bc.txt_text(doc), encoding="utf-8")


# ----------------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------------
VEH_IDS = [f"NW-{k}-{n:04d}" for k in "TVBR" for n in range(100, 900)
           if f"nw-{k.lower()}-{n:04d}" not in GOLD.identifiers]
NOTE_TPL = ["{thing} checked", "awaiting {thing2}", "see {code}", "re-test due", "no action needed", "escalated to {role}",
            "{thing} updated in {sys}", "second check done", "waiting for {supplier}", "reviewed by {person}", "moved to next week"]


def csv_cell(g: Gen, spec: str, i: int, plain=False) -> str:
    kind, *args = spec.split(":")
    r = g.r
    if kind == "id":
        pfx = args[0]
        if pfx == "NWX":
            return g.pick(VEH_IDS)
        return g.code(pfx, 1000, 9899) if pfx != "VND" else g.code("VND", 8760, 8899)
    if kind == "site":
        return g.pick(SITES)
    if kind == "date":
        return g.iso(2025, 2026)
    if kind == "person":
        return g.person()
    if kind == "thing":
        return g.pick(g.t["things"])
    if kind == "supplier":
        return g.pick(B.SUPPLIERS)[0]
    if kind == "customer":
        return g.pick(B.CUSTOMERS)
    if kind == "int":
        return str(r.randint(int(args[0]), int(args[1])))
    if kind == "eur":
        return f"{r.uniform(float(args[0]), float(args[1])):.2f}"
    if kind == "dec":
        return f"{r.uniform(float(args[0]), float(args[1])):.{args[2]}f}"
    if kind == "choice":
        return g.pick(args[0].split("/"))
    if kind == "clock":
        return g.pick(POOLS["clock"])
    if kind == "pct":
        return str(r.randint(1, 99))
    if kind == "note":
        return g.one(NOTE_TPL)
    raise KeyError(spec)


def build_csv(name: str, topic: dict, idx: int, attempt: int) -> bytes:
    g = Gen(name, attempt, topic, "register")
    r = g.r
    _, spec = topic["csv"][idx % len(topic["csv"])]
    flavour = idx % 15
    delim = "," if flavour not in (3, 7, 11, 13) else {3: ";", 7: "\t", 11: "|", 13: ";"}[flavour]
    bom = flavour in (3, 8, 12)
    no_header = flavour in (5, 9) or idx in (21, 33, 41)
    ragged = idx in (17, 29, 44)
    nrows = r.choice([r.randint(40, 90), r.randint(90, 200), r.randint(150, 400), r.randint(200, 400), r.randint(80, 330)])
    rows = []
    for i in range(nrows):
        rows.append([csv_cell(g, s, i) for _, s in spec])
    # first column that looks like an id or date: sort for plausibility
    rows.sort(key=lambda x: (x[0] if spec[0][1].startswith("id") else x[min(2, len(x) - 1)]))
    if delim == ";":  # decimal comma in semicolon files
        rows = [[c.replace(".", ",") if re.fullmatch(r"\d+\.\d+", c) else c for c in row] for row in rows]
    if ragged:
        for row in rows:
            if r.random() < 0.18:
                row.append(g.one(NOTE_TPL))
            elif r.random() < 0.1:
                del row[-1]
    out = []
    if not no_header:
        out.append([c for c, _ in spec])
    out += rows
    return bc.rows_to_csv(out, delim, bom=bom)


# ----------------------------------------------------------------------------
# Assemble
# ----------------------------------------------------------------------------
def make_doc(name: str, fmt: str, kind: str, topic: dict, attempt: int):
    g = Gen(name, attempt, topic, kind)
    title, blocks = BUILDERS[kind](g, target_chars(g.r, fmt, kind))
    return {"header": doc_header(g, title), "blocks": blocks, "facts": []}


def violations_of(text: str) -> list[str]:
    v = [f"{r}:{d}" for r, d in GOLD.check_text(text)]
    m = SOFT_AVOID.search(text)
    if m:
        v.append(f"soft:{m.group(0)}")
    return v


def lexicon_selfcheck():
    """Fail fast when a word bank or template itself contains a forbidden word."""
    blobs = []
    for t in B.TOPICS:
        blobs += [t["name"], *t["things"], *t["roles"], *t["systems"], *t["subs"]]
    for name in dir(TP):
        v = getattr(TP, name)
        if isinstance(v, list):
            for x in v:
                blobs += [x] if isinstance(x, str) else [y for y in x if isinstance(y, str)]
    blobs += B.FIRST + B.LAST + [s for s, _ in B.SUPPLIERS] + B.CUSTOMERS
    bad = []
    for s in blobs:
        txt = re.sub(r"\{\w+\}", "", s)
        low = txt.lower()
        for label, rx in GOLD.absent:
            if rx.search(low):
                bad.append((s, label))
        if SOFT_AVOID.search(txt):
            bad.append((s, "soft:" + SOFT_AVOID.search(txt).group(0)))
        if not GOLD.phrase_ok(txt):
            bad.append((s, "gold phrase"))
    if bad:
        for s, why in bad[:30]:
            print("LEXICON ERROR:", why, "|", s, file=sys.stderr)
        raise SystemExit(1)


def build_all(out_dir: Path = OUT):
    lexicon_selfcheck()
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.iterdir():
        if f.is_file():
            f.unlink()
    topics = B.TOPICS
    plan: list[tuple[str, str, str, dict, int]] = []  # name, fmt, kind, topic, idx
    order = random.Random(SEED)
    for fi, fmt in enumerate(("pdf", "docx", "md", "txt")):
        kinds = [k for k, n in PLAN[fmt].items() for _ in range(n)]
        order.shuffle(kinds)
        tp = list(range(len(topics)))
        for i, kind in enumerate(kinds):
            if i % len(topics) == 0:
                order.shuffle(tp)
            t = topics[tp[(i + fi) % len(topics)]]
            plan.append((f"{t['slug']}-{kind}-{i + 1:03d}.{fmt}", fmt, kind, t, i))
    for i in range(CSV_COUNT):
        t = topics[(i * 7 + 3) % len(topics)]
        plan.append((f"{t['slug']}-{t['csv'][i % len(t['csv'])][0]}-{i + 1:03d}.csv", "csv", "csv", t, i))

    names = [p[0] for p in plan]
    assert len(set(names)) == len(names) == 300, len(set(names))
    for n in names:
        assert not GOLD.check_name(n), n

    stats = {"regenerated": 0}
    for name, fmt, kind, topic, idx in plan:
        path = out_dir / name
        for attempt in range(MAX_ATTEMPTS):
            if fmt == "csv":
                data = build_csv(name, topic, idx, attempt)
                text = data.decode("utf-8-sig")
                v = violations_of(text)
                if v:
                    stats["regenerated"] += 1
                    continue
                path.write_bytes(data)
                break
            doc = make_doc(name, fmt, kind, topic, attempt)
            text = doc_text(doc)
            v = violations_of(text)
            if v:
                stats["regenerated"] += 1
                print(f"  regenerate {name} attempt {attempt}: {v[0]}", file=sys.stderr)
                continue
            {"pdf": render_pdf, "docx": render_docx, "md": render_md, "txt": render_txt}[fmt](path, doc)
            break
        else:
            raise SystemExit(f"could not generate a clean {name}")
    return names, stats


def write_manifest(out_dir: Path = OUT):
    import platform

    files = []
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            files.append({"name": p.name, "size": p.stat().st_size, "sha256": dc.sha256_file(p), "format": p.suffix.lstrip(".")})
    env = {"python": platform.python_version()}
    for mod in ("reportlab", "docx"):
        try:
            m = __import__(mod)
            env["python-docx" if mod == "docx" else mod] = getattr(m, "Version", None) or getattr(m, "__version__", "unknown")
        except Exception:
            pass
    counts: dict[str, int] = {}
    for f in files:
        counts[f["format"]] = counts.get(f["format"], 0) + 1
    manifest = {
        "generator": "fixtures/build_distractors.py", "generator_version": GENERATOR_VERSION, "seed": SEED,
        "dataset": "benchmarks/datasets/golden-v1", "counts": counts, "total_documents": len(files),
        "total_bytes": sum(f["size"] for f in files), "environment": env,
        "note": "PDF and DOCX bytes depend on the reportlab / python-docx versions in 'environment'; text formats do not.",
        "files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main():
    names, stats = build_all()
    m = write_manifest()
    print(f"built {m['total_documents']} documents ({m['counts']}), {m['total_bytes'] / 1e6:.2f} MB, "
          f"{stats['regenerated']} regenerations; manifest -> {MANIFEST.relative_to(REPO)}")


if __name__ == "__main__":
    main()
