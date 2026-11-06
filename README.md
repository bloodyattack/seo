feat: add Python subdomain enumeration tool

## رادار سئو

رادار سئو یک ابزار وب فارسی برای ممیزی فنی یک صفحهٔ عمومی است. مواردی مثل عنوان و توضیحات متا، H1، نسخهٔ موبایل، canonical، تصاویر بدون alt، دادهٔ ساختاریافته و noindex را بررسی می‌کند و برای هر مورد پیشنهاد اصلاح می‌دهد.

### اجرا

پیش‌نیاز: Python 3.7 یا جدیدتر.

```bash
python server.py
```

سپس `http://127.0.0.1:8765` را در مرورگر باز کنید. سرور فقط روی رایانهٔ خودتان گوش می‌دهد. بررسی پایه فقط یک صفحه را می‌خواند، آدرس‌های خصوصی و محلی را نمی‌پذیرد و اندازهٔ HTML را به ۱٫۵ مگابایت محدود می‌کند.

تحلیل PageSpeed موبایل به‌صورت اختیاری از Google PageSpeed Insights استفاده می‌کند و ممکن است به‌دلیل محدودیت سرویس در دسترس نباشد. امتیاز فنی این ابزار، رتبهٔ گوگل نیست. برای جایگاه واقعی در نتایج جست‌وجو، کلمهٔ کلیدی، کشور و داده‌های Google Search Console یا یک سرویس رتبه‌گیری معتبر لازم است. این ابزار یک صفحه را بررسی می‌کند و ممیزی کامل سایت یا تضمین رتبه ارائه نمی‌دهد.

برای اجرای ابزار شناسایی زیردامنهٔ قدیمی، جداگانه `pip install requests` را اجرا کنید.

## ابزار شناسایی زیردامنه

This tool combines passive and active discovery methods to identify subdomains associated with a target domain, mapping them to resolved IP addresses.

Key features:
- Passive enumeration using crt.sh and other OSINT sources [citation:8]
- DNS resolution with validation (removes inactive subdomains) [citation:2]
- Concurrent scanning with thread support for performance [citation:3]
- Export results to JSON format for further analysis

This is useful for:
- Security assessments and penetration testing
- Asset inventory discovery
- Attack surface mapping

Requirements for the subdomain script: Python 3.7+, pip install requests

Example:
python subdomain.py 

<img width="736" height="645" alt="sdsdsdss" src="https://github.com/user-attachments/assets/ec7453f3-3e97-46a7-8bb2-0e273cff0f72" />
python server.py

