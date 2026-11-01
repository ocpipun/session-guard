# Session Guard

**Oturumunuzun ne kadar büyüdüğünü, ne kadar ara verdiğinizi ve ne kadar kota kaldığını ölçen, oturum pahalılaşmadan işi düzgünce devreden ve biten oturumları kilitleyerek sonradan yazılan hiçbir şeyin işi karıştırmasına izin vermeyen bir Claude Code eklentisi.**

[English](README.md) · Sürüm 1.2

---

## Neden yazıldı

Bir Claude Code oturumu her mesajla pahalılaşır: her yeni mesaj bütün konuşmayı (*bağlamı*) yeniden gönderir. Birkaç yüz bin tokenı geçince tek bir mesaj, kısa bir oturumun tamamı kadar tutabilir. Bunu üç şey kötüleştirir:

1. **Claude kendi bağlam büyüklüğünü göremez.** Uygulama size gösterir ama model bu sayıyı hiç almaz; bu yüzden kendiliğinden uyaramaz ya da duramaz. `CLAUDE.md`'deki "300k'da devret" gibi kurallar en iyi ihtimalle tahmindir.
2. **Uzun aralar pahalıdır.** İstem önbelleğinin süresi dolar (abonelikte bir saat). Uzun aradan sonraki ilk mesaj bütün bağlamı tam fiyatla yeniden işler.
3. **Kota adımın ortasında biter.** 5 saatlik ya da haftalık sınır dolunca iş nerede kaldıysa orada durur.

Bir de sessiz bir sorun var: devir notunu yazıp yeni oturuma geçtikten sonra eski oturuma tekrar yazmak, aynı işin birbirinden ayrılan iki sürümünü doğurur.

Session Guard bunların hepsini tahminle değil ölçümle çözer.

## Ne yapar

| Durum | Normal mod | Uzun mod |
|---|---|---|
| 250k token | Bildirim | Bildirim |
| 300k | **Devir**: STATUS.md toparlanır, biten maddeler arşive gider, devir notu yazılır. Sonrasında: yeni görev yok, yalnız sorular | Yalnız bildirim |
| 350k | Hedef durma: yeni oturum açın | — |
| 400k | **Kesin durma**, oturum kilitlenir | — |
| 700k | — | Bildirim; iş devredilebilir hâle getirilir (Claude çalışmaya devam edebilir) |
| 800k | — | Yeni iş yok; yalnız basit sorular; son devir notu |
| 900k | — | **Kesin durma**, oturum kilitlenir |
| 1 saatten uzun ara, bağlam > 100k | Mesajınız **çalıştırılmaz**. Claude devir notunu yazar, mesajınızı kelimesi kelimesine nota ekler, oturum kilitlenir | Mola kuralı kapalı |
| 1 saatten uzun ara, bağlam ≤ 100k | Tek satır hatırlatma, sonra mesajınız çalışır | Mola kuralı kapalı |
| Kota %90 (5 saatlik) ya da %98 (haftalık) | Yeni görev yok; mevcut iş 15 dakikada devredilebilir hâle getirilir; sıfırlanana kadar kilit | aynı |
| `finish session` | Devir + kullanım ölçümü, sonra oturum kilitlenir | aynı |

- **Eşikler yalnız Session Guard söyleyince.** Claude hiçbir eşikte önden davranmaz: erken bildirim yok, erken devir yok, sıradaki iş büyük görünüyor diye "yeni oturum açın" yok. Session Guard, Claude çalışırken her araç çağrısından sonra ölçer ve 400k'da durdurur; yukarıdaki plan her zaman geçerlidir. Her eşik size tek biçimde gelir, örneğin: "250k barajı geçildi: yalnız bilgilendirme. *Bu bir Session Guard bildirimidir."
- **Mola kuralı yalnız *sizin* yokluğunuzu sayar.** Claude bir arka plan işi (render, uzun bir betik, alt ajan) başlatıp onu bekliyorsa bu süre mola sayılmaz.
- **Kilitli oturum hiçbir mesajı Claude'a iletmez.** Mesaj gönderilmeden engellenir; kota harcanmaz, hiçbir şey değişmez. Kilitler türüne göre ayrı tutulur: bağlam sınırı, finish session, mola, kota (sıfırlanınca kalkar) ve yeniden başlatma (kurulumdan önce başlamış oturum).
- **Yeniden açılan sohbetler kilitli kalır.** Masaüstü uygulaması eski bir sohbeti yeni oturum kimliğiyle açabilir. Session Guard onu mesaj kimliklerinden tanır; kilit (ve uzun mod) korunur.
- **Oturum başı:** Claude kuralları ve `docs/STATUS.md`'nizi kendiliğinden alır. Bir daha "STATUS.md'yi oku" yazmazsınız; arşiv (`docs/STATUS_ARCHIVE.md`) yalnız gerektiğinde aranır.
- **Token tasarrufu yapan yazım biçimi:** sade kelimeler, kısa cümleler, her konu numaralı, yalnız gereken etiketlerle: SORUN / ÖNERİ / VARSAYIM / UYARI / DOĞRULANMADI (sizin dilinizde). Daha fazla ayrıntı isterseniz sorun.
- **Bilgi bilgidir.** Claude araştırmadığı ya da emin olmadığı şeyi bilgi gibi sunmaz (DOĞRULANMADI etiketi alır); kendi önerilerini ve varsayımlarını kendisininmiş gibi işaretler.
- **Proje dosyaları.** Her proje `CLAUDE.md`, `docs/STATUS.md` ve `docs/STATUS_ARCHIVE.md` alır. Var olan dosyaların üstüne asla yazılmaz. Aynı işi başka adla yapan dosyalar (AGENTS.md, GEMINI.md, .cursorrules, HANDOFF*, NOTES*, TODO*, PROGRESS*, status dosyaları vb.) onayınızla incelenir: çakışan satırlar silinir, işe yarar içerik STATUS / ARCHIVE'a taşınır. Ev klasörünüzde, sürücü kökünde ya da sistem klasörlerinde hiçbir şey oluşturulmaz; git worktree ana çalışma kopyasının dosyalarını kullanır.
- **Tek kural seti.** Session Guard'ın genel kuralları genel `~/.claude/CLAUDE.md` dosyanızda işaretli bir blokta durur. Onlarla çakışan kurallar, skill'ler, betikler ve hook'lar kurulumda onayınızla kaldırılır.
- **Bildirimler sizin dilinizde.** Claude sizinle sizin dilinizde konuşur. Uygulamanın doğrudan gösterdiği bildirimler (kilitler, engellenen mesajlar, eşikler) kurulum anında Claude masaüstü uygulamasının ayarlarında seçili olan dilde gelir (bir kez okunur; Türkçe eklentiyle gelir, diğer dilleri kurulumda Claude çevirir). Okunamazsa Claude sorar: "Which language would you like session-guard notices in?" Bir daha kontrol edilmez; sonradan başka bir dil için Claude'dan eklemesini isteyin. Terminalde İngilizcedir.

## Kota koruması (Session Follow, yalnız bilgisayarda)

- **Terminal:** Session Guard'ın durum çubuğundan okunur, token harcamaz (başka bir durum çubuğunuz yoksa).
- **Masaüstü uygulaması:** orada durum çubuğu çalışmaz; son okuma 20 dakikadan eskiyse Claude uygulamanın kullanım aracını okur (5 saatlik %85 ya da haftalık %95'ten sonra her mesajda; uzun işte en çok 5 dakikada bir). Okuma başına yaklaşık 400 token (tahmin).
- **5 saatlikte %90 ya da haftalıkta %98'de**, sıfırlanmaya en az 45 dakika varsa: Claude yeni görev başlatmaz, mevcut işi 15 dakika içinde devredilebilir hâle getirir, STATUS.md'ye tek satır yazar ("şurada durdu ..."). Sonra oturum sıfırlanmaya kadar kilitlenir. Bu arada açılan oturumlar hemen kilitlenir. Sıfırlanmaya 45 dakikadan az varsa iş sürer.
- **Sıfırlanınca** kilit kendiliğinden kalkar. Normal modda 1 saatten uzun ara verdiyseniz mola kuralı her zamanki gibi uygulanır.
- Bir okuma bilgisayardaki bütün oturumlara yeter (kota hesaba aittir).
- **Bulutta yok:** bulut oturumunda hiçbir hook kotayı okuyamaz (belgelere bakıldı, denendi). Bulut oturumlarında diğer bütün kurallar geçerlidir.

## Komutlar

Mesajın **sonuna** ya da **başına** (ardından virgül veya nokta ile) yazın. Başka bir yerde (cümle içinde, alıntıda, yapıştırılan metinde) bilerek yok sayılır.

| Komut | Etkisi |
|---|---|
| `start long mode` | Yalnız bu oturum için uzun mod. Kapatılamaz; yeni oturum normal modda başlar. Uzun, karmaşık ya da gözetimsiz işler için. |
| `finish session` | Konuyu bitirir: devir notu + kullanım ölçümü, sonra oturum kilitlenir. Bir konu bittiğinde kullanın; böylece bir oturum birden çok konuyu karıştırmaz. |

Örnekler: `Start long mode, render the remaining clips` · `Bugünlük bu kadar, finish session`

## Nasıl çalışır

Session Guard bir Claude Code **eklentisidir**: hook'lar, bir betik, skill'ler ve çevirilerden oluşan bir paket.

- **Hook'lar** betiği üç olayda kendiliğinden çalıştırır: oturum başı, gönderdiğiniz her mesaj ve Claude'un kullandığı her araçtan sonra. Dördüncü, arka plan hook'u 24 saat bekçisidir.
- **Betik** (`scripts/session_guard.py`, Python, yalnız standart kütüphane) Claude Code'un diskinizde zaten tuttuğu oturum kaydını (`~/.claude/projects/...`) okur. Bağlam büyüklüğü Claude'un son yanıtının token kullanımından, ara son yanıttan bu yana geçen süreden alınır. Sonra:
  - bir eşikte (ya da molada, uzun modda, finish session'da, kota durdurmasında) ölçülen sayılarla Claude'un bağlamına talimat ekler; normal bir mesaj hiçbir şey almaz, token harcamaz,
  - bir eşik ilk geçildiğinde size kısa bir bildirim gösterir,
  - kilitli oturumda mesajları engeller,
  - iş ortasında kesin sınıra gelinirse Claude'un turunu durdurur.
- **Skill'ler** prosedürleri tutar (kurulum ve proje incelemesi, devir, mola devri, finish session). Claude onları yalnız gerektiğinde yükler.
- **Durum**, Session Guard'ın `~/.claude/session_guard/` içindeki kendi küçük kaydıdır: oturum başına uzun mod, kilitler ve bildirilen eşikler; ortak kota okumaları (`session_follow/`); kurulum kaydı, bildirim dili ve kendi çevirileriniz (`config/`). İçinde hiçbir proje dosyası ya da sohbet yoktur. 30 gün kullanılmayan oturum kayıtları silinir; kilitli oturumun kaydı tutulur, böylece eski bir oturum aylar sonra açılsa da kilitli kalır.

Hiçbir yere bir şey gönderilmez. Betik yalnız yerel dosyaları okur ve kendi dosyalarını yazar.

## Kurulum (bilgisayar)

Gerekenler: Claude Code (masaüstü uygulaması ya da terminal), Python 3 (Windows'ta `python`, macOS/Linux'ta `python3`). Windows'ta Claude Code hook'lar için Git Bash kullanmalı (Git for Windows kuruluysa varsayılan budur).

Şu tek satırı Claude Code'a yapıştırın:

```
Install Session Guard: run "claude plugin marketplace add ocpipun/session-guard", then follow INSTALL.md in ~/.claude/plugins/marketplaces/session-guard
```

Hepsi o tek oturumda olur:

1. Claude eklentiyi kurar.
2. Genel `~/.claude` klasörünüzü (CLAUDE.md, hafıza dosyaları, skill'ler, komutlar, ajanlar, betikler, çıktı stilleri, kurallar, hook'lar) ve Claude Code'un oturumu olan her projeyi tarar, **tek liste** gösterir: Session Guard'la çakışan ya da onu tekrarlayan kurallar, aynı işi yapan skill, betik ya da hook'lar ve proje dosyalarının işini başka adla yapan dosyalar. Proje bilgileri ve projenin kendi STATUS düzeni çakışma sayılmaz.
3. Aynı mesaj kurulumun başka ne yaptığını da söyler: `~/.claude/CLAUDE.md`'deki genel kurallar bloğu (diğer kurallarınız kalır), eksik proje dosyaları ve `~/.claude/settings.json`'daki durum çubuğu (terminalde kota okuması; zaten başka bir durum çubuğunuz varsa ona dokunulmaz; kod burada herkese açık, MIT). Uygulama dili okunamazsa dil sorusu da bu mesajdadır.
4. **Bir kez** cevap verirsiniz. Onaylanan maddeler kalıcı olarak silinir (yedek kopya yok, yarım taşınmış bir şey kalmasın diye). **Onaylamazsanız Session Guard kapalı kalır;** `/session-guard:setup` ile istediğiniz zaman yeniden çalıştırın.
5. Son adım: **Claude'u tamamen kapatın** (masaüstü uygulamasında sistem tepsisinden de) **ve yeniden açın.** O zamana kadar kurulum sırasında açık olan oturumlar korumasızdır, ya da Session Guard'ın zaten çalıştığı oturumlarda kilitlidir. Yeniden açtıktan sonra hiçbir şey yazmazsınız, size hiçbir şey sorulmaz.

Kullanıcı kapsamında kurulduğu için o bilgisayardaki her projede çalışır. Sonradan açılan ve kendi CLAUDE.md'si, benzer dosyaları, skill'leri ya da hook'ları olan bir proje, yalnız o proje için bir kez aynı incelemeden geçer (kendiliğinden başlar); reddetmek Session Guard'ı yalnız o projede kapatır. Bir güncelleme kuralları değiştirirse kurulum yeniden çalışır.

## Bulut oturumları (claude.ai/code)

Bulut oturumları eklenti kurmaz. Session Guard her depoya bir kez, o deponun bir bulut oturumundan, şu tek satırla kurulur:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Claude deponun inceleme listesini gösterir, "Which language would you like session-guard notices in?" diye sorar (bulut uygulama dilini okuyamaz) ve tek onay sorusu sorar. Reddederseniz hiçbir şey kurulmaz. Onaylarsanız Claude Session Guard'ı deponun `.claude/` klasörüne kurar, eksik proje dosyalarını oluşturur ve hepsini doğrudan deponun ana dalına commit eder. Aynı oturumda devam edersiniz: Session Guard orada bir sonraki mesajınızdan itibaren, o deponun her yeni oturumunda da çalışır. Depo başına bir kez yeter. Bulutta genel CLAUDE.md olmadığı için depo kopyası genel kuralları kendisi getirir. Eklentinin kurulu olduğu bir bilgisayarda depo kopyası sessiz kalır; koruma iki kez çalışmaz. **Bulutta kota koruması yoktur.**

## Kaldırma

Claude'dan Session Guard'ı kaldırmasını isteyin. Kendi durum çubuğunu kaldırır (`session_guard.py --uninstall-statusline`), sonra şunu çalıştırır:

```bash
claude plugin uninstall session-guard@session-guard
```

Ardından `~/.claude/CLAUDE.md`'deki `<!-- session-guard:begin` ile `<!-- session-guard:end -->` arasındaki bloğu silin. Bulut için kurulmuş bir depoda Claude'dan `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` klasörlerini ve `.claude/settings.json`'daki Session Guard hook'larını kaldırmasını isteyin.

## Sınırlar ve açık notlar

- Bağlam sayısı *son yanıtın* büyüklüğüdür, bir adım geriden gelebilir. Claude çalışırken eşikler her araç çağrısından sonra yeniden kontrol edilir.
- "Bitti" haberi hiç gelmeyen bir arka plan işi ya da alt ajan en fazla 24 saat "sürüyor" sayılır. Sonra, ve Claude tek bir mesaj üzerinde 24 saatten uzun çalışırsa, Session Guard bunun bir hata olabileceğini söyler, takıldıysa `finish session` önerir. Her mesajla 24 saatlik bir zamanlayıcı başlar (yerel bir kontrol, Claude'a istek değil, kota harcamaz); yeni mesaj gelmeden 24 saat geçer ve oturum hâlâ meşgulse Claude'u uyandırır, Claude size söyler. Bulutta, bulut boşta kalan oturumu daha önce kapatırsa zamanlayıcı da kapanır.
- Uygulama dili masaüstü uygulamasının kendi ayar dosyasından okunur (`config.json`, anahtar `locale`). Bu dosya belgelenmemiştir: Windows'ta ölçüldü; macOS (`~/Library/Application Support/Claude/`) ve Linux'ta (`~/.config/Claude/`) uygulamanın kodundan çıkarıldı, denenmedi. Bulunamazsa Claude sorar.
- Masaüstünde kota okuması token harcar (tanesi yaklaşık 400, tahmin); terminal durum çubuğu harcamaz.
- "Her proje", Claude Code'un oturumu olan her projedir (`~/.claude/projects`'ten bulunur). Claude Code'da hiç açılmamış klasörler ilk açıldıklarında incelenir.
- Bir maddenin çakışıp çakışmadığına Claude karar verir, siz onaylarsınız; betik yalnız adayları bulur.
- Eşikler betikte sabittir (`NORMAL_STEPS`, `LONG_STEPS`, `STOP_AT`). Çalışma değerleridir, kanıtlanmış kota tasarrufu değildir.
- v1.2 kurulum akışları henüz gerçek bir kurulumda (bilgisayar ya da bulut) denenmedi.

## Testler

```bash
python -m unittest discover -s tests
```

## Lisans

MIT
