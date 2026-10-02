"""
auth.py — sistem login (Firebase + sesi web) & gerbang 3 bab.
Dipisah dari main.py biar rapi dan gampang diperiksa.

ATURAN PENTING:
  • naver.db SELALU BERSIH → data pengguna di users.db
  • UID Firebase dipercaya HANYA setelah token diverifikasi (firebase-admin)
  • Sesi web pakai cookie httpOnly (token acak 32 byte), bukan uid
  • Batas 3 bab dihitung PER NOVEL, per tamu (cookie tamu) / per pengguna
"""
import os
import re
import json
import time
import hmac
import base64
import hashlib
import secrets
import sqlite3
import logging
from datetime import datetime, timedelta

log = logging.getLogger('auth')

DIR = os.path.dirname(os.path.abspath(__file__))
# ⚠️ dB pengguna ada di INDUK folder (bersama naver.db), BUKAN di dalam web/
DB_PENGGUNA = os.environ.get('NAVER_USERS_DB') or os.path.join(DIR, '..', 'users.db')

# ── setelan ────────────────────────────────────────────────────
BATAS_TAMU = 3                     # 3 bab pertama per novel, tanpa login
UMUR_SESI = 30                     # hari
UMUR_TAMU = 180                    # hari (cookie tamu)
COOKIE_SESI = 'nvs'                # sesi pengguna
COOKIE_TAMU = 'nvt'                # jejak tamu
RAHASIA_TAMU = os.environ.get('NAVER_TAMU_KEY') or 'ubah-di-env'

# ── Firebase Admin (opsional — kalau kunci belum ada, fitur dimatikan) ──
_fb = None
_fb_siap = False


def _siapkan_firebase():
    """Muat kunci service account dari ENV (isinya JSON) atau berkas .json.
    Kunci TIDAK PERNAH dicetak ke log/console."""
    global _fb, _fb_siap
    if _fb_siap:
        return _fb is not None
    _fb_siap = True
    try:
        import firebase_admin
        from firebase_admin import credentials
    except Exception as e:
        log.warning('firebase-admin belum terpasang: %s', e)
        return False

    kunci_env = os.environ.get('FIREBASE_SA_JSON')
    kunci_berkas = os.environ.get('FIREBASE_SA_FILE') or os.path.join(DIR, '..', 'rahasia', 'firebase-admin.json')

    kred = None
    try:
        if kunci_env:
            kred = credentials.Certificate(json.loads(kunci_env))
        elif os.path.exists(kunci_berkas):
            kred = credentials.Certificate(kunci_berkas)
    except Exception as e:
        log.error('kunci Firebase gagal dibaca: %s', type(e).__name__)
        return False

    if kred is None:
        log.warning('kunci Firebase belum ada — Google Sign-In dimatikan sementara')
        return False

    try:
        if not firebase_admin._apps:
            _fb = firebase_admin.initialize_app(kred)
        else:
            _fb = firebase_admin.get_app()
        log.info('Firebase Admin SIAP')
        return True
    except Exception as e:
        log.error('Firebase init gagal: %s', type(e).__name__)
        return False


def firebase_siap():
    return _siapkan_firebase()


def verifikasi_token(id_token):
    """Verifikasi ID token Firebase → dict klaim. None kalau tidak sah."""
    if not _siapkan_firebase():
        return None
    try:
        from firebase_admin import auth as fb_auth
        return fb_auth.verify_id_token(id_token, check_revoked=False)
    except Exception as e:
        log.warning('token ditolak: %s', type(e).__name__)
        return None


# ══════════════════════════════════════════════════════════════
#  dB
# ══════════════════════════════════════════════════════════════
def sambung():
    db = sqlite3.connect(DB_PENGGUNA, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def _q(sql, args=(), satu=False):
    db = sambung()
    try:
        cur = db.execute(sql, args)
        r = cur.fetchone() if satu else cur.fetchall()
        db.commit()
        return (dict(r) if (satu and r) else r)
    finally:
        db.close()


def _x(sql, args=()):
    db = sambung()
    try:
        cur = db.execute(sql, args)
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
#  pengguna & sesi
# ══════════════════════════════════════════════════════════════
def pengguna_dari_firebase(klaim):
    """Buat/perbarui baris pengguna dari klaim Firebase. Kembalikan dict."""
    uid = klaim.get('uid') or klaim.get('user_id') or klaim.get('sub')
    if not uid:
        return None
    email = klaim.get('email')
    nama = klaim.get('name') or (email.split('@')[0] if email else 'Pembaca')
    foto = klaim.get('picture')

    lama = _q('SELECT * FROM pengguna WHERE uid=?', (uid,), satu=True)
    if lama:
        _x('UPDATE pengguna SET email=?, nama=?, foto=?, terakhir=datetime("now") WHERE id=?',
           (email, nama, foto, lama['id']))
        return _q('SELECT * FROM pengguna WHERE id=?', (lama['id'],), satu=True)

    pid = _x('INSERT INTO pengguna (uid, email, nama, foto, penyedia) VALUES (?,?,?,?,?)',
             (uid, email, nama, foto, 'firebase'))
    return _q('SELECT * FROM pengguna WHERE id=?', (pid,), satu=True)


def buat_sesi(pengguna_id, ip=None, agen=None):
    token = secrets.token_urlsafe(32)
    ked = (datetime.utcnow() + timedelta(days=UMUR_SESI)).strftime('%Y-%m-%d %H:%M:%S')
    _x('INSERT INTO sesi (token, pengguna_id, ip, agen, kedaluwarsa) VALUES (?,?,?,?,?)',
       (token, pengguna_id, (ip or '')[:60], (agen or '')[:200], ked))
    return token


def pengguna_dari_sesi(token):
    if not token:
        return None
    r = _q("""SELECT p.* FROM sesi s JOIN pengguna p ON p.id=s.pengguna_id
              WHERE s.token=? AND s.dicabut=0 AND s.kedaluwarsa > datetime('now')""",
           (token,), satu=True)
    return r


def akhiri_sesi(token):
    if token:
        _x('UPDATE sesi SET dicabut=1 WHERE token=?', (token,))


# ══════════════════════════════════════════════════════════════
#  tamu & gerbang 3 bab
# ══════════════════════════════════════════════════════════════
def tamu_baru():
    return secrets.token_urlsafe(24)


def _tanda(tamu_id):
    return hmac.new(RAHASIA_TAMU.encode(), tamu_id.encode(), hashlib.sha256).hexdigest()[:32]


def tamu_sah(nilai):
    """cookie tamu berformat '<id>.<tanda>' — dicek supaya tidak bisa dikarang."""
    if not nilai or '.' not in nilai:
        return None
    tid, tanda = nilai.rsplit('.', 1)
    if not tid or len(tid) > 80:
        return None
    if hmac.compare_digest(_tanda(tid), tanda):
        return tid
    return None


def bungkus_tamu(tamu_id):
    return '{}.{}'.format(tamu_id, _tanda(tamu_id))


def sudah_dibaca_tamu(tamu_id, novel_id, urutan):
    return _q('SELECT 1 FROM baca_tamu WHERE tamu_id=? AND novel_id=? AND urutan=?',
              (tamu_id, novel_id, urutan), satu=True) is not None


def catat_baca_tamu(tamu_id, novel_id, urutan):
    try:
        _x('INSERT OR IGNORE INTO baca_tamu (tamu_id, novel_id, urutan) VALUES (?,?,?)',
           (tamu_id, novel_id, urutan))
    except Exception:
        pass


def jumlah_baca_tamu(tamu_id, novel_id):
    r = _q('SELECT COUNT(*) n FROM baca_tamu WHERE tamu_id=? AND novel_id=?',
           (tamu_id, novel_id), satu=True)
    return r['n'] if r else 0


def bab_sudah_dibaca_tamu(tamu_id, novel_id):
    """kumpulan urutan bab yang sudah dibaca tamu ini pada satu novel (set)"""
    b = _q('SELECT urutan FROM baca_tamu WHERE tamu_id=? AND novel_id=?',
           (tamu_id, novel_id))
    return {r['urutan'] for r in b}


def novel_dibaca(pengguna_id):
    """semua nomor urutan bab yang sudah dibaca pengguna, per novel:
    {novel_id: {urutan, ...}} — dipakai untuk tanda ✓ di daftar bab"""
    b = _q('SELECT novel_id, urutan FROM baca_pengguna WHERE pengguna_id=?',
           (pengguna_id,))
    hasil = {}
    for r in b:
        hasil.setdefault(r['novel_id'], set()).add(r['urutan'])
    return hasil


def boleh_baca_tanpa_login(tamu_id, novel_id, urutan):
    """True = masih boleh (≤3 bab & belum pernah baca bab itu).
    False = wajib login."""
    if sudah_dibaca_tamu(tamu_id, novel_id, urutan):
        return True                      # pernah dibaca → jangan halangi ulang
    return jumlah_baca_tamu(tamu_id, novel_id) < BATAS_TAMU


# ══════════════════════════════════════════════════════════════
#  riwayat & favorit (pengguna login)
# ══════════════════════════════════════════════════════════════
def catat_baca(pengguna_id, novel_id, urutan):
    try:
        _x('INSERT OR IGNORE INTO baca_pengguna (pengguna_id, novel_id, urutan) VALUES (?,?,?)',
           (pengguna_id, novel_id, urutan))
    except Exception:
        pass


def riwayat(pengguna_id, batas=30):
    return _q("""SELECT novel_id, MAX(urutan) urutan, MAX(waktu) waktu
                 FROM baca_pengguna WHERE pengguna_id=?
                 GROUP BY novel_id ORDER BY waktu DESC LIMIT ?""",
              (pengguna_id, batas))


# ══════════════════════════════════════════════════════════════
#  REQUEST NOVEL (wajib masuk) — dikirim ke Telegram pemilik
# ══════════════════════════════════════════════════════════════
def permintaan_tambah(pengguna_id, judul, sumber, catatan, halaman=""):
    """simpan permintaan; balikan id baris baru"""
    _x("""INSERT INTO request_novel
          (pengguna_id, judul, sumber, catatan, halaman)
          VALUES (?,?,?,?,?)""",
       (pengguna_id, judul[:200], (sumber or "")[:400],
        (catatan or "")[:600], (halaman or "")[:200]))
    return _q("SELECT id, waktu FROM request_novel WHERE pengguna_id=? ORDER BY id DESC LIMIT 1",
              (pengguna_id,), satu=True)


def permintaan_cek_duplikat(judul):
    """ada tidak permintaan dengan judul sama (belum selesai)?"""
    r = _q("""SELECT 1 FROM request_novel
              WHERE lower(judul)=lower(?) AND status='baru' LIMIT 1""", (judul,), satu=True)
    return r is not None


def permintaan_jumlah_kini(pengguna_id, jam=1):
    """berapa permintaan dalam `jam` jam terakhir (batas laju)"""
    import datetime as _dt
    batas = (_dt.datetime.utcnow() - _dt.timedelta(hours=jam)).strftime('%Y-%m-%d %H:%M:%S')
    r = _q("""SELECT COUNT(*) n FROM request_novel
              WHERE pengguna_id=? AND waktu >= ?""",
           (pengguna_id, batas), satu=True)
    return r['n'] if r else 0


def permintaan_daftar(pengguna_id=None, batas=50):
    if pengguna_id:
        return _q("SELECT * FROM request_novel WHERE pengguna_id=? ORDER BY id DESC LIMIT ?",
                  (pengguna_id, batas))
    return _q("SELECT * FROM request_novel ORDER BY id DESC LIMIT ?", (batas,))


def favorit_tambah(pengguna_id, novel_id):
    _x('INSERT OR IGNORE INTO favorit (pengguna_id, novel_id) VALUES (?,?)',
       (pengguna_id, novel_id))


def favorit_buang(pengguna_id, novel_id):
    _x('DELETE FROM favorit WHERE pengguna_id=? AND novel_id=?', (pengguna_id, novel_id))


def favorit_ada(pengguna_id, novel_id):
    return _q('SELECT 1 FROM favorit WHERE pengguna_id=? AND novel_id=?',
              (pengguna_id, novel_id), satu=True) is not None


def daftar_favorit(pengguna_id):
    return _q('SELECT novel_id, waktu FROM favorit WHERE pengguna_id=? ORDER BY waktu DESC',
              (pengguna_id,))


# ══════════════════════════════════════════════════════════════
#  batas percobaan masuk
# ══════════════════════════════════════════════════════════════
def terlalu_sering(kunci, maks=40, menit=10):
    r = _q("""SELECT COUNT(*) n FROM coba_masuk
              WHERE kunci=? AND waktu > datetime('now', ?)""",
           (kunci, '-{} minutes'.format(menit)), satu=True)
    return (r['n'] if r else 0) >= maks


def catat_coba(kunci):
    _x('INSERT INTO coba_masuk (kunci) VALUES (?)', (kunci[:80],))


def bersihkan_lama():
    """Buang data kedaluwarsa (panggil sesekali)."""
    _x("DELETE FROM sesi WHERE kedaluwarsa < datetime('now','-7 day')")
    _x("DELETE FROM coba_masuk WHERE waktu < datetime('now','-2 day')")


if __name__ == '__main__':
    print('  users.db ada :', os.path.exists(DB_PENGGUNA))
    print('  Firebase siap:', firebase_siap())
    print('  batas tamu   :', BATAS_TAMU, 'bab per novel')
