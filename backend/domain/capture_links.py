"""Pure agent share-link extraction and indexed identity lookup candidates."""
import re
from urllib.parse import urlsplit

from backend.domain.captures import canonical_capture_url, capture_card_url, platform_item_identity
from backend.domain.sources.douyin import DOUYIN_INPUT_HOSTS

SHORT_LINK_HOSTS = {
    "douyin": frozenset({"v.douyin.com", "v.iesdouyin.com"}),
    "xiaohongshu": frozenset({"xhslink.com", "www.xhslink.com"}),
}
SHORT_LINK_PLATFORM_HOSTS = {
    "douyin": DOUYIN_INPUT_HOSTS,
    "xiaohongshu": frozenset({"xhslink.com", "www.xhslink.com", "xiaohongshu.com",
                              "www.xiaohongshu.com", "m.xiaohongshu.com"}),
}


def extract_capture_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s<>\"'，。；！？、）】》」』]+", text, re.IGNORECASE)
    return match.group().rstrip(".,!?;:，。！？，；：)]}）】》」』…") if match else None


def raw_identity_candidates(platform: str, url: str) -> tuple[set[str], set[str]]:
    """Exact raw IDs plus URL bases whose query/path suffixes need index ranges."""
    canonical = canonical_capture_url(url)
    exact = {canonical, capture_card_url(platform, canonical)}
    bases = set(exact)
    identity = platform_item_identity(platform, canonical)
    if identity:
        item_id = identity.split(":", 1)[1]
        exact.add(item_id)
        if platform == "reddit":
            exact.add(f"t3_{item_id}")
        paths = {
            "xiaohongshu": [f"/{kind}/{item_id}" for kind in ("explore", "search_result", "note", "discovery/item")],
            "youtube": [f"/watch?v={item_id}", f"/shorts/{item_id}", f"/live/{item_id}", f"/embed/{item_id}"],
            "douyin": [f"/video/{item_id}", f"/share/video/{item_id}"],
        }.get(platform, [])
        hosts = {
            "xiaohongshu": ("www.xiaohongshu.com", "xiaohongshu.com", "m.xiaohongshu.com"),
            "youtube": ("www.youtube.com", "youtube.com", "m.youtube.com"),
            "douyin": tuple(DOUYIN_INPUT_HOSTS - SHORT_LINK_HOSTS["douyin"]),
        }.get(platform, ())
        bases.update(f"{scheme}://{host}{path}" for scheme in ("https", "http") for host in hosts for path in paths)
        if platform == "youtube":
            bases.add(f"https://youtu.be/{item_id}")
    exact.update(bases)
    prefixes = {base + separator for base in bases for separator in ("?", "&", "/", "#")}
    return exact, prefixes


def short_link_platform(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return next((platform for platform, hosts in SHORT_LINK_HOSTS.items() if host in hosts), None)
