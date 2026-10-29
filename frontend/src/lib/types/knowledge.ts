export type KnowledgeLink = { knowledge_id: string; kind: string; title: string; deep_link: string };
export type KnowledgeListItem = KnowledgeLink & {
  kind_label: string; subtype: string; subtype_label: string; use_note: string;
  attribution_kind: string; attribution_ref: string | null; strength: number;
  research_status: string | null; origin_freshness: string;
  created_at: string; updated_at: string; evidence_count: number; has_conflict: boolean;
};
export type KnowledgeFacet = {
  kind: string; label: string; count: number;
  subtypes: { subtype: string; label: string; count: number }[];
};
export type KnowledgeLibraryPage = { items: KnowledgeListItem[]; next_cursor: string | null; facets: KnowledgeFacet[] };
export type KnowledgeLibraryQuery = { kind?: string; subtype?: string; research_status?: string; query?: string; limit?: number; cursor?: string };
export type FieldRule = { label: string; properties?: Record<string, FieldRule> };
export type FieldValue = string | number | null | string[] | Record<string, string | null>;
export type KnowledgeCard = KnowledgeLink & {
  kind_label: string; subtype: string; subtype_label: string;
  attribution_kind: string; attribution_ref: string | null; use_note: string;
  fields: Record<string, FieldValue>; field_schema: Record<string, FieldRule>;
  has_conflict: boolean; research_status: string | null;
  evidence: { evidence_id: string; card_id: string; title: string; platform: string;
    published_at: string | null; quotes: string[]; action: string; note: string | null; deep_link: string }[];
  related_cards: KnowledgeLink[];
  status_history: { event_id: string; value: string; note: string | null; actor: string; created_at: string }[];
};
export type KnowledgeBrief = {
  layout: "knowledge_v1"; brief_date: string; profile_version: number;
  counts: Record<string, number>; stock: Record<string, number>;
  items: { number: number; section: string; knowledge_id: string; kind: string; subtype: string;
    title: string; use_note: string; attribution_ref: string | null; strength: number;
    is_update: boolean; deep_link: string; origin_card_link: string }[];
};
export const KNOWLEDGE_LABELS: Record<string, string> = {
  project: "项目", opportunity: "商机", method: "方法", product: "产品", resource: "资源", topic: "选题", news: "资讯",
};
export const RESEARCH_LABELS: Record<string, string> = {
  pending: "待调研", researching: "用户调研", project: "立项", observe: "继续观察", drop: "放弃",
};
