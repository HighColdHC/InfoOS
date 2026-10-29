import type { MouseEvent } from "react";

export function cardReaderLinkProps(cardId: string, open: (cardId: string) => void) {
  return {
    href: `/r/card/${encodeURIComponent(cardId)}`,
    onClick(event: MouseEvent<HTMLAnchorElement>) {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      open(cardId);
    },
  };
}
