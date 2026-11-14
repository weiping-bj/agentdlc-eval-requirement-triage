# workshop-studio/ · AWS Workshop Studio 内容仓库骨架（维护者）

把本目录的内容复制到 Workshop Studio 的内容仓库根目录。WS 路径不依赖 GitHub：实验代码以 S3 Assets
（`adlc-lab.zip`）分发，EC2 启动时自动下载、解压、安装。CloudShell / GitHub 路径（`ec2/`）保持不变。

| 路径 | 作用 |
|------|------|
| `contentspec.yaml` | WS 配置：每位学员一个账号（`workshop_studio`），区域 us-west-2（推荐）/ us-east-1，部署 `static/adlc-lab.yaml` 并传入 `{{.AssetsBucketName}}` / `{{.AssetsBucketPrefix}}` |
| `static/adlc-lab.yaml` | CloudFormation（WS 按文件名把堆栈命名为 `adlc-lab`）。由 `ec2/adlc-ec2.yaml` 派生：同样无入站端口、仅 SSM、IMDSv2、同一实例角色和权限边界，另加只读实验包对象的 `s3:GetObject`。实验的 3 个服务角色（Runtime、评估器 Lambda、在线评估）由堆栈预先创建（固定的服务信任），实例角色只能把它们传给各自的服务（PassRole），除服务关联角色外没有其他 IAM 写权限 |
| `static/iam-policy.json` | 学员角色附加策略（另附 AWS 托管策略 `ReadOnlyAccess`）：只对带 `adlc:lab=agentdlc` 标签的实例开 Session Manager、只能恢复/结束自己的会话；可在 S3 控制台向本账号的 `adlc-*` 桶上传/删除文件（手册扩展 6 用它把讨论模板拷到 EC2） |
| `content/` | 中文操作手册（WS markdown，每页 `index.zh.md`；章号与脚本编号一致：第 N 章用 `N0`–`N9` 号脚本，扩展用 `ext-*.sh`）。图片在 `static/images/`：飞轮 SVG 由 `tools/ws_flywheel.py` 生成，截图清单见 `SHOTLIST.md` |
| `assets/` | `tools/build-ws-bundle.sh` 的输出位置，zip 不提交 |

## 发布步骤

```bash
./tools/build-ws-bundle.sh      # 生成 workshop-studio/assets/adlc-lab.zip 并打印 sha256（有未提交改动时拒绝构建）
```

1. 用 WS 控制台 Assets 页面给出的凭证/命令，把 `adlc-lab.zip` 上传到内容的 assets 位置（对象名必须是 `adlc-lab.zip`）。
2. 把 `contentspec.yaml`、`static/`、`content/` 推送到 WS 内容仓库，等构建成功后新建测试活动（assets 在构建时冻结，改了 zip 要重新构建）。

## EC2 启动时做什么（UserData，root 运行一次）

1. 写 `/etc/profile.d/adlc.sh`（区域、`LAB_SUFFIX=ws`），`dnf` 安装 git/jq/unzip。
2. 从 `s3://<AssetsBucketName>/<AssetsBucketPrefix>adlc-lab.zip` 下载（记录 sha256、`unzip -t` 检查完整性）并以 ec2-user 身份解压到 `/home/ec2-user/adlc-lab`，`git init` 并提交一次（和 git clone 一样，`tools/pull-templates.sh` 与手册里的 `git diff` 需要），写入后缀（`tools/set-suffix.sh`）。
3. 以 ec2-user 运行 `ec2/setup-ec2.sh`（最多 3 次、每次限时，日志 `/home/ec2-user/adlc-setup.log`）。
4. **Transaction Search（必需）**：WS 活动账号默认关闭。运行 `./11-enable-observability.sh`（已开启则不改，`.lab-state/observability.json` 记录是否由本实验开启，供 `99-cleanup.sh --all --account-level` 还原），再用 `--verify` 轮询到 `Destination=CloudWatchLogs`、`Status=ACTIVE`（最多约 10 分钟），并确认 X-Ray 写 `aws/spans` 的日志资源策略和 Default 索引规则；未就绪则堆栈失败，原因里是观察到的状态。
5. 用 `curl` 给 `SetupWait`（WaitCondition，超时 60 分钟）发信号：成功后堆栈才 `CREATE_COMPLETE`，输出 `SetupResult` 记录结果和包的来源 commit（`--allow-dirty` 构建的包会标 `DIRTY-TEST-BUILD`）；失败时失败步骤和日志末尾出现在堆栈事件里（回滚会删掉实例；CloudFormation 只显示 ASCII，中文会变成 `?`）。引导日志 `/var/log/adlc-bootstrap.log`。

学员在 Session Manager 中登录后是 `ssm-user`，先执行 `sudo su - ec2-user`（自动进入 `~/adlc-lab` 并激活 `.venv`）。

## 在自己的账号里模拟 WS

建一个私有桶（阻止公有访问），把 zip 传到某个前缀下（前缀以 `/` 结尾），再部署：

```bash
aws cloudformation create-stack --stack-name adlc-lab --template-body file://workshop-studio/static/adlc-lab.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=AssetsBucketName,ParameterValue=<桶> ParameterKey=AssetsBucketPrefix,ParameterValue=<前缀>/
```

清理：在实例上 `./99-cleanup.sh`，再删除堆栈和桶。
