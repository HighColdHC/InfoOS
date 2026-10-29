import { describe, expect, it } from "vitest";
import { resolveMediaUrl } from "../mediaUrl";

describe("resolveMediaUrl", () => {
  it("keeps web and API URLs unchanged", () => {
    expect(resolveMediaUrl("https://example.com/video.mp4")).toBe("https://example.com/video.mp4");
    expect(resolveMediaUrl("/api/assets/cards/card_a/video.mp4")).toBe("/api/assets/cards/card_a/video.mp4");
  });

  it("maps local asset paths to the API asset route", () => {
    expect(resolveMediaUrl("cards/card_a/video.mp4")).toBe("/api/assets/cards/card_a/video.mp4");
    expect(resolveMediaUrl("data/assets/cards/card_a/video.mp4")).toBe("/api/assets/cards/card_a/video.mp4");
    expect(resolveMediaUrl("/home/user/.infoos-test/data/assets/cards/card_a/video.mp4")).toBe(
      "/api/assets/cards/card_a/video.mp4",
    );
  });
});
