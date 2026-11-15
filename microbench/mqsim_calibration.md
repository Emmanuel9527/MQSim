# MQSim 校準工作紀錄與執行入口

狀態：**功能短測與24組基準均已完成；趨勢尚未對齊，尚未選出校準參數。**
所有模擬與真機實驗由使用者執行 bash 腳本。準備 XML、讀取既有結果、靜態／離線檢查不會啟動實驗。

## 校準目標：以趨勢變化為主

使用者要求：校準的是負載／QD變化時的反應趨勢，不追求絕對IOPS或latency逐點吻合。
本文件統一保存校準計畫、來源審查、測試紀錄與結果判讀；後續分析程式更新此文件，不另產生每輪Markdown報告。
原始XML、JSON、CSV、log與程式snapshot仍留在各結果目錄，維持可追溯性。

主要比較項目：

1. **吞吐增益**：IOPS(QD) / IOPS(QD1)。檢查增加併發後能帶來多少增益。
2. **延遲成長**：mean_latency(QD) / mean_latency(QD1)。檢查排隊隨需求增加的形狀。
3. **局部增益與斜率**：相鄰測試點的IOPS比值，以及 Δlog(IOPS)/Δlog(QD)。斜率接近1表示在該區間近似線性成長，接近0表示吞吐近似平台；這是描述，非內部飽和證明。
4. **轉折位置與範圍**：看多個連續區間是否穩定趨平，並保留seed／真機repeat的不確定性。不能只用QD1與32判定拐點，也不任意縮放QD軸讓曲線吻合。
5. **跨工作負載的一致性**：同一組模型參數是否能解釋request size、sequential/random與後續batch趨勢。

正規化採每個模擬seed、每個真機repeat自己的QD1，完整保留所有比值，再報中位數與min/max。
另外保留相鄰QD增益，避免QD1快慢變動主導整條曲線。真機repeat不是固定硬體狀態，這些比值仍可能混入時間變動。
不得刪掉快速時段、每QD各調參數、任意拉伸軸、或用補償係數來美化趨勢。

絕對數值只作背景、單位檢查與量級診斷；不以「約25k IOPS」作必須逼近的校準目標。
即使絕對值不同，若趨勢在重複變動範圍內相符，模型可對特定研究問題有用；但可用範圍與剩餘差異必須註明。
同樣地，正規化後曲線吻合也不證明內部硬體結構正確。

## 第一輪的目的

先回答「目前的 MQSim 候選模型，對 4 KiB random read 的 QD 變化如何反應？」
不是直接宣稱重建 GM7。建立實際模擬結果之後，才有依據決定哪些既有機制值得校準、哪些觀測仍無法解釋。
25k IOPS 是真機部分時段的常見平台，不是先驗硬體最大吞吐，也不是必須硬配的常數。

## 來源審查發現

| 項目 | 本地程式依據 | 校準處理 |
|---|---|---|
| Engine 時間 | `src/sim/Sim_Defs.h`，ONE_SECOND=1e9 | 事件時間為 ns，不需導入全域逐 cycle 引擎 |
| XML response time | `src/host/IO_Flow_Base.cpp`，Get_device_response_time / Get_end_to_end_request_delay 除以1000後轉uint32 | XML 已是整數 µs；README 的 ns 說明與實作不同，不再除1000 |
| XML IOPS | 同檔 Report_results_in_XML：generated count / Simulator::Time | 同時核對完成讀取 bytes = planned count×4096，避免未排空仍稱完成IOPS |
| QD workload | `src/host/IO_Flow_Synthetic.cpp`：初始產生QD筆，每次completion補一筆 | 對應continuous refill；不重播有漂移的burst trace |
| I/O大小 | Average_Request_Size 是sector數，SECTOR_SIZE_IN_BYTE=512 | 4KiB=8 sectors；alignment=8，不沿用範例的16-sector alignment |
| Working set | `src/exec/IO_Flow_Parameter_Set.h` 是unsigned int百分比；Host_System.cpp 限制1–100 | 選最接近32GiB的整數百分比，將實際差距明列，不稱完全對齊 |
| 資源與延遲 | `src/ssd/NVM_PHY_ONFI_NVDDR2.cpp` 已有channel busy、command transfer、die execution與事件 | 不再把既有等待重複加到tR上 |
| transaction統計 | `src/ssd/Host_Interface_Base.cpp` / Host_Interface_NVMe.cpp | 匯出execution、transfer、waiting、turnaround µs；不能相加當host-request critical path |

初始 `ssdconfig.xml` 為8 channels、4 chips/channel、2 dies/chip、2 planes/die、8KiB page、MLC、75µs page read等候選值。
這些不是已確認的GM7規格。容量與技術代際也不對齊真機，因此目前診斷結果只能顯示差距，不能直接作正式擬合驗收。

## 實驗設定與固定假設

- 單一flow、4KiB、random uniform、read=100%、QUEUE_DEPTH，QD1/2/4/8/16/32/64/128。
- 三個model seeds：321/322/323，每組100,000筆，完成全部請求才結束，共24次模擬。
- NVMe queue容量沿用設定；它不是生成器QD。腳本要求queue容量大於生成器QD。
- 停用read data cache路徑的工作負載模式：WRITE_CACHE（純讀），但保留現有CMT/mapping模型。
- 在每個獨立case複製XML，明確啟用模擬preconditioning，initial occupancy=75%。這是初始化假設，不代表GM7占用率或實際資料歷史。
- 從本地幾何與overprovisioning，按單flow分配計算可用LBA範圍，再選整數working-set百分比。實際約33.33GiB，與真機32GiB不完全相同；精確數值存在plan.json。
- 使用固定筆數，包含startup/drain，尚無量測窗warmup切分；不能假定100k筆已收斂。真機參考為60秒量測、前置10秒read warmup。
- 隨機數產生器與真機fio不同；相同seed不代表相同地址序列。此輪只對齊分布類型，不宣稱地址逐筆配對。

## 真機參考資料

預設從 `results/mqsim_long_20261112T153235Z_c53192bf/` 的原始fio JSON重新擷取。
先核對全部manifest SHA-256，再輸出每次量測的IOPS、fio latency、來源路徑與來源SHA。
所有快慢時段與重複均保留，不挑選只接近模擬的資料，也不把fio latency直接當NAND tR。

QD1/4/16/64標為fit candidates；2/8/32/128預留比較。因為我們已看過真機資料，這不是盲測或完全獨立驗證。
Burst、request-size與locality曲線是後續交叉檢查，不能以本輪QD曲線吻合就宣稱全面準確。

## 使用者執行指令

先建立少量功能模擬的輸入（只準備檔案，不執行MQSim）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_mqsim_calibration.sh --prepare --qds 1 32 --seeds 321 --requests 4096
```

把上一步印出的新目錄帶入以下指令，才會編譯並執行MQSim。這不會讀取SIFT檔或啟動fio：

```bash
bash microbench/run_mqsim_calibration.sh --run microbench/results/實際準備目錄
```

功能模擬成功後，準備24個診斷cases並執行：

```bash
bash microbench/run_mqsim_calibration.sh --prepare
bash microbench/run_mqsim_calibration.sh --run microbench/results/上一步印出的目錄
```

每case預設wall-time timeout3600秒；已完成4096筆短測，但100k筆整輪速度尚未量過，不承諾總耗時。失敗保留INCOMPLETE、stdout/stderr，不假造未完成結果。
已執行的目錄拒絕覆寫；修改source後需重新prepare，來源hash不符會拒絕執行。
`--run`會用make強制重新編譯，保存binary SHA、執行參數、各case XML、原始結果及完成manifest。

## 可選：參數敏感度（先看基準，再執行）

```bash
bash microbench/run_mqsim_calibration.sh --prepare --sensitivity
```

這會準備7個候選×8QD×3seeds=168次，仍不執行模擬：

| 變動 | 對應模型行為 | 目前證據狀態 |
|---|---|---|
| reference | 現有設定＋明列初始化 | 未校準候選 |
| tR ±20% | NAND array讀取服務時間 | 敏感度幅度由實驗者指定，非量測範圍 |
| channel rate ±20% | NAND channel資料傳輸時間／可用頻寬 | 同上；不改PCIe頻寬 |
| CMT容量 ×0.5／×2 | mapping table cache容量 | 同上；不能等同真機HMB大小或命中率 |

每個候選固定套用所有QD，不能隨QD換參數。沒有搜尋「最佳配置」或加入QD-specific correction。
必須先看敏感度是否符合機制預期，再找硬體證據限制候選範圍；沒有硬體證據就保留假設，不宣布實際SSD參數。

## 輸出與下一階段

- `plan.json`：source hashes、input hashes、工作範圍、候選變動、初始化假設。
- `real_per_run.csv`：直接從真機原始JSON匯出，保留全部重複。
- 每case原始XML、stdout/stderr、命令；執行binary與hash。
- `analysis/sim_per_run.csv`：模型host/device latency、transaction execution/transfer/waiting。
- `analysis/trend_per_run.csv`：正規化吞吐／延遲、相鄰QD增益與斜率，是主要比較結果。
- `analysis/comparison.csv`：保留絕對數值作追溯；文字判讀及執行紀錄統一寫在本文件。

下一階段需解決working set精確對齊、初始化與warmup邊界、GM7硬體結構證據，再檢查較長請求數的穩定性。
若外部曲線仍無法同時解釋，報告模型缺口，而不是把OS成本灌進tR或亂改channel數。
待模型可用範圍確立，再做NAND transaction一次排入／分批釋出的受控研究。

## 第一輪功能短測人工判讀（合併歷史紀錄）

狀態：兩組執行完整通過；模型尚未校準。
18個result manifest檔案hash核對通過；QD1及QD32各完成4096筆4KiB read，完成讀取bytes與預定數量吻合，無host write。

| QD | MQSim IOPS | 真機IOPS中位數 [min,max] | 模擬host mean µs | 真機fio mean中位數 µs |
|---:|---:|---|---:|---:|
| 1 | 6183.6 | 5647.6 [2568.0,8393.9] | 161 | 176.8 |
| 32 | 111879.4 | 23929.9 [23856.0,24122.9] | 284 | 1337.0 |

QD1落在真機觀察範圍內不能當成已校準；真機本身變動很大。QD32的絕對吞吐比約4.68、延遲比約21.2%，僅保留作歷史背景，不以逼近這些絕對值為校準目標。
趨勢上，模擬QD1→32吞吐增加約18.09倍、延遲增加約1.76倍；真機應用repeat內正規化分布比較，見下方自動紀錄。
這不證明真機只有25k上限，也不能據此直接把tR乘4.68或減少channel數。尚有模型幾何、mapping、狀態、host path與量測邊界等不同。

### 已有延遲機制

| QD | transaction execution µs | transfer µs | waiting µs | turnaround µs |
|---:|---:|---:|---:|---:|
| 1 | 75 | 12 | 72 | 160 |
| 32 | 75 | 12 | 195 | 282 |

各項輸出獨立取整至µs，因此相加可差1µs。這是transaction統計，不是host request各階段互斥分解。

QD1的4096次CMT read查詢中，有3881次miss（約94.75%）、215次hit；模型發出3881次mapping read，共7977次flash read command。
QD32同樣記錄3881次CMT read miss，但mapping read command為3867；miss數與發出command數並不一對一，不能自行補齊或假定相等。
這支持mapping dependency是需要檢查的候選等待來源；目前沒有事件級資料可以把所有waiting逐筆歸因。
兩組Total_GC_Executions均為0，所以不能用這兩組結果推論GC造成了延遲。

### Pipeline利用率欄位的限制

`src/nvm_chip/flash_memory/Flash_Chip.cpp` 的輸出為：

```cpp
Fraction_of_Time_Idle =
    (Simulator->Time() - STAT_totalOverlappedXferExecTime - STAT_totalXferTime)
    / double(Simulator->Time());
```

它沒有直接扣除STAT_totalExecTime。現有execution、transfer、overlap、idle數值不能當作互斥狀態分割後畫成百分比餅圖；QD32相加可超過1。
在追查各counter更新與die交錯語意前，不以這個idle欄位證明pipeline underfill，也不草率修改成1−execution−transfer。
本次沒有修改模擬器行為或歷史輸出。

### 下一步

1. （已完成）執行24組基準，取得完整QD曲線與三個seed；後續判讀見下方基準分析。
2. 觀察mapping miss、transaction waiting與曲線轉折是否一起改變；必要時增加事件級記錄。
3. 在正式擬合前解決32GiB工作範圍精確對齊、初始化與讀取暖機語意。
4. 再決定有證據支持的參數範圍與敏感度實驗。候選不能每個QD各換一組，也不能用任意延遲補償真機慢速狀態。

4096筆僅為功能短測。模擬時間可由N/IOPS估算：QD1約0.662秒、QD32約0.0366秒；它們不是長時間穩態。真機參考含60秒量測窗，仍有時間範圍與邊界差異。

[原始模擬彙整](results/mqsim_calibration_initial_pilot/analysis/sim_per_run.csv) · [絕對數值對照](results/mqsim_calibration_initial_pilot/analysis/comparison.csv)

## 完整基準執行狀態

`results/mqsim_calibration_initial_baseline`：8個QD×3個seed，各100,000筆，共24組。
已確認 COMPLETE，24組皆完成；詳見下方基準結果判讀。

<!-- calibration-run:a35593aaee523ba3:begin -->
## 執行紀錄：mqsim_calibration_initial_pilot

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_initial_pilot`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 32 | 18.093 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6183.6 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 32 | 111879.4 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.
<!-- calibration-run:a35593aaee523ba3:end -->

<!-- calibration-run:c19c7ccbe071d9f2:begin -->
## 執行紀錄：mqsim_calibration_initial_baseline

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_initial_baseline`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 321 | 4 | 3.774 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 321 | 8 | 7.010 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 321 | 16 | 12.119 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 321 | 32 | 18.182 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 321 | 64 | 22.403 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 321 | 128 | 24.275 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 322 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 322 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 322 | 4 | 3.772 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 322 | 8 | 7.013 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 322 | 16 | 12.110 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 322 | 32 | 18.215 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 322 | 64 | 22.408 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 322 | 128 | 24.299 | 4.410 [2.964, 9.697] | 5.280 | 29.057 [13.208, 43.250] |
| reference | 323 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 323 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.025 | 2.497 [1.126, 3.797] |
| reference | 323 | 4 | 3.777 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 323 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 323 | 16 | 12.115 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 323 | 32 | 18.177 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 323 | 64 | 22.336 | 6.860 [2.958, 9.665] | 2.870 | 9.339 [6.625, 21.670] |
| reference | 323 | 128 | 24.257 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6188.9 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 2 | 12115.7 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 321 | 4 | 23353.8 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 321 | 8 | 43380.9 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 321 | 16 | 75004.5 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 321 | 32 | 112524.8 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 321 | 64 | 138650.4 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 321 | 128 | 150236.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 322 | 1 | 6183.2 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 322 | 2 | 12103.9 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 322 | 4 | 23325.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 322 | 8 | 43361.1 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 322 | 16 | 74878.1 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 322 | 32 | 112629.9 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 322 | 64 | 138555.5 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 322 | 128 | 150248.5 | 24900.9 [24883.5, 24906.6] | 850.0 | 850.0 | 5140.0 |
| reference | 323 | 1 | 6188.7 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 323 | 2 | 12118.9 | 4524.0 [4426.6, 4560.8] | 165.0 | 165.0 | 441.6 |
| reference | 323 | 4 | 23373.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 323 | 8 | 43391.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 323 | 16 | 74974.1 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 323 | 32 | 112490.9 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 323 | 64 | 138229.0 | 24831.5 [24819.4, 38743.4] | 462.0 | 462.0 | 2577.1 |
| reference | 323 | 128 | 150119.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_initial_baseline/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_initial_baseline/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:c19c7ccbe071d9f2:end -->


## 完整基準結果判讀：mqsim_calibration_initial_baseline

### 完整性與比較方法

已核對 result_manifest.json 所列150個檔案的SHA-256、輸入雜湊，以及24組XML的生成數與完成讀取bytes。每組100,000筆4KiB讀取，合計2,400,000筆；COMPLETE存在且無INCOMPLETE。此次只分析既有結果，未啟動新實驗、未修改模擬器或調整物理參數。

以下模擬增益先在各seed內計算，再取三個seed中位數；真機先在各repeat內計算，再取三次中位數。不同QD並非同時或固定SSD狀態下量測，repeat配對不代表已控制快慢狀態。範圍為觀測min/max，不是信賴區間。

| 比較區間 | 模擬吞吐倍數 | 真機吞吐倍數中位數 | 模擬平均延遲倍數 | 真機平均延遲倍數中位數 |
|---|---:|---:|---:|---:|
| QD1→32 | 18.182 | 4.224 | 1.764 | 7.583 |
| QD1→128 | 24.275 | 4.410 | 5.286 | 29.057 |
| QD32→128 | 1.335 | 1.041 | 2.996 | 3.845 |

### 最可信的差距：高QD吞吐仍有過多增益

QD32→128時，真機各repeat吞吐只增加3.15–4.40%，延遲增加3.832–3.878倍；模型三個seed吞吐增加33.40–33.51%，延遲增加2.993–2.996倍。這個比較避開波動很大的QD1，也不依賴異常偏快的QD64，較適合作為本輪的趨勢約束。

模擬相鄰QD翻倍的吞吐增益依序約為1.958、1.928、1.858、1.728、1.500、1.230、1.084倍。模型有逐步趨平，但QD32後仍保有比真機這些觀測窗口更大的增益。現有真機QD32與128支持「高QD近似平台」；QD64仍出現一次快速量測，因此不能宣稱整台SSD永遠在同一平台，也不能精確鎖定唯一轉折QD。

這不是單純將全部IOPS乘上一個常數就能消除的差異。固定QD、持續補入的工作負載中，平均延遲與吞吐受在途請求數約束，兩項趨勢並非完全獨立的證據；外部趨平也不證明NAND pipeline已填滿。

### 低QD與隨機種子的限制

真機QD1為2,568–8,394 IOPS；QD4、16、64也有較快量測。真機QD1→2正規化增益中位數甚至只有0.801，不能解讀為SSD增加QD必然降低吞吐。這條曲線混入時間／狀態差異，不應把每個鋸齒都擬合進靜態參數。

各QD的模擬三seed IOPS最大／最小差異僅約0.07–0.31%。這表示本設定對地址seed相對穩定，不表示已重現真機的時變行為；繼續增加地址seed不能替代對真機快慢狀態的解釋。

### 模型內的線索，不是已證實的真機機制

現有transaction平均execution維持75µs、transfer維持12µs；waiting從QD1約72µs升至QD32約194–195µs、QD128約761–762µs。模型確實產生隨負載增加的等待，並非只計算tR加transfer。但這些聚合counter不能定位等待發生於channel、die、mapping依賴或其他資源，也不能相加當成host-request的critical path。先前idle counter語意問題仍未解決，不能以它判定pipeline填滿程度。

100,000筆對應的模擬時窗約為QD1的16.2秒至QD128的0.67秒，仍包含startup/drain。相較4096筆pilot，QD32吞吐只變動約0.6%，可作短測一致性線索，不能據此宣稱所有QD、內部狀態與真機長期平均均已收斂。

### 下一步校準決策

1. 將高QD增益（尤其QD32→128）、局部斜率與轉折區域列為主要約束；低QD正規化保留所有repeat與範圍，不強迫重現鋸齒。不能宣稱已重現長期平均，現有真機僅三次窗口。
2. 先釐清模型mapping等待與channel/die等待的counter及事件邊界，並處理working set精確對齊與量測暖機。現有33.33GiB對32GiB、初始化與量測窗差異仍須明列。
3. 再使用已定義的tR、channel transfer與CMT單因子敏感度，檢查哪些機制改變曲線形狀；±20%或容量倍率只是診斷擾動，不是GM7硬體規格。未知拓撲不能為了配曲線任意減少channel/die數。
4. 候選參數需同時通過其他request size、地址模式與working-set測試的趨勢檢查，再評估burst問題。這輪尚未選參數，也尚不能對內部NAND分批或burst效益作結論。

資料：[趨勢逐次CSV](results/mqsim_calibration_initial_baseline/analysis/trend_per_run.csv)、[模擬逐次CSV](results/mqsim_calibration_initial_baseline/analysis/sim_per_run.csv)、[真機逐次CSV](results/mqsim_calibration_initial_baseline/real_per_run.csv)。

<!-- calibration-run:bb39a1677ec5c61f:begin -->
## 執行紀錄：mqsim_calibration_sensitivity_round1

狀態：已準備 35 個cases，尚未執行。
輸入與來源：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_sensitivity_round1`。
目標：QD吞吐增益、延遲成長及曲線轉折；絕對數值僅供追溯。
<!-- calibration-run:bb39a1677ec5c61f:end -->


## 下一輪：單因子敏感度診斷（已準備，尚未執行）

目標是找出哪些既有硬體機制會改變曲線形狀，不是選出最接近絕對IOPS的設定。本輪不是GM7硬體參數估計，也不是校準完成驗收。前輪三個seed差異很小，因此先用seed321篩選機制，候選再以多seed及其他工作負載驗證。

設定：QD1、8、32、64、128；每組100,000筆4KiB random read；7種設定×5個QD＝35組。保留reference重跑作本輪控制，其他工作負載、初始化、拓撲皆固定。原始輸入存於 `results/mqsim_calibration_sensitivity_round1`。

| 設定 | 變更 | 對應機制／限制 |
|---|---|---|
| reference | 現有參數 | 本輪控制組 |
| tr_minus20 / tr_plus20 | page read latency 75µs→60／90µs | SSD_Device.cpp將頁面類別讀取延遲傳給Flash_Chip；改變NAND讀取服務時間，不加入任意host延遲 |
| channel_slower / channel_faster | Channel_Transfer_Rate 333→250／500 | SSD_Device.cpp:97的整數換算使傳入channel的兩個時序參數由6ns→8／4ns；不是宣稱實際傳輸時間精確按相同比例變動 |
| cmt_half / cmt_double | CMT 2MiB→1／4MiB | Address_Mapping_Unit_Page_Level.cpp:334將bytes除以entry大小換算mapping容量；影響mapping命中與額外讀取，不代表SSD資料快取或HMB容量 |

**修正原先channel ±20%診斷計畫：** 程式採 `floor(1000 / rate) * 2` ns。333與原計畫266都得到6ns，因而原本的減速組可能完全沒有改變channel時序。現在選用相鄰可表示的時序區間，避免把無效擾動誤判為不敏感。只修改實驗輸入生成器，未修改模擬器時序公式；整數量化是模型限制，不是硬體量測證據。

判讀順序：

1. 核對reference與前輪seed321結果是否一致；先確認完成數、manifest與時序有效值。
2. 比較各variant的IOPS(128)/IOPS(32)、latency(128)/latency(32)，以及32→64、64→128局部增益；與同輪reference比較後，再對照真機觀測範圍。
3. 同看QD1→8→32成長，避免只改善高QD卻破壞低QD形狀。稀疏五點只供篩選，不能精確定位轉折。
4. 查原始XML的CMT命中／miss、mapping read與transaction等待；聚合統計只是機制線索，不能用來分配完整host latency。
5. 若參數主要只縮放絕對值、對正規化形狀幾乎無影響，就不能靠它解決趨勢差距；若有方向性影響，也需獨立硬體依據和跨工作負載驗證，不能立即認定GM7使用該參數。

目前working set約33.33GiB對真機32GiB、暖機邊界與真機時變狀態仍未解決；本輪只作模型內控制實驗，不把結果當正式擬合驗收。既有分析會把各variant圖與結果更新到本文件，PNG／SVG保留在該輪analysis/figures。

已離線核對35組輸入、雜湊與變更欄位隔離，沒有啟動模擬。由使用者執行：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_mqsim_calibration.sh --run microbench/results/mqsim_calibration_sensitivity_round1
```


## 校準定位：既有真機證據與程式路徑審查

本節重新核對 `mqsim_suite_sqsmu6q7` 的486個manifest檔案，從原始fio JSON以既有extract函式重算38組非arrival結果（另8組arrival不混入此比較）。沒有執行新實驗。該suite每組30秒量測、10秒ramp、兩個repeat；不是長期平均的充分估計。大小與job數不同時，不宣稱地址序列完全配對。

![既有真機實驗對瓶頸假設的約束](results/mqsim_calibration_initial_baseline/analysis/figures/existing_ssd_evidence.png)

### 證據與可排除的簡單解釋

| 比較 | 重新計算的結果 | 判讀與限制 |
|---|---|---|
| 4KiB/QD32，sequential vs random | 256MiB：seq 223.8–237.6k、random 24.78–24.81k IOPS；32GiB：seq 211.8–223.6k、random 24.01–24.05k | 地址模式有大影響，單一固定host IOPS上限無法解釋所有工作負載。不能因此唯一定位mapping；預取、合併、實體配置與NAND平行度均可能相關 |
| 4KiB random/QD32，256MiB vs32GiB | 兩次repeat小範圍增益約1.033、1.030倍 | 128倍範圍差沒有消除高QD平台，削弱「縮小到256MiB就能解決」的簡單cache假設。不能排除mapping cache：尚不知道容量門檻、歷史與命中率 |
| 總QD32，1job×32 vs8jobs×4 | 24,010.6→24,002.3及23,960.9→23,501.9 IOPS | 這兩次增加job未改善，削弱單一fio工作者供應不足的解釋；未量測實際NVMe queue分配，不能排除所有OS因素 |
| random/QD32，4/64/1024KiB | 約93.8–93.9／124.7–1087.7／1779.6–2399.5MiB/s | 非固定94MiB/s傳輸上限；64KiB差異達8.7倍，不能用兩次不同狀態數據直接擬合channel bandwidth |
| QD1 request size | 4KiB同suite不同組約95–386µs；1024KiB約530–8124µs | 固定延遲＋bytes/bandwidth的回歸會混入狀態差異，現階段不從這些點推導真實tR |

4KiB random/QD32的fio slat約3µs，clat約1.3ms；多數時間在提交後，但clat仍包含OS／裝置路徑，不等於NAND時間。小slat不證明host無瓶頸。較大request可能被OS拆分，fio QD也不等於NAND transaction數。

### 程式審查：現有waiting不是單一資源排隊時間

- `src/ssd/Host_Interface_Base.cpp:107`：read transaction waiting以 `完成時間 - Issue_time - execution - transfer` 計算，是殘餘量，不是獨立量到的channel等待。因此不能把先前QD128約762µs直接稱作NAND contention。
- `src/ssd/Address_Mapping_Unit_Page_Level.cpp:567`、1698附近：存在等待映射的read集合與mapping完成後釋放路徑。這是第一個應補測的時間邊界：資料transaction何時因映射不可用而等待、何時可進入TSU。
- 現行設定為 `PRIORITY_OUT_OF_ORDER`，對應 `src/ssd/TSU_Priority_OutOfOrder.cpp`，不是其他TSU實作。278／292附近檢查channel狀態，340附近在read服務中選擇mapping queue；應追蹤mapping/user兩類工作如何共享服務機會。
- `src/ssd/Flash_Transaction_Queue.cpp`已在enqueue/dequeue做probe；`src/ssd/Queue_Probe.cpp:172`輸出的等待已轉為整數µs。可以先利用現有queue輸出，但它只涵蓋該queue停留時間，不包含全部映射等待或下游傳輸等待。

### 下一步已鎖定的工作，而非無限制掃描

**優先修改觀測位置：Address_Mapping_Unit_Page_Level → 現行TSU → NVM_PHY_ONFI_NVDDR2。** 先補上transaction建立、mapping阻塞起訖、TSU入列／派送、command／data transfer及die execution的事件關聯；以同一request ID串起多個transaction。要驗證每個時間戳的語意、重入與重疊，不以各transaction平均值相加冒充request critical path。核心排程行為暫不修改。

最有判別力的下一個模型實驗是**地址模式×working set**，固定4KiB、QD32，並取得mapping命中、mapping讀取數、mapping阻塞與TSU等待：

- 若模型縮小working set後大量改善，而真機只約3%，先查模型mapping容量／工作範圍／初始化與mapping讀取機制；不能只把CMT調到曲線吻合。
- 若mapping相關等待變化很小，但模型無法重現sequential/random的大差距，將優先檢查地址配置、請求合併、預取及資源排程，仍需額外證據區分它們。
- 若模型能重現地址模式與working-set趨勢，卻仍在高QD有過大增益，再針對有效平行度與資源服務重疊定位。

這組實驗尚不能直接交付執行：現有生成器固定RANDOM_UNIFORM及整數working-set百分比，256MiB在現有容量下不足1%，不能誠實聲稱已與真機對齊。需先審查／實作精確地址範圍、相應地址模式與觀測，並驗證初始化和量測邊界。先前35組敏感度保留為輔助，不作此步驟的先決條件。此輪沒有為吻合數據而修改tR、拓撲或加入人造延遲。


## 原始碼修改：精確工作範圍（已編譯，尚未執行模擬驗證）

已修改 `src/exec/IO_Flow_Parameter_Set.{h,cpp}`、`src/exec/Host_System.cpp`、`src/host/IO_Flow_Synthetic.{h,cpp}`。新增可選XML欄位 `Working_Set_Sectors`，單位為512-byte sector；0或未指定時維持既有百分比地址生成路徑。指定非零時覆蓋百分比所決定的工作範圍，範圍不得超出flow配置容量。

- 256MiB：`<Working_Set_Sectors>524288</Working_Set_Sectors>`。
- 32GiB：`<Working_Set_Sectors>67108864</Working_Set_Sectors>`。
- 精確模式目前只接受FIXED request size、alignment等於request size、起點與範圍均對齊，地址模式為RANDOM_UNIFORM或STREAMING。其他組合拒絕，避免默默產生不同分布。
- RANDOM_UNIFORM以完整request block為均勻取樣單位，可取到最後完整區塊；STREAMING從範圍起點開始、最後一筆後回捲。這是明確定義的主機工作負載語意，不是新增SSD硬體機制。
- 工作範圍比例也更新供既有初始化統計使用；preconditioning與暖機的等價性尚未驗證，不能宣稱已與fio完全對齊。

驗證：`make -B -j2`完整重編譯成功；尚未執行MQSim或fio，不能把編譯通過當成執行正確。下一步還需要可由使用者執行的邊界短測、legacy模式回歸，以及mapping／TSU／PHY事件觀測。此刻尚未完成整套新實驗，不應開始正式參數擬合。

**舊準備目錄注意：** 原始碼雜湊已改變，因此之前準備但未執行的 `mqsim_calibration_sensitivity_round1` 不再適用目前來源；原執行器會拒絕。不可修改舊manifest繞過檢查，後續須建立新輸入目錄。已完成的歷史輸出與binary snapshot保持原狀。


## Mapping／TSU觀測實作與下一個驗證入口

已修改MQSim原始碼，增加不參與排程決策的觀測欄位：

- `NVM_Transaction.h`：transaction各自保存mapping集合進入時間、累計停留ns、episodes，以及TSU累計停留ns／departures。
- `Address_Mapping_Unit_Page_Level.cpp`：進入 `Waiting_unmapped_read_transactions` 時開始，mapping完成回呼取出該entry並轉交後續流程前結束。只測這個集合中的停留；不包含全部FTL查詢成本、其他GC barrier或cache等待。
- `Queue_Probe.cpp`：利用既有enqueue/dequeue時間戳，將queue停留累加到transaction。現有呼叫點來自Flash_Transaction_Queue。若未來增加其他使用者需重新審查命名；queue停留也不能區分channel忙與chip忙。
- `Host_Interface_Base`與NVMe XML：在現有cache→host transaction完成通知累計，輸出Observed_Read_Completed、Mapping_Blocked_Count、Mapping_Episodes、TSU_Departures、Open_Mapping_At_Completion，以及Mapping_Wait_Sum_NS／TSU_Queue_Sum_NS。

計數母體是既有完成通知所見read transactions，不是所有mapping／GC transactions，也不是host request數的通用替代。以sum_ns／completed算包含零等待的平均；mapping條件平均可另以blocked_count作分母。這兩個觀測是既有residual waiting的部分來源，不可再加在原waiting上。尚未實作PHY逐階段時間戳、request ID關聯或完整critical path；不能宣稱已取得完整SSD延遲分解。

驗證狀態：完整 `make -B -j2` 通過，bash語法、Python語法與diff空白檢查通過；沒有執行MQSim或fio。功能回歸必須由使用者執行以下腳本：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_observation_check.sh
```

六組：兩組legacy（QD1、32）應與已封存pilot的IOPS及既有latency指標完全一致；另四組是256MiB／32GiB × random／streaming，QD32，各4096筆。沿用pilot初始化與裝置設定，固定seed321。驗證完成bytes、read觀測數、mapping已關閉、TSU departure數；差異即停止並保留INCOMPLETE。每次建立新目錄，保存binary、來源雜湊、輸入、stdout/stderr、XML及結果manifest，紀錄回寫本文件。

這六組只檢查功能與觀測不改變legacy數值，不能證明均勻分布、範圍邊界、暖機或長期收斂；特別是4096筆streaming不會覆蓋256MiB。通過後仍需LBA邊界驗證與正式量測窗設計，才用來判定mapping／排程的校準方向。依先前pilot每組約3分鐘，粗估六組約20分鐘，但精確範圍可能改變初始化成本，尚無本次實測時間。

<!-- calibration-run:7e8078b0b9e3ba6b:begin -->
## 觀測驗證：mqsim_observation_check_df32f044

狀態：INCOMPLETE，Unexpected transaction population; inspect raw output；保留原始輸出於 `/home/emmanuel/projects/MQSim/microbench/results/mqsim_observation_check_df32f044`。
<!-- calibration-run:7e8078b0b9e3ba6b:end -->


## 觀測短測失敗定位與修正：mqsim_observation_check_df32f044

本輪不是六組完成，而是在第二組legacy_qd32完成模擬後被檢查器停止。14個封存檔案雜湊通過。離線重新比對兩組XML：QD1、QD32的IOPS、device/end-to-end latency與原有transaction turnaround/execution/transfer/waiting皆與歷史pilot完全一致。失敗在新觀測分類，不是既有性能指標改變。

QD1完成4096筆、mapping blocked 3881筆、queue departures 4096；QD32完成4096筆、mapping blocked 3881筆，但queue departures達5206，因此原本「TSU departures=完成數」的檢查停止。四組精確範圍案例尚未執行。

根因：前次對Queue_Probe使用者的審查不完整。`NVM_PHY_ONFI_NVDDR2.cpp`也建立Flash_Transaction_Queue：WaitingReadTX、WaitingGCRead_TX、WaitingMappingRead_TX。約509–524行將讀取執行完成但無法立即傳出的transaction入列，約466–475行在channel可服務時取出。舊觀測把TSU排隊和PHY資料傳出排隊混加，不能把舊TSU_Queue_Sum_NS全解釋為排程器等待。5206−4096=1110是額外queue departure的差額，單憑舊彙總不能追溯每筆歸屬，不能事後合成拆分數據。

已修正：PHY三個queue明確標記為data-out queue，Queue_Probe依分類分別累積；NVMe XML新增Observed_Read_PHY_Data_Out_Queue_Sum_NS及Observed_Read_PHY_Data_Out_Departures。原TSU檢查維持，並要求本純讀短測PHY出列數介於0與完成數之間。PHY欄位只量等待佇列停留，不包含實際data transfer，也不代表所有PHY等待。

保留舊INCOMPLETE與所有原始資料。再次執行 `bash microbench/run_observation_check.sh` 會建立新目錄，完整重跑六組，不覆蓋舊結果。新增分類的執行正確性仍待使用者重跑確認；未調整任何NAND時間、排程優先權或服務事件。

<!-- calibration-run:cb836ed0eb3f71b5:begin -->
## 觀測驗證：mqsim_observation_check_322228e8

狀態：六組完成，legacy數值回歸與計數檢查通過；不是正式校準。

原始輸出：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_observation_check_322228e8`。

| Case | Completed reads | Mapping mean ns / all reads | TSU mean ns / all reads | PHY data-out queue mean ns / all reads |
|---|---:|---:|---:|---:|
| legacy_qd1 | 4096 | 72812.335 | 0.000 | 0.000 |
| legacy_qd32 | 4096 | 108163.238 | 84617.663 | 2227.959 |
| RANDOM_UNIFORM_256MiB | 4096 | 24381.834 | 74285.145 | 3966.456 |
| STREAMING_256MiB | 4096 | 98292.288 | 55307.285 | 4474.595 |
| RANDOM_UNIFORM_32768MiB | 4096 | 107556.703 | 85456.946 | 2226.089 |
| STREAMING_32768MiB | 4096 | 98292.288 | 55307.285 | 4474.595 |

僅4096筆功能驗證：不保證工作範圍已走完／cache已暖機，不以此表推論容量或正式趨勢；尚無逐筆LBA邊界稽核。
<!-- calibration-run:cb836ed0eb3f71b5:end -->


## 六組觀測驗證判讀：322228e8（2026-11-14）

重新驗證result_manifest列出的35個檔案，全數通過；COMPLETE存在、無INCOMPLETE。從六組原始XML重新核對：每組4096筆讀取完成、TSU出列4096次、完成時mapping active為0。legacy QD1與QD32所有既有解析性能指標與歷史pilot完全一致。這證明本輪回歸案例未見觀測改變輸出，不代表所有工作負載都已驗證。

![Mapping、TSU與PHY等待觀測](results/mqsim_observation_check_322228e8/analysis/wait_observations.png)

| Case | IOPS | Host end-to-end mean µs | Mapping blocked / 4096 | PHY data-out queue departures |
|---|---:|---:|---:|---:|
| legacy QD1 | 6183.6 | 161 | 3881 | 0 |
| legacy QD32 | 111879.4 | 284 | 3881 | 1110 |
| random 256MiB | 164841.9 | 192 | 991 | 1354 |
| sequential 256MiB | 126750.3 | 251 | 4005 | 1513 |
| random 32GiB | 111813.7 | 284 | 3857 | 1124 |
| sequential 32GiB | 126750.3 | 251 | 4005 | 1513 |

### 已確認的觀測與尚不能下的結論

1. legacy QD32現在分成TSU出列4096次、PHY等待出列1110次，對應先前誤合併的5206次。平均mapping等待108.16µs、TSU排隊84.62µs、PHY傳出排隊2.23µs。這些是同一完成read transaction母體的階段觀測；不能加到原residual waiting上，也不能當所有host request的critical path。
2. 模型random 256MiB／32GiB吞吐比約1.474，而真機既有兩次約1.030–1.033。模型mapping blocked比例由94.17%降至24.19%，指向mapping／初始化值得優先查。但blocked比例是集合停留事件，不等於CMT miss rate；短測與真機30秒＋10秒ramp的狀態不同，尚不能據此調CMT容量。
3. 模型32GiB sequential／random比約1.134，真機同範圍約8.82–9.29。此差距目前混有短測、初始化與地址歷史差異，不能立即加入預取或硬改scheduler來追上真機。
4. 兩個sequential case均只送4096×4KiB＝16MiB，未達256MiB的第一圈，更未涵蓋32GiB；相同結果符合生成設定，不是證明working-set無影響。sequential有4005筆進入mapping等待，可能包含共等尚未完成的mapping讀取；不得解讀為4005次獨立mapping NAND read。

### 下一個具體校準步驟

本次先驗證了觀測工具，下一步應以Address_Mapping_Unit_Page_Level及初始化為優先查核對象：匯出CMT查詢／miss、真正mapping read命令、共享等待筆數，對照相同地址範圍下各量測窗口的變化。先完成地址邊界／回捲檢查及暖機與量測窗分離，才能區分「短測初始化」與「mapping模型本身」；不需要現在再重跑相同六組。先前的35組參數掃描仍非先決條件，尚無已校準的硬體參數。

本次僅分析既有輸出並產圖；沒有啟動新模擬、沒有改變硬體或排程參數。


## 分段量測腳本與既有log擴充（2026-11-14）

先審查現有IO_Flow_Base response-time log：已有模擬時間與短期平均device/end-to-end latency，因此不新增重複事件log。沿用 `workload.IO_Flow.No_0.log`，保留原前三欄，新增精確time_ns、累計completed/generated、latency/device總和ns、read bytes、全裝置read CMT queries/misses與mapping讀取計數。mapping讀取計數來自現有Stats::Total_flash_reads_for_mapping，是FTL建立／提交路徑的計數，不是精確NAND發出或完成時間。

checkpoint依既有completion回呼觸發：越過logging period後寫入，另補初始與固定請求數最後完成的snapshot；不新增模擬排程事件。修正short-term delay分母的空窗口檢查。log未啟用時不輸出，checkpoint不是逐筆request/NAND事件軌跡；目前不宣稱已量到每筆command的issue/completion。NVMe的mapping／TSU／PHY觀測仍在最後XML，尚未進入分段log。

新增 `mqsim_window_probe.py` 與 `run_window_probe.sh`。四組：QD32、4KiB、seed321，256MiB／32GiB × RANDOM_UNIFORM／STREAMING，使用精確sector範圍；初始化沿用pilot。每10ms模擬時間附近的完成事件記錄一次checkpoint。後處理以累計差值重算窗口IOPS、完成請求平均latency、CMT queries/misses與mapping讀取數，產生windows.csv與PNG/SVG並更新本文件。

預先選定warmup切點1模擬秒，不重置快取／模型狀態。跨切點窗口與generated達上限的最後區間不納入後段摘要；後段仍須檢查趨勢是否漂移，1秒不等於已證實穩態。窗口latency採「在此窗口完成」母體，可能包含窗口前已送入的請求；mapping計數與host completion不是一一對應。單flow下才解讀全裝置mapping/CMT計數。

先由使用者執行功能短測（每組65,536筆＝256MiB）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --smoke
```

短測可能不到1模擬秒；後段摘要顯示N/A屬合理結果，不以此測穩態。四組用於確認log欄位、完整snapshot與分析流程。正式模式每組16,777,216筆＝64GiB，足以讓STREAMING走過32GiB兩圈，但不保證暖機充分；執行時間尚無實測，先檢查短測再決定是否跑正式模式。每case timeout4小時。腳本不執行fio，不替使用者啟動實驗。

已完成C++完整編譯、Python與bash語法及diff檢查；新腳本的真實模擬執行仍待使用者驗證。每次建立新目錄，保存binary、來源hash、腳本、輸入、log、XML、結果manifest。原始輸出完整時標記COMPLETE；分析另需成功才寫入結果表，不把COMPLETE單獨當成校準完成。

<!-- calibration-run:7e196bb5e4f6096d:begin -->
## 分段checkpoint實驗：mqsim_windows_5cd221f4

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_256MiB | 0 | N/A | N/A |
| RANDOM_UNIFORM_32768MiB | 0 | N/A | N/A |
| STREAMING_256MiB | 0 | N/A | N/A |
| STREAMING_32768MiB | 0 | N/A | N/A |

![分段趨勢](results/mqsim_windows_5cd221f4/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:7e196bb5e4f6096d:end -->


## Checkpoint短測與GM7公開規格審查（2026-11-14）

`mqsim_windows_5cd221f4`：28個manifest檔案重新校驗通過；四組各65,536筆完成。random 256MiB結束於0.366035模擬秒、random 32GiB為0.582306秒，兩組streaming皆0.515833秒。因此後段N/A是因全部在1秒warmup切點前結束，不是後處理漏資料；不能把切點事後降低來宣稱穩態。

| 模式 | 累計平均latency µs | CMT read misses | mapping read計數 |
|---|---:|---:|---:|
| random 256MiB | 178.62 | 7119 | 6443 |
| random 32GiB | 284.24 | 61847 | 61609 |
| streaming 256MiB | 251.81 | 63990 | 3420 |
| streaming 32GiB | 251.81 | 63990 | 3420 |

streaming大量miss並未等於同數量的mapping NAND讀取，支持追查共享等待／mapping讀取合併。這只是計數線索，不是已還原每筆相依關係。兩個streaming都僅讀256MiB，32GiB未走完，仍不作穩態容量對照。既有分段圖見上方本輪自動紀錄。

### 規格來源與可信範圍

本機唯讀sysfs確認：Predator SSD GM7 2TB、firmware GT11cb0c，PCI vendor 0x1dee、device 0x1602。不能僅由此判定NAND型號／die數。

- [Predator官方GM7規格書](https://www.predatorstorage.com/wp-content/uploads/2024/12/PredatorGM7Specifications.pdf)：PCIe4.0x4／NVMe2.0，HMB與SLC cache；這版2TB表列循序讀7200MB/s、隨機讀925K IOPS。官方並註明規格可能變更，這不是本機相同測試條件下的目標。
- [Maxio官方MAP1602公告](https://www.maxio-tech.com/news/11645/12956.html)：MAP1602為4通道、無外置DRAM主控，支援高速NAND介面。結合本機識別，4-channel是比目前8-channel更有公開依據的候選；仍不把產品族資訊等同完整本機BOM。
- 公開規格未充分約束本機每channel的chip／die／plane、page/block geometry、NAND代際、讀取時間與韌體mapping政策。不能把HMB大小直接當MQSim CMT容量，兩者存取路徑也不同。

### 參數調整決策

使用者已授權調整channel等設定，後續可建立4-channel候選，保留舊8-channel作控制。不可只把8改4就宣稱硬體對齊：現有其他幾何固定時容量與總die數會一起減半，會混入平行度及初始化影響。需明列容量／總die是否固定，未知幾何作候選假設，不選最吻合的一組當成規格。

另外現有channel timing換算為 `floor(1000 / Channel_Transfer_Rate) * 2` ns；直接填2400會得到0ns！因此不能將公開MT/s數值直接塞入目前介面。下一步要先審查資料傳輸時序的單位及整數精度，再選擇有非零、可驗證傳輸成本的實作；不能用0ns傳輸製造高吞吐。此次未改ssdconfig.xml，也沒有啟動新實驗。


## Channel傳輸精度修正與受控拓撲對照（2026-11-14）

原公式先把1000/rate轉整數再乘2，令高速設定的傳輸成本歸零；舊宏還會截掉不足兩個transfer unit的尾端。新增 `Channel_Transfer_Time.h`，以bytes與channel width計算完整DDR cycles，再用整數有理式對整筆burst做一次向上取整：`cycles=ceil(bytes/(2*width)); ns=ceil(cycles*2000/rate_MT_s)`。驗證零rate/width、乘法overflow；不對每個cycle單獨取整。事件引擎仍用ns，總burst量化誤差小於1ns。這是既有channel速率語意的精度修正，不是新增時脈引擎。

SSD_Device將設定速率傳給ONFI_Channel_NVDDR2；資料in/out共用修正，command/setup等其他時序不變。直接呼叫原channel constructor且未設定rate的路徑保留原時間API。舊333MT/s也會受精度修正影響：4KiB payload舊12288ns、新12301ns；500MT/s為8192ns；2400MT/s為1707ns。這是payload傳輸時間，不含command/setup，也不是NAND array read latency。不能再要求舊333設定的IOPS逐位完全一致，須使用修正後reference作控制。

`microbench/test_channel_transfer.cpp`測試已離線執行：檢查上述預期值、1／3byte尾端、零長度、零rate拒絕、bytes單調性、提高速率不增加時間。未啟動模擬。2400只是算術驗證，尚未套用到本機候選配置；不同ONFI代際的command/setup仍需另行證據。

### 下一個實驗：只改channel共享程度

`mqsim_window_probe.py`新增 `--profile reference|four_channel` 與 `--requests`：reference為8channel×4chip；four_channel為4channel×8chip，兩者其餘die／plane／block／page設定相同，總chip/die與模型容量不變。workload Channel_IDs與Chip_IDs同步更新。四通道的方向有MAP1602公開資訊支持，但8chip/channel是控制變因安排，不是GM7 BOM；目前仍為舊MLC幾何，不能稱已對齊2TB TLC硬體。

兩組均維持333MT/s、原NAND時序與CMT，只切換拓撲；每組四種地址／範圍案例、每case1,048,576筆＝4GiB，checkpoint分段方式相同。小範圍streaming走16圈，32GiB只走1/8圈，不能拿它宣稱大範圍已穩態。主要觀察後段是否持續漂移、channel共享是否改變mapping命令率與吞吐；若只改絕對數值卻無法解釋地址／範圍趨勢，不把它當校準成功。

使用者執行（共八個case，前一命令失敗就不要繼續）：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --profile reference --requests 1048576 &&
bash microbench/run_window_probe.sh --profile four_channel --requests 1048576
```

每次建立獨立結果目錄並自動分析／畫圖到本文件；結果仍由使用者執行後判讀。此步驟不修改根目錄ssdconfig.xml、不重寫舊輸出，不同時改NAND technology、CMT或速率來追曲線。

<!-- calibration-run:85eb14212be1162f:begin -->
## 分段checkpoint實驗：mqsim_windows_389bdf4d

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:85eb14212be1162f:end -->


## 8／4-channel對照中止：389bdf4d

重新校驗20個manifest檔案通過，但本輪為INCOMPLETE，只執行reference的前兩組。256MiB random完成1,048,576筆，模擬時間約5.491秒；32GiB random生成1,048,576筆、實際服務1,048,567筆，差9筆＝36,864bytes。XML Bytes_Transferred_Read=4,294,930,432，與預期4,294,967,296不同，檢查器正確拒絕。串接指令使用&&，因此四通道輪次未開始，不能提出8／4-channel比較結論。

![未完成案例的診斷時間序列](results/mqsim_windows_389bdf4d/diagnostics/incomplete_windows.png)

大範圍random在約0.01秒窗口仍約111k IOPS、283µs，但後段出現顯著慢速與波動，最後一個checkpoint在130.814秒，只記錄1,048,554筆完成；最終XML比它多13筆，因未達下一個period且未全數完成，沒有最後完成checkpoint。不得把缺少的最後窗口補成零或重寫COMPLETE。小範圍後段窗口約18–20萬IOPS，與大範圍明顯不同，但未完成案例不作正式穩態擬合。

這次問題不是四通道造成：它尚未執行。也不能先判定為真機類似的偶發慢速。`Engine.cpp`事件迴圈在event list為空或stop旗標時退出；目前輸出未明列此次退出原因，因此下一步應補退出狀態與未完成request所在層的診斷，檢查host outstanding、mapping待處理集合與TSU／PHY工作是否一致。尚無證據證明9筆的精確遺失位置，不用任意timeout、降低QD或縮短request數來掩蓋。

本次只分析既有資料並繪製標示INCOMPLETE的圖，保留全部原始檔案，未執行新實驗。先處理未排空的正確性問題，再恢復拓撲校準；目前不請使用者重跑相同長測。


## 未排空定位：退出與Host診斷

進一步從389bdf4d原始XML彙總user-read TSU：enqueue=dequeue=1,048,567，與completed一致；不是觀察到9筆user reads仍留在TSU。此聚合證據優先指向TSU之前的路徑，但無法單靠計數確定是host提交、裝置fetch或mapping入口。查程式未見Stop_simulation的呼叫點，仍以新增退出診斷確認，不猜測原因。

已新增不改排程的stderr診斷：Engine退出列印time_ns、pending_events、stop_flag；IO_Flow結果輸出列印generated/completed、host waiting數、software map entry數、SQ head/tail，以及所有非空host pending request的cid、LBA、arrival/enqueue時間。software map可能含空entry，故entry數不可直接當未完成數；逐筆只列非空項目。

既有response log補report時最終snapshot，未完成也保留最後完成數。成功案例可能與完成時snapshot同時間／同完成數，分析器按這兩欄去除重複端點；不補造缺失完成事件。完整編譯、Python語法與diff檢查已通過，尚未執行新模擬。

只由使用者重跑失敗案例，保留原1,048,576請求、seed與設定：

```bash
cd /home/emmanuel/projects/MQSim
bash microbench/run_window_probe.sh --profile reference --requests 1048576 --case RANDOM_UNIFORM_32768MiB
```

若仍少9筆，腳本預期再次標記INCOMPLETE；這是定位實驗，不以成功退出作唯一判準。查看該case stderr.txt中的ENGINE_EXIT、HOST_EXIT與HOST_PENDING，再決定是否需要裝置端fetch／mapping事件追蹤。本輪沒有放寬完成數檢查、沒有修改排程以掩蓋失敗，也不需要重跑其餘七個案例。

<!-- calibration-run:f81ccfb2cea46a6d:begin -->
## 分段checkpoint實驗：mqsim_windows_bc60f285

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:f81ccfb2cea46a6d:end -->


## 單案例重現bc60f285：事件已耗盡，host尚有9筆

本輪manifest重新校驗通過。stderr明列：ENGINE_EXIT time_ns=130815474131、pending_events=0、stop_flag=0；HOST_EXIT generated=1048576、completed=1048567、waiting=0、software_map_entries=9、SQ head=tail=16。九個非空HOST_PENDING：CID 0、18、31、7、17、26、1、16、28；最早arrival為28.616369347秒，最晚129.016265495秒。它們在不同時間累積，不是只在生成器停止後才出現。

因此確定本次是事件耗盡且host仍持有未完成請求，不是Python計算或編譯錯誤；completion驗證應保持失敗。host head=tail是host看到的指標，不足以證明裝置有收到所有SQ entries。尚不能斷言9筆一定留在host、一定卡mapping或一定由channel修正引起。

補充診斷（不改事件／排程）：Host_Interface_NVMe輸出DEVICE_EXIT，包含裝置SQ head/tail/informed、On_the_fly_requests、累計fetched reads／read transactions、waiting與completed-pending數、CQ head/tail；DEVICE_PENDING列出裝置待處理request的CID、LBA、剩餘transaction數與fetch時間。下次可將九筆CID與host清單對照，區分未被fetch與裝置內尚未完成。

仍使用同一條單案例指令重現；每次建立新目錄，不覆蓋bc60f285。若fetched小於generated且裝置無待處理，查NVMe SQ／doorbell／fetch；若裝置仍有pending，沿其transaction查mapping／排程。這是下一步判別條件，不是已確認根因。

<!-- calibration-run:fd7f7f09fa0df8e9:begin -->
## 分段checkpoint實驗：mqsim_windows_cbc14416

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:fd7f7f09fa0df8e9:end -->


## cbc14416：已排除未fetch，定位至裝置內transaction

本輪manifest校驗通過。DEVICE_EXIT fetched_reads=read_transactions=1,048,576，inflight=waiting=9，completed_pending=0；SQ head=tail=informed=16。九筆DEVICE_PENDING的CID與host九筆完全一致，每筆Transaction_list.size()=1。故前輪「未fetch」候選已被此次證據排除，這不是SQ尚有9筆未送達；尚不能僅由pending清單斷定mapping的精確狀態。

來源審查發現：Cached_Mapping_Table::Evict_one_slot無條件淘汰LRU尾端，沒有排除WAITING；mapping完成路徑以Is_slot_reserved_for_lpn_and_waiting決定是否釋放Waiting_unmapped_read_transactions。這形成值得驗證的失去喚醒風險，但目前沒有證據說這九筆必定經過WAITING淘汰。不能因為看見風險就宣稱已找到根因。

新增不改行為的精確診斷：CMT_EVICT_WAITING列出淘汰時刻、stream及LPA；退出時TRANSACTION_PENDING列出CID、LPA、physical_address_determined、mapping_active、mapping episodes／進入時刻、TSU與PHY departure數。可將未完成LPA與淘汰記錄配對；若沒有匹配，需排除此假設，不能任意修改CMT政策。

此前的兩次定位有實質區分：首先確認事件耗盡，接著確認裝置已fetch。尚未修復未排空問題，沒有把慢速結果當成硬體校準資料；不再請使用者跑整套8組比較。新增診斷仍僅需同一個random32GiB案例，保留seed與request數不變。

<!-- calibration-run:c42df9e28f2c677b:begin -->
## 分段checkpoint實驗：mqsim_windows_2c422ae8

狀態：INCOMPLETE；Not all planned reads completed, or unexpected writes
<!-- calibration-run:c42df9e28f2c677b:end -->


## 2c422ae8判讀與修正：GC barrier後的user transaction被刪除（2026-11-14）

本輪stderr的`CMT_EVICT_WAITING`為0次，「WAITING slot被淘汰而失去喚醒」的假設在此重現中不成立。九筆`TRANSACTION_PENDING`的LPA與所屬request的LBA對不上（例如cid 28：LBA 3295792應為LPA 205987，印出3114），其中兩筆為明顯垃圾值；即request的Transaction_list指向已釋放並被重用的記憶體。

根因（由程式碼確認）：`Address_Mapping_Unit_Page_Level::Remove_barrier_for_accessing_lpa`對等在GC barrier後的user transaction只呼叫本單元的`handle_transaction_serviced_signal_from_PHY`，該函式對非MAPPING來源直接return，隨後`delete`。data cache／host interface從未收到完成通知，所屬user request永遠留在裝置。這是上游既有行為，與channel精度修正及觀測欄位無關。

同一份XML顯示純讀工作負載執行了`Total_GC_Executions=2096`、`Issued_Flash_Erase_CMD=2153`、`Issued_Flash_Program_CMD=303102`。來源：讀取未寫入的LPA時`online_create_entry_for_reads`會配置實體頁並把CMT entry標為dirty；preconditioning又把空閒區塊留在GC門檻附近，因此配置即觸發GC。stdout有「initial occupancy could not be satisfied… adjustments」訊息，表示75%佔用被攤到比working set更大的範圍，working set內有未寫入的LPA。後段慢速（整體平均3377µs）來自這些GC的program／erase，不是讀取路徑本身的行為；先前4096與100k筆短測GC為0，所以沒有顯現。

修正：barrier解除後，將等待的讀寫transaction依一般路徑重新`query_cmt`並提交TSU（頁面已搬移，讀取付出一次真實NAND read，不再是零成本完成）。寫入分支採同樣處理，但目前實驗未涵蓋寫入，尚未驗證。另加`FTL_EXIT`stderr診斷：`read_entries_created_online`、`released_from_lpa_barrier`、`gc_executions`。完整編譯通過，尚未執行模擬。

待驗證（使用者執行同一單案例）：預期completed=1,048,576、無HOST_PENDING，`released_from_lpa_barrier`為小量非零。此修正只解決未排空；純讀卻發生GC仍是校準上的混淆因素，需另行處理working set內LPA全數已寫入的初始化，之後才恢復8／4-channel對照。

<!-- calibration-run:666c12879f215d5f:begin -->
## 分段checkpoint實驗：mqsim_windows_c1616df8

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935524 | 7862.3314828510875 | 4064.6359338007364 |

![分段趨勢](results/mqsim_windows_c1616df8/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:666c12879f215d5f:end -->


## 新增裝置參數 GC_Enabled（2026-11-14）

使用者後續研究DiskANN類純讀工作負載，不需要GC。新增可選XML欄位 `<GC_Enabled>false</GC_Enabled>`（Device_Parameter_Set下），未指定時預設true，行為與原本相同；GC／WL程式碼全部保留。

- false時`GC_and_WL_Unit_Page_Level::Check_gc_required`直接返回，不再啟動GC。static wear-leveling只在erase完成後觸發，因此也不會發生。
- 無GC時區塊不會回收：使用者寫入遇到空閒區塊不足時，原本會靜默等待GC；現在改為明確報錯並結束，避免再出現事件耗盡卻未完成的情況。
- 讀取未寫入LPA仍會配置實體頁並產生mapping寫回（program）；關閉GC只移除GC搬移與erase，這部分混淆仍在，可由`FTL_EXIT read_entries_created_online`量測。
- `mqsim_window_probe.py`新增`--no-gc`，寫入該欄位並在plan.json記錄`gc_enabled`。其他腳本尚未加此選項。

完整編譯與Python語法檢查通過，尚未執行模擬。

<!-- calibration-run:75f256420463a2a1:begin -->
## 分段checkpoint實驗：mqsim_windows_59b5f170

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 934671 | 171394.24368188131 | 186.70646640582623 |

![分段趨勢](results/mqsim_windows_59b5f170/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:75f256420463a2a1:end -->


## 59b5f170（GC關閉）判讀：mapping寫回從未執行；新增完整working-set預寫（2026-11-14）

本輪完成全部1,048,576筆，`gc_executions=0`、`released_from_lpa_barrier=0`，GC_Enabled=false生效。但`read_entries_created_online=231819`與GC開啟時相同。

分段結果出現模型假象：0–2.2模擬秒約112k IOPS、284µs，每次CMT miss約對應0.94次mapping read；約2.2秒後跳到約188k IOPS、170µs，CMT miss率仍約94.5%，但mapping read降到每query約0.01。

根因（程式碼確認）：`TSU_Priority_OutOfOrder`把mapping寫入放進`MappingWriteTRQueue`，但沒有任何路徑從該queue取出（TSU_OutofOrder與TSU_FLIN也一樣，皆為上游行為）。XML中`Issued_Flash_Program_CMD=0`，而`Issued_Flash_Program_CMD_For_Mapping=12240`實為「產生」數，不是發出數。寫回永遠不完成，`DepartingMappingEntries`只增不減；`request_mapping_entry`對departing中的MVPN視為資料在記憶體，不發flash read。約2.2秒時working set內MVPN幾乎都變成departing，之後miss全部零成本命中。188k平台不可當作模型性能。

來源鏈：preconditioning的75%佔用（約23.4M LPA）大於32GiB working set（約4.19M LPA），觸發「adjustments」改在整個flow範圍隨機選LPA，working set內每頁約75%機率被寫入；未寫入頁被讀時`online_create_entry_for_reads`配置實體頁並把CMT entry標dirty，淘汰時產生寫回。觀測22%讀取打到未寫入頁，符合預期。

修正（已編譯，尚未執行）：

1. `TSU_Priority_OutOfOrder::get_next_write_service_queue`優先回傳`MappingWriteTRQueue`，與讀取端mapping優先一致。寫入仍只在chip沒有可服務讀取時發出。只修了目前使用的scheduler。
2. 新增可選flow欄位`<Precondition_Full_Working_Set>true</Precondition_Full_Working_Set>`（預設false）：在原隨機佔用之外，把working set每一頁補進preconditioning集合，stderr印`PRECONDITION_FULL_WORKING_SET`與補入頁數。working set外仍維持原隨機佔用。對應真機fio讀取的是已寫滿的檔案。
3. `mqsim_window_probe.py`新增`--full-ws`，plan.json記錄`precondition_full_working_set`。

驗證判準：`--no-gc`單獨執行時`Issued_Flash_Program_CMD`應大於0，且不再出現約2.2秒後的188k跳升；`--no-gc --full-ws`時`read_entries_created_online`應為0，mapping寫入應接近0，後段應維持單一平台。

<!-- calibration-run:36495274092f04c1:begin -->
## 分段checkpoint實驗：mqsim_windows_e4185ae3

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 934671 | 171394.24368188131 | 186.70646640582623 |

![分段趨勢](results/mqsim_windows_e4185ae3/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:36495274092f04c1:end -->

<!-- calibration-run:6dbcd16f8495306b:begin -->
## 分段checkpoint實驗：mqsim_windows_fe6640f9

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935848 | 112511.68588318491 | 284.4157797569691 |

![分段趨勢](results/mqsim_windows_fe6640f9/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:6dbcd16f8495306b:end -->


## e4185ae3／fe6640f9判讀（2026-11-14）

**fe6640f9（`--no-gc --full-ws`）是第一個乾淨的長測。** 補寫1,048,755頁使32GiB的4,194,304頁全部有資料；`read_entries_created_online=0`、program=0、GC=0。9.32模擬秒內每個時段的窗口IOPS中位數都在111.9k–112.8k，延遲284–286µs，每次CMT查詢約0.94次mapping read，沒有漂移或跳升。這組設定可作後續校準的基礎。

**e4185ae3（只`--no-gc`）與59b5f170完全相同，program仍為0，前次TSU修正無效。** 進一步追查：寫回時建立的merge read傳入`related_write=NULL`，write卻以`RelatedRead=readTR`建立。read完成時處理函式只在`RelatedWrite != NULL`時才清除write的RelatedRead，因此`TSU::transaction_is_ready`永遠視該write未就緒。TSU修正（取出MappingWriteTRQueue）仍是必要的；這是同一路徑上的第二個上游bug。已在`generate_flash_writeback_request_for_mapping_data`設定`readTR->RelatedWrite = writeTR`，編譯通過，尚未執行。

對既有基準的影響：舊24組基準同樣有約22%讀取打到未寫入頁，但跳升約在第25萬筆後才出現，100k筆內未觸及；fe6640f9的QD32為112.6k／284µs，與舊基準QD32（112.5k／284µs）幾乎相同。舊QD曲線大致不受此假象影響，但仍以乾淨設定重跑為準。

`mqsim_calibrate.py prepare`新增`--clean`：GC_Enabled=false、精確32GiB working set（Working_Set_Sectors=67108864）、Precondition_Full_Working_Set=true。

<!-- calibration-run:cfdee96ee2d6c550:begin -->
## 分段checkpoint實驗：mqsim_windows_ed0f4ed5

狀態：執行完成；warmup切點 1 模擬秒為預先選定診斷設定，不保證已穩態。

| Case | 後段完成數 | 後段IOPS | 後段mean latency µs |
|---|---:|---:|---:|
| RANDOM_UNIFORM_32768MiB | 935489 | 103720.52581735626 | 308.52111324986186 |

![分段趨勢](results/mqsim_windows_ed0f4ed5/analysis/windows.png)

原始log時間為模擬時間。每段在完成事件越過period後記錄，非精確等寬窗口；最後段較短，mapping讀取數不可直接視作固定時間速率。latency以該窗口完成的requests為母體，包含跨窗口到達的工作。CMT／mapping為全裝置累計差值，本實驗僅單flow，不與完成數硬配對。startup、跨warmup邊界及最後generated達上限的區間不納入後段彙總；仍應檢查後段是否持續漂移。功能短測不代表穩態。
<!-- calibration-run:cfdee96ee2d6c550:end -->


## ed0f4ed5：mapping寫回修正驗證（2026-11-14）

只`--no-gc`、未完整預寫。全部1,048,576筆完成；mapping寫回產生12,240次，實際發出單平面program 12,194次加multiplane program 23次（每次2筆），合計12,240筆全數執行（前兩輪為0），兩個上游bug的修正一起生效。約2.2秒後不再跳到188k：每次CMT查詢的mapping read維持0.94–1.01，窗口IOPS中位數在81k–113k之間，寫回program造成部分時段下降（例如4–12秒約99k–105k、延遲304–322µs）。這是未完整預寫時的預期代價，不是校準設定；校準與DiskANN研究使用`--full-ws`，不產生寫回。

<!-- calibration-run:7cb28e2ff9a6e360:begin -->
## 執行紀錄：mqsim_calibration_20261113T201301Z_b998d705

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261113T201301Z_b998d705`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 321 | 4 | 3.771 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 321 | 8 | 7.009 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 321 | 16 | 12.107 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 321 | 32 | 18.152 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 321 | 64 | 22.351 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 321 | 128 | 24.212 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 322 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 322 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 322 | 4 | 3.773 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 322 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 322 | 16 | 12.095 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 322 | 32 | 18.124 | 4.224 [2.874, 9.319] | 1.770 | 7.583 [3.435, 11.153] |
| reference | 322 | 64 | 22.373 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 322 | 128 | 24.259 | 4.410 [2.964, 9.697] | 5.286 | 29.057 [13.208, 43.250] |
| reference | 323 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 323 | 2 | 1.958 | 0.801 [0.527, 1.776] | 1.019 | 2.497 [1.126, 3.797] |
| reference | 323 | 4 | 3.774 | 3.049 [1.347, 3.831] | 1.062 | 1.312 [1.044, 2.974] |
| reference | 323 | 8 | 7.011 | 2.070 [1.398, 4.699] | 1.143 | 3.867 [1.703, 5.731] |
| reference | 323 | 16 | 12.110 | 7.254 [2.844, 7.798] | 1.323 | 2.206 [2.053, 5.634] |
| reference | 323 | 32 | 18.158 | 4.224 [2.874, 9.319] | 1.764 | 7.583 [3.435, 11.153] |
| reference | 323 | 64 | 22.360 | 6.860 [2.958, 9.665] | 2.863 | 9.339 [6.625, 21.670] |
| reference | 323 | 128 | 24.224 | 4.410 [2.964, 9.697] | 5.292 | 29.057 [13.208, 43.250] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6194.1 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 321 | 2 | 12127.9 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 321 | 4 | 23361.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 321 | 8 | 43414.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 321 | 16 | 74992.9 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 321 | 32 | 112438.0 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 321 | 64 | 138442.4 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 321 | 128 | 149973.8 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 322 | 1 | 6190.8 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 322 | 2 | 12121.2 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 322 | 4 | 23358.1 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 322 | 8 | 43405.2 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 322 | 16 | 74876.8 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 322 | 32 | 112200.1 | 23929.9 [23856.0, 24122.9] | 285.0 | 285.0 | 1337.0 |
| reference | 322 | 64 | 138504.2 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 322 | 128 | 150185.9 | 24900.9 [24883.5, 24906.6] | 851.0 | 851.0 | 5140.0 |
| reference | 323 | 1 | 6193.7 | 5647.6 [2568.0, 8393.9] | 161.0 | 161.0 | 176.8 |
| reference | 323 | 2 | 12126.6 | 4524.0 [4426.6, 4560.8] | 164.0 | 164.0 | 441.6 |
| reference | 323 | 4 | 23374.0 | 11303.9 [7828.9, 21633.5] | 171.0 | 171.0 | 353.7 |
| reference | 323 | 8 | 43425.0 | 11734.8 [11691.6, 12065.7] | 184.0 | 184.0 | 681.5 |
| reference | 323 | 16 | 75007.6 | 23872.9 [18629.0, 44038.0] | 213.0 | 213.0 | 670.0 |
| reference | 323 | 32 | 112463.8 | 23929.9 [23856.0, 24122.9] | 284.0 | 284.0 | 1337.0 |
| reference | 323 | 64 | 138491.2 | 24831.5 [24819.4, 38743.4] | 461.0 | 461.0 | 2577.1 |
| reference | 323 | 128 | 150038.2 | 24900.9 [24883.5, 24906.6] | 852.0 | 852.0 | 5140.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261113T201301Z_b998d705/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261113T201301Z_b998d705/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:7cb28e2ff9a6e360:end -->

## 乾淨基準 b998d705 解讀與真機雙狀態發現（2026-11-14）

### 模擬端檢查

24/24 case 完成：`completed=100000`，`gc_executions=0`，`read_entries_created_online=0`，`released_from_lpa_barrier=0`，working set 全部預先寫入（`lpas=4194304`）。三個 seed 的 IOPS 差異 <0.3%，模型本身已穩定，可作為之後拓撲／NAND 候選的比較基準。

| QD | 1 | 4 | 16 | 32 | 64 | 128 |
|---|---:|---:|---:|---:|---:|---:|
| sim IOPS | 6.19k | 23.4k | 75.0k | 112.4k | 138.5k | 150.0k |
| sim 平均延遲 (µs) | 161 | 171 | 213 | 284 | 461 | 851 |

### 真機逐點中位數混了兩種裝置狀態

重新讀 `mqsim_long_20261112T153235Z_c53192bf` 每個 run 的每秒 IOPS log（`iops_iops.1.log`，`log_avg_msec=1000`）。同一個 QD 內，每秒 IOPS 呈現雙峰，而且狀態會在 run 中途切換（例如 r3_qd16 在第 33–48 秒從 19k 跳到 126k，再掉回 19k）。依每個 QD 對 log(IOPS) 做 2-means 分群，延遲用 Little's law 換算（QD/IOPS）：

| QD | 慢狀態秒數 | 慢 IOPS 中位數 | 慢延遲 µs | 快狀態秒數 | 快 IOPS 中位數 | 快延遲 µs | sim IOPS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 108 | 2,392 | 418 | 72 | 10,048 | 100 | 6,193 |
| 2 | 180 | 4,128 | 484 | 0 | – | – | 12,125 |
| 4 | 149 | 7,375 | 542 | 31 | 42,280 | 95 | 23,364 |
| 8 | 180 | 11,508 | 695 | 0 | – | – | 43,415 |
| 16 | 161 | 18,679 | 857 | 19 | 125,536 | 127 | 74,959 |
| 32 | 180 | 24,024 | 1,332 | 0 | – | – | 112,367 |
| 64 | 175 | 24,843 | 2,576 | 5 | 212,144 | 302 | 138,479 |
| 128 | 180 | 24,931 | 5,134 | 0 | – | – | 150,066 |

- 慢狀態（約 91% 的秒數）：QD1 就要 418µs，QD≥32 硬上限約 25k IOPS。這一段決定了先前的「25k 天花板」與「QD2 比 QD1 慢」，後者是 QD1 中位數混到快狀態秒數所致。
- 快狀態（約 9%）：QD1 100µs、QD4 仍 95µs、QD16 127µs、QD64 約 212k IOPS，是典型隨 NAND 平行度擴張的曲線，形狀與目前模型接近，絕對值比模型快約 1.5 倍。
- 快狀態持續時間隨負載增加而縮短（QD1 約 25–45 秒，QD64 約 5 秒）。可能的解釋：溫度／功耗限速，或背景工作（根目錄也在這顆碟上，使用率 89%，可能有 SLC 折疊／GC）。目前無法區分。
- 量測當下沒有記錄溫度、thermal 計數或主機對同一顆碟的寫入。2026-11-14 閒置時 Composite 溫度 26.85°C，warning 89.85°C、critical 94.85°C（hwmon）。

### 對校準的影響

- 逐點中位數不是單一裝置狀態下的 QD 曲線，不應直接拿來調 NAND 參數。拿模型去追 25k 天花板，等於用 NAND 拓撲去解釋一個可能來自控制器狀態的限制。
- 對 NAND transaction batching 的研究來說，兩個狀態的 knee 完全不同（慢狀態約 QD16–32，快狀態 QD64 以上仍在長），分批結論會依狀態而異。需要先知道哪個狀態代表 DiskANN 實際執行時的裝置。
- 新增 `microbench/state_probe.sh` 與 `microbench/analyze_state_probe.py`：在 fio 量測期間以 1 Hz 記錄溫度（hwmon）、`/proc/diskstats` 的主機讀寫，並在每個階段前後快照 `nvme smart-log`（thermal transition 計數與時間）、電源狀態、HMB、APST。

## state_probe_20261114 結果：排除溫度與主機寫入

資料：`results/state_probe_20261114`，分析：`analyze_state_probe.py`。

- 溫度：全程 26.9–42.9°C。HCTM 門檻 TMT1=75°C、TMT2=80°C；`thm_temp1/2_trans_count` 與 total time 從頭到尾都是 0。**不是熱限速。**
- 主機對 nvme0n1 的寫入：慢狀態大多數秒數為 0 writes/s。qd128 期間另有約 641MB 主機寫入（`data_units_written` +1252），但 qd16_a／qd16_b 寫入幾乎為 0 仍然是慢狀態。**不是同碟背景寫入造成。**
- 電源狀態全程 PS0。HMB 已啟用，HSIZE=10240 頁（40MiB），等於 `hmpre`=`hmmin`，裝置要的大小有全部拿到，OS 上限 128MiB 沒卡住。
- APST 開啟：閒置 100ms 進 PS3（exlat 10ms），2s 進 PS4（exlat 41ms）。

每個 run 一開始都是快狀態，之後驟降：

| run | 前一階段 | 快狀態 | 之後 |
|---|---|---|---|
| qd16_a | idle 120s | 0–3s，約 105k | 慢 18k；77–78s、196–199s 短暫回到快 |
| qd1 | idle 120s | 0–73s，約 10k | 74–114s 慢 2–3k，115s 起又回快 |
| qd128 | qd1（中間只有 fio 啟動的空檔） | 0–8s，約 250k | 第 8s 掉到 102k，之後 20–25k |
| qd16_b | idle 120s | 0–1s，約 130k | 慢 18k |

- 快狀態的長度不能用 I/O 數（0.19M–2.1M）或時間解釋，轉換是驟降而不是漸變。
- qd128 前面只有 fio 啟動的短空檔（數百 ms，超過 APST 的 100ms 門檻），也從快狀態開始。舊基準每個 run 有 10s ramp，高 QD 時快狀態大多被 ramp 吃掉，這說明了為什麼舊資料裡快狀態秒數那麼少。
- 也有不需要閒置就回到快狀態的情況（qd1 第 115s、qd16_a 第 77s）。所以觸發條件不只閒置，比較像控制器內部的背景工作（例如 read disturb 回收、SLC→TLC 搬移、巡檢）佔用資源，閒置／睡眠時暫停或做完一部分。SMART 只統計主機寫入，看不到 NAND 內部寫入，無法直接證實。

### 對 batching 研究的意義

如果閒置空檔能讓裝置回到快狀態，那麼「批次之間的空檔」就會改變裝置狀態本身，而不只是改變排隊。這正是 NAND_TRANSACTION_BATCHING.md 裡「inter-batch gaps」那條假說要面對的混淆。下一步用 `gap_probe.sh`：先連續負載 30s 進入慢狀態，再以「2s 負載＋G ms 閒置」掃 G=0/20/50/100/200/500/1000/3000，APST 開／關各一輪（透過 `pm_qos_latency_tolerance_us`，結束時還原）。

## 伺服器 Crucial T500 對照（state_probe_server_20261114）

裝置：CT2000T500SSD8，韌體 P8CR004，`hmpre=hmmin=0`，表示不使用 HMB，應該有自帶 DRAM。同樣是系統碟（LVM）。讀取目標：`/home/user/datasets/SIFT1B/bigann_base.bvecs.gz` 前 32GiB（92GB 檔案，非 sparse）。

用 SMART `host_read_commands` 在各階段之間的差值換算平均 IOPS（包含 fio 以外的讀取，但 idle 階段差值為 0，可忽略）：

| 階段 | 讀取命令數 | 秒數 | 平均 IOPS | fio 結束時進度列 |
|---|---:|---:|---:|---:|
| QD16 (a) | 56,254,100 | 300 | 187.5k | 187k |
| QD1 | 2,333,043 | 180 | 13.0k（約 77µs） | 12.9k |
| QD128 | 76,589,815 | 180 | 425.5k | 424k |
| QD16 (b) | 22,549,039 | 120 | 187.9k | – |

- 平均值和結束瞬間幾乎相同，兩次 QD16 也一致。
- 逐秒檢查（資料已拉回本機 `results/state_probe_server_20261114`）：除第 0 秒爬升外，QD16 兩次全程 185k–189k，QD128 420k–433k，QD1 12k–13k。沒有開頭快段（第 0 秒反而略低，和 GM7 相反），也沒有中途切換。解析度是 1 秒，更短的切換看不到；但 GM7 的狀態段都長達數秒以上。
- 延遲分布也是單峰：QD1 p50/p90/p99 = 74/75/77µs（GM7 同一個 probe：84/134/733µs）；QD16 80/94/136µs（GM7：469/1663/2703µs）；QD128 289/326/387µs。T500 只有 p99.99 約 2ms 的極少數尾巴（0.01%）。
- 溫度 28.9–43.9°C，thermal 計數全 0。
- 和 GM7 快狀態比：QD1 13.0k vs 10k，QD16 188k vs 126k，QD128 426k vs 約 250k（GM7 只有 QD64 的 5 秒）。
- 推論：GM7 的雙狀態是該顆裝置（DRAM-less／HMB、MAP1602 韌體）的行為，不是 Linux 或量測方法造成。兩顆的控制器也不同，無法只歸因於 DRAM 有無。
- `read_sweep.py` 的裝置檢查改為會追 device-mapper 的 `slaves/`，讓 LVM 上的檔案也能通過（原本只接受分割區直接在 nvme0n1 上）。

## T500 單 job QD 掃描（mqsim_long_20261113T233458Z_a3018661）

伺服器 Crucial T500 2TB，讀取 `/home/user/datasets/SIFT1B/bigann_base.bvecs.gz` 前 32GiB，4KiB 隨機讀，1 個 libaio job，每點 60s＋10s ramp，3 次重複。CPU：AMD Ryzen 7 7700（16 執行緒）。

| QD | IOPS 中位數 | 平均延遲 µs | p99 µs | fio usr+sys CPU % | 每秒 IOPS 範圍 |
|---:|---:|---:|---:|---:|---|
| 1 | 12,991 | 76.7 | 80 | 5.5 | 13k |
| 2 | 24,470 | 81.5 | 92 | 6.8 | 24–25k |
| 4 | 49,119 | 81.3 | 112 | 12.2 | 49k |
| 8 | 96,210 | 83.0 | 124 | 23.3 | 95–97k |
| 16 | 188,083 | 84.9 | 136 | 49.6 | 186–189k |
| 32 | 358,228 | 89.1 | 167 | 86.1 | 349–364k |
| 64 | 425,757 | 150.1 | 232 | 99.5 | 421–432k |
| 128 | 424,370 | 301.4 | 391 | 99.6 | 418–431k |

- 三次重複的 max/min 比都 ≤1.014，每秒資料沒有狀態切換，T500 是穩定的單一狀態。
- QD1→32 幾乎線性擴張（每次加倍約 1.9–2.0 倍），延遲只從 77µs 升到 89µs。
- **QD64/128 的 425k 上限是主機端限制，不是 SSD**：fio usr+sys ≈ 99.5%，等於單一 fio 執行緒用滿一顆核心；context switch 從約 300 萬掉到約 3 千（執行緒一直在忙，不再睡眠）。QD128 延遲剛好是 QD64 的 2 倍，是排在主機端的佇列。
- QD32 時 CPU 已 86%，延遲 89µs 可能也帶一點主機成分。可視為裝置行為的範圍：QD1–16 確定，QD32 待多 job 對照確認。
- 尚未觀察到 T500 本身的轉折點。`read_sweep.py`／`run_long_baseline.sh` 新增 `--jobs`／`JOBS=`（每個 total QD 平均分給多個 job，`group_reporting=1`）；`check_convergence.py` 改為逐窗加總多個 job 的 IOPS log。

## 資料保存位置與目前狀態（2026-11-14）

原始資料都保存在本機 `microbench/results/`（已被 `.gitignore` 排除，不在 GitHub 上）：

| 目錄 | 內容 | 另一份副本 |
|---|---|---|
| `mqsim_long_20261112T153235Z_c53192bf` | GM7 單 job QD 掃描（雙狀態混合） | 無 |
| `state_probe_20261114` | GM7 狀態探測（溫度／主機寫入／SMART） | 無 |
| `mqsim_calibration_20261113T201301Z_b998d705` | MQSim 乾淨基準 24 case | 無（可由 plan 重跑） |
| `state_probe_server_20261114` | T500 狀態探測 | 伺服器 `/home/user/MQSim/microbench/results/` |
| `mqsim_long_20261113T233458Z_a3018661` | T500 單 job QD 掃描 | 伺服器同上 |
| `mqsim_long_20261114T103323Z_b415d92e` | T500 4-job QD 掃描（QD4–512） | 伺服器同上 |
| `mqsim_calibration_20261114T123616Z_694a754d` | MQSim reference vs ideal_map，20 case | 無（可由 plan 重跑） |
| `mqsim_calibration_20261114T_t500_topo` | MQSim chip 數／時序 2×2，32 case（已完成） | 無（可由 plan 重跑） |
| `mqsim_calibration_20261114T_t500_chip128` | MQSim 128 chip／tR／PCIe，24 case（已完成） | 無（可由 plan 重跑） |
| `mqsim_calibration_20261115T_t500_cap` | 作廢（未執行） | – |
| `mqsim_regress_20261115T_onedie` | ONE_DIE 回歸，2 case（通過） | 無 |
| `mqsim_smoke_20261115T_t500_public` | T500 公開規格煙霧測試，8 case（通過） | 無 |
| `mqsim_calibration_20261115T_t500_public` | T500 公開規格 × 派送模式，32 case（已完成；_mp 兩組受 5/6 plane 指令時間 bug 影響） | 無（可由 plan 重跑） |
| `mqsim_calibration_20261115T_t500_vplane` | 每 plane 虛擬 LUN、channel 對照、tR／PCIe、修正版 indep_mp，40 case（已完成） | 無（可由 plan 重跑） |
| `mqsim_regress_20261115T_arbitration` | 預設仲裁回歸，2 case（通過） | 無 |
| `mqsim_calibration_20261115T_t500_cmdfirst` | COMMAND_FIRST × PCIe／channel，24 case（已完成；校準候選） | 無（可由 plan 重跑）；參數另存 `microbench/ssdconfig_t500_calibrated.xml`（git 追蹤） |

目前狀態：
- 校準目標暫定改為 T500（單一狀態、自帶 DRAM、DiskANN 資料在同一台）。GM7 的雙狀態保留為旁證；`gap_probe.sh` 尚未執行，暫緩。
- ~~待辦：T500 4-job 掃描~~ 已完成（`mqsim_long_20261114T103323Z_b415d92e`，見下節）。
- ~~進行中：MQSim `mqsim_calibration_20261114T123616Z_694a754d`~~ 已完成：ideal mapping 讓 QD1 對上（88 vs 77µs），但上限只有 256k，瓶頸是 MQSim 每顆 chip 一次只執行一個 die（見「ideal_map 結果解讀」）。
- ~~已準備、待執行：`mqsim_calibration_20261114T_t500_topo`~~ 已完成：chip64_fast 上限 785k，被 MQSim 的 PCIe 設定（1.0 GB/s×4）卡住；見「chip 數／時序 2×2 結果解讀」。
- ~~已準備、待執行：`mqsim_calibration_20261114T_t500_chip128`~~ 已完成：t65_pcie135 在 QD1–64 與 T500 增益／延遲成長差 <2%，QD≥128 上限偏低偏軟；見「128 chip 結果解讀」。
- 目前最佳候選：ideal mapping、8ch×16chip×1die×1plane、tR 65µs、1200 MT/s、PCIe_Lane_Bandwidth 1.35（PCIe 為控制器上限的替身）。
- ~~已準備、待執行：`mqsim_calibration_20261115T_t500_cap`~~ 作廢：改為保留多 chip／多 die 的真實拓撲，MQSim 新增 `Die_Dispatch_Mode`、`Multiplane_Read_Any_Page`（見「MQSim 修改」）。
- 回歸（逐位相同）與煙霧（8/8）已通過；`mqsim_calibration_20261115T_t500_public` 已完成：indep_mp 最接近但上限 880k、中低 QD 延遲成長過快（plane 不能各自開始）。
- 發現並修正 5／6 plane 指令時間陣列越界 bug（只影響 t500_public 的 _mp 兩組）。
- `mqsim_calibration_20261115T_t500_vplane` 已完成：每 plane 獨立讀取修正了中低 QD；上限受 channel 仲裁造成的車隊效應限制（DATA_OUT_FIRST）。新增 `Channel_Arbitration` 參數。
- 回歸通過；`mqsim_calibration_20261115T_t500_cmdfirst` 完成：pcie135 與 T500 各 QD 差 0–8%，存為 `microbench/ssdconfig_t500_calibrated.xml`（校準候選，尚未獨立驗證）。`*_dispatch` 兩批（8KB page／MLC 推測拓撲）未執行即刪除，由 T500 公開規格版取代。

<!-- calibration-run:c761cb2dfd0128c2:begin -->
## 執行紀錄：mqsim_calibration_20261114T123616Z_694a754d

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261114T123616Z_694a754d`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 2 | 1.958 | 1.884 [1.881, 1.884] | 1.019 | 1.063 [1.063, 1.065] |
| reference | 321 | 4 | 3.771 | 3.823 [3.818, 3.824] | 1.062 | 1.047 [1.046, 1.048] |
| reference | 321 | 8 | 7.009 | 7.583 [7.576, 7.586] | 1.143 | 1.056 [1.056, 1.057] |
| reference | 321 | 16 | 12.107 | 14.794 [14.776, 14.801] | 1.323 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 18.152 | 27.511 [27.476, 27.521] | 1.764 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 22.351 | 46.810 [46.746, 46.839] | 2.863 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 24.212 | 66.322 [66.230, 66.691] | 5.286 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 25.168 | 79.088 [78.986, 79.211] | 10.149 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 25.890 | 80.722 [80.594, 80.739] | 19.702 | 6.364 [6.363, 6.374] |
| ideal_map | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| ideal_map | 321 | 2 | 1.957 | 1.884 [1.881, 1.884] | 1.023 | 1.063 [1.063, 1.065] |
| ideal_map | 321 | 4 | 3.755 | 3.823 [3.818, 3.824] | 1.068 | 1.047 [1.046, 1.048] |
| ideal_map | 321 | 8 | 6.942 | 7.583 [7.576, 7.586] | 1.159 | 1.056 [1.056, 1.057] |
| ideal_map | 321 | 16 | 11.819 | 14.794 [14.776, 14.801] | 1.364 | 1.083 [1.082, 1.084] |
| ideal_map | 321 | 32 | 17.030 | 27.511 [27.476, 27.521] | 1.898 | 1.164 [1.164, 1.166] |
| ideal_map | 321 | 64 | 20.085 | 46.810 [46.746, 46.839] | 3.216 | 1.369 [1.368, 1.371] |
| ideal_map | 321 | 128 | 21.513 | 66.322 [66.230, 66.691] | 5.989 | 1.934 [1.924, 1.937] |
| ideal_map | 321 | 256 | 22.273 | 79.088 [78.986, 79.211] | 11.534 | 3.247 [3.241, 3.251] |
| ideal_map | 321 | 512 | 22.795 | 80.722 [80.594, 80.739] | 22.455 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 6194.1 | 12990.9 [12985.2, 13009.1] | 161.0 | 161.0 | 76.7 |
| reference | 321 | 2 | 12127.9 | 24470.0 [24465.8, 24474.4] | 164.0 | 164.0 | 81.5 |
| reference | 321 | 4 | 23361.1 | 49665.7 [49658.1, 49669.6] | 171.0 | 171.0 | 80.2 |
| reference | 321 | 8 | 43414.0 | 98515.5 [98509.9, 98557.5] | 184.0 | 184.0 | 81.0 |
| reference | 321 | 16 | 74992.9 | 192197.1 [192192.9, 192225.0] | 213.0 | 213.0 | 83.0 |
| reference | 321 | 32 | 112438.0 | 357396.3 [357370.7, 357436.4] | 284.0 | 284.0 | 89.3 |
| reference | 321 | 64 | 138442.4 | 608126.6 [608099.0, 608208.0] | 461.0 | 461.0 | 105.0 |
| reference | 321 | 128 | 149973.8 | 861585.6 [860001.6, 867582.5] | 851.0 | 851.0 | 148.3 |
| reference | 321 | 256 | 155895.4 | 1027533.8 [1026965.1, 1029017.8] | 1634.0 | 1634.0 | 248.9 |
| reference | 321 | 512 | 160363.6 | 1048411.2 [1046989.6, 1050117.4] | 3172.0 | 3172.0 | 488.1 |
| ideal_map | 321 | 1 | 11246.2 | 12990.9 [12985.2, 13009.1] | 88.0 | 88.0 | 76.7 |
| ideal_map | 321 | 2 | 22007.5 | 24470.0 [24465.8, 24474.4] | 90.0 | 90.0 | 81.5 |
| ideal_map | 321 | 4 | 42230.7 | 49665.7 [49658.1, 49669.6] | 94.0 | 94.0 | 80.2 |
| ideal_map | 321 | 8 | 78070.8 | 98515.5 [98509.9, 98557.5] | 102.0 | 102.0 | 81.0 |
| ideal_map | 321 | 16 | 132919.1 | 192197.1 [192192.9, 192225.0] | 120.0 | 120.0 | 83.0 |
| ideal_map | 321 | 32 | 191527.9 | 357396.3 [357370.7, 357436.4] | 167.0 | 167.0 | 89.3 |
| ideal_map | 321 | 64 | 225879.1 | 608126.6 [608099.0, 608208.0] | 283.0 | 283.0 | 105.0 |
| ideal_map | 321 | 128 | 241934.9 | 861585.6 [860001.6, 867582.5] | 527.0 | 527.0 | 148.3 |
| ideal_map | 321 | 256 | 250487.5 | 1027533.8 [1026965.1, 1029017.8] | 1015.0 | 1015.0 | 248.9 |
| ideal_map | 321 | 512 | 256353.4 | 1048411.2 [1046989.6, 1050117.4] | 1976.0 | 1976.0 | 488.1 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![ideal_map：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T123616Z_694a754d/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![ideal_map：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T123616Z_694a754d/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T123616Z_694a754d/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T123616Z_694a754d/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:c761cb2dfd0128c2:end -->

## T500 4-job QD 掃描（mqsim_long_20261114T103323Z_b415d92e）

設定同單 job 掃描，但每個 total QD 平均分給 4 個 libaio job（`JOBS=4`，`group_reporting=1`），QD 4–512，每點 60s＋10s ramp，3 次重複。

| total QD | IOPS 中位數 | 對前一點增益 | 平均延遲 µs | p99 µs | fio usr+sys %（每 job 平均） | 單 job 同 QD IOPS |
|---:|---:|---:|---:|---:|---:|---:|
| 4 | 49,666 | – | 80.2 | 119 | 5.8 | 49,119 |
| 8 | 98,515 | 1.98 | 81.0 | 124 | 8.5 | 96,210 |
| 16 | 192,197 | 1.95 | 83.0 | 131 | 17.3 | 188,083 |
| 32 | 357,396 | 1.86 | 89.3 | 159 | 33.5 | 358,228 |
| 64 | 608,127 | 1.70 | 105.0 | 232 | 54.0 | 425,757（主機受限） |
| 128 | 861,586 | 1.42 | 148.3 | 741 | 67.5 | 424,370（主機受限） |
| 256 | 1,027,534 | 1.19 | 248.9 | 1,044 | 79.3 | – |
| 512 | 1,048,411 | 1.02 | 488.1 | 1,434 | 81.8 | – |

- 所有 run 前後半 IOPS 比 0.995–1.003，3 次重複 max/min ≤1.009，穩定單一狀態。
- QD≤32 時 1 job 與 4 job 幾乎相同（差 <2.5%），單 job 資料在這個範圍可信；QD64/128 的單 job 值確認是主機上限。
- **T500 轉折**：增益從 QD32 起明顯下降（1.86→1.70→1.42→1.19→1.02），QD256 達約 98% 上限，上限約 **1.05M IOPS**。QD256→512 延遲正好加倍（249→488µs），是飽和後的純排隊。
- 上限是否完全屬於 SSD：每 job CPU 在 QD512 約 82%（判斷依據：同 QD 下 4 job 與 1 job 的 CPU 比例只有在「每 job 平均」語意下才合理），未滿載；context switch 隨 QD 下降但未到單 job 飽和時的千位數。傾向是裝置上限，但 8-job 對照才能完全排除主機成分。官方規格 2TB 隨機讀約 1.18M IOPS（不同測試條件），量級吻合。
- 粗估有效平行度（推論，非 BOM）：上限 × 低負載延遲 ≈ 1.05M × 80µs ≈ 84 個同時服務中的讀取。低負載延遲含主機與控制器開銷，NAND 本身的 tR 更短，所以實際平行單元可能更少。
- 與 MQSim 乾淨基準（2MiB CMT、64 die／128 plane）相比：sim QD1 161µs（其中約一半是 mapping 讀取），QD128 才 150k。T500 有自帶 DRAM，整張 L2P 常駐 DRAM，不該有 mapping 讀取。所以第一步改用 `Ideal_Mapping_Table=true`（所有 mapping 都在 DRAM），再調 NAND 時序與拓撲。之前在 GM7（DRAM-less）情境下暫緩 ideal mapping 的理由，對 T500 不適用。

工具變更：`mqsim_calibrate.py prepare` 支援多個 `--reference`（後者覆蓋重疊的 repeat/QD；此次 QD1–2 來自單 job、QD4–512 來自 4-job，不同 archive 的同號 repeat 不是同一次執行），以及 `--variant NAME KEY=VALUE…`、`--base-override KEY=VALUE…`。

## ideal_map 結果解讀（mqsim_calibration_20261114T123616Z_694a754d）

20 case 全部完成，`FTL_EXIT read_entries_created_online=0 released_from_lpa_barrier=0 gc_executions=0`（20/20）。

| QD | reference IOPS | ideal_map IOPS | ideal_map 裝置延遲 µs | T500 IOPS | T500 fio 延遲 µs |
|---:|---:|---:|---:|---:|---:|
| 1 | 6.2k | 11.2k | 88 | 13.0k | 77 |
| 16 | 75k | 133k | 120 | 192k | 83 |
| 64 | 138k | 226k | 283 | 608k | 105 |
| 128 | 150k | 242k | 527 | 862k | 148 |
| 512 | 160k | 256k | 1976 | 1.05M | 488 |

- ideal mapping 符合預期：QD1 從 161µs 降到 88µs = tR 75 + 4KB 通道傳輸 12（333 MT/s）+ 1；mapping 讀取消失（waiting 0）。
- 上限只有 256k，是 T500 的約 1/4；轉折太早（自身上限的 52% 在 QD16、75% 在 QD32，T500 在 QD32 只有 34%）。所以 QD 增益在 QD≥16 就明顯低於真機。
- **瓶頸機制（讀原始碼＋chip 統計確認）**：`TSU_Base::issue_command_to_chip` 每次只對一個 die 送指令就 return，且 `service_read_transaction` 要求 chip 為 IDLE 才發新讀取。所以同一 chip 的兩個 die 不會重疊執行，multi-plane 也只在同 PageID 時成立（隨機讀幾乎不會）。實際平行單位是 **32 顆 chip**，不是 64 die／128 plane。
  - QD512 每顆 chip：execution 時間佔 0.59、data transfer 0.10；256k/32 = 8k 讀取/s/chip，即每次讀取約 125µs（75 tR + 12 傳輸 + 其餘等通道／排程）。
  - PCIe 不是瓶頸：4KB 含 TLP overhead 約 1.25µs，上限約 80 萬。
- 推論：在這個 MQSim 版本裡，「顆 chip 數」就是 tR 層級的平行度旋鈕；die/plane 數對隨機 4K 讀幾乎無效。這點也直接影響 NAND_TRANSACTION_BATCHING 研究：chip 是一次只接一批（同 die）的指令。

### 下一批（已準備，未執行）：`mqsim_calibration_20261114T_t500_topo`

全部 variant 都套 `Ideal_Mapping_Table=true`（`--base-override`），總 plane 數不變（容量與 working set 同前一批），QD 1/4/16/32/64/128/256/512，seed 321，`--clean`，32 case：

| variant | 改動 | 用途 |
|---|---|---|
| reference | 無（＝ideal_map，同批對照） | 確認同一 binary 結果可重現 |
| chip64 | 8 ch × 8 chip × 1 die | 只加平行單位：上限是否約翻倍、轉折是否後移 |
| fast_io | tR 50µs、通道 1200 MT/s（4KB 約 3.4µs） | 只改時序：QD1 延遲與每單位速率 |
| chip64_fast | 兩者同時 | 預估 QD1 約 55µs、上限接近 1M |

這些數值是校準候選值，不是 T500 的實際規格（T500 的 NAND die 數、plane 數、tR 都未查證）。

<!-- calibration-run:e91d063ed2284d21:begin -->
## 執行紀錄：mqsim_calibration_20261114T_t500_topo

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261114T_t500_topo`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 4 | 3.755 | 3.823 [3.818, 3.824] | 1.068 | 1.047 [1.046, 1.048] |
| reference | 321 | 16 | 11.819 | 14.794 [14.776, 14.801] | 1.364 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 17.030 | 27.511 [27.476, 27.521] | 1.898 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 20.085 | 46.810 [46.746, 46.839] | 3.216 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 21.513 | 66.322 [66.230, 66.691] | 5.989 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 22.273 | 79.088 [78.986, 79.211] | 11.534 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 22.795 | 80.722 [80.594, 80.739] | 22.455 | 6.364 [6.363, 6.374] |
| chip64 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| chip64 | 321 | 4 | 3.849 | 3.823 [3.818, 3.824] | 1.045 | 1.047 [1.046, 1.048] |
| chip64 | 321 | 16 | 13.338 | 14.794 [14.776, 14.801] | 1.205 | 1.083 [1.082, 1.084] |
| chip64 | 321 | 32 | 20.949 | 27.511 [27.476, 27.521] | 1.534 | 1.164 [1.164, 1.166] |
| chip64 | 321 | 64 | 26.162 | 46.810 [46.746, 46.839] | 2.466 | 1.369 [1.368, 1.371] |
| chip64 | 321 | 128 | 29.141 | 66.322 [66.230, 66.691] | 4.420 | 1.934 [1.924, 1.937] |
| chip64 | 321 | 256 | 30.998 | 79.088 [78.986, 79.211] | 8.295 | 3.247 [3.241, 3.251] |
| chip64 | 321 | 512 | 32.149 | 80.722 [80.594, 80.739] | 15.932 | 6.364 [6.363, 6.374] |
| fast_io | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| fast_io | 321 | 4 | 3.777 | 3.823 [3.818, 3.824] | 1.055 | 1.047 [1.046, 1.048] |
| fast_io | 321 | 16 | 12.254 | 14.794 [14.776, 14.801] | 1.291 | 1.083 [1.082, 1.084] |
| fast_io | 321 | 32 | 18.791 | 27.511 [27.476, 27.521] | 1.691 | 1.164 [1.164, 1.166] |
| fast_io | 321 | 64 | 23.937 | 46.810 [46.746, 46.839] | 2.655 | 1.369 [1.368, 1.371] |
| fast_io | 321 | 128 | 26.169 | 66.322 [66.230, 66.691] | 4.873 | 1.934 [1.924, 1.937] |
| fast_io | 321 | 256 | 26.940 | 79.088 [78.986, 79.211] | 9.418 | 3.247 [3.241, 3.251] |
| fast_io | 321 | 512 | 27.626 | 80.722 [80.594, 80.739] | 18.327 | 6.364 [6.363, 6.374] |
| chip64_fast | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| chip64_fast | 321 | 4 | 3.874 | 3.823 [3.818, 3.824] | 1.018 | 1.047 [1.046, 1.048] |
| chip64_fast | 321 | 16 | 13.837 | 14.794 [14.776, 14.801] | 1.145 | 1.083 [1.082, 1.084] |
| chip64_fast | 321 | 32 | 23.796 | 27.511 [27.476, 27.521] | 1.327 | 1.164 [1.164, 1.166] |
| chip64_fast | 321 | 64 | 34.949 | 46.810 [46.746, 46.839] | 1.818 | 1.369 [1.368, 1.371] |
| chip64_fast | 321 | 128 | 40.743 | 66.322 [66.230, 66.691] | 3.127 | 1.934 [1.924, 1.937] |
| chip64_fast | 321 | 256 | 42.920 | 79.088 [78.986, 79.211] | 5.927 | 3.247 [3.241, 3.251] |
| chip64_fast | 321 | 512 | 43.215 | 80.722 [80.594, 80.739] | 11.764 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 11246.2 | 12990.9 [12985.2, 13009.1] | 88.0 | 88.0 | 76.7 |
| reference | 321 | 4 | 42230.7 | 49665.7 [49658.1, 49669.6] | 94.0 | 94.0 | 80.2 |
| reference | 321 | 16 | 132919.1 | 192197.1 [192192.9, 192225.0] | 120.0 | 120.0 | 83.0 |
| reference | 321 | 32 | 191527.9 | 357396.3 [357370.7, 357436.4] | 167.0 | 167.0 | 89.3 |
| reference | 321 | 64 | 225879.1 | 608126.6 [608099.0, 608208.0] | 283.0 | 283.0 | 105.0 |
| reference | 321 | 128 | 241934.9 | 861585.6 [860001.6, 867582.5] | 527.0 | 527.0 | 148.3 |
| reference | 321 | 256 | 250487.5 | 1027533.8 [1026965.1, 1029017.8] | 1015.0 | 1015.0 | 248.9 |
| reference | 321 | 512 | 256353.4 | 1048411.2 [1046989.6, 1050117.4] | 1976.0 | 1976.0 | 488.1 |
| chip64 | 321 | 1 | 11246.2 | 12990.9 [12985.2, 13009.1] | 88.0 | 88.0 | 76.7 |
| chip64 | 321 | 4 | 43281.5 | 49665.7 [49658.1, 49669.6] | 92.0 | 92.0 | 80.2 |
| chip64 | 321 | 16 | 150003.1 | 192197.1 [192192.9, 192225.0] | 106.0 | 106.0 | 83.0 |
| chip64 | 321 | 32 | 235592.6 | 357396.3 [357370.7, 357436.4] | 135.0 | 135.0 | 89.3 |
| chip64 | 321 | 64 | 294226.3 | 608126.6 [608099.0, 608208.0] | 217.0 | 217.0 | 105.0 |
| chip64 | 321 | 128 | 327722.8 | 861585.6 [860001.6, 867582.5] | 389.0 | 389.0 | 148.3 |
| chip64 | 321 | 256 | 348608.9 | 1027533.8 [1026965.1, 1029017.8] | 730.0 | 730.0 | 248.9 |
| chip64 | 321 | 512 | 361548.5 | 1048411.2 [1046989.6, 1050117.4] | 1402.0 | 1402.0 | 488.1 |
| fast_io | 321 | 1 | 18171.2 | 12990.9 [12985.2, 13009.1] | 55.0 | 55.0 | 76.7 |
| fast_io | 321 | 4 | 68631.7 | 49665.7 [49658.1, 49669.6] | 58.0 | 58.0 | 80.2 |
| fast_io | 321 | 16 | 222678.0 | 192197.1 [192192.9, 192225.0] | 71.0 | 71.0 | 83.0 |
| fast_io | 321 | 32 | 341461.5 | 357396.3 [357370.7, 357436.4] | 93.0 | 93.0 | 89.3 |
| fast_io | 321 | 64 | 434969.4 | 608126.6 [608099.0, 608208.0] | 146.0 | 146.0 | 105.0 |
| fast_io | 321 | 128 | 475515.2 | 861585.6 [860001.6, 867582.5] | 268.0 | 268.0 | 148.3 |
| fast_io | 321 | 256 | 489530.7 | 1027533.8 [1026965.1, 1029017.8] | 518.0 | 518.0 | 248.9 |
| fast_io | 321 | 512 | 501995.0 | 1048411.2 [1046989.6, 1050117.4] | 1008.0 | 1008.0 | 488.1 |
| chip64_fast | 321 | 1 | 18171.2 | 12990.9 [12985.2, 13009.1] | 55.0 | 55.0 | 76.7 |
| chip64_fast | 321 | 4 | 70398.0 | 49665.7 [49658.1, 49669.6] | 56.0 | 56.0 | 80.2 |
| chip64_fast | 321 | 16 | 251438.0 | 192197.1 [192192.9, 192225.0] | 63.0 | 63.0 | 83.0 |
| chip64_fast | 321 | 32 | 432407.7 | 357396.3 [357370.7, 357436.4] | 73.0 | 73.0 | 89.3 |
| chip64_fast | 321 | 64 | 635060.7 | 608126.6 [608099.0, 608208.0] | 100.0 | 100.0 | 105.0 |
| chip64_fast | 321 | 128 | 740352.7 | 861585.6 [860001.6, 867582.5] | 172.0 | 172.0 | 148.3 |
| chip64_fast | 321 | 256 | 779909.7 | 1027533.8 [1026965.1, 1029017.8] | 326.0 | 326.0 | 248.9 |
| chip64_fast | 321 | 512 | 785276.3 | 1048411.2 [1046989.6, 1050117.4] | 647.0 | 647.0 | 488.1 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![chip64：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![chip64：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![chip64_fast：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![chip64_fast：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![fast_io：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![fast_io：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v2_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v3_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_topo/analysis/figures/trend_v3_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:e91d063ed2284d21:end -->

## chip 數／時序 2×2 結果解讀（mqsim_calibration_20261114T_t500_topo）

32 case 全部完成，FTL_EXIT 計數全為 0（32/32）。reference 與前一批 ideal_map 逐點完全相同（11/42/133/192/226/242/250/256k），同一 binary＋seed 結果可重現。

| QD | 1 | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T500 IOPS (k) | 13 | 50 | 192 | 357 | 608 | 862 | 1028 | 1048 |
| T500 fio 延遲 µs | 77 | 80 | 83 | 89 | 105 | 148 | 249 | 488 |
| reference（32 chip, tR75, 333MT/s） | 11 | 42 | 133 | 192 | 226 | 242 | 250 | 256 |
| chip64（64 chip, tR75, 333） | 11 | 43 | 150 | 236 | 294 | 328 | 349 | 362 |
| fast_io（32 chip, tR50, 1200） | 18 | 69 | 223 | 341 | 435 | 476 | 490 | 502 |
| chip64_fast（64 chip, tR50, 1200） | 18 | 70 | 251 | 432 | 635 | 740 | 780 | 785 |
| chip64_fast 延遲 µs | 55 | 56 | 63 | 73 | 100 | 172 | 326 | 647 |

QD 增益（以 QD1 正規化）在 QD64：T500 46.8；reference 20.1、chip64 26.2、fast_io 23.9、chip64_fast 34.9。延遲成長 QD1→64：T500 1.37，chip64_fast 1.82。

- chip 數加倍但通道仍是 333 MT/s（chip64）：上限只多 1.41×，chip execution 佔比從 0.59 掉到 0.41，代表每通道 8 顆 chip 時通道／派送開始卡住。
- 通道與 tR 都變快（chip64_fast）：上限 785k，chip execution 0.61。**785k 已經是 PCIe 上限**：4KB 含 TLP overhead 4992 bytes／4 lanes／1.0 GB/s ≈ 1.25µs → 約 80 萬 IOPS。所以 chip64_fast 的硬轉折是 PCIe 造成的，不是 NAND。
- 真機的形狀：轉折很硬（QD128 82%、QD256 98%），但 QD64 前延遲只成長 37%。比較像「NAND 平行單位很多（隨機碰撞少），上限由一個中央資源決定」。T500 是 PCIe Gen4 x4（約 7.9 GB/s），PCIe 本身不會卡在 1.05M，中央資源較可能是控制器／韌體。MQSim 沒有控制器處理時間的參數，暫時用 PCIe 頻寬當這個中央上限的替身（**建模選擇，不代表 T500 的 PCIe 真的這麼慢**）。
- QD1 延遲：sim 55µs 的邊界不含主機軟體；fio 77µs 含 libaio／kernel／中斷，估計數 µs 到十幾 µs，未量測。下一批把 tR 拉到 65µs，讓 sim QD1 約 70µs。

### 下一批（已準備，未執行）：`mqsim_calibration_20261114T_t500_chip128`

共同設定：`Ideal_Mapping_Table=true`，8 ch × 16 chip × 1 die × 1 plane（plane 總數仍 128，容量與 working set 不變），通道 1200 MT/s，QD 1/4/16/32/64/128/256/512，seed 321，`--clean`，24 case。

| variant | 改動 | 用途 |
|---|---|---|
| reference | tR 50 | 與 chip64_fast 比：128 個平行單位對中段曲線的影響（上限仍被 PCIe 80 萬卡住） |
| t65 | tR 65 | QD1 拉到約 70µs |
| t65_pcie135 | tR 65、PCIe_Lane_Bandwidth 1.35 | 中央上限約 1.08M 的替身，看轉折形狀是否接近 T500 |

（中間曾誤設 `Block_No_Per_Plane=4096` 使容量翻倍，已刪除該目錄重做，未執行。）

<!-- calibration-run:5d6716e434b77e90:begin -->
## 執行紀錄：mqsim_calibration_20261114T_t500_chip128

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261114T_t500_chip128`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 4 | 3.924 | 3.823 [3.818, 3.824] | 1.018 | 1.047 [1.046, 1.048] |
| reference | 321 | 16 | 14.691 | 14.794 [14.776, 14.801] | 1.073 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 26.798 | 27.511 [27.476, 27.521] | 1.182 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 42.165 | 46.810 [46.746, 46.839] | 1.509 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 43.391 | 66.322 [66.230, 66.691] | 2.945 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 43.413 | 79.088 [78.986, 79.211] | 5.891 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 43.416 | 80.722 [80.594, 80.739] | 11.764 | 6.364 [6.363, 6.374] |
| t65 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| t65 | 321 | 4 | 3.931 | 3.823 [3.818, 3.824] | 1.014 | 1.047 [1.046, 1.048] |
| t65 | 321 | 16 | 14.806 | 14.794 [14.776, 14.801] | 1.071 | 1.083 [1.082, 1.084] |
| t65 | 321 | 32 | 27.299 | 27.511 [27.476, 27.521] | 1.171 | 1.164 [1.164, 1.166] |
| t65 | 321 | 64 | 45.684 | 46.810 [46.746, 46.839] | 1.386 | 1.369 [1.368, 1.371] |
| t65 | 321 | 128 | 55.173 | 66.322 [66.230, 66.691] | 2.314 | 1.934 [1.924, 1.937] |
| t65 | 321 | 256 | 55.206 | 79.088 [78.986, 79.211] | 4.629 | 3.247 [3.241, 3.251] |
| t65 | 321 | 512 | 55.244 | 80.722 [80.594, 80.739] | 9.243 | 6.364 [6.363, 6.374] |
| t65_pcie135 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| t65_pcie135 | 321 | 4 | 3.935 | 3.823 [3.818, 3.824] | 1.014 | 1.047 [1.046, 1.048] |
| t65_pcie135 | 321 | 16 | 14.849 | 14.794 [14.776, 14.801] | 1.087 | 1.083 [1.082, 1.084] |
| t65_pcie135 | 321 | 32 | 27.444 | 27.511 [27.476, 27.521] | 1.174 | 1.164 [1.164, 1.166] |
| t65_pcie135 | 321 | 64 | 46.009 | 46.810 [46.746, 46.839] | 1.391 | 1.369 [1.368, 1.371] |
| t65_pcie135 | 321 | 128 | 60.064 | 66.322 [66.230, 66.691] | 2.130 | 1.934 [1.924, 1.937] |
| t65_pcie135 | 321 | 256 | 65.806 | 79.088 [78.986, 79.211] | 3.899 | 3.247 [3.241, 3.251] |
| t65_pcie135 | 321 | 512 | 69.378 | 80.722 [80.594, 80.739] | 7.362 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 18171.2 | 12990.9 [12985.2, 13009.1] | 55.0 | 55.0 | 76.7 |
| reference | 321 | 4 | 71306.0 | 49665.7 [49658.1, 49669.6] | 56.0 | 56.0 | 80.2 |
| reference | 321 | 16 | 266945.7 | 192197.1 [192192.9, 192225.0] | 59.0 | 59.0 | 83.0 |
| reference | 321 | 32 | 486953.0 | 357396.3 [357370.7, 357436.4] | 65.0 | 65.0 | 89.3 |
| reference | 321 | 64 | 766199.5 | 608126.6 [608099.0, 608208.0] | 83.0 | 83.0 | 105.0 |
| reference | 321 | 128 | 788472.7 | 861585.6 [860001.6, 867582.5] | 162.0 | 162.0 | 148.3 |
| reference | 321 | 256 | 788873.4 | 1027533.8 [1026965.1, 1029017.8] | 324.0 | 324.0 | 248.9 |
| reference | 321 | 512 | 788931.2 | 1048411.2 [1046989.6, 1050117.4] | 647.0 | 647.0 | 488.1 |
| t65 | 321 | 1 | 14279.2 | 12990.9 [12985.2, 13009.1] | 70.0 | 70.0 | 76.7 |
| t65 | 321 | 4 | 56134.3 | 49665.7 [49658.1, 49669.6] | 71.0 | 71.0 | 80.2 |
| t65 | 321 | 16 | 211416.2 | 192197.1 [192192.9, 192225.0] | 75.0 | 75.0 | 83.0 |
| t65 | 321 | 32 | 389807.9 | 357396.3 [357370.7, 357436.4] | 82.0 | 82.0 | 89.3 |
| t65 | 321 | 64 | 652323.6 | 608126.6 [608099.0, 608208.0] | 97.0 | 97.0 | 105.0 |
| t65 | 321 | 128 | 787821.5 | 861585.6 [860001.6, 867582.5] | 162.0 | 162.0 | 148.3 |
| t65 | 321 | 256 | 788303.5 | 1027533.8 [1026965.1, 1029017.8] | 324.0 | 324.0 | 248.9 |
| t65 | 321 | 512 | 788837.9 | 1048411.2 [1046989.6, 1050117.4] | 647.0 | 647.0 | 488.1 |
| t65_pcie135 | 321 | 1 | 14349.5 | 12990.9 [12985.2, 13009.1] | 69.0 | 69.0 | 76.7 |
| t65_pcie135 | 321 | 4 | 56464.3 | 49665.7 [49658.1, 49669.6] | 70.0 | 70.0 | 80.2 |
| t65_pcie135 | 321 | 16 | 213079.4 | 192197.1 [192192.9, 192225.0] | 75.0 | 75.0 | 83.0 |
| t65_pcie135 | 321 | 32 | 393810.9 | 357396.3 [357370.7, 357436.4] | 81.0 | 81.0 | 89.3 |
| t65_pcie135 | 321 | 64 | 660211.3 | 608126.6 [608099.0, 608208.0] | 96.0 | 96.0 | 105.0 |
| t65_pcie135 | 321 | 128 | 861888.9 | 861585.6 [860001.6, 867582.5] | 147.0 | 147.0 | 148.3 |
| t65_pcie135 | 321 | 256 | 944287.2 | 1027533.8 [1026965.1, 1029017.8] | 269.0 | 269.0 | 248.9 |
| t65_pcie135 | 321 | 512 | 995538.5 | 1048411.2 [1046989.6, 1050117.4] | 508.0 | 508.0 | 488.1 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![t65：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![t65：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![t65_pcie135：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![t65_pcie135：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261114T_t500_chip128/analysis/figures/trend_v2_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:5d6716e434b77e90:end -->

## 128 chip 結果解讀（mqsim_calibration_20261114T_t500_chip128，2026-11-15）

24 case 全部完成，FTL_EXIT 計數全為 0（24/24）。共同設定：ideal mapping、8 ch × 16 chip × 1 die × 1 plane、1200 MT/s。

| QD | 1 | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T500 IOPS (k) | 13 | 50 | 192 | 357 | 608 | 862 | 1028 | 1048 |
| reference（tR50, PCIe 1.0） | 18 | 71 | 267 | 487 | 766 | 788 | 789 | 789 |
| t65（tR65, PCIe 1.0） | 14 | 56 | 211 | 390 | 652 | 788 | 788 | 789 |
| **t65_pcie135**（tR65, PCIe 1.35） | 14 | 56 | 213 | 394 | 660 | **862** | 944 | 996 |
| T500 增益（÷QD1） | 1.0 | 3.8 | 14.8 | 27.5 | 46.8 | 66.3 | 79.1 | 80.7 |
| t65_pcie135 增益 | 1.0 | 3.9 | 14.8 | 27.4 | 46.0 | 60.1 | 65.8 | 69.4 |
| T500 延遲成長 | 1.00 | 1.05 | 1.08 | 1.16 | 1.37 | 1.93 | 3.25 | 6.37 |
| t65_pcie135 延遲成長 | 1.00 | 1.01 | 1.09 | 1.17 | 1.39 | 2.13 | 3.90 | 7.36 |
| T500 fio 延遲 µs | 77 | 80 | 83 | 89 | 105 | 148 | 249 | 488 |
| t65_pcie135 裝置延遲 µs | 69 | 70 | 75 | 81 | 96 | 147 | 269 | 508 |

- 128 個平行單位讓中段曲線對上了：t65_pcie135 在 **QD1–64 的增益與延遲成長都在 T500 的 2% 內**；QD128 絕對 IOPS 862k 與真機相同。
- 剩下的差異在 QD≥128：sim 接近上限的過程比真機軟（QD256 為自身最大值的 95%，真機 98%），QD512 只到 996k（真機 1048k）。QD512 時 chip execution 只佔 0.51，NAND 不是瓶頸；限制在 PCIe 替身上限（1.35 GB/s×4 約 108 萬，扣掉 CQ 等訊息後實際略低）。
- reference 與 t65 都卡在 789k，再次確認 PCIe 1.0 的上限約 79 萬；QD64 時 reference 已達 97%，太早飽和。
- 絕對值：低 QD 時 sim IOPS 比真機高約 10%，因為 sim 延遲 69µs 不含主機軟體，fio 77µs 含。差 8µs 可視為主機開銷的估計值（推論，未量測）。
- 對 DiskANN 研究：若工作負載的實際 QD 落在 64 以內，這組參數已能重現 T500 的趨勢；QD128 以上還需要微調中央上限。

### 下一批（已準備，未執行）：`mqsim_calibration_20261115T_t500_cap`

共同：同 t65_pcie135 拓撲，24 case。

| variant | 改動 | 用途 |
|---|---|---|
| reference | tR 65、PCIe 1.45（上限約 1.16M） | 讓 QD512 接近 1.05M |
| pcie160 | tR 65、PCIe 1.6（約 1.28M） | 看上限再往上時 QD128–256 是否超過真機 |
| t72 | tR 72、PCIe 1.45 | 把約 8µs 主機開銷併入 tR，讓 QD1 約 76µs、低 QD 絕對 IOPS 對齊（代價：NAND 佔用時間被高估） |

<!-- calibration-run:b6878d66739d9ed1:begin -->
## 執行紀錄：mqsim_calibration_20261115T_t500_cap

狀態：已準備 24 個cases，尚未執行。
輸入與來源：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261115T_t500_cap`。
目標：QD吞吐增益、延遲成長及曲線轉折；絕對數值僅供追溯。
<!-- calibration-run:b6878d66739d9ed1:end -->

## MQSim 修改：die 派送模式與獨立 multi-plane 讀取（2026-11-15）

動機：t65_pcie135 用 8 ch × 16 chip × 1 die × 1 plane，只是用「很多顆 chip」來繞過 MQSim 的序列化，不像真實 SSD。真實 NAND 一個 channel 掛多個 CE／die（ONFI LUN），各 die 可以獨立執行，新的 NAND 也支援各 plane 讀不同 page。

查證：
- `Flash_Chip` 本來就以 die 為單位排程完成事件，die 可以同時執行。
- 2020 年上游 commit `e338620`（新增 Priority OOO scheduler）重構 `issue_command_to_chip` 時，在 die 迴圈裡加了 `return`。之前的版本（`e338620^` 的 `TSU_OutofOrder.cpp`）一次派送會送給這顆 chip 所有有工作的 die。所以「一次一個 die」看起來是重構造成的退化，不是設計。
- 即使是舊版，下一次派送仍要等整顆 chip IDLE，die 之間是「同步批次」，不是各自獨立。

新增兩個 device 參數（預設值保持舊行為）：

| 參數 | 值 | 行為 |
|---|---|---|
| `Die_Dispatch_Mode` | `ONE_DIE`（預設） | 現行行為：一次派送只送一個 die，chip 全部閒置才派下一次 |
| | `ALL_DIES` | e338620 以前的行為：一次派送送給所有有工作的 die（die-interleaved），下一次仍要等整顆 chip 閒置 |
| | `INDEPENDENT_DIES` | 只要有空閒的 die、其他 die 只在做讀取，就可以馬上派新讀取給空閒的 die（目前只支援 `PRIORITY_OUT_OF_ORDER`） |
| `Multiplane_Read_Any_Page` | `false`（預設） | multi-plane 讀取要求各 plane 同一 page offset（MQSim 原行為） |
| | `true` | 同一 die 的各 plane 可讀不同 page，一個指令同時執行（同步啟動；不是每個 plane 各自獨立計時） |

程式變更：`TSU_Base.h/.cpp`（模式、派送迴圈；非 ONE_DIE 模式下 multi-plane 比較對象改為該 die 第一筆被選中的交易，修正原本拿第一個 die 的 page 比對其他 die 的問題）、`TSU_Priority_OutOfOrder.cpp`（INDEPENDENT_DIES 的讀取路徑）、`NVM_PHY_ONFI_NVDDR2.h/.cpp`（`Is_die_free`、`Can_issue_independent_read`；data-out 結束時若仍有 die 在執行，chip 狀態設為 READING 而不是停在 DATA_OUT）、`Device_Parameter_Set`、`SSD_Device.cpp`；`ssdconfig.xml` 加入兩個預設值標籤。

- ONE_DIE 路徑逐行保留舊邏輯；DATA_OUT→READING 的修正只在「data-out 時仍有 die 在執行」時觸發，ONE_DIE 下不會發生。以 `mqsim_regress_20261115T_onedie` 驗證（輸入與 t65_pcie135 QD1/QD64 完全相同，除了新的預設標籤；預期 IOPS 14349.466915／660211.279229 逐位相同）。
- 限制：`Fraction_of_Time_in_Execution` 仍是 chip 層級（任一 die 在執行就算），多 die 同時執行時會低估 die 使用率。寫入／GC 與 INDEPENDENT_DIES 的交互未驗證（目前校準都是純讀、GC 關閉）。
- 對照 ONFI 與 NDSearch（ISCA 2024）：MQSim 的 Die 對應 ONFI 的 LUN（可獨立執行指令的最小單位）；Chip 對應共用 CE／I/O 的 target；channel 是一次只能一個 LUN 傳資料的共用匯流排。LUN 的排程由 SSD 控制器（flash channel controller）發指令決定，chip 內部只有每個 LUN 自己的執行狀態機。標準 multi-plane 要求各 plane 同 page 位址（NDSearch 也依此限制設計資料擺放），所以 `Multiplane_Read_Any_Page=true` 是「非標準、廠商特有的獨立 plane 讀取」假設，結論需標明。MQSim 未模擬 Read Status Enhanced／Change Read Column 的輪詢時間，也不會讓同一 page buffer 裡的第二筆讀取省掉 tR。
- **T500 硬體（2026-11-15 查證，第三方評測＋Micron 公告）**：控制器 Phison E25（4 channel），NAND 為兩顆 Micron 232L TLC B58R 封裝（MT29F8T08EULCHD5，每顆 8Tb＝1TB），1Tb die → 共 16 die（die 數由料號推算）；Micron 公告 232L 為每 die 6 plane、各 plane 可獨立讀取；DRAM 2GB LPDDR4。所以 4 ch × 16 LUN × 6 plane＝96 個 plane 與 dispatch 批次的拓撲一致。推論：T500 上限 1.05M × 數十 µs 的 tR ≫ 16，只靠 die 平行做不到，韌體幾乎必定用了 plane 層級的平行讀取；因此對 T500 而言 `Multiplane_Read_Any_Page=true` 有硬體依據（但 MQSim 是各 plane 同步啟動，Micron 的獨立讀取可能是各 plane 非同步）。
- 這個參數本身就是 NAND_TRANSACTION_BATCHING 研究的變數：`ALL_DIES`＋`Multiplane_Read_Any_Page` ≈「一次全送、等整批完成」；`INDEPENDENT_DIES` ≈「有空就送」。

### 已準備、未執行

`mqsim_calibration_20261115T_t500_cap` 作廢（原始碼 hash 已變，且使用 1 die/chip 拓撲），不執行。

共同拓撲（候選值，非查證的 T500 BOM）：4 ch × 2 chip × 2 die × 6 plane（16 die、96 plane），2400 MT/s，tR 60µs，ideal mapping，PCIe 1.35（控制器上限替身）。選這個是因為 2TB 用 1Tb die 是 16 die、4 通道控制器常見；6 plane 是對 232 層 NAND 的推測，未查證。96 個 plane 也接近先前用 Little's law 估的有效平行度約 84。

| 目錄 | 內容 |
|---|---|
| `mqsim_regress_20261115T_onedie` | 2 case：ONE_DIE 回歸測試 |
| ~~`mqsim_smoke_20261115T_dispatch`~~ | 未執行即刪除，見「T500 公開規格模型」 |
| ~~`mqsim_calibration_20261115T_t500_dispatch`~~ | 未執行即刪除，見「T500 公開規格模型」 |



## T500 公開規格模型與實驗計畫（2026-11-15）

設定來源分三類，之後所有批次都照這個分法標示：

| 類別 | 參數 | 值 | 來源 |
|---|---|---|---|
| 公開 | channel 數 | 4 | Phison E25 規格 |
| 公開 | die 數 | 16（每 channel 4） | 兩顆 8Tb 封裝 ÷ 1Tb die，由料號推算 |
| 公開 | plane 數 | 6／die，可獨立讀 | Micron 232L 公告 |
| 公開 | NAND 類型 | TLC（三種 page 讀取時間設相同） | Micron 232L |
| 公開 | NAND I/O | 2400 MT/s（4KB 約 1.7µs） | Micron 232L／E25 宣稱值 |
| 公開 | 主機介面 | PCIe 4.0 x4，`PCIe_Lane_Bandwidth=1.97` | 規格 |
| 公開 | mapping | DRAM 2GB → `Ideal_Mapping_Table=true` | 評測 |
| 選擇 | CE 分組 | 每 channel 2 chip × 2 die | 未公開；INDEPENDENT_DIES 下影響小 |
| 選擇 | page 大小 | 16KB | 232L 推測值，未查證 |
| 選擇 | 容量 | 每 plane 1024 block × 256 page（約 357GiB 邏輯容量，非 2TB） | 控制模擬器記憶體；32GiB working set 不受影響 |
| 待擬合 | tR | 先用 60µs | 未公開 |
| 待擬合 | 控制器上限 | 先不設（用公開 PCIe 值） | MQSim 無對應參數 |
| 待擬合 | 派送模式 | 四種比較 | 韌體行為未公開 |

實驗順序：
1. `mqsim_regress_20261115T_onedie`：ONE_DIE 回歸，IOPS 必須與舊結果逐位相同（14349.466915／660211.279229）。
2. `mqsim_smoke_20261115T_t500_public`：QD 1/64、3000 requests，確認 16KB page、TLC、6 plane 拓撲與新模式能跑完。
3. `mqsim_calibration_20261115T_t500_public`（32 case）：固定公開參數與 tR 60µs，比較 reference（ONE_DIE、標準 multi-plane）、indep（INDEPENDENT_DIES）、all_dies_mp（ALL_DIES＋各 plane 不同 page）、indep_mp（INDEPENDENT_DIES＋各 plane 不同 page）。問題：只用公開硬體參數，哪一種派送機制能產生 T500 的曲線形狀？預期 ONE_DIE／indep 上限遠低於 1.05M（只有 8 chip／16 die 平行），_mp 兩種的上限可能高於 1.05M（PCIe 1.97 約 158 萬上限），代表還需要控制器上限。
4. 依第 3 步結果，對選定的模式擬合 tR 與中央上限（兩個待擬合參數）；若 _mp 形狀仍不對，再實作 plane 層級的獨立派送。

<!-- calibration-run:a7e9e2d786e83eb7:begin -->
## 執行紀錄：mqsim_regress_20261115T_onedie

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_regress_20261115T_onedie`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 64 | 46.009 | 46.810 [46.746, 46.839] | 1.391 | 1.369 [1.368, 1.371] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 14349.5 | 12990.9 [12985.2, 13009.1] | 69.0 | 69.0 | 76.7 |
| reference | 321 | 64 | 660211.3 | 608126.6 [608099.0, 608208.0] | 96.0 | 96.0 | 105.0 |

### 本輪限制

- Simulator working set approx 33.3312 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_regress_20261115T_onedie/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。
<!-- calibration-run:a7e9e2d786e83eb7:end -->

## 回歸與煙霧測試結果（2026-11-15）

- **回歸 `mqsim_regress_20261115T_onedie`：通過。** QD1 14349.466915、QD64 660211.279229 IOPS，與改程式前的 t65_pcie135 逐位相同；ONE_DIE 預設路徑未受影響，之前所有結果仍有效。
- **煙霧 `mqsim_smoke_20261115T_t500_public`：8/8 完成**，3000/3000 請求全部完成，FTL_EXIT 計數全為 0。16KB page、TLC、6 plane、2400 MT/s、新派送模式都能正常執行。

| variant | QD1 IOPS | QD1 延遲 µs | QD64 IOPS | QD64 延遲 µs |
|---|---:|---:|---:|---:|
| reference（ONE_DIE） | 15,954 | 62 | 121,291 | 512 |
| indep | 15,954 | 62 | 221,690 | 280 |
| all_dies_mp | 15,954 | 62 | 482,506 | 129 |
| indep_mp | 15,954 | 62 | 491,488 | 127 |
| T500 | 12,991 | 77（fio） | 608,127 | 105（fio） |

- 只有 3000 個請求，啟動與收尾佔比高，數字只用來確認方向，不作擬合。
- QD1 62µs＝tR 60＋4KB 傳輸 1.7＋指令。
- 方向符合預期：ONE_DIE 只有 8 chip 平行；indep 已接近 16 die 的上限（16/62µs≈258k）；兩個 _mp 才有足夠平行度，但 QD64 仍低於 T500（相差約 20%）。all_dies_mp 與 indep_mp 在 QD64 只差 2%，批次派送與獨立派送的差別要看完整 QD 曲線。

<!-- calibration-run:5480029b342e4319:begin -->
## 執行紀錄：mqsim_smoke_20261115T_t500_public

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_smoke_20261115T_t500_public`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 64 | 7.602 | 46.810 [46.746, 46.839] | 8.258 | 1.369 [1.368, 1.371] |
| indep | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| indep | 321 | 64 | 13.895 | 46.810 [46.746, 46.839] | 4.516 | 1.369 [1.368, 1.371] |
| all_dies_mp | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| all_dies_mp | 321 | 64 | 30.243 | 46.810 [46.746, 46.839] | 2.081 | 1.369 [1.368, 1.371] |
| indep_mp | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| indep_mp | 321 | 64 | 30.806 | 46.810 [46.746, 46.839] | 2.048 | 1.369 [1.368, 1.371] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| reference | 321 | 64 | 121290.8 | 608126.6 [608099.0, 608208.0] | 512.0 | 512.0 | 105.0 |
| indep | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| indep | 321 | 64 | 221689.6 | 608126.6 [608099.0, 608208.0] | 280.0 | 280.0 | 105.0 |
| all_dies_mp | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| all_dies_mp | 321 | 64 | 482505.6 | 608126.6 [608099.0, 608208.0] | 129.0 | 129.0 | 105.0 |
| indep_mp | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| indep_mp | 321 | 64 | 491487.5 | 608126.6 [608099.0, 608208.0] | 127.0 | 127.0 | 105.0 |

### 本輪限制

- Simulator working set approx 32.1408 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![all_dies_mp：以QD1正規化的吞吐與延遲](results/mqsim_smoke_20261115T_t500_public/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![indep：以QD1正規化的吞吐與延遲](results/mqsim_smoke_20261115T_t500_public/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![indep_mp：以QD1正規化的吞吐與延遲](results/mqsim_smoke_20261115T_t500_public/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_smoke_20261115T_t500_public/analysis/figures/trend_v3_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。
<!-- calibration-run:5480029b342e4319:end -->

<!-- calibration-run:e27efcecc9881c46:begin -->
## 執行紀錄：mqsim_calibration_20261115T_t500_public

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261115T_t500_public`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 4 | 3.258 | 3.823 [3.818, 3.824] | 1.226 | 1.047 [1.046, 1.048] |
| reference | 321 | 16 | 6.362 | 14.794 [14.776, 14.801] | 2.532 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 7.254 | 27.511 [27.476, 27.521] | 4.452 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 7.803 | 46.810 [46.746, 46.839] | 8.274 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 8.188 | 66.322 [66.230, 66.691] | 15.774 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 8.526 | 79.088 [78.986, 79.211] | 30.274 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 9.255 | 80.722 [80.594, 80.739] | 55.726 | 6.364 [6.363, 6.374] |
| indep | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| indep | 321 | 4 | 3.611 | 3.823 [3.818, 3.824] | 1.113 | 1.047 [1.046, 1.048] |
| indep | 321 | 16 | 9.593 | 14.794 [14.776, 14.801] | 1.677 | 1.083 [1.082, 1.084] |
| indep | 321 | 32 | 12.469 | 27.511 [27.476, 27.521] | 2.581 | 1.164 [1.164, 1.166] |
| indep | 321 | 64 | 14.210 | 46.810 [46.746, 46.839] | 4.548 | 1.369 [1.368, 1.371] |
| indep | 321 | 128 | 15.122 | 66.322 [66.230, 66.691] | 8.532 | 1.934 [1.924, 1.937] |
| indep | 321 | 256 | 15.977 | 79.088 [78.986, 79.211] | 16.129 | 3.247 [3.241, 3.251] |
| indep | 321 | 512 | 17.309 | 80.722 [80.594, 80.739] | 29.758 | 6.364 [6.363, 6.374] |
| all_dies_mp | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| all_dies_mp | 321 | 4 | 3.376 | 3.823 [3.818, 3.824] | 1.194 | 1.047 [1.046, 1.048] |
| all_dies_mp | 321 | 16 | 10.579 | 14.794 [14.776, 14.801] | 1.516 | 1.083 [1.082, 1.084] |
| all_dies_mp | 321 | 32 | 18.985 | 27.511 [27.476, 27.521] | 1.694 | 1.164 [1.164, 1.166] |
| all_dies_mp | 321 | 64 | 30.985 | 46.810 [46.746, 46.839] | 2.081 | 1.369 [1.368, 1.371] |
| all_dies_mp | 321 | 128 | 42.973 | 66.322 [66.230, 66.691] | 3.000 | 1.934 [1.924, 1.937] |
| all_dies_mp | 321 | 256 | 51.132 | 79.088 [78.986, 79.211] | 5.016 | 3.247 [3.241, 3.251] |
| all_dies_mp | 321 | 512 | 55.136 | 80.722 [80.594, 80.739] | 9.274 | 6.364 [6.363, 6.374] |
| indep_mp | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| indep_mp | 321 | 4 | 3.643 | 3.823 [3.818, 3.824] | 1.097 | 1.047 [1.046, 1.048] |
| indep_mp | 321 | 16 | 11.700 | 14.794 [14.776, 14.801] | 1.371 | 1.083 [1.082, 1.084] |
| indep_mp | 321 | 32 | 20.083 | 27.511 [27.476, 27.521] | 1.597 | 1.164 [1.164, 1.166] |
| indep_mp | 321 | 64 | 31.520 | 46.810 [46.746, 46.839] | 2.048 | 1.369 [1.368, 1.371] |
| indep_mp | 321 | 128 | 43.083 | 66.322 [66.230, 66.691] | 2.984 | 1.934 [1.924, 1.937] |
| indep_mp | 321 | 256 | 51.147 | 79.088 [78.986, 79.211] | 5.016 | 3.247 [3.241, 3.251] |
| indep_mp | 321 | 512 | 55.155 | 80.722 [80.594, 80.739] | 9.274 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| reference | 321 | 4 | 51976.9 | 49665.7 [49658.1, 49669.6] | 76.0 | 76.0 | 80.2 |
| reference | 321 | 16 | 101500.3 | 192197.1 [192192.9, 192225.0] | 157.0 | 157.0 | 83.0 |
| reference | 321 | 32 | 115725.7 | 357396.3 [357370.7, 357436.4] | 276.0 | 276.0 | 89.3 |
| reference | 321 | 64 | 124489.4 | 608126.6 [608099.0, 608208.0] | 513.0 | 513.0 | 105.0 |
| reference | 321 | 128 | 130628.2 | 861585.6 [860001.6, 867582.5] | 978.0 | 978.0 | 148.3 |
| reference | 321 | 256 | 136029.3 | 1027533.8 [1026965.1, 1029017.8] | 1877.0 | 1877.0 | 248.9 |
| reference | 321 | 512 | 147656.4 | 1048411.2 [1046989.6, 1050117.4] | 3455.0 | 3455.0 | 488.1 |
| indep | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| indep | 321 | 4 | 57604.1 | 49665.7 [49658.1, 49669.6] | 69.0 | 69.0 | 80.2 |
| indep | 321 | 16 | 153044.3 | 192197.1 [192192.9, 192225.0] | 104.0 | 104.0 | 83.0 |
| indep | 321 | 32 | 198939.6 | 357396.3 [357370.7, 357436.4] | 160.0 | 160.0 | 89.3 |
| indep | 321 | 64 | 226709.3 | 608126.6 [608099.0, 608208.0] | 282.0 | 282.0 | 105.0 |
| indep | 321 | 128 | 241262.8 | 861585.6 [860001.6, 867582.5] | 529.0 | 529.0 | 148.3 |
| indep | 321 | 256 | 254899.2 | 1027533.8 [1026965.1, 1029017.8] | 1000.0 | 1000.0 | 248.9 |
| indep | 321 | 512 | 276155.1 | 1048411.2 [1046989.6, 1050117.4] | 1845.0 | 1845.0 | 488.1 |
| all_dies_mp | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| all_dies_mp | 321 | 4 | 53864.9 | 49665.7 [49658.1, 49669.6] | 74.0 | 74.0 | 80.2 |
| all_dies_mp | 321 | 16 | 168778.5 | 192197.1 [192192.9, 192225.0] | 94.0 | 94.0 | 83.0 |
| all_dies_mp | 321 | 32 | 302892.6 | 357396.3 [357370.7, 357436.4] | 105.0 | 105.0 | 89.3 |
| all_dies_mp | 321 | 64 | 494343.4 | 608126.6 [608099.0, 608208.0] | 129.0 | 129.0 | 105.0 |
| all_dies_mp | 321 | 128 | 685605.4 | 861585.6 [860001.6, 867582.5] | 186.0 | 186.0 | 148.3 |
| all_dies_mp | 321 | 256 | 815772.6 | 1027533.8 [1026965.1, 1029017.8] | 311.0 | 311.0 | 248.9 |
| all_dies_mp | 321 | 512 | 879663.8 | 1048411.2 [1046989.6, 1050117.4] | 575.0 | 575.0 | 488.1 |
| indep_mp | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| indep_mp | 321 | 4 | 58115.0 | 49665.7 [49658.1, 49669.6] | 68.0 | 68.0 | 80.2 |
| indep_mp | 321 | 16 | 186661.7 | 192197.1 [192192.9, 192225.0] | 85.0 | 85.0 | 83.0 |
| indep_mp | 321 | 32 | 320416.5 | 357396.3 [357370.7, 357436.4] | 99.0 | 99.0 | 89.3 |
| indep_mp | 321 | 64 | 502873.2 | 608126.6 [608099.0, 608208.0] | 127.0 | 127.0 | 105.0 |
| indep_mp | 321 | 128 | 687365.3 | 861585.6 [860001.6, 867582.5] | 185.0 | 185.0 | 148.3 |
| indep_mp | 321 | 256 | 816022.5 | 1027533.8 [1026965.1, 1029017.8] | 311.0 | 311.0 | 248.9 |
| indep_mp | 321 | 512 | 879964.5 | 1048411.2 [1046989.6, 1050117.4] | 575.0 | 575.0 | 488.1 |

### 本輪限制

- Simulator working set approx 32.1408 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![all_dies_mp：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![all_dies_mp：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![indep：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![indep：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![indep_mp：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![indep_mp：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v2_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v3_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_public/analysis/figures/trend_v3_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:e27efcecc9881c46:end -->

## 推論：T500 隨機 4K 讀取一定用到 plane 層級的平行（2026-11-15）

結論：T500 在 QD512 的隨機 4K 讀取，同時在 NAND 陣列上進行的讀取遠多於 16 個 die，只能來自同一個 die 內多個 plane 同時讀；而且隨機位址幾乎不會「各 plane 同 page 位址」，所以應該是「各 plane 可讀不同 page」的獨立 plane 讀取。這是推論，不是直接量測。

### 前提（每一項的來源與可靠度）

| # | 前提 | 來源 | 可靠度 |
|---|---|---|---|
| P1 | 隨機 4K 讀取上限 X ≥ 1.05M IOPS | `mqsim_long_20261114T103323Z_b415d92e`，4 job、QD512，三次重複差 <1% | 量測。若仍有主機限制，真實裝置能力只會更高，結論只會更強，所以這裡當下限使用 |
| P2 | 16 個 NAND die | 兩顆 MT29F8T08 封裝（8Tb＝1TB）÷ 232L 的 1Tb die | 料號推算＋AnandTech；評測未直接寫 die 數 |
| P3 | 每個 die 6 個 plane，可獨立讀 | Micron 232L 公告（AnandTech） | 廠商宣稱，無第三方實測 |
| P4 | 讀到的資料都在 NAND，不是控制器 DRAM 快取 | working set 32GiB 均勻隨機；DRAM 只有 2GB 且主要放 mapping 表 | 就算 2GB 全部拿來快取資料，命中率也只有約 6%，不足以解釋上限 |
| P5 | 每次 4K 讀取至少佔用一個 NAND 平行單位一次 tR（array 讀取時間） | NAND 原理：資料要先從 cell 讀到 page buffer 才能傳出 | 原理 |
| P6 | TLC 的 tR 遠大於 15µs | T500 的 tR 未公開；TLC 一般數十 µs，連 SLC 也約 20µs 以上 | 業界常識範圍，非 T500 量測；QD1 端到端 74µs（p50）只給上界 tR < ~70µs |

### 推理步驟

1. **Little's law**：同時在服務中的讀取數 N＝吞吐 X × 每筆佔用平行單位的時間 S。每筆讀取至少佔一次 tR（P5），所以 N ≥ X × tR。
2. **只靠 16 個 die 時每個 die 的時間預算**：如果每個 die 一次只能做一筆讀取，16 個 die 要撐住 1.05M IOPS，每個 die 每 16 / 1.05M ≈ **15.2µs** 就要完成一筆（tR＋4KB 傳出約 1.7µs＋指令），等於 tR 要 ≤ 約 13µs。
3. **這與 TLC 的 tR 不符**（P6）。換成需要的同時讀取數來看：

   | 假設 tR | 需要同時進行的讀取 N ≥ 1.05M × tR | 是 16 個 die 的幾倍 |
   |---:|---:|---:|
   | 40µs | 42 | 2.6× |
   | 50µs | 53 | 3.3× |
   | 60µs | 63 | 3.9× |
   | 70µs | 74 | 4.6× |

4. **排除其他解釋**：
   - 控制器 DRAM 快取：P4 排除。
   - die 其實更多：如果是 512Gb die（32 die），每 die 預算約 30µs，仍要求 tR ≤ 約 28µs，對 TLC 不合理；而且 232L 是 1Tb die。
   - SLC 快取：資料是很久以前寫入的 index 檔，應已搬到 TLC；就算在 SLC，tR 約 20µs 以上仍超過 13µs 預算。
   - 標準 multi-plane（各 plane 同 page 位址）：均勻隨機讀取下，同一個 die 裡同時有兩筆讀取剛好同 page 位址的機率極低，湊不出 3–5 倍的平行度。
5. **剩下的解釋**：同一個 die 內多個 plane 同時讀、而且各 plane 讀不同 page，也就是 P3 的獨立 plane 讀取。16 die × 6 plane＝96 個單位，足以提供第 3 步需要的 42–74 個同時讀取，也和 4-job 掃描時粗估的有效平行度約 84（1.05M × 80µs，含主機與傳輸時間，偏高估）同一量級。

### 模擬上的佐證（較弱，依賴模型）

- `mqsim_calibration_20261115T_t500_public`：公開拓撲、tR 60µs 下，只有 die 層級平行的 indep 上限 276k，ONE_DIE 148k，都遠低於 1.05M；必須開 plane 平行（_mp）才到 880k。
- 同一批的 indep_mp 在 QD16 延遲成長 1.37，真機只有 1.08；模型中 plane 必須等同 die 的讀取結束才能開始，真機看起來沒有這個等待，支持「各 plane 獨立開始」。

### 會推翻結論的情況

- T500 的 tR 實際 ≤ 約 13µs（與 TLC 物理不符，可能性極低）。
- die 數遠多於 16（與料號和 232L die 容量矛盾）。
- 量測的 1.05M 有大量讀取沒有真的到 NAND（例如控制器有未知的大型讀取快取）；P4 的容量估算不支持這種情況。

## T500 公開規格結果解讀（mqsim_calibration_20261115T_t500_public，2026-11-15）

32 case 全部完成，FTL_EXIT 計數全為 0。公開參數固定、tR 60µs、PCIe 1.97。

| QD | 1 | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T500 IOPS (k) | 13 | 50 | 192 | 357 | 608 | 862 | 1028 | 1048 |
| reference（ONE_DIE） | 16 | 52 | 102 | 116 | 124 | 131 | 136 | 148 |
| indep | 16 | 58 | 153 | 199 | 227 | 241 | 255 | 276 |
| all_dies_mp | 16 | 54 | 169 | 303 | 494 | 686 | 816 | 880 |
| indep_mp | 16 | 58 | 187 | 320 | 503 | 687 | 816 | 880 |
| T500 延遲成長 | 1.00 | 1.05 | 1.08 | 1.16 | 1.37 | 1.93 | 3.25 | 6.37 |
| indep_mp 延遲成長 | 1.00 | 1.10 | 1.37 | 1.60 | 2.05 | 2.98 | 5.02 | 9.27 |

- 只用 die 層級平行（reference、indep）完全不夠：上限 15–28 萬，證實 T500 必須用到 plane 層級的平行讀取。
- indep_mp 最接近，但兩個問題：
  1. **低／中 QD 延遲成長太快**：QD16 時 1.37（真機 1.08）。QD16 共發了 71,605 個單 plane 指令、13,149 個 multi-plane 指令：請求是一個一個到的，die 一旦開始讀，同 die 其他 plane 的新請求就要等 tR 結束，無法湊進去。真機幾乎不成長，表示它的 plane 是各自獨立開始的。
  2. **上限只有 880k**（PCIe 1.97 的上限約 158 萬，不是它）：QD512 平均每個指令 5.4 個 plane，但每個指令週期約 98µs（16 die × 1159 個指令／0.114 s），其中 60µs tR、其餘是 5–6 頁依序傳出（各 1.7µs）加上同 channel 4 個 die 搶匯流排；die 要等自己所有頁傳完才能接下一個指令。
- all_dies_mp 與 indep_mp 上限相同，差別在中低 QD（QD16：169k vs 187k；延遲成長 1.52 vs 1.37）：「一次全送、等整批做完」在負載輕時讓管線吃不飽。這是 NAND_TRANSACTION_BATCHING 研究直接相關的結果。
- QD1：sim 62µs vs fio 77µs，差 15µs（含主機軟體），tR 還沒擬合。

### MQSim 的 channel 模型與 multi-plane 指令時間 bug（2026-11-15）

使用者問：高 QD 延遲差距會不會是 channel 競爭？查 `NVM_PHY_ONFI_NVDDR2`／`ONFI_Channel_NVDDR2` 後的 channel 模型：

- 每個 channel 一條匯流排，只有 BUSY／IDLE，一次只能一個 chip 使用。
- 每筆讀取佔用匯流排兩次：指令／位址（`ReadCommandTime[n]`，1 plane 0.29µs，每多一個 plane 多 0.65µs，主要是 t_DBSY 500ns）＋每筆 4KB 資料傳出（2400 MT/s 約 1.71µs）。`ReadDataOutSetupTime*` 定義了但沒有被使用（data-out 前置時間未計）。
- 匯流排空出來時的優先序：copyback → 等待中的 mapping 讀取傳出 → 等待中的使用者讀取傳出（FIFO）→ GC 讀取傳出 → 都沒有才通知 TSU 輪流派新指令。傳出資料優先於新指令。
- die（或 plane 組）要等自己所有資料傳出才算空閒；**沒有模擬 cache read**（真實 NAND 可以在傳出上一頁時開始下一次 tR）。
- 高 QD 飽和時，延遲＝QD／吞吐（Little's law）：真機 512／1.05M＝488µs，indep_mp 512／0.88M＝575µs。所以高 QD 延遲差距完全來自上限差距；channel 只有在限制上限時才會影響。
- 負載估算：真機 1.05M 平均到 4 channel，每 channel 262k 讀取/s × 約 2µs（指令＋資料，各 plane 各自下指令時）≈ 52%，沒有飽和；但在同步 multi-plane 模型裡，die 要等 5–6 頁（約 10µs）傳完，還要排在同 channel 其他 die 的頁後面，所以 channel 等待會被放大成 die 的佔用時間。QD16–64 時 channel 負載只有約 10–30%，不是中段延遲成長的主因（主因是 plane 必須等同 die 的讀取結束）。

**發現 bug**：`ReadCommandTime`、`ProgramCommandTime`、`EraseCommandTime` 陣列大小是 5（只定義 1–4 個 plane），6 plane 拓撲的 5／6 plane 指令讀到陣列外的相鄰欄位：5 plane 指令只算 30ns（應為 2,890ns）、6 plane 只算 610ns（應為 3,540ns）。

- 修正：`ONFI_Channel_NVDDR2.h/.cpp` 改成依 plane 數 n 用公式計算（上限 16，超過會報錯）；n＝1–4 時與原本的式子逐項相同（已用預設時序數值驗證）。
- 影響：只有 `mqsim_calibration_20261115T_t500_public` 的 all_dies_mp、indep_mp（QD512 平均每指令 5.4 plane）受影響，channel 時間被低估約每 channel 12% 的匯流排時間，所以修正後這兩組會更慢。reference／indep（需要同 page 位址才能合併，極少 5 plane 以上）影響可忽略。之前所有 ≤4 plane 的批次與回歸結果不受影響。
- 該批 _mp 的數字應視為「略為樂觀」；修正後的版本放在下一批的 phys_indep_mp。

### 下一批（已準備，未執行）：`mqsim_calibration_20261115T_t500_vplane`

做法：把每個 plane 當成一個可獨立執行的單位（虛擬 LUN）：4 ch × 2 chip × **12 die × 1 plane**（＝2 個實體 die × 6 plane），搭配 `INDEPENDENT_DIES`。對純讀取來說，這就是「各 plane 獨立讀取」的近似：每個 plane 有自己的指令、自己的 tR、讀完傳完就能接下一筆；匯流排仍共用。限制：忽略同一實體 die 內各 plane 共用周邊電路可能造成的干擾；寫入與 multi-plane program 不適用。40 case（含 bug 修正後的 binary）：

| variant | 改動 | 用途 |
|---|---|---|
| reference | tR 60、PCIe 1.97、2400 MT/s（純公開值） | 獨立 plane 能否修正中低 QD 的延遲成長；上限預期超過 1.05M |
| fastch | 通道 24000 MT/s（傳 4KB 約 0.17µs） | 與 reference 只差 channel 速度：差異＝channel 競爭的影響 |
| pcie135 | PCIe 1.35 | 加上中央上限替身（約 108 萬） |
| t70_pcie135 | tR 70、PCIe 1.35 | QD1 約 72µs，對齊扣掉主機開銷後的真機延遲 |
| phys_indep_mp | 實體拓撲 2 die × 6 plane、INDEPENDENT_DIES、各 plane 可讀不同 page | indep_mp 的 bug 修正版，與虛擬 LUN 版直接比較 |

## 每 plane 虛擬 LUN 結果解讀（mqsim_calibration_20261115T_t500_vplane，2026-11-15）

40 case 全部完成，FTL_EXIT 計數全為 0。共同設定：4 ch × 2 chip × 12 die × 1 plane、INDEPENDENT_DIES、16KB page、TLC、ideal mapping。

| QD | 1 | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T500 IOPS (k) | 13 | 50 | 192 | 357 | 608 | 862 | 1028 | 1048 |
| reference（tR60, PCIe 1.97, 2400MT/s） | 16 | 63 | 232 | 420 | 663 | 778 | 847 | 892 |
| fastch（同上，24000MT/s） | 16 | 64 | 240 | 437 | 728 | 1047 | 1284 | 1419 |
| pcie135 | 16 | 62 | 231 | 418 | 669 | 800 | 858 | 895 |
| t70_pcie135 | 14 | 54 | 200 | 362 | 589 | 722 | 778 | 813 |
| phys_indep_mp（bug 修正後） | 16 | 58 | 187 | 320 | 503 | 682 | 800 | 857 |

| 延遲成長（÷QD1） | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|
| T500 | 1.08 | 1.16 | 1.37 | 1.93 | 3.25 | 6.37 |
| reference | 1.10 | 1.23 | 1.55 | 2.65 | 4.84 | 9.15 |
| fastch | 1.08 | 1.20 | 1.43 | 1.98 | 3.23 | 5.80 |
| t70_pcie135 | 1.11 | 1.22 | 1.50 | 2.44 | 4.53 | 8.62 |
| phys_indep_mp | 1.37 | 1.60 | 2.05 | 3.02 | 5.13 | 9.52 |

1. **各 plane 獨立讀取修正了中低 QD**：QD16 延遲成長 1.10（真機 1.08，同步 multi-plane 的 phys_indep_mp 是 1.37）。確認中段的差距來自「plane 必須等同 die 的讀取結束」。
2. **t70_pcie135 的低 QD 絕對值對上**：QD1–64 的 IOPS 是真機的 0.97–1.08 倍。
3. **上限卡在約 0.8–0.9M，原因是 channel，不是 PCIe**：PCIe 1.97→1.35 幾乎沒差（892k→895k）；channel 加速 10 倍（fastch）上限升到 1.42M，而且 QD128–256 的增益與延遲成長幾乎完全等於真機（增益 64.0／78.5 vs 66.3／79.1；延遲成長 1.98／3.23 vs 1.93／3.25）。回答「是不是 channel 競爭」：**在這個模型的高 QD，是**。
4. **但 channel 並沒有忙滿**：reference QD512 時 channel 只有約 45% 時間在傳輸（每 chip data-xfer 0.223 × 2 chip），而每顆 chip「有任一 die 在執行」的時間反而從 QD64 的 0.99 掉到 QD512 的 0.74，也就是請求在排隊、die 卻閒著。每個 plane 單位一個讀取週期約 107µs（892k／96），扣掉 tR 60 與傳輸 2µs，約 45µs 在等。
   - 推測原因（排程政策造成的車隊效應）：PHY 在匯流排空出來時，**一定先把所有等待中的資料傳完，才通知 TSU 派新指令**（DATA_OUT_FIRST）。tR 是固定值，同一批派出去的 plane 會同時讀完，接著它們的資料要一筆一筆傳（12 筆 × 1.7µs ≈ 20µs）；已經傳完、空出來的 plane 也要等整串傳完才拿得到新指令。channel 越慢，這段空等越長，所以 fastch 能消除它。
   - 真機的控制器多半會穿插：讀取指令只佔匯流排約 0.29µs，卻能啟動 60–70µs 的 tR，先派指令可以讓更多 plane 保持忙碌。
5. phys_indep_mp（bug 修正後）上限 857k，比修正前 880k 低，符合預期。

### MQSim 修改：`Channel_Arbitration`

新增 device 參數 `Channel_Arbitration`：`DATA_OUT_FIRST`（預設，原行為）或 `COMMAND_FIRST`（匯流排空出來時先讓 TSU 派指令；TSU 沒派任何指令時，才傳等待中的資料）。只改 `NVM_PHY_ONFI_NVDDR2::Execute_simulator_event` 的尾段順序；copyback 仍最優先。不會無限延後資料傳出：只有空閒且有工作的 die 能接新指令，等待傳出的 die 不是空閒的。這也是批次研究的一個變數（指令 vs 資料傳出誰優先）。

### 下一批（已準備，未執行）

- `mqsim_regress_20261115T_arbitration`：預設仲裁下重跑 t70_pcie135 的 QD1／QD64，輸入除新標籤外完全相同，IOPS 必須逐位等於 13702.008713／589129.565074。
- `mqsim_calibration_20261115T_t500_cmdfirst`（24 case）：vplane 拓撲、tR 70、COMMAND_FIRST。

| variant | 改動 | 用途 |
|---|---|---|
| reference | PCIe 1.97、2400MT/s | 換成 COMMAND_FIRST 後，車隊效應是否消失、上限是否接近 fastch |
| pcie135 | PCIe 1.35 | 加上中央上限替身後，QD256–512 是否對上 1.03–1.05M |
| fastch_pcie135 | 24000MT/s、PCIe 1.35 | 不受 channel 限制時的對照 |

<!-- calibration-run:e9fd465f5c547af1:begin -->
## 執行紀錄：mqsim_calibration_20261115T_t500_vplane

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261115T_t500_vplane`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 4 | 3.921 | 3.823 [3.818, 3.824] | 1.016 | 1.047 [1.046, 1.048] |
| reference | 321 | 16 | 14.556 | 14.794 [14.776, 14.801] | 1.097 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 26.307 | 27.511 [27.476, 27.521] | 1.226 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 41.581 | 46.810 [46.746, 46.839] | 1.548 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 48.770 | 66.322 [66.230, 66.691] | 2.645 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 53.081 | 79.088 [78.986, 79.211] | 4.839 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 55.907 | 80.722 [80.594, 80.739] | 9.145 | 6.364 [6.363, 6.374] |
| fastch | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| fastch | 321 | 4 | 3.920 | 3.823 [3.818, 3.824] | 1.016 | 1.047 [1.046, 1.048] |
| fastch | 321 | 16 | 14.664 | 14.794 [14.776, 14.801] | 1.082 | 1.083 [1.082, 1.084] |
| fastch | 321 | 32 | 26.731 | 27.511 [27.476, 27.521] | 1.197 | 1.164 [1.164, 1.166] |
| fastch | 321 | 64 | 44.512 | 46.810 [46.746, 46.839] | 1.426 | 1.369 [1.368, 1.371] |
| fastch | 321 | 128 | 64.038 | 66.322 [66.230, 66.691] | 1.984 | 1.934 [1.924, 1.937] |
| fastch | 321 | 256 | 78.482 | 79.088 [78.986, 79.211] | 3.230 | 3.247 [3.241, 3.251] |
| fastch | 321 | 512 | 86.744 | 80.722 [80.594, 80.739] | 5.803 | 6.364 [6.363, 6.374] |
| pcie135 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| pcie135 | 321 | 4 | 3.920 | 3.823 [3.818, 3.824] | 1.032 | 1.047 [1.046, 1.048] |
| pcie135 | 321 | 16 | 14.551 | 14.794 [14.776, 14.801] | 1.113 | 1.083 [1.082, 1.084] |
| pcie135 | 321 | 32 | 26.322 | 27.511 [27.476, 27.521] | 1.226 | 1.164 [1.164, 1.166] |
| pcie135 | 321 | 64 | 42.153 | 46.810 [46.746, 46.839] | 1.532 | 1.369 [1.368, 1.371] |
| pcie135 | 321 | 128 | 50.408 | 66.322 [66.230, 66.691] | 2.565 | 1.934 [1.924, 1.937] |
| pcie135 | 321 | 256 | 54.066 | 79.088 [78.986, 79.211] | 4.774 | 3.247 [3.241, 3.251] |
| pcie135 | 321 | 512 | 56.397 | 80.722 [80.594, 80.739] | 9.113 | 6.364 [6.363, 6.374] |
| t70_pcie135 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| t70_pcie135 | 321 | 4 | 3.922 | 3.823 [3.818, 3.824] | 1.028 | 1.047 [1.046, 1.048] |
| t70_pcie135 | 321 | 16 | 14.579 | 14.794 [14.776, 14.801] | 1.111 | 1.083 [1.082, 1.084] |
| t70_pcie135 | 321 | 32 | 26.431 | 27.511 [27.476, 27.521] | 1.222 | 1.164 [1.164, 1.166] |
| t70_pcie135 | 321 | 64 | 42.996 | 46.810 [46.746, 46.839] | 1.500 | 1.369 [1.368, 1.371] |
| t70_pcie135 | 321 | 128 | 52.704 | 66.322 [66.230, 66.691] | 2.444 | 1.934 [1.924, 1.937] |
| t70_pcie135 | 321 | 256 | 56.757 | 79.088 [78.986, 79.211] | 4.528 | 3.247 [3.241, 3.251] |
| t70_pcie135 | 321 | 512 | 59.356 | 80.722 [80.594, 80.739] | 8.625 | 6.364 [6.363, 6.374] |
| phys_indep_mp | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| phys_indep_mp | 321 | 4 | 3.643 | 3.823 [3.818, 3.824] | 1.097 | 1.047 [1.046, 1.048] |
| phys_indep_mp | 321 | 16 | 11.704 | 14.794 [14.776, 14.801] | 1.371 | 1.083 [1.082, 1.084] |
| phys_indep_mp | 321 | 32 | 20.088 | 27.511 [27.476, 27.521] | 1.597 | 1.164 [1.164, 1.166] |
| phys_indep_mp | 321 | 64 | 31.539 | 46.810 [46.746, 46.839] | 2.048 | 1.369 [1.368, 1.371] |
| phys_indep_mp | 321 | 128 | 42.734 | 66.322 [66.230, 66.691] | 3.016 | 1.934 [1.924, 1.937] |
| phys_indep_mp | 321 | 256 | 50.131 | 79.088 [78.986, 79.211] | 5.129 | 3.247 [3.241, 3.251] |
| phys_indep_mp | 321 | 512 | 53.713 | 80.722 [80.594, 80.739] | 9.516 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| reference | 321 | 4 | 62556.9 | 49665.7 [49658.1, 49669.6] | 63.0 | 63.0 | 80.2 |
| reference | 321 | 16 | 232231.6 | 192197.1 [192192.9, 192225.0] | 68.0 | 68.0 | 83.0 |
| reference | 321 | 32 | 419706.5 | 357396.3 [357370.7, 357436.4] | 76.0 | 76.0 | 89.3 |
| reference | 321 | 64 | 663396.8 | 608126.6 [608099.0, 608208.0] | 96.0 | 96.0 | 105.0 |
| reference | 321 | 128 | 778093.1 | 861585.6 [860001.6, 867582.5] | 164.0 | 164.0 | 148.3 |
| reference | 321 | 256 | 846864.2 | 1027533.8 [1026965.1, 1029017.8] | 300.0 | 300.0 | 248.9 |
| reference | 321 | 512 | 891965.1 | 1048411.2 [1046989.6, 1050117.4] | 567.0 | 567.0 | 488.1 |
| fastch | 321 | 1 | 16355.1 | 12990.9 [12985.2, 13009.1] | 61.0 | 61.0 | 76.7 |
| fastch | 321 | 4 | 64118.1 | 49665.7 [49658.1, 49669.6] | 62.0 | 62.0 | 80.2 |
| fastch | 321 | 16 | 239830.0 | 192197.1 [192192.9, 192225.0] | 66.0 | 66.0 | 83.0 |
| fastch | 321 | 32 | 437191.8 | 357396.3 [357370.7, 357436.4] | 73.0 | 73.0 | 89.3 |
| fastch | 321 | 64 | 728006.3 | 608126.6 [608099.0, 608208.0] | 87.0 | 87.0 | 105.0 |
| fastch | 321 | 128 | 1047344.9 | 861585.6 [860001.6, 867582.5] | 121.0 | 121.0 | 148.3 |
| fastch | 321 | 256 | 1283579.5 | 1027533.8 [1026965.1, 1029017.8] | 197.0 | 197.0 | 248.9 |
| fastch | 321 | 512 | 1418710.7 | 1048411.2 [1046989.6, 1050117.4] | 354.0 | 354.0 | 488.1 |
| pcie135 | 321 | 1 | 15877.6 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| pcie135 | 321 | 4 | 62236.2 | 49665.7 [49658.1, 49669.6] | 64.0 | 64.0 | 80.2 |
| pcie135 | 321 | 16 | 231035.5 | 192197.1 [192192.9, 192225.0] | 69.0 | 69.0 | 83.0 |
| pcie135 | 321 | 32 | 417932.2 | 357396.3 [357370.7, 357436.4] | 76.0 | 76.0 | 89.3 |
| pcie135 | 321 | 64 | 669286.5 | 608126.6 [608099.0, 608208.0] | 95.0 | 95.0 | 105.0 |
| pcie135 | 321 | 128 | 800362.0 | 861585.6 [860001.6, 867582.5] | 159.0 | 159.0 | 148.3 |
| pcie135 | 321 | 256 | 858430.8 | 1027533.8 [1026965.1, 1029017.8] | 296.0 | 296.0 | 248.9 |
| pcie135 | 321 | 512 | 895443.5 | 1048411.2 [1046989.6, 1050117.4] | 565.0 | 565.0 | 488.1 |
| t70_pcie135 | 321 | 1 | 13702.0 | 12990.9 [12985.2, 13009.1] | 72.0 | 72.0 | 76.7 |
| t70_pcie135 | 321 | 4 | 53744.0 | 49665.7 [49658.1, 49669.6] | 74.0 | 74.0 | 80.2 |
| t70_pcie135 | 321 | 16 | 199758.6 | 192197.1 [192192.9, 192225.0] | 80.0 | 80.0 | 83.0 |
| t70_pcie135 | 321 | 32 | 362153.7 | 357396.3 [357370.7, 357436.4] | 88.0 | 88.0 | 89.3 |
| t70_pcie135 | 321 | 64 | 589129.6 | 608126.6 [608099.0, 608208.0] | 108.0 | 108.0 | 105.0 |
| t70_pcie135 | 321 | 128 | 722144.7 | 861585.6 [860001.6, 867582.5] | 176.0 | 176.0 | 148.3 |
| t70_pcie135 | 321 | 256 | 777686.7 | 1027533.8 [1026965.1, 1029017.8] | 326.0 | 326.0 | 248.9 |
| t70_pcie135 | 321 | 512 | 813301.1 | 1048411.2 [1046989.6, 1050117.4] | 621.0 | 621.0 | 488.1 |
| phys_indep_mp | 321 | 1 | 15954.3 | 12990.9 [12985.2, 13009.1] | 62.0 | 62.0 | 76.7 |
| phys_indep_mp | 321 | 4 | 58115.0 | 49665.7 [49658.1, 49669.6] | 68.0 | 68.0 | 80.2 |
| phys_indep_mp | 321 | 16 | 186730.0 | 192197.1 [192192.9, 192225.0] | 85.0 | 85.0 | 83.0 |
| phys_indep_mp | 321 | 32 | 320485.3 | 357396.3 [357370.7, 357436.4] | 99.0 | 99.0 | 89.3 |
| phys_indep_mp | 321 | 64 | 503183.9 | 608126.6 [608099.0, 608208.0] | 127.0 | 127.0 | 105.0 |
| phys_indep_mp | 321 | 128 | 681794.3 | 861585.6 [860001.6, 867582.5] | 187.0 | 187.0 | 148.3 |
| phys_indep_mp | 321 | 256 | 799806.8 | 1027533.8 [1026965.1, 1029017.8] | 318.0 | 318.0 | 248.9 |
| phys_indep_mp | 321 | 512 | 856945.8 | 1048411.2 [1046989.6, 1050117.4] | 590.0 | 590.0 | 488.1 |

### 本輪限制

- Simulator working set approx 32.1408 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![fastch：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![fastch：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![pcie135：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![pcie135：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![phys_indep_mp：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![phys_indep_mp：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v2_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v3_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v3_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![t70_pcie135：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v4_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![t70_pcie135：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_vplane/analysis/figures/trend_v4_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:e9fd465f5c547af1:end -->

## COMMAND_FIRST 結果：T500 校準候選完成（mqsim_calibration_20261115T_t500_cmdfirst，2026-11-15）

- **回歸 `mqsim_regress_20261115T_arbitration`：通過**，預設仲裁下 13702.008713／589129.565074 IOPS 與上一批逐位相同；`Channel_Arbitration` 預設不改變任何結果。
- cmdfirst 24 case 全部完成，FTL_EXIT 計數全為 0。

| QD | 1 | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T500 IOPS (k) | 13.0 | 49.7 | 192 | 357 | 608 | 862 | 1028 | 1048 |
| reference（PCIe 1.97） | 13.8 | 54 | 201 | 367 | 610 | 877 | 1075 | 1189 |
| **pcie135** | 13.7 | 54 | 200 | 365 | 607 | 873 | 1054 | 1059 |
| fastch_pcie135 | 14.0 | 55 | 205 | 373 | 622 | 894 | 1059 | 1061 |
| pcie135 ÷ T500 | 1.05 | 1.08 | 1.04 | 1.02 | 1.00 | 1.01 | 1.03 | 1.01 |

| | 4 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---:|---:|---:|---:|---:|---:|---:|
| T500 增益（÷QD1） | 3.8 | 14.8 | 27.5 | 46.8 | 66.3 | 79.1 | 80.7 |
| pcie135 增益 | 3.9 | 14.6 | 26.6 | 44.3 | 63.7 | 76.9 | 77.3 |
| T500 延遲成長 | 1.05 | 1.08 | 1.16 | 1.37 | 1.93 | 3.25 | 6.37 |
| pcie135 延遲成長 | 1.03 | 1.10 | 1.21 | 1.46 | 2.03 | 3.33 | 6.64 |
| T500 fio 延遲 µs | 80 | 83 | 89 | 105 | 148 | 249 | 488 |
| pcie135 裝置延遲 µs | 74 | 79 | 87 | 105 | 146 | 240 | 478 |

1. **車隊效應假設成立**：COMMAND_FIRST 後，QD512 每顆 chip「有任一 die 在執行」從 0.74 回到 0.99；上限從 813k（DATA_OUT_FIRST、同 tR）升到 1.19M（PCIe 1.97），並與「channel 快 10 倍」的對照幾乎相同（pcie135 1,059k vs fastch_pcie135 1,061k）。2400 MT/s 的 channel 在 QD512 約 53–59% 忙碌，不再限制上限。
2. **pcie135 是目前最佳候選**：各 QD 絕對 IOPS 與 T500 差 0–8%；吞吐增益差 ≤5%；延遲成長在 QD128 以下差 ≤7%，QD512 差 4%。
3. 剩餘差異：低 QD 的 sim IOPS 高 4–8%，因為 sim 延遲不含主機軟體（QD1：72 vs 77µs）；QD32–64 延遲成長略高（1.21／1.46 vs 1.16／1.37），表示模型中段的碰撞稍多（可能是真機 plane 數更多可用、或排程更好），目前不再調。
4. 沒有 PCIe 替身上限時（reference）QD512 衝到 1.19M，確認需要一個中央上限；它代表的是控制器／韌體的處理上限，不是 T500 的 PCIe 真的只有 1.35 GB/s×4。

### 校準候選參數（`microbench/ssdconfig_t500_calibrated.xml`，複製自 pcie135_s321_qd64）

| 類別 | 參數 | 值 |
|---|---|---|
| 公開 | channel／die／plane | 4 ch、16 die、每 die 6 plane（以 2 chip × 12 虛擬 die × 1 plane 表示） |
| 公開 | NAND | TLC、16KB page、2400 MT/s |
| 公開 | mapping | `Ideal_Mapping_Table=true`（DRAM 2GB） |
| 近似 | 獨立 plane 讀取 | 每 plane 一個虛擬 LUN＋`Die_Dispatch_Mode=INDEPENDENT_DIES`（純讀取有效；寫入不適用） |
| 擬合 | tR | 70µs（與主機開銷互相抵換，不可單獨辨識） |
| 擬合 | 中央上限替身 | `PCIe_Lane_Bandwidth=1.35`（約 1.06M IOPS） |
| 選擇 | channel 仲裁 | `Channel_Arbitration=COMMAND_FIRST`（DATA_OUT_FIRST 會產生車隊效應，上限低 25%） |
| 實驗用 | GC | 關閉（`--clean`）；working set 32GiB 全部預寫 |

限制與下一步：
- 這組參數是用同一份 T500 QD 掃描擬合出來的，**還沒有獨立驗證**。下一步應該用沒參與擬合的條件比較真機與模擬（例如不同請求大小、不同 working set，或批次送出的 fio 設定），再用於 NAND_TRANSACTION_BATCHING 研究。
- 研究中「一次全送 vs 分批」與「指令 vs 資料傳出誰優先」都會改變結果；校準時固定為 INDEPENDENT_DIES＋COMMAND_FIRST，研究時要把它們當實驗變數，並說明哪一個是校準基準。

<!-- calibration-run:a1f53965c75267fa:begin -->
## 執行紀錄：mqsim_regress_20261115T_arbitration

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_regress_20261115T_arbitration`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 64 | 42.996 | 46.810 [46.746, 46.839] | 1.500 | 1.369 [1.368, 1.371] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 13702.0 | 12990.9 [12985.2, 13009.1] | 72.0 | 72.0 | 76.7 |
| reference | 321 | 64 | 589129.6 | 608126.6 [608099.0, 608208.0] | 108.0 | 108.0 | 105.0 |

### 本輪限制

- Simulator working set approx 32.1408 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_regress_20261115T_arbitration/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。
<!-- calibration-run:a1f53965c75267fa:end -->

<!-- calibration-run:0e9fa4520c1239d0:begin -->
## 執行紀錄：mqsim_calibration_20261115T_t500_cmdfirst

狀態：完整執行、原始輸出校驗通過；尚未宣稱校準完成。
原始資料與CSV：`/home/emmanuel/projects/MQSim/microbench/results/mqsim_calibration_20261115T_t500_cmdfirst`。

### 趨勢比較（主要目標）

模擬每個seed、真機每個repeat各自以QD1正規化。真機欄位為repeat內比值的中位數及min/max，不是信賴區間。
同repeat的不同QD在不同時間執行，因此快慢狀態可能影響正規化結果；比值不能消除這個混淆。

| Variant | Seed | QD | 模擬吞吐增益 | 真機吞吐增益中位數 [min,max] | 模擬延遲成長 | 真機延遲成長中位數 [min,max] |
|---|---:|---:|---:|---|---:|---|
| reference | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| reference | 321 | 4 | 3.926 | 3.823 [3.818, 3.824] | 1.028 | 1.047 [1.046, 1.048] |
| reference | 321 | 16 | 14.640 | 14.794 [14.776, 14.801] | 1.097 | 1.083 [1.082, 1.084] |
| reference | 321 | 32 | 26.658 | 27.511 [27.476, 27.521] | 1.208 | 1.164 [1.164, 1.166] |
| reference | 321 | 64 | 44.337 | 46.810 [46.746, 46.839] | 1.444 | 1.369 [1.368, 1.371] |
| reference | 321 | 128 | 63.728 | 66.322 [66.230, 66.691] | 2.014 | 1.934 [1.924, 1.937] |
| reference | 321 | 256 | 78.109 | 79.088 [78.986, 79.211] | 3.264 | 3.247 [3.241, 3.251] |
| reference | 321 | 512 | 86.412 | 80.722 [80.594, 80.739] | 5.875 | 6.364 [6.363, 6.374] |
| pcie135 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| pcie135 | 321 | 4 | 3.925 | 3.823 [3.818, 3.824] | 1.028 | 1.047 [1.046, 1.048] |
| pcie135 | 321 | 16 | 14.628 | 14.794 [14.776, 14.801] | 1.097 | 1.083 [1.082, 1.084] |
| pcie135 | 321 | 32 | 26.627 | 27.511 [27.476, 27.521] | 1.208 | 1.164 [1.164, 1.166] |
| pcie135 | 321 | 64 | 44.304 | 46.810 [46.746, 46.839] | 1.458 | 1.369 [1.368, 1.371] |
| pcie135 | 321 | 128 | 63.681 | 66.322 [66.230, 66.691] | 2.028 | 1.934 [1.924, 1.937] |
| pcie135 | 321 | 256 | 76.923 | 79.088 [78.986, 79.211] | 3.333 | 3.247 [3.241, 3.251] |
| pcie135 | 321 | 512 | 77.315 | 80.722 [80.594, 80.739] | 6.639 | 6.364 [6.363, 6.374] |
| fastch_pcie135 | 321 | 1 | 1.000 | 1.000 [1.000, 1.000] | 1.000 | 1.000 [1.000, 1.000] |
| fastch_pcie135 | 321 | 4 | 3.916 | 3.823 [3.818, 3.824] | 1.014 | 1.047 [1.046, 1.048] |
| fastch_pcie135 | 321 | 16 | 14.636 | 14.794 [14.776, 14.801] | 1.099 | 1.083 [1.082, 1.084] |
| fastch_pcie135 | 321 | 32 | 26.677 | 27.511 [27.476, 27.521] | 1.197 | 1.164 [1.164, 1.166] |
| fastch_pcie135 | 321 | 64 | 44.414 | 46.810 [46.746, 46.839] | 1.437 | 1.369 [1.368, 1.371] |
| fastch_pcie135 | 321 | 128 | 63.870 | 66.322 [66.230, 66.691] | 2.000 | 1.934 [1.924, 1.937] |
| fastch_pcie135 | 321 | 256 | 75.667 | 79.088 [78.986, 79.211] | 3.366 | 3.247 [3.241, 3.251] |
| fastch_pcie135 | 321 | 512 | 75.775 | 80.722 [80.594, 80.739] | 6.732 | 6.364 [6.363, 6.374] |

完整相鄰測試QD增益與log(IOPS)對log(QD)斜率見 `analysis/trend_per_run.csv`。
兩點短測不能識別飽和轉折；即使多點也不自動把外部平台當作NAND飽和。

### 絕對數值（背景與追溯，不作主要擬合目標）

Status: UNCALIBRATED. No best parameter set selected.
All measured repeats and simulation seeds retained. Ratios are descriptive, not fit acceptance.

| Variant | Seed | QD | Sim IOPS | Real median [min,max] | Sim device us | Sim end-to-end us | Real fio mean us (median) |
|---|---:|---:|---:|---|---:|---:|---:|
| reference | 321 | 1 | 13759.1 | 12990.9 [12985.2, 13009.1] | 72.0 | 72.0 | 76.7 |
| reference | 321 | 4 | 54012.2 | 49665.7 [49658.1, 49669.6] | 74.0 | 74.0 | 80.2 |
| reference | 321 | 16 | 201436.4 | 192197.1 [192192.9, 192225.0] | 79.0 | 79.0 | 83.0 |
| reference | 321 | 32 | 366796.8 | 357396.3 [357370.7, 357436.4] | 87.0 | 87.0 | 89.3 |
| reference | 321 | 64 | 610043.6 | 608126.6 [608099.0, 608208.0] | 104.0 | 104.0 | 105.0 |
| reference | 321 | 128 | 876841.7 | 861585.6 [860001.6, 867582.5] | 145.0 | 145.0 | 148.3 |
| reference | 321 | 256 | 1074715.3 | 1027533.8 [1026965.1, 1029017.8] | 235.0 | 235.0 | 248.9 |
| reference | 321 | 512 | 1188954.3 | 1048411.2 [1046989.6, 1050117.4] | 423.0 | 423.0 | 488.1 |
| pcie135 | 321 | 1 | 13702.0 | 12990.9 [12985.2, 13009.1] | 72.0 | 72.0 | 76.7 |
| pcie135 | 321 | 4 | 53783.2 | 49665.7 [49658.1, 49669.6] | 74.0 | 74.0 | 80.2 |
| pcie135 | 321 | 16 | 200439.5 | 192197.1 [192192.9, 192225.0] | 79.0 | 79.0 | 83.0 |
| pcie135 | 321 | 32 | 364840.0 | 357396.3 [357370.7, 357436.4] | 87.0 | 87.0 | 89.3 |
| pcie135 | 321 | 64 | 607060.2 | 608126.6 [608099.0, 608208.0] | 105.0 | 105.0 | 105.0 |
| pcie135 | 321 | 128 | 872557.4 | 861585.6 [860001.6, 867582.5] | 146.0 | 146.0 | 148.3 |
| pcie135 | 321 | 256 | 1053996.6 | 1027533.8 [1026965.1, 1029017.8] | 240.0 | 240.0 | 248.9 |
| pcie135 | 321 | 512 | 1059366.5 | 1048411.2 [1046989.6, 1050117.4] | 478.0 | 478.0 | 488.1 |
| fastch_pcie135 | 321 | 1 | 13996.6 | 12990.9 [12985.2, 13009.1] | 71.0 | 71.0 | 76.7 |
| fastch_pcie135 | 321 | 4 | 54807.7 | 49665.7 [49658.1, 49669.6] | 72.0 | 72.0 | 80.2 |
| fastch_pcie135 | 321 | 16 | 204847.3 | 192197.1 [192192.9, 192225.0] | 78.0 | 78.0 | 83.0 |
| fastch_pcie135 | 321 | 32 | 373386.5 | 357396.3 [357370.7, 357436.4] | 85.0 | 85.0 | 89.3 |
| fastch_pcie135 | 321 | 64 | 621649.5 | 608126.6 [608099.0, 608208.0] | 102.0 | 102.0 | 105.0 |
| fastch_pcie135 | 321 | 128 | 893964.3 | 861585.6 [860001.6, 867582.5] | 142.0 | 142.0 | 148.3 |
| fastch_pcie135 | 321 | 256 | 1059083.1 | 1027533.8 [1026965.1, 1029017.8] | 239.0 | 239.0 | 248.9 |
| fastch_pcie135 | 321 | 512 | 1060595.9 | 1048411.2 [1046989.6, 1050117.4] | 478.0 | 478.0 | 488.1 |

### 本輪限制

- Simulator working set approx 32.1408 GiB; real working set 32 GiB. Integer-percent interface prevents exact alignment.
- Sim timing XML is integer microseconds (source implementation), not nanoseconds as README states. Sub-us precision is already lost.
- Sim counts include startup/drain, with no measured-phase read warmup. Real baseline has a read warmup and a 60-second measured phase.
- Sim preconditioning at 75% occupancy is an initialization assumption, not measured GM7 occupancy or a reproduced history.
- Topology, technology, cache and timing values inherited from local XML are candidate assumptions; they are not verified GM7 specifications.
- fio and simulator latency boundaries differ. Host submission/scheduling cost must not be folded into NAND read latency.
- Transaction execution/transfer/waiting averages are not an additive decomposition of host-request latency: multiple transactions can overlap.
- No p99 is invented; this output provides only existing aggregate mean/min/max statistics.
- Reserved QDs were declared after seeing real data; they are comparison points, not blind independent validation.
- Sensitivity changes are parameter experiments only. Burst behavior and hardware mechanisms are not validated.

### 趨勢圖

細線為各次量測，粗線為逐點中位數，陰影為min/max而非信賴區間。每個seed／repeat分別正規化；不同QD在不同時間執行，連線不代表固定SSD狀態下的因果曲線。

![fastch_pcie135：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v0_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![fastch_pcie135：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v0_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![pcie135：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v1_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![pcie135：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v1_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。

![reference：以QD1正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v2_qd1.png)

以QD1為基準；左圖為吞吐增益、右圖為平均延遲成長。QD1本身的快慢差異會影響整條正規化曲線。

![reference：以QD32正規化的吞吐與延遲](results/mqsim_calibration_20261115T_t500_cmdfirst/analysis/figures/trend_v2_qd32.png)

以QD32為基準；左圖為吞吐增益、右圖為平均延遲成長。這張圖避開QD1的變動，但仍保留QD64的快速量測。
<!-- calibration-run:0e9fa4520c1239d0:end -->

## DiskANN workload 驗證：計畫與伺服器資訊（2026-11-15）

目的：在真實 DiskANN 讀取模式下，比較 T500 與校準後 MQSim（`microbench/ssdconfig_t500_calibrated.xml`）的每筆請求延遲。

方法與限制：
- 第一步用**開放迴路**重播：照真機 trace 的請求送出時間（block_rq_issue）送進 MQSim，比較每筆延遲（真機 issue→complete vs MQSim 回應時間）、各時段同時處理中的請求數。吞吐在開放迴路下必然相同，不能拿來比較；模擬偏慢時誤差會被放大。
- 之後研究派送策略要改用**閉迴路**重播：用 DiskANN 的 per-iteration trace（`trace_access.csv`／`trace_stats.csv`，含 steady_clock 時間戳記與 worker id），上一輪完成才送下一輪。
- 沿用 DiskANN_cpp `experiments/split_async/trace_sift100m.sh` 的做法：trace-cmd 以 CLOCK_MONOTONIC 記錄 block 事件，filefrag 換算檔案 offset 與裝置 sector。

伺服器（`diskann_probe_server.txt`）：
- 單一 NVMe `nvme0n1`（T500 1.8T），根目錄 ext4 在 LVM（`ubuntu--vg-ubuntu--lv`，dm 252:0）上；scheduler `none`、`nr_requests=1023`。
- Xeon w7-2495X 24C/48T、125GB RAM。10 秒內背景 0 讀、21 寫。
- 已有 SIFT100M disk index：`/home/user/DiskANN/build/data/SIFT100M/disk_index_SIFT100M_base100M_R64_L100_A1.2_disk.index`（40.96GB，float，R64 L100），query `/home/user/DiskANN/build/data/SIFT100M/bigann_query.fbin`（10,000 筆）。
- DiskANN_cpp（`vucann` 分支 21279b0）已在伺服器編譯：`/home/user/DiskANN_cpp/build/apps/search_disk_index`；trace-cmd、blktrace、filefrag 都已安裝。

工具：`microbench/diskann_block_trace.sh`（伺服器執行）：預設 T16／W4／L100／K10、不快取節點；記錄 block_rq_issue／complete、DiskANN per-iteration trace、filefrag（前後各一次）、dmsetup table、分區起點、diskstats。

### 第一次抓 trace 失敗（diskann_trace_20261114T201912Z_T16_W4_L100）

- `search_disk_index` 讀 query 時失敗：「File size mismatch. Actual size is 1280008 while expected size is 5120008」。伺服器上 SIFT100M 的 `.fbin` 檔其實是 **uint8**：query 1,280,008＝8＋10,000×128×1；base 12,800,000,008＝8＋10⁸×128×1；disk index 40.96GB＝10⁷ sector×4KB，對應 uint8 節點 388 bytes、每 sector 10 個節點（float 會是 81.9GB）。
- 腳本原本預設 `DATA_TYPE=float`（我的錯），而且搜尋失敗仍顯示完成。已修正：預設 uint8；執行前用檔頭（npts、dim）檢查 query 檔大小與資料型別；搜尋 log 有錯誤或沒有 `trace_stats.csv` 時以錯誤結束。
- 該目錄只有 2 個 block 事件，無分析價值，保留作為紀錄。

### 真機 DiskANN trace（diskann_trace_20261114T202209Z_T16_W4_L100）

- 設定：SIFT100M uint8 disk index（R64 L100，40.96GB），10,000 query，T16、W4、L100、K10、不快取節點；trace-cmd 無遺失事件，index extents 前後一致。
- DiskANN 回報：QPS 3,438.65，平均延遲 4,586.61µs（p99.9 15,287µs），平均每 query 118.35 次 IO、30.87 hops，平均 IO 時間 4,041.68µs／query。
- 轉換（`microbench/diskann_trace_to_mqsim.py`，輸出在 `<trace>/mqsim_replay/`）：
  - 位址換算：整碟 sector − 6,400,000（nvme0n1p3 起點 6,397,952＋dm-linear 偏移 2,048）＝LV sector，再用 62 個 filefrag extents 換成 index 檔內 offset。
  - 搜尋期間 index 範圍內的讀取 1,183,194 筆（另有 23,673 筆讀其他檔案，主要是載入 PQ；62 筆寫入；2 筆在搜尋窗外），全部有配對的完成事件。
  - **交叉核對完全吻合**：其中 282 筆 8KB、6 筆 12KB、1 筆 16KB 是 kernel 合併相鄰 4KB 的結果，換回 4KB 共 1,183,491 次，等於 DiskANN `trace_access.csv` 的筆數，而且每個 sector 的次數逐一相同。
  - 真機（block 層 issue→complete）：平均 406,895 IOPS、平均同時處理 40.2 筆；延遲平均 98.9µs、p50 82.9、p90 131.5、p99 382.8、p99.9 599.4、最大 1,263.5µs。唯一 4KB sector 1,010,571 個（重複讀約 15%）。
- 對照合成 QD 掃描：QD32 為 357k IOPS／89µs、QD64 為 608k／105µs；DiskANN 平均 40 筆在途、407k IOPS 下平均 99µs，比同 QD 的均勻穩定負載稍高，推測來自突發性。

### MQSim 修改與重播工具

- `MQSIM_REQUEST_LOG=<path>`（`src/host/IO_Flow_Base.cpp`）：設定時每完成一筆請求寫一行「arrival enqueue complete start_lba lba_count type」；未設定時不影響任何行為。
- `microbench/run_mqsim_trace_replay.sh <replay_dir> [config]`：用 `ssdconfig_t500_calibrated.xml` 建立 trace-based workload（Initial_Occupancy 75%，與校準一致；trace 模式會優先預寫被讀到的 LPA），執行並比較。
- `microbench/compare_trace_replay.py`：依到達時間逐筆配對，比較延遲分布、逐筆相關、10 ms 時間序列的平均延遲與在途數；已用「真機資料當模擬結果」自我測試，各項完全相同。
- **MQSim trace 模式 bug（2026-11-15 修正）**：第一次重播在預寫階段以「Unhandled address distribution type in FTL's preconditioning function」結束。`IO_Flow_Trace_Based::Get_statistics` 從未設定 `Address_distribution_type`、`generate_aligned_addresses`、`alignment_value`、hot/cold 比例與 `Working_set_ratio`（未初始化記憶體），FTL 預寫卻會讀這些欄位。修正：trace 統計明確設為 RANDOM_UNIFORM、不對齊、無 hot/cold、working set 比例 1（只影響預寫時穩態有效頁分布的估計；trace 位址照原樣使用）。合成負載路徑不受影響。失敗的那次執行已移出結果目錄。

### 開放迴路重播結果（run_ssdconfig_t500_calibrated，2026-11-15）

`FTL_EXIT read_entries_created_online=0 released_from_lpa_barrier=0 gc_executions=0`：所有讀到的頁面都有預寫，沒有 GC。1,183,194 筆請求全部完成，到達時間與真機逐筆相同。

| 每筆延遲（µs） | 平均 | p1 | p50 | p90 | p99 | p99.9 | 最大 |
|---|---:|---:|---:|---:|---:|---:|---:|
| T500 全部 | 98.9 | – | 82.9 | 131.5 | 382.8 | 599.4 | 1,263.5 |
| MQSim 全部 | 92.6 | – | 75.5 | 141.3 | 228.7 | 322.9 | 1,111.1 |
| T500（扣掉前 0.2 s） | 96.7 | 70.4 | 82.7 | 128.5 | 359.7 | 595.0 | |
| MQSim（扣掉前 0.2 s） | 92.8 | 73.0 | 75.5 | 141.7 | 229.3 | 322.1 | |

| 超過門檻的比例（扣掉前 0.2 s） | >200µs | >300µs | >500µs |
|---|---:|---:|---:|
| T500 | 2.23% | 1.39% | 0.28% |
| MQSim | 2.16% | 0.18% | 0.003% |

解讀：
1. **主體分布接近**：CDF 到約 90% 幾乎重疊；平均差 4%。p50 模擬低約 7µs，與 QD1 時「block 層 issue→complete 比 MQSim 裝置延遲多約 5µs」的邊界差一致；p90 模擬高約 13µs，與校準時 QD32–64 延遲成長略高（1.21／1.46 vs 1.16／1.37）一致。
2. **真機尾端重很多**：>300µs 的比例 1.39% vs 0.18%，p99.9 595 vs 322µs。真機慢請求在時間上有聚集（38% 落在 10% 的 10 ms 區間）。同樣的到達時間（含 DiskANN 的突發性）在模型裡沒有產生這種尾端，所以尾端不是突發到達本身造成的，而是模型沒有的東西：SSD 內部的偶發慢事件（讀取重試／ECC、韌體背景工作等）或主機完成路徑（中斷、softirq）的延遲。只用 block trace 無法分辨兩者。合成掃描的 fio p99 在 QD32／64 是 159／232µs，也低於 DiskANN 的 383µs。
3. **開頭 0.2 s 的暖機**：真機前 0.2 s 平均 142µs、p90 297µs（57,815 筆），之後才穩定；模型沒有這段。可能是 SSD 從省電狀態醒來或內部快取冷啟動，未查證。
4. **時間序列反相關是開放迴路的假象**：1 ms 區間內，真機延遲與同區間到達數相關係數 −0.86（真機一變慢，閉迴路的 DiskANN 就少送請求）。重播照這些到達時間送，模型在那些區間反而變空、延遲變低，所以 10 ms 平均延遲相關係數 −0.52（含暖機 −0.66）。這不是模型錯，而是開放迴路比較的限制。
5. **逐筆相關約 0（r＝−0.01）是預期的**：哪個 LBA 落在哪個 plane 由 FTL 決定，T500 與 MQSim 不同，個別請求的碰撞不同；只能比較分布。

結論：在 DiskANN 讀取模式下，模型的主體延遲分布可信（約 10% 內），但低估尾端（p99 約 0.6 倍、p99.9 約 0.54 倍），也沒有真機的偶發慢區間。下一步要做閉迴路重播，才能比較 QPS 與每輪延遲，並避免第 4 點的假象。

## 閉迴路重播（2026-11-15）

目的：研究 SSD 內部行為時，讓 DiskANN 的每一輪讀取依賴「模擬中」上一輪的完成時間，而不是真機的時間戳記；這樣 QPS、每輪等待時間會隨 SSD 模型改變。

MQSim 新增 `IO_Flow_Closed_Loop_Replay`（`src/host/IO_Flow_Closed_Loop_Replay.h/.cpp`），在 trace-based workload 加上 `Replay_Schedule_File` 即啟用（`File_Path` 的 ASCII trace 只用於預寫統計，與排程中的讀取完全相同）：
- 每個 worker 依序重播「輪」：在輪開始後依實測 offset 送出該輪的讀取 → 等該輪全部讀取在模擬 SSD 中完成 → 加上實測的完成後主機時間（post）→ 加上實測的計算間隔（gap）→ 開始下一輪。
- 只有裝置時間由模擬決定；主機端時間（送出路徑、完成路徑、搜尋計算、換 query）都取自真機。
- `MQSIM_ROUND_LOG=<path>` 記錄每輪的開始、最後完成、結束時間。

排程產生（`microbench/build_closed_loop_schedule.py`，輸出 `<trace>/closed_loop/`）：
- 每一輪（query × iteration）以「相同 4KB sector、送出時間落在該輪 read_begin～read_end 內」對應 block 請求；1,183,194 筆 block 請求全部對應（kernel 合併造成的沿用 297 次＝282＋2×6＋3，與合併數完全一致），共 308,703 輪、16 個 worker。
- 真機每輪：read_begin→read_end 平均 131.4µs（p50 116.1、p99 480.0）；送出前主機時間平均 5.2µs（p99 8.9）；最後完成到 DiskANN 收到平均 2.7µs（p99 8.0）。150 輪的 post 因時鐘些微負值被設為 0。
- 依構造，「輪開始＋offset＋真機延遲＋post」恰好等於真機的 read_end，所以排程在真機延遲下會完全重現真機時間軸。
- `microbench/compare_closed_loop.py` 自我測試（真機資料當模擬結果）：各項完全相同；由輪紀錄算出的 query 速率 3,438.8／s，與 DiskANN 回報 QPS 3,438.65 一致。

假設與限制：DiskANN 每輪等 W 個讀取全部回來才處理，搜尋路徑與 SSD 快慢無關；主機時間視為固定（不模擬 CPU 競爭、I/O stack 變動）；W 個讀取的送出間隔沿用實測值。

執行：`microbench/run_mqsim_closed_loop.sh <trace_dir> [config]`；`ROUNDS_PER_WORKER=N` 只重播每個 worker 的前 N 輪做煙霧測試。


## 閉迴路啟動崩潰：編譯相依修正（2026-11-15）

run_t500_closed_loop_20261115_045636在約0.07秒以signal11退出，沒有完成重播。查到IO_Flow_Parameter_Set.h與解析object在04:45更新，main.o仍為03:31；原Makefile未追蹤header依賴。新增Replay_Schedule_File改變物件layout時，混用舊配置大小與新解析程式可能越界；沒有core/backtrace，目前不能當作已動態證實的根因。

已修正Makefile：編譯產生-MMD/-MP依賴檔並載入，Makefile變動也重編object，補各object的目錄依賴並去除重複main來源。完整重編譯消除混用；main.d確認包含IO_Flow_Parameter_Set.h，make -n -W的乾跑確認header變動會重編依賴來源。沒有啟動模擬。

閉迴路腳本修正：無TAG且全輪模式時，避免command substitution最後條件為false導致set -e提前退出；模擬非零退出現在顯示狀態碼、目錄與stderr尾端，保留FAILED標記。舊失敗目錄保留。下一步由使用者先跑每worker200輪的短測，確認新binary能啟動並完成，再跑全輪；重編成功不等於segfault已經執行驗證消失。


<!-- full-closed-loop-t500-20261115:start -->
## T500完整閉迴路重播結果（2026-11-15）

執行：`run_t500_full_20261115_050240`，設定 `ssdconfig_t500_calibrated.xml`；DiskANN trace為T16／W4／L100。此節從封存comparison.json與兩側round紀錄產生，沒有啟動新實驗。

共308,703輪、10,000個query、1,183,194筆讀取。兩側輪數、排程read總數一致；HOST_EXIT generated=completed=1183194、waiting=0、software_map_entries=0。FTL_EXIT gc_executions=0、read_entries_created_online=0。這輪完成，沒有前次啟動崩潰或未排空問題。

### 吞吐與平均值

| 指標 | T500 | MQSim | 模擬相對差異 |
|---|---:|---:|---:|
| 重播時間 s | 2.9080 | 2.9186 | +0.36% |
| Read IOPS | 406,881.4541 | 405,402.6327 | -0.36% |
| Query rate /s | 3,438.8397 | 3,426.3412 | -0.36% |

![吞吐比較](results/diskann_trace_20261114T202209Z_T16_W4_L100/closed_loop/run_t500_full_20261115_050240/report_figures/throughput.png)

### 延遲分布

| 指標 µs | T500 | MQSim | 模擬相對差異 |
|---|---:|---:|---:|
| 單筆讀取 mean | 98.89 | 91.07 | -7.91% |
| 單筆讀取 p50 | 82.92 | 75.43 | -9.03% |
| 單筆讀取 p90 | 131.48 | 138.49 | +5.33% |
| 單筆讀取 p99 | 382.79 | 216.75 | -43.38% |
| 單筆讀取 p99.9 | 599.43 | 303.99 | -49.29% |
| 每輪read call mean | 131.39 | 131.50 | +0.09% |
| 每輪read call p50 | 116.11 | 127.42 | +9.75% |
| 每輪read call p90 | 173.82 | 192.51 | +10.76% |
| 每輪read call p99 | 479.98 | 275.59 | -42.58% |
| 每輪read call p99.9 | 716.04 | 361.88 | -49.46% |
| Query I/O span mean | 4,573.45 | 4,577.01 | +0.08% |
| Query I/O span p50 | 4,378.20 | 4,554.92 | +4.04% |
| Query I/O span p90 | 5,206.24 | 4,971.06 | -4.52% |
| Query I/O span p99 | 8,069.85 | 5,506.79 | -31.76% |
| Query I/O span p99.9 | 15,238.99 | 6,159.77 | -59.58% |

![延遲分位數比較](results/diskann_trace_20261114T202209Z_T16_W4_L100/closed_loop/run_t500_full_20261115_050240/report_figures/latency_quantiles.png)

分位數圖使用對數y軸；mean是平均值，與後面的分位數是不同統計量，連線只供比較。

### 分段時間趨勢

![每50ms輪延遲趨勢](results/diskann_trace_20261114T202209Z_T16_W4_L100/closed_loop/run_t500_full_20261115_050240/report_figures/round_timeline.png)

以各自時間軸的round start分入50ms區間；畫每區間mean與p99，最後區間可能不足50ms。閉迴路會讓同一query在兩側不同時刻開始，因此同時間區間不是相同query集合，不以曲線逐點差值推論同一批請求的快慢。

### 判讀與適用範圍

1. 整體query rate差−0.36%、平均每輪時間差+0.09%，支持模型在這份完整T16／W4／L100 trace下重現整體處理速度；不證明所有負載或內部資源行為已對齊。
2. 每輪p50較真機高約9.7%，但p99低約42.6%、p99.9低約49.5%。均值接近可能包含分布中段偏高與尾端偏低的抵消，不能只看QPS宣稱延遲模型完整。
3. Query I/O span只涵蓋該query第一個read_begin至最後read_end，並非完整端到端query latency；query rate也是該重播窗口下的速率。主機post/gap與送出offset沿用真機，屬固定主機時間假設，QPS不是完全獨立預測。
4. 單筆latency兩側各1183194筆，已無煙霧測試的樣本數差異；真機為block issue→complete，模型使用自己的request log邊界，不能當作純NAND時間逐筆對齊。
5. Batch barrier受一輪最慢請求影響，尾端低估可能影響分批策略與pipeline填滿的結論。尚不能僅由這些分位數辨識ECC、韌體背景工作或host完成路徑；不添加任意隨機延遲去配p99。
6. 先前前200輪的巨大差距在完整重播平均上沒有延續；分段圖提供啟動與後續變化的觀察，但不因此排除後段的局部偏差。下一步依相同輪／query與batch大小比較最慢讀取、完成跨度及窗口變化，再決定是否需要新機制。

來源：[comparison.json](results/diskann_trace_20261114T202209Z_T16_W4_L100/closed_loop/run_t500_full_20261115_050240/comparison.json)。圖表保留PNG／SVG；可用 `python3 -B microbench/plot_closed_loop_record.py <trace_dir> <run_dir>` 從既有資料重新產圖。
<!-- full-closed-loop-t500-20261115:end -->

### 舊結果是否受 header 相依問題影響（2026-11-15 檢查）

原 Makefile 不追蹤 header，增量編譯可能混用舊物件。檢查各批次的 binary 來源：
- 所有 `mqsim_calibration_*`／`mqsim_regress_*` 批次都由 `run_mqsim_calibration.sh --run` 執行，該腳本用 `make -B` 強制全部重編，並比對原始碼 hash，不受影響。
- 開放迴路重播（`mqsim_replay/run_ssdconfig_t500_calibrated`，04:29）：最後一次全部重編是 cmdfirst 批次（含 `Channel_Arbitration` 的 header 變更）；之後到重播前只改了 `IO_Flow_Base.cpp` 與 `IO_Flow_Trace_Based.cpp` 兩個 .cpp，沒有改 header，增量編譯不會混用，結果有效。
- 受影響的只有加入 `Replay_Schedule_File`（改 `IO_Flow_Parameter_Set.h`）後的第一次閉迴路執行（`run_t500_closed_loop_20261115_045636`，signal 11），已由上節的 Makefile 修正處理。

### 尾端對整體效能的影響上限（2026-11-15）

使用者觀點：尾端占比小的話，對整體效能影響也小，不一定需要調查。以 `real_rounds.csv` 與 `run_t500_full_20261115_050240` 量化：
- 每個 worker 的時間中，讀取輪（read_begin→read_end）占 87.2%，其餘是主機計算。
- 真機 >300µs 的輪占 2.78% 的輪數、9.73% 的輪時間；>500µs 占 0.84% 的輪數、3.90% 的輪時間。
- 超過 250µs 的部分（尾端的「多出來」時間）：真機占輪時間 4.57%，模型 0.60%；超過 300µs：3.37% vs 0.17%。
- 所以模型少掉的尾端約等於輪時間的 4%、worker 時間的約 3.5%；就算完全補上，QPS 也只會變動約 4% 以內。完整閉迴路 QPS 只差 −0.36%，是因為模型的中段（p50 +9.7%）稍慢，抵消了尾端偏快。

決定：暫不調查尾端來源。研究時把「約 4% 的 QPS 不確定度」當成模型的解析度：派送策略造成的 QPS 差異若小於約 5%，不據此下結論；若研究問題本身是尾端延遲（例如最慢讀取、p99 輪時間），則需先回頭補尾端。
