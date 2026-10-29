import { describe, expect, it } from "vitest";
import { stopReasonLabel } from "../acquisition";

describe("stopReasonLabel", () => {
  it.each([
    ["cursor_not_reached", "未衔接上次位置，两轮之间可能有内容未抓到"],
    ["unknown_reason", "unknown_reason"],
    [null, "停止原因未知"],
    [undefined, "停止原因未知"],
  ])("labels %s", (reason, expected) => {
    expect(stopReasonLabel(reason)).toBe(expected);
  });
});
