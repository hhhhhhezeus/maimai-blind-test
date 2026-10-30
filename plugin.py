"""MaiBot entry point for 麦麦盲测局."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase
from .core import BlindError, Store
from .web import run

logger = logging.getLogger('maimai_blind_test')


class Settings(PluginConfigBase):
    config_version: str = Field(default='1.0.0', description='配置版本')
    enabled: bool = Field(default=True, description='启用盲测局')
    bind_host: str = Field(default='127.0.0.1', description='独立页面监听地址，修改后重启插件')
    bind_port: int = Field(default=18762, description='独立页面监听端口，修改后重启插件')
    public_url: str = Field(default='http://127.0.0.1:18762', description='群成员可访问的完整页面地址；局域网部署需配置主机地址')


class Config(PluginConfigBase):
    plugin: Settings = Field(default_factory=Settings)


class BlindTestPlugin(MaiBotPlugin):
    @classmethod
    def get_config_model(cls):
        return Config

    async def on_load(self):
        self.store = Store(self.ctx.paths.data_dir)
        self.server = None
        cfg = self.config.plugin
        if cfg.enabled:
            self.server = run(self.store, cfg.bind_host, cfg.bind_port)
            logger.info('盲测局页面监听 %s:%s', cfg.bind_host, cfg.bind_port)

    async def on_unload(self):
        if self.server:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            self.server = None

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str):
        logger.info('盲测局配置更新；监听地址变更需重启插件')

    def link(self, token):
        base = self.config.plugin.public_url.rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise BlindError('请先在插件配置里设置正确的 public_url（仅站点地址）。')
        return base + '/#' + token

    async def private(self, platform, user_id, text):
        opened = await self.ctx.chat.open_session(platform=platform, chat_type='private', user_id=user_id)
        if not isinstance(opened, dict) or not opened.get('success'):
            raise BlindError('未能打开私聊，请先与机器人建立私聊后重试。')
        stream = opened.get('stream_id') or opened.get('session_id')
        if not stream or not await self.ctx.send.text(text, str(stream)):
            raise BlindError('私聊发送失败，请检查适配器的私聊权限。')

    async def reply(self, stream_id, text):
        if stream_id:
            await self.ctx.send.text(text, stream_id)
        return True, '', 2

    @Command('blind_test', description='发起和参与匿名评测', pattern=r'^/盲测(?:\s+(?P<args>.*))?$')
    async def command(self, stream_id='', user_id='', group_id='', platform='qq', matched_groups=None, **kwargs):
        if not self.config.plugin.enabled or not self.server:
            return await self.reply(stream_id, '盲测局当前未启用，请检查插件设置。')
        if not group_id or not user_id:
            return await self.reply(stream_id, '请在参与活动的群聊中使用命令。')
        platform, user_id, group_id = str(platform or 'qq'), str(user_id), str(group_id)
        args = str((matched_groups or {}).get('args') or '').strip()
        try:
            action, _, rest = args.partition(' ')
            rest = rest.strip()
            if action in ('创建', '复测'):
                parent = None
                if action == '复测':
                    parent, sep, rest = rest.partition(' ')
                    if not sep: raise BlindError('用法：/盲测 复测 原活动编号 新主题')
                    parent = parent.upper()
                if not rest: raise BlindError('用法：/盲测 创建 纪录片配音选择')
                # Check configuration and private availability before creating an activity.
                self.link('check')
                code = self.store.create(rest, user_id, group_id, platform, stream_id, parent)
                token = self.store.issue(code, 'owner', user_id, group_id, platform)
                try:
                    await self.private(platform, user_id, '🎭 盲测局主持入口（仅自己使用）\n'+self.link(token)+'\n上传作品后点击开始。若链接失效，在原群发送 /盲测 管理 '+code)
                except BlindError as exc:
                    raise BlindError(f'活动 {code} 已创建，但{exc} 建立私聊后在本群发送 /盲测 管理 {code}') from exc
                return await self.reply(stream_id, '🎭 盲测局已创建：'+rest+'\n活动编号：'+code+'\n主持人请查看麦麦私聊。')
            if action in ('管理', '参加'):
                code = rest.upper()
                if not code: raise BlindError('请提供活动编号。')
                role = 'owner' if action == '管理' else 'reviewer'
                token = self.store.issue(code, role, user_id, group_id, platform)
                await self.private(platform, user_id, '🎭 你的盲测'+('主持' if role == 'owner' else '评测')+'入口（请勿转发）\n'+self.link(token))
                return await self.reply(stream_id, '🎭 请查看麦麦私聊中的个人入口。')
            if action == '状态':
                if not rest:
                    raise BlindError('用法：/盲测 状态 活动编号')
                state = self.store.status(rest.upper(), group_id, platform)
                label = {'draft': '准备中', 'open': '评测中', 'revealed': '已揭晓'}[state['state']]
                return await self.reply(stream_id, f"🎭 {state['title']} · {label}\n活动编号：{state['id']}｜作品 {state['items']} 件｜已提交 {state['votes']} 份")
            if action == '结果':
                code = rest.upper()
                with self.store.connect() as db:
                    r = db.execute('SELECT group_id,platform FROM rounds WHERE id=?', (code,)).fetchone()
                if not r or r['group_id'] != group_id or r['platform'] != platform:
                    raise BlindError('未找到本群活动。')
                result = self.store.results(code)
                lines = ['🎭 '+result['title']+' · 已揭晓', '有效反馈：'+str(result['count'])+' 份（仅代表本场参与者）']
                for i, item in enumerate(result['items']):
                    lines.append(f"作品 {chr(65+i)}：{item['identity']}｜自然 {result['counts']['natural'][item['id']]}｜符合主题 {result['counts']['fit'][item['id']]}")
                for k, name in [('natural','自然'),('fit','符合主题')]:
                    lines.append(f"{name}：差不多 {result['counts'][k]['equal']}｜无法判断 {result['counts'][k]['unknown']}")
                lines.append('个人链接可查看匿名文字反馈。')
                return await self.reply(stream_id, '\n'.join(lines))
            return await self.reply(stream_id, '🎭 麦麦盲测局\n/盲测 创建 主题\n/盲测 管理 编号\n/盲测 参加 编号\n/盲测 结果 编号\n/盲测 状态 编号\n/盲测 复测 原编号 新主题\n作品与评测通过私聊入口完成。')
        except BlindError as e:
            return await self.reply(stream_id, '🎭 '+str(e))
        except Exception:
            logger.exception('盲测命令失败')
            return await self.reply(stream_id, '🎭 操作未完成，请检查插件日志和页面配置。')


def create_plugin():
    return BlindTestPlugin()
