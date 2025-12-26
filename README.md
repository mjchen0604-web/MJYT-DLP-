# MJYT-DLP MCP

一个合并版 MCP：**翻译 + yt-dlp 视频信息抓取**，且**不下载视频文件**。支持远程 SSE/HTTP，带中文控制面板。

## 功能
- MCP（SSE + Streamable HTTP）
- 翻译工具（可接任意 OpenAI Compatible Provider，例如你部署在 Render 的 ChatMock）
- yt-dlp 工具：元数据、格式直链、字幕列表、字幕文本/直链
- 中文控制面板

## 快速启动（本地）

安装：
```
pip install -r requirements.txt
```

启动（PowerShell）：
```
$env:MJYTDLP_ADMIN_PASSWORD = "change-me"
$env:MJYTDLP_SECRET_KEY = "change-me"
python -m mjytdlp
```

启动（bash）：
```
export MJYTDLP_ADMIN_PASSWORD=change-me
export MJYTDLP_SECRET_KEY=change-me
python -m mjytdlp
```

管理面板：
- http://127.0.0.1:8000/admin
- http://127.0.0.1:8000/admin/mcp

MCP 入口：
- SSE: http://127.0.0.1:8000/mcp/sse
- HTTP: http://127.0.0.1:8000/mcp

## Render 部署（Docker）

1) 创建 Render Web Service（Docker）。
2) 挂载持久盘 `/data`（可选，但推荐）。
3) 环境变量：
   - `MJYTDLP_ADMIN_PASSWORD`（必填）
   - `MJYTDLP_SECRET_KEY`（必填）
   - `MJYTDLP_HOME=/data`（推荐）
4) 部署后打开 `/admin` 和 `/admin/mcp` 配置 Provider。

## ASR 转写（whisper-asr-webservice）
1) 先单独部署 ASR 服务（例：`http://你的ASR服务器IP:9000`）。
2) 在 Render 环境变量里设置：
   - `MJYTDLP_ASR_URL=http://你的ASR服务器IP:9000`
   - （可选）`MJYTDLP_ASR_API_KEY`、`MJYTDLP_ASR_AUTH_HEADER`、`MJYTDLP_ASR_AUTH_PREFIX`

## YouTube/多平台验证（cookies）
- 在控制面板 `/admin` 上传 `cookies.txt`（默认，会保存到 `<data_dir>/cookies.txt`）。
- 也可上传命名 cookies（例如 `youtube` / `bilibili` / `douyin`），文件保存到 `<data_dir>/cookies/<name>.txt`。
- yt-dlp 会自动使用默认 cookies；也可在工具参数里传：
  - `options.cookies_name`（优先使用命名 cookies）
  - 或 `options.cookies_path`（指定完整路径）

## 工具列表

翻译：
- `translate`：文本翻译
- `list_providers`：列出已配置 Provider（不含密钥）

yt-dlp（不下载视频文件）：
- `probe`：获取视频元数据
- `formats`：列出格式 + 下载直链（原站直链 + 必要 headers）
- `list_subs`：列出字幕轨道（含下载直链）
- `download_subs`：返回字幕文本，或返回字幕直链
- `version`：yt-dlp 版本

ASR：
- `transcribe`：转写音频为字幕/文本（会先下载音频，再上传到外部 ASR）

## 示例 MCP 配置（SSE）
```
{
  "mcpServers": {
    "mjyt-dlp": {
      "transport": {
        "type": "sse",
        "url": "https://<your-domain>/mcp/sse"
      }
    }
  }
}
```

## 说明
- **不下载视频文件**，只返回元数据/直链/字幕文本。
- 直链可能有有效期，需现取现用。
