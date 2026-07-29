"""渲染基础设施：字体、元素配色、图片下载缓存、通用绘制助手、两遍法卡片渲染

渲染模式参考 nonebot-plugin-bili-dynamic 的 renderer.py：
- 字体全局缓存（skia.Typeface 只加载一次）
- 图片 httpx 下载 + localstore 缓存目录落盘
- render_card 两遍法：先 dry-run 测高，再按实际高度建 surface 正式绘制
- PNG 输出 surface.makeImageSnapshot().encodeToData(...) 转 bytes
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import skia
from nonebot import logger

from ..core.meta import RES_DIR

FONT_DIR = RES_DIR / "fonts"

# ---------------------------------------------------------------------------
# 颜色
# ---------------------------------------------------------------------------


def color(hex_color: str) -> int:
    """#RGB / #RRGGBB / #AARRGGBB -> skia color int"""
    h = (hex_color or "#000000").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 6:
        return skia.ColorSetARGB(255, int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if len(h) == 8:
        return skia.ColorSetARGB(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    return skia.ColorBLACK


# ---------------------------------------------------------------------------
# 元素配色（从 refs/miao-plugin resources/common/bg/bg-*.webp 采样提炼：
# 每张背景图取暗角 -> 中部亮色的对角渐变，对应 elem.html 的 body class 配色）
# ---------------------------------------------------------------------------

# elem key -> (渐变起点, 渐变终点)
ELEM_COLORS: dict[str, tuple[str, str]] = {
    "pyro": ("#461719", "#7a3e39"),
    "hydro": ("#17244d", "#2258a6"),
    "anemo": ("#134a49", "#2b8081"),
    "electro": ("#3f1d5f", "#7e4caa"),
    "dendro": ("#1e562f", "#339534"),
    "cryo": ("#104368", "#439ab7"),
    "geo": ("#5a4623", "#8b7334"),
    "quantum": ("#090f4b", "#273a78"),
    "sr": ("#1c2749", "#2e3962"),
}

# 中文元素/命途名 -> elem key（对应 elem.html 的 elemCls 映射；虚数沿用 geo 金棕色）
_ELEM_ALIAS = {
    "火": "pyro",
    "水": "hydro",
    "风": "anemo",
    "雷": "electro",
    "草": "dendro",
    "冰": "cryo",
    "岩": "geo",
    "量子": "quantum",
    "虚数": "geo",
    "物理": "sr",
}

_DEFAULT_ELEM = "hydro"


def normalize_elem(elem: str | None) -> str:
    """把元素名（英文/中文/命途）归一化为 ELEM_COLORS 的 key，未知给默认"""
    key = str(elem or "").strip()
    key = _ELEM_ALIAS.get(key, key.lower())
    return key if key in ELEM_COLORS else _DEFAULT_ELEM


def elem_gradient(elem: str | None) -> list[int]:
    """元素渐变背景色（两个 skia color），未知 elem 也有结果（默认水系）"""
    start, end = ELEM_COLORS[normalize_elem(elem)]
    return [color(start), color(end)]


# 星级主色：边框/星星/图标底
STAR_COLORS = {5: "#d3a94e", 4: "#9a63d0", 3: "#5f87b8"}
# 星级图标底色（比边框深一档，白字可读）
STAR_BG = {5: "#8a6a34", 4: "#6d4396", 3: "#3d5a80"}

# miao 通用金/灰文字色
GOLD = "#d3bc8e"
NUM_GOLD = "#ffde9d"
TEXT_MAIN = "#ffffff"
TEXT_SUB = "#aaaaaa"

# ---------------------------------------------------------------------------
# 字体（resources/fonts，全局缓存）
# ---------------------------------------------------------------------------

# kind -> 候选字体文件（woff 加载失败记 warning 并顺延，TTF 兜底，最后用 skia 默认字体）
_FONT_FILES = {
    "default": ("HYWH-65W.woff", "华文中宋.TTF"),
    "title": ("NZBZ.woff", "HYWH-65W.woff", "华文中宋.TTF"),
    "number": ("tttgbnumber.woff", "华文中宋.TTF"),
}

_TYPEFACES: dict[str, skia.Typeface] = {}


def _load_typeface(path: Path) -> skia.Typeface | None:
    try:
        tf = skia.Typeface.MakeFromFile(str(path), 0)
        if tf and tf.countGlyphs() > 0:
            return tf
    except Exception as e:
        logger.warning(f"[miao-render] 字体加载失败 {path.name}: {e}")
    return None


def _typeface(kind: str = "default") -> skia.Typeface:
    if kind in _TYPEFACES:
        return _TYPEFACES[kind]
    tf = None
    candidates = _FONT_FILES.get(kind, _FONT_FILES["default"])
    for name in candidates:
        path = FONT_DIR / name
        if not path.is_file():
            continue
        tf = _load_typeface(path)
        if tf is None and name.lower().endswith(".woff"):
            logger.warning(f"[miao-render] woff 字体 {name} 加载失败，尝试 TTF 兜底")
        if tf is not None:
            break
    if tf is None:
        logger.warning(f"[miao-render] {kind} 字体全部加载失败，使用 skia 默认字体")
        tf = skia.Typeface(None)
    _TYPEFACES[kind] = tf
    return tf


def font(size: float, kind: str = "default") -> skia.Font:
    """获取字体。kind: default(汉仪文黑) / title(NZBZ 标题) / number(tttgbnumber 数字)"""
    return skia.Font(_typeface(kind), float(size))


# tttgbnumber 只有 195 个字形（纯数字字库），含字母/中文的串不能用
_NUMBER_RE = re.compile(r"^[0-9.,]+$")


def num_font(text: object, size: float) -> skia.Font:
    """纯数字串用 tttgbnumber，其余退回 default"""
    return font(size, "number" if _NUMBER_RE.match(str(text)) else "default")


# ---------------------------------------------------------------------------
# 文字测量与绘制
# ---------------------------------------------------------------------------


def measure(text: str, f: skia.Font) -> float:
    if not text:
        return 0.0
    return f.measureText(str(text), skia.TextEncoding.kUTF8)


def text_height(f: skia.Font) -> float:
    m = f.getMetrics()
    return m.fDescent - m.fAscent


def baseline_center(y: float, height: float, f: skia.Font) -> float:
    """让文字在 [y, y+height] 区域内垂直居中的 baseline"""
    m = f.getMetrics()
    return y + (height - (m.fDescent - m.fAscent)) / 2 - m.fAscent


def draw_text(canvas: skia.Canvas, text: object, x: float, y: float, f: skia.Font, c: int) -> float:
    """左对齐绘制（y 为 baseline），返回文字宽度"""
    text = str(text)
    canvas.drawString(text, x, y, f, skia.Paint(AntiAlias=True, Color=c))
    return measure(text, f)


def draw_text_center(canvas: skia.Canvas, text: object, cx: float, y: float, f: skia.Font, c: int) -> None:
    """水平居中绘制（y 为 baseline）"""
    text = str(text)
    canvas.drawString(text, cx - measure(text, f) / 2, y, f, skia.Paint(AntiAlias=True, Color=c))


def ellipsize(text: str, f: skia.Font, max_w: float) -> str:
    """按宽度截断文本，超出加 …"""
    text = str(text)
    if measure(text, f) <= max_w:
        return text
    ell = "…"
    while text and measure(text + ell, f) > max_w:
        text = text[:-1]
    return text + ell


def draw_text_ellipsis(
    canvas: skia.Canvas, text: object, x: float, y: float, max_w: float, f: skia.Font, c: int
) -> float:
    """左对齐绘制并省略截断（y 为 baseline），返回实际绘制宽度"""
    t = ellipsize(str(text), f, max_w)
    return draw_text(canvas, t, x, y, f, c)


# ---------------------------------------------------------------------------
# 形状绘制助手
# ---------------------------------------------------------------------------


def rrect(x: float, y: float, w: float, h: float, r: float) -> skia.RRect:
    return skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r)


def draw_rounded(canvas: skia.Canvas, x: float, y: float, w: float, h: float, r: float, c: int) -> None:
    canvas.drawRRect(rrect(x, y, w, h, r), skia.Paint(AntiAlias=True, Color=c))


def draw_shadow(
    canvas: skia.Canvas, rr: skia.RRect, blur: float = 12, c: int | None = None, dy: float = 4
) -> None:
    """柔和阴影（MaskFilter blur），先画在主体下层"""
    paint = skia.Paint(AntiAlias=True, Color=c if c is not None else color("#55000000"))
    paint.setMaskFilter(skia.MaskFilter.MakeBlur(skia.BlurStyle.kNormal_BlurStyle, blur / 2))
    b = rr.rect()
    canvas.drawRRect(
        rrect(b.x(), b.y() + dy, b.width(), b.height(), rr.getSimpleRadii().x()),
        paint,
    )


def draw_gradient(
    canvas: skia.Canvas,
    x: float,
    y: float,
    w: float,
    h: float,
    colors: list[int],
    r: float = 0,
    diagonal: bool = True,
) -> None:
    """线性渐变填充矩形（默认对角线方向）"""
    pts = [(x, y), (x + w, y + h)] if diagonal else [(x, y), (x, y + h)]
    paint = skia.Paint(AntiAlias=True)
    paint.setShader(skia.GradientShader.MakeLinear(pts, colors, None, skia.TileMode.kClamp))
    if r > 0:
        canvas.drawRRect(rrect(x, y, w, h, r), paint)
    else:
        canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint)


def _star_path(cx: float, cy: float, r: float) -> skia.Path:
    path = skia.Path()
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        px, py = cx + rr * math.cos(angle), cy + rr * math.sin(angle)
        if i == 0:
            path.moveTo(px, py)
        else:
            path.lineTo(px, py)
    path.close()
    return path


def draw_star(canvas: skia.Canvas, cx: float, cy: float, r: float, c: int) -> None:
    canvas.drawPath(_star_path(cx, cy, r), skia.Paint(AntiAlias=True, Color=c))


def draw_stars(canvas: skia.Canvas, cx: float, cy: float, count: int, r: float = 8, gap: float | None = None) -> None:
    """水平排列 count 颗星星（5 金 / 4 紫 / 3 蓝），cx 为整排中心"""
    gap = gap if gap is not None else r * 2.4
    c = color(STAR_COLORS.get(count, STAR_COLORS[5]))
    start = cx - (count - 1) * gap / 2
    for i in range(count):
        draw_star(canvas, start + i * gap, cy, r, c)


# ---------------------------------------------------------------------------
# 图片：本地包内资源 / 远程下载 + 缓存
# ---------------------------------------------------------------------------

_IMG_MEM: dict[str, skia.Image | None] = {}
_HTTP_CLIENT: httpx.AsyncClient | None = None


def get_local_image(rel: str) -> skia.Image | None:
    """读取包内 resources 下的图片（占位图等），不存在返回 None

    miao 的 "gacha/imgs/xxx" 相对路径映射到本包的 resources/imgs/xxx
    """
    rel = str(rel or "").lstrip("/")
    if not rel:
        return None
    candidates = [RES_DIR / rel]
    if rel.startswith("gacha/imgs/"):
        candidates.append(RES_DIR / "imgs" / rel[len("gacha/imgs/"):])
    for path in candidates:
        try:
            if path.is_file():
                img = skia.Image.MakeFromEncoded(path.read_bytes())
                if img:
                    return img
        except Exception as e:
            logger.warning(f"[miao-render] 本地图片读取失败 {rel}: {e}")
    return None


def _mirror() -> str:
    """静态资源镜像地址（config.miao_res_mirror），nonebot 未初始化时用默认值"""
    try:
        from nonebot import get_plugin_config

        from ..config import Config

        return get_plugin_config(Config).miao_res_mirror.rstrip("/")
    except Exception:
        return "https://cdn.jsdelivr.net/gh/yoimiya-kokomi/miao-plugin@master/resources"


def _cache_dir() -> Path:
    """图片缓存目录（localstore 插件缓存目录/images，延迟导入）"""
    from nonebot_plugin_localstore import get_plugin_cache_dir

    return Path(get_plugin_cache_dir()) / "images"


def _decode(data: bytes) -> skia.Image | None:
    img = skia.Image.MakeFromEncoded(data)
    if img:
        try:
            img = img.makeRasterImage() or img
        except Exception:
            pass
    return img


async def _client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=15, headers={"User-Agent": "nonebot-plugin-miao"})
    return _HTTP_CLIENT


async def fetch_image(rel_path: str) -> skia.Image | None:
    """按相对 resources 的路径取图：包内资源 > 磁盘缓存 > 远程下载，失败返回 None"""
    rel = str(rel_path or "").lstrip("/")
    if not rel:
        return None
    if rel in _IMG_MEM:
        return _IMG_MEM[rel]

    img = get_local_image(rel)
    if img is None:
        # 磁盘缓存（localstore 在非插件上下文下取不到目录，降级为仅内存缓存）
        cache_file = None
        try:
            suffix = Path(rel).suffix or ".webp"
            cache_file = _cache_dir() / f"{hashlib.md5(rel.encode()).hexdigest()}{suffix}"
            if cache_file.is_file():
                img = _decode(cache_file.read_bytes())
        except Exception as e:
            logger.debug(f"[miao-render] 图片缓存目录不可用: {e}")
            cache_file = None
        if img is None:
            url = f"{_mirror()}/{rel}"
            try:
                resp = await (await _client()).get(url)
                if resp.status_code == 200 and resp.content:
                    img = _decode(resp.content)
                    if img and cache_file is not None:
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        cache_file.write_bytes(resp.content)
                else:
                    logger.warning(f"[miao-render] 图片下载失败 HTTP {resp.status_code}: {url}")
            except Exception as e:
                logger.warning(f"[miao-render] 图片获取失败 {rel}: {e}")
                img = None
    _IMG_MEM[rel] = img
    return img


def draw_image_cover(
    canvas: skia.Canvas, img: skia.Image, x: float, y: float, w: float, h: float, r: float = 0
) -> None:
    """cover 模式把图片绘制进目标区域（居中裁剪），r>0 时按圆角裁剪"""
    iw, ih = max(1, img.width()), max(1, img.height())
    scale = max(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
    canvas.save()
    if r > 0:
        canvas.clipRRect(rrect(x, y, w, h, r), skia.ClipOp.kIntersect, True)
    else:
        canvas.clipRect(skia.Rect.MakeXYWH(x, y, w, h), skia.ClipOp.kIntersect, True)
    canvas.drawImageRect(img, skia.Rect.MakeXYWH(dx, dy, dw, dh), skia.SamplingOptions(), skia.Paint(AntiAlias=True))
    canvas.restore()


def draw_image_or_placeholder(
    canvas: skia.Canvas,
    img: skia.Image | None,
    rect: tuple[float, float, float, float],
    label: str = "",
    r: float = 8,
) -> None:
    """绘制图片；img 为 None 时画圆角灰底 + 名字首字占位"""
    x, y, w, h = rect
    if img is not None:
        draw_image_cover(canvas, img, x, y, w, h, r)
        return
    draw_rounded(canvas, x, y, w, h, r, color("#4a4f5a"))
    ch = (str(label).strip() or "?")[0]
    f = font(min(w, h) * 0.42)
    draw_text_center(canvas, ch, x + w / 2, baseline_center(y, h, f), f, color("#e8e8e8"))


# ---------------------------------------------------------------------------
# 两遍法卡片渲染
# ---------------------------------------------------------------------------


def render_card(width: int, builder: Callable[[skia.Canvas, int, int], int]) -> bytes:
    """两遍法渲染：builder(canvas, width, height) 返回使用高度

    第一遍用窄 surface dry-run 测高（height=0，builder 应跳过整卡背景），
    第二遍按实际高度建 surface 正式绘制，输出 PNG bytes。
    """
    probe = skia.Surface(width, 16)
    height = max(1, int(builder(probe.getCanvas(), width, 0)))
    surface = skia.Surface(width, height)
    builder(surface.getCanvas(), width, height)
    data = surface.makeImageSnapshot().encodeToData(skia.EncodedImageFormat.kPNG, 100)
    return bytes(data)
