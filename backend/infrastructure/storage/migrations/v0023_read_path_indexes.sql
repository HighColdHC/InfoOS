create index idx_tasks_queue_created on tasks(queue_name, created_at desc, task_id);

create index idx_card_content_blocks_degraded_flags on card_content_blocks(card_id, status, provenance)
where (instr(lower(coalesce(provenance, '')), 'metadata_fallback') > 0
       or lower(status) in ('failed', 'error', 'blocked', 'pending'));

create index idx_card_evaluations_passed_relevance on card_evaluations(passed_quality, relevance desc, quality desc);
