"""Web UI for the OA Archive Tracker (Django).

A thin layer over ``oa_tracker``: pages render the same rows/report the
CLI writes to files, and every button goes through ``actions.apply_single``
— the code path behind ``oa action`` — with ``source="web:<user>"`` in the
audit log. Django's own tables (logins, sessions) live in a separate
SQLite file; the tracker database schema is untouched.
"""
