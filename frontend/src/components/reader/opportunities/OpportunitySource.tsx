import { safeExternalUrl } from "../../../lib/cardPresentation";
import type { OpportunitySource as Source } from "../../../lib/types/opportunities";
import { PlatformMark } from "../../ui/PlatformMark";
import { dateText } from "./shared";

export function OpportunitySource({ source }: { source: Source }) {
  const original = safeExternalUrl(source.source_url);
  const profile = safeExternalUrl(source.author_url);
  return <div className="opportunity-source" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
    <div className="opportunity-source__account"><PlatformMark platform={source.platform} size="sm" /><strong>{source.author === null ? "账号未知" : source.author}</strong></div>
    <div className="opportunity-source__meta"><span>{source.source_name}</span>
      {source.published_at !== null ? <time dateTime={source.published_at}>{dateText(source.published_at)}</time> : null}
      {original !== null ? <a href={original} target="_blank" rel="noopener noreferrer">查看原帖</a> : null}
      {profile !== null ? <a href={profile} target="_blank" rel="noopener noreferrer">账号主页</a> : null}</div>
  </div>;
}
