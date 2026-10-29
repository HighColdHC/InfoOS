/** Feed pointer coordinates to `.spotlight` surfaces as --mx/--my custom properties. */
export function installSpotlight(): void {
  if (!window.matchMedia("(pointer: fine)").matches) return;
  let frame = 0;
  let last: PointerEvent | null = null;
  window.addEventListener("pointermove", (event) => {
    last = event;
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      const target = last?.target instanceof Element ? last.target.closest<HTMLElement>(".spotlight") : null;
      if (!target || !last) return;
      const rect = target.getBoundingClientRect();
      target.style.setProperty("--mx", `${last.clientX - rect.left}px`);
      target.style.setProperty("--my", `${last.clientY - rect.top}px`);
    });
  }, { passive: true });
}
