"""scribe-perf (T32): synthetic scale/performance harness for the dense and lexical retrievers.

Everything here runs against a THROWAWAY database (name must start with ``perf_``); it never
touches real data and never calls any embedding provider (vectors are seeded synthetic)."""
