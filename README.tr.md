<!-- SESSION-GUARD: 2.0 | component: doc-readme-tr | version: 2.0.0 -->
# Session Guard

**Sürüm 2.0** · Açık kaynak, **MIT lisansı** · [English](README.md) · Tasarım: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Session Guard, Claude Code için bir eklentidir. Claude'un göremediği şeyleri (bağlam büyüklüğü, mola, kota) ölçer,
Claude'a ne zaman devredeceğini ve duracağını söyler, biten oturumları kilitler. 2.0'da yeni: her proje, oturum ve
görev bir kimlik alır; hangi görevin kime ait olduğu korunur, böylece paralel ya da sonraki oturumlar birbirinin
işini ezmez.

## Ne yapar

### Bağlam eşikleri (1.4'ten; uzun mod değişti)
| Bağlam | Normal mod | Uzun mod |
|---|---|---|
| 250k | bildirim | bildirim |
| 300k | oturumun sahip olduğu her görevin devri; yeni görev yok | bildirim |
| 350k | hedef durma | — |
| 400k | kesin durma, oturum kilitlenir | — |
| 700k | — | bildirim; görev notları güncel tutulur, henüz devir yok (1.4'ten farklı) |
| 800k | — | oturumun sahip olduğu her görevin devri; sonra iş yok, basit sorular |
| 900k | — | kesin durma, oturum kilitlenir |

300k devrinden (normal mod) ya da 800k devrinden (uzun mod) sonra "start long mode" ve "finish session" reddedilir: devir hiçbir zaman iki kez yazılmaz.

### Molalar (1.4 ile aynı)
Normal modda 1 saatten uzun ara: bağlam 100k üstündeyse mesaj çalıştırılmaz; Session Guard mesajı kelimesi
kelimesine her görevin devir notuna kendisi yazar ve oturumu kilitler. 100k ya da altındaysa tek satır hatırlatma,
sonra mesaj çalışır. Claude'un kendi arka plan işini beklemek mola sayılmaz. Uzun modda mola kuralı yok.

### Kota (bilgisayar, 1.4 ile aynı)
5 saatlikte %90 / haftalıkta %98, sıfırlanmaya 45 dakikadan fazla varsa: mevcut adım güvenli noktaya getirilir,
oturum sıfırlanmaya kadar kilitlenir.

### Projeler, oturumlar, görevler (yeni)
- **Kimlikler:** proje `PRJ-XXXXXXXX`, oturum `S-YYYYAAGG-xxxxxx`, görev `TASK-001`, `TASK-001-1`, `TASK-001-2-1`.
- **Sahiplik:** görevi yalnız sahibi değiştirir. Boştaki görev (sahibi yok, sahibi bitti ya da devir bekliyor)
  alınabilir; aktif bir oturumun görevi yalnız sizin onayınızla. Geçmiş silinmez.
- **Sınırlar:** en fazla 3 normal ana görev; 3'ü doluysa gerekçeli ve onaylı 1 acil görev; alt görevlerde sınır
  yok; kurulum ve migration görevleri muaf. İlgisiz işler backlog'a gider.
- **Gerektiğinde yeni kimlik:** kaldığı yerden devam aynı kimlik; hatalı biten işi yeniden yapmak ya da biten işe
  özellik eklemek eskisine bağlı yeni görev. Alt görev bitince üst görev kendiliğinden bitmez.
- **Görev başına devir notu**, doğrulanmış git push ile yayımlanır; devralınacak birden fazla iş varsa yeni
  oturum size sorar. Devir alınınca eski oturum kilitlenir.
- **Güvenli paralel yazma:** proje başına kilit, sürüm denetimi, üretilen STATUS; görev kaydının elle
  düzenlenmesi reddedilir, elle bozulan görev bölümü yeniden oluşturulur.

### Proje dosyaları
| Dosya | Görevi |
|---|---|
| `CLAUDE.md` | kalıcı kurallar ve kararlar |
| `docs/STATUS.md` | güncel durum; görev bölümü üretilir, en fazla 50 satır |
| `docs/STATUS_ARCHIVE.md` | geçmiş; yalnız eklenir, aranır, bütünü okunmaz |
| `docs/TASK_BACKLOG.md` | bekleyen fikirler |
| `docs/COMPLETED_TASKS.md` | biten her görev için tek satır |
| `.claude/session-guard2/store.json` | görev kaydı (projeyle birlikte commitlenir) |

## Nerelerde çalışır
| Yer | Çalışır mı | Not |
|---|---|---|
| Masaüstü uygulaması, terminal | evet | bütün özellikler; Windows'ta denendi |
| Remote Control | evet | oturum bilgisayarda çalışır (belgede yazıyor) |
| Bulut oturumları | depo başına | depo kopyası; kota koruması yok |
| macOS / Linux | beklenir | denenmedi |

Bir proje ya bilgisayarda ya bulutta yürütülür, ikisinde birden asla (1.4 kuralı).

## Kurulum ve migration
Yalnız `session-guard-executive` adlı proje klasöründe. 2.0, 1.x ile aynı eklenti kimliğini kullanır; güncelleme
1.x'in yerini alır, 1.x kurulu kaldıkça 2.0 pasif bekler. Sonra her proje bir geçişten geçer (inceleme → plan →
geri dönüş noktası → uygulama → doğrulama → push); her adım sizin onayınızla: [INSTALL.md](INSTALL.md). Bulut:
[CLOUD.md](CLOUD.md).

## Komutlar
Mesajın başına (ardından virgül ya da nokta) ya da sonuna: `start long mode`, `finish session`,
`continue installation`. `sg task ...`, `sg handoff ...`, `sg migrate ...` komutlarını Claude kendisi kullanır.

## Sınırlar (docs/ARCHITECTURE.md §7–8)
- Session Guard'ın okuduğu oturum kaydı biçimi Anthropic tarafından belgelenmemiştir, değişebilir.
- Eski bulut oturumları bilgisayardan kilitlenemez; onları claude.ai/code'da arşivleyin.
- Git kullanmayan projelerde git geri dönüşü yoktur; yalnız dosya kopyaları tutulur.
- Kilitler hook'larla çalışır: hook'suz açılan oturum kilitlenmez.

## Testler
```bash
python -m unittest discover -s tests
```

## Lisans
MIT ([LICENSE](LICENSE)). Olduğu gibi, garantisiz sunulur.
