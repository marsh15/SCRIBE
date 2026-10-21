"""Local human-review tooling for evaluation items (task A1).

Nothing in ``scribe_intel`` or any app route may import this package. ``require`` is dependency-free; the rest needs
FastAPI (a runtime dependency) and, to serve, uvicorn (dev group, imported lazily by ``server.serve``).
"""
