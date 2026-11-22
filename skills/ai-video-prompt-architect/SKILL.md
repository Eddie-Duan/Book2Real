---
name: ai-video-prompt-architect
description: Generate structured, high-consistency prompts for AI text-to-video (T2V) and image-to-video (I2V) models such as Sora, Kling, Runway Gen-3, Veo, Luma, Hailuo, and Seedance. Use when writing video prompts, converting a rough idea, script, or reference image into generation prompts, building multi-shot storyboards, or keeping character identity, art style, lighting, and camera language consistent across clips.
whenToUse: The user asks for AI video prompts, a shot list, a storyboard, a T2V or I2V sequence, or consistency of a character or style across generated clips.
---

# AI Video Prompt Architect

Produce production-grade prompts for AI video generators that hold subject identity, art direction, physical plausibility, and camera language across every shot.

## Output contract

Apply to every response unless the user overrides it.

- **Prompt language: English.** It is the native control language of every major T2V/I2V model. Explanations may be in the user's language.
- **Both forms.** Give each shot as a bracketed structured block *and* as a single-paragraph merged prompt for tools that do not accept tags.
- **Anchors verbatim.** Repeat the subject, style, and lighting anchors word-for-word in every shot. Never swap in a pronoun ("he", "she", "it") without re-anchoring the key visual traits in the same sentence.
- **One beat per clip.** One primary action + one camera move per 5–10 seconds. Split plot beats into separate shots instead of cramming them.
- **State assumptions** (platform, aspect ratio, duration, shot count) in one line up front. Infer strong cinematic defaults from a vague one-liner rather than stalling.
- **Chinese notes per shot** (or the user's language): 2–5 bullets on camera logic, motion timing, and which anchors were locked.

## Core principles

### 1. Anchor tokenization (the consistency engine)

Define each immutable anchor once, then reuse the exact same string in every prompt:

- **Subject anchor** — age, build, face shape, eyes, hair (length, color, texture, parting), skin details, scars/marks, outfit layers with fabric and color, footwear, props held.
- **Style & medium anchor** — film stock or render engine, lens family, color grade, contrast, grain, aspect ratio, quality tags.
- **Environment & lighting anchor** — architecture and materials, time of day, practical sources, key-light direction, color temperature of fill, atmosphere (dust, rain, volumetric fog, haze).

Example string to reuse verbatim: `a 29-year-old Ethiopian woman, close-cropped curly hair with a faint shaved line on the left temple, a thin silver hoop in the right ear, wearing a sand-colored linen work shirt with rolled sleeves and a canvas tool belt`.

### 2. T2V vs I2V separation

- **T2V** must build the whole frame: `[Style & Medium] + [Subject Anchor] + [Action & Timeline] + [Environment & Lighting] + [Camera]`.
- **I2V** already has the frame locked by the input image. Do **not** re-describe static visuals that could contradict the reference — spend ~80% of the prompt on motion vectors, temporal progression, micro-expressions, secondary physics (hair, fabric, particles, water), and camera trajectory. Never restate colors or clothing the image already shows; reinforce style continuity instead.

### 3. Temporal & physical plausibility

Write motion as a timeline inside the clip: **start state → action → end state**. Name the physics that make it believable: weight and momentum, ground contact, wind response, cloth drag, water displacement and ripple direction, dust kicked up, breath and blinking rate. Keep speed natural — real-time unless the user asks for slow motion or time-lapse.

### 4. Cinematic camera grammar

Always specify: shot size (wide / medium / close-up / extreme close-up), angle (eye level, low, high, over-the-shoulder, Dutch), movement and speed (static, slow dolly-in, tracking left, crane up, handheld breathing), lens (focal length, anamorphic vs spherical), and depth of field. One movement per shot; a cut or a second movement causes warping.

## Workflow

### Step 1 — Triage the input

Identify: mode (T2V vs I2V), target model if named (each has quirks: Kling favors natural-language paragraphs, Runway favors concise motion, Veo favors detailed cinematography), aspect ratio, clip length, and single shot vs sequence. Ask only when a wrong guess would waste the user's generation budget; otherwise infer and label the assumptions.

### Step 2 — Build the Consistency Bible (multi-shot or complex scenes)

Before writing shot prompts, emit a short locked block with the three anchors above plus: character wardrobe continuity notes, location geography (where the door, window, and light source sit), time-of-day progression, and the grade LUT / palette swatches. This block is the contract every shot must match.

### Step 3 — Draft structured prompts

**Template A — Text-to-Video (T2V)**

```
[Style & Medium]: {art style, film stock or render engine, palette, grain, quality tags}
[Subject]: {verbatim subject anchor}
[Action & Timeline]: {start state -> action -> end state over 5s, with physics}
[Environment & Lighting]: {verbatim environment & lighting anchor}
[Camera]: {shot size, angle, movement and speed, lens, depth of field}
```

**Template B — Image-to-Video (I2V)**

```
[Subject Motion]: {body movement, facial micro-expression, gaze shift, timed beats}
[Environment & Secondary Physics]: {wind in hair and fabric, flickering light, background motion, particles}
[Camera Trajectory]: {push-in, pan, orbit, handheld drift — relative to the starting frame}
[Style & Continuity Lock]: {maintain exact character appearance, lighting, and art style of the reference frame; smooth natural motion; 24fps}
```

Then add the merged prompt: one paragraph, 60–120 words, same content, ordered style → subject → action → environment → camera.

### Step 4 — Harden the prompt

- Append the applicable negative block (below).
- For sequences, pair every shot with the shot it cuts from: match screen direction, eyeline, and light side.
- Check the delivery checklist at the end.

## Multi-shot continuity

- **Frame chaining:** generate Shot N, export its final frame, and use it as the I2V input for Shot N+1. Prompt only the delta (small motion + camera move) so identity does not drift.
- **Anchor repetition:** paste the three anchor strings into every shot prompt; do not paraphrase them between shots.
- **Edit pairs:** for dialogue or reaction shots, keep the camera on one side of the 180° line and the same light direction; specify "same lighting as previous shot".
- **Grade consistency:** hold one LUT/palette per sequence; never change film stock mid-sequence.
- **Beat budget:** if a sequence exceeds ~8 shots, group shots into scenes and restate the bible at each scene boundary.

## Negative prompt / anti-artifact block

```
morphing, distorted limbs, extra fingers, extra limbs, flickering textures, sudden jump cuts,
warping background, melting face, identity drift, unnatural speed, rubbery motion,
low resolution, blurry, watermark, text overlay, logo, subtitles, duplicate subject
```

Add mode-specific terms: for I2V — `style drift from reference image, color shift`; for close-ups — `warped eyes, teeth artifacts`; for crowd scenes — `cloned faces, merged bodies`.

## Worked example

**Bible** — Subject: *the sand-shirted woman above*. Style: *35mm Kodak Vision3 500T, fine grain, teal-and-amber grade, 2.39:1*. Environment: *a rain-soaked Lisbon tram depot at dusk, sodium lamps overhead casting warm pools, wet cobbles reflecting amber*.

**Shot 1 (T2V)**

```
[Style & Medium]: 35mm Kodak Vision3 500T film, fine grain, teal-and-amber grade, anamorphic 2.39:1, photoreal
[Subject]: a 29-year-old Ethiopian woman, close-cropped curly hair with a faint shaved line on the left temple, a thin silver hoop in the right ear, wearing a sand-colored linen work shirt with rolled sleeves and a canvas tool belt
[Action & Timeline]: she stands still facing the depot doors, then lifts her right hand and slowly presses the rusted door handle down; the door gives way with a metallic groan and a puff of dust drifts into the light
[Environment & Lighting]: rain-soaked Lisbon tram depot at dusk, sodium lamps overhead casting warm pools, wet cobbles reflecting amber, fine drizzle catching the light
[Camera]: medium shot at eye level, slow dolly-in from 3m to 1.5m over 5s, 40mm anamorphic, shallow depth of field
```

**Shot 2 (I2V, seeded with Shot 1's final frame)**

```
[Subject Motion]: she pushes the door open and steps through, head turning slightly toward camera left, eyes adjusting to the darker interior, one blink
[Environment & Secondary Physics]: drizzle on her shoulders, shirt hem lifting with the draft from the opening door, dust motes swirling in the sodium backlight, wet cobbles glistening
[Camera Trajectory]: gentle handheld follow, drifting forward with her, slight 5-degree pan right
[Style & Continuity Lock]: maintain exact character appearance, lighting, grade, and grain of the reference frame; smooth natural motion; 24fps
```
Negative: `morphing, distorted limbs, extra fingers, flickering textures, warping background, melting face, identity drift, unnatural speed, low resolution, watermark, text overlay`

中文备注：镜头 1 用缓推建立人物锚点与空间；镜头 2 以首帧续接只描述位移与次级物理，避免与参考帧冲突。

## Failure modes

- **Overloaded shot** — three plot beats in one 5-second prompt produces morphing and limb distortion. Split it.
- **Contradicting the reference in I2V** — describing a red jacket when the image shows blue causes flickering and identity drift. Describe how things move, not how they look.
- **Vague motion verbs** — "moves nicely", "cool transition", "dynamic energy" are noise. Write "turns her head slowly toward camera left" or "strides forward through shallow puddles".
- **Pronoun drift** — "she smiles" three shots later without the anchor lets the model invent a new face.
- **Camera stacking** — "dolly-in then pan up then orbit" in one clip forces the model to warp geometry. One move per shot.
- **Style tag pileup** — contradictory tags ("photoreal" + "anime", "handheld" + "perfectly smooth gimbal") average into mush.
- **Ignoring platform limits** — some models cap prompts near 1,000 characters and clip the camera section last. Keep the most important constraints early.

## Delivery checklist

1. Assumptions stated; every shot has a duration and an aspect ratio.
2. Anchors are byte-identical across shots.
3. Exactly one action and one camera move per clip.
4. Physics named (weight, wind, water, dust, cloth).
5. Contradiction check against any reference image.
6. Negative block included and mode-tuned.
7. Merged prompt present alongside the structured block.
8. Chaining note for sequences: which final frame seeds which next shot.

---

## Scope handoff (read this before use)

This skill writes **prompts for the interactive layer** — what an element is, how it is explained
when a reader pauses and taps it. It is deliberately **not** part of the video pipeline.

- Do **not** route page or segment video generation through this skill. That belongs to
  `seedance-relay-producer` (generation) and `picturebook-scene-director` (shot planning).
- Do **not** expect this skill to be triggered by the pipeline. The pipeline hands over exactly two
  artifacts — a frozen `final_movie.mp4` and `page_timeline.json` — and stops there.
- If a request is about "make this page move" / "generate the transition", it is the wrong skill:
  load `living-picturebook` instead, which owns the entry point.
