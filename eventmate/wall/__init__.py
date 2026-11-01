"""The signature wall: visitors leave a name on the left wall of the 3D room.

This package is deliberately isolated. It has its own SQLite file, imports no plan, guest, draft or workspace store, and nothing in it reads or
returns data from them. Wall names are public by design; they never enter a prompt, a trace, a log line or a private export.
"""
