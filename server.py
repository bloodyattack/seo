import http.client
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

HOST = "127.0.0.1"
PORT = 8765
MAX_PAGE_BYTES = 1_500_000
MAX_REDIRECTS = 4


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.robots = ""
        self.viewport = ""
        self.lang = ""
        self.h1 = []
        self.images = 0
        self.images_without_alt = 0
        self.links = 0
        self.internal_links = 0
        self.has_schema = False
        self.has_og_title = False
        self.has_og_description = False
        self._inside_title = False
        self._inside_h1 = False
        self._current_h1 = ""
        self._page_host = ""
        self._word_text = []

    def feed_page(self, source, page_url):
        self._page_host = (urlsplit(page_url).hostname or "").lower()
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        tag = tag.lower()
        if tag == "html":
            self.lang = values.get("lang", "").strip()
        elif tag == "title":
            self._inside_title = True
        elif tag == "meta":
            name = values.get("name", "").lower()
            prop = values.get("property", "").lower()
            content = values.get("content", "").strip()
            if name == "description":
                self.description = content
            elif name == "robots":
                self.robots = content
            elif name == "viewport":
                self.viewport = content
            elif prop == "og:title":
                self.has_og_title = bool(content)
            elif prop == "og:description":
                self.has_og_description = bool(content)
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonical = values.get("href", "").strip()
        elif tag == "h1":
            self._inside_h1 = True
            self._current_h1 = ""
        elif tag == "img":
            self.images += 1
            if "alt" not in values:
                self.images_without_alt += 1
        elif tag == "a" and values.get("href"):
            self.links += 1
            target = urlsplit(urljoin("https://" + self._page_host, values["href"]))
            if not target.hostname or target.hostname.lower() == self._page_host:
                self.internal_links += 1
        elif tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.has_schema = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._inside_title = False
        elif tag.lower() == "h1" and self._inside_h1:
            text = " ".join(self._current_h1.split())
            if text:
                self.h1.append(text)
            self._inside_h1 = False

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        self._word_text.append(text)
        if self._inside_title:
            self.title += text + " "
        if self._inside_h1:
            self._current_h1 += text + " "


def validate_public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("آدرس باید با http:// یا https:// شروع شود.")
    if parts.username or parts.password:
        raise ValueError("آدرس دارای اطلاعات ورود قابل بررسی نیست.")
    if parts.port not in (None, 80, 443):
        raise ValueError("فقط پورت‌های استاندارد وب قابل بررسی هستند.")
    host = parts.hostname.rstrip(".").lower()
    try:
        addresses = {result[4][0] for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ValueError("دامنه پیدا نشد یا DNS پاسخ نداد.") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("برای امنیت، آدرس‌های محلی و شبکه‌های خصوصی قابل بررسی نیستند.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, "")), sorted(addresses)


def fetch_page(url):
    current_url, _ = validate_public_url(url)
    headers = {"User-Agent": "SiteSageSEO/1.0 (single-page technical audit)", "Accept": "text/html"}
    started = time.monotonic()
    for _ in range(MAX_REDIRECTS + 1):
        current_url, addresses = validate_public_url(current_url)
        parts = urlsplit(current_url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection = None
        last_error = None
        for address in addresses:
            try:
                raw_socket = socket.create_connection((address, port), timeout=12)
                if parts.scheme == "https":
                    raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parts.hostname)
                    connection = http.client.HTTPSConnection(parts.hostname, port, timeout=12)
                else:
                    connection = http.client.HTTPConnection(parts.hostname, port, timeout=12)
                connection.sock = raw_socket
                connection.request("GET", path, headers={**headers, "Accept-Encoding": "identity"})
                response = connection.getresponse()
                break
            except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                last_error = error
                if connection:
                    connection.close()
                connection = None
        if connection is None:
            raise ValueError("دریافت صفحه انجام نشد؛ اتصال یا گواهی SSL را بررسی کنید.") from last_error
        try:
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("مسیر انتقال صفحه معتبر نیست.")
                current_url = urljoin(current_url, location)
                continue
            if response.status >= 400:
                raise ValueError(f"سرور سایت پاسخ خطا داد (HTTP {response.status}).")
            content_type = response.getheader("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise ValueError("این آدرس یک صفحه HTML نیست.")
            chunks = []
            size = 0
            while True:
                chunk = response.read(16_384)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    raise ValueError("حجم صفحه از سقف ۱٫۵ مگابایت بیشتر است.")
                chunks.append(chunk)
            encoding = response.headers.get_content_charset() or "utf-8"
            body = b"".join(chunks).decode(encoding, errors="replace")
            return current_url, response.status, response.headers, body, size, round((time.monotonic() - started) * 1000)
        finally:
            connection.close()
    raise ValueError("تعداد انتقال‌های صفحه بیش از حد مجاز است.")


def make_finding(severity, title, detail, fix, points):
    return {"severity": severity, "title": title, "detail": detail, "fix": fix, "points": points}


def audit_page(url):
    final_url, status, headers, source, byte_size, response_ms = fetch_page(url)
    parser = PageParser()
    parser.feed_page(source, final_url)
    title = " ".join(parser.title.split())
    description = " ".join(parser.description.split())
    findings = []

    def add(severity, title_text, detail, fix, points):
        findings.append(make_finding(severity, title_text, detail, fix, points))

    if urlsplit(final_url).scheme != "https":
        add("high", "سایت از HTTPS استفاده نمی‌کند", "ارتباط این صفحه رمزنگاری نشده است.",
            "گواهی SSL فعال کنید و همهٔ نسخه‌های HTTP را به HTTPS منتقل کنید.", 12)
    if not title:
        add("high", "عنوان صفحه وجود ندارد", "تگ title خالی یا غایب است.",
            "برای این صفحه یک عنوان دقیق و یکتا در تگ <title> بنویسید.", 12)
    elif len(title) < 20 or len(title) > 60:
        add("medium", "طول عنوان نیاز به بازبینی دارد", f"عنوان فعلی {len(title)} نویسه است.",
            "عنوانی روشن و توصیفی بنویسید؛ طول را راهنما بدانید، نه قانون قطعی گوگل.", 4)
    if not description:
        add("medium", "توضیحات متا وجود ندارد", "توضیحات متا برای این صفحه پیدا نشد.",
            "یک توضیح یکتا و مرتبط با محتوای صفحه در meta description قرار دهید.", 8)
    elif len(description) < 70 or len(description) > 160:
        add("low", "طول توضیحات متا قابل بهبود است", f"توضیحات فعلی {len(description)} نویسه است.",
            "توضیح را موجز و جذاب نگه دارید؛ گوگل ممکن است متن دیگری نمایش دهد.", 3)
    if not parser.h1:
        add("high", "سرتیتر اصلی (H1) پیدا نشد", "صفحه تگ H1 قابل‌مشاهده ندارد.",
            "یک H1 توصیفی و منطبق با موضوع اصلی صفحه اضافه کنید.", 8)
    elif len(parser.h1) > 1:
        add("medium", "چند H1 در صفحه وجود دارد", f"{len(parser.h1)} سرتیتر H1 پیدا شد.",
            "ساختار سرتیترها را بازبینی کنید و سلسله‌مراتب معنایی روشنی بسازید.", 3)
    if not parser.viewport:
        add("high", "تنظیم نمایش موبایل وجود ندارد", "meta viewport در صفحه نیست.",
            'تگ <meta name="viewport" content="width=device-width, initial-scale=1"> را اضافه کنید.', 8)
    if not parser.lang:
        add("low", "زبان صفحه مشخص نشده", "ویژگی lang در تگ html پیدا نشد.",
            'زبان محتوا را در تگ html مشخص کنید؛ برای فارسی از lang="fa" استفاده کنید.', 3)
    if not parser.canonical:
        add("low", "آدرس canonical وجود ندارد", "تگ canonical در صفحه پیدا نشد.",
            "برای کنترل نسخهٔ اصلی محتوا، canonical مطلق و درست اضافه کنید.", 4)
    elif urlsplit(urljoin(final_url, parser.canonical)).hostname != urlsplit(final_url).hostname:
        add("high", "Canonical به دامنهٔ دیگری اشاره می‌کند", "مقصد canonical با دامنهٔ صفحه یکسان نیست.",
            "مقصد canonical را بررسی کنید تا ناخواسته اعتبار این صفحه منتقل نشود.", 8)
    if parser.images_without_alt:
        add("medium", "تصویر بدون متن جایگزین", f"{parser.images_without_alt} تصویر از {parser.images} تصویر alt ندارد.",
            "برای تصاویر معنادار alt توصیفی بنویسید و برای تصاویر تزئینی alt خالی بگذارید.",
            min(10, parser.images_without_alt * 2))
    if not parser.has_schema:
        add("low", "دادهٔ ساختاریافته پیدا نشد", "JSON-LD در HTML اولیه پیدا نشد.",
            "در صورت تناسب، Schema.org معتبر و منطبق با محتوای قابل‌مشاهده اضافه کنید.", 3)
    if not parser.has_og_title or not parser.has_og_description:
        add("low", "اطلاعات اشتراک‌گذاری ناقص است", "عنوان یا توضیحات Open Graph پیدا نشد.",
            "برای نمایش بهتر لینک در شبکه‌های اجتماعی og:title و og:description اضافه کنید.", 2)
    robots_directive = parser.robots.lower()
    if "noindex" in robots_directive:
        add("high", "صفحه از ایندکس خارج شده", 'متای robots شامل "noindex" است.',
            "اگر صفحه باید در نتایج باشد، noindex را پس از بررسی سیاست ایندکس حذف کنید.", 15)

    score = max(0, 100 - sum(item["points"] for item in findings))
    if not findings:
        add("good", "مشکل مهمی در بررسی پایه پیدا نشد", "سیگنال‌های فنی بررسی‌شده وضعیت مناسبی دارند.",
            "برای ارزیابی کامل‌تر، داده‌های Search Console و PageSpeed را هم بررسی کنید.", 0)
    word_count = len(" ".join(parser._word_text).split())
    return {
        "url": final_url, "status": status, "title": title, "description": description,
        "score": score, "findings": findings, "metrics": {
            "h1": len(parser.h1), "images": parser.images, "imagesMissingAlt": parser.images_without_alt,
            "links": parser.links, "internalLinks": parser.internal_links, "words": word_count,
            "bytes": byte_size, "responseMs": response_ms, "lang": parser.lang or "—",
            "canonical": parser.canonical or "—", "https": urlsplit(final_url).scheme == "https",
            "robots": parser.robots or "پیش‌فرض"
        }
    }


def pagespeed_audit(url):
    safe_url, _ = validate_public_url(url)
    endpoint = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = urlencode({"url": safe_url, "strategy": "mobile", "category": ["performance", "seo"]}, doseq=True)
    request = Request(f"{endpoint}?{params}", headers={"User-Agent": "SiteSageSEO/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            data = json.loads(response.read(4_000_000).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError("PageSpeed پاسخ نداد؛ ممکن است سرویس موقتاً محدود یا در دسترس نباشد.") from error
    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})
    metrics = {}
    for key, label in (("largest-contentful-paint", "LCP"), ("cumulative-layout-shift", "CLS"),
                       ("interaction-to-next-paint", "INP"), ("first-contentful-paint", "FCP")):
        item = audits.get(key, {})
        if item.get("displayValue"):
            metrics[label] = item["displayValue"]
    return {
        "performance": round((categories.get("performance", {}).get("score") or 0) * 100),
        "seo": round((categories.get("seo", {}).get("score") or 0) * 100),
        "metrics": metrics,
        "source": "Google PageSpeed Insights / Lighthouse (موبایل)"
    }


class AppHandler(BaseHTTPRequestHandler):
    def host_is_local(self):
        return self.headers.get("Host", "").lower() in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.host_is_local():
            self.send_error(403)
            return
        if self.path.startswith("/api/pagespeed?"):
            params = parse_qs(urlsplit(self.path).query)
            try:
                result = pagespeed_audit(params.get("url", [""])[0])
                self.send_json(200, result)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except Exception:
                self.send_json(502, {"error": "ارتباط با PageSpeed ناموفق بود. کمی بعد دوباره تلاش کنید."})
            return
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            with open(Path(__file__).resolve().with_name("index.html"), "rb") as page:
                body = page.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            self.send_error(500, "index.html پیدا نشد")

    def do_POST(self):
        if not self.host_is_local():
            self.send_error(403)
            return
        if self.path != "/api/audit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                raise ValueError("درخواست بیش از حد بزرگ است.")
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = audit_page(payload.get("url", ""))
            self.send_json(200, result)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except Exception:
            self.send_json(502, {"error": "بررسی صفحه ناموفق بود. آدرس و دسترسی عمومی سایت را بررسی کنید."})

    def log_message(self, format_string, *args):
        return


if __name__ == "__main__":
    print(f"SiteSage SEO is running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), AppHandler).serve_forever()