# Operations

- Monitor `GET /health` and error responses. The kit logs method and path (no
  query strings, bodies or contact details) and unhandled errors to stderr.
- BenchLink makes no outbound calls and sends no messages, so there are no
  delivery queues or third-party outages to watch.
- Roll back by redeploying the previous reviewed image. Migrations are
  forward-only, so an older version must not run against a database migrated by
  a newer one; restore the matching backup instead.
- Support tasks such as fixing a roster are done in the app by a league admin
  (re-import or edit players); operators should not edit rows by hand.
