"""Whole-page parsing comparison engines; optional tools are imported only on selection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Protocol

from scribe_ingest.ocr import GeminiOcrClient, OCR_MODEL, OCR_PROMPT, OcrClient, render_page_png
from scribe_eval.cache import DiskCache
from scribe_eval.cost import Usage, estimate_tokens, tokens_usd

from .dataset import safe_doc_path

OUTPUT_TOKEN_ALLOWANCE = 30_000  # budget assumption tied to the OCR page character limit; not provider usage or a hard bound

class Engine(Protocol):
    name: str
    model_id: str | None

    def extract(self, pdf_path: Path, page: int) -> tuple[str, float, float]: ...


class PdfPlumberEngine:
    name = "pdfplumber"
    model_id = None

    def __init__(self, extract_page: Any = None) -> None:
        self._extract_page = extract_page

    def extract(self, pdf_path: Path, page: int) -> tuple[str, float, float]:
        started = time.perf_counter()
        if self._extract_page is not None:
            text = self._extract_page(pdf_path, page)
        else:
            import pdfplumber

            with pdfplumber.open(str(pdf_path)) as pdf:
                if page < 1 or page > len(pdf.pages):
                    raise ValueError(f"page {page} is outside {pdf_path.name} (1..{len(pdf.pages)})")
                text = pdf.pages[page - 1].extract_text() or ""
        return text, time.perf_counter() - started, 0.0


class TesseractEngine:
    name = "tesseract"
    model_id = None

    def __init__(self, ocr_image: Any = None) -> None:
        self._ocr_image = ocr_image

    def extract(self, pdf_path: Path, page: int) -> tuple[str, float, float]:
        started = time.perf_counter()
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if page < 1 or page > len(pdf):
                raise ValueError(f"page {page} is outside {pdf_path.name} (1..{len(pdf)})")
            bitmap = pdf[page - 1].render(scale=150 / 72)
            try:
                image = bitmap.to_pil().convert("RGB")
                if self._ocr_image is None:
                    import pytesseract

                    text = pytesseract.image_to_string(image)
                else:
                    text = self._ocr_image(image)
            finally:
                bitmap.close()
        finally:
            pdf.close()
        return text, time.perf_counter() - started, 0.0


def _gemini_key(model: str, png: bytes) -> str:
    payload = json.dumps([model, "parsing-ocr-v1", hashlib.sha256(png).hexdigest()], separators=(",", ":"))
    return "parse-ocr:" + hashlib.sha256(payload.encode()).hexdigest()


class GeminiVisionEngine:
    name = "gemini"

    def __init__(self, *, cache: Any, client: OcrClient | None = None, fake: bool = False) -> None:
        self.cache = cache
        self.client = client or GeminiOcrClient()
        self.model_id = self.client.model
        self.fake = fake
        self.pages: dict[str, dict[str, Any]] = {}
        self.usages: list[Usage] = []
        self._cache_hits = 0
        self._live_calls = 0

    def cache_key_for(self, pdf_path: Path, page: int) -> str:
        png = render_page_png(pdf_path.read_bytes(), page)
        return _gemini_key(self.model_id, png)

    def cached(self, key: str) -> Any:
        found, value = self.cache._get(key) if hasattr(self.cache, "_get") else (False, None)
        return value if found else None

    def estimate_live_cost(self, keys: list[tuple[str, bytes]]) -> float:
        estimate = 0.0
        for key, _ in keys:
            if self.cached(key) is None:
                # This is a budget allowance tied to the page text limit, not a provider usage estimate.
                estimate += tokens_usd(self.model_id, estimate_tokens(OCR_PROMPT) + 8000,
                                        OUTPUT_TOKEN_ALLOWANCE)
        return estimate

    async def _transcribe(self, png: bytes, page: int, key: str) -> tuple[str, int, int, bool]:
        computed = False

        async def compute() -> dict[str, Any]:
            nonlocal computed
            computed = True
            result = await self.client.transcribe(png, page=page)
            return {"text": result.text, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens}

        raw = await self.cache.get_or_compute(key, compute)
        return str(raw["text"]), int(raw["input_tokens"]), int(raw["output_tokens"]), not computed

    async def extract_async(self, pdf_path: Path, page: int) -> tuple[str, float, float]:
        started = time.perf_counter()
        png = render_page_png(pdf_path.read_bytes(), page)
        key = _gemini_key(self.model_id, png)
        try:
            text, input_tokens, output_tokens, cached = await self._transcribe(png, page, key)
        except Exception as exc:
            raise RuntimeError(f"Gemini extraction failed for {pdf_path.name} page {page}: {type(exc).__name__}") from None
        elapsed = time.perf_counter() - started
        self.pages[key] = {"seconds": elapsed, "cached": cached}
        estimated = input_tokens == 0 or (output_tokens == 0 and bool(text))
        if input_tokens == 0:
            input_tokens = estimate_tokens(OCR_PROMPT) + 1000
        if output_tokens == 0 and text:
            output_tokens = estimate_tokens(text)
        self.usages.append(Usage(self.model_id, input_tokens, output_tokens, estimated=estimated, cached=cached))
        self._cache_hits += int(cached)
        self._live_calls += int(not cached)
        return text, elapsed, 0.0 if cached else tokens_usd(self.model_id, input_tokens, output_tokens)

    def extract(self, pdf_path: Path, page: int) -> tuple[str, float, float]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.extract_async(pdf_path, page))
        raise RuntimeError("GeminiVisionEngine.extract cannot run inside an event loop; use extract_async")

    def preflight(self, pages: list[tuple[Path, int]], *, spend_cap: float | None, ledger: Path) -> float:
        keys = [(self.cache_key_for(path, page), b"") for path, page in pages]
        estimate = self.estimate_live_cost(keys)
        from scribe_eval.cost import preflight

        preflight(estimate, spend_cap=spend_cap, path=ledger, what="run parsing comparison")
        return estimate

    def write_spend(self, run_id: str, ledger: Path) -> None:
        from scribe_eval.cost import RunSpend

        if not self.usages:
            return
        spend = RunSpend("parse-compare", run_id)
        for usage in self.usages:
            spend.add(usage)
        spend.write(ledger)
