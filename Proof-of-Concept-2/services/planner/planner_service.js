const http = require("http");
const crypto = require("crypto");

const PORT = Number(process.env.PLANNER_PORT || "8094");
const PROTOCOL_VERSION = "0.1";

function newUuid() {
  return crypto.randomUUID();
}

function makeError(code, message, parentMessageId, details = {}) {
  const body = {
    protocol_version: PROTOCOL_VERSION,
    message_id: newUuid(),
    intent: "error",
    payload: {
      error: {
        code,
        message,
        retryable: false,
        details,
      },
    },
    extensions: {},
  };
  if (parentMessageId) {
    body.extensions.trace = {
      parent_message_id: parentMessageId,
      depth: 0,
      path: [],
    };
  }
  return body;
}

function validateCore(message) {
  if (typeof message !== "object" || message === null || Array.isArray(message)) {
    return makeError("E_BAD_MESSAGE", "Message must be an object", null);
  }

  for (const field of ["protocol_version", "message_id", "intent", "payload"]) {
    if (!(field in message)) {
      return makeError("E_BAD_MESSAGE", `Missing required field: ${field}`, message.message_id || null);
    }
  }

  if (typeof message.protocol_version !== "string") {
    return makeError("E_BAD_MESSAGE", "protocol_version must be string", message.message_id || null);
  }
  if (typeof message.message_id !== "string") {
    return makeError("E_BAD_MESSAGE", "message_id must be string", message.message_id || null);
  }
  if (typeof message.intent !== "string") {
    return makeError("E_BAD_MESSAGE", "intent must be string", message.message_id || null);
  }
  if (typeof message.payload !== "object" || message.payload === null || Array.isArray(message.payload)) {
    return makeError("E_BAD_MESSAGE", "payload must be object", message.message_id || null);
  }
  if (
    "extensions" in message &&
    message.extensions !== null &&
    (typeof message.extensions !== "object" || Array.isArray(message.extensions))
  ) {
    return makeError("E_BAD_MESSAGE", "extensions must be object if present", message.message_id || null);
  }

  return null;
}

function ensureTrace(message, parentMessageId, hop) {
  if (!message.extensions || typeof message.extensions !== "object") {
    message.extensions = {};
  }
  if (!message.extensions.trace || typeof message.extensions.trace !== "object") {
    message.extensions.trace = {
      parent_message_id: parentMessageId || message.message_id,
      depth: 0,
      path: [],
    };
  }
  if (!Array.isArray(message.extensions.trace.path)) {
    message.extensions.trace.path = [];
  }
  message.extensions.trace.depth = Number(message.extensions.trace.depth || 0) + 1;
  if (hop) {
    message.extensions.trace.path.push(hop);
  }
}

function handlePlanner(message) {
  const validationError = validateCore(message);
  if (validationError) {
    return validationError;
  }

  if (message.intent !== "plan_route") {
    return makeError("E_NO_ROUTE", "planner.alpha only handles plan_route", message.message_id);
  }

  const missingCapability = message.payload.missing_capability;
  const original = message.payload.original_message || {};

  if (missingCapability !== "say_hi") {
    return makeError(
      "E_NO_ROUTE",
      `Planner could not map capability: ${String(missingCapability)}`,
      message.message_id,
      { missing_capability: missingCapability }
    );
  }

  const originalText =
    original && original.payload && typeof original.payload.text === "string"
      ? original.payload.text
      : "Hello!";

  const planned = {
    protocol_version: PROTOCOL_VERSION,
    message_id: newUuid(),
    intent: "echo",
    payload: {
      text: `Hi! ${originalText || "Hello!"}`,
    },
    extensions: {},
  };

  if (
    original.extensions &&
    typeof original.extensions === "object" &&
    original.extensions.identity &&
    typeof original.extensions.identity === "object"
  ) {
    planned.extensions.identity = original.extensions.identity;
  }

  ensureTrace(planned, original.message_id || message.message_id, "planner.alpha");
  return planned;
}

function sendJson(res, statusCode, body) {
  const payload = Buffer.from(JSON.stringify(body), "utf-8");
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Content-Length": payload.length,
  });
  res.end(payload);
}

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    sendJson(res, 200, { ok: true, service: "planner.alpha" });
    return;
  }

  if (req.method !== "POST" || req.url !== "/bdp") {
    sendJson(res, 404, { ok: false });
    return;
  }

  let raw = "";
  req.on("data", (chunk) => {
    raw += chunk;
  });

  req.on("end", () => {
    let message;
    try {
      message = JSON.parse(raw || "{}");
    } catch {
      sendJson(res, 200, makeError("E_BAD_MESSAGE", "Invalid JSON body", null));
      return;
    }

    const response = handlePlanner(message);
    sendJson(res, 200, response);
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`planner.alpha listening on :${PORT}`);
});
