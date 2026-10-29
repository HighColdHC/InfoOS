import { render, screen, fireEvent } from "@testing-library/react";
import { HeaderBar } from "../HeaderBar";

describe("HeaderBar", () => {
  it("renders title without eyebrow", () => {
    render(<HeaderBar title="信息流" status="sync" onRefresh={vi.fn()} />);
    expect(screen.queryByText("INFOOS")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "信息流" })).toBeInTheDocument();
  });

  it("shows '已连接本地服务' in sync state", () => {
    render(<HeaderBar title="Y" status="sync" onRefresh={vi.fn()} />);
    expect(screen.getByText("已连接本地服务")).toBeInTheDocument();
  });

  it("shows loading pill (badge--warning) in loading state", () => {
    const { container } = render(
      <HeaderBar title="Y" status="loading" onRefresh={vi.fn()} />
    );
    expect(container.querySelector(".status-pill")).toBeInTheDocument();
    expect(container.querySelector(".badge--warning")).toBeInTheDocument();
  });

  it("shows error pill (badge--danger) in error state", () => {
    const { container } = render(
      <HeaderBar title="Y" status="error" errorMessage="boom" onRefresh={vi.fn()} />
    );
    expect(container.querySelector(".status-pill")).toBeInTheDocument();
    expect(container.querySelector(".badge--danger")).toBeInTheDocument();
    expect(screen.getByText("数据异常")).toBeInTheDocument();
  });

  it("calls onRefresh when refresh button clicked", () => {
    const onRefresh = vi.fn();
    render(<HeaderBar title="Y" status="sync" onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("renders rightSlot content", () => {
    render(
      <HeaderBar
        title="Y" status="sync" onRefresh={vi.fn()}
        rightSlot={<button>Switch</button>}
      />
    );
    expect(screen.getByRole("button", { name: "Switch" })).toBeInTheDocument();
  });
});
