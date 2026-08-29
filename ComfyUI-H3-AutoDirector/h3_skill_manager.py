# -*- coding: utf-8 -*-
"""H3SkillManager — install / delete / refresh custom prompt skill templates.

Skills are JSON files stored in ComfyUI-H3-AutoDirector/skills/.
Each skill extends or replaces the system prompt used by H3Screenwriter.

Skill JSON format:
  {
    "display_name": "显示名",
    "key": "my_skill",
    "standalone": true,
    "style_contract": "Cinematic live-action",
    "system_prompt": "..."
  }

- standalone=true: system_prompt replaces the entire system prompt.
- standalone=false: system_prompt is appended to the base full-reference
  engine (same mechanism as the 16 built-in style appendices).

After installing/deleting a skill, H3Screenwriter's task_mode dropdown will
include the new skill for nodes created from that point on (existing nodes may
need a browser refresh to pick up new options).
"""
import json
import os


class H3SkillManager:
    """Manage custom prompt skill templates for H3Screenwriter."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "action": (["(无/None)",
                              "安装新技能 (add)",
                              "删除技能 (delete)",
                              "刷新技能列表 (refresh)"], {
                    "default": "(无/None)",
                    "tooltip": "选择动作：安装/删除/刷新。刷新会把 skills/ 目录重新扫描进 H3Screenwriter。"
                }),
                "skill_name": ("STRING", {
                    "default": "",
                    "tooltip": "安装时 = 内部 key（也作为文件名）；删除时 = key 或 display_name。"
                }),
            },
            "optional": {
                "display_name": ("STRING", {
                    "default": "",
                    "tooltip": "安装时显示在下拉菜单里的中文/英文名称。为空则使用 skill_name。"
                }),
                "style_contract": ("STRING", {
                    "default": "Cinematic live-action",
                    "tooltip": "该 skill 默认视觉风格前缀（注入 user_brief 的 VISUAL STYLE）。"
                }),
                "standalone": ("BOOLEAN", {
                    "default": True,
                    "label_on": "独立 system prompt",
                    "label_off": "追加到 base",
                    "tooltip": "独立=用 system_prompt 替换全部系统提示；追加=在基础全参考提示词后追加本 skill。"
                }),
                "system_prompt": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "skill 的系统提示词内容。安装必填；删除/刷新可空。"
                }),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "manage"
    CATEGORY = "H3 helper/micxin/AutoDirector"

    def manage(self, action, skill_name, display_name="",
               style_contract="Cinematic live-action",
               standalone=True, system_prompt=""):
        # Delayed import avoids circular dependency at module load time.
        from . import h3_screenwriter as sw

        if action == "(无/None)":
            return ("no action selected",)

        if action.startswith("刷新"):
            sw._refresh_custom_skills()
            return (f"refreshed custom skills; now {len(sw.CUSTOM_SKILL_KEYS)} skill(s) loaded.",)

        if action.startswith("安装"):
            key = (skill_name or "").strip()
            if not key:
                return ("error: skill_name (key) is required for install.",)
            sp = (system_prompt or "").strip()
            if not sp:
                return ("error: system_prompt is required for install.",)
            data = {
                "key": key,
                "display_name": (display_name or key).strip(),
                "standalone": bool(standalone),
                "style_contract": (style_contract or "Cinematic live-action").strip(),
                "system_prompt": sp,
            }
            os.makedirs(sw.SKILLS_DIR, exist_ok=True)
            path = os.path.join(sw.SKILLS_DIR, f"{key}.json")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                return (f"error writing skill file: {e}",)
            sw._refresh_custom_skills()
            return (f"installed skill '{data['display_name']}' -> {path}",)

        if action.startswith("删除"):
            name = (skill_name or "").strip()
            if not name:
                return ("error: skill_name is required for delete.",)
            target_path = None
            target_display = None
            for key, info in sw.CUSTOM_SKILLS.items():
                if key == name or info["display_name"] == name:
                    target_path = info.get("path")
                    target_display = info.get("display_name", key)
                    break
            if not target_path or not os.path.exists(target_path):
                return (f"error: custom skill '{name}' not found.",)
            try:
                os.remove(target_path)
            except Exception as e:
                return (f"error deleting skill file {target_path}: {e}",)
            sw._refresh_custom_skills()
            return (f"deleted custom skill '{target_display}'",)

        return (f"unknown action: {action}",)


NODE_CLASS_MAPPINGS = {"H3SkillManager": H3SkillManager}
NODE_DISPLAY_NAME_MAPPINGS = {"H3SkillManager": "H3 Skill Manager (micxin)"}
