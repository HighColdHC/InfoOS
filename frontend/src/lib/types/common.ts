export type ContentBlockLayer = "original" | "media" | "context" | "derived" | "intelligence";

export const KNOWN_CONTENT_BLOCK_KINDS = [
 "analysis",
 "author_profile",
 "card_content",
 "enrichment",
 "enrichment_gap",
 "external_doc",
 "image",
 "image_description",
 "image_ocr",
 "intel_summary",
 "opportunity_breakdown",
 "opportunity_verification",
 "linked_article",
 "llm_context",
 "media_download_gap",
 "metadata",
 "original_tweet",
 "platform_comments",
 "quote",
 "quoted_tweet",
 "reddit_metrics",
 "reddit_outbound_link",
 "reddit_post",
 "reply_parent",
 "relation_note",
 "repost_original",
 "summary",
 "screening_evidence",
 "thread",
 "transcript",
 "translation",
 "tweet",
 "video",
 "video_transcript",
 "x_article",
] as const;

export type KnownContentBlockKind = (typeof KNOWN_CONTENT_BLOCK_KINDS)[number];

export type ContentBlockKind = KnownContentBlockKind | (string & {});

const knownContentBlockKinds: ReadonlySet<string> = new Set(KNOWN_CONTENT_BLOCK_KINDS);

export function isKnownContentBlockKind(value: string): value is KnownContentBlockKind {
 return knownContentBlockKinds.has(value);
}

export type BlockStatus =
 | "preserved"
 | "downloaded"
 | "enriched"
 | "pending"
 | "blocked"
 | "failed"
 | "cleaned"
 | "not_found"
 | "unsupported"
 | "depth_limited"
 | "body_fetched"
 | "done";

export type SetupStep =
 | "fresh"
 | "launch_chrome"
 | "install_extension"
 | "login_accounts"
 | "first_fetch"
 | "complete"
 | "skipped";

export type AccountStatus =
 | "unknown"
 | "needs_login"
 | "verified"
 | "skipped"
 | "failed";

export interface SetupAccountState {
 status: AccountStatus;
 last_checked: string | null;
 error: string | null;
 persisted: boolean;
 persistence_checked_at: string | null;
 persistence_stage: string | null;
 persistence_error: string | null;
}

export interface SetupPlatformVerificationResult {
 ok: boolean;
 platform: string;
 status: AccountStatus;
 last_checked: string | null;
 error: string | null;
 persisted: boolean;
 persistence_checked_at: string | null;
 persistence_stage: string | null;
}

export interface SetupState {
 version: number;
 current_step: SetupStep;
 completed_steps: string[];
 chrome: { binary_path: string | null; version: string | null; profile_dir: string | null; running: boolean };
 extension: { loaded: boolean; id: string | null; manifest_version: number | null; verified_at: string | null };
 session: { context_id: string | null; alias: string | null; bound_at: string | null };
 accounts: Partial<Record<string, SetupAccountState>>;
 first_fetch: { ok: boolean | null; fetched_count: number | null; ran_at: string | null };
 wizard_opens: number;
 wizard_last_seen_step: string | null;
}

export interface BrowserVisibility {
 headless_during_fetch: boolean;
}
