# workshop-studio/ · AWS Workshop Studio 内容仓库骨架（维护者）

把本目录的内容复制到 Workshop Studio 的内容仓库根目录。WS 路径不依赖 GitHub：实验代码以 S3 Assets
（`adlc-lab.zip`）分发，EC2 启动时自动下载、解压、安装。CloudShell / GitHub 路径（`ec2/`）保持不变。

| 路径 | 作用 |
|------|------|
| `contentspec.yaml` | WS 配置：每位学员一个账号（`workshop_studio`），区域 us-west-2（推荐）/ us-east-1，部署 `static/adlc-lab.yaml` 并传入 `{{.AssetsBucketName}}` / `{{.AssetsBucketPrefix}}` |
| `static/adlc-lab.yaml` | CloudFormation（WS 按文件名把堆栈命名为 `adlc-lab`）。由 `ec2/adlc-ec2.yaml` 派生：同样无入站端口、仅 SSM、IMDSv2、同一实例角色和权限边界，另加只读实验包对象的 `s3:GetObject` |
| `static/iam-policy.json` | 学员角色附加策略：只允许对带 `adlc:lab=agentdlc` 标签的实例开 Session Manager（另附 AWS 托管策略 `ReadOnlyAccess`） |
| `content/index.zh.md` | 占位首页（手册迁移是后续任务） |
| `assets/` | `tools/build-ws-bundle.sh` 的输出位置，zip 不提交 |

## 发布步骤

```bash
./tools/build-ws-bundle.sh --require-clean      # 生成 workshop-studio/assets/adlc-lab.zip 并打印 sha256
```

1. 用 WS 控制台 Assets 页面给出的凭证/命令，把 `adlc-lab.zip` 上传到内容的 assets 位置（对象名必须是 `adlc-lab.zip`）。
2. 把 `contentspec.yaml`、`static/`、`content/` 推送到 WS 内容仓库，等构建成功后新建测试活动（assets 在构建时冻结，改了 zip 要重新构建）。

## EC2 启动时做什么（UserData，root 运行一次）

1. 写 `/etc/profile.d/adlc.sh`（区域、`LAB_SUFFIX=ws`），`dnf` 安装 git/jq/unzip/aws-cfn-bootstrap。
2. 从 `s3://<AssetsBucketName>/<AssetsBucketPrefix>adlc-lab.zip` 下载、校验并以 ec2-user 身份解压到 `/home/ec2-user/adlc-lab`，写入后缀（`tools/set-suffix.sh`）。
3. 以 ec2-user 运行 `ec2/setup-ec2.sh`（日志 `/home/ec2-user/adlc-setup.log`），再运行 `./02-enable-observability.sh`（失败只告警，学员可重跑）。
4. `cfn-signal`：成功后堆栈才 `CREATE_COMPLETE`（超时 40 分钟）；失败时把日志末尾写进失败原因。引导日志 `/var/log/adlc-bootstrap.log`。

学员在 Session Manager 中登录后是 `ssm-user`，先执行 `sudo su - ec2-user`（自动进入 `~/adlc-lab` 并激活 `.venv`）。

## 在自己的账号里模拟 WS

建一个私有桶（阻止公有访问），把 zip 传到某个前缀下（前缀以 `/` 结尾），再部署：

```bash
aws cloudformation create-stack --stack-name adlc-lab --template-body file://workshop-studio/static/adlc-lab.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=AssetsBucketName,ParameterValue=<桶> ParameterKey=AssetsBucketPrefix,ParameterValue=<前缀>/
```

清理：在实例上 `./99-cleanup.sh`，再删除堆栈和桶。
