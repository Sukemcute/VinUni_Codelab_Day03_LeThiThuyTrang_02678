const chatFeed = document.querySelector("#chatFeed");
const traceFeed = document.querySelector("#traceFeed");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendButton = document.querySelector("#sendButton");
const resetButton = document.querySelector("#resetButton");
const engineBadge = document.querySelector("#engineBadge");
const runState = document.querySelector("#runState");
const metricSteps = document.querySelector("#metricSteps");
const metricTools = document.querySelector("#metricTools");
const metricStatus = document.querySelector("#metricStatus");
const charCount = document.querySelector("#charCount");
const panelSubtitle = document.querySelector(".panel-subtitle");
const modeButtons = [...document.querySelectorAll(".mode-button")];
const initialChat = chatFeed.innerHTML;

let mode = "agent";
let busy = false;
let controller = null;
let toolCalls = 0;
let stepCount = 0;
let currentFlights = [];
let typingMessage = null;

const formatPrice = (value) => `${new Intl.NumberFormat("vi-VN").format(value)} ₫`;

function setRunState(label, kind = "") {
  runState.className = `run-state ${kind}`;
  runState.lastChild.textContent = ` ${label}`;
  metricStatus.textContent = label;
}

function setBusy(value) {
  busy = value;
  sendButton.disabled = value;
  input.disabled = value;
  modeButtons.forEach((button) => { button.disabled = value; });
}

function scrollDown(element) {
  element.scrollTop = element.scrollHeight;
}

function makeMessage(role, answer, flights = [], isError = false) {
  const row = document.createElement("div");
  row.className = `message ${role === "user" ? "user-message" : "assistant-message"}${isError ? " message-error" : ""}`;
  if (role !== "user") {
    const avatar = document.createElement("div");
    avatar.className = "avatar assistant-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = "A";
    row.append(avatar);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  if (role !== "user") {
    const meta = document.createElement("div");
    meta.className = "message-meta";
    const name = document.createElement("strong");
    name.textContent = "AeroDesk";
    const time = document.createElement("span");
    time.textContent = new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    meta.append(name, time);
    content.append(meta);
  }
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = answer;
  content.append(bubble);

  if (flights.length) {
    const list = document.createElement("div");
    list.className = "flight-list";
    for (const flight of flights) {
      const card = document.createElement("div");
      card.className = "flight-card";
      const details = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${flight.flight_number} · ${flight.origin} → ${flight.destination}`;
      const subtitle = document.createElement("small");
      subtitle.textContent = `${flight.airline} · Khởi hành ${flight.departure_time}`;
      details.append(title, subtitle);
      const price = document.createElement("b");
      price.textContent = formatPrice(flight.price_vnd);
      card.append(details, price);
      list.append(card);
    }
    content.append(list);
  }
  row.append(content);
  chatFeed.append(row);
  scrollDown(chatFeed);
  return row;
}

function showTyping() {
  typingMessage = makeMessage("assistant", "Đang xử lý yêu cầu...");
  typingMessage.classList.add("typing-indicator");
  const bubble = typingMessage.querySelector(".message-bubble");
  bubble.textContent = "Agent đang làm việc ";
  const dots = document.createElement("span");
  dots.className = "typing-dots";
  for (let i = 0; i < 3; i++) dots.append(document.createElement("i"));
  bubble.append(dots);
}

function hideTyping() {
  typingMessage?.remove();
  typingMessage = null;
}

function updateMetrics() {
  metricSteps.textContent = String(stepCount);
  metricTools.textContent = String(toolCalls);
}

function makeTraceEntry(type, iteration) {
  document.querySelector("#traceEmpty")?.remove();
  const entry = document.createElement("article");
  entry.className = `trace-entry ${type}`;
  const icon = document.createElement("div");
  icon.className = "trace-entry-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = { thought: "✧", action: "↗", observation: "✓", final: "★" }[type];
  const body = document.createElement("div");
  body.className = "trace-entry-body";
  const heading = document.createElement("div");
  heading.className = "trace-entry-title";
  const label = document.createElement("strong");
  label.textContent = { thought: "THOUGHT", action: "ACTION · GỌI TOOL", observation: "OBSERVATION", final: "FINAL ANSWER" }[type];
  const step = document.createElement("span");
  step.textContent = iteration ? `Bước ${iteration}` : new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  heading.append(label, step);
  body.append(heading);
  entry.append(icon, body);
  traceFeed.append(entry);
  scrollDown(traceFeed);
  return body;
}

function appendText(body, value, className = "trace-text") {
  const element = document.createElement("p");
  element.className = className;
  element.textContent = value;
  body.append(element);
}

function appendJson(body, value, collapsible = false) {
  const code = document.createElement("pre");
  code.className = "trace-code";
  code.textContent = JSON.stringify(value, null, 2);
  if (collapsible) {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Xem dữ liệu JSON";
    details.append(summary, code);
    body.append(details);
  } else {
    body.append(code);
  }
}

function renderEvent(event) {
  if (event.type === "start") {
    setRunState("Đang xử lý", "running");
    return;
  }
  if (event.type === "done") {
    setRunState(
      event.status === "completed" || event.status === "success" ? "Hoàn thành" : event.status === "max_iterations_reached" ? "Đạt giới hạn" : "Có lỗi",
      event.status === "completed" || event.status === "success" ? "done" : "error"
    );
    return;
  }
  if (event.type === "thought") {
    stepCount = Math.max(stepCount, event.iteration || 1);
    updateMetrics();
    appendText(makeTraceEntry("thought", event.iteration), String(event.data));
    return;
  }
  if (event.type === "action") {
    stepCount = Math.max(stepCount, event.iteration || 1);
    toolCalls++;
    updateMetrics();
    const body = makeTraceEntry("action", event.iteration);
    const tool = document.createElement("span");
    tool.className = "tool-name";
    tool.textContent = event.data.name;
    body.append(tool);
    appendJson(body, event.data.args);
    return;
  }
  if (event.type === "observation") {
    const body = makeTraceEntry("observation", event.iteration);
    const data = event.data;
    if (Array.isArray(data)) {
      currentFlights = data.filter((item) => item && item.flight_number);
      appendText(body, data.length ? `Tìm thấy ${data.length} chuyến bay phù hợp.` : "Không có chuyến bay phù hợp.", "observation-summary");
    } else if (data && typeof data === "object" && "temperature_c" in data) {
      appendText(body, `${data.city} · ${data.temperature_c}°C · ${data.condition}. ${data.recommendation}`, "observation-summary");
    } else if (data && typeof data === "object" && data.error) {
      appendText(body, `Tool báo lỗi: ${data.error}`, "observation-summary");
    } else {
      appendText(body, typeof data === "string" ? data : "Đã nhận kết quả từ tool.", "observation-summary");
    }
    appendJson(body, data, true);
    return;
  }
  if (event.type === "final") {
    hideTyping();
    appendText(makeTraceEntry("final"), String(event.data));
    makeMessage("assistant", String(event.data), currentFlights, event.status === "error");
    if (event.status === "error") setRunState("Có lỗi", "error");
  }
}

async function readEvents(response) {
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: "Không thể gửi yêu cầu." }));
    throw new Error(error.error || "Không thể gửi yêu cầu.");
  }
  if (!response.body) throw new Error("Trình duyệt không hỗ trợ đọc luồng phản hồi.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) renderEvent(JSON.parse(line));
    }
    if (done) break;
  }
  if (buffer.trim()) renderEvent(JSON.parse(buffer.trim()));
}

async function sendMessage(message) {
  const trimmed = message.trim();
  if (!trimmed || busy) return;
  setBusy(true);
  currentFlights = [];
  toolCalls = 0;
  stepCount = 0;
  updateMetrics();
  traceFeed.replaceChildren();
  appendText(makeTraceEntry("thought"), "Đang nhận yêu cầu mới...");
  makeMessage("user", trimmed);
  input.value = "";
  input.style.height = "";
  charCount.textContent = "0 / 2000";
  showTyping();
  controller = new AbortController();
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: trimmed, mode, max_iterations: 5 }),
      signal: controller.signal,
    });
    traceFeed.replaceChildren();
    await readEvents(response);
  } catch (error) {
    if (error.name !== "AbortError") {
      hideTyping();
      makeMessage("assistant", `Không kết nối được với máy chủ: ${error.message}`, [], true);
      setRunState("Có lỗi", "error");
    }
  } finally {
    controller = null;
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener("input", () => {
  charCount.textContent = `${input.value.length} / 2000`;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});
chatFeed.addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (button && !busy) sendMessage(button.dataset.prompt);
});
modeButtons.forEach((button) => button.addEventListener("click", () => {
  if (busy) return;
  mode = button.dataset.mode;
  panelSubtitle.textContent = mode === "baseline"
    ? "Baseline trả lời một lượt, không gọi công cụ để tra cứu."
    : "Hỏi về chuyến bay, ngân sách và thời tiết điểm đến.";
  modeButtons.forEach((item) => {
    const selected = item === button;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
}));
resetButton.addEventListener("click", () => {
  controller?.abort();
  hideTyping();
  chatFeed.innerHTML = initialChat;
  traceFeed.innerHTML = `<div class="trace-empty" id="traceEmpty"><div class="empty-graphic" aria-hidden="true"><span>Thought</span><b>→</b><span>Action</span><b>→</b><span>Observation</span></div><h3>Chưa có hoạt động</h3><p>Gửi một câu hỏi để xem agent suy luận, gọi công cụ và tổng hợp câu trả lời.</p></div>`;
  toolCalls = 0;
  stepCount = 0;
  updateMetrics();
  setRunState("Sẵn sàng");
  input.value = "";
  charCount.textContent = "0 / 2000";
  input.focus();
});

fetch("/api/status")
  .then((response) => response.json())
  .then((status) => {
    engineBadge.classList.add("online");
    engineBadge.lastChild.textContent = status.mode === "nvidia" ? `NVIDIA · ${status.model}` : "Demo offline · dữ liệu mẫu";
    engineBadge.title = status.mode === "nvidia" ? status.model : "Không có NVIDIA API key hoặc LAB_MODE=local";
  })
  .catch(() => {
    engineBadge.lastChild.textContent = "Máy chủ chưa sẵn sàng";
  });
