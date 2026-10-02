/* ═══════════════════════════════════════════════════════════
   web.js — perilaku halaman gaya Miruro (tanpa framework)
   ═══════════════════════════════════════════════════════════ */
(function () {
/* ── baca balasan dengan AMAN ──
   Kalau server membalas HTML (halaman galat / gerbang / 502 Cloudflare),
   jangan panggil .json() langsung — nanti muncul
   "Unexpected token '<', "<!DOCTYPE"..." yang membingungkan.
   Fungsi ini mengembalikan objek { ok, pesan, lanjut } yang selalu bisa dibaca. */
function bacaBalasan(r) {
  var ct = (r.headers && r.headers.get('content-type')) || '';
  if (ct.indexOf('application/json') >= 0) {
    return r.json().catch(function () {
      return { ok: false, pesan: 'Balasan server rusak (JSON tidak sah).' };
    });
  }
  return r.text().then(function (t) {
    return {
      ok: false,
      pesan: (r.status === 502 || r.status === 503 || r.status === 504)
        ? 'Server sedang tidak siap (' + r.status + '). Coba lagi sebentar.'
        : 'Server membalas halaman, bukan data (' + r.status + '). Coba muat ulang.'
    };
  });
}

  'use strict';

  /* ── 1. HERO CAROUSEL: kartu tengah membesar, samping mengecil ── */
  var track = document.getElementById('heroTrack');
  var titik = document.getElementById('heroTitik');
  if (track) {
    var item = Array.prototype.slice.call(track.querySelectorAll('.hero-item'));
    var noktah = titik ? Array.prototype.slice.call(titik.querySelectorAll('span')) : [];
    var sekarang = 0;
    var diam = null;

    function tandai(i) {
      sekarang = (i + item.length) % item.length;
      item.forEach(function (el, k) { el.classList.toggle('aktif', k === sekarang); });
      noktah.forEach(function (el, k) { el.classList.toggle('on', k === sekarang); });
    }

    /* Geser ke kartu ke-i dengan menempatkannya di tengah layar. */
    function ke(i) {
      var k = (i + item.length) % item.length;
      var el = item[k];
      if (!el) return;
      var kiri = el.offsetLeft - (track.clientWidth - el.offsetWidth) / 2;
      track.scrollTo({ left: Math.max(0, kiri), behavior: 'smooth' });
      tandai(k);
      mulaiDiam();
    }

    /* Saat pengguna menggeser sendiri: tandai kartu terdekat ke tengah. */
    function dariGulir() {
      var tengah = track.scrollLeft + track.clientWidth / 2;
      var dekat = 0, jarak = Infinity;
      item.forEach(function (el, k) {
        var p = el.offsetLeft + el.offsetWidth / 2;
        var d = Math.abs(p - tengah);
        if (d < jarak) { jarak = d; dekat = k; }
      });
      if (dekat !== sekarang) tandai(dekat);
    }

    /* Rotasi otomatis tiap 6 dtk, berhenti sebentar setelah disentuh. */
    function mulaiDiam() {
      clearTimeout(diam);
      diam = setTimeout(function () { ke(sekarang + 1); }, 6000);
    }

    track.addEventListener('scroll', function () {
      window.clearTimeout(track._t);
      track._t = setTimeout(dariGulir, 90);
    }, { passive: true });

    ['pointerdown', 'touchstart', 'wheel'].forEach(function (ev) {
      track.addEventListener(ev, function () { clearTimeout(diam); }, { passive: true });
    });
    track.addEventListener('touchend', mulaiDiam, { passive: true });

    noktah.forEach(function (el, k) {
      el.addEventListener('click', function () { ke(k); });
    });

    /* Panah kiri/kanan papan ketik */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft') ke(sekarang - 1);
      if (e.key === 'ArrowRight') ke(sekarang + 1);
    });

    /* Mulai di kartu pertama (posisi tengah) */
    requestAnimationFrame(function () { ke(0); });
  }

  /* ── 2. TOMBOL PANAH RAK: geser rak ke kiri/kanan ── */
  document.querySelectorAll('[data-geser]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var rak = document.getElementById(btn.getAttribute('data-rak'));
      if (!rak) return;
      var langkah = Math.max(240, rak.clientWidth * 0.8);
      rak.scrollBy({
        left: btn.getAttribute('data-geser') === 'kiri' ? -langkah : langkah,
        behavior: 'smooth'
      });
    });
  });

  /* ── 3. Sembunyikan panah kalau rak tidak bisa digeser ── */
  document.querySelectorAll('.rak').forEach(function (rak) {
    var kotak = rak.closest('.bagian');
    if (!kotak) return;
    var panah = kotak.querySelectorAll('[data-geser]');
    function segarkan() {
      var bisa = rak.scrollWidth > rak.clientWidth + 8;
      var diAwal = rak.scrollLeft <= 4;
      var diAkhir = rak.scrollLeft + rak.clientWidth >= rak.scrollWidth - 4;
      if (panah[0]) panah[0].style.opacity = (bisa && !diAwal) ? '1' : '.35';
      if (panah[1]) panah[1].style.opacity = (bisa && !diAkhir) ? '1' : '.35';
    }
    rak.addEventListener('scroll', segarkan, { passive: true });
    window.addEventListener('resize', segarkan);
    segarkan();
  });
  /* ── 4. KIPAS "Jelajahi Acak": mekar saat masuk layar (#1)
         + kartu terangkat saat ditunjuk (#3).
     Hemat: cuma pakai transform/opacity (tidak memicu layout ulang).
     Animasi baru jalan saat section masuk layar (IntersectionObserver). */
  var kipas = document.querySelector('.fan');
  if (kipas && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var kartuKipas = Array.prototype.slice.call(kipas.querySelectorAll('.fi'));
    kipas.classList.add('kipas-siap');

    /* Sudut asli (--r) & geser (--ty) SUDAH dipasang dari server di _kipas.html
       → tidak perlu hitung ulang di sini. Kalau ada yang kosong, isi cadangan. */
    kartuKipas.forEach(function (el) {
      if (!el.style.getPropertyValue('--r')) {
        var m = /rotate\((-?[\d.]+)deg\)/.exec(el.getAttribute('style') || '');
        el.style.setProperty('--r', (m ? m[1] : '0') + 'deg');
      }
    });

    function mekar() {
      kartuKipas.forEach(function (el, i) {
        el.style.animationDelay = (i * 60) + 'ms';
      });
      kipas.classList.add('kipas-mekar');
    }

    if ('IntersectionObserver' in window) {
      /* ⚠️ Pelajaran 29 Sep: threshold 0.25 bikin animasi sering TIDAK jalan
         (perlu 25% .fan terlihat) → section kelihatan KOSONG. Sekarang:
         threshold kecil + rootMargin bawah 15% supaya animasi mulai tepat
         saat kipas mau masuk layar. */
      var amat = new IntersectionObserver(function (masuk) {
        masuk.forEach(function (m) {
          if (m.isIntersecting) { mekar(); amat.disconnect(); }
        });
      }, { threshold: 0.05, rootMargin: '0px 0px -8% 0px' });
      amat.observe(kipas);

      /* PENGAMAN: kalau karena apa pun animasi belum jalan dalam 2,5 dtk
         setelah pengamat dipasang DAN kipas sudah masuk layar, paksa mekar.
         (mencegah section kosong permanen) */
      setTimeout(function () {
        var r = kipas.getBoundingClientRect();
        if (!kipas.classList.contains('kipas-mekar') &&
            r.top < window.innerHeight && r.bottom > 0) {
          mekar();
          amat.disconnect();
        }
      }, 2500);
    } else {
      mekar();
    }
  }

  /* ══════════════════════════════════════════════════════════════
     MINGGU INI — section sorotan (gaya "Opening Story")
     Prev/Next BERFUNGSI: memutar indeks daftar novel, lalu mengganti
     cover (tengah + 2 tetangga), tag, judul, penulis, sinopsis, dan
     judul Sebelumnya/Berikutnya — tanpa memuat ulang halaman.
     ⚠️ Data diambil dari atribut data-minggu (JSON) di _minggu.html,
        BUKAN dari <script> inline (biar aman & tidak kena CSP).
     ══════════════════════════════════════════════════════════════ */
  var mgData = document.getElementById('mgData');
  if (mgData) {
    var mg = [];
    try { mg = JSON.parse(mgData.getAttribute('data-minggu') || '[]'); } catch (e) { mg = []; }

    var elKiri = document.getElementById('mgKiri');
    var elUtama = document.getElementById('mgUtama');
    var elKanan = document.getElementById('mgKanan');
    var elTags = document.getElementById('mgTags');
    var elJudul = document.getElementById('mgJudul');
    var elPenulis = document.getElementById('mgPenulis');
    var elDesk = document.getElementById('mgDesk');
    var elMore = document.getElementById('mgMore');
    var elPrevJ = document.getElementById('mgPrevJudul');
    var elNextJ = document.getElementById('mgNextJudul');

    var mgIdx = 0;
    function mgAmbil(i) {            /* indeks melingkar (tidak pernah habis) */
      var n = mg.length;
      return mg[((i % n) + n) % n];
    }

    function mgGambar(el, elImg, n) {
      if (!n) return;
      var c = n.cover || '';
      if (elImg.getAttribute('src') !== c) elImg.setAttribute('src', c);
      elImg.setAttribute('alt', 'Cover ' + (n.judul || ''));
      el.setAttribute('href', n.url || '#');
    }

    var mgArah = 0;   /* -1 = mundur, +1 = maju, 0 = pertama kali */

    function mgBersihAnim() {
      var ae = document.querySelectorAll('.mg-anim');
      for (var k = 0; k < ae.length; k++) ae[k].classList.remove('mg-anim');
    }

    function mgPasangAnim(geser) {
      if (geser === undefined) geser = mgArah < 0 ? -26 : (mgArah > 0 ? 26 : 0);
      document.documentElement.style.setProperty('--mg-geser', geser + 'px');
      var sel = [elKiri, elUtama, elKanan,
                 document.querySelector('.mg-tags'),
                 document.querySelector('.mg-meta'),
                 document.querySelector('.mg-nav')];
      for (var k = 0; k < sel.length; k++) {
        if (!sel[k]) continue;
        sel[k].classList.remove('mg-anim');
        void sel[k].offsetWidth;          /* paksa reflow → animasi restart */
        sel[k].classList.add('mg-anim');
      }
      window.setTimeout(mgBersihAnim, 700);
    }

    /* pra-muat cover tetangga supaya ganti kartu tidak kedip */
    function mgPra(n) {
      if (!n || !n.cover) return;
      var im = new Image();
      im.src = n.cover;
    }

    function mgTampil(i) {
      if (!mg.length) return;
      mgIdx = i;
      var n = mgAmbil(i);
      mgGambar(elKiri, document.getElementById('mgKiriImg'), mgAmbil(i - 1));
      mgGambar(elUtama, document.getElementById('mgUtamaImg'), n);
      mgGambar(elKanan, document.getElementById('mgKananImg'), mgAmbil(i + 1));

      elJudul.textContent = n.judul || '';
      elPenulis.textContent = n.penulis || '';
      elDesk.textContent = n.desk || '';
      elMore.setAttribute('href', n.url || '#');

      elTags.innerHTML = '';
      (n.tags || []).forEach(function (tg) {
        var s = document.createElement('span');
        s.className = 'mg-tag';
        s.textContent = '#' + tg;
        elTags.appendChild(s);
      });

      elPrevJ.textContent = mgAmbil(i - 1).judul || '—';
      elNextJ.textContent = mgAmbil(i + 1).judul || '—';

      /* pra-muat tetangga → kartu berikutnya tidak kedip */
      mgPra(mgAmbil(i - 2)); mgPra(mgAmbil(i - 1));
      mgPra(mgAmbil(i + 1)); mgPra(mgAmbil(i + 2));
    }

    ['mgPrev', 'mgNext'].forEach(function (id) {
      var b = document.getElementById(id);
      if (b) b.addEventListener('click', function () {
        mgArah = parseInt(b.getAttribute('data-dir'), 10) || 0;
        mgTampil(mgIdx + mgArah);
        mgPasangAnim();
      });
    });

    /* geser (swipe) di area cover untuk HP */
    var area = document.querySelector('.mg-stage');
    if (area) {
      var x0 = null;
      area.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
      area.addEventListener('touchend', function (e) {
        if (x0 === null) return;
        var d = e.changedTouches[0].clientX - x0;
        if (Math.abs(d) > 40) {
          mgArah = (d < 0 ? 1 : -1);
          mgTampil(mgIdx + mgArah);
          mgPasangAnim(mgArah * 26);
        }
        x0 = null;
      }, { passive: true });
    }

    mgTampil(0);
    mgPasangAnim(0);
  }

  /* ── HEADER: jadi kaca/blur saat halaman discroll ── */
  (function () {
    var kp = document.querySelector('.kepala');
    if (!kp) return;
    var ambang = 8, minta = false;
    function segar() {
      minta = false;
      var y = window.scrollY || window.pageYOffset || 0;
      if (y > ambang) kp.classList.add('kepala-gulir');
      else kp.classList.remove('kepala-gulir');
    }
    window.addEventListener('scroll', function () {
      if (!minta) { minta = true; requestAnimationFrame(segar); }
    }, { passive: true });
    segar();
  })();

  /* ══ NAV SAMPING ══
     ⚠️ 29 Sep: logika buka/tutup laci dipindah ke dalam templates/_drawer.html
     (berkas laci baru punya skripnya sendiri). Blok lama dihapus supaya
     tidak ada dua skrip berebut elemen yang sama. */
})();


/* ══════════════════════════════════════════════════════════
   GERBANG BAB: kotak login nempel di bawah (fixed)
   → cukup beri kelas pada body supaya isi bab dapat ruang bawah
   ══════════════════════════════════════════════════════════ */
(function(){
  if (document.querySelector('.kunci')) document.body.classList.add('ada-kunci');
})();


/* ══════════════════════════════════════════════════════════
   TOMBOL FAVORIT (♥) di halaman detail novel
   ══════════════════════════════════════════════════════════ */
(function(){
  var b = document.getElementById('btnFavorit');
  if (!b) return;
  var hati = b.querySelector('.hati');
  var teks = b.querySelector('.hati-teks');
  var novelId = b.getAttribute('data-novel');
  var sibuk = false;

  function pasang(aktif){
    b.classList.toggle('aktif', aktif);
    b.setAttribute('aria-pressed', aktif ? 'true' : 'false');
    if (hati) hati.textContent = aktif ? '\u2665' : '\u2661';
    if (teks) teks.textContent = aktif ? 'Tersimpan' : 'Favorit';
  }

  b.addEventListener('click', function(){
    if (sibuk) return;
    sibuk = true;
    b.classList.add('sedang');
    fetch('/api/favorit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify({novel_id: Number(novelId)})
    }).then(function(r){
      if (r.status === 401) {           // belum masuk → ke halaman masuk
        var lanjut = encodeURIComponent(location.pathname);
        location.href = '/masuk?lanjut=' + lanjut;
        return null;
      }
      return bacaBalasan(r);
    }).then(function(d){
      if (d && d.ok) pasang(d.favorit);
      sibuk = false;
      b.classList.remove('sedang');
    }).catch(function(){
      sibuk = false;
      b.classList.remove('sedang');
    });
  });
})();


/* ══════════════════════════════════════════════════════════
   FORMULIR REQUEST NOVEL
   ══════════════════════════════════════════════════════════ */
(function(){
  var f = document.getElementById('formRequest');
  if (!f) return;
  var judul = document.getElementById('reqJudul');
  var sumber = document.getElementById('reqSumber');
  var catatan = document.getElementById('reqCatatan');
  var hitung = document.getElementById('reqHitung');
  var tombol = document.getElementById('reqKirim');
  var pesan = document.getElementById('reqPesan');
  var sibuk = false;

  function tulis(kelas, teks){ pesan.className = 'req-pesan ' + (kelas || ''); pesan.textContent = teks || ''; }

  catatan.addEventListener('input', function(){
    hitung.textContent = catatan.value.length + '/600';
  });

  function kirim(){
    if (sibuk) return;
    var j = judul.value.trim();
    if (j.length < 2){ tulis('gagal', 'Judulnya diisi dulu ya (minimal 2 huruf).'); judul.focus(); return; }
    sibuk = true;
    tombol.disabled = true;
    tulis('sedang', 'Mengirim…');
    fetch('/api/request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify({
        judul: j,
        sumber: sumber.value.trim(),
        catatan: catatan.value.trim(),
        halaman: location.pathname
      })
    }).then(function(r){
      if (r.status === 401){ location.href = '/masuk?lanjut=/request'; return null; }
      return bacaBalasan(r);
    }).then(function(d){
      sibuk = false;
      tombol.disabled = false;
      if (!d) return;
      if (d.ok){
        tulis('ok', d.balasan || 'Request terkirim!');
        judul.value = ''; sumber.value = ''; catatan.value = '';
        hitung.textContent = '0/600';
      } else {
        tulis('gagal', d.balasan || 'Gagal mengirim. Coba lagi ya.');
      }
    }).catch(function(){
      sibuk = false; tombol.disabled = false;
      tulis('gagal', 'Koneksi bermasalah. Coba lagi ya.');
    });
  }

  f.addEventListener('submit', function(e){ e.preventDefault(); kirim(); });
})();

/* ═══ GENRE/TAG: tombol "+N lagi" (halaman detail) ═══ */
(function () {
  'use strict';
  ['genre', 'tag'].forEach(function (nama) {
    var tombol = document.getElementById(nama + 'Lagi');
    var sisa = document.getElementById(nama + 'Sisa');
    if (!tombol || !sisa) return;

    var jumlah = (tombol.textContent.match(/\d+/) || ['0'])[0];
    var buka = false;

    tombol.addEventListener('click', function () {
      buka = !buka;
      sisa.hidden = !buka;
      tombol.setAttribute('aria-expanded', buka ? 'true' : 'false');
      tombol.textContent = buka ? '− Sembunyikan' : '+' + jumlah + ' lagi';
    });
  });
})();
