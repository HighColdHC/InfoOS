import { useState } from "react";
import type { SourceRunTrace } from "../../../../lib/types/sourceRuns";

export function TaskEvidencePanel({
  discovery,
  onCopy,
}: {
  discovery: SourceRunTrace["discovery"];
  onCopy: (value: string, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const evidence = discovery.evidence;
  return (
    <section className="collector-task-evidence">
      <button type="button" className="collector-task-disclosure" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "收起 OpenCLI 请求证据" : "查看 OpenCLI 请求证据"}
      </button>
      {open ? (
        <div className="collector-task-evidence-body">
          {evidence.availability === "unavailable" ? (
            <p className="collector-task-unavailable">证据不可用</p>
          ) : (
            <>
              <dl>
                <div>
                  <dt>执行器</dt>
                  <dd>{evidence.executor ?? "unavailable"}</dd>
                </div>
                <div>
                  <dt>会话</dt>
                  <dd>{evidence.session_alias ?? "unavailable"}</dd>
                </div>
                <div>
                  <dt>脱敏参数</dt>
                  <dd>
                    <code>
                      {Array.isArray(evidence.sanitized_arguments)
                        ? evidence.sanitized_arguments.join(" ")
                        : (evidence.sanitized_arguments ?? "unavailable")}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>FETCH 任务</dt>
                  <dd>
                    <code>{evidence.fetch_task_id ?? "unavailable"}</code>
                    {evidence.fetch_task_id ? (
                      <button type="button" onClick={() => onCopy(evidence.fetch_task_id!, "任务 ID")}>
                        复制
                      </button>
                    ) : null}
                  </dd>
                </div>
              </dl>
              <details>
                <summary>链接明细（{evidence.links.length}）</summary>
                <ol>
                  {evidence.links.map((link) => (
                    <li key={link}>
                      <a href={link} target="_blank" rel="noreferrer">
                        {link}
                      </a>
                    </li>
                  ))}
                </ol>
              </details>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
