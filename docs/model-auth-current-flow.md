# 模型访问权限现状分析（当前实现）

## 1. 结论

当前项目的模型访问权限，核心来自 `.env` 中的 API Key（`GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）。  
这些凭据有两种进入路径：

1. 手动写入 `.env`（本地/Docker/GitHub Actions 环境变量）。
2. 通过 Web 设置页调用后端接口写入 `.env`，再触发运行时配置重载。

模型调用时不走 OAuth/browser 登录；调用链是直接把 API Key 传给各厂商 SDK 客户端。

## 2. 关键调用链

### 2.1 凭据加载链（认证来源）

1. `src/config.py:21` `setup_env()` 读取 `.env`。  
2. `src/config.py:286` `Config._load_from_env()` 从环境变量映射到 `Config`。  
3. `src/config.py:381`~`src/config.py:396` 加载 `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 等模型配置。  
4. `src/config.py:660` `get_config()` 返回全局单例，供全项目调用。

### 2.2 Web 设置写入链（凭据更新）

1. 前端提交配置：
   - `apps/dsa-web/src/hooks/useSystemConfig.ts:270`
   - `apps/dsa-web/src/api/systemConfig.ts:92`
2. 后端接收并更新：
   - `api/v1/endpoints/system_config.py:59`
   - `src/services/system_config_service.py:102`
3. `.env` 原子更新：
   - `src/core/config_manager.py:66`
   - `src/core/config_manager.py:97`
4. 更新后重载配置：
   - `src/services/system_config_service.py:139` `Config.reset_instance()`
   - `src/services/system_config_service.py:140` `setup_env(override=True)`

### 2.3 模型调用链（文本分析）

1. 业务入口（API/异步任务/CLI）进入分析服务：
   - `api/v1/endpoints/analysis.py:178`
   - `src/services/task_queue.py:357`
   - `src/services/analysis_service.py:71`
2. Pipeline 初始化分析器：
   - `src/core/pipeline.py:77` `self.analyzer = GeminiAnalyzer()`
3. 分析器初始化供应商客户端：
   - `src/analyzer.py:520` 入口，优先级 `Gemini > Anthropic > OpenAI`
   - `src/analyzer.py:655` `genai.configure(api_key=...)`
   - `src/analyzer.py:579` `Anthropic(api_key=...)`
   - `src/analyzer.py:626` `OpenAI(api_key=..., base_url=...)`
4. 实际调用：
   - Gemini: `src/analyzer.py:922` `self._model.generate_content(...)`
   - Anthropic: `src/analyzer.py:752` `messages.create(...)`
   - OpenAI: `src/analyzer.py:839` `chat.completions.create(...)`

### 2.4 模型调用链（图片识别 Vision）

1. API 入口：`api/v1/endpoints/stocks.py:40` `/extract-from-image`  
2. 调用提取服务：`api/v1/endpoints/stocks.py:97`  
3. 提取服务供应商选择：
   - `src/services/image_stock_extractor.py:127` `_select_vision_provider()`
   - 优先级：`Gemini -> Anthropic -> OpenAI`
4. SDK 认证与调用：
   - Gemini: `src/services/image_stock_extractor.py:144`
   - Anthropic: `src/services/image_stock_extractor.py:167`
   - OpenAI: `src/services/image_stock_extractor.py:198`

## 3. 相关代码清单（按职责）

| 职责 | 文件 | 关键位置 |
|---|---|---|
| 读取/持有模型凭据 | `src/config.py` | `21`, `286`, `381-396`, `660` |
| `.env` 原子写入 | `src/core/config_manager.py` | `66-96`, `97-137` |
| 配置校验与重载 | `src/services/system_config_service.py` | `102-156` |
| 设置页字段元数据 | `src/core/config_registry.py` | `187-340` |
| 前端设置提交 | `apps/dsa-web/src/api/systemConfig.ts` | `92-123` |
| 前端设置保存流程 | `apps/dsa-web/src/hooks/useSystemConfig.ts` | `244-299` |
| 分析主流程入口 | `src/core/pipeline.py` | `77`, `290` |
| LLM 客户端初始化与重试 | `src/analyzer.py` | `520-560`, `561-642`, `722-1007` |
| Vision 提取链路 | `src/services/image_stock_extractor.py` | `127-301` |
| API 同步分析入口 | `api/v1/endpoints/analysis.py` | `178-234` |
| API 异步分析入口 | `src/services/task_queue.py` | `357-417` |

## 4. 当前机制特征

1. 认证方式是 API Key 静态凭据，不是 browser OAuth 会话。  
2. 凭据落地在 `.env`，支持 Web UI 更新，但本质仍是 Key 模式。  
3. 运行时支持多供应商回退，调用层与认证层耦合在 `GeminiAnalyzer` 与 `image_stock_extractor`。  
4. 若未配置 Key，会返回“AI 不可用”降级结果（`src/analyzer.py:1049`）。  
