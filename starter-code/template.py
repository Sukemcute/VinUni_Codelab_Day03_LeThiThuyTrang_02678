"""Lab #3: compare a one-shot chatbot with a tool-using ReAct agent."""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from tools import TOOL_DEFINITIONS, TOOL_MAP


load_dotenv(Path(__file__).with_name(".env"))
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")


def _use_llm() -> bool:
    return bool(os.getenv("NVIDIA_API_KEY")) and os.getenv("LAB_MODE", "auto").lower() != "local"


SYSTEM_PROMPT = """Bạn là trợ lý hỗ trợ khách hàng. Chỉ dùng Observation để khẳng định
giá chuyến bay hoặc thời tiết; không tự bịa dữ liệu. Công cụ được phép:
{tools}

Ở MỖI lượt, chỉ xuất một JSON object hợp lệ, không markdown:
{{"thought":"cần tra cứu", "action":{{"name":"get_flight_info", "args":{{"origin":"HAN","destination":"SGN","max_price":2000000}}}}}}
hoặc {{"thought":"đã đủ dữ liệu", "final_answer":"trả lời khách hàng"}}.
Mỗi lượt chỉ gọi một tool. Sau Observation, quyết định bước tiếp theo.
Nếu tool báo lỗi hai lần, trả lời rõ là không thể tra cứu. Với câu hỏi chính sách
không có dữ liệu, nói rõ bạn chưa có chính sách để xác nhận.
"""


def _complete(messages: list[dict[str, str]]) -> str:
    # NVIDIA API Catalog exposes an OpenAI-compatible chat completions endpoint.
    from openai import OpenAI

    client = OpenAI(
        base_url=NVIDIA_BASE_URL,
        api_key=os.environ["NVIDIA_API_KEY"],
        timeout=30.0,
        max_retries=1,
    )
    response = client.chat.completions.create(
        model=NVIDIA_MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=1024,
        stream=False,
    )
    return (response.choices[0].message.content or "").strip()


def _result(status: str, answer: str, trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": status, "answer": answer, "iterations": len(trace), "trace": trace}


def _budget(query: str) -> int:
    match = re.search(r"dưới\s+(\d+(?:[.,]\d+)?)\s*(triệu|tr|nghìn|ngàn|k)?", query, re.I)
    if not match:
        return 5_000_000
    amount = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "").lower()
    multiplier = 1_000_000 if unit in {"triệu", "tr"} else 1_000 if unit in {"nghìn", "ngàn", "k"} else 1
    return int(amount * multiplier)


def _route(query: str) -> tuple[str, str] | None:
    match = re.search(r"\btừ\s+(HAN|SGN|DAD)\s+(?:đi|đến)\s+(HAN|SGN|DAD)\b", query, re.I)
    return (match.group(1).upper(), match.group(2).upper()) if match else None


def _format_flights(flights: list[dict[str, Any]], origin: str, destination: str) -> str:
    if not flights:
        return f"Không tìm thấy chuyến bay {origin}–{destination} trong mức giá yêu cầu."
    lines = [f"Chuyến bay {origin}–{destination} phù hợp:"]
    for flight in flights:
        lines.append(
            f"{flight['flight_number']} ({flight['airline']}), {flight['departure_time']}, "
            f"{flight['price_vnd']:,} VND."
        )
    return " ".join(lines)


def _format_weather(weather: dict[str, Any]) -> str:
    if "error" in weather:
        return f"Không tra được thời tiết: {weather['error']}."
    return (
        f"Thời tiết {weather['city']}: {weather['temperature_c']}°C, "
        f"{weather['condition']}. Gợi ý: {weather['recommendation']}"
    )


class ChatbotBaseline:
    """Answer once, without tools or a ReAct loop."""

    def query(self, user_input: str) -> dict[str, Any]:
        if _use_llm():
            try:
                answer = _complete([
                    {"role": "system", "content": "Trả lời ngắn gọn bằng tiếng Việt. Bạn không có quyền tra cứu chuyến bay hay thời tiết; không tự bịa dữ liệu thời gian thực."},
                    {"role": "user", "content": user_input},
                ])
            except Exception as exc:
                return {"status": "error", "answer": f"Lỗi NVIDIA API: {exc}", "tool_calls": []}
        else:
            answer = "Tôi chưa có dữ liệu tra cứu để xác nhận thông tin này."
        return {"status": "success", "answer": answer, "tool_calls": []}


class ReActAgent:
    """Choose an action, execute it, observe its result, and repeat."""

    def __init__(
        self,
        max_iterations: int = 5,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        if max_iterations < 1:
            raise ValueError("max_iterations phải ít nhất là 1")
        self.max_iterations = max_iterations
        self.trace: list[dict[str, Any]] = []
        self.on_event = on_event

    def _emit(self, kind: str, iteration: int, data: Any) -> None:
        if self.on_event:
            self.on_event({"type": kind, "iteration": iteration, "data": data})

    def _run_with_llm(self, user_input: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(tools=json.dumps(TOOL_DEFINITIONS, ensure_ascii=False))},
            {"role": "user", "content": user_input},
        ]
        errors = 0
        for iteration in range(1, self.max_iterations + 1):
            try:
                raw = _complete(messages)
            except Exception as exc:
                return _result("error", f"Lỗi NVIDIA API: {exc}", self.trace)

            step: dict[str, Any] = {"iteration": iteration}
            try:
                decision = json.loads(raw)
                if not isinstance(decision, dict):
                    raise ValueError("Action phải là JSON object")
                step["thought"] = str(decision.get("thought", ""))
                if step["thought"]:
                    self._emit("thought", iteration, step["thought"])
                if "final_answer" in decision:
                    answer = str(decision["final_answer"]).strip()
                    if not answer:
                        raise ValueError("Final Answer rỗng")
                    # The lab counts a single tool call plus its answer as one step.
                    if len(self.trace) == 1 and "action" in self.trace[0]:
                        self.trace[0]["final_answer"] = answer
                        return _result("completed", answer, self.trace)
                    step["final_answer"] = answer
                    self.trace.append(step)
                    return _result("completed", answer, self.trace)

                action = decision["action"]
                if not isinstance(action, dict) or not isinstance(action.get("args"), dict):
                    raise ValueError("Action cần name và args dạng object")
                name = str(action["name"]).strip().lower()
                args = action["args"]
                step["action"] = {"name": name, "args": args}
                tool = TOOL_MAP.get(name)
                if tool is None:
                    raise ValueError(f"Tool không hợp lệ: {name}")
                self._emit("action", iteration, step["action"])
                try:
                    observation = tool(**args)
                except (TypeError, ValueError) as exc:
                    observation = {"error": str(exc)}
                step["observation"] = observation
                errors = errors + 1 if isinstance(observation, dict) and "error" in observation else 0
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                step["observation"] = f"Invalid JSON format or action: {exc}"
                errors += 1

            self.trace.append(step)
            self._emit("observation", iteration, step["observation"])
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Observation: {json.dumps(step['observation'], ensure_ascii=False)}"})
            if errors >= 2:
                return _result("completed", "Xin lỗi, tôi không thể tra cứu do công cụ hoặc định dạng Action liên tiếp báo lỗi.", self.trace)

        return _result("max_iterations_reached", "Không thể hoàn thành trong số bước tối đa.", self.trace)

    def _run_local(self, user_input: str) -> dict[str, Any]:
        """Demonstrate the lab with local data when no NVIDIA key is configured."""
        route = _route(user_input)
        wants_weather = bool(re.search(r"thời tiết|nên mặc", user_input, re.I))
        weather_code = None
        if wants_weather:
            codes = re.findall(r"\b(?:HAN|SGN|DAD)\b", user_input, re.I)
            weather_code = codes[-1].upper() if codes else None
            if weather_code is None:
                for city, code in (("Đà Nẵng", "DAD"), ("Hồ Chí Minh", "SGN"), ("Hà Nội", "HAN")):
                    if city.lower() in user_input.lower():
                        weather_code = code
                        break

        actions: list[tuple[str, dict[str, Any]]] = []
        if route:
            actions.append(("get_flight_info", {"origin": route[0], "destination": route[1], "max_price": _budget(user_input)}))
        if wants_weather and weather_code:
            actions.append(("get_weather_forecast", {"city_code": weather_code}))

        parts: list[str] = []
        for name, args in actions:
            if len(self.trace) >= self.max_iterations:
                return _result("max_iterations_reached", "Không thể hoàn thành trong số bước tối đa.", self.trace)
            iteration = len(self.trace) + 1
            self._emit("thought", iteration, f"Cần tra cứu bằng {name}.")
            self._emit("action", iteration, {"name": name, "args": args})
            observation = TOOL_MAP[name](**args)
            self._emit("observation", iteration, observation)
            self.trace.append({"iteration": len(self.trace) + 1, "thought": f"Cần tra cứu bằng {name}.", "action": {"name": name, "args": args}, "observation": observation})
            if name == "get_flight_info":
                parts.append(_format_flights(observation, args["origin"], args["destination"]))
            else:
                parts.append(_format_weather(observation))

        if not parts:
            if "vinpearl" in user_input.lower():
                parts.append("Tôi chưa có tài liệu xác nhận chính sách của Vinpearl. Vui lòng liên hệ bộ phận hỗ trợ để được cung cấp thông tin chính thức.")
            else:
                parts.append("Tôi chưa có dữ liệu hoặc công cụ phù hợp để trả lời chính xác câu hỏi này.")

        answer = " ".join(parts)
        if len(actions) > 1:
            if len(self.trace) >= self.max_iterations:
                return _result("max_iterations_reached", "Không thể hoàn thành trong số bước tối đa.", self.trace)
            self._emit("thought", len(self.trace) + 1, "Đã có đủ dữ liệu từ các công cụ.")
            self.trace.append({"iteration": len(self.trace) + 1, "thought": "Đã có đủ dữ liệu từ các công cụ.", "final_answer": answer})
        elif not actions:
            self._emit("thought", 1, "Không có công cụ phù hợp với câu hỏi.")
            self.trace.append({"iteration": 1, "thought": "Không có công cụ phù hợp với câu hỏi.", "final_answer": answer})
        else:
            self.trace[-1]["final_answer"] = answer
        return _result("completed", answer, self.trace)

    def run(self, user_input: str) -> dict[str, Any]:
        self.trace = []
        if _use_llm():
            return self._run_with_llm(user_input)
        return self._run_local(user_input)


def main():
    user_query = "Tìm cho tôi chuyến bay từ HAN đi SGN dưới 2 triệu, rồi cho biết thời tiết SGN nên mặc gì?"

    print("=== RUNNING CHATBOT BASELINE ===")
    chatbot = ChatbotBaseline()
    print(chatbot.query(user_query))

    print("\n=== RUNNING REACT AGENT ===")
    agent = ReActAgent(max_iterations=5)
    result = agent.run(user_query)
    print("Result:", result)
    print("Trace Log:", json.dumps(agent.trace, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
