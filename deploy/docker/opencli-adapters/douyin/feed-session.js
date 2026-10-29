const STOP_MESSAGES = {
  no_progress: 'Feed did not advance after two scroll attempts',
  skipped_position: 'Feed skipped the next position',
  item_unreadable: 'Feed item could not be read',
  detached: 'Browser debugger or session disconnected',
  unknown: 'Feed advancement failed',
};

export class FeedReadError extends Error {
  constructor(message, { code = 'unknown', cause } = {}) {
    super(message, { cause });
    this.name = 'FeedReadError';
    this.code = Object.hasOwn(STOP_MESSAGES, code) ? code : 'unknown';
  }
}

export function feedReadError(error, code = 'unknown') {
  if (error instanceof FeedReadError) return error;
  // Keep the original error/code on cause; only the stable classification is exported.
  for (let cause = error, depth = 0; cause && depth < 8; cause = cause.cause, depth++) {
    if (/detached|(?:debugger|session).*(?:disconnect|closed)|target closed/i.test(String(cause))) {
      code = 'detached';
      break;
    }
  }
  return new FeedReadError(STOP_MESSAGES[code], { code, cause: error });
}

export function dwellOptions(min = 0, max = 0) {
  const values = [min, max].map(value => Number(value ?? 0));
  if (values.some(value => !Number.isInteger(value) || value < 0 || value > 60) || values[0] > values[1]) {
    throw new Error('dwell_min/dwell_max must be integers in 0-60 with dwell_min <= dwell_max');
  }
  return values;
}

export async function openWithDetachedRetry(open) {
  try { await open(); } catch (error) {
    if (!/detached while handling command/i.test(String(error))) throw error;
    await open();
  }
}

export async function collectFeed({ limit, read, advance, wait, dwellMin = 0, dwellMax = 0, random = Math.random, withMeta = false, prefilter = null }) {
  const rows = [];
  let stop = null;
  let current = await read();
  const seconds = (min, max) => min + Math.floor(random() * (max - min + 1));
  while (rows.length < limit) {
    if (prefilter) current.item = await prefilter(current.item);
    rows.push({ index: current.position, ...current.item, feed_url: 'https://www.douyin.com/?recommend=1' });
    // Legacy zero/final-item timing remains byte-for-byte compatible when off.
    if (rows.length === limit && (!prefilter || current.item.prefilter?.status === 'skipped')) break;
    // Zero dwell preserves the original wheel polling, including for ads.
    try {
      const missed = current.item.prefilter?.status === 'missed';
      if (dwellMax > 0 || (prefilter && missed)) {
        const dwell = prefilter && missed ? seconds(1, 2)
          : seconds(dwellMin, dwellMax) + (current.item.is_ad ? seconds(3, 8) : 0);
        if (dwell) await wait(dwell);
      }
      if (rows.length === limit) break;
      current = await advance(current);
    } catch (error) {
      const failure = feedReadError(error);
      stop = { code: failure.code, position: current.position,
        message: STOP_MESSAGES[failure.code].slice(0, 160) };
      break;
    }
  }
  if (!withMeta) return rows;
  return { items: rows, acquisition: {
    requested_count: limit, observed_count: rows.length,
    complete: rows.length === limit && stop === null,
    stop_reason: stop?.code ?? null, stop_position: stop?.position ?? null,
    stop_message: stop?.message ?? null,
  } };
}
