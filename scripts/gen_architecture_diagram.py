#!/usr/bin/env python3
"""Generate docs/assets/architecture.{excalidraw,svg} from one spec.

    python3 scripts/gen_architecture_diagram.py

The .excalidraw file is the editable source (open it at https://excalidraw.com or in the VS Code Excalidraw
extension). The .svg is what the README embeds. Both come from the BOXES / ARROWS tables below, so edit the
tables and re-run instead of editing the SVG by hand. Standard library only.

Every box and arrow here must correspond to something in the code; docs/ARCHITECTURE.md section 1 says which file.
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "assets"
W, H = 1300, 840

# Colours by role.
C = dict(client="#e7f5ff", next="#d3f9d8", py="#fff3bf", data="#ffe3e3", blob="#f3d9fa",
         ext="#e5dbff", opt="#f8f9fa", tool="#f1f3f5", cron="#ffe8cc")

# id: (x, y, w, h, fill, [title, *lines], dashed_border)
BOXES = {
    "browser": (20, 270, 130, 120, C["client"], ["Browser", "React UI, useChat", "pdf.js citation", "viewer"], False),
    "cron": (20, 150, 130, 80, C["cron"], ["Vercel Cron", "daily 00:00, 01:00"], False),
    "next": (235, 150, 265, 280, C["next"], [
        "Next.js 16 (Node functions)",
        "proxy.ts: Clerk middleware",
        "/api/chat: AI SDK stream",
        "/api/sources/*: Blob intake",
        "/api/evidence, /api/compare",
        "/api/internal/ingest/{run,sweep,tick}",
        "/api/billing/*, Razorpay webhook",
        "Mints the 60 s internal token"], False),
    "py": (590, 150, 230, 280, C["py"], [
        "Python FastAPI",
        "Vercel Python function",
        "/retrieve  /compare",
        "/evidence/open  /verify",
        "/ingest/{source,step,",
        "sweep,purge}",
        "Dense + FTS, RRF, rerank",
        "Parsers, chunker, indexer"], False),
    "clerk": (235, 540, 110, 60, C["ext"], ["Clerk", "sessions"], False),
    "razor": (360, 540, 170, 60, C["opt"], ["Razorpay", "billing, off by default"], True),
    "smem": (545, 540, 215, 60, C["opt"], ["Supermemory", "optional, legacy engine"], True),
    "gem": (890, 130, 200, 85, C["ext"], ["Google Gemini", "embeddings, OCR, verifier", "chat (AI SDK)"], False),
    "voy": (890, 240, 200, 60, C["ext"], ["Voyage rerank", "behind an adapter"], False),
    "db": (890, 330, 200, 120, C["data"], ["Neon Postgres", "pgvector, Postgres FTS", "RLS policies,", "queue functions"], False),
    "blob": (890, 480, 200, 80, C["blob"], ["Vercel Blob", "private originals"], False),
    "tool": (1140, 480, 150, 100, C["tool"], ["scribe-eval", "scribe-perf", "CI retrieval gate"], False),
}
ZONES = [
    ("vercel", 210, 100, 630, 355, "Vercel project (Hobby), one deployment"),
]

# (id, [points], label, (lx, ly), dashed, start_id, end_id)
ARROWS = [
    ("a_br_next", [(150, 330), (235, 330)], "HTTPS", (165, 318), False, "browser", "next"),
    ("a_cron", [(150, 190), (235, 190)], "Bearer", (160, 178), False, "cron", "next"),
    ("a_next_py", [(500, 290), (590, 290)], "/api/py/*", (510, 267), False, "next", "py"),
    ("a_py_gem", [(820, 172), (890, 172)], "", (0, 0), False, "py", "gem"),
    ("a_py_voy", [(820, 270), (890, 270)], "", (0, 0), False, "py", "voy"),
    ("a_py_db", [(820, 390), (890, 390)], "", (0, 0), False, "py", "db"),
    ("a_py_blob", [(705, 430), (705, 520), (890, 520)], "download originals", (730, 508), False, "py", "blob"),
    ("a_next_db", [(330, 150), (330, 70), (1130, 70), (1130, 360), (1090, 360)], "drizzle, neon-http", (700, 58), False, "next", "db"),
    ("a_next_gem", [(430, 150), (430, 92), (990, 92), (990, 130)], "chat stream, legacy embeddings", (520, 88), False, "next", "gem"),
    ("a_next_clerk", [(290, 430), (290, 540)], "", (0, 0), False, "next", "clerk"),
    ("a_next_razor", [(400, 430), (400, 540)], "", (0, 0), True, "next", "razor"),
    ("a_next_smem", [(480, 430), (480, 485), (650, 485), (650, 540)], "", (0, 0), True, "next", "smem"),
    ("a_br_blob", [(85, 390), (85, 650), (990, 650), (990, 560)],
     "direct upload with a client token; Blob then calls /api/sources/upload-complete", (160, 638), True, "browser", "blob"),
    ("a_tool_db", [(1215, 480), (1215, 420), (1090, 420)], "SQL", (1160, 408), True, "tool", "db"),
]
NOTES = [
    "Query: Next.js authenticates (Clerk), resolves the workspace, mints a 60 s token and calls POST /api/py/retrieve. Python runs one REPEATABLE READ",
    "transaction: dense + lexical search, RRF (k=60), Voyage rerank, dedup, parent expansion, 6,000-token packing, answerability gate, [E#] evidence bundle.",
    "Ingest: upload to Blob, then leased resumable steps PARSE, OCR, CHUNK, EMBED, ACTIVATE, driven by a self-firing tick route (Cron is the daily safety net).",
    "Dashed arrows: optional or outside the request path. Razorpay is off unless FEATURE_BILLING_ENABLED; Supermemory applies to the legacy engine only.",
    "scribe-eval / scribe-perf and the CI retrieval gate run locally or in GitHub Actions, never on Vercel, against a separate database and the same providers.",
]
FONT = "Helvetica, Arial, sans-serif"
INK = "#1e1e1e"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg():
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-label="Scribe 2.0 architecture">',
         '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">'
         f'<path d="M0 0L10 5L0 10z" fill="{INK}"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<g font-family="{FONT}" fill="{INK}">',
         '<text x="20" y="34" font-size="22" font-weight="bold">Scribe 2.0 architecture</text>']
    for _, x, y, w, h, label in ZONES:
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" stroke="#868e96" '
                 f'stroke-width="1.5" stroke-dasharray="7 5"/>')
        o.append(f'<text x="{x+14}" y="{y+22}" font-size="14" fill="#495057">{esc(label)}</text>')
    for _id, pts, label, (lx, ly), dashed, *_ in ARROWS:
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        da = ' stroke-dasharray="7 5"' if dashed else ""
        o.append(f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.8"{da} marker-end="url(#ah)"/>')
    for x, y, w, h, fill, lines, dashed in BOXES.values():
        da = ' stroke-dasharray="6 4"' if dashed else ""
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{INK}" '
                 f'stroke-width="1.8"{da}/>')
        total = 20 * len(lines)
        top = y + (h - total) / 2 + 15
        for i, s in enumerate(lines):
            attrs = 'font-size="15" font-weight="bold"' if i == 0 else 'font-size="13"'
            o.append(f'<text x="{x+w/2}" y="{top+i*20}" text-anchor="middle" {attrs}>{esc(s)}</text>')
    for _id, pts, label, (lx, ly), *_ in ARROWS:
        if label:
            tw = len(label) * 6.6 + 8
            o.append(f'<rect x="{lx-4}" y="{ly-13}" width="{tw}" height="18" fill="#ffffff" opacity="0.92"/>')
            o.append(f'<text x="{lx}" y="{ly}" font-size="12.5" fill="#343a40">{esc(label)}</text>')
    o.append('<line x1="20" y1="700" x2="1280" y2="700" stroke="#ced4da"/>')
    for i, s in enumerate(NOTES):
        o.append(f'<text x="20" y="{726+i*22}" font-size="13.5" fill="#343a40">{esc(s)}</text>')
    o.append("</g></svg>")
    return "\n".join(o)


# ---------------------------------------------------------------- Excalidraw
_n = [0]


def _base(t, x, y, w, h, **kw):
    _n[0] += 1
    e = dict(id=f"el{_n[0]}", type=t, x=x, y=y, width=w, height=h, angle=0, strokeColor=INK,
             backgroundColor="transparent", fillStyle="solid", strokeWidth=2, strokeStyle="solid", roughness=0,
             opacity=100, groupIds=[], frameId=None, roundness=None, seed=_n[0] * 7919,
             version=1, versionNonce=_n[0] * 104729, isDeleted=False, boundElements=[], updated=1,
             link=None, locked=False)
    e.update(kw)
    return e


def _text(x, y, s, size, *, container=None, align="left", valign="top", w=None, color=INK):
    lines = s.split("\n")
    tw = w if w is not None else max(len(l) for l in lines) * size * 0.56
    th = size * 1.25 * len(lines)
    return _base("text", x, y, tw, th, text=s, fontSize=size, fontFamily=2, textAlign=align,
                 verticalAlign=valign, containerId=container, originalText=s, autoResize=container is None,
                 lineHeight=1.25, strokeColor=color)


def excalidraw():
    els, ids = [], {}
    els.append(_text(20, 10, "Scribe 2.0 architecture", 24))
    for zid, x, y, w, h, label in ZONES:
        z = _base("rectangle", x, y, w, h, strokeColor="#868e96", strokeStyle="dashed", strokeWidth=1,
                  roundness={"type": 3})
        els.append(z)
        els.append(_text(x + 14, y + 6, label, 14, color="#495057"))
    for bid, (x, y, w, h, fill, lines, dashed) in BOXES.items():
        r = _base("rectangle", x, y, w, h, backgroundColor=fill, roundness={"type": 3},
                  strokeStyle="dashed" if dashed else "solid")
        ids[bid] = r
        els.append(r)
        title, body = lines[0], lines[1:]
        # one bound text per box: title line first, body below (Excalidraw has one font size per text element,
        # so the title is a second, unbound text placed above the bound body).
        if body:
            body_s = "\n".join(body)
            bh = 13 * 1.25 * len(body)
            tw = max(len(l) for l in body) * 13 * 0.56
            t_body = _text(x + (w - tw) / 2, y + (h - bh) / 2 + 10, body_s, 13, container=r["id"], align="center",
                           valign="middle", w=tw)
            r["boundElements"].append({"id": t_body["id"], "type": "text"})
            els.append(t_body)
        tt = _text(x, y + (h - (15 * 1.25 + (13 * 1.25 * len(body) if body else 0))) / 2 - 2, title, 15,
                   align="center", w=w)
        els.append(tt)
    for aid, pts, label, (lx, ly), dashed, sid, eid in ARROWS:
        x0, y0 = pts[0]
        rel = [[px - x0, py - y0] for px, py in pts]
        wd = max(abs(p[0]) for p in rel)
        ht = max(abs(p[1]) for p in rel)
        a = _base("arrow", x0, y0, wd, ht, points=rel, lastCommittedPoint=None,
                  startBinding={"elementId": ids[sid]["id"], "focus": 0, "gap": 1},
                  endBinding={"elementId": ids[eid]["id"], "focus": 0, "gap": 1},
                  startArrowhead=None, endArrowhead="arrow", strokeWidth=2,
                  strokeStyle="dashed" if dashed else "solid", roundness=None)
        ids[sid]["boundElements"].append({"id": a["id"], "type": "arrow"})
        ids[eid]["boundElements"].append({"id": a["id"], "type": "arrow"})
        els.append(a)
        if label:
            els.append(_text(lx, ly - 14, label, 12, color="#343a40"))
    els.append(_text(20, 712, "\n".join(NOTES), 13.5, color="#343a40"))
    return {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com", "elements": els,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "architecture.svg").write_text(svg() + "\n")
    (OUT / "architecture.excalidraw").write_text(json.dumps(excalidraw(), indent=2) + "\n")
    print("wrote", OUT)
