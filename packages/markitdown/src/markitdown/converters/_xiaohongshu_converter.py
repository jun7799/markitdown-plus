"""
小红书笔记转换器

将 xiaohongshu.com 的笔记转为 Markdown，支持图文和视频笔记。
图片下载到本地，可选 OCR 文字识别。

数据获取策略：
1. 优先通过 CDP（Chrome DevTools Protocol）站内 fetch 获取，绕过 WAF
2. CDP 不可用时回退到 HTTP + Cookie 方式

CDP 模式需要：
  - web-access skill 的 cdp-proxy 运行中（localhost:3456）
  - 用户 Chrome 已打开且已登录小红书
"""

import hashlib
import json
import os
import re
from typing import Any, BinaryIO, Optional

import requests

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo

# 笔记 ID: 24 位十六进制
_NOTE_ID_RE = re.compile(r"/([a-f0-9]{24})")

_COOKIE_ENV = "XHS_COOKIE"

# CDP Proxy 配置
CDP_PROXY_HOST = "127.0.0.1"
CDP_PROXY_PORT = 3456
CDP_PROXY_BASE = f"http://{CDP_PROXY_HOST}:{CDP_PROXY_PORT}"


class XiaohongshuConverter(DocumentConverter):
    """小红书笔记转换器，支持图文/视频笔记，图片可 OCR。

    优先使用 CDP 站内 fetch（绕过 WAF），不可用时回退 HTTP + Cookie。
    """

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        url = stream_info.url or ""
        if "xiaohongshu.com" not in url:
            return False
        if not re.search(r"/(explore|discovery/item)/", url):
            return False
        return bool(_NOTE_ID_RE.search(url))

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        url = stream_info.url or ""

        # 提取笔记 ID
        m = _NOTE_ID_RE.search(url)
        if not m:
            raise ValueError(f"无法从小红书链接提取笔记 ID: {url}")
        note_id = m.group(1)

        # 按优先级尝试获取笔记数据
        note = None

        # 1. 优先 CDP 站内 fetch
        note = self._fetch_via_cdp(note_id, url)

        # 2. 回退 HTTP + Cookie
        if not note:
            cookie = self._get_cookie()
            if cookie:
                note = self._fetch_via_http(note_id, cookie)

        if not note:
            raise ValueError(
                f"获取笔记数据失败 (ID: {note_id})。\n"
                "可能原因:\n"
                "1. Cookie 过期或笔记不可访问\n"
                "2. WAF 拦截了请求\n"
                "建议: 在 Chrome 中登录小红书，确保 cdp-proxy 运行中"
            )

        title = (
            note.get("title", "")
            or note.get("displayTitle", "")
            or f"小红书笔记_{note_id[:8]}"
        )
        markdown = self._build_markdown(note, note_id, url)

        return DocumentConverterResult(markdown=markdown, title=title)

    # ------------------------------------------------------------------
    # CDP 站内 fetch（绕过 WAF 的核心方法）
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_via_cdp(note_id: str, original_url: str) -> Optional[dict]:
        """通过 CDP proxy 在 XHS 页面内执行 fetch，获取笔记数据。

        原理：在用户已登录的 Chrome 中，先打开 XHS 页面建立同源上下文，
        然后用 fetch() 请求笔记页面 HTML，从 __INITIAL_STATE__ 提取数据。
        同源请求自动携带 Cookie，且不触发页面导航级别的 WAF 检测。
        """
        try:
            # 1. 检查 CDP proxy 是否可用
            resp = requests.get(
                f"{CDP_PROXY_BASE}/targets",
                timeout=3,
            )
            if resp.status_code != 200 or not isinstance(resp.json(), list):
                return None
        except Exception:
            return None

        try:
            # 2. 创建新 tab 打开 XHS 首页（建立同源上下文）
            resp = requests.get(
                f"{CDP_PROXY_BASE}/new",
                params={"url": "https://www.xiaohongshu.com/explore"},
                timeout=30,
            )
            data = resp.json()
            target_id = data.get("targetId")
            if not target_id:
                return None

            # 等待首页加载
            import time
            time.sleep(4)

            try:
                # 3. 在页面内执行 fetch 获取笔记数据
                fetch_url = f"/discovery/item/{note_id}"

                # 保留原始 URL 中的 xsec_token 等参数
                if "xsec_token=" in original_url:
                    # 提取查询参数
                    query_match = re.search(r"\?(.+)$", original_url)
                    if query_match:
                        fetch_url += "?" + query_match.group(1)

                js_code = f"""
                (async () => {{
                    try {{
                        const resp = await fetch({json.dumps(fetch_url)}, {{
                            credentials: "include"
                        }});
                        const html = await resp.text();
                        const match = html.match(/__INITIAL_STATE__\\s*=\\s*(\\u007b.*?\\u007d)\\s*<\\/script>/s);
                        if (!match) return JSON.stringify({{ noMatch: true }});

                        const raw = match[1].replace(/undefined/g, "null");
                        const state = JSON.parse(raw);

                        const noteDetail = state?.note?.noteDetailMap?.[{json.dumps(note_id)}];
                        if (!noteDetail?.note) {{
                            return JSON.stringify({{
                                noNote: true,
                                serverInfo: state?.note?.serverRequestInfo,
                                keys: Object.keys(state?.note?.noteDetailMap || {{}})
                            }});
                        }}

                        return JSON.stringify(noteDetail.note);
                    }} catch (e) {{
                        return JSON.stringify({{ error: e.message }});
                    }}
                }})()
                """

                eval_resp = requests.post(
                    f"{CDP_PROXY_BASE}/eval",
                    params={"target": target_id},
                    data=js_code,
                    timeout=20,
                )
                eval_data = eval_resp.json()
                raw_note = eval_data.get("value", "{}")

                note = json.loads(raw_note)
                if isinstance(note, dict) and note.get("title") or note.get("desc") or note.get("imageList"):
                    return note

                return None

            finally:
                # 4. 关闭 tab
                try:
                    requests.post(
                        f"{CDP_PROXY_BASE}/close",
                        params={"target": target_id},
                        timeout=5,
                    )
                except Exception:
                    pass

        except Exception:
            return None

    # ------------------------------------------------------------------
    # HTTP + Cookie 方式（回退方案）
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_via_http(note_id: str, cookie: str) -> Optional[dict]:
        """通过 HTTP 请求 + Cookie 获取笔记数据（可能被 WAF 拦截）"""
        session = requests.Session()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": cookie,
            "Referer": "https://www.xiaohongshu.com/",
        }

        for path in [f"/explore/{note_id}", f"/discovery/item/{note_id}"]:
            try:
                resp = session.get(
                    f"https://www.xiaohongshu.com{path}",
                    headers=headers,
                    timeout=15,
                    allow_redirects=True,
                )

                # 拦截到安全页
                if "sec_" in resp.url or ("404" in resp.url and "error_code" in resp.url):
                    continue

                match = re.search(
                    r"__INITIAL_STATE__\s*=\s*({.*?})\s*</script>",
                    resp.text,
                    re.DOTALL,
                )
                if not match:
                    continue

                raw = match.group(1).replace("undefined", "null")
                state = json.loads(raw)

                # 新版结构: state.note.noteDetailMap[id].note
                note_detail = state.get("note", {}).get("noteDetailMap", {}).get(note_id, {})
                note = note_detail.get("note", {})

                # 旧版结构: state.noteData.data.note
                if not note:
                    note = state.get("noteData", {}).get("data", {}).get("note", {})

                if isinstance(note, dict) and (
                    note.get("title") or note.get("desc") or note.get("imageList")
                ):
                    return note

            except Exception:
                continue

        return None

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    @staticmethod
    def _get_cookie() -> str:
        """按优先级获取 Cookie: 环境变量 > 当前目录 .xhs_cookie > 家目录 .xhs_cookie"""
        cookie = os.environ.get(_COOKIE_ENV, "").strip()
        if cookie:
            return cookie

        for base in [os.getcwd(), os.path.expanduser("~")]:
            path = os.path.join(base, ".xhs_cookie")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    cookie = f.read().strip()
                if cookie:
                    return cookie

        return ""

    # ------------------------------------------------------------------
    # Markdown 生成
    # ------------------------------------------------------------------

    def _build_markdown(self, note: dict, note_id: str, original_url: str) -> str:
        parts: list[str] = []

        # ---- 标题 ----
        title = note.get("title", "") or note.get("displayTitle", "")
        if title:
            parts.append(f"# {title}\n")

        # ---- 元信息 ----
        meta: list[str] = []
        user = note.get("user", {})
        if isinstance(user, dict) and user.get("nickname"):
            meta.append(f"**作者**: {user['nickname']}")

        note_type = note.get("type", "")
        meta.append(f"**类型**: {'视频' if note_type == 'video' else '图文'}")

        time_str = note.get("time", note.get("lastUpdateTime", ""))
        if time_str:
            # 时间戳转日期
            if isinstance(time_str, (int, float)) and time_str > 1e12:
                from datetime import datetime
                try:
                    dt = datetime.fromtimestamp(time_str / 1000)
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_str = str(time_str)
            meta.append(f"**发布时间**: {time_str}")

        meta.append(f"**原文链接**: {original_url}")

        if meta:
            parts.append("\n".join(meta))
            parts.append("\n---\n")

        # ---- 正文描述 ----
        desc = note.get("desc", "")
        if desc:
            # 清理话题标签格式: #xxx[话题]# -> #xxx
            desc = re.sub(r"#([^#\s]+)\[话题\]#", r"#\1", desc)
            parts.append(desc)
            parts.append("")

        # ---- 图片 ----
        image_dir = os.path.join(os.getcwd(), "images")
        image_list = note.get("imageList", [])

        if image_list:
            os.makedirs(image_dir, exist_ok=True)
            for idx, img in enumerate(image_list, start=1):
                if not isinstance(img, dict):
                    continue
                # 优先 urlDefault，其次 urlPre，最后 url
                img_url = (
                    img.get("urlDefault", "")
                    or img.get("urlPre", "")
                    or img.get("url_default", "")
                    or img.get("url", "")
                )
                # 也尝试从 infoList 获取
                if not img_url:
                    info_list = img.get("infoList", [])
                    if info_list:
                        for info in info_list:
                            if isinstance(info, dict) and info.get("url"):
                                img_url = info["url"]
                                break

                if not img_url:
                    continue
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

                filename = self._download_image(img_url, image_dir, idx)
                if filename:
                    parts.append(f"![图片{idx}](images/{filename})")
                    # OCR
                    ocr = self._ocr_image(os.path.join(image_dir, filename))
                    if ocr:
                        parts.append(f"\n> OCR识别:\n> {ocr}\n")
                else:
                    parts.append(f"![图片{idx}]({img_url})")
                parts.append("")

        # ---- 视频 ----
        video = note.get("video", {})
        if isinstance(video, dict) and video:
            video_url = video.get("url", video.get("mediaUrl", ""))
            if video_url:
                if video_url.startswith("//"):
                    video_url = "https:" + video_url
                parts.append("---\n")
                parts.append(f"**视频下载**: [点击下载]({video_url})")
                parts.append("")

                # 封面
                cover = video.get("cover", "")
                if isinstance(cover, dict):
                    cover_url = cover.get("urlDefault", cover.get("url_default", cover.get("url", "")))
                elif isinstance(cover, str):
                    cover_url = cover
                else:
                    cover_url = ""

                if cover_url:
                    if cover_url.startswith("//"):
                        cover_url = "https:" + cover_url
                    os.makedirs(image_dir, exist_ok=True)
                    cname = self._download_image(cover_url, image_dir, 0)
                    if cname:
                        parts.append(f"![视频封面](images/{cname})")
                    else:
                        parts.append(f"![视频封面]({cover_url})")
                    parts.append("")

        # ---- 标签 ----
        tag_list = note.get("tagList", [])
        if tag_list:
            tags = []
            for t in tag_list:
                name = t.get("name", "") if isinstance(t, dict) else str(t)
                if name:
                    tags.append(f"#{name}")
            if tags:
                parts.append("---\n")
                parts.append("**标签**: " + " ".join(tags))
                parts.append("")

        # ---- 互动数据 ----
        interact = note.get("interactInfo", {})
        if isinstance(interact, dict):
            liked = interact.get("likedCount", "")
            collected = interact.get("collectedCount", "")
            comments = interact.get("commentCount", "")
            shares = interact.get("shareCount", "")
            if any([liked, collected, comments, shares]):
                parts.append("\n| 点赞 | 收藏 | 评论 | 分享 |")
                parts.append("|------|------|------|------|")
                parts.append(
                    f"| {liked or '-'} | {collected or '-'} "
                    f"| {comments or '-'} | {shares or '-'} |"
                )
                parts.append("")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 图片下载
    # ------------------------------------------------------------------

    @staticmethod
    def _download_image(url: str, image_dir: str, index: int) -> Optional[str]:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                ),
                "Referer": "https://www.xiaohongshu.com/",
            }
            resp = requests.get(url, headers=headers, timeout=30, stream=True)
            resp.raise_for_status()

            ct = resp.headers.get("Content-Type", "").lower()
            if "webp" in ct or "webp" in url:
                ext = ".webp"
            elif "png" in ct:
                ext = ".png"
            elif "gif" in ct:
                ext = ".gif"
            else:
                ext = ".jpg"

            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            filename = f"xhs_{index:03d}_{url_hash}{ext}"
            filepath = os.path.join(image_dir, filename)

            if os.path.exists(filepath):
                return filename

            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            return filename
        except Exception:
            return None

    # ------------------------------------------------------------------
    # OCR（可选，需安装 pytesseract + Tesseract OCR）
    # ------------------------------------------------------------------

    @staticmethod
    def _ocr_image(image_path: str) -> Optional[str]:
        try:
            import pytesseract
            from PIL import Image

            img = Image.open(image_path)
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            return text.strip() or None
        except ImportError:
            return None
        except Exception:
            return None
