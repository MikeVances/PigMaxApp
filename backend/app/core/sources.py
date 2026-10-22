"""
Справочник источников расчёта — одна запись на каждый `source_id`, который отдаёт
расчёт. Формат перенесён из MINMAXapp (backend/app/core/sources.py).

Зачем отдельный модуль, а не строка цитаты рядом с кодом: ответ называет источник
меткой (`cigr_2002`), и человеку или агенту без кода нечем объяснить, что за ней
стоит. Здесь у каждой метки — выверенный пересказ правила, как калькулятор его
применяет и где наша модель от источника отличается. Полный каталог собранных
материалов — docs/sources.md; сюда попадает только то, на что ссылается код.

Правило пополнения: новый `source_id` в ядре — сразу запись здесь, иначе красный
тест test_sources (каждый id, который отдаёт расчёт, есть в справочнике).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Optional

# Чего стоит источник:
#   standard     — стандарт или нормативный документ;
#   manufacturer — руководство селекционной компании или производителя оборудования;
#   research     — научная публикация;
#   extension    — методика университетской службы внедрения;
#   unverified   — первоисточник не установлен.
SourceKind = Literal["standard", "manufacturer", "research", "extension", "unverified"]


@dataclass(frozen=True)
class Source:
    id: str
    kind: SourceKind
    citation: str               # короткая ссылка — как в `sources` ответов расчёта
    title: str
    publisher: str
    year: Optional[int]
    locator: Optional[str]      # раздел, таблица, страница — где проверить
    applies_to: str             # к какой части расчёта относится
    statement: str              # что утверждает источник (наш выверенный пересказ)
    how_used: str               # как калькулятор это применяет
    limits: Optional[str] = None  # где модель отличается от источника или чего он не покрывает
    url: Optional[str] = None


_SOURCES: tuple[Source, ...] = (

    # ── Тепло, влага, CO₂ ─────────────────────────────────────────────────────
    Source(
        id="cigr_2002", kind="research",
        citation="Pedersen & Sällvik (eds.) 2002, CIGR 4th Report, Heat and moisture production at animal and house levels",
        title="Climatization of Animal Houses. Heat and moisture production at animal and house levels",
        publisher="CIGR Section II Working Group / DIAS", year=2002,
        locator="ур. 6–10 (с. 10–11), табл. 2.1; ур. 25 и табл. 2.3 (с. 15–18); ур. 30, 32 (с. 21, 25); гл. 5, ур. 36 (с. 37)",
        applies_to="полное, явное и скрытое тепло свиней всех групп; выделение CO₂",
        statement=(
            "Полное тепло при 20 °C = поддержание + доля сверхподдерживающей энергии корма: "
            "поросята 7.4·m^0.66, растущие 5.09·m^0.75 с кратностью кормления n (табл. 2.1), "
            "холостые и супоросные свиноматки, хряки, свинки 4.85·m^0.75 + 8·10⁻⁵·p³ + 76·Y2, "
            "подсосная свиноматка с поросятами 4.85·m^0.75 + 28·Y1. На каждый °C выше 20 полное "
            "тепло падает на 1.2 % (для малых поросят рекомендовано 2 %). Явное тепло на уровне "
            "здания: откорм и доращивание — ур. 32, прочие группы — ур. 30. CO₂ 0.185 м³/ч на "
            "1000 Вт полного тепла при 20 °C, среднесуточно."
        ),
        how_used=(
            "Методика по умолчанию (`cigr2002`). n — столбец табл. 2.1 по выбору, по умолчанию "
            "DK 900 г/сут; ниже 20 кг — ур. 6, n от 4.1 при 2 кг (Brown-Brandl 2004, табл. 3) "
            "линейно до значения столбца при 20 кг. Скрытое тепло = полное − явное, пар = скрытое / 2430 Дж/г."
        ),
        limits=(
            "Табл. 2.1 кончается на 120 кг — тяжелее n держится на значении 120 кг. CIGR завышает "
            "тепло лёгких и занижает тяжёлых свиней современной генетики (Brown-Brandl 2004). "
            "Y1 = 6 кг молока в сутки устарело (современные свиноматки 10–12) — задаётся параметром. "
            "Коэффициент 2 %/°C для поросят и подстановка его в ур. 32 — наше распространение "
            "рекомендации CIGR."
        ),
        url="https://cigr.org/sites/default/files/documets/CIGR_4TH_WORK_GR.pdf",
    ),
    Source(
        id="brown_brandl_2004", kind="research",
        citation="Brown-Brandl, Nienaber, Xin, Gates 2004, Trans. ASAE 47(1):259–270",
        title="A literature review of swine heat production",
        publisher="ASAE", year=2004,
        locator="ур. 11 и 13 (с. 263), табл. 3 (с. 261)",
        applies_to="полное тепло растущих свиней современной генетики (данные 1988–2002)",
        statement=(
            "Регрессия по 7 независимым исследованиям 1988–2002 гг., свиньи 10–100 кг: "
            "log10(HP, Вт/кг) = 1.189 − 0.005·t − 0.345·log10(m). Теплопродукция современных "
            "линий на 12–35 % выше, чем до 1988 г. Скрытое тепло не анализировалось — данных мало."
        ),
        how_used=(
            "Методика `brown_brandl_2004`: полное тепло по ур. 13 при температуре помещения. "
            "Доля явного тепла — по CIGR 2002, ур. 32 (в обзоре разделения нет). CO₂ — правилом "
            "CIGR от полного тепла при 20 °C."
        ),
        # url не указан: в тексте статьи нет DOI, а адрес не проверен
        limits="Только растущие свиньи; данные 10–100 кг — вне диапазона предупреждение. Свиноматок и хряков нет.",
    ),
    Source(
        id="rd_apk_2012", kind="standard",
        citation="РД-АПК 1.10.02.04-12, разд. 13, табл. 15–16",
        title="Методические рекомендации по технологическому проектированию свиноводческих ферм и комплексов",
        publisher="Минсельхоз России", year=2012,
        locator="разд. 13: табл. 15 (тепло, пар, CO₂ по массе), табл. 16 (поправки на температуру)",
        applies_to="полное и свободное (явное) тепло, пар и CO₂ свиней всех групп — российская норма",
        statement=(
            "Табл. 15 даёт на одну голову при 10 °C и RH 70–75 %: общую и свободную теплоту, "
            "водяные пары и CO₂ по группам и массе. Свободная теплота при 10 °C — 72 % общей. "
            "Табл. 16 — множители к общей теплоте, свободной теплоте и пару для температур от −5 "
            "до 30 °C. CO₂ — 0.036 л на 1 кДж/ч общей теплоты."
        ),
        how_used=(
            "Методика `rd_apk_2012`: линейная интерполяция табл. 15 по массе и табл. 16 по "
            "температуре. CO₂ — по правилу прим. 4 (0.036 л на кДж/ч общей теплоты при 10 °C, "
            "без пересчёта на температуру: табл. 16 множителя для CO₂ не даёт); столбец CO₂ "
            "таблицы этим правилом и посчитан. Опечатка хряка 200 кг исправлена: свободная "
            "теплота 1222.5 кДж/ч по значению в ккал (292) из той же ячейки — тогда пар сходится "
            "(465 кДж/ч / 2.45 = 190 г/ч против 194 в таблице). Опечатка CO₂ отъёмыша 20 кг "
            "(8.6 вместо ≈18.5) обходится правилом."
        ),
        limits=(
            "Пар — от животного, без испарения с пола и навоза. Ночные значения на 20 % ниже — "
            "калькулятор считает дневные. Масса вне строк таблицы — берётся крайняя строка с предупреждением."
        ),
        url="https://meganorm.ru/Data2/1/4293787/4293787783.htm",
    ),

    # ── Уставки и плотность ───────────────────────────────────────────────────
    Source(
        id="pic_2019_w2f", kind="manufacturer",
        citation="PIC Wean to Finish Guidelines 2019, табл. 4.1, 5.1, с. 4-10",
        title="Wean to Finish Guidelines (рус. «Руководство по работе на доращивании и откорме»)",
        publisher="PIC", year=2019,
        locator="табл. 4.1 (уставки и минимальная вентиляция), табл. 5.1 (плотность), с. 4-10 (газы)",
        applies_to="уставки температуры, минимальная вентиляция на голову, плотность посадки, пределы газов",
        statement=(
            "Табл. 4.1: по дню после отъёма и среднему весу (5.4–147 кг) — нужная, зимняя и летняя "
            "уставки и минимальная вентиляция м³/гол/ч для сухих щелевых полов. Газы: NH₃ < 20, "
            "CO₂ < 3000, CO < 30, H₂S < 5 ppm."
        ),
        how_used="Интерполяция табл. 4.1 по весу; строки без брудеров и ковриков.",
        limits="В русском переводе пределы газов ошибочно подписаны «мг/м³» — калькулятор берёт ppm из оригинала.",
        url="https://www.pic.com/wp-content/uploads/sites/3/2021/12/PIC-Wean-To-Finish-Manual.pdf",
    ),
    Source(
        id="pic_2021_sow", kind="manufacturer",
        citation="PIC Gilt & Sow Management Guidelines 2021, табл. 3.1 и 5.2",
        title="Gilt & Sow Management Guidelines",
        publisher="PIC", year=2021,
        locator="табл. 3.1 (осеменение и супоросность), табл. 5.2 (опорос)",
        applies_to="минимальная вентиляция свиноматок и хряков на голову",
        statement=(
            "Осеменение и супоросность: 18–20 °C, минимальная вентиляция зимой 12 cfm/гол, "
            "максимальная 150 cfm/гол, RH < 65 %. Опорос: 21–23 °C при опоросе со снижением до "
            "19 °C к 7–10 дню, вентиляция зимой 20 cfm/гол, летом 650 cfm/гол, RH < 65 %."
        ),
        how_used=(
            "Норма `pic` для свиноматок: 12 cfm = 20.4 м³/ч (холостые, супоросные), 20 cfm = "
            "34.0 м³/ч (подсосная со станком). Хрякам — норма табл. 3.1: своей у PIC нет, хряки "
            "стоят в секторе осеменения."
        ),
        limits="Норма одна на весь срок, без зависимости от массы и дня супоросности.",
        url="https://www.pic.com/wp-content/uploads/sites/3/2021/12/PIC-Gilt-Sow-Management-Guidelines.pdf",
    ),
    Source(
        id="hypor_2023", kind="manufacturer",
        citation="Hypor Wean to Finish Management Manual 2023, табл. 7; Hypor Feeding Guide W2F (Magnus), табл. 2 и 4",
        title="Wean to Finish Management Manual; Feeding Guide Wean to Finish (Magnus)",
        publisher="Hypor (Hendrix Genetics)", year=2023,
        locator="W2F Manual табл. 7 (с. 12); Feeding Guide табл. 2 (доращивание), табл. 4 (откорм)",
        applies_to="уставки температуры по весу и кривая роста — профиль `hypor_2023`",
        statement=(
            "Температура на уровне свиней по весу: 5 кг — 31 °C, 6 — 27, 8 — 26, 10 — 24, 12 — 23, "
            "17 — 22, 25 — 21, 40 — 19, 60 — 18, 105 — 16 °C. RH 55–75 %. Рост: доращивание 6.0 → 25.9 кг "
            "за 42 дня; откорм Magnus понедельно 25.0 → 138.2 кг за 17 недель."
        ),
        how_used=(
            "Полосы табл. 7 сплошные, поэтому это точки одной кривой. Кривые роста сшиты по весу: "
            "откорм сдвинут на −1.1 сут, чтобы 25.9 кг совпали (таблицы расходятся на 0.9 кг). "
            "Предел RH — верхний край, 75 %."
        ),
        limits="Норм вентиляции на голову у Hypor нет — норму берите `pic`, `rd_apk_2012` или физику.",
    ),
    Source(
        id="klimaatplatform_2021", kind="extension",
        citation="Klimaatplatform Varkenshouderij 2021, Richtlijnen klimaatinstellingen (Topigs Norsvin TN70, табл. 12)",
        title="Richtlijnen klimaatinstellingen",
        publisher="Klimaatplatform Varkenshouderij (NL); журнал Varkens, 2021", year=2021,
        locator="одна страница; TN70 Feed Manual 2023, табл. 12 и 24",
        applies_to="начало вентиляции, мин. и макс. вентиляция на голову по дням — профиль `klimaatplatform_2021`",
        statement=(
            "Доращивание (посадка ≈7.5 кг, день 21, день 42): мин. 3/6/8, макс. 12/18/25 м³/ч на голову, "
            "начало вентиляции 26 (сплошной подогреваемый пол) или 28 (полностью щелевой), 24, 20 °C. "
            "Откорм (день 1 ≈23 кг, 5, 50, 100): мин. 6/8/14/17, макс. 40/40/80/80, начало 25/22/20/19 °C. "
            "P-band 5 °C; нейтральная зона отопления 1–2 °C; минимум — по CO₂ ≤ 3000 ppm в покое."
        ),
        how_used=(
            "Дни переведены в вес: откорм — по TN Select (кастраты, табл. 1) со сдвигом, чтобы день 1 = 23 кг; "
            "доращивание — 7.5 кг на посадке, 23 кг на 42-й день, 21-й день по форме кривой Hypor (гипотеза). "
            "Уставка — НАЧАЛО ВЕНТИЛЯЦИИ: расчётная температура помещения на 1.5 °C ниже. "
            "Посадка доращивания — вариант полностью щелевого пола (28 °C)."
        ),
        limits="Свиноматки в таблице есть — используются на этапе свиноматок.",
        url="https://www.varkens.nl/public/file/183845.pdf",
    ),
    Source(
        id="danbred_manual", kind="manufacturer",
        citation="DanBred Manual (web), Pig finisher manual — production facilities, двухклиматный станок",
        title="DanBred Manual — Pig finisher manual: production facilities",
        publisher="DanBred", year=None,
        locator="таблица «Weight / Ambient temperature beneath the cover / Set temp. room / Set humidity room»",
        applies_to="уставки помещения и RH доращивания 5.5–30 кг — профиль `danbred`",
        statement=(
            "По весу 5.5 / 6 / 7 / 8.5 / 11 / 15 / 18 / 25 / 30 кг: под крышкой 31–32 … 21–22 °C; пол под "
            "крышкой 32 °C до 11 кг; помещение 25–26 / 24 / 24 / 23 / 22 / 21 / 20 / 19 / 18 °C; "
            "RH помещения 60 / 61 / 61 / 62 / 63 / 64 / 70 / 71 / 72 %."
        ),
        how_used="Уставка помещения — температура помещения; диапазон 25–26 °C взят серединой. RH — как предел для баланса влаги.",
        limits="Только доращивание до 30 кг; откорм и нормы вентиляции на голову в открытой части руководства не даны.",
        url="https://danbred-manual.com/en/pig-finisher-manual/production-facilities/",
    ),

    Source(
        id="stinn_xin_2014", kind="research",
        citation="Stinn J.P., Xin H. 2014, Trans. ASABE 57(5):1517–1528",
        title="Heat and moisture production rates of a modern U.S. swine breeding, gestation, and farrowing facility",
        publisher="ASABE", year=2014,
        locator="табл. 3 (супоросные), табл. 4 (подсосные с гнездом), ур. 1–2",
        applies_to="тепло, влага и CO₂ свиноматок — методика `stinn_xin_2014`",
        statement=(
            "Непрямая калориметрия (O₂/CO₂ за вычетом горелок и навоза), 4300 свиноматок, 16 месяцев. "
            "На уровне помещения, Вт/кг: осеменение и ранняя супоросность (204 кг) при 20/25 °C — полное "
            "1.89/1.82, скрытое 0.74/1.08, явное 1.15/0.74; поздняя супоросность (219 кг) — 1.57/1.26, "
            "0.57/0.87, 1.00/0.39. Подсосные с гнездом, 20–26 °C, на кг свиноматки С ГНЕЗДОМ: до опороса "
            "2.74; дни 0–6 — 3.35; 7–12 — 4.15; 13–18 — 4.38."
        ),
        how_used=(
            "Супоросные — линейно по температуре между 20 и 25 °C, ранняя стадия до 40-го дня. Подсосные — по "
            "дню лактации, масса свиноматки + гнезда (без массы гнезда — масса из таблицы). CO₂ — из полного "
            "тепла и RQ по ур. 1–2: CO₂ = THP / (16.18/RQ + 5.02)."
        ),
        limits="Генетика и условия США; суточные средние; хряков и растущих свиней нет.",
    ),
    Source(
        id="hypor_libra_2023", kind="manufacturer",
        citation="Hypor Libra Farrowing Management Manual 2.0 (2023)",
        title="Libra Farrowing Management Manual 2.0", publisher="Hypor (Hendrix Genetics)", year=2023,
        locator="разделы о температуре помещения и гнезда",
        applies_to="уставки опоросника и гнезда — профиль свиноматок `hypor_2023`",
        statement=(
            "Помещение: вокруг опороса 23–24 °C, 1-я неделя лактации 22–23, до отъёма 20–21. Гнездо: день "
            "опороса 34–35 °C, 1-я неделя 32–33, до отъёма 30–31. RH 55–75 %."
        ),
        how_used="Середины диапазонов; «1-я неделя» — 3.5-й день, «до отъёма» — с 14-го дня.",
        limits="Уставок для холостых и супоросных в этом руководстве нет.",
    ),

    # ── Эффективная температура свиней ────────────────────────────────────────
    Source(
        id="mcfarlane_2000", kind="extension",
        citation="McFarlane J.M. 2000, How Do Your Pigs Really Feel? Effective Temperature for Pigs (через OMAFRA, Chambers 2012, табл. 1)",
        title="How Do Your Pigs Really Feel? Effective Temperature for Pigs",
        publisher="Animal Environment Specialists; приведено в OMAFRA «Controlling heat stress in swine», 2012", year=2000,
        locator="табл. 1 в статье OMAFRA",
        applies_to="охлаждение свиней скоростью воздуха (эффективная температура)",
        statement="Скорость воздуха 30 / 90 / 300 ft/min (0.15 / 0.46 / 1.52 м/с) снижает эффективную температуру на 4 / 7 / 10 °C.",
        how_used="Линейно между точками; быстрее 1.52 м/с — 10 °C (данных нет). Обратная функция даёт скорость, нужную до уставки.",
        limits="Эмпирика по поведению, без зависимости от веса и RH; совпадает с таблицей Hubbard Feeds, первоисточник McFarlane не прочитан.",
    ),
    Source(
        id="huynh_2005", kind="research",
        citation="Huynh T.T.T. et al. 2005, J Anim Sci 83:1385–1396 (PMID 15890816)",
        title="Effects of increasing temperatures on physiological changes in pigs at different relative humidities",
        publisher="Journal of Animal Science", year=2005,
        locator="аннотация (refs/norms_cooling/huynh_2005_pubmed_abstract.txt)",
        applies_to="тепловой стресс растущих свиней: пороги и потеря корма",
        statement=("Свинки 58–75 кг в камерах, температура 16→32 °C шагом 2 °C в сутки при RH 50/65/80 %. "
                   "Порог реакции: дыхание 21.3–23.4 °C, теплопродукция 22.9, ректальная температура "
                   "24.6–27.1, потребление корма 25.5 °C; выше порога корм падает на 81/99/106 г/(гол·сут) "
                   "на °C при RH 50/65/80 %. Пороги дыхания и ректальной температуры наименьшие при 80 % RH."),
        how_used=("Пороги дыхания и ректальной температуры — нижние границы при любой RH; потеря корма — "
                  "наклон по RH линейно между точками опыта, умноженный на превышение 25.5 °C. "
                  "В «сейчас» — при эффективной температуре (если дана скорость) и пределе RH; в проекте — "
                  "в самый тяжёлый день при летнем воздухе без обдува."),
        limits=("Прочитана только аннотация. Опыт — 60 кг, вне 58–75 кг и выше 32 °C — экстраполяция "
                "(в ответе заметка). Эффективная температура вместо воздуха камеры — перенос, не результат "
                "Huynh. Влага свиней в RH проекта не прибавлена."),
    ),
    Source(
        id="close_1987", kind="research",
        citation="Close W.H. 1987, табл. 3 (в репринте «Environmental housing requirements: climatic needs and responses of pigs», 2000)",
        title="Environmental housing requirements — climatic needs and responses of pigs",
        publisher="W.H. Close; thepigsite.com", year=1987,
        locator="табл. 3 (поправки к критической температуре), табл. 4 (LCT групп)",
        applies_to="влияние пола на теплоощущение свиней",
        statement=(
            "Относительно деревянного пола: солома 22 мм −4 °C, асфальт −2, бетонные щели +3 к нижней критической "
            "температуре. Холодный мокрый пол эквивалентен снижению температуры воздуха на 7–10 °C (Mount 1975)."
        ),
        how_used=(
            "Поправка к эффективной температуре ОТНОСИТЕЛЬНО ЩЕЛЕВОГО пола (уставки PIC заданы для щелей): "
            "солома +7, асфальт +5, дерево +3, мокрый бетон −8.5 °C (середина 7–10, гипотеза)."
        ),
        limits="Заголовок табл. 3 «to be added to air temperature» прочитан как сдвиг критической температуры — иначе солома была бы холоднее щелей.",
    ),

    # ── Корпус, климат, панели — перенесено из MINMAXapp sources.py (59390c7) ──
    Source(
        id="br443_2019_s3", kind="standard",
        citation="BRE BR 443 (2019) Conventions for U-value calculations, §3 — "
                 "design values per BS EN ISO 10456 / CIBSE Guide A",
        title="Conventions for U-value calculations (BR 443)", publisher="BRE", year=2019, locator="§3",
        applies_to="теплопроводность λ материалов справочника",
        statement="Расчётные значения λ строительных материалов по BS EN ISO 10456 и CIBSE Guide A.",
        how_used="λ материала из справочника (`construction.MATERIALS`); U слоя = d/λ.",
        limits="Для утеплителя точнее λ из паспорта (декларации) конкретной панели — его можно "
               "передать в слое вместо материала.",
    ),
    Source(
        id="br443_2019_s4_3", kind="standard",
        citation="BRE BR 443 (2019) §4.3 — surface resistances per BS EN ISO 6946",
        title="Conventions for U-value calculations (BR 443)", publisher="BRE", year=2019, locator="§4.3",
        applies_to="сопротивления теплообмену у поверхностей",
        statement="По ISO 6946: Rse = 0.04; Rsi = 0.13 для стен (горизонтальный поток) и 0.10 для "
                  "кровли (поток вверх) м²·К/Вт.",
        how_used="U = 1 / (Rsi + Σ d/λ + Rse); скат круче 60° считается стеной.",
    ),
    Source(
        id="marcegaglia_dop_100p_2022", kind="manufacturer",
        citation="Marcegaglia DoP 100P 09/2022 (EN 14509), PIR core λD 0.022 W/(m·K)",
        title="Declaration of Performance 100P (EN 14509)", publisher="Marcegaglia", year=2022,
        locator="DoP 09/2022",
        applies_to="материал `pir_core` (сердечник PIR сэндвич-панели)",
        statement="Заявленная теплопроводность сердечника PIR λD = 0.022 Вт/(м·К).",
        how_used="λ материала `pir_core` в справочнике.",
        limits="Значение одного производителя — типичное, не гарантия для чужих панелей.",
    ),
    Source(
        id="ashrae_2017_ch1", kind="standard",
        citation="ASHRAE Handbook Fundamentals 2017, ch. 1 — psychrometrics, standard atmosphere",
        title="ASHRAE Handbook — Fundamentals", publisher="ASHRAE", year=2017, locator="гл. 1",
        applies_to="влажный воздух и давление на высоте площадки",
        statement="Психрометрические соотношения и давление стандартной атмосферы по высоте.",
        how_used="Плотность и влагосодержание воздуха считаются при давлении на высоте `altitude_m`.",
    ),
    Source(
        id="era5_open_meteo", kind="data",
        citation="ERA5 via Open-Meteo (CC BY 4.0), 2015–2024",
        title="ERA5 reanalysis via Open-Meteo Historical Weather API", publisher="ECMWF / Open-Meteo",
        year=2024, locator="часовые данные 2015–2024", url="https://open-meteo.com",
        applies_to="расчётные температуры площадки (`design_climate`)",
        statement="Почасовые температура и влажность реанализа ERA5 за 10 лет.",
        how_used="Зима — температура обеспеченностью 99.6 %; лето — 0.4 % по сухому термометру "
                 "с совпадающей влажностью и 0.4 % по мокрому (предел панелей).",
        limits="Реанализ сглаживает местные условия (низины, побережье) — при местных данных "
               "лучше передать свои температуры. В PigMaxApp запрос к Open-Meteo — только через API v1 с ключом и лимитом.",
    ),
    Source(
        id="sanhe_pads", kind="manufacturer",
        citation="SANHE evaporative pad pressure-drop curves (digitised manufacturer data)",
        title="Кривые сопротивления испарительных панелей", publisher="SANHE", year=None, locator=None,
        applies_to="сопротивление панелей в статическом давлении туннеля",
        statement="Падение давления на панели в зависимости от скорости воздуха и толщины.",
        how_used="Слагаемое панелей в оценке давления туннеля и рабочей точке вентилятора.",
        limits="Кривые оцифрованы с графиков производителя; значение 41 Па для 150 мм при 1.78 м/с "
               "с поставщиком не сверено.",
    ),
    Source(
        id="uga_pad_velocity", kind="extension",
        citation="UGA (Czarick) Evaporative cooling pad design spreadsheet — 350 fpm (1.78 m/s) "
                 "for 150 mm pads, 225 fpm (1.14 m/s) for 100 mm",
        title="Evaporative cooling pad design spreadsheet", publisher="University of Georgia (M. Czarick)",
        year=None, locator=None,
        applies_to="площадь панелей испарительного (панельного) охлаждения",
        statement="Проектная скорость воздуха через панель: 1.78 м/с для 150 мм, 1.14 м/с для 100 мм. "
                  "Та же 1.78 м/с — в Cobb 2021 для панелей 15 см.",
        how_used="`pad_area_m2` = проектный расход туннеля / проектная скорость панели.",
    ),
    Source(
        id="amca_210", kind="standard",
        citation="AMCA 210 — fan ratings at standard air 1.2 kg/m³",
        title="AMCA 210 Laboratory Methods of Testing Fans", publisher="AMCA", year=None, locator=None,
        applies_to="характеристики вентиляторов и оценка давления",
        statement="Каталожные расходы вентиляторов даются при стандартном воздухе плотностью 1.2 кг/м³.",
        how_used="Давление туннеля оценивается при 1.2 кг/м³, чтобы сравнивать его с каталожной "
                 "кривой в тех же условиях; рабочая точка — пересечение с кривой из запроса.",
        limits="Потери давления по длине корпуса не считаются (0), пока нет замеров — для корпусов "
               "с перегородками станков это недооценка.",
    ),
    Source(
        id="uga_leakage", kind="extension",
        citation="UGA Poultry House Leakage Estimating Spreadsheet 2014; Poultry411 Tightness Calculator",
        title="Poultry House Leakage Estimating Spreadsheet", publisher="University of Georgia", year=2014,
        locator=None,
        applies_to="утечки корпуса и классы герметичности (методика выведена для птичников, физика щелей общая)",
        statement="Эквивалентная площадь щелей ELA = относительная утечка × площадь пола / 1000; "
                  "расход через щели Q = ELA·√(2ΔP/ρ).",
        how_used="Утечка при 4 Па (ветер и тяга) — нижний предел воздухообмена при обогреве: "
                 "расход = max(Qmin, утечка), утечка к Qmin не прибавляется. Класс переводится в "
                 "утечку по верхней границе (в запас для обогрева): tight 0.48, moderate 0.65, "
                 "leaky 1.0, very_leaky 1.2 м² на 1000 м² пола. Без данных — класс moderate (допущение в ответе).",
        limits="Влияет на обогрев, только когда утечка больше Qmin (`air_exchange_basis` = `leakage`).",
    ),
)


CATALOG: dict[str, Source] = {s.id: s for s in _SOURCES}


def get_source(source_id: str) -> Optional[Source]:
    return CATALOG.get(source_id)


def citation(source_id: str) -> Optional[str]:
    s = CATALOG.get(source_id)
    return s.citation if s else None


def as_dict(s: Source) -> dict:
    """Запись для API: пустые необязательные поля не отдаются."""
    return {k: v for k, v in asdict(s).items() if v is not None}
