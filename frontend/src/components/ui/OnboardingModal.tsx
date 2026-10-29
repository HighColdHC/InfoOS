import { useState, type ReactElement } from "react";
import { Modal } from "./Modal";
import { Empty } from "./primitives/Empty";
import { Button } from "./primitives/Button";
import type { SetupState } from "../../lib/types";
import { loadSetupState, loadPlatformCapabilities } from "../../lib/api";
import { markOnboardingComplete } from "../../lib/onboarding";
import { useResource } from "./hooks/useResource";
import { SetupMode } from "./onboarding/SetupMode";

const TOUR_STEPS: { title: string; body: string; illustration: "brief" | "flow" | "library" }[] = [
  { title: "采集与信息卡", body: "从持续来源或 Save to InfoOS 捕获内容，整理为本地信息卡。", illustration: "brief" },
  { title: "agent 评估与日报", body: "外部 agent 依据关注画像经 /mcp 写回评估，InfoOS 汇总为日报。", illustration: "flow" },
  { title: "Web 管理与阅读", body: "在 Web 管理来源、任务、环境、Token 与导出，打开深链接阅读或轻量浏览信息卡。", illustration: "library" },
];

export function OnboardingModal({ onClose }: { onClose: () => void }): ReactElement {
  const [tourStep, setTourStep] = useState(0);
  const { data, setData } = useResource(async (signal) => {
    try {
      const [state, catalog] = await Promise.all([loadSetupState(), loadPlatformCapabilities()]);
      return {
        state,
        platforms: catalog.filter((entry) => entry.configurable && entry.requires_browser_session),
      };
    } catch (error) {
      if (!signal.aborted) console.error("[OnboardingModal] loadSetupState failed", error);
      throw error;
    }
  }, []);
  const state = data?.state;
  const platforms = data?.platforms ?? [];
  const setState = (next: SetupState) => setData((current) => (current ? { ...current, state: next } : current));

  if (!state) {
    return (
      <Modal open={true} title="欢迎使用 InfoOS" onClose={onClose}>
        <div className="onboarding">加载中...</div>
      </Modal>
    );
  }

  if (state.current_step !== "complete" && state.current_step !== "skipped") {
    return <SetupMode state={state} setState={setState} platforms={platforms} onClose={onClose} />;
  }
  return <TourMode step={tourStep} setStep={setTourStep} onClose={onClose} />;
}

function TourMode({
  step,
  setStep,
  onClose,
}: {
  step: number;
  setStep: (n: number) => void;
  onClose: () => void;
}): ReactElement {
  const current = TOUR_STEPS[step];
  const isLast = step === TOUR_STEPS.length - 1;
  const finish = () => {
    markOnboardingComplete();
    onClose();
  };
  return (
    <Modal open={true} title="欢迎使用 InfoOS" onClose={finish}>
      <div className="onboarding">
        <p className="onboarding-step">
          第 {step + 1} 步 / 共 {TOUR_STEPS.length} 步
        </p>
        <h3 className="onboarding-title">{current.title}</h3>
        <Empty text={current.body} illustration={current.illustration} />
        <div className="onboarding-dots">
          {TOUR_STEPS.map((_, i) => (
            <span key={i} className={`onboarding-dot ${i === step ? "is-active" : ""}`} />
          ))}
        </div>
        <div className="onboarding-actions">
          <Button variant="ghost" onClick={finish}>
            跳过
          </Button>
          <Button variant="primary" onClick={() => (isLast ? finish() : setStep(step + 1))}>
            {isLast ? "完成" : "下一步"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
