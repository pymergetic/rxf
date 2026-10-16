"""TLS 1.3 alerts (RFC 8446 §6): typed refusals that carry their alert name.

The stack refuses by name (docs/25 §17); an alert is the wire name of that
refusal. `TlsAlert` sits on `net.schema.Refusal`, so the body catches a refusal
like every other one — and `.alert` says which RFC 8446 §6 description went (or
would go) on the wire: `protocol_version`, `unexpected_message`,
`handshake_failure`, and friends. A refusal is never a bare error code.
"""

from __future__ import annotations

from typing import Final

from ...schema import Refusal

# RFC 8446 §6: AlertLevel.
WARNING: Final = 1
FATAL: Final = 2

# RFC 8446 §6: AlertDescription, name -> number (what this card can send).
ALERTS: Final = {
    "close_notify": 0,
    "unexpected_message": 10,
    "bad_record_mac": 20,
    "record_overflow": 22,
    "handshake_failure": 40,
    "bad_certificate": 42,
    "unsupported_certificate": 43,
    "certificate_revoked": 44,
    "certificate_expired": 45,
    "certificate_unknown": 46,
    "illegal_parameter": 47,
    "unknown_ca": 48,
    "access_denied": 49,
    "decode_error": 50,
    "decrypt_error": 51,
    "protocol_version": 70,
    "insufficient_security": 71,
    "internal_error": 80,
    "inappropriate_fallback": 86,
    "user_canceled": 90,
    "missing_extension": 109,
    "unsupported_extension": 110,
    "unrecognized_name": 112,
    "bad_certificate_status_response": 113,
    "unknown_psk_identity": 115,
    "certificate_required": 116,
    "no_application_protocol": 120,
}

_BY_NUMBER: Final = {number: name for name, number in ALERTS.items()}


def name_of(number: int) -> str:
    """The alert's wire name; unknown numbers name themselves honestly."""
    return _BY_NUMBER.get(number, f"alert_{number}")


def alert_bytes(name: str, level: int = FATAL) -> bytes:
    """The two-byte alert payload: level, description (RFC 8446 §6)."""
    try:
        description = ALERTS[name]
    except KeyError:
        raise ValueError(f"no RFC 8446 §6 alert is named {name!r}") from None
    return bytes([level, description])


class TlsAlert(Refusal):
    """A TLS refusal: the message is the honest words, `.alert` the wire name.

    Example: `TlsAlert("TLS 1.2 and older are refused by name (docs/25 §17)",
    "protocol_version")`.
    """

    def __init__(self, message: str, alert: str) -> None:
        super().__init__(message)
        if alert not in ALERTS:
            raise ValueError(f"no RFC 8446 §6 alert is named {alert!r}")
        self.alert = alert

    def __repr__(self) -> str:
        return f"TlsAlert({self.args[0]!r}, alert={self.alert!r})"
