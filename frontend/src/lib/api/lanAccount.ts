export type LanAccount = {
  username: string | null;
  requires_current_username: boolean;
  legacy_hash: boolean | null;
};
export type AccountChange = {
  current_username?: string;
  current_password: string;
  new_password?: string;
  confirm_password?: string;
  new_username?: string;
};
async function request<T>(path: string, body?: AccountChange): Promise<T> {
  const response = await fetch(`/api/lan-account${path}`, {
    method: body ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.redirected) throw new Error("登录已失效，请刷新页面重新登录");
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || "账号服务暂不可用");
  return result as T;
}
export const fetchLanAccount = () => request<LanAccount>("");
export const changeLanAccount = (action: "password" | "username", body: AccountChange) =>
  request<{ ok: boolean; message: string; login_url: string }>(`/${action}`, body);
export const returnToLogin = () => window.location.assign("/login/");
