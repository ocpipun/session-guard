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
- **Token tasarrufu sağlayan yazım biçimi:** sade dil, kısa cümle; her sorun numaralı, altında yalnız gereken etiketler: SORUN / ÖNERİ / VARSAYIM / UYARI / DOĞRULANMADI (sizin dilinizde). Claude tahmini kesin bilgi gibi sunmaz. Ayrıntı isterseniz sorarsınız.
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
- **Skill'ler** adımları tutar (devir, mola devri, oturum sonlandırma, bulut kurulumu). Claude onları yalnız gerektiğinde yükler.
- **Durum** (uzun mod, kilit, bildirilen eşikler) oturum başına küçük bir JSON dosyasıdır: `~/.claude/session_guard/`. 30 günden eski dosyalar silinir.

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

## Bulut oturumları (claude.ai/code)

Bulut oturumları plugin kurmaz. Ama **deponun içindeki** hook'ları ve skill'leri çalıştırır. Bu yüzden bulutta kullandığınız her depo için kendi bilgisayarınızda bir kez çalıştırın:

```
/session-guard:cloud-setup
```

Betiği, hook'ları ve devir skill'ini deponun `.claude/` klasörüne kopyalar. Commit edip gönderin; o deponun bulut oturumları da korunur. Plugin'in kurulu olduğu bilgisayarda bu proje kopyası sessiz kalır; Session Guard iki kez çalışmaz.

## Kaldırma

```bash
claude plugin uninstall session-guard@session-guard
```

Ya da Claude'a kaldırmasını söyleyin. Bulut için kurulmuş bir depoda Claude'dan `.claude/session-guard/`, `.claude/skills/session-guard/` klasörlerini ve `.claude/settings.json` içindeki Session Guard hook'larını silmesini isteyin.

## Sınırlar ve açık notlar

- Bağlam sayısı *son yanıtın* büyüklüğüdür; bir adım geriden gelebilir. Claude çalışırken her araçtan sonra eşikler yeniden kontrol edilir.
- "Bitti" haberi hiç gelmeyen bir arka plan işi en fazla 24 saat "sürüyor" sayılır.
- Bulut kullanımı resmi belgelere dayanır (tek depolu bulut oturumunda deponun hook ve skill'leri çalışır; Python 3 kurulu gelir). Henüz gerçek bir bulut oturumunda test edilmedi.
- Eşikler betikte sabittir (`NORMAL_STEPS`, `LONG_STEPS`). Çalışma değerleridir; kanıtlanmış kota tasarrufu değildir.

## Testler

```bash
python -m unittest discover -s tests
```

## Lisans

MIT
