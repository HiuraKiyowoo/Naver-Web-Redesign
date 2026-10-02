/* ══════════════════════════════════════════════════════════════
   auth-web.js — Masuk/Daftar pakai Firebase (Google + Email/Sandi)
   Cara kerja:
     1) Pengguna masuk lewat Firebase (di BROWSER)
     2) Firebase menyerahkan ID token
     3) Token itu dikirim ke /api/auth/sesi → DIVERIFIKASI di server
     4) Server memasang cookie sesi httpOnly
   ⚠️ Batas 3 bab TIDAK bisa diakali: hitungannya di server (users.db),
      bukan di cookie yang bisa dihapus pengguna.
   ══════════════════════════════════════════════════════════════ */
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

  /* config Firebase dipasok /static/fb.js (window.FB_KONFIG) */
  var KONFIG = window.FB_KONFIG || {};

  var elGalat = document.getElementById('authGalat');
  var btnGoogle = document.getElementById('btnGoogle');
  var form = document.getElementById('formEmail');
  var btnKirim = document.getElementById('btnKirim');
  var labelSandi2 = document.getElementById('labelSandi2');
  var sandi2 = document.getElementById('sandi2');
  var linkAli = document.getElementById('linkAli');
  var teksAli = document.getElementById('teksAli');
  var linkLupa = document.getElementById('linkLupa');
  var judul = document.querySelector('.auth-judul');
  var mode = 'masuk';   /* masuk | daftar */

  function tampilPesan(teks) {
    if (!elGalat) return;
    elGalat.textContent = teks;
    elGalat.hidden = !teks;
  }
  function sibuk(ya) {
    if (btnKirim) { btnKirim.disabled = ya; btnKirim.textContent = ya ? 'Mohon tunggu…' : (mode === 'masuk' ? 'Masuk' : 'Daftar'); }
    if (btnGoogle) btnGoogle.disabled = ya;
  }

  /* kalau Firebase belum siap di server, jangan lanjut */
  if (!KONFIG.apiKey) {
    if (btnGoogle) btnGoogle.disabled = true;
    return;
  }

  /* ── muat pustaka Firebase (kompat) ── */
  var libs = [
    'https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js',
    'https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js'
  ];
  var n = 0;
  function muat() {
    if (n >= libs.length) { mulai(); return; }
    var s = document.createElement('script');
    s.src = libs[n++];
    s.onload = muat;
    s.onerror = function () { tampilPesan('Gagal memuat Firebase. Periksa koneksi internet.'); };
    document.head.appendChild(s);
  }

  function mulai() {
    if (!window.firebase) { tampilPesan('Firebase tidak tersedia.'); return; }
    try { firebase.initializeApp(KONFIG); } catch (e) { /* sudah ada */ }
    var auth = firebase.auth();

    /* ── Google ── */
    if (btnGoogle) {
      btnGoogle.addEventListener('click', function () {
        tampilPesan('');
        sibuk(true);
        var p = new firebase.auth.GoogleAuthProvider();
        p.setCustomParameters({ prompt: 'select_account' });
        auth.signInWithPopup(p)
          .then(function (r) { return kirimToken(r.user); })
          .catch(function (e) { sibuk(false); tampilPesan(pesan(e)); });
      });
    }

    /* ── Email + sandi ── */
    if (form) {
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        tampilPesan('');
        var email = document.getElementById('email').value.trim();
        var sandi = document.getElementById('sandi').value;
        if (!email || sandi.length < 6) { tampilPesan('Email & sandi minimal 6 huruf.'); return; }

        sibuk(true);
        if (mode === 'masuk') {
          auth.signInWithEmailAndPassword(email, sandi)
            .then(function (r) { return kirimToken(r.user); })
            .catch(function (e) { sibuk(false); tampilPesan(pesan(e)); });
        } else {
          if (!sandi2 || sandi !== sandi2.value) { sibuk(false); tampilPesan('Sandi tidak sama.'); return; }
          auth.createUserWithEmailAndPassword(email, sandi)
            .then(function (r) {
              if (r.user && r.user.sendEmailVerification) {
                try { r.user.sendEmailVerification(); } catch (e) {}
              }
              return kirimToken(r.user);
            })
            .catch(function (e) { sibuk(false); tampilPesan(pesan(e)); });
        }
      });
    }

    /* ── Lupa sandi ── */
    if (linkLupa) {
      linkLupa.addEventListener('click', function (ev) {
        ev.preventDefault();
        var email = (document.getElementById('email') || {}).value || '';
        email = email.trim();
        if (!email) { tampilPesan('Isi email dulu, lalu klik "Lupa sandi?".'); return; }
        auth.sendPasswordResetEmail(email)
          .then(function () { tampilPesan(''); alert('Tautan atur ulang sandi sudah dikirim ke ' + email); })
          .catch(function (e) { tampilPesan(pesan(e)); });
      });
    }

    /* ── ganti Masuk <-> Daftar ── */
    if (linkAli) {
      linkAli.addEventListener('click', function (ev) {
        ev.preventDefault();
        mode = (mode === 'masuk') ? 'daftar' : 'masuk';
        var d = (mode === 'daftar');
        if (labelSandi2) labelSandi2.hidden = !d;
        if (sandi2) sandi2.hidden = !d;
        sandi2 && (sandi2.required = d);
        if (btnKirim) btnKirim.textContent = d ? 'Daftar' : 'Masuk';
        if (teksAli) teksAli.textContent = d ? 'Sudah punya akun?' : 'Belum punya akun?';
        linkAli.textContent = d ? 'Masuk' : 'Daftar';
        if (judul) judul.textContent = d ? 'Buat akun baru' : 'Masuk untuk lanjut membaca';
        var s1 = document.getElementById('sandi');
        if (s1) s1.autocomplete = d ? 'new-password' : 'current-password';
        tampilPesan('');
      });
    }
  }

  /* ── kirim ID token ke server ── */
  function kirimToken(user) {
    return user.getIdToken(true).then(function (tok) {
      var lanjut = new URLSearchParams(location.search).get('lanjut') || '/';
      return fetch('/api/auth/sesi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ idToken: tok, lanjut: lanjut })
      });
    }).then(bacaBalasan)
      .then(function (j) {
        if (!j.ok) throw new Error(j.pesan || 'Gagal masuk.');
        location.href = j.lanjut || '/';
      })
      .catch(function (e) { sibuk(false); tampilPesan(e.message || 'Gagal masuk.'); });
  }

  function pesan(e) {
    var k = (e && e.code) || '';
    if (k.indexOf('wrong-password') >= 0 || k.indexOf('invalid-credential') >= 0) return 'Email atau sandi salah.';
    if (k.indexOf('user-not-found') >= 0) return 'Akun tidak ada. Pilih "Daftar" dulu.';
    if (k.indexOf('email-already-in-use') >= 0) return 'Email sudah dipakai. Pilih "Masuk".';
    if (k.indexOf('weak-password') >= 0) return 'Sandi terlalu lemah (minimal 6 huruf).';
    if (k.indexOf('invalid-email') >= 0) return 'Bentuk email tidak sah.';
    if (k.indexOf('popup-closed') >= 0) return '';
    if (k.indexOf('too-many-requests') >= 0) return 'Terlalu banyak percobaan. Tunggu sebentar.';
    return (e && e.message) || 'Terjadi kesalahan.';
  }

  muat();
})();
