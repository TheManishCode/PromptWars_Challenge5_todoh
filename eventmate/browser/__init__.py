"""A read-only browser for pages that only show their text after scripts run (ADR-010, bounded).

This package has no model in it, takes no input from a person or a model, and does not click, type or submit. It opens a public page, lets the page's scripts run, and
returns the text. That text goes to the same deterministic readers as any fetched page (price patterns, contact patterns); it is never put in a prompt.
"""
