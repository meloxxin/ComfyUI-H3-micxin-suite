# -*- coding: utf-8 -*-
"""H3 Asset Library v3.0 — 后端 API 与数据持久化

参考 ComfyUI_GJJ_Nodes 的资产管理设计：
- aiohttp 路由提供 CRUD API
- JSON 文件持久化到 ComfyUI/user/default/h3_assets/
- 支持文件夹扫描、缩略图、角色/场景/道具三库

数据格式（与现有 PromptWriter/Story Setup 兼容）：
{
    "characters": [{"id":"S1","name":"女主","description":"...","image":"path.png"}],
    "scenes":     [{"id":"E1","name":"客厅","description":"...","image":"path.png"}],
    "props":      [{"id":"P1","name":"手机","description":"...","image":"path.png"}]
}
"""
import json
import os
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# 数据路径
# ---------------------------------------------------------------------------

def _get_user_dir() -> Path:
    """获取 ComfyUI 用户数据目录（ComfyUI/user/default/）。"""
    try:
        import folder_paths
        base = Path(folder_paths.base_path)
        user_dir = base / "user" / "default" / "h3_assets"
    except Exception:
        # fallback：当前工作目录
        user_dir = Path.cwd() / "h3_assets"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _get_library_path() -> Path:
    return _get_user_dir() / "library.json"


# ---------------------------------------------------------------------------
# 数据加载 / 保存
# ---------------------------------------------------------------------------

def load_library() -> dict:
    """加载资产库 JSON。文件不存在时返回空结构。"""
    path = _get_library_path()
    if not path.exists():
        return {"characters": [], "scenes": [], "props": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 确保三个 key 都存在
        data.setdefault("characters", [])
        data.setdefault("scenes", [])
        data.setdefault("props", [])
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[H3 AssetLibrary] 加载失败: {e}，返回空库。", flush=True)
        return {"characters": [], "scenes": [], "props": []}


def save_library(data: dict) -> None:
    """保存资产库 JSON。"""
    path = _get_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CRUD 操作
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "characters": "characters",
    "character": "characters",
    "char": "characters",
    "scenes": "scenes",
    "scene": "scenes",
    "props": "props",
    "prop": "props",
}


def _resolve_category(category: str) -> str:
    key = (category or "").lower().strip()
    return CATEGORY_MAP.get(key, "characters")


def _next_id(library: dict, category: str) -> str:
    """生成下一个 ID（S1/S2/... 或 E1/E2/... 或 P1/P2/...）。"""
    prefix = {"characters": "S", "scenes": "E", "props": "P"}[category]
    existing = {item.get("id", "") for item in library.get(category, [])}
    n = 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


def list_items(category: str = None) -> dict:
    """列出资产。category=None 时返回全部。"""
    library = load_library()
    if category:
        cat = _resolve_category(category)
        return {cat: library.get(cat, [])}
    return library


def get_item(category: str, item_id: str) -> dict | None:
    """获取单个资产。"""
    library = load_library()
    cat = _resolve_category(category)
    for item in library.get(cat, []):
        if item.get("id") == item_id:
            return item
    return None


def create_item(category: str, data: dict) -> dict:
    """创建资产。"""
    library = load_library()
    cat = _resolve_category(category)
    item = {
        "id": data.get("id") or _next_id(library, cat),
        "name": data.get("name", "").strip(),
        "description": data.get("description", "").strip(),
        "image": data.get("image", "").strip(),
    }
    library.setdefault(cat, []).append(item)
    save_library(library)
    return item


def update_item(category: str, item_id: str, data: dict) -> dict | None:
    """更新资产。"""
    library = load_library()
    cat = _resolve_category(category)
    for item in library.get(cat, []):
        if item.get("id") == item_id:
            if "name" in data:
                item["name"] = data["name"].strip()
            if "description" in data:
                item["description"] = data["description"].strip()
            if "image" in data:
                item["image"] = data["image"].strip()
            save_library(library)
            return item
    return None


def delete_item(category: str, item_id: str) -> bool:
    """删除资产。"""
    library = load_library()
    cat = _resolve_category(category)
    items = library.get(cat, [])
    new_items = [item for item in items if item.get("id") != item_id]
    if len(new_items) == len(items):
        return False
    library[cat] = new_items
    save_library(library)
    return True


# ---------------------------------------------------------------------------
# 文件夹扫描
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def scan_folder(folder_path: str, category: str = None) -> dict:
    """扫描文件夹中的图片。

    如果 folder_path 下有 characters/、scenes/、props/ 子目录，自动归类。
    否则按文件名排序，全部返回。
    """
    folder = Path(folder_path).expanduser()
    if not folder.exists() or not folder.is_dir():
        return {"error": f"文件夹不存在: {folder_path}", "images": []}

    result = {"characters": [], "scenes": [], "props": [], "all": []}

    # 检查是否有子目录
    subdirs = {
        "characters": folder / "characters",
        "scenes": folder / "scenes",
        "props": folder / "props",
    }
    has_subdirs = any(d.exists() and d.is_dir() for d in subdirs.values())

    if has_subdirs:
        for cat, subdir in subdirs.items():
            if subdir.exists() and subdir.is_dir():
                for f in sorted(subdir.iterdir()):
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                        result[cat].append(str(f))
                        result["all"].append(str(f))
        # 同时扫描根目录的图片（放在 all 中，不归类）
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                path_str = str(f)
                if path_str not in result["all"]:
                    result["all"].append(path_str)
    else:
        # 无子目录，全部扫描
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                result["all"].append(str(f))

    return result


def import_from_folder(folder_path: str, category: str = None) -> dict:
    """从文件夹导入图片到资产库。

    category=None 时，按子目录自动归类；无子目录时全部导入到 characters。
    """
    scan_result = scan_folder(folder_path, category)
    if "error" in scan_result:
        return scan_result

    library = load_library()
    imported = []

    if category:
        cat = _resolve_category(category)
        images = scan_result.get("all", [])
    else:
        # 按子目录归类
        images_by_cat = {
            "characters": scan_result.get("characters", []),
            "scenes": scan_result.get("scenes", []),
            "props": scan_result.get("props", []),
        }
        # 如果没有子目录，全部导入到 characters
        if not any(images_by_cat.values()):
            images_by_cat["characters"] = scan_result.get("all", [])

        for cat, images in images_by_cat.items():
            for img_path in images:
                name = Path(img_path).stem
                item = {
                    "id": _next_id(library, cat),
                    "name": name,
                    "description": "",
                    "image": img_path,
                }
                library.setdefault(cat, []).append(item)
                imported.append(item)
        save_library(library)
        return {"imported": imported, "total": len(imported)}

    # 指定 category 的导入
    for img_path in images:
        name = Path(img_path).stem
        item = {
            "id": _next_id(library, cat),
            "name": name,
            "description": "",
            "image": img_path,
        }
        library.setdefault(cat, []).append(item)
        imported.append(item)

    save_library(library)
    return {"imported": imported, "total": len(imported)}


# ---------------------------------------------------------------------------
# aiohttp 路由注册（ComfyUI 标准方式：PromptServer.instance.routes 装饰器）
# ---------------------------------------------------------------------------

def _register_routes():
    """注册 H3 Asset Library API 路由。

    注意：具体路径（scan/import/stats）必须在参数路径（{category}）之前注册，
    否则 /h3/asset_library/import 会被 {category} 路由抢先匹配。
    """
    try:
        from server import PromptServer
        from aiohttp import web
    except ImportError:
        print("[H3 AssetLibrary] 无法导入 server/PromptServer，路由注册跳过。", flush=True)
        return

    if PromptServer.instance is None:
        print("[H3 AssetLibrary] PromptServer.instance 为空，路由注册跳过。", flush=True)
        return

    routes = PromptServer.instance.routes

    # === 具体路径路由（必须在 {category} 之前）===

    @routes.get("/h3/asset_library/stats")
    async def api_stats(request):
        """获取资产库统计信息。"""
        library = load_library()
        return web.json_response({
            "ok": True,
            "data": {
                "characters": len(library.get("characters", [])),
                "scenes": len(library.get("scenes", [])),
                "props": len(library.get("props", [])),
                "path": str(_get_library_path()),
            }
        })

    @routes.post("/h3/asset_library/scan")
    async def api_scan(request):
        """扫描文件夹中的图片（不导入，仅返回列表）。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        folder = body.get("folder", "")
        category = body.get("category")
        if not folder:
            return web.json_response({"ok": False, "error": "缺少 folder 参数"}, status=400)
        result = scan_folder(folder, category)
        return web.json_response({"ok": "error" not in result, "data": result})

    @routes.post("/h3/asset_library/import")
    async def api_import(request):
        """从文件夹导入图片到资产库。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        folder = body.get("folder", "")
        category = body.get("category")
        if not folder:
            return web.json_response({"ok": False, "error": "缺少 folder 参数"}, status=400)
        result = import_from_folder(folder, category)
        return web.json_response({"ok": "error" not in result, "data": result})

    # === 列表路由 ===

    @routes.get("/h3/asset_library")
    async def api_list(request):
        """列出全部资产，或按 category 筛选。"""
        category = request.query.get("category")
        data = list_items(category)
        return web.json_response({"ok": True, "data": data})

    # === 参数路径路由（{category} / {category}/{item_id}）===

    @routes.post("/h3/asset_library/{category}")
    async def api_create(request):
        """创建资产。"""
        category = request.match_info["category"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        item = create_item(category, body)
        return web.json_response({"ok": True, "data": item})

    @routes.get("/h3/asset_library/{category}/{item_id}")
    async def api_get(request):
        """获取单个资产。"""
        category = request.match_info["category"]
        item_id = request.match_info["item_id"]
        item = get_item(category, item_id)
        if not item:
            return web.json_response({"ok": False, "error": "未找到"}, status=404)
        return web.json_response({"ok": True, "data": item})

    @routes.put("/h3/asset_library/{category}/{item_id}")
    async def api_update(request):
        """更新资产。"""
        category = request.match_info["category"]
        item_id = request.match_info["item_id"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        item = update_item(category, item_id, body)
        if not item:
            return web.json_response({"ok": False, "error": "未找到"}, status=404)
        return web.json_response({"ok": True, "data": item})

    @routes.delete("/h3/asset_library/{category}/{item_id}")
    async def api_delete(request):
        """删除资产。"""
        category = request.match_info["category"]
        item_id = request.match_info["item_id"]
        ok = delete_item(category, item_id)
        if not ok:
            return web.json_response({"ok": False, "error": "未找到"}, status=404)
        return web.json_response({"ok": True})

    # === 图片文件服务路由（解决浏览器无法直接访问本地绝对路径的问题）===

    @routes.get("/h3/asset_library/file")
    async def api_file(request):
        """按路径返回图片文件内容（供前端缩略图/预览用）。

        query: ?path=<绝对路径或相对input路径>
        """
        path = request.query.get("path", "")
        resolved = _resolve_image_path(path)
        if not resolved:
            return web.json_response({"ok": False, "error": "文件不存在"}, status=404)
        ext = os.path.splitext(resolved)[1].lower()
        if ext not in IMAGE_EXTENSIONS:
            return web.json_response({"ok": False, "error": "不是图片文件"}, status=400)
        try:
            return web.FileResponse(resolved)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    # === LLM 自动打标路由（参考 LoRA 打标：LLM 看图生成英文描述）===

    @routes.post("/h3/asset_library/describe")
    async def api_describe(request):
        """用 LLM 为单张图片生成描述（自动打标）。

        body: {"image": "path/to/image.png"}
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        image = body.get("image", "")
        if not image:
            return web.json_response({"ok": False, "error": "缺少 image 参数"}, status=400)
        try:
            description = describe_image(image)
            return web.json_response({"ok": True, "data": {"description": description}})
        except Exception as e:
            print(f"[H3 AssetLibrary] describe 失败: {e}", flush=True)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    @routes.post("/h3/asset_library/describe_all")
    async def api_describe_all(request):
        """批量打标：为多个图片生成描述，并写回资产库。

        body: {
            "items": [{"category":"characters","id":"S1","image":"path.png"}, ...]
        }
        返回每个 item 的新描述。
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        items = body.get("items", []) or []
        if not items:
            return web.json_response({"ok": False, "error": "缺少 items 参数"}, status=400)

        results = []
        for item in items:
            category = item.get("category", "characters")
            item_id = item.get("id", "")
            image = item.get("image", "")
            if not image or not item_id:
                results.append({"category": category, "id": item_id, "ok": False, "error": "缺少 image/id"})
                continue
            try:
                desc = describe_image(image)
                update_item(category, item_id, {"description": desc})
                results.append({"category": category, "id": item_id, "ok": True, "description": desc})
            except Exception as e:
                print(f"[H3 AssetLibrary] describe_all {item_id} 失败: {e}", flush=True)
                results.append({"category": category, "id": item_id, "ok": False, "error": str(e)})

        return web.json_response({"ok": True, "data": {"results": results}})

    print(f"[H3 AssetLibrary] API 路由已注册（PromptServer.instance.routes 装饰器）: /h3/asset_library/*", flush=True)


# ---------------------------------------------------------------------------
# LLM 自动打标（参考 LoRA 打标思路）
# 复用 H3 PromptWriter 的 Qwen3-VL 视觉加载/推理（_load_local_llm / _call_local_llm /
# _load_image_tensor_from_paths / _images_to_contents），不重复造轮子。
# ---------------------------------------------------------------------------

# 打标用 system prompt：按资产类型生成简洁英文描述，用于 LLM 生成视频提示词。
_DESCRIBE_SYSTEM_PROMPTS = {
    "characters": (
        "You are an AI asset tagger for video generation. Describe the character "
        "in this image with a concise English prompt (50-120 words, comma-separated "
        "tags preferred). Cover: gender, approximate age, ethnicity, hair style and "
        "color, facial features, body type, outfit (clothing, colors, accessories), "
        "pose, expression, and any distinctive traits. Focus on VISUAL details that "
        "would help generate a consistent character. Output ONLY the English "
        "description, no preamble, no quotes."
    ),
    "scenes": (
        "You are an AI asset tagger for video generation. Describe this location/scene "
        "with a concise English prompt (50-120 words, comma-separated tags preferred). "
        "Cover: type of location, time of day, lighting, atmosphere, color palette, "
        "key furniture/objects, camera angle hint. Focus on VISUAL details useful for "
        "generating a consistent background. Output ONLY the English description, "
        "no preamble, no quotes."
    ),
    "props": (
        "You are an AI asset tagger for video generation. Describe this object/prop "
        "with a concise English prompt (30-80 words, comma-separated tags preferred). "
        "Cover: what the object is, material, color, size, texture, distinctive "
        "details, and how it might be used in a scene. Output ONLY the English "
        "description, no preamble, no quotes."
    ),
}


def _describe_llm_config():
    """打标用的 LLM 配置：优先复用 PromptWriter 已加载的实例，否则自己加载。"""
    import folder_paths

    def _resolve_llm_dir():
        if getattr(folder_paths, "models_dir", None):
            return os.path.join(folder_paths.models_dir, "LLM")
        return os.path.join(folder_paths.base_path, "models", "LLM")

    def _list_gguf(mmproj_only=False):
        base = _resolve_llm_dir()
        if not os.path.isdir(base):
            return []
        files = [f for f in os.listdir(base) if f.lower().endswith(".gguf")]
        if mmproj_only:
            files = [f for f in files if "mmproj" in f.lower()]
        return sorted(files)

    gguf_files = _list_gguf(mmproj_only=False)
    mmproj_files = _list_gguf(mmproj_only=True)
    # 排除 mmproj 后选主模型
    main_gguf = [f for f in gguf_files if "mmproj" not in f.lower()]
    gguf_name = main_gguf[0] if main_gguf else (gguf_files[0] if gguf_files else "")
    mmproj_name = mmproj_files[0] if mmproj_files else ""
    return gguf_name, mmproj_name


def _load_describe_llm():
    """加载打标用 LLM。优先用屏幕writer 已缓存的实例。"""
    try:
        from .h3_screenwriter import _LOCAL, _load_local_llm
    except ImportError:
        from h3_screenwriter import _LOCAL, _load_local_llm

    gguf_name, mmproj_name = _describe_llm_config()
    if not gguf_name:
        raise RuntimeError("没有找到 GGUF 模型（ComfyUI/models/LLM/ 下没有 .gguf 文件）")
    if not mmproj_name:
        raise RuntimeError("没有找到 mmproj 视觉模型（需要 Qwen3-VL 的 mmproj 才能看图打标）")

    # 打标用固定配置：小上下文即可，n_gpu_layers=-1 全量进 GPU
    try:
        llm = _load_local_llm(gguf_name, mmproj_name, -1, 8192)
        return llm
    except Exception as e:
        raise RuntimeError(f"加载 LLM 失败: {e}")


def _load_single_image_content(image_path):
    """加载单张图片为 VLM content。

    注意：_load_image_tensor_from_paths 返回三元组 (tensor, img_count, max_side)，
    必须解包取第 0 个元素才是张量。
    """
    try:
        from .h3_screenwriter import _load_image_tensor_from_paths, _images_to_contents
    except ImportError:
        from h3_screenwriter import _load_image_tensor_from_paths, _images_to_contents

    # 解析图片路径：绝对路径 → input 目录 → 原样
    resolved = _resolve_image_path(image_path)
    if resolved is None:
        raise RuntimeError(f"图片不存在: {image_path}")

    loaded = _load_image_tensor_from_paths(resolved)
    if loaded is None:
        raise RuntimeError(f"无法加载图片: {resolved}")
    tensor = loaded[0]  # 解包三元组
    contents = _images_to_contents(tensor)
    if not contents:
        raise RuntimeError(f"图片加载后没有有效内容: {resolved}")
    return contents


def _resolve_image_path(image_path):
    """解析资产图片路径：绝对路径 → input 目录 → folder_paths。

    返回存在的完整路径，找不到返回 None。
    """
    image_path = (image_path or "").strip()
    if not image_path:
        return None
    # 1) 原路径
    if os.path.exists(image_path):
        return image_path
    # 2) input 目录
    try:
        from comfy.cli_args import args
        input_dir = getattr(args, "input_directory", None) or "input"
    except Exception:
        input_dir = "input"
    cand = os.path.join(input_dir, image_path)
    if os.path.exists(cand):
        return cand
    # 3) folder_paths annotated
    try:
        import folder_paths
        return folder_paths.get_annotated_filepath(image_path)
    except Exception:
        pass
    return None


def describe_image(image_path, category="characters"):
    """用 LLM 为单张图片生成描述。category 决定打标侧重点。"""
    try:
        from .h3_screenwriter import _call_local_llm
    except ImportError:
        from h3_screenwriter import _call_local_llm

    llm = _load_describe_llm()
    contents = _load_single_image_content(image_path)

    system_prompt = _DESCRIBE_SYSTEM_PROMPTS.get(category, _DESCRIBE_SYSTEM_PROMPTS["characters"])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": "Describe this image."}] + contents},
    ]
    desc = _call_local_llm(llm, messages, temperature=0.3, seed=0, max_tokens=512)
    desc = (desc or "").strip()
    # 清理多余换行/引号
    desc = " ".join(desc.split())
    desc = desc.strip('"').strip("'")
    return desc


# 模块加载时立即注册
_register_routes()
