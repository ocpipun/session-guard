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
| 1 saatten uzun ara, bağlam > 125k | Mesajınız **çalıştırılmaz**. Claude devir notunu yazar, mesajınızı kelimesi kelimesine nota ekler, oturum kilitlenir | Mola kuralı kapalı |
| 1 saatten uzun ara, bağlam ≤ 125k | Tek satır hatırlatma, sonra mesajınız çalışır | Mola kuralı kapalı |
| `finish session` | Devir + kullanım ölçümü, sonra oturum kilitlenir | aynı |

- **Mola kuralı yalnız *sizin* yokluğunuzu sayar.** Claude arka planda bir iş başlattıysa (render, uzun betik, alt-ajan) ve onu bekliyorsa, geçen süre mola sayılmaz.
- **Kilitli oturum hiçbir mesajı Claude'a iletmez.** Mesaj gönderilmeden engellenir; kota harcanmaz, hiçbir şey değişmez. Ekranda "Session closed, open a new session" görürsünüz.
- **Yeniden açılan sohbet kilitli kalır.** Masaüstü uygulaması eski bir sohbeti yeni bir oturum kimliğiyle açabilir. Session Guard sohbeti mesaj kimliklerinden tanır; kilit (ve uzun mod) korunur.
- **Oturum başı:** Claude kuralları ve `docs/STATUS.md` dosyanızı kendiliğinden alır. Artık "STATUS.md'yi oku" yazmanız gerekmez; arşiv (`docs/STATUS_ARCHIVE.md`) yalnız gerektiğinde aranır.
- **Token tasarrufu sağlayan yazım biçimi:** sade dil, kısa cümle; her sorun numaralı, altında yalnız gereken etiketler: SORUN / ÖNERİ / VARSAYIM / UYARI / DOĞRULANMADI (sizin dilinizde). Ayrıntı isterseniz sorarsınız.
- **Bilgi bilgidir.** Claude araştırmadığı ya da emin olmadığı şeyi kesin bilgi gibi sunmaz (DOĞRULANMADI diye işaretler); önerilerini ve varsayımlarını kendi önerisi / varsayımı olarak belirtir.
- **Proje dosyaları.** Her yeni projenin ilk oturumunda `CLAUDE.md`, `docs/STATUS.md` ve `docs/STATUS_ARCHIVE.md` oluşturulur. Var olan dosyalar (büyük/küçük harf farkı olsa da, ya da `.claude/CLAUDE.md`) asla yeniden oluşturulmaz, üzerine yazılmaz. Ana klasörünüzde, sürücü kökünde ve sistem klasörlerinde hiçbir şey oluşturulmaz; git worktree ana kopyanın dosyalarını kullanır.
- **Tek kural seti.** Session Guard'ın genel kuralları genel `~/.claude/CLAUDE.md` dosyanızda işaretli bir blokta durur. Bunlarla çelişen kural, skill, betik ve hook'lar tek seferlik kurulumda sizin onayınızla silinir (aşağıda).
- **Her dil.** Komutlar ve eklentinin kendi metinleri İngilizcedir; Claude sizinle sizin dilinizde konuşur.

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
  - Claude'un bağlamına bir durum satırı ve eşikte bir talimat ekler,
  - bir eşik ilk kez geçildiğinde size kısa bir mesaj gösterir,
  - kilitli oturumda mesajları engeller,
  - iş sırasında kesin sınıra gelinirse Claude'un turunu durdurur.
- **Skill'ler** adımları tutar (kurulum ve proje incelemesi, devir, mola devri, oturum sonlandırma). Claude onları yalnız gerektiğinde yükler.
- **Durum**, Session Guard'ın oturum başına tuttuğu küçük kendi kaydıdır (`~/.claude/session_guard/`): uzun mod açık mı, oturum kilitli mi, hangi eşik zaten bildirildi. Sizin işiniz değildir: içinde proje dosyası, sohbet ya da Claude verisi yoktur. 30 gün kullanılmayan kayıtlar, hook hızlı kalsın diye silinir; kilitli oturumun kaydı hiç silinmez, böylece eski bir oturum aylar sonra yeniden açılsa da kilitli kalır. Kurulum onayı `~/.claude/session_guard/config/setup.json`'da durur.

Hiçbir veri bir yere gönderilmez. Betik yalnız yerel dosyaları okur ve kendi durum dosyalarını yazar.

## Kurulum

Gerekenler: Claude Code, Python 3 (Windows'ta `python`, macOS/Linux'ta `python3`). Windows'ta Claude Code hook'lar için Git Bash kullanmalıdır (Git for Windows kuruluysa varsayılan budur).

Terminalde:

```bash
claude plugin marketplace add ocpipun/session-guard
claude plugin install session-guard@session-guard
```

Ya da Claude Code içinde: `/plugin marketplace add ocpipun/session-guard`, sonra `/plugin install session-guard@session-guard`.

Kullanıcı düzeyinde kurulunca o bilgisayardaki **bütün projelerde** çalışır. Kurulumdan sonra yeni oturum açın. Kimsenin bu depoyu kopyalaması gerekmez; güncellemeler buradan gelir.

## Tek seferlik kurulum (zorunlu)

Kurulum onaylanana kadar Session Guard **hiçbir şey yapmaz**. Yüklemeden sonraki ilk oturumda Claude bunu söyler ve kurulumu şimdi çalıştırıp çalıştırmayacağını sorar (ya da `/session-guard:setup` yazın):

1. Genel `~/.claude` klasörünüzü (CLAUDE.md, hafıza dosyaları, skill, komut, ajan, betik, output style, rules, hook) ve Claude Code'da oturumu olan bütün projeleri tarar.
2. Session Guard'la çakışan her şeyi listeler: bağlam sınırı, devir, mola, oturum bitirme, STATUS dosyaları ya da yazım biçimi üzerine farklı şey söyleyen veya Session Guard'ı tekrarlayan kurallar; aynı işi yapan skill, betik ve hook'lar. Proje bilgileri ve projenin kendi STATUS düzeni çakışma sayılmaz.
3. Listeyi onaylarsınız (bir maddenin çakışma olmadığını söyleyebilirsiniz). Onaylanan maddeler kalıcı olarak silinir (yedek kopya yok; yarım taşınmış bir şey kalmaz) ve genel kurallar bloğu `~/.claude/CLAUDE.md`'ye yazılır (yoksa oluşturulur; diğer kurallarınız kalır).
4. **Onaylamazsanız Session Guard kapalı kalır.** `/session-guard:setup` ile istediğiniz zaman yeniden çalıştırırsınız.

Sonradan açılan ve kendi CLAUDE.md'si, skill'i ya da hook'u olan bir proje aynı incelemeden bir kez, yalnız o proje için geçer; reddederseniz Session Guard yalnız o projede kapanır. İncelenecek bir şeyi olmayan yeni proje incelemesiz başlar. Bir güncelleme kuralları değiştirirse kurulum yeniden çalışır.

## Bulut oturumları (claude.ai/code)

Bulut oturumları plugin kurmaz. Session Guard her depoya bir kez, o deponun bir bulut oturumundan, şu tek satırla kurulur:

```
Install Session Guard: git clone --depth 1 https://github.com/ocpipun/session-guard /tmp/session-guard, then follow /tmp/session-guard/CLOUD.md
```

Claude Session Guard'ı indirir, deponun onunla çakışan kurallarını gösterip onayınızı ister (reddederseniz hiçbir şey kurulmaz), deponun `.claude/` klasörüne kurar ve onayınızla doğrudan deponun ana dalına (main) kaydeder. **Her depo için bir kez yeterlidir:** o deponun bütün yeni bulut oturumları ve sohbetleri Session Guard'la başlar (kurulumu yaptığınız oturum hariç, çünkü hook'lar oturum başında okunur). Bulutta genel CLAUDE.md yoktur; genel kuralları depodaki kopya kendisi getirir; CLAUDE.md'si olmayan depoya proje dosyaları oluşturulur. Plugin'in kurulu olduğu bilgisayarda depodaki kopya sessiz kalır; Session Guard iki kez çalışmaz.

## Kaldırma

```bash
claude plugin uninstall session-guard@session-guard
```

Ya da Claude'a kaldırmasını söyleyin. Sonra `~/.claude/CLAUDE.md` içindeki `<!-- session-guard:begin` ile `<!-- session-guard:end -->` arasındaki bloğu silin. Bulut için kurulmuş bir depoda Claude'dan `.claude/session-guard/`, `.claude/skills/session-guard/`, `.claude/skills/session-guard-setup/` klasörlerini ve `.claude/settings.json` içindeki Session Guard hook'larını silmesini isteyin.

## Sınırlar ve açık notlar

- Bağlam sayısı *son yanıtın* büyüklüğüdür; bir adım geriden gelebilir. Claude çalışırken her araçtan sonra eşikler yeniden kontrol edilir.
- "Bitti" haberi hiç gelmeyen bir arka plan işi ya da alt-ajan en fazla 24 saat "sürüyor" sayılır. Sonra, ve Claude tek bir mesaj üzerinde 24 saatten uzun çalışırsa, Session Guard size bunun bir hata olabileceğini söyler, takıldıysa `finish session` önerir. Her mesajla 24 saatlik bir zamanlayıcı başlar (arka plan hook'u; yeni mesaj bir dakika içinde eskisinin yerini alır; zamanlayıcı yerel bir kontroldür, Claude'a istek göndermez, kota harcamaz). Yeni mesaj gelmeden 24 saat geçer ve oturum hâlâ meşgulse Claude'u uyandırır, Claude size söyler. Bitmiş ve sizi bekleyen bir yanıt sayılmaz. Bulutta, bulut boşta kalan oturumu daha önce kapatırsa zamanlayıcı da kapanır.
- Bulut kullanımı resmi belgelere dayanır (tek depolu bulut oturumunda deponun hook ve skill'leri çalışır; Python 3 kurulu gelir). Henüz gerçek bir bulut oturumunda test edilmedi.
- "Bütün projeler", Claude Code'da oturumu olan projelerdir (`~/.claude/projects`'ten bulunur). Claude Code'da hiç açılmamış klasörler taranmaz; ilk açıldıklarında incelenir.
- Bir maddenin çakışıp çakışmadığına Claude karar verir, siz onaylarsınız; betik yalnız adayları bulur.
- Eşikler betikte sabittir (`NORMAL_STEPS`, `LONG_STEPS`). Çalışma değerleridir; kanıtlanmış kota tasarrufu değildir.

## Testler

```bash
python -m unittest discover -s tests
```

## Lisans

MIT
