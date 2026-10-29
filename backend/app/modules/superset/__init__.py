"""Nova's side of the Superset BI surface (docs/arch-17-superset.md).

Superset holds no Nova credential and no data policy. This package gives it
exactly two things, both bound to a live Nova session:

* a **BI handle** — an opaque, HttpOnly cookie value Superset trades for the
  caller's identity and Superset role on every request; and
* a **data token** — a short-lived secret Superset presents to the MySQL proxy
  as its password, which the proxy verifies with its own scramble and turns
  into an engine session as the principal under the session's active role.
"""
