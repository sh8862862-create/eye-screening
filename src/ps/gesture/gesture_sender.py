"""Send only current gesture events to the local PC test page, without retries."""
import ipaddress
import json
import math
import time
import urllib.request
import urllib.parse
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GestureSender:
    def __init__(self, base_url, timeout=.6):
        parts = urllib.parse.urlsplit(base_url)
        try:
            address = ipaddress.IPv4Address(parts.hostname)
            port = parts.port
        except (ValueError, TypeError) as exc:
            raise ValueError("Use the PC's literal local IPv4 address") from exc
        if (parts.scheme != "http" or not (address.is_private or address.is_loopback)
                or parts.username is not None or parts.password is not None
                or parts.path not in ("", "/") or parts.query or parts.fragment):
            raise ValueError("Expected local PC URL, e.g. http://192.168.2.1:8765")
        self.url = f"http://{address}" + (f":{port}" if port is not None else "") + "/event"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.session = uuid.uuid4().hex
        self.seq = 0
        self.timeout = timeout

    def send(self, result, captured_at=None):
        # Releases describe current control state, not the age of a lost image.
        age = 0. if result["mode"] == "IDLE" else (time.monotonic() - captured_at) * 1000
        if not math.isfinite(age) or not 0 <= age <= 5000:
            raise ValueError("Gesture result is stale; not sent")
        self.seq += 1
        event = {"session": self.session, "seq": self.seq,
                 "mode": result["mode"], "event": result["event"],
                 "scroll_px": result["scroll_px"], "zoom_factor": result["zoom_factor"],
                 "result_age_ms": round(age, 2)}
        request = urllib.request.Request(self.url,
                                         json.dumps(event, allow_nan=False).encode("utf-8"),
                                         {"Content-Type": "application/json"}, method="POST")
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                raise ValueError("Unexpected PC acknowledgement")
        ack = json.loads(raw)
        if not isinstance(ack, dict) or ack.get("ok") is not True or ack.get("accepted") is not True:
            raise ValueError("PC did not accept the gesture")


def publish(sender, control, result, captured_at):
    """On uncertain delivery, forget activation. Never queue or replay motion."""
    if sender is None:
        return result, "LOG_ONLY", ""
    try:
        sender.send(result, captured_at)
        return result, "SENT", ""
    except (OSError, ValueError) as exc:
        return control.reset("STOP_RELEASE"), "SEND_FAILED", str(exc)
