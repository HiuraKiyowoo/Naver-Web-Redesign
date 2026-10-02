#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web/main.py — WEB NAVER (SSR) untuk arsip novel sendiri.

Arsitektur:
  · FastAPI + Jinja2 (HTML dirender SERVER → ramah SEO)
  · Database: naver.db (SQLite, dibaca LANGSUNG -- tidak lewat HTTP API,
    supaya waktu muat cepat & tidak dobel rate-limit)
  · Tampilan: meniru UX novelpia (hero, kartu grid, rak geser),
    WARNA & SECTION mengikuti APK Naver:
        Bg #071018 · Header #0E1B26 · Aksen Cyan #049DFD
  · Tailwind dibaca dari file LOKAL (assets/tw.css) — kalau tidak ada,
    jatuh ke CDN. JS dari assets/web.js (bukan inline, biar CSP aman).

Jalankan:
    uvicorn main:app --host 127.0.0.1 --port 8100
"""
import json
import os
import re
import sqlite3
import rate_limit
import secrets
import urllib.request
import urllib.parse
import datetime


# ── env bot Telegram (dibaca dari berkas, TIDAK pernah dicetak) ──
def _muat_tele():
    jalur = '/root/.hermes/tele-bot.env'
    isi = {}
    try:
        for baris in open(jalur):
            b = baris.strip()
            if b.startswith('export '):
                b = b[7:]
            if '=' in b and not b.startswith('#'):
                k, v = b.split('=', 1)
                isi[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return isi.get('TELE_TOKEN', ''), isi.get('TELE_CHAT', '')

TELE_TOKEN, TELE_CHAT = _muat_tele()

# ⚠️ muat kunci Firebase dari ENV berkas (TIDAK pernah dicetak)
try:
    import muat_env
    muat_env.muat()
except Exception:
    pass

import auth
import admin
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               PlainTextResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
DB = ROOT / "naver.db"

app = FastAPI(title="Naver Web", docs_url=None, redoc_url=None,
                  openapi_url=None)   # audit 1 Okt: openapi_url LUPA dimatikan -> bocor 10 KB peta route


# ═════════════════════════════════════════════════════════════
#  RATE LIMIT (audit 1 Okt, putaran 2) — disimpan di SQLite
# ═════════════════════════════════════════════════════════════
_RAPORT = {}


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    jalur = request.url.path

    # ── BLOKIR BOT SCRAPER AI (ClaudeBot, GPTBot, CCBot, dll) ──
    # Terbukti: ClaudeBot menyedot rak tanpa izin & mengabaikan robots.txt.
    ua = (request.headers.get("user-agent") or "").lower()
    for bot in ("claudebot", "anthropic-ai", "gptbot", "chatgpt-user", "ccbot",
                "google-extended", "bytespider", "petalbot", "amazonbot",
                "applebot-extended", "img2dataset", "diffbot", "omgili",
                "scrapy", "node-fetch", "okhttp", "headless", "phantomjs",
                "selenium", "puppeteer", "playwright"):
        if bot in ua:
            rate_limit.kena_honeypot(request)   # blokir 24 jam
            print(f"[BOT-BLOCK] {ua[:60]} -> diblokir", flush=True)
            return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)

    # ── HONEYPOT: link gaib, hanya scraper yang mengikutinya ──
    if jalur.startswith("/jangan-ke-sini") or jalur.startswith("/wp-login") \
            or jalur.startswith("/.git") or jalur.startswith("/xmlrpc"):
        ip = rate_limit.kena_honeypot(request)
        print(f"[HONEYPOT] {ip} menyentuh {jalur} -> blokir 24 jam", flush=True)
        return HTMLResponse("<h1>403</h1>", status_code=403)

    kena, sisa, sebab = rate_limit.periksa(
        request, jalur, batas_umum=240, batas_bab=10)
    if kena:
        _RAPORT[rate_limit.ip_asli(request)] = sebab
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
            "<h2>Terlalu banyak permintaan</h2>"
            f"<p>Coba lagi dalam <b>{sisa}</b> detik.</p></body></html>",
            status_code=429, headers={"Retry-After": str(sisa)})

    return await call_next(request)

# ═════════════════════════════════════════════════════════════
#  AUTH — baca cookie sesi (pengguna) & tamu di setiap permintaan
# ═════════════════════════════════════════════════════════════
@app.middleware("http")
async def _siapkan_pengguna(request: Request, call_next):
    """Tempelkan request.state.pengguna (kalau login) & .tamu (selalu)."""
    request.state.pengguna = None
    request.state.tamu = auth.tamu_sah(request.cookies.get(auth.COOKIE_TAMU))
    try:
        tok = request.cookies.get(auth.COOKIE_SESI)
        if tok:
            request.state.pengguna = auth.pengguna_dari_sesi(tok)
    except Exception:
        request.state.pengguna = None
    resp = await call_next(request)
    # pasang cookie TAMU kalau belum ada (httpOnly → tidak bisa dibaca JS)
    if not request.state.tamu and request.url.path not in ('/static',):
        baru = auth.tamu_baru()
        resp.set_cookie(auth.COOKIE_TAMU, auth.bungkus_tamu(baru),
                        max_age=60 * 60 * 24 * auth.UMUR_TAMU,
                        httponly=True, samesite='lax', secure=False)
    return resp
# ⚠️ BERKAS GAMBAR (cover & ilustrasi) DISAJIKAN LANGSUNG dari disk.
#    Dulu web TIDAK punya route /cover/* → beranda minta /cover/1987.webp
#    ke port 8100 → 404 → browser menampilkan ikon "kertas rusak" di
#    SEMUA kartu. Route ini yang memperbaikinya (tanpa lewat API 8000,
#    jadi lebih cepat & tidak ikut batas laju API).
GAMBAR = ROOT / "gambar"
COVER = ROOT / "cover"
if GAMBAR.exists():
    app.mount("/gambar", StaticFiles(directory=str(GAMBAR)), name="gambar")
if COVER.exists():
    app.mount("/cover", StaticFiles(directory=str(COVER)), name="cover")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
tpl = Jinja2Templates(directory=str(BASE / "templates"))




# ═══════════ PANEL ADMIN (rahasia) ═══════════
# Pembungkus tipis: setiap render template otomatis mendapat:
#   'admin'    → True/False (email termasuk admin?)
#   'pengguna' → dict pengguna dari sesi (nama/email/foto) atau None kalau tamu
# supaya laci (base.html → _drawer.html) menampilkan profil di SEMUA halaman
# tanpa perlu tiap route mengirim 'pengguna' sendiri-sendiri.
_TR_ASLI = tpl.TemplateResponse

# Admin bawaan (dipakai kalau env NAVER_ADMIN_EMAIL kosong)
_ADMIN_EMAIL = [x.strip().lower() for x in
                (os.environ.get("NAVER_ADMIN_EMAIL") or "hiura0012@gmail.com").split(",") if x.strip()]


def _admin_masuk(req) -> bool:
    """True kalau pengguna pada request ini emailnya termasuk admin."""
    try:
        p = getattr(getattr(req, "state", None), "pengguna", None)
        if not p:
            return False
        email = (p["email"] if not isinstance(p, dict) else p.get("email")) or ""
        return email.strip().lower() in _ADMIN_EMAIL
    except Exception:
        return False


def _TR(*a, **k):
    """Sisipkan admin=True/False ke konteks SEBELUM render.

    Bentuk yang didukung:
      (request, "x.html", {…})      ← bentuk baru (dipakai main.py)
      ("x.html", {"request": …})    ← bentuk lama
      (request, "x.html")           ← tanpa konteks
    """
    # 1. temukan request
    req = None
    if a and hasattr(a[0], "state"):
        req = a[0]

    # 2. temukan dict konteks (bukan request)
    idk = None
    for i, x in enumerate(a):
        if isinstance(x, dict) and not hasattr(x, "state"):
            idk = i
            break
    if idk is None and isinstance(k.get("context"), dict):
        idk = -1

    if req is None and idk is not None and idk >= 0:
        req = a[idk].get("request")

    nilai = bool(_admin_masuk(req))

    # pengguna dari sesi (dict: nama/email/foto/…) — None untuk tamu.
    # Hanya ditambahkan kalau route BELUM mengirim 'pengguna', supaya
    # route yang mengirim versi sendiri (masuk.html) tidak tertimpa.
    try:
        _peng = getattr(getattr(req, "state", None), "pengguna", None)
    except Exception:
        _peng = None

    def _tanpa_pengguna(d):
        return isinstance(d, dict) and "pengguna" not in d

    if idk == -1:
        ctx = {**k["context"]}
        if _tanpa_pengguna(ctx):
            ctx["pengguna"] = _peng
        k = {**k, "context": {**ctx, "admin": nilai}}
    elif idk is not None:
        d = {**a[idk]}
        if _tanpa_pengguna(d):
            d["pengguna"] = _peng
        a = a[:idk] + ({**d, "admin": nilai},) + a[idk + 1:]
    elif len(a) >= 2:
        a = (a[0], {"request": req, "admin": nilai, "pengguna": _peng}) + a[1:]

    return _TR_ASLI(*a, **k)


tpl.TemplateResponse = _TR
# ═══════════════════════════════════════════════

@app.middleware("http")
async def atur_cache(request, call_next):
    """Berkas /static: cache 1 jam + wajib revalidate.

    Cloudflare sebelumnya menyimpan CSS 4 jam (`max-age=14400`) → tampilan
    lama bertahan lama. Sekarang 1 jam dan `must-revalidate` supaya berkas
    yang berubah cepat terpakai. HTML tidak di-cache (selalu segar).
    """
    jawab = await call_next(request)
    if request.url.path.startswith("/static/"):
        jawab.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
    elif jawab.headers.get("content-type", "").startswith("text/html"):
        jawab.headers["Cache-Control"] = "no-cache"
    return jawab


def _versi_aset() -> str:
    """Versi aset ikut waktu-ubah berkas CSS/JS.

    Dipakai di base.html sebagai `?v=` supaya browser & Cloudflare TIDAK
    menyajikan CSS basi (pelajaran 28 Sep: CF cache `max-age=14400` bikin
    tampilan lama bertahan sampai 4 jam walau berkas sudah diperbarui).
    """
    try:
        t = max((BASE / "static" / n).stat().st_mtime
                for n in ("miruro.css", "web.css", "web.js")
                if (BASE / "static" / n).exists())
        return str(int(t))
    except Exception:
        return "1"


tpl.env.globals["versi_aset"] = _versi_aset


def tipe_lencana(n) -> str:
    """'LN' / 'WN' / '' — lencana tipe untuk kartu kipas (section Jelajahi Acak).

    Dipakai di _kipas.html. Ambil dari field `tipe` milik dB (web 320 · light 149).
    """
    t = (n.get("tipe") or "").lower() if isinstance(n, dict) else ""
    if "light" in t:
        return "LN"
    if "web" in t:
        return "WN"
    return ""


tpl.env.globals["tipe_lencana"] = tipe_lencana

# ── konfigurasi situs (dipakai di <head> utk SEO) ──
SITUS = os.environ.get("NAVER_SITUS", "https://navernovel.my.id")

# ── Domain yang dikenal (1 Okt: pindah utama ke navernovel.my.id / CF) ──
DOMAIN_DIKENAL = (
    "navernovel.my.id", "www.navernovel.my.id",
    # naver.zone.id DIMATIKAN 1 Okt (user: "naver zone matiin aja").
    # Kalau mau dihidupkan lagi: buang tanda # di 2 baris di bawah.
    # "naver.zone.id", "www.naver.zone.id",
)


def situs_untuk(request) -> str:
    """Pilih domain untuk canonical/OG/sitemap sesuai yang diakses pengunjung.
    Header Host diisi proxy/CF (bukan bisa dipalsukan sembarangan); kalau
    hostnya tidak dikenal, pakai SITUS default."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in DOMAIN_DIKENAL:
        skema = "https" if (request.headers.get("x-forwarded-proto") == "https"
                            or request.url.scheme == "https") else "http"
        return f"{skema}://{host}"
    return SITUS
NAMA = "Naver Novel"
DESK = ("Baca novel terjemahan Indonesia gratis: web novel & light novel "
        "lengkap dengan ilustrasi, update tiap hari.")


def db():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=25)
    con.row_factory = sqlite3.Row
    return con


def kartu(r, dasar="") -> dict:
    """Bentuk 1 novel jadi kartu tampilan."""
    return {
        "id": r["id"], "slug": r["slug"], "judul": r["judul"],
        "cover": f"/cover/{r['id']}.webp" if r["cover_webp"] else None,
        "jumlah_bab": r["jumlah_bab"], "rating": r["rating"],
        "status": r["status"], "tipe": r["tipe"], "country": r["country"],
        # bookmark dipakai kartu gaya novelpia ("1.6K pembaca") — 148/495 novel
        # bookmark-nya 0, jadi kartu wajib cek >0 dulu sebelum menampilkannya.
        "bookmark": r["bookmark"] if "bookmark" in r.keys() else None,
        "url": f"/novel/{r['slug']}",
    }


def ambil(q, par=(), satu=False):
    con = db()
    try:
        cur = con.execute(q, par)
        r = cur.fetchone() if satu else cur.fetchall()
        return r
    finally:
        con.close()


# ═════════════════════════════════════════════════════════════
#  HALAMAN: BERANDA
# ═════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
def beranda(request: Request):
    situs = situs_untuk(request)
    n = ambil("SELECT COUNT(*) c FROM novel", satu=True)["c"]
    b = ambil("SELECT COUNT(*) c FROM bab", satu=True)["c"]
    # HERO: novel rating tertinggi — SEMUA sudah berating (rating >= 4.0).
    # ⚠️ 148 novel ratingnya 0 → kalau tidak disaring, hero bisa jadi kosong.
    hero = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND COALESCE(rating,0) > 0 "
        "ORDER BY rating DESC, COALESCE(bookmark,0) DESC LIMIT 5")]
    # ═══════════════════════════════════════════════════════════════════
    #  ISI SECTION — HARUS JUJUR (aturan user 29 Sep: "isi dengan jujur
    #  sesuai api kita"). Tiap judul di bawah WAJIB dibuktikan query-nya.
    #  Jejak data dB: rating terisi 347/496 · bookmark>0 345/496 ·
    #  tipe light 149 / web 320 · status complete 207 / ongoing 289.
    # ═══════════════════════════════════════════════════════════════════
    # PALING PANJANG — DIHAPUS atas permintaan user 29 Sep ("gajelas").
    # BERILUSTRASI — DIHAPUS juga atas permintaan user.
    # DARI JEPANG / DARI KOREA — DIHAPUS atas permintaan user 29 Sep ("hapus aja").
    # WEB NOVEL / LIGHT NOVEL — tipe nyata dari dB (web 320 · light 149). DIPERTAHANKAN.
    light = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND LOWER(COALESCE(tipe,'')) LIKE '%light%' "
        "ORDER BY COALESCE(rating,0) DESC, jumlah_bab DESC LIMIT 12")]
    webnovel = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND LOWER(COALESCE(tipe,'')) LIKE '%web%' "
        "ORDER BY COALESCE(rating,0) DESC, jumlah_bab DESC LIMIT 12")]
    # RANDOM: tetap sama tiap hari (seed tanggal) supaya tidak berkedip saat reload
    seed = int(time.strftime("%Y%m%d"))
    acak = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "ORDER BY (id * ?) % 10007 LIMIT 12", (seed,))]
    # BARU DITAMBAH: created_at terbaru (TERISI SEMUA 496/496, tidak seperti
    # waktu_update yang cuma 17/496 → itu sebabnya dulu tidak dipakai).
    baru = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "ORDER BY created_at DESC, id DESC LIMIT 12")]
    # UPDATE TERBARU: pakai bab.tanggal (waktu_update KOSONG di 479/496 novel).
    # ⚠️ JANGAN "GROUP BY n.id + MAX()" → 12 dtk (pindai 111.689 bab).
    # ⚠️ JANGAN cuma 400 bab: 400 bab terakhir cuma dari 7 NOVEL (satu novel
    #    bisa 110 bab berurutan) → section isinya nyaris 1 novel.
    #    JUJUR = ambil 6.000 bab (kena index idx_bab_tanggal) → 58 novel unik.
    update, sudah = [], set()
    for r in ambil("""
        SELECT b.tanggal AS tg, n.*
        FROM bab b JOIN novel n ON n.id = b.novel_id
        WHERE b.tanggal IS NOT NULL AND b.tanggal != ''
          AND n.cover_webp IS NOT NULL AND n.judul != ''
        ORDER BY b.tanggal DESC LIMIT 6000"""):
        if r["id"] in sudah:
            continue
        sudah.add(r["id"])
        k = kartu(r)
        k["tanggal"] = (r["tg"] or "")[:10]
        k["c_bab"] = r["jumlah_bab"]
        # nomor urut 1..12 — dipakai templat daftar 2 kolom (beranda.html).
        # ⚠️ JANGAN hitung di Jinja: `dict()` & `list.append()` TIDAK ada di
        #    Jinja → nomor keluar KOSONG (pelajaran 29 Sep).
        k["no"] = len(update) + 1
        update.append(k)
        if len(update) >= 16:
            break

    # ── JELAJAHI ACAK: hitung posisi kipas DI SINI, jangan di Jinja ──
    # ⚠️ Jinja tidak bisa `dict()`/`append`/kurung kurawal → pelajaran 29 Sep.
    # Rumus user: offset = i-1.5 · translateY(|offset|*7) · rotate(offset*7)
    # ⚠️ Posisi HORIZONTAL sekarang ditentukan CSS dari `--i` (tidak lagi
    #    `margin-left` bertingkat) supaya kipas benar-benar di tengah &
    #    kartunya ikut membesar di layar lebar (pelajaran 29 Sep).
    for j, k in enumerate(acak[:5]):
        d = j - 1.5
        k["i"] = j
        k["lr"] = j * 52          # masih dikirim, tapi tidak dipakai lagi
        k["sudut"] = round(d * 7, 1)
        k["geser"] = round(abs(d) * 7, 1)
        k["z"] = j
    # TAMAT: status Completed (207 novel — judul jujur).
    tamat = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND LOWER(COALESCE(status,'')) LIKE '%complete%' "
        "ORDER BY COALESCE(rating,0) DESC, jumlah_bab DESC LIMIT 12")]
    # MASIH JALAN: status Ongoing (289 novel) — dulu ada tapi tidak dipakai.
    ongoing = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND LOWER(COALESCE(status,'')) LIKE '%ongoing%' "
        "ORDER BY COALESCE(rating,0) DESC, COALESCE(bookmark,0) DESC LIMIT 12")]
    genre = [dict(r) for r in ambil("""
        SELECT g.slug, g.nama, COUNT(ng.novel_id) jumlah FROM genre g
        JOIN novel_genre ng ON ng.genre_id = g.id
        GROUP BY g.id ORDER BY jumlah DESC LIMIT 18""")]
    tag = [dict(r) for r in ambil(
        "SELECT slug, nama, jumlah_novel FROM tag ORDER BY jumlah_novel DESC LIMIT 24")]

    # RATING TERTINGGI — ambang 4.2 (112 novel, SEMUA benar-benar berating).
    # Dulu ambang 4.5 → cuma 43 novel, dan daftarnya bercampur novel rating 0
    # yang ikut terurut di bawah → tidak jujur.
    premium = [kartu(r) for r in ambil(
        "SELECT * FROM novel WHERE cover_webp IS NOT NULL AND judul != '' "
        "AND COALESCE(rating,0) >= 4.2 "
        "ORDER BY rating DESC, jumlah_bab DESC LIMIT 12")]

    # ═══════════════════════════════════════════════════════════════
    #  MINGGU INI  —  section sorotan gaya "Opening Story" (3 cover)
    #  ⚠️ status di dB = 'Completed' (C BESAR!) — 'completed' hasilnya 0.
    #  Saring cover_webp IS NOT NULL → 21 dari 207 novel Completed tidak
    #  punya cover (kalau tidak disaring, muncul gambar rusak).
    #  Daftar diambil 8 novel; JS (web.js) yang menggeser Prev/Next dengan
    #  memutar indeks melingkar, dan cover tetangga ikut berputar.
    # ═══════════════════════════════════════════════════════════════
    minggu = []
    for r in ambil(
        "SELECT id, slug, judul, COALESCE(NULLIF(author,''), penulis) AS penulis, "
        "sinopsis, jumlah_bab, rating FROM novel "
        "WHERE status='Completed' AND cover_webp IS NOT NULL AND judul != '' "
        "ORDER BY rating DESC, jumlah_bab DESC LIMIT 8"):
        mg = dict(r)
        mg["cover"] = f"/cover/{r['id']}.webp"
        mg["url"] = f"/novel/{r['slug']}"
        mg["tags"] = [t["nama"] for t in ambil(
            "SELECT t.nama FROM tag t JOIN novel_tag nt ON nt.tag_id=t.id "
            "WHERE nt.novel_id=? ORDER BY t.jumlah_novel DESC LIMIT 5", (r["id"],))]
        mg["desk"] = (r["sinopsis"] or "").strip()
        mg["penulis"] = mg["penulis"] or "Tanpa Penulis"
        minggu.append(mg)

    return tpl.TemplateResponse(request, "beranda.html", {
        "situs": situs, "halaman": "beranda", "nama": NAMA, "desk": DESK,
        "kanon": situs + "/", "total_novel": n, "total_bab": b,
        "hero": hero, "acak": acak, "baru": baru,
        "update": update, "tamat": tamat, "ongoing": ongoing,
        "genre": genre, "tag": tag, "premium": premium,
        "light": light, "webnovel": webnovel, "minggu": minggu,
        "gambar_og": situs + "/static/img/logo.png",
    })


# ═════════════════════════════════════════════════════════════
#  HALAMAN: DETAIL NOVEL
# ═════════════════════════════════════════════════════════════
@app.get("/novel/{slug}", response_class=HTMLResponse)
def novel(request: Request, slug: str):
    situs = situs_untuk(request)
    r = ambil("SELECT * FROM novel WHERE slug=?", (slug,), satu=True)
    if not r:
        raise HTTPException(404)
    genre = [dict(x) for x in ambil("""
        SELECT g.slug, g.nama FROM genre g
        JOIN novel_genre ng ON ng.genre_id=g.id WHERE ng.novel_id=?""", (r["id"],))]
    tag = [dict(x) for x in ambil("""
        SELECT t.slug, t.nama FROM tag t
        JOIN novel_tag nt ON nt.tag_id=t.id WHERE nt.novel_id=?""", (r["id"],))]
    bab = [dict(x) for x in ambil(
        "SELECT urutan, nomor, judul, volume, tanggal, ada_gambar FROM bab "
        "WHERE novel_id=? ORDER BY urutan", (r["id"],))]
    # ── KELOMPOK VOLUME (collapsible) ──
    # Bab dikelompokkan per volume; urut NAIK (Volume 1 → N), bab di dalam juga naik.
    # Kalau ADA bab yang volumenya kosong → seluruh daftar tanpa collapsible
    # (aturan user: novel tanpa volume = daftar bab langsung).
    vol_ada = all(b.get("volume") is not None for b in bab)
    grup_volume = []
    if vol_ada and bab:
        dari = {}
        for b in bab:
            dari.setdefault(b["volume"], []).append(b)
        grup_volume = [{"volume": v, "bab": dari[v]} for v in sorted(dari)]
    serupa = [kartu(x) for x in ambil("""
        SELECT DISTINCT n.* FROM novel n
        JOIN novel_genre ng ON ng.novel_id=n.id
        WHERE ng.genre_id IN (SELECT genre_id FROM novel_genre WHERE novel_id=?)
          AND n.id<>? AND n.cover_webp IS NOT NULL
        ORDER BY n.jumlah_bab DESC LIMIT 12""", (r["id"], r["id"]))]
    return tpl.TemplateResponse(request, "novel.html", {
        "situs": situs, "halaman": "novel", "batas_bab": auth.BATAS_TAMU, "nama": NAMA,
        "judul": f"{r['judul']}: {NAMA}",
        "desk": (r["sinopsis"] or DESK)[:160],
        "kanon": f"{situs}/novel/{slug}",
        "gambar_og": f"{situs}/cover/{r['id']}.webp" if r["cover_webp"] else None,
        "n": dict(r), "cover": f"/cover/{r['id']}.webp" if r["cover_webp"] else None,
        "genre": genre, "tag": tag, "bab": bab, "serupa": serupa,
        "jlh_bab": len(bab), "grup_volume": grup_volume,
        "pengguna": getattr(request.state, "pengguna", None),
        "favorit": (auth.favorit_ada(request.state.pengguna["id"], r["id"])
                    if getattr(request.state, "pengguna", None) else False),
        "sudah": _sudah_dibaca(request, r["id"]),
        "lanjut": _lanjut_baca(request, r["id"]),
    })


# ═════════════════════════════════════════════════════════════
#  HALAMAN: BACA BAB
# ═════════════════════════════════════════════════════════════
@app.get("/novel/{slug}/bab/{urutan}", response_class=HTMLResponse)
def bab(request: Request, slug: str, urutan: int):
    situs = situs_untuk(request)
    n = ambil("SELECT * FROM novel WHERE slug=?", (slug,), satu=True)
    if not n:
        raise HTTPException(404)
    b = ambil("SELECT * FROM bab WHERE novel_id=? AND urutan=?", (n["id"], urutan), satu=True)
    if not b:
        raise HTTPException(404)

    # ── GERBANG: bab 1..3 BEBAS SELAMANYA · bab 4+ WAJIB LOGIN ──
    # (berdasarkan NOMOR BAB, bukan berapa kali dibaca)
    pengguna = getattr(request.state, "pengguna", None)
    if pengguna:
        # pengguna login → catat (untuk tanda ✓ + tombol "Lanjut baca")
        auth.catat_baca(pengguna["id"], n["id"], urutan)
    elif urutan <= auth.BATAS_TAMU:
        # tamu (bab bebas) → catat juga, biar tanda ✓ tetap muncul
        tamu = getattr(request.state, "tamu", None)
        if tamu:
            try:
                auth.catat_baca_tamu(tamu, n["id"], urutan)
            except Exception:
                pass
    # bab di atas batas & belum login → halaman tetap tampil,
    # tapi isi di-blur + pop-up naik dari bawah (gerbang_dasar)
    gerbang_dasar = (not pengguna) and urutan > auth.BATAS_TAMU
    gmb = [dict(x) for x in ambil(
        "SELECT urutan, file_lokal, url_asli, caption FROM bab_gambar WHERE bab_id=? ORDER BY urutan",
        (b["id"],))]
    for g in gmb:
        # gambar lokal  → /gambar/<bab_id>/<urutan><ekstensi asli>
        #   ⚠️ ekstensi bisa .png / .jpg / .jpeg / .webp — ambil dari file_lokal!
        # belum diunduh → pakai URL asli langsung (biar tetap tampil)
        if g["file_lokal"]:
            ext = os.path.splitext(g["file_lokal"])[1] or ".jpg"
            g["sumber"] = f"/gambar/{b['id']}/{g['urutan']}{ext}"
        else:
            g["sumber"] = g["url_asli"]
    sebelum = ambil("SELECT MAX(urutan) u FROM bab WHERE novel_id=? AND urutan<?",
                    (n["id"], urutan), satu=True)["u"]
    sesudah = ambil("SELECT MIN(urutan) u FROM bab WHERE novel_id=? AND urutan>?",
                    (n["id"], urutan), satu=True)["u"]
    # paragraf: pisah baris → HTML aman (autoescape Jinja)
    teks = (b["teks"] or "").split("\n")
    ada_teks = bool((b["teks"] or "").strip())
    # daftar bab ringkas untuk panel di bawah halaman baca (template bab.html)
    semua_bab = [dict(x) for x in ambil(
        "SELECT urutan, nomor, judul FROM bab WHERE novel_id=? ORDER BY urutan",
        (n["id"],))]
    return tpl.TemplateResponse(request, "bab.html", {
        "situs": situs, "halaman": "bab", "batas_bab": auth.BATAS_TAMU, "nama": NAMA,
        "judul": f"{b['judul']}: {n['judul']} | {NAMA}",
        "desk": "Baca bab {} · {} — {} di {}.".format(urutan, (b["judul"] or "")[:80], (n["judul"] or "")[:70], NAMA),
        "kanon": f"{situs}/novel/{slug}/bab/{urutan}",
        "n": dict(n), "b": dict(b), "paragraf": teks, "gambar": gmb,
        "sebelum": sebelum, "sesudah": sesudah, "semua_bab": semua_bab,
        "ada_teks": ada_teks,
        "gerbang_dasar": gerbang_dasar,
        "pengguna": pengguna,
        "sudah": _sudah_dibaca(request, n["id"]),
        "gambar_og": (gmb[0]["sumber"] if gmb and gmb[0].get("sumber") else None),
        "bab_awal": [dict(x) for x in ambil(
            "SELECT urutan, judul FROM bab WHERE novel_id=? ORDER BY urutan LIMIT ?",
            (n["id"], auth.BATAS_TAMU))] if gerbang_dasar else [],
    })


# ═════════════════════════════════════════════════════════════
#  HALAMAN: JELAJAH / GENRE / TAG / CARI
# ═════════════════════════════════════════════════════════════
# ═════════════════════════════════════════════════════════════
#  HALAMAN MASUK / DAFTAR  +  API AUTH (Firebase)
# ═════════════════════════════════════════════════════════════
FB_WEB = {
    "apiKey":            os.environ.get("FB_API_KEY", ""),
    "authDomain":        os.environ.get("FB_AUTH_DOMAIN", ""),
    "projectId":         os.environ.get("FB_PROJECT_ID", ""),
    "storageBucket":     os.environ.get("FB_STORAGE_BUCKET", ""),
    "messagingSenderId": os.environ.get("FB_SENDER_ID", ""),
    "appId":             os.environ.get("FB_APP_ID", ""),
}
FB_JSLIBS = [
    "https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js",
    "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js",
]


# ══════════════════════════════════════════════════════════════
#  CONFIG FIREBASE WEB → BERKAS JS (bukan di dalam HTML)
#  ⚠️ Config web Firebase MEMANG publik (bukan rahasia) — keamanannya
#     pakai ATURAN IZIN, bukan menyembunyikan kunci.
#     Dipisah ke berkas supaya HTML bersih & gampang diganti.
# ══════════════════════════════════════════════════════════════
@app.get("/fb.js")
def fb_js():
    isi = ("/* config Firebase WEB — dihasilkan server dari env (publik) */\n"
           "window.FB_KONFIG = " + json.dumps(FB_WEB) + ";\n")
    return Response(isi, media_type="application/javascript; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/masuk", response_class=HTMLResponse)
def masuk(request: Request):
    situs = situs_untuk(request)
    return tpl.TemplateResponse(request, "masuk.html", {
        "situs": situs, "halaman": "masuk", "nama": NAMA,
        "judul": f"Masuk · {NAMA}",
        "desk": "Masuk untuk membaca semua bab, simpan riwayat & pustaka pribadi.",
        "kanon": f"{situs}/masuk",
        "fb_web": FB_WEB, "fb_jslibs": FB_JSLIBS,
        "fb_siap": bool(FB_WEB["apiKey"] and auth.firebase_siap()),
        "pengguna": getattr(request.state, "pengguna", None),
    })


@app.post("/api/auth/sesi")
async def api_auth_sesi(request: Request):
    """Tukar ID token Firebase (dari browser) dengan cookie sesi web.

    ⚠️ Token DIVERIFIKASI di server (firebase-admin) — bukan dipercaya begitu saja.
    """
    if auth.terlalu_sering("ip:" + (request.client.host if request.client else "-")):
        return JSONResponse({"ok": False, "pesan": "Terlalu banyak percobaan. Coba lagi nanti."}, 429)
    auth.catat_coba("ip:" + (request.client.host if request.client else "-"))

    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    id_token = (data or {}).get("idToken", "")
    if not id_token:
        return JSONResponse({"ok": False, "pesan": "Token tidak ada."}, 400)

    klaim = auth.verifikasi_token(id_token)
    if not klaim:
        return JSONResponse({"ok": False, "pesan": "Token tidak sah atau kadaluarsa."}, 401)

    p = auth.pengguna_dari_firebase(klaim)
    if not p:
        return JSONResponse({"ok": False, "pesan": "Gagal membuat akun."}, 500)

    tok = auth.buat_sesi(
        p["id"],
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )
    resp = JSONResponse({
        "ok": True,
        "pengguna": {"nama": p["nama"], "email": p["email"], "foto": p["foto"]},
        "lanjut": (data or {}).get("lanjut") or "/",
    })
    resp.set_cookie(auth.COOKIE_SESI, tok, max_age=60 * 60 * 24 * auth.UMUR_SESI,
                    httponly=True, samesite="lax", secure=True)
    return resp


@app.get("/api/auth/me")
def api_auth_me(request: Request):
    situs = situs_untuk(request)
    p = getattr(request.state, "pengguna", None)
    if not p:
        return JSONResponse({"ok": True, "masuk": False})
    return JSONResponse({"ok": True, "masuk": True,
                         "pengguna": {"nama": p["nama"], "email": p["email"], "foto": p["foto"]}})




# ══════════════════════════════════════════════════════════════
#  FAVORIT — tambah / buang (wajib masuk)
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#  TELEGRAM — pengirim pesan (dipakai fitur Request Novel)
# ══════════════════════════════════════════════════════════════
def kirim_telegram(teks, tombol=None):
    """kirim HTML ke Telegram pemilik. Balikan True kalau sukses."""
    if not TELE_TOKEN or not TELE_CHAT:
        return False
    data = {"chat_id": TELE_CHAT, "text": teks[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if tombol:
        data["reply_markup"] = json.dumps({"inline_keyboard": tombol})
    try:
        req = urllib.request.Request(
            "https://api.telegram.org/bot{}/sendMessage".format(TELE_TOKEN),
            data=urllib.parse.urlencode(data).encode())
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        print("  ⚠️ telegram gagal:", str(e)[:120])
        return False


def _esc(s):
    """amankan teks untuk HTML Telegram"""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ══════════════════════════════════════════════════════════════
#  REQUEST NOVEL — WAJIB MASUK
# ══════════════════════════════════════════════════════════════
@app.post("/api/request")
async def api_request(request: Request):
    pengguna = getattr(request.state, "pengguna", None)
    if not pengguna:
        return JSONResponse({"ok": False, "pesan": "masuk_dulu"}, status_code=401)
    try:
        isi = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "pesan": "data_salah"}, status_code=400)

    judul = (isi.get("judul") or "").strip()
    sumber = (isi.get("sumber") or "").strip()
    catatan = (isi.get("catatan") or "").strip()
    halaman = (isi.get("halaman") or "").strip()[:200]

    if len(judul) < 2:
        return JSONResponse({"ok": False, "pesan": "judul_kosong"}, status_code=400)
    if len(judul) > 200 or len(sumber) > 400 or len(catatan) > 600:
        return JSONResponse({"ok": False, "pesan": "kepanjangan"}, status_code=400)

    # batas laju: 5 per jam
    try:
        if auth.permintaan_jumlah_kini(pengguna["id"], jam=1) >= 5:
            return JSONResponse({"ok": False, "pesan": "terlalu_banyak",
                                 "balasan": "Sudah 5 request dalam 1 jam terakhir. Coba lagi nanti ya."},
                                status_code=429)
    except Exception:
        pass

    # sudah ada di arsip kita?
    ada_novel = ambil("SELECT slug, judul FROM novel WHERE lower(judul) LIKE lower(?) LIMIT 1",
                      ("%" + judul + "%",), satu=True)

    # simpan ke dB (selalu, walau tele gagal)
    try:
        baris = auth.permintaan_tambah(pengguna["id"], judul, sumber, catatan, halaman)
        no = baris["id"] if baris else 0
    except Exception as e:
        print("  ⚠️ simpan request gagal:", str(e)[:120])
        no = 0

    # kirim ke Telegram
    waktu = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    nama = pengguna.get("nama") or pengguna.get("email") or "pengguna"
    baris_tele = [
        "🆕 <b>REQUEST NOVEL BARU</b>", "",
        "📖 Judul  : <b>{}</b>".format(_esc(judul)),
    ]
    if sumber:
        baris_tele.append("🔗 Sumber : {}".format(_esc(sumber)))
    if catatan:
        baris_tele.append("💬 Catatan: {}".format(_esc(catatan)))
    baris_tele += [
        "", "─────────────────",
        "👤 Dari   : {}".format(_esc(nama)),
        "📧 Akun   : {}".format(_esc(pengguna.get("email") or "-")),
        "🌐 Halaman: {}".format(_esc(halaman or "-")),
        "🕐 Waktu  : {} WIB".format(waktu),
        "🔢 No.    : <b>#{}</b>".format(no),
    ]
    if ada_novel:
        baris_tele += ["", "✅ <i>Mungkin sudah ada di arsip: {} (/{})</i>".format(
            _esc(ada_novel["judul"]), _esc(ada_novel["slug"]))]

    terkirim = kirim_telegram(chr(10).join(baris_tele))
    if terkirim and no:
        try:
            auth._x("UPDATE request_novel SET telegram=1 WHERE id=?", (no,))
        except Exception:
            pass

    if ada_novel:
        balas = "Novel mirip sudah ada di arsip — tapi requestmu tetap kukirim."
    else:
        balas = "Request terkirim! Nanti dicek ya 🙏" if terkirim else                 "Request tersimpan. (notifikasi ke pemilik sedang tidak aktif)"
    return JSONResponse({"ok": True, "no": no, "terkirim": terkirim,
                         "ada_mirip": bool(ada_novel), "balasan": balas})


@app.get("/api/request/saya")
async def api_request_saya(request: Request):
    pengguna = getattr(request.state, "pengguna", None)
    if not pengguna:
        return JSONResponse({"ok": False, "pesan": "masuk_dulu"}, status_code=401)
    try:
        b = auth.permintaan_daftar(pengguna["id"], batas=50)
    except Exception:
        b = []
    return JSONResponse({"ok": True, "jumlah": len(b), "daftar": [
        {"judul": r["judul"], "status": r["status"], "waktu": r["waktu"]} for r in b]})


@app.post("/api/favorit")
async def api_favorit(request: Request):
    pengguna = getattr(request.state, "pengguna", None)
    if not pengguna:
        return JSONResponse({"ok": False, "pesan": "masuk_dulu"}, status_code=401)
    try:
        isi = await request.json()
        novel_id = int(isi.get("novel_id") or 0)
    except Exception:
        return JSONResponse({"ok": False, "pesan": "data_salah"}, status_code=400)
    if not novel_id:
        return JSONResponse({"ok": False, "pesan": "novel_id_kosong"}, status_code=400)
    ada = ambil("SELECT 1 FROM novel WHERE id=?", (novel_id,), satu=True)
    if not ada:
        return JSONResponse({"ok": False, "pesan": "novel_tidak_ada"}, status_code=404)
    if auth.favorit_ada(pengguna["id"], novel_id):
        auth.favorit_buang(pengguna["id"], novel_id)
        sekarang = False
    else:
        auth.favorit_tambah(pengguna["id"], novel_id)
        sekarang = True
    jml = len(auth.daftar_favorit(pengguna["id"]))
    return JSONResponse({"ok": True, "favorit": sekarang, "jumlah": jml})
@app.post("/keluar")
@app.get("/keluar")
def keluar(request: Request):
    situs = situs_untuk(request)
    auth.akhiri_sesi(request.cookies.get(auth.COOKIE_SESI))
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE_SESI)
    return resp


@app.get("/jelajah", response_class=HTMLResponse)
def jelajah(request: Request,
            q: str = "", status: str = "", tipe: str = "",
            genre: str = "", tag: str = "", negara: str = "",
            order: str = "populer", page: int = 1, per: int = 24):
    situs = situs_untuk(request)
    where, par = ["n.cover_webp IS NOT NULL"], []
    if q:
        where.append("(n.judul LIKE ? OR COALESCE(n.penulis, n.author) LIKE ?)")
        par += [f"%{q}%", f"%{q}%"]
    if status:
        where.append("LOWER(COALESCE(n.status,''))=?"); par.append(status.lower())
    if tipe:
        where.append("LOWER(COALESCE(n.tipe,'')) LIKE ?"); par.append(f"%{tipe.lower()}%")
    if genre:
        where.append("n.id IN (SELECT ng.novel_id FROM novel_genre ng "
                     "JOIN genre g ON g.id=ng.genre_id WHERE g.slug=?)")
        par.append(genre)
    if tag:
        where.append("n.id IN (SELECT nt.novel_id FROM novel_tag nt "
                     "JOIN tag t ON t.id=nt.tag_id WHERE t.slug=?)")
        par.append(tag)
    # negara (dipakai section "Dari Jepang"/"Dari Korea" di beranda)
    if negara:
        where.append("LOWER(COALESCE(n.country,'')) LIKE ?")
        par.append(f"%{negara.lower()}%")
    # ⚠️ "populer" TETAP ada (bookmark) tapi tidak dipakai di beranda karena
    #    151/496 novel bookmark-nya 0 → urutannya tidak jujur. Sekarang beranda
    #    pakai "panjang" (jumlah_bab nyata) & "update" (bab.tanggal terbaru).
    urut = {"populer": "COALESCE(n.bookmark,0) DESC, n.jumlah_bab DESC",
            "panjang": "n.jumlah_bab DESC",
            "update": "COALESCE(n.waktu_update,n.created_at) DESC",
            "judul": "n.judul COLLATE NOCASE ASC", "rating": "n.rating DESC"}\
        .get(order, "n.jumlah_bab DESC")
    # "ilustrasi": hanya novel yang benar-benar punya baris di bab_gambar.
    if order == "ilustrasi":
        where.append("n.id IN (SELECT n2.id FROM novel n2 "
                     "JOIN bab b2 ON b2.novel_id=n2.id "
                     "JOIN bab_gambar g2 ON g2.bab_id=b2.id)")
    if order == "acak":
        urut = f"(n.id * {int(time.strftime('%Y%m%d'))}) % 10007 ASC"
    W = " WHERE " + " AND ".join(where)
    total = ambil(f"SELECT COUNT(*) c FROM novel n{W}", tuple(par), satu=True)["c"]
    baris = ambil(f"SELECT n.* FROM novel n{W} ORDER BY {urut} LIMIT ? OFFSET ?",
                  tuple(par) + (per, (page - 1) * per))
    g_list = [dict(x) for x in ambil("""
        SELECT g.slug, g.nama, COUNT(ng.novel_id) j FROM genre g
        JOIN novel_genre ng ON ng.genre_id=g.id GROUP BY g.id
        ORDER BY j DESC""")]
    t_list = [dict(x) for x in ambil(
        "SELECT slug, nama, jumlah_novel FROM tag ORDER BY jumlah_novel DESC LIMIT 60")]
    return tpl.TemplateResponse(request, "jelajah.html", {
        "situs": situs, "halaman": "jelajah", "nama": NAMA,
        "judul": f"Jelajah Novel: {NAMA}", "desk": DESK,
        "kanon": f"{situs}/jelajah",
        "hasil": [kartu(x) for x in baris], "total": total,
        "q": q, "status": status, "tipe": tipe, "genre": genre, "tag": tag,
        "negara": negara,
        "order": order, "page": page, "per": per,
        "total_hal": (total + per - 1) // per,
        "genre_list": g_list, "tag_list": t_list,
    })


@app.get("/genre/{slug}", response_class=HTMLResponse)
def genre(request: Request, slug: str, page: int = 1, per: int = 24):
    situs = situs_untuk(request)
    g = ambil("SELECT * FROM genre WHERE slug=?", (slug,), satu=True)
    if not g:
        raise HTTPException(404)
    total = ambil("SELECT COUNT(*) c FROM novel_genre WHERE genre_id=?", (g["id"],),
                  satu=True)["c"]
    baris = ambil("""SELECT n.* FROM novel n JOIN novel_genre ng ON ng.novel_id=n.id
        WHERE ng.genre_id=? AND n.cover_webp IS NOT NULL
        ORDER BY n.jumlah_bab DESC LIMIT ? OFFSET ?""",
                  (g["id"], per, (page - 1) * per))
    return tpl.TemplateResponse(request, "daftar.html", {
        "situs": situs, "halaman": "daftar", "nama": NAMA,
        "judul": f"Genre {g['nama']}: {NAMA}", "desk": f"Novel genre {g['nama']}.",
        "kanon": f"{situs}/genre/{slug}",
        "kepala": f"Genre: {g['nama']}", "hasil": [kartu(x) for x in baris],
        "total": total, "page": page, "per": per,
        "total_hal": (total + per - 1) // per, "dasar_url": f"/genre/{slug}",
    })


@app.get("/tag/{slug}", response_class=HTMLResponse)
def tag(request: Request, slug: str, page: int = 1, per: int = 24):
    situs = situs_untuk(request)
    t = ambil("SELECT * FROM tag WHERE slug=?", (slug,), satu=True)
    if not t:
        raise HTTPException(404)
    total = ambil("SELECT COUNT(*) c FROM novel_tag WHERE tag_id=?", (t["id"],),
                  satu=True)["c"]
    baris = ambil("""SELECT n.* FROM novel n JOIN novel_tag nt ON nt.novel_id=n.id
        WHERE nt.tag_id=? AND n.cover_webp IS NOT NULL
        ORDER BY n.jumlah_bab DESC LIMIT ? OFFSET ?""",
                  (t["id"], per, (page - 1) * per))
    return tpl.TemplateResponse(request, "daftar.html", {
        "situs": situs, "halaman": "daftar", "nama": NAMA,
        "judul": f"Tag {t['nama']}: {NAMA}", "desk": f"Novel dengan tag {t['nama']}.",
        "kanon": f"{situs}/tag/{slug}",
        "kepala": f"Tag: {t['nama']}", "hasil": [kartu(x) for x in baris],
        "total": total, "page": page, "per": per,
        "total_hal": (total + per - 1) // per, "dasar_url": f"/tag/{slug}",
    })


@app.get("/cari", response_class=HTMLResponse)
def cari(request: Request, q: str = "", page: int = 1, per: int = 24):
    situs = situs_untuk(request)
    hasil, total, bab_hits = [], 0, []
    if q:
        total = ambil("SELECT COUNT(*) c FROM novel WHERE judul LIKE ? OR "
                      "COALESCE(penulis,author) LIKE ?",
                      (f"%{q}%", f"%{q}%"), satu=True)["c"]
        hasil = [kartu(x) for x in ambil(
            "SELECT * FROM novel WHERE judul LIKE ? OR COALESCE(penulis,author) LIKE ? "
            "ORDER BY jumlah_bab DESC LIMIT ? OFFSET ?",
            (f"%{q}%", f"%{q}%", per, (page - 1) * per))]
        try:
            bab_hits = [dict(x) for x in ambil("""
                SELECT b.judul, b.urutan, n.slug, n.judul AS novel_judul
                FROM bab_fts f JOIN bab b ON b.id=f.rowid
                JOIN novel n ON n.id=b.novel_id
                WHERE bab_fts MATCH ? LIMIT 10""", (q,))]
        except sqlite3.Error:
            bab_hits = []
    return tpl.TemplateResponse(request, "cari.html", {
        "situs": situs, "halaman": "cari", "nama": NAMA,
        "judul": f"Cari {q}: {NAMA}", "desk": DESK,
        "kanon": f"{situs}/cari?q={q}",
        "q": q, "hasil": hasil, "total": total, "bab_hits": bab_hits,
        "page": page, "per": per, "total_hal": (total + per - 1) // per,
    })


# ═════════════════════════════════════════════════════════════
#  SEO: sitemap.xml + robots.txt
# ═════════════════════════════════════════════════════════════
_POTONG = 45000          # di bawah batas Google 50.000 URL per berkas


@app.get("/sitemap.xml")
def sitemap_index():
    """INDEX sitemap — karena Google batas 50.000 URL per berkas.

    Kita punya 495 novel + 111.680 bab → WAJIB dipecah.
    Berkas anak:
      /sitemap-halaman.xml   → beranda, jelajah, genre, tag
      /sitemap-novel-N.xml   → novel (potong 45.000)
      /sitemap-bab-N.xml     → bab   (potong 45.000)
    """
    jml_novel = ambil("SELECT COUNT(*) c FROM novel", satu=True)["c"]
    jml_bab = ambil("""SELECT COUNT(*) c FROM bab b
        WHERE b.teks IS NOT NULL""", satu=True)["c"]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for nama in ["halaman"]:
        out.append(f"<sitemap><loc>{SITUS}/sitemap-{nama}.xml</loc></sitemap>")
    for i in range(1, (jml_novel // _POTONG) + 2):
        out.append(f"<sitemap><loc>{SITUS}/sitemap-novel-{i}.xml</loc></sitemap>")
    for i in range(1, (jml_bab // _POTONG) + 2):
        out.append(f"<sitemap><loc>{SITUS}/sitemap-bab-{i}.xml</loc></sitemap>")
    out.append("</sitemapindex>")
    return Response("\n".join(out), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


_POTONG = 45000          # di bawah batas Google 50.000


@app.get("/sitemap-halaman.xml")
def sitemap_halaman():
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    out.append(f"<url><loc>{SITUS}/</loc><changefreq>hourly</changefreq>"
               "<priority>1.0</priority></url>")
    out.append(f"<url><loc>{SITUS}/jelajah</loc><changefreq>daily</changefreq>"
               "<priority>0.9</priority></url>")
    for r in ambil("SELECT slug FROM genre ORDER BY id"):
        out.append(f"<url><loc>{SITUS}/genre/{r['slug']}</loc>"
                   "<changefreq>weekly</changefreq><priority>0.7</priority></url>")
    for r in ambil("SELECT slug FROM tag ORDER BY jumlah_novel DESC LIMIT 3000"):
        out.append(f"<url><loc>{SITUS}/tag/{r['slug']}</loc>"
                   "<changefreq>weekly</changefreq><priority>0.5</priority></url>")
    out.append("</urlset>")
    return Response("\n".join(out), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap-novel-{bagian}.xml")
def sitemap_novel(bagian: int):
    if bagian < 1:
        raise HTTPException(404)
    geser = (bagian - 1) * _POTONG
    baris = ambil("SELECT slug FROM novel ORDER BY id LIMIT ? OFFSET ?",
                  (_POTONG, geser))
    if not baris:
        raise HTTPException(404)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for r in baris:
        out.append(f"<url><loc>{SITUS}/novel/{r['slug']}</loc>"
                   "<changefreq>daily</changefreq><priority>0.8</priority></url>")
    out.append("</urlset>")
    return Response("\n".join(out), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap-bab-{bagian}.xml")
def sitemap_bab(bagian: int):
    if bagian < 1:
        raise HTTPException(404)
    geser = (bagian - 1) * _POTONG
    baris = ambil("""SELECT n.slug, b.urutan FROM bab b
        JOIN novel n ON n.id = b.novel_id
        WHERE b.teks IS NOT NULL AND b.teks <> ''
        ORDER BY b.id LIMIT ? OFFSET ?""", (_POTONG, geser))
    if not baris:
        raise HTTPException(404)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for r in baris:
        out.append(f"<url><loc>{SITUS}/novel/{r['slug']}/bab/{r['urutan']}</loc>"
                   "<changefreq>weekly</changefreq><priority>0.6</priority></url>")
    out.append("</urlset>")
    return Response("\n".join(out), media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nDisallow: /api/\n"
        "User-agent: ClaudeBot\nDisallow: /\n"
        "User-agent: GPTBot\nDisallow: /\n"
        "User-agent: CCBot\nDisallow: /\n"
        "User-agent: Google-Extended\nDisallow: /\n"
        "User-agent: Bytespider\nDisallow: /\n"
        "User-agent: Amazonbot\nDisallow: /\n"
        "User-agent: Applebot-Extended\nDisallow: /\n"
        "User-agent: PetalBot\nDisallow: /\n"
        "User-agent: anthropic-ai\nDisallow: /\n"
        "User-agent: Meta-ExternalAgent\nDisallow: /\n"
        f"Sitemap: {SITUS}/sitemap.xml\n")


def _stat_ringkas():
    """angka ringkas arsip — dipakai halaman /status DAN api /api/status"""
    def _satu(q, par=()):
        try:
            r = ambil(q, par, satu=True)
            return (r["c"] or 0) if r else 0
        except Exception:
            return 0
    return {
        "novel": _satu("SELECT COUNT(*) c FROM novel"),
        "bab": _satu("SELECT COUNT(*) c FROM bab"),
        "gambar": _satu("SELECT COUNT(*) c FROM bab_gambar"),
        "genre": _satu("SELECT COUNT(*) c FROM genre"),
        "tag": _satu("SELECT COUNT(*) c FROM tag"),
        "huruf": _satu("SELECT SUM(LENGTH(COALESCE(isi,''))) c FROM bab"),
        "ilustrasi": _satu("SELECT COUNT(DISTINCT b.novel_id) c FROM bab b "
                           "JOIN bab_gambar g ON g.bab_id=b.id"),
        "bersampul": _satu("SELECT COUNT(*) c FROM novel WHERE cover_webp IS NOT NULL"),
        "sudah_tamat": _satu("SELECT COUNT(*) c FROM novel "
                             "WHERE LOWER(COALESCE(status,'')) LIKE '%complete%'"),
        "masih_jalan": _satu("SELECT COUNT(*) c FROM novel "
                             "WHERE LOWER(COALESCE(status,'')) LIKE '%ongoing%'"),
    }


@app.get("/api/status")
def api_status():
    """angka mentah (JSON) — dipakai footer / pemantau"""
    s = _stat_ringkas()
    return {"aplikasi": "Naver Web", "novel": s["novel"],
            "bab": s["bab"], "gambar": s["gambar"]}


@app.get("/status", response_class=HTMLResponse)
def halaman_status(request: Request):
    """HALAMAN status: isi arsip apa adanya + request pengguna"""
    situs = situs_untuk(request)
    s = _stat_ringkas()
    try:
        req_jml = len(auth.permintaan_daftar(None, batas=9999))
        req = auth.permintaan_daftar(None, batas=12)
    except Exception:
        req_jml, req = 0, []
    try:
        teratas = [dict(x) for x in ambil(
            "SELECT id, slug, judul, jumlah_bab FROM novel "
            "WHERE cover_webp IS NOT NULL ORDER BY jumlah_bab DESC LIMIT 8")]
    except Exception:
        teratas = []
    try:
        a = ambil("SELECT MAX(tanggal) t FROM bab", satu=True)
        akhir = a["t"] if a else None
    except Exception:
        akhir = None
    return tpl.TemplateResponse(request, "status.html", {
        "situs": situs, "halaman": "status", "nama": NAMA,
        "judul": "Status Arsip: " + NAMA,
        "desk": "Jumlah novel, bab, dan ilustrasi di arsip Naver.",
        "kanon": situs + "/status",
        "s": s, "req": req, "req_jml": req_jml,
        "teratas": teratas, "akhir": akhir,
        "pengguna": getattr(request.state, "pengguna", None),
    })


@app.get("/apk", response_class=HTMLResponse)
def apk(request: Request):
    """Halaman unduh APK (file ditaruh di web/static/naver.apk kalau ada)."""
    situs = situs_untuk(request)
    apk = BASE / "static" / "naver.apk"
    n = ambil("SELECT COUNT(*) c FROM novel", satu=True)["c"]
    b = ambil("SELECT COUNT(*) c FROM bab", satu=True)["c"]
    return tpl.TemplateResponse(request, "apk.html", {
        "situs": situs, "halaman": "apk", "nama": NAMA,
        "judul": f"Unduh APK: {NAMA}",
        "desk": "Unduh aplikasi Android Naver Novel untuk baca offline.",
        "kanon": f"{situs}/apk",
        "apk_url": "/static/naver.apk" if apk.exists() else None,
        "total_novel": n, "total_bab": b,
    })


# ═════════════════════════════════════════════════════════════
#  HALAMAN: PUSTAKA & PROFIL (nav bawah)
#  ⚠️ BELUM ada sistem login — jadi halaman ini JUJUR menjelaskan
#     keadaannya, BUKAN pura-pura punya data pengguna.
#     (Rencana login ada di CATATAN/nanti-kerjaan-tertunda.md)
# ═════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#  BANTUAN: ambil detail novel dari sebuah daftar id (untuk
#  halaman Pustaka: favorit & riwayat baca)
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#  BANTUAN: bab yang sudah dibaca + posisi lanjut baca
#     pengguna login → dB users.db (baca_pengguna)
#     tamu           → dB users.db (baca_tamu, cookie bertanda-tangan)
# ══════════════════════════════════════════════════════════════
def _sudah_dibaca(request, novel_id):
    """kumpulan nomor urutan bab yang sudah dibaca (set)"""
    situs = situs_untuk(request)
    try:
        pengguna = getattr(request.state, "pengguna", None)
        if pengguna:
            return auth.novel_dibaca(pengguna["id"]).get(novel_id, set())
        tamu = getattr(request.state, "tamu", None)
        if tamu:
            return auth.bab_sudah_dibaca_tamu(tamu, novel_id)
    except Exception:
        pass
    return set()


def _lanjut_baca(request, novel_id):
    """urutan bab terakhir yang dibaca (untuk tombol 'Lanjut baca')"""
    situs = situs_untuk(request)
    try:
        pengguna = getattr(request.state, "pengguna", None)
        if pengguna:
            for r in auth.riwayat(pengguna["id"], batas=200):
                if r["novel_id"] == novel_id:
                    return r["urutan"]
    except Exception:
        pass
    return None


def _novel_dari_id(ids):
    """ids: daftar id novel (urutannya dipertahankan)"""
    if not ids:
        return []
    tanda = ",".join("?" * len(ids))
    baris = ambil(f"SELECT * FROM novel WHERE id IN ({tanda})", tuple(ids))
    peta = {r["id"]: dict(r) for r in baris}
    out = []
    for i in ids:
        if i in peta:
            n = peta[i]
            n["cover"] = f"/cover/{i}.webp" if n.get("cover_webp") else None
            out.append(n)
    return out


def _posisi_baca(pengguna_id):
    """novel_id -> urutan terakhir yang dibaca (paling baru di atas)"""
    baris = auth.riwayat(pengguna_id, batas=60)
    return [dict(r) for r in baris]


# ══════════════════════════════════════════════════════════════
#  HALAMAN: REQUEST NOVEL (menu laci · wajib masuk untuk kirim)
# ══════════════════════════════════════════════════════════════
@app.get("/request", response_class=HTMLResponse)
def halaman_request(request: Request):
    situs = situs_untuk(request)
    pengguna = getattr(request.state, "pengguna", None)
    permintaan = []
    if pengguna:
        try:
            permintaan = auth.permintaan_daftar(pengguna["id"], batas=30)
        except Exception:
            permintaan = []
    return tpl.TemplateResponse(request, "request.html", {
        "situs": situs, "halaman": "request", "nama": NAMA,
        "judul": "Request Novel: " + NAMA,
        "desk": "Minta novel baru untuk ditambahkan ke arsip.",
        "kanon": situs + "/request",
        "pengguna": pengguna, "permintaan": permintaan,
    })


@app.get("/pustaka", response_class=HTMLResponse)
def pustaka(request: Request):
    situs = situs_untuk(request)
    pengguna = getattr(request.state, "pengguna", None)
    fav, riw = [], []
    if pengguna:
        try:
            fav = _novel_dari_id([x["novel_id"] for x in auth.daftar_favorit(pengguna["id"])])
        except Exception:
            fav = []
        try:
            pos = _posisi_baca(pengguna["id"])
            riw = []
            for p in pos:
                baris = ambil("SELECT * FROM novel WHERE id=?", (p["novel_id"],), satu=True)
                if not baris:
                    continue
                nn = dict(baris)
                nn["cover"] = f"/cover/{nn['id']}.webp" if nn.get("cover_webp") else None
                nn["bab_terakhir"] = p["urutan"]
                nn["waktu_baca"] = p.get("waktu")
                riw.append(nn)
        except Exception:
            riw = []
    return tpl.TemplateResponse(request, "pustaka.html", {
        "situs": situs, "halaman": "pustaka", "nama": NAMA,
        "judul": f"Pustaka: {NAMA}",
        "desk": "Koleksi pribadi, favorit, dan riwayat baca.",
        "kanon": f"{situs}/pustaka",
        "pengguna": pengguna, "fav": fav, "riwayat": riw,
    })


@app.get("/profil", response_class=HTMLResponse)
def profil(request: Request):
    situs = situs_untuk(request)
    pengguna = getattr(request.state, "pengguna", None)
    jml_fav = 0
    if pengguna:
        try:
            jml_fav = len(auth.daftar_favorit(pengguna["id"]))
        except Exception:
            jml_fav = 0
    return tpl.TemplateResponse(request, "profil.html", {
        "situs": situs, "halaman": "profil", "nama": NAMA,
        "judul": f"Profil: {NAMA}",
        "desk": "Akun dan pengaturan.",
        "kanon": f"{situs}/profil",
        "pengguna": pengguna, "jml_fav": jml_fav, "fb_siap": auth.firebase_siap(),
        "fb_web": FB_WEB,
    })


# ═════════════════════════════════════════════════════════════
#  HALAMAN GALAT (antislop A-05 · R-27: state error harus ada)
#  Tanpa ini, galat keluar jadi JSON {"detail":"Not Found"} di situs HTML.
#  Pesan menyebut SEBAB + LANGKAH berikutnya, bukan sekadar "error".
# ═════════════════════════════════════════════════════════════
def _hal_galat(request: Request, kode: int, pesan: str, rinci: str):
    situs = situs_untuk(request)
    q = request.query_params.get("q", "")
    return tpl.TemplateResponse(request, "galat.html", {
        "situs": situs, "halaman": "galat", "nama": NAMA, "kanon": str(request.url),
        "judul": f"{kode}: {pesan} | {NAMA}",
        "desk": rinci,
        "kode": kode, "pesan": pesan, "rinci": rinci, "q": q,
    }, status_code=kode)



# =============================================================
#  HEADER KEAMANAN (audit 1 Okt) -- pola mengikuti sakuranovel.id
#  Catatan: web ini pakai FastAPI (bukan Flask), jadi pakai middleware("http").
# =============================================================
@app.middleware("http")
async def _header_aman(request: Request, call_next):
    jawab = await call_next(request)
    H = jawab.headers
    # 1) HSTS -- paksa HTTPS (web sudah HTTPS penuh lewat proxy)
    H.setdefault("Strict-Transport-Security",
                 "max-age=31536000; includeSubDomains")
    # 2) Cegah browser menebak tipe berkas (anti XSS lewat .jpg berisi JS)
    H.setdefault("X-Content-Type-Options", "nosniff")
    # 3) Anti clickjacking
    H.setdefault("X-Frame-Options", "SAMEORIGIN")
    # 4) Jangan bocorkan URL internal ke situs luar
    H.setdefault("Referrer-Policy", "same-origin")
    # 5) Matikan fitur perangkat yang tidak dipakai
    H.setdefault("Permissions-Policy",
                 "geolocation=(), microphone=(), camera=(), payment=(), usb=()")
    # 6) CSP -- mode LAPOR dulu (tidak memblokir apa pun), biar aman
    H.setdefault(
        "Content-Security-Policy-Report-Only",
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "script-src 'self' 'unsafe-inline' https://www.gstatic.com "
        "https://apis.google.com; font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://identitytoolkit.googleapis.com "
        "https://securetoken.googleapis.com; frame-src 'self' "
        "https://naver-zone-id.firebaseapp.com https://navernovel-my-id.firebaseapp.com; object-src 'none'; base-uri 'self'")
    return jawab


@app.exception_handler(404)
async def galat_404(request: Request, exc):
    return _hal_galat(
        request, 404, "Halaman tidak ditemukan",
        "Alamat yang kamu buka tidak ada di arsip ini. "
        "Mungkin tautannya salah ketik, atau novelnya sudah berganti judul.")


@app.exception_handler(500)
async def galat_500(request: Request, exc):
    return _hal_galat(
        request, 500, "Ada gangguan di server",
        "Server sedang bermasalah saat memuat halaman ini. "
        "Coba muat ulang sebentar lagi; kalau tetap gagal, kembali ke beranda.")


# ── route /panel + /api/panel/status (modul terpisah web/admin.py) ──
admin.daftarkan(app, lambda req: getattr(getattr(req, "state", None), "pengguna", None), tpl)
