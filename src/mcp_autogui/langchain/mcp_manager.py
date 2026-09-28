#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, get_default_environment
from langchain_mcp import MCPToolkit

class McpManager:
    json_mtime = 0.0
    mcp_tools = []
    tasks = []
    is_exit = False
    loaded_tasks = 0

    async def load(self, settings_file_path='settings/mcp_config.json'):
        if os.path.isfile(settings_file_path) and self.json_mtime != os.path.getmtime(settings_file_path):
            self.is_exit = True
            await asyncio.gather(*self.tasks)
            self.tasks = []
            self.mcp_tools = []
            self.is_exit = False
            self.json_mtime = os.path.getmtime(settings_file_path)
            with open(settings_file_path, mode='r', encoding='UTF-8') as f:
                mcp_dict_all = json.load(f)
            self.loaded_tasks = 0
            for target in mcp_dict_all['mcpServers'].values():
                self.tasks.append(asyncio.create_task(self.add_server(target)))
            while self.loaded_tasks < len(self.tasks) and not self.is_exit:
                await asyncio.sleep(0.1)
            return True
        return False

    async def add_server(self, target):
        def _merged_env():
            return (get_default_environment() | target['env']) if 'env' in target else None

        def _build_sse_url():
            return target.get('url')

        async def _run_session(read, write):
            async with ClientSession(read, write) as session:
                toolkit = MCPToolkit(session=session)
                await toolkit.initialize()
                self.mcp_tools += toolkit.get_tools()
                self.loaded_tasks += 1
                while not self.is_exit:
                    await asyncio.sleep(0.1)

        try:
            sse_url = _build_sse_url()
            transport = target.get('transport', 'stdio')
            use_remote = transport in {'sse', 'streamable-http'}

            if use_remote:
                proc = None
                try:
                    if 'command' in target and target['command']:
                        env = _merged_env()
                        if os.name == 'nt' and target['command'] != 'cmd':
                            cmd = ['cmd', '/c', target['command'], *target.get('args', [])]
                        else:
                            cmd = [target['command'], *target.get('args', [])]
                        proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            env=env,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )

                    if sse_url is None:
                        raise ValueError(f'{transport} transport requires "url".')

                    connect_timeout_s = target.get('connect_timeout_s', 30)
                    poll_s = 0.5
                    attempts = max(1, int(connect_timeout_s / poll_s))
                    last_exc = None
                    for _ in range(attempts):
                        if self.is_exit:
                            break
                        try:
                            if transport == 'sse':
                                async with sse_client(
                                    sse_url,
                                    headers=target.get('headers'),
                                    timeout=target.get('timeout', 5),
                                    sse_read_timeout=target.get('sse_read_timeout', 300),
                                ) as (read, write):
                                    await _run_session(read, write)
                            else:
                                async with streamablehttp_client(
                                    sse_url,
                                    headers=target.get('headers'),
                                    timeout=target.get('timeout', 5),
                                    sse_read_timeout=target.get('sse_read_timeout', 300),
                                ) as (read, write, _get_session_id):
                                    await _run_session(read, write)
                            return
                        except Exception as exc:
                            last_exc = exc
                            await asyncio.sleep(poll_s)
                    if last_exc is not None:
                        raise last_exc
                    return
                finally:
                    if proc is not None and proc.returncode is None:
                        proc.terminate()
                        await proc.wait()

            if os.name == 'nt' and target['command'] != 'cmd':
                server_params = StdioServerParameters(
                    command='cmd',
                    args=['/c', target['command'], *target['args']],
                    env=_merged_env(),
                )
            else:
                server_params = StdioServerParameters(
                    command=target['command'],
                    args=target['args'],
                    env=_merged_env(),
                )
            async with stdio_client(server_params) as (read, write):
                await _run_session(read, write)
        except Exception:
            self.loaded_tasks += 1
            return

    def get_tools(self):
        return self.mcp_tools

    def stop_servers(self):
        self.is_exit = True

    def __del__(self):
        self.is_exit = True
