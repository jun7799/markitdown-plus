"""
B站视频转换器

将 bilibili.com 的视频转为 Markdown，包含视频元信息和 AI 字幕。

数据获取策略：
1. 通过 B站 API 获取视频信息（标题、UP主、时长等）
2. 通过 /x/v2/dm/view 接口获取 AI 字幕
3. 字幕优先 ai-zh，其次取第一个可用字幕

Cookie 管理：
  - 环境变量 BILIBILI_SESSDATA 或 BILIBILI_COOKIE
  - Cookie 文件 bilibili_cookies.txt（当前目录 / 家目录）
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Optional

import requests

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo

# BV号正则
_BV_RE = re.compile(r"(BV[\w]+)")

# Cookie 相关
_SESSDATA_ENV = "BILIBILI_SESSDATA"
_COOKIE_ENV = "BILIBILI_COOKIE"

# 通用请求头
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class BilibiliConverter(DocumentConverter):
    """B站视频转换器，提取视频元信息和 AI 字幕。"""

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        url = stream_info.url or ""
        if "bilibili.com/video/" not in url:
            return False
        return bool(_BV_RE.search(url))

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        url = stream_info.url or ""

        # 提取 BV 号
        m = _BV_RE.search(url)
        if not m:
            raise ValueError(f"无法从B站链接提取 BV 号: {url}")
        bvid = m.group(1)

        # 获取 Cookie
        sessdata = self._get_sessdata()

        headers = {
            "User-Agent": _DEFAULT_UA,
            "Referer": "https://www.bilibili.com",
        }
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata}"

        # 获取视频信息
        info = self._get_video_info(bvid, headers)

        # 获取字幕（不强制要求登录，部分视频可无Cookie获取）
        subtitle_text = ""
        subtitle_lang = ""
        try:
            subtitle_text, subtitle_lang = self._get_subtitle(
                info["cid"], info["aid"], headers
            )
        except Exception:
            pass

        # 生成 Markdown
        markdown = self._build_markdown(info, bvid, url, subtitle_text, subtitle_lang)

        return DocumentConverterResult(
            markdown=markdown,
            title=info["title"],
        )

    # ------------------------------------------------------------------
    # 视频信息获取
    # ------------------------------------------------------------------

    @staticmethod
    def _get_video_info(bvid: str, headers: dict) -> dict:
        api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        resp = requests.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data["code"] != 0:
            raise RuntimeError(
                f"B站API错误: {data.get('message', 'unknown')} (code: {data['code']})"
            )

        v = data["data"]
        stat = v.get("stat", {})

        # 时长格式化
        duration = v.get("duration", 0)
        if isinstance(duration, (int, float)):
            minutes, seconds = divmod(int(duration), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = str(duration)

        # 发布时间
        pubdate = v.get("pubdate", 0)
        if isinstance(pubdate, (int, float)) and pubdate > 0:
            pub_str = datetime.fromtimestamp(int(pubdate)).strftime("%Y-%m-%d %H:%M")
        else:
            pub_str = ""

        return {
            "cid": v["cid"],
            "aid": v["aid"],
            "title": v["title"],
            "desc": v.get("desc", ""),
            "duration": duration_str,
            "duration_sec": v.get("duration", 0),
            "owner": v.get("owner", {}).get("name", ""),
            "owner_mid": v.get("owner", {}).get("mid", ""),
            "view": stat.get("view", 0),
            "like": stat.get("like", 0),
            "coin": stat.get("coin", 0),
            "favorite": stat.get("favorite", 0),
            "danmaku": stat.get("danmaku", 0),
            "reply": stat.get("reply", 0),
            "share": stat.get("share", 0),
            "pubdate": pub_str,
            "pic": v.get("pic", ""),
        }

    # ------------------------------------------------------------------
    # 字幕获取
    # ------------------------------------------------------------------

    @staticmethod
    def _get_subtitle(
        cid: int, aid: int, headers: dict
    ) -> tuple[str, str]:
        api_url = (
            f"https://api.bilibili.com/x/v2/dm/view"
            f"?oid={cid}&type=1&pid={aid}"
        )
        resp = requests.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data["code"] != 0:
            return "", ""

        subtitle_info = data.get("data", {}).get("subtitle", {})
        subtitles = subtitle_info.get("subtitles", [])

        if not subtitles:
            return "", ""

        # 优先 ai-zh
        target = None
        for sub in subtitles:
            if sub.get("lan") == "ai-zh":
                target = sub
                break

        # fallback: 第一个
        if target is None:
            target = subtitles[0]

        subtitle_url = target.get("subtitle_url", "")
        lang_doc = target.get("lan_doc", "")

        if not subtitle_url:
            return "", lang_doc

        # 下载字幕内容
        text = BilibiliConverter._download_subtitle(subtitle_url)
        return text, lang_doc

    @staticmethod
    def _download_subtitle(subtitle_url: str) -> str:
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        elif subtitle_url.startswith("http://"):
            subtitle_url = "https://" + subtitle_url[7:]

        try:
            resp = requests.get(subtitle_url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return ""

        body = data.get("body", [])
        if not body:
            return ""

        lines = []
        for item in body:
            content = item.get("content", "").strip()
            if content:
                lines.append(content)

        return "".join(lines)

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    @staticmethod
    def _get_sessdata() -> str:
        # 优先环境变量 BILIBILI_SESSDATA
        val = os.environ.get(_SESSDATA_ENV, "").strip()
        if val:
            return val

        # 环境变量 BILIBILI_COOKIE（完整 Cookie 字符串中提取 SESSDATA）
        cookie_str = os.environ.get(_COOKIE_ENV, "").strip()
        if cookie_str:
            m = re.search(r"SESSDATA=([^;]+)", cookie_str)
            if m:
                return m.group(1)

        # Cookie 文件
        for base in [os.getcwd(), str(Path.home())]:
            path = os.path.join(base, "bilibili_cookies.txt")
            if os.path.isfile(path):
                sessdata = BilibiliConverter._parse_sessdata_from_file(path)
                if sessdata:
                    return sessdata

        return ""

    @staticmethod
    def _parse_sessdata_from_file(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 7 and parts[-2] == "SESSDATA":
                    return parts[-1]
        return ""

    # ------------------------------------------------------------------
    # Markdown 生成
    # ------------------------------------------------------------------

    def _build_markdown(
        self,
        info: dict,
        bvid: str,
        url: str,
        subtitle_text: str,
        subtitle_lang: str,
    ) -> str:
        parts: list[str] = []

        # 标题
        title = info["title"]
        parts.append(f"# {title}\n")

        # 元信息
        meta: list[str] = []
        if info.get("owner"):
            meta.append(f"**UP主**: {info['owner']}")
        if info.get("duration"):
            meta.append(f"**时长**: {info['duration']}")
        if info.get("pubdate"):
            meta.append(f"**发布时间**: {info['pubdate']}")

        view = info.get("view", 0)
        if view:
            meta.append(f"**播放**: {self._format_number(view)}")

        meta.append(f"**原文链接**: {url}")
        meta.append(f"**BV号**: {bvid}")

        if meta:
            parts.append("\n".join(meta))
            parts.append("\n---\n")

        # 互动数据
        like = info.get("like", 0)
        coin = info.get("coin", 0)
        favorite = info.get("favorite", 0)
        danmaku = info.get("danmaku", 0)
        reply = info.get("reply", 0)

        if any([like, coin, favorite, danmaku, reply]):
            parts.append("| 点赞 | 投币 | 收藏 | 弹幕 | 评论 |")
            parts.append("|------|------|------|------|------|")
            parts.append(
                f"| {self._format_number(like)} "
                f"| {self._format_number(coin)} "
                f"| {self._format_number(favorite)} "
                f"| {self._format_number(danmaku)} "
                f"| {self._format_number(reply)} |"
            )
            parts.append("")

        # 视频简介
        desc = info.get("desc", "")
        if desc:
            parts.append("## 视频简介\n")
            parts.append(desc)
            parts.append("")

        # 字幕
        if subtitle_text:
            parts.append(f"## 字幕 ({subtitle_lang})\n")
            # 按标点断句，每行不要太长
            sentences = self._split_subtitle(subtitle_text)
            parts.append("\n".join(sentences))
            parts.append("")
        else:
            parts.append("## 字幕\n")
            parts.append("*该视频暂无 AI 字幕。如需获取字幕，请配置 BILIBILI_SESSDATA 环境变量或 bilibili_cookies.txt 文件。*\n")

        return "\n".join(parts)

    @staticmethod
    def _format_number(n: int) -> str:
        if n >= 100_000_000:
            return f"{n / 100_000_000:.1f}亿"
        if n >= 10_000:
            return f"{n / 10_000:.1f}万"
        return str(n)

    @staticmethod
    def _split_subtitle(text: str, max_len: int = 80) -> list[str]:
        # 按中文标点断句
        sentences = re.split(r"(?<=[。！？；\n])", text)
        result = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            # 超长句按 max_len 切分
            while len(s) > max_len:
                result.append(s[:max_len])
                s = s[max_len:]
            if s:
                result.append(s)
        return result
