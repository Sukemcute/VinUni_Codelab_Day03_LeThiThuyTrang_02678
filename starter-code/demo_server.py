"""Local web demo for the Lab #3 flight assistant.

Run: python starter-code/demo_server.py
"""

import argparse
import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from template import ChatbotBaseline, NVIDIA_MODEL, ReActAgent, _use_llm
from tools import TOOL_DEFINITIONS


FRONTEND_DIR = Path(__file__).with_name("frontend")


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/status":
            self._send_json(200, {
                "mode": "nvidia" if _use_llm() else "local",
                "model": NVIDIA_MODEL if _use_llm() else None,
                "tools": [{"name": tool["name"], "description": tool["description"]} for tool in TOOL_DEFINITIONS],
            })
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/chat":
            self._send_json(404, {"error": "Endpoint không tồn tại."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Nội dung yêu cầu quá dài hoặc rỗng.")
            payload = json.loads(self.rfile.read(length))
            message = payload.get("message", "")
            mode = payload.get("mode", "agent")
            max_iterations = payload.get("max_iterations", 5)
            if not isinstance(message, str) or not 0 < len(message.strip()) <= 2000:
                raise ValueError("Câu hỏi phải có từ 1 đến 2000 ký tự.")
            if mode not in {"agent", "baseline"}:
                raise ValueError("Chế độ không hợp lệ.")
            if type(max_iterations) is not int or not 1 <= max_iterations <= 8:
                raise ValueError("Giới hạn bước phải từ 1 đến 8.")
        except (ValueError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: dict) -> None:
            line = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
            self.wfile.write(line)
            self.wfile.flush()
            if not _use_llm() and event.get("type") in {"thought", "action", "observation"}:
                time.sleep(0.24)  # Make local tool steps visible during a classroom demo.

        try:
            emit({"type": "start", "mode": mode, "engine": "nvidia" if _use_llm() else "local"})
            if mode == "baseline":
                emit({"type": "thought", "iteration": 1, "data": "Chatbot trả lời một lượt, không gọi công cụ."})
                result = ChatbotBaseline().query(message.strip())
            else:
                agent = ReActAgent(max_iterations=max_iterations, on_event=emit)
                result = agent.run(message.strip())

            emit({"type": "final", "data": result["answer"], "status": result["status"]})
            emit({"type": "done", "status": result["status"], "iterations": result.get("iterations", 1)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                emit({"type": "final", "data": f"Lỗi máy chủ: {exc}", "status": "error"})
                emit({"type": "done", "status": "error", "iterations": 0})
            except (BrokenPipeError, ConnectionResetError):
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy giao diện demo ReAct Agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"Demo đang chạy tại http://{args.host}:{args.port}")
    print("Nhấn Ctrl+C để dừng.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
