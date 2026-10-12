/**
 * Generates golden fixtures for the Python legacy chunker port
 * (intelligence/src/scribe_ingest/legacy_chunker.py) by running the REAL
 * lib/chunking.ts chunkContent.
 *
 *   npx tsx scripts/dump-legacy-chunks.ts
 *
 * Writes intelligence/tests/ingestion/fixtures/legacy_chunks/*.json.
 */
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { chunkContent } from "../lib/chunking";

const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "intelligence/tests/ingestion/fixtures/legacy_chunks");
const CORPUS = path.join(ROOT, "fixtures/corpus");

const para = (n: number) =>
    `Paragraph ${n}. The quick brown fox jumps over the lazy dog while the committee reviews the quarterly figures in detail.`;
const repeated = "This identical paragraph appears several times in the document and must not confuse offsets. ".repeat(6).trim();

const prose = (count: number, every = 0) => {
    const parts: string[] = [];
    for (let i = 0; i < count; i++) {
        parts.push(every && i % every === 0 ? repeated : para(i).repeat(4));
    }
    return parts.join("\n\n");
};

interface Case {
    name: string;
    options?: { fileName?: string; numPages?: number };
    content?: string;
    source?: string;
}

const synthetic: Case[] = [
    { name: "empty", content: "" },
    { name: "whitespace_only", content: "  \n\n \t " },
    { name: "short", content: "Hello world." },
    { name: "only_newlines", content: "\n".repeat(9000) },
    { name: "only_double_newlines", content: "\n\n".repeat(5000) },
    { name: "newlines_with_text_edges", content: "\n\n\nstart" + "\n".repeat(4500) + "end\n\n\n" },
    { name: "long_prose_repeated_paragraphs", content: prose(80, 7), options: { fileName: "prose.txt", numPages: 12 } },
    { name: "identical_paragraphs_only", content: (repeated + "\n\n").repeat(20) },
    { name: "single_line_long", content: "word ".repeat(3000) },
    { name: "no_whitespace_run", content: "x".repeat(9500) },
    { name: "single_newline_lines", content: Array.from({ length: 400 }, (_, i) => `Line number ${i} has some text in it.`).join("\n") },
    {
        name: "unicode_mixed",
        content: Array.from({ length: 60 }, (_, i) => `Párrafo ${i}: naïve café — 日本語のテキスト、数字${i}。 ünïcödé   spaces…`.repeat(3)).join("\n\n"),
        options: { numPages: 3 },
    },
    {
        name: "emoji_surrogates",
        content: Array.from({ length: 50 }, (_, i) => `Item ${i} 😀🎉👩‍👩‍👧‍👦 emoji run ${"🙂".repeat(60)}`).join("\n\n"),
    },
    { name: "emoji_no_whitespace", content: "😀".repeat(4500) },
    { name: "crlf", content: Array.from({ length: 120 }, (_, i) => `Windows line ${i} with text.`).join("\r\n\r\n") },
    { name: "js_whitespace_trim", content: "﻿   hello \u0085\u001c world\n\n" + "tail text ".repeat(500) + "　 " },
    { name: "boundary_3999_4000_4001", content: ["a".repeat(3999), "b".repeat(4000), "c".repeat(4001), "d".repeat(10)].join("\n\n") },
    { name: "overlap_dup_words", content: ("alpha beta gamma delta ".repeat(300) + "\n\n").repeat(6) },
];

const cases: Case[] = [...synthetic];
for (const f of readdirSync(CORPUS).sort()) {
    if (f.endsWith(".txt") || f.endsWith(".md")) {
        cases.push({ name: `corpus_${f.replace(/[^a-zA-Z0-9]+/g, "_")}`, source: `fixtures/corpus/${f}`, options: { fileName: f, numPages: 5 } });
    }
}

async function main() {
    mkdirSync(OUT, { recursive: true });
    for (const c of cases) {
        const content = c.source ? readFileSync(path.join(ROOT, c.source), "utf8") : (c.content as string);
        const chunks = await chunkContent(content, c.options ?? {});
        const doc: Record<string, unknown> = { name: c.name, options: c.options ?? {}, chunks };
        if (c.source) doc.source = c.source;
        else doc.content = content;
        writeFileSync(path.join(OUT, `${c.name}.json`), JSON.stringify(doc, null, 1) + "\n");
        console.log(`${c.name}: ${chunks.length} chunks`);
    }
}

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
