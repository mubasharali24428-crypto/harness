API_PREFIX = "/api/v1"

ERROR_BODY_EXAMPLE = {"error": {"code": "string", "message": "string"}}


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def ws_envelope(type_: str, payload: dict, ts: str, seq: int) -> dict:
    return {"type": type_, "payload": payload, "ts": ts, "seq": seq}
