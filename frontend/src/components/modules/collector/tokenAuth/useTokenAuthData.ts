import { errorMessage } from "../../../../lib/format";
import { useEffect, useState } from "react";
import { isAbort } from "../../../../lib/isAbort";
import { fetchAdminTokens, fetchRequestLogDetail, fetchRequestLogs, fetchTokenOverview } from "../../../../lib/api/tokenAuth";
import type { RequestLogDetail, RequestLogPage, TokenAdminRecord, TokenOverview as TokenOverviewType } from "../../../../lib/types/tokenAuth";
import type { TokenTab } from "./TokenTabs";

const PAGE_SIZE = 25;

export function useTokenAuthData(tab: TokenTab) {
  const [overview, setOverview] = useState<TokenOverviewType | null>(null);
  const [tokens, setTokens] = useState<TokenAdminRecord[]>([]);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authRefresh, setAuthRefresh] = useState(0);
  const [logs, setLogs] = useState<RequestLogPage | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [logsRefresh, setLogsRefresh] = useState(0);
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [timeRange, setTimeRange] = useState("24h");
  const [offset, setOffset] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [requestDetail, setRequestDetail] = useState<RequestLogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "auth") return;
    const controller = new AbortController();
    let current = true;
    setAuthLoading(true);
    setAuthError(null);
    void Promise.all([fetchTokenOverview(controller.signal), fetchAdminTokens(controller.signal)])
      .then(([nextOverview, nextTokens]) => {
        if (current) {
          setOverview(nextOverview);
          setTokens(nextTokens);
        }
      })
      .catch((error: unknown) => {
        if (current && !isAbort(error)) setAuthError(errorMessage(error));
      })
      .finally(() => {
        if (current) setAuthLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [tab, authRefresh]);

  useEffect(() => {
    if (tab !== "requests") return;
    const controller = new AbortController();
    let current = true;
    setLogsLoading(true);
    setLogsError(null);
    void fetchRequestLogs({ query, status, time_range: timeRange, limit: PAGE_SIZE, offset }, controller.signal)
      .then((result) => {
        if (current) setLogs(result);
      })
      .catch((error: unknown) => {
        if (current && !isAbort(error)) setLogsError(errorMessage(error));
      })
      .finally(() => {
        if (current) setLogsLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [tab, query, status, timeRange, offset, logsRefresh]);

  useEffect(() => {
    if (!detailId) return;
    const controller = new AbortController();
    let current = true;
    setRequestDetail(null);
    setDetailLoading(true);
    setDetailError(null);
    void fetchRequestLogDetail(detailId, controller.signal)
      .then((result) => {
        if (current) setRequestDetail(result);
      })
      .catch((error: unknown) => {
        if (current && !isAbort(error)) setDetailError(errorMessage(error));
      })
      .finally(() => {
        if (current) setDetailLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [detailId]);
  return { overview, tokens, authLoading, authError, setAuthRefresh, logs, logsLoading, logsError,
    queryDraft, status, timeRange, offset, setLogsRefresh, setQuery, setQueryDraft, setStatus,
    setTimeRange, setOffset, requestDetail, detailLoading, detailError, setDetailId,
    setRequestDetail, setDetailError };
}
