# jolinskill

Jolin 个人 Claude Code skill 集合。

## 包含的 skill

| 目录 | 名称 | 说明 |
|---|---|---|
| `find-myself/` | find-myself | 基于荣格《红书》的自我探索与人际分析 |
| `article-structure-intent/` | article-structure-intent | 文章结构优化与深层意图还原 |
| `shudong/` | 树洞 | 情绪倾诉与心理陪伴 |
| `video-use/` | video-use | 通过对话编辑视频 |
| `skills/知乎抓取C端用户感受/` | 知乎抓取C端用户感受 | 每日抓取知乎 C 端用户感受，完整报告推送钉钉群“海外信息同步” |
| `skills/知乎家纺摄影客户需求挖掘/` | 知乎家纺摄影客户需求挖掘 | 每日挖掘家纺摄影客户需求、内容机会和成交意图 |

| `skills/catalog-making/` | catalog制作 | 高端 B 端产品图册：材质、节日场景、OEM 独立选型与 PDF 完整性检查 |

## 使用方式

在 Claude Code 中通过 plugin marketplace 引用本仓库，或将单个 skill 目录复制到 `~/.claude/skills/`。

### catalog制作

调用 `$catalog-making`，或说“用 catalog制作 制作/更新产品图册”。技能显示名为 **catalog制作**。

此技能的单一安装位置为 `~/.codex/skills/catalog-making`。其他宿主需要使用时通过指向此目录的兼容桥接或符号链接引用，避免维护第二份实体副本。完整流程、样式规范与检查脚本见 [`SKILL.md`](skills/catalog-making/SKILL.md)。
