"""摄影机语言：把「镜头」拆成可下发的结构化字段。

字段（每镜一份）：
    shot_size   景别：ELS 大远景 / LS 全景 / MS 中景 / CU 近景
    angle       机位：eye 平视 / low 仰拍 / high 俯拍
    move        运镜：固定 static（本 skill 只允许这一种）
    speed       速度：slow
    subject     主体动作（人物动作或环境动态）

铁律（与 picturebook-pipeline 一致）：
    1. 机位锁定：不推近、不拉远、不摇、不移、不变焦，画面主体尺寸与位置保持不变。
       景别只在不同镜头之间变化（建立镜 LS → 中景 → 收尾 CU），镜头内部恒定，
       避免 Seedance 把"景别变化"理解成"一路 zoom"。
    2. 动作不锁：人物动作必须完整、可见、按正常速度完成，不允许只写呼吸/眨眼/微动。

2026-11-25 团队反馈：旧默认句"呼吸、眨眼、轻微重心转移"导致视频几乎不动。
本版本把动作改成"完整可见"，并允许调用方传入本镜的具体动作（action），
由 build_prompts 从本页正文提取。

注意：本 skill 仍是**固定机位**旧路径；需要"有动机的物理运镜"时走
skills/picturebook-one-take（物理移动 + 数字 zoom 门禁）。
"""

from __future__ import annotations

SHOT_SIZE_TEXT = {"ELS": "大远景", "LS": "全景", "MS": "中景", "CU": "近景"}
ANGLE_TEXT = {"eye": "平视机位", "low": "轻微仰拍", "high": "轻微俯拍"}
MOVE_TEXT = {"static": "固定机位"}
SPEED_TEXT = {"slow": "慢速", "medium": "中速", "fast": "快速"}

# 机位锁定句：所有镜头共用，禁止推拉摇移与变焦
CAMERA_LOCK_SENTENCE = (
    "固定机位，全程不推近、不拉远、不摇、不移、不变焦；"
    "画面主体与元素的尺寸、位置保持不变。"
)


def plan_camera(n_shots: int, scene_no: int) -> list[dict]:
    """给一场内的 n_shots 个镜头分配景别与角度；运镜恒为 static。"""
    if n_shots <= 1:
        return [{"shot_size": "LS" if scene_no == 1 else "MS",
                 "angle": "eye" if scene_no % 3 else "low",
                 "move": "static",
                 "speed": "slow"}]
    out: list[dict] = []
    for i in range(n_shots):
        if i == 0:
            cam = {"shot_size": "LS", "angle": "eye", "move": "static", "speed": "slow"}
        elif i == n_shots - 1:
            cam = {"shot_size": "CU", "angle": "eye", "move": "static", "speed": "slow"}
        else:
            cam = {
                "shot_size": "MS" if i % 2 else "CU",
                "angle": ("eye", "low", "high")[(i + scene_no) % 3],
                "move": "static",
                "speed": "slow",
            }
        if out and cam["shot_size"] == out[-1]["shot_size"]:
            cam["shot_size"] = "CU" if cam["shot_size"] == "MS" else "MS"
        out.append(cam)
    return out


def subject_action(has_characters: bool, action: str = "") -> str:
    if has_characters:
        base = ("人物动作完整可见：肢体、躯干与表情都有明显幅度，动作在镜头内完成；"
                "正常速度，不放慢、不冻结、不只做呼吸眨眼。")
        if action:
            base += f"本镜动作：{action}。"
        return base
    return ("环境动态可见（风、光、雨丝、衣角等），但主体轮廓、尺寸与位置保持稳定；"
            "不整体缩放、不平移画面。")


def camera_sentence(cam: dict, has_characters: bool, action: str = "") -> str:
    return (f"镜头：{SHOT_SIZE_TEXT[cam['shot_size']]}、{ANGLE_TEXT[cam['angle']]}、"
            f"{CAMERA_LOCK_SENTENCE}"
            f"{subject_action(has_characters, action)}"
            f"不做剪辑、不切机位。")


def enrich(prompts: dict, scene_characters: dict | None = None) -> dict:
    """把 camera 写进每个 shot，并把摄影机句子拼进该场景 prompt。"""
    scene_characters = scene_characters or {}
    for si, seg in enumerate(prompts.get("segments") or []):
        shots = seg.get("shots") or []
        if not shots:
            shots = [{"kind": "single", "pages": seg.get("pages"),
                      "duration_sec": seg.get("duration_sec")}]
            seg["shots"] = shots
        cams = plan_camera(len(shots), si + 1)
        has_chars = bool(scene_characters.get(seg.get("scene_id")))
        lines = []
        for k, shot in enumerate(shots):
            shot["camera"] = cams[k]
            action = shot.get("action") or seg.get("action") or ""
            lines.append(f"[镜头 {k + 1}] {camera_sentence(cams[k], has_chars, action)}")
        seg["prompt"] = (seg.get("prompt") or "").rstrip() + "\n" + "\n".join(lines)
        seg["camera_plan"] = cams
    return prompts
