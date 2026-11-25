"""Owner-only controls for the scheduled EC/GFS overview GitHub Actions workflow.

This is separate from manual-overview.yml.  Only GitHub Actions APIs can
start/cancel a remote run; changing a Streamlit flag is not a cancellation.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

import requests

WORKFLOW = 'update-overview.yml'
ACTIVE = frozenset(('queued', 'in_progress', 'waiting', 'pending', 'requested'))
REPO_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def config_value(st, name, default=''):
    try:
        v = st.secrets.get(name, '')
    except Exception:
        v = ''
    return str(v or os.environ.get(name) or default)


def resolve_repository(st, root):
    repo = config_value(st, 'BURNING_SKY_GITHUB_REPOSITORY') or config_value(st, 'GITHUB_REPOSITORY')
    if not repo:
        try:
            completed = subprocess.run(('git', 'remote', 'get-url', 'origin'), cwd=Path(root),
                                       capture_output=True, text=True, timeout=2, check=False)
            match = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$', completed.stdout.strip())
            if match:
                repo = match.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return repo if REPO_PATTERN.fullmatch(repo) else ''


class ActionsOverview:
    def __init__(self, repo, token, branch='main', request=requests.request):
        if not REPO_PATTERN.fullmatch(repo):
            raise ValueError('GitHub 仓库名不合法')
        if not token:
            raise ValueError('缺少 GitHub Actions Token')
        self.base = f'https://api.github.com/repos/{repo}'
        self.branch = branch
        self.request = request
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

    def api(self, method, path, **kwargs):
        response = self.request(method, self.base + path, headers=self.headers, timeout=12, **kwargs)
        if not response.ok:
            # Do not leak request headers or PAT in UI exceptions.
            raise RuntimeError(f'GitHub API HTTP {response.status_code}，请检查 Actions 权限及工作流状态')
        return response.json() if response.content else {}

    def workflow_exists(self):
        return bool(self.api('GET', f'/actions/workflows/{WORKFLOW}').get('id'))

    def list_runs(self):
        payload = self.api('GET', f'/actions/workflows/{WORKFLOW}/runs',
                           params={'per_page': 100})
        return payload.get('workflow_runs') or []

    def active_runs(self):
        return [run for run in self.list_runs() if run.get('status') in ACTIVE]

    def dispatch(self, run_mode):
        if run_mode not in ('fresh', 'resume'):
            raise ValueError('运行模式必须是 fresh 或 resume')
        # Guard immediately before dispatch; do not enqueue a duplicate.
        if self.active_runs():
            raise RuntimeError('已有定时概览工作流运行中或排队中，已阻止重复启动')
        return self.api('POST', f'/actions/workflows/{WORKFLOW}/dispatches',
                        json={'ref': self.branch, 'inputs': {'run_mode': run_mode}})

    def cancel(self, run_ids):
        # Cancel only run IDs from this workflow's ACTIVE list, never arbitrary IDs.
        allowed = {int(run['id']) for run in self.active_runs()}
        cancelled = []
        for run_id in run_ids:
            rid = int(run_id)
            if rid not in allowed:
                continue
            self.api('POST', f'/actions/runs/{rid}/cancel')
            cancelled.append(rid)
        return cancelled


def render_overview_actions(st, root):
    """Draw independent start/stop controls in Pro's *scheduled* overview page."""
    with st.expander('定时三日概览 · GitHub Actions 控制', expanded=True):
        st.caption('仅控制 update-overview.yml，不影响独立的手动地点查询。停止将向 GitHub 发送取消请求，不只是隐藏网页进度。')
        repo = resolve_repository(st, root)
        token = config_value(st, 'BURNING_SKY_GITHUB_TOKEN')
        if not repo or not token:
            st.warning('尚未配置 GitHub Actions 连接。请检查现有 BURNING_SKY_GITHUB_TOKEN 和仓库配置，Token 需具备 Actions 读写权限。')
            return
        branch = config_value(st, 'BURNING_SKY_GITHUB_BRANCH', 'main')
        api = ActionsOverview(repo, token, branch)
        try:
            if not api.workflow_exists():
                st.error('找不到 update-overview.yml 工作流')
                return
            runs = api.list_runs()
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            st.error(f'读取 GitHub Actions 状态失败：{exc}')
            return
        active = [run for run in runs if run.get('status') in ACTIVE]
        if active:
            run = active[0]
            st.info(f'后台正在运行/排队：#{run.get("run_number", "?")} · {run.get("status", "未知")} · '
                    f'{"定时触发" if run.get("event") == "schedule" else "手动触发"}')
        elif runs:
            last = runs[0]
            st.caption(f'最近任务：#{last.get("run_number", "?")} · '
                       f'{last.get("status", "未知")} / {last.get("conclusion") or "未结束"}')
        else:
            st.caption('目前没有工作流运行记录。')
        st.caption('点击启动将重新更新未来三日的 12 项；已有后台任务时按钮自动禁用。')
        c1, c2, c3 = st.columns([2, 1, 1])
        submitted = c1.button('▶ 手动启动定时概览', type='primary',
                              disabled=bool(active), key='overview_actions_start')
        stopped = c2.button('■ 停止任务', disabled=not bool(active), key='overview_actions_stop')
        refreshed = c3.button('刷新状态', key='overview_actions_refresh')
        if submitted:
            last_submit = st.session_state.get('overview_actions_last_dispatch', 0)
            if time.monotonic() - last_submit < 45:
                st.warning('刚提交过一次任务，请勿重复启动。')
            else:
                try:
                    api.dispatch('fresh')
                    st.session_state['overview_actions_last_dispatch'] = time.monotonic()
                    st.success('已向 GitHub Actions 提交启动指令。工作流状态出现可能稍有延迟。')
                except (requests.RequestException, ValueError, RuntimeError) as exc:
                    st.error(f'提交失败：{exc}')
        if stopped:
            try:
                cancelled = api.cancel([run['id'] for run in active])
                st.info(f'已请求取消 {len(cancelled)} 个定时概览工作流任务。GitHub 需要片刻终止进程；已发布的预测数据不会被清空。')
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                st.error(f'取消失败：{exc}')
        if refreshed:
            st.rerun()
        st.caption(f'工作流：{repo} / {WORKFLOW} · 定时更新仍按 GitHub cron 自动运行。')
