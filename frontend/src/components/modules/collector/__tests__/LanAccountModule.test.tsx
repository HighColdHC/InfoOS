import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LanAccountModule } from "../LanAccountModule";
import * as api from "../../../../lib/api/lanAccount";

vi.mock("../../../../lib/api/lanAccount", () => ({
  fetchLanAccount: vi.fn(), changeLanAccount: vi.fn(), returnToLogin: vi.fn(),
}));
function fill(form: HTMLElement, label: string, value: string) {
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchLanAccount).mockResolvedValue({ username: "tester", requires_current_username: false, legacy_hash: true });
  vi.mocked(api.changeLanAccount).mockResolvedValue({ ok: true, message: "修改成功，请重新登录", login_url: "/login/" });
});
describe("LAN account forms", () => {
  it("shows legacy hint and validates before submitting", async () => {
    render(<LanAccountModule />);
    expect(await screen.findByText("建议修改一次密码以升级加密方式")).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "修改密码" });
    fill(form, "当前密码", "old-password-only");
    fill(form, "新密码", "replacement-secret");
    fill(form, "确认新密码", "different-secret");
    fireEvent.submit(form);
    expect(screen.getByRole("alert")).toHaveTextContent("不一致");
    expect(api.changeLanAccount).not.toHaveBeenCalled();
    fill(form, "确认新密码", "replacement-secret");
    fireEvent.submit(form);
    await screen.findByRole("status");
    expect(api.changeLanAccount).toHaveBeenCalledWith("password", {
      current_username: "tester", current_password: "old-password-only",
      new_password: "replacement-secret", confirm_password: "replacement-secret",
    });
    expect(within(form).getByLabelText("当前密码")).toHaveValue("");
    await waitFor(() => expect(api.returnToLogin).toHaveBeenCalled(), { timeout: 2500 });
  });
  it("submits an email account and displays server errors", async () => {
    vi.mocked(api.changeLanAccount).mockRejectedValue(new Error("新账户名不可用"));
    render(<LanAccountModule />);
    const form = await screen.findByRole("form", { name: "修改账户名" });
    fill(form, "当前密码", "old-password-only");
    fill(form, "新账户名", "bad:name");
    fireEvent.submit(form);
    expect(api.changeLanAccount).not.toHaveBeenCalled();
    fill(form, "新账户名", "name@example.com");
    fireEvent.submit(form);
    expect(await screen.findByRole("alert")).toHaveTextContent("新账户名不可用");
    expect(api.changeLanAccount).toHaveBeenCalledWith("username", {
      current_username: "tester", current_password: "old-password-only", new_username: "name@example.com",
    });
  });
  it("requires a current account name for multi-user databases", async () => {
    vi.mocked(api.fetchLanAccount).mockResolvedValue({ username: null, requires_current_username: true, legacy_hash: null });
    render(<LanAccountModule />);
    const form = await screen.findByRole("form", { name: "修改账户名" });
    fill(form, "当前密码", "old-password-only");
    fill(form, "新账户名", "another@example.com");
    fireEvent.submit(form);
    expect(api.changeLanAccount).not.toHaveBeenCalled();
    fill(form, "当前账户名", "tester");
    fireEvent.submit(form);
    await screen.findByRole("status");
    expect(api.changeLanAccount).toHaveBeenCalled();
  });
});
