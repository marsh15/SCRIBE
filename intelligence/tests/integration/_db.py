from __future__ import annotations

import uuid
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "fixtures" / "corpus"
GOLDEN = REPO / "benchmarks" / "datasets" / "golden-v1" / "cases.jsonl"


async def aconnect(url: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(url, autocommit=True, prepare_threshold=None)


async def new_workspace(conn, kind: str = "eval") -> str:
    cur = await conn.execute("insert into workspaces(kind, owner_user_id) values (%s,%s) returning id",
                             (kind, f"t12-{uuid.uuid4()}"))
    return str((await cur.fetchone())[0])


async def new_file(conn, wid: str, name="note.md", mime="text/markdown", storage_url=None) -> int:
    cur = await conn.execute(
        "insert into files(name,type,size,user_id,workspace_id,status,storage_url) "
        "values (%s,%s,1,'t12',%s,'processing',%s) returning id", (name, mime, wid, storage_url))
    return (await cur.fetchone())[0]


def doc(n: int) -> bytes:
    body = "\n\n".join(f"Paragraph {i} of version {n}. " + "The quick brown fox jumps. " * 12 for i in range(6))
    return f"# Title {n}\n\n{body}\n\n## Second\n\nMore text for version {n}.\n".encode()
