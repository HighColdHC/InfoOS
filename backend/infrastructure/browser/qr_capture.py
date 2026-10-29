"""Unverified platform selectors are deliberately centralized here.

TTLs are conservative provisional values, pending real platform acceptance.
Never persist or return the full viewport screenshot.
"""
import base64
import json
import logging
import os
import time
import zlib
import struct
from pathlib import Path
from typing import TypedDict

from backend.infrastructure.browser.cookie_status import authorization_url_for_platform
from backend.infrastructure.browser.png_crop import crop_png
from backend.infrastructure.opencli.commands import run_opencli, close_site_session
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.runtime.environment import state_home

logger = logging.getLogger(__name__)


class QrSelectors(TypedDict):
    qr: str
    entries: tuple[tuple[str, str], ...]


# Login pages checked live on 2026-10-22; QR expiry is still unverified.
# Douyin hashes its class names, so it keys on the login panel's stable ids.
SELECTORS: dict[str, QrSelectors] = {
    'xiaohongshu': {'qr': '.login-container .qrcode-img, .login-container canvas, .qrcode img', 'entries': (('--text', '登录'),)},
    'douyin': {'qr': '#douyin_login_comp_scan_code #animate_qrcode_container img', 'entries': (('--text', '登录'),)},
    'zsxq': {'qr': 'iframe[src*="open.weixin.qq.com"]', 'entries': (('--css', '.get-qr-btn'), ('--css', '.agree-btn'))},
}


class QrCaptureError(Exception):
    pass


def _command(site: str, *args: str, timeout: float = 20) -> str:
    result = run_opencli(['--profile', 'infoos-fetch', 'browser', f'site:{site}', *args], timeout=timeout)
    if result.returncode:
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        error = payload.get('error') if isinstance(payload, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if args[0] == 'find' and code == 'semantic_not_found':
            return '{"entries":[]}'
        logger.warning('qr_opencli_failed step=%s returncode=%s code=%s', args[0], result.returncode, code)
        raise QrCaptureError(f'qr_opencli_{args[0]}_failed')
    return result.stdout


def image_path(incident_id: str, round_no: int, code: int) -> Path:
    return state_home() / 'auth-qr' / f'{incident_id}-{round_no}-{code}.png'


def close_qr_tab(site: str) -> None:
    with opencli_tab_lease(site, close_tabs=False):
        result = close_site_session(site)
        if result.returncode:
            raise QrCaptureError('qr_tab_close_failed')


def capture_qr(platform: str, site: str, path: Path) -> str:
    try:
        with opencli_tab_lease(site, close_tabs=False) as lease:
            _command(site, 'open', authorization_url_for_platform(platform))
            selector = SELECTORS[platform]
            deadline = time.monotonic() + 20
            rectangle = _rectangle(site, selector, deadline=deadline)
            for entry in selector['entries']:
                if rectangle is not None:
                    break
                # Open a login entry only when the QR is not already visible.
                _click_entry(site, entry, deadline=deadline)
                rectangle = _rectangle(site, selector, deadline=deadline)
            while rectangle is None:
                if time.monotonic() >= deadline:
                    raise QrCaptureError('qr_element_not_found')
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
                rectangle = _rectangle(site, selector, deadline=deadline)
            # No path argument: full viewport bytes live in memory only.
            screenshot = _command(site, 'screenshot').strip()
            data = base64.b64decode(screenshot.split(',')[-1], validate=True)
            cropped = crop_png(data, tuple(rectangle[k] for k in ('x', 'y', 'width', 'height')), rectangle['viewport_width'])
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(cropped)
            os.chmod(path, 0o600)
            return lease.lease_id
    except (OSError, ValueError, KeyError, TypeError, zlib.error, struct.error) as exc:
        raise QrCaptureError('qr_capture_failed') from exc


def _click_entry(site: str, entry: tuple[str, str], *, deadline: float) -> None:
    # The agreement dialog can appear shortly after the preceding entry click.
    find_deadline = min(deadline, time.monotonic() + 3)
    while time.monotonic() < find_deadline:
        found = json.loads(_command(site, 'find', *entry, '--limit', '1', timeout=_remaining(deadline)))
        if found['entries']:
            _command(site, 'click', str(found['entries'][0]['ref']), timeout=_remaining(deadline))
            return
        time.sleep(min(0.5, max(0, find_deadline - time.monotonic())))


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise QrCaptureError('qr_element_not_found')
    return remaining


def _rectangle(site: str, selector: QrSelectors, *, deadline: float) -> dict | None:
    expression = '''(() => {const e=document.querySelector(SELECTOR); if(!e)return null;
        const r=e.getBoundingClientRect(); return r.width>=40 && r.height>=40 && r.top>=0 && r.left>=0
        && r.bottom<=innerHeight && r.right<=innerWidth ? {x:r.x,y:r.y,width:r.width,height:r.height,
        viewport_width:innerWidth} : null})()'''
    return json.loads(_command(site, 'eval', expression.replace('SELECTOR', json.dumps(selector['qr'])), timeout=_remaining(deadline)))
