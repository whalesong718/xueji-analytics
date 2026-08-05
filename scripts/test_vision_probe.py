"""视觉能力探针 — 测试你的模型 API 能不能接收并理解图片。

用法（在项目根目录）:
  1. 设置环境变量（填你的真实 key）:
     # Git Bash
     export VISION_API_KEY="你的讯飞 APIPassword 或 APIKey:APISecret"
     # PowerShell
     $env:VISION_API_KEY="你的讯飞 APIPassword 或 APIKey:APISecret"

  2. 运行（传一张作业照片路径）:
     python scripts/test_vision_probe.py "C:/path/to/作业照片.jpg"

它会:
  - 把图片发给模型，问"这张图里有什么"
  - 打印模型返回的原文
  - 判定: ✅支持视觉 / ❌不支持(纯文本或报错)

如果报 401/403 → key 或 auth 格式不对
如果报 model not found → 改下面的 MODEL 名
如果模型说"我看不到图/我不支持图片" → 纯文本模型，整条链第一步就跑不了
"""

import os
import sys
import base64
import httpx

# ============ 在这里改你的配置 ============
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 通义千问 OpenAI 兼容端点
MODEL = "qwen-vl-plus"                                            # 通义视觉模型(plus 较便宜)
API_KEY = os.environ.get("VISION_API_KEY", "")                   # 从环境变量读，不写死
# =========================================

PROMPT = "请描述这张图片里的内容。如果这是一份作业或试卷，请说出你看到了什么题目和作答。"


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/test_vision_probe.py <图片路径>")
        sys.exit(1)

    img_path = sys.argv[1]
    if not os.path.exists(img_path):
        print(f"❌ 图片不存在: {img_path}")
        sys.exit(1)

    if not API_KEY:
        print("❌ 未设置 API key。请先 set/export VISION_API_KEY")
        sys.exit(1)

    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    url = BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"端点: {url}")
    print(f"模型: {MODEL}")
    print(f"图片: {img_path} ({os.path.getsize(img_path)} bytes)")
    print(f"key:  {API_KEY[:6]}...{API_KEY[-4:]}")
    print("-" * 50)

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"❌ 请求失败(网络/超时): {e}")
        sys.exit(1)

    print(f"HTTP {resp.status_code}")

    if resp.status_code != 200:
        print("❌ 响应非 200，原文:")
        print(resp.text[:1000])
        if resp.status_code in (401, 403):
            print("\n💡 鉴权失败。讯飞 OpenAI 兼容接口的 key 通常是 'APIPassword'，")
            print("   在讯飞控制台生成。格式可能是单独一串，也可能是 'APIKey:APISecret'。")
        sys.exit(1)

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        print("❌ 返回结构异常，完整响应:")
        print(data)
        sys.exit(1)

    print("✅ 模型返回内容:")
    print("-" * 50)
    print(content)
    print("-" * 50)

    # 简单判定
    lower = content.lower()
    bad_signals = ["无法", "不支持", "不能", "看不到", "unable", "cannot", "don't support", "no image"]
    if any(s in lower for s in bad_signals) and len(content) < 60:
        print("\n⚠️  模型可能不支持图片输入(返回了拒绝/空话)。")
        print("   结论: 这是纯文本模型，不能用做拍照识别。需要换真·视觉模型。")
    else:
        print("\n✅ 模型似乎能理解图片! 可以用于视觉判题链路。")


if __name__ == "__main__":
    main()
