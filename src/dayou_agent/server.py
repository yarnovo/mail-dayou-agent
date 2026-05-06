"""阿空大邮 (xiaohua) FastAPI · /health + /api/avatars/generate."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from typing import Literal

from .dashscope_client import generate_avatars, STYLE_PROMPTS, DEFAULT_STYLE, pick_model


app = FastAPI(title="dayou-agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://m.mail.agentaily.com",
        "https://chat.xiaohua.agentaily.com",
        "https://staging.m.mail.agentaily.com",
        "https://staging.chat.xiaohua.agentaily.com",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "dayou", "version": "0.1.0"}


StyleLiteral = Literal["doubao", "flat", "claymation", "anime", "realistic"]


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=4, max_length=500, description="用户描述 (气质/场景/颜色等)")
    style: StyleLiteral = Field(DEFAULT_STYLE, description="风格预设 (default doubao 圆润 mascot)")
    n: int = Field(4, ge=1, le=8, description="出几张")
    size: str = Field("1024*1024", pattern=r"^\d{3,4}\*\d{3,4}$")


class GenerateResponse(BaseModel):
    urls: list[str]
    model: str
    style: str
    n: int


@app.post("/api/avatars/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    try:
        urls = generate_avatars(req.prompt, style=req.style, n=req.n, size=req.size)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return GenerateResponse(urls=urls, model=pick_model(req.style), style=req.style, n=len(urls))


@app.get("/api/avatars/styles")
def list_styles():
    """列出当前支持的风格预设 (前端 chips 用)."""
    return {
        "default": DEFAULT_STYLE,
        "styles": [
            {"slug": "doubao",     "name": "豆包风",     "desc": "圆润 3D mascot · 黏土质感 · 蓝白调 · 默认推荐"},
            {"slug": "flat",       "name": "扁平插画",   "desc": "几何 · 纯色 · dribbble 风"},
            {"slug": "claymation", "name": "黏土定格",   "desc": "Aardman 风 · 手作橡皮泥质感"},
            {"slug": "anime",      "name": "二次元",     "desc": "日漫 · cel-shading · 大眼"},
            {"slug": "realistic",  "name": "真实摄影",   "desc": "85mm 人像 · 写实 · 切 wan2.2-plus 模型"},
        ],
    }
