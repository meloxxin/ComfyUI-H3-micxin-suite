# -*- coding: utf-8 -*-
"""script_to_h3_prompt: 用户分镜提示词 → H3 六段式标准 JSON（确定性转换，不调 LLM）。

用法：
    from script_to_h3_prompt import script_to_h3_prompt
    result = script_to_h3_prompt(your_script, roles=None, segment_seconds=5)

输入约定（宽松）：
    - 空行 = 分镜分隔（每段一个 Segment）
    - 镜头号可选：镜10 / Shot 1 / [Shot 1]
    - 参考图引用：<Picture 1> 原样保留
    - 角色绑定：名字与图同段出现即绑定（林溪<Picture 2> / <Picture 1> 林建国）
    - 对话：S1说："..." 或 S2说："..."（S 编号保留，不换成名字）
    - 音效：段内出现"……声/声响"字样自动进 overall_soundscape

角色表（可选）：
    roles = {"S1": {"role": "father", "picture": "1"}, "S2": {"role": "daughter", "picture": "2"}}
    不传则从分镜自动推断名字↔S↔图绑定，role 留空。

注意：
    - 描述模板为英文；动作/音效按你的原文保留（要全英文动作需后续润色或接翻译）。
    - <d> 对话永远保留原文语言。
"""
import json
import re

# ---------- 镜头语言 中→英 ----------
_CAMERA_RULES = [
    (r"中全景", "wide"),
    (r"中近景", "medium close-up"),
    (r"中景", "medium shot"),
    (r"全景", "wide shot"),
    (r"特写", "close-up"),
    (r"近景", "close shot"),
    (r"远景", "long shot"),
    (r"微俯拍", "slightly high angle"),
    (r"俯拍", "high angle"),
    (r"微仰拍", "slightly low angle"),
    (r"仰拍", "low angle"),
    (r"退拉", "pull-back"),
    (r"跟踪", "tracking"),
    (r"跟摇", "follow pan"),
    (r"推", "push-in"),
    (r"摇", "pan"),
    (r"移", "dolly"),
    (r"固定", "locked"),
    (r"缓", "slow"),
]
_CAMERA_TERMS = [k for k, _ in _CAMERA_RULES]


def _to_camera(text):
    """按原文出现顺序把镜头词转英文；找不到给默认 medium shot + locked。"""
    got = []
    rest = text
    pos = 0
    for m in re.finditer(r"中全景|中近景|中景|全景|特写|近景|远景|微俯拍|俯拍|微仰拍|仰拍|退拉|跟踪|跟摇|推|摇|移|固定|缓", rest):
        for cn, en in _CAMERA_RULES:
            if rest.startswith(cn, m.start()):
                got.append(en)
                break
    if not got:
        return "medium shot, locked camera"
    joined = " ".join(dict.fromkeys(got))
    if not any(w in joined for w in ("tracking", "pull-back", "push-in", "pan", "dolly", "follow")):
        joined += ", locked camera"
    return joined


def _find_pictures(text):
    return re.findall(r"<Picture\s+(\d+)>", text)


def _find_dialogue(text):
    """提取 S编号说："..." → [(S编号, 台词)]"""
    out = []
    for m in re.finditer(r"S(\d+)\s*说\s*[：:]\s*[“\"']?(.+?)[”\"']?\s*$", text, flags=re.M):
        out.append((f"S{m.group(1)}", m.group(2).strip()))
    return out


def _find_sfx(text):
    """提取音效描述：按句/短语切分，只取以声/声响/音效结尾的短块"""
    out = []
    t = re.sub(r"<Picture\s+\d+>", " ", text)
    for sentence in re.split(r"[。；\n]", t):
        for phrase in re.split(r"[，、,]", sentence):
            phrase = phrase.strip()
            if not phrase:
                continue
            if re.search(r"(?:声|声响|音效|窸窣)$", phrase) and len(phrase) <= 25:
                if phrase not in out:
                    out.append(phrase)
    return out


_EXCLUDE_BIND = set("跟踪 退拉 推 摇 移 固定 场景 环境 背景 画面 中景 全景 特写 近景 远景 缓".split())
_EXCLUDE_PREFIX = ("的", "她", "他", "我", "你", "这", "那", "就", "把", "将", "从", "在", "向", "与")


def _find_subject_bindings(seg_text):
    """名字 ↔ Picture 绑定：林溪<Picture 2> / <Picture 1> 林建国（限 2-3 字，排除镜头/标记词）"""
    binds = {}
    for m in re.finditer(r"([\u4e00-\u9fff]{2,3})\s*<Picture\s+(\d+)>", seg_text):
        name, pic = m.group(1), m.group(2)
        if name in _EXCLUDE_BIND or name.startswith(_EXCLUDE_PREFIX):
            continue
        binds[name] = pic
    for m in re.finditer(r"<Picture\s+(\d+)>\s*([\u4e00-\u9fff]{2,3})", seg_text):
        pic, name = m.group(1), m.group(2)
        if name in _EXCLUDE_BIND or name.startswith(_EXCLUDE_PREFIX):
            continue
        binds[name] = pic
    return binds


def _find_speaker_ids(seg_text, name):
    """推断名字↔S编号：同段出现 '名字...S2说' → 名字=S2。"""
    for m in re.finditer(r"S(\d+)\s*说", seg_text):
        seg_before = seg_text[: m.start()]
        if name in seg_before:
            return f"S{m.group(1)}"
    return None


def _clean_action(text):
    """去掉镜头号/Picture/对话/镜头词/标记词/音效短语后，剩余动作描述原文。"""
    t = text
    t = re.sub(r"镜\d+\s*", "", t)
    t = re.sub(r"\[Shot\s*\d+\]|\[Shot\s*\d+", "", t)
    t = re.sub(r"<Picture\s+\d+>", "", t)
    t = re.sub(r"场景\s*[。，]?", "", t)          # "<Picture N> 场景。" 的标记词
    t = re.sub(r"环境\s*[。，]?|背景\s*[。，]?", "", t)
    t = re.sub(r"S\d+\s*说\s*[：:]\s*[“\"'].*?[”\"']", "", t, flags=re.S)
    t = re.sub(r"<d>.*?</d>", "", t, flags=re.S)
    for sfx in _find_sfx(t):
        t = t.replace(sfx, " ")
    t = re.sub(r"中全景|中近景|中景|全景|特写|近景|远景|微俯拍|俯拍|微仰拍|仰拍|退拉|跟踪|跟摇|推|摇|移|固定|缓", " ", t)
    t = re.sub(r"[\s，。；：、,]+", " ", t).strip()
    return t


def _parse_segment(seg_text):
    """一段分镜 → 结构化字段"""
    binds = _find_subject_bindings(seg_text)
    return {
        "camera": _to_camera(seg_text),
        "pictures": _find_pictures(seg_text),
        "dialogue": _find_dialogue(seg_text),
        "sfx": _find_sfx(seg_text),
        "binds": binds,
        "names": list(binds.keys()),
        "action": _clean_action(seg_text),
        "raw": seg_text.strip(),
    }


# ---------- 六段式模板 ----------
def _subject_definitions(sids, background_pic, roles):
    """角色定义 + 背景定义。sids: [(S编号, Picture编号, role)]"""
    parts = []
    for i, (sid, pic, role) in enumerate(sids, 1):
        if role:
            parts.append(f"<Subject {i}> ({sid}, {role}): exact face, hairstyle, and clothing locked from <Picture {pic}>.")
        else:
            parts.append(f"<Subject {i}> ({sid}): exact face, hairstyle, and clothing locked from <Picture {pic}>.")
    parts.append(
        f"<Subject B>: background scene only. The entire background must be inherited from "
        f"<Picture {background_pic}> as the scene; <Picture {background_pic}> alone defines its "
        f"appearance, so do not invent, redraw, or describe scene details.")
    return " ".join(parts)


def _summary(i, camera, sec, bg, action, dialogue, sfx, extra_refs=None):
    parts = [f"Segment {i}, {sec} seconds, one continuous {camera} at the scene from <Picture {bg}>."]
    if action:
        parts.append(action)
    if dialogue:
        dlg = " ".join(f"{sid} says: {line}" for sid, line in dialogue)
        parts.append(dlg)
    elif sfx:
        parts.append("No dialogue. " + " ".join(sfx))
    if extra_refs:
        parts.append("Reference: " + " ".join(extra_refs) + ".")
    return " ".join(parts)


def _retention(sids, bg, action, dialogue, sfx, active_sids=None):
    parts = []
    for i, (sid, pic, role) in enumerate(sids, 1):
        if active_sids and sid not in active_sids:
            parts.append(
                f"<Subject {i}>: fully_preserved appearance; face, hair, and clothing locked; "
                f"present in frame, no independent action.")
        else:
            parts.append(
                f"<Subject {i}>: fully_preserved appearance; face, hair, and clothing locked; "
                f"action: {action or 'present in frame'}.")
    parts.append(
        f"<Subject B>: fully_preserved background; the entire background stays as the scene from "
        f"<Picture {bg}>, with its reference appearance unchanged and no extra scene description.")
    if sfx:
        parts.append("SFX: " + " ".join(sfx))
    return " ".join(parts)


def _detailed(camera, bg, action, dialogue, sec):
    parts = [
        f"One continuous {camera}, camera focuses on the scene inherited from <Picture {bg}>.",
        f"Spatial layout: subjects are positioned per <Picture {bg}> composition; the background stays unchanged.",
    ]
    if action:
        parts.append(action)
    if dialogue:
        for sid, line in dialogue:
            parts.append(f"<d>00:00-00:{sec:02d} ({sid}): \"{line}\"</d>")
    else:
        parts.append(f"<d>00:00-00:{sec:02d}: No dialogue. All mouths closed.</d>")
    parts.append("Expressions are natural and subtle, not exaggerated.")
    return " ".join(parts)


def _soundscape(sfx):
    if sfx:
        return "Constant room tone continues; " + " ".join(sfx) + "."
    return "Constant room tone and matching physical SFX continue throughout the video."


def _build_segment(i, seg, sids, bg, sec, active_sids=None):
    action = seg["action"]
    dialogue = seg["dialogue"]
    sfx = seg["sfx"]
    # 段内非角色、非背景的 Picture（如运镜终点/场景参考）保留进 summary
    role_pics = {p for _, p, _ in sids}
    extra_refs = [f"<Picture {p}>" for p in seg["pictures"] if p not in role_pics and p != bg]
    v = [
        f"subject_definitions: {_subject_definitions(sids, bg, None)}",
        f"summary: {_summary(i, seg['camera'], sec, bg, action, dialogue, sfx, extra_refs)}",
        f"retention_analysis: {_retention(sids, bg, action, dialogue, sfx, active_sids)}",
        f"detailed_description: {_detailed(seg['camera'], bg, action, dialogue, sec)}",
        f"overall_soundscape: {_soundscape(sfx)}",
        f"non_diegetic_music: N/A",
    ]
    return "\n".join(v)


# ---------- 主入口 ----------
def script_to_h3_prompt(script, roles=None, background=None, segment_seconds=5):
    """
    script: str，空行分镜。
    roles: {S编号: {"role": ..., "picture": ...}}，可选。
    background: 背景图编号（如 "3"），可选；默认取第一段出现且非角色绑定的图，否则 "1"。
    返回: (segments_json_str, total_seconds)
    """
    segs_text = [s.strip() for s in re.split(r"\n\s*\n", script) if s.strip()]
    if not segs_text:
        raise ValueError("script 为空，未找到分镜段落（空行分隔）。")

    parsed = [_parse_segment(s) for s in segs_text]

    # 1) 角色表：优先 roles 参数（可带 name 用于人名替换）
    sids = []
    name_sid_map = {}
    if roles:
        for k in sorted(roles):
            r = roles[k]
            sids.append((k, str(r.get("picture", "1")), r.get("role", "")))
            if r.get("name"):
                name_sid_map[r["name"]] = k
    else:
        # 自动推断：名字↔图（跨段合并）、名字↔S（同段推断）
        name_pic = {}
        name_sid = {}
        for seg in parsed:
            for name, pic in seg["binds"].items():
                name_pic[name] = pic
            for name in seg["binds"]:
                sid = _find_speaker_ids(seg["raw"], name)
                if sid:
                    name_sid[name] = sid
        # 按 S 编号排序输出
        seen = {}
        for name, sid in sorted(name_sid.items(), key=lambda x: x[1]):
            seen[sid] = name_pic.get(name, "1")
        # 有对话但没绑上名字的 S
        for seg in parsed:
            for sid, _ in seg["dialogue"]:
                if sid not in seen:
                    seen[sid] = "1"
        for sid in sorted(seen, key=lambda x: int(x[1:])):
            sids.append((sid, seen[sid], ""))

    # 2) 背景图：参数 > 第一段场景绑定外的图 > 默认
    bg = background
    if not bg:
        for seg in parsed:
            role_pics = {p for _, p, _ in sids}
            for p in seg["pictures"]:
                if p not in role_pics:
                    bg = p
                    break
            if bg:
                break
    if not bg:
        bg = "1"

    # 自动推断模式：名字↔S（跨段：名字在任一段 S说 之前出现）
    if not roles:
        all_names = {}
        for seg in parsed:
            for n in seg["names"]:
                all_names.setdefault(n, [])
        for n in all_names:
            for seg in parsed:
                sid = _find_speaker_ids(seg["raw"], n)
                if sid:
                    name_sid_map[n] = sid
                    break

    total = len(parsed)
    out = {}
    for i, seg in enumerate(parsed):
        active = None
        if not roles:
            active = {sid for sid, _ in seg["dialogue"]}
            for name in seg["names"]:
                if name in name_sid_map:
                    active.add(name_sid_map[name])
        else:
            active = {sid for sid, _ in seg["dialogue"]}
            for name in seg["names"]:
                if name in name_sid_map:
                    active.add(name_sid_map[name])
            if not active:
                active = None
        # 动作/摘要里出现的中文人名 → S 编号（任何位置出现人名即废稿）
        for n, sid in name_sid_map.items():
            seg["action"] = seg["action"].replace(n, sid)
        out[str(i)] = _build_segment(i, seg, sids, bg, segment_seconds, active)

    return json.dumps(out, ensure_ascii=False), total * segment_seconds


# ---------- 直接运行：测试 ----------
if __name__ == "__main__":
    demo = """镜10 <Picture 3> 场景。林溪<Picture 2> 的脸。她的眼眶在看到屏幕文字的瞬间变红，泪水涌上来但没有掉落，嘴唇微微颤抖，欲言又止。S2说：“你凭什么删我东西……”

镜11 中全景跟踪退拉<Picture 4> 。<Picture 1> 林建国从背后（沙发靠背后/柜子旁）抽出一个黑色大垃圾袋，大步走到餐桌旁，将袋子倒扣猛地一抖——舞鞋、护膝、练功服哗啦啦全倒在餐桌上。垃圾袋窸窣声、物品倒在桌面的杂乱声响。

镜12 中景微俯拍固定。餐桌上散落的舞鞋、护膝和练功服，与远处试卷的红叉形成画面呼应。<Picture 1> 林建国的手指入画，指向那堆物品。S1说：“凭我是你爸！跳舞能当饭吃吗？你别做这个没前途的白日梦了！”"""
    j, t = script_to_h3_prompt(demo, segment_seconds=5)
    data = json.loads(j)
    print(f"=== {len(data)} 段 / {t}s ===")
    for k in sorted(data, key=int):
        print(f"\n----- {k} -----")
        print(data[k])
