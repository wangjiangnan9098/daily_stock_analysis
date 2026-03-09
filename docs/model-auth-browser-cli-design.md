# Browser CLI 认证改造设计（最小影响方案）

## 1. 目标与约束

## 1.1 目标

1. 将模型认证从“仅 API Key”扩展为“可选 browser CLI 认证”。
2. 改造后默认行为不变（仍默认 API Key），确保零破坏升级。
3. 改动独立、可回滚，尽量不影响数据获取、通知、回测等模块。

## 1.2 非目标

1. 不重构整个分析 Pipeline。
2. 不替换现有供应商 SDK 逻辑（作为保底路径保留）。
3. 不引入与业务强耦合的第三方网关服务。

## 2. 设计原则

1. **开关化**：通过配置切换认证模式，默认 `api_key`。  
2. **适配器隔离**：新增独立模块处理 browser CLI 交互，不在业务层散落 `subprocess` 调用。  
3. **失败可回退**：Browser CLI 失败时行为可配置（默认不回退到 API Key，避免隐性 token 消耗）。  
4. **最小改动面**：只改“模型初始化和调用边界”以及“配置元数据”。

## 3. 总体方案

## 3.1 新增认证模式

新增配置项（建议）：

1. `LLM_AUTH_MODE=api_key|browser_cli`（默认 `api_key`）
2. `LLM_BROWSER_CLI_COMMAND`（默认 `openai` 或你的实际 CLI 命令）
3. `LLM_BROWSER_CLI_TIMEOUT_SEC`（默认 `120`）
4. `LLM_BROWSER_CLI_MODEL`（为空时复用 `OPENAI_MODEL`）
5. `LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK=false|true`（默认 `false`）

说明：`browser_cli` 模式下，认证由 CLI 的浏览器登录会话负责，项目本身不再要求 `OPENAI_API_KEY`（可留空）。

## 3.2 新增独立适配器

新增文件：`src/llm/browser_cli_adapter.py`

建议接口：

```python
class BrowserCliAdapter:
    def is_available(self) -> bool: ...
    def generate_text(self, prompt: str, system_prompt: str, temperature: float, max_output_tokens: int) -> str: ...
    def generate_vision(self, image_b64: str, mime_type: str, prompt: str, max_output_tokens: int = 1024) -> str: ...
```

该模块职责：

1. 统一封装 CLI 调用、超时、重试、错误归一化。
2. 解析 CLI 输出为纯文本响应。
3. 记录调用日志（不记录敏感 token）。

## 3.3 与现有代码的集成点（最小改动）

### A. 配置层

修改 `src/config.py`：

1. `Config` 增加上述 5 个字段。
2. `_load_from_env()` 读取新字段。
3. `validate()` 增加 browser_cli 模式必要校验（如命令为空时给 warning）。

### B. 分析器主链路

修改 `src/analyzer.py`：

1. `GeminiAnalyzer.__init__()` 首先判断 `LLM_AUTH_MODE`。  
2. 若 `browser_cli`：
   - 初始化 `BrowserCliAdapter`
   - 设置 `self._use_browser_cli = True`
   - 不强依赖 `GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY`
3. 在 `_call_api_with_retry()` 增加 browser_cli 分支，调用 `adapter.generate_text(...)`。
4. 保留现有 Gemini/Anthropic/OpenAI 路径，避免影响默认行为。

### C. Vision 图片识别链路

修改 `src/services/image_stock_extractor.py`：

1. `_select_vision_provider()` 增加 `browser_cli` 选项（优先级可配置，默认最高）。
2. 新增 `_call_browser_cli(...)`，调用 `adapter.generate_vision(...)`。
3. 失败时按现有顺序降级（受 `LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK` 控制）。

### D. Web 配置页（可选但建议）

修改：

1. `src/core/config_registry.py` 增加新配置项元数据。
2. `apps/dsa-web/src/utils/systemConfigI18n.ts` 增加中文标题与说明。

这样可以从 Settings 页面直接切换认证模式，不需要手动改 `.env`。

## 4. 代码改动清单（拟修改）

1. `src/config.py`：新增配置字段与读取逻辑。  
2. `src/analyzer.py`：新增 browser_cli 初始化与调用分支。  
3. `src/services/image_stock_extractor.py`：新增 Vision browser_cli 调用分支。  
4. `src/core/config_registry.py`：新增配置元数据（UI 可编辑）。  
5. `apps/dsa-web/src/utils/systemConfigI18n.ts`：补充字段文案。  
6. `docs/model-auth-current-flow.md`：现状说明（已新增）。  
7. `README.md` / `.env.example`：补充新配置说明。  
8. `tests/`：新增 browser_cli 模式相关测试。

新增文件：

1. `src/llm/browser_cli_adapter.py`（核心隔离模块）

## 5. 兼容性与回滚

## 5.1 兼容性

1. 默认 `LLM_AUTH_MODE=api_key`，老用户无感。  
2. 不删除任何现有 API Key 字段。  
3. 保留现有回退策略（Gemini > Anthropic > OpenAI）作为非 browser_cli 模式行为。

## 5.2 回滚

1. 将 `LLM_AUTH_MODE` 改回 `api_key` 即可回滚。  
2. 若 browser_cli 故障且需要立即恢复，可开启 `LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK=true` 作为临时兜底。

## 6. 测试设计

建议新增测试：

1. `browser_cli` 模式下无 API Key 仍可初始化分析器。  
2. `browser_cli` 文本分析成功返回结果。  
3. `browser_cli` 超时/命令失败时错误可观测。  
4. Vision 提取走 browser_cli 分支。  
5. `LLM_BROWSER_CLI_ALLOW_APIKEY_FALLBACK=false` 时不应自动消耗 API Key。  
6. `api_key` 模式回归测试全部通过（确保无行为回退）。

## 7. 风险与控制

1. CLI 输出格式变更风险：在 `BrowserCliAdapter` 内做单点解析与错误兜底。  
2. CLI 登录态过期：返回明确错误提示，引导重新 browser login。  
3. 性能波动：增加超时与有限重试，日志打点便于监控。  
4. 并发调用稳定性：适配器内控制并发或复用进程，避免高并发频繁拉起 CLI。

## 8. 实施顺序（建议）

1. 先落 `src/llm/browser_cli_adapter.py` + `src/config.py`（仅加能力，不启用）。  
2. 接入 `src/analyzer.py` 文本链路，完成单测。  
3. 接入 `src/services/image_stock_extractor.py` Vision 链路，完成单测。  
4. 最后补充 Settings/UI 字段、README、`.env.example`。  
5. 以开关灰度启用 `LLM_AUTH_MODE=browser_cli`。

---

该方案满足“独立修改、对项目影响最小”的核心点：  
仅在模型认证边界新增适配层，不改业务流程与数据结构，默认路径保持原样。
