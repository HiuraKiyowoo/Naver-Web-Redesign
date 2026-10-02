"""admin.py — panel admin Naver (RAHASIA).

🔐 KUNCI ADMIN: HANYA email di ADMIN_BAWAAN/env yang boleh membuka panel.
   Pemeriksaan DI SERVER (bukan JS) → tidak bisa dipalsukan.
   Bukan admin → balas 404 (seperti halaman tidak ada) biar tidak ketahuan.

Modul ini BERDIRI SENDIRI (tidak mengubah auth.py/main.py kecuali
+1 import dan +1 pemanggilan admin.daftarkan(...)).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ── lokasi berkas (BASIS = folder web/) ──
WEB = Path(__file__).resolve().parent          # /root/WORKS/naver-server/web
BASE = WEB.parent                              # /root/WORKS/naver-server
DB_NAVER = BASE / "naver.db"
DB_USER = BASE / "users.db"

ADMIN_BAWAAN = ["hiura0012@gmail.com"]


def daftar_admin() -> list:
    mentah = os.environ.get("NAVER_ADMIN_EMAIL", "").strip()
    if mentah:
        return [x.strip().lower() for x in mentah.split(",") if x.strip()]
    return [x.lower() for x in ADMIN_BAWAAN]


def _email_dari(pengguna):
    if not pengguna:
        return ""
    try:
        return (pengguna["email"] if not isinstance(pengguna, dict) else pengguna.get("email")) or ""
    except Exception:
        # Row sqlite3 / objek lain
        try:
            return pengguna["email"] or ""
        except Exception:
            return ""


def apakah_admin(pengguna) -> bool:
    return _email_dari(pengguna).strip().lower() in daftar_admin()


# ── dB (hanya baca untuk naver.db) ──
def _qnaver(sql, args=(), satu=False):
    c = sqlite3.connect("file:%s?mode=ro" % DB_NAVER, uri=True)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute(sql, args)
        return r.fetchone() if satu else r.fetchall()
    finally:
        c.close()


def _quser(sql, args=(), satu=False):
    c = sqlite3.connect(DB_USER)
    c.row_factory = sqlite3.Row
    try:
        r = c.execute(sql, args)
        return r.fetchone() if satu else r.fetchall()
    finally:
        c.close()


def _satu(sql, args=(), bawaan=0):
    """Ambil satu nilai dari naver.db (kolom pertama)."""
    try:
        r = _qnaver(sql, args, satu=True)
        if r is None:
            return bawaan
        v = r[0]
        return bawaan if v is None else v
    except Exception:
        return bawaan


def _uji_port(port, host="127.0.0.1", detik=1.2):
    try:
        with socket.create_connection((host, port), timeout=detik):
            return True
    except Exception:
        return False


def _uji_http(url, detik=5.0):
    mulai = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NaverPanel/1.0"})
        with urllib.request.urlopen(req, timeout=detik) as r:
            return r.status, int((time.time() - mulai) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, int((time.time() - mulai) * 1000)
    except Exception as e:
        return None, str(e)[:50]


def _proses_ada(nama):
    try:
        out = subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True, timeout=5).stdout
        return any(nama in ln for ln in out.splitlines())
    except Exception:
        return None


router = APIRouter()


def daftarkan(app, ambil_pengguna, tpl):
    """main.py memanggil ini sekali di akhir: admin.daftarkan(app, _ambil_pengguna, tpl)."""

    def _p(request):
        try:
            return ambil_pengguna(request)
        except Exception:
            return None

    # ── HALAMAN /panel ──
    @router.get("/panel", response_class=HTMLResponse)
    def halaman_panel(request: Request):
        p = _p(request)
        if not apakah_admin(p):
            # balas 404 seperti halaman tidak ada
            return HTMLResponse(
                tpl.env.get_template("galat.html").render(
                    request=request, judul="Tidak ditemukan", nama="Naver Novel",
                    kode=404, pesan="Halaman yang kamu cari tidak ada.",
                    rinci="", q="", pengguna=p,
                ), status_code=404)
        return tpl.TemplateResponse(request, "panel.html", {
            "judul": "Panel Admin", "nama": "Naver Novel",
            "pengguna": p, "admin": True,
        })

    # ── API: data panel ──
    @router.get("/api/panel/status")
    def api_panel_status(request: Request):
        p = _p(request)
        if not apakah_admin(p):
            return JSONResponse({"ok": False}, 404)

        # ⚡ cepat: count(*) memakai indeks. sum(length(teks)) DIBUANG (50 dtk!)
        arsip = {
            "novel": _satu("SELECT count(*) FROM novel"),
            "bab": _satu("SELECT count(*) FROM bab"),
            "ilustrasi": _satu("SELECT count(*) FROM bab_gambar"),
            "tanpa_cover": _satu("SELECT count(*) FROM novel WHERE cover_webp IS NULL OR cover_webp=''"),
            "tanpa_sinopsis": _satu("SELECT count(*) FROM novel WHERE sinopsis IS NULL OR sinopsis=''"),
            "rating_kosong": _satu("SELECT count(*) FROM novel WHERE rating IS NULL OR rating=0"),
        }
        # perkiraan jumlah huruf dari 400 bab acak (cepat & cukup akurat)
        try:
            r = _qnaver("SELECT avg(length(teks)) a FROM (SELECT teks FROM bab LIMIT 400)", satu=True)
            rata = (r["a"] or 0) if r else 0
            arsip["huruf_bab_perkiraan"] = int(rata * arsip["bab"])
        except Exception:
            arsip["huruf_bab_perkiraan"] = None

        if not arsip["novel"]:
            arsip["galat"] = "naver.db tidak terbaca"

        try:
            kini = datetime.now(timezone.utc).isoformat()
            sehari = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            peng = {
                "pengguna": _quser("SELECT count(*) c FROM pengguna", satu=True)["c"],
                "favorit": _quser("SELECT count(*) c FROM favorit", satu=True)["c"],
                "baca": _quser("SELECT count(*) c FROM baca_pengguna", satu=True)["c"],
                "request": _quser("SELECT count(*) c FROM request_novel", satu=True)["c"],
                "sesi": _quser("SELECT count(*) c FROM sesi WHERE dicabut=0 AND kedaluwarsa > ?",
                               (kini,), satu=True)["c"],
                "baru24": _quser("SELECT count(*) c FROM pengguna WHERE dibuat > ?",
                                 (sehari,), satu=True)["c"],
            }
        except Exception as e:
            peng = {"galat": str(e)[:70]}

        # ⚡ cek cepat: port lokal + proses (TIDAK memanggil HTTP keluar)
        #    (memanggil HTTP ke publik lewat tunnel = 50 dtk → panel terasa macet)
        sehat = {
            "web_8100": _uji_port(8100),
            "api_8000": _uji_port(8000),
            "tunnel": bool(_proses_ada("cloudflared tunnel")),
            "web_proses": bool(_proses_ada("uvicorn main:app")),
        }

        try:
            sumber = [dict(r) for r in _qnaver(
                "SELECT coalesce(sumber_web,'(kosong)') s, count(*) c "
                "FROM novel GROUP BY s ORDER BY c DESC LIMIT 8")]
        except Exception:
            sumber = []

        try:
            terbaru = [dict(r) for r in _qnaver(
                "SELECT id, slug, judul, jumlah_bab, waktu_update "
                "FROM novel ORDER BY id DESC LIMIT 8")]
        except Exception:
            terbaru = []

        try:
            admin_lihat = [dict(r) for r in _quser(
                "SELECT id, email, nama, peran, dibuat, terakhir FROM pengguna "
                "ORDER BY id DESC LIMIT 10")]
        except Exception:
            admin_lihat = []

        return JSONResponse({
            "ok": True, "waktu": datetime.now(timezone.utc).isoformat(),
            "arsip": arsip, "pengguna": peng, "sehat": sehat,
            "sumber": sumber, "terbaru": terbaru, "akun": admin_lihat,
        })

    app.include_router(router)
    return router
