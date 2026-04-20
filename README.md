# MarkItDown Plus

[![PyPI](https://img.shields.io/pypi/v/markitdown.svg)](https://pypi.org/project/markitdown/)
![PyPI - Downloads](https://img.shields.io/pypi/dd/markitdown)

基于微软 [markitdown](https://github.com/microsoft/markitdown) 二次开发，新增 **微信公众号**、**X/Twitter**、**小红书** 和 **B站视频** 转 Markdown 功能。

## 新增功能

### 微信公众号文章 -> Markdown

自动识别 `mp.weixin.qq.com` 链接，提取标题、公众号名、作者、正文，图片下载到本地。

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("https://mp.weixin.qq.com/s/xxxxx")
print(result.markdown)
```

```bash
markitdown https://mp.weixin.qq.com/s/xxxxx -o output.md
```

**特性：**
- 提取标题、公众号名、作者、发布时间
- 正文排版保留（加粗、段落、小节标题）
- 图片自动下载到本地 `images/` 文件夹，不会过期
- 自动检测反爬验证页，用移动端 UA 重试

### X (Twitter) 推文 -> Markdown

自动识别 `x.com` / `twitter.com` 推文链接，通过 FXTwitter API 获取数据。

| 类型 | 说明 |
|------|------|
| 普通推文 | 文字 + 图片 + 互动数据 |
| 长文 (Article) | 标题 + 全文 + 图片 + 代码块/引用/列表 |
| 视频推文 | 文字 + 视频 mp4 下载链接 + 缩略图 |

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("https://x.com/xxx/status/123456")
print(result.markdown)
```

```bash
markitdown https://x.com/xxx/status/123456 -o tweet.md
```

**特性：**
- 长文全文提取，支持标题(h2)、引用、列表、代码块、加粗样式
- 视频提供最高清 mp4 下载链接
- 图片下载到本地
- 互动数据表格（浏览/点赞/转发/收藏/评论）

### 小红书笔记 -> Markdown

自动识别 `xiaohongshu.com` 链接，提取笔记内容、图片、互动数据。

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("https://www.xiaohongshu.com/discovery/item/xxxxx")
print(result.markdown)
```

```bash
markitdown https://www.xiaohongshu.com/discovery/item/xxxxx -o xhs.md
```

**特性：**
- 支持图文笔记和视频笔记
- 图片自动下载到本地 `images/` 文件夹
- 可选 OCR 文字识别（需安装 pytesseract + Tesseract OCR）
- 提取互动数据（点赞、收藏、评论、分享）
- 两种获取模式：
  - **CDP 模式**（推荐）：通过 Chrome DevTools Protocol 站内 fetch，绕过 WAF
  - **HTTP + Cookie 模式**：通过环境变量 `XHS_COOKIE` 提供登录 Cookie

**CDP 模式使用：**
1. 安装 [web-access skill](https://github.com/anthropics/claude-code) 的 cdp-proxy
2. 在 Chrome 中登录小红书
3. 启动 cdp-proxy（`node check-deps.mjs`）
4. 直接转换即可，无需手动提供 Cookie

**HTTP + Cookie 模式：**
设置环境变量或创建 `.xhs_cookie` 文件：
```bash
export XHS_COOKIE='a1=xxx; web_session=xxx'
```

### B站视频 -> Markdown

自动识别 `bilibili.com/video/` 链接，提取视频元信息和 AI 字幕。

```python
from markitdown import MarkItDown

md = MarkItDown()
result = md.convert("https://www.bilibili.com/video/BV1BXQABNE4y/")
print(result.markdown)
```

```bash
markitdown https://www.bilibili.com/video/BV1BXQABNE4y/ -o bilibili.md
```

**特性：**
- 提取标题、UP主、时长、发布时间、播放量
- 互动数据表格（点赞/投币/收藏/弹幕/评论）
- AI 字幕自动提取（优先中文自动生成）
- 部分视频无需登录即可获取字幕
- 需要登录的视频可通过环境变量配置：
```bash
export BILIBILI_SESSDATA='你的SESSDATA值'
```
或创建 `bilibili_cookies.txt`（Netscape 格式）

## 原有功能

继承 markitdown 原生支持的所有格式：

- PDF
- PowerPoint
- Word
- Excel
- Images (EXIF metadata and OCR)
- Audio (EXIF metadata and speech transcription)
- HTML
- Text-based formats (CSV, JSON, XML)
- ZIP files (iterates over contents)
- Youtube URLs
- EPubs
- ... and more!

## 安装

```bash
git clone https://github.com/jun7799/markitdown-plus.git
cd markitdown-plus
pip install -e 'packages/markitdown[all]'
```

> 需要 Python 3.10+

## 使用示例

```bash
# 公众号文章
markitdown https://mp.weixin.qq.com/s/xxxxx -o wechat.md

# 推特长文
markitdown https://x.com/xxx/status/123456 -o tweet.md

# 本地文件
markitdown path-to-file.pdf -o document.md

# 管道方式
cat example.pdf | markitdown
```

## 致谢

- [microsoft/markitdown](https://github.com/microsoft/markitdown) - 原始项目
- [FXTwitter](https://github.com/FixTweet/FxTwitter) - X/Twitter 数据 API
