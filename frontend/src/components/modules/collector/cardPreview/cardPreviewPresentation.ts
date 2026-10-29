import type { ContentBlock } from "../../../../lib/types/card";

export function isTranscriptBlock(block: ContentBlock): boolean {
  return block.block_kind === "transcript" || block.block_kind === "video_transcript";
}

export function isOriginalBlock(block: ContentBlock): boolean {
  return block.layer !== "intelligence" && !isTranscriptBlock(block) && block.block_kind !== "translation";
}

export function bodyTranslationBlocks(blocks: ContentBlock[]): ContentBlock[] {
  return blocks.filter((block) => block.layer === "intelligence" && block.block_kind === "translation"
    && (blockPurpose(block) === "body_translation" || !blockPurpose(block)));
}

export function interpretationBlocks(blocks: ContentBlock[]): ContentBlock[] {
  return blocks.filter((block) => block.layer === "intelligence" && block.block_kind === "summary"
    && (blockPurpose(block) === "ai_interpretation" || !blockPurpose(block)));
}

export function transcriptTranslationFor(transcript: ContentBlock, blocks: ContentBlock[]): ContentBlock | undefined {
  const translations = blocks.filter((block) => block.layer === "intelligence"
    && block.block_kind === "translation" && blockPurpose(block) === "transcript_translation");
  return translations.find((block) => sourceBlockIds(block).includes(transcript.block_id))
    ?? (translations.length === 1 ? translations[0] : undefined);
}

function blockPurpose(block: ContentBlock): string | null {
  const purpose = block.raw_payload?.purpose;
  return typeof purpose === "string" ? purpose : null;
}

function sourceBlockIds(block: ContentBlock): string[] {
  const value = block.raw_payload?.source_block_ids;
  return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
}
