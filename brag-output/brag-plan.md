# Scribe 2.0 — launch video plan

**One sentence:** Scribe is an open-source workspace where you add private documents, ask a question, and open every citation at the exact page, with a verdict on whether each claim in the answer is supported.
**For:** people who answer questions from contracts, policies, research and ops documents.
**Sets it apart:** evidence is part of the answer. Citations open at the exact passage, and each claim is checked after the answer streams. Missing evidence is stated plainly.
**Share caption:** Two leave policies, two answers. Scribe cites the right one and shows you the page.

## Angle

The version conflict. The Northwind demo corpus (fictional) holds a 2024 and a 2026 leave policy: 24 days vs 27 days. The whole video follows one question through the real flow: ask, answer, open the citation, see the verdict.

## Honesty rules (from the README)

Scribe 2.0 is unproven in production and no retrieval-quality number has been measured. So: no accuracy, speed or "% correct" claims anywhere. Only the product doing its job, using its real strings, tokens and the real fixture PDFs rendered with the project's own pdf.js. All names and documents are the fictional Northwind fixtures; no secrets, emails or customer data.

## Tone and identity

`polished`: calm, precise, quiet (the product's own design brief). 3–6 beats, long holds, soft fades, no bounce or glow.
- Colors (from `app/globals.css`): alabaster `#F4F1EB`, graphite `#2A2826`, muted olive `#6B705C`, burnt clay `#B07D62`, RAG teal `#087565`, 1px graphite 20% borders, 8px radius, 8px hard offset shadow.
- Type: the project's own stacks. Serif display (DM Serif Display → Iowan Old Style) for headlines, sans (DM Sans → Avenir Next), mono (JetBrains Mono → SF Mono/Menlo).
- Real UI reused: three-pane Sources / Conversation / Evidence, `ClaimBadge` styles and `STATUS_META` strings, evidence dialog anatomy and amber `mark` highlight, "Verifying citations…" and "Verification complete: 1 verified" strings, landing-page grid and final CTA.

## Storyboard (1920×1080, 30 fps, ~20.6 s)

| # | Time | Scene | On screen |
|---|---|---|---|
| A | 0.0–3.6 | Hook | Two real policy cards: `hr-leave-policy-2024.pdf` "24 working days" and `hr-leave-policy-2026.pdf` "27 working days". Serif: "Two leave policies. Two answers." then "Which one did the AI read?" |
| B | 3.6–7.4 | Reveal | Dip to the workspace. Tagline "Ask your documents. Check every answer." Sources list shows both PDFs ready. The question types into the input: "How many days of annual leave do full-time employees get?" |
| C | 7.4–11.2 | Answer | Answer streams: "Full-time employees are entitled to 27 working days of annual leave per calendar year. [E1]". Evidence pane fills. "Verifying citations…" becomes the ✓ VERIFIED badge and "Verification complete: 1 verified". |
| D | 11.2–14.8 | Inspect | Click [E1]. The citation dialog opens: "hr-leave-policy-2026.pdf, Page 2", the quote highlighted amber, over the real rendered PDF page with the highlight box on the sentence. |
| E | 14.8–18.0 | Verdict | Six status chips with their real labels. Serif: "Every claim gets a verdict." Sub: "Scribe never implies certainty it does not have." |
| F | 18.0–20.6 | Outro | Graphite ground (the landing page's CTA): pen-mark logo, "Scribe", "Bring a document. Ask the hard question.", `github.com/marsh15/SCRIBE` · Open source, MIT. |

Transitions: dip through the background (old content out, then new content in), never a crossfade of two busy layouts.
Poster: the settled citation-dialog frame in D (the real page with the highlighted sentence).

## Sound

One piece in D major / B minor pentatonic at 72 bpm: a soft pad (D–A–Bm–G, slow swells), a sparse felt-piano-like pluck line, light sub on the hook and outro. SFX are tuned to the same key: key-tap ticks for typing (very quiet, pitched), a soft rising two-note chime for the Verified badge (A5 → D6), a low airy "open" swell for the dialog, a soft tick per status chip. Everything is low-passed, effects sit about 10–14 dB under the music, with a gentle fade at the end.
