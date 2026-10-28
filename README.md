# 网点智能接待与导览助手 · 部署与上线说明

## 一、本机运行（已跑通）

```bash
cd branch-assistant
./run.sh                 # 默认 8000 端口，可用 PORT=8080 ./run.sh 改端口
```

打开 http://127.0.0.1:8000

- 客户模式：直接对话，无需登录。
- 柜员模式：点右上「柜员模式」，演示工号 `T1001` / 口令 `888888`（合规岗 `T1002` / `666666`）。
- 演示数据：进度查询用 `A001` + 手机尾号 `8888`；`B002` + `6666`。

## 二、可选：接入大模型

未配置时全部由规则引擎 + 知识库应答，功能完整、可离线演示。需要自然语言生成时设置环境变量即可（OpenAI 兼容协议）：

```bash
export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://api.deepseek.com/v1   # 或行内网关地址
export LLM_MODEL=deepseek-chat
./run.sh
```

接入后正则在服务端仍会二次审查输出（禁用词、荐品、承诺类表述），大模型不能绕过合规护栏。

## 三、当前可访问地址（生产模式）

已用 gunicorn 生产模式运行（`deploy/serve.sh`，单进程多线程，避免多 worker 会话状态分裂）：

- 本机：http://127.0.0.1:8000
- 同一局域网 / 同一 WiFi 的电脑和手机：http://192.168.86.102:8000
- 公网（launchd 保活，断线自动重连）：见 `data/public_url.txt`，或 `cat ~/xiaohui-assistant/data/public_url.txt`

公网地址每次隧道重连都会变，以 `data/public_url.txt` 里的最新值为准。该隧道仅用于演示，不能承载真实客户信息。

## 四、正式上线三步走

### 第 0 步：阿里云 ECS 实操（从注册到网站可访问）

**① 买服务器**（控制台 → 云服务器 ECS → 创建实例）

| 项 | 选择 | 说明 |
| --- | --- | --- |
| 计费 | 包年包月 | 备案要求服务器有效期 ≥ 3 个月 |
| 地域 | 杭州 / 上海 / 北京（离客户近） | 选中国内地就必须备案 |
| 实例 | 经济型 e，2 核 2G | 这个助手并发不高，够用 |
| 镜像 | Ubuntu 22.04 64 位 | 下面的脚本按 Debian 系写 |
| 系统盘 | 40G ESSD | 默认即可 |
| 带宽 | 固定 3Mbps | 演示够；人多再升 |
| 登录凭证 | 自定义密码或密钥对 | 建议密钥对 |

**② 安全组放行端口**（实例详情 → 安全组 → 配置规则 → 入方向）

- `22` 来源填你的公网 IP（不要填 0.0.0.0/0，避免被爆破）
- `80` 来源 `0.0.0.0/0`
- `443` 来源 `0.0.0.0/0`
- 8000 端口**不要**放行，它只监听 127.0.0.1

**③ 一条命令部署**（在你本机执行，会自动装 Docker、上传代码、起容器）

```bash
cd "/Volumes/HP P900/数字孪生/branch-assistant"
chmod +x deploy/deploy-to-server.sh
./deploy/deploy-to-server.sh root@你的服务器公网IP
```

部署完先在服务器上确认：

```bash
curl http://127.0.0.1:8000/api/health
```

**④ 备案未通过时的临时访问**

阿里云内地节点会拦截**未备案域名**的 80/443 访问。备案期间改用高位端口（8080）访问，不受拦截：

```bash
# 先在安全组临时放行 8080
ssh root@你的服务器IP
cd /opt/xiaohui-assistant
HTTP_PORT=8080 NGINX_CONF=./deploy/nginx.http.conf docker compose up -d
# 浏览器访问 http://你的服务器公网IP:8080
```

助手容器的 8000 端口只监听服务器本机（`127.0.0.1:8000`），公网访问一律经过 Nginx，不要在安全组放行 8000。

备案通过后改回标准配置：`docker compose up -d --force-recreate nginx`。

**⑤ 域名与备案**（控制台 → 域名 / 备案）

1. 在阿里云万网注册域名，完成实名认证（个人或企业主体需与备案主体一致）。
2. 提交 ICP 备案：需要该 ECS 的**备案服务码**（控制台 → 备案 → 备案服务码），服务器购买时长 ≥ 3 个月。
3. 备案通过后，域名解析添加 A 记录，指向 ECS 公网 IP。
4. 备案是唯一不能当天完成的环节，审核期间网站只能用 IP 访问。

**⑥ 上 HTTPS**

1. 控制台 → 数字证书管理服务 → 免费证书 → 申请（绑定你的域名）→ 签发后下载 **Nginx 格式**。
2. 把证书文件传到服务器：

```bash
scp fullchain.pem privkey.pem root@你的服务器IP:/opt/xiaohui-assistant/deploy/certs/
```

3. 把 `deploy/nginx.conf` 里的 `YOUR_DOMAIN` 改成你的域名（两处），然后切回 HTTPS 配置：

```bash
ssh root@你的服务器IP "cd /opt/xiaohui-assistant && sed -i 's/YOUR_DOMAIN/你的域名/g' deploy/nginx.conf && docker compose up -d --force-recreate nginx"
```

4. 访问 `https://你的域名` 验证证书有效、HTTP 自动跳 HTTPS。

**⑦ 上线验收**

```bash
curl -I https://你的域名                       # 应返回 200 + HSTS 头
curl -s https://你的域名/api/health             # 应返回 ok
docker compose ps                               # 两个容器都 healthy/running
docker compose logs --tail=50 assistant          # 无异常堆栈
```

### 第 1 步：一台服务器（云服务器或行内虚拟机）

最省事的方式是 Docker，镜像里已包含 gunicorn、健康检查和数据卷：

```bash
# 服务器上（需先装 Docker）
scp -r branch-assistant user@server:/opt/
cd /opt/branch-assistant
cp .env.example .env        # 需要大模型时再填 LLM_API_KEY
docker compose up -d        # 助手 + Nginx
curl http://127.0.0.1:8000/api/health
```

公网入口由 `deploy/nginx.conf` 负责（80 跳 443、安全响应头、gzip、静态缓存），把 `YOUR_DOMAIN` 换成实际域名、证书放到 `deploy/certs/` 即可。没有域名时先把 compose 里 nginx 的 80 端口放开，用 `http://服务器IP` 访问。

不想用 Docker 时：`pip install -r requirements.txt` 后 `PORT=8000 ./deploy/serve.sh`，前面挂行内 Nginx 即可。

### 第 2 步：域名与 HTTPS

- 国内服务器：域名需先完成 ICP 备案，否则 80/443 会被拦截。
- 证书：免费证书（Let's Encrypt / 云厂商免费证书）放到 `deploy/certs/fullchain.pem`、`privkey.pem`，Nginx 配置已写好 TLS 1.2/1.3 与 HSTS。
- 不想开放公网时，走行内域名 + 内网 DNS 更符合银行数据不出内网的要求。

### 第 3 步：银行场景的必做项（不做完不算正式上线）

- 柜员鉴权换成行内统一身份认证 / 工作台免登，删掉 `data/tellers.json` 里的演示口令。
- 把取号、预约、进度、工单、产品参数接到真实系统（接口位置见第五节表格）。
- 知识库换成行内正式文件，保留文号、生效日期、更新时间、状态四字段。
- 关闭演示数据：设置 `DEMO_MODE=0`，清空 `data/audit.jsonl` 中的测试留痕。
- 合规、消保、信息科技三方验收，留痕保留期限按监管要求配置。

## 五、本机保活自启（已配置）

本机已注册两个 launchd 服务，开机自启、进程崩溃自动拉起、隧道断线自动重连：

| 服务 | Label | 日志 |
| --- | --- | --- |
| 助手服务（gunicorn） | `com.xiaohui.assistant` | `/tmp/xiaohui.out`、`/tmp/xiaohui.err` |
| 公网隧道 | `com.xiaohui.tunnel` | `/tmp/xiaohui-tunnel.out`、`/tmp/xiaohui-tunnel.err` |

**运行副本在 `~/xiaohui-assistant`**，不是外接硬盘。原因：macOS 禁止 launchd 后台进程访问 `/Volumes` 外接卷（会报 `Operation not permitted`）。改完外接硬盘上的源码后，执行：

```bash
cd "/Volumes/HP P900/数字孪生/branch-assistant" && ./deploy/sync.sh
```

同步并重启服务。常用运维命令：

```bash
launchctl list | grep xiaohui                        # 查看状态（第二列是 PID，0 表示未运行）
tail -f /tmp/xiaohui-tunnel.out                      # 看隧道日志和当前公网地址
cat ~/xiaohui-assistant/data/public_url.txt          # 当前公网地址
launchctl unload ~/Library/LaunchAgents/com.xiaohui.tunnel.plist   # 停止隧道
```

**保活的前提（无法绕过）**：Mac 开机、已登录、不休眠、联网。关机或睡眠后服务停止，公网地址失效。若需要几个小时内不休眠：

```bash
caffeinate -dimsu &     # 按住不休眠，直到进程被杀或 reboot
```

以上仅适用于演示。真正 7x24 对外提供服务，必须用服务器（第四节）。


## 六、接口替换点（上线前必须做）

| 能力 | 当前实现 | 上线替换为 |
| --- | --- | --- |
| 取号 | `agent/flows.py: take_number`（内存计数器） | 排队叫号系统 API |
| 预约 | `agent/flows.py: make_appointment`（内存） | 预约/CRM 系统 API |
| 进度查询 | `agent/flows.py: progress`（种子数据） | 排队叫号 + 业务系统实时状态 |
| 投诉工单 | `agent/flows.py: complaint_ticket`（生成编号） | 工单/消保系统 API |
| 产品参数 | `data/product_params.json` | 产品中心参数接口 |
| 柜员鉴权 | `data/tellers.json` + OTP | 行内统一身份认证 / 工作台免登 |
| 审计留痕 | `data/audit.jsonl` | 日志平台或数据库（保留期限按监管要求） |

同时把 `data/` 下的示例知识库替换为行内真实文件，并保持每篇文档的 `authority / effective_date / updated_at / status` 四个字段完整——这是柜员侧“引用可追溯、废止文件不复用”的基础。

## 七、上线前合规检查清单

- [ ] 所有对外话术经合规/消保审核，禁用词库由合规部维护（`data/risk_templates.json`）
- [ ] 理财类回答必带风险提示；荐品、涨跌预测、收益承诺零命中（可用 `coze/06_工作流与插件设计.json` 的 acceptance 用例回归）
- [ ] 贷款类回答无额度/利率/放款时间承诺
- [ ] 客户侧无法触达内部知识库（`visibility=internal` 仅柜员鉴权可见，已实现）
- [ ] 敏感信息（卡号、密码、CVV、验证码）不入库、不回显，输入侧已脱敏并提示
- [ ] 投诉、挂失、盗刷等升级路径有真人兜底（值班经理响应机制）
- [ ] 留痕字段齐全可追溯：会话 ID、身份、意图、置信度、路由、是否升级、是否拦截、工具调用
- [ ] 大字号/无障碍模式可用；老年客户可申请大堂经理陪同
- [ ] 明确告知客户“本服务为智能助手，可随时转人工”

## 九、零成本上线（不买任何东西）

三条路的取舍：

| 方案 | 固定网址 | 免备案 | 代价 |
| --- | --- | --- | --- |
| 本机 + 隧道（已配置） | 否，每次重连变化 | 是 | Mac 必须开机联网 |
| **Render 免费实例** | 是，`xxx.onrender.com` | 是 | 15 分钟无访问休眠，首次打开慢 30-60 秒；免费磁盘不持久 |
| Hugging Face Spaces | 是，`xxx.hf.space` | 是 | 境外访问国内不稳定；空间默认公开 |

### Render 免费部署（推荐）

仓库里已放好 `render.yaml`，Docker 方式，无需改代码：

1. 把 `branch-assistant` 目录推到 GitHub（公开或私有都行）
2. 注册 https://render.com （邮箱即可，免费实例不需要付款方式）
3. New → Web Service → 选这个仓库 → 自动识别 Dockerfile
4. Plan 选 **Free**，Region 选 Singapore，点 Create
5. 等 3-5 分钟构建完，得到 `https://xiaohui-assistant.onrender.com`

注意事项：
- 免费实例 **15 分钟无人访问会休眠**，下次打开需等 30-60 秒冷启动，这是免费层的机制，改不了
- 免费实例**没有持久磁盘**，`data/audit.jsonl` 在重启/重新部署后清空，演示够用
- 每月 750 实例小时，单个服务持续唤醒刚好卡在额度边缘，不建议用定时 ping 强行保活

### Hugging Face Spaces（备选）

仓库同目录可直接作为 Docker Space 部署，需在 Space 设置里指定容器端口 8000。国内访问 hf.space 不稳定，只建议作为备用镜像。


## 十、Coze 版本

`coze/` 目录为可直接导入扣子的配置包：提示词、知识库分册、意图分流决策表、工作流与插件节点设计。导入步骤见 `coze/使用说明.md`。
