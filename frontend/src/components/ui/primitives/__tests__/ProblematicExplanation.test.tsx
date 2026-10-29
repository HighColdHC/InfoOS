import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProblematicExplanation } from "../ProblematicExplanation";
import { getStatusExplanation } from "../../../../lib/statusExplanation";

vi.mock("../../../../lib/statusExplanation", () => ({
  getStatusExplanation: vi.fn()
}));

const mockedExplanation = vi.mocked(getStatusExplanation);

describe("ProblematicExplanation", () => {
  it("renders nothing when status is null", () => {
    const { container } = render(
      <ProblematicExplanation status={null} blockKind="text" />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when status is undefined", () => {
    const { container } = render(
      <ProblematicExplanation status={undefined} blockKind="text" />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when status is empty string", () => {
    const { container } = render(
      <ProblematicExplanation status="" blockKind="text" />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders text variant by default", () => {
    mockedExplanation.mockReturnValue("Image not available");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="image"
        fallback="https://example.com/img.png"
      />
    );
    expect(screen.getByText("Image not available")).toBeInTheDocument();
    expect(screen.getByText("https://example.com/img.png")).toBeInTheDocument();
    const explanation = document.querySelector(".content-block-explanation");
    expect(explanation).toBeInTheDocument();
    const fallback = document.querySelector(".content-block-fallback");
    expect(fallback).toBeInTheDocument();
  });

  it("renders text variant when variant is explicitly 'text'", () => {
    mockedExplanation.mockReturnValue("Article not found");
    render(
      <ProblematicExplanation
        status="not_found"
        blockKind="linked_article"
        variant="text"
        fallback="cached excerpt"
      />
    );
    expect(screen.getByText("Article not found")).toBeInTheDocument();
    expect(screen.getByText("cached excerpt")).toBeInTheDocument();
    expect(document.querySelector(".content-block-explanation")).toBeInTheDocument();
    expect(document.querySelector(".content-block-fallback")).toBeInTheDocument();
  });

  it("renders media variant when variant is 'media'", () => {
    mockedExplanation.mockReturnValue("Image not available");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="image"
        variant="media"
      />
    );
    expect(screen.getByText("Image not available")).toBeInTheDocument();
    const placeholder = document.querySelector(".media-placeholder");
    expect(placeholder).toBeInTheDocument();
    const icon = document.querySelector(".media-placeholder-icon");
    expect(icon).toBeInTheDocument();
    expect(icon?.textContent).toBe("⚠");
  });

  it("does not render fallback when fallback is undefined", () => {
    mockedExplanation.mockReturnValue("Video not available");
    render(
      <ProblematicExplanation status="failed" blockKind="video" variant="text" />
    );
    expect(document.querySelector(".content-block-fallback")).toBeNull();
  });

  it("does not render fallback when fallback is empty string", () => {
    mockedExplanation.mockReturnValue("Video not available");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="video"
        variant="text"
        fallback=""
      />
    );
    expect(document.querySelector(".content-block-fallback")).toBeNull();
  });

  it("does not render media-fallback when fallback is empty", () => {
    mockedExplanation.mockReturnValue("Image not available");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="image"
        variant="media"
        fallback=""
      />
    );
    expect(document.querySelector(".media-fallback")).toBeNull();
  });

  it("calls getStatusExplanation with status and blockKind", () => {
    mockedExplanation.mockReturnValue("Content blocked");
    render(
      <ProblematicExplanation status="blocked" blockKind="repost_original" />
    );
    expect(mockedExplanation).toHaveBeenCalledWith("blocked", "repost_original");
  });

  it("applies custom className to text variant", () => {
    mockedExplanation.mockReturnValue("x");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="text"
        className="custom-class"
      />
    );
    const node = document.querySelector(".custom-class");
    expect(node).toBeInTheDocument();
    expect(node?.classList.contains("content-block-explanation")).toBe(false);
  });

  it("applies custom className to media variant", () => {
    mockedExplanation.mockReturnValue("x");
    render(
      <ProblematicExplanation
        status="failed"
        blockKind="image"
        variant="media"
        className="custom-media"
      />
    );
    const node = document.querySelector(".custom-media");
    expect(node).toBeInTheDocument();
    expect(node?.classList.contains("media-placeholder")).toBe(false);
  });
});
