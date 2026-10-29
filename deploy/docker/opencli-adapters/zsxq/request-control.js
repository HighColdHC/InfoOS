// One controller per command process: serialize only ZSXQ API requests.
export function createZsxqRequestControl({
  now = () => performance.now(),
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
  random = Math.random,
  env = process.env,
} = {}) {
  const interval = setting(env.INFOOS_ZSXQ_REQUEST_INTERVAL_MS, 1500);
  const jitter = setting(env.INFOOS_ZSXQ_REQUEST_JITTER_MS, 1000);
  const retries = Math.min(2, Math.floor(setting(env.INFOOS_ZSXQ_1059_RETRIES, 1)));
  const cooldown = Math.min(60000, setting(env.INFOOS_ZSXQ_1059_COOLDOWN_MS, 20000));
  let lastRequest = null;
  let cooldownWait = 0;
  let tail = Promise.resolve();

  async function execute(request) {
    for (let attempt = 0; ; attempt += 1) {
      if (lastRequest !== null) {
        const delay = interval + random() * jitter - (now() - lastRequest);
        if (delay > 0) await sleep(delay);
      }
      let result;
      try {
        result = await request();
      } finally {
        // Pace from completion: even variable signing/bridge latency cannot
        // compress the spacing between two actual XHR sends.
        lastRequest = now();
      }
      if (result?.data?.succeeded !== false || String(result.data.code) !== '1059') return result;
      if (attempt >= retries || cooldownWait + cooldown > 60000) {
        const error = new Error('zsxq risk control 1059 (rate limited)');
        error.code = 'RATE_LIMITED';
        throw error;
      }
      cooldownWait += cooldown;
      await sleep(cooldown);
    }
  }

  return {
    run(path, request) {
      if (new URL(path).hostname !== 'api.zsxq.com') return request();
      const pending = tail.then(() => execute(request));
      tail = pending.catch(() => {});
      return pending;
    },
  };
}

function setting(value, fallback) {
  if (value === undefined || String(value).trim() === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}
