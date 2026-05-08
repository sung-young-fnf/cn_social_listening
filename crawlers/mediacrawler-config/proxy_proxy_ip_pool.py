# -*- coding: utf-8 -*-
# 패치본: MediaCrawler/proxy/proxy_ip_pool.py
# 변경:
#   1. import에 new_oxylabs_proxy 추가 (36행)
#   2. IpProxyProvider dict에 OXYLABS_PROVIDER 등록 (157행)
#
# 설치:
#   cp crawlers/mediacrawler-config/proxy_proxy_ip_pool.py crawlers/MediaCrawler/proxy/proxy_ip_pool.py

import random
from typing import Dict, List

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed
from tools.httpx_util import make_async_client

import config
from proxy.providers import (
    new_kuai_daili_proxy,
    new_wandou_http_proxy,
)
from proxy.providers.oxylabs_proxy import new_oxylabs_proxy   # ← 우리 추가
from tools import utils

from .base_proxy import ProxyProvider
from .types import IpInfoModel, ProviderNameEnum


class ProxyIpPool:

    def __init__(
        self, ip_pool_count: int, enable_validate_ip: bool, ip_provider: ProxyProvider
    ) -> None:
        self.valid_ip_url = "https://echo.apifox.cn/"
        self.ip_pool_count = ip_pool_count
        self.enable_validate_ip = enable_validate_ip
        self.proxy_list: List[IpInfoModel] = []
        self.ip_provider: ProxyProvider = ip_provider
        self.current_proxy: IpInfoModel | None = None

    async def load_proxies(self) -> None:
        self.proxy_list = await self.ip_provider.get_proxy(self.ip_pool_count)

    async def _is_valid_proxy(self, proxy: IpInfoModel) -> bool:
        utils.logger.info(
            f"[ProxyIpPool._is_valid_proxy] testing {proxy.ip} is it valid "
        )
        try:
            if proxy.user and proxy.password:
                proxy_url = f"http://{proxy.user}:{proxy.password}@{proxy.ip}:{proxy.port}"
            else:
                proxy_url = f"http://{proxy.ip}:{proxy.port}"

            async with make_async_client(proxy=proxy_url) as client:
                response = await client.get(self.valid_ip_url)
            if response.status_code == 200:
                return True
            else:
                return False
        except Exception as e:
            utils.logger.info(
                f"[ProxyIpPool._is_valid_proxy] testing {proxy.ip} err: {e}"
            )
            raise e

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def get_proxy(self) -> IpInfoModel:
        if len(self.proxy_list) == 0:
            await self._reload_proxies()

        proxy = random.choice(self.proxy_list)
        self.proxy_list.remove(proxy)
        if self.enable_validate_ip:
            if not await self._is_valid_proxy(proxy):
                raise Exception(
                    "[ProxyIpPool.get_proxy] current ip invalid and again get it"
                )
        self.current_proxy = proxy
        return proxy

    def is_current_proxy_expired(self, buffer_seconds: int = 30) -> bool:
        if self.current_proxy is None:
            return True
        return self.current_proxy.is_expired(buffer_seconds)

    async def get_or_refresh_proxy(self, buffer_seconds: int = 30) -> IpInfoModel:
        if self.is_current_proxy_expired(buffer_seconds):
            utils.logger.info(
                f"[ProxyIpPool.get_or_refresh_proxy] Current proxy expired or not set, getting new proxy..."
            )
            return await self.get_proxy()
        return self.current_proxy

    async def _reload_proxies(self):
        self.proxy_list = []
        await self.load_proxies()


IpProxyProvider: Dict[str, ProxyProvider] = {
    ProviderNameEnum.KUAI_DAILI_PROVIDER.value: new_kuai_daili_proxy(),
    ProviderNameEnum.WANDOU_HTTP_PROVIDER.value: new_wandou_http_proxy(),
    ProviderNameEnum.OXYLABS_PROVIDER.value: new_oxylabs_proxy(),   # ← 우리 추가
}


async def create_ip_pool(ip_pool_count: int, enable_validate_ip: bool) -> ProxyIpPool:
    pool = ProxyIpPool(
        ip_pool_count=ip_pool_count,
        enable_validate_ip=enable_validate_ip,
        ip_provider=IpProxyProvider.get(config.IP_PROXY_PROVIDER_NAME),
    )
    await pool.load_proxies()
    return pool
