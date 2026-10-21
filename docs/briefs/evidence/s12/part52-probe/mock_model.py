"""Scripted OpenAI-compatible model: asks the runtime's bash tool to run a fixed list of commands.

Usage: python3 mock_model.py <port> <commands.json>
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
COMMANDS = json.load(open(sys.argv[2]))
LOG = open(sys.argv[2] + ".requests.log", "a", encoding="utf-8")


def bash_tool_name(body):
    for tool in body.get("tools") or []:
        name = (tool.get("function") or {}).get("name") or tool.get("name") or ""
        if "bash" in name.lower():
            return name
    return "bash"


def tool_results_so_far(body):
    count = 0
    for m in body.get("messages", []):
        if m.get("role") == "tool":
            count += 1
        content = m.get("content")
        if isinstance(content, list):
            count += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_result")
    return count


def decide(body):
    n = tool_results_so_far(body)
    if n < len(COMMANDS):
        return bash_tool_name(body), {"command": COMMANDS[n]}
    return None, "probe complete"


def completion(body):
    name, payload = decide(body)
    if name is None:
        return {"role": "assistant", "content": payload}, "stop"
    call = {
        "id": f"call_{len(body.get('messages', []))}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(payload)},
    }
    return {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw or b"{}")
        LOG.write(json.dumps({"path": self.path, "stream": body.get("stream"),
                              "n_messages": len(body.get("messages", [])),
                              "last_role": (body.get("messages") or [{}])[-1].get("role")}) + "\n")
        LOG.flush()
        with open(sys.argv[2] + f".body{len(open(sys.argv[2] + '.requests.log').readlines())}.json", "w", encoding="utf-8") as dump:
            json.dump(body, dump, indent=1)
        if self.path.endswith("/v1/messages"):
            return self.anthropic(body)
        message, finish = completion(body)
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant"}
            if message.get("tool_calls"):
                call = message["tool_calls"][0]
                delta["tool_calls"] = [{"index": 0, **call}]
            elif message.get("content"):
                delta["content"] = message["content"]
            chunks = [
                {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body.get("model"),
                 "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body.get("model"),
                 "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
            ]
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        out = {"id": "x", "object": "chat.completion", "created": 0, "model": body.get("model"),
               "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def anthropic(self, body):
        name, payload = decide(body)
        usage = {"input_tokens": 1, "output_tokens": 1}
        if name is None:
            blocks, stop = [{"type": "text", "text": payload}], "end_turn"
        else:
            blocks = [{"type": "tool_use", "id": f"toolu_{tool_results_so_far(body)}", "name": name,
                       "input": payload}]
            stop = "tool_use"
        if not body.get("stream"):
            out = {"id": "msg_x", "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": blocks, "stop_reason": stop, "stop_sequence": None, "usage": usage}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        events = [("message_start", {"type": "message_start", "message": {
            "id": "msg_x", "type": "message", "role": "assistant", "model": body.get("model"),
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": usage}})]
        for i, block in enumerate(blocks):
            if block["type"] == "text":
                events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                       "content_block": {"type": "text", "text": ""}}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "text_delta", "text": block["text"]}}))
            else:
                events.append(("content_block_start", {"type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
                events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
            events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop,
                                        "stop_sequence": None}, "usage": {"output_tokens": 1}}))
        events.append(("message_stop", {"type": "message_stop"}))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event, data in events:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
