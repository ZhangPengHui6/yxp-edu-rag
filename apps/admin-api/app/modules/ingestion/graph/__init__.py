"""LangGraph nodes for the offline ingestion pipeline.

Modules:

* :mod:`state` — graph state and the non-serializable runtime container;
* :mod:`base` — node contract and job-step bookkeeping;
* :mod:`builder` — graph assembly and the compiled entry point;
* :mod:`nodes` — one module per pipeline step;
* :mod:`documents` / :mod:`persistence` — pure Mongo document and write helpers;
* :mod:`deps` — provider/storage lookups (patchable in tests).
"""
