#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reels 分析工作台 —— 本地服务
- 单条模式：粘贴 Facebook Reels/视频链接 → 下载 + 简介 + 口播文案
- 批量模式：主页链接（或粘贴多条链接）→ 逐条编号下载、识别文案、抓指标与评论 → 导出 Excel
启动：python3 app.py  然后浏览器打开 http://127.0.0.1:8756
"""
import os
import re
import sys
import time
import json
import uuid
import threading
import multiprocessing
from datetime import datetime

# 中国大陆访问 HuggingFace 模型仓库较慢/不通，默认走镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
TMP_DIR = os.path.join(BASE_DIR, ".tmp")      # 仅转写不保存时的临时视频目录（转写后自动清空）
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
DATA_DIR = os.path.join(BASE_DIR, "data")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")
for _d in (DOWNLOADS_DIR, EXPORTS_DIR, JOBS_DIR, DATA_DIR):
    os.makedirs(_d, exist_ok=True)

# 把 imageio-ffmpeg 自带的 ffmpeg 加入 PATH，供 yt-dlp 合并音视频使用（如需要）
try:
    import imageio_ffmpeg
    _ff_bin = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    if _ff_bin and _ff_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ff_bin + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import yt_dlp
from flask import Flask, jsonify, request, send_from_directory, send_file


def _patch_pyav_compat():
    """PyAV >= 19 移除了 av.open 的 metadata_errors 参数，faster-whisper 1.2.x 依赖它。
    此处打兼容补丁，避免因 av 版本升级导致转写全挂。"""
    try:
        import av
        major = int(str(av.__version__).split(".")[0])
        if major >= 19:
            import av.container.core as _core
            _orig_open = _core.open
            def _compat_open(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                return _orig_open(*args, **kwargs)
            _core.open = _compat_open
            av.open = _compat_open
    except Exception:
        pass


_patch_pyav_compat()

app = Flask(__name__)

JOBS = {}          # job_id -> 任务状态（内存；长任务同时落盘 jobs/<id>.json）
_MODEL_CACHE = {}  # model_name -> WhisperModel 实例
_MODEL_LOCK = threading.Lock()

ALLOWED_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
MAX_VIDEOS = 300   # 批量模式安全上限
PAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pages")
MAX_COMMENTS_PER_VIDEO = 80


# ----------------------------- 任务状态存取 -----------------------------

def _save_job(job):
    try:
        with open(os.path.join(JOBS_DIR, job["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in job.items() if k != "lock"}, f, ensure_ascii=False)
    except Exception:
        pass


def _load_job(job_id):
    # 子进程模式：任务在独立进程更新文件，主进程内存可能是旧值 → 文件优先
    fp = os.path.join(JOBS_DIR, job_id + ".json")
    if os.path.exists(fp):
        try:
            with open(fp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return JOBS.get(job_id)


# ----------------------------- 工具函数 -----------------------------

def _progress_hook(job, d):
    try:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                job["percent"] = max(job.get("percent") or 0, 5 + int(30 * d.get("downloaded_bytes", 0) / total))
        elif d.get("status") == "finished":
            job["percent"] = max(job.get("percent") or 0, 40)
    except Exception:
        pass


def _get_model(name):
    if name not in ALLOWED_MODELS:
        name = "small"
    with _MODEL_LOCK:
        if name not in _MODEL_CACHE:
            from faster_whisper import WhisperModel
            _MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _MODEL_CACHE[name]


def _extract_page_name(url):
    if not url:
        return ""
    m = re.search(r"facebook\.com/profile\.php\?id=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"facebook\.com/([^/?#]+)", url)
    if m:
        return m.group(1)
    return url.strip().strip("/")


def _scraper_cookies_file():
    """facebook-scraper 的解析器会跳过所有以 '#' 开头的行（包括 #HttpOnly_ 前缀的会话 cookie），
    导致 xs/datr/fr 等关键 cookie 丢失。这里生成去掉 '#HttpOnly_' 前缀的副本供 scraper 使用；
    yt-dlp 的 MozillaCookieJar 原生支持 #HttpOnly_，仍用原始 cookies.txt。"""
    if not os.path.exists(COOKIES_FILE):
        return None
    out = os.path.join(BASE_DIR, "cookies_scraper.txt")
    with open(COOKIES_FILE, encoding="utf-8") as f:
        content = f.read()
    fixed = "\n".join(
        (line[len("#HttpOnly_"):] if line.startswith("#HttpOnly_") else line)
        for line in content.splitlines()
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(fixed)
    return out


def _ytdl_opts(job):
    opts = {
        "outtmpl": os.path.join(DOWNLOADS_DIR, "%(id)s.%(ext)s"),
        "format": "best[ext=mp4]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "progress_hooks": [lambda d: _progress_hook(job, d)],
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _transcribe(path, model_name, job):
    model = _get_model(model_name)
    segments, seg_info = model.transcribe(path, language=None, vad_filter=True)
    text_parts, timed = [], []
    for s in segments:
        t = (s.text or "").strip()
        if not t:
            continue
        text_parts.append(t)
        timed.append({"start": round(s.start, 1), "end": round(s.end, 1), "text": t})
    return " ".join(text_parts), timed, getattr(seg_info, "language", None)


def _fb_fetch(url, timeout=25):
    """用 curl_cffi 模拟 Chrome TLS 指纹 + 登录 cookies 抓取 Facebook 页面。
    普通 requests 会被 FB 反爬以 HTTP 400 拒绝，curl_cffi 可正常返回完整 HTML。"""
    import http.cookiejar
    from curl_cffi import requests as cr
    cj = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
    cj.load(ignore_discard=True, ignore_expires=True)
    ck = {c.name: c.value for c in cj}
    last = None
    for _ in range(4):
        try:
            r = cr.get(url, impersonate="chrome", cookies=ck, timeout=(8, 30))
            if r.status_code == 200 and len(r.text) > 50000:
                return r.text
            last = f"HTTP {r.status_code}, 长度 {len(r.text)}"
        except Exception as e:
            last = str(e)[:150]
            time.sleep(2)
    raise RuntimeError(f"抓取 {url} 失败：{last}（需要有效的登录 cookies）")


def _unescape_js_text(s):
    """还原 JSON 字符串中的 Unicode 转义（\\uXXXX）、斜杠、引号、换行等转义。
    先按代理对重组（FB 的 emoji 用 \\ud83d\\udd25 这类 UTF-16 代理对），再统一还原。"""
    if not s:
        return ""
    try:
        s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
        s = s.encode("utf-16", "surrogatepass").decode("utf-16")
        s = s.encode("utf-8", "ignore").decode("utf-8")  # 丢弃残留的孤立代理字符
    except Exception:
        pass
    return (s.replace("\\/", "/").replace("\\n", "\n").replace("\\t", " ")
             .replace('\\"', '"').replace("\\\\", "\\"))


def _clean_win(win):
    """把 FB 内嵌 JSON 的 \" 转义形式统一为普通引号，使正则只需匹配一种写法。"""
    return win.replace('\\"', '"')


def _enumerate_page_videos(page, job=None, max_videos=MAX_VIDEOS):
    """抓取主页视频列表并解析全部视频帖（curl_cffi + 嵌入 JSON 解析）。
    主数据源：主页的 /videos 标签（视频网格，含 URL/标题/文案/观看数/时长）；
    辅数据源：主页时间线（补点赞/评论/分享互动数）。两者按 video_id 合并去重。
    返回 [ {url, post_id, video_id, caption, title, publish_time, likes, comments, shares, views, duration} ]
    覆盖范围：/videos 标签首屏视频网格（通常为该主页公开视频的一部分，可能不含最早的历史视频）。"""
    html = _fb_fetch(f"https://www.facebook.com/{page}/videos")
    videos, by_id = [], {}
    pat = re.compile(r'(?:\\"__typename\\":\\"Video\\",\\"id\\":\\"(\d+)\\"|"__typename":"Video","id":"(\d+)")')
    for m in pat.finditer(html):
        vid = m.group(1) or m.group(2)
        if not vid or vid in by_id:
            continue
        win = _clean_win(html[max(0, m.start() - 6000): m.end() + 4000])
        um = re.search(r'"canonical_uri_with_fallback":"(https?:\/\/www\.facebook\.com\/[^"]*?)"', win)
        pm = re.search(r'"publish_time":(\d+)', win)
        tm = re.search(r'"savable_title":\{"text":"((?:[^"]|\\.)*?)"', win)
        cm = re.search(r'"message":\{"text":"((?:[^"]|\\.)*?)","ranges"', win)
        if not cm:
            cm = re.search(r'"message":\{"text":"((?:[^"]|\\.)*?)","referenced_sticker"', win)
        vcm = re.search(r'"play_count":(\d+)', win)
        dm = re.search(r'"playable_duration":(\d+)', win)
        dum = re.search(r'"playable_url":"([^"]+\.mp4[^"]*?)"', win)
        if um:
            url = _unescape_js_text(um.group(1)).split("?")[0]
        else:
            url = f"https://www.facebook.com/video.php?v={vid}"
        rec = {
            "url": url, "post_id": "", "video_id": vid,
            "direct": _unescape_js_text(dum.group(1)) if dum else "",
            "caption": _unescape_js_text(cm.group(1)) if cm else "",
            "title": _unescape_js_text(tm.group(1)) if tm else "",
            "publish_time": int(pm.group(1)) if pm else None,
            "likes": None, "comments": None, "shares": None,
            "views": int(vcm.group(1)) if vcm else None,
            "duration": int(dm.group(1)) if dm else None,
        }
        by_id[vid] = rec
        videos.append(rec)
        if len(videos) >= max_videos:
            break

    # 时间线补互动数（点赞/评论/分享）与 reel 直达链接
    try:
        t_html = _fb_fetch(f"https://www.facebook.com/{page}")
        for m in pat.finditer(t_html):
            vid = m.group(1) or m.group(2)
            rec = by_id.get(vid)
            if not rec:
                continue
            win = _clean_win(t_html[max(0, m.start() - 30000): m.end() + 35000])
            oidm = re.search(r'"post_id":"(\d+)"', win)
            rcm = re.findall(r'"reaction_count":\{"count":(\d+)\}', win)
            scm = re.findall(r'"share_count":\{"count":(\d+)\}', win)
            ccm = re.findall(r'"comments":\{"total_count":(\d+)\}', win)
            um = re.search(r'"url":"(https?:\/\/www\.facebook\.com\/(?:reel|watch)\/\d+[\/]?)"', win)
            if oidm:
                rec["post_id"] = oidm.group(1)
            if not rec["title"]:
                tm = re.search(r'"title_with_entities":\{"text":"((?:[^"]|\\.)*?)"', win)
                if tm:
                    rec["title"] = _unescape_js_text(tm.group(1))
            if not rec["caption"]:
                cm = re.search(r'"message":\{"text":"((?:[^"]|\\.)*?)","referenced_sticker"', win)
                if cm:
                    rec["caption"] = _unescape_js_text(cm.group(1))
            if rcm:
                rec["likes"] = max(map(int, rcm))
            if scm:
                rec["shares"] = max(map(int, scm))
            if ccm:
                rec["comments"] = max(map(int, ccm))
            if um:
                rec["url"] = _unescape_js_text(um.group(1)).split("?")[0]
    except Exception:
        pass
    videos.sort(key=lambda r: r.get("publish_time") or 0, reverse=True)
    return videos


def _download_direct(url, path, job):
    """用 curl_cffi（Chrome 指纹）流式下载直链 mp4，避免 yt-dlp 页面抓取被 FB 限流。"""
    import http.cookiejar
    from curl_cffi import requests as cr
    cj = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
    cj.load(ignore_discard=True, ignore_expires=True)
    ck = {c.name: c.value for c in cj}
    last = None
    for attempt in range(3):
        try:
            r = cr.get(url, impersonate="chrome", cookies=ck, stream=True, timeout=(8, 60),
                       headers={"Referer": "https://www.facebook.com/"})
            try:
                if r.status_code != 200:
                    last = f"HTTP {r.status_code}"
                    continue
                total = int(r.headers.get("content-length") or 0)
                done = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                if total and done < total * 0.9:
                    last = f"不完整 ({done}/{total})"
                    continue
                return path
            finally:
                r.close()
        except Exception as e:
            last = str(e)[:150]
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
    raise RuntimeError(f"直链下载失败：{last}")


def _extract_direct_mp4(raw, strict=False):
    """从视频页 HTML 提取直链 mp4。strict=True 时只认 browser_native_hd_url/sd_url
    （token 有效、可直接下载）；strict=False 时额外退回 DASH 质量标签 / 最长 URL 兜底。"""
    def un(s):
        return (s.replace("\\/", "/").replace("\\u0026", "&")
                .replace("\\u0025", "%").replace("&amp;", "&"))
    m = re.search(r'"browser_native_hd_url":"((?:[^"\\]|\\.)*)"', raw)
    if m and m.group(1).startswith("https"):
        return un(m.group(1))
    m = re.search(r'"browser_native_sd_url":"((?:[^"\\]|\\.)*)"', raw)
    if m and m.group(1).startswith("https"):
        return un(m.group(1))
    if strict:
        return ""
    pairs = re.findall(r'FBQualityLabel=\\"?(\d+)p\\"?[^>]*?>\u003CBaseURL>(https:[^<]+?\.mp4[^<]*)', raw)
    if pairs:
        return un(max(pairs, key=lambda p: int(p[0]))[1])
    cands = re.findall(r'https:[^"\'\s]+?\.mp4[^"\'\s]*', raw)
    return un(sorted(set(cands), key=len)[-1]) if cands else ""


def _fetch_post_counts(rec):
    """从视频帖子页补抓互动数（点赞/分享/评论/观看）。失败静默，保留原值。"""
    vid = rec.get("video_id") or ""
    if not vid:
        m = re.search(r"[/?]v=(\d+)", rec.get("url") or "")
        if not m:
            m = re.search(r"/(?:reel|videos|watch)/(\d+)", rec.get("url") or "")
        vid = m.group(1) if m else ""
    if not vid:
        return
    try:
        html = _fb_fetch(f"https://www.facebook.com/video.php?v={vid}")
        win = _clean_win(html)
        rcm = re.findall(r'"reaction_count":\{"count":(\d+)\}', win)
        # 分享数：页面有多种结构，全部尝试取最大
        scm = (re.findall(r'"share_count":\{"count":(\d+)\}', win)
               + re.findall(r'"reshares":\{"count":(\d+)\}', win)
               + re.findall(r'"i18n_share_count":"([\d,]+)"', win))
        # 评论总数：comment_rendering_instance 或顶层 comments 结构
        ccm = (re.findall(r'"comments":\{"total_count":(\d+)\}', win)
               + re.findall(r'"comment_count":(\d+)', win)
               + re.findall(r'"top_level_comment_count":(\d+)', win))
        vcm = re.findall(r'"(?:play_count|video_view_count|view_count)":(\d+)', win)
        if rcm and rec.get("likes") is None:
            rec["likes"] = max(map(int, rcm))
        if scm and rec.get("shares") is None:
            rec["shares"] = max(map(int, [int(s.replace(",", "")) for s in scm]))
        if ccm and rec.get("comments") is None:
            rec["comments"] = max(map(int, ccm))
        # 打通字段：表格/导出统一用 comments_count
        if rec.get("comments") is not None:
            rec["comments_count"] = rec["comments"]
        if vcm and rec.get("views") is None:
            rec["views"] = max(map(int, vcm))
        if not rec.get("publish_time"):
            ptm = re.search(r'"(?:publish_time|creation_time)":(\d+)', win)
            if ptm:
                rec["publish_time"] = int(ptm.group(1))
                rec["upload_date"] = _fmt_time(rec["publish_time"])
        if rec.get("duration") is None:
            dm = re.search(r'"playable_duration":(\d+)', win)
            if dm:
                rec["duration"] = int(dm.group(1))
        if not rec.get("direct"):
            d = _extract_direct_mp4(html, strict=True)
            if not d:
                try:
                    mhtml = _fb_fetch(f"https://m.facebook.com/video.php?v={vid}", timeout=30)
                    d = _extract_direct_mp4(mhtml)
                except Exception:
                    pass
            if not d:
                d = _extract_direct_mp4(html)
            if d:
                rec["direct"] = d
        if not rec.get("title"):
            tm = re.search(r'"title_with_entities":\{"text":"((?:[^"]|\\.)*?)"', win)
            if tm:
                rec["title"] = _unescape_js_text(tm.group(1))[:300]
        if not rec.get("caption"):
            cm = re.search(r'"message":\{"text":"((?:[^"]|\\.)*?)"', win)
            if cm:
                rec["caption"] = _unescape_js_text(cm.group(1))[:2000]
    except Exception:
        pass


def _fmt_time(ts):
    """Unix 时间戳 → 'YYYY-MM-DD HH:MM'；无效返回空串。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _fetch_comments_best_effort(post_url, limit=MAX_COMMENTS_PER_VIDEO):
    """从 m.facebook.com（移动端）帖子/视频页抓评论——移动端服务端渲染，评论以 JSON 嵌入页面。
    返回 {"comments": [ {comment_id, author, text, time, likes} ], "count": 总评论数或 None}。"""
    url = post_url.replace("www.facebook.com", "m.facebook.com") if "www.facebook.com" in post_url else post_url
    if "://" not in url:
        url = "https://m.facebook.com" + url
    out, seen, count = [], set(), None
    try:
        for _page in range(3):  # 最多跟 3 次「查看更早评论」翻页
            html = _fb_fetch(url)
            win = _clean_win(html)
            cm = re.search(r'"total_comment_count":(\d+)', win)
            if cm and count is None:
                count = int(cm.group(1))
            for m in re.finditer(r'"body":\{"text":"((?:[^"]|\\.)*?)","ranges"', win):
                text = _unescape_js_text(m.group(1)).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                seg = win[max(0, m.start() - 1500): m.start() + 900]
                aid = re.search(r'"legacy_fbid":"(\d+)"', seg)
                nm = re.search(r'"author":\{"__typename":"User","id":"[^"]*?","name":"((?:[^"]|\\.)*?)"', seg)
                tm = re.search(r'"created_time":(\d+)', seg)
                lm = re.findall(r'"count_reduced":"(\d+)"', win[max(0, m.start() - 1500): m.start()])
                out.append({
                    "comment_id": aid.group(1) if aid else "",
                    "author": _unescape_js_text(nm.group(1)) if nm else "",
                    "text": text,
                    "time": _fmt_time(tm.group(1)) if tm else "",
                    "likes": int(lm[-1]) if lm else None,
                })
                if len(out) >= limit:
                    return {"comments": out, "count": count}
            more = re.search(r'href="(/[^"]*comment_id[^"]*)"[^>]*>[^<]*?(?:View|查看)[^<]*?(?:more|更多|留言)', html, re.I)
            if more and more.group(1) not in url:
                url = "https://m.facebook.com" + more.group(1)
                continue
            break
    except Exception:
        pass
    return {"comments": out, "count": count}


# ----------------------------- 单条任务 -----------------------------

def _run_single_job(job_id, url, model_name):
    job = JOBS[job_id]

    def setp(msg, pct=None):
        job["message"] = msg
        if pct is not None:
            job["percent"] = pct

    try:
        print(f"[job {job_id}] 单条处理 url={url} model={model_name}", flush=True)
        setp("正在获取视频信息并下载…", 3)
        with yt_dlp.YoutubeDL(_ytdl_opts(job)) as ydl:
            info = ydl.extract_info(url, download=True)
        if not info:
            raise RuntimeError("无法解析该链接，请确认链接是公开的 Facebook 视频/Reels")
        path = ydl.prepare_filename(info)
        if not os.path.exists(path):
            base = os.path.splitext(os.path.basename(path))[0]
            cands = [f for f in os.listdir(DOWNLOADS_DIR) if f.startswith(base)]
            if not cands:
                raise RuntimeError("视频已下载但文件未找到，请查看 downloads 目录")
            path = os.path.join(DOWNLOADS_DIR, cands[0])
        fname = os.path.basename(path)

        meta = {
            "title": info.get("title") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "upload_date": info.get("upload_date") or "",
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "like_count": info.get("like_count"),
            "comment_count": info.get("comment_count"),
            "description": (info.get("description") or "").strip(),
            "webpage_url": info.get("webpage_url") or url,
        }

        transcript, timed, language = None, [], None
        warning = None
        try:
            setp("正在识别视频语音文案（首次运行会先下载语音模型，请耐心等待）…", 60)
            transcript, timed, language = _transcribe(path, model_name, job)
        except Exception as e:
            print(f"[job {job_id}] 语音识别失败: {e}", flush=True)
            warning = f"语音识别失败：{e}。视频与简介已保留，可稍后重试或更换模型。"

        job["result"] = {
            "meta": meta, "file": fname,
            "download_url": "/downloads/" + fname,
            "transcript": transcript, "segments": timed[:800],
            "model": model_name, "language": language, "warning": warning,
        }
        setp("完成", 100)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    _save_job(job)


# ----------------------------- 批量任务 -----------------------------

def _enumerate_page_videos_smart(page, job=None, max_videos=MAX_VIDEOS):
    """主页视频全量枚举：本地深度抓取缓存优先（浏览器滚动抓到的全部 URL），
    再用实时首屏枚举去重合并，确保同一主页能拿到全部 Reels/视频。"""
    m = re.search(r"id=(\d+)", page) or re.search(r"(\d+)", page)
    page_id = m.group(1) if m else ""
    cache_path = os.path.join(PAGES_DIR, f"{page_id}_all_urls.txt") if page_id else ""
    cached_recs = []
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and "/" in ln]
            seen = set()
            for u in lines:
                mid = re.search(r"/(?:reel|videos|watch)/(\d+)", u)
                vid = mid.group(1) if mid else ""
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                cached_recs.append({"url": u.split("?")[0], "video_id": vid, "from_cache": True})
        except Exception as e:
            if job:
                job["log"].append(f"缓存读取失败: {str(e)[:80]}")
    if cached_recs:
        if job:
            job["log"].append(f"命中本地深度抓取缓存 {len(cached_recs)} 条（浏览器全量滚动抓取）")
        return cached_recs[:max_videos]

    live = _enumerate_page_videos(page, job, max_videos=max_videos)
    if job:
        job["log"].append(f"首屏枚举 {len(live)} 条（Facebook 公开页仅渲染首屏，历史视频建议使用浏览器深度抓取后缓存）")
    return live


def _process_one_video(no, total, v, source, model_name, want_comments):
    """批量任务中单条视频的处理：抓数据 → 下载（保存/临时）→ 转写 → 删临时。
    运行在并发线程池内：不写 job 状态（避免并发写文件冲突），只返回结果。"""
    url = v["url"] if isinstance(v, dict) else v
    rec = {
        "no": no, "url": url,
        "video_id": v.get("video_id", "") if isinstance(v, dict) else "",
        "title": v.get("title", "") if isinstance(v, dict) else "",
        "caption": v.get("caption", "") if isinstance(v, dict) else "",
        "post_id": v.get("post_id", "") if isinstance(v, dict) else "",
        "publish_time": v.get("publish_time") if isinstance(v, dict) else None,
        "upload_date": _fmt_time(v.get("publish_time")) if isinstance(v, dict) and v.get("publish_time") else "",
        "likes": v.get("likes") if isinstance(v, dict) else None,
        "comments_count": v.get("comments") if isinstance(v, dict) else None,
        "shares": v.get("shares") if isinstance(v, dict) else None,
        "views": v.get("views") if isinstance(v, dict) else None,
        "duration": v.get("duration") if isinstance(v, dict) else None,
        "status": "处理中",
    }
    comments = []
    try:
        if not rec.get("video_id"):
            m = re.search(r"[/?]v=(\d+)", url) or re.search(r"/(?:reel|videos|watch)/(\d+)", url)
            if m:
                rec["video_id"] = m.group(1)
        _fetch_post_counts(rec)  # 优先从帖子页补互动数+直链+文案+发布时间
        direct = rec.get("direct") or (v.get("direct", "") if isinstance(v, dict) else "")
        dl_file = None
        save_video = not bool(source.get("skip_download"))    # 用户要保存视频到本地
        need_trans = not bool(source.get("skip_transcribe"))  # 用户要口播文案
        if direct and (save_video or need_trans):
            vid = rec.get("video_id") or ""
            if not vid:
                m = re.search(r"[/?]v=(\d+)", url) or re.search(r"/(?:reel|videos|watch)/(\d+)", url)
                vid = m.group(1) if m else f"video{no}"
            if save_video:
                dl_path = os.path.join(DOWNLOADS_DIR, vid + ".mp4")
            else:
                os.makedirs(TMP_DIR, exist_ok=True)
                dl_path = os.path.join(TMP_DIR, vid + ".mp4")
            try:
                if not os.path.exists(dl_path):
                    _download_direct(direct, dl_path, None)
                dl_file = dl_path
                if save_video:
                    rec["file"] = os.path.basename(dl_path)
                    rec["download_url"] = "/downloads/" + os.path.basename(dl_path)
                else:
                    rec["file"] = None
                    rec["download_url"] = None
            except Exception as e:
                rec["warning"] = f"直链下载失败({str(e)[:60]})，改走 yt-dlp"
                dl_file = None
        if not dl_file and (save_video or need_trans):
            with yt_dlp.YoutubeDL(_ytdl_opts(None)) as ydl:
                info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if not os.path.exists(path):
                base = os.path.splitext(os.path.basename(path))[0]
                cands = [f for f in os.listdir(DOWNLOADS_DIR) if f.startswith(base)]
                if not cands:
                    raise RuntimeError("文件未找到")
                path = os.path.join(DOWNLOADS_DIR, cands[0])
            dl_file = path
            if save_video:
                rec["file"] = os.path.basename(path)
                rec["download_url"] = "/downloads/" + os.path.basename(path)
            rec["title"] = info.get("title") or ""
            rec["upload_date"] = info.get("upload_date") or ""
            rec["duration"] = info.get("duration")
            if rec.get("views") is None:
                rec["views"] = info.get("view_count")
            if rec.get("likes") is None:
                rec["likes"] = info.get("like_count")
            if rec.get("comments_count") is None:
                rec["comments_count"] = info.get("comment_count")
            rec["description"] = (info.get("description") or "").strip()
            if not rec.get("caption"):
                rec["caption"] = rec.get("description", "")

        if source.get("skip_transcribe") or not dl_file:
            rec["transcript"] = None
            rec["language"] = None
            rec["segments"] = []
            if not dl_file and need_trans:
                rec["warning"] = "视频获取失败，无法转写口播文案（数据仍完整）"
            elif source.get("skip_transcribe"):
                rec["warning"] = "已跳过语音识别（仅下载+数据）"
        else:
            try:
                transcript, timed, language = _transcribe(dl_file, model_name, None)
                rec["transcript"] = transcript
                rec["language"] = language
                rec["segments"] = timed[:800]
            except Exception as e:
                rec["transcript"] = None
                rec["warning"] = f"语音识别失败：{str(e)[:200]}"
            finally:
                if need_trans and not save_video and dl_file and os.path.exists(dl_file):
                    try:
                        os.remove(dl_file)
                    except Exception:
                        pass

        if want_comments:
            cdata = _fetch_comments_best_effort(url)
            cmts, ccount = cdata.get("comments") or [], cdata.get("count")
            if cmts:
                for c in cmts:
                    c["no"] = no
                comments = cmts
            if rec.get("comments_count") is None and ccount is not None:
                rec["comments_count"] = ccount
        rec["status"] = "成功"
    except Exception as e:
        rec["status"] = f"失败: {str(e)[:200]}"
    return rec, comments


def _run_batch_job(job_id, source, model_name, want_comments):
    # 任务运行在独立子进程（multiprocessing），从磁盘恢复 job 状态，与主服务完全隔离
    job = _load_job(job_id) or {"id": job_id, "type": "batch", "status": "queued", "message": "已加入队列",
                                "percent": 0, "result": None, "error": None, "model": model_name,
                                "log": [], "videos": [], "total": None, "current": None,
                                "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def setp(msg, pct=None, log=None):
        job["message"] = msg
        if pct is not None:
            job["percent"] = pct
        if log:
            job["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {log}")
            if len(job["log"]) > 300:
                job["log"] = job["log"][-300:]
        _save_job(job)  # 子进程模式：每次状态变更落盘，主服务据此轮询

    try:
        videos = source.get("videos") or []
        page = source.get("page") or ""
        if source.get("mode") == "page" and not videos:
            setp("正在枚举主页视频…", 1, f"枚举主页 {page}")
            try:
                videos = _enumerate_page_videos_smart(page, job, max_videos=source.get("limit") or MAX_VIDEOS)
            except Exception as e:
                setp("枚举主页失败", None, f"枚举失败: {str(e)[:200]}")
                raise RuntimeError(f"主页枚举失败：{e}。请确认已上传登录 cookies（主页模式需要），或改用「粘贴多条链接」模式。")
            if not videos:
                raise RuntimeError("未枚举到视频：请确认已上传登录 cookies 且未失效，或改用「粘贴多条链接」模式。")
            setp(f"枚举到 {len(videos)} 条视频，开始逐条处理", 3, f"共 {len(videos)} 条视频")
            job["total"] = len(videos)

        total = len(videos)
        job["total"] = total
        results = []
        comments_all = []
        # 并发处理：3 条同时下载/转写，共享一个语音模型，速度约 3 倍
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = [None] * total
        done_count = 0
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_process_one_video, no, total, v, source, model_name, want_comments): no
                    for no, v in enumerate(videos, 1)}
            for fut in as_completed(futs):
                no = futs[fut]
                try:
                    rec, cmts = fut.result()
                except Exception as e:
                    u = videos[no - 1]
                    rec = {"no": no, "status": f"失败: {str(e)[:150]}",
                           "url": u["url"] if isinstance(u, dict) else str(u)}
                    cmts = []
                results[no - 1] = rec
                comments_all.extend(cmts)
                done_count += 1
                job["current"] = {"no": no, "total": total, "url": rec.get("url", "")}
                setp(f"已完成 {done_count}/{total} 条…", 3 + int(done_count / total * 90), f"#{no} 完成")
                job["videos"] = list(results)
                _save_job(job)
                time.sleep(0.3)  # 轻微放缓，降低 FB 限流概率

        # 汇总
        setp("正在生成汇总…", 95, "汇总计算中")
        summary = _build_summary(results, comments_all)
        job["result"] = {"videos": results, "comments": comments_all, "summary": summary,
                         "total": total, "model": model_name, "comments_enabled": want_comments,
                         "page": page, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if page:
            _save_page_result(page, job["result"])
        setp("完成，可导出 Excel", 100, f"完成：成功 {sum(1 for r in results if r['status']=='成功')}/{total}")
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        setp("批量处理失败", None, f"任务失败: {str(e)[:200]}")
    _save_job(job)


def _build_summary(videos, comments):
    ok = [v for v in videos if v.get("status") == "成功"]
    def nums(k):
        return [v[k] for v in ok if isinstance(v.get(k), (int, float))]
    views, likes, cmts, shares = nums("views"), nums("likes"), nums("comments_count"), nums("shares")
    top_views = sorted(ok, key=lambda v: v.get("views") or 0, reverse=True)[:5]
    top_likes = sorted(ok, key=lambda v: v.get("likes") or 0, reverse=True)[:5]
    summary = {
        "total": len(videos), "success": len(ok),
        "views_sum": sum(views), "views_avg": round(sum(views) / len(views), 1) if views else None,
        "likes_sum": sum(likes), "likes_avg": round(sum(likes) / len(likes), 1) if likes else None,
        "comments_sum": sum(cmts), "shares_sum": sum(shares),
        "top_views": [{"no": v["no"], "title": (v.get("title") or v.get("caption") or "")[:60], "views": v.get("views")} for v in top_views],
        "top_likes": [{"no": v["no"], "title": (v.get("title") or v.get("caption") or "")[:60], "likes": v.get("likes")} for v in top_likes],
        "comment_texts": [c["text"] for c in comments],
        "duration_avg": round(sum(v.get("duration") or 0 for v in ok) / len(ok), 1) if ok else None,
        "durations": [v.get("duration") for v in ok if isinstance(v.get("duration"), (int, float))],
    }
    # 高频词（jieba，转写 + 评论）
    try:
        import jieba
        from collections import Counter
        texts = " ".join([(v.get("transcript") or "") for v in ok] + summary["comment_texts"])
        words = [w for w in jieba.cut(texts) if len(w.strip()) >= 2 and not re.fullmatch(r"[\d\W_]+", w)]
        stop = set("我们 你们 他们 这个 那个 可以 还是 就是 真的 一下 什么 怎么 自己 现在 已经 一个 因为 所以 如果 但是 没有 不是 不会 不能 这些 那些 大家 今天 昨天 明天 时候 东西 视频 评论 关注 点赞 谢谢 哈哈".split())
        words = [w for w in words if w not in stop]
        summary["top_words"] = [{"word": w, "count": c} for w, c in Counter(words).most_common(30)]
    except Exception:
        summary["top_words"] = []
    return summary


# ----------------------------- 路由 -----------------------------

@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.post("/api/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "请先粘贴 Facebook 视频 / Reels 链接"}), 400
    if not re.match(r"^https?://", url):
        url = "https://" + url
    model = data.get("model") or "small"
    if model not in ALLOWED_MODELS:
        model = "small"
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "type": "single", "status": "queued", "message": "已加入队列",
                    "percent": 0, "result": None, "error": None, "url": url, "model": model,
                    "log": [], "videos": [], "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    threading.Thread(target=_run_single_job, args=(job_id, url, model), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.post("/api/analyze_batch")
def analyze_batch():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode") or "urls"
    model = data.get("model") or "small"
    if model not in ALLOWED_MODELS:
        model = "small"
    want_comments = bool(data.get("comments"))
    source = {}
    if mode == "page":
        page = (data.get("page") or "").strip()
        if not page:
            return jsonify({"error": "请填写主页链接或主页名"}), 400
        source = {"mode": "page", "page": _extract_page_name(page)}
        try:
            lim = int(data.get("limit") or 0)
            if lim > 0:
                source["limit"] = min(lim, MAX_VIDEOS)
        except (TypeError, ValueError):
            pass
    else:
        urls = [u.strip() for u in (data.get("urls") or "").splitlines() if u.strip()]
        if not urls:
            return jsonify({"error": "请至少粘贴一条视频链接"}), 400
        urls = [u if re.match(r"^https?://", u) else "https://" + u for u in urls]
        if len(urls) > MAX_VIDEOS:
            urls = urls[:MAX_VIDEOS]
        source = {"mode": "urls", "videos": urls}
    source["skip_transcribe"] = bool(data.get("skip_transcribe"))
    source["skip_download"] = bool(data.get("skip_download"))
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "type": "batch", "status": "queued", "message": "已加入队列",
                    "percent": 0, "result": None, "error": None, "model": model,
                    "log": [], "videos": [], "total": None, "current": None,
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _save_job(JOBS[job_id])
    # 任务跑在独立子进程（fork：系统 Python 上 spawn 不可用，fork 直接继承内存、无需重载）：
    # 抓取/下载/转写即使崩溃（如底层库段错误）也不影响主服务
    multiprocessing.get_context("fork").Process(
        target=_run_batch_job, args=(job_id, source, model, want_comments), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def status(job_id):
    job = _load_job(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({
        "status": job.get("status"), "message": job.get("message"), "percent": job.get("percent"),
        "result": job.get("result"), "error": job.get("error"),
        "log": job.get("log", [])[-30:], "total": job.get("total"), "current": job.get("current"),
    })


@app.get("/api/page_result/<job_id>")
def page_result(job_id):
    job = _load_job(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    if job.get("status") != "done" or not job.get("result"):
        return jsonify({"status": job.get("status"), "message": job.get("message")}), 202
    return jsonify(job["result"])


# ----------------------------- 已保存结果（主页分析自动存档） -----------------------------

def _page_key(page):
    """主页标识 → 存档文件名。仅接受安全的字母数字与 -_ 字符。"""
    key = re.sub(r"[^A-Za-z0-9_-]", "_", page or "page")
    return key or "page"


def _save_page_result(page, result):
    """主页批量分析完成后自动存档到 data/<page>_result.json，下次打开网页可直接加载。"""
    try:
        fp = os.path.join(DATA_DIR, _page_key(page) + "_result.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass


def _load_saved_result(page):
    fp = os.path.join(DATA_DIR, _page_key(page) + "_result.json")
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@app.get("/api/saved_results")
def saved_results():
    """列出 data/ 下所有已存档的主页分析结果。"""
    items = []
    try:
        for fn in os.listdir(DATA_DIR):
            if not fn.endswith("_result.json"):
                continue
            try:
                with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            page = d.get("page") or fn[:-len("_result.json")]
            items.append({
                "page": page,
                "total": d.get("total"),
                "success": (d.get("summary") or {}).get("success"),
                "generated_at": d.get("generated_at"),
                "model": d.get("model"),
            })
    except Exception:
        pass
    items.sort(key=lambda x: x.get("generated_at") or "", reverse=True)
    return jsonify(items)


@app.get("/api/saved_result/<page>")
def saved_result(page):
    d = _load_saved_result(page)
    if not d:
        return jsonify({"error": "该主页没有已保存的分析结果"}), 404
    return jsonify(d)


@app.get("/api/export_saved/<page>")
def export_saved(page):
    d = _load_saved_result(page)
    if not d:
        return jsonify({"error": "该主页没有已保存的分析结果"}), 404
    out_path = _build_excel(d)
    if not out_path:
        return jsonify({"error": "Excel 生成失败"}), 500
    return send_file(out_path, as_attachment=True, download_name=f"{_page_key(page)}_全量分析.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/export_videos_saved/<page>")
def export_videos_saved(page):
    """把已保存主页分析中下载过的视频打包成 zip 下载。"""
    import io as _io
    import zipfile as _zipfile
    d = _load_saved_result(page)
    if not d:
        return jsonify({"error": "该主页没有已保存的分析结果"}), 404
    files = []
    for v in d.get("videos", []):
        if v.get("file"):
            p = os.path.join(DOWNLOADS_DIR, v["file"])
            if os.path.exists(p):
                files.append((p, f"{v.get('no')}-{os.path.basename(v['file'])}"))
    if not files:
        return jsonify({"error": "该主页没有已下载的视频文件"}), 400
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_STORED) as z:
        for p, name in files:
            z.write(p, name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{_page_key(page)}_全部视频_{len(files)}条.zip")


@app.post("/api/download_one")
def download_one():
    """按需下载单条视频：实时抓直链 → 下载 → 返回本地下载链接。默认不囤视频，需要哪条下哪条。"""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少视频链接"}), 400
    try:
        rec = {"url": url}
        _fetch_post_counts(rec)
        direct = rec.get("direct")
        if not direct:
            return jsonify({"error": "未找到可下载直链（可能需要有效登录 cookies）"}), 400
        vid = rec.get("video_id") or ""
        if not vid:
            m = re.search(r"[/?]v=(\d+)", url) or re.search(r"/(?:reel|videos|watch)/(\d+)", url)
            vid = m.group(1) if m else "video"
        dl_path = os.path.join(DOWNLOADS_DIR, vid + ".mp4")
        if not os.path.exists(dl_path):
            _download_direct(direct, dl_path, {"id": "one"})
        return jsonify({"ok": True, "file": "/downloads/" + os.path.basename(dl_path),
                        "size": os.path.getsize(dl_path) if os.path.exists(dl_path) else 0})
    except Exception as e:
        return jsonify({"error": f"下载失败：{str(e)[:150]}"}), 500


PW_STATE_FILE = os.path.join(BASE_DIR, "pw_login_state.json")
PW_STATE = {"status": "none", "msg": ""}   # playwright 登录线程状态


def _load_pw_state():
    try:
        with open(PW_STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("status", "none"), d.get("message", "")
    except Exception:
        return "none", ""


def _pw_login_worker():
    """通过独立 venv 中的 playwright 弹出浏览器窗口让用户登录 Facebook，
    登录完成后导出 cookies 到 COOKIES_FILE。主服务不依赖 playwright 包。"""
    PW_STATE["status"] = "pending"
    PW_STATE["msg"] = "正在打开浏览器登录窗口…"
    try:
        venv_py = os.path.join(BASE_DIR, ".venv", "bin", "python")
        worker = os.path.join(BASE_DIR, "login_worker.py")
        if not (os.path.exists(venv_py) and os.path.exists(worker)):
            PW_STATE["status"] = "error"
            PW_STATE["msg"] = "登录组件未安装完成，请稍后重试"
            return
        import subprocess
        subprocess.run([venv_py, worker, COOKIES_FILE, PW_STATE_FILE], timeout=720)
    except Exception as e:
        PW_STATE["status"] = "error"
        PW_STATE["msg"] = f"登录失败：{str(e)[:120]}"
    st, msg = _load_pw_state()
    PW_STATE["status"] = st
    PW_STATE["msg"] = msg


@app.post("/api/facebook_login")
def facebook_login():
    if PW_STATE["status"] == "pending":
        return jsonify({"ok": False, "error": "登录窗口已在打开中，请先完成当前登录"}), 409
    threading.Thread(target=_pw_login_worker, daemon=True).start()
    return jsonify({"ok": True, "message": "正在打开 Facebook 登录窗口…"})


@app.get("/api/login_status")
def login_status():
    fst, fmsg = _load_pw_state()
    st = fst if fst != "none" else PW_STATE["status"]
    msg = fmsg if fmsg else PW_STATE["msg"]
    has_cookies = os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 100
    if st == "pending":
        return jsonify({"status": "pending", "message": msg or "正在登录…", "has_cookies": has_cookies})
    if st == "error":
        return jsonify({"status": "error", "message": msg or "登录失败", "has_cookies": has_cookies})
    if has_cookies:
        mtime = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(COOKIES_FILE)))
        return jsonify({"status": "logged_in", "message": f"已配置登录态（cookies 更新于 {mtime}）", "has_cookies": True})
    return jsonify({"status": "none", "message": "未登录。点击「登录 Facebook」在弹出窗口完成登录即可。", "has_cookies": False})


@app.post("/api/upload_cookies")
def upload_cookies():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "未收到文件"}), 400
    f.save(COOKIES_FILE)
    size = os.path.getsize(COOKIES_FILE)
    return jsonify({"ok": True, "size": size, "hint": "cookies 已保存，批量模式将使用该登录态"})


@app.delete("/api/upload_cookies")
def delete_cookies():
    if os.path.exists(COOKIES_FILE):
        os.remove(COOKIES_FILE)
    return jsonify({"ok": True})


@app.get("/api/export/<job_id>")
def export_excel(job_id):
    job = _load_job(job_id)
    if not job or job.get("status") != "done" or not job.get("result"):
        return jsonify({"error": "任务未完成，无法导出"}), 400
    out_path = _build_excel(job["result"])
    if not out_path:
        return jsonify({"error": "Excel 生成失败"}), 500
    return send_file(out_path, as_attachment=True, download_name=f"reels分析_{job_id}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/export_videos/<job_id>")
def export_videos_zip(job_id):
    """把任务中已下载的全部视频打包成 zip 下载。"""
    import io as _io
    import zipfile as _zipfile
    job = _load_job(job_id)
    if not job or not job.get("result"):
        return jsonify({"error": "任务不存在或未完成"}), 404
    files = []
    for v in job["result"].get("videos", []):
        if v.get("file"):
            p = os.path.join(DOWNLOADS_DIR, v["file"])
            if os.path.exists(p):
                files.append((p, f"{v.get('no')}-{os.path.basename(v['file'])}"))
    if not files:
        return jsonify({"error": "该任务没有已下载的视频文件"}), 400
    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w", _zipfile.ZIP_STORED) as z:
        for p, name in files:
            z.write(p, name)
    buf.seek(0)
    page = job.get("result", {}).get("page") or "reels"
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{page}_全部视频_{len(files)}条.zip")


@app.get("/downloads/<path:fname>")
def download(fname):
    safe = os.path.basename(fname)
    return send_from_directory(DOWNLOADS_DIR, safe, as_attachment=True)


# ----------------------------- Excel 导出 -----------------------------

def _build_excel(result):
    """依据 result 生成 xlsx：视频汇总 / 评论明细 / 分析摘要 / 说明 四个工作表。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    import unicodedata

    def dw(v):
        return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in str(v or ""))

    def fit(ws, widths):
        for col, w in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = w

    HEADER_FILL = PatternFill("solid", fgColor="1877F2")
    HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
    WRAP = Alignment(vertical="center", wrap_text=True)
    CENTER = Alignment(vertical="center", horizontal="center", wrap_text=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "视频汇总"
    headers = ["编号", "链接", "标题", "发布简介", "发布日期", "时长(秒)", "观看数", "点赞数",
               "评论数", "分享数", "收藏数", "语言", "模型", "状态", "口播文案(转写)", "备注"]
    for j, h in enumerate(headers, 1):
        c = ws.cell(1, j, h)
        c.fill, c.font, c.alignment = HEADER_FILL, HEADER_FONT, CENTER
    ws.freeze_panes = "A2"
    for i, v in enumerate(result.get("videos", []), 2):
        row = [v.get("no"), v.get("url"), v.get("title") or "", v.get("caption") or "",
               v.get("upload_date") or "", v.get("duration"), v.get("views"), v.get("likes"),
               v.get("comments_count"), v.get("shares"), "不可得", v.get("language") or "",
               result.get("model", ""), v.get("status"), v.get("transcript") or "",
               v.get("warning") or ""]
        for j, val in enumerate(row, 1):
            c = ws.cell(i, j, val)
            if j in (7, 8, 9, 10, 6):
                c.number_format = "#,##0"
                c.alignment = CENTER
            elif j in (1, 11, 12, 13):
                c.alignment = CENTER
            else:
                c.alignment = WRAP
    fit(ws, {1: 6, 2: 46, 3: 30, 4: 36, 5: 12, 6: 10, 7: 10, 8: 9, 9: 9, 10: 9, 11: 9, 12: 9, 13: 9, 14: 12, 15: 60, 16: 24})
    ws.auto_filter.ref = f"A1:P{ws.max_row}"

    ws2 = wb.create_sheet("评论明细")
    h2 = ["编号", "评论者", "评论时间", "评论内容", "评论点赞"]
    for j, h in enumerate(h2, 1):
        c = ws2.cell(1, j, h)
        c.fill, c.font, c.alignment = HEADER_FILL, HEADER_FONT, CENTER
    ws2.freeze_panes = "A2"
    comments = result.get("comments", [])
    for i, c in enumerate(comments, 2):
        vals = [c.get("no"), c.get("author") or c.get("commenter") or "", c.get("time") or "", c.get("text") or "", c.get("likes")]
        for j, val in enumerate(vals, 1):
            cell = ws2.cell(i, j, val)
            cell.alignment = WRAP if j == 4 else CENTER
    if not comments:
        ws2.cell(2, 4, "（未抓到评论内容：可能需要登录 cookies 或受反爬限制；评论数见「视频汇总」表）").alignment = WRAP
    fit(ws2, {1: 6, 2: 22, 3: 18, 4: 60, 5: 10})
    ws2.auto_filter.ref = f"A1:E{max(ws2.max_row, 2)}"

    ws3 = wb.create_sheet("分析摘要")
    s = result.get("summary") or {}
    n = len(result.get("videos", [])) + 1  # 数据行上限（含表头）
    R = f"视频汇总!A2:A{n}"
    V = f"视频汇总!G2:G{n}"
    L = f"视频汇总!H2:H{n}"
    C = f"视频汇总!I2:I{n}"
    S = f"视频汇总!J2:J{n}"
    D = f"视频汇总!F2:F{n}"
    rows = [
        ("指标", "数值/说明"),
        ("视频总数", f"=COUNTA({R})"),
        ("处理成功数", f"=COUNTIF(视频汇总!N2:N{n},\"成功\")"),
        ("观看数合计", f"=SUM({V})"),
        ("观看数平均", f"=IFERROR(ROUND(AVERAGE({V}),0),\"\")"),
        ("点赞数合计", f"=SUM({L})"),
        ("点赞数平均", f"=IFERROR(ROUND(AVERAGE({L}),0),\"\")"),
        ("评论数合计", f"=SUM({C})"),
        ("分享数合计", f"=SUM({S})"),
        ("平均时长(秒)", f"=IFERROR(ROUND(AVERAGE({D}),1),\"\")"),
        ("估算互动率(赞+评+分享)/观看", f"=IFERROR(ROUND((SUM({L})+SUM({C})+SUM({S}))/SUM({V}),4),\"\")"),
        ("", ""),
        ("观看数 Top5", ""),
    ]
    for j, (a, b) in enumerate(rows, 1):
        ws3.cell(j, 1, a).alignment = WRAP
        ws3.cell(j, 2, b).alignment = WRAP
    r0 = 14
    for k, t in enumerate(s.get("top_views", [])):
        ws3.cell(r0 + k, 1, f"Top{k+1} 编号").alignment = WRAP
        ws3.cell(r0 + k, 2, t.get("no")).alignment = CENTER
        ws3.cell(r0 + k, 3, t.get("title") or "").alignment = WRAP
        ws3.cell(r0 + k, 4, t.get("views")).number_format = "#,##0"
    ws3.cell(r0 + 5, 1, "点赞数 Top5").font = Font(bold=True)
    r1 = r0 + 6
    for k, t in enumerate(s.get("top_likes", [])):
        ws3.cell(r1 + k, 1, f"Top{k+1} 编号").alignment = WRAP
        ws3.cell(r1 + k, 2, t.get("no")).alignment = CENTER
        ws3.cell(r1 + k, 3, t.get("title") or "").alignment = WRAP
        ws3.cell(r1 + k, 4, t.get("likes")).number_format = "#,##0"
    words = s.get("top_words", [])
    ws3.cell(r1 + 6, 1, "高频词（口播+评论，jieba 分词）").font = Font(bold=True)
    for k, w in enumerate(words[:30]):
        ws3.cell(r1 + 7 + k, 1, w.get("word") or "").alignment = WRAP
        ws3.cell(r1 + 7 + k, 2, w.get("count")).alignment = CENTER
    fit(ws3, {1: 34, 2: 26, 3: 44, 4: 12})

    ws4 = wb.create_sheet("说明")
    notes = [
        "Reels 批量分析导出（生成时间：" + (result.get("generated_at") or "") + "）",
        "数据来源：yt-dlp 元数据 + Facebook 公开页面 + faster-whisper 语音识别。",
        "「收藏数」：Facebook 对第三方主页不公开收藏/保存数据（仅自己主页后台可见），故标注不可得。",
        "「观看数/点赞数/评论数」：以公开页面显示与 yt-dlp 元数据为准，部分视频可能缺失。",
        "「评论内容」：通过移动端（m.facebook.com）评论通道尽量抓取，可能受登录墙/反爬限制而不完整；评论数量见视频汇总表。",
        "「口播文案」：由本地 faster-whisper 识别，小模型可能存在个别错字；可换 medium/large-v3 重新跑。",
        "「编号」：批量任务内按处理顺序编号，视频汇总/评论明细两表通过编号对应。",
        "「估算互动率」：为 (点赞+评论+分享)/观看 的简化估算，非官方指标。",
        "「主页模式覆盖范围」：主页枚举基于首屏时间线（通常最近 10~20 条帖），更早的历史帖需用「粘贴多条链接」模式补抓。",
    ]
    for i, t in enumerate(notes, 1):
        c = ws4.cell(i, 1, t)
        c.alignment = WRAP
    fit(ws4, {1: 100})

    try:
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        out_path = os.path.join(EXPORTS_DIR, "reels分析.xlsx")
        wb.save(out_path)
        return out_path
    except Exception as e:
        print("[export] Excel 生成失败:", e, flush=True)
        return None


if __name__ == "__main__":
    print("=" * 56)
    print(" Reels 分析工作台已启动")
    print(" 请在浏览器打开: http://127.0.0.1:8756")
    print(" 停止服务: 按 Ctrl+C")
    print("=" * 56)
    app.run(host="127.0.0.1", port=8756, threaded=True)
