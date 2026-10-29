/** Display copy for persisted browser/account diagnostics, including older snapshots. */
const messages: Record<string, string> = {
  "Dedicated browser session is not configured": "当前实例未找到有效的专用浏览器会话记录",
  "Dedicated InfoOS Chrome profile is not running": "专用 Chrome 未运行",
  "Dedicated InfoOS OpenCLI profile is not bound": "专用 OpenCLI 配置未绑定",
  "Dedicated InfoOS OpenCLI profile is not connected": "专用 OpenCLI 配置未连接",
  "No Browser Bridge context is connected.": "Browser Bridge 扩展未连接",
  "Browser Bridge extension is not connected": "Browser Bridge 扩展未连接",
  "Chrome cookie database was not found in the dedicated profile": "专用浏览器中未找到登录状态数据库",
  "No login cookie was found in the dedicated profile": "未在专用浏览器中找到该平台的登录凭据",
  "Unsupported browser platform": "暂不支持检查该平台的登录状态",
  "Dedicated Chrome binding is not verified": "尚未确认专用 Chrome 的绑定状态",
  "Dedicated Chrome did not restart gracefully": "专用 Chrome 未能正常重启",
  "Restarted Chrome binding proof is invalid": "重启后专用 Chrome 的绑定凭据无效",
  "Functional login probe was not verified before restart": "重启前未能确认平台登录状态",
  "Passive login state was not logged in after restart": "重启后未找到平台登录凭据",
  "Functional login probe was not verified after restart": "重启后未能确认平台登录状态",
  "Passive login state is logged out": "未在专用浏览器中找到该平台的登录凭据",
  "Login persistence verification failed": "登录持久化验证失败",
  "Browser verification is busy; try again": "浏览器正在验证，请稍后再试",
  "Install the OpenCLI Browser Bridge extension in InfoOS Chrome, then click Verify again.": "请在专用 Chrome 中安装 OpenCLI Browser Bridge 扩展，然后重新检查连接",
};

export function browserStatusMessage(message: string): string {
  if (messages[message]) return messages[message];
  if (message.startsWith("auth_required:")) return "尚未确认该平台在专用浏览器中的登录状态（auth_required）";
  if (message.startsWith("Could not read Chrome cookie database:")) return "无法读取专用浏览器的登录状态";
  if (/[\u3400-\u9fff]/u.test(message)) return message;
  return "浏览器或平台状态检查未通过，请检查专用浏览器会话";
}
