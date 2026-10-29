import { stepStateLabels, type PipelineStep, type StepKey } from "./sourceRunPipelineViewModel";

export function PipelineSteps({ steps, mini = false, expandedSteps, onToggle }: {
  steps: PipelineStep[]; mini?: boolean; expandedSteps?: Set<string>; onToggle?: (key: StepKey) => void;
}) {
  return <ol className={`collector-pipeline-steps${mini ? " is-mini" : ""}`} aria-label={mini ? "逐篇处理流程" : "处理流程步骤"}>
    {steps.map((step, index) => {
      const content = <>
        <span className="collector-pipeline-number" aria-hidden="true">{step.state === "complete" ? "✓" : index + 1}</span>
        <div><strong>{step.label}</strong><span>{step.state === "unknown" ? <><span>证据</span>不可用</> : stepStateLabels[step.state]}</span>
          {!mini && index > 2 && step.state !== "not_executed" && <small>{step.complete}/{step.total} 篇{step.failed ? ` · 失败或阻塞 ${step.failed}` : ""}</small>}
        </div>
      </>;
      return <li key={step.key} className={`is-${step.state}`}>
        {onToggle ? <button type="button" aria-expanded={expandedSteps?.has(step.key) ?? false}
          onClick={() => onToggle(step.key)}>{content}</button> : content}
      </li>;
    })}
  </ol>;
}
