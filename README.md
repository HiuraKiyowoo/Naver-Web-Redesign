# Naver Web (SSR)

Frontend **Naver** — web baca novel (SSR, ramah SEO) yang membaca
langsung basis data `naver.db` milik **Naver Server**.

Muka kedua dari aplikasi **Naver** (APK): data sama, tampilan web.

## Isi repo

```
main.py              # aplikasi FastAPI (SSR + Jinja2) — semua route
templates/           # halaman Jinja2
  base.html          # kerangka (header, footer, meta SEO)
  beranda.html       # hero + populer + baru + random + ongoing + tag + tamat
  jelajah.html       # daftar + filter (genre/status/tipe/urutan)
  novel.html         # detail novel + daftar bab
  bab.html           # baca bab (teks + ilustrasi + navigasi)
  daftar.html        # hasil daftar (genre/tag/status)
  cari.html          # hasil pencarian
  apk.html           # halaman unduh APK
  _kartu.html        # potongan kartu novel (dipakai berulang)
static/
  web.css            # tema GELAP mengikuti APK:
                     #   latar  #071018 · header #0E1B26 · aksen cyan #049DFD
  web.js             # interaksi ringan (tanpa framework)
```

## Tema warna (ikut APK Naver)

| Peran  | Warna     |
|--------|-----------|
| Latar  | `#12151A` |
| Header | transparan/charcoal dengan blur saat scroll |
| Aksen  | `#049DFD` |

## Cara menjalankan

```bash
# 1. Pasang kebutuhan
pip install fastapi uvicorn jinja2

# 2. Naikkan web (port 8100)
python -m uvicorn main:app --host 127.0.0.1 --port 8100
```

Web membaca `../naver.db` dalam mode **hanya-baca** (`mode=ro`),
jadi aman dijalankan bersamaan dengan scraper yang menulis.

## URL

| Halaman | URL |
|---|---|
| Beranda | `/` |
| Jelajah | `/jelajah` (+ `?genre=&status=&tipe=&order=&hal=`) |
| Genre | `/genre/{slug}` |
| Tag    | `/tag/{slug}` |
| Novel  | `/novel/{slug}` |
| Baca bab | `/novel/{slug}/bab/{urutan}` |
| Cari   | `/cari?q=` |
| APK    | `/apk` |
| status | `/status` |

## SEO

- HTML dirender di server (SSR) → isi langsung terbaca mesin pencari.
- `<title>`, meta deskripsi, kanonik, OG/Twitter card di tiap halaman.
- **Sitemap terpecah** (`/sitemap.xml` = index):
  - `/sitemap-halaman.xml` — beranda, jelajah, genre, tag
  - `/sitemap-novel-{n}.xml` — novel (potong 45.000)
  - `/sitemap-bab-{n}.xml` — bab (potong 45.000)
- `/robots.txt`.

> ⚠️ Batas Google: **50.000 URL / 50 MB per berkas**. Karena ada ratusan
> ribu bab, sitemap **wajib** dipecah seperti di atas.

## Ilustrasi bab

Bab bisa punya ilustrasi. Bila berkasnya sudah diunduh ke server,
gambar disajikan lewat `/api/gambar/{bab_id}/{urutan}`; kalau belum,
halaman memakai URL asli sumbernya. Gambar yang gagal dimuat
disembunyikan otomatis (`onerror`).

## Catatan

- Repo ini **hanya frontend**. Basis data, API, dan scraper **tidak**
  disertakan (ukurannya besar dan berisi data pribadi).
- Jangan pernah menaruh kredensial di repo ini (sudah dijaga `.gitignore`).


## UI redesign terbaru

Repo ini berisi source final setelah redesign UI Naver Web. File preview dan data dummy tidak disertakan.

### Bagian yang didesain ulang

- **Request Novel** (`templates/request.html`)
  - Hero intro yang lebih jelas.
  - Form request dengan field hint, counter catatan, feedback submit, dan riwayat request.
  - Layout responsive desktop/mobile.
  - Backend `/api/request` dan perilaku submit di `static/web.js` tetap dipertahankan.

- **Panel Admin** (`templates/panel.html`)
  - Dashboard charcoal dengan status card server, statistik arsip, statistik pengguna, dan tabel data.
  - Grid Arsip dikunci agar card Novel, Bab, Ilustrasi, dan metrik lain selalu konsisten; desktop 4 kolom dan mobile 2 kolom.
  - Endpoint `/api/panel/status` dan seluruh ID JavaScript tetap dipertahankan.

- **Footer** (`templates/base.html`)
  - Format baru rata tengah:
    - logo gambar yang sudah dipakai situs,
    - `Novel Translation Archive`,
    - `Telegram · Discord · DMCA`,
    - `© 2026 Naver Novel`.

- **Drawer / akun** (`templates/_drawer.html`)
  - Item `Profil / Akun` di menu utama dihapus.
  - Identitas pengguna dipindahkan ke bagian paling atas drawer.
  - User login menampilkan avatar, nama, email, dan status akun/admin.
  - Tamu menampilkan status `Tamu · Masuk` dan mengarah ke `/masuk`.

- **Detail Novel** (`templates/novel.html`, `static/miruro.css`)
  - Hero detail memakai dua layer poster: cover besar sebagai backdrop blur/glass dan cover kecil di tengah.
  - Informasi novel, metadata, genre, sinopsis, dan tombol dibuat terpusat.
  - Background detail menggunakan charcoal `#12151A`.

### Catatan untuk agent AI berikutnya

- **Header adalah locked. Jangan ubah markup atau styling header** di `templates/base.html` dan bagian header `static/miruro.css` tanpa instruksi eksplisit.
- Header yang dipertahankan: logo asli, hamburger drawer, posisi sticky, serta efek blur/glass saat scroll melalui class `.kepala-gulir`.
- Gunakan palet utama charcoal/cyan yang sudah ada di `static/miruro.css`.
- Jangan menghapus ID JavaScript yang sudah dipakai, terutama ID panel Admin dan form Request.
- Preview lokal bersifat sementara dan sengaja tidak dikirim ke repo ini.

### Status publish

Repo ini adalah salinan source final khusus redesign UI. Tidak menyertakan database, credential, file preview, atau file dummy.
