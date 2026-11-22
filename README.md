# Agents Bot

Agents Bot 是一个 Windows 多智能体开发工作台。你可以把一个软件任务交给 2 至 6 位 AI 成员，让它们分工写代码、互相审阅，再用你指定的命令验收。它适合开发小型工具、为现有项目补模块或测试，以及让不同模型协作处理同一份需求。

## 能做什么

- **组长统筹。** 任选一位成员负责拆任务和验收，其余成员并行实现。例如让 GPT 分派任务，DeepSeek 实现功能，Kimi 编写测试。
- **平等协作。** 成员先交换接口约定，再分别提交文件并互审集成结果。
- **手机扫码查看进度。** 在 Windows 工作台点「手机监控」，再用安卓端扫码绑定，即可查看任务状态、组长和最近的协作记录。两端连接同一个 Tailscale 私有网络后，手机也可以使用移动数据查看进度。[安装与配对说明](手机监控.md)
- **独立连接。** 支持 GPT、Gemini、Claude、DeepSeek、Kimi 和 GLM。每位成员分别配置官方 API Key 或本机账号客户端，同一团队可以混用两种方式。
- **交付可检查。** 工作台在独立副本中集成文件，展示协作记录和文件差异，并收集验收命令的退出码与输出。可以暂停、恢复或补充要求。

## 快速开始

Windows x64 便携包和 Android 8.0 及以上的安装包在 [GitHub Releases](https://github.com/rrship/agents-bot/releases) 下载。

1. 在 Windows 完整解压便携包，双击 `AgentsBot.exe`。从源码启动时，使用带 Tk 的 Python 3.11 或更新版本，在仓库目录运行 `python app.py`。
2. 打开「成员与连接」，选至少两位成员并测试连接。组长模式还需要选一位组长。
3. 选择项目目录，输入目标和验收标准。例如「做一个离线待办应用，支持新增和重启后查询，请同时提供单元测试和启动检查」。
4. 如需自动验收，填写测试和启动检查命令，每行一个 JSON 参数数组，例如 `["python","smoke.py"]`。也可以留空，交付后人工验收。
5. 点击「开始协作」。完成后从「交付目录」查看程序，从「改动差异」检查修改。

账号客户端、环境变量、DeepSeek Harness 接入和完整示例见[中文使用说明](使用说明.md)。工作台会将任务以及所需的项目文本发送给你选定的服务。验收命令以当前 Windows 用户权限运行。

## 开发与验证

在仓库目录运行 `python -m unittest discover -v`。测试使用模拟模型回复，并实际执行本地验收命令。真实账号和 API 需要使用者配置后通过「测试连接」核验。

安卓扫码使用 [ZXing Core](https://github.com/zxing/zxing)，电脑二维码使用 [Project Nayuki QR Code generator](https://www.nayuki.io/page/qr-code-generator-library)。

## 项目参考

成员交接参考 [crew](https://github.com/dichovsky/crew)。分工与独立验收参考 [multi-agent-collaboration](https://github.com/Gambler134201/multi-agent-collaboration)。DeepSeek Harness 账号模式可通过 [dsh-baton](https://github.com/JialaoLiu/dsh-baton) 的本机命令派单并等待返回。
