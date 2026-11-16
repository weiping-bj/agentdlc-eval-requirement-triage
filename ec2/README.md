# ec2/ · 实验用 EC2

> **只在专用的实验账号中使用。** 实例角色权限较宽（创建 `s12lab*` 的 IAM 角色——带权限边界、Lambda、AgentCore 资源，并可调整 Transaction Search 等账号级设置），不要部署到生产账号。**手动创建路径（PowerUserAccess + 内联策略）没有权限边界，EC2 上的人可以给自己提权到管理员——只在用完即弃的实验账号里用这条路径。**

每位学员在实验账号里开一台自己的 EC2（Amazon Linux 2023），**只能通过 SSM Session Manager 登录**：不开任何入站端口、不用密钥对。所有实验命令都在这台 EC2 上执行。

下文中的 `<后缀>` 是你的个人后缀：2-12 位小写字母或数字，以字母开头，例如 `zhang3`。同一账号里每人的后缀必须不同。

## 文件说明

| 文件 | 在哪里运行 | 作用 |
|------|-----------|------|
| `s12lab-ec2.yaml` | CloudFormation | 创建 EC2、无入站规则的安全组、实例角色（SSM + 实验权限）；没有默认 VPC 时顺便建一个小 VPC |
| `launch-from-cloudshell.sh` | AWS CloudShell | 一键部署上面的模板（堆栈名 `s12lab-ec2-<后缀>`），打印登录链接，并等待实例在 SSM 中上线 |
| `setup-ec2.sh` | EC2（ec2-user） | 安装 uv + Python 3.12、Node.js、Claude Code，配置 Claude Code 走 Bedrock，创建 `.venv` 并安装依赖。可重复运行 |
| `manual-iam-inline-policy.json` | IAM 控制台 | 手动创建 EC2 时，给实例角色添加的内联策略 |

## 方式一：CloudShell 一键创建（推荐）

1. 登录 AWS 控制台，右上角切换到讲师指定的区域（`us-west-2` 或 `us-east-1`）。
2. 点击顶部工具栏的 CloudShell 图标（`>_`），等待命令行就绪。
3. 复制执行（把 `zhang3` 换成你的后缀）：

   ```bash
   mkdir -p ~/s12lab && cd ~/s12lab
   curl -fsSLO https://raw.githubusercontent.com/weiping-bj/agentdlc-eval-requirement-triage/main/ec2/s12lab-ec2.yaml
   curl -fsSLO https://raw.githubusercontent.com/weiping-bj/agentdlc-eval-requirement-triage/main/ec2/launch-from-cloudshell.sh
   bash launch-from-cloudshell.sh zhang3
   ```

4. 看到 `状态: 已上线` 后，打开脚本打印的 **登录链接**，然后跳到下面的「登录后」。

> 重复运行是安全的：堆栈已存在时不会重新部署，只会再打印一次登录链接。

## 方式二：控制台手动创建（CloudShell 不可用时）

**A. 先创建实例角色（IAM 控制台）**

1. 打开 IAM 控制台 → 左侧「角色 Roles」→「创建角色 Create role」。
2. 可信实体类型选「AWS 服务」，使用案例选 **EC2** →「下一步」。
3. 搜索并勾选两个托管策略：`AmazonSSMManagedInstanceCore`、`PowerUserAccess` →「下一步」。
4. 角色名称填 `s12lab-ec2-<后缀>`（例如 `s12lab-ec2-zhang3`）→「创建角色」。
5. 打开刚创建的角色 →「添加权限 Add permissions」→「创建内联策略 Create inline policy」→ 切换到 **JSON**，把 [`manual-iam-inline-policy.json`](manual-iam-inline-policy.json) 的内容整体粘贴进去 → 策略名填 `s12lab-iam` →「创建策略」。

**B. 再创建 EC2（EC2 控制台）**

1. 打开 EC2 控制台 →「启动实例 Launch instance」。
2. 名称：`s12lab-<后缀>`（例如 `s12lab-zhang3`，安装脚本会从名称识别你的后缀）。
3. 应用程序和操作系统映像（AMI）：选 **Amazon Linux 2023**（64 位 x86）。
4. 实例类型：`t3.large`。
5. 密钥对：选「**在没有密钥对的情况下继续 Proceed without a key pair**」。
6. 网络设置：保持默认 VPC，「自动分配公有 IP」为「启用」；防火墙选「创建安全组」，并**取消勾选「允许来自以下对象的 SSH 流量 Allow SSH traffic from」**（如果点了「编辑」，就把默认的 SSH 入站规则删除）。入站规则必须为空。
7. 配置存储：`30` GiB，类型 `gp3`。
8. 展开「高级详细信息 Advanced details」：
   - 「IAM 实例配置文件 IAM instance profile」：选第 A 步创建的 `s12lab-ec2-<后缀>`；
   - 「元数据版本 Metadata version」：选「**仅 V2（需要令牌）V2 only (token required)**」；
   - 「元数据响应跃点限制 Metadata response hop limit」：填 `2`。
9. 点「启动实例 Launch instance」，等待 2-3 分钟。
10. 实例列表中选中它 →「连接 Connect」→「Session Manager」选项卡 →「连接 Connect」。
    如果提示 SSM Agent 未在线，再等 2-3 分钟后刷新；仍不行请检查第 8 步的 IAM 实例配置文件。

## 登录后（两种方式相同）

Session Manager 默认用户是 `ssm-user`，**请先切换到 `ec2-user`**，再拉代码并安装工具：

```bash
sudo su - ec2-user
git clone https://github.com/weiping-bj/agentdlc-eval-requirement-triage ~/scenario12-lab && cd ~/scenario12-lab
bash ec2/setup-ec2.sh
```

安装约 3-5 分钟，结束时会打印各工具版本和下一步命令（日志在 `~/s12lab-setup.log`，出错可直接重新运行）。然后：

```bash
cd ~/scenario12-lab && source ~/.bashrc && source .venv/bin/activate && ./01-check-env.sh
```

> 以后每次重新登录：先 `sudo su - ec2-user`，再 `cd ~/scenario12-lab && source .venv/bin/activate`。
