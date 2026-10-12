"""Drop-in strategy modules (T14 hybrid profiles A-E live here).

`registry.discover_strategies()` imports every module in this package once; a module registers
its handlers at import time with `@register("name")`. No other file needs editing.
"""
