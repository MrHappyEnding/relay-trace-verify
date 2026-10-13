"""Replay only immutable public dependency responses; never enable verifier networking."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path

CACHE_ROOT = Path("/opt/rtv-offline-http/cache")

try:
    import requests
except ImportError:
    requests = None

if requests is not None:
    original_send = requests.sessions.Session.send

    def cached_send(session, request, **kwargs):
        if request.method != "GET" or not re.fullmatch(
            r"https://raw\.githubusercontent\.com/[^/]+/[^/]+/[0-9a-f]{40}/[^?#]+", request.url
        ):
            raise RuntimeError("Verifier dependency request is not an immutable public URL")
        root = CACHE_ROOT
        path = root / (hashlib.sha256(request.url.encode()).hexdigest() + ".json")
        if os.environ.get("RTV_CACHE_RECORD") == "1":
            response = original_send(session, request, **kwargs)
            if response.status_code not in (200, 404):
                raise RuntimeError("Unexpected dependency response during image preparation")
            root.mkdir(parents=True, exist_ok=True)
            value = {
                "url": request.url,
                "status": response.status_code,
                "content": base64.b64encode(response.content).decode(),
                "encoding": response.encoding,
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
            path.write_text(json.dumps(value, sort_keys=True) + "\n")
            return response
        value = json.loads(path.read_text())
        content = base64.b64decode(value["content"], validate=True)
        if value["url"] != request.url or hashlib.sha256(content).hexdigest() != value["sha256"]:
            raise RuntimeError("Cached dependency failed integrity check")
        response = requests.Response()
        response.status_code = value["status"]
        response._content = content
        response.encoding = value["encoding"]
        response.url = request.url
        response.request = request
        return response

    requests.sessions.Session.send = cached_send
