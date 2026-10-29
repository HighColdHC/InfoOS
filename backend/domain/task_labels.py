"""Stable task labels and display order."""

TASK_TYPE_LABELS: dict[str, str] = {
    "fetch": "抓取", "card_build": "构建卡片", "enrichment": "副文本化",
    "content_enrich": "文本富化", "media_download": "媒体下载",
    "image_understand": "图片理解", "video_extract_audio": "音频准备",
    "video_transcribe": "视频文稿提取", "context_pack": "上下文打包",
    "card_finalize": "卡片定稿", "card_llm_enrich": "来源模型处理",
    "webhook_deliver": "Webhook 通知",
    "summary": "AI 总结", "semantic_extract": "语义提取", "score": "价值评分",
    "decide": "决策路由", "output_generate": "输出生成", "cluster": "聚类",
    "daily_brief": "日报",
}

TASK_TYPE_ORDER: tuple[str, ...] = (
    "fetch", "card_build", "content_enrich", "media_download", "image_understand",
    "video_extract_audio", "video_transcribe", "card_finalize", "card_llm_enrich",
    "webhook_deliver", "context_pack", "summary",
    "semantic_extract", "score", "decide", "output_generate", "cluster",
    "daily_brief", "enrichment",
)
