import json
import nonebot
from omega_miya.utils.Omega_Base import DBTable, Result
from omega_miya.utils.Omega_plugin_utils import HttpFetcher, PicEncoder
from .config import Config

DYNAMIC_API_URL = 'https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/space_history'
GET_DYNAMIC_DETAIL_API_URL = 'https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail'
USER_INFO_API_URL = 'https://api.bilibili.com/x/space/acc/info'
DYNAMIC_URL = 'https://t.bilibili.com/'

global_config = nonebot.get_driver().config
plugin_config = Config(**global_config.dict())
BILI_SESSDATA = global_config.bili_sessdata
BILI_CSRF = global_config.bili_csrf
BILI_UID = global_config.bili_uid
ENABLE_DYNAMIC_CHECK_POOL_MODE = plugin_config.enable_dynamic_check_pool_mode


def check_bili_cookies() -> Result.DictResult:
    cookies = {}
    if BILI_SESSDATA and BILI_CSRF:
        cookies.update({'SESSDATA': BILI_SESSDATA})
        cookies.update({'bili_jct': BILI_CSRF})
        return Result.DictResult(error=False, info='Success', result=cookies)
    else:
        return Result.DictResult(error=True, info='None', result=cookies)


async def fetch_json(url: str, paras: dict = None) -> HttpFetcher.FetcherJsonResult:
    cookies = None

    # 检查cookies
    cookies_res = check_bili_cookies()
    if cookies_res.success():
        cookies = cookies_res.result

    headers = {'accept': 'application/json, text/plain, */*',
               'accept-encoding': 'gzip, deflate',
               'accept-language': 'zh-CN,zh;q=0.9',
               'dnt': '1',
               'origin': 'https://t.bilibili.com',
               'referer': 'https://t.bilibili.com/',
               'sec-ch-ua': '"Google Chrome";v="89", "Chromium";v="89", ";Not A Brand";v="99"',
               'sec-ch-ua-mobile': '?0',
               'sec-fetch-dest': 'empty',
               'sec-fetch-mode': 'cors',
               'sec-fetch-site': 'same-site',
               'sec-gpc': '1',
               'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/89.0.4389.114 Safari/537.36'
               }

    fetcher = HttpFetcher(timeout=10, flag='bilibili_dynamic_monitor', headers=headers, cookies=cookies)
    result = await fetcher.get_json(url=url, params=paras)
    return result


# 图片转base64
async def pic_2_base64(url: str) -> Result.TextResult:
    headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/89.0.4389.114 Safari/537.36',
               'origin': 'https://t.bilibili.com',
               'referer': 'https://t.bilibili.com/'}

    fetcher = HttpFetcher(
        timeout=30, attempt_limit=2, flag='bilibili_dynamic_monitor_get_image', headers=headers)
    bytes_result = await fetcher.get_bytes(url=url)
    if bytes_result.error:
        return Result.TextResult(error=True, info='Image download failed', result='')

    encode_result = PicEncoder.bytes_to_b64(image=bytes_result.result)

    if encode_result.success():
        return Result.TextResult(error=False, info='Success', result=encode_result.result)
    else:
        return Result.TextResult(error=True, info=encode_result.info, result='')


# 根据用户uid获取用户信息
async def get_user_info(user_uid) -> Result.DictResult:
    url = USER_INFO_API_URL
    payload = {'mid': user_uid}
    result = await fetch_json(url=url, paras=payload)
    if not result.success():
        return result
    else:
        user_info = result.result
        try:
            _res = {
                'status': user_info['code'],
                'name': user_info['data']['name']
            }
            result = Result.DictResult(error=False, info='Success', result=_res)
        except Exception as e:
            result = Result.DictResult(error=True, info=f'User info parse failed: {repr(e)}', result={})
    return result


# 返回某个up的所有动态id的列表
async def get_user_dynamic(user_id: int) -> Result.ListResult:
    t = DBTable(table_name='Bilidynamic')
    _res = await t.list_col_with_condition('dynamic_id', 'uid', user_id)
    if _res.error:
        return _res
    dynamic_list = [int(x) for x in _res.result]
    result = Result.ListResult(error=False, info='Success', result=dynamic_list)
    return result


# 查询动态并返回动态类型及内容
async def get_user_dynamic_history(dy_uid) -> Result.DictResult:
    _DYNAMIC_INFO = {}  # 这个字典用来放最后的输出结果
    url = DYNAMIC_API_URL
    if BILI_UID and BILI_CSRF:
        payload = {'csrf': BILI_CSRF, 'visitor_uid': BILI_UID, 'host_uid': dy_uid,
                   'offset_dynamic_id': 0, 'need_top': 0, 'platform': 'web'}
    else:
        payload = {'host_uid': dy_uid, 'offset_dynamic_id': 0, 'need_top': 0, 'platform': 'web'}

    result = await fetch_json(url=url, paras=payload)
    if not result.success():
        return result
    else:
        dynamic_info = result.result
        if not dynamic_info.get('data'):
            result = Result.DictResult(
                error=True, info=f"Get dynamic info failed: {dynamic_info.get('message')}", result={})
            return result
    for card_num in range(len(dynamic_info['data']['cards'])):
        cards = dynamic_info['data']['cards'][card_num]
        card = json.loads(cards['card'])
        '''
        动态type对应如下: 
        1 转发
        2 消息(有图片)
        4 消息(无图片)
        8 视频投稿
        16 小视频(含playurl地址)
        32 番剧更新
        64 专栏
        256 音频
        512 番剧更新(含详细信息)
        1024 未知(没遇见过)
        2048 B站活动相关(直播日历, 草图?计划?之内的)(大概是了)
        '''
        # type=1, 这是一条转发的动态
        if cards['desc']['type'] == 1:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是转发动态时评论的内容
            content = card['item']['content']
            # 这是被转发的原动态id
            origin_dynamic_id = cards['desc']['origin']['dynamic_id']
            card_dic = dict({'id': dy_id, 'type': 1, 'url': url,
                             'name': name, 'content': content, 'origin': origin_dynamic_id})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=2, 这是一条原创的动态(有图片)
        elif cards['desc']['type'] == 2:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是动态的内容
            description = card['item']['description']
            # 这是动态图片列表
            pic_urls = []
            for pic_info in card['item']['pictures']:
                pic_urls.append(pic_info['img_src'])
            card_dic = dict({'id': dy_id, 'type': 2, 'url': url, 'pic_urls': pic_urls,
                             'name': name, 'content': description, 'origin': ''})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=4, 这是一条原创的动态(无图片)
        elif cards['desc']['type'] == 4:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是动态的内容
            description = card['item']['content']
            card_dic = dict({'id': dy_id, 'type': 4, 'url': url,
                             'name': name, 'content': description, 'origin': ''})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=8, 这是发布视频
        elif cards['desc']['type'] == 8:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是视频的简介和标题
            content = card['dynamic']
            title = card['title']
            # 这是视频封面
            cover_pic_url = card.get('pic')
            card_dic = dict({'id': dy_id, 'type': 8, 'url': url, 'cover_pic_url': cover_pic_url,
                             'name': name, 'content': content, 'origin': title})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=16, 这是小视频(现在似乎已经失效？)
        elif cards['desc']['type'] == 16:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是简介
            try:
                content = card['item']['description']
            except (KeyError, TypeError):
                content = card['item']['desc']
            card_dic = dict({'id': dy_id, 'type': 16, 'url': url,
                             'name': name, 'content': content, 'origin': ''})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=32, 这是番剧更新
        elif cards['desc']['type'] == 32:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是番剧标题
            title = card['title']
            card_dic = dict({'id': dy_id, 'type': 32, 'url': url,
                             'name': name, 'content': '', 'origin': title})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=64, 这是文章动态
        elif cards['desc']['type'] == 64:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是文章的摘要和标题
            content = card['summary']
            title = card['title']
            card_dic = dict({'id': dy_id, 'type': 64, 'url': url,
                             'name': name, 'content': content, 'origin': title})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=256, 这是音频
        elif cards['desc']['type'] == 256:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是动态的内容
            description = card['intro']
            title = card['title']
            card_dic = dict({'id': dy_id, 'type': 256, 'url': url,
                             'name': name, 'content': description, 'origin': title})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=512, 番剧更新（详情）
        elif cards['desc']['type'] == 512:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是番剧标题
            title = card['apiSeasonInfo']['title']
            card_dic = dict({'id': dy_id, 'type': 512, 'url': url,
                             'name': name, 'content': '', 'origin': title})
            _DYNAMIC_INFO[card_num] = card_dic
        # type=2048, B站活动相关
        elif cards['desc']['type'] == 2048:
            # 这是动态的ID
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            # 这是动态发布者的名称
            name = cards['desc']['user_profile']['info']['uname']
            # 这是动态的内容
            content = card['vest']['content']
            # 这是日历标题和描述
            origin = str(card['sketch']['title']) + ' - ' + str(card['sketch']['desc_text'])
            card_dic = dict({'id': dy_id, 'type': 2048, 'url': url,
                             'name': name, 'content': content, 'origin': origin})
            _DYNAMIC_INFO[card_num] = card_dic
        else:
            # 其他未知类型
            dy_id = cards['desc']['dynamic_id']
            # 这是动态的链接
            url = DYNAMIC_URL + str(cards['desc']['dynamic_id'])
            name = 'Unknown'
            card_dic = dict({'id': dy_id, 'type': cards['desc']['type'], 'url': url,
                             'name': name, 'content': '', 'origin': ''})
            _DYNAMIC_INFO[card_num] = card_dic
    return Result.DictResult(error=False, info='Success', result=_DYNAMIC_INFO)


async def get_dynamic_info(dynamic_id) -> Result.DictResult:
    __payload = {'dynamic_id': dynamic_id}
    _res = await fetch_json(url=GET_DYNAMIC_DETAIL_API_URL, paras=__payload)
    if not _res.success():
        return _res
    else:
        try:
            origin_dynamic = _res.result
            origin_card = origin_dynamic['data']['card']
            origin_name = origin_card['desc']['user_profile']['info']['uname']
            origin_pics_list = []
            if origin_card['desc']['type'] == 1:
                origin_description = json.loads(origin_card['card'])['item']['content']
            elif origin_card['desc']['type'] == 2:
                origin_description = json.loads(origin_card['card'])['item']['description']
                origin_pics = json.loads(origin_card['card'])['item']['pictures']
                for item in origin_pics:
                    try:
                        origin_pics_list.append(item['img_src'])
                    except (KeyError, TypeError):
                        continue
            elif origin_card['desc']['type'] == 4:
                origin_description = json.loads(origin_card['card'])['item']['content']
            elif origin_card['desc']['type'] == 8:
                origin_description = json.loads(origin_card['card'])['dynamic']
                if not origin_description:
                    origin_description = json.loads(origin_card['card'])['title']
                try:
                    origin_pics_list.append(json.loads(origin_card['card'])['pic'])
                except (KeyError, TypeError):
                    pass
            elif origin_card['desc']['type'] == 16:
                origin_description = json.loads(origin_card['card'])['item']['description']
            elif origin_card['desc']['type'] == 32:
                origin_description = json.loads(origin_card['card'])['title']
            elif origin_card['desc']['type'] == 64:
                origin_description = json.loads(origin_card['card'])['summary']
            elif origin_card['desc']['type'] == 256:
                origin_description = json.loads(origin_card['card'])['intro']
            elif origin_card['desc']['type'] == 512:
                origin_description = json.loads(origin_card['card'])['apiSeasonInfo']['title']
            elif origin_card['desc']['type'] == 2048:
                origin_description = json.loads(origin_card['card'])['vest']['content']
            else:
                origin_description = ''
            origin = dict({'id': dynamic_id, 'type': origin_card['desc']['type'], 'url': '',
                           'name': origin_name, 'content': origin_description, 'origin': '',
                           'origin_pics': origin_pics_list})
            result = Result.DictResult(error=False, info='Success', result=origin)
        except Exception as e:
            # 原动态被删除
            origin = dict({'id': dynamic_id, 'type': -1, 'url': '',
                           'name': 'Unknown', 'content': '原动态被删除', 'origin': repr(e)})
            result = Result.DictResult(error=True, info='Dynamic not found', result=origin)
        return result


__all__ = [
    'pic_2_base64',
    'get_user_info',
    'get_user_dynamic',
    'get_user_dynamic_history',
    'get_dynamic_info',
    'ENABLE_DYNAMIC_CHECK_POOL_MODE'
]
