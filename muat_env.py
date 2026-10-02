import os

# ══════════════════════════════════════════════════════════════
#  muat-env.py — dibaca main.py saat start:
#    1) /root/.hermes/fb-web.env      → kunci WEB Firebase (publik)
#    2) /root/.hermes/fb-admin.env    → kunci SERVER (RAHASIA, JSON)
#    3) naver-tamu.env                → kunci tanda-tangan cookie tamu
#  ⚠️ ISI BERKAS INI TIDAK PERNAH DICETAK KE LOG.
# ══════════════════════════════════════════════════════════════
BERKAS = [
    '/root/.hermes/fb-web.env',
    '/root/.hermes/fb-admin.env',
    '/root/.hermes/naver-tamu.env',
]


def _baca_jalur(p):
    if not os.path.exists(p):
        return {}
    hasil = {}
    try:
        with open(p, 'r') as f:
            for baris in f:
                baris = baris.strip()
                if not baris or baris.startswith('#') or '=' not in baris:
                    continue
                if baris.startswith('export '):
                    baris = baris[7:]
                k, _, v = baris.partition('=')
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                if k:
                    hasil[k] = v
    except Exception:
        return {}
    return hasil


def muat(paksa=False):
    """Tempelkan ke os.environ (tidak menimpa yang sudah ada, kecuali paksa)."""
    terpasang = []
    for p in BERKAS:
        d = _baca_jalur(p)
        for k, v in d.items():
            if paksa or k not in os.environ:
                os.environ[k] = v
                terpasang.append(k)
    return terpasang


def rahasia_ada():
    return os.path.exists('/root/.hermes/fb-admin.env')


if __name__ == '__main__':
    n = muat()
    print('  env terpasang: {} kunci'.format(len(n)))
    for k in sorted(set(n)):
        print('    ' + k)
    print('  kunci server (fb-admin.env): {}'.format('ADA' if rahasia_ada() else 'belum'))
