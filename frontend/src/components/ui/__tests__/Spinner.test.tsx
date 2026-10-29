// Spinner tests — uses design-token color, has a11y role.

import { render, screen } from "@testing-library/react";
import { Spinner } from "../Spinner";

describe("Spinner", () => {
  test("renders with role=status and accessible label", () => {
    render(<Spinner />);
    const el = screen.getByRole("status");
    expect(el).toHaveAttribute("aria-label", "加载中");
  });

  test("accepts a custom label", () => {
    render(<Spinner label="正在拉取数据" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "正在拉取数据");
  });

  test("applies size to inline style", () => {
    render(<Spinner size={48} />);
    const el = screen.getByRole("status");
    expect(el.style.width).toBe("48px");
    expect(el.style.height).toBe("48px");
  });
});
