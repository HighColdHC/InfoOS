import type { CaptureCapabilities, CaptureReceipt } from "../src/shared/types";

export const capabilities: CaptureCapabilities = {
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

export const receipt: CaptureReceipt = {
  schema: "infoos.capture-receipt.v1",
  capture_id: "cap_server_1",
  status: "accepted",
  disposition: "new",
  raw_item_id: "raw_1",
  task_id: "task_1",
  card_id: null,
  infoos_url: "/?menu=cards&capture_id=cap_server_1",
  request_id: "req_1"
};
