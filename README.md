# Agents Bot

Agents Bot 是一个 Windows 多智能体开发工作台。你可以让 GPT 当组长、DeepSeek 写功能、Gemini 写测试，看它们如何分工、交接草稿并审阅同一份项目。工作台支持 GPT、Gemini、Claude、DeepSeek、Kimi 和 GLM。一个团队可以同时使用 API Key 和已登录的本机账号。

**桌面版 v1.4.0** 在左侧增加了「我的 Bot」。你可以给不同职责的助手起名、选择模型账号，切换到各自的对话。将 `AgentsBot-v1.4.0-Windows-x64.zip` 完整解压到可写目录，双击 `AgentsBot.exe` 即可打开工作台。

[下载 Windows x64 便携包](https://github.com/rrship/agents-bot/releases/download/v1.4.0/AgentsBot-v1.4.0-Windows-x64.zip) · [下载配套安卓监控端 1.1（Android 8.0+）](https://github.com/rrship/agents-bot/releases/download/v1.4.0/AgentsBot-Android.apk) · [下载源码 ZIP](https://github.com/rrship/agents-bot/releases/download/v1.4.0/AgentsBot-v1.4.0-source.zip) · [查看 v1.4.0 发布说明](https://github.com/rrship/agents-bot/releases/tag/v1.4.0)

## 从侧栏创建一个 Bot

![v1.4.0 工作台：左侧列出自定义 Bot，右侧是代码审查 Bot 的示例对话及团队绑定入口](docs/images/bot-sidebar.png)

1. 在「我的 Bot」点「＋ 新建」。首次使用时也可直接点「代码审查」「研究助手」或「测试助手」作为起点。
2. 给它起名，选择模型账号，写下它的职责。例如给代码审查 Bot 填入「逐条列出文件名、风险和修改建议」。首次使用可在此处点「新建账号…」配置服务商、模型 ID 和 API 密钥。
3. 保存后点侧栏的 Bot 名称即可对话。点名字旁的「⋯」或对话页的「Bot 设置…」可编辑名称、职责和账号。设置会在重启后保留，各 Bot 的对话、附件和草稿保留在本次窗口内。关闭前点「导出草稿…」可保存生成的文件。
4. 想让它参与团队，打开这个 Bot，点「用于团队内置 Agent」，再到「成员与连接」勾选队友。要让它领队，选「组长统筹」，将「内置 Agent」设为组长。团队有一个内置 Agent 席位，你可以从侧栏选择另一位 Bot 并重新绑定。[Bot 设置与团队绑定指南](使用说明.md#我的-bot)

| 你想做什么 | 从哪里开始 |
| --- | --- |
| 只有一个 API Key，想直接分析文件或生成草稿 | [使用内置 Agent](#使用内置-agent) |
| 在一台电脑上试用两个模型 | [完成第一个本地任务](#完成第一个本地任务) |
| 和异地队友共用项目，或关掉电脑后让 API 成员继续工作 | [加入云服务器房间](#加入云服务器房间) |
| 在手机上查看进度 | [绑定安卓手机](#绑定安卓手机) |

## 使用内置 Agent

1. 打开「内置 Agent 与账号」→「新建」。填写账号名称、模型服务和该账号可用的模型 ID，再填 API Key 或环境变量名。点「保存」。同一服务商可以分别保存个人和工作账号。
2. 选好账号，在「任务或问题」输入要求并点「发送」。例如，点「添加参考文件…」选择一份费用明细 JSON，再输入「按类别汇总金额，列出缺少类别的记录，生成 summary.md」。Agent 可以列出、读取和搜索提供的文本。答复显示在页内，生成的文件用「导出草稿…」保存到新目录。
3. 想让它参与原有的多人团队，展开「账号管理」，点「用于团队内置 Agent」，再到「成员与连接」选其他成员及组长。也可点「用于同名模型成员」，为已有的 GPT、Gemini 等成员指定档案。独立对话切换账号后，原账号的本次对话和草稿可切回查看。

已有多把密钥时，可以将账号写在同一个 JSON 文件里。下面的示例从环境变量读取 DeepSeek 密钥。先设置 `DEEPSEEK_API_KEY` 并重新打开工作台，把 `your-model-id` 换成账号已开通的模型 ID，再将内容保存为 UTF-8 的 `accounts.json`，点「导入账号 JSON」选中它。

```json
{
  "version": 1,
  "accounts": [
    {
      "name": "项目 DeepSeek",
      "provider": "DeepSeek",
      "model": "your-model-id",
      "key_env": "DEEPSEEK_API_KEY"
    }
  ]
}
```

账号管理器只保存环境变量名。直接填写 API Key 时，工作台使用 Windows 当前用户的 DPAPI 加密保存密钥。要把费用明细等业务 JSON 交给模型分析，请在对话中点「添加参考文件…」。[账号 JSON 格式与完整操作](使用说明.md#内置-agent-与账号管理)

## 完成第一个本地任务

1. **启动。** 将 Windows 便携包完整解压到可写目录，双击 `AgentsBot.exe`。包内自带 Python 和 Tk。
2. **接入成员。** 在「成员与连接」勾选至少两位成员。六家模型和内置 Agent 可以组成最多七人的团队。例如，GPT 可以选「账号登录」，先安装 Codex CLI，再点「登录账号…」。DeepSeek 可以选「API Key」，填写模型 ID 和密钥。在这两个成员页分别点「测试连接」，确认两位成员都能回复。[六家模型的账号客户端和密钥配置](使用说明.md#选择团队和模型)
3. **选择协作方式。** 想让 GPT 派单和验收，就选「组长统筹」，将 GPT 设为组长；想让成员先商量接口、再并行实现和互审，就选「平等协作」。
4. **给团队一个可验收的小任务。** 在「任务工作台」点「选择…」，选一个空目录。把下面这段复制到「需求与验收条件」：

   > 用 Python 标准库写一个离线命令行待办工具。运行 `python todo.py add 买牛奶` 后，退出并重新运行 `python todo.py list` 仍能看到“买牛奶”。请提供自动化测试，以及使用临时目录验证这个过程的 `smoke.py`。

5. **运行并查看交付。** 展开「自动验收命令」，在「构建或测试命令」填 `["python","-m","unittest","discover","-v"]`，在「启动冒烟检查」填 `["python","smoke.py"]`。点「开始协作」，在「协作记录」查看分工、修改和检查结果。结束后点「交付目录」取回文件，点「改动差异」审查修改。两栏命令都留空时，交付会标记为「待人工验收」。

成员在实现阶段可以把已完成的文件、接口决定和剩余工作一起交给下一位。工作台也会在上下文首次超过字符预算且有接手人时自动交接。可以在协作记录中查看原因，暂停后用「继续」恢复。[上下文交接说明](使用说明.md#上下文交接)

## 加入云服务器房间

准备一台可通过 SSH 登录的 Linux 服务器，安装带 SQLite 和 HTTPS 支持的 Python 3.11 或更新版本。异地队友使用同一个受信任的项目 SSH 账号，每人可以使用自己的 SSH 密钥。两台电脑都能访问服务器的 SSH 端口即可协作。

1. **发起人创建房间。** 在「成员与连接」选好整个团队，在「任务工作台」写目标。打开「云服务器」，填写服务器、端口和 SSH 用户，点「连接」。首次登录时核对主机指纹，密码在 OpenSSH 窗口输入。点「从工作台创建房间」→「复制房间号」，把房间号发给队友。
2. **队友加入并提供成员。** 连接同一服务器和项目账号，输入房间号，点「加入」。在「本次提供的成员」勾选自己负责的模型。用本机已登录的账号时点「本机参与」，并保持电脑在线；希望关机后继续时，选用 API Key 的成员，点「启动服务器 API」。确认后，服务器进程通过 SSH 接收所选密钥，并使用相应 API 额度。
3. **取回结果。** 在房间查看分工、交接和已提交文件，点「导出文件…」保存到新的本地目录。云房间面向小型文本项目，组长可在云端审阅交付，构建和测试命令在导出后本地执行。[服务器准备、文件大小限制与断线恢复](使用说明.md#云服务器协作)

## 绑定安卓手机

1. 点电脑左侧栏底部的「手机监控」，手机打开配套安卓应用，点「扫码绑定」并扫描电脑上的二维码。同一可信 Wi-Fi 下可直接配对。
2. 外出使用移动数据时，让手机和电脑加入同一个 Tailscale 私有网络。确认电脑配对界面显示「可跨网」。原来通过局域网配对的手机，需要切换到 Tailscale 地址后重新扫码。
3. 查看云任务时，在已加入房间的电脑勾选「手机显示此房间」。手机通过这台电脑获取更新，查看期间请保持工作台和 SSH 会话在线。已启动的服务器 API 成员可以在电脑关机后继续工作。[配对、跨网查看与解绑](手机监控.md)

## 从源码运行

使用带 Tk 的 Python 3.11 或更新版本，在项目目录运行 `python app.py`。运行自动测试使用 `python -m unittest discover -v`。测试使用模拟模型回复，真实账号或密钥可在工作台用「测试连接」核验。[完整中文使用说明](使用说明.md)

## 项目参考

内置 Agent 借鉴 Pi 的小型工具循环，通过现有模型适配器按需读取共享文本。多账号选择和 JSON 导入的交互参考 Cockpit Tools，账号文件使用 Agents Bot 自己的格式。[Pi coding agent](https://github.com/earendil-works/pi/tree/main/packages/coding-agent) · [Cockpit Tools](https://github.com/jlcodes99/cockpit-tools)

成员交接参考 [crew](https://github.com/dichovsky/crew)，分工与独立验收参考 [multi-agent-collaboration](https://github.com/Gambler134201/multi-agent-collaboration)。DeepSeek Harness 账号可通过本机命令派单并等待回复。[dsh-baton](https://github.com/JialaoLiu/dsh-baton)

云房间借鉴 HUD 将任务环境与模型连接分开的设计，SSH 房间、任务领取和上下文交接由本项目实现。[hud-evals/hud-python](https://github.com/hud-evals/hud-python)

安卓扫码与电脑端二维码分别使用 [ZXing Core](https://github.com/zxing/zxing) 和 [Project Nayuki QR Code generator](https://www.nayuki.io/page/qr-code-generator-library)。
