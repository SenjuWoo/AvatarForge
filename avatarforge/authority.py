"""Authenticated owner commands for the loopback desktop service."""
import json
import secrets


def local_request_authorized(handler, token):
    port = handler.server.server_port
    host = handler.headers.get("Host")
    origin = handler.headers.get("Origin")
    return (handler.client_address[0] == "127.0.0.1"
            and host in {f"127.0.0.1:{port}", f"localhost:{port}"}
            and (origin is None or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"})
            and secrets.compare_digest(handler.headers.get("Authorization", ""), "Bearer " + token))


def read_local_request(handler, token):
    """Read a bounded command selected by this desktop session's owner.

    This grants owner authority to select local files/tools; it does not make
    archive members, model contents, or shader data trusted. Never bypass the
    loopback/Host/Origin/nonce checks or expose this helper on a remote server.
    """
    if not local_request_authorized(handler, token):
        raise PermissionError("Unauthorized local request.")
    size = int(handler.headers.get("Content-Length", 0))
    if size < 0 or size > 256 * 1024:
        raise ValueError("Request exceeds 256 KiB.")
    args = json.loads(handler.rfile.read(size) or b"{}")
    if not isinstance(args, dict):
        raise ValueError("Request must be an object.")
    return args

