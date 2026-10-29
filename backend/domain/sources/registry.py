"""Canonical platform and source capability registry.

This module contains product capability facts only.  Infrastructure and UI
layers consume projections of this registry instead of maintaining platform
lists of their own.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.domain.calibration.matrix import (
    LOGIN_NOT_APPLICABLE,
    LOGIN_VERIFIABLE,
    NO_LOGIN,
    OPTIONAL_LOGIN,
    REQUIRED_LOGIN,
)
from backend.domain.sources.registry_types import CatalogItem


@dataclass(frozen=True)
class SourceTypeCapability:
    source_type: str
    initial_status: str
    default_cadence: str
    default_max_items_per_run: int | None = 20
    label: str | None = None
    identifier_hint: str | None = None
    identifier_options: tuple[tuple[str, str], ...] = ()
    requires_login: str | None = None
    login_verifiable: str | None = None
    max_items_limit: int | None = None
    research_capable: bool = False


@dataclass(frozen=True)
class ResearchCapability:
    """Platform-level research contract, independent from maintained sources."""

    status: str
    mode: str
    max_items_limit: int
    supports_date_range: bool = True
    requires_source_selection: bool = False
    limitation: str | None = None
    sort_options: tuple[str, ...] = ("latest",)
    default_sort: str = "latest"


@dataclass(frozen=True)
class QrLoginCapability:
    qr_ttl_seconds: int
    scan_app_label: str


@dataclass(frozen=True)
class PlatformDefinition:
    platform: str
    label: str
    source_types: tuple[SourceTypeCapability, ...]
    cadence_options: tuple[float, ...]
    requires_login: str = NO_LOGIN
    login_verifiable: str = LOGIN_NOT_APPLICABLE
    requires_browser_session: bool = False
    opencli_tab_policy: bool = False
    max_items_limit: int = 100
    research: ResearchCapability | None = None
    qr_login: QrLoginCapability | None = None

    @property
    def can_verify_login(self) -> bool:
        return self.login_verifiable == LOGIN_VERIFIABLE

    @property
    def can_fetch(self) -> bool:
        return any(source.initial_status != "unsupported" for source in self.source_types)


def _source(
    source_type: str,
    cadence: str,
    max_items: int | None = 20,
    *,
    label: str | None = None,
    identifier_hint: str | None = None,
    identifier_options: tuple[tuple[str, str], ...] = (),
    requires_login: str | None = None,
    login_verifiable: str | None = None,
    max_items_limit: int | None = None,
    research_capable: bool = False,
) -> SourceTypeCapability:
    return SourceTypeCapability(
        source_type=source_type,
        initial_status="implemented_uncalibrated",
        default_cadence=cadence,
        default_max_items_per_run=max_items,
        label=label,
        identifier_hint=identifier_hint,
        identifier_options=identifier_options,
        requires_login=requires_login,
        login_verifiable=login_verifiable,
        max_items_limit=max_items_limit,
        research_capable=research_capable,
    )


_ZSXQ_TABS = (
    ("all", "全部"),
    ("ai", "AI"),
    ("xiaohongshu", "小红书"),
    ("douyin", "抖音"),
    ("gongzhonghao", "公众号"),
)


_BROWSER = {
    "requires_login": REQUIRED_LOGIN,
    "login_verifiable": LOGIN_VERIFIABLE,
    "requires_browser_session": True,
    "opencli_tab_policy": True,
}

_NATIVE_RESEARCH = ResearchCapability(
    status="available",
    mode="keyword_search",
    max_items_limit=100,
)

PLATFORM_REGISTRY: dict[str, PlatformDefinition] = {
    # Auxiliary login capability. Google is not a configurable source.
    "google": PlatformDefinition("google", "Google", (), (), login_verifiable=LOGIN_VERIFIABLE, requires_browser_session=True),
    "x": PlatformDefinition(
        "x", "X",
        (
            _source("creator", "1h", 50, label="账号", identifier_hint="账号用户名，例如 openai"),
            _source("list", "30min", 50, label="列表", identifier_hint="X 列表 ID"),
            _source(
                "home_feed", "1h", label="首页 Feed",
                identifier_hint="读取当前 InfoOS 专用 Chrome 中已登录 X 账号的首页流",
                identifier_options=(("for-you", "为你推荐"), ("following", "正在关注")),
            ),
        ),
        (.25, .5, 1, 5, 6, 24), research=_NATIVE_RESEARCH, **_BROWSER,
    ),
    "youtube": PlatformDefinition(
        "youtube", "YouTube",
        (
            _source("channel", "6h", label="频道", identifier_hint="频道 @handle、UC ID 或频道链接"),
            _source("keyword", "6h", label="关键词", identifier_hint="用于搜索视频的关键词"),
            _source(
                "home_feed", "6h", label="首页 Feed",
                identifier_hint="读取当前 InfoOS 专用 Chrome 中已登录 YouTube 账号的首页推荐",
                identifier_options=(("recommended", "推荐"),),
                requires_login=REQUIRED_LOGIN,
                login_verifiable=LOGIN_VERIFIABLE,
            ),
        ),
        (.5, 1, 5, 6, 24),
        requires_login=OPTIONAL_LOGIN,
        login_verifiable=LOGIN_VERIFIABLE,
        requires_browser_session=True,
        opencli_tab_policy=True,
        research=ResearchCapability(
            status="available",
            mode="keyword_search",
            max_items_limit=50,
        ),
    ),
    "rss": PlatformDefinition(
        "rss",
        "RSS",
        (_source("feed", "30min", None, label="订阅地址", identifier_hint="完整的 RSS/Atom URL"),),
        (.5, 1, 5, 24),
        research=ResearchCapability(
            status="conditional",
            mode="selected_sources",
            max_items_limit=200,
            requires_source_selection=True,
            limitation="RSS 没有全站搜索，必须选择已有 RSS 来源后在可见条目内匹配关键词。",
        ),
    ),
    "reddit": PlatformDefinition(
        "reddit", "Reddit",
        (
            _source("subreddit", "1h", label="社区", identifier_hint="subreddit 名称，例如 LocalLLaMA"),
            _source("keyword", "1h", label="关键词"),
            _source(
                "home_feed", "1h", label="首页 Feed",
                identifier_hint="读取当前 InfoOS 专用 Chrome 中已登录 Reddit 账号的个性化 Best",
                identifier_options=(("best", "最佳"),),
            ),
        ),
        (.5, 1, 5, 24), research=_NATIVE_RESEARCH, **_BROWSER,
    ),
    "github": PlatformDefinition(
        "github",
        "GitHub",
        (_source("releases", "5h", label="版本发布", identifier_hint="owner/repository"), _source("commits", "5h", label="提交记录", identifier_hint="owner/repository")),
        (.5, 1, 5, 24),
        research=ResearchCapability(
            status="available",
            mode="repository_search",
            max_items_limit=100,
            limitation="首版把仓库作为调研对象，不扩展到 Issue、PR 或代码搜索。",
        ),
    ),
    "hackernews": PlatformDefinition(
        "hackernews",
        "Hacker News",
        (_source("feed", "30min", label="榜单", identifier_options=tuple((value, value) for value in ("top", "new", "best", "ask", "show", "jobs"))), _source("keyword", "30min", label="关键词")),
        (.5, 1, 5, 24),
        research=ResearchCapability(
            status="available",
            mode="keyword_search",
            max_items_limit=100,
            limitation="通过 Hacker News 的公开 Algolia 索引检索 story。",
        ),
    ),
    "producthunt": PlatformDefinition(
        "producthunt",
        "Product Hunt",
        (_source("feed", "5h", label="榜单", identifier_options=(("today", "今日榜单"), ("posts", "最新产品"))), _source("category", "5h", label="分类", identifier_hint="Product Hunt 分类 slug")),
        (.5, 1, 5, 24),
        research=ResearchCapability(
            status="limited",
            mode="visible_feed_filter",
            max_items_limit=50,
            supports_date_range=False,
            limitation="公开适配器没有关键词搜索；仅在当前可见产品 Feed 内匹配，覆盖度不完整。",
        ),
    ),
    "xiaohongshu": PlatformDefinition(
        "xiaohongshu", "小红书",
        (
            _source("keyword", "5h", label="关键词"),
            _source("creator", "5h", label="创作者", identifier_hint="创作者主页链接或用户 ID"),
            _source(
                "home_feed", "5h", label="首页 Feed",
                identifier_hint="读取当前 InfoOS 专用 Chrome 中已登录小红书账号的首页推荐",
                identifier_options=(("recommended", "推荐"),),
            ),
        ),
        (5, 24), qr_login=QrLoginCapability(120, "小红书 App"), research=ResearchCapability(
            status="available", mode="keyword_search", max_items_limit=100,
            sort_options=("general", "latest", "most-liked", "most-commented", "most-collected"),
            default_sort="general",
        ), **_BROWSER,
    ),
    "douyin": PlatformDefinition(
        "douyin",
        "抖音",
        (
            _source("keyword", "5h", label="关键词"),
            _source("creator", "5h", label="创作者", identifier_hint="创作者 sec_uid 或主页标识"),
            _source(
                "item",
                "24h",
                1,
                label="单个作品",
                identifier_hint="抖音作品链接、v.douyin.com/v.iesdouyin.com 短链或完整分享文本",
            ),
            _source(
                "home_feed", "1h", 40, label="首页 Feed",
                identifier_hint="读取当前 InfoOS 专用 Chrome 中已登录抖音账号的首页推荐",
                identifier_options=(("recommended", "推荐"),),
                max_items_limit=50,
            ),
        ),
        (1, 5, 24),
        qr_login=QrLoginCapability(120, "抖音 App"),
        research=ResearchCapability(
            status="available",
            mode="keyword_search",
            max_items_limit=30,
            sort_options=("general", "latest", "most-liked"),
            default_sort="general",
        ),
        **_BROWSER,
    ),
    "zsxq": PlatformDefinition(
        "zsxq",
        "生财有术",
        (
            _source("topics", "5h", label="全部帖子", identifier_options=_ZSXQ_TABS),
            _source("opportunity", "5h", label="风向标", identifier_options=_ZSXQ_TABS),
            _source(
                "keyword",
                "5h",
                label="关键词",
                identifier_hint="站内搜索关键词",
                max_items_limit=100,
                research_capable=True,
            ),
        ),
        (5, 6, 24),
        qr_login=QrLoginCapability(120, "微信"),
        research=ResearchCapability(
            status="available", mode="keyword_search", max_items_limit=100,
            sort_options=("latest", "earliest"),
        ),
        **_BROWSER,
    ),
    # Capture-only fallback. It is deliberately not configurable or scheduled.
    "web": PlatformDefinition("web", "Web", (), ()),
}

PLATFORM_ALIASES = {"twitter": "x", "xhs": "xiaohongshu"}

# Browser-capture URL recognition lives beside the canonical platform registry
# so API, workflow, and UI layers do not grow independent platform lists.
CAPTURE_HOST_PLATFORMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x", ("x.com", "twitter.com")),
    ("youtube", ("youtube.com", "youtu.be")),
    ("reddit", ("reddit.com",)),
    ("github", ("github.com",)),
    ("hackernews", ("news.ycombinator.com",)),
    ("producthunt", ("producthunt.com",)),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com")),
    ("douyin", ("douyin.com", "iesdouyin.com")),
)


def canonical_platform(value: str) -> str:
    normalized = str(value).strip().lower()
    return PLATFORM_ALIASES.get(normalized, normalized)


def platform_definition(platform: str) -> PlatformDefinition | None:
    return PLATFORM_REGISTRY.get(canonical_platform(platform))


def capture_platform_for_host(hostname: str) -> str:
    normalized = str(hostname or "").strip().rstrip(".").lower()
    for platform, suffixes in CAPTURE_HOST_PLATFORMS:
        if any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in suffixes):
            return platform
    return "web"


def capture_adapter_for_platform(platform: str) -> str:
    """Return only explicitly registered per-item capture capability."""
    return {"douyin": "douyin:item", "github": "github:item"}.get(canonical_platform(platform), "web")


def source_type_capability(platform: str, source_type: str) -> SourceTypeCapability | None:
    definition = platform_definition(platform)
    normalized = str(source_type).strip().lower()
    if definition is None:
        return None
    return next((entry for entry in definition.source_types if entry.source_type == normalized), None)


def is_research_source_type(platform: str, source_type: str) -> bool:
    # MCP research sources are internal, not configurable source types.
    if source_type == "research":
        definition = platform_definition(platform)
        return bool(definition and definition.research)
    capability = source_type_capability(platform, source_type)
    return bool(capability and capability.research_capable)


def source_task_view(platform: str, source_type: str) -> str:
    return "research" if is_research_source_type(platform, source_type) else "collection"


def research_source_type_pairs() -> tuple[tuple[str, str], ...]:
    return tuple(
        (entry.platform, source.source_type)
        for entry in PLATFORM_REGISTRY.values()
        for source in entry.source_types
        if source.research_capable
    ) + tuple((entry.platform, "research") for entry in PLATFORM_REGISTRY.values() if entry.research)


def configurable_platforms() -> tuple[PlatformDefinition, ...]:
    return tuple(entry for entry in PLATFORM_REGISTRY.values() if entry.source_types)


def browser_platforms() -> tuple[str, ...]:
    return tuple(entry.platform for entry in PLATFORM_REGISTRY.values() if entry.requires_browser_session)


def source_browser_platforms() -> frozenset[str]:
    return frozenset(entry.platform for entry in configurable_platforms() if entry.requires_browser_session)


def opencli_tab_policy_platforms() -> frozenset[str]:
    return frozenset(entry.platform for entry in PLATFORM_REGISTRY.values() if entry.opencli_tab_policy)


def capability_catalog() -> list[CatalogItem]:
    """Return the stable JSON-ready projection consumed by API clients."""
    return [
        {
            "platform": entry.platform,
            "label": entry.label,
            "source_types": [
                {
                    "source_type": source.source_type,
                    "label": source.label or source.source_type,
                    "initial_status": source.initial_status,
                    "default_cadence": source.default_cadence,
                    "default_cadence_hours": _cadence_hours(source.default_cadence),
                    "default_max_items": source.default_max_items_per_run,
                    "identifier_mode": "select" if source.identifier_options else "text",
                    "identifier_hint": source.identifier_hint,
                    "identifier_options": [
                        {"value": value, "label": label}
                        for value, label in source.identifier_options
                    ],
                    "requires_login": source.requires_login or entry.requires_login,
                    "login_verifiable": source.login_verifiable or entry.login_verifiable,
                    "max_items_limit": source.max_items_limit or entry.max_items_limit,
                    "research_capable": source.research_capable,
                }
                for source in entry.source_types
            ],
            "cadence_options": list(entry.cadence_options),
            "requires_login": entry.requires_login,
            "login_verifiable": entry.login_verifiable,
            "requires_browser_session": entry.requires_browser_session,
            "configurable": bool(entry.source_types),
            "default_max_items": next(
                (
                    source.default_max_items_per_run
                    for source in entry.source_types
                    if source.default_max_items_per_run is not None
                ),
                20,
            ),
            "max_items_limit": entry.max_items_limit,
            "research": (
                {
                    "status": entry.research.status,
                    "mode": entry.research.mode,
                    "max_items_limit": entry.research.max_items_limit,
                    "supports_date_range": entry.research.supports_date_range,
                    "requires_source_selection": entry.research.requires_source_selection,
                    "limitation": entry.research.limitation,
                    "sort_options": list(entry.research.sort_options),
                    "default_sort": entry.research.default_sort,
                }
                if entry.research is not None
                else None
            ),
        }
        for entry in PLATFORM_REGISTRY.values()
    ]


def _cadence_hours(value: str) -> float:
    normalized = str(value).strip().lower()
    if normalized.endswith("min"):
        return float(normalized.removesuffix("min")) / 60
    if normalized.endswith("h"):
        return float(normalized.removesuffix("h"))
    return 24.0


def research_default_sort(platform: str) -> str:
    definition = platform_definition(platform)
    return definition.research.default_sort if definition and definition.research else "latest"
