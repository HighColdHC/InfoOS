import { Modal } from "../ui/Modal";
import { BrowserConsoleFrame } from "../ui/BrowserConsoleFrame";

export function BrowserConsoleModal({ url, onClose, description }: { url: string | null; onClose: () => void; description?: string }) {
  if (!url) return null;
  return <Modal
    open
    onClose={onClose}
    title="容器浏览器控制台"
    ariaLabel="容器浏览器控制台"
    width="min(1440px, 96vw)"
  >
    <div className="collector-browser-console-shell">
      <p>{description ?? "该窗口直接操作 InfoOS 容器内的专用 Chrome。完成登录后关闭窗口，再执行账号状态检查；空闲 10 分钟会自动关闭。"}</p>
      <BrowserConsoleFrame url={url} onExpired={onClose} />
    </div>
  </Modal>;
}
