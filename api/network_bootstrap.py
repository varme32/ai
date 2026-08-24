"""Apply network defaults before any HTTP/gRPC client is imported.

On some Windows ISPs the IPv6 path to Google (Gemini / Cloud TTS / STT)
hangs or returns a Google frontend 502 HTML page. Prefer IPv4 and make
gRPC use the same resolver as Python so the preference applies.
"""

from __future__ import annotations

import os
import socket


def apply_network_bootstrap() -> None:
    os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

    current = socket.getaddrinfo
    if getattr(current, "_dograh_ipv4", False):
        return

    original_getaddrinfo = current

    def _ipv4_preferred_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = original_getaddrinfo(host, port, family, type, proto, flags)
        return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)

    _ipv4_preferred_getaddrinfo._dograh_ipv4 = True
    socket.getaddrinfo = _ipv4_preferred_getaddrinfo

    # Patch aioice to prevent crashes on Python 3.13 when STUN retry timer
    # fires after peer connection close / transport destruction.
    try:
        import aioice.ice

        _orig_send_stun = aioice.ice.StunProtocol.send_stun

        def _safe_send_stun(self, message, addr):
            transport = getattr(self, "transport", None)
            if transport is None or transport.is_closing():
                return
            try:
                _orig_send_stun(self, message, addr)
            except Exception:
                pass

        aioice.ice.StunProtocol.send_stun = _safe_send_stun
    except (ImportError, AttributeError):
        pass


apply_network_bootstrap()

