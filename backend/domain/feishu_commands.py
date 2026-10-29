"""The private-message command vocabulary; no execution or I/O."""
import unicodedata
from dataclasses import dataclass
from typing import Literal

from backend.domain.sources.registry import PLATFORM_REGISTRY, platform_definition


@dataclass(frozen=True)
class Command:
    kind: Literal['qr', 'status', 'help']
    platform: str | None = None


QR_PLATFORMS = tuple(entry.platform for entry in PLATFORM_REGISTRY.values() if entry.qr_login is not None)
_ALIASES = {
    'xiaohongshu': ('小红书', '红书', 'xhs'),
    'douyin': ('抖音', 'douyin'),
    'zsxq': ('知识星球', '星球', '生财有术', '生财', 'zsxq'),
}
# Numbered replies are the primary menu; keywords stay as an alternative.
MENU = (*(Command('qr', platform) for platform in QR_PLATFORMS), Command('status'))


def qr_menu_number(platform: str) -> int:
    return MENU.index(Command('qr', platform)) + 1


def menu_text() -> str:
    lines = [f"{number} {platform_definition(c.platform).label}二维码" if c.kind == 'qr' else f'{number} 登录状态'
             for number, c in enumerate(MENU, 1)]
    return '回复数字：\n' + '\n'.join(lines) + '\n也可以直接说「小红书二维码」「小红书登录了吗」。'


def parse_command(text: str) -> Command:
    text = unicodedata.normalize('NFKC', text).strip().lower()
    if text.isdecimal():
        return MENU[int(text) - 1] if 1 <= int(text) <= len(MENU) else Command('help')
    platform = next((platform for platform, aliases in _ALIASES.items()
        if platform in QR_PLATFORMS and any(alias in text for alias in aliases)), None)
    if any(word in text for word in ('吗', '状态', '是否', '了没', '没有')):
        return Command('status', platform)
    if platform is not None and any(word in text for word in ('二维码', '码', '扫码', '登录', '重新生成', '重发')):
        return Command('qr', platform)
    return Command('help')
