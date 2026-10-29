import type { SourceConfigurationPayload, SourceManagementItem } from "../../../../lib/types/sourceManagement";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";
import { SourceBasicFields } from "./SourceBasicFields";
import { SourcePlatformFields } from "./SourcePlatformFields";
import { SourceMediaAiFields } from "./SourceMediaAiFields";
import { normalizeFeedSettings } from "./sourceFormUtils";
import { useSourceForm } from "./useSourceForm";


type Props = {
  source: SourceManagementItem | null | undefined;
  busy: boolean;
  error: string | null;
  duplicateSourceId: string | null;
  onOpenDuplicate: () => void;
  onClose: () => void;
  onSave: (payload: SourceConfigurationPayload) => void;
};
export function SourceCreateEditModal({ source, busy, error, duplicateSourceId, onOpenDuplicate, onClose, onSave }: Props) {
  const open = source !== undefined;
  const editing = Boolean(source);
  const state = useSourceForm(source);
  const { form, catalogError } = state;
  if (!open) return null;

  return (
    <Modal
      open
      title={editing ? "编辑信息源" : "新增信息源"}
      ariaLabel={editing ? "编辑信息源" : "新增信息源"}
      onClose={onClose}
      width={680}
      busy={busy}
    >
      <form
        className="collector-source-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(
            normalizeFeedSettings({
              ...form,
              display_name: form.display_name.trim(),
              identifier: form.identifier.trim(),
            }),
          );
        }}
      >
        {catalogError ? (
          <p className="collector-source-form__error" role="alert">
            平台能力目录读取失败，请关闭后重试。
          </p>
        ) : null}
        {error ? (
          <div className="collector-source-form__error" role="alert">
            <span>{error}</span>
            {duplicateSourceId ? (
              <Button
                size="sm"
                onClick={onOpenDuplicate}
                disabled={busy}
                aria-label={`打开既有来源 ${duplicateSourceId}`}
              >
                打开既有来源
              </Button>
            ) : null}
          </div>
        ) : null}
        <SourceBasicFields state={state} editing={editing} busy={busy} />
        <SourcePlatformFields state={state} source={source} editing={editing} busy={busy} />
        <SourceMediaAiFields state={state} busy={busy} />
        <div className="collector-source-form__actions">
          <Button type="submit" variant="primary" loading={busy}>
            {editing ? "保存新配置版本" : "保存并首次采集"}
          </Button>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
        </div>
      </form>
    </Modal>
  );
}
