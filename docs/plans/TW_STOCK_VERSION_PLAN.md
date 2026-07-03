# 台股版本建置計畫（TW Stock Signal MVP）

版本：`v1.0-tw-adaptation-plan`
日期：`2026-07-03`
研究方法：三路並行網路查證（資料源／市場制度／策略證據）+ 全庫原始碼盤點
母專案：`D:\Crypto-Trading`（Crypto Quant Signal MVP，Core MVP 已完成、Goal O 紙上驗證進行中）
執行位置：新儲存庫 `D:\TW-Stock-Trading`（本計畫 §9 有 bootstrap 程序）

---

## 0. 一句話定位

```text
台股現貨、只做多、公開資料、每日收盤後的訊號通知系統：
每個交易日收盤資料定稿後（~18:00 台北），系統告訴使用者 0050 的階梯部位該加還是該減、為什麼；
使用者次一交易日自行手動下單。一個 100,000 TWD 虛擬帳戶同步照做，作為誠實記分板。
永遠不下真單、永遠不碰券商帳戶 API、永遠不需要任何憑證。
```

與加密版是**同一產品哲學、同一架構、同一驗證紀律**的姊妹專案——但**不是同一個價值主張**（見 §1.1，這是本計畫最重要的一條）。

---

## 1. 研究結論摘要（2026-07-03 網路查證）

### 1.1 策略證據：價值主張必須改寫 [信心：high]

加密版的立足證據是「日線均線規則在扣除 10-50bps 成本後**勝過**買進持有」。這條證據**無法移植到台股**：

- 2000 年後的股票指數層級均線擇時，扣成本、樣本外普遍**不再增報酬**（Zakamulin 2014；Huang-Li-Wang-Zhou 2020 JFE；TSMOM ETF 樣本外衰退研究）。Faber 本人 2017 年回顧也承認擇時是「風險降低技術，不是報酬增強技術」，2006-2016 樣本外有 8 年中 6 年輸給買進持有。
- 台灣特定證據：1975-1995 年代均線規則在台股確實有效（Bessembinder & Chan 1995；Ratner & Leal 1999 兩篇頂刊），但那是 7% 漲跌幅、散戶主導、電子化前的市場；2000 年後**沒有任何可信的扣成本、含息的指數層級複製成功**。台灣公司層級證據（PBFJ 2021）顯示均線效果集中在高資訊不確定性的年輕小型股，**成熟大型股最差**——直接不利於 2330 個股擇時。
- 台灣橫斷面動能出名地弱（Chui-Titman-Wei 2010），與指數時序趨勢不完全同件事（日本先例證明可分離），但台股指數層級時序動能「**未被證實、也未被證偽**」——只有 pooled 新興市場毛報酬證據。
- 文獻上站得住的主張：**趨勢規則大幅降低回撤與波動**（一世紀全球證據；2008 台股 -58%、2022 -32% 正是此規則收割的長趨勢熊市），代價是 CAGR 約輸買進持有 1-3%/年（洗價損耗 + 空手期股利損失）。
- 誠實警告：**2023-2026 的「V 型急跌 + 融漲」行情是此規則最差的 regime，而且它就是現在的 regime**（2024-08-05 -8.35%、2025-04-07 -9.70% 後 14 個月 +160%、2026-06-08 記錄盤中跌點後 V 轉——三次都是趨勢規則的絞肉機）。

**設計決策**：台股版策略合約的預先登記主張（primary claim）從「增報酬」改為「**降回撤**」：

```text
主要主張（預先登記、閘門檢定）：
  MaxDD(策略) ≤ 60% × MaxDD(0050 含息買進持有)
且 CAGR(策略) ≥ CAGR(0050 含息買進持有) − 3 個百分點
（扣全成本、以含息序列計算、閒置現金利率 0%，敏感度另跑 1.5%）

若登記回測 + holdout 無法同時滿足兩條 → 誠實結論是「台股適合買進持有，不適合這套訊號」，
出 FAIL 報告，產品到此為止。FAIL 也是本計畫的合格結局。
```

比較基準**強制**為 0050 含息買進持有（次要參考：發行量加權報酬指數）。絕不可用不含息的加權指數做比較——2003 年以來價格指數與報酬指數已相差 2.3 倍（約 3.5-4%/年的股利楔子），多數中文回測文章正是死在這裡。

### 1.2 市場制度：關鍵數值（全部經官方來源驗證，詳附錄 B）

| 項目 | 台股值 | 對系統的含義 |
| --- | --- | --- |
| 交易時段 | 09:00-13:30 逐筆；收盤 13:25-13:30 集合競價（可延至 13:33） | 決策時點 = 台北收盤後 |
| 資料定稿 | 收盤價 ~14:00 起可得；盤後含零股/鉅額的最終量 ~17:30 | 每日排程 **≥18:00 台北** |
| 漲跌幅 | ±10%（2015-06-01 起），0050/2330 皆適用 | 加密版 -20% 單日災難事件**不可能觸發**，必須重定義 |
| 跌停鎖死 | 2025-04-07：1,702 檔跌停、0050 史上首次鎖死跌停 | 「訊號出了但出不掉」要進回測假設與風險事件 |
| tick 表 | 股票 6 級距；ETF <50 元 0.01、≥50 元 0.05 | 需分級 tick 表（現制單一 `price_tick` 不夠） |
| 交易單位 | 整股 1000 股；零股 1-999 股，盤中每 5 秒撮合；0050 是全市場零股成交王 | 記分板用零股粒度（1 股）合理可行 |
| 交割 | T+2 | 紙上帳純現金制可忽略，文件註明即可 |
| 手續費 | 上限 0.1425%/邊；電子單主流約 6 折（0.0855%）；最低 NT$20（零股部分券商 NT$1） | 費用模型需：折扣係數 + 最低費 |
| 證交稅 | 僅賣方：股票 0.3%、**ETF 0.1%**；無資本利得稅 | ETF 成本優勢 → 支持 0050；稅是賣方單邊 |
| 來回成本 | 0050 約 0.27% + 滑價；2330 約 0.47% + 滑價（tick 就 20bp） | 閘門成本假設：ETF 30-35bps，壓力測試 ×2 |
| 股利 | 0050 半年配（1 月/7 月除息；2026：1/22 配 1.00、7/21 配 0.60）；2330 季配（3/6/9/12 月） | 帳本需股利事件；訊號需還原價 |
| 二代健保 | 單筆股利 ≥ NT$20,000 扣 2.11%（ETF 僅 54C 股利所得部分計入） | 10 萬帳戶單筆配息遠低於門檻 → 設定檔開關，預設關 |
| 0050 分割 | 2025-06 1→4 分割，**停牌 2025-06-11~06-17**，6/18 以 47.16 恢復 | 原始序列有 -75% 斷層 + 6 個交易日空洞：還原管線與「停牌容忍」都是必要功能 |
| 行事曆 | ~245 交易日/年；2026 春節 2/12-2/23 連 11 個日曆日無交易；**補班日股市休市**；颱風假臨時休市 | 交易日曆模組是新的必要基礎設施 |
| 市場現況 | 2026-07-03：加權 46,780.62；0050 收 108.35；2330 約 2,465 | 10 萬 TWD 帳戶 → 0050 零股執行 |

### 1.3 資料源選型（全部端點經實際抓取驗證，詳附錄 A）

| 用途 | 定案 | 理由 |
| --- | --- | --- |
| 每日 OHLCV（主） | TWSE RWD `STOCK_DAY`（按月 JSON、免金鑰） | 官方、當日 ~17:30 定稿、涵蓋 ETF；**歷史下限 2010-01-04** |
| 歷史回補（2010 前） | FinMind `TaiwanStockPrice`（匿名 300 req/hr 即可） | 一次呼叫回傳 0050 全生涯 5,661 根（2003-06-30 起）、2330 到 1994；含當日 |
| 公司行動 | TWSE `TWT49U`（除權息結果，2003-05-05 起）+ `TWTCAU`（ETF 分割）+ `TWTAUU`(減資) + OpenAPI `TWT48U_ALL`（預告） | 官方、免金鑰、欄位含前收/參考價 → 還原因子 = 參考價 ÷ 前收盤價，可自建還原序列 |
| 交易日曆 | TWSE OpenAPI `holidaySchedule`（當年度）+ 「18:30 仍無資料」臨時休市偵測 + FinMind `TaiwanStockTradingDate` 對帳 | 颱風假不在預告表內，必須用執行期偵測 |
| 交叉驗證 | FinMind 日線等值比對（每日）；yfinance **只比還原報酬**（每週） | Yahoo 對 0050 整段回溯改寫（無 split 事件、停牌日填假 K 棒、分割前股利 ÷4、量差 3-8%）——絕不可當原始價源 |

「永不需要 API key」的產品規則**得以保留**：上述主路徑全部免金鑰；FinMind 匿名即可用（註冊 token 僅提高配額，屬選配，且是資料 token、非帳戶憑證）。券商 API（Shioaji/Fugle 等）明確排除。

---

## 2. 不變的部分（架構分層的回報）

以下**原樣沿用**，這是加密版架構紀律的直接回報：

- 分層架構與模組邊界（data → features → strategy → portfolio → risk → execution → accounting → runtime/backtest → api）與 import-linter 邊界。
- **策略數學**：Daily Trend Ensemble SMA 20/65/150/200、子訊號投票、0/25/50/75/100% 階梯、合約鎖定參數、reason codes、warmup 200 根。階梯式部分曝險本身就是研究建議的「減少洗價成本」手段。
- **驗證閘門六關的數學**：trial registry（append-only、N 單調遞增）、CSCV S=16 → PBO ≤ 0.05、DSR ≥ 0.95、單次使用 holdout（~12 個月）、≥3 個月紙上驗證、成本量測 1.5× 重校準規則。`src/backtest/registry.py`、`validation.py`、`holdout.py` 市場無關，直接重用。
- 通知冪等（persist-before-deliver、idempotency key、重啟零重複）、JSONL 事件儲存、重啟安全、append-only 帳本。
- 唯讀 dashboard（FastAPI + 輪詢 JSON）、無下單路徑、無私有 API 路徑。
- 安全硬規則全數沿用：只做多、無負部位、無槓桿/融資/融券/衍生品、只用已收盤 K 線、次一 bar 才可執行、成本顯式、全程稽核。
- 工具鏈：Python 3.12、ruff/mypy --strict/import-linter/pytest（`not network` 為預設）、TimescaleDB compose。

---

## 3. 設計決策（D1-D10）

### D1 產品邊界 [信心：high]
同加密版：訊號通知 + 手動執行 + 紙上記分板。永久排除清單原樣沿用並加一條：**券商 API（含 Shioaji、Fugle 交易/行情 API）屬私有 API，永久排除**。

### D2 決策時點與執行假設 [信心：high]
- 決策 K 線：台股**交易日**日線（13:30 台北收盤）。內部時間戳維持 UTC（收盤 = 05:30 UTC），K 線加掛 `trading_date`（台北日期）欄位。
- 每日週期：台北 **18:00** 起跑（資料 17:30 定稿 + 緩衝）→ 抓當日 K → 品質檢查 → 特徵/策略/風險 → 通知（當晚送達）。
- 記分板成交規則：**次一交易日開盤價**成交（維持加密版 later-bar 規則；回測/runtime 同一條路徑）。
- 盤後定價交易（14:00-14:30 以當日收盤價成交）可把「隔夜跳空」消掉，但需在 14:00 前跑初步資料且只能整股——記為 Goal P 級的執行時點實驗，MVP 不做。

### D3 標的宇宙 [信心：high]
```text
決策宇宙：0050（元大台灣50）——風險預算 100%
執行替代：006208（富邦台50，同指數）——僅文件註明可替代執行，不是第二個訊號資產
明確排除：2330 個股擇時（證據三殺：成熟大型股是均線效果最差族群、
          賣稅 0.3% 使來回成本 ~0.47%+20bp tick、跌停鎖死排隊風險最集中）
未來研究（Goal P 級、需預先登記）：第二資產（如債券/海外 ETF sleeve）、0050 以外的 slice
```
單一資產讓試驗數 N 最小、成本最低、與研究證據對齊。注意誠實揭露：0050 的 TSMC 權重 2025-26 已達 57-64%，「指數擇時」實質上六成是台積電趨勢；且與加密帳的 AI 週期相關性上升——這寫進策略合約的 Caveats。

### D4 策略合約 [信心：high]
`STRATEGY_DAILY_TREND_ENSEMBLE_TW.md`：數學與加密版完全相同（SMA 20/65/150/200、階梯、邊界值算不站上、warmup 200 交易日），差異只有三處：
1. 輸入改為**還原收盤價序列**（除權息/分割還原；帳務仍用原始價）——否則每個除息日都是假跌破。
2. 價值主張章節改寫為「回撤降低」（§1.1 的登記主張全文）。
3. Caveats 新增：TSMC 集中度、漲跌停執行風險、當前 regime 警告。

### D5 成本模型 [信心：high]
```text
買進成本 = notional × 0.1425% × discount(預設 0.6)，低消 max(·, NT$20)
賣出成本 = 同上手續費 + notional × sell_tax_bps（ETF 10bps / 股票 30bps）
滑價     = 5 bps（0050 零股 tick ≈ 4.6bp 的近似；壓力測試另跑）
閘門成本假設：0050 來回 ≈ 30-35 bps；Gate 6 實測 > 1.5× 假設 → 重校準重跑
```
設定檔結構：`fee_bps_ceiling / fee_discount / min_fee_twd / sell_tax_bps_by_instrument / slippage_bps`。最低手續費是新概念（加密版沒有），10 萬帳戶 25% 階梯步 ≈ NT$25,000 單 → 手續費 ~21 元 > 低消 20 元，剛好安全；`min_notional_twd = 10,000` 保證低消侵蝕 ≤ 20bps。

### D6 風險參數重定義 [信心：medium，回測階段校準]
| 參數 | 加密版 | 台股版 | 理由 |
| --- | --- | --- | --- |
| 災難事件 | 單日 ≤ -20% | 單日收盤 ≤ **-9%**（跌停帶）**或** 3 個交易日累計 ≤ **-15%** | ±10% 下 -20% 不可能；2025-04 關稅崩盤 3 日 -18% 應觸發 |
| 最大回撤暫停 | 0.65（預期 DD 50-60%） | **0.40**（策略預期 DD 15-25%，B&H 最壞 -58%） | 煞車須在預期帶之上、災難之下；TW-E 回測登記校準，避免重蹈加密版 trial-1 鎖死 |
| 單日虧損暫停 | 0.10 | 0.095 | 滿倉 + 跌停 = -10% 帳戶日虧，門檻須略低於它才有意義 |
| stale 資料 | 129,600 秒 | **交易日計**：18:30 後仍無當日資料、且日曆/對帳來源顯示今天是交易日 → stale-halt（禁加碼、允許減碼） | 秒數制在春節 11 天連假必誤報 |
| 停牌容忍 | 無此概念 | 多交易日無資料 + 有公司行動預告 → `SUSPENSION` 健康事件，非 stale 事故 | 0050 分割停牌 6 個交易日是真實案例 |
| 漲跌停感知 | 無此概念 | 成交日開盤價觸及漲跌停 → fill 照常但記 `LIMIT_DAY` 旗標 + 健康事件 | MVP 誠實記錄；「鎖死順延成交」模型記為登記實驗 |

### D7 股利與公司行動帳務 [信心：high]
帳本新增事件型別（append-only 不變）：
- `DIVIDEND_ACCRUED`：除息日按持有股數登記應收股利（計入權益——原始價已在除息日下跌，不掛應收會低估權益）。
- `DIVIDEND_PAID`：發放日應收轉現金（0050 約除息後 3-4 週）。
- `SPLIT_ADJUST`：分割/反分割/減資——股數 × 比率、平均成本 ÷ 比率，權益不變（0050 2025 分割為黃金測試案例）。
- 二代健保 2.11% 扣繳：設定檔開關 + NT$20,000 單筆門檻檢查，預設關（10 萬帳戶碰不到）。

### D8 資料架構：雙價格序列 [信心：high]
```text
原始序列（raw）    ：TWSE/TPEx 官方數字。用途：帳務、成交、通知顯示價。
還原序列（adjusted）：raw × 鏈式還原因子（每個除權息/分割事件：因子 = 官方參考價 ÷ 前收盤價）。
                     用途：SMA 特徵、策略決策、回測報酬與基準比較。
```
還原因子表獨立持久化（含事件來源、日期、因子），事件新增時**只回溯重算還原序列、不改 raw**。序列品質檢查改為交易日曆感知：GAP = 缺交易日；週末/假日/停牌不是 GAP。

### D9 驗證閘門 TW 化 [信心：high]
六關數學不變，參數 TW 化：
- Gate 2 資料下限：≥1,000 **交易日**觀測（≈4.1 年）；regime 覆蓋要求改為「至少含一次熊市進入（2022）、一次崩盤 V 轉（2024-08 或 2025-04）、一次融漲（2023-26）、一次盤整（2010-16 段）」→ **建議回測全樣本 2008-01 起（~4,400+ 觀測）**，0050 資料自 2003-06 可得。
- Gate 3/4（PBO/DSR）：原樣。
- Gate 5 holdout：最近 ~12 個月鎖定、單次使用、不可迭代——原樣。
- Gate 6：≥3 個日曆月紙上運行 + 實測成本 ≤ 1.5× 假設（35bps → 上限 ~52bps）——原樣。
- **新增主要主張檢定**（D4 的降回撤主張）：在全樣本與 holdout 上同時檢定 MaxDD 與 CAGR 兩條件；此主張在第一筆登記回測前寫死於合約，之後不可改。

### D10 儲存庫策略 [信心：high]
**新獨立 repo**，不做同庫多市場 adapter：
- 理由：市場語意滲透全層（日曆/幣別/稅費/公司行動）；加密版 Goal O 紙上時鐘正在走，不可動搖；文件/合約/GOALS 全需 TW 版本。
- `git clone D:\Crypto-Trading D:\TW-Stock-Trading` 保留完整歷史作為出處證明，首個 commit 為「TW redefinition」。
- **刪除**（非保留）加密專屬程式碼：`binance.py`、`binance_public_hosts.py`、15m 路徑、`large_liquid_trend_15.py`——新產品不背舊市場的死碼，git 歷史即出處。（加密 repo 內的「不可刪除」規則屬於該 repo，不隨 clone 繼承。）
- 共用程式庫抽取：**明確否決**（過早抽象）；兩系統各自穩定後再議。

---

## 4. 模組改動地圖（檔案級）

| 模組 | 動作 | 內容 |
| --- | --- | --- |
| `src/binance_public_hosts.py` | 刪除→新增 | `src/tw_public_hosts.py`：TWSE RWD/OpenAPI、TPEx、FinMind base URLs |
| `src/data/binance.py` (610行) | 刪除→新增 | `src/data/twse.py`（RWD STOCK_DAY 月批次 + OpenAPI）+ `src/data/finmind.py`（回補/對帳）。純 REST、無 WebSocket。解析：ROC 年（三種格式，含 2 位數年前導空白）、逗號千分位、`漲跌價差="X"`、`註記="**"`、MI_INDEX 內嵌 HTML |
| 新 `src/data/calendar.py` | 新增 | 交易日曆：holidaySchedule 擷取（過濾「僅辦理結算交割」與資訊列）、交易日算術（next/prev/count）、颱風臨時休市偵測協定、年度更新提醒（API 僅回當年） |
| 新 `src/data/adjustments.py` | 新增 | TWT49U/TWTCAU/TWTAUU 擷取 → 事件表 → 鏈式還原因子 → 還原序列；pre-2011 TWT49U 17 欄舊 schema 相容 |
| `src/data/quality.py` | 重寫 gap/stale | `timeframe_delta` 的 1d=24h 假設改為日曆感知：expected next open = 下一交易日；stale 以交易日計；停牌容忍 |
| `src/data/types.py` | 修改 | `SymbolFilters` → `TwInstrumentRules`（tick 級距表、instrument_type ∈ {etf, stock}、lot 規則）；`UniverseSelectionRules` TW 化 |
| `src/domain/types.py` | 小改 | `Symbol`（已是彈性字串）：value=`0050`、base=`0050`、quote=`TWD`；`Candle` 加 `trading_date`；帳本事件枚舉加 D7 三型別 |
| `src/features/daily_trend.py` | 近乎不變 | 輸入改接還原序列；warmup 200 交易日 |
| `src/strategies/daily_trend_ensemble.py` | 不變 | — |
| `src/portfolio/ladder.py` | 小改 | 權重→股數：零股粒度 1 股（設定檔可切整股 1000） |
| `src/risk/gate.py`、`events.py` | 中改 | D6 全部：災難雙觸發、DD 0.40、stale 交易日制、SUSPENSION、LIMIT_DAY |
| `src/execution/broker.py` | 重寫費用 | D5 費用模型（折扣、低消、賣方稅、分級 tick 進位） |
| `src/accounting/ledger.py` | 中改 | D7 股利/分割事件、TWD 2 位小數 |
| `src/backtest/*` | 重用 | registry/validation/holdout 原樣；engine 接日曆與 TW 費用；報告加基準比較（0050 含息 B&H）與閒置現金利率選項（預設 0%，敏感度 1.5%） |
| `src/runtime/engine.py` | 中改 | 週期排程（18:00 台北、假日跳過、颱風偵測）、MISSED_DAYS 只計交易日、除息日帳務掛鉤 |
| `src/notify/`、`src/api/` | 小改 | TWD 顯示、代號+中文名、股利/除息日曆面板、基準曲線、LIMIT_DAY/SUSPENSION 狀態 |
| `scripts/` | 修改 | `run_daily_cycle.cmd` 排平日 18:00 台北；`ingest_public_ohlcv.py` → TW 回補（FinMind 2003→今 + TWSE 2010→今 對帳拼接） |
| `configs/runtime/paper_runtime.yaml` | 全面改值 | 見 §5 範本 |
| `tests/` | 鏡像 + 新增 | 新增黃金測試：0050 分割月（2025-06）、2330 除息（2026-06-11 配 6 元）、2026 春節缺口、2/20 補班日休市、2025-04-07 跌停週、TWT49U 舊 schema |

## 5. 設定檔範本（`configs/runtime/paper_runtime.yaml` TW 版核心差異）

```yaml
data_source:
  provider: twse_public
  symbols: ["0050"]
  backfill_provider: finmind_public   # 匿名即可，token 選配
  rest_timeout_seconds: "10"
  daily_job_time_taipei: "18:00"
  api_key_required: false
strategy:
  name: daily_trend_ensemble          # 合約鎖 SMA 20/65/150/200，無可調參數
portfolio:
  risk_budgets: { "0050": "1.0" }
  quantity_mode: odd_lot              # 1 股粒度
risk:
  min_notional_twd: "10000"
  stale_trading_days: 1
  max_drawdown_fraction: "0.40"       # TW-E 回測登記校準
  daily_loss_pause_fraction: "0.095"
  disaster_single_day_drop_fraction: "0.09"
  disaster_multi_day: { sessions: 3, drop_fraction: "0.15" }
execution:
  mode: paper
  fee_bps_ceiling: "14.25"
  fee_discount: "0.6"
  min_fee_twd: "20"
  sell_tax_bps: { etf: "10", stock: "30" }
  slippage_bps: "5"
  tick_table: tw_default              # 內建分級表
account:
  initial_cash: "100000"
  quote_asset: TWD
  dividend_nhi_withholding_enabled: false   # 單筆 ≥20,000 才適用
```

## 6. 目標序列（TW-A ~ TW-H）

> 完成定義沿用加密版紀律：每個 Goal 結束都必須通過 baseline verification
> （ruff / ruff format / mypy --strict / lint-imports / pytest -m "not network"）。

### TW-A：Repo Bootstrap + 文件重寫
- clone → 刪加密專屬碼（D10 清單）→ 重命名（pyproject、DB 名 `tw_quant`、README）。
- 撰寫：`tw_quant_architecture.md`、`GOALS.md`（本節內容展開）、`docs/research/TW_SIGNAL_DESIGN_RESEARCH.md`（把本計畫 §1 三份查證報告全文整理歸檔，含來源清單與被否決論點——對齊加密版研究報告格式）、四份合約（STRATEGY_TW / UNIVERSE_TW / VALIDATION_GATE_TW / DATA_ADAPTER_TWSE）。
- Done when：文件齊、殘留的 crypto 引用 grep 為零、baseline 綠。

### TW-B：Domain / Config / 交易日曆
- D6/D5 設定模型、`trading_date`、tick 表型別；`src/data/calendar.py` 全功能 + 2026 年曆 fixtures（春節、2/20 補班休市、颱風偵測協定）。
- Done when：日曆算術測試齊（含跨年、連假、停牌）；config 拒絕 real-trading/私有 API 旗標不變。

### TW-C：資料層（本計畫最大的新工程）
- `twse.py`/`finmind.py` 客戶端 + 全部解析陷阱（附錄 A）；quality.py 日曆感知重寫；`adjustments.py` 還原管線；回補腳本（FinMind 2003→今、TWSE 2010→今逐月對帳、拼接、落地 `data/candles/`）。
- Done when：0050 全史回補完成且兩源對帳零差異（2010+）；還原序列通過黃金測試（分割月、除息日因子）；`pytest.mark.network` 煙霧測試通過。

### TW-D：策略/投組/風險/執行/帳務 retrofit
- §4 對應模組全部改完；黃金測試：跌停週災難觸發、股利三事件、低消侵蝕、階梯股數換算。
- Done when：確定性測試（同史同決策）、只做多不可表示負部位、費稅逐筆可稽核。

### TW-E：回測 + 第一筆登記試驗（真相時刻之一）
- backtest 接日曆/費用/基準；**先鎖 holdout**（最近 ~12 個月，單次使用）；登記 trial #1：ensemble on 0050，2008-01→holdout 邊界，全成本、含息基準比較；成本壓力 ×2 重跑；PBO/DSR 輸出。
- Done when：報告含 §1.1 主要主張的樣本內裁決 + 洗價普查（2010-16、2023-26 兩段各多少次來回、成本吃掉多少）。**若樣本內就不滿足主張 → 直接跳 TW-H 出 FAIL 報告，不建 runtime。**

### TW-F：Runtime + 通知 + Dashboard
- 18:00 週期、颱風/停牌/缺日健康事件、通知冪等重啟測試、dashboard TW 顯示（含除息日曆、基準曲線、閘門狀態頁）。
- Done when：錄製重播端到端 + 一次真實公開資料煙霧 + 重啟零重複。

### TW-G：TW Core MVP Complete
- 對齊加密版 Goal N 的 11 條驗收，全部在真實 0050 資料上驗證。

### TW-H：Signal-Live Qualification（= 加密版 Goal O）
- 凍結 → PBO/DSR → 花費 holdout（單次、記錄）→ ≥3 個日曆月紙上運行 + 實測成本 → `docs/reports/` 出裁決報告（PASS 或 FAIL 都算完成）。

**工作量粗估**：TW-A~B 各一個工作段；TW-C 兩段（回補與對帳含等待）；TW-D 一段；TW-E 一段 + 計算時間；TW-F 一段；TW-G 半段。TW-H 需 3+ 個月牆鐘（與加密版 Goal O 平行計時，互不干擾）。

## 7. 風險與開放問題（只有自己的登記回測能回答）

1. 20/65/150/200 ensemble 在 0050 含息序列、扣全成本下的實際 CAGR/MaxDD/Sharpe（2008-2026）——主要主張成敗在此。
2. 洗價普查：2010-2016 盤整段與 2023-2026 融漲段的來回次數與成本；階梯 vs 全進全出的差異量化。
3. 2025-04 崩盤情境重建：訊號幾號出場、幾號回補、含跌停日滑價後是賺是虧（初步文獻重建 ≈ 打平）。
4. TSMC 集中度敏感度：同規則在 ex-TSMC 口徑下是否還成立（登記實驗，非 MVP）。
5. 二代健保年度歸戶改革（2025-11 暫緩，未死）——TW-H 前重查。
6. 券商折扣是行銷變數——Gate 6 用實測成本說話，不信任何名目折數。

## 8. 誠實條款

```text
本計畫的成功定義不是「建出台股版」，而是「對 0050 擇時說真話」。
證據目前說：報酬別想贏買進持有，回撤保護是唯一可辯護的主張，而且現在的 regime 對它最不利。
若 TW-E 或 TW-H 的閘門否決主張，正確產出是一份 FAIL 報告與「請直接買進持有」的結論。
閘門的工作是說真話，不是放行。
```

## 9. Bootstrap 程序（TW-A 第一步，供照做）

```powershell
git clone D:\Crypto-Trading D:\TW-Stock-Trading
cd D:\TW-Stock-Trading
git remote remove origin 2>$null   # clone 本地路徑會掛 origin，移除
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements\constraints-dev.txt
# 之後按 TW-A 清單刪檔、重命名、重寫文件，首 commit 訊息："TW redefinition: fork from crypto MVP @ed9b194"
```

---

## 附錄 A：已驗證資料端點（2026-07-03 實抓確認）

### TWSE（免金鑰）
- **每日 OHLCV（月批次）**：`https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMMDD&stockNo=0050&response=json`
  - 回傳該月整月；列格式 `["115/06/01","成交股數","成交金額","開","高","低","收","漲跌價差","成交筆數","註記"]`；ROC 年、逗號千分位、漲跌可為 `"X"`（不比價）、註記 `**` = 分割/面額變更恢復日。
  - **歷史下限 2010-01-04**（更早回 `stat` 錯誤訊息）；當日資料 ~14:00 初步、**~17:30 最終**（含零股/鉅額）。
  - 節流：官方未文件化；社群通報快速抓取會被 IP 封鎖。守則：單線程、每 3-5 秒 1 請求、真實 User-Agent。全史回補 ≈ 200 請求/檔 ≈ 15 分鐘。
- **單日全市場**：`.../MI_INDEX?date=YYYYMMDD&type=ALLBUT0999&response=json`（~247KB；**值內嵌 HTML** 如 `<p style='color:red'>+</p>`，解析須剝除）。
- **除權息結果**：`https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=YYYYMMDD&endDate=YYYYMMDD&response=json`
  - 欄位含除權息前收盤價、參考價、權值+息值、權/息類別、開盤競價基準 → **還原因子 = 參考價 ÷ 前收盤價**。歷史下限 **2003-05-05**；**~2011 前為 17 欄舊 schema**（權值/息值分列、型別混雜），解析按欄名。
- **ETF 分割**：`.../split/TWTCAU?startDate=...&endDate=...&response=json` — 已驗證 0050 列：`['114/06/18','0050','元大台灣50','分割','188.65','47.16','51.85','42.45','47.16']`（比率自算 188.65/47.16≈4）。
- **減資**：`.../reducation/TWTAUU?startDate=...&endDate=...&response=json`（恢復買賣日、前收、參考價、原因）。
- **OpenAPI**（`https://openapi.twse.com.tw/v1/`，swagger 143 路徑）：
  - `exchangeReport/TWT48U_ALL`（除權息**預告**；ETF 金額公告前為空欄）。
  - `holidaySchedule/holidaySchedule`（**僅當年度**、ROC 日期、含「僅辦理結算交割」列與資訊列，需過濾）。
  - ⚠️ `exchangeReport/STOCK_DAY_ALL` 是 **T+1**（實測 Last-Modified = 次日凌晨 05:20 台北）——不可用於當日訊號。

### FinMind（`https://api.finmindtrade.com/api/v4/data`；匿名 300 req/hr、免費 token 600 req/hr）
- `TaiwanStockPrice`：0050 一次回傳全史 5,661 列（2003-06-30 → 當日，17:30-17:45 更新）；2330 到 1994-09-13；停牌日直接缺列；量的單位=股。
- `TaiwanStockDividendResult`（除息前後價/參考價 → 還原因子鏡像源）、`TaiwanStockSplitPrice`（0050 分割已驗證）、`TaiwanStockCapitalReductionReferencePrice`、`TaiwanStockTradingDate`（實際交易日名冊，18:00 更新——颱風對帳用）。
- ⚠️ `TaiwanStockPriceAdj`（現成還原價）**付費牆**——還原自建（本計畫本來就要求，因子可稽核）。
- 週日 00:00-07:00 維護停機；資料條款教育/非商用；GitHub 活躍（2.0.4，2026-06-26）。
- 下市股票資料保留（已驗證 2311/3474）——未來宇宙研究的 survivorship-safe 來源。

### TPEx（僅未來納入上櫃時）
- `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=5483&date=2026/06/01&response=json`
- ⚠️ 單位陷阱：TPEx 成交量單位=**張**（×1000 股）、金額=**仟元**，與 TWSE（股/元）不同；不含鉅額交易。OpenAPI（225 路徑）當日 ~17:40 已有資料。

### Yahoo/yfinance（僅還原報酬交叉驗證）
- 0050 整段歷史被回溯改寫：無 split 事件、停牌日填 volume=0 假 K 棒、分割前股利顯示 ÷4（2025-01-17 顯示 0.675，實際 2.70）、成交量差 3-8%。**絕不可作原始價源或股利金額源**；2024-11 起 429 限流問題持續。

## 附錄 B：費稅與制度數值（來源信心標註）

| 項目 | 數值 | 生效/驗證 | 信心 |
| --- | --- | --- | --- |
| 手續費上限 | 0.1425%/邊；電子單主流 ~6 折、激進 2-2.8 折；低消 NT$20（零股部分券商 NT$1） | 2026 現行 | High（折數屬行銷、易變） |
| 證交稅 | 賣方：股票 0.3%、ETF 0.1%；現股當沖 0.15% 延至 **2027-12-31**（三讀 2024-12-31） | 財政部 | High |
| 資本利得稅 | 無（證所稅停徵） | — | High |
| 股利稅 | 合併 8.5% 抵減（上限 8 萬）或 28% 分離，擇一 | 財政部 | High |
| 二代健保 | 單筆股利 ≥ NT$20,000 扣 2.11%；ETF 僅 54C 部分；**年度歸戶改革 2025-11 暫緩** | 衛福部/媒體 | High（改革動向需追蹤） |
| 漲跌幅 | ±10%（2015-06-01 起）；除權息日以參考價為基準 | TWSE | High |
| tick | 股票：<10:0.01｜10-50:0.05｜50-100:0.1｜100-500:0.5｜500-1000:1｜≥1000:5；ETF：<50:0.01、≥50:0.05 | TWSE | High |
| 時段 | 09:00-13:30；收盤集合競價 13:25-13:30（可延 13:33）；盤後定價 14:00-14:30；盤中零股 09:10 起每 **5 秒**撮合（2024-12-02 起） | TWSE/金管會 | High |
| 交割 | T+2；零股 T+1 可賣、不可當沖 | TWSE | High |
| 行事曆 | ~245 日/年；2026 春節 2/11 收後至 2/23 開盤；**補班日休市**（2026-02-20 已驗證）；颱風依台北市停班公告臨時休市 | TWSE holidaySchedule | High |
| 0050 | 2026-07-03 收 108.35；2025-06 1→4 分割（停牌 6/11-6/17）；半年配（2026：1/22 除息 1.00、7/21 除息 0.60）；經理費級距 0.15%→**0.05%**（規模 >1 兆，2025-12）；TSMC 權重 ~57-64% | TWSE/發行商 | High |
| 2330 | ~2,465（tick=5 → 單 tick ≈20bp）；季配 3/6/9/12（2026-06-11 除息 6 元、2026-09-16 將除息 7 元）；2025-04-07 史上首鎖跌停；2026-02-24 曾列注意股（純揭露） | TSMC IR/TWSE | High |
| Regime 表 | 2008 -58%｜2011 -28%｜2015 ~-28%*｜2018 ~-17%*｜2020 -30%｜2022 -32%｜2024-08-05 單日 -8.35%｜2025-04-07 單日 -9.70%（1,702 檔跌停）｜2025-04→2026-06 +160%｜2026-06-08 盤中記錄跌點、收 -3.5%（*=Med 信心，用前重驗） | 多源 | High/Med |
| 國安基金 | 9 次進場；最近 2025-04-08 → 2026-01-12（279 天，史上最長） | 媒體 | High |

## 附錄 C：策略證據來源精選（完整清單見 TW-A 的研究報告歸檔）

Faber 2007/2017（SSRN 962461；JPM 2017 覆盤）· Zakamulin 2014（J. Asset Mgmt）· Huang-Li-Wang-Zhou 2020（JFE 135）· Hurst-Ooi-Pedersen 2017（世紀證據）· Moskowitz-Ooi-Pedersen 2012（JFE）· Bessembinder & Chan 1995（PBFJ）· Ratner & Leal 1999（JBF）· Chui-Titman-Wei 2010（JF）· Griffin-Ji-Martin 2003（JF）· Georgopoulou & Wang 2017（Rev. Finance）· PBFJ 2021 台灣公司生命週期均線研究 · TWSE 報酬指數說明 · 台股漲跌停價格發現研究（TWSE/KJFS）
