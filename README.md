# pydantic
dynamic decision agent - turbine investigator

基于 Pydantic AI 的只读风机调查助手：FastAPI 提供本地页面与 SSE 对话，结合 PDF 知识检索、合成遥测、工单和统计分析工具进行调查。

## 在另一台电脑运行

需要 Python 3.12、Git。以下命令使用 PowerShell，在克隆后的仓库根目录执行。
Windows 建议使用用户目录下的短路径虚拟环境，避免 OneDrive 长路径导致依赖安装失败。

```powershell
git clone https://github.com/lixueman2019/pydantic.git
cd pydantic
py -3.12 -m venv "$HOME/.venvs/asset-assistant"
& "$HOME/.venvs/asset-assistant/Scripts/python.exe" -m pip install -c asset_assistant/constraints-win-py312.txt -e "./asset_assistant[dev]"
Copy-Item asset_assistant/.env.example asset_assistant/.env
# 编辑 asset_assistant/.env，填写自己的模型端点、部署、模型和准确的 API 版本。
& "$HOME/.venvs/asset-assistant/Scripts/python.exe" -m asset_assistant
```

打开 http://127.0.0.1:8000。macOS/Linux 可使用 Python 3.12 创建虚拟环境，并使用其 bin/python；当前依赖约束在 Windows 验证，其他平台需另行验证。

模型调用使用当前电脑在应用外建立的 Azure CLI 登录身份，不使用 API key。
真实聊天还需要模型访问权限及完整配置；未配置时仍可打开界面、检查健康与运行离线测试，但不会生成模拟答案。
GitHub 不同步登录凭据、本地环境变量、虚拟环境、运行中的进程、内存会话或 Copilot 聊天记录。

## 离线验证

```powershell
& "$HOME/.venvs/asset-assistant/Scripts/python.exe" -m pytest -c asset_assistant/pyproject.toml asset_assistant/tests -q
& "$HOME/.venvs/asset-assistant/Scripts/python.exe" -m ruff check asset_assistant
node asset_assistant/scripts/check_frontend.cjs
```

前端检查需要 Node.js；不需要 npm 安装或单独的前端服务器。

## 项目结构

- [Agent 与后端说明](asset_assistant/README.md)
- [Agent 定义](asset_assistant/src/asset_assistant/agent.py)
- [依赖与测试配置](asset_assistant/pyproject.toml)
- [环境变量模板](asset_assistant/.env.example)（本地 .env 不上传）
- [素材来源与限制](asset_assistant/data/provenance.json)

## 数据与使用限制

遥测和工单都是训练用合成数据，两份 PDF 是实验知识素材，不可用于真实风机运维决策。
遥测 C# 连接器仅作为数据源读取，不执行 C#，无需部署 MCP 服务。
上游素材的来源和版权/许可核验状态保留在 provenance 中；本仓库不对第三方素材授予额外许可。
前端第三方许可证随 vendor 目录保留。真实模型和业务端到端验收状态见后端说明。
