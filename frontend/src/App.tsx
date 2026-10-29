import { lazy, Suspense } from "react";
import { KnowledgeReaderPage } from "./components/reader/KnowledgeReaderPage";
import { KnowledgeLibraryPage } from "./components/reader/knowledge/KnowledgeLibraryPage";
import { CardReaderPage } from "./components/reader/CardReaderPage";
import { BriefReaderPage } from "./components/reader/BriefReaderPage";
import { BrowseReaderPage } from "./components/reader/BrowseReaderPage";

const OpportunityBoardPage = lazy(() => import("./components/reader/opportunities/OpportunityBoardPage").then(module => ({ default: module.OpportunityBoardPage })));
const OpportunityStatsPage = lazy(() => import("./components/reader/opportunities/OpportunityStatsPage").then(module => ({ default: module.OpportunityStatsPage })));

const ManagementApp = lazy(() => import("./ManagementApp"));
// Reading routes render their shell immediately; Markdown remains lazy inside ReaderBlock.

export default function App({ refreshIntervalMs = 30_000 }: { refreshIntervalMs?: number } = {}) {
  const path = window.location.pathname;
  const page = /^\/r\/browse\/?$/.test(path) ? <BrowseReaderPage />
    : /^\/r\/opportunities\/stats\/?$/.test(path) ? <OpportunityStatsPage />
    : /^\/r\/opportunities\/?$/.test(path) ? <OpportunityBoardPage />
    : /^\/r\/knowledge\/?$/.test(path) ? <KnowledgeLibraryPage />
    : path.startsWith("/r/knowledge/") ? <KnowledgeReaderPage />
    : path.startsWith("/r/card/") ? <CardReaderPage />
    : path.startsWith("/r/brief/") ? <BriefReaderPage />
    : <ManagementApp refreshIntervalMs={refreshIntervalMs} />;
  return <Suspense fallback={<div className="empty" role="status"><p className="empty__description">正在加载…</p></div>}>{page}</Suspense>;
}
