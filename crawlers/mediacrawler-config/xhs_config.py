# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/config/xhs_config.py
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


# Xiaohongshu platform configuration

# 국제판(rednote.com) 사용 여부. False = 중국 본토 xiaohongshu.com (우리는 이거)
XHS_INTERNATIONAL = False

# Sorting method, the specific enumeration value is in media_platform/xhs/field.py
SORT_TYPE = "popularity_descending"

# Specify the note URL list, which must carry the xsec_token parameter
XHS_SPECIFIED_NOTE_URL_LIST = [
    "https://www.xiaohongshu.com/explore/64b95d01000000000c034587?xsec_token=AB0EFqJvINCkj6xOCKCQgfNNh8GdnBC_6XecG4QOddo3Q=&xsec_source=pc_cfeed"
    # ........................
]

# Specify the creator URL list, which needs to carry xsec_token and xsec_source parameters.

XHS_CREATOR_ID_LIST = [
    # === celeb (68) ===
    "https://www.xiaohongshu.com/user/profile/5842afd75e87e7332ea90fda",  # 虞书欣Esther
    "https://www.xiaohongshu.com/user/profile/5a0184984eacab2b30e4dc48",  # 章若楠
    "https://www.xiaohongshu.com/user/profile/600e2e43000000000101c66e",  # 张凌赫official
    "https://www.xiaohongshu.com/user/profile/5a67827f4eacab25721a35e3",  # 卢昱晓
    "https://www.xiaohongshu.com/user/profile/59074ad76a6a6964ff83912e",  # 孙怡
    "https://www.xiaohongshu.com/user/profile/60f59b14000000002002eb65",  # 檀健次
    "https://www.xiaohongshu.com/user/profile/59fc16cfe8ac2b264a44329a",  # 宋祖儿lareina
    "https://www.xiaohongshu.com/user/profile/5f686d100000000001003b18",  # 李一桐
    "https://www.xiaohongshu.com/user/profile/5eaab0f10000000001001a23",  # 张婧仪
    "https://www.xiaohongshu.com/user/profile/5a6edcafe8ac2b424ac81be2",  # 沈月
    "https://www.xiaohongshu.com/user/profile/5df1e7e900000000010084bd",  # 时代少年团
    "https://www.xiaohongshu.com/user/profile/5611b16a8a75e17f23a61ed6",  # 周也
    "https://www.xiaohongshu.com/user/profile/5aec04f6e8ac2b18b1c3b362",  # 李沁
    "https://www.xiaohongshu.com/user/profile/5820ab095e87e7075db156f2",  # 金靖
    "https://www.xiaohongshu.com/user/profile/5a548fcfe8ac2b38e2616925",  # 周洁琼
    "https://www.xiaohongshu.com/user/profile/5a2e319b11be1035130f8df5",  # 孟子义
    "https://www.xiaohongshu.com/user/profile/5dc045580000000001001cd4",  # 孙千
    "https://www.xiaohongshu.com/user/profile/5acc62a7e8ac2b04829875e1",  # 王玉雯Uvin
    "https://www.xiaohongshu.com/user/profile/5ad47bb680008671255c693f",  # 杨超越
    "https://www.xiaohongshu.com/user/profile/5935627182ec394b31d24b69",  # 龚俊Simon
    "https://www.xiaohongshu.com/user/profile/5ad450a3e8ac2b3296cc5a45",  # 刘浩存
    "https://www.xiaohongshu.com/user/profile/5ac9afa3e8ac2b4f33b17fe7",  # 陈都灵
    "https://www.xiaohongshu.com/user/profile/5c0551c7000000000500b8a1",  # 程潇
    "https://www.xiaohongshu.com/user/profile/5cfbaf16000000001000b1e3",  # 周翊然Tz
    "https://www.xiaohongshu.com/user/profile/5c5de629000000001a02f29f",  # 宋威龙
    "https://www.xiaohongshu.com/user/profile/5a5c59cee8ac2b792ce546e6",  # Zzt-朱正廷
    "https://www.xiaohongshu.com/user/profile/614210010000000002022b5e",  # 黄子弘凡Lars
    "https://www.xiaohongshu.com/user/profile/5bcc0e27f328770001846105",  # 包上恩
    "https://www.xiaohongshu.com/user/profile/5bc495dfec27fc0001bd4010",  # 侯明昊Neo
    "https://www.xiaohongshu.com/user/profile/5e91ade20000000001003bdb",  # 张子枫
    "https://www.xiaohongshu.com/user/profile/5c89ab8a000000001603107f",  # 艾米
    "https://www.xiaohongshu.com/user/profile/584646cd82ec390d801d2816",  # 王楚然
    "https://www.xiaohongshu.com/user/profile/5aa6061f4eacab09f53a8598",  # 黄子韬
    "https://www.xiaohongshu.com/user/profile/580d848c6a6a694ef13536bc",  # 谭松韵seven
    "https://www.xiaohongshu.com/user/profile/5cbff12600000000100265f1",  # 颜安
    "https://www.xiaohongshu.com/user/profile/5ad47bb580008671255c6917",  # 吴宣仪
    "https://www.xiaohongshu.com/user/profile/5bacf038bd54a600014be87c",  # 刘轩丞
    "https://www.xiaohongshu.com/user/profile/5ae72ebc11be101a234053f4",  # 邢菲
    "https://www.xiaohongshu.com/user/profile/55ec26c7c2bdeb5b93ede5b2",  # 展轩
    "https://www.xiaohongshu.com/user/profile/6269538e000000002102a58a",  # 文淇
    "https://www.xiaohongshu.com/user/profile/5ef77159000000000101da2f",  # 鹿晗
    "https://www.xiaohongshu.com/user/profile/61c1e4b30000000002020e03",  # 徐若晗
    "https://www.xiaohongshu.com/user/profile/58f0f78c5e87e7517b0ec8e8",  # 代露娃
    "https://www.xiaohongshu.com/user/profile/5a1fbe434eacab1f2fbf9911",  # 田嘉瑞
    "https://www.xiaohongshu.com/user/profile/5bf101434b5cbd0001bd3d10",  # 任敏
    "https://www.xiaohongshu.com/user/profile/5f1530700000000001005397",  # 张艺凡
    "https://www.xiaohongshu.com/user/profile/6861d7f8000000001b0200a5",  # 鞠婧祎_Official
    "https://www.xiaohongshu.com/user/profile/5b1e5f51e8ac2b2efd0d8c7a",  # Kelly于文文
    "https://www.xiaohongshu.com/user/profile/60f3b058000000000101e8bb",  # 常华森
    "https://www.xiaohongshu.com/user/profile/5b8d5f695a5d4630d9bba7be",  # 陈昊宇Amy
    "https://www.xiaohongshu.com/user/profile/60e3f916000000000101d907",  # 费启鸣
    "https://www.xiaohongshu.com/user/profile/6076b2b4000000000101ce9b",  # 张康乐
    "https://www.xiaohongshu.com/user/profile/5e1a633c000000000100b255",  # 向涵之
    "https://www.xiaohongshu.com/user/profile/6051e58a000000000100b9a2",  # 董思成Winwin
    "https://www.xiaohongshu.com/user/profile/5ad5a69511be1041b8e7152e",  # 辛芷蕾
    "https://www.xiaohongshu.com/user/profile/5c62c3f400000000110213cf",  # 陈星旭
    "https://www.xiaohongshu.com/user/profile/600382980000000001004aa1",  # 邓恩熙
    "https://www.xiaohongshu.com/user/profile/63de4da20000000026006ba9",  # 陈鑫海Ocean
    "https://www.xiaohongshu.com/user/profile/5bcb47175597250001f5cb40",  # 王影璐-
    "https://www.xiaohongshu.com/user/profile/6833f5b8000000000e01efaa",  # 张晚意
    "https://www.xiaohongshu.com/user/profile/60b7c6760000000001002ab6",  # 闫桉
    "https://www.xiaohongshu.com/user/profile/5eff7d690000000001004f4c",  # 杨幂FashionNotes
    "https://www.xiaohongshu.com/user/profile/62f23d9f000000001f016c9c",  # 唐嫣
    "https://www.xiaohongshu.com/user/profile/5ded1e200000000001007b0f",  # 庆怜Caelan
    "https://www.xiaohongshu.com/user/profile/6338e860000000001802d74d",  # 鹤秋
    "https://www.xiaohongshu.com/user/profile/5b289f534eacab2893f68dbe",  # 我是梓渝
    "https://www.xiaohongshu.com/user/profile/5fafc1d0000000000100b0b1",  # 我是徐振轩
    "https://www.xiaohongshu.com/user/profile/601d6c8700000000010007b5",  # 我老板是张艺兴
    # === influencer (75) ===
    "https://www.xiaohongshu.com/user/profile/5a16311de8ac2b349577ec8e",  # 豆豆_Babe
    "https://www.xiaohongshu.com/user/profile/5aae4070e8ac2b068d00451d",  # 不潘
    "https://www.xiaohongshu.com/user/profile/5f0f0cb90000000001000a2e",  # 垫底辣孩
    "https://www.xiaohongshu.com/user/profile/5a8cf39111be10466d285d6b",  # 白昼小熊
    "https://www.xiaohongshu.com/user/profile/5e78c0c60000000001004607",  # 人猿泰山
    "https://www.xiaohongshu.com/user/profile/64cf6150000000000e02404c",  # 爆胎草莓粥
    "https://www.xiaohongshu.com/user/profile/69a39255000000002102283d",  # Enndme
    "https://www.xiaohongshu.com/user/profile/5bc9d2fa636c170001715db8",  # 碳酸饮料拜拜
    "https://www.xiaohongshu.com/user/profile/5f55f9f5000000000101e74b",  # 郭猫宁
    "https://www.xiaohongshu.com/user/profile/62db7c75000000001e01e1ac",  # 李歪歪
    "https://www.xiaohongshu.com/user/profile/604cd6d80000000001008eb8",  # 十一列车-
    "https://www.xiaohongshu.com/user/profile/584d62cb6a6a6905ded39c87",  # 黑黑草梅
    "https://www.xiaohongshu.com/user/profile/657baa5d000000001902de7d",  # 井川里予
    "https://www.xiaohongshu.com/user/profile/68c5a00f00000000190232e8",  # 火山大王
    "https://www.xiaohongshu.com/user/profile/678554a70000000008018d31",  # ranran
    "https://www.xiaohongshu.com/user/profile/5b8b63b3b0d787000169b7b3",  # 愚蠢小刘
    "https://www.xiaohongshu.com/user/profile/5c454bb900000000070116a5",  # 子回頭是浪
    "https://www.xiaohongshu.com/user/profile/5cf52d67000000001800e140",  # 茜茜XIXI
    "https://www.xiaohongshu.com/user/profile/5a886cba11be102fb6f3459a",  # chichi是吃吃
    "https://www.xiaohongshu.com/user/profile/5dcbdadc0000000001003230",  # 姜 峰 勇 🌊
    "https://www.xiaohongshu.com/user/profile/5e8c05aa00000000010051ff",  # 曲曲
    "https://www.xiaohongshu.com/user/profile/603bb0a8000000000100501a",  # 陶四七-
    "https://www.xiaohongshu.com/user/profile/646c717400000000100255ad",  # 一只白
    "https://www.xiaohongshu.com/user/profile/5a406ae44eacab4a2af82e8b",  # FortyLions
    "https://www.xiaohongshu.com/user/profile/66de6ddf000000000d0278b1",  # 是小龙吖
    "https://www.xiaohongshu.com/user/profile/5ac5d8be11be1064a950185f",  # 小黄油块跑
    "https://www.xiaohongshu.com/user/profile/58aea2a750c4b45dc979e9ad",  # 球球你了
    "https://www.xiaohongshu.com/user/profile/5619d5f93f0f3c04bc4c4b80",  # 全智鹅
    "https://www.xiaohongshu.com/user/profile/5b2e5624f7e8b930df5d9a88",  # 双下巴真可爱
    "https://www.xiaohongshu.com/user/profile/5ca3fcfd0000000010020801",  # Alex.
    "https://www.xiaohongshu.com/user/profile/5b2662e44eacab49106bc176",  # 敢敢子
    "https://www.xiaohongshu.com/user/profile/5dd22650000000000100baf9",  # 西in欣
    "https://www.xiaohongshu.com/user/profile/582e9987a9b2ed28b78bf5be",  # Dee
    "https://www.xiaohongshu.com/user/profile/690190630000000037005cee",  # 乌鱼小丸子
    "https://www.xiaohongshu.com/user/profile/67f7d629000000000d008bed",  # judy黄
    "https://www.xiaohongshu.com/user/profile/58ea4bc35e87e75974f682a3",  # maomao
    "https://www.xiaohongshu.com/user/profile/6011bb7b000000000101ee1f",  # 伏特嘉
    "https://www.xiaohongshu.com/user/profile/5e87e7cc00000000010024ca",  # 李雨真Jenny
    "https://www.xiaohongshu.com/user/profile/57368f7382ec3909efa30c55",  # 吕颖ivy
    "https://www.xiaohongshu.com/user/profile/5b7e6f8b48820b0001ef70c2",  # 仙女软本人
    "https://www.xiaohongshu.com/user/profile/5e35808c00000000010022ab",  # 杨日白喜欢扯皮
    "https://www.xiaohongshu.com/user/profile/5a52cc7111be1053a77bb537",  # 单眼皮的妮可
    "https://www.xiaohongshu.com/user/profile/5bea9b834bb78a0001b97ebc",  # zoe小涵
    "https://www.xiaohongshu.com/user/profile/5d209ec600000000120057e3",  # 黄饱饱了
    "https://www.xiaohongshu.com/user/profile/5dd112f70000000001003ad5",  # 天亮就睡
    "https://www.xiaohongshu.com/user/profile/5a168ecc11be1037d2c3e342",  # 怡含怡含
    "https://www.xiaohongshu.com/user/profile/5c0a2603000000000700acb3",  # 鸡腿子
    "https://www.xiaohongshu.com/user/profile/60a278dc0000000001003d77",  # 楠楠睡了
    "https://www.xiaohongshu.com/user/profile/5a7e73fbe8ac2b0c12b500b1",  # Imzsy12
    "https://www.xiaohongshu.com/user/profile/5a9a74dd11be1053476212dd",  # 温柔可爱
    "https://www.xiaohongshu.com/user/profile/61210f92000000002002c68e",  # 一芳在散步
    "https://www.xiaohongshu.com/user/profile/5b49481ae8ac2b1106ee77ba",  # NN王
    "https://www.xiaohongshu.com/user/profile/5af47bca4eacab7fbc35021d",  # 整整吃了一天
    "https://www.xiaohongshu.com/user/profile/5a017a3d11be102b0d6fb922",  # -稚笑一
    "https://www.xiaohongshu.com/user/profile/5b3c364911be10556aeb83ef",  # 一粒维c
    "https://www.xiaohongshu.com/user/profile/5b3bb183e8ac2b2ca89c6c75",  # 臭屁辣妹
    "https://www.xiaohongshu.com/user/profile/576ec2336a6a697c64af01d8",  # 钟金琪
    "https://www.xiaohongshu.com/user/profile/5b58613e11be102df4349843",  # 青梅
    "https://www.xiaohongshu.com/user/profile/5b2b0df94eacab669042495e",  # 零肆叁
    "https://www.xiaohongshu.com/user/profile/59361c6f5e87e72499716248",  # 厉害的小红
    "https://www.xiaohongshu.com/user/profile/5daec1730000000001009ff2",  # 張恩镜Zej
    "https://www.xiaohongshu.com/user/profile/5b272f736b58b7056dd5f160",  # 这位小朋友很厉害
    "https://www.xiaohongshu.com/user/profile/5c72962b0000000012016770",  # 狮紫_
    "https://www.xiaohongshu.com/user/profile/588d476a50c4b428c632f60d",  # 橙c小宝贝oran wuu
    "https://www.xiaohongshu.com/user/profile/5b7a55d4c2dad70001361f80",  # 不吃葱的蚊崽
    "https://www.xiaohongshu.com/user/profile/5875958882ec392558477d1f",  # JINGSchannel
    "https://www.xiaohongshu.com/user/profile/5ed49e90000000000100162f",  # 半夜去杀猪x
    "https://www.xiaohongshu.com/user/profile/5b2f483511be1043427371f9",  # 路易艾斯
    "https://www.xiaohongshu.com/user/profile/5a9ce51c11be107b8712d3d4",  # Twice-Chic
    "https://www.xiaohongshu.com/user/profile/5b590cb44eacab645eb3905f",  # Hooooka
    "https://www.xiaohongshu.com/user/profile/54f008d0b4c4d64655d3eb87",  # 布拿拿仔
    "https://www.xiaohongshu.com/user/profile/5f9fe966000000000101ea25",  # 白白草梅
    "https://www.xiaohongshu.com/user/profile/5eae59000000000001001506",  # EreCHEN
    "https://www.xiaohongshu.com/user/profile/5bbf12575607f4000195c958",  # 砂锅鸭
    "https://www.xiaohongshu.com/user/profile/5b2a521411be10039a8208d2",  # 陳创-
    # === brand (15) ===
    "https://www.xiaohongshu.com/user/profile/6346248c0000000009031092",  # TheNorthFace
    "https://www.xiaohongshu.com/user/profile/605855110000000001007f3a",  # MAMMUT 猛犸象
    "https://www.xiaohongshu.com/user/profile/5c92e68e0000000010039df7",  # MLB品牌
    "https://www.xiaohongshu.com/user/profile/6995aba6000000001d01d315",  # HOKA
    "https://www.xiaohongshu.com/user/profile/5dd38e810000000001006883",  # Discovery Expedition
    "https://www.xiaohongshu.com/user/profile/67bf1be6000000000e01d007",  # On昂跑
    "https://www.xiaohongshu.com/user/profile/55fd47b0b7ba224786fd7403",  # ARC'TERYX始祖鸟
    "https://www.xiaohongshu.com/user/profile/5c65661700000000100130cf",  # NIKE
    "https://www.xiaohongshu.com/user/profile/5b547b35e8ac2b101436d612",  # 迪桑特DESCENTE
    "https://www.xiaohongshu.com/user/profile/5b10c1a1f7e8b90a8d7040f9",  # adidas
    "https://www.xiaohongshu.com/user/profile/5b3340c211be10137cbca836",  # lululemon
    "https://www.xiaohongshu.com/user/profile/5ed9da53000000000101f01d",  # FILA运动
    "https://www.xiaohongshu.com/user/profile/6005231d000000000100bbd8",  # KOLONSPORT可隆
    "https://www.xiaohongshu.com/user/profile/6690c789000000000f036425",  # SALOMON萨洛蒙种草官
    "https://www.xiaohongshu.com/user/profile/6688cc51000000000f035d43",  # KAILAS凯乐石官方旗舰店
    # === megapage (44) ===
    "https://www.xiaohongshu.com/user/profile/616d04410000000002025e03",  # YeeLin 星装达人
    "https://www.xiaohongshu.com/user/profile/5a9cb70ae8ac2b6a4bdb34d2",  # 有种刀了我
    "https://www.xiaohongshu.com/user/profile/62050246000000001000fad6",  # lemon穿搭
    "https://www.xiaohongshu.com/user/profile/5fc9f1640000000001003a7b",  # freeloop- Fashion
    "https://www.xiaohongshu.com/user/profile/5f9bf6c7000000000100b038",  # Choc Fashion明星同款
    "https://www.xiaohongshu.com/user/profile/61a6174a000000001000e6b7",  # 明星衣橱_Daily
    "https://www.xiaohongshu.com/user/profile/63badcf8000000002702a6de",  # 狸狸&明星同款手册
    "https://www.xiaohongshu.com/user/profile/61b9ff230000000010009e3d",  # 明星时尚Note
    "https://www.xiaohongshu.com/user/profile/63b7ac48000000002702aa94",  # 潮流时尚猎手
    "https://www.xiaohongshu.com/user/profile/5d3e6e7e000000001603a77f",  # FASHIONB1
    "https://www.xiaohongshu.com/user/profile/6745e63b0000000001003c1c",  # happy*2(明星穿搭）
    "https://www.xiaohongshu.com/user/profile/655b44bf00000000020365db",  # Yoga穿搭笔记
    "https://www.xiaohongshu.com/user/profile/621f8f7200000000100059de",  # 元素周期表爱穿搭
    "https://www.xiaohongshu.com/user/profile/5c6f65ed0000000010009b4c",  # 圆圈学明星穿搭
    "https://www.xiaohongshu.com/user/profile/63f07f21000000001400cce3",  # 明星同款穿搭GET
    "https://www.xiaohongshu.com/user/profile/60890403000000000100aed4",  # Get明星私服穿搭
    "https://www.xiaohongshu.com/user/profile/5a01947311be1058659153f3",  # 女明星的日常穿搭合集
    "https://www.xiaohongshu.com/user/profile/5d37c957000000001603d4f6",  # vogue明星私服穿搭
    "https://www.xiaohongshu.com/user/profile/5f7411f6000000000101f40e",  # Soleil的穿搭分享
    "https://www.xiaohongshu.com/user/profile/6126533c000000002002cc96",  # Fashion穿搭星球
    "https://www.xiaohongshu.com/user/profile/5b46e24b4eacab45f044a1ac",  # PINKer_私服穿搭
    "https://www.xiaohongshu.com/user/profile/66ff445b000000000d026ec0",  # 果果时尚笔记
    "https://www.xiaohongshu.com/user/profile/5d5e9a6d0000000001019f2f",  # 小妍同学【明星同款】
    "https://www.xiaohongshu.com/user/profile/642526b70000000012010a95",  # FashionStation
    "https://www.xiaohongshu.com/user/profile/55157f552e1d936d09665d49",  # 54MAN
    "https://www.xiaohongshu.com/user/profile/5d0356490000000011027818",  # 明星今日同款
    "https://www.xiaohongshu.com/user/profile/657556b4000000003d0282df",  # 一本时尚书
    "https://www.xiaohongshu.com/user/profile/5ce8ea880000000018033b2e",  # Vogue Dupes
    "https://www.xiaohongshu.com/user/profile/60e5bd3400000000010018c6",  # Fashion Beep
    "https://www.xiaohongshu.com/user/profile/629ce92c0000000021021e96",  # Chicoutfit
    "https://www.xiaohongshu.com/user/profile/658e54d0000000002001c11b",  # 披萨颖
    "https://www.xiaohongshu.com/user/profile/54f85b6a2e1d934c451d1575",  # Stylenotes
    "https://www.xiaohongshu.com/user/profile/5dcb76670000000001001cde",  # 明星潮搭Daily
    "https://www.xiaohongshu.com/user/profile/627131b10000000021022ec7",  # FashionAmericano
    "https://www.xiaohongshu.com/user/profile/5a7db8b64eacab3948179299",  # mrgod
    "https://www.xiaohongshu.com/user/profile/67590a3a000000001c01a035",  # Stylish Book
    "https://www.xiaohongshu.com/user/profile/5a756a8b11be1001cc6da683",  # Nicole爱种草
    "https://www.xiaohongshu.com/user/profile/5f0d6a890000000001002b7e",  # Paradise明星同款更新
    "https://www.xiaohongshu.com/user/profile/66052d4c000000000b00ddbb",  # 图图明星同款
    "https://www.xiaohongshu.com/user/profile/62d3bc33000000000e00d245",  # 剧集穿搭指南
    "https://www.xiaohongshu.com/user/profile/6642d28b000000000303121d",  # Sync-Chic
    "https://www.xiaohongshu.com/user/profile/6081764c0000000001001a23",  # 秋林 Fashion
    "https://www.xiaohongshu.com/user/profile/61861c2700000000210277cb",  # Matching outfits
    "https://www.xiaohongshu.com/user/profile/5aee79cce8ac2b3edf849f8a",  # 明星同款
]
