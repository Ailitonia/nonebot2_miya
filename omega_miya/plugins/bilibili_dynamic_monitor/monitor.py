import asyncio
import random
from nonebot import logger, require, get_bots
from nonebot.adapters.cqhttp import MessageSegment
from omega_miya.utils.Omega_Base import DBFriend, DBSubscription, DBDynamic, DBTable
from .utils import get_user_dynamic_history, get_user_info, get_user_dynamic, get_dynamic_info, pic_2_base64
from .utils import ENABLE_DYNAMIC_CHECK_POOL_MODE


# 检查池模式使用的检查队列
checking_pool = []

# 启用检查动态状态的定时任务
scheduler = require("nonebot_plugin_apscheduler").scheduler


# 创建用于更新数据库里面UP名称的定时任务
@scheduler.scheduled_job(
    'cron',
    # year=None,
    # month=None,
    # day='*/1',
    # week=None,
    # day_of_week=None,
    hour='3',
    minute='3',
    second='22',
    # start_date=None,
    # end_date=None,
    # timezone=None,
    id='dynamic_db_upgrade',
    coalesce=True,
    misfire_grace_time=60
)
async def dynamic_db_upgrade():
    logger.debug('dynamic_db_upgrade: started upgrade subscription info')
    t = DBTable(table_name='Subscription')
    sub_res = await t.list_col_with_condition('sub_id', 'sub_type', 2)
    for sub_id in sub_res.result:
        sub = DBSubscription(sub_type=2, sub_id=sub_id)
        _res = await get_user_info(user_uid=sub_id)
        if not _res.success():
            logger.error(f'获取用户信息失败, uid: {sub_id}, error: {_res.info}')
        up_name = _res.result.get('name')
        _res = await sub.add(up_name=up_name, live_info='B站动态')
        if not _res.success():
            logger.error(f'dynamic_db_upgrade: 更新用户信息失败, uid: {sub_id}, error: {_res.info}')
            continue
    logger.debug('dynamic_db_upgrade: upgrade subscription info completed')


# 创建动态检查函数
async def bilibili_dynamic_monitor():

    logger.debug(f"bilibili_dynamic_monitor: checking started")

    # 获取当前bot列表
    bots = []
    for bot_id, bot in get_bots().items():
        bots.append(bot)

    # 获取所有有通知权限的群组
    t = DBTable(table_name='Group')
    group_res = await t.list_col_with_condition('group_id', 'notice_permissions', 1)
    all_noitce_groups = [int(x) for x in group_res.result]

    # 获取所有启用了私聊功能的好友
    friend_res = await DBFriend.list_exist_friends_by_private_permission(private_permission=1)
    all_noitce_friends = [int(x) for x in friend_res.result]

    # 获取订阅表中的所有动态订阅
    t = DBTable(table_name='Subscription')
    sub_res = await t.list_col_with_condition('sub_id', 'sub_type', 2)
    check_sub = [int(x) for x in sub_res.result]

    if not check_sub:
        logger.debug(f'bilibili_dynamic_monitor: no dynamic subscription, ignore.')
        return

    # 注册一个异步函数用于检查动态
    async def check_dynamic(dy_uid):
        # 获取动态并返回动态类型及内容
        try:
            _res = await get_user_dynamic_history(dy_uid=dy_uid)
            if not _res.success():
                logger.error(f'bilibili_dynamic_monitor: 获取动态失败, uid: {dy_uid}, error: {_res.info}')
                return
        except Exception as _e:
            logger.error(f'bilibili_dynamic_monitor: 获取动态失败, uid: {dy_uid}, error: {repr(_e)}')
            return

        dynamic_info = dict(_res.result)

        # 用户所有的动态id
        _res = await get_user_dynamic(user_id=dy_uid)
        if not _res.success():
            logger.error(f'bilibili_dynamic_monitor: 获取用户已有动态失败, uid: {dy_uid}, error: {_res.info}')
            return
        user_dy_id_list = list(_res.result)

        sub = DBSubscription(sub_type=2, sub_id=dy_uid)

        # 获取订阅了该直播间的所有群
        sub_group_res = await sub.sub_group_list()
        sub_group = sub_group_res.result
        # 需通知的群
        notice_groups = list(set(all_noitce_groups) & set(sub_group))

        # 获取订阅了该直播间的所有好友
        sub_friend_res = await sub.sub_user_list()
        sub_friend = sub_friend_res.result
        # 需通知的好友
        notice_friends = list(set(all_noitce_friends) & set(sub_friend))

        for num in range(len(dynamic_info)):
            try:
                # 如果有新的动态
                if dynamic_info[num]['id'] not in user_dy_id_list:
                    logger.info(f"用户: {dy_uid}/{dynamic_info[num]['name']} 新动态: {dynamic_info[num]['id']}")
                    # 转发的动态
                    if dynamic_info[num]['type'] == 1:
                        # 获取原动态信息
                        origin_dynamic_id = dynamic_info[num]['origin']
                        _dy_res = await get_dynamic_info(dynamic_id=origin_dynamic_id)
                        if not _dy_res.success():
                            msg = '{}转发了{}的动态！\n\n“{}”\n{}\n{}\n@{}: {}'.format(
                                dynamic_info[num]['name'], 'Unknown',
                                dynamic_info[num]['content'], dynamic_info[num]['url'], '=' * 16,
                                'Unknown', '获取原动态失败'
                            )
                        else:
                            origin_dynamic_info = _dy_res.result
                            # 原动态type=2 或 8, 带图片
                            if origin_dynamic_info['type'] in [2, 8]:
                                # 处理图片序列
                                pic_segs = ''
                                for pic_url in origin_dynamic_info['origin_pics']:
                                    _res = await pic_2_base64(pic_url)
                                    pic_b64 = _res.result
                                    pic_segs += f'{MessageSegment.image(pic_b64)}\n'
                                msg = '{}转发了{}的动态！\n\n“{}”\n{}\n{}\n@{}: {}\n{}'.format(
                                    dynamic_info[num]['name'], origin_dynamic_info['name'],
                                    dynamic_info[num]['content'], dynamic_info[num]['url'], '=' * 16,
                                    origin_dynamic_info['name'], origin_dynamic_info['content'],
                                    pic_segs
                                )
                            # 原动态为其他类型, 无图
                            else:
                                msg = '{}转发了{}的动态！\n\n“{}”\n{}\n{}\n@{}: {}'.format(
                                    dynamic_info[num]['name'], origin_dynamic_info['name'],
                                    dynamic_info[num]['content'], dynamic_info[num]['url'], '=' * 16,
                                    origin_dynamic_info['name'], origin_dynamic_info['content']
                                )
                    # 原创的动态（有图片）
                    elif dynamic_info[num]['type'] == 2:
                        # 处理图片序列
                        pic_segs = ''
                        for pic_url in dynamic_info[num]['pic_urls']:
                            _res = await pic_2_base64(pic_url)
                            pic_b64 = _res.result
                            pic_segs += f'{MessageSegment.image(pic_b64)}\n'
                        msg = '{}发布了新动态！\n\n“{}”\n{}\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['content'],
                            dynamic_info[num]['url'], pic_segs)
                    # 原创的动态（无图片）
                    elif dynamic_info[num]['type'] == 4:
                        msg = '{}发布了新动态！\n\n“{}”\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['content'], dynamic_info[num]['url'])
                    # 视频
                    elif dynamic_info[num]['type'] == 8:
                        cover_pic_url = dynamic_info[num].get('cover_pic_url')
                        _res = await pic_2_base64(cover_pic_url)
                        pic_seg = MessageSegment.image(_res.result)
                        if dynamic_info[num]['content']:
                            msg = '{}发布了新的视频！\n\n《{}》\n“{}”\n{}\n{}'.format(
                                dynamic_info[num]['name'], dynamic_info[num]['origin'],
                                dynamic_info[num]['content'], dynamic_info[num]['url'], pic_seg)
                        else:
                            msg = '{}发布了新的视频！\n\n《{}》\n{}\n{}'.format(
                                dynamic_info[num]['name'], dynamic_info[num]['origin'],
                                dynamic_info[num]['url'], pic_seg)
                    # 小视频
                    elif dynamic_info[num]['type'] == 16:
                        msg = '{}发布了新的小视频动态！\n\n“{}”\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['content'], dynamic_info[num]['url'])
                    # 番剧
                    elif dynamic_info[num]['type'] in [32, 512]:
                        msg = '{}发布了新的番剧！\n\n《{}》\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['origin'], dynamic_info[num]['url'])
                    # 文章
                    elif dynamic_info[num]['type'] == 64:
                        msg = '{}发布了新的文章！\n\n《{}》\n“{}”\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['origin'],
                            dynamic_info[num]['content'], dynamic_info[num]['url'])
                    # 音频
                    elif dynamic_info[num]['type'] == 256:
                        msg = '{}发布了新的音乐！\n\n《{}》\n“{}”\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['origin'],
                            dynamic_info[num]['content'], dynamic_info[num]['url'])
                    # B站活动相关
                    elif dynamic_info[num]['type'] == 2048:
                        msg = '{}发布了一条活动相关动态！\n\n【{}】\n“{}”\n{}'.format(
                            dynamic_info[num]['name'], dynamic_info[num]['origin'],
                            dynamic_info[num]['content'], dynamic_info[num]['url'])
                    else:
                        logger.warning(f"未知的动态类型: {dynamic_info[num]['type']}, id: {dynamic_info[num]['id']}")
                        msg = None

                    if msg:
                        # 向群组发送消息
                        for group_id in notice_groups:
                            for _bot in bots:
                                try:
                                    await _bot.call_api(api='send_group_msg', group_id=group_id, message=msg)
                                    logger.info(f"向群组: {group_id} 发送新动态通知: {dynamic_info[num]['id']}")
                                except Exception as _e:
                                    logger.warning(f"向群组: {group_id} 发送新动态通知: {dynamic_info[num]['id']} 失败, "
                                                   f"error: {repr(_e)}")
                                    continue
                        # 向好友发送消息
                        for user_id in notice_friends:
                            for _bot in bots:
                                try:
                                    await _bot.call_api(api='send_private_msg', user_id=user_id, message=msg)
                                    logger.info(f"向好友: {user_id} 发送新动态通知: {dynamic_info[num]['id']}")
                                except Exception as _e:
                                    logger.warning(f"向好友: {user_id} 发送新动态通知: {dynamic_info[num]['id']} 失败, "
                                                   f"error: {repr(_e)}")
                                    continue

                    # 更新动态内容到数据库
                    dy_id = dynamic_info[num]['id']
                    dy_type = dynamic_info[num]['type']
                    content = dynamic_info[num]['content']
                    # 向数据库中写入动态信息
                    dynamic = DBDynamic(uid=dy_uid, dynamic_id=dy_id)
                    _res = await dynamic.add(dynamic_type=dy_type, content=content)
                    if _res.success():
                        logger.info(f"向数据库写入动态信息: {dynamic_info[num]['id']} 成功")
                    else:
                        logger.error(f"向数据库写入动态信息: {dynamic_info[num]['id']} 失败")
            except Exception as _e:
                logger.error(f'bilibili_dynamic_monitor: 解析新动态: {dy_uid} 的时发生了错误, error info: {repr(_e)}')

    # 启用了检查池模式
    if ENABLE_DYNAMIC_CHECK_POOL_MODE:
        global checking_pool

        # checking_pool为空则上一轮检查完了, 重新往里面放新一轮的uid
        if not checking_pool:
            checking_pool.extend(check_sub)

        # 看下checking_pool里面还剩多少
        waiting_num = len(checking_pool)

        # 默认单次检查并发数为2, 默认检查间隔为20s
        logger.debug(f'bili dynamic pool mode debug info, B_checking_pool: {checking_pool}')
        if waiting_num >= 2:
            # 抽取检查对象
            now_checking = random.sample(checking_pool, k=2)
            # 更新checking_pool
            checking_pool = [x for x in checking_pool if x not in now_checking]
        else:
            now_checking = checking_pool.copy()
            checking_pool.clear()
        logger.debug(f'bili dynamic pool mode debug info, A_checking_pool: {checking_pool}')
        logger.debug(f'bili dynamic pool mode debug info, now_checking: {now_checking}')

        # 检查now_checking里面的直播间(异步)
        tasks = []
        for uid in now_checking:
            tasks.append(check_dynamic(uid))
        try:
            await asyncio.gather(*tasks)
            logger.debug(f"bilibili_dynamic_monitor: pool mode enable, checking completed, "
                         f"checked: {', '.join([str(x) for x in now_checking])}.")
        except Exception as e:
            logger.error(f'bilibili_dynamic_monitor: pool mode enable, error occurred in checking  {repr(e)}')

    # 没有启用检查池模式
    else:
        # 检查所有在订阅表里面的直播间(异步)
        tasks = []
        for uid in check_sub:
            tasks.append(check_dynamic(uid))
        try:
            await asyncio.gather(*tasks)
            logger.debug(f"bilibili_dynamic_monitor: pool mode disable, checking completed, "
                         f"checked: {', '.join([str(x) for x in check_sub])}.")
        except Exception as e:
            logger.error(f'bilibili_dynamic_monitor: pool mode disable, error occurred in checking  {repr(e)}')


# 分时间段创建计划任务, 夜间闲时降低检查频率
# 根据检查池模式初始化检查时间间隔
if ENABLE_DYNAMIC_CHECK_POOL_MODE:
    # 检查池启用
    scheduler.add_job(
        bilibili_dynamic_monitor,
        'cron',
        # year=None,
        # month=None,
        # day='*/1',
        # week=None,
        # day_of_week=None,
        # hour='9-23',
        # minute='*/3',
        second='*/20',
        # start_date=None,
        # end_date=None,
        # timezone=None,
        id='bilibili_dynamic_monitor_pool_enable',
        coalesce=True,
        misfire_grace_time=30
    )
else:
    # 检查池禁用
    scheduler.add_job(
        bilibili_dynamic_monitor,
        'cron',
        # year=None,
        # month=None,
        # day='*/1',
        # week=None,
        # day_of_week=None,
        # hour=None,
        minute='*/3',
        # second='*/30',
        # start_date=None,
        # end_date=None,
        # timezone=None,
        id='bilibili_dynamic_monitor_pool_disable',
        coalesce=True,
        misfire_grace_time=30
    )

__all__ = [
    'scheduler'
]
