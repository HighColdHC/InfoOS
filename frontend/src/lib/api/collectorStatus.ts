import { getJson } from "./http";
import type { CollectorStatus } from "../types/collectorStatus";

export function loadCollectorStatus(signal?: AbortSignal): Promise<CollectorStatus> {
  return getJson<CollectorStatus>("/api/collector-status", signal);
}
