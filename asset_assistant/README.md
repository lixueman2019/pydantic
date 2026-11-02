# Asset Performance Assistant — 本地后端

单 CPython 3.12 进程、FastAPI/Uvicorn、固定绑定 127.0.0.1:8000；入口为 Python 模块
asset_assistant 或安装后的 asset-assistant。没有 Functions、容器、数据库、应用登录或部署脚本。
已使用 CPython 3.12.10 安装并执行离线测试；实际解析版本记录于 constraints-win-py312.txt。
当前隔离解释器位于用户目录 .venvs/asset-assistant（工作区路径过长导致目录内 venv 安装失败）。
安装时可用 pip 的 -c constraints-win-py312.txt 固定已验证版本；其他 OS 需重新验证。

## 本地启动与验证

- 在工作区根目录使用该虚拟环境的 Python，运行 `-m asset_assistant`；打开 http://127.0.0.1:8000。
- 离线测试参数：`-m pytest -c asset_assistant/pyproject.toml asset_assistant/tests -q`。
- 素材审计入口：asset_assistant/scripts/audit_sources.py；只生成本地审计产物，不上传 PDF。
- 前端 static/api/index.js 已连接 liveClient，同一 Python 进程同源托管页面和 API；模拟客户端与预览状态切换器已移除。

## 配置与身份

配置持久化：复制 .env.example 为 asset_assistant/.env（已被 git 忽略），修改后重启进程。
仅自动加载这个固定文件，不搜索当前目录或父目录；进程环境优先，包括显式空值。
显式传给 Settings.from_env 的映射完全隔离于磁盘和进程环境。不会展开配置中的变量引用。
支持资源 HTTPS 根地址，或门户提供的完整 /openai/responses?api-version=... URI。
仅接受单资源标签的 openai.azure.com / cognitiveservices.azure.com 主机；拒绝用户信息、
其他路径、片段、重复或额外查询参数。完整 URI 自动选择 OpenAIResponsesModel，并提取原有版本；
显式版本与 URI 版本冲突时拒绝配置。API version 必须由用户确认，不提供猜测默认值。
资源根地址默认保留 Chat Completions 兼容；也可设 AZURE_OPENAI_API_MODE=responses，
并明确提供 AZURE_OPENAI_API_VERSION。Responses 发往 dated /openai/responses，
deployment 放在请求体 model 中；Chat Completions 保留部署路径。
AZURE_OPENAI_DEPLOYMENT 为部署路由名称，AZURE_OPENAI_MODEL 独立选择底层模型能力配置。
示例使用用户确认的 GPT gpt-5.4-pro 配置，不使用 Claude；配置有效不代表部署、版本或权限已验证。
AzureCliCredential 仅消费用户在应用外自行建立的 Azure CLI 登录身份，不主动登录。
应用拒绝 API key 和静态 token 配置；不要把秘密输入聊天、浏览器或版本库。
缺模型配置仍能打开 UI、创建会话和检查 health；chat 返回 503 MODEL_NOT_CONFIGURED。
health 不请求模型；只有真实模型响应完成后 model_verified 才为 true。

## API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | / | 同源 UI |
| GET | /static/{asset_id} | 显式白名单静态资源，可含嵌套资源 ID |
| GET | /api/health | 本地健康，不登录或调用模型 |
| POST | /api/sessions | 服务端随机会话 ID，201 |
| DELETE | /api/sessions/{session_id} | 清空并取消运行，204 |
| POST | /api/chat | session_id/message；SSE |
| GET | /api/documents/{source_id} | aeris_manual / northgate_standards 两份 PDF |
| GET | /api/sessions/{session_id}/charts/{chart_id} | 会话所有权隔离的 PNG |

错误统一为 error.code/message/details；不返回原始异常。SSE envelope 包含
run_id/seq/type/time/payload。文本增量 provisional=true；只有引用属于本会话真实检索结果
才允许 run_completed，否则 run_error。thinking/reasoning 和原始工具参数不进入公共事件。
每会话一个运行，64 会话、30 分钟空闲 TTL、12轮/64K字符历史、128引用、8张图表，每张2MB。
默认最多20工具、600秒整轮预算、有限 token、每工具最多3次错误修正。OpenAI 客户端仅对可恢复
传输/限流/暂时服务故障默认做至多1次 SDK 重试，尊重服务重试提示；不自动重放整轮聊天。
取消会丢弃计算线程返回值；Python 线程无法强制终止，计算模块须保持有界只读。

### 长推理等待与超时

| 可选环境变量 | 默认值 | 范围 / 含义 |
|---|---:|---|
| ASSET_ASSISTANT_RUN_TIMEOUT_SECONDS | 600 | 1–1800 秒；包含身份获取、所有模型请求、重试退避、工具及流处理 |
| ASSET_ASSISTANT_REQUEST_TIMEOUT_SECONDS | 300 | 1–600 秒；SDK HTTP 各阶段/读流无数据等待，实际取不大于整轮预算；连接等待最多10秒 |
| ASSET_ASSISTANT_REQUEST_MAX_RETRIES | 1 | 0–2 次额外 SDK 请求重试；0 禁止 SDK 自动重试 |

这些非秘密配置可加入固定本地配置文件，修改后重启；未配置即采用新默认值。
空值、非法整数或越界值回退默认，不取消全局上限；进程环境仍优先。
HTTP 超时不是总推理时长保证：重试、退避、工具和多轮模型往返共享整轮预算，
不能简单将单次等待乘以重试数来预测总时长。SDK 重试可能再次提交请求并产生费用；
已经开始的流不保证可恢复，应用不会自动重放聊天。取消/断连仍会取消异步运行。

界面按本地单调时钟显示已等待秒数和最近公共事件阶段；不是模型思考、百分比或心跳证据。
成功、错误、超时、停止和清空会话均清理计时。整轮超时返回 RUN_TIMEOUT，包含实际等待、
整轮预算及工具开始/引用事件计数等显式安全元数据，不含原始异常、请求、端点或凭据。
引用或工具完成只能证明已到达工具阶段，不代表最终回答成功，也不令 model_verified 为真。

排查依据：原实现整轮硬上限120秒、SDK等待30秒且额外重试2次；这是代码事实，
并非此次真实请求各阶段耗时测量。长推理、服务延迟、网络停顿、重试等待或 token 预算
均未在本次离线修复中确认；不能将 RUN_TIMEOUT 直接归因于其中任何一项。
保持现有模型/部署/API版本和 reasoning 设置不变；输出上限仍4096、总token上限仍64000，
Responses 的 max_tokens 由 SDK 映射为 max_output_tokens，不保证推理后仍有足够答案空间。
建议先缩小问题手动重试，必要时在边界内调整预算；真正推理可用性仍需授权后的真实请求验收。

## 并行模块接口与待验证

- knowledge.KnowledgeService(root)：ready、documents、search_documents(query, limit)。
  搜索返回 Citation 字段字典列表，或包含 citations/results 的字典；字段见 contracts.py。
- tools.service.ToolService(root)：list_assets、get_asset_status、query_telemetry、list_work_orders、
  get_work_order、analyze_correlation。同步方法返回可 JSON 序列化字典；错误为 error.code。
- 分析可返回 charts 列表，元素为 PNG bytes 或含 png bytes 的字典；网关移除二进制后发往模型。
- 模块缺失或格式不匹配明确阻塞，不用假的数据。reference_date、真实 SDK 离线事件桥接、引用和会话图表接口已有集成回归测试。
- 前端使用真实同源 API，健康与会话请求不触发推理；模型缺配置时真实聊天返回明确的 503，不降级生成演示答案。
- 已执行离线 Python/API/工具/统计测试、依赖一致性和前端静态/Node 契约检查。
  尚未完成真实 Entra、Azure OpenAI 文本/工具流或业务端到端验收。没有数据库、迁移或 seed data。
- 两份 PDF 均是训练素材，不可用于真实风机决策；遥测是 original synthetic，工单是 replacement synthetic。
- 温度手册额外要求连续有效负载运行30分钟；日采样不能证明该条件，分析只能是探索性，不能作为温度阈值有效性证明。