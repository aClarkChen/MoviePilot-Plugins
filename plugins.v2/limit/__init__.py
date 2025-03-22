import datetime
import threading
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import ServiceInfo


def _parse_tag_map(tag_map: str) -> Dict[str, int]:
    """解析标签限速配置"""
    parsed_map = {}
    for item in tag_map.split("\n"):
        parts = item.split(":")
        if len(parts) == 2 and parts[1].strip().isdigit():
            parsed_map[parts[0].strip()] = int(parts[1].strip())
    return parsed_map


class Limit(_PluginBase):
    # 插件名称
    plugin_name = "自动限速"
    # 插件描述
    plugin_desc = "给qb、tr的下载任务限速"
    # 插件图标
    plugin_icon = "Youtube-dl_A.png"
    # 插件版本
    plugin_version = "1.2.0"
    # 插件作者
    plugin_author = "ClarkChen"
    # 作者主页
    author_url = "https://github.com/aClarkChen"
    # 插件配置项ID前缀
    plugin_config_prefix = "Limit_"
    # 加载顺序
    plugin_order = 22
    # 可使用的用户级别
    auth_level = 2
    # 日志前缀
    LOG_TAG = "[Limit]"

    # 退出事件
    _event = threading.Event()
    # 私有属性
    sites_helper = None
    downloader_helper = None
    _scheduler = None
    _enabled = False
    _onlyonce = False
    _cover = False
    _global = False
    _interval = "计划任务"
    _interval_cron = "0 13 * * *"
    _interval_time = 24
    _interval_unit = "小时"
    _downloaders = None
    _global_speed = 0
    _tag_map = "标签:限速(KB)"
    _parsed_tag_map = {}

    def init_plugin(self, config: dict = None):
        self.downloader_helper = DownloaderHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._cover = config.get("cover")
            self._global = config.get("global")
            self._interval = config.get("interval") or "计划任务"
            self._interval_cron = config.get("interval_cron") or "0 13 * * *"
            self._interval_time = self.str_to_number(config.get("interval_time"), 24)
            self._interval_unit = config.get("interval_unit") or "小时"
            self._downloaders = config.get("downloaders")
            self._global_speed = self.str_to_number(config.get("global_speed"), 0)
            self._tag_map = config.get("tag_map") or "标签:限速(KB)"
            self._parsed_tag_map = _parse_tag_map(self._tag_map)

        # 停止现有任务
        self.stop_service()

        if self._onlyonce:
            # 创建定时任务控制器
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            # 执行一次, 关闭onlyonce
            self._onlyonce = False
            config.update({"onlyonce": self._onlyonce})
            self.update_config(config)
            # 执行自动限速
            self._scheduler.add_job(func=self._complete_limit, trigger='date',
                                    run_date=datetime.datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                    )
            if self._scheduler and self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        if not self._downloaders:
            logger.warning("尚未配置下载器，请检查配置")
            return None

        services = self.downloader_helper.get_services(name_filters=self._downloaders)
        if not services:
            logger.warning("获取下载器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的下载器，请检查配置")
            return None

        return active_services

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled:
            if self._interval == "计划任务" or self._interval == "固定间隔":
                if self._interval == "固定间隔":
                    if self._interval_unit == "小时":
                        return [{
                            "id": "Limit",
                            "name": "自动限速",
                            "trigger": "interval",
                            "func": self._complete_limit,
                            "kwargs": {
                                "hours": self._interval_time
                            }
                        }]
                    else:
                        if self._interval_time < 5:
                            self._interval_time = 5
                            logger.info(f"{self.LOG_TAG}启动定时服务: 最小不少于5分钟, 防止执行间隔太短任务冲突")
                        return [{
                            "id": "Limit",
                            "name": "自动限速",
                            "trigger": "interval",
                            "func": self._complete_limit,
                            "kwargs": {
                                "minutes": self._interval_time
                            }
                        }]
                else:
                    return [{
                        "id": "Limit",
                        "name": "自动限速",
                        "trigger": CronTrigger.from_crontab(self._interval_cron),
                        "func": self._complete_limit,
                        "kwargs": {}
                    }]
        return []

    @staticmethod
    def str_to_number(s: str, i: int) -> int:
        try:
            return int(s)
        except ValueError:
            return i

    def _complete_limit(self):
        if not self.service_infos:
            return
        logger.info(f"{self.LOG_TAG}开始执行 ...")
        for service in self.service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            logger.info(f"{self.LOG_TAG}开始扫描下载器 {downloader} ...")
            if not downloader_obj:
                logger.error(f"{self.LOG_TAG} 获取下载器失败 {downloader}")
                continue
            # 全局限速
            if self._global:
                downloader_obj.set_speed_limit(download_limit=0, upload_limit=self._global_speed)
            # 按标签限速
            if not self._parsed_tag_map:
                continue
            # 获取下载器中的种子
            torrents, error = downloader_obj.get_torrents()
            # 如果下载器获取种子发生错误 或 没有种子 则跳过
            if error or not isinstance(torrents, list):
                logger.error(f"{self.LOG_TAG} 下载器 {downloader} 获取种子失败: {error}")
                continue
            logger.info(f"{self.LOG_TAG}下载器 {downloader} 分析种子信息中 ...")
            for torrent in torrents:
                if self._get_limited(self, torrent=torrent, dl_type=service.type):
                    continue
                try:
                    if self._event.is_set():
                        logger.info(f"{self.LOG_TAG}停止服务")
                        return
                    # 获取种子hash
                    _hash = self._get_hash(torrent=torrent, dl_type=service.type)
                    # 获取种子当前标签
                    torrent_tags = self._get_tags(torrent=torrent, dl_type=service.type)
                    for tag in torrent_tags:
                        if tag in self._parsed_tag_map:
                            speed = self._parsed_tag_map[tag]
                            self._set_torrent_speed(service=service, _hash=_hash, _speed=speed)
                            break
                except Exception as e:
                    logger.error(
                        f"{self.LOG_TAG}分析种子信息时发生了错误: 下载器={downloader}, 错误={str(e)}")
        logger.info(f"{self.LOG_TAG}执行完成")

    @staticmethod
    def _get_hash(torrent: Any, dl_type: str):
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            logger.error(f"获取种子哈希值失败: {str(e)}")
            return ""

    @staticmethod
    def _get_tags(torrent: Any, dl_type: str):
        try:
            return [str(tag).strip() for tag in torrent.get("tags", "").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            logger.error(f"获取种子标签失败: {str(e)}")
            return []

    @staticmethod
    def _get_limited(self, torrent: Any, dl_type: str):
        if self._cover or dl_type != "qbittorrent":
            return False
        try:
            return torrent.up_limit > 0
        except Exception as e:
            logger.error(f"判断种子是否已限速失败: {str(e)}")
            return False

    def _set_torrent_speed(self, service: ServiceInfo, _hash: str, _speed: int = None):
        if not service or not service.instance:
            return
        downloader_obj = service.instance
        # 下载器api不通用, 因此需分开处理
        if service.type == "qbittorrent":
            downloader_obj.qbc.torrents_set_upload_limit(torrent_hashes=_hash, limit=_speed)
        else:
            downloader_obj.change_torrent(hash_string=_hash, upload_limit=_speed)
        logger.info(f"{self.LOG_TAG}下载器: {service.name} 种子id: {_hash} 上传限速为 {_speed}KB/S")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cover',
                                            'label': '覆盖模式',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'global',
                                            'label': '全局限速',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 3
                                },
                                'content': [
                                    {
                                        'component': 'VCheckboxBtn',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '运行一次'
                                        }
                                    }
                                ]
                            },
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'model': 'downloaders',
                                            'label': '下载器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in self.downloader_helper.get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval',
                                            'label': '定时任务',
                                            'items': [
                                                {'title': '禁用', 'value': '禁用'},
                                                {'title': '计划任务', 'value': '计划任务'},
                                                {'title': '固定间隔', 'value': '固定间隔'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_cron',
                                            'label': '计划任务设置',
                                            'placeholder': '0 13 * * *'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'interval_time',
                                            'label': '时间间隔, 每',
                                            'placeholder': '24'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 6,
                                    'md': 3,
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'interval_unit',
                                            'label': '单位',
                                            'items': [
                                                {'title': '小时', 'value': '小时'},
                                                {'title': '分钟', 'value': '分钟'}
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "global_speed",
                                            "label": "全局限速(KB)",
                                            "rows": 1,
                                            "placeholder": "如:100",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "tag_map",
                                            "label": "标签:限速(KB)",
                                            "rows": 5,
                                            "placeholder": "如:XX:50\nYY:100",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '每行配置一个，行数越高优先级越高，0为不限速。注意！！需用英文的:。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cover": False,
            "global": False,
            "interval": "计划任务",
            "interval_cron": "0 13 * * *",
            "interval_time": "24",
            "interval_unit": "小时",
            "global_speed": "0",
            "tag_map": "标签:限速(KB)"
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止服务时发生错误: {str(e)}")
