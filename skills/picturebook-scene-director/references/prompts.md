# Seedance 分镜提示词模板（8 套，≤ 1000 字符）

**规则**

1. 绝不覆盖上游 `seedance_prompt.txt`。模板产出到独立的 `prompts/seg_###_prompt.txt`。
2. 结构固定五段：`[Camera]` / `[Subject Motion]` / `[Environment & Style]` / `[Transition/Continuity Lock]` / `[Negative Constraints]`。Seedance 对分段标签的遵循度高于长散文。
3. 连续性与结构锁定词**放在靠前**；超长时先删形容词，后删环境描写，**永远不删锁定词与负向块**。
4. 一个镜头只允许**一条运镜 + 一条动作弧**。两个动作写两段，不要在一段里塞。
5. 参数后缀由执行层追加，模板本身**不含** `--resolution` 等：
   ` --resolution {480p|720p|1080p} --ratio {ratio} --duration {5|10} --seed {n} --watermark false`

占位符：`{camera_motion}` 运镜短语；`{action}` 单动作；`{environment_motion}` 环境微动（风/光/尘）；`{style_dna_summary}` 从上游 manifest 复制的风格 DNA 一句话；`{screen_direction}` `left-to-right` / `right-to-left` / `toward camera` / `away from camera`。

---

## 1. 同镜头双锚定 `CONTINUOUS_DUAL_ANCHOR`

```text
[Camera]: {camera_motion}, maintaining steady perspective and exact framing alignment from start frame to end frame.
[Subject Motion]: {single_continuous_action_arc}, moving smoothly across the full duration without rushing or freezing.
[Environment & Style]: {environment_motion}, preserving {style_dna_summary} brushwork, line weight, and static paper grain.
[Transition/Continuity Lock]: Continuous single-shot interpolation between start and end composition; maintain rigid anatomical proportions and consistent scale without morphing.
[Negative Constraints]: NO text, NO subtitles, NO page numbers, NO watermarks, NO scene cuts, NO limb distortion, NO flickering.
```

## 2. 同镜头单锚定续接 `CONTINUOUS_OPEN_END`

用于同场景长动作被拆成多段时的非末段。

```text
[Camera]: {camera_motion}, keeping smooth continuous momentum without abrupt stops.
[Subject Motion]: {ongoing_action_part1}, advancing steadily through the frame while maintaining exact character proportions and costume colors.
[Environment & Style]: {environment_motion}, preserving {style_dna_summary} hand-painted texture and lighting cohesion.
[Transition/Continuity Lock]: Hold fluid mid-action trajectory cleanly toward the end of the clip so it connects seamlessly into the next beat.
[Negative Constraints]: NO text, NO subtitles, NO page numbers, NO watermarks, NO sudden zoom jump, NO extra characters.
```

## 3. 硬切出点：动作峰值 `HARD_CUT_OUT`

```text
[Camera]: {camera_motion}, framing the character clearly as kinetic energy builds.
[Subject Motion]: {action_leading_to_peak}, accelerating smoothly into peak motion velocity right at the final frames without settling back to rest.
[Environment & Style]: {environment_motion}, matching {style_dna_summary} texture, palette, and lighting direction.
[Transition/Continuity Lock]: End cleanly at maximum motion velocity and clear screen direction ({screen_direction}) for a seamless cut on action.
[Negative Constraints]: NO text, NO subtitles, NO watermarks, NO slow-motion freeze at the end, NO facial distortion.
```

## 4. 硬切入点：动量延续 `HARD_CUT_IN`

```text
[Camera]: {new_angle_or_shot_scale_camera}, preserving the established 180-degree screen axis.
[Subject Motion]: Beginning already in mid-motion ({action_follow_through}), carrying forward the exact momentum and direction ({screen_direction}) before settling naturally.
[Environment & Style]: {environment_motion}, strictly consistent with {style_dna_summary} medium, line thickness, and paper grain.
[Transition/Continuity Lock]: Immediate kinetic continuity from frame zero; preserve identical character identity and lighting angle.
[Negative Constraints]: NO text, NO subtitles, NO watermarks, NO static pause at start, NO axis flip, NO morphing.
```

## 5. 匹配剪辑 `MATCH_CUT`

```text
[Camera]: {matched_camera_motion}, locking the key visual motif ({matched_shape_or_object}) at {normalized_screen_coords}.
[Subject Motion]: {action_matching_geometry_or_trajectory}, echoing the silhouette and screen trajectory across the cut point.
[Environment & Style]: {environment_motion}, rendered in authentic {style_dna_summary} artistic medium with cohesive color harmony.
[Transition/Continuity Lock]: Maintain strict compositional geometry and focal alignment on {matched_shape_or_object} so the visual form bridges the scene change effortlessly.
[Negative Constraints]: NO text, NO captions, NO watermarks, NO jarring camera shake, NO sudden scale distortion.
```

## 6A. 共享 T 入：甩镜/遮挡的上半段 `SHARED_T_IN`

```text
[Camera]: Rapid lateral {pan_direction} camera sweep, building smooth directional motion blur or foreground occlusion toward the final frame.
[Subject Motion]: {subject_initial_motion} before the foreground/motion sweeps across.
[Environment & Style]: Elements streak naturally in {style_dna_summary} pigment until the frame resolves cleanly into the target bridge frame.
[Transition/Continuity Lock]: Accelerate lateral optical flow smoothly into the final frame without warping character anatomy beforehand.
[Negative Constraints]: NO text, NO subtitles, NO watermarks, NO bounce-back motion, NO digital glitch artifacts.
```

## 6B. 共享 T 出：甩镜/遮挡的下半段 `SHARED_T_OUT`

```text
[Camera]: Starting from directional blur/foreground occlusion in frame zero, rapidly sweep {pan_direction} and decelerate smoothly to settle on the new scene.
[Subject Motion]: Reveal {new_subject_action} naturally as the camera locks onto the composition.
[Environment & Style]: Blur clears smoothly into crisp {style_dna_summary} brushwork, line work, and static paper grain.
[Transition/Continuity Lock]: Match incoming lateral motion vector from frame zero and stabilize cleanly without overshoot.
[Negative Constraints]: NO text, NO subtitles, NO watermarks, NO double exposure ghosting, NO morphing.
```

## 7. 黑场/白场共享 T `SHARED_T_FADE`

```text
[Camera]: Gentle, unhurried {camera_motion}, pacing the emotional transition.
[Subject Motion]: {gentle_subject_motion}, slowing softly as luminance shifts.
[Environment & Style]: {environment_motion} in {style_dna_summary} medium; lighting smoothly {dims to black / brightens to white} over the final 0.8 seconds to match the target bridge frame cleanly.
[Transition/Continuity Lock]: Smooth, uniform luminance transition without color banding or abrupt flicker.
[Negative Constraints]: NO text, NO credits, NO subtitles, NO watermarks, NO harsh strobe flashes.
```

## 8. 独立桥接镜头 `BRIDGE_INSERT_SHOT`

```text
[Camera]: Steady close-up insert framing, subtle micro-push-in.
[Subject Motion]: {simple_single_bridge_action}, simple and clearly readable within 2 seconds.
[Environment & Style]: {ambient_detail}, strictly matching {style_dna_summary} hand-painted pigment, pencil outlines, and paper grain.
[Transition/Continuity Lock]: Keep single focal object centered and structurally rigid; maintain exact color palette continuity with adjacent scenes.
[Negative Constraints]: NO text, NO labels, NO watermarks, NO extra characters, NO photorealistic 3D rendering.
```

---

## 组装时的三个检查

1. **字符数**：五段加起来 ≤ 1000（脚本按 950 预算，留 50 给后缀）。超了先删 `[Environment & Style]` 里的装饰性形容词。
2. **动作唯一**：`[Subject Motion]` 里出现两个 `and` 连接的独立动作 = 超载，拆段。
3. **负向块不重复**：`NO text` 只在负向块出现一次；片段之间不要拼接两套负向块。
