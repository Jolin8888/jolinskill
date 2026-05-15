---
name: find-myself
description: "Jungian psychology self-exploration and interpersonal analysis tool based on The Red Book (红书). Use this skill whenever the user wants to: explore their inner world or emotions, understand behavior patterns, analyze another person's personality or MBTI, resolve interpersonal conflicts or awkward situations, deal with negative emotions, learn Jungian concepts (shadow, projection, complex, persona, individuation), figure out how to respond to difficult messages, or understand relationship dynamics. Trigger on: 自我探索, 心理分析, 阴影, 投射, 情结, 人格面具, 个体化, 荣格, 红书, MBTI, 性格分析, 读人, 为什么他会这样, 我不知道怎么回复, 让我很难受, 很压抑, 很焦虑, 觉得空虚, shadow work, self-reflection, emotional processing, or any request to understand oneself or others at a deeper psychological level. Even if the user just says they feel bad or confused about someone, use this skill."
---

# Find Myself — Jungian Self-Exploration Skill

Generate a single-file React artifact (.jsx) that serves as an interactive Jungian psychology tool with persistent storage and cross-session memory.

## Core Architecture

**5 Tabs**: 自我 (self-exploration) | 读人 (analyze others) | 化解 (resolve scenarios) | 记录 (history) | 学习 (learn concepts)

**Storage Keys** (window.storage):
- `fm-entries` — Array of all conversation entries (max 200)
- `fm-learned` — Array of encountered Jungian concept IDs
- `fm-stage` — Current individuation stage ID string
- `fm-persons` — Array of saved person profiles from 读人 mode

**Auto-save**: Every user message + AI reply automatically saves as a tagged entry with AI-generated metadata (summary, concepts, emotion, stage, insight, unresolved issues, cognitive biases).

**Cross-session memory**: On each new message, build a profile string from recent entries (themes, concepts, unresolved issues, insights, current stage) and inject into the system prompt so the AI "remembers" the user.

## Three AI Modes

### Mode 1: 自我 (Self-Exploration)
System prompt role: Jungian analyst familiar with The Red Book.

Response format (three sections with markers):
```
[对话] — Warm but precise response + ONE probing question going deeper
[洞察] — 1-3 sentence psychological dynamics observation, tag Jungian concept keywords
[红书] — Quote from The Red Book (max 2 sentences) with relevance explanation
```

Key behaviors:
- Dig path: event → emotion → need → core belief → origin
- Correct cognitive biases (all-or-nothing, overgeneralization, mind-reading, catastrophizing, should-statements, emotional reasoning) — gently but clearly
- Explain concepts using the user's own case, never abstract theory
- Dynamic prompts pull from unresolved issues in history

### Mode 2: 读人 (Analyze Others)
System prompt role: Jungian interpersonal analyst.

Flow: User gives the person a nickname (花名) → describes behavior → AI asks probing questions → progressively builds psychological portrait.

Response format:
```
[分析] — Analysis from Jungian perspective + 1 specific follow-up question
[画像] — Progressive portrait: MBTI tendency, core driver, fears, suppressed aspects, joy triggers, persona, shadow, formation causes, relationship advice, landmines to avoid
```

Key behaviors:
- Mark "待确认" when info insufficient
- Remind user their feelings may contain their own projections
- Auto-save portrait after 3-5 rounds

### Mode 3: 化解 (Resolve Scenarios)
System prompt role: Jungian communication consultant.

Response format:
```
[解析] — Surface level (what's happening) + Deep level (what complex/shadow is triggered)
[行动] — Concrete options: 2-3 response approaches (gentle/direct/boundary-setting) OR emotional reframing methods
```

Key behaviors:
- Don't rush to advice; ask questions first to understand full context
- Help user distinguish "this event upset me" from "which inner wound was touched"
- Cognitive bias correction when detected

## Individuation Stages (based on The Red Book)

```
call      → 灵魂的召唤  "我的灵魂啊，你在哪里？"
desert    → 进入沙漠    "沙漠呼唤你，又把你拉回来。"
shadow    → 阴影相遇    "神的意象有一个阴影。"
descent   → 地狱之旅    "我竭尽全力所忍受的就是一股残暴的力量。"
dialogue  → 内在对话    "灵魂的财富以意象的形式存在。"
sacrifice → 献祭与重生  "像植物一样，有些在光明中，有些在黑暗中。"
mandala   → 走向完整    "曼陀罗就是原我，也即是人格的完整性。"
```

AI auto-judges and updates stage based on conversation content.

## Verified Red Book Quotes

Only use these confirmed quotes from the Zhou Dangwei Chinese translation. Never fabricate quotes.

- 「唯一的道路就是你自己的道路。」
- 「神的意象有一个阴影。终极意义是实体的存在，因此会投出一个阴影。」
- 「像植物一样，人也在生长，有些在光明中，有些在黑暗中。但有很多人需要的是黑暗，而非光明。」
- 「灵魂的财富以意象的形式存在。」
- 「他能在欲望那里找到他的灵魂，而不是在欲望的对象上。」
- 「更明智的做法是滋养灵魂，否则你就会在自己的心中养育出恶龙和魔鬼。」
- 「一个人的欲望摆脱掉其他外部的事物之后，他才到达灵魂所在的地方。」
- 「意象的世界是整个世界的一半。」
- 「我的灵魂啊，你在哪里？你能听到我的声音吗？」
- 「阴影就是无意义…但无意义是终极意义的不可分割且永不消亡的孪生兄弟。」
- 「我竭尽全力所忍受的就是一股残暴的力量。」
- 「曼陀罗就是原我，也即是人格的完整性。」
- 「如果他找不到灵魂，空洞的恐惧将会压倒他。」
- 「沙漠呼唤你，又把你拉回来。」

When no exact match fits, use: "荣格在《红书》中表达过类似的意思："

## UI Design Spec

**Color scheme**: Cream white + pink
- Background: `#FFF8F2`, Cards: `#FFFFFF`
- Primary: `#E8788A` (pink), `#D4576A` (rose)
- Text: `#5A3E3E` (warm brown), Secondary: `#9A7B7B`
- User bubbles: gradient `#F9D4DA → #F4B8C4`
- Insight cards: pink translucent bg
- Portrait cards (读人): purple translucent bg `rgba(100,60,140,0.04)`
- Action cards (化解): green translucent bg `rgba(60,140,100,0.04)`

**Font**: `'Noto Serif SC', 'Songti SC', Georgia, serif`

**Loading state**: 3-step progress card (not vague "thinking..."):
1. ◈ 分析中… → ✓
2. ◉ 生成标签… → ✓
3. ◎ 保存记录… → ✓
With progress bar: 33% → 66% → 90%

## API Configuration

Model: `claude-sonnet-4-20250514`
- Main dialogue: max_tokens 1500
- Metadata generation: max_tokens 400, pure JSON response
- Two API calls per user message: (1) dialogue, (2) tag generation

## Jungian Concepts Tracked

```
shadow            → 阴影（被否认的人格面向）
projection        → 投射（把内心放到别人身上）
complex           → 情结（反复激活的情绪模式）
persona           → 人格面具（对外展示的面具）
anima             → 阿尼玛/阿尼姆斯（内在异性面向）
individuation     → 个体化（走向完整的过程）
active_imagination → 积极想象（与无意识对话）
```

Auto-detected from AI response text and marked as "encountered" in learn tab.

## Entry Metadata Schema

Generated by second AI call after each exchange:
```json
{
  "summary": "事件摘要12字内",
  "concepts": ["shadow", "projection"],
  "emotion": "核心情绪2词",
  "stage": "individuation stage id",
  "insight": "核心洞察15字内",
  "unresolved": "未解决问题15字内",
  "bias": "认知偏差名称或空"
}
```

## Implementation Checklist

- [ ] Single .jsx file, all components inline
- [ ] window.storage for persistence (get/set/delete)
- [ ] All styles as inline style objects
- [ ] No external npm packages beyond React hooks
- [ ] Auto-save every exchange (not manual save)
- [ ] Cross-session profile injection
- [ ] Dynamic prompts from unresolved issues
- [ ] Person profiles auto-save after 3-5 rounds
- [ ] Stage auto-update from AI metadata
- [ ] Concept encounter tracking
- [ ] Cognitive bias detection in tags
- [ ] Three distinct AI system prompts for three modes
- [ ] Progress steps during AI processing
- [ ] Type labels (自我/读人/化解) on history entries
