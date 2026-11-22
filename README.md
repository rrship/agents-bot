# Agents Bot

Agents Bot 是一个 Windows 多智能体开发工作台。把同一项软件需求交给 2 至 6 位 AI 成员，让它们分工写代码、交接草稿并审阅结果。例如，让 GPT 拆解需求、DeepSeek 实现功能、Gemini 编写测试。团队可以在一台电脑上工作，也可以通过 SSH 加入同一个 Linux 云房间，手机端能查看进度。

**下载 v1.2.0**　[Windows x64 便携包](https://github.com/rrship/agents-bot/releases/download/v1.2.0/AgentsBot-v1.2.0-Windows-x64.zip) · [配套安卓监控端 1.1（Android 8.0+）](https://github.com/rrship/agents-bot/releases/download/v1.2.0/AgentsBot-Android.apk) · [版本说明与源码](https://github.com/rrship/agents-bot/releases/tag/v1.2.0)

## 能做什么

- **组长统筹。** 任选一位成员负责拆任务和验收，其余成员并行实现。例如让 GPT 分派任务，DeepSeek 实现功能，Kimi 编写测试。
- **云服务器协作。** 通过 SSH 创建项目房间。不同地点的队友登录同一台服务器的项目账号，用房间号加入，可以各自提供已登录的模型。把 API 成员启动在服务器后台后，关闭发起任务的电脑，这些成员仍能继续工作。
- **上下文交接。** 成员在实现阶段可以提交草稿、列出待办并指定接手人。上下文超过工作台的字符预算时，系统自动生成交接摘要。接手人从共享文件继续，协作记录保留交接原因。
- **平等协作。** 成员先交换接口约定，再分别提交文件并互审集成结果。
- **手机扫码查看进度。** 在 Windows 工作台点「手机监控」，再用安卓端扫码绑定，即可查看任务状态、组长和最近的协作记录。两端连接同一个 Tailscale 私有网络后，手机也可以使用移动数据查看进度。[安装与配对说明](手机监控.md)
- **独立连接。** 支持 GPT、Gemini、Claude、DeepSeek、Kimi 和 GLM。每位成员分别配置官方 API Key 或本机账号客户端，同一团队可以混用两种方式。
- **交付可检查。** 工作台在独立副本中集成文件，展示协作记录和文件差异，并收集验收命令的退出码与输出。可以暂停、恢复或补充要求。

## 在电脑上完成第一个任务

1. 在 Windows 完整解压便携包，双击 `AgentsBot.exe`。从源码启动时，使用带 Tk 的 Python 3.11 或更新版本，在仓库目录运行 `python app.py`。
2. 打开「成员与连接」，勾选至少两位成员。每人分别填写模型 ID 和 API Key，或安装对应的本机账号客户端后点「登录账号…」。逐个点「测试连接」。若希望由一人派单和复核，再选「组长统筹」并指定组长。
3. 选择项目目录，输入目标和验收标准。例如「做一个离线待办应用，支持新增和重启后查询，请同时提供单元测试和启动检查」。
4. 如需自动验收，填写测试和启动检查命令，每行一个 JSON 参数数组，例如 `["python","smoke.py"]`。也可以留空，交付后人工验收。
5. 点击「开始协作」。完成后从「交付目录」查看程序，从「改动差异」检查修改。

账号客户端、环境变量、DeepSeek Harness 接入和完整示例见[中文使用说明](使用说明.md)。工作台会将任务以及所需的项目文本发送给你选定的服务。验收命令以当前 Windows 用户权限运行。

## 开发与验证

在仓库目录运行 `python -m unittest discover -v`。测试使用模拟模型回复，并实际执行本地验收命令。真实账号和 API 需要使用者配置后通过「测试连接」核验。

安卓扫码使用 [ZXing Core](https://github.com/zxing/zxing)，电脑二维码使用 [Project Nayuki QR Code generator](https://www.nayuki.io/page/qr-code-generator-library)。

## 和异地队友完成同一个项目

1. 发起人先选好团队，在「任务工作台」填写目标。打开「云服务器」，用项目 SSH 账号连接装有 Python 3.11+ 的 Linux 服务器，点「从工作台创建房间」并复制房间号。
2. 队友连接同一服务器和项目账号，输入房间号并点「加入」。每人勾选自己提供的成员，使用本机账号时点「本机参与」，需要让 API 成员在电脑关闭后继续工作时点「启动服务器 API」。
3. 在房间中查看成员进度和交接记录，点「导出文件…」保存交付。需要运行构建或测试时，在本地执行验收命令。[完整云服务器指南](使用说明.md#云服务器协作)

**用手机看云任务。** 在已连接房间的电脑勾选「手机显示此房间」，然后用安卓端扫码绑定。已绑定的手机可以直接打开。手机通过这台电脑获取更新，查看期间请保持工作台和 SSH 连接在线。[手机绑定和跨网查看](手机监控.md)

## 项目参考

成员交接参考 [crew](https://github.com/dichovsky/crew)。分工与独立验收参考 [multi-agent-collaboration](https://github.com/Gambler134201/multi-agent-collaboration)。DeepSeek Harness 账号模式可通过 [dsh-baton](https://github.com/JialaoLiu/dsh-baton) 的本机命令派单并等待返回。

云工作区借鉴了 HUD 将任务环境与模型连接分开的设计。SSH 房间、领取任务和上下文交接由本项目实现。[hud-evals/hud-python](https://github.com/hud-evals/hud-python)
