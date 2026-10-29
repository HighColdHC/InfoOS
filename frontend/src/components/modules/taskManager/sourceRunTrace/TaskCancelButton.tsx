import { useId, useState } from "react";
import { cancelSourceRunTask } from "../../../../lib/api/sourceRuns";
import { Button } from "../../../ui/primitives/Button";

export function TaskCancelButton({ taskId, status, onCancelled }: {
  taskId: string;
  status: string;
  onCancelled: () => void;
}) {
  const reasonId = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  if (status !== "pending" && status !== "blocked") return null;
  if (cancelled) return <small role="status">已取消：{reason.trim()}</small>;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await cancelSourceRunTask(taskId, reason.trim());
      setCancelled(true);
      onCancelled();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  return <>
    <Button type="button" size="sm" variant="secondary" aria-label={`取消任务 ${taskId}`}
      disabled={busy} onClick={() => setOpen(!open)}>取消</Button>
    {open && <div role="group" aria-label={`取消任务原因 ${taskId}`}>
      <label htmlFor={reasonId}>取消原因</label>
      <input id={reasonId} value={reason} required disabled={busy}
        onChange={(event) => setReason(event.target.value)} />
      <Button type="button" size="sm" disabled={busy || !reason.trim()} onClick={() => void submit()}>
        {busy ? "提交中…" : "确认取消任务"}
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={() => setOpen(false)}>返回</Button>
      {error && <p role="alert">取消失败：{error}</p>}
    </div>}
  </>;
}
