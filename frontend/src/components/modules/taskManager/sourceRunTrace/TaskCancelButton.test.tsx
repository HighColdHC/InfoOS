import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskCancelButton } from "./TaskCancelButton";

afterEach(() => vi.unstubAllGlobals());

describe("任务取消", () => {
  it.each(["pending", "blocked", "running", "success", "failed", "skipped"])("%s 的按钮显隐符合状态合同", (status) => {
    render(<TaskCancelButton taskId="task-1" status={status} onCancelled={vi.fn()} />);
    expect(Boolean(screen.queryByRole("button", { name: "取消任务 task-1" }))).toBe(
      status === "pending" || status === "blocked",
    );
  });

  it("要求原因并提交精确任务，成功后刷新", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: "task-1", status: "skipped" })));
    vi.stubGlobal("fetch", fetch);
    const onCancelled = vi.fn();
    const user = userEvent.setup();
    render(<TaskCancelButton taskId="task-1" status="blocked" onCancelled={onCancelled} />);
    await user.click(screen.getByRole("button", { name: "取消任务 task-1" }));
    const submit = screen.getByRole("button", { name: "确认取消任务" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("取消原因"), { target: { value: "  已有后续成功  " } });
    await user.click(submit);
    await waitFor(() => expect(onCancelled).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledWith("/api/tasks/task-1/cancel", expect.objectContaining({
      method: "POST", body: JSON.stringify({ reason: "已有后续成功" }),
    }));
    expect(screen.queryByRole("button", { name: "取消任务 task-1" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("已取消：已有后续成功");
  });
});
