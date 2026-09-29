# Resource limits

Single process, single thread (wsgiref). Request bodies are capped at 2 MiB so
that a 512 KiB CSV fits after JSON encoding; the importer itself refuses CSV
text over 512 KiB or over 2,000 data rows (400). Idle connections time out after
30 seconds. Suggested container limits: 256 MiB memory, 0.5 CPU, 1 GiB data
volume, which is ample for many leagues.
