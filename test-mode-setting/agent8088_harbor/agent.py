"""Harbor installed-agent adapter for Agent8088.

Installs Agent8088 into each Terminal-Bench task container under /opt/agent8088
with its own uv-managed Python -- about half the task images have no Python,
and the verifier runs in the same container, so nothing touches the system
interpreter -- then runs one headless turn per trial.

    PYTHONPATH=test-mode-setting harbor run \\
        -a agent8088_harbor.agent:Agent8088 -m openai/qwen3.8-27b \\
        --ak wheel_path=/abs/path/agent8088-X-py3-none-any.whl \\
        --ak base_url=http://host.docker.internal:8000/v1 --ak budget_sec=870 ...

See test-mode-setting/README.md.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from agent8088_harbor.config_edit import budget_edits, config_edit_command

PREFIX = "/opt/agent8088"
BENCH_REMOTE = f"{PREFIX}/test-mode-setting"
BENCH_LOCAL = Path(__file__).resolve().parent.parent  # test-mode-setting
UV = f"{PREFIX}/uv/uv"


class Agent8088Options(InstalledAgentOptions):
    wheel_path: str = Field(description="Host path to the agent8088 wheel (uv build --wheel).")
    base_url: str = Field(
        default="http://host.docker.internal:8000/v1",
        description="OpenAI-compatible endpoint as seen from inside the task container.")
    budget_sec: int = Field(
        default=0,
        description="In-container watchdog in seconds (task agent timeout minus a margin); 0 = none.")
    workdir: str = Field(default="/app", description="Task working directory (PROJECT_ROOT).")
    python_version: str = Field(default="3.12", description="uv-managed Python for agent8088.")


class Agent8088(BaseInstalledAgent):
    options_model = Agent8088Options
    MODEL_CONNECTION = ModelConnectionSpec(default_provider="openai", passthrough=True)
    # uv + Python + agent8088's dependencies download inside the container.
    DEFAULT_SETUP_TIMEOUT_SEC: ClassVar[float] = 900.0

    @staticmethod
    def name() -> str:
        return "agent8088"

    def get_version_command(self) -> str | None:
        return f"{PREFIX}/bin/agent8088 --version"

    def parse_version(self, stdout: str) -> str:
        return stdout.strip().removeprefix("agent8088").strip()

    def _served_model(self) -> str:
        return (self.model_name or "").split("/", 1)[-1]

    async def install(self, environment: BaseEnvironment) -> None:
        options = self.options
        wheel = Path(options.wheel_path).expanduser().resolve()
        if not wheel.is_file():
            raise FileNotFoundError(f"agent8088 wheel not found: {wheel}")
        await self.ensure_system_dependencies(environment, ("curl",))
        await self.exec_as_root(environment, command=f"mkdir -p {PREFIX} /installed-agent")
        await environment.upload_dir(BENCH_LOCAL, BENCH_REMOTE)
        remote_wheel = f"/installed-agent/{wheel.name}"
        await environment.upload_file(wheel, remote_wheel)

        uv_env = (
            f"UV_INSTALL_DIR={PREFIX}/uv UV_NO_MODIFY_PATH=1 "
            f"UV_PYTHON_INSTALL_DIR={PREFIX}/python UV_PYTHON_PREFERENCE=only-managed "
            f"UV_TOOL_DIR={PREFIX}/tools UV_TOOL_BIN_DIR={PREFIX}/bin "
            f"UV_CACHE_DIR={PREFIX}/cache"
        )
        py = shlex.quote(options.python_version)
        await self.exec_as_root(environment, command=(
            "set -euo pipefail; "
            f"export {uv_env}; "
            "curl -LsSf https://astral.sh/uv/install.sh | sh; "
            f"{UV} python install {py}; "
            f"{UV} tool install --python {py} {shlex.quote(remote_wheel)}; "
            f"rm -rf {PREFIX}/cache; "
            f"chmod +x {BENCH_REMOTE}/container/run-agent.sh"
        ))
        await self._write_config(environment)

    async def _write_config(self, environment: BaseEnvironment) -> None:
        """Point the shipped profile at this run's endpoint, model and budget."""
        options = self.options
        model = self._served_model()
        edits = {
            "provider.vllm.base_url": options.base_url,
            "model_base_url": options.base_url,
            "provider.vllm.model": model,
            "model_name": model,
            "project_root": options.workdir,
            "shell_cwd": options.workdir,
        }
        # Let the loop wrap up on its own before the watchdog kills it, and
        # size tool timeouts and thinking to this task's time.
        edits.update(budget_edits(options.budget_sec))
        await self.exec_as_root(environment, command=config_edit_command(
            f"{BENCH_REMOTE}/config.txt", edits))

    def populate_context_post_run(self, context: AgentContext) -> None:
        pass

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment,
                  context: AgentContext) -> None:
        options = self.options
        instruction_path = "/installed-agent/instruction.md"
        local = self.logs_dir / "instruction.md"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(instruction, encoding="utf-8")
        await environment.upload_file(local, instruction_path)

        env = {
            **self.model_connection.env,
            "AGENT8088_CONFIG": f"{BENCH_REMOTE}/config.txt",
            "AGENT8088_HOME": "/installed-agent/home",
            "AGENT8088_DISPOSABLE_CONTAINER": "1",
            "AGENT8088_UNATTENDED": "1",
            "AGENT8088_PERMISSION": "full-auto",
            "AGENT8088_WORKDIR": options.workdir,
        }
        env.setdefault("OPENAI_API_KEY", "EMPTY")
        await self.exec_as_agent(environment, env=env, command=(
            f"{BENCH_REMOTE}/container/run-agent.sh {instruction_path} {int(options.budget_sec)}"
        ))
