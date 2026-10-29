export async function collectDocument(options) {
  const deadline = Date.now() + options.budgetMs;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, Math.max(0, Math.min(ms, deadline - Date.now()))));
  const leafBlocks = () => [...document.querySelectorAll('[data-block-id]')]
    .filter((node) => !node.querySelector('[data-block-id]'));
  // Slow first paints are common; giving up early would fall back to first-screen text.
  const waitDeadline = Math.min(deadline, Date.now() + 12000);
  while (!leafBlocks().length && Date.now() < waitDeadline) await sleep(250);
  const scrollable = [...document.querySelectorAll('*')]
    .filter((node) => /^(auto|scroll)$/.test(getComputedStyle(node).overflowY)
      && node.clientHeight > 0 && node.scrollHeight > node.clientHeight + 2)
    .sort((a, b) => b.scrollHeight - a.scrollHeight);
  // Prefer the scroller that holds the document blocks over a taller sidebar or catalogue.
  const scrollContainer = scrollable.find((node) => typeof node.querySelector === 'function'
    && node.querySelector('[data-block-id]')) || scrollable[0] || document.scrollingElement;
  const blocks = new Map();
  const allImages = new Set();
  const allVideos = new Set();
  let chars = 0;
  const toAbsoluteHttp = (value) => {
    if (!value) return '';
    try {
      const url = new URL(value, location.href);
      return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
    } catch { return ''; }
  };
  const unique = (values, limit) => [...new Set(values.map(toAbsoluteHttp).filter(Boolean))].slice(0, limit);
  const sheetRequested = (() => {
    try { return new URL(location.href).searchParams.has('sheet'); }
    catch { return false; }
  })();
  if (sheetRequested) {
    await new Promise((resolve) => {
      const sheetDeadline = Math.min(deadline, Date.now() + 15000);
      const waitForSheet = () => {
        const sheet = window.spread && Array.isArray(window.spread.sheets)
          ? window.spread.sheets[0]
          : null;
        if (
          sheet
          && typeof sheet.getRowCount === 'function'
          && typeof sheet.getColumnCount === 'function'
          && typeof sheet.getText === 'function'
        ) {
          resolve();
          return;
        }
        if (Date.now() >= sheetDeadline) {
          resolve();
          return;
        }
        setTimeout(waitForSheet, 250);
      };
      waitForSheet();
    });
  }
  const spreadsheetText = (() => {
    const sheets = window.spread && Array.isArray(window.spread.sheets)
      ? window.spread.sheets.slice(0, 20)
      : [];
    const sections = [];
    let totalChars = 0;
    for (const sheet of sheets) {
      if (
        !sheet
        || typeof sheet.getRowCount !== 'function'
        || typeof sheet.getColumnCount !== 'function'
        || typeof sheet.getText !== 'function'
      ) {
        continue;
      }
      let rowCount = 0;
      let columnCount = 0;
      try {
        rowCount = Math.min(Math.max(Number(sheet.getRowCount()) || 0, 0), 500);
        columnCount = Math.min(Math.max(Number(sheet.getColumnCount()) || 0, 0), 50);
      } catch {
        continue;
      }
      const rows = [];
      for (let row = 0; row < rowCount && totalChars < 120000; row += 1) {
        const cells = [];
        let lastNonEmpty = -1;
        for (let column = 0; column < columnCount; column += 1) {
          let text = '';
          try {
            text = String(sheet.getText(row, column) || '')
              .replace(/[\r\n\t]+/g, ' ')
              .trim();
          } catch {
            text = '';
          }
          cells.push(text);
          if (text) lastNonEmpty = column;
        }
        if (lastNonEmpty < 0) continue;
        const line = cells.slice(0, lastNonEmpty + 1).join('\t');
        rows.push(line);
        totalChars += line.length + 1;
      }
      if (!rows.length) continue;
      let name = '';
      try {
        name = String(
          typeof sheet.name === 'function'
            ? sheet.name()
            : sheet._name || ''
        ).trim();
      } catch {
        name = '';
      }
      sections.push([name ? '# Sheet: ' + name : '# Sheet', ...rows].join('\n'));
      totalChars += name.length + 10;
      if (totalChars >= 120000) break;
    }
    return sections.join('\n\n').slice(0, 120000);
  })();
  const isTrustedMedia = (value) => {
    try {
      const url = new URL(value);
      const host = url.hostname.toLowerCase().replace(/\.$/, '');
      return url.protocol === 'https:' && !url.username && !url.password
        && (!url.port || url.port === '443')
        && (
          host === 'feishu.cn' || host.endsWith('.feishu.cn')
          || host === 'larksuite.com' || host.endsWith('.larksuite.com')
        );
    } catch {
      return false;
    }
  };
  const toBase64 = (bytes) => {
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  };
  const readBounded = async (response, limit) => {
    const declared = Number(response.headers.get('content-length') || 0);
    if (Number.isFinite(declared) && declared > limit) return null;
    if (!response.body || typeof response.body.getReader !== 'function') {
      const buffer = await response.arrayBuffer();
      return buffer.byteLength > 0 && buffer.byteLength <= limit
        ? new Uint8Array(buffer)
        : null;
    }
    const reader = response.body.getReader();
    const chunks = [];
    let size = 0;
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      if (!value || value.byteLength === 0) continue;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
    if (size === 0) return null;
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return bytes;
  };
  const sniffMimeType = (bytes, kind) => {
    if (
      kind === 'image'
      && bytes.length >= 8
      && bytes[0] === 0x89 && bytes[1] === 0x50
      && bytes[2] === 0x4e && bytes[3] === 0x47
    ) return 'image/png';
    if (
      kind === 'image'
      && bytes.length >= 3
      && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff
    ) return 'image/jpeg';
    if (
      kind === 'image'
      && bytes.length >= 6
      && String.fromCharCode(...bytes.subarray(0, 3)) === 'GIF'
    ) return 'image/gif';
    if (
      kind === 'image'
      && bytes.length >= 12
      && String.fromCharCode(...bytes.subarray(0, 4)) === 'RIFF'
      && String.fromCharCode(...bytes.subarray(8, 12)) === 'WEBP'
    ) return 'image/webp';
    if (
      kind === 'video'
      && bytes.length >= 12
      && String.fromCharCode(...bytes.subarray(4, 8)) === 'ftyp'
    ) return 'video/mp4';
    if (
      kind === 'video'
      && bytes.length >= 4
      && bytes[0] === 0x1a && bytes[1] === 0x45
      && bytes[2] === 0xdf && bytes[3] === 0xa3
    ) return 'video/webm';
    return '';
  };
  const mediaBlobs = [];
  const seenMedia = new Set();
  let totalBytes = 0;
  const mediaDeadline = Math.min(deadline, Date.now() + 30000);
  const captureMedia = async () => {
    const candidates = [
      ...[...allImages].map((url) => ({url, kind: 'image'})),
      ...[...allVideos].map((url) => ({url, kind: 'video'}))
    ];
    for (const candidate of candidates) {
      if (
        mediaBlobs.length >= 20
        || Date.now() >= mediaDeadline
        || seenMedia.has(candidate.url)
        || !isTrustedMedia(candidate.url)
      ) {
        continue;
      }
      seenMedia.add(candidate.url);
      const itemLimit = candidate.kind === 'image' ? 10 * 1024 * 1024 : 25 * 1024 * 1024;
      const remainingBytes = 32 * 1024 * 1024 - totalBytes;
      if (remainingBytes <= 0) break;
      const controller = new AbortController();
      const timeoutId = setTimeout(
        () => controller.abort(),
        Math.max(1, Math.min(15000, mediaDeadline - Date.now()))
      );
      try {
        const response = await fetch(candidate.url, {
          credentials: 'include',
          cache: 'force-cache',
          redirect: 'follow',
          signal: controller.signal
        });
        if (!response.ok || !isTrustedMedia(response.url || candidate.url)) continue;
        const bytes = await readBounded(response, Math.min(itemLimit, remainingBytes));
        if (!bytes) continue;
        const contentType = String(response.headers.get('content-type') || '')
          .split(';', 1)[0].trim().toLowerCase();
        const mimeType = contentType.startsWith(candidate.kind + '/')
          ? contentType
          : sniffMimeType(bytes, candidate.kind);
        if (!mimeType) continue;
        mediaBlobs.push({
          url: candidate.url,
          mime_type: mimeType,
          base64: toBase64(bytes)
        });
        totalBytes += bytes.byteLength;
      } catch {
        // A missing individual media object must not discard the readable document body.
      } finally {
        clearTimeout(timeoutId);
      }
    }
  };
  let mediaRead = Promise.resolve();
  // Containers (page, grid, callout, list items with children) keep only their own text;
  // the page block's own text is the title, which is returned separately.
  const blockText = (node) => {
    if (!node.querySelector('[data-block-id]')) return String(node.innerText || '').trim();
    if (node.getAttribute('data-block-type') === 'page' || typeof node.cloneNode !== 'function') return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('[data-block-id]').forEach((child) => child.remove());
    return String(copy.textContent || '').replace(/\u200b/g, '').trim();
  };
  const renderedSignature = () => {
    let size = 0;
    const nodes = document.querySelectorAll('[data-block-id]');
    for (const node of nodes) size += String(node.textContent || '').length;
    return nodes.length + ':' + size;
  };
  // Scrolling renders blocks asynchronously; collecting on a fixed delay skipped late ones.
  const settle = async () => {
    const settleDeadline = Math.min(deadline, Date.now() + 4000);
    let last = renderedSignature();
    let stable = 0;
    await sleep(250);
    while (Date.now() < settleDeadline) {
      const current = renderedSignature();
      stable = current === last ? stable + 1 : 0;
      if (stable >= 2) return;
      last = current;
      await sleep(200);
    }
  };
  const collect = () => {
    for (const node of document.querySelectorAll('[data-block-id]')) {
      const id = node.getAttribute('data-block-id');
      if (!id) continue;
      const text = blockText(node);
      if (!text && node.querySelector('[data-block-id]')) continue;
      const known = blocks.get(id);
      // A block can first appear as an empty or half-loaded placeholder; keep its longest text.
      if (known !== undefined && known.length >= text.length) continue;
      blocks.set(id, text);
      chars += text.length - (known === undefined ? 0 : known.length) + (known === undefined && text ? 1 : 0);
    }
    const images = unique(
    [...document.querySelectorAll('img')].flatMap((node) => [node.currentSrc, node.src]),
    200
  );
    const videos = unique([
    ...[...document.querySelectorAll('video')].flatMap((node) => [node.currentSrc, node.src]),
    ...[...document.querySelectorAll('video source')].map((node) => node.src)
  ], 200);
    images.forEach((url) => allImages.add(url));
    videos.forEach((url) => allVideos.add(url));
    mediaRead = mediaRead.then(captureMedia);
  };
  if (scrollContainer && leafBlocks().length) {
    scrollContainer.scrollTop = 0;
    await settle();
  }
  collect();
  let reachedEnd = false;
  if (blocks.size) {
    while (Date.now() < deadline && chars < 120000) {
      if (!scrollContainer || scrollContainer.scrollHeight <= scrollContainer.clientHeight + 2) {
        reachedEnd = true;
        break;
      }
      const previous = scrollContainer.scrollTop;
      const previousHeight = scrollContainer.scrollHeight;
      scrollContainer.scrollTop = previous + Math.max(1, scrollContainer.clientHeight * 0.6);
      await settle();
      collect();
      if (Date.now() >= deadline) break;
      if (scrollContainer.scrollTop === previous
        && scrollContainer.scrollHeight === previousHeight
        && previous + scrollContainer.clientHeight >= scrollContainer.scrollHeight - 2) {
        reachedEnd = true;
        break;
      }
    }
  }
  const images = [...allImages].slice(0, 200);
  const videos = [...allVideos].slice(0, 200);
  const roots = [...document.querySelectorAll(
    'article, main, [role="main"], [class*="doc-content"], [class*="editor-content"]'
  )];
  const documentText = roots
    .map((node) => String(node.innerText || '').trim())
    .sort((left, right) => right.length - left.length)[0]
    || String(document.body && document.body.innerText || '').trim();
  const visibleText = spreadsheetText.length > documentText.length
    ? spreadsheetText
    : documentText;
  const hasDocumentRoot = blocks.size > 0 || spreadsheetText.length >= 40
    || roots.some((node) => String(node.innerText || '').trim().length >= 200);
  const loginRoute = /\/(?:login|accounts?)(?:\/|$)/i.test(location.pathname);
  const loginControl = Boolean(document.querySelector(
    'form[action*="login"], input[type="password"], [class*="login-page"], '
    + '[data-testid*="login"], [class*="login-qr"], [class*="qrcode"]'
  ));
  await mediaRead;
  const body = blocks.size ? [...blocks.values()].filter(Boolean).join('\n') : visibleText;
  const farScrollable = scrollContainer && scrollContainer.scrollHeight > scrollContainer.clientHeight * 1.5;
  const loginRequired = !hasDocumentRoot && (loginRoute || loginControl);
  const complete = !loginRequired && body.length < 120000 && (blocks.size ? reachedEnd : !farScrollable);
  return {
    complete,
    completeness: complete ? 'complete' : 'partial',
    blocks: blocks.size,
    reached_end: blocks.size ? reachedEnd : !farScrollable,
    url: location.href,
    title: document.title,
    body: body.slice(0, 120000),
    login_required: loginRequired,
    images,
    videos,
    media_blobs: mediaBlobs
  };
}
