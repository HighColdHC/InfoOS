export function createHydrationBudget(limit) {
  const seconds = Math.min(540, Math.max(40, (Number(limit) || 1) * 40));
  return { deadlineMs: Date.now() + seconds * 1000 };
}

export function mergeFeishuDocument(row, document, feishuUrl, skipped = false) {
  const body = document.login_required ? '' : String(document.body || '');
  const content = [row.content, body].filter(Boolean).join('\n\n');
  const status = document.login_required ? 'login_required' : skipped ? 'partial'
    : !body ? 'empty' : document.complete ? 'fetched' : 'partial';
  return {
    ...row,
    content,
    full_text: content,
    original_content: row.content,
    external_doc_body: body,
    content_source: 'feishu',
    content_source_url: document.url || feishuUrl,
    content_source_title: document.title || '',
    content_source_status: status,
    content_source_complete: status === 'fetched',
    content_source_completeness: status === 'fetched' ? 'complete' : 'partial',
    content_source_blocks: document.blocks || 0,
    content_source_reached_end: Boolean(document.reached_end),
    images: [...new Set([...(row.images || []), ...(document.images || [])])],
    videos: [...new Set([...(row.videos || []), ...(document.videos || [])])],
    media_blobs: document.media_blobs || [],
  };
}
