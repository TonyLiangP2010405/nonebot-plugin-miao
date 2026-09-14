"""图片源故障回退与重试，避免永久文字占位。"""
import httpx
import skia

from nonebot_plugin_miao.render import base


async def test_fallback_and_retry(tmp_path, monkeypatch):
    png = bytes(skia.Surface(8, 8).makeImageSnapshot().encodeToData())
    requests = []
    available = False

    def respond(request):
        requests.append(request)
        if available and request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=png)
        return httpx.Response(503)

    monkeypatch.setattr(base, "_IMG_MEM", {})
    monkeypatch.setattr(base, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(base, "_mirror", lambda: "https://broken.example/resources")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(base, "_HTTP_CLIENT", client)
        path = "meta-gs/material/specialty/湖光铃兰.webp"
        assert await base.fetch_image(path) is None
        available = True
        assert await base.fetch_image(path) is not None
        assert len(requests) == 4
        assert await base.fetch_image(path) is not None
        assert len(requests) == 4


async def test_material_images(monkeypatch):
    from nonebot_plugin_miao.render.encyclopedia import _material_images

    paths = []

    async def fetch(path):
        paths.append(path)
        return None

    monkeypatch.setattr(base, "fetch_image", fetch)
    cards = await _material_images({"specialty": "湖光铃兰", "gem": "涤净青金"})
    assert paths == ["meta-gs/material/specialty/湖光铃兰.webp", "meta-gs/material/gem/涤净青金.webp"]
    assert [name for name, _ in cards] == ["湖光铃兰", "涤净青金"]


async def test_character_icons_use_upstream_mapping(monkeypatch):
    from nonebot_plugin_miao.core import meta
    from nonebot_plugin_miao.render.encyclopedia import render_character_encyclopedia, talent_image_path

    character = meta.get_character("芙宁娜", "gs")
    assert talent_image_path(character, "a") == "common/item/atk-sword.webp"
    assert talent_image_path(character, "e").endswith("/icons/cons-5.webp")
    assert talent_image_path(character, "q").endswith("/icons/cons-3.webp")
    paths = []

    async def fetch(path):
        paths.append(path)
        return None

    monkeypatch.setattr(base, "fetch_image", fetch)
    await render_character_encyclopedia(character)
    for i in range(1, 7):
        assert paths.count(f"meta-gs/character/芙宁娜/icons/cons-{i}.webp") == 1
    for i in range(3):
        assert f"meta-gs/character/芙宁娜/icons/passive-{i}.webp" in paths
    assert not any("talent-" in path for path in paths)
