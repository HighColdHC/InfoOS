import { createServer, type Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  CaptureApiError,
  fetchCapabilities,
  submitCapture
} from "../src/shared/api";
import { buildCaptureRequest } from "../src/shared/payload";
import { capabilities, receipt } from "./fixtures";

let server: Server;
let baseUrl = "";
const receivedIds: string[] = [];

beforeAll(async () => {
  server = createServer((request, response) => {
    if (request.headers.authorization !== "Bearer test-token") {
      response.writeHead(401, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ code: "capture_token_invalid", message: "hidden" }));
      return;
    }
    if (request.url === "/api/capture/v1/capabilities") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(capabilities));
      return;
    }
    if (request.url === "/api/capture/v1/items" && request.method === "POST") {
      receivedIds.push(String(request.headers["idempotency-key"]));
      response.writeHead(202, { "Content-Type": "application/json" });
      response.end(JSON.stringify(receipt));
      return;
    }
    response.writeHead(404).end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("server_address_unavailable");
  }
  baseUrl = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve()))
  );
});

describe("capture API client", () => {
  it("checks capabilities without exposing the token in the URL", async () => {
    await expect(fetchCapabilities(baseUrl, "test-token")).resolves.toEqual(
      capabilities
    );
  });

  it("sends the same client ID in the header and body", async () => {
    const request = buildCaptureRequest({
      context: {
        url: "https://example.com/article",
        canonicalUrl: null,
        title: "Article",
        pageLanguage: "en",
        selectedText: null
      },
      mode: "url_only",
      renderedContent: null,
      capabilities,
      clientCaptureId: "cap_stable",
      clientVersion: "0.1.0"
    });
    await expect(submitCapture(baseUrl, "test-token", request)).resolves.toEqual(
      receipt
    );
    expect(receivedIds.at(-1)).toBe("cap_stable");
  });

  it("maps HTTP status and stable code without using server message text", async () => {
    await expect(fetchCapabilities(baseUrl, "wrong-token")).rejects.toMatchObject({
      kind: "unauthorized",
      safeCode: "capture_token_invalid",
      httpStatus: 401
    } satisfies Partial<CaptureApiError>);
  });

  it("turns AbortError into an explicit timeout", async () => {
    const fetchImpl: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError"))
        );
      });
    await expect(
      fetchCapabilities(baseUrl, "test-token", { fetchImpl, timeoutMs: 5 })
    ).rejects.toMatchObject({ kind: "timeout" });
  });
});
