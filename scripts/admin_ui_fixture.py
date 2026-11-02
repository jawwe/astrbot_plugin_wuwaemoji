"""Serve synthetic, temporary fixtures for browser tests; never load real credentials."""

import argparse
import asyncio
import io
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path


def create_app(astrbot_root: Path):
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.requests import Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from PIL import Image, ImageDraw

    temporary = tempfile.TemporaryDirectory(prefix="wuwa-admin-ui-")
    os.environ["ASTRBOT_ROOT"] = temporary.name
    plugin_root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(plugin_root), str(astrbot_root)]
    from astrbot.api.web import PluginRequest, bind_request_context

    from main import WuwaEmojiPlugin

    plugin = WuwaEmojiPlugin(None, {"cache_batch_size": 2})
    images = {}

    def make_item(identifier, number):
        image = Image.new(
            "RGB", (260, 230), ["#fff4d4", "#e0f2eb", "#ece4ff", "#e0efff"][number % 4]
        )
        draw = ImageDraw.Draw(image)
        draw.ellipse((67, 38, 194, 167), fill="#ffdc92", outline="#3f5a5f", width=4)
        draw.ellipse((99, 80, 106, 89), fill="#3f5a5f")
        draw.ellipse((153, 80, 160, 89), fill="#3f5a5f")
        draw.arc((105, 94, 157, 134), 0, 180, fill="#3f5a5f", width=4)
        draw.text((90, 188), f"NICE! {identifier}", fill="#3f5a5f")
        output = io.BytesIO()
        image.save(output, "PNG")
        images[identifier] = output.getvalue()
        return {
            "id": identifier,
            "url": "https://emoji.wuwa.games/media?ticket=synthetic",
            "format": "png",
            "characters": [{"name": "爱弥斯", "slug": "aimisi"}],
            "pack": {
                "title": [
                    "爱弥斯 · 日常问候",
                    "爱弥斯 · 开心时刻",
                    "爱弥斯 · 小小心情",
                    "爱弥斯 · 点赞表情",
                ][number % 4]
            },
        }

    async def api(endpoint, params):
        await asyncio.sleep(0.04)
        page = params.get("page", 1)
        size = params.get("size", 20)
        items = [
            make_item(f"remote-{page}-{index}", index) for index in range(min(8, size))
        ]
        return {"items": items, "page": page, "total": 24, "hasNext": page < 3}

    async def download(item):
        await asyncio.sleep(0.18)
        return images[item["id"]]

    plugin.request_api = api
    plugin.download_image = download

    @asynccontextmanager
    async def lifespan(app):
        for index in range(6):
            record = make_item(f"local-{index}", index)
            await plugin.cache.put(
                record, images[record["id"]], ["开心", "问候", "赞同"][index % 3]
            )
        yield
        await plugin.terminate()
        temporary.cleanup()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["null"], allow_methods=["*"])
    app.state.fail_refresh = False

    @app.get("/", response_class=HTMLResponse)
    async def parent():
        return """<!doctype html><html><head><meta charset="utf-8"><title>Wuwa fixture</title></head><body data-wuwa-fixture style="margin:0"><iframe id="view" src="/view/" sandbox="allow-scripts allow-forms allow-downloads" style="width:100%;height:100vh;border:0"></iframe><script>
const frame=document.querySelector('iframe');
const context={pluginName:'astrbot_plugin_wuwaemoji',isDark:false,locale:'zh-CN'};
window.setTheme=(dark)=>{context.isDark=dark;frame.contentWindow.postMessage({channel:'astrbot-plugin-page',kind:'context',context},'*')};
window.addEventListener('message',async(event)=>{
 if(event.source!==frame.contentWindow||event.data?.channel!=='astrbot-plugin-page')return;
 const message=event.data;
 if(message.kind==='ready'){window.setTheme(false);return}
 if(message.kind!=='request')return;
 try{
  if(!/^admin\\/[a-z/]+$/.test(message.endpoint))throw Error('Invalid fixture endpoint');
  const url=new URL('/fixture-api/'+message.endpoint,location.origin);
  const options={headers:{'Content-Type':'application/json','X-Fixture-Session':'fixture'}};
  if(message.action==='api:post'){options.method='POST';options.body=JSON.stringify(message.body)}
  else for(const [key,value] of Object.entries(message.params||{}))url.searchParams.set(key,value);
  const response=await fetch(url,options);const data=await response.json();
  if(!response.ok||data.status==='error')throw Error(data.message||'Fixture error');
  frame.contentWindow.postMessage({channel:'astrbot-plugin-page',kind:'response',requestId:message.requestId,ok:true,data},'*');
 }catch(error){frame.contentWindow.postMessage({channel:'astrbot-plugin-page',kind:'response',requestId:message.requestId,ok:false,error:error.message},'*')}
});
</script></body></html>"""

    @app.get("/view/", response_class=HTMLResponse)
    async def view():
        html = (plugin_root / "views/admin/index.html").read_text(encoding="utf-8")
        return html.replace(
            '<script type="module"',
            '<script src="/bridge-sdk.js"></script><script type="module"',
        )

    @app.get("/bridge-sdk.js")
    async def sdk():
        return FileResponse(
            astrbot_root / "astrbot/dashboard/plugin_page_bridge.js",
            media_type="application/javascript",
        )

    @app.post("/fixture/fail-refresh")
    async def fail_refresh():
        app.state.fail_refresh = True
        return {"ok": True}

    @app.api_route("/fixture-api/admin/{action:path}", methods=["GET", "POST"])
    async def dispatch(action: str, request_: Request):
        if action == "overview" and app.state.fail_refresh:
            app.state.fail_refresh = False
            return JSONResponse(
                {"message": "synthetic refresh failure"}, status_code=503
            )
        with bind_request_context(
            PluginRequest(
                request_,
                username="fixture-admin"
                if request_.headers.get("X-Fixture-Session") == "fixture"
                else None,
            )
        ):
            return await plugin.admin.handle(action)

    app.mount(
        "/view", StaticFiles(directory=plugin_root / "views/admin"), name="view-assets"
    )
    return app, uvicorn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astrbot-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    app, uvicorn = create_app(arguments.astrbot_root.resolve())
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, log_level="warning")
