// Self-contained so the same pure projection can be injected into the page.
export function slideDataRow(item = {}, withPrefilter = false) {
  const text = value => value == null ? '' : String(value);
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const url = value => {
    const candidate = text(value);
    return candidate.startsWith('//') ? `https:${candidate}` : candidate;
  };
  const json = value => {
    try {
      const parsed = typeof value === 'string' ? JSON.parse(value) : value;
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch { return {}; }
  };
  const exactId = (raw, key, parsed) => {
    if (typeof raw === 'string') {
      const match = raw.match(new RegExp(`"${key}"\\s*:\\s*(?:"(\\d+)"|(\\d+))(?=\\s*[,}])`));
      return match ? match[1] || match[2] : '';
    }
    return typeof parsed[key] === 'string' ? parsed[key] : '';
  };
  const firstUrl = value => {
    if (typeof value === 'string') return url(value);
    if (Array.isArray(value)) return firstUrl(value[0]);
    if (value && typeof value === 'object') return firstUrl(value.src || value.url || value.urlList || value.url_list);
    return '';
  };
  const video = item.video || {};
  const plays = [video.playApi,
    ...(Array.isArray(video.playAddr) ? video.playAddr.map(value => value?.src) : []),
    ...(Array.isArray(video.playAddrH265) ? video.playAddrH265.map(value => value?.src) : []),
  ].filter(value => typeof value === 'string' && value).map(url);
  const playUrls = [...new Set(plays)];
  const ad = json(item.rawAdData);
  const log = json(ad.log_extra);
  const id = text(item.awemeId || '');
  const live = Number(item.awemeType) === 101;
  return {
    ...(withPrefilter ? { prefilter_signals: {
      text_extra_is_commerce: (item.textExtra || []).map(value => value.isCommerce ?? null),
      author_custom_verify: item.authorInfo?.customVerify ?? null,
      author_enterprise_verify_reason: item.authorInfo?.enterpriseVerifyReason ?? null,
    } } : {}),
    aweme_id: id,
    title: text(item.itemTitle || item.desc || ad.title || (live ? '直播中' : '')),
    author: text(item.authorInfo?.nickname),
    author_handle: text(item.authorInfo?.uniqueId || item.authorInfo?.shortId),
    author_url: item.authorInfo?.secUid ? `https://www.douyin.com/user/${item.authorInfo.secUid}` : '',
    digg_count: number(item.stats?.diggCount),
    comment_count: number(item.stats?.commentCount),
    share_count: number(item.stats?.shareCount),
    collect_count: number(item.stats?.collectCount),
    published_at: number(item.createTime),
    duration: Math.floor(number(video.duration) / 1000),
    cover_url: firstUrl(video.cover || video.coverUrl || video.originCover || item.cover),
    play_url: live ? '' : url(video.playApi || video.playAddr?.[0]?.src || ''),
    play_urls: live ? [] : playUrls,
    url: !live && id ? `https://www.douyin.com/video/${id}` : '',
    is_ad: item.isAds === true || !!item.rawAdData,
    is_live: live,
    ad_id: exactId(item.rawAdData, 'ad_id', ad),
    ad_creative_id: exactId(item.rawAdData, 'creative_id', ad),
    ad_title: text(ad.title),
    ad_button_text: text(ad.button_text),
    ad_landing_url: url(ad.open_url),
    ad_web_url: url(ad.web_url),
    ad_group_type: number(ad.group_type),
    ad_source_type: number(ad.ad_source_type),
    ad_kind: text(log.predict_identity),
    ad_is_soft: log.is_soft_ad === true || Number(log.is_soft_ad) === 1,
    ad_advertiser_id: exactId(ad.log_extra, 'soft_ad_ascription_advertiser_id', log),
    ad_live_room_id: exactId(ad.log_extra, 'room_id', log) || text(ad.live_room?.room_id),
  };
}
