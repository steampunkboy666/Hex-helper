# Hex Helper 反馈接收服务

用户在客户端确认截图、描述和诊断后，客户端会通过 HTTPS 把三项打包提交到 Worker。Worker 返回反馈编号，并将 ZIP 存入账户内的 Workers KV，30 天后自动过期。反馈内容可通过 Cloudflare 账号取回。

## 部署

使用 Cloudflare Workers Free 和内置 `workers.dev` 地址。确保终端可运行 Node.js 和 `npx`，然后从仓库根目录运行以下命令。

```powershell
Set-Location feedback-worker
npx wrangler login
npx wrangler kv namespace create hex-helper-feedback
Copy-Item wrangler.example.toml wrangler.toml
```

在 `wrangler.toml` 填入上一步返回的命名空间 ID。首次使用时，在 Cloudflare 控制台设置账户的 `workers.dev` 子域名。然后部署 Worker。

```powershell
npx wrangler deploy
```

将部署输出的 HTTPS 地址加上 `/health`，用浏览器确认响应包含 `hex-helper-feedback`。将同一地址加上 `/v1/feedback`，填入测试版同目录的 `data/settings.json` 中的 `feedback_url`，重启程序后用合成截图和描述试传。发布时可把验证过的地址填入 `config.py` 的 `DEFAULTS` 后再打包。该地址可以公开，Cloudflare 凭据只供维护者使用。

上传最多 1 MiB，Worker 从单个 IP 在同一 Cloudflare 位置每分钟接收最多 5 次请求。上传失败时，用户可重试或另存 ZIP。上线前从大陆和台湾各跑一次提交，确认确实能上传并取回合成包。

## 查看反馈

在 Cloudflare 控制台的 Workers KV 里查看 `hex-helper-feedback` 命名空间。键名为 `feedback/反馈编号`，元数据包含收到时间和包大小。可在 `feedback-worker` 目录使用以下命令列出反馈并保存指定 ZIP。请将命令中的 `RECEIPT` 替换为反馈编号。

```powershell
npx wrangler kv key list --binding FEEDBACK --remote --prefix feedback/
cmd /d /c "npx wrangler kv key get feedback/RECEIPT --binding FEEDBACK --remote > feedback.zip"
```

参考 [Worker 定价与免费额度](https://developers.cloudflare.com/workers/platform/pricing/) 和 [Wrangler KV 命令](https://developers.cloudflare.com/kv/reference/kv-commands/)。
