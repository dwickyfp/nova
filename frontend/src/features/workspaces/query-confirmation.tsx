import type { QuerySnapshot } from "./query-snapshot";
import { useEffect } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useIsNarrowForAssistant } from "@/features/assistant/use-assistant-panel";

export function QueryConfirmationDialog({
  pending,
  onCancel,
  onConfirm,
  onAssistantOpenChange,
}: {
  pending: QuerySnapshot | null;
  onCancel: () => void;
  onConfirm: (snapshot: QuerySnapshot) => void;
  onAssistantOpenChange?: (open: boolean) => void;
}) {
  const narrow = useIsNarrowForAssistant();
  useEffect(() => {
    if (pending && narrow) onAssistantOpenChange?.(false);
  }, [pending, narrow, onAssistantOpenChange]);

  return (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
      title="Run destructive query?"
      desc="This query may change or delete data. Review the SQL before continuing."
      confirmText="Run query"
      destructive
      handleConfirm={() => {
        if (pending) onConfirm(pending);
      }}
    />
  );
}
