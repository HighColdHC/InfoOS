export type AcquisitionEvidence = {
  completeness_status?: "complete" | "partial";
  requested_count?: number;
  observed_count?: number;
  stop_reason?: string | null;
};

export function stopReasonLabel(reason: string | null | undefined): string {
  if (reason === "cursor_not_reached") return "未衔接上次位置，两轮之间可能有内容未抓到";
  return reason ?? "停止原因未知";
}

export function partialAcquisitionLabel(evidence: AcquisitionEvidence): string | null {
  return evidence.completeness_status === "partial"
    ? `部分完成 ${evidence.observed_count ?? "未知"}/${evidence.requested_count ?? "未知"}`
    : null;
}
