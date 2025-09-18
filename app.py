import os
import logging
from typing import Dict, Any, List

from flask import Flask, request, jsonify
import requests

# ====== 环境变量 ======
# 你的 Label Studio 实例地址（不要以 / 结尾）
# 例：LS_URL="https://your-ls-space.hf.space"
LS_URL = os.environ.get("LS_URL", "").rstrip("/")
# 在 Label Studio 用户设置里生成的 API Token
LS_API_KEY = os.environ.get("LS_API_KEY", "")

# Tab1 扣分表（与界面 Choice value 一致；注意 &amp; 和 & 的兼容）
DEDUCTIONS = {
    "Report Generation / Usability Error": 100,
    "Financial Accuracy Error": 15,
    "Business & Moat Understanding Error": 12,
    "Market & Event Accuracy Error": 12,
    "Recommendation & Debate Quality Error": 10,
    "Temporal Accuracy Error": 10,
    "Source & Evidence Coverage Error": 8,
    "Instruction Compliance Error": 8,
    "Internal Consistency Error": 6,
    "Presentation & Clarity Error": 4,
    "Language & Professionalism Error": 2,
}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autoscore")


@app.get("/health")
def health():
    ok = bool(LS_URL and LS_API_KEY)
    return jsonify({"ok": ok, "ls_url": bool(LS_URL), "ls_api_key": bool(LS_API_KEY)}), (200 if ok else 500)


def extract_selected_errors(annotation: Dict[str, Any]) -> List[str]:
    """从 annotation.result 中提取被勾选的错误类别（from_name == 'errors'）"""
    selected: List[str] = []
    for r in annotation.get("result", []):
        if r.get("type") == "choices" and r.get("from_name") == "errors":
            choices = r.get("value", {}).get("choices", []) or []
            selected.extend(choices)
    # 兼容 &amp;
    selected = [c.replace("&amp;", "&") for c in selected]
    return selected


def compute_score(selected: List[str]) -> int:
    total_deduction = sum(DEDUCTIONS.get(x, 0) for x in selected)
    return max(0, 100 - total_deduction)


def patch_annotation_score(annotation_id: int, new_result: List[Dict[str, Any]]) -> None:
    """调用 Label Studio API 更新 annotation 的 result"""
    if not (LS_URL and LS_API_KEY):
        raise RuntimeError("LS_URL or LS_API_KEY not configured")

    url = f"{LS_URL}/api/annotations/{annotation_id}"
    headers = {"Authorization": f"Token {LS_API_KEY}"}
    resp = requests.patch(url, headers=headers, json={"result": new_result}, timeout=30)
    resp.raise_for_status()


@app.post("/webhook")
def webhook():
    """
    Label Studio Webhook 接收端：
    建议在 LS 项目里选中 ANNOTATION_CREATED 和 ANNOTATION_UPDATED 两个事件。
    """
    data = request.json or {}
    action = data.get("action")
    annotation = data.get("annotation") or {}

    # 仅处理 annotation 相关事件
    if action not in {"ANNOTATION_CREATED", "ANNOTATION_UPDATED"}:
        return jsonify({"ok": True, "ignored": action})

    annotation_id = annotation.get("id")
    if not annotation_id:
        return jsonify({"ok": False, "error": "missing annotation id"}), 400

    # 1) 读取选择
    selected = extract_selected_errors(annotation)
    # 2) 计算分数
    score = compute_score(selected)
    logger.info(f"annotation {annotation_id} selected={selected} score={score}")

    # 3) 组装新的 result ：把 final_score 写回（from_name 要与界面 Number name 匹配）
    results = annotation.get("result", []) or []
    filtered = [r for r in results if r.get("from_name") != "final_score"]
    filtered.append({
        "from_name": "final_score",
        "to_name": "text",      # 对应你的 <Text name="text" .../>
        "type": "number",
        "value": {"number": score}
    })

    # 4) 调用 LS API 写回
    try:
        patch_annotation_score(annotation_id, filtered)
    except Exception as e:
        logger.exception("patch annotation failed")
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "score": score})
    

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
