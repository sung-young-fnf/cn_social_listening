# -*- coding: utf-8 -*-
# 패치본: MediaCrawler/tools/httpx_util.py
# 변경: curl_cffi 사용해서 Chrome TLS 위조 (JA3/JA4 핑거프린팅 우회)
#
# 설치:
#   cp crawlers/mediacrawler-config/tools_httpx_util.py crawlers/MediaCrawler/tools/httpx_util.py
#   pip install curl_cffi
#
# curl_cffi 미설치 시 자동으로 httpx로 폴백 (기존 동작 유지).

"""HTTP client with Chrome TLS fingerprint impersonation.

기본은 curl_cffi의 AsyncSession 사용 → JA3/JA4 핑거프린트가 진짜 Chrome으로 위조됨.
RedNote의 TLS 봇 감지(461 Verifytype 301 캡차) 우회용.

curl_cffi가 설치되지 않으면 자동으로 httpx로 폴백 (기존 동작).
설치: pip install curl_cffi
"""
import config

try:
    from curl_cffi.requests import AsyncSession  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

import httpx


def make_async_client(**kwargs):
    """API 호출용 비동기 HTTP 클라이언트.

    curl_cffi 사용 가능 시: Chrome 120 TLS 위조된 AsyncSession 반환.
    아니면: 기존 httpx.AsyncClient (Python TLS, 차단 가능).
    """
    verify = not getattr(config, "DISABLE_SSL_VERIFY", False)

    if _HAS_CURL_CFFI:
        # curl_cffi: impersonate로 Chrome JA3/JA4 위조
        kwargs.setdefault("impersonate", "chrome120")
        kwargs.setdefault("verify", verify)

        # httpx 호환: proxy 인자(str) → curl_cffi의 proxies dict
        if "proxy" in kwargs:
            proxy_val = kwargs.pop("proxy")
            if isinstance(proxy_val, str):
                kwargs.setdefault("proxies", {"http": proxy_val, "https": proxy_val})
            elif isinstance(proxy_val, dict):
                kwargs.setdefault("proxies", proxy_val)

        return AsyncSession(**kwargs)

    # 폴백: 기존 httpx 사용
    kwargs.setdefault("verify", verify)
    return httpx.AsyncClient(**kwargs)
