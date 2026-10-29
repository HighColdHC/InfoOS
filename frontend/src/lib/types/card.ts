import type { ContentBlockKind, ContentBlockLayer, BlockStatus } from "./common";

export type RawBlockAuthor = {
 name?: string | null;
 handle?: string | null;
 url?: string | null;
};

export type RawBlockPayload = {
 quoted_author?: RawBlockAuthor | null;
 quoted_at?: string | null;
 asset_paths?: string[];
 media_kind?: string | null;
 metrics?: Record<string, number | string | null | undefined> | null;
 embedded_in_block_id?: string | null;
 reposted_by?: RawBlockAuthor | null;
 nested_quote?: {
 text?: string | null;
 url?: string | null;
 author?: RawBlockAuthor | null;
 } | null;
 [key: string]: unknown;
};

export type ContentBlock = {
 block_id: string;
 card_id: string;
 layer: ContentBlockLayer;
 block_kind: ContentBlockKind;
 content: string;
 body?: string | null;
 source_url: string | null;
 status: BlockStatus;
 sort_order: number;
 provenance: string | null;
 raw_payload?: RawBlockPayload | null;
};
