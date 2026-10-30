import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MediaGallery } from "./MediaGallery";

describe("video gallery thumbnails", () => {
  it.each([true, false])("uses a thumbnail when available: %s", (hasThumbnail) => {
    const assets = [{ asset_id: "video", card_id: "card", asset_kind: "video", asset_url: "/original.mp4",
      thumbnail_url: hasThumbnail ? "/thumbnail.webp" : null }];
    const onPreview = vi.fn();
    const { container } = render(<MediaGallery assets={assets} onPreview={onPreview} />);
    expect(container.querySelector("img")?.getAttribute("src") ?? null).toBe(hasThumbnail ? "/thumbnail.webp" : null);
    expect(Boolean(container.querySelector("video"))).toBe(!hasThumbnail);
    if (!hasThumbnail) expect(container.querySelector("video")).toHaveAttribute("src", "/original.mp4");
    expect(container.querySelector(".collector-card-media-gallery__play")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览视频 video" }));
    expect(onPreview).toHaveBeenCalledWith(assets, 0);
  });
});
