# Session Guard

**Claude Code oturumunun ne kadar büyüdüğünü ve ne kadar ara verdiğinizi ölçen, oturum pahalılaşmadan işi düzgünce devreden ve biten oturumu kilitleyerek sonradan yazılanların işleri karıştırmasını önleyen bir Claude Code eklentisi (plugin).**

[English](README.md)

---

## Neden yazıldı

Claude Code oturumu her mesajla pahalılaşır: her yeni mesajda bütün sohbet (*bağlam*) yeniden gönderilir. Birkaç yüz bin tokendan sonra tek bir mesaj, kısa bir oturumun tamamı kadar tutabilir. Bunu iki şey daha da kötüleştirir:

1. **Claude kendi bağlam büyüklüğünü göremez.** Uygulama bu sayıyı size gösterir ama modele iletmez. Model sizi uyaramaz, kendiliğinden duramaz. `CLAUDE.md` içine yazılan "300k'da devret" gibi kurallar en iyi ihtimalle tahmindir.
2. **Uzun ara pahalıdır.** Önbellek süresi dolar (abonelikte bir saat). Uzun aradan sonraki ilk mesaj bütün bağlamı tam fiyatla yeniden işler.

Bir de sessiz bir sorun var: devir notunu yazıp yeni oturuma geçtikten sonra eski oturuma yeniden yazarsanız, aynı işin birbirinden ayrılan iki sürümü oluşur.

Session Guard bu üçünü tahminle değil ölçümle çözer.

## Ne yapar

| Durum | Normal mod | Uzun mod |
|---|---|---|
| 250k token | Bildirim | Bildirim |
| 300k | **Devir**: STATUS.md sadeleşir, bitmiş işler arşive gider, devir notu yazılır. Sonrasında yeni görev yok, yalnız sorular | Yalnız bildirim |
| 350k | Hedef durma: yeni oturum açın | — |
| 400k | **Kesin durma**, oturum kilitlenir | — |
| 700k | — | Bildirim; işler devredilebilir hale getirilir (Claude çalışmaya devam edebilir) |
| 800k | — | Yeni iş yok; yalnız basit sorular; son devir notu |
| 900k | — | **Kesin durma**, oturum kilitlenir |
| 1 saatten uzun ara, bağlam > 100k | Mesajınız **çalıştırılmaz**. Claude devir notunu yazar, mesajınızı kelimesi kelimesine nota ekler, oturum kilitlenir | Mola kuralı kapalı |
| 1 saatten uzun ara, bağlam ≤ 100k | Tek satır hatırlatma, sonra mesajınız çalışır | Mola kuralı kapalı |
| `finish session` | Devir + kullanım ölçümü, sonra oturum kilitlenir | aynı |

- **Mola kuralı yalnız *sizin* yokluğunuzu sayar.** Claude arka planda bir iş başlattıysa (render, uzun betik, alt-ajan) ve onu bekliyorsa, geçen süre mola sayılmaz.
- **Kilitli oturum hiçbir mesajı Claude'a iletmez.** Mesaj gönderilmeden engellenir; kota harcanmaz, hiçbir şey değişmez. Ekranda "Session closed, open a new session" görürsünüz. Kilitler türüne göre ayrı tutulur: bağlam sınırı, finish session, mola, kota (sıfırlanınca kalkar) ve yeniden başlatma (kurulumdan önce başlamış oturum).
- **Yeniden açılan sohbet kilitli kalır.** Masaüstü uygulaması eski bir sohbeti yeni bir oturum kimliğiyle açabilir. Session Guard sohbeti mesaj kimliklerinden tanır; kilit (ve uzun mod) korunur.
- **Oturum başı:** Claude kuralları ve `docs/STATUS.md` dosyanızı kendiliğinden alır. Artık "STATUS.md'yi oku" yazmanız gerekmez; arşiv (`docs/STATUS_ARCHIVE.md`) yalnız gerektiğinde aranır.
- **Token tasarrufu sağlayan yazım biçimi:** sade dil, kısa cümle; her sorun numaralı, altında yalnız gereken etiketler: SORUN / ÖNERİ / VARSAYIM / UYARI / DOĞRULANMADI (sizin dilinizde). Ayrıntı isterseniz sorarsınız.
- **Bilgi bilgidir.** Claude araştırmadığı ya da emin olmadığı şeyi kesin bilgi gibi sunmaz (DOĞRULANMADI diye işaretler); önerilerini ve varsayımlarını kendi önerisi / varsayımı olarak belirtir.
- **Proje dosyaları.** Her yeni projenin ilk oturumunda `CLAUDE.md`, `docs/STATUS.md` ve `docs/STATUS_ARCHIVE.md` oluşturulur. Var olan dosyalar (büyük/küçük harf farkı olsa da, ya da `.claude/CLAUDE.md`) asla yeniden oluşturulmaz, üzerine yazılmaz. Ana klasörünüzde, sürücü kökünde ve sistem klasörlerinde hiçbir şey oluşturulmaz; git worktree ana kopyanın dosyalarını kullanır.
- **Tek kural seti.** Session Guard'ın genel kuralları genel `~/.claude/CLAUDE.md` dosyanızda işaretli bir blokta durur. Bunlarla çelişen kural, skill, betik ve hook'lar tek seferlik kurulumda sizin onayınızla silinir (aşağıda).
- **Her dil.** Komutlar İngilizcedir; Claude sizinle sizin dilinizde konuşur. Uygulamanın doğrudan gösterdiği bildirimler (kilitler, engellenen mesajlar, eşikler) kurulum sırasında bir kez Claude'la konuştuğunuz dile çevrilir; o zamana kadar, ve kurulumdan önceki yeniden başlatma bildirimi, İngilizcedir.

## Kota koruması (Session Follow, yalnız bilgisayarda)

Session Guard **5 saatlik** ve **haftalık (tüm modeller)** kotanızı izler; böylece oturum, kota bir adımın ortasında bitmeden önce düzgünce durur.

- **Terminal:** Session Guard'ın durum çubuğundan okunur, token harcamaz (başka bir durum çubuğunuz yoksa).
- **Masaüstü uygulaması:** orada durum çubuğu çalışmaz; son okuma 20 dakikadan eskiyse Claude uygulamanın kullanım aracını okur (5 saatlik %85 ya da haftalık %95'ten sonra her mesajda; uzun işte en çok 5 dakikada bir). Okuma başına yaklaşık 400 token (tahmin).
- **5 saatlikte %90 ya da haftalıkta %98'de**, sıfırlanmaya en az 45 dakika varsa: Claude yeni iş başlatmaz, mevcut işi 15 dakika içinde devredilebilir hâle getirir, STATUS.md'ye tek satır yazar ("şurada durdu ..."). Sonra oturum sıfırlanmaya kadar kilitlenir: "Quota 91% (5-hour), resets at 14:10. Continue after that." Bu arada açılan oturumlar hemen kilitlenir. Sıfırlanmaya 45 dakikadan az varsa iş sürer.
- **Sıfırlanınca** kilit kendiliğinden kalkar. Normal modda 1 saatten uzun ara verdiyseniz mola kuralı her zamanki gibi uygulanır.
- Bir okuma bilgisayardaki bütün oturumlara yeter (kota hesaba aittir).
- **Bulutta yok:** bulut oturumunda hiçbir hook kotayı okuyamaz (belgelere bakıldı, denendi). Bulut oturumlarında diğer bütün kurallar geçerlidir.

## Komutlar

Mesajın **sonuna**, ya da **başına** yazıp ardından virgül veya nokta koyun. Başka yerde (cümle içinde, alıntıda, yapıştırılan metinde) bilerek yok sayılır.

| Komut | Etkisi |
|---|---|
| `start long mode` | Yalnız bu oturum için uzun mod. Kapatılamaz; yeni oturum normal modda başlar. Uzun, karmaşık ya da gözetimsiz işler için (örneğin uzaktan kontrolle takip ederken). |
| `finish session` | Konuyu kapatır: devir notu + kullanım ölçümü, sonra oturum kilitlenir. Bir konu bitince kullanın; böylece bir oturumda birden fazla konu karışmaz. |

Örnekler: `Start long mode, kalan klipleri üret` · `Bugünlük bu kadar, finish session`

## Nasıl çalışır

Session Guard bir Claude Code **plugin**'idir (eklenti): hook'lar, bir betik ve skill'lerden oluşan bir paket.

- **Hook'lar** betiği üç anda kendiliğinden çalıştırır: oturum başında, gönderdiğiniz her mesajda ve Claude'un kullandığı her araçtan sonra.
- **Betik** (`scripts/session_guard.py`, Python, yalnız standart kütüphane) Claude Code'un zaten diskinizde tuttuğu oturum kaydını okur (`~/.claude/projects/...`). Bağlam büyüklüğü Claude'un son yanıtının token kullanımından alınır; ara, o yanıttan bu yana geçen süredir. Sonra:
  - eşikte (ya da molada, uzun modda, finish session'da) Claude'un bağlamına ölçülen sayılarla bir talimat ekler; normal bir mesaja hiçbir şey eklemez, yani token harcamaz,
  - bir eşik ilk kez geçildiğinde size kısa bir mesaj gösterir,
  - kilitli oturumda mesajları engeller,
  - iş sırasında kesin sınıra gelinirse Claude'un turunu durdurur.
- **Skill'ler** adımları tutar (kurulum ve proje incelemesi, devir, mola devri, oturum sonlandırma). Claude onları yalnız gerektiğinde yükler.
- **Durum**, Session Guard'ın oturum başına tuttuğu küçük kendi kaydıdır (`~/.claude/session_guard/`): uzun mod açık mı, oturum kilitli mi, hangi eşik zaten bildirildi. Sizin işiniz değildir: içinde proje dosyası, sohbet ya da Claude verisi yoktur. 30 gün kullanılmayan kayıtlar, hook hızlı kalsın diye silinir; kilitli oturumun kaydı hiç silinmez, böylece eski bir oturum aylar sonra yeniden açılsa da kilitli kalır. Kurulum onayı `~/.claude/session_guard/config/setup.json`'da durur.

Hiçbir veri bir yere gönderilmez. Betik yalnız yerel dosyaları okur ve kendi durum dosyalarını yazar.

## Kurulum

Gerekenler: Claude Code, Python 3 (Windows'ta `python`, macOS/Linux'ta `python3`). Windows'ta Claude Code hook'lar için Git Bash kullanmalı (Git for Windows kuruluysa varsayılan budur).

Şu tek satırı Claude Code'a yapıştırın (masaüstü uygulaması ya da terminal):

```
Install the Session Guard plugin: run "claude plugin marketplace add ocpipun/session-guard" and "claude plugin install session-guard@session-guard", then tell me to fully quit and reopen Claude.
```

Claude marketplace'i ekler ve plugin'i kurar. Sonra Claude'u tamamen kapatıp (masaüstü uygulamasında sistem tepsisinden de) yeniden açın, **bir kez**. O zamana kadar açık olan oturumlar Session Guard'sız çalışır; mesajları "yeniden başlatın" notuyla engellenir.

Kullanıcı kapsamında kurulduğu için o bilgisayardaki **her projede** çalışır. Güncellemeler buradan gelir.

## Tek seferlik kurulum (zorunlu)

Kurulum onaylanana kadar Session Guard **hiçbir şey yapmaz**. Kurulum kendiliğinden başlar: yeniden başlattıktan sonraki ilk mesajınızla Claude onu çalıştırır ve tek soru sorar (`/session-guard:setup` da yazabilirsiniz).

1. Genel `~/.claude` klasörünüzü (CLAUDE.md, hafıza dosyaları, skill'ler, komutlar, ajanlar, betikler, çıktı stilleri, kurallar, hook'lar) ve Claude Code'un oturumu olan her projeyi tarar.
2. Session Guard'la çakışan her şeyi listeler: bağlam sınırı, devir, mola, oturum bitirme, STATUS dosyaları ya da yazım biçimi üzerine farklı bir şey söyleyen ya da Session Guard'ı tekrarlayan kurallar; aynı işi yapan skill, betik ya da hook'lar. Proje bilgileri ve projenin kendi STATUS düzeni çakışma sayılmaz.
3. Proje dosyalarının işini başka adla yapan dosyaları da listeler (AGENTS.md, GEMINI.md, .cursorrules, .windsurfrules, .clinerules, copilot-instructions.md, HANDOFF*, NOTES*, TODO*, PROGRESS*, CONTEXT.md, MEMORY.md, status dosyaları): çakışan satırlar silinir, işe yarar içerik STATUS.md / STATUS_ARCHIVE.md'ye taşınır, eksik proje dosyaları oluşturulur.
4. `~/.claude/settings.json`'a yazacağı durum çubuğunu açıklar (terminalde kota okuması, token harcamaz; başka bir durum çubuğunuz varsa ona dokunulmaz, terminalde kota okunmaz; kod burada herkese açık, MIT).
5. Listeyi tek cevapla onaylarsınız (bir maddenin çakışma olmadığını söyleyebilirsiniz). Onaylanan maddeler kalıcı olarak silinir (yedek kopya yok, yarım taşınmış bir şey kalmasın diye) ve genel kurallar bloğu `~/.claude/CLAUDE.md`'ye yazılır (yoksa oluşturulur; diğer kurallarınız kalır). Session Guard açık bütün oturumlarda hemen devreye girer; ikinci yeniden başlatma gerekmez.
6. **Onaylamazsanız Session Guard kapalı kalır.** `/session-guard:setup` ile istediğiniz zaman yeniden çalıştırın.

Daha sonra açılan ve kendi CLAUDE.md'si, benzer dosyaları, skill'leri ya da hook'ları olan bir proje, yalnız o proje için bir kez aynı incelemeden geçer; bu da kendiliğinden başlar. Reddetmek Session Guard'ı yalnız o projede kapatır. İncelenecek bir şeyi olmayan yeni bir proje incelemeye gerek duymaz. Bir güncelleme kuralları değiştirirse kurulum yeniden çalışır.

## Bulut oturumları (claude.ai/code)

Bulut oturumları plugin kurmaz. Session Guard her depoya bir kez, o deponun bir bulut oturumundan, şu tek satırla kurulur:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Bu satır onayınızdır: Claude tek seferde `docs/STATUS.md`'ye bir devir notu yazar, Session Guard'ı deponun `.claude/` klasörüne kopyalar ve ikisini doğrudan deponun ana dalına commit eder. Sonra o depoda **yeni bir oturum açın**: orada proje incelemesi kendiliğinden başlar (çakışan kurallar, benzer dosyalar, proje dosyaları) ve siz onaylayana kadar Session Guard kapalı kalır. Kurulumu yapan oturum ve önceden açık oturumlar korumasız kalır. **Depo başına bir kez yeter:** sonraki her bulut oturumu Session Guard'ı baştan çalıştırır. Bulutta **kota koruması yoktur** (yukarıya bakın). Bulutta genel CLAUDE.md olmadığı için depo kopyası genel kuralları kendisi getirir. Plugin'in kurulu olduğu bir bilgisayarda depo kopyası sessiz kalır; koruma iki kez çalışmaz.

## Kaldırma

Claude'dan Session Guard'ı kaldırmasını isteyin. Önce kendi durum çubuğunu kaldırır (`session_guard.py --uninstall-statusline`), sonra şunu çalıştırır:

```bash
claude plugin uninstall session-guard@session-guard
```

Ardından `~/.claude/CLAUDE.md`'deki `<!-- session-guard:begin` ile `<!-- session-guard:end -->` arasındaki bloğu silin. Bulut için kurulmuş bir depoda Claude'dan `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` klasörlerini ve `.claude/settings.json`'daki Session Guard hook'larını kaldırmasını isteyin.

## Sınırlar ve açık notlar

- Bağlam sayısı *son yanıtın* büyüklüğüdür; bir adım geriden gelebilir. Claude çalışırken her araçtan sonra eşikler yeniden kontrol edilir.
- "Bitti" haberi hiç gelmeyen bir arka plan işi ya da alt-ajan en fazla 24 saat "sürüyor" sayılır. Sonra, ve Claude tek bir mesaj üzerinde 24 saatten uzun çalışırsa, Session Guard size bunun bir hata olabileceğini söyler, takıldıysa `finish session` önerir. Her mesajla 24 saatlik bir zamanlayıcı başlar (arka plan hook'u; yeni mesaj bir dakika içinde eskisinin yerini alır; zamanlayıcı yerel bir kontroldür, Claude'a istek göndermez, kota harcamaz). Yeni mesaj gelmeden 24 saat geçer ve oturum hâlâ meşgulse Claude'u uyandırır, Claude size söyler. Bitmiş ve sizi bekleyen bir yanıt sayılmaz. Bulutta, bulut boşta kalan oturumu daha önce kapatırsa zamanlayıcı da kapanır.
- Bulut kullanımı resmi belgelere dayanır (tek depolu bulut oturumunda deponun hook ve skill'leri çalışır; Python 3 kurulu gelir). v1.2 bulut kurulumu (tek tur, inceleme sonraki oturumda) henüz gerçek bir bulut oturumunda test edilmedi.
- "Bütün projeler", Claude Code'da oturumu olan projelerdir (`~/.claude/projects`'ten bulunur). Claude Code'da hiç açılmamış klasörler taranmaz; ilk açıldıklarında incelenir.
- Bir maddenin çakışıp çakışmadığına Claude karar verir, siz onaylarsınız; betik yalnız adayları bulur.
- Eşikler betikte sabittir (`NORMAL_STEPS`, `LONG_STEPS`). Çalışma değerleridir; kanıtlanmış kota tasarrufu değildir.

## Testler

```bash
python -m unittest discover -s tests
```

## Lisans

MIT
