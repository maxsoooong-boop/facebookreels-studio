# 公网部署指南（把工作台变成"打开网页就能用"）

> ⚠️ **重要风险提示**：本工具抓取 Facebook 需要登录态 cookies。若把服务暴露到公网且不加保护，任何访问者都会动用你账号的 cookies 去抓 Facebook，极易触发 Facebook 风控、导致**账号被封**。公网部署**必须**：① 加访问密码/鉴权；② 仅限你自己或可信小团队使用。

## 方案 A：自己的云服务器（推荐，稳定）

1. 准备一台云服务器（阿里云/腾讯云/华为云轻量服务器即可，2 核 4G 起步，系统 Ubuntu）
2. 安装 Docker，然后在服务器上：
   ```bash
   git clone <你的仓库> && cd reels-studio
   docker build -t reels-studio .
   docker run -d --name reels \
     -p 127.0.0.1:8756:8756 \
     -v $PWD/data:/app/downloads -v $PWD/data_jobs:/app/jobs \
     reels-studio
   ```
3. 用 Nginx 反代 + 密码保护（Basic Auth）暴露公网：
   ```nginx
   server {
     listen 80;
     server_name your.domain.com;
     auth_basic "Restricted";
     auth_basic_user_file /etc/nginx/.htpasswd;
     location / { proxy_pass http://127.0.0.1:8756; }
   }
   ```
4. 登录功能说明：服务器上的「登录 Facebook」会弹出服务器桌面窗口——云服务器无桌面，所以**服务器部署请改用"上传 cookies"**（页面已保留高级入口），在本地浏览器导出 cookies 文件上传即可。

## 方案 B：家里电脑 + 内网穿透（零服务器成本）

用 Cloudflare Tunnel 或 frp 把本地 8756 映射成公网地址：

```bash
# Cloudflare Tunnel 示例
brew install cloudflared
cloudflared tunnel --url http://127.0.0.1:8756
# 输出一个 https://xxx.trycloudflare.com 临时公网地址
```

同样必须加访问密码（可在服务内加，见下节）。

## 方案 C：给服务加访问密码（简易鉴权）

在 `app.py` 顶部加两行，然后所有请求校验：

```python
ACCESS_PASSWORD = "你的密码"   # 访问密码，留空则关闭

@app.before_request
def _guard():
    if ACCESS_PASSWORD and request.path not in ("/", "/login", "/static/..."):
        token = request.cookies.get("access_token") or request.headers.get("X-Access-Token")
        if token != ACCESS_PASSWORD:
            from flask import redirect, make_response
            if request.path.startswith("/api"):
                return jsonify({"error": "未授权"}), 401
            r = make_response(redirect("/login"))
            return r
```

并提供一个简单登录页（/login）输入密码后种 cookie。完整实现可让助手帮你加。

## 各电脑使用对比

| 方式 | 成本 | 稳定性 | 风险 | 适合 |
|---|---|---|---|---|
| **GitHub 克隆 + 本机运行** | 0 | 高 | 低（各用各的账号） | ✅ 最推荐 |
| 云服务器公网 | 几十元/月 | 高 | 中（需鉴权 + cookies 上传） | 多人共用、想手机访问 |
| 内网穿透 | 0 | 中 | 中（依赖家里电脑在线） | 临时自己用 |
| 网页 SaaS（别人免登录用） | 高 | 中 | 高（封号 + 合规） | ❌ 不建议 |
