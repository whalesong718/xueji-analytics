"""本地答案核算器。

对可计算的小学算术题做确定性校验，降低视觉模型误判。
只处理“能稳定解析”的题；解析不了就原样返回，交给模型结果。
"""

from __future__ import annotations

import logging
import re
from fractions import Fraction
from typing import Optional, Union

from engine.model_client import QuestionJudgement

logger = logging.getLogger(__name__)

_NUM = r"-?\d+(?:\.\d+)?"
_FRAC = rf"(?:{_NUM}\s*/\s*{_NUM}|{_NUM})"
_UNIT = r"(?:平方厘米|平方分米|平方米|立方厘米|立方分米|立方米|厘米|分米|米|千米|毫米|千克|克|吨|升|毫升|元|角|分|度|°|%|％|支|个|人|次|题|本|页|时|分|秒)?"


def _norm_text(s: str) -> str:
    if not s:
        return ""
    t = str(s)
    t = t.replace("×", "*").replace("x", "*").replace("X", "*").replace("·", "*")
    t = t.replace("÷", "/").replace(":", "/")
    t = t.replace("＝", "=").replace("（", "(").replace("）", ")")
    t = t.replace("．", ".").replace("。", ".")
    t = t.replace("＜", "<").replace("＞", ">").replace("≤", "<=").replace("≥", ">=")
    t = t.replace("≈", "=").replace("约等于", "=")
    t = t.replace("﹣", "-").replace("－", "-").replace("＋", "+")
    t = re.sub(r"\s+", "", t)
    return t


def _strip_units(s: str) -> str:
    t = _norm_text(s)
    t = re.sub(
        r"(平方厘米|平方分米|平方米|立方厘米|立方分米|立方米|厘米|分米|米|千米|毫米|"
        r"千克|克|吨|升|毫升|元|角|分|度|°|%|％|支|个|人|次|题|本|页)",
        "",
        t,
    )
    return t


def _parse_number_token(token: str) -> Optional[Union[float, Fraction]]:
    """解析数字/分数 token。"""
    if token is None:
        return None
    t = _strip_units(token)
    if not t:
        return None
    # 百分数
    if t.endswith("%") or t.endswith("％"):
        try:
            return float(t[:-1]) / 100.0
        except ValueError:
            return None
    # 分数 a/b
    if re.fullmatch(rf"{_NUM}/{_NUM}", t):
        a, b = t.split("/", 1)
        try:
            den = float(b)
            if abs(den) < 1e-12:
                return None
            # 优先 Fraction 精确比较
            if "." not in a and "." not in b:
                return Fraction(int(a), int(b))
            return float(a) / den
        except Exception:
            return None
    try:
        if "." in t:
            return float(t)
        return Fraction(int(t), 1)
    except Exception:
        return None


def _to_float(v: Union[float, Fraction, None]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _almost_equal(a: Union[float, Fraction], b: Union[float, Fraction], tol: float = 1e-6) -> bool:
    # Fraction 精确相等优先
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a == b
    fa, fb = _to_float(a), _to_float(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= tol * max(1.0, abs(fa), abs(fb))


def _extract_student_value(answer: str) -> Optional[Union[float, Fraction]]:
    """从学生作答提取可比对的值。"""
    t = _strip_units(answer)
    if not t:
        return None
    # 比较符号本身
    if t in {"<", ">", "=", "<=", ">=", "≠", "!="}:
        return None
    # 直接整段解析
    v = _parse_number_token(t)
    if v is not None:
        return v
    # 从文本里抓最后一个数字/分数
    fracs = re.findall(rf"{_NUM}/{_NUM}", t)
    if fracs:
        return _parse_number_token(fracs[-1])
    nums = re.findall(_NUM, t)
    if nums:
        return _parse_number_token(nums[-1])
    return None


def _safe_eval_expr(expr: str) -> Optional[Union[float, Fraction]]:
    """安全求值：仅数字与 + - * / ()。"""
    t = _norm_text(expr)
    t = _strip_units(t)
    # 去掉尾部 =? / =□ / =
    t = re.sub(r"[=?？_□]+$", "", t)
    if not t or not re.fullmatch(r"[0-9+\-*/().]+", t):
        return None
    # 数字匹配不要带可选负号：否则 3-4 会被吃成 3 和 -4，运算符丢失
    num_pat = r"\d+(?:\.\d+)?"
    try:
        def _repl_num(m: re.Match) -> str:
            s = m.group(0)
            if "." in s:
                return f"Fraction('{s}')"
            return f"Fraction({s})"

        code = re.sub(num_pat, _repl_num, t)
        val = eval(code, {"__builtins__": {}}, {"Fraction": Fraction})  # noqa: S307
        if isinstance(val, Fraction):
            return val
        return float(val)
    except Exception:
        return None


def _extract_expr_from_content(content: str) -> Optional[str]:
    """从题目中提取可计算表达式。"""
    t = _norm_text(content)
    t = _strip_units(t)
    # 去掉中文提示词
    t = re.sub(r"^(计算|直接写出得数|列式计算|口算|脱式计算)[:：]?", "", t)

    candidates = []
    # a?b=? / a?b= / a?b
    for m in re.finditer(r"([0-9+\-*/().]{3,})=\?", t):
        candidates.append(m.group(1))
    for m in re.finditer(r"([0-9+\-*/().]{3,})=", t):
        # 若等号后已有答案数字，表达式仍取左侧
        candidates.append(m.group(1))
    for m in re.finditer(r"([0-9+\-*/().]{3,})", t):
        candidates.append(m.group(1))

    # 选最长且能求值的
    candidates = sorted(set(candidates), key=len, reverse=True)
    for c in candidates:
        # 至少包含一个运算符
        if not re.search(r"[+\-*/]", c):
            continue
        if _safe_eval_expr(c) is not None:
            return c
    return None


def _extract_answer_from_content(content: str) -> Optional[str]:
    """若题目文本自带完整算式 a?b=c，可把 c 当学生答案补全。"""
    t = _norm_text(content)
    m = re.search(rf"([0-9+\-*/().]+)=({_FRAC}){_UNIT}$", t)
    if m:
        return m.group(2)
    m = re.search(rf"=({_FRAC}){_UNIT}$", t)
    if m:
        return m.group(1)
    return None


def _check_compare(content: str, answer: str) -> Optional[bool]:
    """比较大小：12○15 / 12()15 / 比较：3/4 和 2/3。"""
    t = _norm_text(content)
    ans = _strip_units(answer)
    ans = ans.replace("○", "").replace("()", "").replace("（）", "")
    # 标准化学生比较符
    ans_map = {
        "小于": "<", "大于": ">", "等于": "=",
        "＜": "<", "＞": ">", "＝": "=",
        "≠": "!=", "不等于": "!=",
    }
    ans = ans_map.get(ans, ans)
    if ans not in {"<", ">", "=", "<=", ">=", "!="}:
        # 允许学生直接写符号夹在数字中：12<15
        m = re.fullmatch(rf"({_FRAC})([<>]=?|=|!=)({_FRAC})", ans)
        if m:
            ans = m.group(2)
        else:
            return None

    # 12○15 / 12()15 / 12?15
    m = re.search(rf"({_FRAC})[○()（）?？_□]+({_FRAC})", t)
    if not m:
        m = re.search(rf"比较.*?({_FRAC}).*?({_FRAC})", t)
    if not m:
        return None
    left = _parse_number_token(m.group(1))
    right = _parse_number_token(m.group(2))
    if left is None or right is None:
        return None
    lf, rf = _to_float(left), _to_float(right)
    if lf is None or rf is None:
        return None
    expected = "=" if abs(lf - rf) <= 1e-9 else ("<" if lf < rf else ">")
    # 学生写 <= / >= 时，若实际是 = 也算对；若实际严格不等则仅匹配对应方向
    if ans == expected:
        return True
    if ans == "<=" and expected in {"<", "="}:
        return True
    if ans == ">=" and expected in {">", "="}:
        return True
    return False


def _check_triangle_angle(content: str, answer: str) -> Optional[bool]:
    """三角形内角和补角：已知两角求第三角。"""
    t = _norm_text(content)
    if "三角" not in content and "内角" not in content and "角" not in content:
        # 仍尝试匹配“两个角分别是”
        if "角" not in content:
            return None
    angles = [float(x) for x in re.findall(_NUM, t)]
    # 需要至少两个已知角，且提到 180 或 第三个角
    if len(angles) < 2:
        return None
    if not re.search(r"第三|另一个|其余|还|多少度|内角和", content):
        return None
    # 取前两个作为已知角（常见出题）
    a, b = angles[0], angles[1]
    if a + b >= 180:
        return None
    expected = 180.0 - a - b
    stu = _extract_student_value(answer)
    if stu is None:
        return None
    return _almost_equal(expected, stu)


def _check_average(content: str, answer: str) -> Optional[bool]:
    """简单平均数：求平均数 / 平均分。"""
    if "平均" not in content:
        return None
    t = _norm_text(content)
    nums = [float(x) for x in re.findall(_NUM, t)]
    if len(nums) < 2:
        return None
    stu = _extract_student_value(answer)
    if stu is None:
        return None

    # 平均分给 n 人：总数在前，人数在后
    m = re.search(rf"({_NUM}).{{0,12}}平均分给({_NUM})", t)
    if m:
        total = float(m.group(1))
        n = float(m.group(2))
        if abs(n) < 1e-12:
            return None
        return _almost_equal(total / n, stu)

    # 求 a,b,c 的平均数
    if "平均数" in content or "平均是" in content or "平均为" in content:
        # 去掉最后可能的干扰数
        vals = nums
        if len(vals) >= 2:
            expected = sum(vals) / len(vals)
            # 若题目里写了“平均分给k人”已处理；这里用全部数字可能含杂质，保守：仅 2-6 个数
            if 2 <= len(vals) <= 6:
                return _almost_equal(expected, stu)
    return None


def _check_perimeter_area(content: str, answer: str) -> Optional[bool]:
    """长方形/正方形周长面积、圆（π=3.14）。"""
    t = _norm_text(content)
    stu = _extract_student_value(answer)
    if stu is None:
        return None
    nums = [float(x) for x in re.findall(_NUM, t)]

    # 正方形边长
    m = re.search(rf"正方形.{{0,8}}边长.{{0,4}}({_NUM})", t)
    if m:
        side = float(m.group(1))
        if "周长" in content:
            return _almost_equal(side * 4, stu)
        if "面积" in content:
            return _almost_equal(side * side, stu)

    # 长方形 长/宽
    m = re.search(rf"(?:长|长为|长是)({_NUM}).{{0,12}}(?:宽|宽为|宽是)({_NUM})", t)
    if not m:
        m = re.search(rf"({_NUM}).{{0,4}}[×*]({_NUM}).{{0,8}}(?:长方形|长方体)?", t)
    if m and ("长方形" in content or "长方" in content or "周长" in content or "面积" in content):
        a, b = float(m.group(1)), float(m.group(2))
        if "周长" in content:
            return _almost_equal(2 * (a + b), stu)
        if "面积" in content:
            return _almost_equal(a * b, stu)

    # 圆 r / d，π=3.14
    if "圆" in content and ("周长" in content or "面积" in content):
        pi = 3.14
        if re.search(r"π\s*=\s*3\.14|pi\s*=\s*3\.14|π取3\.14", t, re.I):
            pi = 3.14
        r = None
        m = re.search(rf"半径.{{0,4}}({_NUM})", t)
        if m:
            r = float(m.group(1))
        m = re.search(rf"直径.{{0,4}}({_NUM})", t)
        if m:
            r = float(m.group(1)) / 2.0
        if r is not None:
            if "周长" in content:
                return _almost_equal(2 * pi * r, stu) or _almost_equal(pi * (2 * r), stu)
            if "面积" in content:
                return _almost_equal(pi * r * r, stu)
    return None


def local_check_judgement(j: QuestionJudgement) -> QuestionJudgement:
    """对单题做本地核算；无法核算时返回原结果。"""
    content = j.question_content or ""
    answer = j.student_answer or ""

    # 若模型没抽出作答，但题目文本自带 =答案，尝试补全
    if not str(answer).strip():
        embedded = _extract_answer_from_content(content)
        if embedded:
            answer = embedded
            j.student_answer = embedded
        else:
            # 真无作答
            j.correct = None
            j.error_type = None
            j.error_detail = None
            j.confidence = max(float(j.confidence or 0), 0.9)
            return j

    # 1) 比较大小
    cmp_res = _check_compare(content, answer)
    if cmp_res is not None:
        return _apply_bool(j, cmp_res, "比较符号不正确")

    # 2) 三角形补角
    tri = _check_triangle_angle(content, answer)
    if tri is not None:
        return _apply_bool(j, tri, "三角形内角和应为180度")

    # 3) 平均数 / 平均分
    avg = _check_average(content, answer)
    if avg is not None:
        return _apply_bool(j, avg, "平均数量计算不正确")

    # 4) 周长面积
    geo = _check_perimeter_area(content, answer)
    if geo is not None:
        return _apply_bool(j, geo, "图形公式计算不正确")

    # 5) 通用表达式求值（含多步、括号、小数）
    expr = _extract_expr_from_content(content)
    stu = _extract_student_value(answer)
    if expr and stu is not None:
        expected = _safe_eval_expr(expr)
        if expected is not None:
            ok = _almost_equal(expected, stu)
            # 分数题：学生写小数也接受近似
            if not ok:
                ef, sf = _to_float(expected), _to_float(stu)
                if ef is not None and sf is not None:
                    ok = _almost_equal(ef, sf, tol=1e-4)
            detail = f"计算结果应为 {_fmt(expected)}，学生作答 {_fmt(stu)}"
            return _apply_bool(j, ok, detail)

    return j


def _fmt(v: Union[float, Fraction]) -> str:
    if isinstance(v, Fraction):
        if v.denominator == 1:
            return str(v.numerator)
        return f"{v.numerator}/{v.denominator}"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:g}"


def _apply_bool(j: QuestionJudgement, ok: bool, wrong_detail: str) -> QuestionJudgement:
    if ok:
        j.correct = True
        j.error_type = None
        j.error_detail = None
        j.confidence = max(float(j.confidence or 0), 0.98)
        logger.info("本地核算: 题%d 判对", j.q_num)
    else:
        j.correct = False
        j.error_type = j.error_type or "calculation"
        j.error_detail = j.error_detail or wrong_detail
        j.confidence = max(float(j.confidence or 0), 0.95)
        logger.info("本地核算: 题%d 判错 (%s)", j.q_num, j.error_detail)
    return j


def apply_local_checks(judgements: list[QuestionJudgement]) -> list[QuestionJudgement]:
    return [local_check_judgement(j) for j in judgements]
