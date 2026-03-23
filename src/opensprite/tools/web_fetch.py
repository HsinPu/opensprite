#!/usr/bin/env python3
"""
WebFetch - 網頁內容擷取工具 (v5.2 (Firecrawl))

==========================================
使用說明 Usage Instructions
==========================================

## 安裝 Installation

```bash
# 建議安裝 trafilatura (更強的擷取能力)
pip install trafilatura

# html2text 用於 Markdown 轉換
pip install html2text

# Firecrawl (付費服務，可選)
pip install requests

# 或使用 --break-system-packages (如需要)
pip install trafilatura html2text requests --break-system-packages
```

==========================================
## Python 模組使用方式
==========================================

### 最簡單用法 (推薦)
```python
from web_fetch import fetch

# 一行就夠了！所有功能自動處理
result = fetch("https://example.com")

print(result['text'])  # 擷取的內容
```

**自動處理的事：**
- ✅ URL 驗證 (必須 http/https)
- ✅ 403 Cloudflare 重試
- ✅ 優先使用 trafilatura 擷取
- ✅ 可選 Firecrawl (付費服務，需 API Key)
- ✅ 失敗時自動用 turndown (html2text)
- ✅ 圖片自動回傳 base64
- ✅ 5MB 大小限制
- ✅ 30 秒超時

### 回傳格式
```python
result = {
    'url': 'https://example.com',      # 原始 URL
    'status': 200,                      # HTTP 狀態碼
    'contentType': 'text/html',         # 內容類型
    'title': 'Example Domain',          # 頁面標題
    'extractor': 'trafilatura',         # 擷取器: trafilatura | firecrawl | turndown | readability
    'text': '...',                      # 擷取的內容
    'truncated': False,                 # 是否被截斷
    'is_image': False,                  # 是否為圖片
    'attachments': None,                # 圖片附件 (base64)
}
```

### 進階用法
```python
from web_fetch import WebFetcher

# 自訂參數
fetcher = WebFetcher(
    max_chars=50000,        # 最大字數 (預設 50000)
    timeout=30,             # 請求超時秒數 (預設 30)
    prefer_trafilatura=True # 優先使用 trafilatura (預設 True)
)

# 擷取並指定輸出模式
result = fetcher.fetch("https://example.com", mode="markdown")  # 或 "text"

# 或使用便捷函式
from web_fetch import fetch
result = fetch(url, max_chars=10000)
```

### 參數說明

| 參數 | 類型 | 預設 | 說明 |
|------|------|------|------|
| url | str | 必填 | 要擷取的網址 |
| max_chars | int | 50000 | 最大字數限制 |
| timeout | int | 30 | 請求超時(秒) |
| mode | str | "markdown" | 輸出模式: "markdown" 或 "text" |

### 回傳格式

```python
{
    'url': str,           # 原始 URL
    'finalUrl': str,      # 最終 URL (含重導向)
    'status': int,        # HTTP 狀態碼
    'contentType': str,   # Content-Type
    'title': str|None,    # 頁面標題
    'extractor': str,     # 擷取器類型:
                          #   'trafilatura' - 專業級擷取
                          #   'turndown' - HTML 轉 Markdown
                          #   'readability' - 簡易版擷取
                          #   'json' - JSON 解析
                          #   'raw' - 原始內容
    'text': str,          # 擷取的內容
    'truncated': bool     # 是否被截斷
}
```

==========================================
## 命令列使用方式
==========================================

```bash
# 基本用法
python web_fetch.py https://example.com

# 指定最大字數
python web_fetch.py https://example.com --max-chars 5000

# 輸出純文字
python web_fetch.py https://example.com --mode text

# 組合使用
python web_fetch.py https://example.com --max-chars 3000 --mode markdown
```

### 命令列參數

| 參數 | 說明 |
|------|------|
| url | 要擷取的網址 (必填) |
| --max-chars N | 最大字數 (預設 50000) |
| --mode format | 輸出模式: markdown 或 text (預設 markdown) |

==========================================
## 擷取器說明 (優先順序)
==========================================

1. **Trafilatura** - 專業級網頁文章擷取
   - 自動去除廣告、導航、側邊欄
   - 支援中英文
   - 適合: 新聞、部落格、文章

2. **Turndown (html2text)** - HTML 轉 Markdown
   - 類似 opencode 的 turndown 套件
   - 保留標題、連結、列表、程式碼區塊
   - 適合: 當 trafilatura 失敗時

3. **Readability** - 簡易版擷取
   - 當 turndown 也失敗時使用
   - 適合: 簡單的 HTML 頁面

==========================================
## 安裝需求
==========================================

- Python 3.7+

```bash
pip install trafilatura html2text
```

==========================================
"""

import json
import re
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html import unescape

from .base import Tool

# 嘗試引入 trafilatura
try:
    from trafilatura import fetch_url as trafilatura_fetch, extract as trafilatura_extract
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False

# 嘗試引入 requests (用於 Firecrawl)
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# 嘗試引入 html2text (Turndown 風格)
try:
    import html2text
    HTML2TEXT_AVAILABLE = True
except ImportError:
    HTML2TEXT_AVAILABLE = False


# ============================================
# Turndown 風格 HTML 轉 Markdown
# ============================================

def html_to_markdown_turndown(html: str) -> str:
    """使用 html2text 將 HTML 轉換為 Markdown (類似 turndown)"""
    if not HTML2TEXT_AVAILABLE:
        return simple_html_to_markdown(html)
    
    try:
        h = html2text.HTML2Text()
        h.body_width = 0  # 不斷行
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.ignore_tables = False
        h.single_line_break = True
        h.wrap_links = False
        h.wrap_lists = True
        
        markdown = h.handle(html)
        
        # 清理多餘空白
        markdown = re.sub(r'\n{4,}', '\n\n\n', markdown)
        markdown = re.sub(r'\n{3,}', '\n\n', markdown)
        
        return markdown.strip()
    except Exception:
        return simple_html_to_markdown(html)


def extract_text_from_html(html: str) -> str:
    """使用類似 opencode HTMLRewriter 的方式提取純文字"""
    # 移除 script, style, noscript, iframe, object, embed
    text = re.sub(r'<(script|style|noscript|iframe|object|embed)[^>]*>[\s\S]*?</\1>', '', html, flags=re.IGNORECASE)
    
    # 移除所有 HTML 標籤
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # 處理實體
    text = unescape(text)
    
    # 清理空白
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    return text.strip()


def simple_html_to_markdown(html: str) -> str:
    """簡單的 HTML 轉 Markdown (當 html2text 不可用時)"""
    # 標題
    for i in range(6, 0, -1):
        html = re.sub(rf'<h{i}[^>]*>([\s\S]*?)</h{i}>', f"#{'#'*i} \\1\n", html, flags=re.IGNORECASE)
    
    # 連結
    html = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', r'[\2](\1)', html)
    
    # 圖片
    html = re.sub(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', r'![](\1)', html)
    
    # 粗體
    html = re.sub(r'<strong[^>]*>([\s\S]*?)</strong>', r'**\1**', html)
    html = re.sub(r'<b[^>]*>([\s\S]*?)</b>', r'**\1**', html)
    
    # 斜體
    html = re.sub(r'<em[^>]*>([\s\S]*?)</em>', r'*\1*', html)
    html = re.sub(r'<i[^>]*>([\s\S]*?)</i>', r'*\1*', html)
    
    # 程式碼
    html = re.sub(r'<code[^>]*>([\s\S]*?)</code>', r'`\1`', html)
    html = re.sub(r'<pre[^>]*>([\s\S]*?)</pre>', r'```\n\1\n```', html)
    
    # 列表
    html = re.sub(r'<li[^>]*>([\s\S]*?)</li>', r'\n- \1', html)
    html = re.sub(r'<ul[^>]*>', '', html)
    html = re.sub(r'</ul>', '', html)
    
    # 段落
    html = re.sub(r'</p>', '\n\n', html)
    html = re.sub(r'<p[^>]*>', '', html)
    
    # 移除多餘標籤
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'<hr\s*/?>', '\n---\n', html)
    
    # 移除所有剩餘標籤
    html = re.sub(r'<[^>]+>', '', html)
    
    # 實體
    html = unescape(html)
    
    # 清理
    html = re.sub(r'\n{3,}', '\n\n', html)
    
    return html.strip()


# ============================================
# 簡易 Readability (當所有方法失敗時)
# ============================================

class SimpleReadability:
    REMOVE_TAGS = {'script', 'style', 'nav', 'header', 'footer', 'aside',
                   'form', 'iframe', 'noscript', 'svg', 'button', 'input'}
    
    def __init__(self, html: str, url: str = ""):
        self.html = html
        self.url = url
        self.title = self._extract_title()
        
    def _extract_title(self) -> str:
        match = re.search(r'<title[^>]*>([\s\S]*?)</title>', self.html, re.IGNORECASE)
        return self._clean_text(match.group(1)) if match else ""
    
    def _clean_text(self, text: str) -> str:
        text = unescape(text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _remove_tags(self, html: str) -> str:
        html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
        for tag in self.REMOVE_TAGS:
            html = re.sub(rf'<{tag}[^>]*>[\s\S]*?</{tag}>', '', html, flags=re.IGNORECASE)
        return html
    
    def parse(self) -> dict:
        html = self._remove_tags(self.html)
        best_score, best_content = 0, ""
        
        content_tags = re.findall(
            r'<(article|main|section|div|p|td|th|li)[^>]*>([\s\S]*?)</\1>',
            html, re.IGNORECASE
        )
        
        for tag, content in content_tags:
            text = re.sub(r'<[^>]+>', '', content)
            text = self._clean_text(text)
            if len(text) < 50:
                continue
            
            score = min(len(text) / 100, 10)
            if tag in ('article', 'main'):
                score += 25
            if score > best_score:
                best_score, best_content = score, text
        
        if not best_content:
            body_match = re.search(r'<body[^>]*>([\s\S]*?)</body>', html, re.IGNORECASE)
            if body_match:
                best_content = self._clean_text(re.sub(r'<[^>]+>', '', body_match.group(1)))
        
        return {'title': self.title, 'content': best_content}


def extract_readability(html: str, url: str = "") -> dict:
    try:
        return SimpleReadability(html, url).parse()
    except Exception as e:
        return {'title': '', 'content': '', 'error': str(e)}


# ============================================
# 網頁擷取核心
# ============================================

# 預設 User-Agent (正常)
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

# Cloudflare 備用 User-Agent (403 時使用)
FALLBACK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def validate_url(url: str) -> bool:
    """驗證 URL 必須以 http:// 或 https:// 開頭"""
    if not url:
        raise Exception("URL is required")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise Exception("URL must start with http:// or https://")
    return True


def fetch_url(url: str, timeout: int = 30, retry_on_403: bool = True) -> tuple:
    """fetch_url with 403 retry support"""
    
    # 第一次嘗試
    content, status, headers = _do_fetch(url, timeout, DEFAULT_UA)
    
    # 403 Cloudflare 重試 (參考 OpenCode)
    if retry_on_403 and status == 403:
        cf_mitigated = headers.get("cf-mitigated")
        if cf_mitigated == "challenge":
            # 更換 User-Agent 重試
            content, status, headers = _do_fetch(url, timeout, FALLBACK_UA)
    
    return headers.get('Content-Type', 'text/html'), content, status


def _do_fetch(url: str, timeout: int, user_agent: str) -> tuple:
    """執行實際的 HTTP 請求"""
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    request = Request(url, headers=headers)
    
    try:
        with urlopen(request, timeout=timeout) as response:
            return (
                response.read(),
                response.status,
                dict(response.headers)
            )
    except HTTPError as e:
        raise Exception(f"HTTP Error: {e.code} {e.reason}")
    except URLError as e:
        raise Exception(f"URL Error: {e.reason}")


def decode_content(content: bytes, content_type: str) -> str:
    charset = 'utf-8'
    if 'charset=' in content_type:
        charset = content_type.split('charset=')[-1].split(';')[0].strip()
    try:
        return content.decode(charset)
    except UnicodeDecodeError:
        return content.decode('utf-8', errors='replace')


def truncate_text(text: str, max_chars: int) -> tuple:
    return (text, False) if len(text) <= max_chars else (text[:max_chars], True)


# ============================================
# Trafilatura 擷取
# ============================================

def extract_with_trafilatura(url: str, mode: str = 'markdown') -> dict:
    if not TRAFILATURA_AVAILABLE:
        return None
    
    try:
        downloaded = trafilatura_fetch(url)
        if not downloaded:
            return None
        
        output_format = 'markdown' if mode == 'markdown' else 'text'
        text = trafilatura_extract(downloaded, output_format=output_format)
        
        if text:
            title = None
            try:
                meta = trafilatura_extract(downloaded, output_format='json')
                if meta:
                    meta_obj = json.loads(meta)
                    title = meta_obj.get('title')
            except:
                pass
            
            return {'text': text, 'extractor': 'trafilatura', 'title': title}
    except Exception:
        pass
    
    return None


# ============================================
# Firecrawl 擷取 (付費服務)
# ============================================

DEFAULT_FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"


def extract_with_jina(url: str, timeout: int = 20) -> dict | None:
    """使用 Jina Reader API 擷取網頁（免費）
    
    參考: https://github.com/HKXU/quick-jina-reader
    
    參數:
        url: 要擷取的網址
        timeout: 超時秒數
    
    回傳:
        dict: {'text': str, 'title': str, 'extractor': 'jina'}
    """
    import httpx
    
    jina_url = f"https://r.jina.ai/{url}"
    
    try:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        
        response = httpx.get(jina_url, headers=headers, timeout=timeout, follow_redirects=True)
        
        if response.status_code == 200:
            data = response.json()
            
            title = data.get("data", {}).get("title", "") or ""
            text = data.get("data", {}).get("content", "")
            
            if text:
                # 如果有標題，加上標題
                if title:
                    text = f"# {title}\n\n{text}"
                
                return {
                    "text": text,
                    "title": title,
                    "extractor": "jina"
                }
        
        return None
        
    except Exception as e:
        print(f"Jina extraction failed: {e}")
        return None


def extract_with_firecrawl(url: str, mode: str = 'markdown', 
                           api_key: str = None, 
                           timeout: int = 30) -> dict:
    """使用 Firecrawl API 擷取網頁
    
    參數:
        url: 要擷取的網址
        mode: 輸出模式 (markdown/text)
        api_key: Firecrawl API Key
        timeout: 超時秒數
    
    需要:
        pip install requests
        Firecrawl API Key: https://firecrawl.dev
    """
    if not REQUESTS_AVAILABLE:
        return None
    
    if not api_key:
        # 嘗試從環境變數取得
        import os
        api_key = os.environ.get('FIRECRAWL_API_KEY')
    
    if not api_key:
        return None
    
    try:
        endpoint = f"{DEFAULT_FIRECRAWL_BASE_URL}/v2/scrape"
        
        body = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": timeout * 1000,
        }
        
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout
        )
        
        if not response.ok:
            return None
        
        payload = response.json()
        
        if not payload.get('success'):
            return None
        
        data = payload.get('data', {})
        raw_text = data.get('markdown') or data.get('content') or ""
        
        if not raw_text:
            return None
        
        # 轉換為純文字如果需要
        if mode == 'text':
            import re
            # 簡單的 Markdown 轉文字
            text = re.sub(r'#+ ', '', raw_text)  # 標題
            text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # 連結
            text = re.sub(r'[*_`]+', '', text)  # 強調
            text = re.sub(r'\n{3,}', '\n\n', text)  # 多餘空行
            raw_text = text.strip()
        
        metadata = data.get('metadata', {})
        
        return {
            'text': raw_text,
            'extractor': 'firecrawl',
            'title': metadata.get('title'),
            'finalUrl': metadata.get('sourceURL'),
            'status': metadata.get('statusCode'),
        }
    
    except Exception:
        pass
    
    return None


# ============================================
# 主類別
# ============================================

class WebFetcher:
    """網頁擷取器 (參考 OpenCode)"""
    
    # 圖片類型 (不包含 SVG 和特定類型)
    IMAGE_EXCLUDE = {'image/svg+xml', 'image/vnd.fastbidsheet'}
    MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5MB
    
    def __init__(self, max_chars: int = 50000, timeout: int = 30, 
                 prefer_trafilatura: bool = True,
                 permission_callback: callable = None,  # 權限詢問回調
                 retry_on_403: bool = True,             # 403 重試
                 firecrawl_api_key: str = None):       # Firecrawl API Key
        self.max_chars = max_chars
        self.timeout = timeout
        self.prefer_trafilatura = prefer_trafilatura and TRAFILATURA_AVAILABLE
        self.permission_callback = permission_callback
        self.retry_on_403 = retry_on_403
        self.firecrawl_api_key = firecrawl_api_key
    
    def fetch(self, url: str, mode: str = 'markdown') -> dict:
        # URL 驗證 (參考 OpenCode)
        validate_url(url)
        
        # 權限詢問回調 (參考 OpenCode ctx.ask)
        if self.permission_callback:
            self.permission_callback(url, mode, self.timeout)
        content_type, content, status = fetch_url(url, self.timeout, self.retry_on_403)
        
        # 檢查回應大小
        if len(content) > self.MAX_RESPONSE_SIZE:
            raise Exception("Response too large (exceeds 5MB limit)")
        
        result = {
            'url': url, 'finalUrl': url, 'status': status,
            'contentType': content_type, 'extractor': 'raw',
            'title': f"{url} ({content_type})",
            'text': '', 'truncated': False,
            'attachments': None,  # 圖片附件
            'is_image': False
        }
        
        content_type_lower = content_type.lower()
        
        # 圖片處理 (參考 OpenCode)
        mime = content_type.split(';')[0].strip().lower() if ';' in content_type else content_type.strip().lower()
        is_image = mime.startswith('image/') and mime not in self.IMAGE_EXCLUDE
        
        if is_image:
            import base64
            base64_content = base64.b64encode(content).decode('utf-8')
            result['is_image'] = True
            result['extractor'] = 'image'
            result['text'] = 'Image fetched successfully'
            result['attachments'] = [{
                'type': 'file',
                'mime': mime,
                'url': f'data:{mime};base64,{base64_content}'
            }]
            return result
        
        text = decode_content(content, content_type)
        
        # JSON 處理
        if 'application/json' in content_type_lower:
            try:
                json_data = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
                result['text'], result['truncated'] = truncate_text(json_data, self.max_chars)
                result['extractor'] = 'json'
            except:
                result['text'], result['truncated'] = truncate_text(text, self.max_chars)
        
        # HTML 處理
        elif 'text/html' in content_type_lower:
            extractor_used = None
            
            # 1. 優先嘗試 trafilatura
            if self.prefer_trafilatura:
                trafilatura_result = extract_with_trafilatura(url, mode)
                if trafilatura_result and trafilatura_result.get('text'):
                    result['text'] = trafilatura_result['text']
                    result['extractor'] = trafilatura_result['extractor']
                    result['title'] = trafilatura_result.get('title')
                    extractor_used = 'trafilatura'
            
            # 2. 如果 trafilatura 失敗，使用 Turndown (html2text)
            if not extractor_used:
                if mode == 'text':
                    # 純文字模式
                    result['text'] = extract_text_from_html(text)
                    result['extractor'] = 'turndown'
                else:
                    # Markdown 模式 (使用 html2text)
                    result['text'] = html_to_markdown_turndown(text)
                    result['extractor'] = 'turndown'
                
                # 取得標題
                title_match = re.search(r'<title[^>]*>([\s\S]*?)</title>', text, re.IGNORECASE)
                if title_match:
                    result['title'] = unescape(title_match.group(1)).strip()
            
            # 3. 如果也失敗，使用簡易 Readability
            if not result['text'] or len(result['text']) < 50:
                readability_result = extract_readability(text, url)
                if readability_result.get('content'):
                    result['title'] = readability_result.get('title')
                    result['text'] = readability_result['content']
                    result['extractor'] = 'readability'
            
            # 4. Jina Reader (免費，自動 fallback)
            if not result['text'] or len(result['text']) < 50:
                jina_result = extract_with_jina(url)
                if jina_result and jina_result.get('text'):
                    result['title'] = jina_result.get('title', result.get('title'))
                    result['text'] = jina_result['text']
                    result['extractor'] = 'jina'
            
            # 5. 如果全部都失敗，使用 Firecrawl (最後手段，付費服務)
            if (not result['text'] or len(result['text']) < 50) and self.firecrawl_api_key:
                firecrawl_result = extract_with_firecrawl(url, mode, self.firecrawl_api_key, self.timeout)
                if firecrawl_result and firecrawl_result.get('text'):
                    result['text'] = firecrawl_result['text']
                    result['extractor'] = firecrawl_result['extractor']
                    result['title'] = firecrawl_result.get('title')
                    result['finalUrl'] = firecrawl_result.get('finalUrl', url)
            
            result['text'], result['truncated'] = truncate_text(result['text'], self.max_chars)
        
        else:
            result['text'], result['truncated'] = truncate_text(text, self.max_chars)
        
        return result


class WebFetchTool(Tool):
    """Tool-compatible wrapper around WebFetcher."""

    def __init__(
        self,
        max_chars: int = 50000,
        timeout: int = 30,
        prefer_trafilatura: bool = True,
        firecrawl_api_key: str | None = None,
    ):
        self.fetcher = WebFetcher(
            max_chars=max_chars,
            timeout=timeout,
            prefer_trafilatura=prefer_trafilatura,
            firecrawl_api_key=firecrawl_api_key,
        )

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch and extract readable content from URLs. Returns title, text, and metadata."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "description": "Max characters to return", "default": 50000},
            },
            "required": ["url"],
        }

    async def execute(self, url: str, max_chars: int = 50000, **kwargs) -> str:
        result = WebFetcher(
            max_chars=max_chars,
            timeout=self.fetcher.timeout,
            prefer_trafilatura=self.fetcher.prefer_trafilatura,
            firecrawl_api_key=self.fetcher.firecrawl_api_key,
        ).fetch(url)
        return json.dumps(
            {
                "url": result.get("url"),
                "finalUrl": result.get("finalUrl"),
                "status": result.get("status"),
                "title": result.get("title"),
                "extractor": result.get("extractor"),
                "truncated": result.get("truncated"),
                "text": result.get("text"),
            },
            ensure_ascii=False,
        )


# ============================================
# 便捷函式
# ============================================

def fetch(url: str, max_chars: int = 50000, timeout: int = 30,
          permission_callback: callable = None, retry_on_403: bool = True,
          firecrawl_api_key: str = None) -> dict:
    """快速擷取網頁
    
    參數:
        url: 網址
        max_chars: 最大字數
        timeout: 超時秒數
        permission_callback: 權限詢問回調 (url, mode, timeout) -> None
        retry_on_403: 是否在 403 時重試 (預設 True)
        firecrawl_api_key: Firecrawl API Key (可選，付費服務)
    """
    return WebFetcher(
        max_chars=max_chars, 
        timeout=timeout,
        permission_callback=permission_callback,
        retry_on_403=retry_on_403,
        firecrawl_api_key=firecrawl_api_key
    ).fetch(url)


