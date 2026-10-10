"""Tiny PDF writer for tests (Helvetica text, rules, images, RC4 encryption)."""

from __future__ import annotations

import hashlib

_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    out = bytearray()
    i = j = 0
    for b in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page_stream(
    texts: list[tuple[float, float, float, str]] = (),
    lines: list[tuple[float, float, float, float]] = (),
    image: bool = False,
    bold: set[int] | None = None,
) -> str:
    """texts: (x, y_from_top, size, text) on an A4-like 595x842 page."""
    out = []
    for x0, y0, x1, y1 in lines:
        out.append(f"{x0} {842 - y0} m {x1} {842 - y1} l S")
    for k, (x, y, size, t) in enumerate(texts):
        font = "F2" if bold and k in bold else "F1"
        out.append(f"BT /{font} {size} Tf {x} {842 - y} Td ({_esc(t)}) Tj ET")
    if image:
        out.append("q 200 0 0 200 100 400 cm /Im0 Do Q")
    return "\n".join(out)


def build_pdf(pages: list[str], *, user_password: str | None = None, with_image_on: set[int] = frozenset()) -> bytes:
    """pages: content-stream strings. `with_image_on`: 1-based pages that need /Im0 resource."""
    objs: dict[int, bytes] = {}
    n = len(pages)
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{6 + 2 * i} 0 R" for i in range(n))
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objs[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    objs[5] = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
    )
    for i, content in enumerate(pages):
        pg, st = 6 + 2 * i, 7 + 2 * i
        res = "/Font << /F1 3 0 R /F2 4 0 R >>"
        if (i + 1) in with_image_on:
            res += " /XObject << /Im0 5 0 R >>"
        objs[pg] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << {res} >> /Contents {st} 0 R >>"
        ).encode()
        body = content.encode("cp1252")
        objs[st] = b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
    trailer_extra = ""
    if user_password is not None:
        p = -44
        pw = (user_password.encode() + _PAD)[:32]
        owner = (b"owner-secret" + _PAD)[:32]
        o = _rc4(hashlib.md5(owner).digest()[:5], pw)
        fid = b"0123456789abcdef"
        key = hashlib.md5(pw + o + p.to_bytes(4, "little", signed=True) + fid).digest()[:5]
        u = _rc4(key, _PAD)
        objs[max(objs) + 1] = (
            b"<< /Filter /Standard /V 1 /R 2 /P %d /O <%s> /U <%s> >>" % (p, o.hex().encode(), u.hex().encode())
        )
        trailer_extra = f" /Encrypt {max(objs)} 0 R /ID [<{fid.hex()}> <{fid.hex()}>]"
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets[num]
    out += f"trailer\n<< /Size {size} /Root 1 0 R{trailer_extra} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
