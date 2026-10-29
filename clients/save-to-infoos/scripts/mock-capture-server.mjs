import { createServer } from "node:http";

const host = "127.0.0.1";
const port = Number(process.env.SAVE_TO_INFOOS_MOCK_PORT || 19123);
const token = "test-capture-token";
const seen = new Map();

const capabilities = {
  interface_version: "capture.v1",
  capabilities: ["captures:write", "url_only", "readable_content"],
  limits: {
    url_bytes: 4096,
    title_bytes: 2048,
    selected_text_bytes: 65536,
    rendered_content_bytes: 2097152
  },
  accepted_content_formats: ["readability_html"]
};

function json(response, status, payload) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*"
  });
  response.end(JSON.stringify(payload));
}

function fixture(response) {
  response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  response.end(`<!doctype html>
<html lang="zh-CN">
  <head>
    <title>Save to InfoOS 验收文章</title>
    <link rel="canonical" href="http://${host}:${port}/fixture">
  </head>
  <body>
    <main>
      <article>
        <h1>Save to InfoOS 验收文章</h1>
        <p>这是一段用于验证 URL 捕获和正文提取的文章内容。${"可读正文。".repeat(20)}</p>
        <form>
          <label>密码 <input type="password" value="must-not-leave-page"></label>
          <label>MFA <input value="123456"></label>
        </form>
        <img src="/cover.jpg" alt="示例封面">
      </article>
    </main>
  </body>
</html>`);
}

const server = createServer((request, response) => {
  if (request.url === "/fixture") {
    fixture(response);
    return;
  }
  if (request.headers.authorization !== `Bearer ${token}`) {
    json(response, 401, {
      code: "capture_token_invalid",
      message: "safe test response",
      request_id: "req_mock_auth"
    });
    return;
  }
  if (request.method === "GET" && request.url === "/api/capture/v1/capabilities") {
    json(response, 200, capabilities);
    return;
  }
  if (request.method === "POST" && request.url === "/api/capture/v1/items") {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const idempotencyKey = String(request.headers["idempotency-key"] || "");
      if (!idempotencyKey || idempotencyKey !== body.client_capture_id) {
        json(response, 422, {
          code: "idempotency_key_mismatch",
          message: "safe test response",
          request_id: "req_mock_invalid"
        });
        return;
      }
      const duplicate = seen.has(idempotencyKey);
      const captureId = seen.get(idempotencyKey) || `cap_mock_${seen.size + 1}`;
      seen.set(idempotencyKey, captureId);
      json(response, duplicate ? 200 : 202, {
        schema: "infoos.capture-receipt.v1",
        capture_id: captureId,
        status: duplicate ? "duplicate" : "accepted",
        disposition: duplicate ? "idempotent_replay" : "new",
        raw_item_id: `raw_${captureId}`,
        task_id: `task_${captureId}`,
        card_id: null,
        infoos_url: `/?menu=cards&capture_id=${captureId}`,
        request_id: `req_${captureId}`
      });
    });
    return;
  }
  json(response, 404, {
    code: "not_found",
    message: "safe test response",
    request_id: "req_mock_404"
  });
});

server.listen(port, host, () => {
  process.stdout.write(
    `Save to InfoOS mock ready at http://${host}:${port}\n` +
      `Fixture: http://${host}:${port}/fixture\n` +
      `Token: ${token}\n`
  );
});
