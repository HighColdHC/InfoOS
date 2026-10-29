"""Internal runtime services for app-owned flows.

These functions are the product surface used by the web workbench and
scheduler-facing routes. They deliberately avoid importing the external CLI
entrypoint so the app can keep running while CLI compatibility is removed.
"""

