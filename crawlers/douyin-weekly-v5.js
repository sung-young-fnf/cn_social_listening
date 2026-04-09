/**
 * 더우인(抖音) 주간 대량 수집 스크립트 v5
 *
 * v4 대비 개선:
 * - HB 프록시 → Oxylabs 프록시 (HB 프록시 비용 $0)
 * - HB는 브라우저 세션 + 스텔스 + 캡차만 담당
 *
 * 구조:
 * - Hyperbrowser: 세션/스텔스/캡차 (Oxylabs 프록시 사용)
 * - API 호출: 브라우저 내 fetch (anti-bot 토큰 자동 포함)
 * - Node.js HTTP: 영상 CDN 직접 다운로드 ($0)
 *
 * 사용법: node crawlers/douyin-weekly-v5.js
 */

import Hyperbrowser from "@hyperbrowser/sdk";
import puppeteer from "puppeteer-core";
import dotenv from "dotenv";
import { writeFileSync, readFileSync, existsSync, mkdirSync, statSync } from "fs";
import path from "path";
import https from "https";
import http from "http";

dotenv.config();

// ======================== 설정 ========================
const CONFIG = {
  dateStart: "2026-03-16T00:00:00+08:00",
  dateEnd: "2026-03-22T23:59:59+08:00",

  downloadVideos: true,
  preferredQuality: "720p",

  delayBetweenApi: 2000,
  delayBetweenDownloads: 1000,   // 직접 다운로드라 더 빠르게
  delayBetweenAccounts: 3000,

  outputDir: "../output/douyin-weekly-0316-v5",
  accountsFile: "../data/douyin-accounts.json",
  secuidMapFile: "../data/douyin-secuid-map.json",

  maxAccountsPerSession: 5,
  sessionTimeout: 300000,

  maxRetries: 2,
  skipExisting: true,

  // v3 신규
  sortTypes: [0, 1],       // 멀티소트: 0(시간순) + 1(인기순)
  pageSize: 35,             // 페이지 사이즈 (기본 20 → 35)
  gapWarningDays: 3,        // 이 일수 이상 간격이면 경고
};

const DATE_START_TS = Math.floor(new Date(CONFIG.dateStart).getTime() / 1000);
const DATE_END_TS = Math.floor(new Date(CONFIG.dateEnd).getTime() / 1000);

// ======================== 유틸리티 ========================
const client = new Hyperbrowser({ apiKey: process.env.HYPERBROWSER_API_KEY });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function log(msg) {
  const ts = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  console.log(`[${ts}] ${msg}`);
}

function ensureDir(dir) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

function safeName(name) {
  return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim();
}

function loadProgress() {
  const progressFile = path.join(CONFIG.outputDir, "progress.json");
  if (existsSync(progressFile)) {
    try { return JSON.parse(readFileSync(progressFile, "utf-8")); } catch { return {}; }
  }
  return {};
}

function saveProgress(progress) {
  const progressFile = path.join(CONFIG.outputDir, "progress.json");
  writeFileSync(progressFile, JSON.stringify(progress, null, 2), "utf-8");
}

async function removeAllOverlays(page) {
  return page.evaluate(() => {
    let removed = 0;
    document.querySelectorAll("*").forEach((el) => {
      const style = getComputedStyle(el);
      const zIndex = parseInt(style.zIndex) || 0;
      if ((style.position === "fixed" || style.position === "absolute") && zIndex >= 1000) {
        el.remove();
        removed++;
      }
    });
    document.body.style.overflow = "auto";
    return removed;
  });
}

async function callApi(page, apiPath) {
  const key = `__api_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  await page.evaluate((p, k) => {
    window[k] = null;
    fetch(p, { credentials: "include" })
      .then((r) => r.text())
      .then((t) => { window[k] = { ok: true, text: t }; })
      .catch((e) => { window[k] = { ok: false, err: e.message }; });
  }, apiPath, key);

  for (let i = 0; i < 40; i++) {
    await sleep(500);
    const r = await page.evaluate((k) => window[k], key);
    if (r) {
      await page.evaluate((k) => { delete window[k]; }, key).catch(() => {});
      if (!r.ok) return { error: r.err };
      try { return JSON.parse(r.text); } catch { return { parseError: true, text: r.text?.substring(0, 500) }; }
    }
  }
  return { error: "timeout" };
}

// ======================== sec_uid 매핑 로드 ========================
function loadSecUidMap() {
  if (!existsSync(CONFIG.secuidMapFile)) {
    log(`경고: sec_uid 매핑 파일 없음: ${CONFIG.secuidMapFile}`);
    return {};
  }
  const raw = JSON.parse(readFileSync(CONFIG.secuidMapFile, "utf-8"));
  const map = {};
  for (const [k, v] of Object.entries(raw)) {
    if (!k.startsWith("_")) map[k] = v;
  }
  return map;
}

// ======================== 프로필 수집 ========================
async function collectProfile(page, secUid) {
  const params = new URLSearchParams({
    device_platform: "webapp", aid: "6383",
    sec_user_id: secUid, cookie_enabled: "true", platform: "PC",
  });
  const data = await callApi(page, `/aweme/v1/web/user/profile/other/?${params}`);
  const user = data?.user;
  if (!user) return null;

  return {
    nickname: user.nickname,
    uniqueId: user.unique_id,
    signature: user.signature,
    followerCount: user.follower_count,
    followingCount: user.following_count,
    totalFavorited: user.total_favorited,
    awemeCount: user.aweme_count,
    customVerify: user.custom_verify,
    avatarUrl: user.avatar_larger?.url_list?.[0],
    profileUrl: `https://www.douyin.com/user/${secUid}`,
    secUid,
  };
}

// ======================== 게시물 객체 빌더 ========================
function buildPostObject(p, source) {
  return {
    awemeId: p.aweme_id,
    desc: p.desc || "",
    createTime: p.create_time || 0,
    createDate: new Date((p.create_time || 0) * 1000).toISOString(),
    author: p.author?.nickname || "",
    music: `${p.music?.title || ""} - ${p.music?.author || ""}`,
    statistics: {
      likes: p.statistics?.digg_count || 0,
      comments: p.statistics?.comment_count || 0,
      shares: p.statistics?.share_count || 0,
      favorites: p.statistics?.collect_count || 0,
      plays: p.statistics?.play_count || 0,
    },
    hashtags: (p.text_extra || []).filter((t) => t.hashtag_name).map((t) => `#${t.hashtag_name}`),
    videoUrl: `https://www.douyin.com/video/${p.aweme_id}`,
    coverUrl: p.video?.cover?.url_list?.[0] || "",
    duration: p.video?.duration || 0,
    isTop: false,
    source,
  };
}

// ======================== 리스트 API 페이지네이션 (단일 sort_type) ========================
async function paginateListApi(page, secUid, sortType) {
  const posts = [];
  const seenIds = new Set();
  let maxCursor = "0";
  let hasMore = true;
  let pageNum = 0;
  let consecutiveOlder = 0;
  const MAX_CONSECUTIVE_OLDER = 10;
  const MAX_PAGES = 15;

  while (hasMore && consecutiveOlder < MAX_CONSECUTIVE_OLDER && pageNum < MAX_PAGES) {
    pageNum++;
    const params = new URLSearchParams({
      device_platform: "webapp", aid: "6383",
      sec_user_id: secUid, max_cursor: maxCursor,
      count: String(CONFIG.pageSize), cookie_enabled: "true", platform: "PC",
      publish_video_strategy_type: "2",
    });
    if (sortType !== undefined) params.set("sort_type", String(sortType));

    const data = await callApi(page, `/aweme/v1/web/aweme/post/?${params}`);
    if (!data?.aweme_list?.length) break;

    for (const p of data.aweme_list) {
      if (seenIds.has(p.aweme_id)) continue;
      seenIds.add(p.aweme_id);

      const ct = p.create_time || 0;

      if (ct > DATE_END_TS) { consecutiveOlder = 0; continue; }
      if (ct < DATE_START_TS) { consecutiveOlder++; continue; }

      consecutiveOlder = 0;
      posts.push(buildPostObject(p, `list_api_sort${sortType ?? "default"}`));
    }

    hasMore = data.has_more === 1 || data.has_more === true;
    maxCursor = String(data.max_cursor || "0");
    if (hasMore && consecutiveOlder < MAX_CONSECUTIVE_OLDER) await sleep(CONFIG.delayBetweenApi);
  }

  return posts;
}

// ======================== 멀티소트 수집 ========================
async function collectWeeklyPosts(page, secUid) {
  const allPosts = new Map();

  for (let si = 0; si < CONFIG.sortTypes.length; si++) {
    const sortType = CONFIG.sortTypes[si];
    const posts = await paginateListApi(page, secUid, sortType);

    let newCount = 0;
    for (const p of posts) {
      if (!allPosts.has(p.awemeId)) {
        allPosts.set(p.awemeId, p);
        newCount++;
      }
    }

    const label = sortType === 0 ? "시간순" : sortType === 1 ? "인기순" : `sort${sortType}`;
    log(`    [sort=${sortType} ${label}] ${posts.length}개 (신규 ${newCount}개)`);

    if (si < CONFIG.sortTypes.length - 1) await sleep(CONFIG.delayBetweenApi);
  }

  const result = [...allPosts.values()];
  result.sort((a, b) => b.createTime - a.createTime);
  return result;
}

// ======================== 날짜 간격 분석 ========================
function analyzeDateGaps(posts, accountName) {
  if (posts.length < 2) return null;

  const sorted = [...posts].sort((a, b) => a.createTime - b.createTime);
  const gaps = [];

  for (let i = 1; i < sorted.length; i++) {
    const gapDays = (sorted[i].createTime - sorted[i - 1].createTime) / 86400;
    if (gapDays >= CONFIG.gapWarningDays) {
      gaps.push({
        from: sorted[i - 1].createDate.substring(0, 10),
        to: sorted[i].createDate.substring(0, 10),
        gapDays: Math.round(gapDays * 10) / 10,
        fromId: sorted[i - 1].awemeId,
        toId: sorted[i].awemeId,
      });
    }
  }

  // 시작일~첫 게시물 간격 확인
  if (sorted.length > 0) {
    const firstGap = (sorted[0].createTime - DATE_START_TS) / 86400;
    if (firstGap >= CONFIG.gapWarningDays) {
      gaps.unshift({
        from: CONFIG.dateStart.substring(0, 10),
        to: sorted[0].createDate.substring(0, 10),
        gapDays: Math.round(firstGap * 10) / 10,
        fromId: "(범위시작)",
        toId: sorted[0].awemeId,
      });
    }
  }

  // 마지막 게시물~종료일 간격 확인
  if (sorted.length > 0) {
    const lastGap = (DATE_END_TS - sorted[sorted.length - 1].createTime) / 86400;
    if (lastGap >= CONFIG.gapWarningDays) {
      gaps.push({
        from: sorted[sorted.length - 1].createDate.substring(0, 10),
        to: CONFIG.dateEnd.substring(0, 10),
        gapDays: Math.round(lastGap * 10) / 10,
        fromId: sorted[sorted.length - 1].awemeId,
        toId: "(범위끝)",
      });
    }
  }

  if (gaps.length > 0) {
    log(`  ⚠ ${accountName}: ${gaps.length}개 날짜 간격 경고`);
    for (const g of gaps) {
      log(`    ${g.from} ~ ${g.to} (${g.gapDays}일)`);
    }
    return { account: accountName, postsCount: posts.length, gaps };
  }

  return null;
}

// ======================== 화질 선택 ========================
function selectVideoUrl(video) {
  const bitRates = video.bit_rate || [];
  if (!bitRates.length) {
    const urls = (video.play_addr?.url_list || []).sort((a, b) =>
      (!a.includes("douyin.com") ? 0 : 1) - (!b.includes("douyin.com") ? 0 : 1)
    );
    return urls.length ? { urls, quality: "default", width: 0, height: 0, bitrate: 0 } : null;
  }

  const sorted = [...bitRates].sort((a, b) => b.bit_rate - a.bit_rate);
  let selected;
  switch (CONFIG.preferredQuality) {
    case "highest": selected = sorted[0]; break;
    case "1080p": selected = sorted.find((b) => b.play_addr?.height <= 1920) || sorted.at(-1); break;
    case "720p": selected = sorted.find((b) => b.play_addr?.height <= 1280) || sorted.at(-1); break;
    case "540p": selected = sorted.find((b) => b.play_addr?.height <= 1024) || sorted.at(-1); break;
    case "lowest": selected = sorted.at(-1); break;
    default: selected = sorted.find((b) => b.play_addr?.height <= 1280) || sorted[0];
  }

  const urls = (selected.play_addr?.url_list || []).sort((a, b) =>
    (!a.includes("douyin.com") ? 0 : 1) - (!b.includes("douyin.com") ? 0 : 1)
  );
  return {
    urls,
    quality: selected.gear_name,
    width: selected.play_addr?.width,
    height: selected.play_addr?.height,
    bitrate: selected.bit_rate,
  };
}

// ======================== 영상 직접 다운로드 (프록시 미경유 — 비용 $0) ========================
/**
 * v3: 브라우저 내 fetch → base64 → 파일 (HB 프록시 경유, ~$60)
 * v4: Node.js HTTP → CDN 직접 다운로드 (프록시 미경유, $0)
 */
function downloadVideoDirect(url, outputPath, maxRedirects = 5) {
  return new Promise((resolve, reject) => {
    const parsed = new URL(url);
    const proto = parsed.protocol === "https:" ? https : http;

    const options = {
      hostname: parsed.hostname,
      path: parsed.pathname + parsed.search,
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
      },
      timeout: 120000,
    };

    const req = proto.request(options, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
        if (maxRedirects <= 0) { reject(new Error("Too many redirects")); return; }
        downloadVideoDirect(res.headers.location, outputPath, maxRedirects - 1).then(resolve).catch(reject);
        return;
      }

      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode}`));
        return;
      }

      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        const buffer = Buffer.concat(chunks);
        if (buffer.length < 10000) {
          reject(new Error(`파일 너무 작음: ${buffer.length}B`));
          return;
        }
        writeFileSync(outputPath, buffer);
        resolve({ ok: true, size: buffer.length });
      });
      res.on("error", reject);
    });

    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("다운로드 타임아웃")); });
    req.end();
  });
}

// 비용 추적
const costTracker = {
  directDownload: { count: 0, totalBytes: 0, failedCount: 0 },
  sessions: { count: 0, totalDurationMs: 0 },
};

// ======================== 세션 생성 ========================
async function createSession(retries = 3) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    let session, browser;
    try {
      log(`새 세션 생성... (시도 ${attempt}/${retries})`);
      session = await client.sessions.create({
        useStealth: true, solveCaptchas: true,
        proxyServer: "pr.oxylabs.io:7777",
        proxyServerUsername: "customer-prcs_data1_LpjIC-cc-cn",
        proxyServerPassword: "Prcsdata_1234",
        acceptCookies: true, locales: ["zh"], screen: { width: 1920, height: 1080 },
      });
      log(`세션 ID: ${session.id}`);

      browser = await puppeteer.connect({
        browserWSEndpoint: session.wsEndpoint,
        defaultViewport: { width: 1920, height: 1080 },
        protocolTimeout: 180000,
      });
      const page = (await browser.pages())[0] || (await browser.newPage());

      log("쿠키 확보 중...");
      await page.goto("https://www.douyin.com/", { waitUntil: "domcontentloaded", timeout: 60000 });
      await sleep(8000);
      await removeAllOverlays(page);
      await sleep(3000);
      await removeAllOverlays(page);
      const cookies = await page.cookies();
      log(`쿠키: ${cookies.length}개`);

      costTracker.sessions.count++;
      return { session, browser, page };
    } catch (e) {
      log(`세션 생성 실패 (시도 ${attempt}/${retries}): ${e.message}`);
      if (browser) try { await browser.disconnect(); } catch {}
      if (session) try { await client.sessions.stop(session.id); } catch {}
      if (attempt === retries) throw new Error(`세션 생성 ${retries}회 실패: ${e.message}`);
      log("5초 후 재시도...");
      await sleep(5000);
    }
  }
}

async function closeSession(session, browser) {
  if (browser) try { await browser.disconnect(); } catch {}
  if (session) try { await client.sessions.stop(session.id); log("세션 종료."); } catch {}
}

// ======================== 단일 계정 처리 ========================
async function processAccount(page, accountName, secUid, progress) {
  const dirName = safeName(accountName);
  const accountDir = path.join(CONFIG.outputDir, dirName);
  ensureDir(accountDir);

  const result = { name: accountName, status: "pending", secUid, profile: null, posts: [], videosDownloaded: 0, gapWarning: null };

  if (progress[accountName]?.status === "done" && CONFIG.skipExisting) {
    log(`건너뜀 (이미 완료): ${accountName}`);
    return { ...progress[accountName], skipped: true };
  }

  log(`sec_uid: ${secUid.substring(0, 30)}...`);

  // Step 1: 프로필 수집
  log(`프로필 수집 중...`);
  const profile = await collectProfile(page, secUid);
  if (!profile) {
    log(`✗ 프로필 수집 실패`);
    result.status = "profile_failed";
    return result;
  }
  result.profile = profile;

  if (profile.nickname !== accountName && !profile.nickname.includes(accountName) && !accountName.includes(profile.nickname)) {
    log(`⚠ 닉네임 불일치! 예상: "${accountName}" → 실제: "${profile.nickname}"`);
    result.nicknameMatch = false;
  } else {
    result.nicknameMatch = true;
  }

  log(`✓ ${profile.nickname} | 팔로워: ${profile.followerCount} | 게시물: ${profile.awemeCount}`);
  await sleep(CONFIG.delayBetweenApi);

  // Step 2: 멀티소트 리스트 API로 주간 게시물 수집
  log(`주간 게시물 수집 중 (멀티소트: ${CONFIG.sortTypes.join(",")})...`);
  const allPosts = await collectWeeklyPosts(page, secUid);
  result.posts = allPosts;
  log(`✓ 범위 내 게시물 합집합: ${allPosts.length}개`);

  // Step 2.5: 날짜 간격 분석
  if (allPosts.length >= 1) {
    const gapWarning = analyzeDateGaps(allPosts, accountName);
    result.gapWarning = gapWarning;
  }

  // Step 3: 영상 다운로드
  if (CONFIG.downloadVideos && allPosts.length > 0) {
    const videoDir = path.join(accountDir, "videos");
    ensureDir(videoDir);

    for (let i = 0; i < allPosts.length; i++) {
      const post = allPosts[i];
      const filePath = path.join(videoDir, `${post.awemeId}.mp4`);

      if (CONFIG.skipExisting && existsSync(filePath) && statSync(filePath).size > 10000) {
        post.localFile = `${post.awemeId}.mp4`;
        post.fileSize = statSync(filePath).size;
        result.videosDownloaded++;
        log(`  [${i + 1}/${allPosts.length}] 건너뜀 (존재): ${post.awemeId}`);
        continue;
      }

      const detailParams = new URLSearchParams({
        device_platform: "webapp", aid: "6383",
        aweme_id: post.awemeId, cookie_enabled: "true", platform: "PC",
      });
      const detailData = await callApi(page, `/aweme/v1/web/aweme/detail/?${detailParams}`);
      const video = detailData?.aweme_detail?.video;

      if (!video) {
        log(`  [${i + 1}/${allPosts.length}] ✗ 영상 API 실패: ${post.awemeId}`);
        await sleep(CONFIG.delayBetweenApi);
        continue;
      }

      const selected = selectVideoUrl(video);
      if (!selected?.urls?.length) {
        log(`  [${i + 1}/${allPosts.length}] ✗ URL 없음`);
        continue;
      }

      log(`  [${i + 1}/${allPosts.length}] 직접 다운로드: ${post.desc.substring(0, 30)}...`);

      // v4: CDN 직접 다운로드 (프록시 미경유 → 비용 $0)
      let downloaded = false;
      for (let u = 0; u < selected.urls.length; u++) {
        try {
          const dlResult = await downloadVideoDirect(selected.urls[u], filePath);
          post.localFile = `${post.awemeId}.mp4`;
          post.fileSize = dlResult.size;
          result.videosDownloaded++;
          costTracker.directDownload.count++;
          costTracker.directDownload.totalBytes += dlResult.size;
          log(`    ✓ ${(dlResult.size / 1024 / 1024).toFixed(2)} MB [직접 다운로드]`);
          downloaded = true;
          break;
        } catch (e) {
          if (u < selected.urls.length - 1) {
            log(`    ✗ URL ${u + 1} 실패 (${e.message}), 대체 시도...`);
            await sleep(1000);
          }
        }
      }
      if (!downloaded) { costTracker.directDownload.failedCount++; log(`    ✗ 모든 URL 실패`); }

      if (i < allPosts.length - 1) await sleep(CONFIG.delayBetweenDownloads);
    }
  }

  // 결과 저장
  result.status = "done";
  const outputData = {
    profile,
    posts: allPosts,
    collectedAt: new Date().toISOString(),
  };
  writeFileSync(path.join(accountDir, "data.json"), JSON.stringify(outputData, null, 2), "utf-8");

  return result;
}

// ======================== 메인 ========================
async function main() {
  console.log("=======================================================");
  console.log("  더우인(抖音) 주간 대량 수집 v5");
  console.log("  HB 세션/캡차 + Oxylabs 프록시 + 직접 다운로드");
  console.log("=======================================================");
  console.log(`  기간:       ${CONFIG.dateStart.substring(0, 10)} ~ ${CONFIG.dateEnd.substring(0, 10)}`);
  console.log(`  소트:       ${CONFIG.sortTypes.join(", ")}`);
  console.log(`  페이지:     ${CONFIG.pageSize}`);
  console.log("-------------------------------------------------------");
  console.log("  브라우저:     Hyperbrowser (스텔스 + 캡차)");
  console.log("  프록시:       Oxylabs (HB 프록시 미사용)");
  console.log("  API 호출:     브라우저 내 fetch");
  console.log("  영상 다운로드: Node.js 직접 ($0)");
  console.log("=======================================================\n");

  const accounts = JSON.parse(readFileSync(CONFIG.accountsFile, "utf-8"));
  const uniqueAccounts = [...new Set(accounts)];
  const secUidMap = loadSecUidMap();

  const withSecUid = uniqueAccounts.filter((a) => secUidMap[a]);
  const withoutSecUid = uniqueAccounts.filter((a) => !secUidMap[a]);

  log(`계정 목록: ${uniqueAccounts.length}개 (중복 제거 후)`);
  log(`sec_uid 있음: ${withSecUid.length}개 → 수집 대상`);
  log(`sec_uid 없음: ${withoutSecUid.length}개 → 건너뜀`);

  if (withoutSecUid.length > 0) {
    log(`건너뛴 계정: ${withoutSecUid.join(", ")}`);
  }

  ensureDir(CONFIG.outputDir);
  const progress = loadProgress();
  const startTime = Date.now();

  const stats = {
    total: uniqueAccounts.length,
    targetAccounts: withSecUid.length,
    skippedNoSecUid: withoutSecUid.length,
    done: 0, failed: 0, skipped: 0,
    totalPosts: 0, totalVideos: 0,
  };

  const gapWarnings = [];  // 날짜 간격 경고 수집

  let session = null, browser = null, page = null;
  let accountsInSession = 0;

  try {
    for (let idx = 0; idx < withSecUid.length; idx++) {
      const accountName = withSecUid[idx];
      const secUid = secUidMap[accountName];

      if (!session || accountsInSession >= CONFIG.maxAccountsPerSession) {
        if (session) {
          await closeSession(session, browser);
          await sleep(3000);
        }
        const s = await createSession();
        session = s.session;
        browser = s.browser;
        page = s.page;
        accountsInSession = 0;
      }

      console.log(`\n${"=".repeat(50)}`);
      log(`[${idx + 1}/${withSecUid.length}] 계정: ${accountName}`);
      console.log("=".repeat(50));

      try {
        const result = await processAccount(page, accountName, secUid, progress);

        if (result.gapWarning) {
          gapWarnings.push(result.gapWarning);
        }

        progress[accountName] = {
          secUid: result.secUid,
          status: result.status,
          postsCount: result.posts?.length || 0,
          videosCount: result.videosDownloaded || 0,
          processedAt: new Date().toISOString(),
        };
        saveProgress(progress);

        if (result.skipped) {
          stats.skipped++;
          stats.totalPosts += result.postsCount || 0;
          stats.totalVideos += result.videosCount || 0;
        } else if (result.status === "done") {
          stats.done++;
          stats.totalPosts += result.posts?.length || 0;
          stats.totalVideos += result.videosDownloaded || 0;
        } else {
          stats.failed++;
        }

        accountsInSession++;

        log(`현재 진행: 완료 ${stats.done} / 건너뜀 ${stats.skipped} / 실패 ${stats.failed} / 대상 ${stats.targetAccounts}`);

      } catch (e) {
        log(`✗ 계정 처리 에러: ${e.message}`);
        stats.failed++;
        progress[accountName] = { status: "error", error: e.message, processedAt: new Date().toISOString() };
        saveProgress(progress);

        if (e.message.includes("Protocol") || e.message.includes("disconnect") || e.message.includes("closed")) {
          log("세션 에러 감지, 세션 재생성...");
          try { await closeSession(session, browser); } catch {}
          session = null;
          browser = null;
          page = null;
          accountsInSession = CONFIG.maxAccountsPerSession;
        }
      }

      if (idx < withSecUid.length - 1) {
        await sleep(CONFIG.delayBetweenAccounts);
      }
    }
  } finally {
    await closeSession(session, browser);
  }

  // 최종 보고
  const elapsed = Math.floor((Date.now() - startTime) / 1000);
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;

  const dlBytes = costTracker.directDownload.totalBytes;

  console.log("\n" + "=".repeat(70));
  console.log("  최종 수집 결과 (v4 — 직접 다운로드)");
  console.log("=".repeat(70));
  log(`전체 계정 수:      ${stats.total}개`);
  log(`수집 대상:         ${stats.targetAccounts}개 (sec_uid 보유)`);
  log(`sec_uid 없음:      ${stats.skippedNoSecUid}개 (건너뜀)`);
  log(`성공:              ${stats.done}개`);
  log(`건너뜀 (기완료):   ${stats.skipped}개`);
  log(`실패:              ${stats.failed}개`);
  log(`수집 게시물 수:    ${stats.totalPosts}개`);
  log(`다운로드 영상:     ${stats.totalVideos}개`);
  log(`소요 시간:         ${minutes}분 ${seconds}초`);
  log(`출력 디렉토리:     ${CONFIG.outputDir}/`);

  // 비용 분석
  console.log("\n" + "=".repeat(70));
  console.log("  비용 분석 — v3 (HB 올인) vs v4 (직접 다운로드)");
  console.log("=".repeat(70));

  console.log("\n--- 영상 직접 다운로드 ---");
  log(`성공:              ${costTracker.directDownload.count}개`);
  log(`실패:              ${costTracker.directDownload.failedCount}개`);
  log(`다운로드 크기:     ${(dlBytes / 1024 / 1024).toFixed(2)} MB (${(dlBytes / 1024 / 1024 / 1024).toFixed(3)} GB)`);
  log(`프록시 비용:       $0 (직접 CDN 다운로드)`);

  console.log("\n--- v5 비용 구조 ---");
  console.log("  HB 세션/스텔스/캡차: HB 세션 비용만 (프록시 비용 없음)");
  console.log("  프록시: Oxylabs (자체 보유)");
  console.log("  영상 다운로드: 직접 $0");
  console.log(`  영상 ${(dlBytes / 1024 / 1024).toFixed(0)} MB 직접 다운로드`);

  // 날짜 간격 경고 보고서
  if (gapWarnings.length > 0) {
    console.log("\n" + "=".repeat(60));
    log(`⚠ 날짜 간격 경고: ${gapWarnings.length}개 계정`);
    console.log("=".repeat(60));
    for (const w of gapWarnings) {
      log(`  ${w.account}: ${w.postsCount}개 게시물, ${w.gaps.length}개 간격`);
      for (const g of w.gaps) {
        log(`    ${g.from} ~ ${g.to} (${g.gapDays}일)`);
      }
    }
    writeFileSync(
      path.join(CONFIG.outputDir, "gap_warnings.json"),
      JSON.stringify(gapWarnings, null, 2), "utf-8"
    );
    log("날짜 간격 경고 저장: gap_warnings.json");
  } else {
    log("날짜 간격 경고: 없음 ✓");
  }

  const summary = {
    ...stats,
    mode: "v4-direct-download",
    dateRange: { start: CONFIG.dateStart, end: CONFIG.dateEnd },
    sortTypes: CONFIG.sortTypes,
    pageSize: CONFIG.pageSize,
    costTracking: {
      directDownload: {
        count: costTracker.directDownload.count,
        failedCount: costTracker.directDownload.failedCount,
        totalBytes: dlBytes,
        totalMB: Math.round(dlBytes / 1024 / 1024 * 100) / 100,
      },
      costEstimate: {
        v3_hyperbrowser_allin: "~$82-95",
        v4_direct_download: {
          hyperbrowser: "~$15-20 (캡차+스텔스+API)",
          videoDownload: `$0 (직접 ${(dlBytes / 1024 / 1024).toFixed(0)}MB)`,
          total: "~$15-20",
        },
        savings: "~75-80%",
      },
    },
    gapWarningsCount: gapWarnings.length,
    elapsedSeconds: elapsed,
    completedAt: new Date().toISOString(),
    accountsWithoutSecUid: withoutSecUid,
  };
  writeFileSync(path.join(CONFIG.outputDir, "summary.json"), JSON.stringify(summary, null, 2), "utf-8");
  log("최종 통계 저장 완료: summary.json");
}

main().catch(console.error);
