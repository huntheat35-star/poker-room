"""
🃏 Poker Room Server — v3
FastAPI + WebSocket + Telegram Bot
"""
import os, json, hmac, hashlib, asyncio, logging
from urllib.parse import parse_qs, unquote
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from game_engine import create_room, Engine, Table, Phase

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "8000"))
DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
BASE = os.getenv("WEBAPP_URL", "")

def url():
    if BASE: return BASE.rstrip("/")
    if DOMAIN: return f"https://{DOMAIN}"
    return f"http://localhost:{PORT}"

rooms: dict[str, tuple[Table, Engine]] = {}
conns: dict[str, dict[str, WebSocket]] = {}

# ── Bot ──
async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    if c.type == "private":
        await update.message.reply_text("👋 Додайте бота в груповий чат і напишіть /newgame"); return
    a = ctx.args or []
    sb = float(a[0]) if len(a)>0 else 1
    bb = float(a[1]) if len(a)>1 else sb*2
    cur = a[2] if len(a)>2 else "$"
    t, e = create_room(sb=sb, bb=bb, currency=cur)
    rooms[t.id] = (t, e); conns[t.id] = {}
    link = f"{url()}/webapp/?room={t.id}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🃏 Сісти за стіл", url=link)]])
    await update.message.reply_text(
        f"🃏 <b>Покер-рум створено!</b>\n\nБлайнди: {cur}{sb:.0f}/{cur}{bb:.0f}\n"
        f"Бай-ін: {cur}{bb*10:.0f}–{cur}{bb*100:.0f}\nБез рейку 💯",
        parse_mode="HTML", reply_markup=kb)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await update.message.reply_text("👋 Додайте мене в груповий чат з друзями і напишіть /newgame")
    else: await cmd_new(update, ctx)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🃏 <b>Poker Room</b>\n\n/newgame — стіл 1/2\n/newgame 5 10 — блайнди 5/10\n"
        "/newgame 5 10 ₴ — гривні\n/help — допомога", parse_mode="HTML")

# ── Auth ──
def check_tg(data, token):
    if not data or not token: return None
    try:
        p = parse_qs(data); h = p.get("hash",[None])[0]
        if not h: return None
        pairs = sorted(f"{k}={unquote(v[0])}" for k,v in p.items() if k != "hash")
        sec = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        if hmac.new(sec, "\n".join(pairs).encode(), hashlib.sha256).hexdigest() != h: return None
        u = p.get("user",[None])[0]
        return json.loads(unquote(u)) if u else {}
    except: return None

# ── WS ──
async def bcast(rid):
    if rid not in rooms or rid not in conns: return
    t = rooms[rid][0]
    for uid, ws in list(conns[rid].items()):
        try: await ws.send_json({"type":"state","data":t.serialize(viewer=uid)})
        except: pass

async def msg_to(rid, uid, m):
    try: await conns[rid][uid].send_json(m)
    except: pass

async def handle(rid, uid, name, data):
    if rid not in rooms: return
    t, e = rooms[rid]; tp = data.get("type","")

    if tp == "join":
        r = e.join(uid, name, data.get("buy_in", t.bb*50))
        if "error" in r: await msg_to(rid, uid, {"type":"error","message":r["error"]})
        else: await bcast(rid)

    elif tp == "deal":
        if not e.can_deal():
            await msg_to(rid, uid, {"type":"error","message":"Мінімум 2 гравці"}); return
        if t.phase not in (Phase.WAITING, Phase.SHOWDOWN):
            await msg_to(rid, uid, {"type":"error","message":"Роздача йде"}); return
        r = e.deal()
        if "error" in r: await msg_to(rid, uid, {"type":"error","message":r["error"]})
        else: await bcast(rid)

    elif tp == "action":
        r = e.act(uid, data.get("action",""), data.get("amount",0))
        if "error" in r: await msg_to(rid, uid, {"type":"error","message":r["error"]})
        else: await bcast(rid)

    elif tp == "rebuy":
        r = e.rebuy(uid, data.get("amount", t.bb*50))
        if "error" in r: await msg_to(rid, uid, {"type":"error","message":r["error"]})
        else: await bcast(rid)

    elif tp == "leave":
        e.leave(uid); await bcast(rid)

# ── App ──
tg_app = None
@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    if TOKEN:
        tg_app = Application.builder().token(TOKEN).build()
        tg_app.add_handler(CommandHandler("start", cmd_start))
        tg_app.add_handler(CommandHandler("newgame", cmd_new))
        tg_app.add_handler(CommandHandler("help", cmd_help))
        await tg_app.initialize(); await tg_app.start()
        await tg_app.updater.start_polling()
        log.info(f"✅ Bot | {url()}")
    else: log.warning("⚠️ No token")
    yield
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop(); await tg_app.shutdown()

app = FastAPI(title="Poker", lifespan=lifespan)
wd = Path(__file__).parent / "webapp"
if wd.exists(): app.mount("/webapp", StaticFiles(directory=str(wd), html=True), name="webapp")

@app.get("/")
async def root():
    return {"status":"ok","message":"🃏 Poker Room is running!","url":url(),"rooms":len(rooms)}

@app.get("/health")
async def health(): return {"status":"healthy"}

@app.websocket("/ws/{rid}")
async def ws_ep(websocket: WebSocket, rid: str):
    await websocket.accept()
    try: auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except: await websocket.close(code=4001); return

    ui = check_tg(auth.get("init_data",""), TOKEN) if TOKEN else None
    if not ui: ui = {"id": auth.get("user_id", f"d_{id(websocket)}"), "first_name": auth.get("name","Player")}
    uid = str(ui.get("id","")); name = ui.get("first_name","Player")
    if not uid: await websocket.close(code=4002); return

    if rid not in rooms:
        t, e = create_room(); t.id = rid; rooms[rid] = (t, e); conns[rid] = {}
    conns.setdefault(rid, {})[uid] = websocket

    t = rooms[rid][0]; p = t.get(uid)
    if p: p.connected = True

    await websocket.send_json({"type":"state","data":t.serialize(viewer=uid)})
    await websocket.send_json({"type":"auth_ok","user_id":uid,"name":name})

    try:
        while True: await handle(rid, uid, name, await websocket.receive_json())
    except WebSocketDisconnect: pass
    except Exception as ex: log.error(f"WS: {ex}")
    finally:
        conns.get(rid,{}).pop(uid, None)
        if rid in rooms:
            pp = rooms[rid][0].get(uid)
            if pp: pp.connected = False
            await bcast(rid)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=PORT)
