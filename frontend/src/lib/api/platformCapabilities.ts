import type { PlatformCapability } from "../types/source";
import { getJson } from "./http";

export async function loadPlatformCapabilities(): Promise<PlatformCapability[]> {
  // 保留原 request.ts 的语义：null 表示不设超时。
  const payload = await getJson<{ platforms: PlatformCapability[] }>("/api/platform-capabilities", undefined, null);
  return payload.platforms;
}
