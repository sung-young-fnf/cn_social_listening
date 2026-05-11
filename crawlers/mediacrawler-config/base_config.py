# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/base_config.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

# Basic configuration
PLATFORM = "xhs"  # Platform, xhs | dy | ks | bili | wb | tieba | zhihu
KEYWORDS = "编程副业,编程兼职"  # Keyword search configuration, separated by English commas
LOGIN_TYPE = "qrcode"  # qrcode or phone or cookie  (TEST: sessid 박힌 fresh IP에서 새 QR — cookie matching 보장)
COOKIES = "acw_tc=0a00d62f17782131438816595e30c57308f205e4fca1afbca2b96527853080; abRequestId=cd0da71d-b0e1-503e-a1f1-2657059f1a35; xsecappid=xhs-pc-web; a1=19e05c3580a4v7xpgmq82artsai9myz2k8wxwz4jq50000159959; webId=ce82c93816ac941773c1144d30b06881; acw_tc=0a4a38b717782131519001756e755e7021e0af0f034793371b0139eb995828; web_session=030037aece355563bd756d27852e4aae72f33c; websectiga=634d3ad75ffb42a2ade2c5e1705a73c845837578aeb31ba0e442d75c648da36a; sec_poison_id=4cbb2c27-968c-491d-9589-bf01619d9149; ets=1778213466091; webBuild=6.8.2; loadts=1778213665858"
CRAWLER_TYPE = (
    "creator"  # Crawling type, search (keyword search) | detail (post details) | creator (creator homepage data)
)
# Whether to enable IP proxy
ENABLE_IP_PROXY = True

# Number of proxy IP pools
IP_PROXY_POOL_COUNT = 3

# Proxy IP provider name
IP_PROXY_PROVIDER_NAME = "oxylabs"  # kuaidaili | wandouhttp | oxylabs

# Setting to True will not open the browser (headless browser)
# Setting False will open a browser
# If Xiaohongshu keeps scanning the code to log in but fails, open the browser and manually pass the sliding verification code.
# If Douyin keeps prompting failure, open the browser and see if mobile phone number verification appears after scanning the QR code to log in. If it does, manually go through it and try again.
HEADLESS = False

# Whether to save login status
SAVE_LOGIN_STATE = True

# ==================== CDP (Chrome DevTools Protocol) Configuration ====================
# Whether to enable CDP mode - use the user's existing Chrome/Edge browser to crawl, providing better anti-detection capabilities
# Once enabled, the user's Chrome/Edge browser will be automatically detected and started, and controlled through the CDP protocol.
# This method uses the real browser environment, including the user's extensions, cookies and settings, greatly reducing the risk of detection.
#
# ⚠️ 주의: CDP 모드는 Playwright의 proxy 설정이 적용되지 않음 (cdp_browser.py:376 경고).
#         결과적으로 브라우저 트래픽이 사용자 PC IP로 직접 나감 → IP 차단 위험.
#         Oxylabs 프록시를 모든 트래픽에 적용하려면 False 권장 (표준 Playwright 모드).
ENABLE_CDP_MODE = False

# CDP debug port, used to communicate with the browser
# If the port is occupied, the system will automatically try the next available port
CDP_DEBUG_PORT = 9222

# 이미 실행 중인 브라우저(--remote-debugging-port가 켜진)에 attach 할지 여부.
# False = MediaCrawler가 직접 새 Chrome 인스턴스를 띄움 (우리는 이거)
# True = 사용자가 미리 chrome --remote-debugging-port=9222 로 띄워둔 인스턴스에 attach
CDP_CONNECT_EXISTING = False

# Custom browser path (optional)
# If it is empty, the system will automatically detect the installation path of Chrome/Edge
# Windows example: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
# macOS example: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CUSTOM_BROWSER_PATH = ""

# Whether to enable headless mode in CDP mode
# NOTE: Even if set to True, some anti-detection features may not work well in headless mode
CDP_HEADLESS = False

# Browser startup timeout (seconds)
BROWSER_LAUNCH_TIMEOUT = 60

# Whether to automatically close the browser when the program ends
# Set to False to keep the browser running for easy debugging
AUTO_CLOSE_BROWSER = True

# Data saving type option configuration, supports: csv, db, json, jsonl, sqlite, excel, postgres. It is best to save to DB, with deduplication function.
SAVE_DATA_OPTION = "json"  # csv or db or json or jsonl or sqlite or excel or postgres

# Data saving path - auto-generated as red-weekly-YYMMDD
from datetime import datetime as _dt
SAVE_DATA_PATH = f"output/red-weekly-{_dt.now().strftime('%y%m%d')}"

# Browser file configuration cached by the user's browser
USER_DATA_DIR = "%s_user_data_dir"  # %s will be replaced by platform name

# The number of pages to start crawling starts from the first page by default
START_PAGE = 1

# Control the number of crawled videos/posts
CRAWLER_MAX_NOTES_COUNT = 5  # TEST: 100 → 5 (5게시물 검증용, 나중에 복원)

# Date filter for creator notes (KST, inclusive). Leave empty to disable.
CRAWLER_DATE_START = ""  # TEST: 비움 (실제 코드 미사용. 원래 "2026-03-16")
CRAWLER_DATE_END = ""    # TEST: 비움 (원래 "2026-03-22")

# Controlling the number of concurrent crawlers
MAX_CONCURRENCY_NUM = 1

# Whether to enable crawling media mode (including image or video resources), crawling media is not enabled by default
ENABLE_GET_MEIDAS = True

# Whether to enable comment crawling mode. Comment crawling is enabled by default.
ENABLE_GET_COMMENTS = False

# Control the number of crawled first-level comments (single video/post)
CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = 10

# Whether to enable the mode of crawling second-level comments. By default, crawling of second-level comments is not enabled.
# If the old version of the project uses db, you need to refer to schema/tables.sql line 287 to add table fields.
ENABLE_GET_SUB_COMMENTS = False

# word cloud related
# Whether to enable generating comment word clouds
ENABLE_GET_WORDCLOUD = False
# Custom words and their groups
# Add rule: xx:yy where xx is a custom-added phrase, and yy is the group name to which the phrase xx is assigned.
CUSTOM_WORDS = {
    "零几": "年份",  # Recognize "zero points" as a whole
    "高频词": "专业术语",  # Example custom words
}

# Deactivate (disabled) word file path
STOP_WORDS_FILE = "./docs/hit_stopwords.txt"

# Chinese font file path
FONT_PATH = "./docs/STZHONGS.TTF"

# Crawl interval (요청 사이 sleep, 초)
# 정책: 한국 IP + rotation 환경에서는 5초 (사용자 명시).
# 한국 IP는 정상 사용자 IP라 짧은 간격 봇 의심 적음.
CRAWLER_MAX_SLEEP_SEC = 5

from .bilibili_config import *
from .xhs_config import *
from .dy_config import *
from .ks_config import *
from .weibo_config import *
from .tieba_config import *
from .zhihu_config import *
