"""Trust only Supervisor's authenticated ingress, never forwarded client IPs."""

import re

from starlette.requests import Request


def authenticated_ingress(request: Request, *, enabled: bool) -> bool:
    """The App runner must keep Uvicorn proxy_headers=False for this boundary.

    Headers alone are not authentication. The socket peer must be Supervisor,
    and this path is disabled for standalone Bridge installations by default.
    """
    if not enabled or request.client is None:
        return False
    if request.client.host != "172.30.32.2":
        return False
    paths = request.headers.getlist("x-ingress-path")
    users = request.headers.getlist("x-remote-user-id")
    return bool(
        len(paths) == 1
        and len(users) == 1
        and re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]+/?", paths[0])
        and re.fullmatch(r"[0-9a-f]{32}", users[0])
    )
