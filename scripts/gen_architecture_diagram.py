"""Generate docs/assets/architecture.{svg,excalidraw} from one spec.

Run: python3 scripts/gen_architecture_diagram.py
Open the .excalidraw file at https://excalidraw.com to edit it, then re-export.
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "assets"
W, H = 1200, 680

# (id, x, y, w, h, fill, title, subtitle lines)
BOXES = [
    ("browser", 30, 240, 150, 80, "#e7f5ff", "Browser", []),
    ("next", 250, 200, 240, 160, "#d3f9d8", "Next.js on Vercel",
     ["Clerk auth, chat stream,", "uploads, mints 60 s", "internal token"]),
    ("py", 620, 200, 250, 160, "#fff3bf", "Python FastAPI",
     ["/retrieve /compare /verify", "/evidence/open", "/ingest/*"]),
    ("db", 960, 50, 210, 110, "#ffe3e3", "Postgres + pgvector",
     ["RLS policies, FTS,", "queue functions"]),
    ("blob", 960, 230, 210, 90, "#f3d9fa", "Vercel Blob", ["private originals"]),
    ("gem", 620, 460, 190, 90, "#e5dbff", "Gemini", ["embeddings, OCR,", "verifier"]),
    ("voy", 860, 460, 190, 90, "#e5dbff", "Voyage rerank", ["behind an adapter"]),
    ("cli", 250, 40, 240, 80, "#f1f3f5", "scribe-eval / scribe-perf",
     ["local or GitHub Actions"]),
]
B = {b[0]: b for b in BOXES}

# (points, label, label_xy, dashed)
ARROWS = [
    ([(180, 280), (250, 280)], "", (0, 0), False),
    ([(490, 280), (620, 280)], "/api/py/* + token", (500, 268), False),
    ([(490, 215), (960, 105)], "", (0, 0), False),
    ([(870, 215), (960, 140)], "", (0, 0), False),
    ([(870, 300), (960, 280)], "download", (878, 318), False),
    ([(105, 320), (105, 405), (1065, 405), (1065, 320)], "direct upload", (560, 396), True),
    ([(700, 360), (700, 460)], "", (0, 0), False),
    ([(400, 360), (640, 465)], "", (0, 0), False),
    ([(820, 360), (900, 460)], "", (0, 0), False),
    ([(490, 80), (960, 90)], "", (0, 0), False),
]
NOTES = [
    (270, 160, "query: auth, workspace, /retrieve"),
    (270, 180, "ingest: PARSE > OCR > CHUNK > EMBED > ACTIVATE"),
]
FONT = '"Virgil","Segoe Print","Bradley Hand","Comic Sans MS",cursive'


def svg():
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="Scribe 2.0 architecture">',
         '<defs><filter id="rough"><feTurbulence type="fractalNoise" baseFrequency="0.03" numOctaves="2" seed="4"/>'
         '<feDisplacementMap in="SourceGraphic" scale="3"/></filter>'
         '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
         '<path d="M0 0L10 5L0 10" fill="none" stroke="#1e1e1e" stroke-width="1.6"/></marker></defs>',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         f'<g font-family=\'{FONT}\' fill="#1e1e1e" text-anchor="middle">',
         f'<text x="{W/2}" y="28" font-size="22" font-weight="bold">Scribe 2.0 architecture</text>']
    o.append('<g filter="url(#rough)">')
    for _, x, y, w, h, fill, *_ in BOXES:
        o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="#1e1e1e" stroke-width="2"/>')
    for pts, _, _, dashed in ARROWS:
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        da = ' stroke-dasharray="8 6"' if dashed else ""
        o.append(f'<path d="{d}" fill="none" stroke="#1e1e1e" stroke-width="2"{da} marker-end="url(#ah)"/>')
    o.append("</g>")
    for _, x, y, w, h, _, title, sub in BOXES:
        o.append(f'<text x="{x+w/2}" y="{y+28}" font-size="19" font-weight="bold">{title}</text>')
        for i, s in enumerate(sub):
            o.append(f'<text x="{x+w/2}" y="{y+52+i*20}" font-size="14">{s}</text>')
    for _, _, (lx, ly), _ in [(a[0], a[1], a[2], a[3]) for a in ARROWS if a[1]]:
        pass
    for pts, label, (lx, ly), _ in ARROWS:
        if label:
            o.append(f'<text x="{lx}" y="{ly}" font-size="13" text-anchor="start">{label}</text>')
    o.append(f'<text x="30" y="{H-50}" font-size="14" text-anchor="start">Query path: authenticate, resolve workspace, POST /api/py/retrieve, one REPEATABLE READ transaction,</text>')
    o.append(f'<text x="30" y="{H-30}" font-size="14" text-anchor="start">dense + lexical search, RRF (k=60), rerank, dedup, packing, answerability gate, [E#] evidence bundle.</text>')
    o.append(f'<text x="30" y="{H-10}" font-size="14" text-anchor="start">Ingest path: upload to Blob, then leased resumable steps PARSE, OCR, CHUNK, EMBED, ACTIVATE.</text>')
    o.append("</g></svg>")
    return "\n".join(o)


def excalidraw():
    els, n = [], [0]

    def base(t, x, y, w, h, **kw):
        n[0] += 1
        e = dict(id=f"e{n[0]}", type=t, x=x, y=y, width=w, height=h, angle=0,
                 strokeColor="#1e1e1e", backgroundColor="transparent", fillStyle="solid",
                 strokeWidth=2, strokeStyle="solid", roughness=1, opacity=100,
                 groupIds=[], frameId=None, roundness=None, seed=n[0] * 7919,
                 version=1, versionNonce=n[0] * 104729, isDeleted=False,
                 boundElements=None, updated=1, link=None, locked=False)
        e.update(kw)
        return e

    def text(x, y, w, s, size, bold=False, align="center"):
        lines = s.count("\n") + 1
        return base("text", x, y, w, size * 1.25 * lines, text=s, fontSize=size,
                    fontFamily=1, textAlign=align, verticalAlign="top",
                    containerId=None, originalText=s, autoResize=True, lineHeight=1.25,
                    baseline=int(size))

    els.append(text(W / 2 - 150, 4, 300, "Scribe 2.0 architecture", 24))
    for _, x, y, w, h, fill, title, sub in BOXES:
        els.append(base("rectangle", x, y, w, h, backgroundColor=fill, roundness={"type": 3}))
        els.append(text(x, y + 10, w, title, 18))
        if sub:
            els.append(text(x, y + 38, w, "\n".join(sub), 14))
    for pts, label, (lx, ly), dashed in ARROWS:
        (x0, y0) = pts[0]
        rel = [[px - x0, py - y0] for px, py in pts]
        w = max(abs(p[0]) for p in rel); h = max(abs(p[1]) for p in rel)
        els.append(base("arrow", x0, y0, w, h, points=rel, lastCommittedPoint=None,
                        startBinding=None, endBinding=None, startArrowhead=None,
                        endArrowhead="arrow", strokeStyle="dashed" if dashed else "solid"))
        if label:
            els.append(text(lx, ly - 14, 140, label, 13, align="left"))
    foot = ("Query path: authenticate, resolve workspace, POST /api/py/retrieve, one REPEATABLE READ transaction,\n"
            "dense + lexical search, RRF (k=60), rerank, dedup, packing, answerability gate, [E#] evidence bundle.\n"
            "Ingest path: upload to Blob, then leased resumable steps PARSE, OCR, CHUNK, EMBED, ACTIVATE.")
    els.append(text(30, H - 62, 900, foot, 14, align="left"))
    return {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
            "elements": els, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {}}


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "architecture.svg").write_text(svg())
(OUT / "architecture.excalidraw").write_text(json.dumps(excalidraw(), indent=2))
print("wrote", OUT)
