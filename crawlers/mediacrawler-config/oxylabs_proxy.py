# -*- coding: utf-8 -*-
"""Oxylabs Residential Proxy provider — MediaCrawler용

설치:
  cp crawlers/mediacrawler-config/oxylabs_proxy.py \
     crawlers/MediaCrawler/proxy/providers/oxylabs_proxy.py

추가로 다음 두 파일도 패치 필요:

1) MediaCrawler/proxy/types.py — ProviderNameEnum 에 한 줄 추가
   class ProviderNameEnum(Enum):
       KUAI_DAILI_PROVIDER: str = "kuaidaili"
       WANDOU_HTTP_PROVIDER: str = "wandouhttp"
       OXYLABS_PROVIDER: str = "oxylabs"   # ← 추가

2) MediaCrawler/proxy/proxy_ip_pool.py — import + dict 등록
   from proxy.providers.oxylabs_proxy import new_oxylabs_proxy   # ← 추가
   ...
   IpProxyProvider: Dict[str, ProxyProvider] = {
       ...,
       ProviderNameEnum.OXYLABS_PROVIDER.value: new_oxylabs_proxy(),  # ← 추가
   }

기본값은 도우인 코드(crawlers/douyin-weekly-v5.js:421-423) 자격증명과 동일.
필요 시 환경변수로 override:
  OXYLABS_HOST / OXYLABS_PORT / OXYLABS_USERNAME / OXYLABS_PASSWORD
"""

import os
from typing import List

from proxy import IpInfoModel, ProxyProvider
from proxy.types import ProviderNameEnum


class OxylabsProxy(ProxyProvider):
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.proxy_brand_name = ProviderNameEnum.OXYLABS_PROVIDER.value

    async def get_proxy(self, num: int) -> List[IpInfoModel]:
        # Backconnect: 같은 endpoint를 여러 번 등록해도 매 connect 시 다른 출구 IP가 나옴
        return [
            IpInfoModel(
                ip=self.host,
                port=self.port,
                user=self.username,
                password=self.password,
                protocol="http://",
                expired_time_ts=None,
            )
            for _ in range(num)
        ]


def new_oxylabs_proxy() -> OxylabsProxy:
    """sticky session 강제: sessid 미설정 시 매 connection마다 IP 로테이션 →
    cookie 발급 IP ≠ 사용 IP → WAF 461. 항상 sessid + sesstime 박아둠.
    """
    import secrets
    base_user = os.getenv("OXYLABS_USERNAME", "customer-prcs_data1_LpjIC-cc-cn")
    sessid = os.getenv("OXYLABS_SESSID") or secrets.token_hex(8)
    sesstime = os.getenv("OXYLABS_SESSTIME", "30")  # 분 단위, residential 최대 30
    username = f"{base_user}-sessid-{sessid}-sesstime-{sesstime}"
    print(
        f"[OxylabsProxy] sessid={sessid} sesstime={sesstime}m  "
        f"(영구화하려면 export OXYLABS_SESSID={sessid})"
    )
    return OxylabsProxy(
        host=os.getenv("OXYLABS_HOST", "pr.oxylabs.io"),
        port=int(os.getenv("OXYLABS_PORT", "7777")),
        username=username,
        password=os.getenv("OXYLABS_PASSWORD", "Prcsdata_1234"),
    )
