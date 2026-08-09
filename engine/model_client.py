"""学迹分析 · 视觉模型客户端。

职责：调国内视觉模型（通义千问 VL / 智谱 GLM-4V 等），完成两件事：
  1. analyze_image — 图转 md：把作业照片→结构化 Markdown（题目内容 + 学生作答）
  2. judge_questions — 判题：根据图片判断每题对错/错误类型/难度

所有 provider 走 OpenAI 兼容格式（DashScope、智谱均支持），配置在
config/model_providers.yaml，API key 从环境变量读。

不依赖任何第三方 SDK，只用 httpx 发 HTTP 请求。
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "model_providers.yaml"
_KNOWLEDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge" / "primary_math.json"


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    enabled: bool = True


def load_providers() -> list[ProviderConfig]:
    """从 yaml 加载所有 enabled 的 provider，读环境变量拿 key。"""
    if not _CONFIG_PATH.exists():
        return []
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    providers = []
    for p in raw.get("providers", []):
        if not p.get("enabled", True):
            continue
        key = os.environ.get(p.get("api_key_env", ""), "")
        providers.append(ProviderConfig(
            name=p["name"],
            base_url=p["base_url"],
            model=p["model"],
            api_key=key,
            enabled=True,
        ))
    return providers


def load_practice_model() -> Optional[ProviderConfig]:
    """加载举一反三用的文本模型配置。"""
    if not _CONFIG_PATH.exists():
        return None
    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    pm = raw.get("practice_model")
    if not pm:
        return None
    key = os.environ.get(pm.get("api_key_env", ""), "")
    return ProviderConfig(
        name=pm.get("name", "practice"),
        base_url=pm["base_url"],
        model=pm["model"],
        api_key=key,
    )


def load_math_knowledge(grade: int = 4) -> str:
    """加载小学数学教材知识库，并整理成可注入 prompt 的文本。"""
    if not _KNOWLEDGE_PATH.exists():
        return ""
    try:
        raw = json.loads(_KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""

    g = str(grade)
    grade_info = (raw.get("grades") or {}).get(g) or (raw.get("grades") or {}).get("4") or {}
    topics = grade_info.get("topics") or []
    rules = grade_info.get("rules") or []
    common_errors = grade_info.get("common_errors") or []
    principles = raw.get("grading_principles") or []
    few_shot = raw.get("few_shot") or []

    lines = [f"【{g}年级数学教材要点】"]
    if topics:
        lines.append("知识点：" + "、".join(topics[:8]))
    if rules:
        lines.append("判定规则：")
        for r in rules[:6]:
            lines.append(f"- {r}")
    if common_errors:
        lines.append("常见错误：")
        for e in common_errors[:5]:
            lines.append(f"- {e}")
    if principles:
        lines.append("总原则：")
        for p in principles[:5]:
            lines.append(f"- {p}")
    if few_shot:
        lines.append("参考样例：")
        for s in few_shot[:4]:
            ans = s.get("student_answer", "")
            ok = "对" if s.get("correct") is True else ("错" if s.get("correct") is False else "空")
            reason = s.get("reason", "")
            lines.append(f"- 题：{s.get('content','')}；作答：{ans}；判定：{ok}；说明：{reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 判题中间结果
# ---------------------------------------------------------------------------


@dataclass
class QuestionJudgement:
    """单个模型对一题的判定（多模型融合前的中间结果）。"""
    q_num: int
    correct: Optional[bool]
    error_type: Optional[str]
    error_detail: Optional[str]
    difficulty: str = "medium"
    question_content: str = ""
    student_answer: Optional[str] = None
    confidence: float = 0.8
    question_type: str = "calculation"


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

# 图转 md：要求模型把照片里的题目和作答提取成结构化 markdown
EXTRACT_PROMPT = """你是一个作业识别助手。请仔细分析这张作业/试卷照片，提取出所有题目和学生的作答。

输出格式要求（严格按此 JSON 结构，不要输出其他内容）：
```json
{
  "questions": [
    {
      "q_num": 1,
      "type": "calculation",
      "content": "题目的完整文字内容，数学符号用人能直接看懂的写法（如 1/2、x^2、根号2、约等于），不要用 LaTeX",
      "student_answer": "学生的作答内容，如果没有作答则为 null",
      "difficulty": "easy/medium/hard"
    }
  ]
}
```

题型 type 可选：calculation(计算题) / word_problem(应用题) / concept(概念题) / fill_blank(填空题) / choice(选择题)。
如果照片不清晰或不是作业，返回 {"questions": []}。
只输出 JSON，不要解释。"""

# 判题：要求模型判断每题对错
JUDGE_PROMPT = """你是一个经验丰富的教师，正在批改学生的作业照片。

请逐题判断学生的作答是否正确，并对错误题进行分类。

输出格式要求（严格按此 JSON 结构，不要输出其他内容）：
```json
{
  "judgements": [
    {
      "q_num": 1,
      "correct": true,
      "error_type": null,
      "error_detail": null,
      "difficulty": "medium",
      "confidence": 0.9
    },
    {
      "q_num": 2,
      "correct": false,
      "error_type": "calculation",
      "error_detail": "简述错误原因",
      "difficulty": "hard",
      "confidence": 0.85
    }
  ]
}
```

判定规则：
- correct: true=做对, false=做错, null=未作答(空题)
- error_type（仅错题填，5选1）：
  - careless: 粗心/习惯（抄错数、漏符号、会做但做错）
  - concept: 概念不清（公式记错、定理理解错）
  - calculation: 计算失误（进位错、算错数）
  - method: 方法错误（解题方向错、用错方法）
  - reading: 审题错误（看错条件、答非所问）
- difficulty: easy/medium/hard
- confidence: 你对判定的把握 0-1
- 判题要尽量稳定：同一题同一作答，多次批改结论应一致

只输出 JSON，不要解释。"""


# 合并版：图转md + 判题一次搞定（省一半调用/时间/钱）
EXTRACT_AND_JUDGE_PROMPT = """你是严谨的小学/初中数学批改老师。你的第一目标是：正确率必须准确。

请看这张作业照片，完成两件事：
1. 提取每道题的题目和学生作答
2. 判断每题对错

【最高优先级判题规则】
1. 只根据“题目要求 + 学生作答”判断，不要臆造学生没写的内容。
2. 只要学生最终答案正确，就判 correct=true。
3. 书写潦草、格式不漂亮、步骤不完整，但答案正确：仍然 correct=true。
4. 只有最终答案明确错误，才判 correct=false。
5. 看不清/空白/无法确认：correct=null（空题），不要猜成错误。
6. 全对卷子必须全部 correct=true，不能随便判错。
7. 对题：error_type 和 error_detail 必须是 null。
8. 错题：才填 error_type（5选1）和 error_detail。

【输出要求】
- 只输出 JSON，不要解释
- 数学符号用人能看懂的写法（1/2、x^2、根号2），禁止 LaTeX
- 示例仅说明格式，不要照抄示例对错

输出格式：
```json
{
  "questions": [
    {
      "q_num": 1,
      "type": "calculation",
      "content": "题目内容",
      "student_answer": "学生作答",
      "correct": true,
      "error_type": null,
      "error_detail": null,
      "difficulty": "easy",
      "confidence": 0.95
    }
  ]
}
```

字段说明：
- type: calculation / word_problem / concept / fill_blank / choice
- correct: true=对, false=错, null=未作答/看不清
- error_type 仅错题可填：careless / concept / calculation / method / reading
- difficulty: easy / medium / hard
- confidence: 0-1
"""


class ModelClient:
    """单个 provider 的视觉模型客户端。"""

    def __init__(self, provider: ProviderConfig, timeout: float = 45.0):
        self.provider = provider
        self.timeout = timeout

    def _chat(self, messages: list[dict], image_bytes: Optional[bytes] = None) -> str:
        """发 OpenAI 兼容的 chat 请求，返回模型文本输出。"""
        url = self.provider.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.provider.api_key}",
            "Content-Type": "application/json",
        }

        # 构造消息：如果有图片，用 image_url 格式（base64）
        final_messages = []
        for msg in messages:
            if image_bytes and msg["role"] == "user":
                b64 = base64.b64encode(image_bytes).decode("ascii")
                final_messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": msg["content"]},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                })
            else:
                final_messages.append(msg)

        payload = {
            "model": self.provider.model,
            "messages": final_messages,
            # 判题要尽量稳定：同一卷子多次分析结论应接近
            "temperature": 0,
            "top_p": 1,
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def analyze_image(self, image_bytes: bytes) -> list[dict]:
        """图转 md：返回提取的题目列表（dict 形式）。

        每项: {q_num, type, content, student_answer, difficulty}
        """
        text = self._chat(
            [{"role": "user", "content": EXTRACT_PROMPT}],
            image_bytes=image_bytes,
        )
        parsed = _extract_json(text)
        if not parsed or "questions" not in parsed:
            return []
        return parsed["questions"]

    def judge_questions(self, image_bytes: bytes) -> list[QuestionJudgement]:
        """判题：返回每题的判定。"""
        text = self._chat(
            [{"role": "user", "content": JUDGE_PROMPT}],
            image_bytes=image_bytes,
        )
        parsed = _extract_json(text)
        if not parsed or "judgements" not in parsed:
            return []
        results = []
        for j in parsed["judgements"]:
            results.append(QuestionJudgement(
                q_num=int(j.get("q_num", 0)),
                correct=j.get("correct"),
                error_type=j.get("error_type"),
                error_detail=j.get("error_detail"),
                difficulty=str(j.get("difficulty", "medium")),
                confidence=float(j.get("confidence", 0.8)),
                question_type=str(j.get("type", "calculation")),
            ))
        return results

    def extract_and_judge(self, image_bytes: bytes, grade: int = 4) -> list[QuestionJudgement]:
        """合并版：一次调用同时提取题目内容 + 判定对错。

        省一半调用时间/费用。返回的 QuestionJudgement 含 question_content。
        """
        knowledge = load_math_knowledge(grade)
        prompt = EXTRACT_AND_JUDGE_PROMPT
        if knowledge:
            prompt = EXTRACT_AND_JUDGE_PROMPT + "\n\n" + knowledge + "\n\n请结合以上教材要点批改，优先保证对错准确。"
        text = self._chat(
            [{"role": "user", "content": prompt}],
            image_bytes=image_bytes,
        )
        parsed = _extract_json(text)
        if not parsed or "questions" not in parsed:
            return []
        results = []
        for j in parsed["questions"]:
            results.append(QuestionJudgement(
                q_num=int(j.get("q_num", 0)),
                correct=j.get("correct"),
                error_type=j.get("error_type"),
                error_detail=j.get("error_detail"),
                difficulty=str(j.get("difficulty", "medium")),
                confidence=float(j.get("confidence", 0.8)),
                question_type=str(j.get("type", "calculation")),
                question_content=j.get("content", ""),
                student_answer=j.get("student_answer"),
            ))
        return results

    def chat_text(self, prompt: str) -> str:
        """纯文本对话（举一反三用）。"""
        return self._chat([{"role": "user", "content": prompt}])


# ---------------------------------------------------------------------------
# JSON 容错解析
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON（容忍 ```json 包裹和前后文字）。"""
    # 先尝试直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试提取 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { ... } 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# 图片压缩预处理
# ---------------------------------------------------------------------------

# 长边超过此值就缩放（视觉模型不需要超高分辨率，1024px 够识别作业）
_MAX_SIDE = 1024
_JPEG_QUALITY = 85


def compress_image(image_bytes: bytes) -> bytes:
    """压缩图片：长边缩到 1024px、转 JPEG quality=85。

    手机拍的作业照动辄 3-5MB、4000+ 像素，既费 token 又可能超模型限制。
    压缩后通常 100-300KB，识别效果不受影响，省钱省时。

    若 PIL 不可用或非图片，原样返回（不阻断流程）。
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes))

        # 转 RGB（去掉 alpha 通道，JPEG 不支持）
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # 长边缩放
        w, h = img.size
        if max(w, h) > _MAX_SIDE:
            ratio = _MAX_SIDE / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buf.getvalue()
    except Exception:
        # PIL 没装或图片损坏——原样返回，让模型自己处理
        return image_bytes
