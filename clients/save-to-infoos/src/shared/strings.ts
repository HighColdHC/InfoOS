import type { CaptureMode, CaptureOutcome, OutcomeKind } from "./types";

export const MODE_HELP: Record<CaptureMode, string> = {
  url_only: "InfoOS 将自行抓取正文、媒体并生成信息卡。",
  readable_content: "仅本次明确操作会读取克隆后的主要正文；不会读取表单、Cookie 或浏览器存储。"
};

const STATUS_COPY: Record<
  OutcomeKind,
  { label: string; message: string; tone: "success" | "warning" | "error" }
> = {
  accepted: {
    label: "已接收",
    message: "InfoOS 已接收这次捕获，并会在后台继续处理。",
    tone: "success"
  },
  duplicate: {
    label: "已存在",
    message: "InfoOS 已有相同内容或已识别为安全重试。",
    tone: "success"
  },
  unconfirmed: {
    label: "未确认",
    message: "没有拿到可靠回执。你可以使用同一个捕获编号重试。",
    tone: "warning"
  },
  rejected: {
    label: "被拒绝",
    message: "请求不符合 InfoOS 捕获接口要求。",
    tone: "error"
  },
  unauthorized: {
    label: "Token 无效",
    message: "捕获 Token 无效或已撤销，请到设置中替换。",
    tone: "error"
  },
  forbidden: {
    label: "权限不足",
    message: "请使用只包含 captures:write 权限的捕获 Token。",
    tone: "error"
  },
  too_large: {
    label: "正文过大",
    message: "当前正文超过 InfoOS 声明的大小限制，可以改用仅链接模式。",
    tone: "warning"
  },
  unreachable: {
    label: "服务不可达",
    message: "无法连接 InfoOS。请检查地址、网络或 SSH 隧道。",
    tone: "error"
  },
  not_configured: {
    label: "尚未配置",
    message: "请先填写 InfoOS 产品入口地址和捕获 Token。",
    tone: "warning"
  },
  page_inaccessible: {
    label: "页面不可读取",
    message: "Chrome 不允许扩展读取这个页面，或页面没有可读正文。",
    tone: "error"
  },
  invalid_response: {
    label: "回执无效",
    message: "InfoOS 返回了不符合 capture.v1 合同的响应。",
    tone: "error"
  }
};

export function outcomeCopy(outcome: CaptureOutcome): {
  label: string;
  message: string;
  tone: "success" | "warning" | "error";
} {
  const copy = STATUS_COPY[outcome.kind];
  if (outcome.safeCode === "retry_expired") {
    return {
      label: "重试已失效",
      message: "临时请求已被浏览器释放，请重新点击“交给 InfoOS”。",
      tone: "warning"
    };
  }
  if (outcome.safeCode === "downgrade_expired") {
    return {
      label: "降级已失效",
      message: "请切换为“仅链接”后重新提交。",
      tone: "warning"
    };
  }
  return copy;
}
