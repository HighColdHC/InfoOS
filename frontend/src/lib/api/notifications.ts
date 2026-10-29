import { getJson, postJson, sendJson } from "./http";
import type { FeishuConnection, NotificationPatch, NotificationSettings, QrRoundResponse } from "../types/notifications";
const root = "/api/settings/notifications";
export const loadNotifications = () => getJson<NotificationSettings>(root);
export const saveNotifications = (patch: NotificationPatch) => sendJson<NotificationSettings>(root, "PUT", patch);
export const testNotifications = () => postJson<{ results: { channel: string; status: string; error_code: string | null }[] }>(`${root}/test`);
export const sendRecoveryQr = (incidentId: string) => postJson<QrRoundResponse>(`/api/auth-recoveries/${encodeURIComponent(incidentId)}/qr-round`);

export const loadFeishuConnection = () => getJson<FeishuConnection>(`${root}/feishu/connect`);
export const connectFeishu = () => postJson<FeishuConnection>(`${root}/feishu/connect`);
export const cancelFeishuConnection = () => sendJson<FeishuConnection>(`${root}/feishu/connect`, "DELETE");
