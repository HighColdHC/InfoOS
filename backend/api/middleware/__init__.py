"""HTTP middleware owned by the API boundary."""

from backend.api.middleware.plugin_request_audit import PluginRequestAuditMiddleware

__all__ = ["PluginRequestAuditMiddleware"]
