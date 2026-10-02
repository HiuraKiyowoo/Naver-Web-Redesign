
# ═══════════════════════════════════════════════════════════════
#  rate_limit.py — pertahanan anti-scrape (audit 1 Okt, putaran 2)
#  Hitungan disimpan di SQLite -> tidak hilang saat PM2 restart.
# ═══════════════════════════════════════════════════════════════
import sqlite3
import time
import threading

DB = "/root/naver-web/rate_limit.db"
_lok = threading.Lock()
_siap = False


def _sambung():
    s = sqlite3.connect(DB, timeout=5)
    s.execute("PRAGMA journal_mode=WAL")
    return s


def siapkan():
    global _siap
    if _siap:
        return
    with _lok:
        if _siap:
            return
        s = _sambung()
        s.execute("""CREATE TABLE IF NOT EXISTS jejak
                     (ip TEXT PRIMARY KEY, umum TEXT DEFAULT '',
                      bab TEXT DEFAULT '', langgar INTEGER DEFAULT 0,
                      blokir_sampai REAL DEFAULT 0, honeypot INTEGER DEFAULT 0)""")
        s.commit()
        s.close()
        _siap = True


def ip_asli(request):
    """IP pengunjung yang SEBENARNYA.

    Hasil uji log server (dengan mata):
      - request.client.host  = 192.168.11.1  (proxy NAT, SAMA utk semua) -> TIDAK BERGUNA
      - x-real-ip            = 216.73.216.255 (IP asli) -> DIPAKAI
      - x-forwarded-for      = 216.73.216.255 (IP asli) -> CADANGAN

    Catatan: header ini diisi oleh PROXY. Kalau web langsung terbuka ke
    internet (tanpa proxy), klien bisa memalsukannya -> makanya ada
    PEMERIKSAAN: kalau header tidak ada, pakai client.host.
    """
    # ⚠️ PELAJARAN 2 Okt: di belakang CLOUDFLARE, IP asli ada di header
    #    'CF-Connecting-IP'. Tanpa itu semua trafik dianggap SATU IP edge CF
    #    -> rate limit dibagi semua pengunjung -> 429 palsu.
    cfc = (request.headers.get("cf-connecting-ip") or "").strip()
    if cfc:
        return cfc
    xri = (request.headers.get("x-real-ip") or "").strip()
    if xri:
        return xri
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        alamat = [a.strip() for a in xff.split(",") if a.strip()]
        if alamat:
            return alamat[0]      # proxy kita menaruh IP asli di KIRI
    return request.client.host if request.client else "?"


def periksa(request, jalur="", batas_umum=600, batas_bab=60, jendela=60):
    """True kalau permintaan harus DITOLAK (dan sudah menghukum bila perlu).
    Mengembalikan (kena, sisa_detik, sebab)."""
    siapkan()
    ip = ip_asli(request)
    if _privat(ip):
        return False, 0, ""     # IP privat/loopback: jangan pernah dihukum
    kini = time.time()
    with _lok:
        s = _sambung()
        baris = s.execute(
            "SELECT umum, bab, langgar, blokir_sampai, honeypot FROM jejak WHERE ip=?",
            (ip,)).fetchone()
        if baris is None:
            umum, bab, langgar, blokir, honey = "", "", 0, 0.0, 0
        else:
            umum, bab, langgar, blokir, honey = baris

        if kini < blokir:
            sisa = int(blokir - kini)
            s.close()
            return True, sisa, ("honeypot" if honey else "hukuman")

        def segar(txt):
            return [float(x) for x in txt.split(",") if x and kini - float(x) < jendela]

        # pelanggaran 'lutur': sudah 1 jam tanpa pelanggaran -> mulai bersih
        if langgar and baris is not None and "" == umum:
            langgar = 0
        u = segar(umum); u.append(kini)
        b = segar(bab)
        kena = len(u) > batas_umum
        sebab = "umum"
        if jalur and ("/bab/" in jalur or "/chapter/" in jalur):
            b.append(kini)
            if len(b) > batas_bab:
                kena = True; sebab = "bab"
        if kena:
            langgar += 1
            denda = [60, 900, 3600, 21600, 86400][min(langgar, 5) - 1]
            blokir = kini + denda
        s.execute("""INSERT INTO jejak(ip,umum,bab,langgar,blokir_sampai,honeypot)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(ip) DO UPDATE SET umum=?,bab=?,langgar=?,
                     blokir_sampai=?,honeypot=?""",
                  (ip, ",".join(map(str, u[-400:])), ",".join(map(str, b[-200:])),
                   langgar, blokir, honey,
                   ",".join(map(str, u[-400:])), ",".join(map(str, b[-200:])),
                   langgar, blokir, honey))
        s.commit()
        s.close()
        if kena:
            return True, int(blokir - kini), sebab
        return False, 0, ""




IP_PROXY_NAT = ("192.168.11.1",)      # alamat proxy NAT di depan (sama utk semua)
IP_LOOPBACK = ("127.", "::1", "localhost", "0.0.0.0")

PEMILIK = ['103.132.41.180']          # IP pemilik -> selalu bebas


def _privat(ip: str) -> bool:
    """Hanya IP yang TIDAK BOLEH dihukum:
      - kosong / "?"
      - loopback
      - alamat proxy NAT (dipakai bersama semua pengunjung; menghukumnya
        = memblokir seluruh dunia)
      - IP pemilik (whitelist)
    IP privat operator seluler TIDAK dikecualikan -> tetap dihukum."""
    if not ip or ip == "?":
        return True
    if ip in IP_PROXY_NAT:
        return True
    if ip.startswith(IP_LOOPBACK):
        return True
    if ip in PEMILIK:
        return True
    return False


def kena_honeypot(request):
    """Link gaib diakses -> blokir panjang. Loopback dikecualikan."""
    siapkan()
    ip = ip_asli(request)
    if _privat(ip):
        return ip          # jangan hukum, cukup catat
    kini = time.time()
    with _lok:
        s = _sambung()
        baris = s.execute("SELECT langgar FROM jejak WHERE ip=?", (ip,)).fetchone()
        langgar = max(baris[0] if baris else 0, 4)   # >=4 -> hukuman 24 jam
        # TIDAK menumpuk: kalau sudah 24 jam, tetap 24 jam (tidak makin panjang)
        s.execute("""INSERT INTO jejak(ip,langgar,blokir_sampai,honeypot)
                     VALUES(?,?,?,1) ON CONFLICT(ip) DO UPDATE SET
                     langgar=?, blokir_sampai=?, honeypot=1""",
                  (ip, langgar, kini + 86400, langgar, kini + 86400))
        s.commit()
        s.close()
    return ip
