"""Investigators team experiment backend.

One source tree owns the API, the worker and the Investigators database migration history
(spec §3.3, HB-09). Nothing in this package may import another live application's private
modules (INV-0101).
"""

__version__ = "0.1.0"
