"""摄影机语言：受 Stable Cinemetrics（arXiv 2509.26555）四类控制（Setup/Event/Lighting/Camera）启发，
把「镜头」拆成可下发的字段，而不是一句"镜头平稳"。

字段（每镜一份）：
    shot_size   景别：ELS 大远景 / LS 全景 / MS 中景 / CU 近景
    angle       机位：eye 平视 / low 仰拍 / high 俯拍
    move        运镜：static / push_in / pull_out / pan_left / pan_right / tilt_up / tilt_down / follow
    speed       速度：slow / medium / fast
    subject     主体动作（人物动作或环境微动）

规则（确定性）：
    场首镜 = LS + push_in + slow（建立镜头）
    场末镜 = CU + pull_out + slow（把观众交出去）
    场中镜 = MS/CU 交替，move 在 pan/tilt/follow 里轮换，相邻不重复
"""

from __future__ import annotations

SHOT_SIZE_TEXT = {"ELS": "大远景", "LS": "全景", "MS": "中景", "CU": "近景"}
ANGLE_TEXT = {"eye": "平视机位", "low": "轻微仰拍", "high": "轻微俯拍"}
MOVE_TEXT = {
    "static": "固定机位",
    "push_in": "缓慢推近",
    "pull_out": "缓慢拉远",
    "pan_left": "向左横摇",
    "pan_right": "向右横摇",
    "tilt_up": "向上摇",
    "tilt_down": "向下摇",
    "follow": "跟随主体移动",
}
SPEED_TEXT = {"slow": "慢速", "medium": "中速", "fast": "快速"}
MID_MOVES = ["pan_left", "pan_right", "tilt_up", "follow", "push_in"]


def plan_camera(n_shots: int, scene_no: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n_shots):
        if i == 0:
            cam = {"shot_size": "LS", "angle": "eye", "move": "push_in", "speed": "slow"}
        elif i == n_shots - 1 and n_shots > 1:
            cam = {"shot_size": "CU", "angle": "eye", "move": "pull_out", "speed": "slow"}
        else:
            cam = {
                "shot_size": "MS" if i % 2 else "CU",
                "angle": "eye" if i % 3 else "low",
                "move": MID_MOVES[(i + scene_no) % len(MID_MOVES)],
                "speed": "medium",
            }
        if out and cam["move"] == out[-1]["move"] and cam["move"] != "static":
            alt = [m for m in MID_MOVES if m != cam["move"]]
            cam["move"] = alt[(i + scene_no) % len(alt)]
        out.append(cam)
    return out


def subject_action(has_characters: bool) -> str:
    return ("人物自然动作：呼吸、眨眼、轻微重心转移；表情克制，不做夸张形变"
            if has_characters else
            "环境微动：雨丝、光影、纸张纹理缓慢变化；主体轮廓保持稳定")


def camera_sentence(cam: dict, has_characters: bool) -> str:
    return (f"镜头：{SHOT_SIZE_TEXT[cam['shot_size']]}、{ANGLE_TEXT[cam['angle']]}、"
            f"{MOVE_TEXT[cam['move']]}（{SPEED_TEXT[cam['speed']]}）；"
            f"{subject_action(has_characters)}；不做剪辑、不换机位。")


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
            lines.append(f"[镜头 {k + 1}] {camera_sentence(cams[k], has_chars)}")
        seg["prompt"] = (seg.get("prompt") or "").rstrip() + "\n" + "\n".join(lines)
        seg["camera_plan"] = cams
    return prompts
