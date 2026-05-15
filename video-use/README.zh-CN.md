<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

**video-use** — 用 Claude Code 剪辑视频。100% 开源。

把原始素材丢进文件夹，跟 Claude Code 聊几句，拿回 `final.mp4`。适用于任何内容——口播、混剪、教程、旅行、访谈——没有预设模板，没有菜单。

## 能做什么

- **剪掉废话**（`嗯`、`啊`、口误）和片段间的空白停顿
- **自动调色**每段画面（暖调电影感、中性锐利、或任意自定义 ffmpeg 链）
- **30ms 音频淡入淡出**——每个剪切点都不会有爆音
- **烧录字幕**，风格可定制——默认 2 字大写块，完全可配
- **生成动画叠加层**——通过 [HyperFrames](https://github.com/heygen-com/hyperframes)、[Remotion](https://www.remotion.dev/)、[Manim](https://www.manim.community/) 或 PIL，由并行子 Agent 各自动画处理
- **自评估渲染输出**——每个剪切边界反复检查，然后再给你看
- **持久化会话记忆**——存入 `project.md`，下周打开继续上次的进度

## 一键安装提示

复制粘贴到 Claude Code、Codex、Hermes、Openclaw 或任何有 shell 权限的 Agent 中：

```text
Set up https://github.com/browser-use/video-use for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

Agent 会自动处理克隆、依赖安装、技能注册，并提示你输入 ElevenLabs API Key（在 [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) 获取）。

然后让 Agent 指向你的原始素材文件夹：

```bash
cd /path/to/your/videos
claude    # 或 codex、hermes 等
```

进入会话后：

> 把这些剪成一个发布视频

它会盘点素材、提出策略、等你确认，然后在素材旁边生成 `edit/final.mp4`。所有输出都在 `<视频目录>/edit/` 下——技能目录保持干净。

## 手动安装

如果你更想手把手来：

```bash
# 1. 克隆并软链接到 Agent 的技能目录
git clone https://github.com/browser-use/video-use ~/Developer/video-use
ln -sfn ~/Developer/video-use ~/.claude/skills/video-use        # Claude Code
# ln -sfn ~/Developer/video-use ~/.codex/skills/video-use       # Codex

# 2. 安装依赖
cd ~/Developer/video-use
uv sync                         # 或: pip install -e .
brew install ffmpeg             # 必须
brew install yt-dlp             # 可选，用于下载在线素材

# 3. 添加 ElevenLabs API Key
cp .env.example .env
$EDITOR .env                    # ELEVENLABS_API_KEY=...
```

## 工作原理

LLM 永远不会"看"视频。它**读**视频——通过两层信息获取剪切所需的全部精度。

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view 复合视图 — 胶片条 + 说话人轨 + 波形 + 词级标签 + 静音间隙剪切候选" width="100%">
</p>

**第一层 —— 音频转录（始终加载）。** 每个素材调用一次 ElevenLabs Scribe，获得词级时间戳、说话人分离和音频事件（`(笑声)`、`(掌声)`、`(叹息)`）。所有片段打包进一个约 12KB 的 `takes_packed.md`——这是 LLM 的主要阅读视图。

```
## C0103  (时长: 43.0s, 8 个短语)
  [002.52-005.36] S0 一个网页 Agent 百分之九十的工作完全是浪费。
  [006.08-006.74] S0 我们修好了这个问题。
```

**第二层 —— 视觉复合（按需加载）。** `timeline_view` 对任意时间范围生成胶片条 + 波形 + 词级标签的 PNG。只在决策点调用——模糊停顿、重拍对比、剪切点合理性检查。

> 朴素方案：30,000 帧 × 1,500 tokens = **4500 万 tokens 的噪音**。
> Video Use：**12KB 文本 + 几张 PNG**。

和 browser-use 给 LLM 结构化 DOM 而不是截图的思路一样——只不过换成了视频。

## 流程

```
转录 ──> 打包 ──> LLM 推理 ──> EDL ──> 渲染 ──> 自评估
                                                    │
                                                    └─ 有问题？修复 + 重新渲染（最多 3 次）
```

自评估循环在渲染输出的每个剪切边界运行 `timeline_view`——捕捉视觉跳跃、音频爆音、隐藏字幕。只有通过检查你才会看到预览。

## 设计原则

1. **文本 + 按需视觉。** 不逐帧倾倒。转录才是界面。
2. **音频为主，视觉跟随。** 剪切点来自语音边界和静音间隙。
3. **询问 → 确认 → 执行 → 自评 → 持久化。** 未经策略批准绝不落刀。
4. **零内容类型假设。** 先看，再问，然后剪。
5. **12 条硬规则，其余艺术自由。** 制作正确性不可妥协，品味可以。

完整制作规则和剪辑工艺参见 [`SKILL.md`](./SKILL.md)。
