# Reels 分析工作台（Facebook Reels Studio）

一键下载、识别文案、分析数据的 Facebook Reels 本地工作台。**粘贴主页地址 → 批量下载全部视频 → Whisper 识别口播文案 → 抓取观看/点赞/评论/发布时间 → 表格排序筛选 → 导出 Excel。**

> ⚠️ 本工具仅用于分析公开内容。抓取数据可能违反 Facebook 服务条款，账号/风险请自行评估；请勿将本工具用于侵权内容下载或账号滥用。

## ✨ 功能

- **主页全量批量分析**：粘贴 Facebook 公共主页地址，自动枚举主页全部视频/Reels（配合登录态可全量抓取），逐条下载 + 转写 + 抓数据
- **口播文案识别**：内置 faster-whisper（tiny→large-v3 可选），把视频里说的每句话转成文字，方便复刻爆款话术
- **数据抓取**：标题/文案、发布时间、时长、观看数、点赞数、评论数（尽力抓取评论内容）
- **一键登录 Facebook**：点击「登录 Facebook」弹出浏览器窗口，登录后 cookies 自动写入服务端（不再需要手动导出 Cookie-Editor 文件）
- **表格排序筛选**：点击表头按观看/点赞/评论/时长/时间排序，支持搜索、语言/状态筛选，类似 Excel 体验
- **Excel 导出**：视频明细 + 评论明细 + 爆款分析（互动率 TOP / 观看 TOP / 月度分布 / 时长分布 / 标签词频）+ 运营指导，一键导出 .xlsx
- **视频打包下载**：全部视频 zip 打包下载

## 🚀 快速开始（macOS / Linux / Windows-WSL）

```bash
# 1. 克隆仓库
git clone <你的仓库地址> && cd reels-studio

# 2. 一键安装（创建虚拟环境 + 安装依赖 + 浏览器内核）
bash install.sh

# 3. 启动
bash start.sh
# 或 macOS 双击「启动服务.command」

# 4. 浏览器打开
#    http://127.0.0.1:8756
```

### 手动安装

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium   # 「登录 Facebook」功能需要
./.venv/bin/python app.py
```

## 📖 使用说明

1. 打开网页后，先点 **「🔑 登录 Facebook」**，在弹出的浏览器窗口登录你的 Facebook 账号（用于抓取登录态，避免公开页面被限流）
2. **方式A（全量）**：粘贴主页地址（如 `https://www.facebook.com/profile.php?id=xxxxx`），点「开始批量分析」，自动枚举该主页全部视频并逐条分析
3. **方式B（单条/多条）**：在文本框每行粘贴一条 Reels/视频链接，跳过枚举直接处理
4. 分析完成后：
   - 表格点表头排序（观看/点赞/评论…），搜索框过滤
   - 点「导出 Excel」拿到完整分析报表（含爆款分析 Sheet）
   - 点「打包下载全部视频」下载所有 mp4

## 🗂️ 项目结构

```
reels-studio/
├── app.py               # Flask 后端（抓取/下载/转写/分析/导出）
├── index.html           # 前端页面（登录、批量、排序筛选表格）
├── login_worker.py      # 登录助手（Playwright 弹窗登录 → 自动导出 cookies）
├── requirements.txt     # Python 依赖
├── install.sh           # 一键安装脚本
├── start.sh             # 启动脚本
├── Dockerfile           # 容器化部署（可选）
└── downloads/           # 下载的视频（git 忽略）
```

## 🐳 Docker 部署（公网/服务器）

```bash
docker build -t reels-studio .
docker run -d -p 8756:8756 -v $(pwd)/data:/app/downloads -v $(pwd)/data_jobs:/app/jobs reels-studio
```

部署到公网请务必加访问鉴权，详见 `DEPLOY.md`。

## ❓ FAQ

- **只能抓 9 条？** 公开页服务端只渲染首屏 9 条。用「登录 Facebook」登录后可全量抓取；对个别主页可先用浏览器滚动浏览其视频页，助手会把全量 URL 缓存到 `pages/`，之后每次全量命中。
- **评论抓不全？** Facebook 评论反爬严格，公开抓取仅能取到部分；评论**总数**尽量记录。全量评论建议用 Creator Studio 官方后台。
- **转写为空？** 视频可能无人声（纯音乐/纯画面），或模型较小。选 base/small 模型提升准确率。
- **登录按钮弹不出窗口？** 需在虚拟环境里安装 playwright chromium：`./.venv/bin/python -m playwright install chromium`。

## ⚖️ 免责声明

本工具用于学习与技术交流。请遵守 Facebook 服务条款与当地法律法规，尊重内容版权，勿用于爬取非公开数据、批量抓取侵权内容或任何商业滥用。使用本工具产生的任何风险由使用者自行承担。
