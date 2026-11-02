# 前端集成说明

- 入口：`index.html`；FastAPI `GET /` 返回入口，`/static/` 同源托管本目录。原生 ES modules，无 npm 或编译步骤。
- `api/index.js` 导出 `api = liveClient`，所有页面经这一入口访问真实同源后端；无单独前端服务器或代理。
- 脚手架模拟客户端、URL 状态切换器、示意线索与加载占位均已移除；不提供模拟回答或模拟图表回退。
- `api/types.js` 是 JSDoc `ApiClient` 契约，后端 `contracts.py` 是规范来源。`api/errors.js` 只构造同源受控 ID 路径，不接收 URL、文件名或路径。
- 计划 8 条路由：`GET /`（入口）、`GET /static/{asset_id}`（本地资源）、`GET /api/health`、`POST /api/sessions`、`DELETE /api/sessions/{session_id}`、`POST /api/chat`、`GET /api/documents/{source_id}`、`GET /api/sessions/{session_id}/charts/{chart_id}`。
- `health`：`status/model_configured/model_verified/data_sources_ready/reference_date`；`session`：`session_id/reference_date/data_labels`。配置、真实探测和资料状态相互独立。
- POST chat JSON：`{session_id,message}`；使用 `fetch` + `AbortController`，不自动重放。服务器断连时须停止模型任务。客户端每个界面只允许一个运行，停止后等读取清理完成才解锁。
- SSE：`{run_id,seq,type,time,payload}`，seq 单调递增，同一 run_id，以 run_started 开始，run_completed 或 run_error 结束。支持 LF/CRLF、多行 data、跨 UTF-8 块，必须有完整终止帧；断流不是成功。
- 公共事件：run_started/text_delta/tool_started/tool_completed/tool_error/retry/citation/chart/run_completed/run_error。text_delta 接受 `text` 或 `delta`；工具按 tool_call_id 关联，仅呈现 summary/result_summary/message/error.message，不显示原始结果或思维链。
- citation：`citation_id,source_id,page,section,excerpt,title`；page 是正整数或 null，section 字符串或 null；按实际 PDF 页序号构造 #page，印刷页码应在 section/title 中注明。chart：`chart_id,title`。两者同时兼容直接 payload 和 payload.result；不接受事件中的任意 URL。
- HTTP 错误 `{error:{code,message,details}}`；run_error.payload 可为该 error envelope 或直接 code/message。模型未配置必须返回 MODEL_NOT_CONFIGURED，不可生成演示回答。
- 默认无回复/图表；资料状态和 UTC 参考日期来自真实 health/session 响应。调查问题限制 1–8000 字，与后端一致。
- 使用 textContent / Text 节点；不解析 HTML/Markdown，不使用浏览器 storage/cookies。主题跟随系统，可手动切换但不持久化。所有外部内容必须由后端脱敏；前端白名单摘要不是后端保密边界。
- 验证：`node asset_assistant/scripts/check_frontend.cjs`（从仓库根执行）；无服务器、无浏览器、无模型调用。真实网络、取消传播、PDF 来源所有权、图表、数据核验、键盘与屏幕阅读器还需集成验收。

## 本地第三方资源

- Pico.css **2.1.1**：https://github.com/picocss/pico/tree/v2.1.1 ，原始发行 CSS 本地保存，许可在 `vendor/PICO-LICENSE.md`。
- Lucide **0.468.0**：https://github.com/lucide-icons/lucide/tree/0.468.0 ，本地 SVG 子集，许可在 `vendor/LUCIDE-LICENSE`。运行时无 CDN/远程字体/远程图标。