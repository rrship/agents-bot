# Agents Bot

Agents Bot 是一个 Windows 多智能体开发工作台。你可以让 GPT 当组长、DeepSeek 写功能、Gemini 写测试，看它们如何分工、交接草稿并审阅同一份项目。工作台支持 GPT、Gemini、Claude、DeepSeek、Kimi 和 GLM。一个团队可以同时使用 API Key 和已登录的本机账号。

**下载 v1.2.0**　[Windows x64 便携包](https://github.com/rrship/agents-bot/releases/download/v1.2.0/AgentsBot-v1.2.0-Windows-x64.zip) · [安卓监控端 1.1（Android 8.0+）](https://github.com/rrship/agents-bot/releases/download/v1.2.0/AgentsBot-Android.apk) · [版本说明与源码](https://github.com/rrship/agents-bot/releases/tag/v1.2.0)

| 你想做什么 | 从哪里开始 |
| --- | --- |
| 在一台电脑上试用两个模型 | [完成第一个本地任务](#完成第一个本地任务) |
| 和异地队友共用项目，或关掉电脑后让 API 成员继续工作 | [加入云服务器房间](#加入云服务器房间) |
| 在手机上查看进度 | [绑定安卓手机](#绑定安卓手机) |

## 完成第一个本地任务

1. **启动。** 将 Windows 便携包完整解压到可写目录，双击 `AgentsBot.exe`。包内自带 Python 和 Tk。
2. **接入成员。** 在「成员与连接」勾选 2 至 6 位成员。举例来说，GPT 可以选「账号登录」，先安装 Codex CLI，再点「登录账号…」；DeepSeek 可以选「API Key」，填写模型 ID 和密钥。分别点「测试连接」，确认两位成员都能回复。[六家模型的账号客户端和密钥配置](使用说明.md#选择团队和模型)
3. **选择协作方式。** 想让 GPT 派单和验收，就选「组长统筹」，将 GPT 设为组长；想让成员先商量接口、再并行实现和互审，就选「平等协作」。
4. **给团队一个可验收的小任务。** 在「任务工作台」点「选择…」，选一个空目录。把下面这段复制到「需求与验收条件」：

   > 用 Python 标准库写一个离线命令行待办工具。运行 `python todo.py add 买牛奶` 后，退出并重新运行 `python todo.py list` 仍能看到“买牛奶”。请提供自动化测试，以及使用临时目录验证这个过程的 `smoke.py`。

5. **运行并查看交付。** 在「构建或测试命令」填 `["python","-m","unittest","discover","-v"]`，在「启动冒烟检查」填 `["python","smoke.py"]`。点「开始协作」，在「协作记录」查看分工、修改和检查结果。结束后点「交付目录」取回文件，点「改动差异」审查修改。两栏命令都留空时，交付会标记为「待人工验收」。

成员在实现阶段可以把已完成的文件、接口决定和剩余工作一起交给下一位。工作台也会在上下文首次超过字符预算且有接手人时自动交接。可以在协作记录中查看原因，暂停后用「继续」恢复。[上下文交接说明](使用说明.md#上下文交接)

## 加入云服务器房间

准备一台可通过 SSH 登录的 Linux 服务器，安装带 SQLite 和 HTTPS 支持的 Python 3.11 或更新版本。异地队友使用同一个受信任的项目 SSH 账号，每人可以使用自己的 SSH 密钥。两台电脑都能访问服务器的 SSH 端口即可协作。

1. **发起人创建房间。** 在「成员与连接」选好整个团队，在「任务工作台」写目标。打开「云服务器」，填写服务器、端口和 SSH 用户，点「连接」。首次登录时核对主机指纹，密码在 OpenSSH 窗口输入。点「从工作台创建房间」→「复制房间号」，把房间号发给队友。
2. **队友加入并提供成员。** 连接同一服务器和项目账号，输入房间号，点「加入」。在「本次提供的成员」勾选自己负责的模型。用本机已登录的账号时点「本机参与」，并保持电脑在线；希望关机后继续时，选用 API Key 的成员，点「启动服务器 API」。确认后，服务器进程通过 SSH 接收所选密钥，并使用相应 API 额度。
3. **取回结果。** 在房间查看分工、交接和已提交文件，点「导出文件…」保存到新的本地目录。云房间面向小型文本项目，组长可在云端审阅交付，构建和测试命令在导出后本地执行。[服务器准备、文件大小限制与断线恢复](使用说明.md#云服务器协作)

## 绑定安卓手机

1. 电脑右上角点「手机监控」，手机打开配套安卓应用，点「扫码绑定」并扫描电脑上的二维码。同一可信 Wi-Fi 下可直接配对。
2. 外出使用移动数据时，让手机和电脑加入同一个 Tailscale 私有网络。确认电脑配对界面显示「可跨网」。原来通过局域网配对的手机，需要切换到 Tailscale 地址后重新扫码。
3. 查看云任务时，在已加入房间的电脑勾选「手机显示此房间」。手机通过这台电脑获取更新，查看期间请保持工作台和 SSH 会话在线。已启动的服务器 API 成员可以在电脑关机后继续工作。[配对、跨网查看与解绑](手机监控.md)

## 从源码运行

使用带 Tk 的 Python 3.11 或更新版本，在项目目录运行 `python app.py`。运行自动测试使用 `python -m unittest discover -v`。测试使用模拟模型回复，真实账号或密钥可在工作台用「测试连接」核验。[完整中文使用说明](使用说明.md)

## 项目参考

成员交接参考 [crew](https://github.com/dichovsky/crew)，分工与独立验收参考 [multi-agent-collaboration](https://github.com/Gambler134201/multi-agent-collaboration)。DeepSeek Harness 账号可通过本机命令派单并等待回复。[dsh-baton](https://github.com/JialaoLiu/dsh-baton)

云房间借鉴 HUD 将任务环境与模型连接分开的设计，SSH 房间、任务领取和上下文交接由本项目实现。[hud-evals/hud-python](https://github.com/hud-evals/hud-python)

安卓扫码与电脑端二维码分别使用 [ZXing Core](https://github.com/zxing/zxing) 和 [Project Nayuki QR Code generator](https://www.nayuki.io/page/qr-code-generator-library)。
