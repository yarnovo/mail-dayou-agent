"""通义万相文生图 client · 包装 dashscope SDK · 给 server 调用。

接口文档: https://help.aliyun.com/zh/model-studio/text-to-image-api-reference
模型选型:
  - wanx2.1-t2i-turbo: 极速版 · 7-10s · 适合高频用户头像生成
  - wanx2.1-t2i-plus:  高质量版 · 20-30s · 适合最终精修
  - 默认走 turbo · 用户嫌质量再切 plus
"""
from __future__ import annotations

import os
import json
from pathlib import Path

import dashscope
from dashscope.api_entities.dashscope_response import DashScopeAPIResponse


DEFAULT_MODEL = "wanx2.1-t2i-turbo"
REALISTIC_MODEL = "wan2.2-t2i-plus"  # 写实风格切 plus · 人像皮肤更自然
DEFAULT_SIZE = "1024*1024"
DEFAULT_N = 4  # 一次出 4 张让用户挑

# 风格预设 (subagent 5-6 调研)
STYLE_PROMPTS = {
    "doubao": (
        "3D rendered chibi mascot avatar, oversized rounded head with small body, "
        "soft vinyl/clay material with subsurface scattering, smooth matte surface, "
        "large glossy expressive eyes with light catchlights, friendly warm smile, "
        "soft pastel blue gradient background, three-point soft studio lighting, "
        "Pop Mart blind-box aesthetic, octane render, 8k high detail, kawaii character design"
    ),
    "flat": (
        "flat vector illustration, minimal geometric shapes, clean solid colors, "
        "no gradient, 2D flat design, dribbble style, clear silhouette"
    ),
    "claymation": (
        "claymation style, handmade plasticine clay character, visible thumb prints, "
        "stop-motion aesthetic, matte surface, Aardman studio look, warm tones"
    ),
    "anime": (
        "anime style portrait, cel-shaded, Japanese manga aesthetic, "
        "vibrant colors, large sparkling eyes, makoto shinkai inspired, soft skin shading"
    ),
    "realistic": (
        "professional portrait photography, 85mm f/1.4 lens, natural lighting, "
        "photorealistic, sharp focus, studio backdrop, DSLR shot, magazine cover quality"
    ),
}

DEFAULT_STYLE = "doubao"  # 老板 5-6 拍 · 默认豆包风格 (跟阿空品牌"圆润 · 不卷"调性匹配)
REALISTIC_STYLES = {"realistic"}  # 这些 style 切 plus model


def resolve_api_key() -> str:
    if k := os.getenv("DASHSCOPE_API_KEY"):
        return k
    secrets = Path(__file__).resolve().parents[2] / ".vault" / "secrets.json"
    if secrets.exists():
        data = json.loads(secrets.read_text())
        if k := data.get("dashscope-main", {}).get("api_key"):
            return k
    fallback = Path.home() / ".claude/repos/vault/data/credentials/dashscope-main.json"
    if fallback.exists():
        data = json.loads(fallback.read_text())
        if k := data.get("values", {}).get("api_key"):
            return k
    raise RuntimeError("DASHSCOPE_API_KEY 缺失")


def build_avatar_prompt(user_prompt: str, style: str = DEFAULT_STYLE) -> str:
    """给用户描述拼上"头像 + 风格"约束。

    user_prompt: 用户描述（人物气质 / 场景 / 颜色等 · 如 "戴眼镜温和气质"）
    style: doubao / flat / claymation / anime / realistic (default doubao)
    """
    if style not in STYLE_PROMPTS:
        raise ValueError(f"未知 style={style} · 可选: {list(STYLE_PROMPTS)}")
    return (
        f"{user_prompt}. "
        f"{STYLE_PROMPTS[style]}. "
        "Head and shoulders portrait composition, centered, clean background, "
        "suitable for profile picture, no text watermark."
    )


def pick_model(style: str) -> str:
    """写实 style 切 plus · 其他 turbo (subagent 5-6 调研建议)."""
    return REALISTIC_MODEL if style in REALISTIC_STYLES else DEFAULT_MODEL


def generate_avatars(user_prompt: str, *, style: str = DEFAULT_STYLE,
                     n: int = DEFAULT_N, size: str = DEFAULT_SIZE,
                     model: str | None = None) -> list[str]:
    """同步出 n 张头像 URL · 失败抛 RuntimeError。

    model 不传则按 style 自动选 (realistic→plus · 其他→turbo)。
    """
    dashscope.api_key = resolve_api_key()
    use_model = model or pick_model(style)
    rsp: DashScopeAPIResponse = dashscope.ImageSynthesis.call(
        model=use_model,
        prompt=build_avatar_prompt(user_prompt, style),
        n=n,
        size=size,
    )
    if rsp.status_code != 200:
        raise RuntimeError(f"wanx 出图失败 status={rsp.status_code} code={rsp.code} msg={rsp.message}")
    results = rsp.output.results or []
    urls = [r.url for r in results if getattr(r, "url", None)]
    if not urls:
        raise RuntimeError(f"wanx 没返 URL · output={rsp.output}")
    return urls
