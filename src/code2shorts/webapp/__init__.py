"""Phase 7.0 web application.

Importing this package starts nothing and touches no pipeline component -
`create_app()` builds the application, and only `serve()` binds a port.
"""

from code2shorts.webapp.app import create_app, serve

__all__ = ["create_app", "serve"]
