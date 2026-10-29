import { useContext, useEffect, useRef, type ReactNode } from "react";
import { BookOpenText, Compass, Moon, Sun } from "lucide-react";
import { BrandMark } from "../ui/BrandMark";
import { ThemeContext } from "../ui/ThemeProvider";
import { latestBriefDate } from "../../lib/briefDate";

/** Shared frame for the public reading pages: glass top bar with scroll progress. */
export function ReaderChrome({ active, children, wide = false }: { active?: "browse" | "brief" | "card" | "knowledge" | "opportunities"; children: ReactNode; wide?: boolean }) {
  const progress = useRef<HTMLSpanElement>(null);
  const theme = useContext(ThemeContext);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = max > 0 ? Math.min(1, window.scrollY / max) : 0;
      progress.current?.style.setProperty("--progress", String(ratio));
      document.documentElement.toggleAttribute("data-reader-scrolled", window.scrollY > 8);
    };
    const onScroll = () => { if (!frame) frame = window.requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <main className={`standalone-card-reader${wide ? " standalone-card-reader--wide" : ""}`}>
      <header className="reader-bar">
        <span className="reader-bar__progress" ref={progress} aria-hidden="true" />
        <div className="reader-bar__inner">
          <span className="reader-bar__brand"><BrandMark size={24} /><span>InfoOS</span></span>
          <nav className="reader-bar__nav" aria-label="阅读导航">
            <a className="card-reader__browse-link" href="/r/browse" aria-current={active === "browse" ? "page" : undefined}>
              <Compass size={15} aria-hidden="true" />漫步
            </a>
            <a href={`/r/brief/${latestBriefDate()}`} aria-current={active === "brief" ? "page" : undefined}>
              <BookOpenText size={15} aria-hidden="true" />日报
            </a>
            <a href="/r/knowledge" aria-current={active === "knowledge" ? "page" : undefined}>知识</a>
            <a href="/r/opportunities" aria-current={active === "opportunities" ? "page" : undefined}>商机</a>
            {theme ? (
              <button
                type="button"
                className="reader-bar__theme"
                aria-label="切换外观"
                onClick={theme.toggle}
              >
                {theme.theme === "dark" ? <Sun size={15} aria-hidden="true" /> : <Moon size={15} aria-hidden="true" />}
              </button>
            ) : null}
          </nav>
        </div>
      </header>
      <div className="card-reader__page">{children}</div>
    </main>
  );
}
