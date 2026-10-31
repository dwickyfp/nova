"""Nova's integration package for Apache Superset (docs/arch-17-superset.md).

Superset authenticates nobody and stores no Nova credential. On every request
it trades the ``nova_bi`` cookie for the caller's identity at Nova, and every
connection to the "Nova" database uses a data token Nova issues for that same
session. Ranger, behind Nova's MySQL proxy, decides what each query returns.
"""
